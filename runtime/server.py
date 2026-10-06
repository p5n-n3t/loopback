from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import urllib.parse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from audit import AuditStore
from browser import BrowserAdapter
from dashboard import render_dashboard, render_login
from documents import DocumentTools
from fleet import FleetStore
from jobs import JobManager, TerminalManager
from policy import PolicyError, PolicyStore


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
STARTED_AT = time.time()

POLICY = PolicyStore(CFG)
if not POLICY.path.exists() and ACCESS_MODE in {"standard", "trusted", "read-only", "locked"}:
    POLICY.data["profile"] = ACCESS_MODE
AUDIT = AuditStore(CFG)
JOBS = JobManager(CFG, SHELL)
TERMINALS = TerminalManager(SHELL)
FLEET = FleetStore(CFG)
BROWSER = BrowserAdapter()
DOCUMENTS = DocumentTools()
_ADMIN_SESSIONS: dict[str, float] = {}


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



@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def read_file(
    path: str,
    start_line: int = 1,
    max_lines: int = 1000,
) -> dict[str, Any]:
    """Read a text file with line-range controls."""

    p = POLICY.check_path(path, write=False)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def write_file(
    path: str,
    content: str,
    append: bool = False,
    create_parents: bool = True,
) -> dict[str, Any]:
    """Write or append UTF-8 text as the current user."""

    p = POLICY.check_path(path, write=True)

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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def list_dir(
    path: str = "~",
    max_entries: int = 1000,
) -> dict[str, Any]:
    """List a directory with simple metadata."""

    p = POLICY.check_path(path, write=False)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def patch_file(
    path: str,
    old_text: str,
    new_text: str,
    count: int = 1,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Replace exact text in a file, optionally keeping a timestamped backup."""
    p = POLICY.check_path(path, write=True)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def search_text(
    query: str,
    path: str = "~",
    glob: str = "*",
    max_results: int = 200,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search text recursively while skipping dependency/cache directories."""
    import fnmatch
    root = POLICY.check_path(path, write=False)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def tail_file(path: str, lines: int = 200) -> dict[str, Any]:
    """Return the last N lines of a text or log file."""
    from collections import deque
    p = POLICY.check_path(path, write=False)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
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
# Loopback v2: execution, jobs, terminals, processes, policy and fleet routing.
# ---------------------------------------------------------------------------

def _secure_cwd(value: str | None) -> str:
    p = POLICY.check_path(value or "~", write=False)
    if not p.is_dir():
        raise ValueError(f"cwd is not a directory: {p}")
    return str(p)


@mcp.tool(
    title="Execute command",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def execute(
    command: str,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Run a shell command with bounded output and Loopback policy enforcement."""
    POLICY.check_command(command, approval_id=approval_id)
    workdir = _secure_cwd(cwd)
    limit = max(1, min(int(timeout), 3600))
    started = time.time()
    try:
        proc = subprocess.run(
            [SHELL, "-lc", command],
            cwd=workdir,
            text=True,
            capture_output=True,
            timeout=limit,
        )
        stdout, out_trunc = clip(proc.stdout)
        stderr, err_trunc = clip(proc.stderr)
        return {
            "command": command,
            "cwd": workdir,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
            "duration_ms": int((time.time() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "command": command,
            "cwd": workdir,
            "returncode": None,
            "stdout": clip(out)[0],
            "stderr": clip(err)[0],
            "timed_out": True,
            "duration_ms": int((time.time() - started) * 1000),
        }


@mcp.tool(title="Start background job", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
def job_start(command: str, cwd: str | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Start a long-running command and return immediately with a job ID."""
    POLICY.check_command(command, approval_id=approval_id)
    return JOBS.start(command, _secure_cwd(cwd))


@mcp.tool(title="List background jobs", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def job_list() -> list[dict[str, Any]]:
    """List background jobs started by Loopback."""
    return JOBS.list()


@mcp.tool(title="Read job output", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=False))
def job_output(job_id: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
    """Read incremental output from a background job."""
    return JOBS.output(job_id, offset, max_bytes)


@mcp.tool(title="Stop background job", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False))
def job_stop(job_id: str, signal_name: str = "TERM") -> dict[str, Any]:
    """Stop a Loopback background job and its process group."""
    POLICY.check_process_control()
    sig = getattr(signal, "SIG" + signal_name.upper(), None)
    if not isinstance(sig, signal.Signals):
        raise ValueError("unsupported signal")
    return JOBS.stop(job_id, int(sig))


@mcp.tool(title="Start persistent terminal", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
def terminal_start(cwd: str | None = None, rows: int = 30, cols: int = 120) -> dict[str, Any]:
    """Start a persistent PTY shell for interactive or long-running workflows."""
    if POLICY.profile in {"locked", "read-only"} or not POLICY.data.get("allow_shell", True):
        raise PolicyError("terminal access is disabled by policy")
    return TERMINALS.start(_secure_cwd(cwd), rows=rows, cols=cols)


@mcp.tool(title="Write terminal input", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def terminal_write(terminal_id: str, data: str, approval_id: str | None = None) -> dict[str, Any]:
    """Write keystrokes/text to a persistent terminal."""
    if data.strip():
        POLICY.check_command(data, approval_id=approval_id)
    return TERMINALS.write(terminal_id, data)


@mcp.tool(title="Read terminal output", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=False))
def terminal_read(terminal_id: str, max_bytes: int = 131072, clear: bool = True) -> dict[str, Any]:
    """Read buffered output from a persistent terminal."""
    return TERMINALS.read(terminal_id, max_bytes=max_bytes, clear=clear)


@mcp.tool(title="Resize terminal", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def terminal_resize(terminal_id: str, rows: int = 30, cols: int = 120) -> dict[str, Any]:
    """Resize a persistent terminal PTY."""
    return TERMINALS.resize(terminal_id, rows, cols)


@mcp.tool(title="Close terminal", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False))
def terminal_close(terminal_id: str) -> dict[str, Any]:
    """Close a persistent terminal session."""
    POLICY.check_process_control()
    return TERMINALS.close(terminal_id)


@mcp.tool(title="List processes", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def process_list(query: str = "", limit: int = 200) -> dict[str, Any]:
    """Return structured process information, optionally filtered by text."""
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,user=,%cpu=,%mem=,stat=,etimes=,comm=,args="],
        text=True,
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ps failed")
    needle = query.lower().strip()
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 8)
        if len(parts) < 9:
            continue
        pid, ppid, user, cpu, mem, stat, etimes, comm, args = parts
        if needle and needle not in line.lower():
            continue
        rows.append({
            "pid": int(pid),
            "ppid": int(ppid),
            "user": user,
            "cpu_percent": float(cpu),
            "memory_percent": float(mem),
            "state": stat,
            "elapsed_seconds": int(etimes),
            "command": comm,
            "args": args,
        })
        if len(rows) >= max(1, min(int(limit), 2000)):
            break
    return {"returned": len(rows), "processes": rows}


@mcp.tool(title="Signal process", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def process_kill(pid: int, signal_name: str = "TERM", tree: bool = False) -> dict[str, Any]:
    """Send a signal to a process, optionally signaling descendants first."""
    POLICY.check_process_control()
    target = int(pid)
    if target in {1, os.getpid(), os.getppid()}:
        raise PolicyError("refusing to signal a protected Loopback/system process")
    sig = getattr(signal, "SIG" + signal_name.upper(), None)
    if not isinstance(sig, signal.Signals):
        raise ValueError("unsupported signal")
    killed: list[int] = []
    if tree:
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
    return {"signal": signal_name.upper(), "pids": killed}


@mcp.tool(title="Loopback policy status", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def policy_status() -> dict[str, Any]:
    """Show the effective Loopback security profile without exposing secrets."""
    return {
        "profile": POLICY.profile,
        "allowed_roots": POLICY.data.get("allowed_roots", []),
        "denied_roots": POLICY.data.get("denied_roots", []),
        "allow_shell": POLICY.data.get("allow_shell", True),
        "allow_process_control": POLICY.data.get("allow_process_control", True),
        "allow_fleet": POLICY.data.get("allow_fleet", True),
        "pending_approvals": len(POLICY.pending()),
    }


@mcp.tool(title="List Loopback nodes", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def node_list() -> list[dict[str, Any]]:
    """List configured remote Loopback nodes. Credentials are never returned."""
    POLICY.check_fleet()
    return [{"name": "local", "url": f"http://127.0.0.1:{LOCAL_PORT}/mcp", "enabled": True}] + FLEET.list()


@mcp.tool(title="Call tool on Loopback node", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
async def node_call(node: str, tool: str, arguments: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Call an MCP tool on another registered Loopback node through one gateway."""
    POLICY.check_fleet()
    if node == "local":
        raise ValueError("node_call is for remote nodes; call local tools directly")
    info, token = FLEET.get(node)
    if not info.get("enabled", True):
        raise PolicyError("node is disabled")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, timeout=max(5, min(int(timeout), 3600))) as client:
        async with streamable_http_client(str(info["url"]), http_client=client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
                if hasattr(result, "model_dump"):
                    return result.model_dump(by_alias=True, exclude_none=True)
                return {"result": str(result)}


@mcp.tool(title="Read file on Loopback node", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
async def node_read_file(
    node: str,
    path: str,
    start_line: int = 1,
    max_lines: int = 1000,
) -> dict[str, Any]:
    """Read a text file on a registered Loopback node."""
    return await node_call(
        node,
        "read_file",
        {"path": path, "start_line": start_line, "max_lines": max_lines},
    )


@mcp.tool(title="Execute command on Loopback node", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
async def node_execute(
    node: str,
    command: str,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Run a command on a registered Loopback node using that node's own policy."""
    args: dict[str, Any] = {
        "command": command,
        "timeout": timeout,
    }
    if cwd is not None:
        args["cwd"] = cwd
    if approval_id is not None:
        args["approval_id"] = approval_id
    return await node_call(node, "execute", args, timeout=timeout)


@mcp.tool(title="Node diagnostics", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
async def node_diagnostics(node: str, timeout: int = 30) -> dict[str, Any]:
    """Return diagnostics from a registered Loopback node."""
    return await node_call(node, "diagnostics", {}, timeout=timeout)






# ---------------------------------------------------------------------------
# Optional browser and document capabilities.
# ---------------------------------------------------------------------------

@mcp.tool(title="Browser adapter status", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def browser_status() -> dict[str, Any]:
    """Report whether the optional structured browser adapter is available."""
    return {**BROWSER.status(), "enabled_by_policy": bool(POLICY.data.get("allow_browser", False))}


@mcp.tool(title="Open browser URL", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True))
def browser_open(url: str, session: str = "loopback") -> dict[str, Any]:
    """Open an HTTPS URL in the optional browser automation session."""
    POLICY.check_browser()
    return BROWSER.open(url, session)


@mcp.tool(title="Inspect browser page", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=True))
def browser_snapshot(session: str = "loopback", interactive_only: bool = True) -> dict[str, Any]:
    """Return an accessibility snapshot with element references from the browser."""
    POLICY.check_browser()
    return BROWSER.snapshot(session, interactive_only)


@mcp.tool(title="Click browser element", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True))
def browser_click(ref: str, session: str = "loopback") -> dict[str, Any]:
    """Click one element reference returned by browser_snapshot."""
    POLICY.check_browser()
    return BROWSER.click(ref, session)


@mcp.tool(title="Fill browser field", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True))
def browser_fill(ref: str, text: str, session: str = "loopback") -> dict[str, Any]:
    """Fill one browser field reference with text."""
    POLICY.check_browser()
    return BROWSER.fill(ref, text, session)


@mcp.tool(title="Get browser URL", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True))
def browser_get_url(session: str = "loopback") -> dict[str, Any]:
    """Return the current URL for a browser automation session."""
    POLICY.check_browser()
    return BROWSER.url(session)


@mcp.tool(title="Close browser session", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True))
def browser_close(session: str = "loopback") -> dict[str, Any]:
    """Close an optional browser automation session."""
    POLICY.check_browser()
    return BROWSER.close(session)


@mcp.tool(title="Read Word document", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def docx_text(path: str) -> dict[str, Any]:
    """Extract paragraphs and table text from a DOCX file."""
    return DOCUMENTS.docx_text(POLICY.check_path(path, write=False))


@mcp.tool(title="Replace Word text", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def docx_replace_text(path: str, old_text: str, new_text: str, output_path: str | None = None) -> dict[str, Any]:
    """Replace text in a DOCX and save a new or explicitly selected output file."""
    src = POLICY.check_path(path, write=False)
    dst = POLICY.check_path(output_path or str(src.with_name(src.stem + ".loopback.docx")), write=True)
    return DOCUMENTS.docx_replace_text(src, old_text, new_text, dst)


@mcp.tool(title="Read Excel range", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def xlsx_read_range(path: str, sheet: str, cell_range: str) -> dict[str, Any]:
    """Read values/formulas from a rectangular XLSX range."""
    return DOCUMENTS.xlsx_read_range(POLICY.check_path(path, write=False), sheet, cell_range)


@mcp.tool(title="Write Excel range", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def xlsx_write_range(
    path: str,
    sheet: str,
    start_cell: str,
    values: list[list[Any]],
    output_path: str | None = None,
) -> dict[str, Any]:
    """Write a 2D value array to an XLSX range and save an output workbook."""
    src = POLICY.check_path(path, write=False)
    dst = POLICY.check_path(output_path or str(src.with_name(src.stem + ".loopback.xlsx")), write=True)
    return DOCUMENTS.xlsx_write_range(src, sheet, start_cell, values, dst)


@mcp.tool(title="Read PDF text", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def pdf_text(path: str, start_page: int = 1, max_pages: int = 20) -> dict[str, Any]:
    """Extract text from a bounded PDF page range."""
    return DOCUMENTS.pdf_text(POLICY.check_path(path, write=False), start_page, max_pages)


@mcp.tool(title="Merge PDFs", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
def pdf_merge(paths: list[str], output_path: str) -> dict[str, Any]:
    """Merge PDF files in order into a new output PDF."""
    srcs = [POLICY.check_path(path, write=False) for path in paths]
    dst = POLICY.check_path(output_path, write=True)
    return DOCUMENTS.pdf_merge(srcs, dst)


@mcp.tool(title="Extract PDF pages", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
def pdf_extract_pages(path: str, pages: list[int], output_path: str) -> dict[str, Any]:
    """Create a new PDF containing selected 1-based pages."""
    src = POLICY.check_path(path, write=False)
    dst = POLICY.check_path(output_path, write=True)
    return DOCUMENTS.pdf_extract_pages(src, pages, dst)


# ---------------------------------------------------------------------------
# OAuth 2.1-style authorization-code + PKCE facade.
#
# Direct MCP clients may keep using the machine bearer token. OAuth clients
# receive short-lived signed access tokens instead, so the permanent machine
# secret is never handed to ChatGPT or another OAuth client.
# ---------------------------------------------------------------------------

_OAUTH_CLIENTS: dict[str, dict[str, Any]] = {}
_OAUTH_CODES: dict[str, dict[str, Any]] = {}
_CODE_TTL_SECONDS = 300
_ACCESS_TTL_SECONDS = 3600
_REFRESH_TTL_SECONDS = 30 * 24 * 3600
_OAUTH_SIGNING_KEY = hashlib.sha256(("loopback-oauth:" + TOKEN).encode()).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _oauth_base() -> str:
    return f"https://{PUBLIC_HOST}"


def _oauth_resource() -> str:
    return f"{_oauth_base()}/mcp"


def _redirect_uri_allowed(uri: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment or parsed.username or parsed.password:
        return False
    if parsed.scheme == "https" and bool(parsed.netloc):
        return True
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    return False


def _issue_oauth_token(kind: str, client_id: str, resource: str, ttl: int) -> str:
    now = int(time.time())
    claims = {
        "iss": _oauth_base(),
        "sub": client_id,
        "aud": resource,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(12),
        "typ": kind,
        "scope": "loopback",
        "client_id": client_id,
    }
    return jwt.encode(claims, _OAUTH_SIGNING_KEY, algorithm="HS256")


def _decode_oauth_token(token: str, kind: str, resource: str) -> dict[str, Any]:
    claims = jwt.decode(
        token,
        _OAUTH_SIGNING_KEY,
        algorithms=["HS256"],
        audience=resource,
        issuer=_oauth_base(),
    )
    if claims.get("typ") != kind or claims.get("scope") != "loopback":
        raise jwt.InvalidTokenError("wrong token type or scope")
    return claims


def _valid_mcp_credential(value: str) -> bool:
    raw = value.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if secrets.compare_digest(raw, TOKEN):
        return True
    try:
        _decode_oauth_token(raw, "access", _oauth_resource())
        return True
    except Exception:
        return False


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request) -> Response:
    return JSONResponse({
        "resource": _oauth_resource(),
        "authorization_servers": [_oauth_base()],
        "scopes_supported": ["loopback"],
        "bearer_methods_supported": ["header"],
    })


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request) -> Response:
    base = _oauth_base()
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
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": "redirect_uris required"}, status_code=400)
    if any(not isinstance(uri, str) or not _redirect_uri_allowed(uri) for uri in redirect_uris):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    client_id = secrets.token_urlsafe(18)
    _OAUTH_CLIENTS[client_id] = {
        "redirect_uris": redirect_uris,
        "client_name": str(body.get("client_name") or "MCP client")[:200],
        "created_at": time.time(),
    }
    return JSONResponse(
        {
            "client_id": client_id,
            "client_name": _OAUTH_CLIENTS[client_id]["client_name"],
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
    response_type = q.get("response_type", "code")
    code_challenge = q.get("code_challenge", "")
    code_challenge_method = q.get("code_challenge_method", "")
    resource = q.get("resource", _oauth_resource())
    scope = q.get("scope", "loopback")
    supplied_token = q.get("loopback_token", "")

    client = _OAUTH_CLIENTS.get(client_id)
    if response_type != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if not client or redirect_uri not in client.get("redirect_uris", []):
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if code_challenge_method != "S256" or not code_challenge:
        return JSONResponse({"error": "invalid_request", "error_description": "PKCE S256 is required"}, status_code=400)
    if resource != _oauth_resource():
        return JSONResponse({"error": "invalid_target"}, status_code=400)
    if "loopback" not in scope.split():
        return JSONResponse({"error": "invalid_scope"}, status_code=400)

    if not supplied_token:
        hidden = "".join(
            f'<input type="hidden" name="{html_lib.escape(k, quote=True)}" value="{html_lib.escape(v, quote=True)}">'
            for k, v in [
                ("client_id", client_id),
                ("redirect_uri", redirect_uri),
                ("state", state),
                ("response_type", response_type),
                ("code_challenge", code_challenge),
                ("code_challenge_method", code_challenge_method),
                ("resource", resource),
                ("scope", scope),
            ]
        )
        safe_host = html_lib.escape(PUBLIC_HOST, quote=True)
        safe_client = html_lib.escape(str(client.get("client_name") or client_id), quote=True)
        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loopback // OAuth</title><style>
:root{{--panel:#11151a;--line:#2a3138;--yellow:#ffd600;--cyan:#6ee7ff;--muted:#91a0ad;--text:#f5f7f8}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 0,#1b2229 0,#0b0e11 45%,#050607 100%);color:var(--text);font-family:"SFMono-Regular",Consolas,monospace;padding:24px}}
.card{{width:min(560px,100%);background:linear-gradient(180deg,#12171c,#0d1115);border:1px solid #2b333a;border-radius:22px;box-shadow:0 28px 90px #000a;overflow:hidden}}.top{{padding:28px 30px 22px;border-bottom:1px solid var(--line)}}.badge{{color:var(--yellow);font-size:12px;letter-spacing:.18em}}h1{{font-size:27px;margin:16px 0 8px}}.sub{{color:var(--muted);font-size:13px;line-height:1.65}}.meta{{display:grid;grid-template-columns:90px 1fr;gap:8px 14px;margin-top:18px;padding:13px 15px;border:1px solid var(--line);border-radius:12px;background:#080b0e;font-size:12px}}.meta b{{color:var(--cyan)}}.body{{padding:25px 30px 30px}}label{{display:block;font-size:12px;margin-bottom:9px}}input{{width:100%;border:1px solid #38424b;background:#07090b;color:#fff;border-radius:11px;padding:14px 15px;font:inherit;outline:none}}input:focus{{border-color:var(--yellow)}}button{{width:100%;margin-top:14px;border:0;border-radius:11px;padding:14px;background:var(--yellow);color:#090909;font:700 13px inherit;cursor:pointer}}code{{color:var(--cyan)}}.hint{{font-size:11px;color:var(--muted);line-height:1.55}}
</style></head><body><main class="card"><section class="top"><div class="badge">◆ LOOPBACK AUTH GATE</div><h1>Authorize machine access</h1>
<p class="sub">Authorize {safe_client} to access this Loopback endpoint. The permanent machine token stays on this host; the client receives a short-lived OAuth credential.</p>
<div class="meta"><b>HOST</b><span>{safe_host}</span><b>CLIENT</b><span>{safe_client}</span></div></section>
<section class="body"><form method="get" action="/oauth/authorize" autocomplete="off">{hidden}
<label for="loopback_token">Loopback token</label><input id="loopback_token" type="password" name="loopback_token" autocomplete="current-password" autofocus required>
<p class="hint">Retrieve locally with <code>loopback token</code>. PKCE protects the authorization code exchange.</p><button>Authorize connector</button></form></section></main></body></html>"""
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    if not secrets.compare_digest(supplied_token, TOKEN):
        return HTMLResponse(
            "<!doctype html><body style='background:#07090b;color:#fff;font:14px monospace;padding:40px'>"
            "<h2 style='color:#ffd600'>LOOPBACK // ACCESS DENIED</h2><p>The supplied token is invalid.</p></body>",
            status_code=401,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    code = secrets.token_urlsafe(32)
    _OAUTH_CODES[code] = {
        "client_id": client_id,
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "resource": resource,
        "scope": scope,
        "expires": time.time() + _CODE_TTL_SECONDS,
    }
    params = {"code": code}
    if state:
        params["state"] = state
    params["iss"] = _oauth_base()
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urllib.parse.urlencode(params), status_code=302)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request) -> Response:
    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    resource = str(form.get("resource") or _oauth_resource())
    if resource != _oauth_resource():
        return JSONResponse({"error": "invalid_target"}, status_code=400)

    if grant_type == "authorization_code":
        code = str(form.get("code") or "")
        verifier = str(form.get("code_verifier") or "")
        client_id = str(form.get("client_id") or "")
        redirect_uri = str(form.get("redirect_uri") or "")
        entry = _OAUTH_CODES.pop(code, None)
        if not entry or entry["expires"] < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if client_id != entry["client_id"] or redirect_uri != entry["redirect_uri"] or resource != entry["resource"]:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        calc = _b64url(hashlib.sha256(verifier.encode()).digest())
        if not secrets.compare_digest(calc, entry["code_challenge"]):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        return JSONResponse({
            "access_token": _issue_oauth_token("access", client_id, resource, _ACCESS_TTL_SECONDS),
            "refresh_token": _issue_oauth_token("refresh", client_id, resource, _REFRESH_TTL_SECONDS),
            "token_type": "Bearer",
            "expires_in": _ACCESS_TTL_SECONDS,
            "scope": "loopback",
        }, headers={"Cache-Control": "no-store"})

    if grant_type == "refresh_token":
        refresh = str(form.get("refresh_token") or "")
        try:
            claims = _decode_oauth_token(refresh, "refresh", resource)
        except Exception:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        client_id = str(claims["client_id"])
        return JSONResponse({
            "access_token": _issue_oauth_token("access", client_id, resource, _ACCESS_TTL_SECONDS),
            "refresh_token": _issue_oauth_token("refresh", client_id, resource, _REFRESH_TTL_SECONDS),
            "token_type": "Bearer",
            "expires_in": _ACCESS_TTL_SECONDS,
            "scope": "loopback",
        }, headers={"Cache-Control": "no-store"})

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


def _admin_session_ok(request: Request) -> bool:
    sid = request.cookies.get("loopback_admin", "")
    exp = _ADMIN_SESSIONS.get(sid, 0)
    if exp <= time.time():
        _ADMIN_SESSIONS.pop(sid, None)
        return False
    return True


def _human_bytes(value: int) -> str:
    n = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def _system_snapshot() -> dict[str, Any]:
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
    disk = shutil.disk_usage(Path.home())
    return {
        "hostname": socket.gethostname(),
        "uptime_seconds": int(time.time() - STARTED_AT),
        "load": [round(x, 2) for x in load],
        "memory_total": mem_total,
        "memory_used": max(0, mem_total - mem_available),
        "memory_total_human": _human_bytes(mem_total),
        "memory_used_human": _human_bytes(max(0, mem_total - mem_available)),
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_total_human": _human_bytes(disk.total),
        "disk_used_human": _human_bytes(disk.used),
        "python": os.sys.version.split()[0],
    }


def _admin_denied(request: Request) -> JSONResponse | None:
    if not _admin_session_ok(request):
        return JSONResponse({"detail": "admin authentication required"}, status_code=401)
    return None


@mcp.custom_route("/admin/login", methods=["GET", "POST"])
async def admin_login(request: Request) -> Response:
    if request.method == "GET":
        return HTMLResponse(render_login(PUBLIC_HOST), headers={"Cache-Control": "no-store"})
    form = await request.form()
    supplied = str(form.get("token", ""))
    if not secrets.compare_digest(supplied, TOKEN):
        return HTMLResponse(render_login(PUBLIC_HOST, "Invalid Loopback token"), status_code=401)
    sid = secrets.token_urlsafe(32)
    _ADMIN_SESSIONS[sid] = time.time() + 12 * 3600
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie("loopback_admin", sid, max_age=12 * 3600, httponly=True,
                        secure=PUBLIC_HOST not in {"localhost", "127.0.0.1"},
                        samesite="strict", path="/")
    return response


@mcp.custom_route("/admin/logout", methods=["GET"])
async def admin_logout(request: Request) -> Response:
    sid = request.cookies.get("loopback_admin", "")
    _ADMIN_SESSIONS.pop(sid, None)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie("loopback_admin", path="/")
    return response


@mcp.custom_route("/admin", methods=["GET"])
async def admin_dashboard(request: Request) -> Response:
    if not _admin_session_ok(request):
        return RedirectResponse("/admin/login", status_code=303)
    return HTMLResponse(render_dashboard(PUBLIC_HOST), headers={"Cache-Control": "no-store"})


@mcp.custom_route("/api/admin/overview", methods=["GET"])
async def admin_overview(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    return JSONResponse({
        "system": _system_snapshot(),
        "policy": {"profile": POLICY.profile},
        "jobs": JOBS.list(),
        "terminals": TERMINALS.list(),
        "approvals": POLICY.pending(),
        "nodes": FLEET.list(),
        "audit": AUDIT.stats(),
        "recent": AUDIT.recent(80),
    })


@mcp.custom_route("/api/admin/policy", methods=["GET", "POST"])
async def admin_policy(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    if request.method == "GET":
        return JSONResponse(dict(POLICY.data))
    body = await request.json()
    allowed_keys = {
        "profile", "allowed_roots", "denied_roots", "allow_shell",
        "allow_process_control", "allow_fleet", "allow_browser",
        "allowed_tools", "denied_tools", "approval_ttl_seconds",
        "require_approval_patterns", "deny_command_patterns",
    }
    updates = {k: v for k, v in body.items() if k in allowed_keys}
    if "profile" in updates and updates["profile"] not in {"standard", "trusted", "read-only", "locked"}:
        return JSONResponse({"detail": "invalid profile"}, status_code=400)
    return JSONResponse(POLICY.save(updates))


@mcp.custom_route("/api/admin/approvals/{approval_id}/approve", methods=["POST"])
async def admin_approve(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    body = await request.json()
    try:
        result = POLICY.approve(request.path_params["approval_id"], uses=int(body.get("uses", 1)),
                                ttl_seconds=body.get("ttl_seconds"))
    except KeyError:
        return JSONResponse({"detail": "approval not found"}, status_code=404)
    return JSONResponse(result)


@mcp.custom_route("/api/admin/jobs/{job_id}/stop", methods=["POST"])
async def admin_stop_job(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    POLICY.check_process_control()
    try:
        return JSONResponse(JOBS.stop(request.path_params["job_id"]))
    except KeyError:
        return JSONResponse({"detail": "job not found"}, status_code=404)


@mcp.custom_route("/api/admin/terminals/{terminal_id}/close", methods=["POST"])
async def admin_close_terminal(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    POLICY.check_process_control()
    try:
        return JSONResponse(TERMINALS.close(request.path_params["terminal_id"]))
    except KeyError:
        return JSONResponse({"detail": "terminal not found"}, status_code=404)


@mcp.custom_route("/api/admin/nodes", methods=["GET", "POST"])
async def admin_nodes(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    if request.method == "GET":
        return JSONResponse(FLEET.list())
    body = await request.json()
    name = str(body.get("name", "")).strip()
    url = str(body.get("url", "")).strip()
    if not name or not url:
        return JSONResponse({"detail": "name and url are required"}, status_code=400)
    try:
        node = FLEET.upsert(name, url, str(body.get("token", "")) or None,
                            enabled=bool(body.get("enabled", True)),
                            description=str(body.get("description", "")))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(node, status_code=201)


@mcp.custom_route("/api/admin/nodes/{node_name}", methods=["DELETE"])
async def admin_node_delete(request: Request) -> Response:
    denied = _admin_denied(request)
    if denied:
        return denied
    return JSONResponse({"removed": FLEET.remove(request.path_params["node_name"])})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    return JSONResponse({
        "ok": True,
        "service": "loopback",
        "version": "3.0.0-v2-preview",
        "policy_profile": POLICY.profile,
        "uptime_seconds": int(time.time() - STARTED_AT),
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


class PolicyGate:
    """Enforce per-tool allow/deny policy at the MCP JSON-RPC boundary."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/mcp" or scope.get("method") != "POST":
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

        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                params = payload.get("params") or {}
                if isinstance(params, dict):
                    POLICY.check_tool(str(params.get("name") or ""))
        except PolicyError as exc:
            payload_id = None
            try:
                payload_id = json.loads(body.decode("utf-8")).get("id")
            except Exception:
                pass
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload_id,
                    "error": {"code": -32001, "message": str(exc)},
                },
                status_code=200,
            )
            return await response(scope, receive, send)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        return await self.wrapped(scope, replay_receive, send)


class AuditGate:
    """Record MCP activity without storing bearer credentials or request bodies."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/mcp" or scope.get("method") != "POST":
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

        status_code = 200

        async def wrapped_send(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 200))
            await send(message)

        tool = "mcp"
        target = None
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
            if isinstance(payload, dict):
                method = str(payload.get("method") or "mcp")
                tool = method
                params = payload.get("params") or {}
                if method == "tools/call" and isinstance(params, dict):
                    target = str(params.get("name") or "") or None
                    tool = target or method
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        started = time.time()
        error = None
        try:
            await self.wrapped(scope, replay_receive, wrapped_send)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            AUDIT.record(
                tool,
                "ok" if error is None and status_code < 400 else (error or f"http_{status_code}"),
                duration_ms=int((time.time() - started) * 1000),
                target=target,
                detail={"user_agent": headers.get("user-agent", "")[:300]},
            )


# Browser-based MCP clients need the MCP headers exposed.
cors = CORSMiddleware(
    AuditGate(PolicyGate(inner)),
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

            valid = any(_valid_mcp_credential(x) for x in candidates if x)

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