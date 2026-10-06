from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

CFG = Path(os.environ.get("LOOPBACK_CONFIG_DIR", Path.home() / ".config" / "loopback"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


DEFAULT_POLICY: dict[str, Any] = {
    "profile": "standard",
    "filesystem": {
        "allow": ["~"],
        "deny": [
            "~/.ssh",
            "~/.gnupg",
            "~/.aws",
            "~/.azure",
            "~/.oci",
            "~/.1password",
            "~/.config/1Password",
            "~/.git-credentials",
        ],
    },
    "tools": {},
    "command": {
        "deny_patterns": [
            r"(^|\s)rm\s+-rf\s+/(\s|$)",
            r"(^|\s)mkfs(\.|\s)",
            r"(^|\s)dd\s+.*\bof=/dev/",
            r"(^|\s)(shutdown|reboot|poweroff)\b",
        ],
        "confirm_patterns": [
            r"(^|\s)(sudo|doas)\b",
            r"(^|\s)(apt|dnf|yum|pacman|brew)\s+(remove|uninstall|purge)\b",
            r"(^|\s)docker\s+(rm|rmi|system\s+prune)\b",
            r"(^|\s)systemctl\s+(stop|disable|mask)\b",
            r"(^|\s)git\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|push\s+.*--force)\b",
        ],
    },
}

PROFILE_TOOLS: dict[str, dict[str, bool]] = {
    "read-only": {
        "command_run": False, "command_start": False, "task_run": False,
        "terminal_start": False, "terminal_write": False, "terminal_read": False,
        "terminal_close": False, "process_list": True, "process_info": True,
        "process_kill": False, "node_command": False, "browser_control": False,
    },
    "standard": {
        "command_run": False, "command_start": False, "task_run": False,
        "terminal_start": False, "terminal_write": False, "terminal_read": True,
        "terminal_close": False, "process_list": True, "process_info": True,
        "process_kill": False, "node_command": False, "browser_control": False,
    },
    "trusted": {
        "command_run": True, "command_start": True, "task_run": True,
        "terminal_start": True, "terminal_write": True, "terminal_read": True,
        "terminal_close": True, "process_list": True, "process_info": True,
        "process_kill": True, "node_command": True, "browser_control": True,
    },
}


class Policy:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CFG / "policy.json")
        if not self.path.exists():
            self.save(DEFAULT_POLICY)

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = json.loads(json.dumps(DEFAULT_POLICY))
        value.setdefault("profile", "standard")
        value.setdefault("filesystem", json.loads(json.dumps(DEFAULT_POLICY["filesystem"])))
        value.setdefault("tools", {})
        value.setdefault("command", json.loads(json.dumps(DEFAULT_POLICY["command"])))
        if value["profile"] not in PROFILE_TOOLS:
            value["profile"] = "standard"
        value["effective_tools"] = {
            **PROFILE_TOOLS[value["profile"]],
            **value.get("tools", {}),
        }
        return value

    def save(self, value: dict[str, Any]) -> None:
        clean = dict(value)
        clean.pop("effective_tools", None)
        atomic_json(self.path, clean)

    def set_profile(self, profile: str) -> dict[str, Any]:
        if profile not in PROFILE_TOOLS:
            raise ValueError(f"unknown profile: {profile}")
        value = self.load()
        value["profile"] = profile
        value["tools"] = {}
        self.save(value)
        return self.load()

    def set_tool(self, tool: str, enabled: bool) -> dict[str, Any]:
        value = self.load()
        value.setdefault("tools", {})[tool] = bool(enabled)
        self.save(value)
        return self.load()

    def set_paths(self, allow: list[str], deny: list[str]) -> dict[str, Any]:
        value = self.load()
        value["filesystem"] = {"allow": allow or ["~"], "deny": deny or []}
        self.save(value)
        return self.load()

    def tool_allowed(self, tool: str) -> bool:
        return bool(self.load()["effective_tools"].get(tool, False))

    def require_tool(self, tool: str) -> None:
        if not self.tool_allowed(tool):
            profile = self.load()["profile"]
            raise PermissionError(
                f"{tool} is disabled by Loopback policy (profile={profile}). "
                "Enable it from the dashboard or CLI policy settings."
            )

    @staticmethod
    def _inside(child: Path, parent: Path) -> bool:
        return child == parent or parent in child.parents

    def path_allowed(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        cfg = self.load()["filesystem"]
        allow = [Path(x).expanduser().resolve() for x in cfg.get("allow", ["~"])]
        deny = [Path(x).expanduser().resolve() for x in cfg.get("deny", [])]
        if not any(self._inside(target, root) for root in allow):
            raise PermissionError(f"path is outside the Loopback allowlist: {target}")
        if any(self._inside(target, root) for root in deny):
            raise PermissionError(f"path is denied by Loopback policy: {target}")
        return target

    def command_decision(self, command: str) -> str:
        cfg = self.load()["command"]
        for pattern in cfg.get("deny_patterns", []):
            if re.search(pattern, command, flags=re.IGNORECASE):
                return "deny"
        for pattern in cfg.get("confirm_patterns", []):
            if re.search(pattern, command, flags=re.IGNORECASE):
                return "confirm"
        return "allow"


class ApprovalStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def request(self, command: str, ttl: int = 600) -> dict[str, Any]:
        item = {
            "id": "apr_" + secrets.token_urlsafe(12),
            "command": command,
            "created": time.time(),
            "expires": time.time() + ttl,
            "approved": False,
            "used": False,
        }
        with self.lock:
            self.items[item["id"]] = item
        return dict(item)

    def list(self) -> list[dict[str, Any]]:
        now = time.time()
        with self.lock:
            for key in list(self.items):
                item = self.items[key]
                if item["expires"] < now or item["used"]:
                    self.items.pop(key, None)
            return [dict(item) for item in self.items.values()]

    def approve(self, approval_id: str) -> bool:
        with self.lock:
            item = self.items.get(approval_id)
            if not item or item["expires"] < time.time() or item["used"]:
                return False
            item["approved"] = True
            return True

    def consume(self, approval_id: str | None, command: str) -> bool:
        if not approval_id:
            return False
        with self.lock:
            item = self.items.get(approval_id)
            if (
                not item or item["expires"] < time.time() or item["used"]
                or not item["approved"] or item["command"] != command
            ):
                return False
            item["used"] = True
            return True
