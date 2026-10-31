"""Staged host discovery and enrollment (DISCOVERY-DESIGN.md, ledger H18/H19).

This module extends, and does not replace, ``discovery.py`` (deep route inventory
from installed workers) and ``ssh_targets.py`` (registered SSH destinations).

Stages implemented here:

* S0 scope      — local host, saved records, exclusions. Excluded identities are never contacted.
* S1 providers  — Tailscale, Cloudflare Mesh, OpenSSH config, manual entries, SSH targets, workers.
* S2 candidates — provider-visible devices and aliases, merged into hosts with provenance.
* S3 reach      — selected hosts only: DNS, TCP/SSH, host key, authentication as separate axes.
* S4 bootstrap  — the fixed read-only metadata script from ``ssh_targets`` on approved SSH access.
* S5 enrollment — a previewed plan; apply registers an SSH target and hands worker install to Fleet.
* S6/S7         — delegated to ``DiscoveryService`` (worker inventory and route classification).

Listing (S0–S2) never runs a remote command. Probing (S3/S4) runs only for hosts the
operator selects. Nothing installs a worker or package without an applied plan.

Every external command goes through an injectable ``Runner`` so tests and the later
installation lab can replay sanitized fixtures (LAB-ROADMAP.md, "Prepare now" 3).
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

SCHEMA_VERSION = 1
STAGES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")
STAGE_TITLES = {
    "S0": "Scope", "S1": "Local providers", "S2": "Candidates", "S3": "Reachability", "S4": "Bootstrap inventory",
    "S5": "Enrollment", "S6": "Deep inventory", "S7": "Correlation", "S8": "Verification", "S9": "Review",
}
# Host states shown in the dashboard. They are deliberately distinct (DASHBOARD-UX.md §3).
HOST_STATES = ("local", "new", "existing", "excluded", "offline", "stale", "auth_required", "host_key_review",
               "human_check", "enrollment_required", "failed", "reachable")
DEFAULT_LIMITS = {"probe_concurrency": 4, "enroll_concurrency": 2, "command_timeout": 8.0, "scan_timeout": 120.0,
                  "observation_ttl": 900.0, "max_candidates": 500, "max_output": 65536}
HOST_TOKEN = re.compile(r"^[A-Za-z0-9_.:%-]{1,253}$")
ALIAS_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
USER_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- injection seams
@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class Runner(Protocol):
    def __call__(self, argv: list[str], timeout: float, stdin: str | None = None) -> CommandResult: ...


def system_runner(argv: list[str], timeout: float, stdin: str | None = None) -> CommandResult:
    import shutil
    import subprocess

    if not shutil.which(argv[0]):
        return CommandResult(127, "", f"{argv[0]}: command not found")
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                              stdin=None if stdin is not None else subprocess.DEVNULL, errors="replace")
        return CommandResult(proc.returncode, proc.stdout[: DEFAULT_LIMITS["max_output"]], proc.stderr[:8192])
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timed out", timed_out=True)
    except OSError as exc:
        return CommandResult(126, "", str(exc)[:500])


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(i[4][0]).split("%", 1)[0] for i in infos})


@dataclass
class ScanEnv:
    """Everything the scanner touches outside its own state. Replace members in tests."""
    runner: Runner = system_runner
    resolver: Callable[[str], list[str]] = system_resolver
    clock: Callable[[], float] = time.time
    read_text: Callable[[Path], str] = lambda p: p.read_text(encoding="utf-8", errors="replace")
    home: Path = field(default_factory=Path.home)


def observation(subject: str, kind: str, result: str, *, source: str, env: ScanEnv, error: str | None = None,
                ttl: float | None = None, detail: dict[str, Any] | None = None, duration_ms: int | None = None) -> dict[str, Any]:
    """One timestamped fact. Absence of an observation is never proof of absence."""
    now = env.clock()
    return {"subject": subject, "kind": kind, "result": result, "error": error, "source": source, "observed_at": now,
            "expires_at": now + (ttl if ttl is not None else DEFAULT_LIMITS["observation_ttl"]),
            "duration_ms": duration_ms, "detail": detail or {}}


def freshness(obs: dict[str, Any] | None, now: float) -> str:
    if not obs:
        return "unknown"
    return "fresh" if now <= float(obs.get("expires_at") or 0) else "stale"


# --------------------------------------------------------------------------- providers (S1/S2)
class CandidateProvider(Protocol):
    name: str

    def detect(self, env: ScanEnv) -> dict[str, Any]: ...

    def list_candidates(self, env: ScanEnv) -> list[dict[str, Any]]: ...


def candidate(provider: str, record_id: str, *, name: str, env: ScanEnv, account: str | None = None,
              addresses: Iterable[str] = (), dns_name: str | None = None, online: bool | None = None,
              os_name: str | None = None, ssh: dict[str, Any] | None = None, kind: str = "unknown",
              confidence: str = "observed", evidence: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    now = env.clock()
    return {
        "provider": provider, "record_id": record_id, "account": account, "name": name, "dns_name": dns_name,
        "addresses": sorted({a for a in addresses if a}), "online": online, "os": os_name, "kind": kind,
        "ssh": ssh, "observed_at": now, "expires_at": now + DEFAULT_LIMITS["observation_ttl"],
        "confidence": confidence, "evidence": evidence[:300], **(extra or {}),
    }


class TailscaleProvider:
    """``tailscale status --json``. Visibility is limited to the current account and ACL."""
    name = "tailscale"

    def __init__(self) -> None:
        self._status: dict[str, Any] | None = None

    def _load(self, env: ScanEnv) -> tuple[dict[str, Any], dict[str, Any] | None]:
        res = env.runner(["tailscale", "status", "--json"], 6.0)
        if res.returncode == 127:
            return {"state": "missing_binary", "remedy": "Install Tailscale or choose another connectivity provider."}, None
        if res.timed_out:
            return {"state": "unavailable", "error": "tailscale status timed out", "remedy": "Check tailscaled."}, None
        text = (res.stdout or "").strip()
        if res.returncode and not text:
            low = (res.stderr or "").lower()
            if "failed to connect" in low or "doesn't appear to be running" in low or "is tailscaled running" in low:
                return {"state": "daemon_down", "remedy": "Start tailscaled (for example: sudo systemctl start tailscaled)."}, None
            return {"state": "unavailable", "error": (res.stderr or "")[:200]}, None
        try:
            data = json.loads(text)
        except ValueError:
            return {"state": "malformed", "error": "tailscale returned output that is not JSON",
                    "remedy": "Update Tailscale; Loopback needs status --json."}, None
        if not isinstance(data, dict):
            return {"state": "malformed", "error": "unexpected JSON shape"}, None
        backend = str(data.get("BackendState") or "Unknown")
        if backend in {"NeedsLogin", "NeedsMachineAuth", "NoState"}:
            return {"state": "unenrolled", "backend_state": backend, "remedy": "Run 'tailscale up' and approve this device."}, data
        if backend == "Stopped":
            return {"state": "stopped", "backend_state": backend, "remedy": "Run 'tailscale up' to reconnect."}, data
        tailnet = (data.get("CurrentTailnet") or {}).get("Name") or data.get("MagicDNSSuffix")
        return {"state": "running", "backend_state": backend, "account": tailnet, "version": data.get("Version"),
                "self": (data.get("Self") or {}).get("HostName"),
                "limitation": "Only devices visible to this account under its ACL appear. Visibility grants no command access."}, data

    def detect(self, env: ScanEnv) -> dict[str, Any]:
        info, self._status = self._load(env)
        return info

    def list_candidates(self, env: ScanEnv) -> list[dict[str, Any]]:
        data = self._status
        if not data:
            return []
        account = (data.get("CurrentTailnet") or {}).get("Name") or data.get("MagicDNSSuffix")
        out: list[dict[str, Any]] = []
        peers = data.get("Peer") or {}
        if not isinstance(peers, dict):
            return []
        for key, peer in list(peers.items())[: DEFAULT_LIMITS["max_candidates"]]:
            if not isinstance(peer, dict):
                continue
            dns = str(peer.get("DNSName") or "").rstrip(".") or None
            name = str(peer.get("HostName") or (dns.split(".")[0] if dns else key))[:80]
            ips = [str(a) for a in peer.get("TailscaleIPs") or [] if isinstance(a, str)]
            out.append(candidate("tailscale", str(peer.get("ID") or key), name=name, env=env, account=account, addresses=ips,
                                 dns_name=dns, online=bool(peer.get("Online")), os_name=peer.get("OS") or None,
                                 evidence="visible in tailscale status", extra={
                                     "last_seen": peer.get("LastSeen"),
                                     "tailscale_ssh": bool(peer.get("SSH_HostKeys")),
                                 }))
        return out


class MeshProvider:
    """Cloudflare Mesh through the local Cloudflare One Client (``warp-cli``).

    ``warp-cli`` reports this device's registration. It does not list peers, so other
    Mesh nodes come from saved records, SSH config or manual entry (D20).
    """
    name = "mesh"

    def __init__(self, detector: Callable[[ScanEnv], dict[str, Any]] | None = None):
        self.detector = detector
        self._info: dict[str, Any] = {}

    def detect(self, env: ScanEnv) -> dict[str, Any]:
        if self.detector is not None:
            self._info = self.detector(env)
        else:
            import mesh

            self._info = mesh.detect(env.runner)
        return {**self._info, "peer_listing": "unsupported_locally",
                "limitation": "warp-cli exposes this device only; add other Mesh nodes through SSH config or manual entry."}

    def list_candidates(self, env: ScanEnv) -> list[dict[str, Any]]:
        return []


SSH_KEYWORDS = {"hostname", "port", "user", "proxyjump", "proxycommand", "identityfile", "certificatefile", "include", "match", "host"}


def parse_ssh_config(text: str, *, base: Path, env: ScanEnv, depth: int = 0, seen: set[str] | None = None) -> dict[str, Any]:
    """Concrete ``Host`` aliases with nonsecret routing facts. Patterns are never machines (D11).

    ``Match`` blocks and ``ProxyCommand`` can run commands when OpenSSH evaluates them, so
    they are flagged for controlled evaluation instead of being evaluated here.
    Identity and certificate files are recorded as present, never read.
    """
    seen = seen if seen is not None else set()
    hosts: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    current: list[str] = []
    in_match = False
    for raw in text.splitlines()[:20000]:
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = re.split(r"\s*=\s*|\s+", line, maxsplit=1)
        key = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        if key not in SSH_KEYWORDS:
            continue
        if key == "include" and depth < 4:
            for pattern in value.split():
                path = Path(os.path.expanduser(pattern))
                if not path.is_absolute():
                    path = base / path
                matches = sorted(path.parent.glob(path.name)) if any(c in path.name for c in "*?[") else [path]
                for inc in matches[:32]:
                    if str(inc) in seen:
                        continue
                    seen.add(str(inc))
                    try:
                        nested = parse_ssh_config(env.read_text(inc), base=base, env=env, depth=depth + 1, seen=seen)
                    except OSError:
                        notes.append(f"include unreadable: {inc.name}")
                        continue
                    for alias, entry in nested["hosts"].items():
                        hosts.setdefault(alias, entry)
                    notes.extend(nested["notes"])
            continue
        if key == "host":
            in_match = False
            current = []
            for alias in value.split():
                if alias.startswith("!") or any(c in alias for c in "*?") or not ALIAS_TOKEN.fullmatch(alias):
                    continue
                hosts.setdefault(alias, {"alias": alias, "hostname": None, "port": None, "user": None, "proxy_jump": None,
                                         "proxy_command": False, "identity_file": False, "certificate": False, "match_blocks": False})
                current.append(alias)
            continue
        if key == "match":
            in_match = True
            current = []
            notes.append("Match block present: evaluated only by OpenSSH for an explicitly selected alias")
            continue
        if in_match:
            continue
        for alias in current:
            entry = hosts[alias]
            if key == "hostname" and entry["hostname"] is None and HOST_TOKEN.fullmatch(value):
                entry["hostname"] = value
            elif key == "port" and entry["port"] is None and value.isdigit() and 0 < int(value) < 65536:
                entry["port"] = int(value)
            elif key == "user" and entry["user"] is None and USER_TOKEN.fullmatch(value):
                entry["user"] = value
            elif key == "proxyjump" and entry["proxy_jump"] is None:
                entry["proxy_jump"] = value[:200]
            elif key == "proxycommand":
                entry["proxy_command"] = True
            elif key == "identityfile":
                entry["identity_file"] = True
            elif key == "certificatefile":
                entry["certificate"] = True
    if any(n.startswith("Match") for n in notes):
        for entry in hosts.values():
            entry["match_blocks"] = True
    return {"hosts": hosts, "notes": sorted(set(notes))}


class SshConfigProvider:
    name = "ssh_config"

    def __init__(self, path: Path | None = None):
        self.path = path
        self._parsed: dict[str, Any] = {"hosts": {}, "notes": []}

    def detect(self, env: ScanEnv) -> dict[str, Any]:
        path = self.path or env.home / ".ssh" / "config"
        try:
            text = env.read_text(path)
        except FileNotFoundError:
            self._parsed = {"hosts": {}, "notes": []}
            return {"state": "not_found", "remedy": "No OpenSSH config. Add hosts manually or create ~/.ssh/config."}
        except OSError:
            self._parsed = {"hosts": {}, "notes": []}
            return {"state": "unavailable", "error": "ssh config is not readable by the service user"}
        self._parsed = parse_ssh_config(text, base=path.parent, env=env)
        return {"state": "read", "aliases": len(self._parsed["hosts"]), "notes": self._parsed["notes"]}

    def list_candidates(self, env: ScanEnv) -> list[dict[str, Any]]:
        out = []
        for alias, entry in sorted(self._parsed["hosts"].items()):
            hostname = entry.get("hostname")
            addresses = [hostname] if hostname and _ip(hostname) else []
            out.append(candidate("ssh_config", alias, name=alias, env=env, addresses=addresses,
                                 dns_name=hostname if hostname and not _ip(hostname) else None,
                                 ssh={"alias": alias, "endpoint": hostname, "port": entry.get("port"), "user": entry.get("user"),
                                      "jump": entry.get("proxy_jump"), "proxy_command": entry.get("proxy_command"),
                                      "identity_ref": bool(entry.get("identity_file")), "certificate": bool(entry.get("certificate")),
                                      "needs_effective_config": bool(entry.get("match_blocks") or entry.get("proxy_command"))},
                                 evidence="Host alias in OpenSSH config"))
        return out


class StaticProvider:
    """Records supplied by the caller: manual entries, SSH targets, workers, or a VM/cloud adapter.

    VM and cloud inventory adapters (libvirt, Proxmox, cloud APIs) implement the same two
    methods. Their candidates carry ``power_state`` and provider instance IDs and never
    imply SSH health (D37/D38). None ships enabled in this release.
    """

    def __init__(self, name: str, records: Callable[[ScanEnv], list[dict[str, Any]]], state: str = "configured"):
        self.name = name
        self._records = records
        self._state = state
        self._cache: list[dict[str, Any]] = []
        self._error: str | None = None

    def detect(self, env: ScanEnv) -> dict[str, Any]:
        try:
            self._cache = self._records(env)
            self._error = None
        except Exception as exc:  # noqa: BLE001 - an optional source must not break the scan
            self._cache = []
            self._error = str(exc)[:200]
            return {"state": "unavailable", "error": self._error}
        return {"state": self._state, "records": len(self._cache)}

    def list_candidates(self, env: ScanEnv) -> list[dict[str, Any]]:
        return list(self._cache)


# --------------------------------------------------------------------------- identity merge (S2)
def _ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value.strip("[]"))
        return True
    except ValueError:
        return False


def identity_tokens(record: dict[str, Any]) -> set[str]:
    """Every string that could name a host: used for exclusion and address-based merging."""
    tokens: set[str] = set()
    for value in [record.get("name"), record.get("dns_name"), record.get("record_id") if record.get("provider") == "ssh_config" else None,
                  *(record.get("addresses") or []), *(((record.get("ssh") or {}).get(k)) for k in ("alias", "endpoint"))]:
        if not value:
            continue
        v = str(value).strip().rstrip(".").lower()
        tokens.add(v)
        if not _ip(v) and "." in v:
            tokens.add(v.split(".", 1)[0])
    return tokens


def _strong_keys(record: dict[str, Any]) -> set[str]:
    """Keys that justify merging records from different providers: addresses and full DNS names."""
    keys = {f"ip:{a.lower()}" for a in record.get("addresses") or []}
    for value in (record.get("dns_name"), (record.get("ssh") or {}).get("endpoint")):
        if value and "." in str(value):
            keys.add(f"dns:{str(value).rstrip('.').lower()}")
        elif value and _ip(str(value)):
            keys.add(f"ip:{str(value).lower()}")
    if record.get("install_id"):
        keys.add(f"worker:{record['install_id']}")
    return keys


def host_id_for(record: dict[str, Any]) -> str:
    """Stable internal ID from provider identity plus account context, never a raw secret."""
    basis = f"{record.get('provider')}|{record.get('account') or ''}|{record.get('record_id')}"
    return "h_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def merge_candidates(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group records into hosts. Returns ``(hosts, conflicts)``.

    Records merge only on a shared address, full DNS name or worker installation ID.
    A shared short name alone produces a conflict record, not a merge (D13).
    """
    groups: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for rec in records:
        keys = _strong_keys(rec)
        hits = sorted({index[k] for k in keys if k in index})
        if not hits:
            groups.append({"records": [rec], "keys": set(keys)})
            gi = len(groups) - 1
        else:
            gi = hits[0]
            for other in hits[1:]:
                groups[gi]["records"].extend(groups[other]["records"])
                groups[gi]["keys"] |= groups[other]["keys"]
                groups[other] = {"records": [], "keys": set()}
            groups[gi]["records"].append(rec)
            groups[gi]["keys"] |= keys
        for k in groups[gi]["keys"]:
            index[k] = gi
    hosts = []
    for g in groups:
        if not g["records"]:
            continue
        recs = sorted(g["records"], key=lambda r: ["worker", "ssh_target", "tailscale", "mesh", "ssh_config", "manual"].index(r["provider"])
                      if r["provider"] in {"worker", "ssh_target", "tailscale", "mesh", "ssh_config", "manual"} else 9)
        primary = recs[0]
        aliases = sorted({r["name"] for r in recs if r.get("name")} | {r["dns_name"] for r in recs if r.get("dns_name")})
        ssh = next((r["ssh"] for r in recs if r.get("ssh")), None)
        hosts.append({
            "id": host_id_for(primary), "display_name": primary.get("name"), "aliases": aliases,
            "provider_ids": {r["provider"]: r["record_id"] for r in recs},
            "addresses": sorted({a for r in recs for a in r.get("addresses") or []}),
            "dns_names": sorted({r["dns_name"] for r in recs if r.get("dns_name")}),
            "os": next((r["os"] for r in recs if r.get("os")), None),
            "kind": next((r["kind"] for r in recs if r.get("kind") and r["kind"] != "unknown"), "unknown"),
            "online": next((r["online"] for r in recs if r.get("online") is not None), None),
            "ssh": copy.deepcopy(ssh), "sources": recs,
            "managed": any(r["provider"] in {"worker", "ssh_target"} for r in recs),
            "worker": next((r.get("worker") for r in recs if r.get("worker")), None),
        })
    by_short: dict[str, list[dict[str, Any]]] = {}
    for h in hosts:
        short = str(h.get("display_name") or "").split(".")[0].lower()
        if short:
            by_short.setdefault(short, []).append(h)
    conflicts = []
    for short, group in sorted(by_short.items()):
        if len(group) > 1:
            ids = [h["id"] for h in group]
            conflicts.append({"kind": "same_short_name", "name": short, "host_ids": ids,
                              "resolution": "Hosts share a short name but no address or DNS name. They stay separate until you merge them."})
            for h in group:
                h.setdefault("conflicts", []).append({"kind": "same_short_name", "with": [i for i in ids if i != h["id"]]})
    return hosts, conflicts


def is_excluded(tokens: set[str], excluded: Iterable[str]) -> str | None:
    """Return the matching exclusion entry. Any equivalent identity (alias, FQDN, short name, IP) matches (D17)."""
    for entry in excluded:
        e = str(entry).strip().rstrip(".").lower()
        if not e:
            continue
        if e in tokens:
            return entry
        if "/" in e:
            try:
                net = ipaddress.ip_network(e, strict=False)
            except ValueError:
                continue
            for t in tokens:
                if _ip(t) and ipaddress.ip_address(t.strip("[]")) in net:
                    return entry
    return None


# --------------------------------------------------------------------------- SSH probing (S3/S4)
_HOST_KEY_UNKNOWN = ("host key verification failed", "no ed25519 host key is known", "no ecdsa host key is known",
                     "no rsa host key is known", "authenticity of host")
_HOST_KEY_CHANGED = ("remote host identification has changed", "host key for", "offending")
_HUMAN_CHECK = ("tailscale ssh requires an additional check", "to authenticate, visit", "login.tailscale.com/a/")
_AUTH = ("permission denied", "publickey", "authentication failed", "too many authentication failures", "no supported authentication methods")
_UNREACHABLE = ("connection refused", "timed out", "no route", "network is unreachable", "host is down", "connection reset")
_DNS = ("could not resolve", "name or service not known", "temporary failure in name resolution", "nodename nor servname")


def classify_probe_failure(res: CommandResult) -> tuple[str, str]:
    """Map an SSH failure to ``(axis_result, host_state)``. Order matters: key problems first."""
    low = (res.stderr or "").lower()
    if res.timed_out:
        return "timeout", "offline"
    if any(m in low for m in _HUMAN_CHECK):
        return "human_check_required", "human_check"
    if any(m in low for m in _HOST_KEY_CHANGED) and "changed" in low:
        return "host_key_mismatch", "failed"
    if any(m in low for m in _HOST_KEY_UNKNOWN):
        return "host_key_unknown", "host_key_review"
    if any(m in low for m in _DNS):
        return "dns_failure", "offline"
    if any(m in low for m in _AUTH):
        return "auth_required", "auth_required"
    if any(m in low for m in _UNREACHABLE):
        return "unreachable", "offline"
    return "error", "failed"


def ssh_probe_argv(ssh: dict[str, Any], *, known_hosts: Path | None, timeout: int = 5) -> list[str]:
    """BatchMode probe argv. Never ``StrictHostKeyChecking=no``; unknown keys need confirmation (D10)."""
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={max(1, min(timeout, 15))}",
            "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "LogLevel=ERROR"]
    if known_hosts is not None:
        argv += ["-o", f"UserKnownHostsFile=~/.ssh/known_hosts {known_hosts}"]
    alias = ssh.get("alias")
    if alias:
        # An OpenSSH alias keeps its own HostName, Port, ProxyJump and agent settings (D09/D11).
        argv += ["--", str(alias)]
        return argv
    host = str(ssh.get("endpoint") or "")
    if not HOST_TOKEN.fullmatch(host):
        raise ValueError("invalid SSH endpoint")
    argv += ["-p", str(int(ssh.get("port") or 22))]
    if ssh.get("jump"):
        argv += ["-J", str(ssh["jump"])]
    user = ssh.get("user")
    argv += ["--", f"{user}@{host}" if user else host]
    return argv


# --------------------------------------------------------------------------- persistence
def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "hosts": {}, "manual": [], "runs": [], "providers": {}, "conflicts": [],
            "plans": {}, "owned_resources": [], "known_host_confirmations": []}


def migrate_state(data: Any) -> dict[str, Any]:
    """Versioned migration. Unknown future versions are kept read-only by refusing to overwrite them."""
    if not isinstance(data, dict):
        return empty_state()
    version = int(data.get("schema_version") or 0)
    if version > SCHEMA_VERSION:
        raise ValueError(f"discovery state schema {version} is newer than this runtime ({SCHEMA_VERSION})")
    state = empty_state()
    for key in state:
        if key in data and key != "schema_version":
            state[key] = data[key]
    return state


# --------------------------------------------------------------------------- scanner
class StagedScanner:
    def __init__(self, state_path: Path, *, env: ScanEnv | None = None, providers: list[Any] | None = None,
                 excluded: Callable[[], list[str]] = lambda: [], limits: dict[str, Any] | None = None,
                 known_hosts: Path | None = None, local_host: Callable[[], dict[str, Any]] | None = None):
        self.state_path = Path(state_path)
        self.env = env or ScanEnv()
        self.providers = providers if providers is not None else []
        self.excluded = excluded
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.known_hosts = known_hosts
        self.local_host = local_host or (lambda: {"hostname": socket.gethostname()})
        self._lock = threading.RLock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self.state = self._load()
        self._recover_interrupted()

    # ---------------------------------------------------------------- state
    def _load(self) -> dict[str, Any]:
        try:
            return migrate_state(json.loads(self.state_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return empty_state()
        except (OSError, ValueError):
            return empty_state()

    def _save(self) -> None:
        with self._lock:
            self.state["runs"] = self.state["runs"][-20:]
            _atomic_write(self.state_path, self.state)

    def _recover_interrupted(self) -> None:
        """A run that was in progress when the gateway stopped is marked interrupted, keeping partial results (D16/D33)."""
        changed = False
        for run in self.state.get("runs") or []:
            if run.get("status") == "running":
                run["status"] = "interrupted"
                run["finished_at"] = self.env.clock()
                for st in run.get("stages", {}).values():
                    if st.get("state") == "running":
                        st["state"] = "interrupted"
                changed = True
        if changed:
            self._save()

    def _new_run(self, kind: str, scope: dict[str, Any]) -> dict[str, Any]:
        run = {"id": "run_" + uuid.uuid4().hex[:12], "kind": kind, "status": "running", "started_at": self.env.clock(),
               "finished_at": None, "scope": scope, "stages": {s: {"state": "pending", "title": STAGE_TITLES[s]} for s in STAGES},
               "hosts": {}, "progress": {"done": 0, "total": 0}}
        self.state["runs"].append(run)
        return run

    def _stage(self, run: dict[str, Any], stage: str, state: str, **detail: Any) -> None:
        entry = run["stages"][stage]
        now = self.env.clock()
        if state == "running":
            entry["started_at"] = now
        elif state in {"done", "partial", "failed", "skipped", "cancelled"}:
            entry["finished_at"] = now
        entry["state"] = state
        entry.update(detail)

    # ---------------------------------------------------------------- S0..S2 (no remote contact)
    def list_hosts(self) -> dict[str, Any]:
        """Run S0–S2. Reads local provider state only; contacts no remote host."""
        with self._lock:
            run = self._new_run("list", {"stages": ["S0", "S1", "S2"]})
            excluded = list(self.excluded())
            self._stage(run, "S0", "running")
            local = self.local_host()
            self._stage(run, "S0", "done", excluded=excluded, manual=len(self.state["manual"]), local=local.get("hostname"))

            self._stage(run, "S1", "running")
            providers: dict[str, Any] = {}
            records: list[dict[str, Any]] = []
            for provider in self.providers:
                started = time.monotonic()
                try:
                    info = provider.detect(self.env)
                    recs = provider.list_candidates(self.env)
                except Exception as exc:  # noqa: BLE001 - one provider failing is a partial result (D30)
                    info, recs = {"state": "error", "error": str(exc)[:200]}, []
                info["duration_ms"] = int((time.monotonic() - started) * 1000)
                info["observed_at"] = self.env.clock()
                info["candidates"] = len(recs)
                providers[provider.name] = info
                records.extend(recs)
            records.extend(self._manual_records())
            self._stage(run, "S1", "done" if all(p.get("state") not in {"error"} for p in providers.values()) else "partial",
                        providers={k: v.get("state") for k, v in providers.items()})

            self._stage(run, "S2", "running")
            merged, conflicts = merge_candidates(records[: self.limits["max_candidates"]])
            previous = self.state["hosts"]
            hosts: dict[str, dict[str, Any]] = {}
            now = self.env.clock()
            for host in merged:
                tokens = set()
                for src in host["sources"]:
                    tokens |= identity_tokens(src)
                prior = previous.get(host["id"]) or {}
                host["axes"] = prior.get("axes") or {}
                host["first_seen"] = prior.get("first_seen") or now
                host["last_listed"] = now
                hit = is_excluded(tokens, excluded)
                host["excluded"] = bool(hit)
                host["exclusion_match"] = hit
                if hit:
                    # An excluded host keeps only its listing; no axes, no probe history.
                    host["axes"] = {}
                    host["ssh"] = None
                host["state"] = self._host_state(host, prior, now)
                host["next_action"] = self._next_action(host)
                hosts[host["id"]] = host
            # Hosts that disappeared from every provider are kept as stale, not deleted (H18).
            for hid, prior in previous.items():
                if hid not in hosts and prior.get("id") != "local":
                    keep = copy.deepcopy(prior)
                    keep["state"] = "stale" if not prior.get("excluded") else "excluded"
                    keep["listed"] = False
                    hosts[hid] = keep
            hosts["local"] = self._local_record(local, now, previous.get("local"))
            self.state["hosts"] = hosts
            self.state["providers"] = providers
            self.state["conflicts"] = conflicts
            self._stage(run, "S2", "done", hosts=len(hosts), conflicts=len(conflicts))
            for st in STAGES[3:]:
                self._stage(run, st, "skipped", reason="select hosts to probe or enroll")
            run["status"] = "done"
            run["finished_at"] = self.env.clock()
            self._save()
            return self.snapshot()

    def _local_record(self, local: dict[str, Any], now: float, prior: dict[str, Any] | None) -> dict[str, Any]:
        return {"id": "local", "display_name": local.get("hostname") or "local", "aliases": ["local"], "provider_ids": {"local": "local"},
                "addresses": [], "dns_names": [], "os": local.get("os"), "kind": local.get("kind", "unknown"), "online": True,
                "ssh": None, "sources": [], "managed": True, "excluded": False, "state": "local", "first_seen": (prior or {}).get("first_seen") or now,
                "last_listed": now, "axes": {"process": observation("local", "process", "ok", source="gateway", env=self.env)},
                "next_action": "Inspect local services in Discovery", "worker": {"installed": True, "target": "local"}}

    def _manual_records(self) -> list[dict[str, Any]]:
        out = []
        for m in self.state.get("manual") or []:
            out.append(candidate("manual", m["id"], name=m.get("label") or m["host"], env=self.env,
                                 addresses=[m["host"]] if _ip(m["host"]) else [], dns_name=None if _ip(m["host"]) else m["host"],
                                 ssh={"alias": None, "endpoint": m["host"], "port": m.get("port") or 22, "user": m.get("user"),
                                      "jump": m.get("jump"), "identity_ref": False, "needs_effective_config": False},
                                 confidence="manual", evidence="entered by an administrator", kind=m.get("kind") or "unknown"))
        return out

    def _host_state(self, host: dict[str, Any], prior: dict[str, Any], now: float) -> str:
        if host.get("excluded"):
            return "excluded"
        if host.get("managed"):
            return "existing"
        reach = (host.get("axes") or {}).get("ssh")
        if reach and freshness(reach, now) == "fresh":
            mapping = {"ok": "enrollment_required", "auth_required": "auth_required", "host_key_unknown": "host_key_review",
                       "human_check_required": "human_check", "host_key_mismatch": "failed"}
            if reach["result"] in mapping:
                return mapping[reach["result"]]
            return "offline" if reach["result"] in {"unreachable", "timeout", "dns_failure"} else "failed"
        if reach:
            return "stale"
        if host.get("online") is False:
            return "offline"
        return "new" if not prior else (prior.get("state") if prior.get("state") in {"new", "offline"} else "new")

    @staticmethod
    def _next_action(host: dict[str, Any]) -> str:
        return {
            "excluded": "Excluded by policy. Remove the exclusion to manage this host.",
            "existing": "Already managed. Refresh the worker inventory from Fleet.",
            "new": "Select the host and run Probe to check SSH access.",
            "offline": "The provider reports the host offline. Retry when it is online.",
            "stale": "The last observation expired. Probe again.",
            "auth_required": "SSH authentication failed. Load a key into the service agent or fix the SSH user, then retry.",
            "host_key_review": "Review and confirm the host key fingerprint before Loopback trusts it.",
            "human_check": "Tailscale SSH needs a browser check. Complete it, then retry this host.",
            "enrollment_required": "SSH works. Preview the enrollment plan to register it and install a worker.",
            "failed": "The last probe failed. Open the host for the exact layer and retry.",
        }.get(host.get("state", ""), "Probe the host.")

    # ---------------------------------------------------------------- manual entries
    def add_manual(self, *, host: str, port: int = 22, user: str | None = None, label: str | None = None,
                   jump: str | None = None, kind: str = "unknown") -> dict[str, Any]:
        host = str(host or "").strip()
        if not HOST_TOKEN.fullmatch(host) or host.startswith("-"):
            raise ValueError("host must be a hostname or IP address")
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError("port must be an integer") from None
        if not 0 < port < 65536:
            raise ValueError("port must be between 1 and 65535")
        if user is not None and user != "" and not USER_TOKEN.fullmatch(str(user)):
            raise ValueError("user must be a valid account name")
        if jump and not re.fullmatch(r"[A-Za-z0-9_.@:,\[\]-]{1,200}", jump):
            raise ValueError("jump must be an SSH ProxyJump value")
        if label and len(label) > 80:
            raise ValueError("label must be at most 80 characters")
        entry = {"id": "m_" + hashlib.sha256(f"{host}|{port}|{user or ''}".encode()).hexdigest()[:12], "host": host, "port": port,
                 "user": user or None, "label": label or None, "jump": jump or None, "kind": kind, "created_at": self.env.clock()}
        with self._lock:
            manual = [m for m in self.state["manual"] if m["id"] != entry["id"]]
            manual.append(entry)
            self.state["manual"] = manual
            self._save()
        return entry

    def remove_manual(self, entry_id: str) -> bool:
        with self._lock:
            before = len(self.state["manual"])
            self.state["manual"] = [m for m in self.state["manual"] if m["id"] != entry_id]
            self._save()
            return len(self.state["manual"]) != before

    # ---------------------------------------------------------------- S3/S4 probing
    def start_probe(self, host_ids: list[str], *, background: bool = True) -> dict[str, Any]:
        """Probe only the selected hosts. Excluded hosts are rejected before any DNS or SSH work."""
        ids = [h for h in dict.fromkeys(host_ids) if isinstance(h, str)]
        if not ids:
            raise ValueError("select at least one host")
        with self._lock:
            for run in self.state["runs"]:
                if run["status"] == "running" and run["kind"] == "probe" and set(run["scope"]["host_ids"]) == set(ids):
                    return run  # duplicate click: return the run already in progress (D33)
            unknown = [h for h in ids if h not in self.state["hosts"]]
            if unknown:
                raise KeyError(f"unknown host: {unknown[0]}")
            run = self._new_run("probe", {"host_ids": ids})
            for st in ("S0", "S1", "S2"):
                self._stage(run, st, "skipped", reason="uses the last listing")
            for st in STAGES[5:]:
                self._stage(run, st, "skipped", reason="probe run")
            run["progress"]["total"] = len(ids)
            cancel = threading.Event()
            self._cancel[run["id"]] = cancel
            self._save()
        if background:
            t = threading.Thread(target=self._probe_run, args=(run["id"], ids, cancel), daemon=True, name=f"lb-probe-{run['id']}")
            self._threads[run["id"]] = t
            t.start()
        else:
            self._probe_run(run["id"], ids, cancel)
        return self.run(run["id"])

    def _probe_run(self, run_id: str, ids: list[str], cancel: threading.Event) -> None:
        run = self._run_ref(run_id)
        self._stage(run, "S3", "running")
        deadline = time.monotonic() + float(self.limits["scan_timeout"])
        results: dict[str, dict[str, Any]] = {}

        def one(hid: str) -> tuple[str, dict[str, Any]]:
            if cancel.is_set() or time.monotonic() > deadline:
                return hid, {"outcome": "cancelled" if cancel.is_set() else "timeout"}
            lock = self._host_locks.setdefault(hid, threading.Lock())
            if not lock.acquire(blocking=False):
                return hid, {"outcome": "busy", "detail": "another probe or enrollment holds this host"}
            try:
                return hid, self._probe_host(hid)
            finally:
                lock.release()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(self.limits["probe_concurrency"]))) as pool:
            for hid, outcome in pool.map(one, ids):
                results[hid] = outcome
                with self._lock:
                    run["hosts"][hid] = outcome
                    run["progress"]["done"] += 1
        with self._lock:
            cancelled = cancel.is_set()
            states = {r.get("outcome") for r in results.values()}
            self._stage(run, "S3", "cancelled" if cancelled else ("done" if states <= {"ok", "excluded"} else "partial"))
            self._stage(run, "S4", "cancelled" if cancelled else ("done" if any(r.get("inventory") for r in results.values()) else "skipped"))
            run["status"] = "cancelled" if cancelled else "done"
            run["finished_at"] = self.env.clock()
            self._cancel.pop(run_id, None)
            self._save()

    def _probe_host(self, hid: str) -> dict[str, Any]:
        with self._lock:
            host = copy.deepcopy(self.state["hosts"].get(hid))
        if host is None:
            return {"outcome": "unknown_host"}
        if host.get("excluded") or host.get("id") == "local":
            return {"outcome": "excluded" if host.get("excluded") else "local", "detail": "not contacted"}
        ssh = host.get("ssh")
        axes: dict[str, Any] = {}
        if not ssh:
            axes["ssh"] = observation(hid, "ssh", "no_access_route", source="scan", env=self.env,
                                      error="No SSH alias or endpoint is known for this host. Add it through SSH config or manual entry.")
            self._store_axes(hid, axes)
            return {"outcome": "no_access_route"}
        # DNS axis: only for endpoints that are names and are not handled by an OpenSSH alias.
        endpoint = ssh.get("endpoint")
        if endpoint and not _ip(endpoint) and not ssh.get("alias"):
            started = time.monotonic()
            try:
                addrs = self.env.resolver(endpoint)
                axes["dns"] = observation(hid, "dns", "ok", source="resolver", env=self.env, detail={"addresses": addrs[:8]},
                                          duration_ms=int((time.monotonic() - started) * 1000))
            except OSError as exc:
                axes["dns"] = observation(hid, "dns", "dns_failure", source="resolver", env=self.env, error=str(exc)[:200])
                axes["ssh"] = observation(hid, "ssh", "dns_failure", source="scan", env=self.env, error="name did not resolve")
                self._store_axes(hid, axes)
                return {"outcome": "dns_failure"}
        try:
            argv = ssh_probe_argv(ssh, known_hosts=self.known_hosts, timeout=int(self.limits["command_timeout"]))
        except ValueError as exc:
            axes["ssh"] = observation(hid, "ssh", "error", source="scan", env=self.env, error=str(exc))
            self._store_axes(hid, axes)
            return {"outcome": "error"}
        from ssh_targets import _PROBE_SCRIPT, machine_kind_from, parse_probe_output

        started = time.monotonic()
        res = self.env.runner([*argv, "sh -c " + shlex.quote(_PROBE_SCRIPT)], float(self.limits["command_timeout"]) + 2)
        duration = int((time.monotonic() - started) * 1000)
        fields = parse_probe_output(res.stdout or "")
        if res.returncode != 0 and "end" not in fields:
            result, _state = classify_probe_failure(res)
            from redaction import redact

            axes["ssh"] = observation(hid, "ssh", result, source="openssh", env=self.env, duration_ms=duration,
                                      error=redact((res.stderr or "").strip())[:300] or f"ssh exited {res.returncode}")
            self._store_axes(hid, axes)
            return {"outcome": result}
        uname = fields.get("uname", "").split()
        inventory = {"hostname": fields.get("hostname"), "os": fields.get("os"), "kernel": " ".join(uname[:2]) or None,
                     "arch": uname[2] if len(uname) >= 3 else None, "user": fields.get("user"),
                     "machine_kind": machine_kind_from(fields.get("virt", ""), fields.get("container") == "yes")}
        axes["ssh"] = observation(hid, "ssh", "ok", source="openssh", env=self.env, duration_ms=duration)
        axes["bootstrap"] = observation(hid, "bootstrap", "ok", source="read-only metadata script", env=self.env, detail=inventory)
        self._store_axes(hid, axes, inventory=inventory)
        return {"outcome": "ok", "inventory": inventory}

    def _store_axes(self, hid: str, axes: dict[str, Any], inventory: dict[str, Any] | None = None) -> None:
        with self._lock:
            host = self.state["hosts"].get(hid)
            if host is None:
                return
            host.setdefault("axes", {}).update(axes)
            if inventory:
                host["inventory"] = inventory
                host["os"] = host.get("os") or inventory.get("os")
                if inventory.get("machine_kind") and host.get("kind") in (None, "unknown"):
                    host["kind"] = inventory["machine_kind"]
            host["state"] = self._host_state(host, host, self.env.clock())
            host["next_action"] = self._next_action(host)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            event = self._cancel.get(run_id)
            if event is not None:
                event.set()
        return self.run(run_id)

    def wait(self, run_id: str, timeout: float = 30) -> dict[str, Any]:
        t = self._threads.get(run_id)
        if t is not None:
            t.join(timeout)
        return self.run(run_id)

    # ---------------------------------------------------------------- host keys (D10)
    def host_key_scan(self, hid: str) -> dict[str, Any]:
        host = self._host(hid)
        ssh = host.get("ssh") or {}
        target = ssh.get("endpoint") or ssh.get("alias")
        if not target or not HOST_TOKEN.fullmatch(str(target)):
            raise ValueError("no SSH endpoint to scan")
        port = int(ssh.get("port") or 22)
        res = self.env.runner(["ssh-keyscan", "-T", "5", "-p", str(port), str(target)], 8.0)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError("ssh-keyscan returned no host keys")
        fp = self.env.runner(["ssh-keygen", "-lf", "-"], 5.0, stdin=res.stdout)
        prints = [line.strip() for line in (fp.stdout or "").splitlines() if "SHA256:" in line][:6]
        return {"host_id": hid, "target": target, "port": port, "fingerprints": prints,
                "note": "Compare a fingerprint with one obtained out of band (console or provider) before confirming."}

    def confirm_host_key(self, hid: str, fingerprint: str) -> dict[str, Any]:
        """Trust exactly the keys matching ``fingerprint`` in Loopback's own known_hosts file."""
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/=]{20,80}", fingerprint or ""):
            raise ValueError("fingerprint must be an OpenSSH SHA256 fingerprint")
        if self.known_hosts is None:
            raise RuntimeError("no Loopback known_hosts file is configured")
        host = self._host(hid)
        ssh = host.get("ssh") or {}
        target = ssh.get("endpoint") or ssh.get("alias")
        port = int(ssh.get("port") or 22)
        res = self.env.runner(["ssh-keyscan", "-T", "5", "-p", str(port), str(target)], 8.0)
        accepted = []
        for line in (res.stdout or "").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            fp = self.env.runner(["ssh-keygen", "-lf", "-"], 5.0, stdin=line + "\n")
            if fingerprint in (fp.stdout or ""):
                accepted.append(line.strip())
        if not accepted:
            raise ValueError("the host no longer presents that fingerprint; nothing was trusted")
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.known_hosts, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            for line in accepted:
                handle.write(line + "\n")
        with self._lock:
            self.state["known_host_confirmations"].append({"host_id": hid, "fingerprint": fingerprint, "at": self.env.clock()})
            self._save()
        return {"host_id": hid, "trusted": len(accepted), "fingerprint": fingerprint}

    # ---------------------------------------------------------------- S5 enrollment
    def plan_enrollment(self, host_ids: list[str], *, ssh_store: Any = None, install_worker: bool = True) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        existing = set()
        if ssh_store is not None:
            try:
                existing = {t["name"] for t in ssh_store.list()}
            except Exception:  # noqa: BLE001
                existing = set()
        for hid in dict.fromkeys(host_ids):
            host = self._host(hid)
            if host.get("excluded"):
                blocked.append({"host_id": hid, "reason": "excluded by discovery policy"})
                continue
            ssh_axis = (host.get("axes") or {}).get("ssh") or {}
            if ssh_axis.get("result") != "ok" or freshness(ssh_axis, self.env.clock()) != "fresh":
                blocked.append({"host_id": hid, "reason": "probe the host successfully first (fresh SSH ok is required)"})
                continue
            name = _target_name(host)
            ssh = host.get("ssh") or {}
            if name in existing:
                steps.append({"host_id": hid, "action": "reuse_ssh_target", "name": name, "mutation": False})
            else:
                fields = ({"name": name, "host": ssh["alias"], "use_ssh_config": True, "user": ssh.get("user") or "loopback"} if ssh.get("alias") else
                          {"name": name, "host": ssh.get("endpoint"), "port": int(ssh.get("port") or 22), "user": ssh.get("user") or "root"})
                fields.update({"display_name": host.get("display_name"), "kind": _ssh_kind(host.get("kind"))})
                steps.append({"host_id": hid, "action": "register_ssh_target", "name": name, "fields": fields, "mutation": True,
                              "rollback": f"remove SSH target {name}"})
            if install_worker and not (host.get("worker") or {}).get("installed"):
                steps.append({"host_id": hid, "action": "install_worker", "name": name, "mutation": True,
                              "privilege": "the SSH user's own account; no sudo", "requires": "Python 3.11+ with venv on the host",
                              "rollback": "stop the worker and remove ~/.local/share/loopback-worker on the host"})
        plan = {"id": "plan_" + uuid.uuid4().hex[:12], "created_at": self.env.clock(), "host_ids": list(dict.fromkeys(host_ids)),
                "steps": steps, "blocked": blocked, "secrets_needed": [], "status": "planned",
                "note": "Applying registers SSH targets, then hands worker installation to the existing Fleet installer. "
                        "Listing or probing never installs anything."}
        with self._lock:
            self.state["plans"][plan["id"]] = plan
            self.state["plans"] = dict(list(self.state["plans"].items())[-20:])
            self._save()
        return plan

    def apply_enrollment(self, plan_id: str, *, ssh_store: Any, workers: Any = None) -> dict[str, Any]:
        """Apply a stored plan. Completed steps are checkpointed and skipped on retry (D16)."""
        with self._lock:
            plan = self.state["plans"].get(plan_id)
            if plan is None:
                raise KeyError("plan not found")
            if plan["status"] == "applying":
                return plan
            plan["status"] = "applying"
            self._save()
        results = []
        failed_hosts: set[str] = set()
        for i, step in enumerate(plan["steps"]):
            if step.get("done"):
                results.append({"step": i, "outcome": "already_done"})
                continue
            if step["host_id"] in failed_hosts:
                # An earlier step for this host failed in this run: do not run dependent steps (for example
                # install_worker without a registered target). A later apply retries it.
                step["skipped_reason"] = "an earlier step for this host failed"
                results.append({"step": i, "outcome": "skipped", "reason": step["skipped_reason"]})
                continue
            step.pop("skipped_reason", None)
            lock = self._host_locks.setdefault(step["host_id"], threading.Lock())
            with lock:
                try:
                    if step["action"] == "register_ssh_target":
                        ssh_store.upsert(**step["fields"])
                        self._own({"kind": "ssh_target", "name": step["name"], "plan_id": plan_id})
                    elif step["action"] == "install_worker":
                        if workers is None:
                            raise RuntimeError("worker installer is unavailable in this runtime")
                        workers.install(step["name"])
                        self._own({"kind": "worker", "name": step["name"], "plan_id": plan_id})
                    step["done"] = True
                    step["error"] = None
                    results.append({"step": i, "outcome": "done"})
                except Exception as exc:  # noqa: BLE001 - recorded per step; later steps for this host are skipped
                    step["error"] = str(exc)[:300]
                    results.append({"step": i, "outcome": "failed", "error": step["error"]})
                    failed_hosts.add(step["host_id"])
            with self._lock:
                self._save()
        with self._lock:
            plan["status"] = "applied" if all(s.get("done") for s in plan["steps"]) else "partial"
            plan["results"] = results
            for step in plan["steps"]:
                host = self.state["hosts"].get(step["host_id"])
                if host and step.get("done"):
                    host["managed"] = True
                    if step["action"] == "install_worker":
                        host["worker"] = {"installed": True, "target": step["name"]}
                    host["state"] = "existing"
                    host["next_action"] = self._next_action(host)
            self._save()
            return copy.deepcopy(plan)

    def _own(self, resource: dict[str, Any]) -> None:
        with self._lock:
            if resource not in self.state["owned_resources"]:
                self.state["owned_resources"].append({**resource, "created_at": self.env.clock()})

    def rollback_plan(self, plan_id: str, *, ssh_store: Any) -> dict[str, Any]:
        """Remove only SSH targets this plan created. Workers are listed for manual removal (unowned state is preserved)."""
        removed, preserved = [], []
        with self._lock:
            owned = [r for r in self.state["owned_resources"] if r.get("plan_id") == plan_id]
        for res in owned:
            if res["kind"] == "ssh_target":
                try:
                    if ssh_store.remove(res["name"]):
                        removed.append(res)
                except Exception:  # noqa: BLE001
                    preserved.append({**res, "reason": "removal failed"})
            else:
                preserved.append({**res, "reason": "remove the worker from Fleet; rollback never deletes remote files automatically"})
        with self._lock:
            self.state["owned_resources"] = [r for r in self.state["owned_resources"] if r not in removed]
            self._save()
        return {"plan_id": plan_id, "removed": removed, "preserved": preserved}

    # ---------------------------------------------------------------- views
    def _host(self, hid: str) -> dict[str, Any]:
        with self._lock:
            host = self.state["hosts"].get(hid)
            if host is None:
                raise KeyError(f"unknown host: {hid}")
            return copy.deepcopy(host)

    def _run_ref(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            for run in self.state["runs"]:
                if run["id"] == run_id:
                    return run
        raise KeyError("run not found")

    def run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._run_ref(run_id))

    def snapshot(self, *, include_sources: bool = False) -> dict[str, Any]:
        now = self.env.clock()
        with self._lock:
            hosts = copy.deepcopy(list(self.state["hosts"].values()))
            providers = copy.deepcopy(self.state["providers"])
            runs = copy.deepcopy(self.state["runs"][-5:])
            conflicts = copy.deepcopy(self.state["conflicts"])
            manual = copy.deepcopy(self.state["manual"])
        for h in hosts:
            for axis in (h.get("axes") or {}).values():
                axis["freshness"] = freshness(axis, now)
            if not include_sources:
                h["sources"] = [{k: s.get(k) for k in ("provider", "record_id", "account", "observed_at", "evidence", "confidence", "online")}
                                for s in h.get("sources") or []]
        hosts.sort(key=lambda h: (h["id"] != "local", h.get("excluded", False), str(h.get("display_name") or "").lower()))
        counts: dict[str, int] = {}
        for h in hosts:
            counts[h.get("state", "new")] = counts.get(h.get("state", "new"), 0) + 1
        listed = any(r.get("kind") == "list" for r in self.state["runs"])
        empty_reason = None
        if len(hosts) <= 1:
            if not listed:
                empty_reason = "not_scanned"
            elif all(p.get("state") in {"missing_binary", "not_found"} for p in providers.values()):
                empty_reason = "no_provider"
            elif any(p.get("state") in {"daemon_down", "unenrolled", "unavailable", "error", "malformed", "stopped"} for p in providers.values()):
                empty_reason = "provider_unavailable"
            else:
                empty_reason = "no_peers"
        return {"schema_version": SCHEMA_VERSION, "generated_at": now, "hosts": hosts, "providers": providers, "runs": runs,
                "conflicts": conflicts, "manual": manual, "counts": counts, "empty_reason": empty_reason,
                "excluded": list(self.excluded()), "stages": [{"id": s, "title": STAGE_TITLES[s]} for s in STAGES],
                "limits": {k: self.limits[k] for k in ("probe_concurrency", "enroll_concurrency", "command_timeout", "scan_timeout")}}


def _target_name(host: dict[str, Any]) -> str:
    base = str((host.get("ssh") or {}).get("alias") or host.get("display_name") or host["id"]).split(".")[0]
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", base)[:40] or host["id"][:40]
    return "host-" + name if name == "local" else name


def _ssh_kind(kind: Any) -> str:
    return {"physical": "remote-physical", "virtual-machine": "remote-vm", "vm": "remote-vm", "container": "container"}.get(str(kind), "unknown")


# --------------------------------------------------------------------------- runtime wiring
def build_scanner(ctx: Any, env: ScanEnv | None = None) -> StagedScanner:
    env = env or ScanEnv(home=Path(getattr(ctx.identity, "home", None) or Path.home()))

    def ssh_target_records(e: ScanEnv) -> list[dict[str, Any]]:
        store = getattr(ctx, "ssh", None)
        if store is None:
            return []
        out = []
        for t in store.list():
            host = str(t.get("host") or "")
            ssh = ({"alias": host, "endpoint": None, "port": None, "user": None, "jump": None} if t.get("use_ssh_config") else
                   {"alias": None, "endpoint": host, "port": t.get("port"), "user": t.get("user"), "jump": None})
            out.append(candidate("ssh_target", t["name"], name=t.get("display_name") or t["name"], env=e,
                                 addresses=[host] if _ip(host) else [], dns_name=host if "." in host and not _ip(host) else None,
                                 ssh=ssh, kind=t.get("kind") or "unknown", confidence="registered", evidence="registered SSH target"))
        return out

    def worker_records(e: ScanEnv) -> list[dict[str, Any]]:
        workers = getattr(ctx, "workers", None)
        if workers is None:
            return []
        out = []
        for name in workers.targets():
            if name == "local":
                continue
            out.append(candidate("worker", name, name=name, env=e, confidence="registered", evidence="installed Loopback worker",
                                 extra={"worker": {"installed": True, "target": name}}))
        return out

    def excluded() -> list[str]:
        cfg = (getattr(ctx.config, "data", {}) or {}).get("discovery") or {}
        return [str(x) for x in (cfg.get("excluded_targets") or [])]

    def limits() -> dict[str, Any]:
        cfg = (getattr(ctx.config, "data", {}) or {}).get("discovery") or {}
        return {k: cfg[k] for k in DEFAULT_LIMITS if k in cfg}

    providers = [TailscaleProvider(), MeshProvider(), SshConfigProvider(),
                 StaticProvider("ssh_target", ssh_target_records), StaticProvider("worker", worker_records)]

    def local_host() -> dict[str, Any]:
        import platform

        return {"hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}"}

    cfg_dir = Path(ctx.identity.config_dir)
    return StagedScanner(cfg_dir / "discovery_state.json", env=env, providers=providers, excluded=excluded, limits=limits(),
                         known_hosts=cfg_dir / "known_hosts", local_host=local_host)


__all__ = ["StagedScanner", "ScanEnv", "CommandResult", "TailscaleProvider", "MeshProvider", "SshConfigProvider",
           "StaticProvider", "merge_candidates", "parse_ssh_config", "is_excluded", "classify_probe_failure",
           "ssh_probe_argv", "build_scanner", "migrate_state", "STAGES"]
