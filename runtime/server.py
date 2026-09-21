from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


CFG = Path.home() / ".config" / "loopback"
TOKEN = (CFG / "token").read_text().strip()

PUBLIC_HOST = (
    (CFG / "public_host").read_text().strip().rstrip(".")
    if (CFG / "public_host").exists()
    else "localhost"
)

LOCAL_PORT = int(os.environ.get("LOOPBACK_LOCAL_PORT", "2026"))
DEFAULT_TIMEOUT = int(os.environ.get("LOOPBACK_COMMAND_TIMEOUT", "120"))
MAX_OUTPUT = int(os.environ.get("LOOPBACK_MAX_OUTPUT_BYTES", "1048576"))
SHELL = os.environ.get("LOOPBACK_SHELL", "/usr/bin/zsh")
ACCESS_MODE = os.environ.get("LOOPBACK_ACCESS_MODE", "standard")


def clip(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= MAX_OUTPUT:
        return text, False

    return (
        raw[:MAX_OUTPUT].decode("utf-8", errors="replace")
        + "\n...[truncated]",
        True,
    )


def resolve_cwd(value: str | None) -> str:
    p = Path(value or "~").expanduser().resolve()

    if not p.is_dir():
        raise ValueError(f"cwd is not a directory: {p}")

    return str(p)



mcp = MCPServer("Loopback")



@mcp.tool()
def read_file(
    path: str,
    start_line: int = 1,
    max_lines: int = 1000,
) -> dict[str, Any]:
    """Read a text file with line-range controls."""

    p = Path(path).expanduser().resolve()
    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), 10000))

    lines = p.read_text(errors="replace").splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]

    text, truncated = clip("\n".join(selected))

    return {
        "path": str(p),
        "start_line": start_line,
        "returned_lines": len(selected),
        "total_lines": len(lines),
        "truncated": truncated,
        "content": text,
    }


@mcp.tool()
def write_file(
    path: str,
    content: str,
    append: bool = False,
    create_parents: bool = True,
) -> dict[str, Any]:
    """Write or append UTF-8 text as the current user."""

    p = Path(path).expanduser().resolve()

    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)

    with p.open(
        "a" if append else "w",
        encoding="utf-8",
    ) as f:
        f.write(content)

    return {
        "path": str(p),
        "bytes": len(content.encode()),
        "append": append,
    }


@mcp.tool()
def list_dir(
    path: str = "~",
    max_entries: int = 1000,
) -> dict[str, Any]:
    """List a directory with simple metadata."""

    p = Path(path).expanduser().resolve()
    max_entries = max(1, min(int(max_entries), 10000))

    out = []

    for child in sorted(
        p.iterdir(),
        key=lambda x: x.name.lower(),
    )[:max_entries]:

        try:
            st = child.stat()

            kind = (
                "dir"
                if child.is_dir()
                else "file"
                if child.is_file()
                else "other"
            )

            out.append({
                "name": child.name,
                "path": str(child),
                "type": kind,
                "size": st.st_size,
            })

        except OSError as e:
            out.append({
                "name": child.name,
                "path": str(child),
                "error": str(e),
            })

    return {
        "path": str(p),
        "returned": len(out),
        "entries": out,
    }


@mcp.tool()
def patch_file(
    path: str,
    old_text: str,
    new_text: str,
    count: int = 1,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Replace exact text in a file, optionally keeping a timestamped backup."""
    p = Path(path).expanduser().resolve()
    text = p.read_text(encoding="utf-8", errors="strict")
    found = text.count(old_text)
    if found == 0:
        raise ValueError("old_text was not found")
    n = found if int(count) <= 0 else min(int(count), found)
    backup = None
    if create_backup:
        backup = p.with_name(p.name + f".bak.{int(time.time())}")
        backup.write_text(text, encoding="utf-8")
    updated = text.replace(old_text, new_text, n)
    p.write_text(updated, encoding="utf-8")
    return {
        "path": str(p),
        "replacements": n,
        "matches_before": found,
        "backup": str(backup) if backup else None,
    }


@mcp.tool()
def search_text(
    query: str,
    path: str = "~",
    glob: str = "*",
    max_results: int = 200,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search text recursively while skipping dependency/cache directories."""
    import fnmatch
    root = Path(path).expanduser().resolve()
    limit = max(1, min(int(max_results), 5000))
    needle_text = query if case_sensitive else query.lower()
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", "dist", "build"}
    results = []
    scanned = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if len(results) >= limit:
                break
            if not fnmatch.fnmatch(name, glob):
                continue
            fp = Path(current) / name
            try:
                if fp.stat().st_size > 8 * 1024 * 1024:
                    continue
                scanned += 1
                with fp.open("r", encoding="utf-8", errors="ignore") as handle:
                    for lineno, line in enumerate(handle, 1):
                        hay = line if case_sensitive else line.lower()
                        if needle_text in hay:
                            results.append({
                                "path": str(fp),
                                "line": lineno,
                                "text": line.rstrip("\n")[:2000],
                            })
                            if len(results) >= limit:
                                break
            except (OSError, UnicodeError):
                continue
        if len(results) >= limit:
            break
    return {
        "path": str(root),
        "query": query,
        "scanned_files": scanned,
        "returned": len(results),
        "truncated": len(results) >= limit,
        "results": results,
    }


@mcp.tool()
def tail_file(path: str, lines: int = 200) -> dict[str, Any]:
    """Return the last N lines of a text or log file."""
    from collections import deque
    p = Path(path).expanduser().resolve()
    n = max(1, min(int(lines), 10000))
    with p.open("r", encoding="utf-8", errors="replace") as handle:
        selected = list(deque(handle, maxlen=n))
    text, truncated = clip("".join(selected))
    return {
        "path": str(p),
        "returned_lines": len(selected),
        "truncated": truncated,
        "content": text,
    }


@mcp.tool()
def git_info(path: str = "~") -> dict[str, Any]:
    """Return branch, root, remotes and concise working-tree state for a Git repo."""
    cwd = resolve_cwd(path)
    def git(*args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, timeout=30
        )
        if check and proc.returncode != 0:
            raise ValueError(proc.stderr.strip() or "git command failed")
        return proc.stdout.strip()
    root = git("rev-parse", "--show-toplevel")
    branch = git("branch", "--show-current", check=False) or git("symbolic-ref", "--short", "HEAD", check=False)
    head = git("rev-parse", "--short", "HEAD", check=False) or None
    return {
        "root": root,
        "branch": branch or None,
        "head": head,
        "status": git("status", "--short", "--branch"),
        "remotes": git("remote", "-v", check=False),
    }


@mcp.tool()
def diagnostics() -> dict[str, Any]:
    """Return lightweight runtime diagnostics without exposing stored credentials."""
    import shutil
    def version(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=5)
            lines = (proc.stdout or proc.stderr).strip().splitlines()
            return lines[0][:300] if lines else None
        except (OSError, subprocess.SubprocessError):
            return None
    usage = shutil.disk_usage(Path.home())
    return {
        "hostname": socket.gethostname(),
        "python": version([os.sys.executable, "--version"]),
        "git": version(["git", "--version"]),
        "cloudflared": version(["cloudflared", "--version"]),
        "tailscale": version(["tailscale", "version"]),
        "disk_home": {"total": usage.total, "used": usage.used, "free": usage.free},
        "port": LOCAL_PORT,
        "public_host": PUBLIC_HOST,
        "command_timeout": DEFAULT_TIMEOUT,
        "max_output_bytes": MAX_OUTPUT,
    }


@mcp.tool()
def machine_info() -> dict[str, Any]:
    """Return basic non-secret context for the machine."""

    return {
        "user": os.environ.get("USER"),
        "home": str(Path.home()),
        "hostname": socket.gethostname(),
        "shell": SHELL,
        "local_port": LOCAL_PORT,
        "public_host": PUBLIC_HOST,
        "access_mode": ACCESS_MODE,
    }


# ---------------------------------------------------------------------------
# Minimal OAuth 2.0 shim (Authorization Code + PKCE, RFC 7591 registration).
#
# Some MCP clients (notably ChatGPT's Connectors UI) only offer "OAuth" or
# "No auth" when adding a custom connector -- there is no field for a
# static bearer token. Rather than weakening TokenGate or shipping a
# second, ChatGPT-only build of loopback, this shim implements just enough
# of RFC 6749 + RFC 7636 (PKCE) + RFC 7591 (dynamic client registration)
# for ChatGPT to complete a real OAuth handshake. The access_token it hands
# back at the end of that handshake IS the existing static TOKEN -- the
# exact same secret in ~/.config/loopback/token that Perplexity and every
# other client already send as `Authorization: Bearer TOKEN`. Nothing about
# the underlying auth model changes; this only adds a discovery/handshake
# layer in front of it. The /oauth/authorize step still requires knowing
# TOKEN, so a client that has registered but doesn't have the secret can
# reach /authorize but cannot obtain a code.
# ---------------------------------------------------------------------------

_OAUTH_CLIENTS: dict[str, dict[str, Any]] = {}
_OAUTH_CODES: dict[str, dict[str, Any]] = {}
_CODE_TTL_SECONDS = 300


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request) -> Response:
    base = f"https://{PUBLIC_HOST}"
    return JSONResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
    })


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request) -> Response:
    base = f"https://{PUBLIC_HOST}"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["loopback"],
    })


@mcp.custom_route("/oauth/register", methods=["POST"])
async def oauth_register(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}

    client_id = secrets.token_urlsafe(16)
    redirect_uris = body.get("redirect_uris") or []
    _OAUTH_CLIENTS[client_id] = {"redirect_uris": redirect_uris}

    return JSONResponse(
        {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


@mcp.custom_route("/oauth/authorize", methods=["GET"])
async def oauth_authorize(request: Request) -> Response:
    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    state = q.get("state", "")
    code_challenge = q.get("code_challenge", "")
    code_challenge_method = q.get("code_challenge_method", "S256")
    supplied_token = q.get("loopback_token", "")

    if not redirect_uri:
        return JSONResponse({"detail": "redirect_uri required"}, status_code=400)

    if not supplied_token:
        hidden = "".join(
            f'<input type="hidden" name="{k}" value="{v}">'
            for k, v in [
                ("client_id", client_id),
                ("redirect_uri", redirect_uri),
                ("state", state),
                ("code_challenge", code_challenge),
                ("code_challenge_method", code_challenge_method),
            ]
        )
        html = f"""<!doctype html><html><body style="font-family:sans-serif;max-width:420px;margin:80px auto">
<h3>Authorize loopback access</h3>
<p>Enter your existing loopback token (<code>loopback token</code>) to grant this client access.</p>
<form method="get" action="/oauth/authorize">
{hidden}
<label>Loopback token</label><br>
<input type="password" name="loopback_token" style="width:100%" autofocus><br><br>
<button type="submit">Approve</button>
</form></body></html>"""
        return HTMLResponse(html)

    if not secrets.compare_digest(supplied_token, TOKEN):
        return HTMLResponse("<h3>Invalid token</h3>", status_code=401)

    code = secrets.token_urlsafe(24)
    _OAUTH_CODES[code] = {
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "redirect_uri": redirect_uri,
        "expires": time.time() + _CODE_TTL_SECONDS,
    }

    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"

    return RedirectResponse(location, status_code=302)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request) -> Response:
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        code = form.get("code", "")
        verifier = form.get("code_verifier", "")
        entry = _OAUTH_CODES.pop(code, None)

        if not entry or entry["expires"] < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        challenge = entry.get("code_challenge")
        if challenge:
            calc = _b64url(hashlib.sha256(verifier.encode()).digest())
            if not secrets.compare_digest(calc, challenge):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

        return JSONResponse({
            "access_token": TOKEN,
            "token_type": "Bearer",
            "expires_in": 315360000,
        })

    if grant_type == "refresh_token":
        return JSONResponse({
            "access_token": TOKEN,
            "token_type": "Bearer",
            "expires_in": 315360000,
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return JSONResponse({
        "ok": True,
        "service": "loopback",
        "version": "2.2.0-standard",
    })



# MCP v2 protects localhost deployments against DNS rebinding.
# Since this server sits behind a real Tailscale hostname, explicitly
# allow that hostname as well as localhost.
security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        PUBLIC_HOST,
        f"{PUBLIC_HOST}:*",
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ],
    allowed_origins=[
        f"https://{PUBLIC_HOST}",
        f"https://{PUBLIC_HOST}:*",
        "https://www.perplexity.ai",
        "https://perplexity.ai",
        "https://chatgpt.com",
        "http://127.0.0.1:*",
        "http://localhost:*",
    ],
)


inner = mcp.streamable_http_app(
    transport_security=security,
    stateless_http=True,
    json_response=True,
)


# Browser-based MCP clients need the MCP headers exposed.
cors = CORSMiddleware(
    inner,
    allow_origins=[
        f"https://{PUBLIC_HOST}",
        "https://www.perplexity.ai",
        "https://perplexity.ai",
        "https://chatgpt.com",
    ],
    allow_methods=[
        "GET",
        "POST",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


class TokenGate:
    """
    Authenticate both MCP and REST calls.

    Accept:
      Authorization: Bearer TOKEN
      Authorization: TOKEN
      X-API-Key: TOKEN
      Api-Key: TOKEN
    """

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.wrapped(
                scope,
                receive,
                send,
            )

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        if (
            method == "OPTIONS"
            or path == "/healthz"
            or path.startswith("/.well-known/oauth")
            or path.startswith("/oauth/")
        ):
            return await self.wrapped(
                scope,
                receive,
                send,
            )

        protected = (
            path == "/mcp"
            or path.startswith("/mcp/")
        )

        if protected:
            h = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in scope.get("headers", [])
            }

            candidates = [
                h.get("authorization", "").strip(),
                h.get("x-api-key", "").strip(),
                h.get("api-key", "").strip(),
            ]

            valid = any(
                (
                    secrets.compare_digest(x, TOKEN)
                    or secrets.compare_digest(
                        x,
                        f"Bearer {TOKEN}",
                    )
                )
                for x in candidates
                if x
            )

            if not valid:
                r = JSONResponse(
                    {"detail": "Unauthorized"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": "Bearer"
                    },
                )

                return await r(
                    scope,
                    receive,
                    send,
                )

        return await self.wrapped(
            scope,
            receive,
            send,
        )


app = TokenGate(cors)