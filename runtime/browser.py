from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from url_policy import UrlPolicy, UrlPolicyError

SUPPORTED_ACTIONS = ["open", "snapshot", "click", "fill", "get_url", "close"]


class BrowserAdapter:
    """Structured adapter around the optional agent-browser CLI.

    Loopback deliberately does not expose arbitrary JavaScript evaluation here.
    The adapter is capability-detected and disabled by policy unless explicitly
    enabled.

    Sessions are namespaced by the caller's stable owner key, so one client can
    never name, read or close another client's browser session (H06). Every
    navigation passes the parsed URL policy and is re-checked after redirects (H04).
    agent-browser keeps a separate profile per session; Loopback never passes a
    personal browser profile.
    """

    def __init__(self, executable: str = "agent-browser", *, url_policy: Callable[[], UrlPolicy] | None = None,
                 session_ttl: float = 1800, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None):
        self.executable = executable
        self.url_policy = url_policy or UrlPolicy
        self.session_ttl = session_ttl
        self._runner = runner or subprocess.run
        self._which: Callable[[str], str | None] = shutil.which
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.last_verified: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        path = self._which(self.executable)
        return {
            "available": bool(path),
            "executable": path,
            "dependency_installed": bool(path),
            "functional": self.last_verified,
            "supported_actions": SUPPORTED_ACTIONS if path else [],
            "session_ttl_seconds": int(self.session_ttl),
            "open_sessions": len(self._sessions),
            "remedy": None if path else "install agent-browser and its browser engine with the opt-in installer, then enable browser tools in policy",
        }

    @staticmethod
    def _session_name(session: str, owner: str | None) -> str:
        name = (session or "").strip() or "loopback"
        if len(name) > 48 or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid browser session name")
        if owner is None:
            return name
        tag = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:10]
        return f"lb{tag}-{name}"

    def _run(self, session: str, args: list[str], timeout: int = 60, *, owner: str | None = None) -> str:
        path = self._which(self.executable)
        if not path:
            raise RuntimeError(
                "agent-browser is not installed; install it separately before enabling browser tools"
            )
        name = self._session_name(session, owner)
        self.expire_sessions()
        proc = self._runner(
            [path, "--session", name, *args],
            text=True,
            capture_output=True,
            timeout=max(1, min(int(timeout), 300)),
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:8000])
        with self._lock:
            self._sessions.setdefault(name, {"owner": owner, "started_at": time.time()})["last_used"] = time.time()
        return proc.stdout

    def _require_session(self, session: str, owner: str | None) -> None:
        name = self._session_name(session, owner)
        with self._lock:
            if name not in self._sessions:
                raise KeyError("browser session not found; open a URL first")

    def expire_sessions(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        with self._lock:
            stale = [n for n, s in self._sessions.items() if self.session_ttl and now - s.get("last_used", 0) > self.session_ttl]
        path = self._which(self.executable)
        for name in stale:
            if path:
                try:
                    self._runner([path, "--session", name, "close"], text=True, capture_output=True, timeout=15)
                except (OSError, subprocess.SubprocessError):
                    pass
            with self._lock:
                self._sessions.pop(name, None)
        return stale

    def open(self, url: str, session: str = "loopback", *, owner: str | None = None) -> dict[str, Any]:
        checked = self.url_policy().check(url)
        out = self._run(session, ["open", url], owner=owner)
        final = self._run(session, ["get", "url"], owner=owner).strip()
        if final and final != url:
            try:
                self.url_policy().check(final)
            except UrlPolicyError as exc:
                self._close_quietly(session, owner)
                raise UrlPolicyError(exc.code, f"navigation ended at a URL outside policy and the session was closed ({exc})") from None
        return {"session": session, "url": url, "final_url": final or url, "exposure": checked["exposure"], "output": out[-4000:]}

    def _close_quietly(self, session: str, owner: str | None) -> None:
        try:
            self._run(session, ["close"], owner=owner)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
        with self._lock:
            self._sessions.pop(self._session_name(session, owner), None)

    def snapshot(self, session: str = "loopback", interactive_only: bool = True, *, owner: str | None = None) -> dict[str, Any]:
        self._require_session(session, owner)
        args = ["snapshot"]
        if interactive_only:
            args.append("-i")
        out = self._run(session, args, owner=owner)
        return {"session": session, "snapshot": out[-131072:]}

    def click(self, ref: str, session: str = "loopback", *, owner: str | None = None) -> dict[str, Any]:
        if not ref.startswith("@e") or not ref[2:].isdigit():
            raise ValueError("browser ref must look like @e12")
        self._require_session(session, owner)
        out = self._run(session, ["click", ref], owner=owner)
        self._recheck(session, owner)
        return {"session": session, "ref": ref, "output": out[-4000:]}

    def fill(self, ref: str, text: str, session: str = "loopback", *, owner: str | None = None) -> dict[str, Any]:
        if not ref.startswith("@e") or not ref[2:].isdigit():
            raise ValueError("browser ref must look like @e12")
        self._require_session(session, owner)
        out = self._run(session, ["fill", ref, text], owner=owner)
        return {"session": session, "ref": ref, "output": out[-4000:]}

    def _recheck(self, session: str, owner: str | None) -> None:
        """A click can navigate: close the session if it left the permitted URL space."""
        final = self._run(session, ["get", "url"], owner=owner).strip()
        if not final or final.startswith("about:"):
            return
        try:
            self.url_policy().check(final)
        except UrlPolicyError as exc:
            self._close_quietly(session, owner)
            raise UrlPolicyError(exc.code, f"page navigated outside policy and the session was closed ({exc})") from None

    def url(self, session: str = "loopback", *, owner: str | None = None) -> dict[str, Any]:
        self._require_session(session, owner)
        out = self._run(session, ["get", "url"], owner=owner)
        return {"session": session, "url": out.strip()}

    def close(self, session: str = "loopback", *, owner: str | None = None) -> dict[str, Any]:
        self._require_session(session, owner)
        out = self._run(session, ["close"], owner=owner)
        with self._lock:
            self._sessions.pop(self._session_name(session, owner), None)
        return {"session": session, "output": out[-4000:]}
