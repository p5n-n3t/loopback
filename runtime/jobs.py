from __future__ import annotations

import os
import pty
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from policy import CFG

STATE = CFG / "state"
STATE.mkdir(parents=True, exist_ok=True)


@dataclass
class Job:
    id: str
    command: str
    cwd: str
    log_path: str
    pid: int
    started: float
    process: subprocess.Popen[Any] = field(repr=False)
    node: str = "local"

    def snapshot(self) -> dict[str, Any]:
        rc = self.process.poll()
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.pid,
            "node": self.node,
            "started": self.started,
            "running": rc is None,
            "returncode": rc,
            "log_path": self.log_path,
        }


class JobManager:
    def __init__(self) -> None:
        self.dir = STATE / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, command: str, cwd: str, shell: str, node: str = "local") -> dict[str, Any]:
        job_id = "job_" + secrets.token_urlsafe(9)
        log = self.dir / f"{job_id}.log"
        handle = log.open("ab", buffering=0)
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            executable=shell,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        handle.close()
        job = Job(job_id, command, cwd, str(log), proc.pid, time.time(), proc, node)
        with self.lock:
            self.jobs[job_id] = job
        return job.snapshot()

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [job.snapshot() for job in self.jobs.values()]

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise KeyError(f"unknown job: {job_id}")
        return job

    def output(self, job_id: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, Any]:
        job = self.get(job_id)
        path = Path(job.log_path)
        size = path.stat().st_size if path.exists() else 0
        offset = max(0, min(int(offset), size))
        max_bytes = max(1, min(int(max_bytes), 1024 * 1024))
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(max_bytes)
            next_offset = handle.tell()
        return {
            **job.snapshot(),
            "offset": offset,
            "next_offset": next_offset,
            "size": size,
            "content": raw.decode("utf-8", errors="replace"),
            "truncated": next_offset < size,
        }

    def stop(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job.process.poll() is None:
            try:
                os.killpg(job.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            job.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(job.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return job.snapshot()


@dataclass
class Terminal:
    id: str
    pid: int
    master_fd: int
    process: subprocess.Popen[Any] = field(repr=False)
    created: float = field(default_factory=time.time)
    cwd: str = "~"

    def snapshot(self) -> dict[str, Any]:
        rc = self.process.poll()
        return {
            "id": self.id,
            "pid": self.pid,
            "created": self.created,
            "cwd": self.cwd,
            "running": rc is None,
            "returncode": rc,
        }


class TerminalManager:
    def __init__(self) -> None:
        self.items: dict[str, Terminal] = {}
        self.lock = threading.Lock()

    def start(self, shell: str, cwd: str) -> dict[str, Any]:
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [shell, "-l"],
            cwd=cwd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        os.set_blocking(master, False)
        term = Terminal("term_" + secrets.token_urlsafe(9), proc.pid, master, proc, cwd=cwd)
        with self.lock:
            self.items[term.id] = term
        return term.snapshot()

    def get(self, terminal_id: str) -> Terminal:
        with self.lock:
            term = self.items.get(terminal_id)
        if not term:
            raise KeyError(f"unknown terminal: {terminal_id}")
        return term

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [term.snapshot() for term in self.items.values()]

    def write(self, terminal_id: str, data: str) -> dict[str, Any]:
        term = self.get(terminal_id)
        if term.process.poll() is not None:
            raise RuntimeError("terminal is no longer running")
        written = os.write(term.master_fd, data.encode())
        return {**term.snapshot(), "written": written}

    def read(self, terminal_id: str, max_bytes: int = 65536) -> dict[str, Any]:
        term = self.get(terminal_id)
        max_bytes = max(1, min(int(max_bytes), 1024 * 1024))
        chunks: list[bytes] = []
        total = 0
        while total < max_bytes:
            try:
                part = os.read(term.master_fd, min(8192, max_bytes - total))
            except BlockingIOError:
                break
            except OSError:
                break
            if not part:
                break
            chunks.append(part)
            total += len(part)
        return {**term.snapshot(), "content": b"".join(chunks).decode("utf-8", errors="replace")}

    def close(self, terminal_id: str) -> dict[str, Any]:
        term = self.get(terminal_id)
        if term.process.poll() is None:
            try:
                os.killpg(term.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            os.close(term.master_fd)
        except OSError:
            pass
        return term.snapshot()
