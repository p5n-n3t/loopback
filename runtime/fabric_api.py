"""Admin API routes for the Loopback fabric: nodes, SSH targets, topology, ingress.

Implements the "Fleet, SSH targets and topology" and "Ingress" sections of
``docs/ADMIN-API.md``. Authentication and CSRF are enforced by the server's
``/api/admin/*`` wrapper, so handlers here never re-check sessions.

Conventions: 400 ``{"detail": msg}`` on invalid input, 404 when an object is
missing, 503 when a subsystem is unavailable. Secrets (node bearer tokens,
tunnel credentials, key material) are never returned.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
NODE_KINDS = ("remote-physical", "remote-vm", "container", "local-computer", "gateway", "unknown")


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
        raise _HTTPError(400, "request body must be a JSON object") from None
    if not isinstance(data, dict):
        raise _HTTPError(400, "request body must be a JSON object")
    return data


def _opt_str(body: dict[str, Any], key: str, limit: int) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _HTTPError(400, f"{key} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise _HTTPError(400, f"{key} must be at most {limit} characters")
    return value or None


def _bool(body: dict[str, Any], key: str, default: bool) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise _HTTPError(400, f"{key} must be a boolean")
    return value


def _require(obj: Any, what: str) -> Any:
    if obj is None:
        raise _HTTPError(503, f"{what} subsystem is not available")
    return obj


def _audit(ctx: Any, action: str, target: str | None, status: str = "ok", detail: dict[str, Any] | None = None) -> None:
    try:
        ctx.audit.record(f"admin.{action}", status, target=target, detail=detail)
    except Exception:  # noqa: BLE001 - audit must never break a request
        pass


def _handler(fn: Any) -> Any:
    """Wrap a handler: map _HTTPError/ValueError/KeyError to JSON errors."""

    async def wrapped(request: Request) -> JSONResponse:
        try:
            return await fn(request)
        except _HTTPError as exc:
            return _err(exc.status, exc.detail, **exc.extra)
        except KeyError as exc:
            msg = exc.args[0] if exc.args else "not found"
            return _err(404, str(msg))
        except ValueError as exc:
            extra: dict[str, Any] = {"errors": [e.strip() for e in str(exc).split(";") if e.strip()]}
            diags = getattr(exc, "diagnostics", None)
            if diags:
                extra["diagnostics"] = diags
            return _err(400, str(exc), **extra)

    wrapped.__name__ = getattr(fn, "__name__", "handler")
    wrapped.__doc__ = fn.__doc__
    return wrapped


def routes(ctx: Any) -> list[Route]:
    """Return the fabric admin routes bound to *ctx*."""
    from topology import NodeMetaStore, safe_url

    meta_store = getattr(getattr(ctx, "topology", None), "node_meta", None) or NodeMetaStore(ctx.cfg_dir)

    def node_probe_state() -> dict[str, Any]:
        topo = getattr(ctx, "topology", None)
        if topo is None:
            return {}
        try:
            return topo.state()
        except Exception:  # noqa: BLE001
            return {}

    def public_node(node: dict[str, Any], state: dict[str, Any], meta_all: dict[str, Any]) -> dict[str, Any]:
        name = str(node.get("name"))
        meta = meta_all.get(name) if isinstance(meta_all.get(name), dict) else {}
        has_token = False
        try:
            has_token = bool(ctx.fleet.get(name)[1])
        except Exception:  # noqa: BLE001
            pass
        return {
            "name": name, "url": safe_url(node.get("url") or ""), "enabled": bool(node.get("enabled", True)),
            "description": node.get("description") or "", "display_name": meta.get("display_name"),
            "kind": meta.get("kind") or "unknown", "group": meta.get("group"), "has_token": has_token,
            "last_probe": state.get(f"gateway->node:{name}"),
        }

    # ------------------------------------------------------------------ nodes
    @_handler
    async def nodes_list(request: Request) -> JSONResponse:
        fleet = _require(ctx.fleet, "fleet")
        state, meta_all = node_probe_state(), meta_store.all()
        return JSONResponse([public_node(n, state, meta_all) for n in fleet.list() if isinstance(n, dict)])

    @_handler
    async def nodes_upsert(request: Request) -> JSONResponse:
        fleet = _require(ctx.fleet, "fleet")
        body = await _body(request)
        name = body.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name.strip()):
            raise _HTTPError(400, "name must match ^[A-Za-z0-9_.-]{1,40}$")
        name = name.strip()
        if name.lower() == "local":
            raise _HTTPError(400, "'local' is reserved")
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            raise _HTTPError(400, "url is required")
        url = url.strip()
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise _HTTPError(400, "url must be an absolute http(s) URL")
        if parts.username or parts.password:
            raise _HTTPError(400, "url must not embed credentials; use the token field")
        if parts.query or parts.fragment:
            raise _HTTPError(400, "url must not contain a query string or fragment")
        if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            # Plain HTTP would send the bearer token in clear text across the network.
            if not body.get("allow_insecure_http"):
                raise _HTTPError(400, "url must use https (plain http is only allowed for localhost)")
        token = body.get("token")
        if token is not None:
            if not isinstance(token, str) or len(token) > 4096 or any(c in token for c in "\r\n"):
                raise _HTTPError(400, "token must be a single-line string")
            token = token.strip() or None
        enabled = _bool(body, "enabled", True)
        description = _opt_str(body, "description", 200) or ""
        display_name = _opt_str(body, "display_name", 80)
        group = _opt_str(body, "group", 40)
        kind = body.get("kind") or "unknown"
        if kind not in NODE_KINDS:
            raise _HTTPError(400, f"kind must be one of {', '.join(NODE_KINDS)}")
        existed = any(n.get("name") == name for n in fleet.list())
        node = fleet.upsert(name, url, token, enabled=enabled, description=description)
        meta_store.set(name, display_name=display_name, kind=kind, group=group)
        _audit(ctx, "node.update" if existed else "node.create", name,
               detail={"url": safe_url(url), "token_changed": token is not None})
        return JSONResponse(public_node(node, node_probe_state(), meta_store.all()), status_code=200 if existed else 201)

    @_handler
    async def nodes_delete(request: Request) -> JSONResponse:
        fleet = _require(ctx.fleet, "fleet")
        name = request.path_params["name"]
        if not any(n.get("name") == name for n in fleet.list()):
            raise _HTTPError(404, f"node not found: {name}")
        fleet.remove(name)
        meta_store.remove(name)
        topo = getattr(ctx, "topology", None)
        if topo is not None:
            topo.forget(f"gateway->node:{name}")
        _audit(ctx, "node.delete", name)
        return JSONResponse({"ok": True, "deleted": name})

    @_handler
    async def nodes_probe(request: Request) -> JSONResponse:
        _require(ctx.fleet, "fleet")
        topo = _require(getattr(ctx, "topology", None), "topology")
        name = request.path_params["name"]
        try:
            ctx.fleet.get(name)
        except KeyError:
            raise _HTTPError(404, f"node not found: {name}") from None
        result = await topo.probe_node(name)
        _audit(ctx, "node.probe", name, detail={"status": result.get("status")})
        return JSONResponse(result)

    # ------------------------------------------------------------------ ssh
    @_handler
    async def ssh_list(request: Request) -> JSONResponse:
        ssh = _require(getattr(ctx, "ssh", None), "ssh")
        return JSONResponse(await asyncio.to_thread(ssh.list))

    @_handler
    async def ssh_upsert(request: Request) -> JSONResponse:
        ssh = _require(getattr(ctx, "ssh", None), "ssh")
        body = await _body(request)
        allowed = ("name", "host", "port", "user", "identity_file", "display_name", "kind", "group", "via")
        fields = {k: body[k] for k in allowed if k in body}
        name = fields.get("name")
        if not isinstance(name, str):
            raise _HTTPError(400, "name is required")
        for key in ("host", "user", "identity_file", "display_name", "kind", "group", "via"):
            if key in fields and fields[key] is not None and not isinstance(fields[key], str):
                raise _HTTPError(400, f"{key} must be a string")
        if "port" in fields and (isinstance(fields["port"], bool) or not isinstance(fields["port"], (int, str))):
            raise _HTTPError(400, "port must be an integer")
        existed = True
        try:
            ssh.get(name.strip())
        except KeyError:
            existed = False
        target = await asyncio.to_thread(lambda: ssh.upsert(**fields))
        _audit(ctx, "ssh.update" if existed else "ssh.create", target["name"],
               detail={"host": target.get("host"), "port": target.get("port")})
        return JSONResponse(target, status_code=200 if existed else 201)

    @_handler
    async def ssh_delete(request: Request) -> JSONResponse:
        ssh = _require(getattr(ctx, "ssh", None), "ssh")
        name = request.path_params["name"]
        if not ssh.remove(name):
            raise _HTTPError(404, f"ssh target not found: {name}")
        topo = getattr(ctx, "topology", None)
        if topo is not None:
            topo.forget(f"gateway->ssh:{name}")
        _audit(ctx, "ssh.delete", name)
        return JSONResponse({"ok": True, "deleted": name})

    @_handler
    async def ssh_probe(request: Request) -> JSONResponse:
        ssh = _require(getattr(ctx, "ssh", None), "ssh")
        name = request.path_params["name"]
        ssh.get(name)  # 404 if missing
        result = await asyncio.to_thread(ssh.probe, name)
        _audit(ctx, "ssh.probe", name, detail={"status": result.get("status")})
        return JSONResponse(result)

    # ------------------------------------------------------------------ topology
    @_handler
    async def topology_get(request: Request) -> JSONResponse:
        topo = _require(getattr(ctx, "topology", None), "topology")
        if getattr(ctx, "workers", None):
            await asyncio.to_thread(ctx.workers.snapshot)
        return JSONResponse(await asyncio.to_thread(topo.build))

    @_handler
    async def topology_refresh(request: Request) -> JSONResponse:
        topo = _require(getattr(ctx, "topology", None), "topology")
        result = await topo.refresh()
        _audit(ctx, "topology.refresh", None, detail={"duration_ms": getattr(topo, "last_refresh_ms", None)})
        return JSONResponse(result)

    @_handler
    async def topology_rename(request: Request) -> JSONResponse:
        topo = _require(getattr(ctx, "topology", None), "topology")
        node_id = request.path_params["id"]
        body = await _body(request)
        display_name = _opt_str(body, "display_name", 80)
        group = _opt_str(body, "group", 40)
        node = await asyncio.to_thread(topo.rename, node_id, display_name, group)
        _audit(ctx, "topology.rename", node_id)
        return JSONResponse(node)

    # ------------------------------------------------------------------ ingress
    @_handler
    async def ingress_get(request: Request) -> JSONResponse:
        ing = _require(getattr(ctx, "ingress", None), "ingress")
        return JSONResponse(await asyncio.to_thread(ing.status))

    @_handler
    async def ingress_cloudflare(request: Request) -> JSONResponse:
        ing = _require(getattr(ctx, "ingress", None), "ingress")
        body = await _body(request)
        enabled = _bool(body, "enabled", True)
        hostname = _opt_str(body, "hostname", 253)
        tunnel_id = _opt_str(body, "tunnel_id", 64)
        token_file = _opt_str(body, "token_file", 4096)
        access_policy = body.get("access_policy") or "oauth"
        if not isinstance(access_policy, str):
            raise _HTTPError(400, "access_policy must be a string")
        try:
            result = await asyncio.to_thread(ing.apply_cloudflare, enabled, hostname, tunnel_id, token_file,
                                             access_policy)
        except ValueError as exc:
            _audit(ctx, "ingress.cloudflare", hostname, "error", {"error": str(exc)[:300]})
            raise
        _audit(ctx, "ingress.cloudflare", hostname or "cloudflare", detail={"enabled": enabled,
                                                                            "access_policy": access_policy})
        return JSONResponse(result)

    @_handler
    async def ingress_tailscale(request: Request) -> JSONResponse:
        ing = _require(getattr(ctx, "ingress", None), "ingress")
        body = await _body(request)
        enabled = _bool(body, "enabled", True)
        mode = body.get("mode") or "serve"
        if mode not in ("serve", "funnel"):
            raise _HTTPError(400, "mode must be 'serve' or 'funnel'")
        port = body.get("https_port", 443)
        if isinstance(port, bool) or not isinstance(port, int):
            raise _HTTPError(400, "https_port must be an integer")
        confirm = _bool(body, "confirm_public", False)
        try:
            result = await asyncio.to_thread(ing.apply_tailscale, enabled, mode, port, confirm)
        except ValueError as exc:
            _audit(ctx, "ingress.tailscale", "tailscale", "error", {"error": str(exc)[:300]})
            raise
        _audit(ctx, "ingress.tailscale", "tailscale", detail={"enabled": enabled, "mode": mode, "https_port": port})
        return JSONResponse(result)

    @_handler
    async def ingress_restart(request: Request) -> JSONResponse:
        ing = _require(getattr(ctx, "ingress", None), "ingress")
        name = request.path_params["name"]
        if name not in ("cloudflare", "tailscale"):
            raise _HTTPError(404, f"unknown ingress: {name}")
        try:
            result = await asyncio.to_thread(ing.restart, name)
        except ValueError as exc:
            _audit(ctx, "ingress.restart", name, "error", {"error": str(exc)[:300]})
            raise
        _audit(ctx, "ingress.restart", name)
        return JSONResponse(result)

    @_handler
    async def ingress_diagnostics(request: Request) -> JSONResponse:
        ing = _require(getattr(ctx, "ingress", None), "ingress")
        name = request.path_params["name"]
        if name not in ("cloudflare", "tailscale"):
            raise _HTTPError(404, f"unknown ingress: {name}")
        return JSONResponse(await asyncio.to_thread(ing.diagnostics, name))

    # Worker control is dashboard-only: it never becomes an MCP tool or accepts
    # bearer credentials at the administration boundary.
    @_handler
    async def workers_get(request: Request) -> JSONResponse:
        manager = _require(getattr(ctx, "workers", None), "workers")
        return JSONResponse(await asyncio.to_thread(manager.snapshot))

    @_handler
    async def workers_install(request: Request) -> JSONResponse:
        manager = _require(getattr(ctx, "workers", None), "workers")
        target = request.path_params["target"]
        result = await asyncio.to_thread(manager.install, target)
        _audit(ctx, "worker.install", target)
        return JSONResponse(result)

    @_handler
    async def bindings_save(request: Request) -> JSONResponse:
        manager = _require(getattr(ctx, "workers", None), "workers")
        result = await asyncio.to_thread(manager.save, await _body(request))
        _audit(ctx, "binding.save", result["name"])
        return JSONResponse(result, status_code=201)

    @_handler
    async def bindings_action(request: Request) -> JSONResponse:
        manager = _require(getattr(ctx, "workers", None), "workers")
        name, action = request.path_params["name"], request.path_params["action"]
        result = await asyncio.to_thread(manager.action, name, action)
        _audit(ctx, "binding." + action, name)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return [
        Route("/api/admin/workers", workers_get, methods=["GET"]),
        Route("/api/admin/workers/{target}/install", workers_install, methods=["POST"]),
        Route("/api/admin/bindings", bindings_save, methods=["POST"]),
        Route("/api/admin/bindings/{name}/{action}", bindings_action, methods=["POST"]),
        Route("/api/admin/nodes", nodes_list, methods=["GET"]),
        Route("/api/admin/nodes", nodes_upsert, methods=["POST"]),
        Route("/api/admin/nodes/{name}", nodes_delete, methods=["DELETE"]),
        Route("/api/admin/nodes/{name}/probe", nodes_probe, methods=["POST"]),
        Route("/api/admin/ssh", ssh_list, methods=["GET"]),
        Route("/api/admin/ssh", ssh_upsert, methods=["POST"]),
        Route("/api/admin/ssh/{name}", ssh_delete, methods=["DELETE"]),
        Route("/api/admin/ssh/{name}/probe", ssh_probe, methods=["POST"]),
        Route("/api/admin/topology", topology_get, methods=["GET"]),
        Route("/api/admin/topology/refresh", topology_refresh, methods=["POST"]),
        Route("/api/admin/topology/nodes/{id}", topology_rename, methods=["PUT"]),
        Route("/api/admin/ingress", ingress_get, methods=["GET"]),
        Route("/api/admin/ingress/cloudflare", ingress_cloudflare, methods=["PUT"]),
        Route("/api/admin/ingress/tailscale", ingress_tailscale, methods=["PUT"]),
        Route("/api/admin/ingress/{name}/restart", ingress_restart, methods=["POST"]),
        Route("/api/admin/ingress/{name}/diagnostics", ingress_diagnostics, methods=["GET"]),
    ]
