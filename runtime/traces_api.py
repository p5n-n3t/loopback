"""Authenticated trace ingestion and admin reports. Never executes or retries a tool."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import auth_http

COVERAGE = {
    "gateway": "automatic",
    "client_preflight": "instrumented_clients_only",
    "chatgpt_web": "unavailable",
    "note": "Gateway traces begin after authentication. A missing receipt does not identify an upstream blocker. "
            "ChatGPT Web does not send this recorder's preflight telemetry.",
}


def owner_key(principal):
    key = f"{principal.kind}:{principal.subject}"
    return key + (f"#{principal.client_id}" if principal.client_id else "")


async def _body(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise ValueError("trace metadata exceeds 8 KiB")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def routes(ctx):
    def principal(request):
        p = ctx.auth.authenticate(request.headers.get("authorization"),
                                  request.headers.get("x-api-key") or request.headers.get("api-key"),
                                  legacy_machine_token=ctx.legacy_mcp_token_enabled())
        if p is None:
            return None, auth_http.unauthorized(ctx, request, error="invalid_token")
        if not p.has("loopback:read"):
            return None, auth_http.forbidden_scope(ctx, request, "loopback:read", "trace access requires loopback:read")
        return p, None

    async def intent(request: Request):
        p, error = principal(request)
        if error is not None:
            return error
        try:
            body = await _body(request)
            # This endpoint never accepts commands, raw arguments, or arbitrary descriptions.
            if set(body) - {"tool", "target", "client", "ttl_seconds"}:
                raise ValueError("unsupported trace field")
            catalog = {t["name"]: t for t in ctx.tool_catalog()}
            tool = body.get("tool")
            if not isinstance(tool, str) or tool not in catalog:
                raise ValueError("unknown tool")
            target = body.get("target")
            if catalog[tool].get("target") == "local":
                target = "local"
            elif target is not None and (not isinstance(target, str) or len(target) > 128):
                raise ValueError("invalid target")
            record = ctx.traces.create_intent(owner_key(p), tool, target, body.get("client", "unknown"),
                                              ttl_seconds=int(body.get("ttl_seconds", 120)))
            return JSONResponse(record, status_code=201, headers=auth_http.NO_STORE)
        except (ValueError, TypeError, OverflowError):
            return JSONResponse({"error": "invalid_trace_metadata"}, status_code=400, headers=auth_http.NO_STORE)

    async def client_report(request: Request):
        p, error = principal(request)
        if error is not None:
            return error
        trace_id = request.path_params["trace_id"]
        if ctx.traces.get(trace_id, owner_key(p)) is None:
            return JSONResponse({"error": "trace_not_found"}, status_code=404, headers=auth_http.NO_STORE)
        try:
            body = await _body(request)
            if set(body) - {"outcome", "error", "receipt_id"}:
                raise ValueError("unsupported trace field")
            ctx.traces.client_report(owner_key(p), trace_id, body.get("outcome"), body.get("error"), body.get("receipt_id"))
            return JSONResponse(ctx.traces.get(trace_id, owner_key(p)), headers=auth_http.NO_STORE)
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "invalid_trace_report"}, status_code=400, headers=auth_http.NO_STORE)

    async def listing(request: Request):
        owner = None
        if not request.url.path.startswith("/api/admin/"):
            p, error = principal(request)
            if error is not None:
                return error
            owner = owner_key(p)
        try:
            params = request.query_params
            times = {}
            for name in ("since", "until"):
                if params.get(name):
                    times[name] = float(params[name])
                    if not math.isfinite(times[name]):
                        raise ValueError("invalid timestamp")
            ctx.traces.reconcile()
            records = ctx.traces.recent(limit=int(params.get("limit", 100)), owner=owner,
                                       status=params.get("status"), search=params.get("search"), **times)
            return JSONResponse({"traces": records, "coverage": COVERAGE}, headers=auth_http.NO_STORE)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_trace_filter"}, status_code=400, headers=auth_http.NO_STORE)

    async def detail(request: Request):
        owner = None
        if not request.url.path.startswith("/api/admin/"):
            p, error = principal(request)
            if error is not None:
                return error
            owner = owner_key(p)
        ctx.traces.reconcile()
        record = ctx.traces.report(request.path_params["trace_id"], owner=owner)
        if record is None:
            return JSONResponse({"error": "trace_not_found"}, status_code=404, headers=auth_http.NO_STORE)
        if not request.url.path.endswith("/export"):
            return JSONResponse(record, headers=auth_http.NO_STORE)
        fmt = request.query_params.get("format", "json")
        if fmt not in {"json", "markdown"}:
            return JSONResponse({"error": "unsupported_format"}, status_code=400, headers=auth_http.NO_STORE)
        extension = "json" if fmt == "json" else "md"
        payload = json.dumps(record, indent=2) if fmt == "json" else record["report_markdown"]
        return Response(payload, media_type="application/json" if fmt == "json" else "text/markdown",
                        headers={**auth_http.NO_STORE, "X-Content-Type-Options": "nosniff",
                                 "Content-Disposition": f'attachment; filename="loopback-trace-{record["trace"]["trace_id"]}.{extension}"'})

    out = [Route("/api/traces/intents", intent, methods=["POST"]),
           Route("/api/traces/{trace_id}/report", client_report, methods=["POST"])]
    for prefix in ("/api/traces", "/api/admin/traces"):
        out += [Route(prefix, listing, methods=["GET"]),
                Route(prefix + "/{trace_id}", detail, methods=["GET"]),
                Route(prefix + "/{trace_id}/export", detail, methods=["GET"])]
    return out


async def reconcile_forever(ctx, stop):
    """Persist incomplete lifecycles without executing any remote operation."""
    try:
        await asyncio.to_thread(ctx.traces.backfill_ssh_receipts, ctx.audit)
    except Exception as exc:
        logging.getLogger(__name__).error("trace history import failed (%s)", type(exc).__name__)
    last_pruned = 0.0
    while not stop.is_set():
        try:
            await asyncio.to_thread(ctx.traces.reconcile)
            if time.monotonic() - last_pruned >= 3600:
                days = int(ctx.config.data.get("maintenance", {}).get("audit_retention_days", 90))
                await asyncio.to_thread(ctx.traces.prune, retention_days=days)
                last_pruned = time.monotonic()
        except Exception as exc:
            logging.getLogger(__name__).error("trace reconciliation failed (%s)", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass
