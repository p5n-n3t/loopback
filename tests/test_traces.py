"""Privacy, ownership and lifecycle tests for persistent operation traces."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from traces import SAFETY_BLOCK_TEXT, TraceStore


class TraceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="loopback-traces-")
        self.addCleanup(self.tmp.cleanup)
        self.store = TraceStore(Path(self.tmp.name))

    def insert_legacy_audit(self, rows):
        db = sqlite3.connect(self.store.path)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                       "tool TEXT NOT NULL, status TEXT NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0, target TEXT, detail TEXT, "
                       "principal TEXT, principal_kind TEXT)")
            db.executemany("INSERT INTO audit_events(ts,tool,status,duration_ms,target,detail,principal,principal_kind) "
                           "VALUES(?,?,?,?,?,?,?,?)", rows)
            db.commit()
        finally:
            db.close()

    def test_gateway_only_lifecycle_records_stages_without_arguments(self):
        receipt = "a" * 32
        trace_id = self.store.attach_gateway("oauth:client", "ssh_execute", "ora1", receipt)
        self.assertTrue(trace_id)
        self.store.stage(receipt, "received", {"timeout_seconds": 900, "secret": "do-not-store"})
        self.store.stage(receipt, "ssh_dispatch_attempted", {"command": "sensitive command"})
        self.store.stage(receipt, "completed", {"returncode": 0, "duration_ms": 932, "stdout": "secret output"})

        result = self.store.get(trace_id, "oauth:client")
        self.assertEqual(result["classification"], "successful_execution")
        self.assertEqual(result["remote_execution"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["duration_ms"], 932)
        self.assertGreaterEqual(result["deadline_at"], result["created_at"] + 900)
        serialized = json.dumps(result)
        for secret in ("do-not-store", "sensitive command", "secret output"):
            self.assertNotIn(secret, serialized)

    def test_preflight_reconciles_to_unknown_and_explicit_report_is_separate(self):
        intent = self.store.create_intent("oauth:c", "ssh_execute", "ora1", client="chatgpt-web")
        result = self.store.client_report("oauth:c", intent["trace_id"], "safety_block", SAFETY_BLOCK_TEXT)
        self.assertEqual(result["state"], "unobserved_at_gateway")
        self.assertEqual(result["classification"], "client_reported_openai_safety_block")
        self.assertEqual(result["client_reports"][0]["error_message"], SAFETY_BLOCK_TEXT)
        # A caller cannot smuggle private content into a report field.
        result = self.store.client_report("oauth:c", intent["trace_id"], "safety_block", "blocked token=secret-value")
        self.assertEqual(result["client_reports"][-1]["error_code"], "safety_block_text_unverified")
        self.assertIsNone(result["client_reports"][-1]["error_message"])
        self.assertNotIn("secret-value", json.dumps(result))
        reconciled = self.store.reconcile(now=intent["deadline_at"] + 1)
        self.assertEqual(reconciled, 0)  # explicit client report already resolved this intent
        self.assertEqual(self.store.get(intent["trace_id"], "oauth:c")["classification"], "client_reported_openai_safety_block")

    def test_gateway_correlation_requires_owner_tool_and_target_match(self):
        intent = self.store.create_intent("oauth:a", "ssh_execute", "ora1")
        other_owner_trace = self.store.attach_gateway("oauth:b", "ssh_execute", "ora1", "b" * 32, intent["trace_id"])
        self.assertNotEqual(other_owner_trace, intent["trace_id"])
        self.assertEqual(self.store.get(intent["trace_id"], "oauth:a")["receipt_id"], None)

        mismatched_trace = self.store.create_intent("oauth:a", "ssh_execute", "ora1")
        new_id = self.store.attach_gateway("oauth:a", "ssh_execute", "ora2", "c" * 32, mismatched_trace["trace_id"])
        self.assertNotEqual(new_id, mismatched_trace["trace_id"])
        self.assertEqual(self.store.get(mismatched_trace["trace_id"], "oauth:a")["receipt_id"], None)

    def test_policy_and_exit_results_have_distinct_classification(self):
        denied = self.store.attach_gateway("owner", "ssh_execute", "ora1", "d" * 32)
        self.store.stage("d" * 32, "policy_denied")
        self.assertEqual(self.store.get(denied)["classification"], "loopback_policy_rejection")

        failed = self.store.attach_gateway("owner", "ssh_execute", "ora1", "e" * 32)
        self.store.stage("e" * 32, "nonzero_exit", {"returncode": 9})
        self.assertEqual(self.store.get(failed)["classification"], "remote_command_failure")
        self.assertEqual(self.store.get(failed)["returncode"], 9)

        timed = self.store.attach_gateway("owner", "ssh_execute", "ora1", "f" * 32)
        self.store.stage("f" * 32, "timed_out")
        self.assertEqual(self.store.get(timed)["classification"], "unknown_remote_state")

        local = self.store.attach_gateway("owner", "execute", None, "7" * 32)
        self.store.stage("7" * 32, "nonzero_exit", {"returncode": 2, "duration_ms": 45})
        local_report = self.store.report(local)
        self.assertEqual(local_report["trace"]["classification"], "local_command_failure")
        self.assertIn("Exit code: `2`", local_report["report_markdown"])
        self.assertIn("Duration: `45 ms`", local_report["report_markdown"])

    def test_reports_are_owner_scoped_searchable_and_persistent(self):
        intent = self.store.create_intent("owner:a", "machine_info", client="chatgpt-web")
        receipt = "1" * 32
        trace = self.store.attach_gateway("owner:a", "machine_info", None, receipt, intent["trace_id"])
        self.store.stage(receipt, "handler_started")
        self.store.stage(receipt, "handler_completed")
        self.assertIsNone(self.store.get(trace, "owner:b"))
        result = self.store.get(trace, "owner:a")
        self.assertEqual(result["classification"], "tool_handler_returned")
        self.assertEqual(result["remote_execution"], "unknown")
        self.assertEqual(len(self.store.recent(owner="owner:a", status="success", search="machine")), 1)
        self.assertEqual(len(self.store.recent(owner="owner:a", since=result["created_at"] - 1, until=time.time())), 1)
        self.assertIn("Loopback operation trace", self.store.report(trace, "owner:a")["report_markdown"])
        reopened = TraceStore(Path(self.tmp.name))
        self.assertEqual(reopened.get(trace, "owner:a")["receipt_id"], receipt)

    def test_legacy_receipt_backfill_is_complete_idempotent_and_keeps_terminal_state(self):
        receipt = "2" * 32
        self.insert_legacy_audit([(time.time() + n / 1000, "ssh_execute", stage, 0, "ora1",
                                   json.dumps({"receipt_id": receipt, **({"returncode": 0} if stage == "completed" else {})}),
                                   "old-client", "oauth")
                                  for n, stage in enumerate(("received", "ssh_dispatch_attempted", "completed"))])

        self.assertEqual(self.store.backfill_ssh_receipts(None), 1)
        trace = self.store.recent(owner="old-client")[0]
        self.assertEqual(trace["state"], "completed")
        self.assertEqual(trace["classification"], "successful_execution")
        event_count = len(trace["events"])
        self.assertEqual(self.store.backfill_ssh_receipts(None), 0)
        self.assertEqual(len(self.store.get(trace["trace_id"])["events"]), event_count)

    def test_concurrent_preflight_cannot_be_bound_to_two_gateway_calls(self):
        intent = self.store.create_intent("owner:race", "ssh_execute", "ora1")
        barrier = threading.Barrier(2)

        def bind(receipt):
            # Separate store instances exercise SQLite's cross-thread/process lock,
            # rather than relying on this instance's Python mutex.
            store = TraceStore(Path(self.tmp.name))
            barrier.wait()
            return store.attach_gateway("owner:race", "ssh_execute", "ora1", receipt, intent["trace_id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(bind, ("3" * 32, "4" * 32)))
        self.assertEqual(ids.count(intent["trace_id"]), 1)
        self.assertEqual(len(set(ids)), 2)
        bound = self.store.get(intent["trace_id"], "owner:race")
        self.assertIn(bound["receipt_id"], {"3" * 32, "4" * 32})
        separate = next(trace for trace in self.store.recent(owner="owner:race") if trace["trace_id"] != intent["trace_id"])
        self.assertEqual(separate["correlation_status"], "unmatched_client_id")

    def test_search_happens_before_limit_and_safety_client_reports_are_blocked_filter(self):
        old = self.store.create_intent("owner:search", "ssh_execute", "ora7")
        self.store.client_report("owner:search", old["trace_id"], "safety_block", SAFETY_BLOCK_TEXT)
        for n in range(5):
            self.store.create_intent("owner:search", "machine_info", f"host{n}")
        filtered = self.store.recent(owner="owner:search", status="blocked", search="ora7", limit=1)
        self.assertEqual([trace["trace_id"] for trace in filtered], [old["trace_id"]])
        with self.assertRaises(ValueError):
            self.store.client_report("owner:search", old["trace_id"], "openai_safety_block", "anything")

    def test_backfill_includes_terminal_receipts_past_recent_audit_window(self):
        receipts = [f"{n:032x}" for n in range(340)]
        event_pairs = ((receipt, stage) for receipt in receipts
                       for stage in ("received", "ssh_dispatch_attempted", "completed"))
        legacy_rows = [(time.time() + n / 1000, "ssh_execute", stage, 0, "ora1",
                        json.dumps({"receipt_id": receipt, **({"returncode": 0} if stage == "completed" else {})}),
                        "old-client", "oauth")
                       for n, (receipt, stage) in enumerate(event_pairs)]
        self.insert_legacy_audit(legacy_rows)
        self.assertEqual(self.store.backfill_ssh_receipts(None), len(receipts))
        imported = self.store.recent(owner="old-client", limit=500)
        self.assertEqual(len(imported), len(receipts))
        self.assertTrue(all(item["state"] == "completed" for item in imported))

    def test_prune_removes_old_resolved_rows_but_keeps_unknown_incidents(self):
        resolved = self.store.create_intent("owner:retention", "machine_info")
        pending = self.store.create_intent("owner:retention", "machine_info")
        old = time.time() - 10 * 86400
        with self.store._connect() as db:
            db.execute("UPDATE operation_traces SET state='handler_completed',created_at=? WHERE trace_id=?", (old, resolved["trace_id"]))
            db.execute("UPDATE operation_trace_events SET ts=? WHERE trace_id=?", (old, resolved["trace_id"]))
            db.execute("UPDATE operation_traces SET created_at=?,deadline_at=? WHERE trace_id=?", (old, old + 10, pending["trace_id"]))
            db.execute("UPDATE operation_trace_events SET ts=? WHERE trace_id=?", (old, pending["trace_id"]))
        self.assertEqual(self.store.prune(retention_days=1), 1)
        self.assertIsNone(self.store.get(resolved["trace_id"], "owner:retention"))
        self.assertIsNotNone(self.store.get(pending["trace_id"], "owner:retention"))

    def test_unresolved_owner_quota_is_bounded_and_clears_when_resolved(self):
        now = time.time()
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT INTO operation_traces(trace_id,owner,tool,target,client,created_at,deadline_at,correlation_status,state,source) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)",
                           [(uuid.uuid4().hex, "owner:quota", "machine_info", None, "gateway", now, now + 60,
                             "gateway_only", "intent_recorded", "gateway") for _ in range(1000)])
        with self.assertRaisesRegex(ValueError, "too many unresolved"):
            self.store.create_intent("owner:quota", "machine_info")
        with self.store._connect() as db:
            db.execute("UPDATE operation_traces SET state='handler_completed' WHERE owner='owner:quota'")
        self.assertEqual(self.store.create_intent("owner:quota", "machine_info")["state"], "intent_recorded")

    def test_expired_dispatched_trace_is_unknown_remote_state_until_late_exit(self):
        receipt = "5" * 32
        trace = self.store.attach_gateway("owner:late", "ssh_execute", "ora1", receipt)
        self.store.stage(receipt, "received", {"timeout_seconds": 10})
        self.store.stage(receipt, "ssh_dispatch_attempted")
        current = self.store.get(trace)
        self.assertEqual(self.store.reconcile(now=current["deadline_at"] + 1), 1)
        reconciled = self.store.get(trace)
        self.assertEqual(reconciled["classification"], "unknown_remote_state")
        self.assertEqual(reconciled["remote_execution"], "unknown")
        self.store.stage(receipt, "completed", {"returncode": 0, "duration_ms": 12001})
        late = self.store.get(trace)
        self.assertEqual(late["classification"], "successful_execution")
        self.assertEqual(late["duration_ms"], 12001)


if __name__ == "__main__":
    unittest.main()
