"""Ingress management: Tailscale Serve/Funnel and Cloudflare Tunnel.

Exposure model (stated explicitly, never softened):

* Tailscale ``serve``  -> exposure ``tailnet`` (private, governed by tailnet ACLs).
* Tailscale ``funnel`` -> exposure ``public`` (anyone on the internet).
* Cloudflare Tunnel    -> exposure ``public`` unless ``access_policy`` is
  ``cloudflare-access`` (then ``access-protected``). A tunnel is never private.

Loopback only touches the Serve/Funnel handler for its own HTTPS port (never
``serve reset`` / ``funnel reset``) and manages a single detached
``cloudflared`` process via ``cfg_dir/cloudflared.pid`` and
``cfg_dir/cloudflared.log``. Tunnel tokens are passed with ``--token-file`` so
they never appear in process listings, and no credential is ever returned.

No side effects at import time.
"""
from __future__ import annotations

import copy
import datetime as _dt
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TUNNEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
FQDN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)
ACCESS_POLICIES = ("oauth", "cloudflare-access", "both")
TAILSCALE_PORTS = (443, 8443, 10000)
LOG_MAX_BYTES = 5 * 1024 * 1024

CLOUDFLARE_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)]
TAILSCALE_NETS = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]

_SECRETISH = [
    (re.compile(r"(?i)(token|secret|password|credential|authorization)([\"'=:\s]+)([^\s\"',]{8,})"), r"\1\2[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(\.[A-Za-z0-9_-]+)?"), "[redacted-jwt]"),
    (re.compile(r"\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), r"\1-…"),
    (re.compile(r"(?<![/\w.-])[A-Za-z0-9+_-]{40,}={0,2}"), "[redacted]"),
]


class IngressError(ValueError):
    """Validation/apply failure; ``diagnostics`` carries structured checks for the API."""

    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


# --------------------------------------------------------------------------- helpers
def redact(text: str) -> str:
    """Remove token-like material and shorten UUIDs in log/diagnostic text."""
    out = str(text or "")
    for pattern, repl in _SECRETISH:
        out = pattern.sub(repl, out)
    return out


def _clip(text: Any, limit: int = 800) -> str:
    s = redact(str(text or "")).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
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


def _run(args: list[str], timeout: float = 20.0) -> dict[str, Any]:
    """Run a CLI command; never raises. Returns {ok, returncode, stdout, stderr, cmd}."""
    display = " ".join(args[:6])
    if not args or not shutil.which(args[0]):
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{args[0] if args else '?'} is not installed",
                "cmd": display, "missing": True}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"timed out after {timeout}s", "cmd": display}
    except OSError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc), "cmd": display}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout[:262144],
            "stderr": proc.stderr[:65536], "cmd": display}


def _cmd_summary(res: dict[str, Any]) -> dict[str, Any]:
    return {"cmd": res.get("cmd"), "ok": res.get("ok"), "returncode": res.get("returncode"),
            "output": _clip((res.get("stderr") or "") + ("\n" + res["stdout"] if res.get("stdout") else ""), 600)}


def http_health(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """GET *url*; returns {ok, status, latency_ms, error, url}. Never raises."""
    import httpx

    started = time.monotonic()
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=False)
        latency = int((time.monotonic() - started) * 1000)
        ok = 200 <= resp.status_code < 300
        return {"ok": ok, "status": resp.status_code, "latency_ms": latency,
                "error": None if ok else f"HTTP {resp.status_code}", "url": url, "checked_at": time.time()}
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "latency_ms": None, "url": url, "checked_at": time.time(),
                "error": _clip(str(exc) or exc.__class__.__name__, 300)}


def resolve(host: str) -> list[str]:
    try:
        return sorted({str(i[4][0]).split("%")[0] for i in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError):
        return []


def _in_nets(addr: str, nets: list[Any]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip.version == n.version and ip in n for n in nets)


def cert_days_left(host: str, port: int = 443, timeout: float = 5.0) -> dict[str, Any]:
    """Return {ok, days_left, not_after, issuer, error} for the TLS certificate of host:port."""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
        not_after = cert.get("notAfter")
        if not not_after:
            return {"ok": False, "days_left": None, "error": "certificate has no expiry"}
        expires = ssl.cert_time_to_seconds(not_after)
        days = int((expires - time.time()) // 86400)
        issuer = ""
        for rdn in cert.get("issuer") or ():
            for key, value in rdn:
                if key in ("organizationName", "commonName"):
                    issuer = value
        return {"ok": days > 7, "days_left": days, "not_after": _dt.datetime.fromtimestamp(expires, tz=_dt.UTC).isoformat(),
                "issuer": issuer, "error": None}
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {"ok": False, "days_left": None, "error": _clip(str(exc), 300)}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[-1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    return True


def _pid_is_cloudflared(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return b"cloudflared" in raw
    except OSError:
        pass
    res = _run(["ps", "-p", str(pid), "-o", "command="], timeout=3)
    return "cloudflared" in (res.get("stdout") or "")


def parse_serve_status(data: Any, https_port: int, local_port: int) -> dict[str, Any]:
    """Interpret ``tailscale serve status --json`` for *our* port only.

    Returns {serve_active, funnel_active, host_port, proxy}. ``serve_active``
    means a handler on ``<host>:<https_port>`` proxies to 127.0.0.1/localhost:<local_port>.
    """
    out = {"serve_active": False, "funnel_active": False, "host_port": None, "proxy": None, "other_target": None}
    if not isinstance(data, dict):
        return out
    targets = {f"http://127.0.0.1:{local_port}", f"http://localhost:{local_port}", f"127.0.0.1:{local_port}",
               f"localhost:{local_port}", f"http://[::1]:{local_port}"}
    webs: list[dict[str, Any]] = []
    if isinstance(data.get("Web"), dict):
        webs.append(data["Web"])
    allow: dict[str, Any] = dict(data.get("AllowFunnel") or {}) if isinstance(data.get("AllowFunnel"), dict) else {}
    for fg in (data.get("Foreground") or {}).values() if isinstance(data.get("Foreground"), dict) else []:
        if isinstance(fg, dict):
            if isinstance(fg.get("Web"), dict):
                webs.append(fg["Web"])
            if isinstance(fg.get("AllowFunnel"), dict):
                allow.update(fg["AllowFunnel"])
    for web in webs:
        for host_port, cfg in web.items():
            if not str(host_port).endswith(f":{https_port}") or not isinstance(cfg, dict):
                continue
            for handler in (cfg.get("Handlers") or {}).values():
                proxy = str((handler or {}).get("Proxy") or "").rstrip("/")
                if proxy in targets:
                    out.update(serve_active=True, host_port=host_port, proxy=proxy)
                    out["funnel_active"] = bool(allow.get(host_port))
                elif proxy:
                    out["other_target"] = proxy
    return out


# --------------------------------------------------------------------------- manager
class IngressManager:
    """Status, apply, diagnostics and process management for ingress paths."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[Any] | None = None
        self._last_error: dict[str, str | None] = {"cloudflare": None, "tailscale": None}

    # ---------------------------------------------------------------- paths / config
    @property
    def cfg_dir(self) -> Path:
        return Path(self.ctx.cfg_dir)

    @property
    def pid_path(self) -> Path:
        return self.cfg_dir / "cloudflared.pid"

    @property
    def log_path(self) -> Path:
        return self.cfg_dir / "cloudflared.log"

    @property
    def yml_path(self) -> Path:
        return self.cfg_dir / "cloudflared.yml"

    @property
    def home(self) -> Path:
        return Path(getattr(self.ctx.identity, "home", "") or os.path.expanduser("~"))

    def _cfg(self, name: str) -> dict[str, Any]:
        return copy.deepcopy(((self.ctx.config.data or {}).get("ingress") or {}).get(name) or {})

    @property
    def local_port(self) -> int:
        return int((self.ctx.config.data or {}).get("port") or 2026)

    def _expand(self, value: str) -> Path:
        raw = str(value).strip()
        if raw == "~" or raw.startswith("~/"):
            raw = str(self.home) + raw[1:]
        return Path(raw)

    # ---------------------------------------------------------------- tailscale
    @staticmethod
    def _tailscale_status() -> dict[str, Any] | None:
        try:
            from ssh_targets import tailscale_status_json

            return tailscale_status_json(timeout=5.0, max_age=2.0)
        except ImportError:  # pragma: no cover - sibling module missing
            res = _run(["tailscale", "status", "--json"], timeout=5)
            try:
                return json.loads(res["stdout"]) if res["ok"] or res["stdout"] else None
            except (json.JSONDecodeError, ValueError):
                return None

    def _invalidate_ts_cache(self) -> None:
        try:
            import ssh_targets

            with ssh_targets._TS_CACHE_LOCK:  # noqa: SLF001 - sibling module cache
                ssh_targets._TS_CACHE.update(ts=0.0, data=None)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass

    def _serve_status(self, https_port: int) -> dict[str, Any]:
        res = _run(["tailscale", "serve", "status", "--json"], timeout=8)
        data: Any = None
        try:
            data = json.loads(res.get("stdout") or "null")
        except (json.JSONDecodeError, ValueError):
            data = None
        return parse_serve_status(data, https_port, self.local_port)

    def tailscale_status(self, check_health: bool = True) -> dict[str, Any]:
        cfg = self._cfg("tailscale")
        mode = cfg.get("mode") or "serve"
        port = int(cfg.get("https_port") or 443)
        out: dict[str, Any] = {
            "enabled": bool(cfg.get("enabled")), "mode": mode, "https_port": port,
            "exposure": "public" if mode == "funnel" else "tailnet",
            "installed": bool(shutil.which("tailscale")), "backend_state": None,
            "dns_name": cfg.get("dns_name"), "self_ips": [], "serve_active": False, "funnel_active": False,
            "peers_count": 0, "public_health": None, "last_error": self._last_error.get("tailscale"),
        }
        if not out["installed"]:
            out["status"] = "not-installed" if out["enabled"] else "disabled"
            return out
        st = self._tailscale_status()
        if isinstance(st, dict):
            self_node = st.get("Self") or {}
            out["backend_state"] = st.get("BackendState")
            dns = str(self_node.get("DNSName") or "").rstrip(".")
            if dns:
                out["dns_name"] = dns
            out["self_ips"] = [str(ip) for ip in (self_node.get("TailscaleIPs") or [])][:4]
            out["peers_count"] = len(st.get("Peer") or {})
        serve = self._serve_status(port)
        out["serve_active"] = serve["serve_active"]
        out["funnel_active"] = serve["funnel_active"]
        if serve.get("other_target") and not serve["serve_active"]:
            out["port_conflict"] = f"https port {port} is served to another target"
        if out["enabled"] and check_health and out["dns_name"]:
            sfx = "" if port == 443 else f":{port}"
            out["public_health"] = http_health(f"https://{out['dns_name']}{sfx}/healthz")
        if not out["enabled"]:
            out["status"] = "disabled"
        elif out["backend_state"] != "Running":
            out["status"] = "backend-down"
        elif not (out["funnel_active"] if mode == "funnel" else out["serve_active"]):
            out["status"] = "not-configured"
        elif out["public_health"] and out["public_health"].get("ok"):
            out["status"] = "healthy"
        else:
            out["status"] = "active" if mode == "serve" else "degraded"
        return out

    def apply_tailscale(self, enabled: bool, mode: str = "serve", https_port: int = 443,
                        confirm_public: bool = False) -> dict[str, Any]:
        """Configure Tailscale Serve/Funnel for Loopback's port only.

        Raises IngressError (a ValueError) on invalid input or command failure;
        configuration is restored on failure.
        """
        mode = str(mode or "serve")
        if mode not in ("serve", "funnel"):
            raise IngressError("mode must be 'serve' (tailnet only) or 'funnel' (public internet)")
        try:
            https_port = int(https_port)
        except (TypeError, ValueError):
            raise IngressError("https_port must be an integer") from None
        if https_port not in TAILSCALE_PORTS:
            raise IngressError("https_port must be 443, 8443 or 10000")
        if enabled and mode == "funnel" and not confirm_public:
            raise IngressError("Funnel exposes Loopback to the public internet. Resend with confirm_public=true to confirm")
        prev = self._cfg("tailscale")
        prev_port = int(prev.get("https_port") or 443)
        commands: list[dict[str, Any]] = []
        local = f"http://127.0.0.1:{self.local_port}"
        with self._lock:
            if enabled and not shutil.which("tailscale"):
                raise IngressError("tailscale is not installed", self.diagnostics("tailscale"))
            if enabled:
                st = self._tailscale_status() or {}
                if st.get("BackendState") != "Running":
                    raise IngressError(f"Tailscale backend is not running (state: {st.get('BackendState') or 'unknown'})",
                                       self.diagnostics("tailscale"))
                current = self._serve_status(https_port)
                self._assert_ts_ownership(https_port, allow_owned=bool(prev.get("enabled") and prev_port == https_port))
                # Turn off our previous handler if the port moved.
                if prev.get("enabled") and prev_port != https_port:
                    commands += self._ts_off(prev_port)
                if mode == "serve" and current.get("funnel_active"):
                    commands.append(_cmd_summary(_run(["tailscale", "funnel", f"--https={https_port}", "off"])))
                res = _run(["tailscale", mode, "--bg", f"--https={https_port}", local], timeout=30)
                commands.append(_cmd_summary(res))
                if not res["ok"]:
                    msg = f"tailscale {mode} failed: {_clip(res['stderr'] or res['stdout'], 400)}"
                    self._last_error["tailscale"] = msg
                    self._restore_tailscale(prev)
                    raise IngressError(msg, self.diagnostics("tailscale"))
                self._invalidate_ts_cache()
                st = self._tailscale_status() or {}
                dns_name = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".") or prev.get("dns_name")
                updates = {"enabled": True, "mode": mode, "https_port": https_port, "dns_name": dns_name}
            else:
                commands += self._ts_off(prev_port)
                updates = {"enabled": False, "mode": mode, "https_port": https_port, "dns_name": prev.get("dns_name")}
            marker = self._backup_marker()
            try:
                self.ctx.config.save({"ingress": {"tailscale": updates}}, actor="dashboard")
                self.ctx.refresh_auth_urls()
            except Exception as exc:  # noqa: BLE001
                self._restore_section("tailscale", prev, marker)
                if enabled:
                    self._ts_off(https_port)
                    self._restore_tailscale(prev)
                raise IngressError(f"could not save tailscale configuration: {exc}") from exc
            self._last_error["tailscale"] = None
        verification: dict[str, Any] = {"state": "disabled"}
        if enabled and updates.get("dns_name"):
            sfx = "" if https_port == 443 else f":{https_port}"
            health = http_health(f"https://{updates['dns_name']}{sfx}/healthz", timeout=8)
            verification = {"state": "ok" if health["ok"] else "unverified", "health": health}
            if not health["ok"]:
                verification["note"] = ("Serve endpoints are reachable inside the tailnet; the gateway host may not be "
                                        "able to reach its own MagicDNS name. Verify from another tailnet device.")
        elif enabled:
            verification = {"state": "unverified", "note": "MagicDNS name unknown"}
        return {"ok": True, "commands": commands, "verification": verification,
                "tailscale": self.tailscale_status(check_health=False)}

    def _assert_ts_ownership(self, port: int, *, allow_owned: bool) -> None:
        from node_agent import listeners

        response = _run(["tailscale", "serve", "status", "--json"], timeout=8)
        if not response["ok"]:
            raise IngressError("Cannot inspect existing Tailscale listeners; refusing to change them")
        try:
            configured = listeners(json.loads(response["stdout"] or "{}"), port)
        except (ValueError, TypeError):
            raise IngressError("Invalid Tailscale listener status; refusing to change it") from None
        expected = {"/": {"Proxy": f"http://127.0.0.1:{self.local_port}"}}
        if configured and (not allow_owned or not all(web.get("Handlers") == expected for _, web in configured)):
            raise IngressError("Tailscale port is occupied by an existing listener; choose another port. Existing routes are protected")

    def _ts_off(self, port: int) -> list[dict[str, Any]]:
        """Remove only our Serve/Funnel handler on *port* (errors are tolerated)."""
        out: list[dict[str, Any]] = []
        if not shutil.which("tailscale"):
            return out
        current = self._serve_status(port)
        self._assert_ts_ownership(port, allow_owned=True)
        if current.get("funnel_active"):
            out.append(_cmd_summary(_run(["tailscale", "funnel", f"--https={port}", "off"])))
        if current.get("serve_active"):  # only when the handler on this port is ours
            out.append(_cmd_summary(_run(["tailscale", "serve", f"--https={port}", "off"])))
        self._invalidate_ts_cache()
        return out

    def _restore_tailscale(self, prev: dict[str, Any]) -> None:
        if not prev.get("enabled"):
            return
        mode = prev.get("mode") or "serve"
        port = int(prev.get("https_port") or 443)
        _run(["tailscale", mode, "--bg", f"--https={port}", f"http://127.0.0.1:{self.local_port}"], timeout=30)
        self._invalidate_ts_cache()

    def _backup_marker(self) -> tuple[tuple[str, int], ...]:
        """Snapshot of config backups, used to tell whether our save created one."""
        bdir = getattr(self.ctx.config, "backup_dir", None)
        if not bdir:
            return ()
        try:
            return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in Path(bdir).glob("config-*.json")))
        except OSError:
            return ()

    def _restore_section(self, name: str, prev: dict[str, Any], marker: tuple[tuple[str, int], ...] | None) -> None:
        """Restore ``ingress.<name>`` to *prev*: ConfigStore.rollback() when our save made a backup,
        otherwise (or if the rollback result differs) save the previous section explicitly."""
        if marker is not None and self._backup_marker() != marker:
            try:
                self.ctx.config.rollback()
            except Exception:  # noqa: BLE001 - fall through to explicit restore
                pass
        if self._cfg(name) != prev:
            try:
                self.ctx.config.save({"ingress": {name: prev}}, actor="dashboard")
            except Exception:  # noqa: BLE001
                pass
        try:
            self.ctx.refresh_auth_urls()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- cloudflare process
    def _read_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def cloudflared_pid(self) -> int | None:
        """PID of the managed cloudflared if it is running, else None."""
        with self._lock:
            if self._proc is not None:
                if self._proc.poll() is None:
                    return self._proc.pid
                self._proc = None
            pid = self._read_pid()
            if pid and _pid_alive(pid) and _pid_is_cloudflared(pid):
                return pid
            return None

    def _rotate_log(self) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_MAX_BYTES:
                os.replace(self.log_path, self.log_path.with_name(self.log_path.name + ".1"))
        except OSError:
            pass

    def _cloudflared_cmd(self, cfg: dict[str, Any]) -> list[str]:
        binary = shutil.which("cloudflared")
        if not binary:
            raise IngressError("cloudflared is not installed")
        if cfg.get("token_file"):
            tf = self._expand(cfg["token_file"])
            if not tf.is_file() or not os.access(tf, os.R_OK):
                raise IngressError(f"token_file is missing or unreadable: {tf}")
            return [binary, "tunnel", "--no-autoupdate", "run", "--token-file", str(tf)]
        tid = cfg.get("tunnel_id")
        conf = Path(cfg.get("config_file") or self.yml_path)
        if not tid or not conf.is_file():
            raise IngressError("cloudflare needs token_file or tunnel_id with a generated cloudflared.yml")
        return [binary, "tunnel", "--no-autoupdate", "--config", str(conf), "run", str(tid)]

    def start_cloudflare(self) -> dict[str, Any]:
        """Start the detached cloudflared process if it is not running."""
        with self._lock:
            pid = self.cloudflared_pid()
            if pid:
                return {"ok": True, "pid": pid, "already_running": True}
            cfg = self._cfg("cloudflare")
            try:
                cmd = self._cloudflared_cmd(cfg)
            except IngressError as exc:
                self._last_error["cloudflare"] = str(exc)
                return {"ok": False, "error": str(exc)}
            self._rotate_log()
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.chmod(self.log_path, 0o600)
                kwargs: dict[str, Any] = {}
                if sys.platform != "win32":
                    kwargs["start_new_session"] = True
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, cwd=str(self.cfg_dir),
                                        close_fds=True, **kwargs)
            except OSError as exc:
                self._last_error["cloudflare"] = f"failed to start cloudflared: {exc}"
                return {"ok": False, "error": self._last_error["cloudflare"]}
            finally:
                os.close(fd)
            self._proc = proc
            _atomic_write(self.pid_path, f"{proc.pid}\n")
            time.sleep(0.5)
            if proc.poll() is not None:
                err = self._log_errors(5) or [f"cloudflared exited with code {proc.returncode}"]
                self._last_error["cloudflare"] = err[-1]
                self._proc = None
                return {"ok": False, "error": err[-1], "log": err}
            self._last_error["cloudflare"] = None
            return {"ok": True, "pid": proc.pid}

    def stop_cloudflare(self, timeout: float = 8.0) -> dict[str, Any]:
        """Stop the managed cloudflared process (SIGTERM, then SIGKILL)."""
        with self._lock:
            pid = self.cloudflared_pid()
            stopped = False
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self._proc is not None and self._proc.pid == pid:
                        if self._proc.poll() is not None:
                            break
                    elif not _pid_alive(pid):
                        break
                    time.sleep(0.2)
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if self._proc is not None:
                        try:
                            self._proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                stopped = True
            self._proc = None
            try:
                self.pid_path.unlink()
            except OSError:
                pass
            return {"ok": True, "stopped": stopped, "pid": pid}

    def restart(self, name: str) -> dict[str, Any]:
        """Restart an ingress path: 'cloudflare' (process) or 'tailscale' (re-apply handler)."""
        if name == "cloudflare":
            if not self._cfg("cloudflare").get("enabled"):
                raise IngressError("Cloudflare ingress is not enabled")
            self.stop_cloudflare()
            res = self.start_cloudflare()
            if not res.get("ok"):
                raise IngressError(res.get("error") or "cloudflared failed to start", self.diagnostics("cloudflare"))
            return {**res, "cloudflare": self.cloudflare_status(check_health=False)}
        if name == "tailscale":
            cfg = self._cfg("tailscale")
            if not cfg.get("enabled"):
                raise IngressError("Tailscale ingress is not enabled")
            return self.apply_tailscale(True, cfg.get("mode") or "serve", int(cfg.get("https_port") or 443),
                                        confirm_public=(cfg.get("mode") == "funnel"))
        raise KeyError(f"unknown ingress: {name}")

    def _log_errors(self, limit: int = 5) -> list[str]:
        try:
            size = self.log_path.stat().st_size
            with open(self.log_path, "rb") as f:
                f.seek(max(0, size - 65536))
                tail = f.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return []
        errs = [_clip(line, 300) for line in tail if " ERR " in line or "error" in line.lower()]
        return errs[-limit:]

    def _log_has_connection(self) -> bool:
        try:
            size = self.log_path.stat().st_size
            with open(self.log_path, "rb") as f:
                f.seek(max(0, size - 65536))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False
        idx = tail.rfind("Registered tunnel connection")
        return idx >= 0 and idx > tail.rfind("Starting tunnel")

    # ---------------------------------------------------------------- cloudflare config
    def cloudflare_status(self, check_health: bool = True) -> dict[str, Any]:
        cfg = self._cfg("cloudflare")
        tid = str(cfg.get("tunnel_id") or "")
        host = cfg.get("hostname")
        pid = self.cloudflared_pid()
        token_present = bool(cfg.get("token_file")) and self._expand(cfg["token_file"]).is_file()
        conf = Path(cfg.get("config_file") or self.yml_path)
        out: dict[str, Any] = {
            "enabled": bool(cfg.get("enabled")), "hostname": host, "tunnel_id_prefix": tid[:8] or None,
            "access_policy": cfg.get("access_policy") or "oauth",
            "exposure": "access-protected" if cfg.get("access_policy") == "cloudflare-access" else "public",
            "installed": bool(shutil.which("cloudflared")), "running": bool(pid), "pid": pid,
            "config_file": conf.is_file(), "token_file": token_present,
            "credentials_file": bool(tid) and (self.home / ".cloudflared" / f"{tid}.json").is_file(),
            "last_error": self._last_error.get("cloudflare"), "dns_ok": None, "public_health": None,
        }
        if not out["last_error"] and out["enabled"] and not pid:
            errs = self._log_errors(1)
            out["last_error"] = errs[-1] if errs else None
        if out["enabled"] and host and check_health:
            addrs = resolve(str(host))
            out["dns_ok"] = bool(addrs)
            out["public_health"] = http_health(f"https://{host}/healthz") if addrs else {
                "ok": False, "status": None, "latency_ms": None, "error": "hostname does not resolve",
                "url": f"https://{host}/healthz", "checked_at": time.time()}
        if not out["enabled"]:
            out["status"] = "disabled"
        elif not out["installed"]:
            out["status"] = "not-installed"
        elif not pid:
            out["status"] = "stopped"
        elif out["public_health"] is None:
            out["status"] = "running"
        else:
            out["status"] = "healthy" if out["public_health"].get("ok") else "degraded"
        return out

    def _write_cloudflared_yml(self, tunnel_id: str, hostname: str) -> Path:
        creds = self.home / ".cloudflared" / f"{tunnel_id}.json"
        lines = [
            "# Generated by Loopback. Changes are overwritten when ingress is reconfigured.",
            f"tunnel: {json.dumps(tunnel_id)}",
        ]
        if creds.is_file():
            lines.append(f"credentials-file: {json.dumps(str(creds))}")
        lines += [
            "ingress:",
            f"  - hostname: {json.dumps(hostname)}",
            f"    service: {json.dumps(f'http://127.0.0.1:{self.local_port}')}",
            "  - service: http_status:404",
            "",
        ]
        _atomic_write(self.yml_path, "\n".join(lines))
        return self.yml_path

    def apply_cloudflare(self, enabled: bool, hostname: str | None = None, tunnel_id: str | None = None,
                         token_file: str | None = None, access_policy: str = "oauth") -> dict[str, Any]:
        """Validate, save and (re)start the Cloudflare tunnel; restore config on failure.

        Raises IngressError (ValueError) with ``diagnostics`` on any failure.
        """
        prev = self._cfg("cloudflare")
        with self._lock:
            if not enabled:
                stop = self.stop_cloudflare()
                try:
                    self.ctx.config.save({"ingress": {"cloudflare": {"enabled": False}}}, actor="dashboard")
                    self.ctx.refresh_auth_urls()
                except ValueError as exc:
                    raise IngressError(str(exc)) from exc
                return {"ok": True, "stopped": stop, "cloudflare": self.cloudflare_status(check_health=False)}

            host = str(hostname or "").strip().lower().rstrip(".")
            if not FQDN_RE.match(host):
                raise IngressError("hostname must be a fully-qualified domain name (e.g. loopback.example.com)")
            tid = str(tunnel_id).strip().lower() if tunnel_id else None
            if tid and not UUID_RE.match(tid):
                raise IngressError("tunnel_id must be a tunnel UUID")
            tf: str | None = None
            if token_file:
                tpath = self._expand(str(token_file))
                if not tpath.is_absolute():
                    raise IngressError("token_file must be an absolute path (or start with ~/)")
                if not tpath.is_file() or not os.access(tpath, os.R_OK):
                    raise IngressError(f"token_file does not exist or is not readable: {tpath}")
                tf = str(tpath)
            if not tid and not tf:
                raise IngressError("provide tunnel_id (named tunnel) or token_file")
            if access_policy not in ACCESS_POLICIES:
                raise IngressError("access_policy must be oauth, cloudflare-access or both")
            if not shutil.which("cloudflared"):
                raise IngressError("cloudflared is not installed", self.diagnostics("cloudflare"))
            if tid and not tf and not (self.home / ".cloudflared" / f"{tid}.json").is_file():
                raise IngressError(f"tunnel credentials ~/.cloudflared/{tid[:8]}….json not found. Run "
                                   "'cloudflared tunnel login' and 'cloudflared tunnel create', or provide token_file",
                                   self.diagnostics("cloudflare"))
            config_file = str(self._write_cloudflared_yml(tid, host)) if tid else None
            updates = {"enabled": True, "hostname": host, "tunnel_id": tid, "token_file": tf,
                       "access_policy": access_policy, "config_file": config_file}
            marker = self._backup_marker()
            try:
                self.ctx.config.save({"ingress": {"cloudflare": updates}}, actor="dashboard")
            except ValueError as exc:
                raise IngressError(str(exc)) from exc
            self.ctx.refresh_auth_urls()

            self.stop_cloudflare()
            start = self.start_cloudflare()
            health: dict[str, Any] | None = None
            failure: str | None = None
            if not start.get("ok"):
                failure = start.get("error") or "cloudflared failed to start"
            else:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if not self.cloudflared_pid():
                        errs = self._log_errors(3)
                        failure = errs[-1] if errs else "cloudflared exited"
                        break
                    health = http_health(f"https://{host}/healthz", timeout=4)
                    if health["ok"]:
                        break
                    time.sleep(2)
                if failure is None and not (health and health["ok"]) and not self._log_has_connection():
                    failure = ("tunnel did not become healthy within 20s: "
                               + ((health or {}).get("error") or "no tunnel connection registered"))
            if failure:
                diags = self.diagnostics("cloudflare")
                self._last_error["cloudflare"] = _clip(failure, 400)
                self.stop_cloudflare()
                self._restore_section("cloudflare", prev, marker)
                if prev.get("enabled"):
                    self.start_cloudflare()
                raise IngressError(f"Cloudflare tunnel failed, previous configuration restored: {_clip(failure, 400)}",
                                   diags)
        verification = {"state": "ok" if health and health.get("ok") else "unverified", "health": health}
        if verification["state"] == "unverified":
            verification["note"] = "tunnel connected but public health not yet reachable (DNS may still be propagating)"
        return {"ok": True, "verification": verification, "cloudflare": self.cloudflare_status(check_health=False)}

    def list_tunnels(self) -> dict[str, Any]:
        """``cloudflared tunnel list --output json`` as {ok, tunnels:[{id_prefix,id,name,created_at,connections}]}."""
        res = _run(["cloudflared", "tunnel", "list", "--output", "json"], timeout=20)
        if not res["ok"]:
            return {"ok": False, "error": _clip(res["stderr"] or "cloudflared tunnel list failed", 400)}
        try:
            rows = json.loads(res["stdout"] or "[]")
        except (json.JSONDecodeError, ValueError):
            return {"ok": False, "error": "unexpected cloudflared output"}
        tunnels = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                tunnels.append({"id": row.get("id"), "id_prefix": str(row.get("id") or "")[:8], "name": row.get("name"),
                                "created_at": row.get("created_at"), "connections": len(row.get("connections") or [])})
        return {"ok": True, "tunnels": tunnels[:200]}

    def create_tunnel(self, name: str) -> dict[str, Any]:
        """``cloudflared tunnel create NAME``; returns {ok, id, name, credentials_file_present}. Never returns credentials."""
        if not TUNNEL_NAME_RE.match(str(name or "")):
            return {"ok": False, "error": "tunnel name must be 1-63 letters, digits, '.', '_' or '-'"}
        res = _run(["cloudflared", "tunnel", "--output", "json", "create", str(name)], timeout=30)
        if not res["ok"]:
            return {"ok": False, "error": _clip(res["stderr"] or res["stdout"] or "cloudflared tunnel create failed", 400)}
        tid = None
        try:
            doc = json.loads(res["stdout"] or "{}")
            tid = doc.get("id") if isinstance(doc, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass
        if not tid:
            m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", res["stdout"] + res["stderr"], re.I)
            tid = m.group(0) if m else None
        return {"ok": bool(tid), "id": tid, "name": name, "error": None if tid else "could not parse tunnel id",
                "credentials_file_present": bool(tid) and (self.home / ".cloudflared" / f"{tid}.json").is_file()}

    def route_dns(self, tunnel_id: str, hostname: str) -> dict[str, Any]:
        """``cloudflared tunnel route dns ID HOSTNAME`` as {ok, output|error}."""
        if not UUID_RE.match(str(tunnel_id or "")):
            return {"ok": False, "error": "tunnel_id must be a tunnel UUID"}
        if not FQDN_RE.match(str(hostname or "")):
            return {"ok": False, "error": "hostname must be a fully-qualified domain name"}
        res = _run(["cloudflared", "tunnel", "route", "dns", str(tunnel_id), str(hostname)], timeout=30)
        if not res["ok"]:
            return {"ok": False, "error": _clip(res["stderr"] or res["stdout"] or "route dns failed", 400)}
        return {"ok": True, "output": _clip(res["stderr"] + res["stdout"], 400)}

    # ---------------------------------------------------------------- aggregate status
    def status(self, check_health: bool = True) -> dict[str, Any]:
        """Ingress status as documented for ``GET /api/admin/ingress``."""
        local = (self.ctx.config.data or {}).get("ingress", {}).get("local", {}) or {}
        return {
            "local": {"enabled": bool(local.get("enabled", True)), "url": f"http://127.0.0.1:{self.local_port}",
                      "bind": (self.ctx.config.data or {}).get("bind"), "exposure": "local"},
            "cloudflare": self.cloudflare_status(check_health=check_health),
            "tailscale": self.tailscale_status(check_health=check_health),
        }

    def ensure_started(self) -> dict[str, Any]:
        """Server-start hook: launch cloudflared if enabled and not running. Never raises."""
        try:
            if self._cfg("cloudflare").get("enabled") and not self.cloudflared_pid():
                return self.start_cloudflare()
            return {"ok": True, "action": "none"}
        except Exception as exc:  # noqa: BLE001
            self._last_error["cloudflare"] = _clip(str(exc), 300)
            return {"ok": False, "error": self._last_error["cloudflare"]}

    # ---------------------------------------------------------------- diagnostics
    def _local_port_check(self) -> dict[str, Any]:
        try:
            with socket.create_connection(("127.0.0.1", self.local_port), timeout=2):
                return {"name": "Local listener", "ok": True, "detail": f"127.0.0.1:{self.local_port} accepts connections",
                        "fix": None}
        except OSError as exc:
            return {"name": "Local listener", "ok": False, "detail": f"127.0.0.1:{self.local_port}: {exc}",
                    "fix": "Start Loopback (loopback start) and check server.log"}

    def diagnostics(self, name: str) -> list[dict[str, Any]]:
        """Human-readable checks ``[{name, ok, detail, fix}]`` for 'cloudflare' or 'tailscale'."""
        if name == "cloudflare":
            return self._cloudflare_diagnostics()
        if name == "tailscale":
            return self._tailscale_diagnostics()
        raise KeyError(f"unknown ingress: {name}")

    def _cloudflare_diagnostics(self) -> list[dict[str, Any]]:
        cfg = self._cfg("cloudflare")
        checks: list[dict[str, Any]] = []
        installed = bool(shutil.which("cloudflared"))
        ver = _run(["cloudflared", "--version"], timeout=5) if installed else {}
        checks.append({"name": "cloudflared installed", "ok": installed,
                       "detail": _clip(ver.get("stdout") or ver.get("stderr") or "", 120) if installed else "not found in PATH",
                       "fix": None if installed else "Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"})
        tid = str(cfg.get("tunnel_id") or "")
        cert = (self.home / ".cloudflared" / "cert.pem").is_file()
        checks.append({"name": "Cloudflare login (cert.pem)", "ok": cert or bool(cfg.get("token_file")),
                       "detail": "origin certificate present" if cert else ("not needed with token_file" if cfg.get("token_file") else "~/.cloudflared/cert.pem missing"),
                       "fix": None if cert or cfg.get("token_file") else "Run: cloudflared tunnel login"})
        if cfg.get("token_file"):
            tf = self._expand(cfg["token_file"])
            ok = tf.is_file() and os.access(tf, os.R_OK)
            checks.append({"name": "Tunnel token file", "ok": ok, "detail": "present and readable" if ok else "missing or unreadable",
                           "fix": None if ok else "Save the tunnel token to a file (mode 0600) and set token_file"})
        elif tid:
            creds = (self.home / ".cloudflared" / f"{tid}.json").is_file()
            checks.append({"name": "Tunnel credentials", "ok": creds,
                           "detail": f"credentials for tunnel {tid[:8]}… {'present' if creds else 'missing'}",
                           "fix": None if creds else "Run: cloudflared tunnel create <name> (or provide token_file)"})
            conf = Path(cfg.get("config_file") or self.yml_path).is_file()
            checks.append({"name": "Tunnel config", "ok": conf, "detail": "cloudflared.yml present" if conf else "cloudflared.yml missing",
                           "fix": None if conf else "Re-save the Cloudflare settings in the dashboard"})
        else:
            checks.append({"name": "Tunnel credentials", "ok": False, "detail": "no tunnel_id or token_file configured",
                           "fix": "Configure a named tunnel id or a token file"})
        pid = self.cloudflared_pid()
        errs = self._log_errors(3)
        checks.append({"name": "Tunnel process", "ok": bool(pid),
                       "detail": f"running (pid {pid})" if pid else ("stopped" + (f": {errs[-1]}" if errs else "")),
                       "fix": None if pid else "Restart the tunnel from the dashboard; see cloudflared.log"})
        if pid:
            conn = self._log_has_connection()
            checks.append({"name": "Edge connection", "ok": conn,
                           "detail": "tunnel connection registered" if conn else "no registered connection in recent log",
                           "fix": None if conn else "Check outbound connectivity to Cloudflare (port 7844) and the token/credentials"})
        checks.append(self._local_port_check())
        host = cfg.get("hostname")
        if host:
            addrs = resolve(str(host))
            cf_ok = bool(addrs) and all(_in_nets(a, CLOUDFLARE_NETS) for a in addrs)
            checks.append({"name": "DNS", "ok": cf_ok,
                           "detail": (f"{host} -> {', '.join(addrs[:4])}" + ("" if cf_ok else " (not Cloudflare edge addresses)")) if addrs else f"{host} does not resolve",
                           "fix": None if cf_ok else (f"Run: cloudflared tunnel route dns {tid[:8] + '…' if tid else '<tunnel>'} {host} "
                                                      "(or create a proxied CNAME to <tunnel-id>.cfargotunnel.com)")})
            if addrs:
                health = http_health(f"https://{host}/healthz")
                checks.append({"name": "Public health", "ok": health["ok"],
                               "detail": f"HTTP {health['status']} in {health['latency_ms']} ms" if health["status"] else health["error"],
                               "fix": None if health["ok"] else "Check the tunnel ingress rule points to the local listener"})
                cert_info = cert_days_left(str(host))
                checks.append({"name": "TLS certificate", "ok": bool(cert_info["ok"]),
                               "detail": (f"{cert_info['days_left']} days left (issuer {cert_info.get('issuer') or 'unknown'})"
                                          if cert_info.get("days_left") is not None else cert_info.get("error")),
                               "days_left": cert_info.get("days_left"),
                               "fix": None if cert_info["ok"] else "Check the Cloudflare edge certificate for this hostname"})
        else:
            checks.append({"name": "Hostname", "ok": False, "detail": "no public hostname configured",
                           "fix": "Set a hostname in the Cloudflare ingress settings"})
        exposure = "access-protected" if cfg.get("access_policy") == "cloudflare-access" else "public"
        checks.append({"name": "Exposure", "ok": True, "detail": f"{exposure}: a Cloudflare tunnel is reachable from the "
                       "internet; clients still authenticate with Loopback OAuth/API tokens", "fix": None})
        return checks

    def _tailscale_diagnostics(self) -> list[dict[str, Any]]:
        cfg = self._cfg("tailscale")
        mode = cfg.get("mode") or "serve"
        port = int(cfg.get("https_port") or 443)
        checks: list[dict[str, Any]] = []
        installed = bool(shutil.which("tailscale"))
        checks.append({"name": "tailscale installed", "ok": installed,
                       "detail": "found in PATH" if installed else "not found in PATH",
                       "fix": None if installed else "Install Tailscale: https://tailscale.com/download"})
        if not installed:
            checks.append(self._local_port_check())
            return checks
        st = self._tailscale_status() or {}
        state = st.get("BackendState")
        checks.append({"name": "Logged in", "ok": state not in (None, "NeedsLogin", "NoState"),
                       "detail": f"backend state: {state or 'unknown'}",
                       "fix": None if state not in (None, "NeedsLogin", "NoState") else "Run: tailscale up"})
        checks.append({"name": "Backend running", "ok": state == "Running", "detail": f"backend state: {state or 'unknown'}",
                       "fix": None if state == "Running" else "Run: tailscale up (or start tailscaled)"})
        dns = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
        checks.append({"name": "MagicDNS name", "ok": bool(dns), "detail": dns or "no MagicDNS name",
                       "fix": None if dns else "Enable MagicDNS and HTTPS certificates in the Tailscale admin console"})
        serve = self._serve_status(port)
        active = serve["funnel_active"] if mode == "funnel" else serve["serve_active"]
        user = getattr(self.ctx.identity, "username", "") or "$USER"
        fix = None
        if not active:
            fix = (f"Re-apply from the dashboard. If tailscale reports 'Access denied', run: sudo tailscale set --operator={user}"
                   + ("; Funnel must also be allowed in the tailnet policy (nodeAttrs funnel)" if mode == "funnel" else ""))
        checks.append({"name": f"{mode.capitalize()} handler", "ok": bool(active),
                       "detail": (f"https://{serve['host_port']} -> {serve['proxy']}" if serve["serve_active"]
                                  else (f"port {port} serves another target ({serve['other_target']})" if serve.get("other_target")
                                        else f"no handler for port {port}")),
                       "fix": fix})
        if mode == "serve" and serve["funnel_active"]:
            checks.append({"name": "Exposure mismatch", "ok": False, "detail": "Funnel is enabled but configured mode is serve",
                           "fix": f"Run: tailscale funnel --https={port} off"})
        checks.append(self._local_port_check())
        if dns:
            sfx = "" if port == 443 else f":{port}"
            health = http_health(f"https://{dns}{sfx}/healthz")
            ok = bool(health["ok"])
            checks.append({"name": "Health via MagicDNS", "ok": ok or mode == "serve",
                           "detail": (f"HTTP {health['status']} in {health['latency_ms']} ms" if health["status"]
                                      else ("unverified from this host: " if mode == "serve" else "") + str(health["error"])),
                           "fix": None if ok or mode == "serve" else "Check Funnel is permitted and the handler is active"})
            cert_info = cert_days_left(dns, port)
            if cert_info.get("days_left") is not None or mode == "funnel":
                checks.append({"name": "TLS certificate", "ok": bool(cert_info["ok"]),
                               "detail": (f"{cert_info['days_left']} days left" if cert_info.get("days_left") is not None
                                          else cert_info.get("error")),
                               "days_left": cert_info.get("days_left"),
                               "fix": None if cert_info["ok"] else "Tailscale renews certificates automatically; check tailscaled logs"})
            addrs = resolve(dns)
            if addrs:
                in_tailnet = any(_in_nets(a, TAILSCALE_NETS) for a in addrs)
                checks.append({"name": "DNS", "ok": True, "detail": f"{dns} -> {', '.join(addrs[:4])}"
                               + (" (tailnet address)" if in_tailnet else " (public Funnel relay)"), "fix": None})
        checks.append({"name": "Exposure", "ok": True,
                       "detail": "public: anyone on the internet can reach the endpoint" if mode == "funnel"
                       else "tailnet: only devices allowed by your tailnet ACLs can reach the endpoint", "fix": None})
        return checks
