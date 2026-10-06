from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_POLICY: dict[str, Any] = {
    "profile": "standard",
    "allowed_roots": ["~"],
    "denied_roots": [
        "~/.ssh",
        "~/.gnupg",
        "~/.aws",
        "~/.config/1Password",
        "~/.config/gh/hosts.yml",
    ],
    "allow_shell": True,
    "allow_process_control": True,
    "allow_fleet": True,
    "require_approval_patterns": [
        r"(^|\s)sudo(\s|$)",
        r"\b(rm|unlink)\b.*\s(-[^\n]*r|--recursive)",
        r"\b(systemctl|service)\s+(stop|disable|mask)\b",
        r"\b(docker|podman)\s+(rm|system\s+prune|volume\s+rm)\b",
        r"\b(apt|apt-get|dnf|yum|pacman)\b.*\b(remove|purge|autoremove)\b",
        r"\b(shutdown|reboot|poweroff|halt)\b",
    ],
    "deny_command_patterns": [
        r"(^|\s)rm\s+-rf\s+/(\s|$)",
        r"\bmkfs(\.|\s)",
        r"\bwipefs\b",
        r"\bdd\b[^\n]*\bof=/dev/",
        r":\(\)\s*\{\s*:\|:&\s*\};:",
    ],
    "approval_ttl_seconds": 900,
}


class PolicyError(PermissionError):
    pass


class ApprovalRequired(PolicyError):
    def __init__(self, approval_id: str, message: str):
        super().__init__(message)
        self.approval_id = approval_id


@dataclass
class Approval:
    id: str
    fingerprint: str
    summary: str
    created_at: float
    expires_at: float
    approved: bool = False
    uses_left: int = 1


class PolicyStore:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.path = config_dir / "policy.json"
        self.approvals_path = config_dir / "approvals.json"
        self._lock = threading.RLock()
        self._approvals: dict[str, Approval] = {}
        self.reload()
        self._load_approvals()

    def reload(self) -> None:
        with self._lock:
            data = dict(DEFAULT_POLICY)
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data.update(raw)
                except (OSError, json.JSONDecodeError):
                    pass
            self.data = data

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = dict(self.data)
            merged.update(updates)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            self.data = merged
            return dict(self.data)

    @property
    def profile(self) -> str:
        return str(self.data.get("profile", "standard")).lower()

    def _expanded(self, values: list[str]) -> list[Path]:
        return [Path(v).expanduser().resolve(strict=False) for v in values]

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return path == root

    def check_path(self, value: str | Path, *, write: bool = False) -> Path:
        p = Path(value).expanduser().resolve(strict=False)
        if self.profile == "locked":
            raise PolicyError("Loopback policy is locked")
        if write and self.profile == "read-only":
            raise PolicyError("Loopback policy is read-only")

        denied = self._expanded(list(self.data.get("denied_roots") or []))
        if any(self._within(p, root) for root in denied):
            raise PolicyError(f"path is denied by policy: {p}")

        allowed = self._expanded(list(self.data.get("allowed_roots") or ["~"]))
        if allowed and not any(self._within(p, root) for root in allowed):
            raise PolicyError(f"path is outside allowed roots: {p}")
        return p

    def _fingerprint(self, action: str, value: str) -> str:
        return hashlib.sha256(f"{action}\0{value}".encode()).hexdigest()

    def _load_approvals(self) -> None:
        with self._lock:
            try:
                rows = json.loads(self.approvals_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rows = []
            now = time.time()
            self._approvals = {}
            for row in rows if isinstance(rows, list) else []:
                try:
                    a = Approval(**row)
                except TypeError:
                    continue
                if a.expires_at > now and a.uses_left > 0:
                    self._approvals[a.id] = a

    def _save_approvals(self) -> None:
        rows = [a.__dict__ for a in self._approvals.values()]
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.approvals_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.approvals_path)

    def request_approval(self, action: str, value: str, summary: str) -> Approval:
        with self._lock:
            fp = self._fingerprint(action, value)
            now = time.time()
            for a in self._approvals.values():
                if a.fingerprint == fp and a.expires_at > now and a.uses_left > 0:
                    return a
            aid = secrets.token_urlsafe(12)
            a = Approval(
                id=aid,
                fingerprint=fp,
                summary=summary,
                created_at=now,
                expires_at=now + int(self.data.get("approval_ttl_seconds", 900)),
            )
            self._approvals[aid] = a
            self._save_approvals()
            return a

    def approve(self, approval_id: str, *, uses: int = 1, ttl_seconds: int | None = None) -> dict[str, Any]:
        with self._lock:
            a = self._approvals.get(approval_id)
            if not a:
                raise KeyError("approval not found")
            a.approved = True
            a.uses_left = max(1, min(int(uses), 100))
            if ttl_seconds is not None:
                a.expires_at = time.time() + max(30, min(int(ttl_seconds), 86400))
            self._save_approvals()
            return a.__dict__.copy()

    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                a.__dict__.copy()
                for a in self._approvals.values()
                if a.expires_at > now and a.uses_left > 0 and not a.approved
            ]

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
            self._save_approvals()
            return True

    def check_command(self, command: str, *, approval_id: str | None = None) -> None:
        if self.profile in {"locked", "read-only"} or not bool(self.data.get("allow_shell", True)):
            raise PolicyError("shell execution is disabled by policy")

        for pattern in self.data.get("deny_command_patterns") or []:
            if re.search(pattern, command, re.I | re.M):
                raise PolicyError("command is blocked by policy")

        if self.profile == "trusted":
            return

        for pattern in self.data.get("require_approval_patterns") or []:
            if re.search(pattern, command, re.I | re.M):
                if self._consume(approval_id, "command", command):
                    return
                a = self.request_approval(
                    "command",
                    command,
                    "Sensitive shell command requires Loopback approval",
                )
                raise ApprovalRequired(
                    a.id,
                    f"approval_required:{a.id}: approve it in the Loopback dashboard or switch the policy profile to trusted",
                )

    def check_process_control(self) -> None:
        if self.profile in {"locked", "read-only"} or not bool(self.data.get("allow_process_control", True)):
            raise PolicyError("process control is disabled by policy")

    def check_fleet(self) -> None:
        if self.profile == "locked" or not bool(self.data.get("allow_fleet", True)):
            raise PolicyError("fleet routing is disabled by policy")
