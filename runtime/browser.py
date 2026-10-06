from __future__ import annotations

import shutil
import subprocess
from typing import Any


class BrowserAdapter:
    """Structured adapter around the optional agent-browser CLI.

    Loopback deliberately does not expose arbitrary JavaScript evaluation here.
    The adapter is capability-detected and disabled by policy unless explicitly
    enabled.
    """

    def __init__(self, executable: str = "agent-browser"):
        self.executable = executable

    def status(self) -> dict[str, Any]:
        path = shutil.which(self.executable)
        return {"available": bool(path), "executable": path}

    def _run(self, session: str, args: list[str], timeout: int = 60) -> str:
        path = shutil.which(self.executable)
        if not path:
            raise RuntimeError(
                "agent-browser is not installed; install it separately before enabling browser tools"
            )
        name = session.strip() or "loopback"
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid browser session name")
        proc = subprocess.run(
            [path, "--session", name, *args],
            text=True,
            capture_output=True,
            timeout=max(1, min(int(timeout), 300)),
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:8000])
        return proc.stdout

    def open(self, url: str, session: str = "loopback") -> dict[str, Any]:
        if not (url.startswith("https://") or url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
            raise ValueError("browser_open requires HTTPS or a loopback HTTP URL")
        out = self._run(session, ["open", url])
        return {"session": session, "url": url, "output": out[-4000:]}

    def snapshot(self, session: str = "loopback", interactive_only: bool = True) -> dict[str, Any]:
        args = ["snapshot"]
        if interactive_only:
            args.append("-i")
        out = self._run(session, args)
        return {"session": session, "snapshot": out[-131072:]}

    def click(self, ref: str, session: str = "loopback") -> dict[str, Any]:
        if not ref.startswith("@e") or not ref[2:].isdigit():
            raise ValueError("browser ref must look like @e12")
        out = self._run(session, ["click", ref])
        return {"session": session, "ref": ref, "output": out[-4000:]}

    def fill(self, ref: str, text: str, session: str = "loopback") -> dict[str, Any]:
        if not ref.startswith("@e") or not ref[2:].isdigit():
            raise ValueError("browser ref must look like @e12")
        out = self._run(session, ["fill", ref, text])
        return {"session": session, "ref": ref, "output": out[-4000:]}

    def url(self, session: str = "loopback") -> dict[str, Any]:
        out = self._run(session, ["get", "url"])
        return {"session": session, "url": out.strip()}

    def close(self, session: str = "loopback") -> dict[str, Any]:
        out = self._run(session, ["close"])
        return {"session": session, "output": out[-4000:]}
