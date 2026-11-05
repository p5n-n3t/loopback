from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx

from runtime.trace_client import SAFETY_BLOCK, TRACE_META_KEY, TracePreflightError, TracedMcpClient


TRACE_ID = "a" * 32
RECEIPT_ID = "b" * 32


class TraceClientTests(unittest.IsolatedAsyncioTestCase):
    def make_http(self, *, fail_intent=False, fail_report=False):
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body, request.headers.get("authorization")))
            if request.url.path == "/api/traces/intents":
                if fail_intent:
                    return httpx.Response(503, json={"detail": "offline"})
                return httpx.Response(201, json={"trace_id": TRACE_ID})
            if fail_report:
                return httpx.Response(503, json={"detail": "offline"})
            return httpx.Response(200, json={"ok": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://loopback.example")
        return client, calls

    async def test_intent_precedes_single_call_and_correlates_result(self):
        http, requests = self.make_http()
        called = []

        async def invoke(name, arguments, *, meta):
            called.append((name, arguments, meta))
            return SimpleNamespace(isError=False, structuredContent={"receipt_id": RECEIPT_ID}, content=[])

        adapter = TracedMcpClient("https://loopback.example", "owner-token", invoke, http_client=http)
        args = {"target": "ora1", "command": "sudo -n echo confidential"}
        result = await adapter.call_tool("ssh_execute", args)

        self.assertEqual(len(called), 1)
        self.assertIs(called[0][1], args)
        self.assertEqual(called[0][2], {TRACE_META_KEY: TRACE_ID})
        self.assertEqual(result.structuredContent["receipt_id"], RECEIPT_ID)
        self.assertEqual([c[0] for c in requests], ["/api/traces/intents", f"/api/traces/{TRACE_ID}/report"])
        self.assertEqual(requests[0][1], {"tool": "ssh_execute", "target": "ora1", "client": "python-harness", "ttl_seconds": 180})
        self.assertEqual(requests[1][1], {"outcome": "completed", "receipt_id": RECEIPT_ID})
        self.assertNotIn("confidential", json.dumps(requests))
        self.assertNotIn("owner-token", json.dumps([c[1] for c in requests]))
        await http.aclose()

    async def test_preflight_failure_aborts_before_execution(self):
        http, _ = self.make_http(fail_intent=True)
        calls = 0

        async def invoke(*_args, **_kwargs):
            nonlocal calls
            calls += 1

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        with self.assertRaises(TracePreflightError):
            await adapter.call_tool("machine_info")
        self.assertEqual(calls, 0)
        await http.aclose()

    async def test_explicit_safety_error_is_canonical_and_never_retried(self):
        http, requests = self.make_http()
        calls = 0

        async def invoke(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("Script error:\n" + SAFETY_BLOCK)

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        with self.assertRaisesRegex(RuntimeError, "OpenAI's safety checks"):
            await adapter.call_tool("ssh_execute", {"target": "ora1", "command": "secret"})
        self.assertEqual(calls, 1)
        self.assertEqual(requests[-1][1], {"outcome": "safety_block", "error": SAFETY_BLOCK})
        self.assertNotIn("secret", json.dumps(requests))
        await http.aclose()

    async def test_arbitrary_exception_containing_safety_quote_is_not_misclassified(self):
        http, requests = self.make_http()

        async def invoke(*_args, **_kwargs):
            raise RuntimeError("remote output included the words: " + SAFETY_BLOCK)

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        with self.assertRaises(RuntimeError):
            await adapter.call_tool("ssh_execute")
        self.assertEqual(requests[-1][1], {"outcome": "client_error"})
        await http.aclose()

    async def test_safety_error_in_mcp_result_is_reported(self):
        http, requests = self.make_http()

        async def invoke(*_args, **_kwargs):
            return SimpleNamespace(isError=True, structuredContent=None,
                                   content=[SimpleNamespace(text=SAFETY_BLOCK)])

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        result = await adapter.call_tool("tool")
        self.assertTrue(result.isError)
        self.assertEqual(requests[-1][1], {"outcome": "safety_block", "error": SAFETY_BLOCK})
        await http.aclose()

    async def test_quoted_safety_text_in_success_output_is_not_misclassified(self):
        http, requests = self.make_http()
        message = "An example error says: " + SAFETY_BLOCK

        async def invoke(*_args, **_kwargs):
            return SimpleNamespace(isError=False, structuredContent=None,
                                   content=[SimpleNamespace(text=message)])

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        await adapter.call_tool("read_file")
        self.assertEqual(requests[-1][1], {"outcome": "completed"})
        await http.aclose()

    async def test_report_failure_does_not_mask_result_or_retry(self):
        http, requests = self.make_http(fail_report=True)
        calls = 0
        expected = object()

        async def invoke(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return expected

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        self.assertIs(await adapter.call_tool("tool"), expected)
        self.assertEqual(calls, 1)
        self.assertEqual(len(requests), 2)
        await http.aclose()

    async def test_cancellation_is_reported_and_rethrown(self):
        http, requests = self.make_http()
        entered = asyncio.Event()

        async def invoke(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

        adapter = TracedMcpClient("https://loopback.example", "token", invoke, http_client=http)
        task = asyncio.create_task(adapter.call_tool("tool"))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(requests[-1][1], {"outcome": "cancelled"})
        await http.aclose()

    async def test_plain_http_gateway_is_rejected(self):
        for url in (
            "http://remote.example",
            "http://localhost.evil.test",
            "http://127.0.0.1.evil.test",
            "https://user:pass@loopback.example",
            "https://loopback.example/path",
            "https://loopback.example/?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                TracedMcpClient(url, "token", lambda *_a, **_k: None)


if __name__ == "__main__":
    unittest.main()
