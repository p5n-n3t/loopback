from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None


def process_list(limit: int = 200) -> list[dict[str, Any]]:
    if psutil is None:
        raise RuntimeError("psutil is not installed")
    out: list[dict[str, Any]] = []
    for proc in psutil.process_iter(
        ["pid", "ppid", "name", "username", "status", "cpu_percent", "memory_percent", "cmdline"]
    ):
        try:
            row = proc.info
            row["cmdline"] = " ".join(row.get("cmdline") or [])[:2000]
            row["memory_percent"] = round(float(row.get("memory_percent") or 0), 2)
            out.append(row)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    out.sort(
        key=lambda row: (row.get("memory_percent", 0), row.get("cpu_percent", 0)),
        reverse=True,
    )
    return out[: max(1, min(int(limit), 2000))]


def process_info(pid: int) -> dict[str, Any]:
    if psutil is None:
        raise RuntimeError("psutil is not installed")
    proc = psutil.Process(int(pid))
    with proc.oneshot():
        return {
            "pid": proc.pid,
            "ppid": proc.ppid(),
            "name": proc.name(),
            "username": proc.username(),
            "status": proc.status(),
            "cwd": proc.cwd() if proc.is_running() else None,
            "cmdline": proc.cmdline(),
            "create_time": proc.create_time(),
            "cpu_percent": proc.cpu_percent(interval=0.05),
            "memory_percent": proc.memory_percent(),
            "children": [child.pid for child in proc.children(recursive=True)],
        }


def process_kill(pid: int, sig: int = signal.SIGTERM) -> dict[str, Any]:
    if psutil is None:
        raise RuntimeError("psutil is not installed")
    proc = psutil.Process(int(pid))
    if proc.pid in {os.getpid(), os.getppid()}:
        raise PermissionError("Loopback refuses to kill itself or its parent process")
    name = proc.name()
    proc.send_signal(int(sig))
    return {"pid": proc.pid, "name": name, "signal": int(sig)}


def host_metrics() -> dict[str, Any]:
    if psutil is None:
        return {"available": False, "reason": "psutil is not installed"}
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home()))
    net = psutil.net_io_counters()
    boot = psutil.boot_time()
    import time

    return {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=0.05),
        "loadavg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "memory": {
            "total": vm.total,
            "available": vm.available,
            "used": vm.used,
            "percent": vm.percent,
        },
        "disk_home": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
        },
        "boot_time": boot,
        "uptime_seconds": max(0, time.time() - boot),
        "process_count": len(psutil.pids()),
    }
