"""Machine-side policy: profiles, filesystem roots, command rules, approvals.

Policy decisions are made here; OS enforcement for subprocesses is delegated to
``sandbox.py``. Every check takes an explicit *target* so a folder granted for
one remote machine never widens access on the local machine or another node.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DENIED_ROOTS = [
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker/config.json",
    "~/.config/1Password",
    "~/.config/gh/hosts.yml",
    "~/.config/loopback",
    "~/.netrc",
]

SOFT_APPROVAL_PATTERNS = [
    r"(^|\s)sudo(\s|$)",
    r"\b(rm|unlink)\b.*\s(-[^\n]*r|--recursive)",
    r"\b(systemctl|service)\s+(stop|disable|mask)\b",
    r"\b(docker|podman)\s+(rm|system\s+prune|volume\s+rm)\b",
    r"\b(apt|apt-get|dnf|yum|pacman|zypper|apk)\b.*\b(remove|purge|autoremove|del)\b",
    r"\b(shutdown|reboot|poweroff|halt)\b",
    r"\bgit\s+push\b.*\s(--force|-f)\b",
]

DEV_APPROVAL_PATTERNS = [
    r"(^|\s)sudo(\s|$)",
    r"\b(systemctl|service)\s+(stop|disable|mask)\b",
    r"\b(shutdown|reboot|poweroff|halt)\b",
]

HARD_DENY_PATTERNS = [
    r"(^|\s)rm\s+-[a-z]*r[a-z]*\s+(/|~|\$HOME)(\s|$)",
    r"\bmkfs(\.|\s)",
    r"\bwipefs\b",
    r"\bdd\b[^\n]*\bof=/dev/",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bchmod\s+(-R\s+)?[0-7]*777\s+/(\s|$)",
]

PROFILES: dict[str, dict[str, Any]] = {
    "locked": {
        "title": "Locked",
        "summary": "Emergency stop. No file, shell, process or fleet access.",
        "allow_shell": False, "allow_process_control": False, "allow_fleet": False,
        "file_mode": "none", "approvals": "all", "sandbox_required": False,
    },
    "read-only": {
        "title": "Read-only diagnostics",
        "summary": "Read files and run commands with every permitted location mounted read-only. Requires an active OS sandbox for shell access.",
        "allow_shell": True, "allow_process_control": False, "allow_fleet": True,
        "file_mode": "ro", "approvals": "all", "sandbox_required": True,
    },
    "restricted": {
        "title": "Restricted workspace",
        "summary": "Read/write inside permitted locations (your home workspace by default). Sensitive commands need a dashboard approval. OS sandbox required for shell access.",
        "allow_shell": True, "allow_process_control": True, "allow_fleet": True,
        "file_mode": "policy", "approvals": "standard", "sandbox_required": True,
    },
    "development": {
        "title": "Development",
        "summary": "Restricted workspace plus temporary directories and fewer approval prompts for everyday developer tooling. OS sandbox required.",
        "allow_shell": True, "allow_process_control": True, "allow_fleet": True,
        "file_mode": "policy", "approvals": "development", "sandbox_required": True,
    },
    "admin": {
        "title": "Authorized server administration",
        "summary": "No soft approval prompts; hard-deny rules still apply. The sandbox is used when available but not required. Only for machines you deliberately administer through AI.",
        "allow_shell": True, "allow_process_control": True, "allow_fleet": True,
        "file_mode": "policy", "approvals": "hard-only", "sandbox_required": False,
    },
    "custom": {
        "title": "Custom",
        "summary": "Every switch below is honoured exactly as saved.",
        "allow_shell": None, "allow_process_control": None, "allow_fleet": None,
        "file_mode": "policy", "approvals": "standard", "sandbox_required": None,
    },
}
PROFILE_ALIASES = {"standard": "restricted", "trusted": "admin"}


def canonical_profile(name: str | None) -> str:
    key = str(name or "restricted").lower().strip()
    key = PROFILE_ALIASES.get(key, key)
    return key if key in PROFILES else "restricted"


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "profile": "restricted",
        "allowed_roots": [{"path": "~", "mode": "rw"}],
        "denied_roots": list(DEFAULT_DENIED_ROOTS),
        "allow_shell": True,
        "allow_process_control": True,
        "allow_fleet": True,
        "allow_browser": False,
        "allow_desktop": False,
        "allowed_tools": ["*"],
        "denied_tools": [],
        "require_approval_patterns": list(SOFT_APPROVAL_PATTERNS),
        "deny_command_patterns": list(HARD_DENY_PATTERNS),
        "approval_ttl_seconds": 900,
        "sandbox_required": True,
        "targets": {},
    }


DEFAULT_POLICY = default_policy()  # backwards-compatible name


class PolicyError(PermissionError):
    pass


class ApprovalRequired(PolicyError):
    def __init__(self, approval_id: str, message: str):
        super().__init__(message)
        self.approval_id = approval_id


class StaleDecision(ValueError):
    """The approval was already decided, expired, or changed since the operator viewed it."""


@dataclass
class Approval:
    id: str
    fingerprint: str
    summary: str
    created_at: float
    expires_at: float
    approved: bool = False
    uses_left: int = 1
    target: str = "local"
    # Safe structured descriptor (v2). The raw command is bound only through ``fingerprint``;
    # ``detail`` holds a redacted presentation with every hidden value marked.
    action: str = "command"
    detail: dict[str, Any] = field(default_factory=dict)
    principal: str | None = None
    principal_key: str | None = None
    status: str = "pending"
    decided_at: float | None = None
    decided_by: str | None = None
    reason: str | None = None
    grant: dict[str, Any] = field(default_factory=dict)

    def view(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        data = dict(self.__dict__)
        data.pop("principal_key", None)
        if data["status"] in {"pending", "approved"} and self.expires_at <= now:
            data["status"] = "expired"
        data["fingerprint_short"] = self.fingerprint[:12]
        data["expires_in_seconds"] = max(0, int(self.expires_at - now))
        return data


def normalize_roots(values: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in values or []:
        if isinstance(item, str) and item.strip():
            out.append({"path": item.strip(), "mode": "rw"})
        elif isinstance(item, dict) and str(item.get("path", "")).strip():
            mode = str(item.get("mode", "rw")).lower()
            out.append({"path": str(item["path"]).strip(), "mode": "ro" if mode in {"ro", "read", "read-only", "readonly"} else "rw"})
    return out


def validate_policy(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    profile = str(data.get("profile", "restricted"))
    if canonical_profile(profile) != PROFILE_ALIASES.get(profile, profile):
        errors.append(f"unknown profile: {profile}")
    for key in ("allowed_roots", "denied_roots", "allowed_tools", "denied_tools", "require_approval_patterns", "deny_command_patterns"):
        if key in data and not isinstance(data[key], list):
            errors.append(f"{key} must be a list")
    for root in normalize_roots(data.get("allowed_roots", [])):
        if "\x00" in root["path"]:
            errors.append("allowed_roots entries must not contain NUL bytes")
    for key in ("require_approval_patterns", "deny_command_patterns"):
        for pattern in data.get(key, []) or []:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                errors.append(f"{key} contains an invalid regex ({pattern!r}): {exc}")
    for key in ("allow_shell", "allow_process_control", "allow_fleet", "allow_browser", "allow_desktop", "sandbox_required"):
        if key in data and not isinstance(data[key], bool):
            errors.append(f"{key} must be true or false")
    ttl = data.get("approval_ttl_seconds", 900)
    if not isinstance(ttl, int) or not (30 <= ttl <= 86400):
        errors.append("approval_ttl_seconds must be between 30 and 86400")
    targets = data.get("targets", {})
    if not isinstance(targets, dict):
        errors.append("targets must be an object keyed by node name")
    else:
        for name, tp in targets.items():
            if not isinstance(tp, dict):
                errors.append(f"targets.{name} must be an object")
                continue
            if "profile" in tp and canonical_profile(tp["profile"]) != PROFILE_ALIASES.get(tp["profile"], tp["profile"]):
                errors.append(f"targets.{name}.profile is unknown")
    return errors


class PolicyStore:
    def __init__(self, config_dir: Path, *, home: str | None = None):
        self.config_dir = Path(config_dir)
        self.path = self.config_dir / "policy.json"
        self.approvals_path = self.config_dir / "approvals.json"
        self.home = home or os.path.expanduser("~")
        self._lock = threading.RLock()
        self._approvals: dict[str, Approval] = {}
        self.sandbox_active = False  # set by the server once the sandbox manager is ready
        self.sandbox_acknowledged = False
        self.reload()
        self._load_approvals()

    # ------------------------------------------------------------ persistence
    def reload(self) -> None:
        with self._lock:
            data = default_policy()
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data.update(raw)
                except (OSError, json.JSONDecodeError):
                    pass
            data["profile"] = canonical_profile(data.get("profile"))
            data["allowed_roots"] = normalize_roots(data.get("allowed_roots") or [{"path": "~", "mode": "rw"}])
            data.setdefault("targets", {})
            data["schema_version"] = 2
            self.data = data

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = dict(self.data)
            merged.update(updates)
            if "profile" in updates:
                merged["profile"] = canonical_profile(updates["profile"])
                merged.update(self._profile_switches(merged["profile"]))
            merged["allowed_roots"] = normalize_roots(merged.get("allowed_roots"))
            errors = validate_policy(merged)
            if errors:
                raise ValueError("; ".join(errors))
            self._write(self.path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
            self.data = merged
            return dict(self.data)

    def _profile_switches(self, profile: str) -> dict[str, Any]:
        spec = PROFILES[profile]
        out: dict[str, Any] = {}
        for key in ("allow_shell", "allow_process_control", "allow_fleet", "sandbox_required"):
            if spec.get(key) is not None:
                out[key] = spec[key]
        if spec["approvals"] == "development":
            out["require_approval_patterns"] = list(DEV_APPROVAL_PATTERNS)
        elif spec["approvals"] == "standard":
            out["require_approval_patterns"] = list(SOFT_APPROVAL_PATTERNS)
        return out

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    # ------------------------------------------------------------- accessors
    @property
    def profile(self) -> str:
        return canonical_profile(self.data.get("profile"))

    def target_policy(self, target: str = "local") -> dict[str, Any]:
        """Effective policy for one target. Remote targets never inherit local roots."""
        if target in {"local", "", None}:
            return dict(self.data)
        tp = dict((self.data.get("targets") or {}).get(target) or {})
        base = {
            "profile": canonical_profile(tp.get("profile") or self.profile),
            "allowed_roots": normalize_roots(tp.get("allowed_roots") or [{"path": "~", "mode": "rw"}]),
            "denied_roots": list(tp.get("denied_roots") if tp.get("denied_roots") is not None else DEFAULT_DENIED_ROOTS),
            "allow_shell": bool(tp.get("allow_shell", True)),
            "allow_process_control": bool(tp.get("allow_process_control", True)),
            "require_approval_patterns": list(tp.get("require_approval_patterns") or self.data.get("require_approval_patterns") or []),
            "deny_command_patterns": list(self.data.get("deny_command_patterns") or []),
            "enforcement": tp.get("enforcement", "gateway-path-check"),
        }
        return base

    def set_target_policy(self, target: str, updates: dict[str, Any]) -> dict[str, Any]:
        if target in {"local", ""}:
            raise ValueError("use the main policy for the local machine")
        with self._lock:
            targets = dict(self.data.get("targets") or {})
            current = dict(targets.get(target) or {})
            current.update(updates)
            if "allowed_roots" in current:
                current["allowed_roots"] = normalize_roots(current["allowed_roots"])
            targets[target] = current
            self.save({"targets": targets})
            return current

    def remove_target_policy(self, target: str) -> bool:
        with self._lock:
            targets = dict(self.data.get("targets") or {})
            existed = targets.pop(target, None) is not None
            self.save({"targets": targets})
            return existed

    def root_entries(self, target: str = "local") -> list[dict[str, str]]:
        return normalize_roots(self.target_policy(target).get("allowed_roots"))

    def _expand(self, value: str) -> Path:
        text = str(value)
        if text == "~" or text.startswith("~/"):
            text = self.home + text[1:]
        return Path(text).resolve(strict=False)

    def _expanded_roots(self, values: list[Any]) -> list[tuple[Path, str]]:
        out = []
        for entry in normalize_roots(values):
            out.append((self._expand(entry["path"]), entry["mode"]))
        return out

    def _expanded_denied(self, values: list[str]) -> list[Path]:
        return [self._expand(v) for v in values or []]

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    # ---------------------------------------------------------------- checks
    def check_path(self, value: str | Path, *, write: bool = False, target: str = "local") -> Path:
        tp = self.target_policy(target)
        profile = canonical_profile(tp.get("profile"))
        raw = str(value)
        if raw == "~" or raw.startswith("~/"):
            raw = self.home + raw[1:]
        p = Path(raw).expanduser().resolve(strict=False)
        if profile == "locked":
            raise PolicyError("Loopback policy is locked")
        if write and profile == "read-only":
            raise PolicyError("Loopback policy is read-only")
        for root in self._expanded_denied(tp.get("denied_roots") or []):
            if self._within(p, root):
                raise PolicyError(f"path is denied by policy: {p}")
        roots = self._expanded_roots(tp.get("allowed_roots") or [])
        matched = [mode for root, mode in roots if self._within(p, root)]
        if roots and not matched:
            raise PolicyError(f"path is outside allowed roots: {p}")
        if write and matched and all(mode == "ro" for mode in matched):
            raise PolicyError(f"path is read-only by policy: {p}")
        return p

    def shell_enabled(self, target: str = "local") -> tuple[bool, str | None]:
        tp = self.target_policy(target)
        profile = canonical_profile(tp.get("profile"))
        if profile == "locked":
            return False, "policy profile is locked"
        if not bool(tp.get("allow_shell", True)):
            return False, "shell execution is disabled by policy"
        if target not in {"local", "", None}:
            return True, None
        spec = PROFILES[profile]
        required = self.data.get("sandbox_required") if spec["sandbox_required"] is None else spec["sandbox_required"]
        if required and not self.sandbox_active and not self.sandbox_acknowledged:
            return False, ("this profile requires an active OS sandbox but none is available; install bubblewrap, use a "
                           "Landlock-capable kernel, switch to the admin profile, or acknowledge unenforced shell access in the dashboard")
        return True, None

    def check_command(self, command: str, *, approval_id: str | None = None, target: str = "local", cwd: str | None = None,
                      principal_key: str | None = None, principal: str | None = None) -> None:
        ok, reason = self.shell_enabled(target)
        if not ok:
            raise PolicyError(reason or "shell execution is disabled by policy")
        tp = self.target_policy(target)
        profile = canonical_profile(tp.get("profile"))
        for pattern in tp.get("deny_command_patterns") or []:
            if re.search(pattern, command, re.I | re.M):
                raise PolicyError("command is blocked by policy (hard deny rule)")
        if PROFILES[profile]["approvals"] == "hard-only":
            return
        for pattern in tp.get("require_approval_patterns") or []:
            if re.search(pattern, command, re.I | re.M):
                # The fingerprint binds command, cwd, target and caller: any change needs a new decision.
                value = f"{target}\0{command}\0{cwd or ''}\0{principal_key or ''}"
                if self._consume(approval_id, "command", value):
                    return
                from redaction import redact_text

                shown, hidden = redact_text(command)
                detail = {"command": shown, "cwd": cwd, "target": target, "matched_rule": pattern,
                          "redacted_values": hidden, "length": len(command), "lines": command.count("\n") + 1,
                          "privilege": "sudo" if re.search(r"(^|[;&|]\s*)sudo\b", command) else "caller"}
                a = self.request_approval("command", value, f"Sensitive shell command on {target} requires Loopback approval",
                                          target=target, detail=detail, principal=principal, principal_key=principal_key)
                raise ApprovalRequired(
                    a.id,
                    f"approval_required:{a.id}: approve it in the Loopback dashboard (Security → Approvals) or use a less restrictive profile",
                )

    def check_tool(self, tool: str) -> None:
        name = str(tool).strip()
        if not name:
            raise PolicyError("tool name is required")
        denied = {str(x) for x in (self.data.get("denied_tools") or [])}
        if "*" in denied or name in denied:
            raise PolicyError(f"tool is denied by policy: {name}")
        allowed = {str(x) for x in (self.data.get("allowed_tools") or ["*"])}
        if "*" not in allowed and name not in allowed:
            raise PolicyError(f"tool is not in allowed_tools: {name}")

    def check_browser(self) -> None:
        if self.profile in {"locked", "read-only"} or not bool(self.data.get("allow_browser", False)):
            raise PolicyError("browser automation is disabled by policy")

    def check_desktop(self) -> None:
        if self.profile in {"locked", "read-only"} or not bool(self.data.get("allow_desktop", False)):
            raise PolicyError("native desktop automation is disabled by policy")

    def check_process_control(self) -> None:
        if self.profile in {"locked", "read-only"} or not bool(self.data.get("allow_process_control", True)):
            raise PolicyError("process control is disabled by policy")

    def check_fleet(self) -> None:
        if self.profile == "locked" or not bool(self.data.get("allow_fleet", True)):
            raise PolicyError("fleet routing is disabled by policy")

    def file_mode(self, target: str = "local") -> str:
        """``ro`` when the target profile forces read-only, else ``policy``."""
        profile = canonical_profile(self.target_policy(target).get("profile"))
        return PROFILES[profile]["file_mode"]

    def effective_permissions(self, target: str = "local") -> dict[str, Any]:
        tp = self.target_policy(target)
        profile = canonical_profile(tp.get("profile"))
        spec = PROFILES[profile]
        shell_ok, shell_reason = self.shell_enabled(target)
        roots = []
        for entry in normalize_roots(tp.get("allowed_roots")):
            resolved = self._expand(entry["path"])
            roots.append({"path": entry["path"], "resolved": str(resolved), "mode": "ro" if spec["file_mode"] == "ro" else entry["mode"],
                          "exists": resolved.exists()})
        return {
            "target": target,
            "profile": profile,
            "profile_title": spec["title"],
            "profile_summary": spec["summary"],
            "allowed_roots": roots,
            "denied_roots": list(tp.get("denied_roots") or []),
            "other_locations": "denied",
            "shell": {"enabled": shell_ok, "reason": shell_reason, "approvals": spec["approvals"]},
            "process_control": bool(tp.get("allow_process_control", True)) and profile not in {"locked", "read-only"},
            "fleet": bool(self.data.get("allow_fleet", True)) and profile != "locked",
            "sandbox_required": spec["sandbox_required"] if spec["sandbox_required"] is not None else bool(self.data.get("sandbox_required", True)),
            "sandbox_active": self.sandbox_active if target in {"local", ""} else None,
            "enforcement": "os-sandbox" if (target in {"local", ""} and self.sandbox_active) else tp.get("enforcement", "gateway-path-check"),
        }

    # ------------------------------------------------------------- approvals
    def _fingerprint(self, action: str, value: str) -> str:
        return hashlib.sha256(f"{action}\0{value}".encode()).hexdigest()

    HISTORY_LIMIT = 200
    HISTORY_SECONDS = 7 * 86400

    def _load_approvals(self) -> None:
        with self._lock:
            try:
                rows = json.loads(self.approvals_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rows = []
            now = time.time()
            self._approvals = {}
            self._history: list[Approval] = []
            known = set(Approval.__dataclass_fields__)
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                try:
                    a = Approval(**{k: v for k, v in row.items() if k in known})
                except TypeError:
                    continue
                if a.status == "pending" and a.approved:
                    a.status = "approved"  # v1 records
                if a.status in {"pending", "approved"} and a.expires_at > now and a.uses_left > 0:
                    self._approvals[a.id] = a
                elif now - (a.decided_at or a.expires_at) < self.HISTORY_SECONDS:
                    if a.status in {"pending", "approved"}:
                        a.status = "expired" if a.uses_left > 0 else "consumed"
                    self._history.append(a)
            self._history = self._history[-self.HISTORY_LIMIT:]

    def _save_approvals(self) -> None:
        rows = [a.__dict__ for a in list(self._approvals.values()) + list(getattr(self, "_history", []))]
        self._write(self.approvals_path, json.dumps(rows, indent=2) + "\n")

    def _retire(self, a: Approval, status: str) -> None:
        self._approvals.pop(a.id, None)
        a.status = status
        if not hasattr(self, "_history"):
            self._history = []
        self._history.append(a)
        self._history = self._history[-self.HISTORY_LIMIT:]

    def request_approval(self, action: str, value: str, summary: str, *, target: str = "local", detail: dict[str, Any] | None = None,
                         principal: str | None = None, principal_key: str | None = None) -> Approval:
        with self._lock:
            fp = self._fingerprint(action, value)
            now = time.time()
            for a in self._approvals.values():
                if a.fingerprint == fp and a.expires_at > now and a.uses_left > 0:
                    return a
            aid = secrets.token_urlsafe(12)
            a = Approval(id=aid, fingerprint=fp, summary=summary, created_at=now,
                         expires_at=now + int(self.data.get("approval_ttl_seconds", 900)), target=target,
                         action=action, detail=dict(detail or {}), principal=principal, principal_key=principal_key)
            self._approvals[aid] = a
            self._save_approvals()
            return a

    def _decidable(self, approval_id: str, expected_fingerprint: str | None) -> Approval:
        a = self._approvals.get(approval_id)
        if not a:
            past = next((h for h in reversed(getattr(self, "_history", [])) if h.id == approval_id), None)
            if past is not None:
                raise StaleDecision(f"approval was already {past.status}" + (f" by {past.decided_by}" if past.decided_by else ""))
            raise KeyError("approval not found")
        if a.expires_at <= time.time():
            self._retire(a, "expired")
            self._save_approvals()
            raise StaleDecision("approval expired before the decision")
        if expected_fingerprint and not secrets.compare_digest(a.fingerprint[: len(expected_fingerprint)], expected_fingerprint):
            raise StaleDecision("the action changed since it was displayed; review it again")
        return a

    def approve(self, approval_id: str, *, uses: int = 1, ttl_seconds: int | None = None, by: str | None = None,
                expected_fingerprint: str | None = None) -> dict[str, Any]:
        with self._lock:
            a = self._decidable(approval_id, expected_fingerprint)
            if a.approved:
                raise StaleDecision("approval was already granted" + (f" by {a.decided_by}" if a.decided_by else ""))
            a.approved = True
            a.status = "approved"
            a.uses_left = max(1, min(int(uses), 100))
            if ttl_seconds is not None:
                a.expires_at = time.time() + max(30, min(int(ttl_seconds), 86400))
            a.decided_at = time.time()
            a.decided_by = by
            a.grant = {"uses": a.uses_left, "expires_at": a.expires_at,
                       "scope": "this exact command, working directory, target and caller"}
            self._save_approvals()
            return a.view()

    def deny(self, approval_id: str, *, reason: str | None = None, by: str | None = None,
             expected_fingerprint: str | None = None) -> bool:
        with self._lock:
            try:
                a = self._decidable(approval_id, expected_fingerprint)
            except KeyError:
                return False
            a.decided_at = time.time()
            a.decided_by = by
            a.reason = (reason or "").strip()[:500] or "Denied by an administrator"
            self._retire(a, "denied")
            self._save_approvals()
            return True

    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [a.view(now) for a in self._approvals.values() if a.expires_at > now and a.uses_left > 0 and not a.approved]

    def approvals_history(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = [a.view(now) for a in self._approvals.values()] + [a.view(now) for a in getattr(self, "_history", [])]
        if status:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: r.get("decided_at") or r["created_at"], reverse=True)
        return rows[: max(1, min(int(limit), 500))]

    def _consume(self, approval_id: str | None, action: str, value: str) -> bool:
        if not approval_id:
            return False
        with self._lock:
            a = self._approvals.get(approval_id)
            if not a or not a.approved or a.expires_at <= time.time() or a.uses_left <= 0:
                return False
            if not secrets.compare_digest(a.fingerprint, self._fingerprint(action, value)):
                return False
            a.uses_left -= 1
            if a.uses_left <= 0:
                self._retire(a, "consumed")
            self._save_approvals()
            return True
