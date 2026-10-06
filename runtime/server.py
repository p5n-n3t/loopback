from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from audit import AuditLog
from dashboard import dashboard_html, login_html
from jobs import JobManager, TerminalManager
from metrics import host_metrics, process_info as get_process_info, process_kill as kill_process, process_list as get_process_list
from nodes import NodeRegistry
from policy import ApprovalStore, Policy


CFG = Path(os.environ.get("LOOPBACK_CONFIG_DIR", Path.home() / ".config" / "loopback"))
TOKEN_FILE = CFG / "token"
TOKEN = TOKEN_FILE.read_text().strip()

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
INGRESS = os.environ.get("LOOPBACK_INGRESS", "local")
VERSION = "3.0.0"

POLICY = Policy()
AUDIT = AuditLog()
APPROVALS = ApprovalStore()
JOBS = JobManager()
TERMINALS = TerminalManager()
NODES = NodeRegistry()

_CLIENTS: dict[str, dict[str, Any]] = {}
_ADMIN_SESSIONS: dict[str, float] = {}
_OAUTH_CLIENTS: dict[str, dict[str, Any]] = {}
_OAUTH_CODES: dict[str, dict[str, Any]] = {}
_OAUTH_ACCESS: dict[str, dict[str, Any]] = {}
_OAUTH_REFRESH: dict[str, dict[str, Any]] = {}
_CODE_TTL = 300
_ACCESS_TTL = 3600
_REFRESH_TTL = 30 * 24 * 3600


def clip(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= MAX_OUTPUT:
        return text, False
    return raw[:MAX_OUTPUT].decode("utf-8", errors="replace") + "\n...[truncated]", True


def resolve_cwd(value: str | None) -> str:
    return str(POLICY.path_allowed(value or "~"))


def _audit(tool: str, status: str = "ok", **detail: Any) -> None:
    AUDIT.write("tool", tool=tool, status=status, detail=detail)


def _command_guard(command: str, approval_id: str | None = None) -> None:
    decision = POLICY.command_decision(command)
    if decision == "deny":
        _audit("command", "denied", command=command)
        raise PermissionError("command denied by Loopback policy")
    if decision == "confirm" and not APPROVALS.consume(approval_id, command):
        pending = APPROVALS.request(command)
        _audit("command", "approval_required", command=command, approval_id=pending["id"])
        raise PermissionError(
            "Loopback operator approval required. "
            f"Approval ID: {pending['id']}. Approve it in the Loopback dashboard, then retry with approval_id."
        )


def _valid_redirect(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _prune_tokens() -> None:
    now = time.time()
    for mapping in (_OAUTH_CODES, _OAUTH_ACCESS, _OAUTH_REFRESH, _ADMIN_SESSIONS):
        for key in list(mapping):
            value = mapping[key]
            expires = value if isinstance(value, (int, float)) else value.get("expires", 0)
            if expires < now:
                mapping.pop(key, None)


def _new_oauth_pair(subject: str, resource: str) -> dict[str, Any]:
    _prune_tokens()
    access = "lb_at_" + secrets.token_urlsafe(32)
    refresh = "lb_rt_" + secrets.token_urlsafe(40)
    _OAUTH_ACCESS[access] = {"subject": subject, "resource": resource, "expires": time.time() + _ACCESS_TTL}
    _OAUTH_REFRESH[refresh] = {"subject": subject, "resource": resource, "expires": time.time() + _REFRESH_TTL}
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TTL,
        "refresh_token": refresh,
        "scope": "loopback",
    }


def _bearer_valid(value: str) -> bool:
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if secrets.compare_digest(value, TOKEN):
        return True
    _prune_tokens()
    entry = _OAUTH_ACCESS.get(value)
    return bool(entry and entry["expires"] >= time.time())


def _cookie(request: Request, name: str) -> str | None:
    return request.cookies.get(name)


def _admin_ok(request: Request) -> bool:
    _prune_tokens()
    sid = _cookie(request, "loopback_admin")
    return bool(sid and _ADMIN_SESSIONS.get(sid, 0) >= time.time())


def _admin_required(request: Request) -> Response | None:
    if _admin_ok(request):
        return None
    return JSONResponse({"detail": "admin authentication required"}, status_code=401)


def _public_base() -> str:
    if PUBLIC_HOST in {"localhost", "127.0.0.1"}:
        return f"http://127.0.0.1:{LOCAL_PORT}"
    return f"https://{PUBLIC_HOST}"


mcp = MCPServer("Loopback")


# ---------------------------------------------------------------------------
# Portable filesystem / Git tools
# ---------------------------------------------------------------------------

@mcp.tool()
def read_file(path: str, start_line: int = 1, max_lines: int = 1000) -> dict[str, Any]:
    """Read a text file with line-range controls, subject to Loopback filesystem policy."""
    p = POLICY.path_allowed(path)
    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), 10000))
    lines = p.read_text(errors="replace").splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]
    text, truncated = clip("\n".join(selected))
    result = {
        "path": str(p), "start_line": start_line, "returned_lines": len(selected),
        "total_lines": len(lines), "truncated": truncated, "content": text,
    }
    _audit("read_file", path=str(p), lines=len(selected))
    return result


@mcp.tool()
def write_file(path: str, content: str, append: bool = False, create_parents: bool = True) -> dict[str, Any]:
    """Write or append UTF-8 text as the current user, subject to filesystem policy."""
    p = POLICY.path_allowed(path)
    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as handle:
        handle.write(content)
    _audit("write_file", path=str(p), bytes=len(content.encode()), append=append)
    return {"path": str(p), "bytes": len(content.encode()), "append": append}


@mcp.tool()
def list_dir(path: str = "~", max_entries: int = 1000) -> dict[str, Any]:
    """List a directory with metadata, subject to filesystem policy."""
    p = POLICY.path_allowed(path)
    max_entries = max(1, min(int(max_entries), 10000))
    out = []
    for child in sorted(p.iterdir(), key=lambda x: x.name.lower())[:max_entries]:
        try:
            POLICY.path_allowed(child)
            st = child.stat()
            kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
            out.append({"name": child.name, "path": str(child), "type": kind, "size": st.st_size})
        except PermissionError:
            out.append({"name": child.name, "path": str(child), "type": "denied"})
        except OSError as exc:
            out.append({"name": child.name, "path": str(child), "error": str(exc)})
    _audit("list_dir", path=str(p), returned=len(out))
    return {"path": str(p), "returned": len(out), "entries": out}


@mcp.tool()
def patch_file(path: str, old_text: str, new_text: str, count: int = 1, create_backup: bool = True) -> dict[str, Any]:
    """Replace exact text in a file, optionally keeping a timestamped backup."""
    p = POLICY.path_allowed(path)
    text = p.read_text(encoding="utf-8", errors="strict")
    found = text.count(old_text)
    if found == 0:
        raise ValueError("old_text was not found")
    n = found if int(count) <= 0 else min(int(count), found)
    backup = None
    if create_backup:
        backup = p.with_name(p.name + f".bak.{int(time.time())}")
        backup.write_text(text, encoding="utf-8")
    p.write_text(text.replace(old_text, new_text, n), encoding="utf-8")
    _audit("patch_file", path=str(p), replacements=n)
    return {"path": str(p), "replacements": n, "matches_before": found, "backup": str(backup) if backup else None}


@mcp.tool()
def search_text(query: str, path: str = "~", glob: str = "*", max_results: int = 200, case_sensitive: bool = False) -> dict[str, Any]:
    """Search text recursively while skipping dependency, cache, and policy-denied directories."""
    import fnmatch
    root = POLICY.path_allowed(path)
    limit = max(1, min(int(max_results), 5000))
    needle = query if case_sensitive else query.lower()
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", "dist", "build"}
    results = []
    scanned = 0
    for current, dirs, files in os.walk(root):
        kept = []
        for directory in dirs:
            if directory in skip_dirs:
                continue
            try:
                POLICY.path_allowed(Path(current) / directory)
                kept.append(directory)
            except PermissionError:
                continue
        dirs[:] = kept
        for name in files:
            if len(results) >= limit:
                break
            if not fnmatch.fnmatch(name, glob):
                continue
            fp = Path(current) / name
            try:
                POLICY.path_allowed(fp)
                if fp.stat().st_size > 8 * 1024 * 1024:
                    continue
                scanned += 1
                with fp.open("r", encoding="utf-8", errors="ignore") as handle:
                    for lineno, line in enumerate(handle, 1):
                        hay = line if case_sensitive else line.lower()
                        if needle in hay:
                            results.append({"path": str(fp), "line": lineno, "text": line.rstrip("\n")[:2000]})
                            if len(results) >= limit:
                                break
            except (OSError, UnicodeError, PermissionError):
                continue
        if len(results) >= limit:
            break
    _audit("search_text", path=str(root), query=query, returned=len(results))
    return {"path": str(root), "query": query, "scanned_files": scanned, "returned": len(results), "truncated": len(results) >= limit, "results": results}


@mcp.tool()
def tail_file(path: str, lines: int = 200) -> dict[str, Any]:
    """Return the last N lines of a text or log file."""
    from collections import deque
    p = POLICY.path_allowed(path)
    n = max(1, min(int(lines), 10000))
    with p.open("r", encoding="utf-8", errors="replace") as handle:
        selected = list(deque(handle, maxlen=n))
    text, truncated = clip("".join(selected))
    _audit("tail_file", path=str(p), lines=len(selected))
    return {"path": str(p), "returned_lines": len(selected), "truncated": truncated, "content": text}


@mcp.tool()
def git_info(path: str = "~") -> dict[str, Any]:
    """Return branch, root, remotes and concise working-tree state for a Git repo."""
    cwd = resolve_cwd(path)
    def git(*args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=30)
        if check and proc.returncode != 0:
            raise ValueError(proc.stderr.strip() or "git command failed")
        return proc.stdout.strip()
    root = git("rev-parse", "--show-toplevel")
    branch = git("branch", "--show-current", check=False) or git("symbolic-ref", "--short", "HEAD", check=False)
    head = git("rev-parse", "--short", "HEAD", check=False) or None
    _audit("git_info", path=cwd)
    return {"root": root, "branch": branch or None, "head": head, "status": git("status", "--short", "--branch"), "remotes": git("remote", "-v", check=False)}


# ---------------------------------------------------------------------------
# Execution, jobs, terminal sessions, processes
# ---------------------------------------------------------------------------

@mcp.tool()
def command_run(command: str, cwd: str = "~", timeout: int | None = None, approval_id: str | None = None, node: str = "local") -> dict[str, Any]:
    """Run one command and return output. Disabled unless policy permits it."""
    POLICY.require_tool("command_run" if node == "local" else "node_command")
    _command_guard(command, approval_id)
    effective_cwd = resolve_cwd(cwd) if node == "local" else cwd
    result = NODES.command(node, command, shell=SHELL, timeout=min(int(timeout or DEFAULT_TIMEOUT), 3600), cwd=effective_cwd)
    result["stdout"], out_cut = clip(result["stdout"])
    result["stderr"], err_cut = clip(result["stderr"])
    result["truncated"] = out_cut or err_cut
    _audit("command_run", command=command, cwd=cwd, node=node, returncode=result["returncode"])
    return result


@mcp.tool()
def command_start(command: str, cwd: str = "~", approval_id: str | None = None) -> dict[str, Any]:
    """Start a long-running local command. Poll output later with job_output."""
    POLICY.require_tool("command_start")
    _command_guard(command, approval_id)
    result = JOBS.start(command, resolve_cwd(cwd), SHELL)
    _audit("command_start", command=command, cwd=cwd, job_id=result["id"])
    return result


@mcp.tool()
def job_list() -> dict[str, Any]:
    """List commands started through command_start."""
    return {"jobs": JOBS.list()}


@mcp.tool()
def job_output(job_id: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
    """Read incremental output from a background job."""
    return JOBS.output(job_id, offset, max_bytes)


@mcp.tool()
def job_stop(job_id: str) -> dict[str, Any]:
    """Stop a background job and its process group."""
    POLICY.require_tool("command_start")
    result = JOBS.stop(job_id)
    _audit("job_stop", job_id=job_id)
    return result


@mcp.tool()
def task_run(steps: list[str], cwd: str = "~", stop_on_error: bool = True, approval_id: str | None = None) -> dict[str, Any]:
    """Run a bounded sequence of commands in one MCP call to reduce repetitive client approvals."""
    POLICY.require_tool("task_run")
    if not steps or len(steps) > 50:
        raise ValueError("steps must contain 1 to 50 commands")
    task_key = "TASK:" + json.dumps(steps, separators=(",", ":"))
    decisions = [POLICY.command_decision(step) for step in steps]
    if "deny" in decisions:
        raise PermissionError("one or more task commands are denied by Loopback policy")
    if "confirm" in decisions and not APPROVALS.consume(approval_id, task_key):
        pending = APPROVALS.request(task_key)
        raise PermissionError(
            "Loopback operator approval required for this task. "
            f"Approval ID: {pending['id']}. Approve it in the dashboard and retry."
        )
    results = []
    for index, step in enumerate(steps):
        result = NODES.command("local", step, shell=SHELL, timeout=DEFAULT_TIMEOUT, cwd=resolve_cwd(cwd))
        stdout, a = clip(result["stdout"])
        stderr, b = clip(result["stderr"])
        row = {"index": index, "command": step, "returncode": result["returncode"], "stdout": stdout, "stderr": stderr, "truncated": a or b}
        results.append(row)
        if stop_on_error and result["returncode"] != 0:
            break
    _audit("task_run", steps=len(steps), completed=len(results), cwd=cwd)
    return {"cwd": cwd, "requested": len(steps), "completed": len(results), "results": results}


@mcp.tool()
def terminal_start(cwd: str = "~") -> dict[str, Any]:
    """Start a persistent interactive PTY shell."""
    POLICY.require_tool("terminal_start")
    result = TERMINALS.start(SHELL, resolve_cwd(cwd))
    _audit("terminal_start", terminal_id=result["id"], cwd=cwd)
    return result


@mcp.tool()
def terminal_list() -> dict[str, Any]:
    """List persistent PTY sessions."""
    return {"terminals": TERMINALS.list()}


@mcp.tool()
def terminal_write(terminal_id: str, data: str) -> dict[str, Any]:
    """Write keystrokes/text to a persistent PTY session."""
    POLICY.require_tool("terminal_write")
    result = TERMINALS.write(terminal_id, data)
    _audit("terminal_write", terminal_id=terminal_id, bytes=len(data.encode()))
    return result


@mcp.tool()
def terminal_read(terminal_id: str, max_bytes: int = 65536) -> dict[str, Any]:
    """Read currently available output from a persistent PTY."""
    POLICY.require_tool("terminal_read")
    return TERMINALS.read(terminal_id, max_bytes)


@mcp.tool()
def terminal_close(terminal_id: str) -> dict[str, Any]:
    """Close a persistent PTY session."""
    POLICY.require_tool("terminal_close")
    result = TERMINALS.close(terminal_id)
    _audit("terminal_close", terminal_id=terminal_id)
    return result


@mcp.tool()
def process_list(limit: int = 200) -> dict[str, Any]:
    """Return structured process information sorted by resource use."""
    POLICY.require_tool("process_list")
    return {"processes": get_process_list(limit)}


@mcp.tool()
def process_info(pid: int) -> dict[str, Any]:
    """Inspect one process."""
    POLICY.require_tool("process_info")
    return get_process_info(pid)


@mcp.tool()
def process_kill(pid: int, signal_number: int = signal.SIGTERM) -> dict[str, Any]:
    """Signal a process. Mutating process control is disabled outside trusted policy by default."""
    POLICY.require_tool("process_kill")
    result = kill_process(pid, signal_number)
    _audit("process_kill", pid=pid, signal=signal_number)
    return result


# ---------------------------------------------------------------------------
# Fleet / gateway
# ---------------------------------------------------------------------------

@mcp.tool()
def node_list() -> dict[str, Any]:
    """List local and configured remote Loopback gateway nodes."""
    return {"nodes": NODES.list()}


@mcp.tool()
def node_health(name: str) -> dict[str, Any]:
    """Check reachability of a named local/SSH node."""
    return NODES.health(name, SHELL)


@mcp.tool()
def node_command(name: str, command: str, cwd: str = "~", timeout: int | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Run a command on a named node. Remote nodes currently use operator-configured SSH aliases/targets."""
    POLICY.require_tool("node_command")
    _command_guard(command, approval_id)
    result = NODES.command(name, command, shell=SHELL, timeout=min(int(timeout or DEFAULT_TIMEOUT), 3600), cwd=cwd)
    result["stdout"], out_cut = clip(result["stdout"])
    result["stderr"], err_cut = clip(result["stderr"])
    result["truncated"] = out_cut or err_cut
    _audit("node_command", node=name, command=command, returncode=result["returncode"])
    return result


@mcp.tool()
def node_copy(source_node: str, source_path: str, destination_node: str, destination_path: str) -> dict[str, Any]:
    """Copy a file between local and SSH nodes using scp; requires trusted node_command policy."""
    POLICY.require_tool("node_command")
    src_node = NODES.get(source_node)
    dst_node = NODES.get(destination_node)
    if source_node == "local":
        source_path = str(POLICY.path_allowed(source_path))
    if destination_node == "local":
        destination_path = str(POLICY.path_allowed(destination_path))
    if source_node == "local" and destination_node == "local":
        Path(destination_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        rc, err = 0, ""
    else:
        def spec(node: dict[str, Any], name: str, path: str) -> str:
            if name == "local":
                return path
            return f"{node.get('target') or name}:{path}"
        cmd = ["scp", "-p"]
        if source_node != "local" and destination_node != "local":
            cmd.append("-3")
        cmd += [spec(src_node, source_node, source_path), spec(dst_node, destination_node, destination_path)]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=DEFAULT_TIMEOUT)
        rc, err = proc.returncode, proc.stderr
    _audit("node_copy", source_node=source_node, destination_node=destination_node, source=source_path, destination=destination_path, returncode=rc)
    return {"source_node": source_node, "destination_node": destination_node, "source_path": source_path, "destination_path": destination_path, "returncode": rc, "stderr": err}


@mcp.tool()
def policy_status() -> dict[str, Any]:
    """Return active Loopback policy and tool permissions."""
    return POLICY.load()


@mcp.tool()
def system_snapshot() -> dict[str, Any]:
    """Return host, runtime, policy, node and resource state in one read-only call."""
    return {
        "machine": machine_info(),
        "metrics": host_metrics(),
        "policy": POLICY.load(),
        "nodes": NODES.list(),
        "jobs": JOBS.list(),
        "terminals": TERMINALS.list(),
    }


@mcp.tool()
def diagnostics() -> dict[str, Any]:
    """Return lightweight runtime diagnostics without exposing stored credentials."""
    def version(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=5)
            lines = (proc.stdout or proc.stderr).strip().splitlines()
            return lines[0][:300] if lines else None
        except (OSError, subprocess.SubprocessError):
            return None
    usage = shutil.disk_usage(Path.home())
    return {
        "version": VERSION,
        "hostname": socket.gethostname(),
        "python": version([os.sys.executable, "--version"]),
        "git": version(["git", "--version"]),
        "cloudflared": version(["cloudflared", "--version"]),
        "tailscale": version(["tailscale", "version"]),
        "disk_home": {"total": usage.total, "used": usage.used, "free": usage.free},
        "port": LOCAL_PORT,
        "public_host": PUBLIC_HOST,
        "ingress": INGRESS,
        "command_timeout": DEFAULT_TIMEOUT,
        "max_output_bytes": MAX_OUTPUT,
        "policy_profile": POLICY.load()["profile"],
    }


@mcp.tool()
def machine_info() -> dict[str, Any]:
    """Return basic non-secret context for the machine."""
    return {
        "user": os.environ.get("USER"),
        "home": str(Path.home()),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "shell": SHELL,
        "local_port": LOCAL_PORT,
        "public_host": PUBLIC_HOST,
        "access_mode": ACCESS_MODE,
        "policy_profile": POLICY.load()["profile"],
    }


# ---------------------------------------------------------------------------
# OAuth 2.0 Authorization Code + PKCE + Dynamic Client Registration
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request) -> Response:
    base = _public_base()
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base]})


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request) -> Response:
    base = _public_base()
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
        "authorization_response_iss_parameter_supported": True,
    })


@mcp.custom_route("/oauth/register", methods=["POST"])
async def oauth_register(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    redirects = [str(uri) for uri in body.get("redirect_uris", [])]
    if not redirects or any(not _valid_redirect(uri) for uri in redirects):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
    client_id = secrets.token_urlsafe(18)
    _OAUTH_CLIENTS[client_id] = {"redirect_uris": redirects, "created": time.time()}
    return JSONResponse({
        "client_id": client_id,
        "redirect_uris": redirects,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201)


@mcp.custom_route("/oauth/authorize", methods=["GET", "POST"])
async def oauth_authorize(request: Request) -> Response:
    q = await request.form() if request.method == "POST" else request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    state = q.get("state", "")
    challenge = q.get("code_challenge", "")
    method = q.get("code_challenge_method", "")
    supplied = q.get("loopback_token", "")
    resource = q.get("resource", f"{_public_base()}/mcp")
    client = _OAUTH_CLIENTS.get(client_id)
    if not client or redirect_uri not in client.get("redirect_uris", []):
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if method != "S256" or not challenge:
        return JSONResponse({"error": "invalid_request", "error_description": "PKCE S256 required"}, status_code=400)
    if resource != f"{_public_base()}/mcp":
        return JSONResponse({"error": "invalid_target", "error_description": "resource must match this Loopback MCP endpoint"}, status_code=400)
    if not supplied:
        hidden = "".join(
            f'<input type="hidden" name="{html_lib.escape(k, quote=True)}" value="{html_lib.escape(v, quote=True)}">'
            for k, v in [("client_id", client_id), ("redirect_uri", redirect_uri), ("state", state), ("code_challenge", challenge), ("code_challenge_method", method), ("resource", resource)]
        )
        safe_host = html_lib.escape(PUBLIC_HOST, quote=True)
        safe_client = html_lib.escape(client_id, quote=True)
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Loopback // OAuth</title>
<style>:root{{--yellow:#ffd600;--cyan:#6ee7ff;--muted:#91a0ad;--text:#f5f7f8}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 0,#1b2229 0,#0b0e11 45%,#050607 100%);color:var(--text);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;padding:24px}}.card{{width:min(560px,100%);background:linear-gradient(180deg,#12171c,#0d1115);border:1px solid #2b333a;border-radius:22px;box-shadow:0 28px 90px #000a;overflow:hidden}}.top{{padding:28px 30px 22px;border-bottom:1px solid #2a3138}}.badge{{color:var(--yellow);font-size:12px;letter-spacing:.18em}}h1{{font-size:27px;margin:18px 0 8px}}p{{color:var(--muted);line-height:1.6}}.meta{{display:grid;grid-template-columns:90px 1fr;gap:8px 14px;margin-top:18px;padding:13px 15px;border:1px solid #2a3138;border-radius:12px;background:#080b0e;font-size:12px}}.meta b{{color:var(--cyan)}}.body{{padding:25px 30px 30px}}label{{display:block;font-size:12px;margin:0 0 9px;text-transform:uppercase}}input{{width:100%;border:1px solid #38424b;background:#07090b;color:#fff;border-radius:11px;padding:14px 15px;font:inherit}}button{{width:100%;border:0;border-radius:11px;padding:14px;background:var(--yellow);font-weight:800;margin-top:15px}}</style></head>
<body><main class="card"><section class="top"><div class="badge">◆ LOOPBACK AUTH GATE</div><h1>Authorize machine access</h1><p>A web agent is requesting an authenticated MCP session. The machine token is used only to authorize this grant; the client receives a separate short-lived OAuth access token.</p><div class="meta"><b>HOST</b><span>{safe_host}</span><b>CLIENT</b><span>{safe_client}</span></div></section><section class="body"><form method="post" action="/oauth/authorize" autocomplete="off">{hidden}<label>Loopback token</label><input type="password" name="loopback_token" autocomplete="current-password" autofocus required><button type="submit">Authorize connector</button></form></section></main></body></html>"""
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    if not secrets.compare_digest(supplied, TOKEN):
        return HTMLResponse("<body style='background:#07090b;color:white;font:14px monospace;padding:40px'><h2>LOOPBACK // ACCESS DENIED</h2><p>Invalid machine token.</p></body>", status_code=401)
    code = secrets.token_urlsafe(28)
    _OAUTH_CODES[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": challenge, "resource": resource, "expires": time.time() + _CODE_TTL}
    sep = "&" if "?" in redirect_uri else "?"
    redirect_params = {"code": code, "iss": _public_base()}
    if state:
        redirect_params["state"] = state
    location = f"{redirect_uri}{sep}{urlencode(redirect_params)}"
    AUDIT.write("oauth_authorize", detail={"client_id": client_id})
    return RedirectResponse(location, status_code=302)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request) -> Response:
    form = await request.form()
    grant = form.get("grant_type")
    if grant == "authorization_code":
        code = str(form.get("code", ""))
        verifier = str(form.get("code_verifier", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        entry = _OAUTH_CODES.pop(code, None)
        if not entry or entry["expires"] < time.time() or redirect_uri != entry["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        calc = _b64url(hashlib.sha256(verifier.encode()).digest())
        if not secrets.compare_digest(calc, entry["challenge"]):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_new_oauth_pair(entry["client_id"], entry["resource"]))
    if grant == "refresh_token":
        old = str(form.get("refresh_token", ""))
        _prune_tokens()
        entry = _OAUTH_REFRESH.pop(old, None)
        if not entry or entry["expires"] < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_new_oauth_pair(entry["subject"], entry["resource"]))
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ---------------------------------------------------------------------------
# Admin UI and observability API
# ---------------------------------------------------------------------------

@mcp.custom_route("/admin/login", methods=["GET", "POST"])
async def admin_login(request: Request) -> Response:
    if request.method == "GET":
        return HTMLResponse(login_html(PUBLIC_HOST), headers={"Cache-Control": "no-store"})
    form = await request.form()
    supplied = str(form.get("token", ""))
    if not secrets.compare_digest(supplied, TOKEN):
        AUDIT.write("admin_login", status="denied")
        return HTMLResponse(login_html(PUBLIC_HOST, "Invalid Loopback token."), status_code=401)
    sid = "adm_" + secrets.token_urlsafe(32)
    _ADMIN_SESSIONS[sid] = time.time() + 12 * 3600
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie("loopback_admin", sid, httponly=True, secure=PUBLIC_HOST not in {"localhost", "127.0.0.1"}, samesite="strict", max_age=12 * 3600)
    AUDIT.write("admin_login")
    return response


@mcp.custom_route("/admin/logout", methods=["POST"])
async def admin_logout(request: Request) -> Response:
    sid = _cookie(request, "loopback_admin")
    if sid:
        _ADMIN_SESSIONS.pop(sid, None)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("loopback_admin")
    return response


@mcp.custom_route("/admin", methods=["GET"])
async def admin_dashboard(request: Request) -> Response:
    if not _admin_ok(request):
        return RedirectResponse("/admin/login", status_code=303)
    return HTMLResponse(dashboard_html(PUBLIC_HOST), headers={"Cache-Control": "no-store"})


@mcp.custom_route("/api/overview", methods=["GET"])
async def api_overview(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    jobs = JOBS.list()
    terminals = TERMINALS.list()
    return JSONResponse({
        "version": VERSION,
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER"),
        "platform": platform.platform(),
        "port": LOCAL_PORT,
        "public_host": PUBLIC_HOST,
        "ingress": INGRESS,
        "mcp_url": f"{_public_base()}/mcp",
        "metrics": host_metrics(),
        "jobs": {"total": len(jobs), "running": sum(1 for row in jobs if row["running"])},
        "terminals": {"total": len(terminals), "running": sum(1 for row in terminals if row["running"])},
        "clients": len(_CLIENTS),
    })


@mcp.custom_route("/api/policy", methods=["GET"])
async def api_policy(request: Request) -> Response:
    denied = _admin_required(request)
    return denied or JSONResponse(POLICY.load())


@mcp.custom_route("/api/policy/profile", methods=["POST"])
async def api_policy_profile(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    body = await request.json()
    value = POLICY.set_profile(str(body.get("profile", "")))
    AUDIT.write("policy_profile", detail={"profile": value["profile"]})
    return JSONResponse(value)


@mcp.custom_route("/api/policy/tool", methods=["POST"])
async def api_policy_tool(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    body = await request.json()
    value = POLICY.set_tool(str(body.get("tool", "")), bool(body.get("enabled")))
    AUDIT.write("policy_tool", detail={"tool": body.get("tool"), "enabled": bool(body.get("enabled"))})
    return JSONResponse(value)


@mcp.custom_route("/api/policy/paths", methods=["POST"])
async def api_policy_paths(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    body = await request.json()
    allow = [str(x).strip() for x in body.get("allow", []) if str(x).strip()]
    deny_paths = [str(x).strip() for x in body.get("deny", []) if str(x).strip()]
    value = POLICY.set_paths(allow, deny_paths)
    AUDIT.write("policy_paths", detail={"allow": allow, "deny": deny_paths})
    return JSONResponse(value)


@mcp.custom_route("/api/jobs", methods=["GET"])
async def api_jobs(request: Request) -> Response:
    denied = _admin_required(request)
    return denied or JSONResponse({"jobs": JOBS.list(), "terminals": TERMINALS.list()})


@mcp.custom_route("/api/jobs/{job_id}/output", methods=["GET"])
async def api_job_output(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    return JSONResponse(
        JOBS.output(
            request.path_params["job_id"],
            int(request.query_params.get("offset", "0")),
            int(request.query_params.get("max_bytes", "131072")),
        )
    )


@mcp.custom_route("/api/jobs/{job_id}/stop", methods=["POST"])
async def api_job_stop(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    result = JOBS.stop(request.path_params["job_id"])
    AUDIT.write("admin_job_stop", detail={"job_id": result["id"]})
    return JSONResponse(result)


@mcp.custom_route("/api/terminals/{terminal_id}/stop", methods=["POST"])
async def api_terminal_stop(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    result = TERMINALS.close(request.path_params["terminal_id"])
    AUDIT.write("admin_terminal_stop", detail={"terminal_id": result["id"]})
    return JSONResponse(result)


@mcp.custom_route("/api/processes", methods=["GET"])
async def api_processes(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    return JSONResponse({"processes": get_process_list(int(request.query_params.get("limit", "300")))})


@mcp.custom_route("/api/processes/{pid}/kill", methods=["POST"])
async def api_process_kill(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    body = await request.json()
    result = kill_process(int(request.path_params["pid"]), int(body.get("signal", signal.SIGTERM)))
    AUDIT.write("admin_process_kill", detail=result)
    return JSONResponse(result)


@mcp.custom_route("/api/nodes", methods=["GET", "POST"])
async def api_nodes(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    if request.method == "GET":
        return JSONResponse({"nodes": NODES.list()})
    node = NODES.upsert(await request.json())
    AUDIT.write("node_upsert", detail={"node": node})
    return JSONResponse(node)


@mcp.custom_route("/api/nodes/{name}/health", methods=["GET"])
async def api_node_health(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    return JSONResponse(NODES.health(request.path_params["name"], SHELL))


@mcp.custom_route("/api/nodes/{name}", methods=["DELETE"])
async def api_node_delete(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    name = request.path_params["name"]
    NODES.remove(name)
    AUDIT.write("node_remove", detail={"name": name})
    return JSONResponse({"ok": True})


@mcp.custom_route("/api/clients", methods=["GET"])
async def api_clients(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    rows = sorted(_CLIENTS.values(), key=lambda row: row.get("last_seen", 0), reverse=True)
    return JSONResponse({"clients": rows})


@mcp.custom_route("/api/approvals", methods=["GET"])
async def api_approvals(request: Request) -> Response:
    denied = _admin_required(request)
    return denied or JSONResponse({"approvals": APPROVALS.list()})


@mcp.custom_route("/api/approvals/{approval_id}/approve", methods=["POST"])
async def api_approval(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    approval_id = request.path_params["approval_id"]
    ok = APPROVALS.approve(approval_id)
    AUDIT.write("approval", status="ok" if ok else "missing", detail={"approval_id": approval_id})
    return JSONResponse({"ok": ok}, status_code=200 if ok else 404)


@mcp.custom_route("/api/audit", methods=["GET"])
async def api_audit(request: Request) -> Response:
    denied = _admin_required(request)
    if denied:
        return denied
    return JSONResponse({"events": AUDIT.recent(int(request.query_params.get("limit", "200")))})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "service": "loopback", "version": VERSION, "profile": POLICY.load()["profile"]})


# ---------------------------------------------------------------------------
# Transport security and authentication middleware
# ---------------------------------------------------------------------------

_extra_origins = [x.strip() for x in os.environ.get("LOOPBACK_ALLOWED_ORIGINS", "").split(",") if x.strip()]
_origins = [
    f"https://{PUBLIC_HOST}",
    "https://www.perplexity.ai",
    "https://perplexity.ai",
    "https://chatgpt.com",
    *_extra_origins,
]

security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[PUBLIC_HOST, f"{PUBLIC_HOST}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"],
    allowed_origins=[*_origins, f"https://{PUBLIC_HOST}:*", "http://127.0.0.1:*", "http://localhost:*"],
)

inner = mcp.streamable_http_app(transport_security=security, stateless_http=True, json_response=True)
cors = CORSMiddleware(
    inner,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


class TokenGate:
    """Authenticate MCP requests while leaving OAuth, health and admin-login routes available."""

    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return await self.wrapped(scope, receive, send)
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if method == "OPTIONS" or path == "/healthz" or path.startswith("/.well-known/oauth") or path.startswith("/oauth/") or path.startswith("/admin") or path.startswith("/api/"):
            return await self.wrapped(scope, receive, send)
        if path == "/mcp" or path.startswith("/mcp/"):
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            candidates = [
                headers.get("authorization", "").strip(),
                headers.get("x-api-key", "").strip(),
                headers.get("api-key", "").strip(),
            ]
            if not any(_bearer_valid(value) for value in candidates if value):
                response = JSONResponse(
                    {"detail": "Unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'Bearer resource_metadata="{_public_base()}/.well-known/oauth-protected-resource"'},
                )
                return await response(scope, receive, send)
            ua = headers.get("user-agent", "unknown")[:300]
            client = headers.get("x-forwarded-for", "") or (scope.get("client") or ["unknown"])[0]
            key = hashlib.sha256(f"{client}|{ua}".encode()).hexdigest()[:16]
            entry = _CLIENTS.setdefault(key, {"id": key, "user_agent": ua, "client": client, "first_seen": time.time(), "requests": 0})
            entry["last_seen"] = time.time()
            entry["requests"] += 1
        return await self.wrapped(scope, receive, send)


app = TokenGate(cors)
