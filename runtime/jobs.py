from __future__ import annotations

import os
import pty
import select
import signal
import struct
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover
    fcntl = None
    termios = None


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    pid: int
    started_at: float
    log_path: str
    process: subprocess.Popen[Any]


class JobManager:
    def __init__(self, config_dir: Path, shell: str):
        self.dir = config_dir / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shell = shell
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}

    def start(self, command: str, cwd: str) -> dict[str, Any]:
        jid = uuid.uuid4().hex[:12]
        log_path = self.dir / f"{jid}.log"
        handle = log_path.open("ab", buffering=0)
        proc = subprocess.Popen(
            [self.shell, "-lc", command],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        job = Job(jid, command, cwd, proc.pid, time.time(), str(log_path), proc)
        with self._lock:
            self._jobs[jid] = job
        return self.info(jid)

    def info(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError("job not found")
        rc = job.process.poll()
        return {
            "id": job.id,
            "command": job.command,
            "cwd": job.cwd,
            "pid": job.pid,
            "started_at": job.started_at,
            "running": rc is None,
            "returncode": rc,
            "log_path": job.log_path,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._jobs)
        return [self.info(jid) for jid in ids]

    def output(self, job_id: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
        info = self.info(job_id)
        p = Path(info["log_path"])
        if not p.exists():
            return {**info, "offset": 0, "next_offset": 0, "content": ""}
        size = p.stat().st_size
        start = max(0, min(int(offset), size))
        n = max(1, min(int(max_bytes), 1024 * 1024))
        with p.open("rb") as f:
            f.seek(start)
            raw = f.read(n)
            nxt = f.tell()
        return {
            **info,
            "offset": start,
            "next_offset": nxt,
            "content": raw.decode("utf-8", errors="replace"),
            "has_more": nxt < size,
        }

    def stop(self, job_id: str, sig: int = signal.SIGTERM) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError("job not found")
        if job.process.poll() is None:
            try:
                os.killpg(job.process.pid, sig)
            except ProcessLookupError:
                pass
        return self.info(job_id)


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


class TerminalManager:
    def __init__(self, shell: str):
        self.shell = shell
        self._lock = threading.RLock()
        self._terms: dict[str, Terminal] = {}

    def start(self, cwd: str, shell: str | None = None, rows: int = 30, cols: int = 120) -> dict[str, Any]:
        sh = shell or self.shell
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.chdir(cwd)
            os.environ.setdefault("TERM", "xterm-256color")
            os.execv(sh, [sh, "-l"])
        tid = uuid.uuid4().hex[:12]
        term = Terminal(tid, pid, fd, cwd, sh, time.time())
        with self._lock:
            self._terms[tid] = term
        self.resize(tid, rows, cols)
        threading.Thread(target=self._reader, args=(term,), daemon=True).start()
        return self.info(tid)

    def _reader(self, term: Terminal) -> None:
        while term.alive:
            try:
                ready, _, _ = select.select([term.fd], [], [], 0.5)
                if not ready:
                    try:
                        os.kill(term.pid, 0)
                    except OSError:
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

    def info(self, terminal_id: str) -> dict[str, Any]:
        with self._lock:
            term = self._terms.get(terminal_id)
        if not term:
            raise KeyError("terminal not found")
        return {
            "id": term.id,
            "pid": term.pid,
            "cwd": term.cwd,
            "shell": term.shell,
            "started_at": term.started_at,
            "alive": term.alive,
            "bytes_seen": term.bytes_seen,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._terms)
        return [self.info(tid) for tid in ids]

    def write(self, terminal_id: str, data: str) -> dict[str, Any]:
        with self._lock:
            term = self._terms.get(terminal_id)
        if not term or not term.alive:
            raise KeyError("terminal not found or closed")
        raw = data.encode()
        written = os.write(term.fd, raw)
        return {"id": terminal_id, "written": written}

    def read(self, terminal_id: str, max_bytes: int = 131072, clear: bool = True) -> dict[str, Any]:
        with self._lock:
            term = self._terms.get(terminal_id)
            if not term:
                raise KeyError("terminal not found")
            raw = b"".join(term.buffer)
            if clear:
                term.buffer.clear()
        n = max(1, min(int(max_bytes), 1024 * 1024))
        clipped = raw[-n:]
        return {
            **self.info(terminal_id),
            "content": clipped.decode("utf-8", errors="replace"),
            "truncated": len(raw) > len(clipped),
        }

    def resize(self, terminal_id: str, rows: int, cols: int) -> dict[str, Any]:
        with self._lock:
            term = self._terms.get(terminal_id)
        if not term:
            raise KeyError("terminal not found")
        if fcntl is not None and termios is not None:
            winsz = struct.pack("HHHH", max(1, int(rows)), max(1, int(cols)), 0, 0)
            fcntl.ioctl(term.fd, termios.TIOCSWINSZ, winsz)
        return {"id": terminal_id, "rows": rows, "cols": cols}

    def close(self, terminal_id: str) -> dict[str, Any]:
        with self._lock:
            term = self._terms.get(terminal_id)
        if not term:
            raise KeyError("terminal not found")
        term.alive = False
        try:
            os.kill(term.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        try:
            os.close(term.fd)
        except OSError:
            pass
        return self.info(terminal_id)
