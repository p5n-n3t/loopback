"""Opt-in tracing adapter for Python MCP clients calling Loopback.

This adapter records intent before invoking the caller's existing MCP
``ClientSession.call_tool`` route, then attaches a trace id to that same call.
It does not choose or create a second execution path.
"""
from __future__ import annotations

import asyncio
import math
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger(__name__)
SAFETY_BLOCK = "This tool call was blocked by OpenAI's safety checks. Please double check what you are sending."
TRACE_META_KEY = "loopback/trace_id"
_ID = re.compile(r"^[0-9a-f]{32}$")


class TracePreflightError(RuntimeError):
    """Raised before the MCP operation if durable intent recording fails."""


class TracedMcpClient:
    """Record and correlate one call through an existing authorized MCP client.

    ``invoke`` must be the application's normal ``ClientSession.call_tool``
    callback. The adapter calls it once, after the intent has been stored. A
    failure while reporting the result is logged without the error text and
    never causes the MCP operation to be repeated.
    """

    def __init__(self, gateway_url: str, access_token: str,
                 invoke: Callable[..., Awaitable[Any]], *,
                 http_client: httpx.AsyncClient | None = None,
                 client_name: str = "python-harness", request_timeout: float = 5.0):
        try:
            parsed = urlsplit(gateway_url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            secure_http = parsed.scheme == "https" and bool(parsed.hostname)
            if (not (local_http or secure_http) or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("gateway_url must be an HTTPS origin (HTTP is allowed only on localhost)") from None
        if not access_token:
            raise ValueError("access_token is required")
        self.gateway_url = f"{parsed.scheme}://{parsed.netloc}"
        self._token = access_token
        self._invoke = invoke
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(request_timeout))
        self._owns_http = http_client is None
        self._client_name = client_name if client_name in {"python-harness", "codex-cli", "claude-code"} else "python-harness"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> TracedMcpClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(
            self.gateway_url + path,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            json=data,
            follow_redirects=False,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("invalid trace response")
        return value

    async def _report(self, trace_id: str, outcome: str, *, error: str | None = None,
                      receipt_id: str | None = None) -> None:
        body: dict[str, Any] = {"outcome": outcome}
        # Only send the one exact, known platform message. Never serialize an
        # exception or tool result into the persistent incident store.
        if error == SAFETY_BLOCK:
            body["error"] = SAFETY_BLOCK
        if isinstance(receipt_id, str) and _ID.fullmatch(receipt_id):
            body["receipt_id"] = receipt_id
        await self._post(f"/api/traces/{trace_id}/report", body)

    async def _best_effort_report(self, trace_id: str, outcome: str, *,
                                  error: str | None = None,
                                  receipt_id: str | None = None) -> None:
        try:
            await self._report(trace_id, outcome, error=error, receipt_id=receipt_id)
        except Exception as exc:  # a diagnostic must never replace the call's result
            LOG.warning("Loopback call trace report was not acknowledged (%s)", type(exc).__name__)

    @staticmethod
    def _safety_error(value: Any) -> str | None:
        if isinstance(value, str) and value.strip() in {SAFETY_BLOCK, f"Script error: {SAFETY_BLOCK}", f"Script error:\n{SAFETY_BLOCK}"}:
            return SAFETY_BLOCK
        return None

    @classmethod
    def _result_safety_error(cls, result: Any) -> str | None:
        is_error = result.get("isError", result.get("is_error", False)) if isinstance(result, dict) else getattr(result, "isError", getattr(result, "is_error", False))
        if not bool(is_error):
            return None
        content = result.get("content", ()) if isinstance(result, dict) else getattr(result, "content", ())
        for item in content or ():
            if isinstance(item, dict):
                error = cls._safety_error(item.get("text"))
            else:
                error = cls._safety_error(getattr(item, "text", None))
            if error:
                return error
        return None

    @staticmethod
    def _result_receipt(result: Any) -> str | None:
        structured = result.get("structuredContent") if isinstance(result, dict) else getattr(result, "structuredContent", None)
        if not isinstance(structured, dict):
            structured = result.get("structured_content") if isinstance(result, dict) else getattr(result, "structured_content", None)
        value = structured.get("receipt_id") if isinstance(structured, dict) else None
        return value if isinstance(value, str) and _ID.fullmatch(value) else None

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Record intent, call the existing MCP path once, then report its result."""
        args = arguments or {}
        target_value = args.get("target", args.get("node"))
        target = target_value if isinstance(target_value, str) else None
        requested_timeout = args.get("timeout", 120)
        try:
            timeout_seconds = float(requested_timeout) if not isinstance(requested_timeout, bool) else 120.0
        except (TypeError, ValueError):
            timeout_seconds = 120.0
        ttl_seconds = min(86460, max(1, math.ceil(timeout_seconds) + 60))
        try:
            intent = await self._post("/api/traces/intents", {
                "tool": name, "target": target, "client": self._client_name, "ttl_seconds": ttl_seconds,
            })
            trace_id = intent.get("trace_id")
            if not isinstance(trace_id, str) or not _ID.fullmatch(trace_id):
                raise ValueError("invalid trace id")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            # Fail closed before touching the execution callback. Re-running a
            # tool after a trace failure is therefore not an ambiguity.
            raise TracePreflightError(f"Loopback intent could not be recorded ({type(exc).__name__}); operation was not invoked") from None

        meta = {TRACE_META_KEY: trace_id}
        try:
            result = self._invoke(name, args, meta=meta)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            await asyncio.shield(self._best_effort_report(trace_id, "cancelled"))
            raise
        except Exception as exc:
            safety_error = self._safety_error(str(exc))
            outcome = "safety_block" if safety_error else ("transport_error" if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError)) else "client_error")
            await self._best_effort_report(trace_id, outcome, error=safety_error)
            raise

        safety_error = self._result_safety_error(result)
        is_error = bool(result.get("isError", result.get("is_error", False)) if isinstance(result, dict)
                        else getattr(result, "isError", getattr(result, "is_error", False)))
        outcome = "safety_block" if safety_error else "tool_error" if is_error else "completed"
        await self._best_effort_report(trace_id, outcome, error=safety_error,
                                       receipt_id=self._result_receipt(result))
        return result
