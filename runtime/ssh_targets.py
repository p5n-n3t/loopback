"""Managed SSH targets for the Loopback fabric.

An SSH target is a remote machine the gateway reaches with the system ``ssh``
client in non-interactive ``BatchMode``. Loopback never stores or reads key
material: a target may reference an ``identity_file`` path, which must exist and
be readable by the service account, but its contents are never opened or
returned.

State files (all under ``ctx.cfg_dir``, mode 0600, written atomically):

* ``ssh_targets.json`` – the target registry (list of target dicts).
* ``probe_state.json`` – last probe result per target name.

Probe results are also appended to the ``probe_history`` table (see
``topology.ProbeHistory``) under the edge id ``gateway->ssh:<name>``.

The module has no import-time side effects and is usable without a running
server.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:%-]{1,253}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?$")
TARGET_KINDS = ("remote-physical", "remote-vm", "container", "unknown")
VIA_CHOICES = ("auto", "tailscale", "lan", "public")
PROBE_STATUSES = ("healthy", "auth_failed", "unreachable", "degraded", "error")
CONTAINER_VIRT = {"docker", "podman", "lxc", "lxc-libvirt", "systemd-nspawn", "openvz", "wsl", "rkt", "proot", "pouch"}
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

_AUTH_MARKERS = ("permission denied", "publickey", "authentication failed", "too many authentication failures",
                 "no supported authentication methods")
_UNREACHABLE_MARKERS = ("connection refused", "timed out", "no route", "could not resolve", "name or service not known",
                        "network is unreachable", "host is down", "connection reset", "temporary failure in name resolution",
                        "nodename nor servname")

# Remote probe script. Plain POSIX sh; each fact is printed on its own line with
# a unique prefix so banners/MOTD output can never be mistaken for a field.
_PROBE_SCRIPT = r"""
echo "__LB_HOSTNAME=$(hostname 2>/dev/null || uname -n 2>/dev/null)"
echo "__LB_UNAME=$(uname -srm 2>/dev/null)"
if [ -r /etc/os-release ]; then
  echo "__LB_OS=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n 1 | tr -d '"')"
elif command -v sw_vers >/dev/null 2>&1; then
  echo "__LB_OS=$(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null)"
else
  echo "__LB_OS=$(uname -s 2>/dev/null)"
fi
echo "__LB_USER=$(id -un 2>/dev/null)"
echo "__LB_HOME=$HOME"
if command -v systemd-detect-virt >/dev/null 2>&1; then
  echo "__LB_VIRT=$(systemd-detect-virt 2>/dev/null || true)"
else
  echo "__LB_VIRT=unknown"
fi
if [ -f /.dockerenv ] || [ -f /run/.containerenv ]; then echo "__LB_CONTAINER=yes"; else echo "__LB_CONTAINER=no"; fi
echo "__LB_END=1"
"""

_TS_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_TS_CACHE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- helpers
def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to *path* atomically (tmp + fsync + os.replace) with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def read_json(path: Path, default: Any) -> Any:
    """Read JSON from *path*, returning *default* when missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def _clip(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _now() -> float:
    return time.time()


def tailscale_status_json(timeout: float = 5.0, *, max_age: float = 5.0) -> dict[str, Any] | None:
    """Return parsed ``tailscale status --json`` or None when unavailable.

    Results are cached for *max_age* seconds so the topology view and ingress
    status do not spawn the CLI repeatedly.
    """
    with _TS_CACHE_LOCK:
        if _TS_CACHE["data"] is not None and _now() - _TS_CACHE["ts"] < max_age:
            return _TS_CACHE["data"]
    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        data = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    with _TS_CACHE_LOCK:
        _TS_CACHE.update(ts=_now(), data=data)
    return data


def parse_tailscale_peers(status: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract peers from a ``tailscale status --json`` document.

    Returns ``[{hostname, dns_name, ips, os, online}]``; never includes keys.
    """
    if not isinstance(status, dict):
        return []
    peers = status.get("Peer") or {}
    out: list[dict[str, Any]] = []
    if not isinstance(peers, dict):
        return out
    for entry in peers.values():
        if not isinstance(entry, dict):
            continue
        out.append({
            "hostname": str(entry.get("HostName") or ""),
            "dns_name": str(entry.get("DNSName") or "").rstrip("."),
            "ips": [str(ip) for ip in (entry.get("TailscaleIPs") or []) if ip],
            "os": str(entry.get("OS") or ""),
            "online": bool(entry.get("Online")),
        })
    return out[:1000]


def tailscale_peers(timeout: float = 5.0) -> list[dict[str, Any]]:
    """Discover tailnet peers via the local ``tailscale`` CLI (empty list if absent)."""
    return parse_tailscale_peers(tailscale_status_json(timeout))


def infer_via(host: str, tailscale_peers: Iterable[Any] | None = None, *, resolve: bool = True) -> str:
    """Classify how *host* is reached: tailscale | lan | public | unknown.

    * ``tailscale`` – 100.64.0.0/10 (CGNAT), Tailscale ULA, ``*.ts.net`` names, or a
      known tailnet peer (hostname, MagicDNS name or IP).
    * ``lan`` – RFC1918, link-local, loopback (incl. ``localhost``).
    * ``public`` – any other routable address.
    * ``unknown`` – a name that cannot be resolved (or *resolve* is False).
    """
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        return "unknown"
    if h.endswith(".ts.net"):
        return "tailscale"
    names: set[str] = set()
    for peer in tailscale_peers or []:
        if isinstance(peer, dict):
            for key in ("hostname", "dns_name", "HostName", "DNSName"):
                if peer.get(key):
                    value = str(peer[key]).rstrip(".").lower()
                    names.add(value)
                    if key in ("dns_name", "DNSName"):
                        names.add(value.split(".")[0])
            for ip in peer.get("ips") or peer.get("TailscaleIPs") or []:
                names.add(str(ip).lower())
        elif peer:
            names.add(str(peer).rstrip(".").lower())
    if h in names:
        return "tailscale"
    if h in {"localhost", "localhost.localdomain"} or h.endswith(".localhost"):
        return "lan"
    addrs: list[str] = []
    try:
        ipaddress.ip_address(h.split("%")[0])
        addrs = [h.split("%")[0]]
    except ValueError:
        if not resolve:
            return "lan" if h.endswith((".local", ".lan", ".home.arpa", ".internal")) else "unknown"
        try:
            infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
            addrs = [str(i[4][0]).split("%")[0] for i in infos]
        except (socket.gaierror, OSError, UnicodeError):
            return "unknown"
    if not addrs:
        return "unknown"
    verdicts = []
    for raw in addrs:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if ip in TAILSCALE_CGNAT or ip in TAILSCALE_V6 or str(ip) in names:
            verdicts.append("tailscale")
        elif ip.is_private or ip.is_link_local or ip.is_loopback:
            verdicts.append("lan")
        else:
            verdicts.append("public")
    for choice in ("tailscale", "lan", "public"):
        if choice in verdicts:
            return choice
    return "unknown"


def machine_kind_from(virt: str, container_marker: bool) -> str:
    """Map ``systemd-detect-virt`` output (+ /.dockerenv marker) to a machine kind."""
    v = (virt or "").strip().lower()
    if container_marker or v in CONTAINER_VIRT:
        return "container"
    if v == "none":
        return "physical"
    if not v or v == "unknown":
        return "unknown"
    return "virtual-machine"


def classify_ssh_failure(returncode: int, stderr: str) -> str:
    """Map an ssh failure to a probe status (auth_failed | unreachable | error)."""
    low = (stderr or "").lower()
    if returncode == 255 and any(m in low for m in _AUTH_MARKERS):
        return "auth_failed"
    if any(m in low for m in _UNREACHABLE_MARKERS):
        return "unreachable"
    return "error"


def parse_probe_output(stdout: str) -> dict[str, str]:
    """Parse ``__LB_KEY=value`` lines emitted by the remote probe script."""
    fields: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        if line.startswith("__LB_") and "=" in line:
            key, value = line[5:].split("=", 1)
            fields[key.strip().lower()] = value.strip()[:300]
    return fields


# --------------------------------------------------------------------------- store
class SSHTargetStore:
    """Registry, prober and remote executor for SSH targets.

    Construct with the shared ``AppContext``. All methods are thread-safe;
    ``probe`` is blocking (run it via ``asyncio.to_thread`` from async code)
    and ``execute`` is a coroutine.
    """

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- paths
    @property
    def path(self) -> Path:
        return Path(self.ctx.cfg_dir) / "ssh_targets.json"

    @property
    def probe_path(self) -> Path:
        return Path(self.ctx.cfg_dir) / "probe_state.json"

    @property
    def home(self) -> str:
        ident = getattr(self.ctx, "identity", None)
        return str(getattr(ident, "home", "") or os.path.expanduser("~"))

    # ---------------------------------------------------------------- registry
    def _rows(self) -> list[dict[str, Any]]:
        rows = read_json(self.path, [])
        return [r for r in rows if isinstance(r, dict) and r.get("name")] if isinstance(rows, list) else []

    def _public(self, row: dict[str, Any], probes: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {
            "name": row.get("name"),
            "host": row.get("host"),
            "port": int(row.get("port") or 22),
            "user": row.get("user"),
            "identity_file": row.get("identity_file"),
            "use_ssh_config": bool(row.get("use_ssh_config", False)),
            "display_name": row.get("display_name"),
            "kind": row.get("kind") or "unknown",
            "group": row.get("group"),
            "via": row.get("via") or "auto",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        if probes is not None:
            out["last_probe"] = probes.get(str(row.get("name")))
        return out

    def list(self) -> list[dict[str, Any]]:
        """All targets (sorted by name) with ``last_probe``. Never includes key material."""
        probes = self.last_probes()
        return [self._public(r, probes) for r in sorted(self._rows(), key=lambda r: str(r.get("name")))]

    def get(self, name: str) -> dict[str, Any]:
        """Return one target (with ``last_probe``) or raise KeyError."""
        row = next((r for r in self._rows() if r.get("name") == name), None)
        if row is None:
            raise KeyError(f"ssh target not found: {name}")
        return self._public(row, self.last_probes())

    def _resolve_identity_file(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("identity_file must be a path string or null")
        raw = value.strip()
        if "\x00" in raw or "\n" in raw:
            raise ValueError("identity_file contains invalid characters")
        if raw == "~" or raw.startswith("~/"):
            raw = self.home + raw[1:]
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError("identity_file must be an absolute path (or start with ~/)")
        if not path.is_file():
            raise ValueError(f"identity_file does not exist or is not a regular file: {path}")
        if not os.access(path, os.R_OK):
            raise ValueError(f"identity_file is not readable by the service user: {path}")
        return str(path)

    def upsert(self, **fields: Any) -> dict[str, Any]:
        """Create or update a target. Raises ValueError on invalid input.

        Accepted fields: name, host, port, user, identity_file, display_name,
        kind, group, via. Omitted optional fields keep their previous value on update.
        """
        name = str(fields.get("name") or "").strip()
        if not NAME_RE.match(name):
            raise ValueError("name must match ^[A-Za-z0-9_.-]{1,40}$")
        if name.lower() == "local":
            raise ValueError("'local' is reserved")
        with self._lock:
            rows = self._rows()
            existing = next((r for r in rows if r.get("name") == name), {})

            def pick(key: str, default: Any = None) -> Any:
                return fields[key] if key in fields else existing.get(key, default)

            host = str(pick("host") or "").strip()
            if not host or host.startswith("-") or not HOST_RE.match(host):
                raise ValueError("host must be a hostname or IP address")
            try:
                port = int(pick("port", 22) or 22)
            except (TypeError, ValueError):
                raise ValueError("port must be an integer") from None
            if not 1 <= port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            user = pick("user")
            user = str(user).strip() if user not in (None, "") else (getattr(self.ctx.identity, "username", "") or "")
            if not USER_RE.match(user):
                raise ValueError("user must be a valid POSIX account name")
            use_ssh_config = pick("use_ssh_config", False)
            if not isinstance(use_ssh_config, bool):
                raise ValueError("use_ssh_config must be a boolean")
            identity_file = self._resolve_identity_file(pick("identity_file"))
            display_name = pick("display_name")
            if display_name is not None:
                display_name = str(display_name).strip()[:80] or None
            kind = pick("kind", "unknown") or "unknown"
            if kind not in TARGET_KINDS:
                raise ValueError(f"kind must be one of {', '.join(TARGET_KINDS)}")
            group = pick("group")
            if group is not None:
                group = str(group).strip()[:40] or None
            via = pick("via", "auto") or "auto"
            if via not in VIA_CHOICES:
                raise ValueError(f"via must be one of {', '.join(VIA_CHOICES)}")
            now = _now()
            row = {
                "name": name, "host": host, "port": port, "user": user, "identity_file": identity_file, "use_ssh_config": use_ssh_config,
                "display_name": display_name, "kind": kind, "group": group, "via": via,
                "created_at": existing.get("created_at") or now, "updated_at": now,
            }
            rows = [r for r in rows if r.get("name") != name] + [row]
            atomic_write_json(self.path, sorted(rows, key=lambda r: str(r.get("name"))))
            if existing and (existing.get("host") != host or existing.get("port") != port or existing.get("user") != user):
                self._drop_probe(name)  # old result describes a different endpoint
        return self._public(row, self.last_probes())

    def remove(self, name: str) -> bool:
        """Delete a target and its stored probe. Returns False when it did not exist."""
        with self._lock:
            rows = self._rows()
            after = [r for r in rows if r.get("name") != name]
            if len(after) == len(rows):
                return False
            atomic_write_json(self.path, after)
            self._drop_probe(name)
        return True

    # ---------------------------------------------------------------- probe state
    def last_probes(self) -> dict[str, Any]:
        """``{name: last probe result}`` as stored in ``probe_state.json``."""
        data = read_json(self.probe_path, {})
        return data if isinstance(data, dict) else {}

    def _drop_probe(self, name: str) -> None:
        with self._lock:
            data = self.last_probes()
            if name in data:
                data.pop(name, None)
                atomic_write_json(self.probe_path, data)

    def _store_probe(self, name: str, result: dict[str, Any]) -> None:
        with self._lock:
            data = self.last_probes()
            data[name] = result
            known = {r.get("name") for r in self._rows()}
            data = {k: v for k, v in data.items() if k in known or k == name}
            atomic_write_json(self.probe_path, data)
        history = getattr(getattr(self.ctx, "topology", None), "history", None)
        if history is None:
            try:
                from topology import ProbeHistory  # local module; optional

                history = ProbeHistory(Path(self.ctx.cfg_dir) / "loopback.db")
            except Exception:  # noqa: BLE001 - history is best effort
                history = None
        if history is not None:
            try:
                history.record(f"gateway->ssh:{name}", result.get("status", "error"), result.get("latency_ms"),
                               result.get("error"))
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- ssh plumbing
    def _ssh_args(self, target: dict[str, Any], connect_timeout: int = 5) -> list[str]:
        binary = shutil.which("ssh")
        if not binary:
            raise RuntimeError("ssh client is not installed")
        args = [binary, "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(connect_timeout)}",
                "-o", "StrictHostKeyChecking=accept-new", "-o", "UpdateHostKeys=no",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
        if not target.get("use_ssh_config"):
            args += ["-p", str(int(target.get("port") or 22))]
        if target.get("identity_file"):
            args += ["-i", str(target["identity_file"]), "-o", "IdentitiesOnly=yes"]
        args += ["--", str(target["host"]) if target.get("use_ssh_config") else f"{target['user']}@{target['host']}"]
        return args

    def _effective_via(self, target: dict[str, Any], *, resolve: bool) -> str:
        via = target.get("via") or "auto"
        if via != "auto":
            return via
        return infer_via(str(target.get("host") or ""), tailscale_peers(), resolve=resolve)

    def probe(self, name: str, timeout: float = 8) -> dict[str, Any]:
        """Run a BatchMode SSH probe and store the result.

        Returns ``{status, latency_ms, checked_at, error, via, remote:{hostname, os,
        kernel, arch, user, home, machine_kind}}``. Raises KeyError for unknown targets.
        """
        target = self.get(name)
        started = time.monotonic()
        result: dict[str, Any] = {"status": "error", "latency_ms": None, "checked_at": None, "error": None,
                                  "remote": {}, "via": None}
        try:
            args = self._ssh_args(target, connect_timeout=max(1, min(5, int(timeout))))
            args.append("sh -c " + shlex.quote(_PROBE_SCRIPT))
            proc = subprocess.run(args, capture_output=True, text=True, timeout=max(2.0, float(timeout)),
                                  stdin=subprocess.DEVNULL, errors="replace")
            latency = int((time.monotonic() - started) * 1000)
            result["latency_ms"] = latency
            fields = parse_probe_output(proc.stdout[:65536])
            if proc.returncode != 0 and "end" not in fields:
                result["status"] = classify_ssh_failure(proc.returncode, proc.stderr)
                result["error"] = _clip(proc.stderr) or f"ssh exited with code {proc.returncode}"
                if result["status"] == "unreachable":
                    result["latency_ms"] = None
            else:
                uname = fields.get("uname", "").split()
                remote = {
                    "hostname": fields.get("hostname") or None,
                    "os": fields.get("os") or None,
                    "kernel": " ".join(uname[:2]) if len(uname) >= 2 else (uname[0] if uname else None),
                    "arch": uname[2] if len(uname) >= 3 else None,
                    "user": fields.get("user") or None,
                    "home": fields.get("home") or None,
                    "virtualization": fields.get("virt") or None,
                    "machine_kind": machine_kind_from(fields.get("virt", ""), fields.get("container") == "yes"),
                }
                result["remote"] = remote
                missing = [k for k in ("hostname", "os", "kernel", "user", "home") if not remote.get(k)]
                if missing:
                    result["status"] = "degraded"
                    result["error"] = "probe succeeded but missing fields: " + ", ".join(missing)
                else:
                    result["status"] = "healthy"
        except subprocess.TimeoutExpired:
            result.update(status="unreachable", error=f"ssh probe timed out after {timeout}s")
        except (OSError, RuntimeError) as exc:
            result.update(status="error", error=_clip(str(exc)))
        result["checked_at"] = _now()
        try:
            result["via"] = self._effective_via(target, resolve=True)
        except Exception:  # noqa: BLE001
            result["via"] = "unknown"
        self._store_probe(name, result)
        return result

    # ---------------------------------------------------------------- execution
    @staticmethod
    def _remote_command(command: str, cwd: str | None) -> str:
        """Build the remote shell string: ``cd -- <cwd> && <command>`` via bash -lc (sh -lc fallback)."""
        inner = command
        if cwd:
            if cwd == "~":
                cd = 'cd -- "$HOME"'
            elif cwd.startswith("~/"):
                cd = 'cd -- "$HOME"/' + shlex.quote(cwd[2:])
            else:
                cd = "cd -- " + shlex.quote(cwd)
            # Group the command so a failed cd aborts every part of a compound command.
            inner = f"{cd} && {{\n{command}\n}}"
        q = shlex.quote(inner)
        outer = f"if command -v bash >/dev/null 2>&1; then exec bash -lc {q}; else exec sh -lc {q}; fi"
        # Wrap in sh -c so a non-POSIX login shell (fish, csh) on the remote still works.
        return "sh -c " + shlex.quote(outer)

    async def execute(self, name: str, command: str, cwd: str | None = None, timeout: float = 120,
                      max_output: int = 1048576, approval_id: str | None = None) -> dict[str, Any]:
        """Run *command* on target *name* after gateway policy checks.

        Policy (``check_command`` and, when *cwd* is set, ``check_path``) is
        evaluated with ``target=name`` before anything is sent; PolicyError /
        ApprovalRequired propagate to the caller. Output is clipped to
        *max_output* bytes per stream and the process is killed on timeout.
        """
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command is required")
        target = self.get(name)
        self.ctx.policy.check_command(command, approval_id=approval_id, target=name)
        if cwd:
            self.ctx.policy.check_path(cwd, target=name)
        max_output = max(1024, min(int(max_output), 64 * 1024 * 1024))
        timeout = max(1.0, min(float(timeout), 86400.0))
        args = self._ssh_args(target) + [self._remote_command(command, cwd)]
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=True)
        truncated = {"flag": False}

        async def drain(stream: asyncio.StreamReader | None) -> bytes:
            buf = bytearray()
            if stream is None:
                return b""
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                room = max_output - len(buf)
                if room > 0:
                    buf.extend(chunk[:room])
                if len(chunk) > room:
                    truncated["flag"] = True
            return bytes(buf)

        out_task = asyncio.create_task(drain(proc.stdout))
        err_task = asyncio.create_task(drain(proc.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                pass
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.gather(out_task, err_task), timeout=5)
        except TimeoutError:
            out_task.cancel()
            err_task.cancel()
            stdout, stderr = b"", b""
        return {
            "target": name,
            "command": command,
            "cwd": cwd,
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "truncated": truncated["flag"],
            "duration_ms": int((time.monotonic() - started) * 1000),
            "timed_out": timed_out,
            "enforcement": "gateway-path-check",
        }

    # ---------------------------------------------------------------- discovery
    def tailscale_peers(self) -> list[dict[str, Any]]:
        """Tailnet peers from ``tailscale status --json`` (empty when unavailable)."""
        return tailscale_peers()

    def infer_via(self, host: str, peers: Iterable[Any] | None = None) -> str:
        """Instance wrapper for :func:`infer_via` using live tailnet peers by default."""
        return infer_via(host, tailscale_peers() if peers is None else peers)
