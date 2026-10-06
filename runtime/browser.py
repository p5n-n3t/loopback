from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Any
from urllib.parse import urlparse


class BrowserController:
    def __init__(self) -> None:
        configured = os.environ.get("LOOPBACK_BROWSER_COMMAND", "agent-browser")
        self.base = shlex.split(configured)

    def executable(self) -> str | None:
        if not self.base:
            return None
        return shutil.which(self.base[0])

    def status(self) -> dict[str, Any]:
        exe = self.executable()
        if not exe:
            return {"available": False, "command": self.base[0] if self.base else None}
        proc = subprocess.run([*self.base, "--version"], text=True, capture_output=True, timeout=10)
        return {
            "available": proc.returncode == 0,
            "command": exe,
            "version": (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else None,
        }

    def _run(self, session: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
        if not self.executable():
            raise RuntimeError("agent-browser is not installed; install it separately or set LOOPBACK_BROWSER_COMMAND")
        if not session or len(session) > 80:
            raise ValueError("invalid browser session")
        proc = subprocess.run(
            [*self.base, "--session", session, *args],
            text=True,
            capture_output=True,
            timeout=max(1, min(int(timeout), 300)),
        )
        return {
            "session": session,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def open(self, url: str, session: str = "loopback") -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("browser_open requires an http(s) URL")
        return self._run(session, ["open", url], 90)

    def snapshot(self, session: str = "loopback", interactive: bool = True) -> dict[str, Any]:
        args = ["snapshot"]
        if interactive:
            args.append("-i")
        return self._run(session, args, 60)

    def click(self, selector: str, session: str = "loopback") -> dict[str, Any]:
        return self._run(session, ["click", selector], 60)

    def fill(self, selector: str, text: str, session: str = "loopback") -> dict[str, Any]:
        return self._run(session, ["fill", selector, text], 60)

    def get(self, what: str, selector: str | None = None, session: str = "loopback") -> dict[str, Any]:
        allowed = {"text", "html", "value", "title", "url", "count"}
        if what not in allowed:
            raise ValueError(f"browser_get what must be one of: {', '.join(sorted(allowed))}")
        args = ["get", what]
        if selector:
            args.append(selector)
        return self._run(session, args, 60)

    def close(self, session: str = "loopback") -> dict[str, Any]:
        return self._run(session, ["close"], 30)
