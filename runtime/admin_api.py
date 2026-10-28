"""Core dashboard API: overview, configuration, policy/permissions, auth, tools, jobs, audit.

Authentication and CSRF for ``/api/admin/*`` are enforced by the server's
``AdminGate``; handlers here assume an authenticated administrator.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import identity as identity_mod
from auth import AuthError
from auth_http import NO_STORE, SESSION_COOKIE, client_ip
from config import validate as validate_config
from context import AppContext
from policy import PROFILES, PROFILE_ALIASES, PolicyError
from sandbox import SandboxManager


def bad_request(detail: str, errors: list[str] | None = None, status: int = 400) -> JSONResponse:
    body: dict[str, Any] = {"detail": detail}
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, headers=NO_STORE)


def ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


async def read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def human_bytes(value: int) -> str:
    n = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def system_snapshot(ctx: AppContext) -> dict[str, Any]:
    load = list(os.getloadavg()) if hasattr(os, "getloadavg") else [0.0, 0.0, 0.0]
    mem_total = mem_available = 0
    try:
        vals: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            vals[key] = int(raw.strip().split()[0]) * 1024
        mem_total = vals.get("MemTotal", 0)
        mem_available = vals.get("MemAvailable", 0)
    except (OSError, ValueError):
        pass
    try:
        disk = shutil.disk_usage(ctx.identity.home)
    except OSError:
        disk = shutil.disk_usage(str(ctx.cfg_dir))
    return {
        "hostname": socket.gethostname(),
        "uptime_seconds": int(time.time() - ctx.started_at),
        "load": [round(x, 2) for x in load],
        "memory_total": mem_total,
        "memory_used": max(0, mem_total - mem_available),
        "memory_total_human": human_bytes(mem_total),
        "memory_used_human": human_bytes(max(0, mem_total - mem_available)),
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_total_human": human_bytes(disk.total),
        "disk_used_human": human_bytes(disk.used),
        "python": os.sys.version.split()[0],
    }


def ingress_summary(ctx: AppContext) -> dict[str, Any]:
    if ctx.ingress is not None:
        try:
            return ctx.ingress.status()
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": str(exc)[:200]}
    cfg = ctx.config.data["ingress"]
    return {
        "local": {"enabled": True, "url": f"http://127.0.0.1:{ctx.config.port}", "exposure": "local"},
        "cloudflare": {"enabled": bool(cfg["cloudflare"].get("enabled")), "hostname": cfg["cloudflare"].get("hostname"), "exposure": "public"},
        "tailscale": {"enabled": bool(cfg["tailscale"].get("enabled")), "mode": cfg["tailscale"].get("mode"), "dns_name": cfg["tailscale"].get("dns_name"),
                      "exposure": "public" if cfg["tailscale"].get("mode") == "funnel" else "tailnet"},
    }


def basic_alerts(ctx: AppContext) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if ctx.legacy_mcp_token_enabled():
        alerts.append({"level": "warning", "code": "legacy_token", "action": "#/auth",
                       "message": "MCP clients may still authenticate with the shared machine token. Create per-client API tokens or reconnect clients with OAuth, then disable legacy access."})
    if not ctx.auth.has_password():
        alerts.append({"level": "warning", "code": "no_password", "action": "#/auth",
                       "message": "No dashboard password is set. Run `loopback auth enroll` to create one."})
    sb = ctx.sandbox.status()
    if not sb.get("active"):
        alerts.append({"level": "warning" if sb.get("restricted_shell_permitted") else "error", "code": "sandbox_inactive", "action": "#/local",
                       "message": f"OS filesystem sandbox is not active: {sb.get('reason')}"})
    return alerts


def rebuild_sandbox(ctx: AppContext) -> None:
    sb_cfg = ctx.config.data.get("sandbox", {})
    ctx.sandbox = SandboxManager(str(sb_cfg.get("backend", "auto")), require_enforcement=bool(sb_cfg.get("require_enforcement", True)),
                                 allow_tmp=bool(sb_cfg.get("allow_tmp", True)), acknowledge_unenforced=bool(sb_cfg.get("acknowledge_unenforced", False)))
    ctx.policy.sandbox_active = ctx.sandbox.enforced()
    ctx.policy.sandbox_acknowledged = bool(sb_cfg.get("acknowledge_unenforced", False)) or not bool(sb_cfg.get("require_enforcement", True))


def routes(ctx: AppContext) -> list[Route]:
    # ------------------------------------------------------------------ overview
    async def overview(request: Request) -> Response:
        maint = ctx.maintenance
        alerts = basic_alerts(ctx)
        update = {"current": ctx.version, "latest": None, "channel": ctx.config.data["maintenance"]["update_channel"], "checked_at": None, "available": False}
        if maint is not None:
            try:
                alerts = maint.alerts()
            except Exception:
                pass
            try:
                update = maint.updates.cached() if hasattr(maint, "updates") else update
            except Exception:
                pass
        ssh_targets = []
        if ctx.ssh is not None:
            try:
                ssh_targets = ctx.ssh.list()
            except Exception:
                ssh_targets = []
        return ok({
            "version": ctx.version,
            "identity": {**ctx.identity.as_dict(), "machine_kind": identity_mod.detect_virtualization(),
                         "effective_shell": ctx.config.effective_shell(), "initial_cwd": ctx.config.effective_initial_cwd()},
            "system": system_snapshot(ctx),
            "policy": dict(ctx.policy.data),
            "permissions": ctx.policy.effective_permissions("local"),
            "sandbox": ctx.sandbox.status(),
            "auth": ctx.auth.summary(legacy_enabled=ctx.legacy_mcp_token_enabled()),
            "ingress": ingress_summary(ctx),
            "jobs": ctx.jobs.list() if ctx.jobs else [],
            "terminals": ctx.terminals.list() if ctx.terminals else [],
            "approvals": ctx.policy.pending(),
            "nodes": ctx.fleet.list() if ctx.fleet else [],
            "ssh_targets": ssh_targets,
            "audit": ctx.audit.stats(),
            "recent": ctx.audit.recent(60),
            "alerts": alerts,
            "update": update,
            "shield": ctx.shield.summary() if getattr(ctx, "shield", None) else None,
        })

    async def identity_view(request: Request) -> Response:
        facts = identity_mod.describe_host()
        return ok({**ctx.identity.as_dict(), "host": facts, "host_facts": facts, "effective_shell": ctx.config.effective_shell(),
                   "initial_cwd": ctx.config.effective_initial_cwd(), "terminals_supported": bool(ctx.terminals and ctx.terminals.available())})

    # ------------------------------------------------------------------- config
    async def config_get(request: Request) -> Response:
        return ok(ctx.config.public_view())

    async def config_put(request: Request) -> Response:
        body = await read_json(request)
        body.pop("schema_version", None)
        body.pop("version", None)
        before = dict(ctx.config.data)
        try:
            data = ctx.config.save(body, actor="dashboard")
        except ValueError as exc:
            return bad_request("configuration rejected", str(exc).split("; "))
        ctx.refresh_auth_urls()
        notes: list[str] = []
        if "sandbox" in body:
            rebuild_sandbox(ctx)
        if "shell" in body and ctx.jobs:
            ctx.jobs.shell = ctx.config.effective_shell()
            ctx.terminals.shell = ctx.jobs.shell
        if before.get("port") != data.get("port") or before.get("bind") != data.get("bind"):
            notes.append("port/bind changes take effect after `loopback restart`")
        if "auth" in body:
            a = data["auth"]
            ctx.auth.access_ttl = int(a["access_ttl_seconds"])
            ctx.auth.refresh_ttl = int(a["refresh_ttl_seconds"])
            ctx.auth.session_ttl = int(a["session_ttl_seconds"])
            ctx.auth.code_ttl = int(a["code_ttl_seconds"])
        return ok({"config": ctx.config.public_view(), "notes": notes})

    async def config_validate(request: Request) -> Response:
        body = await read_json(request)
        from config import _deep_merge  # local import: helper, not public API

        merged = _deep_merge(ctx.config.data, body)
        return ok({"errors": validate_config(merged)})

    async def config_rollback(request: Request) -> Response:
        try:
            ctx.config.rollback()
        except (FileNotFoundError, ValueError) as exc:
            return bad_request(str(exc))
        ctx.refresh_auth_urls()
        rebuild_sandbox(ctx)
        ctx.audit.record("config.rollback", "ok", target="dashboard")
        return ok(ctx.config.public_view())

    # ------------------------------------------------------------------- policy
    async def policy_get(request: Request) -> Response:
        return ok(dict(ctx.policy.data))

    async def policy_put(request: Request) -> Response:
        body = await read_json(request)
        allowed_keys = {"profile", "allowed_roots", "denied_roots", "allow_shell", "allow_process_control", "allow_fleet",
                        "allow_browser", "allow_desktop", "allowed_tools", "denied_tools", "approval_ttl_seconds",
                        "require_approval_patterns", "deny_command_patterns", "sandbox_required"}
        updates = {k: v for k, v in body.items() if k in allowed_keys}
        if not updates:
            return bad_request("no recognised policy fields in body")
        try:
            data = ctx.policy.save(updates)
        except ValueError as exc:
            return bad_request("policy rejected", str(exc).split("; "))
        ctx.audit.record("policy.change", "ok", target="dashboard", detail={"keys": sorted(updates)})
        warnings: list[str] = []
        shell_ok, reason = ctx.policy.shell_enabled("local")
        if not shell_ok and reason:
            warnings.append(reason)
        return ok({"policy": data, "warnings": warnings, "permissions": ctx.policy.effective_permissions("local")})

    async def policy_tools(request: Request) -> Response:
        """Authoritative per-tool enable/disable merge (fixes lost updates from client-side list editing).

        Body: ``{"changes": {"tool_name": true|false, ...}}`` and/or ``{"all": true|false, "names": [...]}``
        where ``names`` optionally limits a bulk change to a subset (a filtered or category view).
        Disabling every known tool writes the explicit deny-all marker ``["*"]`` so unknown and future
        tools stay refused too; enabling anything afterwards expands the marker into explicit names.
        """
        body = await read_json(request)
        catalog = ctx.tool_catalog() if ctx.tool_catalog else []
        known = [t["name"] for t in catalog]
        changes: dict[str, bool] = {}
        if "all" in body:
            if not isinstance(body["all"], bool):
                return bad_request("all must be true or false")
            names = body.get("names")
            if names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
                return bad_request("names must be a list of tool names")
            for name in (names if names is not None else known):
                changes[name] = body["all"]
        raw = body.get("changes") or {}
        if not isinstance(raw, dict) or not all(isinstance(v, bool) for v in raw.values()):
            return bad_request("changes must map tool names to true/false")
        changes.update({str(k): v for k, v in raw.items()})
        unknown = sorted(set(changes) - set(known))
        if unknown:
            return bad_request("unknown tools", unknown)
        if not changes:
            return bad_request("no tool changes in body")
        with ctx.policy._lock:
            policy = ctx.policy.data
            denied_now = [str(x) for x in (policy.get("denied_tools") or [])]
            allowed_now = [str(x) for x in (policy.get("allowed_tools") or ["*"])]
            wildcard_allowed = "*" in allowed_now or not allowed_now
            # Effective state per known tool from the policy lists alone (browser/desktop gates are separate).
            enabled = {n: (n not in denied_now and "*" not in denied_now and (wildcard_allowed or n in allowed_now)) for n in known}
            enabled.update(changes)
            extra_denied = [x for x in denied_now if x not in known and x != "*"]
            if known and not any(enabled.values()):
                denied = ["*", *extra_denied]
                allowed = allowed_now if not wildcard_allowed else ["*"]
            else:
                denied = sorted(n for n in known if not enabled[n]) + extra_denied
                if wildcard_allowed:
                    allowed = ["*"]
                else:
                    keep = [x for x in allowed_now if x not in known]
                    allowed = keep + sorted(n for n in known if enabled[n])
            try:
                data = ctx.policy.save({"allowed_tools": allowed, "denied_tools": denied})
            except ValueError as exc:
                return bad_request("policy rejected", str(exc).split("; "))
        ctx.audit.record("policy.change", "ok", target="dashboard",
                         detail={"keys": ["allowed_tools", "denied_tools"], "tools": sorted(changes), "enabled": sum(changes.values())})
        warnings: list[str] = []
        tools = ctx.tool_catalog() if ctx.tool_catalog else []
        for t in tools:
            if changes.get(t["name"]) is True and not t["enabled"]:
                warnings.append(f"{t['name']} stays disabled until {t['category']} automation is allowed in the policy")
        return ok({"policy": data, "tools": tools, "warnings": warnings})

    async def policy_profiles(request: Request) -> Response:
        return ok({"profiles": PROFILES, "aliases": PROFILE_ALIASES, "current": ctx.policy.profile})

    async def permissions_get(request: Request) -> Response:
        target = request.query_params.get("target", "local")
        perms = ctx.policy.effective_permissions(target)
        return ok({**perms, "sandbox": ctx.sandbox.status() if target in {"local", ""} else None})

    async def permissions_put(request: Request) -> Response:
        target = request.path_params["target"]
        if target == "local":
            return bad_request("use PUT /api/admin/policy for the local machine")
        body = await read_json(request)
        allowed = {k: v for k, v in body.items() if k in {"allowed_roots", "denied_roots", "allow_shell", "allow_process_control", "profile", "require_approval_patterns"}}
        try:
            data = ctx.policy.set_target_policy(target, allowed)
        except ValueError as exc:
            return bad_request("target policy rejected", str(exc).split("; "))
        ctx.audit.record("policy.target_change", "ok", target=target, detail={"keys": sorted(allowed)})
        return ok({"target": target, "policy": data, "permissions": ctx.policy.effective_permissions(target)})

    async def permissions_delete(request: Request) -> Response:
        target = request.path_params["target"]
        removed = ctx.policy.remove_target_policy(target)
        ctx.audit.record("policy.target_remove", "ok", target=target)
        return ok({"removed": removed})

    async def validate_path(request: Request) -> Response:
        body = await read_json(request)
        raw = str(body.get("path") or "").strip()
        if not raw:
            return bad_request("path is required")
        expanded = raw
        if expanded == "~" or expanded.startswith("~/"):
            expanded = ctx.identity.home + expanded[1:]
        p = Path(expanded)
        resolved = p.resolve(strict=False)
        warnings: list[str] = []
        symlink_target = None
        if p.is_symlink():
            symlink_target = os.readlink(p)
            warnings.append("path is a symbolic link; access is evaluated on the resolved target")
        if not resolved.exists():
            warnings.append("path does not exist yet")
        elif not resolved.is_dir():
            warnings.append("path is a file, not a directory")
        home = Path(ctx.identity.home).resolve()
        within_home = resolved == home or home in resolved.parents
        if resolved == Path("/"):
            warnings.append("granting / exposes the entire filesystem to the shell profile")
        for sensitive in ("/etc", "/root", "/var/lib", "/usr", "/boot", "/proc", "/sys"):
            if str(resolved) == sensitive or str(resolved).startswith(sensitive + "/"):
                warnings.append(f"{sensitive} contains system or credential material")
                break
        if str(resolved).startswith(str(ctx.cfg_dir)):
            warnings.append("this is the Loopback configuration directory; it holds credentials and is denied by default")
        if not os.access(resolved, os.R_OK) if resolved.exists() else False:
            warnings.append("the Loopback service account cannot read this path")
        return ok({"input": raw, "resolved": str(resolved), "exists": resolved.exists(), "is_dir": resolved.is_dir(),
                   "symlink_target": symlink_target, "within_home": within_home, "warnings": warnings})

    async def sandbox_self_test(request: Request) -> Response:
        tp = ctx.policy.target_policy("local")
        plan = ctx.sandbox.plan_from_policy(ctx.policy.root_entries("local"), tp.get("denied_roots") or [], ctx.identity.home)
        result = await asyncio.to_thread(ctx.sandbox.self_test, plan, ctx.config.effective_shell())
        ctx.audit.record("sandbox.self_test", "ok" if result.get("ok") else "failed", target="dashboard")
        return ok(result)

    async def approvals_get(request: Request) -> Response:
        return ok(ctx.policy.pending())

    async def approve(request: Request) -> Response:
        body = await read_json(request)
        try:
            result = ctx.policy.approve(request.path_params["approval_id"], uses=int(body.get("uses", 1)), ttl_seconds=body.get("ttl_seconds"))
        except KeyError:
            return bad_request("approval not found", status=404)
        except (TypeError, ValueError):
            return bad_request("uses/ttl_seconds must be integers")
        ctx.audit.record("approval.grant", "ok", target=request.path_params["approval_id"], detail={"uses": result["uses_left"]})
        return ok(result)

    async def deny(request: Request) -> Response:
        removed = ctx.policy.deny(request.path_params["approval_id"])
        ctx.audit.record("approval.deny", "ok", target=request.path_params["approval_id"])
        return ok({"removed": removed})

    # --------------------------------------------------------------------- auth
    def _methods() -> list[dict[str, Any]]:
        legacy = ctx.legacy_mcp_token_enabled()
        return [
            {"id": "oauth", "title": "OAuth 2.1 + PKCE", "recommended": True, "enabled": True,
             "summary": "For MCP clients that support OAuth (ChatGPT, Claude, Cursor…). The client discovers this server, you approve it once, and it receives short-lived tokens scoped to this instance."},
            {"id": "api_token", "title": "API token (bearer)", "recommended": True, "enabled": True,
             "summary": "For clients that only accept a static bearer header. Create a named, scoped, optionally expiring token here; it is shown once and can be revoked at any time."},
            {"id": "legacy_machine_token", "title": "Legacy shared machine token", "recommended": False, "enabled": legacy,
             "summary": "The pre-v4 behaviour: one root-equivalent secret for every client. Kept only so upgraded installations keep working while you migrate."},
        ]

    async def auth_get(request: Request) -> Response:
        return ok({**ctx.auth.summary(legacy_enabled=ctx.legacy_mcp_token_enabled()), "methods": _methods(),
                   "clients": ctx.auth.list_clients(), "api_tokens": ctx.auth.list_api_tokens(), "grants": ctx.auth.active_grants(),
                   "sessions": ctx.auth.list_sessions(), "events": ctx.auth.recent_events(50),
                   "machine_token_hint": "Print locally with `loopback token`; it is a recovery credential and is never shown here."})

    async def token_create(request: Request) -> Response:
        body = await read_json(request)
        ttl = body.get("ttl_seconds")
        if ttl is not None:
            try:
                ttl = int(ttl)
            except (TypeError, ValueError):
                return bad_request("ttl_seconds must be an integer or null")
            if ttl <= 0 or ttl > 366 * 86400:
                return bad_request("ttl_seconds must be between 1 second and 366 days")
        scopes = body.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        try:
            value, record = ctx.auth.create_api_token(str(body.get("name") or ""), scopes, ttl_seconds=ttl,
                                                      created_by="dashboard", note=str(body.get("note") or ""))
        except AuthError as exc:
            return bad_request(exc.description or exc.error)
        ctx.audit.record("auth.token_create", "ok", target=record["id"], detail={"scope": record["scope"]})
        return ok({"token": value, "record": record, "usage": {"header": "Authorization: Bearer <token>"}}, status=201)

    async def token_delete(request: Request) -> Response:
        removed = ctx.auth.revoke_api_token(request.path_params["token_id"])
        ctx.audit.record("auth.token_revoke", "ok", target=request.path_params["token_id"])
        return ok({"revoked": removed})

    async def client_delete(request: Request) -> Response:
        removed = ctx.auth.revoke_client(request.path_params["client_id"])
        ctx.audit.record("auth.client_revoke", "ok", target=request.path_params["client_id"])
        return ok({"revoked": removed})

    async def grant_delete(request: Request) -> Response:
        ctx.auth.revoke_family(request.path_params["family_id"])
        ctx.audit.record("auth.grant_revoke", "ok", target=request.path_params["family_id"])
        return ok({"revoked": True})

    async def password(request: Request) -> Response:
        body = await read_json(request)
        current = str(body.get("current") or "")
        new = str(body.get("new") or "")
        if ctx.auth.has_password() and not ctx.auth.verify_admin_credential(current):
            ctx.auth.limiter.failure("login", client_ip(request))
            return bad_request("current credential is incorrect", status=403)
        try:
            ctx.auth.set_password(new)
        except AuthError as exc:
            return bad_request(exc.description)
        ctx.audit.record("auth.password_change", "ok", target="dashboard")
        return ok({"password_set": True})

    async def legacy_toggle(request: Request) -> Response:
        body = await read_json(request)
        enabled = bool(body.get("enabled", False))
        try:
            ctx.config.save({"auth": {"legacy_machine_token_mcp": enabled}}, actor="dashboard")
        except ValueError as exc:
            return bad_request(str(exc))
        ctx.auth.record_event("legacy_token_mcp_" + ("enabled" if enabled else "disabled"), "dashboard", client_ip(request))
        return ok({"legacy_machine_token_mcp": enabled})

    async def rotate_machine(request: Request) -> Response:
        ctx.auth.rotate_machine_token()
        ctx.audit.record("auth.machine_token_rotate", "ok", target="dashboard")
        return ok({"rotated": True, "note": "Retrieve the new value locally with `loopback token`. Clients still using the legacy token must be updated."})

    async def rotate_signing(request: Request) -> Response:
        ctx.auth.rotate_signing_key()
        ctx.audit.record("auth.signing_key_rotate", "ok", target="dashboard")
        return ok({"rotated": True, "note": "All OAuth access tokens are now invalid; clients refresh automatically."})

    async def sessions_revoke_all(request: Request) -> Response:
        count = ctx.auth.destroy_all_sessions()
        ctx.audit.record("auth.sessions_revoke_all", "ok", target="dashboard", detail={"count": count})
        response = ok({"revoked": count})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # -------------------------------------------------------------------- tools
    async def tools(request: Request) -> Response:
        return ok(ctx.tool_catalog() if ctx.tool_catalog else [])

    # ------------------------------------------------------------- jobs & audit
    async def jobs(request: Request) -> Response:
        return ok({"jobs": ctx.jobs.list() if ctx.jobs else [], "terminals": ctx.terminals.list() if ctx.terminals else [],
                   "terminals_supported": bool(ctx.terminals and ctx.terminals.available())})

    async def job_stop(request: Request) -> Response:
        try:
            ctx.policy.check_process_control()
            result = ctx.jobs.stop(request.path_params["job_id"])
        except KeyError:
            return bad_request("job not found", status=404)
        except PolicyError as exc:
            return bad_request(str(exc), status=403)
        ctx.audit.record("job.stop", "ok", target=request.path_params["job_id"])
        return ok(result)

    async def terminal_close(request: Request) -> Response:
        try:
            ctx.policy.check_process_control()
            result = ctx.terminals.close(request.path_params["terminal_id"])
        except KeyError:
            return bad_request("terminal not found", status=404)
        except PolicyError as exc:
            return bad_request(str(exc), status=403)
        ctx.audit.record("terminal.close", "ok", target=request.path_params["terminal_id"])
        return ok(result)

    async def audit(request: Request) -> Response:
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            limit = 100
        return ok({"events": ctx.audit.recent(limit), "stats": ctx.audit.stats()})

    return [
        Route("/api/admin/overview", overview, methods=["GET"]),
        Route("/api/admin/identity", identity_view, methods=["GET"]),
        Route("/api/admin/config", config_get, methods=["GET"]),
        Route("/api/admin/config", config_put, methods=["PUT", "POST"]),
        Route("/api/admin/config/validate", config_validate, methods=["POST"]),
        Route("/api/admin/config/rollback", config_rollback, methods=["POST"]),
        Route("/api/admin/policy", policy_get, methods=["GET"]),
        Route("/api/admin/policy", policy_put, methods=["PUT", "POST"]),
        Route("/api/admin/policy/profiles", policy_profiles, methods=["GET"]),
        Route("/api/admin/policy/tools", policy_tools, methods=["POST", "PUT"]),
        Route("/api/admin/permissions", permissions_get, methods=["GET"]),
        Route("/api/admin/permissions/validate-path", validate_path, methods=["POST"]),
        Route("/api/admin/permissions/{target}", permissions_put, methods=["PUT"]),
        Route("/api/admin/permissions/{target}", permissions_delete, methods=["DELETE"]),
        Route("/api/admin/sandbox/self-test", sandbox_self_test, methods=["POST"]),
        Route("/api/admin/approvals", approvals_get, methods=["GET"]),
        Route("/api/admin/approvals/{approval_id}/approve", approve, methods=["POST"]),
        Route("/api/admin/approvals/{approval_id}/deny", deny, methods=["POST"]),
        Route("/api/admin/auth", auth_get, methods=["GET"]),
        Route("/api/admin/auth/tokens", token_create, methods=["POST"]),
        Route("/api/admin/auth/tokens/{token_id}", token_delete, methods=["DELETE"]),
        Route("/api/admin/auth/clients/{client_id:path}", client_delete, methods=["DELETE"]),
        Route("/api/admin/auth/grants/{family_id}", grant_delete, methods=["DELETE"]),
        Route("/api/admin/auth/password", password, methods=["POST"]),
        Route("/api/admin/auth/legacy-token", legacy_toggle, methods=["POST"]),
        Route("/api/admin/auth/rotate-machine-token", rotate_machine, methods=["POST"]),
        Route("/api/admin/auth/rotate-signing-key", rotate_signing, methods=["POST"]),
        Route("/api/admin/auth/sessions/revoke-all", sessions_revoke_all, methods=["POST"]),
        Route("/api/admin/tools", tools, methods=["GET"]),
        Route("/api/admin/jobs", jobs, methods=["GET"]),
        Route("/api/admin/jobs/{job_id}/stop", job_stop, methods=["POST"]),
        Route("/api/admin/terminals/{terminal_id}/close", terminal_close, methods=["POST"]),
        Route("/api/admin/audit", audit, methods=["GET"]),
    ]
