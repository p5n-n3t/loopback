"""Loopback MCP gateway — composition root.

Builds the shared context, registers MCP tools with scope requirements, and
assembles the ASGI application:

    Starlette(parent)
      ├─ auth_http routes      (OAuth AS, RFC 9728 metadata, login/enroll)
      ├─ /api/admin/*          (AdminGate: session + CSRF) → admin_api, fabric_api, maintenance_api
      ├─ /admin, /admin/static (dashboard)
      ├─ /healthz
      └─ Mount("/") → TokenGate → CORS → McpGate → MCP streamable-HTTP app (/mcp)
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

import admin_api
import auth_http
from auth import Principal
from bootstrap import build_context
from browser import BrowserAdapter
from desktop import DesktopAdapter
from documents import DocumentTools
from jobs import QuotaExceeded, shell_argv
from policy import ApprovalRequired, PolicyError
from redaction import redact_text
from url_policy import UrlPolicyError
from url_policy import from_policy as url_policy_from
from version import VERSION

CTX = build_context()
CTX.browser = BrowserAdapter(url_policy=lambda: url_policy_from(POLICY.data))
CTX.documents = DocumentTools()
CTX.desktop = DesktopAdapter()
POLICY = CTX.policy
AUTH = CTX.auth
AUDIT = CTX.audit
JOBS = CTX.jobs
TERMINALS = CTX.terminals
FLEET = CTX.fleet
CFG = CTX.cfg_dir
STARTED_AT = CTX.started_at
WINDOWS = sys.platform.startswith("win")

CURRENT_PRINCIPAL: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("loopback_principal", default=None)


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(CTX.config.data.get(key, default))
    except (TypeError, ValueError):
        return default


def max_output() -> int:
    return _cfg_int("max_output_bytes", 1048576)


def default_timeout() -> int:
    return _cfg_int("command_timeout", 120)


def shell_path() -> str:
    return CTX.config.effective_shell()


def clip(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    limit = max_output()
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="replace") + "\n...[truncated]", True


def _principal_name() -> str | None:
    p = CURRENT_PRINCIPAL.get()
    return p.display if p else None


def _owner_key() -> str:
    """Stable ownership key for jobs and terminals: never the display name.

    OAuth grants are separated per client as well as per subject, so one connector
    cannot read or drive another connector's sessions. Local stdio use (no HTTP
    principal) shares one ``local`` owner. Administrators inspect all resources
    through the dashboard API, which uses the unscoped manager view.
    """
    p = CURRENT_PRINCIPAL.get()
    if p is None:
        return "local"
    key = f"{p.kind}:{p.subject}"
    if p.client_id:
        key += f"#{p.client_id}"
    return key


def _secure_cwd(value: str | None, target: str = "local") -> str:
    p = POLICY.check_path(value or CTX.config.effective_initial_cwd(), write=False, target=target)
    if target == "local" and not p.is_dir():
        raise ValueError(f"cwd is not a directory: {p}")
    return str(p)


def _sandbox_plan(cwd: str | None):
    tp = POLICY.target_policy("local")
    roots = POLICY.root_entries("local")
    if POLICY.file_mode("local") == "ro":
        roots = [{"path": r["path"], "mode": "ro"} for r in roots]
    return CTX.sandbox.plan_from_policy(roots, tp.get("denied_roots") or [], cwd)


def sandbox_wrap(argv: list[str], cwd: str):
    return CTX.sandbox.wrap(argv, _sandbox_plan(cwd))


if JOBS is not None:
    JOBS.wrap = sandbox_wrap
    JOBS.max_completed = int(CTX.config.data["maintenance"].get("max_completed_jobs", 200))
    JOBS.max_running_per_owner = int(CTX.config.data["maintenance"].get("max_running_jobs_per_principal", 16))
if TERMINALS is not None:
    TERMINALS.wrap = sandbox_wrap
    TERMINALS.max_per_owner = int(CTX.config.data["maintenance"].get("max_terminals_per_principal", 8))
    TERMINALS.idle_timeout = float(CTX.config.data["maintenance"].get("terminal_idle_seconds", 4 * 3600))


# ---------------------------------------------------------------------------
# Tool metadata: required scope, category, target, network, approval
# ---------------------------------------------------------------------------
TOOL_META: dict[str, dict[str, Any]] = {}


_ANTICIPATED = (PolicyError, ValueError, KeyError, RuntimeError, OSError, httpx.HTTPError)


def _translate(exc: BaseException) -> ToolError:
    """Turn anticipated failures into ToolError so the message reaches the agent.

    ``approval_required:<id>`` and policy denials are the signals agents act on;
    the SDK hides the text of any other exception type.
    """
    if isinstance(exc, ApprovalRequired):
        return ToolError(str(exc))
    if isinstance(exc, UrlPolicyError):
        return ToolError(f"url_denied: {exc}")
    if isinstance(exc, QuotaExceeded):
        return ToolError(f"quota_exceeded: {exc}")
    if isinstance(exc, PolicyError):
        return ToolError(f"policy_denied: {exc}")
    if isinstance(exc, KeyError):
        return ToolError(f"not_found: {exc.args[0] if exc.args else exc}")
    if isinstance(exc, FileNotFoundError):
        return ToolError(f"not_found: {exc.filename or exc}")
    if isinstance(exc, PermissionError):
        return ToolError(f"os_permission_denied: {exc.filename or exc}")
    return ToolError(f"{type(exc).__name__}: {exc}")


def meta(scope: str, category: str, *, target: str = "local", network: bool = False, approval: str = "none"):
    def deco(fn):
        TOOL_META[fn.__name__] = {"scope": scope, "category": category, "target": target, "network": network, "approval": approval}
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except ToolError:
                    raise
                except _ANTICIPATED as exc:
                    raise _translate(exc) from exc
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except _ANTICIPATED as exc:
                raise _translate(exc) from exc
        return wrapper
    return deco


READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
READ_NONIDEMPOTENT = ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=False)
MUTATE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
MUTATE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

mcp = MCPServer(
    "Loopback",
    version=VERSION,
    instructions=(
        "Loopback exposes an authorized computer (and optionally remote machines) to MCP clients. "
        "Start with machine_info, permissions and connections to learn the local user, home directory, allowed locations and "
        "which targets are local versus remote. Prefer typed file tools over shell commands. Shell tools run inside an OS sandbox "
        "when one is active; an 'approval_required:<id>' error means a human must approve the action in the Loopback dashboard."
    ),
)


# ------------------------------------------------------------------ files
@mcp.tool(title="Read file", annotations=READ)
@meta("loopback:read", "files")
def read_file(path: str, start_line: int = 1, max_lines: int = 1000) -> dict[str, Any]:
    """Read a UTF-8 text file (local machine) with line-range controls. Paths must be inside permitted locations."""
    p = POLICY.check_path(path, write=False)
    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), 10000))
    lines = p.read_text(errors="replace").splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]
    text, truncated = clip("\n".join(selected))
    return {"path": str(p), "start_line": start_line, "returned_lines": len(selected), "total_lines": len(lines),
            "truncated": truncated, "content": text, "target": "local"}


@mcp.tool(title="Write file", annotations=MUTATE)
@meta("loopback:write", "files")
def write_file(path: str, content: str, append: bool = False, create_parents: bool = True) -> dict[str, Any]:
    """Write or append UTF-8 text to a local file inside a read/write permitted location. Overwrites unless append=true."""
    p = POLICY.check_path(path, write=True)
    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": str(p), "bytes": len(content.encode()), "append": append, "target": "local"}


@mcp.tool(title="List directory", annotations=READ)
@meta("loopback:read", "files")
def list_dir(path: str = "~", max_entries: int = 1000) -> dict[str, Any]:
    """List a local directory with entry type and size. '~' is the service user's home directory."""
    p = POLICY.check_path(path, write=False)
    max_entries = max(1, min(int(max_entries), 10000))
    out = []
    for child in sorted(p.iterdir(), key=lambda x: x.name.lower())[:max_entries]:
        try:
            st = child.stat()
            kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
            out.append({"name": child.name, "path": str(child), "type": kind, "size": st.st_size, "symlink": child.is_symlink()})
        except OSError as e:
            out.append({"name": child.name, "path": str(child), "error": str(e)})
    return {"path": str(p), "returned": len(out), "entries": out, "target": "local"}


@mcp.tool(title="Patch file", annotations=MUTATE)
@meta("loopback:write", "files")
def patch_file(path: str, old_text: str, new_text: str, count: int = 1, create_backup: bool = True) -> dict[str, Any]:
    """Replace exact text in a local file. count<=0 replaces every match. Keeps a timestamped .bak copy by default."""
    p = POLICY.check_path(path, write=True)
    text = p.read_text(encoding="utf-8", errors="strict")
    found = text.count(old_text)
    if found == 0:
        raise ValueError("old_text was not found; read the file first and copy the exact text")
    n = found if int(count) <= 0 else min(int(count), found)
    backup = None
    if create_backup:
        backup = p.with_name(p.name + f".bak.{int(time.time())}")
        backup.write_text(text, encoding="utf-8")
    p.write_text(text.replace(old_text, new_text, n), encoding="utf-8")
    return {"path": str(p), "replacements": n, "matches_before": found, "backup": str(backup) if backup else None, "target": "local"}


@mcp.tool(title="Search text", annotations=READ)
@meta("loopback:read", "files")
def search_text(query: str, path: str = "~", glob: str = "*", max_results: int = 200, case_sensitive: bool = False) -> dict[str, Any]:
    """Search text recursively under a permitted local path, skipping dependency and cache directories."""
    import fnmatch

    root = POLICY.check_path(path, write=False)
    limit = max(1, min(int(max_results), 5000))
    needle = query if case_sensitive else query.lower()
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", "dist", "build"}
    results: list[dict[str, Any]] = []
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
                POLICY.check_path(fp, write=False)
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
            except (OSError, UnicodeError, PolicyError):
                continue
        if len(results) >= limit:
            break
    return {"path": str(root), "query": query, "scanned_files": scanned, "returned": len(results), "truncated": len(results) >= limit,
            "results": results, "target": "local"}


@mcp.tool(title="Tail file", annotations=READ)
@meta("loopback:read", "files")
def tail_file(path: str, lines: int = 200) -> dict[str, Any]:
    """Return the last N lines of a local text or log file."""
    from collections import deque

    p = POLICY.check_path(path, write=False)
    n = max(1, min(int(lines), 10000))
    with p.open("r", encoding="utf-8", errors="replace") as handle:
        selected = list(deque(handle, maxlen=n))
    text, truncated = clip("".join(selected))
    return {"path": str(p), "returned_lines": len(selected), "truncated": truncated, "content": text, "target": "local"}


@mcp.tool(title="Git info", annotations=READ)
@meta("loopback:read", "files")
def git_info(path: str = "~") -> dict[str, Any]:
    """Return branch, root, remotes and concise working-tree state for a local Git repository."""
    cwd = _secure_cwd(path)

    def git(*args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=30)
        if check and proc.returncode != 0:
            raise ValueError(proc.stderr.strip() or "git command failed")
        return proc.stdout.strip()

    root = git("rev-parse", "--show-toplevel")
    branch = git("branch", "--show-current", check=False) or git("symbolic-ref", "--short", "HEAD", check=False)
    head = git("rev-parse", "--short", "HEAD", check=False) or None
    return {"root": root, "branch": branch or None, "head": head, "status": git("status", "--short", "--branch"),
            "remotes": git("remote", "-v", check=False), "target": "local"}


# ----------------------------------------------------------------- system
@mcp.tool(title="Diagnostics", annotations=READ)
@meta("loopback:read", "system")
def diagnostics() -> dict[str, Any]:
    """Lightweight runtime diagnostics for the local gateway (versions, disk, sandbox, ingress). No credentials."""
    import shutil

    def version(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=5)
            out = (proc.stdout or proc.stderr).strip().splitlines()
            return out[0][:300] if out else None
        except (OSError, subprocess.SubprocessError):
            return None

    usage = shutil.disk_usage(CTX.identity.home)
    return {
        "version": VERSION,
        "hostname": socket.gethostname(),
        "os": CTX.identity.os_release,
        "python": sys.version.split()[0],
        "git": version(["git", "--version"]),
        "cloudflared": version(["cloudflared", "--version"]),
        "tailscale": version(["tailscale", "version"]),
        "disk_home": {"total": usage.total, "used": usage.used, "free": usage.free},
        "port": CTX.config.port,
        "public_hosts": CTX.config.public_hosts(),
        "command_timeout": default_timeout(),
        "max_output_bytes": max_output(),
        "sandbox": CTX.sandbox.status(),
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


@mcp.tool(title="Machine info", annotations=READ)
@meta("loopback:read", "system")
def machine_info() -> dict[str, Any]:
    """Who and where: the service user, home directory, shell, initial working directory and machine classification."""
    import identity as identity_mod

    return {
        "user": CTX.identity.username,
        "home": CTX.identity.home,
        "hostname": CTX.identity.hostname,
        "os": CTX.identity.os_release,
        "platform": CTX.identity.platform,
        "arch": CTX.identity.arch,
        "machine_kind": identity_mod.detect_virtualization(),
        "display_name": CTX.config.data.get("display_name"),
        "shell": shell_path(),
        "initial_cwd": CTX.config.effective_initial_cwd(),
        "local_port": CTX.config.port,
        "public_hosts": CTX.config.public_hosts(),
        "profile": POLICY.profile,
        "target": "local",
    }


@mcp.tool(title="Policy status", annotations=READ)
@meta("loopback:read", "system")
def policy_status() -> dict[str, Any]:
    """Effective Loopback security profile and switches for the local machine. Never includes secrets."""
    return {
        "profile": POLICY.profile,
        "allowed_roots": POLICY.root_entries("local"),
        "denied_roots": POLICY.data.get("denied_roots", []),
        "allow_shell": POLICY.data.get("allow_shell", True),
        "allow_process_control": POLICY.data.get("allow_process_control", True),
        "allow_fleet": POLICY.data.get("allow_fleet", True),
        "sandbox": CTX.sandbox.status(),
        "pending_approvals": len(POLICY.pending()),
    }


@mcp.tool(title="Effective permissions", annotations=READ)
@meta("loopback:read", "system", target="both")
def permissions(target: str = "local") -> dict[str, Any]:
    """Explain what is permitted on a target ('local' or a remote target name): roots, modes, shell, approvals, enforcement."""
    perms = POLICY.effective_permissions(target)
    principal = CURRENT_PRINCIPAL.get()
    if principal:
        perms["authentication"] = {"kind": principal.kind, "scopes": sorted(principal.scopes)}
    if target in {"local", ""}:
        perms["sandbox"] = CTX.sandbox.status()
    return perms


@mcp.tool(title="Connections", annotations=READ)
@meta("loopback:read", "system", target="both")
def connections() -> dict[str, Any]:
    """Connection topology as last probed: gateway, ingress, clients, remote machines, edge direction/transport/status. No network calls."""
    if CTX.topology is None:
        return {"nodes": [], "edges": [], "detail": "topology module unavailable"}
    return CTX.topology.build()


# -------------------------------------------------------------- execution
@mcp.tool(title="Execute command", annotations=MUTATE)
@meta("loopback:exec", "execution", approval="policy")
def execute(command: str, cwd: str | None = None, timeout: int | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Run one shell command on the local machine with bounded output, inside the OS sandbox when active.
    cwd defaults to the service user's home. Sensitive commands may return approval_required:<id>."""
    POLICY.check_command(command, approval_id=approval_id)
    workdir = _secure_cwd(cwd)
    limit = max(1, min(int(timeout or default_timeout()), 3600))
    started = time.time()
    argv, preexec, env = sandbox_wrap(shell_argv(shell_path(), command), workdir)
    kwargs: dict[str, Any] = {} if WINDOWS else {"preexec_fn": preexec, "start_new_session": True}
    try:
        proc = subprocess.run(argv, cwd=workdir, text=True, capture_output=True, timeout=limit, env=env, **kwargs)
        stdout, out_trunc = clip(proc.stdout)
        stderr, err_trunc = clip(proc.stderr)
        return {"command": command, "cwd": workdir, "returncode": proc.returncode, "stdout": stdout, "stderr": stderr,
                "truncated": out_trunc or err_trunc, "duration_ms": int((time.time() - started) * 1000),
                "target": "local", "sandbox": CTX.sandbox.backend.name, "enforced": CTX.sandbox.enforced()}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        return {"command": command, "cwd": workdir, "returncode": None, "stdout": clip(out)[0], "stderr": clip(err)[0],
                "timed_out": True, "duration_ms": int((time.time() - started) * 1000), "target": "local"}


@mcp.tool(title="Start background job", annotations=CREATE)
@meta("loopback:exec", "execution", approval="policy")
def job_start(command: str, cwd: str | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Start a long-running local command in its own process group and return a job id; read output with job_output."""
    POLICY.check_command(command, approval_id=approval_id)
    return JOBS.start(command, _secure_cwd(cwd), principal=_principal_name(), owner=_owner_key())


@mcp.tool(title="List background jobs", annotations=READ)
@meta("loopback:read", "execution")
def job_list() -> list[dict[str, Any]]:
    """List your background jobs (running and recently finished), with their target machine."""
    return JOBS.list(owner=_owner_key())


@mcp.tool(title="Read job output", annotations=READ_NONIDEMPOTENT)
@meta("loopback:read", "execution")
def job_output(job_id: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
    """Read incremental output from a background job; pass next_offset back to fetch only new bytes."""
    result = JOBS.output(job_id, offset, max_bytes, owner=_owner_key())
    result["content"], hidden = redact_text(result.get("content", ""))
    if hidden:
        result["redacted_values"] = hidden
    return result


@mcp.tool(title="Stop background job", annotations=MUTATE_IDEMPOTENT)
@meta("loopback:exec", "execution")
def job_stop(job_id: str, signal_name: str = "TERM") -> dict[str, Any]:
    """Signal a Loopback background job's process group (TERM by default, KILL to force)."""
    POLICY.check_process_control()
    sig = getattr(signal, "SIG" + signal_name.upper(), None)
    if not isinstance(sig, signal.Signals):
        raise ValueError("unsupported signal")
    return JOBS.stop(job_id, int(sig), owner=_owner_key())


@mcp.tool(title="Start persistent terminal", annotations=CREATE)
@meta("loopback:exec", "execution")
def terminal_start(cwd: str | None = None, rows: int = 30, cols: int = 120) -> dict[str, Any]:
    """Start a persistent PTY shell (POSIX only) in the sandbox for interactive multi-step work. Starts in the home directory."""
    ok, reason = POLICY.shell_enabled("local")
    if not ok:
        raise PolicyError(reason or "terminal access is disabled by policy")
    return TERMINALS.start(_secure_cwd(cwd), rows=rows, cols=cols, principal=_principal_name(), owner=_owner_key())


@mcp.tool(title="Write terminal input", annotations=MUTATE)
@meta("loopback:exec", "execution", approval="policy")
def terminal_write(terminal_id: str, data: str, approval_id: str | None = None) -> dict[str, Any]:
    """Send keystrokes/text to a persistent terminal. Text input is checked against the command policy."""
    if data.strip():
        POLICY.check_command(data, approval_id=approval_id)
    return TERMINALS.write(terminal_id, data, owner=_owner_key())


@mcp.tool(title="Read terminal output", annotations=READ_NONIDEMPOTENT)
@meta("loopback:read", "execution")
def terminal_read(terminal_id: str, max_bytes: int = 131072, clear: bool = True) -> dict[str, Any]:
    """Read buffered output from a persistent terminal."""
    return TERMINALS.read(terminal_id, max_bytes=max_bytes, clear=clear, owner=_owner_key())


@mcp.tool(title="Resize terminal", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
@meta("loopback:exec", "execution")
def terminal_resize(terminal_id: str, rows: int = 30, cols: int = 120) -> dict[str, Any]:
    """Resize a persistent terminal PTY."""
    return TERMINALS.resize(terminal_id, rows, cols, owner=_owner_key())


@mcp.tool(title="Close terminal", annotations=MUTATE_IDEMPOTENT)
@meta("loopback:exec", "execution")
def terminal_close(terminal_id: str) -> dict[str, Any]:
    """Close a persistent terminal session and its shell."""
    POLICY.check_process_control()
    return TERMINALS.close(terminal_id, owner=_owner_key())


@mcp.tool(title="List processes", annotations=READ)
@meta("loopback:read", "execution")
def process_list(query: str = "", limit: int = 200) -> dict[str, Any]:
    """Structured process listing for the local machine, optionally filtered by substring."""
    if WINDOWS:
        proc = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], text=True, capture_output=True, timeout=15)
        rows = []
        for line in proc.stdout.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 5 and (not query or query.lower() in line.lower()):
                rows.append({"pid": int(parts[1]), "command": parts[0], "memory": parts[4]})
            if len(rows) >= max(1, min(int(limit), 2000)):
                break
        return {"returned": len(rows), "processes": rows, "target": "local"}
    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,user=,%cpu=,%mem=,stat=,etimes=,comm=,args="], text=True, capture_output=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ps failed")
    needle = query.lower().strip()
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 8)
        if len(parts) < 9:
            continue
        pid, ppid, user, cpu, mem, stat, etimes, comm, args = parts
        if needle and needle not in line.lower():
            continue
        rows.append({"pid": int(pid), "ppid": int(ppid), "user": user, "cpu_percent": float(cpu), "memory_percent": float(mem),
                     "state": stat, "elapsed_seconds": int(etimes), "command": comm, "args": args[:2000]})
        if len(rows) >= max(1, min(int(limit), 2000)):
            break
    return {"returned": len(rows), "processes": rows, "target": "local"}


@mcp.tool(title="Signal process", annotations=MUTATE)
@meta("loopback:exec", "execution")
def process_kill(pid: int, signal_name: str = "TERM", tree: bool = False) -> dict[str, Any]:
    """Send a signal to a local process (optionally its descendants first). Refuses PID 1 and the Loopback service itself."""
    POLICY.check_process_control()
    target = int(pid)
    if target in {1, os.getpid(), os.getppid()}:
        raise PolicyError("refusing to signal a protected Loopback/system process")
    sig = getattr(signal, "SIG" + signal_name.upper(), None)
    if not isinstance(sig, signal.Signals):
        raise ValueError("unsupported signal")
    killed: list[int] = []
    if tree and not WINDOWS:
        ps = subprocess.run(["ps", "-eo", "pid=,ppid="], text=True, capture_output=True, timeout=10)
        children: dict[int, list[int]] = {}
        for line in ps.stdout.splitlines():
            try:
                child_pid, parent_pid = [int(x) for x in line.split()[:2]]
            except (ValueError, IndexError):
                continue
            children.setdefault(parent_pid, []).append(child_pid)
        stack = list(children.get(target, []))
        descendants: list[int] = []
        while stack:
            child = stack.pop()
            descendants.append(child)
            stack.extend(children.get(child, []))
        for child in reversed(descendants):
            try:
                os.kill(child, sig)
                killed.append(child)
            except ProcessLookupError:
                pass
    os.kill(target, sig)
    killed.append(target)
    return {"signal": signal_name.upper(), "pids": killed, "target": "local"}


# ------------------------------------------------------------------ fleet
@mcp.tool(title="List Loopback nodes", annotations=READ)
@meta("loopback:fleet", "fleet", target="remote")
def node_list() -> list[dict[str, Any]]:
    """List the local gateway and registered remote Loopback nodes (MCP transport). Credentials are never returned."""
    POLICY.check_fleet()
    return [{"name": "local", "url": f"http://127.0.0.1:{CTX.config.port}/mcp", "enabled": True, "transport": "local"}] + [
        {**n, "transport": "mcp"} for n in FLEET.list()]


@mcp.tool(title="Call tool on Loopback node", annotations=MUTATE)
@meta("loopback:fleet", "fleet", target="remote", network=True, approval="policy")
async def node_call(node: str, tool: str, arguments: dict[str, Any] | None = None, timeout: int | None = None) -> dict[str, Any]:
    """Invoke an MCP tool on a registered remote Loopback node. The node enforces its own policy."""
    POLICY.check_fleet()
    if node == "local":
        raise ValueError("node_call is for remote nodes; call local tools directly")
    info, token = FLEET.get(node)
    if not info.get("enabled", True):
        raise PolicyError("node is disabled")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    limit = max(5, min(int(timeout or default_timeout()), 3600))
    async with httpx.AsyncClient(headers=headers, timeout=limit) as client:
        async with streamable_http_client(str(info["url"]), http_client=client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
                payload = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else {"result": str(result)}
                payload["target"] = node
                return payload


@mcp.tool(title="Read file on Loopback node", annotations=READ)
@meta("loopback:fleet", "fleet", target="remote", network=True)
async def node_read_file(node: str, path: str, start_line: int = 1, max_lines: int = 1000) -> dict[str, Any]:
    """Read a text file on a registered remote Loopback node."""
    return await node_call(node, "read_file", {"path": path, "start_line": start_line, "max_lines": max_lines})


@mcp.tool(title="Execute command on Loopback node", annotations=MUTATE)
@meta("loopback:fleet", "fleet", target="remote", network=True, approval="policy")
async def node_execute(node: str, command: str, cwd: str | None = None, timeout: int | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Run a command on a registered remote Loopback node using that node's own shell policy and sandbox."""
    args: dict[str, Any] = {"command": command, "timeout": timeout or default_timeout()}
    if cwd is not None:
        args["cwd"] = cwd
    if approval_id is not None:
        args["approval_id"] = approval_id
    return await node_call(node, "execute", args, timeout=timeout)


@mcp.tool(title="Node diagnostics", annotations=READ)
@meta("loopback:fleet", "fleet", target="remote", network=True)
async def node_diagnostics(node: str, timeout: int = 30) -> dict[str, Any]:
    """Return diagnostics from a registered remote Loopback node."""
    return await node_call(node, "diagnostics", {}, timeout=timeout)


@mcp.tool(title="List SSH targets", annotations=READ)
@meta("loopback:fleet", "fleet", target="remote")
def ssh_target_list() -> list[dict[str, Any]]:
    """List managed SSH machines (name, host, user, kind, last probe). Keys are never returned. These are remote targets."""
    POLICY.check_fleet()
    if CTX.ssh is None:
        return []
    return CTX.ssh.list()


@mcp.tool(title="Execute command over SSH", annotations=MUTATE)
@meta("loopback:fleet", "fleet", target="remote", network=True, approval="policy")
async def ssh_execute(target: str, command: str, cwd: str | None = None, timeout: int | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Run a command on a managed SSH target (remote machine). The gateway checks the target's command and path policy first;
    the remote host itself has no Loopback sandbox unless it runs Loopback."""
    POLICY.check_fleet()
    if CTX.ssh is None:
        raise RuntimeError("SSH targets module is unavailable on this gateway")
    return await CTX.ssh.execute(target, command, cwd=cwd, timeout=int(timeout or default_timeout()), approval_id=approval_id,
                                 max_output=max_output())


# -------------------------------------------------------------- documents
@mcp.tool(title="Read Word document", annotations=READ)
@meta("loopback:read", "documents")
def docx_text(path: str) -> dict[str, Any]:
    """Extract paragraphs and table text from a local DOCX file."""
    return CTX.documents.docx_text(POLICY.check_path(path, write=False))


@mcp.tool(title="Replace Word text", annotations=MUTATE)
@meta("loopback:write", "documents")
def docx_replace_text(path: str, old_text: str, new_text: str, output_path: str | None = None) -> dict[str, Any]:
    """Replace text in a DOCX and save to output_path (defaults to a new .loopback.docx copy)."""
    src = POLICY.check_path(path, write=False)
    dst = POLICY.check_path(output_path or str(src.with_name(src.stem + ".loopback.docx")), write=True)
    return CTX.documents.docx_replace_text(src, old_text, new_text, dst)


@mcp.tool(title="Read Excel range", annotations=READ)
@meta("loopback:read", "documents")
def xlsx_read_range(path: str, sheet: str, cell_range: str) -> dict[str, Any]:
    """Read values/formulas from a rectangular XLSX range (e.g. A1:C10)."""
    return CTX.documents.xlsx_read_range(POLICY.check_path(path, write=False), sheet, cell_range)


@mcp.tool(title="Write Excel range", annotations=MUTATE)
@meta("loopback:write", "documents")
def xlsx_write_range(path: str, sheet: str, start_cell: str, values: list[list[Any]], output_path: str | None = None) -> dict[str, Any]:
    """Write a 2-D array into an XLSX sheet starting at start_cell and save to output_path (new copy by default)."""
    src = POLICY.check_path(path, write=False)
    dst = POLICY.check_path(output_path or str(src.with_name(src.stem + ".loopback.xlsx")), write=True)
    return CTX.documents.xlsx_write_range(src, sheet, start_cell, values, dst)


@mcp.tool(title="Read PDF text", annotations=READ)
@meta("loopback:read", "documents")
def pdf_text(path: str, start_page: int = 1, max_pages: int = 20) -> dict[str, Any]:
    """Extract text from a bounded PDF page range."""
    return CTX.documents.pdf_text(POLICY.check_path(path, write=False), start_page, max_pages)


@mcp.tool(title="Merge PDFs", annotations=CREATE)
@meta("loopback:write", "documents")
def pdf_merge(paths: list[str], output_path: str) -> dict[str, Any]:
    """Merge PDF files in order into a new output PDF."""
    srcs = [POLICY.check_path(p, write=False) for p in paths]
    return CTX.documents.pdf_merge(srcs, POLICY.check_path(output_path, write=True))


@mcp.tool(title="Extract PDF pages", annotations=CREATE)
@meta("loopback:write", "documents")
def pdf_extract_pages(path: str, pages: list[int], output_path: str) -> dict[str, Any]:
    """Create a new PDF containing the selected 1-based pages."""
    return CTX.documents.pdf_extract_pages(POLICY.check_path(path, write=False), pages, POLICY.check_path(output_path, write=True))


# ---------------------------------------------------------------- browser
@mcp.tool(title="Browser adapter status", annotations=READ)
@meta("loopback:read", "browser")
def browser_status() -> dict[str, Any]:
    """Report whether the optional structured browser adapter is installed and enabled by policy."""
    return {**CTX.browser.status(), "enabled_by_policy": bool(POLICY.data.get("allow_browser", False))}


@mcp.tool(title="Open browser URL", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True))
@meta("loopback:exec", "browser", network=True)
def browser_open(url: str, session: str = "loopback") -> dict[str, Any]:
    """Open a public HTTPS URL (or http://localhost) in your own browser automation session (opt-in capability).

    Private, metadata, credential-bearing and ambiguous URLs are rejected; redirects are re-checked."""
    POLICY.check_browser()
    return CTX.browser.open(url, session, owner=_owner_key())


@mcp.tool(title="Inspect browser page", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=True))
@meta("loopback:exec", "browser", network=True)
def browser_snapshot(session: str = "loopback", interactive_only: bool = True) -> dict[str, Any]:
    """Accessibility snapshot with element references from the optional browser session."""
    POLICY.check_browser()
    return CTX.browser.snapshot(session, interactive_only, owner=_owner_key())


@mcp.tool(title="Click browser element", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True))
@meta("loopback:exec", "browser", network=True)
def browser_click(ref: str, session: str = "loopback") -> dict[str, Any]:
    """Click one element reference returned by browser_snapshot."""
    POLICY.check_browser()
    return CTX.browser.click(ref, session, owner=_owner_key())


@mcp.tool(title="Fill browser field", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True))
@meta("loopback:exec", "browser", network=True)
def browser_fill(ref: str, text: str, session: str = "loopback") -> dict[str, Any]:
    """Fill one browser field reference with text."""
    POLICY.check_browser()
    return CTX.browser.fill(ref, text, session, owner=_owner_key())


@mcp.tool(title="Get browser URL", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True))
@meta("loopback:read", "browser", network=True)
def browser_get_url(session: str = "loopback") -> dict[str, Any]:
    """Current URL of a browser automation session."""
    POLICY.check_browser()
    return CTX.browser.url(session, owner=_owner_key())


@mcp.tool(title="Close browser session", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True))
@meta("loopback:exec", "browser")
def browser_close(session: str = "loopback") -> dict[str, Any]:
    """Close an optional browser automation session."""
    POLICY.check_browser()
    return CTX.browser.close(session, owner=_owner_key())


# ---------------------------------------------------------------- desktop
@mcp.tool(title="Native desktop status", annotations=READ)
@meta("loopback:read", "desktop")
def desktop_status() -> dict[str, Any]:
    """Report whether native desktop automation (Linux/X11, opt-in) is available and enabled."""
    return {**CTX.desktop.status(), "enabled_by_policy": bool(POLICY.data.get("allow_desktop", False))}


@mcp.tool(title="List desktop windows", annotations=READ_NONIDEMPOTENT)
@meta("loopback:exec", "desktop")
def desktop_windows(limit: int = 200) -> dict[str, Any]:
    """List visible desktop windows on a Linux/X11 machine."""
    POLICY.check_desktop()
    return CTX.desktop.windows(limit)


@mcp.tool(title="Capture desktop screenshot", annotations=READ_NONIDEMPOTENT)
@meta("loopback:exec", "desktop")
def desktop_screenshot(output_path: str | None = None) -> Image:
    """Capture the desktop and return it as MCP image content. Nothing is saved unless output_path is given."""
    POLICY.check_desktop()
    if output_path:
        dst = POLICY.check_path(output_path, write=True)
        return Image(path=CTX.desktop.screenshot(dst))
    # Without an explicit output path, screen pixels are not persisted (H22): the capture
    # lives in a private temporary directory only long enough to return it.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="loopback-shot-") as tmp:
        data = CTX.desktop.screenshot(Path(tmp) / "desktop.png").read_bytes()
    return Image(data=data, format="png")


@mcp.tool(title="Activate desktop window", annotations=CREATE)
@meta("loopback:exec", "desktop")
def desktop_activate_window(window_id: str) -> dict[str, Any]:
    """Activate one visible desktop window by its X11 window id."""
    POLICY.check_desktop()
    return CTX.desktop.activate_window(window_id)


@mcp.tool(title="Click desktop coordinates", annotations=MUTATE)
@meta("loopback:exec", "desktop")
def desktop_click(x: int, y: int, button: int = 1) -> dict[str, Any]:
    """Move the pointer and click desktop coordinates."""
    POLICY.check_desktop()
    return CTX.desktop.click(x, y, button)


@mcp.tool(title="Type desktop text", annotations=MUTATE)
@meta("loopback:exec", "desktop")
def desktop_type(text: str, delay_ms: int = 10) -> dict[str, Any]:
    """Type text into the currently focused desktop application."""
    POLICY.check_desktop()
    return CTX.desktop.type_text(text, delay_ms)


@mcp.tool(title="Send desktop key", annotations=MUTATE)
@meta("loopback:exec", "desktop")
def desktop_key(keys: str) -> dict[str, Any]:
    """Send a constrained xdotool key expression to the focused application."""
    POLICY.check_desktop()
    return CTX.desktop.key(keys)


# ---------------------------------------------------------------------------
# Tool catalog for the dashboard
# ---------------------------------------------------------------------------
def _hint(ann: Any, snake: str, default: bool) -> bool:
    if ann is None:
        return default
    camel = "".join(part.capitalize() if i else part for i, part in enumerate(snake.split("_")))
    value = getattr(ann, snake, None)
    if value is None:
        value = getattr(ann, camel, None)
    return default if value is None else bool(value)


def tool_enabled(name: str) -> bool:
    """One answer for the dashboard catalog, tools/list visibility and tools/call enforcement."""
    denied = set(POLICY.data.get("denied_tools") or [])
    allowed = set(POLICY.data.get("allowed_tools") or ["*"])
    m = TOOL_META.get(name, {"category": "other"})
    enabled = name not in denied and "*" not in denied and ("*" in allowed or name in allowed)
    if m["category"] == "browser" and not POLICY.data.get("allow_browser") and name != "browser_status":
        enabled = False
    if m["category"] == "desktop" and not POLICY.data.get("allow_desktop") and name != "desktop_status":
        enabled = False
    return enabled


def tool_catalog() -> list[dict[str, Any]]:
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", {}) if manager else {}
    out = []
    for name, tool in sorted(tools.items()):
        ann = getattr(tool, "annotations", None)
        m = TOOL_META.get(name, {"scope": "loopback:exec", "category": "other", "target": "local", "network": False, "approval": "none"})
        enabled = tool_enabled(name)
        out.append({
            "name": name,
            "title": getattr(tool, "title", None) or name,
            "description": (getattr(tool, "description", "") or "").split("\n")[0][:300],
            "annotations": {
                "readOnlyHint": _hint(ann, "read_only_hint", False),
                "destructiveHint": _hint(ann, "destructive_hint", True),
                "idempotentHint": _hint(ann, "idempotent_hint", False),
                "openWorldHint": _hint(ann, "open_world_hint", False),
            },
            **m,
            "enabled": enabled,
        })
    return out


CTX.tool_catalog = tool_catalog
CTX.tool_enabled = tool_enabled

_unfiltered_list_tools = mcp.list_tools


async def _policy_filtered_list_tools():
    """Disabled tools are hidden from tools/list, not only refused at tools/call."""
    tools = []
    for tool in await _unfiltered_list_tools():
        if tool_enabled(tool.name):
            required = TOOL_META.get(tool.name, {}).get("scope", "loopback:exec")
            schemes = [{"type": "oauth2", "scopes": [required]}]
            tools.append(tool.model_copy(update={"meta": {**(tool.meta or {}), "securitySchemes": schemes}}))
    return tools


mcp.list_tools = _policy_filtered_list_tools  # _handle_list_tools resolves self.list_tools at call time


# ---------------------------------------------------------------------------
# ASGI gates
# ---------------------------------------------------------------------------
def _headers(scope) -> dict[str, str]:
    return {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}


class TokenGate:
    """Authenticate MCP requests (OAuth access token, API token, or — if enabled — the legacy machine token)."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.wrapped(scope, receive, send)
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if method == "OPTIONS" or not (path == "/mcp" or path.startswith("/mcp/")):
            return await self.wrapped(scope, receive, send)
        h = _headers(scope)
        principal = AUTH.authenticate(h.get("authorization"), h.get("x-api-key") or h.get("api-key"),
                                      legacy_machine_token=CTX.legacy_mcp_token_enabled())
        if principal is None:
            request = Request(scope)
            supplied = bool(h.get("authorization") or h.get("x-api-key") or h.get("api-key"))
            error = "invalid_token" if supplied else None
            AUDIT.record("mcp.auth", "unauthorized", detail={"user_agent": h.get("user-agent", "")[:200],
                                                          "credential_supplied": supplied, "error": error})
            response = auth_http.unauthorized(CTX, request, error=error,
                                              description="a valid OAuth access token or Loopback API token is required")
            return await response(scope, receive, send)
        scope.setdefault("state", {})["principal"] = principal
        token = CURRENT_PRINCIPAL.set(principal)
        try:
            return await self.wrapped(scope, receive, send)
        finally:
            CURRENT_PRINCIPAL.reset(token)


class McpGate:
    """Per-tool policy + scope enforcement and audit at the JSON-RPC boundary."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in {"/mcp", "/mcp/"} or scope.get("method") != "POST":
            return await self.wrapped(scope, receive, send)
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more = bool(message.get("more_body", False))
        body = b"".join(chunks)
        used = False

        async def replay_receive():
            nonlocal used
            if not used:
                used = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        principal: Principal | None = scope.get("state", {}).get("principal")
        tool = "mcp"
        target = None
        payload_id = None
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            payload_id = payload.get("id")
            method = str(payload.get("method") or "mcp")
            tool = method
            params = payload.get("params") or {}
            if method == "tools/call" and isinstance(params, dict):
                target = str(params.get("name") or "") or None
                tool = target or method
                try:
                    POLICY.check_tool(tool)
                except PolicyError as exc:
                    AUDIT.record(tool, "policy_denied", target=target, principal=principal.display if principal else None,
                                 principal_kind=principal.kind if principal else None)
                    response = JSONResponse({"jsonrpc": "2.0", "id": payload_id, "error": {"code": -32001, "message": str(exc)}}, status_code=200)
                    return await response(scope, receive, send)
                required = TOOL_META.get(tool, {}).get("scope", "loopback:exec")
                if principal is not None and not principal.has(required):
                    AUDIT.record(tool, "insufficient_scope", target=target, principal=principal.display, principal_kind=principal.kind,
                                 detail={"required_scope": required, "granted_scopes": sorted(principal.scopes)})
                    request = Request(scope)
                    description = f"tool {tool} requires scope {required}"
                    if principal.kind == "oauth":
                        challenge = auth_http.www_authenticate(CTX, request, error="insufficient_scope",
                                                               scope=required, description=description)
                        response = JSONResponse({"jsonrpc": "2.0", "id": payload_id, "result": {
                            "isError": True, "content": [{"type": "text", "text": description}],
                            "_meta": {"mcp/www_authenticate": [challenge]},
                        }}, headers=auth_http.NO_STORE)
                    else:
                        response = auth_http.forbidden_scope(CTX, request, required, description)
                    return await response(scope, receive, send)

        status_code = 200

        async def wrapped_send(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 200))
            await send(message)

        started = time.time()
        error = None
        try:
            await self.wrapped(scope, replay_receive, wrapped_send)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            h = _headers(scope)
            AUDIT.record(tool, "ok" if error is None and status_code < 400 else (error or f"http_{status_code}"),
                         duration_ms=int((time.time() - started) * 1000), target=target,
                         detail={"user_agent": h.get("user-agent", "")[:200]},
                         principal=principal.display if principal else None, principal_kind=principal.kind if principal else None)


class AdminGate:
    """Session + CSRF protection for /api/admin/*."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.wrapped(scope, receive, send)
        path = scope.get("path", "")
        if not path.startswith("/api/admin/") or path == "/api/admin/session":
            return await self.wrapped(scope, receive, send)
        request = Request(scope)
        session = auth_http.session_from_request(CTX, request)
        if not session:
            response = JSONResponse({"detail": "admin authentication required"}, status_code=401, headers=auth_http.NO_STORE)
            return await response(scope, receive, send)
        if scope.get("method", "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
            if not auth_http.csrf_ok(session, request):
                AUTH.record_event("csrf_rejected", "session", auth_http.client_ip(request),
                                  {"path": path, "origin": request.headers.get("origin", "")[:200], "host": request.headers.get("host", "")[:200],
                                   "sec_fetch_site": request.headers.get("sec-fetch-site", ""), "csrf_header": bool(request.headers.get("x-loopback-csrf"))})
                response = JSONResponse({"detail": "CSRF validation failed"}, status_code=403, headers=auth_http.NO_STORE)
                return await response(scope, receive, send)
            # Defence in depth: compare Origin with the host this request arrived on. The token above is
            # the real barrier (unobtainable cross-site); a mismatch here is audited, not fatal, because
            # tunnels and reverse proxies legitimately rewrite Host without forwarding the public name.
            origin = request.headers.get("origin")
            if origin:
                from urllib.parse import urlparse

                origin_host = (urlparse(origin).netloc or "").lower()
                hosts = {(request.headers.get("host") or "").lower()}
                forwarded = request.headers.get("x-forwarded-host")
                if forwarded:
                    hosts.add(forwarded.split(",")[0].strip().lower())
                if origin_host not in hosts and origin.rstrip("/") not in CTX.auth.base_urls \
                        and request.headers.get("sec-fetch-site") != "same-origin":
                    AUTH.record_event("csrf_origin_mismatch", "session", auth_http.client_ip(request),
                                      {"path": path, "origin": origin[:200], "host": request.headers.get("host", "")[:200]})
        scope.setdefault("state", {})["admin_session"] = session
        return await self.wrapped(scope, receive, send)


# ---------------------------------------------------------------------------
# Application assembly
# ---------------------------------------------------------------------------
def _allowed_origins() -> list[str]:
    origins = [f"https://{h}" for h in CTX.config.public_hosts()]
    origins += list(CTX.config.data.get("auth", {}).get("allowed_origins", []))
    origins += [f"http://127.0.0.1:{CTX.config.port}", f"http://localhost:{CTX.config.port}"]
    return list(dict.fromkeys(origins))


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[*(h for host in CTX.config.public_hosts() for h in (host, f"{host}:*")), "127.0.0.1:*", "localhost:*", "[::1]:*"],
    allowed_origins=[*_allowed_origins(), "http://127.0.0.1:*", "http://localhost:*", *(f"https://{h}:*" for h in CTX.config.public_hosts())],
)

inner = mcp.streamable_http_app(transport_security=security, stateless_http=True, json_response=True)

mcp_chain = TokenGate(
    CORSMiddleware(
        McpGate(inner),
        allow_origins=_allowed_origins(),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
    )
)


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "service": "loopback", "version": VERSION, "policy_profile": POLICY.profile,
                         "uptime_seconds": int(time.time() - STARTED_AT), "sandbox": CTX.sandbox.backend.name})


def _optional_routes(module_name: str) -> list:
    try:
        module = __import__(module_name)
    except ImportError:
        return []
    try:
        return list(module.routes(CTX))
    except Exception as exc:  # pragma: no cover - a broken optional module must not take the gateway down
        AUDIT.record("startup", "module_error", target=module_name, detail={"error": str(exc)[:300]})
        return []


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    stop = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []
    async with inner.router.lifespan_context(inner):
        if CTX.ingress is not None and hasattr(CTX.ingress, "ensure_started"):
            try:
                await asyncio.to_thread(CTX.ingress.ensure_started)
            except Exception as exc:
                AUDIT.record("ingress.start", "error", detail={"error": str(exc)[:300]})
        if CTX.maintenance is not None and hasattr(CTX.maintenance, "run_forever"):
            tasks.append(asyncio.create_task(CTX.maintenance.run_forever(stop)))
        AUDIT.record("startup", "ok", detail={"version": VERSION, "sandbox": CTX.sandbox.backend.name})
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if TERMINALS is not None:
                TERMINALS.close_all()
            AUDIT.record("shutdown", "ok")


_routes: list = []
_routes += auth_http.routes(CTX)
_routes += admin_api.routes(CTX)
_routes += _optional_routes("fabric_api")
_routes += _optional_routes("maintenance_api")
_routes += _optional_routes("dashboard")
_routes += _optional_routes("shield")
_routes += _optional_routes("discovery")
_routes += [Route("/healthz", healthz, methods=["GET"]), Mount("/", app=mcp_chain)]

app = AdminGate(Starlette(routes=_routes, lifespan=lifespan))
try:
    import shield as _shield_mod

    app = _shield_mod.Shield(app, CTX)  # sets CTX.shield; outermost so it sees every request
except Exception as exc:  # pragma: no cover - endpoint protection is optional and must never block startup
    AUDIT.record("startup", "module_error", target="shield", detail={"error": str(exc)[:300]})
