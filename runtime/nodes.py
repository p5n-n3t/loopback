from __future__ import annotations

import json
import re
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from policy import CFG, atomic_json


class NodeRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CFG / "nodes.json")
        if not self.path.exists():
            self.save({"nodes": [self._local()]})

    @staticmethod
    def _local() -> dict[str, Any]:
        return {
            "name": "local",
            "transport": "local",
            "label": socket.gethostname(),
            "enabled": True,
        }

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {"nodes": []}
        nodes = value.setdefault("nodes", [])
        if not any(node.get("name") == "local" for node in nodes):
            nodes.insert(0, self._local())
        return value

    def save(self, value: dict[str, Any]) -> None:
        atomic_json(self.path, value)

    def list(self) -> list[dict[str, Any]]:
        return self.load().get("nodes", [])

    def get(self, name: str) -> dict[str, Any]:
        for node in self.list():
            if node.get("name") == name:
                return node
        raise KeyError(f"unknown node: {name}")

    def upsert(self, node: dict[str, Any]) -> dict[str, Any]:
        name = str(node.get("name", "")).strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError("invalid node name")
        transport = str(node.get("transport", "ssh"))
        if transport not in {"local", "ssh"}:
            raise ValueError("transport must be local or ssh")
        replacement: dict[str, Any] = {
            "name": name,
            "transport": transport,
            "label": str(node.get("label") or name),
            "enabled": bool(node.get("enabled", True)),
        }
        if transport == "ssh":
            target = str(node.get("target") or name)
            if not re.fullmatch(r"[A-Za-z0-9._@:-]+", target):
                raise ValueError("invalid SSH target")
            replacement["target"] = target
        value = self.load()
        found = False
        for index, existing in enumerate(value["nodes"]):
            if existing.get("name") == name:
                value["nodes"][index] = replacement
                found = True
                break
        if not found:
            value["nodes"].append(replacement)
        self.save(value)
        return replacement

    def remove(self, name: str) -> None:
        if name == "local":
            raise ValueError("local node cannot be removed")
        value = self.load()
        value["nodes"] = [node for node in value["nodes"] if node.get("name") != name]
        self.save(value)

    def command(
        self,
        name: str,
        command: str,
        *,
        shell: str,
        timeout: int = 120,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        node = self.get(name)
        if not node.get("enabled", True):
            raise PermissionError(f"node is disabled: {name}")
        if node["transport"] == "local":
            local_cwd = str(Path(cwd or "~").expanduser().resolve())
            proc = subprocess.run(
                command,
                shell=True,
                executable=shell,
                cwd=local_cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        else:
            target = str(node.get("target") or name)
            remote = command
            if cwd:
                remote = f"cd {shlex.quote(cwd)} && {command}"
            proc = subprocess.run(
                [
                    "ssh",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10",
                    target,
                    remote,
                ],
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        return {
            "node": name,
            "transport": node["transport"],
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def health(self, name: str, shell: str) -> dict[str, Any]:
        started = time.time()
        try:
            result = self.command(name, "printf loopback-ok", shell=shell, timeout=12)
            ok = result["returncode"] == 0 and "loopback-ok" in result["stdout"]
            error = None if ok else (result["stderr"] or result["stdout"]).strip()[:500]
        except Exception as exc:
            ok = False
            error = str(exc)
        return {
            "name": name,
            "ok": ok,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "error": error,
        }
