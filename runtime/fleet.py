from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class FleetStore:
    """Small durable registry for Loopback nodes.

    v2 starts with direct nodes: each remote node keeps its own Cloudflare,
    Tailscale, or reverse-proxy endpoint and bearer token. A gateway can then
    proxy MCP calls to those nodes. A future hosted relay can reuse the same
    node identity model without making cloud infrastructure mandatory.
    """

    def __init__(self, config_dir: Path):
        self.path = config_dir / "nodes.json"
        self.secret_path = config_dir / "nodes.secrets.json"

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def list(self) -> list[dict[str, Any]]:
        rows = self._read(self.path, [])
        return rows if isinstance(rows, list) else []

    def get(self, name: str) -> tuple[dict[str, Any], str | None]:
        node = next((n for n in self.list() if n.get("name") == name), None)
        if not node:
            raise KeyError("node not found")
        secrets = self._read(self.secret_path, {})
        token = secrets.get(name) if isinstance(secrets, dict) else None
        return node, token

    def upsert(
        self,
        name: str,
        url: str,
        token: str | None = None,
        *,
        enabled: bool = True,
        description: str = "",
    ) -> dict[str, Any]:
        if name == "local":
            raise ValueError("'local' is reserved")
        rows = [n for n in self.list() if n.get("name") != name]
        node = {
            "name": name,
            "url": url.rstrip("/"),
            "enabled": bool(enabled),
            "description": description,
        }
        rows.append(node)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        if token is not None:
            secrets = self._read(self.secret_path, {})
            if not isinstance(secrets, dict):
                secrets = {}
            secrets[name] = token
            stmp = self.secret_path.with_suffix(".tmp")
            stmp.write_text(json.dumps(secrets, indent=2) + "\n", encoding="utf-8")
            os.chmod(stmp, 0o600)
            stmp.replace(self.secret_path)
        return node

    def remove(self, name: str) -> bool:
        before = self.list()
        after = [n for n in before if n.get("name") != name]
        self.path.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
        try:
            secrets = self._read(self.secret_path, {})
            if isinstance(secrets, dict) and name in secrets:
                secrets.pop(name, None)
                self.secret_path.write_text(json.dumps(secrets, indent=2) + "\n", encoding="utf-8")
                os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return len(after) != len(before)
