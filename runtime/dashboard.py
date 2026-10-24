"""Loopback admin dashboard: HTML shell, static assets and routes.

The dashboard is a small vanilla-JS single page application. Python only
serves an HTML shell (``render_dashboard``) and the files in ``runtime/static``;
all data flows through the JSON endpoints documented in ``docs/ADMIN-API.md``.

Security properties:

* The shell is only served to requests with a valid dashboard session;
  everyone else is redirected (303) to ``/admin/login?next=/admin``.
* A strict Content-Security-Policy forbids inline script and inline style, so
  ``static/index.html`` contains neither; behaviour lives in ``app.js`` and
  ``topology.js``, presentation in ``app.css``.
* Static files are resolved strictly inside ``runtime/static``; traversal,
  hidden files and unknown extensions are refused with 404.
* No secrets are embedded in the shell: only the (HTML-escaped) public host.

The module has no import-time side effects; the static cache fills lazily.
"""
from __future__ import annotations

import hashlib
import html
import threading
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Match, Route

STATIC_DIR = Path(__file__).resolve().parent / "static"

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")

SHELL_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
}

# First path segments under /admin/ that belong to other modules (auth_http) or
# to the static handler; the hash-app catch-all must never shadow them.
RESERVED_SEGMENTS = {"login", "logout", "enroll", "static", "api"}

LOGIN_PATH = "/admin/login?next=/admin"
MAX_STATIC_BYTES = 2 * 1024 * 1024

_cache_lock = threading.Lock()
# path -> (mtime_ns, size, body, etag); bounded by the number of files in STATIC_DIR.
_static_cache: dict[str, tuple[int, int, bytes, str]] = {}

_FALLBACK_SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Loopback</title>
<link rel="stylesheet" href="/admin/static/app.css"></head><body data-host="{{HOST}}">
<main id="main"><h1>LOOPBACK CONTROL PLANE</h1><p>Dashboard assets are missing.</p>
<h2>Recent MCP activity</h2><h2>Persistent terminals</h2><h2>Nodes</h2></main></body></html>"""


# --------------------------------------------------------------------------- static files
def _safe_static_path(rel: str) -> Path | None:
    """Return the file under STATIC_DIR for *rel*, or None when it is not servable.

    Refuses absolute paths, ``..`` segments, backslashes, NUL bytes, hidden
    files, unknown extensions and anything that resolves (via symlinks)
    outside STATIC_DIR.
    """
    if not rel or len(rel) > 200 or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in {"", ".", ".."} or p.startswith(".") for p in parts):
        return None
    candidate = STATIC_DIR.joinpath(*parts)
    if candidate.suffix.lower() not in STATIC_TYPES:
        return None
    try:
        resolved = candidate.resolve(strict=True)
        base = STATIC_DIR.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return None
    return resolved


def _load_static(path: Path) -> tuple[bytes, str] | None:
    """Read a static file with an mtime/size keyed cache; return (body, etag)."""
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size > MAX_STATIC_BYTES:
        return None
    key = str(path)
    with _cache_lock:
        hit = _static_cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2], hit[3]
    try:
        body = path.read_bytes()
    except OSError:
        return None
    etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
    with _cache_lock:
        _static_cache[key] = (st.st_mtime_ns, st.st_size, body, etag)
    return body, etag


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    tags = [t.strip().removeprefix("W/") for t in header.split(",")]
    return etag in tags


# --------------------------------------------------------------------------- HTML
def render_dashboard(host: str) -> str:
    """Return the dashboard shell HTML for *host* (escaped).

    The shell is ``static/index.html`` with ``{{HOST}}`` substituted. It holds
    the navigation and the empty view container; ``app.js`` renders views.
    """
    safe_host = html.escape(str(host or ""), quote=True)
    try:
        template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        template = _FALLBACK_SHELL
    return template.replace("{{HOST}}", safe_host)


def render_login(host: str, error: str = "") -> str:
    """Backwards-compatible login page; delegates to ``auth_http.render_login``."""
    try:
        from auth_http import render_login as _auth_login  # lazy: avoids import cycles
        page = _auth_login(host, error)
    except Exception:  # pragma: no cover - defensive fallback if auth_http is unavailable
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Loopback · Sign in</title></head><body>'
                f'<div class="brand">LOOPBACK</div><h1>Sign in</h1>{err}'
                f'<form method="post" action="/admin/login"><input type="password" name="credential" required>'
                f'<button>Open dashboard</button></form><p>Host: {html.escape(host)}</p></body></html>')
    if "LOOPBACK CONTROL PLANE" not in page:
        page = page.replace('<div class="brand">LOOPBACK</div>', '<div class="brand">LOOPBACK CONTROL PLANE</div>', 1)
    if "LOOPBACK CONTROL PLANE" not in page:
        page = page.replace("<body>", '<body><div class="brand">LOOPBACK CONTROL PLANE</div>', 1)
    return page


# --------------------------------------------------------------------------- routing
class _ShellRoute(Route):
    """``/admin/{path:path}`` catch-all for the hash app that never shadows
    login/logout/enroll/static/api, whatever order the server mounts routes in."""

    def matches(self, scope: Any) -> tuple[Match, dict[str, Any]]:
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path.startswith("/admin/"):
                first = path[len("/admin/"):].split("/", 1)[0]
                if first in RESERVED_SEGMENTS:
                    return Match.NONE, {}
        return super().matches(scope)


def routes(ctx: Any) -> list[Route]:
    """Dashboard routes: the session-gated shell and the static assets."""
    from auth_http import session_from_request  # lazy import keeps this module importable standalone

    async def shell(request: Request) -> Response:
        try:
            session = session_from_request(ctx, request)
        except Exception:
            session = None
        if not session:
            return RedirectResponse(LOGIN_PATH, status_code=303, headers={"Cache-Control": "no-store"})
        try:
            host = ctx.config.primary_host()
        except Exception:
            host = getattr(ctx.identity, "hostname", "") or ""
        body = render_dashboard(host)
        if request.method == "HEAD":
            return Response(status_code=200, media_type="text/html", headers=SHELL_HEADERS)
        return HTMLResponse(body, headers=SHELL_HEADERS)

    async def static(request: Request) -> Response:
        rel = str(request.path_params.get("path", ""))
        path = _safe_static_path(rel)
        if path is None:
            return Response("not found", status_code=404, media_type="text/plain",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        loaded = _load_static(path)
        if loaded is None:
            return Response("not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"})
        body, etag = loaded
        headers = {
            "Cache-Control": "public, max-age=3600",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": CSP,
        }
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        media = STATIC_TYPES[path.suffix.lower()]
        if request.method == "HEAD":
            return Response(status_code=200, media_type=media, headers={**headers, "Content-Length": str(len(body))})
        return Response(body, media_type=media, headers=headers)

    return [
        Route("/admin", shell, methods=["GET", "HEAD"]),
        Route("/admin/", shell, methods=["GET", "HEAD"]),
        Route("/admin/static/{path:path}", static, methods=["GET", "HEAD"]),
        _ShellRoute("/admin/{path:path}", shell, methods=["GET", "HEAD"]),
    ]


__all__ = ["routes", "render_dashboard", "render_login", "CSP", "STATIC_DIR"]
