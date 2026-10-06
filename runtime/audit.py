from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from policy import CFG


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CFG / "audit.jsonl")
        self.lock = threading.Lock()

    def write(
        self,
        event: str,
        *,
        tool: str | None = None,
        status: str = "ok",
        detail: dict[str, Any] | None = None,
        client: str | None = None,
    ) -> None:
        row = {
            "ts": time.time(),
            "event": event,
            "tool": tool,
            "status": status,
            "client": client,
            "detail": detail or {},
        }
        raw = json.dumps(row, sort_keys=True, default=str)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(raw + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in deque(handle, maxlen=limit):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
