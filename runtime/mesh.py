"""Opt-in Cloudflare Mesh setup (CLOUDFLARE-MESH.md, ledger H20, gate M1).

Cloudflare Mesh is private connectivity through the Cloudflare One Client
(``cloudflare-warp`` package, ``warp-svc`` service, ``warp-cli`` CLI). It is not
Cloudflare Tunnel (``cloudflared``), which publishes public hostnames and is managed
by ``ingress.py``. The two can be chosen together and keep separate state.

Flow: ``detect`` → ``preflight`` → ``plan`` → owner-side ``apply`` → ``verify`` → ``rollback``.

* Detection and planning are read-only and safe to call from the dashboard.
* ``apply`` needs root for package and service changes. It runs only from the owner's
  ``loopback mesh apply`` command, never from an MCP tool or the web dashboard.
* The connector token is read from a secret reference at apply time. It never enters
  settings files, logs, journals, tool output or exceptions. ``warp-cli connector new``
  takes the token as an argument, so it is briefly visible in the local process
  table to the same host's root and owner; the plan states this exposure.
* Every mutation is journaled. Rerunning converges. Rollback removes only resources
  the journal says this installation created, in reverse order.

Live enrollment is UNVERIFIED until the isolated lab runs it (LAB-ROADMAP.md).
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

SETTINGS_SCHEMA_VERSION = 1
DEFAULT_MESH_RANGE = "100.96.0.0/12"
TAILSCALE_RANGE = "100.64.0.0/10"
ROLES = ("node", "subnet_router")
# Platform list as reviewed in the Cloudflare One Client Linux documentation on 2026-10-31.
# Mesh is Beta. Re-check the current supported list before a release claim.
SUPPORTED_PLATFORMS = {
    "ubuntu": {"22.04", "24.04"},
    "debian": {"12", "13"},
    "rhel": {"8", "9"}, "centos": {"8", "9"}, "rocky": {"8", "9"}, "almalinux": {"8", "9"},
}
SUPPORTED_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
APT_KEY_URL = "https://pkg.cloudflareclient.com/pubkey.gpg"
APT_REPO = "https://pkg.cloudflareclient.com/"
TOKEN_REF = re.compile(r"^(env:[A-Z_][A-Z0-9_]{0,63}|file:/[^\0\n]{1,400})$")


class MeshError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


Runner = Callable[..., Any]  # scan.Runner: (argv, timeout, stdin=None) -> CommandResult


# --------------------------------------------------------------------------- settings
def default_settings() -> dict[str, Any]:
    return {"schema_version": SETTINGS_SCHEMA_VERSION, "mesh": {
        "enabled": False, "role": "node", "token_ref": None, "organization": None, "mesh_range": DEFAULT_MESH_RANGE,
        "acknowledge_tailscale_overlap": False, "overlap_mitigation": None, "package_version": None,
        "verify_tcp": None,
    }}


def parse_settings(data: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate the versioned unattended-settings block. Returns ``(settings, errors)``.

    The settings file holds a *reference* to the token (``env:NAME`` or ``file:/path``), never its value.
    """
    errors: list[str] = []
    out = default_settings()
    if not isinstance(data, dict):
        return out, ["settings must be a JSON object"]
    version = data.get("schema_version", SETTINGS_SCHEMA_VERSION)
    if version != SETTINGS_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SETTINGS_SCHEMA_VERSION}")
    mesh = data.get("mesh", {})
    if not isinstance(mesh, dict):
        return out, errors + ["mesh must be an object"]
    unknown = sorted(set(mesh) - set(out["mesh"]))
    if unknown:
        errors.append("unknown mesh keys: " + ", ".join(unknown))
    m = out["mesh"]
    if "enabled" in mesh:
        if not isinstance(mesh["enabled"], bool):
            errors.append("mesh.enabled must be true or false")
        m["enabled"] = bool(mesh["enabled"])
    if "role" in mesh:
        if mesh["role"] not in ROLES:
            errors.append("mesh.role must be node or subnet_router")
        else:
            m["role"] = mesh["role"]
    token_ref = mesh.get("token_ref")
    if token_ref is not None:
        if not isinstance(token_ref, str) or not TOKEN_REF.fullmatch(token_ref):
            errors.append("mesh.token_ref must be env:NAME or file:/absolute/path; never the token itself")
        else:
            m["token_ref"] = token_ref
    if isinstance(token_ref, str) and re.fullmatch(r"[A-Za-z0-9._\-]{40,}", token_ref):
        errors.append("mesh.token_ref looks like a token value; store the token outside settings and reference it")
    org = mesh.get("organization")
    if org is not None:
        if not isinstance(org, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", org):
            errors.append("mesh.organization must be the Zero Trust team name")
        else:
            m["organization"] = org
    if "mesh_range" in mesh:
        try:
            net = ipaddress.ip_network(str(mesh["mesh_range"]), strict=True)
            if net.version != 4 or not net.is_private and not net.subnet_of(ipaddress.ip_network("100.64.0.0/10")):
                errors.append("mesh.mesh_range must be a private or CGNAT IPv4 network")
            m["mesh_range"] = str(net)
        except ValueError:
            errors.append("mesh.mesh_range must be a CIDR network such as 100.96.0.0/12")
    for key in ("acknowledge_tailscale_overlap",):
        if key in mesh:
            if not isinstance(mesh[key], bool):
                errors.append(f"mesh.{key} must be true or false")
            m[key] = bool(mesh[key])
    if mesh.get("overlap_mitigation") is not None:
        if mesh["overlap_mitigation"] not in ("custom_range", "route_exclusions_reviewed", "isolated_namespace"):
            errors.append("mesh.overlap_mitigation must be custom_range, route_exclusions_reviewed or isolated_namespace")
        else:
            m["overlap_mitigation"] = mesh["overlap_mitigation"]
    if mesh.get("package_version") is not None:
        if not isinstance(mesh["package_version"], str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+~-]{0,40}", mesh["package_version"]):
            errors.append("mesh.package_version must be a package version string")
        else:
            m["package_version"] = mesh["package_version"]
    if mesh.get("verify_tcp") is not None:
        v = mesh["verify_tcp"]
        if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}:[0-9]{1,5}", v):
            errors.append("mesh.verify_tcp must be host:port of a known authorized TCP service")
        else:
            m["verify_tcp"] = v
    if m["enabled"] and not m["token_ref"]:
        errors.append("mesh.token_ref is required when mesh.enabled is true")
    return out, errors


# --------------------------------------------------------------------------- detection
def _run(runner: Runner, argv: list[str], timeout: float = 6.0) -> Any:
    return runner(argv, timeout)


def os_release(read: Callable[[], str] | None = None) -> dict[str, str]:
    try:
        text = read() if read else Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def platform_support(release: dict[str, str], machine: str | None = None, system: str | None = None) -> dict[str, Any]:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system != "Linux":
        return {"supported": False, "reason": f"Loopback's Mesh installer supports Linux only; {system} needs the Cloudflare One Client installed manually"}
    distro = (release.get("ID") or "").lower()
    version = release.get("VERSION_ID") or ""
    major = version.split(".")[0]
    arch = SUPPORTED_ARCH.get(machine)
    if not arch:
        return {"supported": False, "reason": f"architecture {machine} is not in the supported set (amd64, arm64)"}
    versions = SUPPORTED_PLATFORMS.get(distro)
    if versions is None:
        return {"supported": False, "reason": f"distribution '{distro or 'unknown'}' is not on the reviewed supported list", "arch": arch}
    if version not in versions and major not in versions:
        return {"supported": False, "reason": f"{distro} {version} is not on the reviewed supported list ({', '.join(sorted(versions))})", "arch": arch}
    family = "apt" if distro in {"ubuntu", "debian"} else "rpm"
    return {"supported": True, "distro": distro, "version": version, "codename": release.get("VERSION_CODENAME"), "arch": arch, "family": family}


def _kv(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…"


def detect(runner: Runner) -> dict[str, Any]:
    """Read-only state of the local Cloudflare One Client. Distinct failure states (D18/D19)."""
    ver = _run(runner, ["warp-cli", "--version"])
    if ver.returncode == 127:
        return {"state": "missing_binary", "installed": False, "remedy": "Choose Cloudflare Mesh in setup to plan an opt-in install."}
    version = (ver.stdout or "").strip().split()[-1] if (ver.stdout or "").strip() else None
    svc = _run(runner, ["systemctl", "is-active", "warp-svc"])
    service = (svc.stdout or "").strip() or ("unknown" if svc.returncode == 127 else "inactive")
    st = _run(runner, ["warp-cli", "--accept-tos", "status"])
    status_text = (st.stdout or "") + (st.stderr or "")
    low = status_text.lower()
    if st.returncode and ("unable to connect" in low or "daemon" in low or "connection refused" in low):
        return {"state": "service_down", "installed": True, "version": version, "service": service,
                "remedy": "Start the client service: sudo systemctl enable --now warp-svc"}
    connected = "status update: connected" in low or re.search(r"\bconnected\b", low) is not None and "disconnected" not in low
    reg = _run(runner, ["warp-cli", "--accept-tos", "registration", "show"])
    reg_low = ((reg.stdout or "") + (reg.stderr or "")).lower()
    if reg.returncode or "missing" in reg_low or "no registration" in reg_low or "registration missing" in low:
        return {"state": "unregistered", "installed": True, "version": version, "service": service,
                "remedy": "Enroll this host with a Mesh connector token: loopback mesh apply --settings <file>"}
    fields = _kv(reg.stdout or "")
    org = fields.get("organization") or fields.get("team") or fields.get("team name")
    device = fields.get("device id") or fields.get("id")
    account_type = fields.get("account type")
    return {"state": "connected" if connected else "registered_disconnected", "installed": True, "version": version,
            "service": service, "organization": org, "device_id": mask(device), "account_type": account_type,
            "connector": "connector" in reg_low or (account_type or "").lower() in {"connector", "mesh"},
            "status_line": status_text.strip().splitlines()[0][:160] if status_text.strip() else None}


# --------------------------------------------------------------------------- coexistence preflight (D21)
def preflight(settings: dict[str, Any], *, tailscale: dict[str, Any] | None = None,
              routes: list[str] | None = None, detection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Block unsafe routing before any change. Returns ``{ok, findings, blocking}``.

    The default Mesh range 100.96.0.0/12 lies inside Tailscale's 100.64.0.0/10. With
    Tailscale running, both can claim the same addresses. A finding is a risk
    statement, not proof of a live collision.
    """
    m = settings["mesh"]
    mesh_net = ipaddress.ip_network(m.get("mesh_range") or DEFAULT_MESH_RANGE)
    ts_net = ipaddress.ip_network(TAILSCALE_RANGE)
    ts = tailscale or {}
    findings: list[dict[str, Any]] = []
    ts_active = ts.get("state") == "running"
    overlap = mesh_net.overlaps(ts_net)
    if overlap:
        findings.append({"id": "range_overlap", "severity": "high" if ts_active else "info",
                         "detail": f"Mesh range {mesh_net} overlaps the Tailscale range {ts_net}."})
    inside = []
    for addr in ts.get("self_addresses") or []:
        try:
            if ipaddress.ip_address(addr) in mesh_net:
                inside.append(addr)
        except ValueError:
            continue
    if inside:
        findings.append({"id": "tailscale_address_in_mesh_range", "severity": "high",
                         "detail": f"{len(inside)} Tailscale address(es) on this host fall inside the Mesh range."})
    route_hits = []
    for r in routes or []:
        try:
            if ipaddress.ip_network(r, strict=False).overlaps(mesh_net):
                route_hits.append(r)
        except ValueError:
            continue
    if route_hits:
        findings.append({"id": "existing_routes_overlap", "severity": "high",
                         "detail": "Existing routes overlap the Mesh range: " + ", ".join(sorted(route_hits)[:10])})
    det = detection or {}
    if det.get("state") in {"connected", "registered_disconnected"} and m.get("organization") and det.get("organization") \
            and det["organization"].lower() != m["organization"].lower():
        findings.append({"id": "organization_mismatch", "severity": "high",
                         "detail": f"This host is enrolled in organization '{det['organization']}', not '{m['organization']}'. "
                                   "Change accounts deliberately; Loopback never unregisters an existing enrollment."})
    if det.get("state") in {"connected", "registered_disconnected"} and not det.get("connector"):
        findings.append({"id": "device_enrollment_present", "severity": "medium",
                         "detail": "The client is enrolled as a user device, not a Mesh server node. Server-node enrollment uses a connector token."})
    if m.get("role") == "subnet_router":
        findings.append({"id": "forwarding_required", "severity": "medium",
                         "detail": "A subnet router enables IPv4 forwarding on this host. Choose it only to route a reviewed private subnet."})
    blocking = []
    mitigated = bool(m.get("acknowledge_tailscale_overlap") and m.get("overlap_mitigation"))
    if m.get("overlap_mitigation") == "custom_range" and overlap:
        mitigated = False  # a custom range must actually avoid the overlap
    for f in findings:
        if f["id"] in {"range_overlap", "tailscale_address_in_mesh_range", "existing_routes_overlap"} and f["severity"] == "high" and not mitigated:
            blocking.append(f["id"])
        if f["id"] == "organization_mismatch":
            blocking.append(f["id"])
    alternatives = []
    if blocking and any(b != "organization_mismatch" for b in blocking):
        alternatives = [
            {"id": "custom_range", "detail": "Configure a Mesh range outside 100.64.0.0/10 in Cloudflare (account-wide change; review impact first)."},
            {"id": "route_exclusions_reviewed", "detail": "Keep the default range and add reviewed split-tunnel exclusions for Tailscale addresses."},
            {"id": "isolated_namespace", "detail": "Run the client on a separate host or network namespace that does not use Tailscale."},
        ]
    return {"ok": not blocking, "findings": findings, "blocking": blocking, "alternatives": alternatives,
            "mesh_range": str(mesh_net), "tailscale_active": ts_active}


def read_routes(runner: Runner) -> list[str]:
    res = _run(runner, ["ip", "-j", "route", "show", "table", "all"])
    if res.returncode:
        return []
    try:
        data = json.loads(res.stdout or "[]")
    except ValueError:
        return []
    out = []
    for r in data if isinstance(data, list) else []:
        dst = r.get("dst") if isinstance(r, dict) else None
        if dst and dst not in {"default", "local", "broadcast"} and "/" in dst:
            out.append(dst)
    return out


# --------------------------------------------------------------------------- plan
def plan(settings: dict[str, Any], detection: dict[str, Any], check: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
    """Ordered, reviewable steps. Already-converged steps are marked ``skip`` so reruns do nothing new."""
    m = settings["mesh"]
    steps: list[dict[str, Any]] = []
    blocked: list[str] = []
    if not m["enabled"]:
        blocked.append("mesh.enabled is false: Mesh setup is opt-in")
    if not support.get("supported"):
        blocked.append(f"unsupported platform: {support.get('reason')}")
    blocked += [f"preflight: {b}" for b in check.get("blocking") or []]
    installed = bool(detection.get("installed"))
    family = support.get("family", "apt")
    pkg = "cloudflare-warp" + (f"={m['package_version']}" if m.get("package_version") and family == "apt" else
                               (f"-{m['package_version']}" if m.get("package_version") else ""))
    if family == "apt":
        steps.append({"id": "repo", "title": "Add the signed Cloudflare package repository", "privilege": "root",
                      "skip": installed, "owned": "apt_repo",
                      "commands": [["curl", "-fsSL", APT_KEY_URL, "-o", "/tmp/loopback-cloudflare-warp.gpg"],
                                   ["gpg", "--batch", "--yes", "--dearmor", "-o", "/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg",
                                    "/tmp/loopback-cloudflare-warp.gpg"],
                                   ["sh", "-c", "echo 'deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] "
                                    f"{APT_REPO} {support.get('codename') or '$(lsb_release -cs)'} main' > /etc/apt/sources.list.d/cloudflare-client.list"],
                                   ["apt-get", "update"]],
                      "rollback": [["rm", "-f", "/etc/apt/sources.list.d/cloudflare-client.list",
                                    "/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg"]]})
        steps.append({"id": "package", "title": f"Install {pkg} (package signature checked by apt)", "privilege": "root",
                      "skip": installed, "owned": "package", "commands": [["apt-get", "install", "-y", pkg]],
                      "rollback": [["apt-get", "remove", "-y", "cloudflare-warp"]]})
    else:
        steps.append({"id": "package", "title": f"Install {pkg} from the Cloudflare rpm repository", "privilege": "root",
                      "skip": installed, "owned": "package",
                      "commands": [["sh", "-c", "curl -fsSL https://pkg.cloudflareclient.com/cloudflare-warp-ascii.repo > /etc/yum.repos.d/cloudflare-warp.repo"],
                                   ["dnf", "install", "-y", pkg]],
                      "rollback": [["dnf", "remove", "-y", "cloudflare-warp"], ["rm", "-f", "/etc/yum.repos.d/cloudflare-warp.repo"]]})
    steps.append({"id": "service", "title": "Enable and start warp-svc", "privilege": "root",
                  "skip": detection.get("service") == "active", "owned": "service",
                  "commands": [["systemctl", "enable", "--now", "warp-svc"]],
                  "rollback": [["systemctl", "disable", "--now", "warp-svc"]]})
    enrolled = detection.get("state") in {"connected", "registered_disconnected"}
    steps.append({"id": "enroll", "title": "Enroll this host as a Mesh server node with its own connector token",
                  "privilege": "root", "skip": enrolled, "owned": "registration", "secret": "token_ref",
                  "commands": [["warp-cli", "--accept-tos", "connector", "new", "{TOKEN}"]],
                  "exposure": "warp-cli takes the token as an argument; it is briefly visible to local root and this user in the process table.",
                  "rollback": [["warp-cli", "--accept-tos", "registration", "delete"]]})
    steps.append({"id": "connect", "title": "Connect the client", "privilege": "root",
                  "skip": detection.get("state") == "connected", "owned": None,
                  "commands": [["warp-cli", "--accept-tos", "connect"]], "rollback": [["warp-cli", "--accept-tos", "disconnect"]]})
    if m["role"] == "subnet_router":
        steps.append({"id": "forwarding", "title": "Enable IPv4 forwarding for the reviewed subnet-router role", "privilege": "root",
                      "skip": False, "owned": "sysctl",
                      "commands": [["sh", "-c", "echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-loopback-mesh.conf && sysctl -p /etc/sysctl.d/99-loopback-mesh.conf"]],
                      "rollback": [["sh", "-c", "rm -f /etc/sysctl.d/99-loopback-mesh.conf && sysctl -w net.ipv4.ip_forward=0"]]})
    manual = ["Create the Mesh node and its connector token in the Cloudflare dashboard (Networks > Mesh); "
              "Loopback cannot create account resources without an API token."]
    if m.get("mesh_range") != DEFAULT_MESH_RANGE:
        manual.append(f"Set the account Mesh range to {m['mesh_range']} in Cloudflare before enrolling (account-wide).")
    return {"id": "meshplan_" + uuid.uuid4().hex[:10], "settings": settings, "steps": steps, "blocked": blocked,
            "ready": not blocked, "manual_steps": manual, "preflight": check, "detection": detection, "platform": support,
            "verify": ["warp-cli status reports Connected", "registration shows the expected organization",
                       f"TCP connect to {m['verify_tcp']}" if m.get("verify_tcp") else "TCP connect to a known authorized service (set mesh.verify_tcp)"],
            "after": "Discovery lists this host. Install a Loopback worker separately through SSH; a Mesh connection grants no shell access."}


# --------------------------------------------------------------------------- apply / rollback (owner-side)
def resolve_secret(ref: str, *, environ: dict[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    if ref.startswith("env:"):
        value = environ.get(ref[4:], "")
    elif ref.startswith("file:"):
        path = Path(ref[5:])
        st = path.stat()
        if st.st_mode & 0o077:
            raise MeshError("token_file_permissions", f"{path} must not be readable by group or others (chmod 600)")
        value = path.read_text(encoding="utf-8").strip()
    else:
        raise MeshError("token_ref_invalid", "token_ref must be env:NAME or file:/path")
    if not value:
        raise MeshError("token_missing", "the referenced token is empty or unset")
    return value


class Journal:
    """Append-only record of owned resources and completed steps (mode 0600)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": 1, "runs": [], "owned": [], "completed": []}

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1)
            os.replace(tmp, self.path)


def _scrub(text: str, secret: str | None) -> str:
    out = text or ""
    if secret:
        out = out.replace(secret, "[REDACTED]")
    from redaction import redact

    return redact(out)[:600]


def apply(plan_data: dict[str, Any], runner: Runner, journal: Journal, *, secret_resolver: Callable[[str], str] = resolve_secret,
          is_root: Callable[[], bool] = lambda: hasattr(os, "geteuid") and os.geteuid() == 0,
          clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Execute a ready plan. Each completed step is journaled before the next starts (resume-safe)."""
    if not plan_data.get("ready"):
        raise MeshError("plan_blocked", "; ".join(plan_data.get("blocked") or ["plan is not ready"]))
    pending = [s for s in plan_data["steps"] if not s.get("skip")]
    if pending and not is_root():
        raise MeshError("privilege_required", "run 'sudo loopback mesh apply' (package, service and enrollment need root)")
    data = journal.load()
    run = {"plan_id": plan_data["id"], "started_at": clock(), "steps": [], "status": "running"}
    data["runs"].append(run)
    journal.save(data)
    token: str | None = None
    for step in plan_data["steps"]:
        record = {"id": step["id"], "outcome": None}
        if step.get("skip"):
            record["outcome"] = "already_converged"
            run["steps"].append(record)
            continue
        if step["id"] in data["completed"] and step["id"] != "connect":
            record["outcome"] = "done_in_earlier_run"
            run["steps"].append(record)
            continue
        if step.get("secret"):
            ref = plan_data["settings"]["mesh"].get("token_ref")
            token = secret_resolver(ref)
        for argv in step["commands"]:
            real = [token if a == "{TOKEN}" else a for a in argv]
            res = runner(real, 180.0)
            if res.returncode != 0:
                record["outcome"] = "failed"
                record["error"] = _scrub((res.stderr or res.stdout or f"exit {res.returncode}").strip(), token)
                record["command"] = " ".join("[TOKEN]" if a == "{TOKEN}" else a for a in argv)
                run["steps"].append(record)
                run["status"] = "failed"
                run["finished_at"] = clock()
                journal.save(data)
                return {"status": "failed", "failed_step": step["id"], "error": record["error"], "journal": str(journal.path),
                        "resume": "Fix the cause and rerun 'loopback mesh apply'; completed steps are skipped."}
        record["outcome"] = "done"
        run["steps"].append(record)
        data["completed"] = sorted(set(data["completed"]) | {step["id"]})
        if step.get("owned") and not any(o["kind"] == step["owned"] for o in data["owned"]):
            data["owned"].append({"kind": step["owned"], "step": step["id"], "rollback": step.get("rollback") or [], "created_at": clock()})
        journal.save(data)
    token = None
    run["status"] = "applied"
    run["finished_at"] = clock()
    journal.save(data)
    return {"status": "applied", "steps": run["steps"], "journal": str(journal.path)}


def verify(runner: Runner, settings: dict[str, Any], *, tcp_check: Callable[[str, int], bool] | None = None) -> dict[str, Any]:
    det = detect(runner)
    checks = [{"id": "registration", "outcome": "PASS" if det["state"] in {"connected", "registered_disconnected"} else "FAIL", "detail": det["state"]},
              {"id": "connected", "outcome": "PASS" if det["state"] == "connected" else "FAIL", "detail": det.get("status_line")}]
    org = settings["mesh"].get("organization")
    if org:
        checks.append({"id": "organization", "outcome": "PASS" if (det.get("organization") or "").lower() == org.lower() else "FAIL",
                       "detail": det.get("organization")})
    target = settings["mesh"].get("verify_tcp")
    if target:
        host, port = target.rsplit(":", 1)
        ok = (tcp_check or _tcp_check)(host, int(port))
        checks.append({"id": "tcp_service", "outcome": "PASS" if ok else "FAIL", "detail": target})
    else:
        checks.append({"id": "tcp_service", "outcome": "NOT TESTED", "detail": "set mesh.verify_tcp to a known authorized service"})
    return {"detection": det, "checks": checks, "ok": all(c["outcome"] in {"PASS", "NOT TESTED"} for c in checks)}


def _tcp_check(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def rollback(runner: Runner, journal: Journal, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Undo only journaled resources, newest first. A pre-existing enrollment is never in the journal."""
    data = journal.load()
    removed, failed = [], []
    for owned in reversed(list(data["owned"])):
        ok = True
        for argv in owned.get("rollback") or []:
            res = runner(argv, 120.0)
            if res.returncode != 0:
                ok = False
                failed.append({"kind": owned["kind"], "error": _scrub(res.stderr or res.stdout or "", None)})
                break
        if ok:
            removed.append(owned["kind"])
            data["owned"].remove(owned)
            data["completed"] = [c for c in data["completed"] if c != owned["step"]]
    data["runs"].append({"rollback_at": clock(), "removed": removed, "failed": failed})
    journal.save(data)
    return {"removed": removed, "failed": failed, "remaining_owned": [o["kind"] for o in data["owned"]]}


# --------------------------------------------------------------------------- dashboard summary
def summary(runner: Runner, settings: dict[str, Any], *, tailscale: dict[str, Any] | None = None,
            release_reader: Callable[[], str] | None = None, machine: str | None = None, system: str | None = None) -> dict[str, Any]:
    det = detect(runner)
    check = preflight(settings, tailscale=tailscale, routes=read_routes(runner), detection=det)
    support = platform_support(os_release(release_reader), machine, system)
    p = plan(settings, det, check, support)
    return {"product": "Cloudflare Mesh (Cloudflare One Client: warp-svc / warp-cli)",
            "distinct_from": "Cloudflare Tunnel (cloudflared) publishes hostnames; configure it under Public tunnels.",
            "detection": det, "preflight": check, "platform": support,
            "plan": {k: p[k] for k in ("ready", "blocked", "manual_steps", "verify", "after")} | {
                "steps": [{k: s.get(k) for k in ("id", "title", "privilege", "skip", "exposure")} for s in p["steps"]]},
            "apply_command": "sudo loopback mesh apply --settings /path/to/mesh-settings.json",
            "live_enrollment": "UNVERIFIED until the isolated lab runs it"}
