"""Admin API for staged discovery and Cloudflare Mesh status (schemas: docs/DISCOVERY-API.md).

Every route lives under ``/api/admin/`` and therefore behind the existing AdminGate
(dashboard session + CSRF). Listing contacts no remote host. Probing and enrollment act
only on hosts named in the request body. Mesh routes are read-only: applying Mesh
changes needs root and runs from ``loopback mesh apply`` on the host.
"""
from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _scanner(ctx: Any):
    scanner = getattr(ctx, "scanner", None)
    if scanner is None:
        import scan

        scanner = scan.build_scanner(ctx)
        ctx.scanner = scanner
    return scanner


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _ids(body: dict[str, Any]) -> list[str]:
    ids = body.get("host_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or not 0 < len(ids) <= 200:
        raise ValueError("host_ids must be a list of 1 to 200 host ids")
    return ids


def _err(exc: Exception) -> JSONResponse:
    status = 404 if isinstance(exc, KeyError) else 400
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return JSONResponse({"detail": str(message)[:400]}, status_code=status)


def _mesh_settings(ctx: Any) -> dict[str, Any]:
    import mesh

    raw = (getattr(ctx.config, "data", {}) or {}).get("mesh_setup") or {}
    settings, _errors = mesh.parse_settings({"schema_version": 1, "mesh": {k: v for k, v in raw.items() if k != "token_ref"}})
    return settings


def routes(ctx: Any) -> list[Route]:
    async def state(request: Request):
        return JSONResponse(await asyncio.to_thread(_scanner(ctx).snapshot))

    async def list_hosts(request: Request):
        data = await asyncio.to_thread(_scanner(ctx).list_hosts)
        ctx.audit.record("discovery.list", "ok", target="dashboard", detail={"hosts": len(data["hosts"])})
        return JSONResponse(data)

    async def probe(request: Request):
        body = await _body(request)
        try:
            run = await asyncio.to_thread(_scanner(ctx).start_probe, _ids(body))
        except (ValueError, KeyError) as exc:
            return _err(exc)
        ctx.audit.record("discovery.probe", "started", target="dashboard", detail={"run": run["id"], "hosts": len(run["scope"]["host_ids"])})
        return JSONResponse(run, status_code=202)

    async def run_get(request: Request):
        try:
            return JSONResponse(_scanner(ctx).run(request.path_params["run_id"]))
        except KeyError as exc:
            return _err(exc)

    async def run_cancel(request: Request):
        try:
            run = _scanner(ctx).cancel(request.path_params["run_id"])
        except KeyError as exc:
            return _err(exc)
        ctx.audit.record("discovery.cancel", "ok", target="dashboard", detail={"run": run["id"]})
        return JSONResponse(run)

    async def manual_add(request: Request):
        body = await _body(request)
        try:
            entry = _scanner(ctx).add_manual(host=body.get("host", ""), port=body.get("port", 22), user=body.get("user") or None,
                                             label=body.get("label") or None, jump=body.get("jump") or None)
        except ValueError as exc:
            return _err(exc)
        ctx.audit.record("discovery.manual_add", "ok", target="dashboard", detail={"id": entry["id"]})
        return JSONResponse(entry, status_code=201)

    async def manual_delete(request: Request):
        removed = _scanner(ctx).remove_manual(request.path_params["entry_id"])
        return JSONResponse({"removed": removed}, status_code=200 if removed else 404)

    async def hostkey_scan(request: Request):
        body = await _body(request)
        try:
            return JSONResponse(await asyncio.to_thread(_scanner(ctx).host_key_scan, str(body.get("host_id") or "")))
        except (ValueError, KeyError, RuntimeError) as exc:
            return _err(exc)

    async def hostkey_confirm(request: Request):
        body = await _body(request)
        try:
            result = await asyncio.to_thread(_scanner(ctx).confirm_host_key, str(body.get("host_id") or ""), str(body.get("fingerprint") or ""))
        except (ValueError, KeyError, RuntimeError) as exc:
            return _err(exc)
        ctx.audit.record("discovery.hostkey_confirm", "ok", target="dashboard", detail={"host": result["host_id"], "fingerprint": result["fingerprint"]})
        return JSONResponse(result)

    async def enroll_plan(request: Request):
        body = await _body(request)
        try:
            plan = await asyncio.to_thread(_scanner(ctx).plan_enrollment, _ids(body), ssh_store=getattr(ctx, "ssh", None),
                                           install_worker=bool(body.get("install_worker", True)))
        except (ValueError, KeyError) as exc:
            return _err(exc)
        return JSONResponse(plan)

    async def enroll_apply(request: Request):
        body = await _body(request)
        plan_id = str(body.get("plan_id") or "")
        if getattr(ctx, "ssh", None) is None:
            return JSONResponse({"detail": "SSH targets are not available in this runtime"}, status_code=404)
        try:
            plan = await asyncio.to_thread(_scanner(ctx).apply_enrollment, plan_id, ssh_store=ctx.ssh, workers=getattr(ctx, "workers", None))
        except (ValueError, KeyError) as exc:
            return _err(exc)
        ctx.audit.record("discovery.enroll", plan["status"], target="dashboard", detail={"plan": plan_id, "steps": len(plan["steps"])})
        return JSONResponse(plan)

    async def enroll_rollback(request: Request):
        body = await _body(request)
        if getattr(ctx, "ssh", None) is None:
            return JSONResponse({"detail": "SSH targets are not available in this runtime"}, status_code=404)
        result = await asyncio.to_thread(_scanner(ctx).rollback_plan, str(body.get("plan_id") or ""), ssh_store=ctx.ssh)
        ctx.audit.record("discovery.rollback", "ok", target="dashboard", detail={"removed": len(result["removed"])})
        return JSONResponse(result)

    async def mesh_status(request: Request):
        import mesh
        import scan

        def build() -> dict[str, Any]:
            scanner = _scanner(ctx)
            ts = scanner.state.get("providers", {}).get("tailscale") or {}
            ts_info = {"state": ts.get("state")}
            return mesh.summary(scanner.env.runner if hasattr(scanner, "env") else scan.system_runner, _mesh_settings(ctx), tailscale=ts_info)

        return JSONResponse(await asyncio.to_thread(build))

    return [
        Route("/api/admin/discovery/hosts", state, methods=["GET"]),
        Route("/api/admin/discovery/hosts/list", list_hosts, methods=["POST"]),
        Route("/api/admin/discovery/probe", probe, methods=["POST"]),
        Route("/api/admin/discovery/runs/{run_id}", run_get, methods=["GET"]),
        Route("/api/admin/discovery/runs/{run_id}/cancel", run_cancel, methods=["POST"]),
        Route("/api/admin/discovery/manual", manual_add, methods=["POST"]),
        Route("/api/admin/discovery/manual/{entry_id}", manual_delete, methods=["DELETE"]),
        Route("/api/admin/discovery/hostkey/scan", hostkey_scan, methods=["POST"]),
        Route("/api/admin/discovery/hostkey/confirm", hostkey_confirm, methods=["POST"]),
        Route("/api/admin/discovery/enroll/plan", enroll_plan, methods=["POST"]),
        Route("/api/admin/discovery/enroll/apply", enroll_apply, methods=["POST"]),
        Route("/api/admin/discovery/enroll/rollback", enroll_rollback, methods=["POST"]),
        Route("/api/admin/mesh", mesh_status, methods=["GET"]),
    ]


__all__ = ["routes"]
