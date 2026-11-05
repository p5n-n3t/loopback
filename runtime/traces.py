"""Persistent, privacy-limited lifecycle traces for MCP calls.

Traces correlate an optional client preflight record with gateway observations.
An absent gateway observation remains explicitly unknown; it never identifies an
upstream safety rule by itself. No request arguments or tool output are stored.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SAFETY_BLOCK_TEXT = "This tool call was blocked by OpenAI's safety checks. Please double check what you are sending."
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,80}$")
_STAGES = {
    "intent_recorded", "gateway_observed", "unobserved_at_gateway", "received", "policy_allowed", "policy_denied",
    "insufficient_scope", "approval_required", "handler_started",
    "handler_completed", "protocol_error", "http_error",
    "ssh_dispatch_attempted", "completed", "nonzero_exit", "ssh_exit_255", "timed_out",
    "execution_error", "handler_not_observed", "unknown_remote_state", "cancelled", "client_reported",
}
_DETAIL_KEYS = {"returncode", "duration_ms", "http_status", "required_scope", "error_type", "approval_id", "timeout_seconds"}
_CLIENT_OUTCOMES = {"safety_block", "transport_error", "tool_error", "client_error", "completed", "cancelled"}


class TraceStore:
    """Owner-scoped records backed by the existing private Loopback SQLite file."""

    def __init__(self, config_dir: str | Path):
        self.path = Path(config_dir) / "loopback.db"
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS operation_traces (
                trace_id TEXT PRIMARY KEY, owner TEXT NOT NULL, tool TEXT NOT NULL, target TEXT,
                client TEXT NOT NULL, created_at REAL NOT NULL, deadline_at REAL NOT NULL,
                gateway_seen_at REAL, receipt_id TEXT UNIQUE, correlation_status TEXT NOT NULL,
                state TEXT NOT NULL, returncode INTEGER, duration_ms INTEGER,
                client_reports TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'client')""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_traces_owner_time ON operation_traces(owner,created_at DESC)")
            db.execute("""CREATE TABLE IF NOT EXISTS operation_trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, ts REAL NOT NULL,
                stage TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trace_id) REFERENCES operation_traces(trace_id) ON DELETE CASCADE)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON operation_trace_events(trace_id,id)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _valid_tool(tool: str) -> str:
        if not isinstance(tool, str) or not _TOOL_RE.fullmatch(tool):
            return "unknown"
        return tool

    @staticmethod
    def _valid_target(target: str | None) -> str | None:
        if target is None or target == "":
            return None
        return target if isinstance(target, str) and _ID_RE.fullmatch(target) else "unknown"

    @staticmethod
    def _safe_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not isinstance(detail, dict):
            return out
        for key in _DETAIL_KEYS:
            value = detail.get(key)
            if key in {"returncode", "duration_ms", "http_status"} and isinstance(value, int):
                out[key] = value
            elif key == "timeout_seconds" and isinstance(value, (int, float)):
                out[key] = max(1, min(int(value), 86460))
            elif key in {"required_scope", "error_type"} and isinstance(value, str) and len(value) <= 100:
                out[key] = value
            # Approval IDs are correlation identifiers, but still keep a tight alphabet.
            elif key == "approval_id" and isinstance(value, str) and _ID_RE.fullmatch(value):
                out[key] = value
        return out

    def _event(self, db: sqlite3.Connection, trace_id: str, stage: str, detail: dict[str, Any] | None = None,
               ts: float | None = None) -> None:
        safe_stage = stage if stage in _STAGES else "unknown_stage"
        db.execute("INSERT INTO operation_trace_events(trace_id,ts,stage,detail) VALUES(?,?,?,?)",
                   (trace_id, ts or time.time(), safe_stage, json.dumps(self._safe_detail(detail), separators=(",", ":"))))

    def create_intent(self, owner: str, tool: str, target: str | None = None, client: str = "unknown",
                      ttl_seconds: int = 120) -> dict[str, Any]:
        """Record client preflight before a call; client label must be a short safe token."""
        if not isinstance(owner, str) or not owner or len(owner) > 240:
            raise ValueError("owner is required")
        client = client if isinstance(client, str) and _ID_RE.fullmatch(client) else "unknown"
        now = time.time()
        ttl = max(10, min(int(ttl_seconds), 86460))
        trace_id = uuid.uuid4().hex
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT COUNT(*) AS n FROM operation_traces WHERE owner=? AND state IN "
                                 "('intent_recorded','gateway_observed','received','policy_allowed','handler_started','ssh_dispatch_attempted')",
                                 (owner,)).fetchone()["n"]
            if pending >= 1000:
                raise ValueError("too many unresolved operation traces for this client")
            db.execute("INSERT INTO operation_traces(trace_id,owner,tool,target,client,created_at,deadline_at,correlation_status,state,source) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (trace_id, owner, self._valid_tool(tool), self._valid_target(target), client, now, now + ttl,
                        "pending", "intent_recorded", "client"))
            self._event(db, trace_id, "intent_recorded", {"duration_ms": ttl * 1000}, now)
        return self.get(trace_id, owner)

    def attach_gateway(self, owner: str, tool: str, target: str | None, receipt_id: str,
                       trace_id: str | None = None) -> str | None:
        """Bind an authenticated gateway receipt; malformed claims never reject execution.

        With no valid client correlation this creates an independent gateway trace.
        """
        if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", receipt_id):
            return None
        now = time.time()
        safe_tool, safe_target = self._valid_tool(tool), self._valid_target(target)
        correlation = "gateway_only"
        attached_id = trace_id if isinstance(trace_id, str) and re.fullmatch(r"[0-9a-f]{32}", trace_id) else None
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT trace_id,owner FROM operation_traces WHERE receipt_id=?", (receipt_id,)).fetchone()
            if existing:
                return str(existing["trace_id"]) if existing["owner"] == owner else None
            row = None
            if attached_id:
                row = db.execute("SELECT * FROM operation_traces WHERE trace_id=? AND owner=?", (attached_id, owner)).fetchone()
                if row and row["tool"] == safe_tool and row["target"] == safe_target and row["receipt_id"] is None:
                    correlation = "matched"
                else:
                    row = None
            if row is None:
                attached_id = uuid.uuid4().hex
                db.execute("INSERT INTO operation_traces(trace_id,owner,tool,target,client,created_at,deadline_at,gateway_seen_at,receipt_id,correlation_status,state,source) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (attached_id, owner or "unknown", safe_tool, safe_target, "gateway", now, now, now,
                            receipt_id, "unmatched_client_id" if trace_id else "gateway_only", "gateway_observed", "gateway"))
            else:
                db.execute("UPDATE operation_traces SET gateway_seen_at=?,receipt_id=?,correlation_status=?,state=? WHERE trace_id=?",
                           (now, receipt_id, correlation, "gateway_observed", attached_id))
            self._event(db, attached_id, "gateway_observed", {}, now)
        return attached_id

    def stage(self, receipt_id: str, stage: str, detail: dict[str, Any] | None = None) -> None:
        """Persist an allow-listed gateway stage and terminal result, if applicable."""
        if not isinstance(receipt_id, str):
            return
        now = time.time()
        safe = self._safe_detail(detail)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT trace_id FROM operation_traces WHERE receipt_id=?", (receipt_id,)).fetchone()
            if not row:
                return
            trace_id = str(row["trace_id"])
            self._event(db, trace_id, stage, safe, now)
            state = stage if stage in _STAGES else "gateway_observed"
            ret = safe.get("returncode")
            duration = safe.get("duration_ms")
            db.execute("UPDATE operation_traces SET state=?,returncode=COALESCE(?,returncode),duration_ms=COALESCE(?,duration_ms) WHERE trace_id=?",
                       (state, ret if stage in {"completed", "nonzero_exit", "ssh_exit_255"} else None,
                        duration, trace_id))
            if stage == "received" and "timeout_seconds" in safe:
                db.execute("UPDATE operation_traces SET deadline_at=MAX(deadline_at,?) WHERE trace_id=?",
                           (now + safe["timeout_seconds"] + 60, trace_id))

    def client_report(self, owner: str, trace_id: str, outcome: str, error: str | None = None,
                      receipt_id: str | None = None) -> dict[str, Any] | None:
        """Append caller-reported evidence; it cannot replace gateway facts."""
        if outcome not in _CLIENT_OUTCOMES:
            raise ValueError("unsupported client outcome")
        safe_error_code = "none"
        safe_error = None
        if outcome == "safety_block":
            # Store only a fixed canonical message, never arbitrary client text.
            normalized = " ".join(error.split()) if isinstance(error, str) else ""
            if normalized.startswith("Script error:"):
                normalized = normalized.removeprefix("Script error:").strip()
            if normalized != SAFETY_BLOCK_TEXT:
                safe_error_code = "safety_block_text_unverified"
            else:
                safe_error_code, safe_error = "openai_safety_block_reported_by_client", SAFETY_BLOCK_TEXT
        elif error:
            safe_error_code = "client_error_reported"
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT client_reports FROM operation_traces WHERE trace_id=? AND owner=?", (trace_id, owner)).fetchone()
            if not row:
                return None
            reports = json.loads(row["client_reports"] or "[]")
            reports.append({"outcome": outcome, "error_code": safe_error_code, "error_message": safe_error,
                            "reported_at": now, "receipt_match": bool(receipt_id and db.execute(
                                "SELECT 1 FROM operation_traces WHERE trace_id=? AND receipt_id=?", (trace_id, receipt_id)).fetchone())})
            reports = reports[-20:]
            # A direct client report is positive evidence about what the caller
            # displayed. If the gateway has not seen this trace, expose that fact
            # immediately; it does not imply why delivery stopped.
            unobserved = outcome in {"safety_block", "transport_error", "tool_error", "client_error", "cancelled"}
            db.execute("UPDATE operation_traces SET client_reports=?,state=CASE WHEN gateway_seen_at IS NULL AND ? "
                       "THEN 'unobserved_at_gateway' ELSE state END,correlation_status=CASE WHEN gateway_seen_at IS NULL AND ? "
                       "THEN 'unobserved_at_gateway' ELSE correlation_status END WHERE trace_id=?",
                       (json.dumps(reports, separators=(",", ":")), int(unobserved), int(unobserved), trace_id))
            self._event(db, trace_id, "client_reported", {}, now)
        return self.get(trace_id, owner)

    def reconcile(self, now: float | None = None) -> int:
        """Mark expired no-arrival intents as unobserved; never as OpenAI blocked."""
        now = time.time() if now is None else now
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT trace_id FROM operation_traces WHERE source='client' AND gateway_seen_at IS NULL "
                              "AND state='intent_recorded' AND deadline_at<=?", (now,)).fetchall()
            for row in rows:
                trace_id = str(row["trace_id"])
                db.execute("UPDATE operation_traces SET state='unobserved_at_gateway',correlation_status='unobserved_at_gateway' WHERE trace_id=?",
                           (trace_id,))
                self._event(db, trace_id, "unobserved_at_gateway", {}, now)
            gateway_rows = db.execute("SELECT trace_id FROM operation_traces WHERE gateway_seen_at IS NOT NULL AND deadline_at<=? "
                                      "AND state IN ('gateway_observed','received','policy_allowed','handler_started','ssh_dispatch_attempted')",
                                      (now,)).fetchall()
            for row in gateway_rows:
                trace_id = str(row["trace_id"])
                dispatched = db.execute("SELECT 1 FROM operation_trace_events WHERE trace_id=? AND stage='ssh_dispatch_attempted' LIMIT 1",
                                        (trace_id,)).fetchone()
                state = "unknown_remote_state" if dispatched else "handler_not_observed"
                stage = "unknown_remote_state" if dispatched else "handler_not_observed"
                db.execute("UPDATE operation_traces SET state=? WHERE trace_id=?", (state, trace_id))
                self._event(db, trace_id, stage, {}, now)
        return len(rows) + len(gateway_rows)

    def _row(self, row: sqlite3.Row, db: sqlite3.Connection, owner: str | None) -> dict[str, Any]:
        trace = dict(row)
        reports = json.loads(trace.pop("client_reports") or "[]")
        events = db.execute("SELECT ts,stage,detail FROM operation_trace_events WHERE trace_id=? ORDER BY id", (trace["trace_id"],)).fetchall()
        trace["events"] = [{"ts": e["ts"], "stage": e["stage"], "detail": json.loads(e["detail"] or "{}")} for e in events]
        trace["client_reports"] = reports
        is_ssh = trace["tool"] == "ssh_execute"
        dispatched = any(e["stage"] == "ssh_dispatch_attempted" for e in trace["events"])
        trace["remote_execution"] = ("not_observed" if not trace["gateway_seen_at"] or trace["state"] in {
            "policy_denied", "insufficient_scope", "approval_required"} else
            "completed" if is_ssh and trace["state"] in {"completed", "nonzero_exit"} else
            "unknown" if trace["state"] in {"timed_out", "cancelled", "ssh_exit_255", "handler_not_observed", "unknown_remote_state"} else
            "dispatch_attempted" if dispatched else "unknown")
        trace["state_changed"] = "unknown"
        trace["requested_action"] = trace["tool"]
        trace["classification"] = self._classification(trace)
        trace["recommended_diagnostic_action"] = self._recommended_action(trace)
        trace["last_successful_receipt_id"] = self._last_success(db, trace["owner"], trace["tool"], trace["target"], trace["created_at"])
        return trace

    @staticmethod
    def _classification(trace: dict[str, Any]) -> str:
        state = trace["state"]
        if state == "policy_denied":
            return "loopback_policy_rejection"
        if state == "insufficient_scope":
            return "authorization_scope_rejection"
        if state == "approval_required":
            return "approval_required"
        if state == "completed":
            return "successful_execution"
        if state == "handler_completed":
            return "tool_handler_returned"
        if state == "nonzero_exit":
            return "remote_command_failure" if trace["tool"] == "ssh_execute" else "local_command_failure"
        if state == "ssh_exit_255":
            return "transport_or_remote_exit_255_ambiguous"
        if state in {"timed_out", "cancelled", "unknown_remote_state"}:
            return "unknown_remote_state" if trace["tool"] == "ssh_execute" else "unknown_execution_state"
        if state in {"execution_error", "protocol_error", "http_error"}:
            return "gateway_execution_error"
        if state == "unobserved_at_gateway":
            reports = trace.get("client_reports", [])
            if any(r["error_code"] == "openai_safety_block_reported_by_client" for r in reports):
                return "client_reported_openai_safety_block"
            return "unobserved_at_gateway_unknown_cause"
        if state == "handler_not_observed":
            return "gateway_handler_unknown"
        return "pending_or_unknown"

    @staticmethod
    def _last_success(db: sqlite3.Connection, owner: str, tool: str, target: str | None, before: float) -> str | None:
        row = db.execute("SELECT receipt_id FROM operation_traces WHERE owner=? AND tool=? AND target IS ? "
                         "AND state IN ('completed','handler_completed') AND receipt_id IS NOT NULL "
                         "AND created_at<? ORDER BY created_at DESC LIMIT 1",
                         (owner, tool, target, before)).fetchone()
        return str(row["receipt_id"]) if row else None

    @staticmethod
    def _recommended_action(trace: dict[str, Any]) -> str:
        return {
            "loopback_policy_rejection": "Review the matching Loopback policy audit event; do not retry with altered permissions.",
            "authorization_scope_rejection": "Review the granted OAuth scopes and current connector authorization.",
            "approval_required": "Review the pending Loopback approval in the dashboard.",
            "successful_execution": "No action required; verify application state separately when needed.",
            "remote_command_failure": "Inspect the target application's stderr and exit code in the caller's result.",
            "local_command_failure": "Inspect the local command's exit status and stderr in the caller's result.",
            "transport_or_remote_exit_255_ambiguous": "Inspect SSH connectivity and remote authentication; remote command completion is unknown.",
            "unknown_remote_state": "Inspect the target before deciding whether the operation should be repeated.",
            "unknown_execution_state": "Check whether the local operation completed before deciding whether it should be repeated.",
            "gateway_execution_error": "Inspect the corresponding gateway log entry using its trace and receipt IDs.",
            "gateway_handler_unknown": "Check server and reverse-proxy logs around the gateway arrival time.",
            "client_reported_openai_safety_block": "Preserve the client error and timestamp; Loopback has no upstream rule detail.",
            "unobserved_at_gateway_unknown_cause": "Compare the client report with ingress and gateway logs; cause is not established.",
        }.get(trace["classification"], "Review the trace timeline and any separately reported client error.")

    def get(self, trace_id: str, owner: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            if owner is None:
                row = db.execute("SELECT * FROM operation_traces WHERE trace_id=?", (trace_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM operation_traces WHERE trace_id=? AND owner=?", (trace_id, owner)).fetchone()
            return self._row(row, db, owner) if row else None

    def recent(self, limit: int = 100, owner: str | None = None, status: str | None = None,
               search: str | None = None, since: float | None = None, until: float | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if owner is not None:
            clauses.append("owner=?")
            args.append(owner)
        if status:
            groups = {
                "success": ("completed", "handler_completed"),
                "failed": ("nonzero_exit", "ssh_exit_255", "timed_out", "cancelled", "execution_error", "protocol_error", "http_error", "handler_not_observed", "unknown_remote_state", "policy_denied", "insufficient_scope", "approval_required"),
                "blocked": ("policy_denied", "insufficient_scope", "approval_required"),
                "unresolved": ("intent_recorded", "unobserved_at_gateway", "gateway_observed", "received", "policy_allowed", "handler_started", "ssh_dispatch_attempted", "unknown_remote_state"),
            }
            if status == "all":
                pass
            elif status in groups:
                states = groups[status]
                state_clause = "state IN (" + ",".join("?" for _ in states) + ")"
                if status == "blocked":
                    state_clause = "(" + state_clause + " OR instr(client_reports,'openai_safety_block_reported_by_client')>0)"
                clauses.append(state_clause)
                args.extend(states)
            else:
                clauses.append("state=?")
                args.append(status[:80])
        if since is not None:
            clauses.append("created_at>=?")
            args.append(float(since))
        if until is not None:
            clauses.append("created_at<=?")
            args.append(float(until))
        if search:
            q = search.lower()[:128]
            clauses.append("(instr(lower(tool),?)>0 OR instr(lower(COALESCE(target,'')),?)>0 OR "
                           "instr(lower(COALESCE(receipt_id,'')),?)>0 OR instr(lower(trace_id),?)>0 OR instr(lower(client),?)>0)")
            args.extend([q] * 5)
        # Every predicate is constant SQL or uses bound parameters; state-list
        # placeholder counts come only from the static groups above.
        query = "SELECT * FROM operation_traces" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at DESC LIMIT ?"  # noqa: S608
        args.append(max(1, min(int(limit), 500)))
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
            out = [self._row(row, db, owner) for row in rows]
        return out

    def report(self, trace_id: str, owner: str | None = None) -> dict[str, Any] | None:
        trace = self.get(trace_id, owner)
        if trace is None:
            return None
        report = {"schema": "loopback.operation-trace.v1", "trace": trace,
                  "interpretation": "Gateway evidence and client-reported evidence are separate. Missing gateway observation does not identify its cause."}
        report["report_markdown"] = self.markdown(report)
        return report

    @staticmethod
    def markdown(report: dict[str, Any]) -> str:
        t = report["trace"]
        def fmt(value: Any) -> str:
            return "unavailable" if value is None else str(value)
        lines = ["# Loopback operation trace", "", f"- Trace: `{t['trace_id']}`", f"- UTC created: `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t['created_at']))}`",
                 f"- Tool / target: `{t['tool']}` / `{fmt(t['target'])}`", f"- Classification: `{t['classification']}`",
                 f"- Gateway receipt: `{fmt(t['receipt_id'])}`", f"- Remote execution: `{t['remote_execution']}`",
                 f"- Exit code: `{fmt(t['returncode'])}`", f"- Duration: `{fmt(t['duration_ms'])} ms`" if t["duration_ms"] is not None else "- Duration: `unavailable`",
                 f"- State change: `{t['state_changed']}`",
                 f"- Last successful related receipt: `{fmt(t['last_successful_receipt_id'])}`", "",
                 f"- Recommended diagnostic action: {t['recommended_diagnostic_action']}", "",
                 "## Gateway events", ""]
        lines.extend(f"- `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['ts']))}` `{e['stage']}`" for e in t["events"])
        lines += ["", "## Client reports", ""]
        lines.extend(f"- `{r['outcome']}` at `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r['reported_at']))}`; evidence `{r['error_code']}`; message `{r['error_message'] or 'unavailable'}`"
                     for r in t["client_reports"])
        lines += ["", report["interpretation"]]
        return "\n".join(lines)

    def backfill_ssh_receipts(self, audit_store) -> int:
        """Idempotently import legacy SSH receipt rows without command arguments/output."""
        # Read all receipt records; AuditStore.recent is intentionally capped.
        # The source audit store itself prunes to a configured maximum row count.
        with self._connect() as source:
            raw_rows = source.execute("SELECT id,ts,tool,status,duration_ms,target,detail,principal FROM audit_events "
                                      "WHERE tool='ssh_execute' AND detail IS NOT NULL "
                                      "AND instr(detail,'receipt_id')>0 ORDER BY id ASC").fetchall()
        rows = []
        for raw in raw_rows:
            try:
                detail = json.loads(raw["detail"] or "{}")
            except json.JSONDecodeError:
                continue
            if detail.get("receipt_id"):
                rows.append({**dict(raw), "detail": detail})
        added = 0
        for event in rows:
            receipt = event["detail"].get("receipt_id")
            owner = event.get("principal") or "legacy:unknown"
            tool = "ssh_execute"
            target = event.get("target")
            with self._lock, self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                trace = db.execute("SELECT trace_id FROM operation_traces WHERE receipt_id=?", (receipt,)).fetchone()
                if not trace:
                    trace_id = uuid.uuid4().hex
                    ts = float(event["ts"])
                    db.execute("INSERT INTO operation_traces(trace_id,owner,tool,target,client,created_at,deadline_at,gateway_seen_at,receipt_id,correlation_status,state,returncode,duration_ms,source) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (trace_id, owner, tool, self._valid_target(target), "legacy_import", ts, ts, ts, receipt,
                                "legacy_import", event["status"], event["detail"].get("returncode"), event.get("duration_ms"), "legacy"))
                    added += 1
                else:
                    trace_id = str(trace["trace_id"])
                    is_live = db.execute("SELECT source FROM operation_traces WHERE trace_id=?", (trace_id,)).fetchone()["source"] != "legacy"
                    if is_live:
                        # The new recorder already owns this receipt; avoid
                        # duplicating its events on every startup.
                        continue
                # Event IDs are not reused; dupe import is avoided by event stage+timestamp uniqueness check.
                exists = db.execute("SELECT 1 FROM operation_trace_events WHERE trace_id=? AND stage=? AND ts=?",
                                    (trace_id, event["status"], float(event["ts"]))).fetchone()
                if not exists:
                    self._event(db, trace_id, event["status"], {"returncode": event["detail"].get("returncode"),
                                                                  "duration_ms": event.get("duration_ms")}, float(event["ts"]))
                db.execute("UPDATE operation_traces SET state=?,returncode=COALESCE(?,returncode),duration_ms=COALESCE(?,duration_ms) WHERE trace_id=?",
                           (event["status"], event["detail"].get("returncode"), event.get("duration_ms"), trace_id))
        return added

    def prune(self, retention_days: int = 180, max_rows: int = 100_000) -> int:
        """Bound persisted trace history while retaining recent unresolved incidents."""
        cutoff = time.time() - max(1, min(int(retention_days), 3650)) * 86400
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            removed = db.execute("DELETE FROM operation_traces WHERE created_at<? AND state NOT IN "
                                 "('intent_recorded','gateway_observed','received','policy_allowed','handler_started','ssh_dispatch_attempted')", (cutoff,)).rowcount
            removed += db.execute("DELETE FROM operation_traces WHERE trace_id IN (SELECT trace_id FROM operation_traces "
                                  "WHERE state NOT IN ('intent_recorded','gateway_observed','received','policy_allowed','handler_started','ssh_dispatch_attempted') "
                                  "ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                                  (max(1000, int(max_rows)),)).rowcount
        return removed

