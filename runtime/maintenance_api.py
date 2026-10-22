"""Admin API routes for maintenance: logs, doctor, diagnostics, backups, updates.

Implements the "Jobs, logs, diagnostics, maintenance" section of
``docs/ADMIN-API.md``. Authentication and CSRF for ``/api/admin/*`` are
enforced by the server's shared session+CSRF guard, so handlers here never
re-check authentication.

Conventions: 400 ``{"detail": msg}`` on invalid input, 404 when a named
object (e.g. a backup) is missing, 503 when the maintenance subsystem is not
available. Nothing here ever returns a secret -- log tails and diagnostic
bundles are redacted through ``maintenance.Redactor`` before leaving the
process.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import maintenance as maintenance_mod


class _HTTPError(Exception):
    def __init__(self, status: int, detail: str, **extra: Any):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra


def _err(status: int, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": detail, **extra}, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is a client error
        return {}
    return data if isinstance(data, dict) else {}


def _require(obj: Any, what: str) -> Any:
    if obj is None:
        raise _HTTPError(503, f"{what} subsystem is not available")
    return obj


def _audit(ctx: Any, action: str, target: str | None = None, status: str = "ok",
           detail: dict[str, Any] | None = None) -> None:
    try:
        ctx.audit.record(f"admin.{action}", status, target=target, detail=detail)
    except Exception:  # noqa: BLE001 - audit must never break a request
        pass


def _handler(fn: Any) -> Any:
    """Wrap a handler: map ``_HTTPError``/``ValueError``/``KeyError`` to JSON errors."""

    async def wrapped(request: Request) -> JSONResponse:
        try:
            return await fn(request)
        except _HTTPError as exc:
            return _err(exc.status, exc.detail, **exc.extra)
        except KeyError as exc:
            msg = exc.args[0] if exc.args else "not found"
            return _err(404, str(msg))
        except ValueError as exc:
            return _err(400, str(exc))

    wrapped.__name__ = getattr(fn, "__name__", "handler")
    wrapped.__doc__ = fn.__doc__
    return wrapped


_LOG_FILES = {"server": "server.log", "cloudflared": "cloudflared.log", "tailscale": "tailscale.log"}


def _tail_lines(path: Path, lines: int) -> list[str]:
    """Efficiently read the last *lines* lines of *path* without loading it whole."""
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    chunk = 8192
    data = b""
    with open(path, "rb") as handle:
        remaining = size
        while remaining > 0 and data.count(b"\n") <= lines:
            read_size = min(chunk, remaining)
            remaining -= read_size
            handle.seek(remaining)
            data = handle.read(read_size) + data
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def routes(ctx: Any) -> list[Route]:
    """Return the maintenance admin routes bound to *ctx*."""

    def _manager() -> Any:
        return _require(ctx.maintenance, "maintenance")

    # ------------------------------------------------------------------ logs
    @_handler
    async def logs_tail(request: Request) -> JSONResponse:
        file_key = request.query_params.get("file", "server")
        if file_key not in _LOG_FILES:
            raise _HTTPError(400, f"file must be one of {sorted(_LOG_FILES)}")
        try:
            lines = int(request.query_params.get("lines", "200"))
        except ValueError:
            raise _HTTPError(400, "lines must be an integer") from None
        lines = max(1, min(lines, 5000))
        log_path = Path(ctx.cfg_dir) / _LOG_FILES[file_key]
        raw_lines = _tail_lines(log_path, lines)
        redactor = maintenance_mod.Redactor([getattr(ctx.auth, "machine_token", None)])
        redacted = [redactor.redact(line) for line in raw_lines]
        return JSONResponse({"file": file_key, "lines": redacted, "redactions": redactor.count})

    # ---------------------------------------------------------------- doctor
    @_handler
    async def doctor_run(request: Request) -> JSONResponse:
        deep = request.query_params.get("deep") in {"1", "true", "yes"}
        checks = await asyncio.to_thread(maintenance_mod.doctor, ctx, deep)
        return JSONResponse({"checks": checks, "deep": deep})

    # ----------------------------------------------------------- diagnostics
    @_handler
    async def diagnostics_bundle(request: Request) -> JSONResponse:
        manager = _manager()
        result = await asyncio.to_thread(manager.diagnostics.create)
        _audit(ctx, "diagnostics_bundle", target=result.get("path"), detail={"size": result.get("size"),
                                                                              "redactions": result.get("redactions")})
        return JSONResponse(result)

    # --------------------------------------------------------------- backups
    @_handler
    async def backups_list(request: Request) -> JSONResponse:
        manager = _manager()
        return JSONResponse(await asyncio.to_thread(manager.backups.list))

    @_handler
    async def backups_create(request: Request) -> JSONResponse:
        manager = _manager()
        body = await _body(request)
        name = body.get("name")
        if name is not None and not isinstance(name, str):
            raise _HTTPError(400, "name must be a string")
        reason = body.get("reason", "manual")
        if not isinstance(reason, str):
            raise _HTTPError(400, "reason must be a string")
        result = await asyncio.to_thread(manager.backups.create, name, reason)
        _audit(ctx, "backup_create", target=result.get("path"), detail={"size": result.get("size")})
        return JSONResponse(result, status_code=201)

    @_handler
    async def backups_restore(request: Request) -> JSONResponse:
        manager = _manager()
        name = request.path_params["name"]
        body = await _body(request)
        confirm = body.get("confirm") is True
        if not confirm:
            raise _HTTPError(400, 'restore requires {"confirm": true}')
        result = await asyncio.to_thread(manager.backups.restore, name, confirm)
        _audit(ctx, "backup_restore", target=name, detail={"files": len(result.get("restored", []))})
        return JSONResponse(result)

    # --------------------------------------------------------------- updates
    @_handler
    async def updates_get(request: Request) -> JSONResponse:
        manager = _manager()
        return JSONResponse(manager.updates.cached())

    @_handler
    async def updates_check(request: Request) -> JSONResponse:
        manager = _manager()
        result = await asyncio.to_thread(manager.updates.check, True)
        _audit(ctx, "update_check", detail={"available": result.get("available"), "latest": result.get("latest")})
        return JSONResponse(result)

    # ---------------------------------------------------------------- alerts
    @_handler
    async def alerts_get(request: Request) -> JSONResponse:
        manager = _manager()
        return JSONResponse({"alerts": manager.alerts()})

    # --------------------------------------------------------------- metrics
    @_handler
    async def metrics_get(request: Request) -> JSONResponse:
        manager = _manager()
        return JSONResponse(manager.metrics())

    # ----------------------------------------------------------- maintenance
    @_handler
    async def maintenance_status(request: Request) -> JSONResponse:
        manager = _manager()
        return JSONResponse(manager.status())

    return [
        Route("/api/admin/logs", logs_tail, methods=["GET"]),
        Route("/api/admin/doctor", doctor_run, methods=["GET"]),
        Route("/api/admin/diagnostics/bundle", diagnostics_bundle, methods=["POST"]),
        Route("/api/admin/backups", backups_list, methods=["GET"]),
        Route("/api/admin/backups", backups_create, methods=["POST"]),
        Route("/api/admin/backups/{name}/restore", backups_restore, methods=["POST"]),
        Route("/api/admin/updates", updates_get, methods=["GET"]),
        Route("/api/admin/updates/check", updates_check, methods=["POST"]),
        Route("/api/admin/alerts", alerts_get, methods=["GET"]),
        Route("/api/admin/metrics", metrics_get, methods=["GET"]),
        Route("/api/admin/maintenance", maintenance_status, methods=["GET"]),
    ]
