"""HTTP surface for authentication: OAuth 2.1 AS endpoints, RFC 9728 metadata,
dashboard login/enrollment and session helpers.

Credentials are only ever accepted in POST bodies or headers, never in query
strings. Error responses before client/redirect validation are returned
directly (never redirected) per OAuth 2.1 §4.1.2.1.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import socket
import urllib.parse
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from auth import (
    DEFAULT_CLIENT_SCOPES,
    SCOPES,
    AuthError,
    b64url,
    normalize_scopes,
    redirect_uri_matches,
    scope_string,
)
from context import AppContext

SESSION_COOKIE = "loopback_admin"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


# --------------------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd and request.client and request.client.host in {"127.0.0.1", "::1"}:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def request_base(ctx: AppContext, request: Request) -> str:
    """Pick the issuer/base URL matching the Host header, falling back to primary."""
    host = (request.headers.get("host") or "").split(",")[0].strip().lower()
    for base in ctx.auth.base_urls:
        parsed = urllib.parse.urlparse(base)
        if parsed.netloc == host or parsed.hostname == host.split(":")[0] and parsed.port is None and ":" not in host:
            return base
    return ctx.auth.base_urls[0]


def is_secure_request(ctx: AppContext, request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


def session_from_request(ctx: AppContext, request: Request) -> dict[str, Any] | None:
    return ctx.auth.get_session(request.cookies.get(SESSION_COOKIE))


def csrf_ok(session: dict[str, Any] | None, request: Request, supplied: str | None = None) -> bool:
    if not session:
        return False
    token = supplied if supplied is not None else request.headers.get("x-loopback-csrf", "")
    if not token or token != session["csrf"]:
        return False
    site = request.headers.get("sec-fetch-site")
    if site and site not in {"same-origin", "none"}:
        return False
    return True


def www_authenticate(ctx: AppContext, request: Request, *, error: str | None = None, scope: str | None = None,
                     description: str | None = None) -> str:
    base = request_base(ctx, request)
    parts = ['realm="loopback"', f'resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"']
    if error:
        parts.append(f'error="{error}"')
    if scope:
        parts.append(f'scope="{scope}"')
    if description:
        parts.append(f'error_description="{description}"')
    return "Bearer " + ", ".join(parts)


def unauthorized(ctx: AppContext, request: Request, *, error: str | None = None, description: str | None = None,
                 scope: str = "loopback:read") -> JSONResponse:
    return JSONResponse({"error": error or "invalid_token", "error_description": description or "authentication required"},
                        status_code=401, headers={"WWW-Authenticate": www_authenticate(ctx, request, error=error, scope=scope, description=description), **NO_STORE})


def forbidden_scope(ctx: AppContext, request: Request, required: str, description: str) -> JSONResponse:
    return JSONResponse({"error": "insufficient_scope", "error_description": description, "scope": required}, status_code=403,
                        headers={"WWW-Authenticate": www_authenticate(ctx, request, error="insufficient_scope", scope=required, description=description), **NO_STORE})


def oauth_error(error: str, description: str = "", status: int = 400) -> JSONResponse:
    body: dict[str, Any] = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status, headers=NO_STORE)


# --------------------------------------------------------------------------- HTML
_STYLE = """
:root{--bg:#0b0e12;--panel:#12171d;--line:#263038;--text:#f2f5f7;--muted:#8d9ba8;--accent:#ffd54a;--accent-ink:#141414;--cyan:#6ee7ff;--red:#ff7b7b;--ok:#7cf0a9}
@media(prefers-color-scheme:light){:root{--bg:#f3f5f7;--panel:#ffffff;--line:#d7dde3;--text:#15202b;--muted:#5b6b7a;--accent:#d9a400;--accent-ink:#fff}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
.card{width:min(520px,100%);background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:28px;box-shadow:0 20px 60px #0005}
.brand{color:var(--accent);letter-spacing:.16em;font-size:12px;font-weight:700}h1{font-size:22px;margin:12px 0 6px}p{color:var(--muted);margin:6px 0 14px}
label{display:block;font-size:13px;margin:12px 0 6px}input{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--text);font:inherit}
input:focus{outline:2px solid var(--accent);outline-offset:1px}button{width:100%;margin-top:16px;padding:12px;border:0;border-radius:10px;background:var(--accent);color:var(--accent-ink);font:700 14px inherit;cursor:pointer}
.err{padding:10px 12px;border:1px solid #7a3030;background:#3a1a1a40;border-radius:9px;color:var(--red);margin:12px 0}.meta{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;padding:12px;border:1px solid var(--line);border-radius:10px;font-size:13px;margin:14px 0}
.meta b{color:var(--cyan);font-weight:600}ul.scopes{margin:0;padding:0 0 0 18px;font-size:13px;color:var(--muted)}ul.scopes code{color:var(--text)}
.small{font-size:12px;color:var(--muted);margin-top:16px}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title><style>{_STYLE}</style></head><body><main class="card">{body}</main></body></html>"""


def render_login(host: str, error: str = "", next_path: str = "/admin", password_set: bool = True) -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    hint = ("Sign in with your dashboard password." if password_set
            else "No dashboard password is set yet. Run <code>loopback auth enroll</code> on this machine to open the one-time setup link, or sign in with the machine token for recovery.")
    return _page("Loopback · Sign in", f"""<div class="brand">LOOPBACK</div><h1>Sign in</h1><p>{hint}</p>{err}
<form method="post" action="/admin/login" autocomplete="off"><input type="hidden" name="next" value="{html.escape(next_path, quote=True)}">
<label for="credential">Password or machine token</label><input id="credential" type="password" name="credential" autocomplete="current-password" autofocus required>
<button>Open dashboard</button></form><div class="small">Host: {html.escape(host)}</div>""")


def render_enroll(error: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return _page("Loopback · First-run setup", f"""<div class="brand">LOOPBACK</div><h1>Create the dashboard password</h1>
<p>This one-time link came from <code>loopback auth enroll</code>. Choose a password (10+ characters). The machine token remains a local recovery credential.</p>{err}
<form method="post" action="/admin/enroll" id="f" autocomplete="off"><input type="hidden" name="code" id="code">
<label for="p1">New password</label><input id="p1" type="password" name="password" minlength="10" autocomplete="new-password" required autofocus>
<label for="p2">Repeat password</label><input id="p2" type="password" name="password2" minlength="10" autocomplete="new-password" required>
<button>Save and open dashboard</button></form>
<script>document.getElementById('code').value=(location.hash||'').replace(/^#code=/,'');if(!document.getElementById('code').value){{document.getElementById('f').insertAdjacentHTML('afterbegin','<div class="err">The enrollment code is missing from the link. Run loopback auth enroll again.</div>')}}</script>""")


def render_consent(*, client_name: str, redirect_uri: str, scopes: list[str], host: str, request_id: str, csrf: str,
                   need_credential: bool, error: str = "") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    scope_items = "".join(f"<li><code>{html.escape(s)}</code> — {html.escape(SCOPES.get(s, ''))}</li>" for s in scopes)
    cred = ("" if not need_credential else
            '<label for="credential">Dashboard password or machine token</label><input id="credential" type="password" name="credential" autocomplete="current-password" required>')
    return _page("Loopback · Authorize", f"""<div class="brand">LOOPBACK · AUTHORIZATION</div><h1>Authorize {html.escape(client_name)}</h1>
<p>The client will receive a short-lived OAuth credential scoped to this Loopback instance. Your password and machine token are never shared with it.</p>{err}
<div class="meta"><b>Client</b><span>{html.escape(client_name)}</span><b>Redirect</b><span>{html.escape(redirect_uri)}</span><b>Resource</b><span>{html.escape(host)}</span></div>
<ul class="scopes">{scope_items}</ul>
<form method="post" action="/oauth/authorize" autocomplete="off"><input type="hidden" name="request_id" value="{html.escape(request_id, quote=True)}"><input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">{cred}
<button name="decision" value="approve">Approve</button><button name="decision" value="deny" style="background:transparent;color:var(--muted);border:1px solid var(--line)">Deny</button></form>""")


def render_message(title: str, text: str) -> str:
    return _page(f"Loopback · {title}", f'<div class="brand">LOOPBACK</div><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>')


# --------------------------------------------------------------------------- metadata
def _prm(ctx: AppContext, base: str) -> dict[str, Any]:
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": DEFAULT_CLIENT_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_name": "Loopback MCP",
        "resource_documentation": "https://github.com/p5n-n3t/loopback/blob/main/docs/AUTHENTICATION.md",
    }


def _as_metadata(ctx: AppContext, base: str) -> dict[str, Any]:
    auth_cfg = ctx.config.data.get("auth", {})
    meta = {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPES),
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": bool(auth_cfg.get("allow_client_id_metadata_documents", True)),
        "service_documentation": "https://github.com/p5n-n3t/loopback/blob/main/docs/AUTHENTICATION.md",
    }
    if auth_cfg.get("allow_dynamic_registration", True):
        meta["registration_endpoint"] = f"{base}/oauth/register"
    return meta


async def _fetch_client_metadata_document(ctx: AppContext, client_id: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(client_id)
    if parsed.scheme != "https" or not parsed.hostname or parsed.fragment or parsed.username:
        raise AuthError("invalid_client", "client_id URL must be https without fragment or userinfo")
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise AuthError("invalid_client", "client_id host does not resolve") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            raise AuthError("invalid_client", "client_id host resolves to a non-public address")
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, headers={"Accept": "application/json"}) as client:
        response = await client.get(client_id)
    if response.status_code != 200 or len(response.content) > 65536:
        raise AuthError("invalid_client", "client metadata document unavailable")
    try:
        doc = response.json()
    except json.JSONDecodeError as exc:
        raise AuthError("invalid_client", "client metadata document is not JSON") from exc
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise AuthError("invalid_client", "client metadata document client_id mismatch")
    return doc


async def _resolve_client(ctx: AppContext, client_id: str) -> dict[str, Any] | None:
    client = ctx.auth.get_client(client_id)
    if client:
        if client.get("kind") == "cimd" and client["created_at"] < __import__("time").time() - 3600:
            client = None  # refresh cached document hourly
        else:
            return client
    if client_id.startswith("https://") and ctx.config.data.get("auth", {}).get("allow_client_id_metadata_documents", True):
        doc = await _fetch_client_metadata_document(ctx, client_id)
        record = ctx.auth.register_client(doc, kind="cimd", client_id=client_id)
        return ctx.auth.get_client(record["client_id"])
    return None


# --------------------------------------------------------------------------- handlers
def routes(ctx: AppContext) -> list[Route]:
    async def prm(request: Request) -> Response:
        return JSONResponse(_prm(ctx, request_base(ctx, request)), headers={"Cache-Control": "public, max-age=300"})

    async def as_metadata(request: Request) -> Response:
        return JSONResponse(_as_metadata(ctx, request_base(ctx, request)), headers={"Cache-Control": "public, max-age=300"})

    async def register(request: Request) -> Response:
        if not ctx.config.data.get("auth", {}).get("allow_dynamic_registration", True):
            return oauth_error("invalid_request", "dynamic client registration is disabled; use a client ID metadata document", 403)
        if not ctx.auth.limiter.allow("register", client_ip(request), 20, 3600):
            return oauth_error("invalid_request", "too many registrations from this address", 429)
        try:
            body = await request.json()
        except Exception:
            return oauth_error("invalid_client_metadata", "body must be JSON")
        if not isinstance(body, dict):
            return oauth_error("invalid_client_metadata", "body must be a JSON object")
        try:
            record = ctx.auth.register_client(body, kind="dcr")
        except AuthError as exc:
            return oauth_error(exc.error, exc.description, exc.status)
        return JSONResponse(record, status_code=201, headers=NO_STORE)

    async def authorize_get(request: Request) -> Response:
        q = request.query_params
        client_id = q.get("client_id", "")
        redirect_uri = q.get("redirect_uri", "")
        if not ctx.auth.limiter.allow("authorize", client_ip(request), 60, 600):
            return oauth_error("invalid_request", "too many authorization requests", 429)
        try:
            client = await _resolve_client(ctx, client_id) if client_id else None
        except AuthError as exc:
            return oauth_error(exc.error, exc.description, exc.status)
        if not client:
            return oauth_error("invalid_client", "unknown client_id")
        if not redirect_uri or not redirect_uri_matches(client["redirect_uris"], redirect_uri):
            return oauth_error("invalid_request", "redirect_uri is not registered for this client")

        base = request_base(ctx, request)

        def redirect_err(error: str, description: str) -> Response:
            params = {"error": error, "error_description": description, "iss": base}
            if q.get("state"):
                params["state"] = q["state"]
            sep = "&" if "?" in redirect_uri else "?"
            return RedirectResponse(redirect_uri + sep + urllib.parse.urlencode(params), status_code=302, headers=NO_STORE)

        if q.get("response_type", "code") != "code":
            return redirect_err("unsupported_response_type", "only response_type=code is supported")
        if q.get("code_challenge_method", "") != "S256" or len(q.get("code_challenge", "")) < 43:
            return redirect_err("invalid_request", "PKCE with code_challenge_method=S256 is required")
        try:
            resource = ctx.auth.canonical_resource(q.get("resource"))
        except AuthError as exc:
            return redirect_err(exc.error, exc.description)
        try:
            scopes = normalize_scopes(q.get("scope") or DEFAULT_CLIENT_SCOPES)
        except AuthError as exc:
            return redirect_err("invalid_scope", exc.description)
        if "loopback:admin" in scopes:
            return redirect_err("invalid_scope", "loopback:admin cannot be granted to OAuth clients; use the dashboard")
        params = {
            "client_id": client["client_id"], "redirect_uri": redirect_uri, "state": q.get("state", ""),
            "code_challenge": q["code_challenge"], "resource": resource, "scope": scope_string(scopes), "base": base,
        }
        rid, csrf = ctx.auth.stash_authz_request(params)
        session = session_from_request(ctx, request)
        need_cred = session is None or bool(ctx.config.data.get("auth", {}).get("require_password_for_consent", True))
        return HTMLResponse(render_consent(client_name=client["client_name"], redirect_uri=redirect_uri, scopes=sorted(scopes),
                                           host=resource, request_id=rid, csrf=csrf, need_credential=need_cred), headers=NO_STORE)

    async def authorize_post(request: Request) -> Response:
        form = await request.form()
        rid = str(form.get("request_id") or "")
        csrf = str(form.get("csrf") or "")
        decision = str(form.get("decision") or "deny")
        params = ctx.auth.pop_authz_request(rid, csrf)
        if not params:
            return HTMLResponse(render_message("Request expired", "The authorization request is no longer valid. Start again from your MCP client."), status_code=400, headers=NO_STORE)
        redirect_uri = params["redirect_uri"]
        base = params["base"]

        def redirect(payload: dict[str, str]) -> Response:
            payload["iss"] = base
            if params.get("state"):
                payload["state"] = params["state"]
            sep = "&" if "?" in redirect_uri else "?"
            return RedirectResponse(redirect_uri + sep + urllib.parse.urlencode(payload), status_code=302, headers=NO_STORE)

        if decision != "approve":
            ctx.auth.record_event("oauth_consent_denied", params["client_id"], client_ip(request))
            return redirect({"error": "access_denied", "error_description": "the user denied the request"})
        ip = client_ip(request)
        if ctx.auth.limiter.locked("login", ip):
            return HTMLResponse(render_message("Locked", "Too many failed attempts. Try again later."), status_code=429, headers=NO_STORE)
        session = session_from_request(ctx, request)
        credential = str(form.get("credential") or "")
        need_cred = session is None or bool(ctx.config.data.get("auth", {}).get("require_password_for_consent", True))
        if need_cred:
            method = ctx.auth.verify_admin_credential(credential)
            if not method:
                locked = ctx.auth.limiter.failure("login", ip)
                ctx.auth.record_event("oauth_consent_bad_credential", params["client_id"], ip, {"locked": locked})
                client = ctx.auth.get_client(params["client_id"]) or {"client_name": params["client_id"]}
                rid2, csrf2 = ctx.auth.stash_authz_request(params)
                return HTMLResponse(render_consent(client_name=client["client_name"], redirect_uri=redirect_uri, scopes=params["scope"].split(),
                                                   host=params["resource"], request_id=rid2, csrf=csrf2, need_credential=True,
                                                   error="Invalid password or machine token."), status_code=401, headers=NO_STORE)
            ctx.auth.limiter.success("login", ip)
        code = ctx.auth.create_code(client_id=params["client_id"], redirect_uri=redirect_uri, code_challenge=params["code_challenge"],
                                    resource=params["resource"], scope=params["scope"])
        ctx.auth.record_event("oauth_code_issued", params["client_id"], ip, {"scope": params["scope"]})
        return redirect({"code": code})

    async def token(request: Request) -> Response:
        ip = client_ip(request)
        if not ctx.auth.limiter.allow("token", ip, 120, 60):
            return oauth_error("invalid_request", "rate limited", 429)
        form = await request.form()
        grant_type = str(form.get("grant_type") or "")
        client_id = str(form.get("client_id") or "")
        if not client_id:
            return oauth_error("invalid_client", "client_id is required", 401)
        client = ctx.auth.get_client(client_id)
        if not client:
            return oauth_error("invalid_client", "unknown client", 401)
        try:
            resource = ctx.auth.canonical_resource(str(form.get("resource") or "") or None)
        except AuthError as exc:
            return oauth_error(exc.error, exc.description)

        if grant_type == "authorization_code":
            code = str(form.get("code") or "")
            verifier = str(form.get("code_verifier") or "")
            redirect_uri = str(form.get("redirect_uri") or "")
            if not code or not verifier:
                return oauth_error("invalid_request", "code and code_verifier are required")
            try:
                entry = ctx.auth.consume_code(code)
            except AuthError as exc:
                ctx.auth.limiter.failure("token", ip, threshold=30)
                return oauth_error(exc.error, exc.description)
            if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri or entry["resource"] != resource:
                return oauth_error("invalid_grant", "code was issued to a different client, redirect_uri or resource")
            calc = b64url(hashlib.sha256(verifier.encode()).digest())
            if not __import__("hmac").compare_digest(calc, entry["code_challenge"]):
                ctx.auth.record_event("pkce_failure", client_id, ip)
                return oauth_error("invalid_grant", "PKCE verification failed")
            scopes = normalize_scopes(entry["scope"])
            refresh, family = ctx.auth.issue_refresh_token(client_id=client_id, resource=resource, scopes=scopes)
            access, claims = ctx.auth.issue_access_token(client_id=client_id, resource=resource, scopes=scopes, family_id=family)
            ctx.auth.touch_client(client_id)
            ctx.auth.record_event("oauth_tokens_issued", client_id, ip, {"grant": "authorization_code"})
            return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ctx.auth.access_ttl,
                                 "refresh_token": refresh, "scope": claims["scope"]}, headers=NO_STORE)

        if grant_type == "refresh_token":
            refresh = str(form.get("refresh_token") or "")
            if not refresh:
                return oauth_error("invalid_request", "refresh_token is required")
            try:
                new_refresh, scopes, family = ctx.auth.rotate_refresh_token(refresh, client_id=client_id, resource=resource)
            except AuthError as exc:
                ctx.auth.limiter.failure("token", ip, threshold=30)
                return oauth_error(exc.error, exc.description)
            requested = str(form.get("scope") or "")
            if requested:
                try:
                    narrowed = normalize_scopes(requested)
                except AuthError as exc:
                    return oauth_error("invalid_scope", exc.description)
                if not narrowed <= scopes:
                    return oauth_error("invalid_scope", "refresh cannot widen the granted scope")
                scopes = narrowed
            access, claims = ctx.auth.issue_access_token(client_id=client_id, resource=resource, scopes=scopes, family_id=family)
            ctx.auth.touch_client(client_id)
            return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ctx.auth.access_ttl,
                                 "refresh_token": new_refresh, "scope": claims["scope"]}, headers=NO_STORE)

        return oauth_error("unsupported_grant_type", "use authorization_code or refresh_token")

    async def revoke(request: Request) -> Response:
        form = await request.form()
        value = str(form.get("token") or "")
        hint = str(form.get("token_type_hint") or "") or None
        if value:
            ctx.auth.revoke_token(value, hint)
            ctx.auth.record_event("token_revoked", str(form.get("client_id") or "client"), client_ip(request))
        return Response(status_code=200, headers=NO_STORE)

    # ---------------------------------------------------------------- dashboard auth
    def _set_cookie(response: Response, request: Request, sid: str, max_age: int) -> None:
        response.set_cookie(SESSION_COOKIE, sid, max_age=max_age, httponly=True, secure=is_secure_request(ctx, request),
                            samesite="strict", path="/")

    def _safe_next(value: str) -> str:
        return value if value.startswith("/admin") and "//" not in value else "/admin"

    async def login(request: Request) -> Response:
        host = ctx.config.primary_host()
        if request.method == "GET":
            return HTMLResponse(render_login(host, next_path=_safe_next(request.query_params.get("next", "/admin")),
                                             password_set=ctx.auth.has_password()), headers=NO_STORE)
        ip = client_ip(request)
        if ctx.auth.limiter.locked("login", ip):
            return HTMLResponse(render_login(host, "Too many failed attempts. Try again in 15 minutes.", password_set=ctx.auth.has_password()), status_code=429, headers=NO_STORE)
        form = await request.form()
        credential = str(form.get("credential") or form.get("token") or "")
        method = ctx.auth.verify_admin_credential(credential)
        if not method:
            locked = ctx.auth.limiter.failure("login", ip)
            ctx.auth.record_event("login_failed", None, ip, {"locked": locked})
            return HTMLResponse(render_login(host, "Invalid credential.", password_set=ctx.auth.has_password()), status_code=401, headers=NO_STORE)
        ctx.auth.limiter.success("login", ip)
        sid, _csrf = ctx.auth.create_session(user_agent=request.headers.get("user-agent", ""), ip=ip)
        ctx.auth.record_event("login", method, ip)
        response = RedirectResponse(_safe_next(str(form.get("next") or "/admin")), status_code=303, headers=NO_STORE)
        _set_cookie(response, request, sid, ctx.auth.session_ttl)
        return response

    async def logout(request: Request) -> Response:
        ctx.auth.destroy_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/admin/login", status_code=303, headers=NO_STORE)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    async def enroll(request: Request) -> Response:
        if request.method == "GET":
            return HTMLResponse(render_enroll(), headers=NO_STORE)
        ip = client_ip(request)
        if ctx.auth.limiter.locked("login", ip) or not ctx.auth.limiter.allow("enroll", ip, 10, 600):
            return HTMLResponse(render_enroll("Too many attempts."), status_code=429, headers=NO_STORE)
        form = await request.form()
        code = str(form.get("code") or "")
        password = str(form.get("password") or "")
        if password != str(form.get("password2") or ""):
            return HTMLResponse(render_enroll("Passwords do not match."), status_code=400, headers=NO_STORE)
        if not code or not ctx.auth.consume_enrollment(code):
            ctx.auth.limiter.failure("login", ip)
            return HTMLResponse(render_enroll("The enrollment code is invalid or expired. Run loopback auth enroll again."), status_code=400, headers=NO_STORE)
        try:
            ctx.auth.set_password(password)
        except AuthError as exc:
            return HTMLResponse(render_enroll(exc.description), status_code=400, headers=NO_STORE)
        sid, _csrf = ctx.auth.create_session(user_agent=request.headers.get("user-agent", ""), ip=ip)
        ctx.auth.record_event("enrolled", "password", ip)
        response = RedirectResponse("/admin#/welcome", status_code=303, headers=NO_STORE)
        _set_cookie(response, request, sid, ctx.auth.session_ttl)
        return response

    async def session_info(request: Request) -> Response:
        session = session_from_request(ctx, request)
        if not session:
            return JSONResponse({"authenticated": False}, status_code=401, headers=NO_STORE)
        return JSONResponse({"authenticated": True, "csrf": session["csrf"], "expires_at": session["expires_at"],
                             "password_set": ctx.auth.has_password()}, headers=NO_STORE)

    return [
        Route("/.well-known/oauth-protected-resource", prm, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", prm, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", as_metadata, methods=["GET"]),
        Route("/.well-known/openid-configuration", as_metadata, methods=["GET"]),
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize_get, methods=["GET"]),
        Route("/oauth/authorize", authorize_post, methods=["POST"]),
        Route("/oauth/token", token, methods=["POST"]),
        Route("/oauth/revoke", revoke, methods=["POST"]),
        Route("/admin/login", login, methods=["GET", "POST"]),
        Route("/admin/logout", logout, methods=["GET", "POST"]),
        Route("/admin/enroll", enroll, methods=["GET", "POST"]),
        Route("/api/admin/session", session_info, methods=["GET"]),
    ]


__all__ = ["routes", "session_from_request", "csrf_ok", "unauthorized", "forbidden_scope", "www_authenticate",
           "client_ip", "SESSION_COOKIE", "NO_STORE", "render_login"]
