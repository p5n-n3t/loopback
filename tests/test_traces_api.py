"""Trace lifecycle through the authenticated HTTP/MCP stack; no remote commands."""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from helpers import MCP_HEADERS, fresh_config_dir, load_server
from starlette.testclient import TestClient


class TraceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.s = load_server()
        cls.cm = TestClient(cls.s.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.cm.__enter__()
        cls.c.post("/admin/login", data={"credential": cls.s.AUTH.machine_token})
        cls.csrf = {"X-Loopback-CSRF": cls.c.get("/api/admin/session").json()["csrf"]}
        cls.headers = []
        for name in ("client-a", "client-b"):
            token, _ = cls.s.AUTH.create_api_token(name, ["loopback:read", "loopback:fleet", "loopback:exec"], ttl_seconds=3600, created_by="tests")
            cls.headers.append({"Authorization": "Bearer " + token})
        cls.c.post("/api/admin/ssh", headers=cls.csrf, json={"name": "traces-test", "host": "127.0.0.1", "user": "test"})

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)

    def intent(self, tool="ssh_execute", target="traces-test", headers=None):
        r = self.c.post("/api/traces/intents", headers=headers or self.headers[0],
                        json={"tool": tool, "target": target, "client": "python-harness"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["trace_id"]

    def call(self, trace_id, command="echo private-marker", argv=None, headers=None):
        with patch.object(self.s.CTX.ssh, "_ssh_args", return_value=argv or ["/bin/sh", "-c", "exit 0"]):
            return self.c.post("/mcp", headers={**MCP_HEADERS, **(headers or self.headers[0])}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "ssh_execute", "arguments": {"target": "traces-test", "command": command},
                    "_meta": {"loopback/trace_id": trace_id}}})

    def report(self, trace_id):
        r = self.c.get(f"/api/traces/{trace_id}", headers=self.headers[0])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_success_denial_launch_error_remote_failure_and_exports(self):
        for command, argv, state in (
            ("echo secret-should-not-persist", ["/bin/sh", "-c", "exit 0"], "completed"),
            ("rm -rf /", ["/bin/sh", "-c", "exit 0"], "policy_denied"),
            ("echo private-marker", ["/does-not-exist/ssh"], "execution_error"),
            ("echo private-marker", ["/bin/sh", "-c", "exit 7"], "nonzero_exit"),
            ("echo private-marker", ["/bin/sh", "-c", "exit 255"], "ssh_exit_255"),
        ):
            with self.subTest(state=state):
                trace_id = self.intent()
                response = self.call(trace_id, command, argv)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["x-loopback-trace"], trace_id)
                record = self.report(trace_id)
                trace = record["trace"]
                self.assertEqual(trace["state"], state)
                self.assertEqual(trace["receipt_id"], response.headers["x-loopback-receipt"])
                self.assertEqual(trace["correlation_status"], "matched")
                self.assertNotIn(command, json.dumps(record))
                if state == "policy_denied":
                    self.assertNotIn("ssh_dispatch_attempted", [e["stage"] for e in trace["events"]])
                for fmt in ("json", "markdown"):
                    export = self.c.get(f"/api/admin/traces/{trace_id}/export?format={fmt}")
                    self.assertEqual(export.status_code, 200)
                    self.assertIn("attachment", export.headers["content-disposition"])
                    self.assertIn(trace_id, export.text)
                    self.assertNotIn(command, export.text)

    def test_owner_cannot_claim_or_read_another_trace(self):
        trace_id = self.intent()
        other = self.headers[1]
        self.assertEqual(self.c.get(f"/api/traces/{trace_id}", headers=other).status_code, 404)
        self.assertEqual(self.c.post(f"/api/traces/{trace_id}/report", headers=other,
                                     json={"outcome": "completed"}).status_code, 404)
        response = self.call(trace_id, headers=other)
        self.assertNotEqual(response.headers["x-loopback-trace"], trace_id)
        self.assertIsNone(self.report(trace_id)["trace"]["receipt_id"])

    def test_unobserved_is_unknown_and_safety_requires_explicit_client_evidence(self):
        trace_id = self.intent()
        self.s.TRACES.reconcile(now=time.time() + 1000)
        self.assertEqual(self.report(trace_id)["trace"]["classification"], "unobserved_at_gateway_unknown_cause")
        from traces import SAFETY_BLOCK_TEXT
        r = self.c.post(f"/api/traces/{trace_id}/report", headers=self.headers[0],
                        json={"outcome": "safety_block", "error": SAFETY_BLOCK_TEXT})
        self.assertEqual(r.status_code, 200, r.text)
        report = self.report(trace_id)
        self.assertEqual(report["trace"]["classification"], "client_reported_openai_safety_block")
        self.assertIn(SAFETY_BLOCK_TEXT, json.dumps(report))
        self.assertIsNone(report["trace"]["receipt_id"])
        # Client claims cannot override the subsequent observed gateway result.
        self.call(trace_id)
        self.assertEqual(self.report(trace_id)["trace"]["classification"], "successful_execution")

    def test_generic_tool_automatic_trace_and_no_execution_claim(self):
        r = self.c.post("/mcp", headers={**MCP_HEADERS, **self.headers[0]}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "machine_info", "arguments": {}}})
        trace_id = r.headers["x-loopback-trace"]
        trace = self.report(trace_id)["trace"]
        self.assertEqual(trace["state"], "handler_completed")
        self.assertEqual(trace["correlation_status"], "gateway_only")
        self.assertEqual(trace["remote_execution"], "unknown")

    def test_concurrent_mcp_requests_keep_receipts_and_outcomes_separate(self):
        ids = [self.intent() for _ in range(6)]
        async def execute(_target, command, *, observe, **_kwargs):
            observe("policy_allowed")
            observe("ssh_dispatch_attempted")
            code = int(command)
            await asyncio.sleep((6 - code) / 100)
            return {"returncode": code, "timed_out": False, "duration_ms": 1}
        def invoke(pair):
            n, trace_id = pair
            return self.c.post("/mcp", headers={**MCP_HEADERS, **self.headers[0]}, json={
                "jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {
                    "name": "ssh_execute", "arguments": {"target": "traces-test", "command": str(n)},
                    "_meta": {"loopback/trace_id": trace_id}}})
        with patch.object(self.s.CTX.ssh, "execute", side_effect=execute), ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(invoke, enumerate(ids)))
        receipts = set()
        for n, (trace_id, response) in enumerate(zip(ids, responses)):
            self.assertEqual(response.headers["x-loopback-trace"], trace_id)
            trace = self.report(trace_id)["trace"]
            self.assertEqual(trace["returncode"], n)
            self.assertEqual(trace["receipt_id"], response.headers["x-loopback-receipt"])
            receipts.add(trace["receipt_id"])
        self.assertEqual(len(receipts), len(ids))

    def test_trace_api_auth_input_and_coverage(self):
        self.assertEqual(self.c.get("/api/traces").status_code, 401)
        r = self.c.post("/api/traces/intents", headers=self.headers[0],
                        json={"tool": "ssh_execute", "command": "password=private-marker"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("private-marker", r.text)
        self.assertEqual(self.c.get("/api/traces?since=nan", headers=self.headers[0]).status_code, 400)
        rows = self.c.get("/api/admin/traces").json()
        self.assertEqual(rows["coverage"]["chatgpt_web"], "unavailable")


class GateCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_ssh_gate_has_final_receipt(self):
        fresh_config_dir()
        s = load_server()
        arrived = asyncio.Event()
        async def inner(scope, receive, send):
            s._ssh_stage("policy_allowed")
            s._ssh_stage("ssh_dispatch_attempted")
            arrived.set()
            await asyncio.Event().wait()
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "ssh_execute", "arguments": {"target": "fixture", "command": "private"}}}).encode()
        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}
        async def send(_message):
            pass
        task = asyncio.create_task(s.McpGate(inner)({"type": "http", "method": "POST", "path": "/mcp", "headers": [], "state": {}}, receive, send))
        await asyncio.wait_for(arrived.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        traces = s.TRACES.recent()
        self.assertEqual(traces[0]["state"], "cancelled")
        self.assertEqual(traces[0]["classification"], "unknown_remote_state")
        self.assertEqual(s.AUDIT.recent()[0]["status"], "cancelled")
        self.assertNotIn("private", json.dumps(traces))

    async def test_disconnect_during_request_body_returns(self):
        fresh_config_dir()
        s = load_server()
        async def receive():
            return {"type": "http.disconnect"}
        async def unused(*args):
            self.fail("incomplete request must not reach handler")
        await asyncio.wait_for(s.McpGate(unused)({"type": "http", "method": "POST", "path": "/mcp"}, receive, unused), 1)


if __name__ == "__main__":
    unittest.main()
