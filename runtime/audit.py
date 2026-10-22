"""Local SQLite audit history.

Stores *who* (principal display name and kind) did *what* (tool/method) with
*which outcome* and *how long it took*. It never stores credentials, request
bodies, file contents or command output.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _create_private_file(path: Path) -> None:
    """Ensure a SQLite file (and therefore its -wal/-shm siblings) is owner-only."""
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class AuditStore:
    def __init__(self, config_dir: Path):
        self.path = Path(config_dir) / "loopback.db"
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _create_private_file(self.path)
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
            cols = {row[1] for row in db.execute("PRAGMA table_info(audit_events)").fetchall()}
            if "principal" not in cols:
                db.execute("ALTER TABLE audit_events ADD COLUMN principal TEXT")
            if "principal_kind" not in cols:
                db.execute("ALTER TABLE audit_events ADD COLUMN principal_kind TEXT")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
        except sqlite3.DatabaseError:
            pass
        return db

    def record(
        self,
        tool: str,
        status: str,
        *,
        duration_ms: int = 0,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
        principal: str | None = None,
        principal_kind: str | None = None,
    ) -> None:
        detail = dict(detail or {})
        principal = principal or detail.pop("principal", None)
        principal_kind = principal_kind or detail.pop("principal_kind", None)
        payload = json.dumps(detail, separators=(",", ":"), ensure_ascii=False)[:4000]
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO audit_events(ts,tool,status,duration_ms,target,detail,principal,principal_kind) VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), tool, status, int(duration_ms), target, payload, principal, principal_kind),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        n = max(1, min(int(limit), 1000))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,ts,tool,status,duration_ms,target,detail,principal,principal_kind FROM audit_events ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"] or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            if item.get("principal"):
                item["detail"]["principal"] = item["principal"]
                item["detail"]["principal_kind"] = item.get("principal_kind")
            out.append(item)
        return out

    def stats(self, since_seconds: int = 86400) -> dict[str, Any]:
        cutoff = time.time() - max(60, int(since_seconds))
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) c FROM audit_events WHERE ts>=?", (cutoff,)).fetchone()["c"]
            failures = db.execute("SELECT COUNT(*) c FROM audit_events WHERE ts>=? AND status!='ok'", (cutoff,)).fetchone()["c"]
            top = db.execute(
                "SELECT tool,COUNT(*) c FROM audit_events WHERE ts>=? GROUP BY tool ORDER BY c DESC LIMIT 10", (cutoff,)
            ).fetchall()
            principals = db.execute(
                "SELECT principal, principal_kind, MAX(ts) last_seen, COUNT(*) calls FROM audit_events"
                " WHERE ts>=? AND principal IS NOT NULL GROUP BY principal, principal_kind ORDER BY last_seen DESC LIMIT 20",
                (cutoff,),
            ).fetchall()
        return {
            "window_seconds": int(since_seconds),
            "total": total,
            "failures": failures,
            "top_tools": [{"tool": r["tool"], "count": r["c"]} for r in top],
            "principals": [dict(r) for r in principals],
        }

    def prune(self, retention_days: int = 90, max_rows: int = 200_000) -> int:
        cutoff = time.time() - max(1, int(retention_days)) * 86400
        with self._lock, self._connect() as db:
            removed = db.execute("DELETE FROM audit_events WHERE ts<?", (cutoff,)).rowcount
            removed += db.execute(
                "DELETE FROM audit_events WHERE id NOT IN (SELECT id FROM audit_events ORDER BY id DESC LIMIT ?)", (max_rows,)
            ).rowcount
        return removed

    def integrity(self) -> str:
        with self._connect() as db:
            return str(db.execute("PRAGMA integrity_check").fetchone()[0])

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
