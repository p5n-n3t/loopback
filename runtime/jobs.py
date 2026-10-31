"""Background jobs and persistent terminals with optional OS sandboxing.

Both managers accept a ``wrap`` callable ``(argv, cwd) -> (argv, preexec_fn,
env)`` supplied by the server; it applies the sandbox backend so restrictions
are enforced at the execution endpoint, not merely checked at the gateway.
"""
from __future__ import annotations

import json
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import pty
except ImportError:  # pragma: no cover - Windows
    pty = None  # type: ignore[assignment]

try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

WrapFn = Callable[[list[str], str], tuple[list[str], Callable[[], None] | None, dict[str, str]]]
WINDOWS = sys.platform.startswith("win")


class QuotaExceeded(RuntimeError):
    """A principal already holds the maximum number of live jobs or terminals."""


def _owns(resource_owner: str | None, owner: str | None) -> bool:
    """``owner=None`` is the explicit administrator/internal view; callers pass their stable key.

    Records without an owner (metadata from before ownership existed) are visible
    only to the administrator view, never to an MCP caller.
    """
    return owner is None or (resource_owner is not None and resource_owner == owner)


def process_start_ticks(pid: int) -> str | None:
    """Kernel start time of ``pid`` (Linux ``/proc``); detects PID reuse after restart."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    try:
        fields = raw.rsplit(")", 1)[1].split()
        return fields[19]
    except IndexError:
        return None


def shell_argv(shell: str, command: str) -> list[str]:
    """Build an argv that runs ``command`` through ``shell`` on this platform."""
    name = os.path.basename(shell).lower()
    if name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return [shell, "-NoProfile", "-NonInteractive", "-Command", command]
    if name in {"cmd", "cmd.exe"}:
        return [shell, "/d", "/c", command]
    return [shell, "-lc", command]


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    pid: int
    started_at: float
    log_path: str
    process: subprocess.Popen[Any] | None
    target: str = "local"
    principal: str | None = None
    finished_at: float | None = None
    returncode: int | None = None
    owner: str | None = None
    start_ticks: str | None = None


class JobManager:
    def __init__(self, config_dir: Path, shell: str, *, wrap: WrapFn | None = None, max_completed: int = 200,
                 max_running_per_owner: int = 16):
        self.dir = Path(config_dir) / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._harden_dir()
        self.shell = shell
        self.wrap = wrap
        self.max_completed = max_completed
        self.max_running_per_owner = max_running_per_owner
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._recover()

    def _harden_dir(self) -> None:
        """Job logs can contain secrets printed by commands: keep them owner-only."""
        if WINDOWS:
            return
        try:
            os.chmod(self.dir, 0o700)
            for entry in self.dir.iterdir():
                if entry.is_file():
                    os.chmod(entry, 0o600)
        except OSError:
            pass

    # ----------------------------------------------------------- persistence
    def _meta_path(self, jid: str) -> Path:
        return self.dir / f"{jid}.json"

    def _persist(self, job: Job) -> None:
        data = {
            "id": job.id, "command": job.command, "cwd": job.cwd, "pid": job.pid, "started_at": job.started_at,
            "log_path": job.log_path, "target": job.target, "principal": job.principal,
            "finished_at": job.finished_at, "returncode": job.returncode, "owner": job.owner,
            "start_ticks": job.start_ticks, "schema_version": 2,
        }
        tmp = self._meta_path(job.id).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data))
        os.replace(tmp, self._meta_path(job.id))

    def _recover(self) -> None:
        """After a restart, previously started jobs reappear with their last known state."""
        for meta in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)[-self.max_completed:]:
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                job = Job(data["id"], data["command"], data["cwd"], int(data["pid"]), float(data["started_at"]),
                          data["log_path"], None, data.get("target", "local"), data.get("principal"),
                          data.get("finished_at"), data.get("returncode"),
                          # Version-1 records have no stable owner: admin-only after migration.
                          data.get("owner"), data.get("start_ticks"))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if job.finished_at is None:
                # The process may still be running (started in its own session).
                if not self._same_process(job):
                    job.finished_at = time.time()
                    job.returncode = None
                    self._persist(job)
            self._jobs[job.id] = job

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if WINDOWS:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _same_process(self, job: Job) -> bool:
        """True only when ``job.pid`` is alive and, where measurable, is the process we started."""
        if not self._pid_alive(job.pid):
            return False
        if job.start_ticks is None:
            return True
        current = process_start_ticks(job.pid)
        return current is None or current == job.start_ticks

    # ------------------------------------------------------------------ api
    def start(self, command: str, cwd: str, *, target: str = "local", principal: str | None = None,
              owner: str | None = None) -> dict[str, Any]:
        if owner is not None and self.max_running_per_owner and \
                self.running_count(owner=owner) >= self.max_running_per_owner:
            raise QuotaExceeded(f"job quota reached ({self.max_running_per_owner} running jobs for this caller); stop one first")
        jid = uuid.uuid4().hex[:12]
        log_path = self.dir / f"{jid}.log"
        argv = shell_argv(self.shell, command)
        preexec = None
        env = dict(os.environ)
        if self.wrap:
            argv, preexec, env = self.wrap(argv, cwd)
        env.setdefault("HOME", os.path.expanduser("~"))
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab", buffering=0) as handle:
            kwargs: dict[str, Any] = {}
            if WINDOWS:
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
                kwargs["preexec_fn"] = preexec
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                    env=env, **kwargs)
        job = Job(jid, command, cwd, proc.pid, time.time(), str(log_path), proc, target, principal,
                  owner=owner, start_ticks=process_start_ticks(proc.pid))
        with self._lock:
            self._jobs[jid] = job
            self._persist(job)
            self._trim()
        return self.info(jid)

    def _trim(self) -> None:
        finished = sorted((j for j in self._jobs.values() if j.finished_at is not None), key=lambda j: j.finished_at or 0)
        for job in finished[:-self.max_completed] if self.max_completed else finished:
            self._jobs.pop(job.id, None)
            for suffix in (".json", ".log"):
                try:
                    (self.dir / f"{job.id}{suffix}").unlink()
                except OSError:
                    pass

    def _poll(self, job: Job) -> None:
        if job.finished_at is not None:
            return
        if job.process is not None:
            rc = job.process.poll()
            if rc is not None:
                job.returncode = rc
                job.finished_at = time.time()
                self._persist(job)
        elif not self._same_process(job):
            job.finished_at = time.time()
            self._persist(job)

    def _get(self, job_id: str, owner: str | None) -> Job:
        job = self._jobs.get(job_id)
        # Another principal's job is reported exactly like a missing one.
        if not job or not _owns(job.owner, owner):
            raise KeyError("job not found")
        return job

    def info(self, job_id: str, *, owner: str | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id, owner)
            self._poll(job)
            return {
                "id": job.id, "command": job.command, "cwd": job.cwd, "pid": job.pid, "started_at": job.started_at,
                "running": job.finished_at is None, "returncode": job.returncode, "finished_at": job.finished_at,
                "log_path": job.log_path, "target": job.target, "principal": job.principal,
                "owned": job.owner is not None,
            }

    def list(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ids = [jid for jid, job in self._jobs.items() if _owns(job.owner, owner)]
        out = []
        for jid in ids:
            try:
                out.append(self.info(jid, owner=owner))
            except KeyError:
                continue
        return out

    def output(self, job_id: str, offset: int = 0, max_bytes: int = 131072, *, owner: str | None = None) -> dict[str, Any]:
        info = self.info(job_id, owner=owner)
        p = Path(info["log_path"])
        if not p.exists():
            return {**info, "offset": 0, "next_offset": 0, "content": "", "has_more": False}
        size = p.stat().st_size
        start = max(0, min(int(offset), size))
        n = max(1, min(int(max_bytes), 1024 * 1024))
        with p.open("rb") as f:
            f.seek(start)
            raw = f.read(n)
            nxt = f.tell()
        return {**info, "offset": start, "next_offset": nxt, "content": raw.decode("utf-8", errors="replace"), "has_more": nxt < size}

    def stop(self, job_id: str, sig: int = signal.SIGTERM, *, owner: str | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id, owner)
            self._poll(job)
        if job.finished_at is None:
            # A recovered job (no Popen handle) is signalled only if its PID still
            # names the process we started; PID reuse must not kill an unrelated group.
            if job.process is None and not self._same_process(job):
                with self._lock:
                    job.finished_at = time.time()
                    self._persist(job)
                return self.info(job_id, owner=owner)
            try:
                if WINDOWS and job.process is not None:
                    job.process.terminate()
                else:
                    os.killpg(job.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        return self.info(job_id, owner=owner)

    def running_count(self, target: str | None = None, *, owner: str | None = None) -> int:
        return sum(1 for j in self.list(owner=owner) if j["running"] and (target is None or j["target"] == target))


@dataclass
class Terminal:
    id: str
    pid: int
    fd: int
    cwd: str
    shell: str
    started_at: float
    buffer: deque[bytes] = field(default_factory=lambda: deque(maxlen=4096))
    bytes_seen: int = 0
    alive: bool = True
    target: str = "local"
    principal: str | None = None
    owner: str | None = None
    last_activity: float = field(default_factory=time.time)


class TerminalManager:
    def __init__(self, shell: str, *, wrap: WrapFn | None = None, max_per_owner: int = 8, idle_timeout: float = 4 * 3600):
        self.shell = shell
        self.wrap = wrap
        self.max_per_owner = max_per_owner
        self.idle_timeout = idle_timeout
        self._lock = threading.RLock()
        self._terms: dict[str, Terminal] = {}

    @staticmethod
    def available() -> bool:
        return pty is not None

    def start(self, cwd: str, shell: str | None = None, rows: int = 30, cols: int = 120, *, principal: str | None = None,
              owner: str | None = None) -> dict[str, Any]:
        if pty is None:
            raise RuntimeError("persistent terminals require a POSIX pty; not available on this platform")
        self.reap_idle()
        if owner is not None and self.max_per_owner:
            live = sum(1 for t in self._terms.values() if t.alive and t.owner == owner)
            if live >= self.max_per_owner:
                raise QuotaExceeded(f"terminal quota reached ({self.max_per_owner} open terminals for this caller); close one first")
        sh = shell or self.shell
        argv: list[str] = [sh, "-l"]
        preexec = None
        env = dict(os.environ)
        if self.wrap:
            argv, preexec, env = self.wrap(argv, cwd)
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(cwd)
                env.setdefault("TERM", "xterm-256color")
                if preexec:
                    preexec()
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        tid = uuid.uuid4().hex[:12]
        term = Terminal(tid, pid, fd, cwd, sh, time.time(), principal=principal, owner=owner)
        with self._lock:
            self._terms[tid] = term
        self.resize(tid, rows, cols, owner=owner)
        threading.Thread(target=self._reader, args=(term,), daemon=True).start()
        return self.info(tid, owner=owner)

    def _reader(self, term: Terminal) -> None:
        while term.alive:
            try:
                ready, _, _ = select.select([term.fd], [], [], 0.5)
                if not ready:
                    try:
                        finished, _ = os.waitpid(term.pid, os.WNOHANG)
                    except ChildProcessError:
                        finished = term.pid
                    if finished:
                        term.alive = False
                    continue
                raw = os.read(term.fd, 65536)
                if not raw:
                    term.alive = False
                    break
                with self._lock:
                    term.buffer.append(raw)
                    term.bytes_seen += len(raw)
            except OSError:
                term.alive = False
                break
        try:
            os.waitpid(term.pid, os.WNOHANG)
        except ChildProcessError:
            pass

    def _get(self, terminal_id: str, owner: str | None) -> Terminal:
        with self._lock:
            term = self._terms.get(terminal_id)
        if not term or not _owns(term.owner, owner):
            raise KeyError("terminal not found")
        return term

    def info(self, terminal_id: str, *, owner: str | None = None) -> dict[str, Any]:
        term = self._get(terminal_id, owner)
        return {"id": term.id, "pid": term.pid, "cwd": term.cwd, "shell": term.shell, "started_at": term.started_at,
                "alive": term.alive, "bytes_seen": term.bytes_seen, "target": term.target, "principal": term.principal,
                "last_activity": term.last_activity}

    def list(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ids = [tid for tid, t in self._terms.items() if _owns(t.owner, owner)]
        return [self.info(tid, owner=owner) for tid in ids]

    def write(self, terminal_id: str, data: str, *, owner: str | None = None) -> dict[str, Any]:
        term = self._get(terminal_id, owner)
        if not term.alive:
            raise KeyError("terminal not found or closed")
        written = os.write(term.fd, data.encode())
        term.last_activity = time.time()
        return {"id": terminal_id, "written": written}

    def read(self, terminal_id: str, max_bytes: int = 131072, clear: bool = True, *, owner: str | None = None) -> dict[str, Any]:
        term = self._get(terminal_id, owner)
        with self._lock:
            raw = b"".join(term.buffer)
            if clear:
                term.buffer.clear()
            term.last_activity = time.time()
        n = max(1, min(int(max_bytes), 1024 * 1024))
        clipped = raw[-n:]
        return {**self.info(terminal_id, owner=owner), "content": clipped.decode("utf-8", errors="replace"),
                "truncated": len(raw) > len(clipped)}

    def resize(self, terminal_id: str, rows: int, cols: int, *, owner: str | None = None) -> dict[str, Any]:
        term = self._get(terminal_id, owner)
        if fcntl is not None and termios is not None:
            winsz = struct.pack("HHHH", max(1, int(rows)), max(1, int(cols)), 0, 0)
            try:
                fcntl.ioctl(term.fd, termios.TIOCSWINSZ, winsz)
            except OSError:
                pass
        return {"id": terminal_id, "rows": rows, "cols": cols}

    def close(self, terminal_id: str, *, owner: str | None = None) -> dict[str, Any]:
        term = self._get(terminal_id, owner)
        term.alive = False
        for sig in (getattr(signal, "SIGHUP", signal.SIGTERM), signal.SIGTERM):
            try:
                os.kill(term.pid, sig)
            except ProcessLookupError:
                break
        try:
            os.close(term.fd)
        except OSError:
            pass
        return self.info(terminal_id, owner=owner)

    def reap_idle(self, now: float | None = None) -> list[str]:
        """Close terminals idle longer than ``idle_timeout``; forget long-dead ones."""
        now = time.time() if now is None else now
        closed: list[str] = []
        for tid, term in list(self._terms.items()):
            if term.alive and self.idle_timeout and now - term.last_activity > self.idle_timeout:
                try:
                    self.close(tid)
                    closed.append(tid)
                except KeyError:
                    pass
            elif not term.alive and now - term.last_activity > max(self.idle_timeout, 3600):
                with self._lock:
                    self._terms.pop(tid, None)
        return closed

    def close_all(self) -> int:
        count = 0
        for tid in list(self._terms):
            try:
                if self._terms[tid].alive:
                    self.close(tid)
                    count += 1
            except KeyError:
                pass
        return count
