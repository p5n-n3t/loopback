from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, config_dir: Path):
        self.path = config_dir / "loopback.db"
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    target TEXT,
                    detail TEXT
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def record(
        self,
        tool: str,
        status: str,
        *,
        duration_ms: int = 0,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False)[:12000]
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO audit_events(ts,tool,status,duration_ms,target,detail) VALUES(?,?,?,?,?,?)",
                (time.time(), tool, status, int(duration_ms), target, payload),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 1000))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,ts,tool,status,duration_ms,target,detail FROM audit_events ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"] or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            out.append(item)
        return out

    def stats(self, since_seconds: int = 86400) -> dict[str, Any]:
        cutoff = time.time() - max(60, int(since_seconds))
        with self._connect() as db:
            total = db.execute(
                "SELECT COUNT(*) c FROM audit_events WHERE ts>=?", (cutoff,)
            ).fetchone()["c"]
            failures = db.execute(
                "SELECT COUNT(*) c FROM audit_events WHERE ts>=? AND status!='ok'", (cutoff,)
            ).fetchone()["c"]
            top = db.execute(
                "SELECT tool,COUNT(*) c FROM audit_events WHERE ts>=? GROUP BY tool ORDER BY c DESC LIMIT 10",
                (cutoff,),
            ).fetchall()
        return {
            "window_seconds": int(since_seconds),
            "total": total,
            "failures": failures,
            "top_tools": [{"tool": r["tool"], "count": r["c"]} for r in top],
        }
