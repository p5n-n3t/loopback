from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class DesktopAdapter:
    """Optional native desktop adapter.

    v3 intentionally starts with Linux/X11 because command-line automation is
    explicit and auditable there. Other platforms report unavailable rather
    than silently falling back to unsafe heuristics.
    """

    def __init__(self, *, runner: Any = None, which: Any = None, environ: dict[str, str] | None = None,
                 system: str | None = None, probe_ttl: float = 30.0):
        self._runner = runner or subprocess.run
        self._which = which or shutil.which
        self._environ = environ
        self._system = system
        self.probe_ttl = probe_ttl
        self._probe_cache: tuple[float, str, dict[str, Any]] | None = None

    @property
    def env(self) -> dict[str, str]:
        return dict(os.environ) if self._environ is None else self._environ

    def _probe_x11(self, xdotool: str, display: str) -> dict[str, Any]:
        """Bounded check that the X server named by DISPLAY accepts this service's connection.

        A non-empty DISPLAY string is not proof of a usable session (H08).
        """
        now = time.monotonic()
        if self._probe_cache and self._probe_cache[1] == display and now - self._probe_cache[0] < self.probe_ttl:
            return self._probe_cache[2]
        try:
            proc = self._runner([xdotool, "getdisplaygeometry"], text=True, capture_output=True, timeout=3,
                                env={**self.env, "DISPLAY": display})
            if proc.returncode == 0 and re.fullmatch(r"\d+ \d+", (proc.stdout or "").strip()):
                w, h = proc.stdout.split()
                result: dict[str, Any] = {"state": "connected", "geometry": [int(w), int(h)]}
            else:
                detail = (proc.stderr or proc.stdout or "").strip()[:200]
                result = {"state": "refused", "detail": detail or f"exit {proc.returncode}"}
        except subprocess.TimeoutExpired:
            result = {"state": "timeout", "detail": "X server did not answer within 3 seconds"}
        except OSError as exc:
            result = {"state": "error", "detail": str(exc)[:200]}
        result["checked_at"] = time.time()
        self._probe_cache = (now, display, result)
        return result

    def status(self) -> dict[str, Any]:
        system = self._system or platform.system()
        env = self.env
        display = env.get("DISPLAY", "")
        session_type = (env.get("XDG_SESSION_TYPE") or "").lower()
        wayland = bool(env.get("WAYLAND_DISPLAY")) or session_type == "wayland"
        xdotool = self._which("xdotool")
        wmctrl = self._which("wmctrl")
        screenshot = self._screenshot_program()
        reason: str | None = None
        x11: dict[str, Any] = {"state": "not_checked"}
        if system != "Linux":
            reason = f"native desktop automation is not supported on {system}; only Linux/X11 is implemented"
        elif wayland:
            reason = "a Wayland session is active; Loopback supports only X11 and does not drive Wayland or XWayland windows"
        elif not display:
            reason = "no X11 DISPLAY is available to the Loopback service; an authorized session broker is required"
        elif not xdotool:
            reason = "xdotool is not installed; install it to enable desktop input and window listing"
        else:
            x11 = self._probe_x11(xdotool, display)
            if x11["state"] != "connected":
                reason = f"the X server for DISPLAY {display} is not usable by this service ({x11['state']}): {x11.get('detail', '')}".strip()
        session_ok = reason is None

        def op(ready: bool, missing: str | None = None) -> dict[str, Any]:
            if not session_ok:
                return {"ready": False, "reason": reason}
            return {"ready": ready, "reason": None if ready else missing}

        operations = {
            "windows": op(bool(xdotool or wmctrl), "install wmctrl or xdotool"),
            "click": op(bool(xdotool), "install xdotool"),
            "type": op(bool(xdotool), "install xdotool"),
            "key": op(bool(xdotool), "install xdotool"),
            "activate_window": op(bool(xdotool), "install xdotool"),
            "screenshot": op(bool(screenshot), "install scrot, gnome-screenshot or xfce4-screenshooter"),
        }
        return {
            "available": session_ok,
            "platform": system,
            "display": display or None,
            "session_type": session_type or None,
            "wayland": wayland,
            "x11": x11,
            "session_source": "service environment (unverified; no session broker)" if display else None,
            "xdotool": xdotool,
            "wmctrl": wmctrl,
            "screenshot_program": screenshot[0] if screenshot else None,
            "operations": operations,
            "reason": reason,
        }

    def _require(self, operation: str = "click") -> str:
        status = self.status()
        entry = status["operations"].get(operation) or {"ready": status["available"], "reason": status["reason"]}
        if not entry["ready"]:
            raise RuntimeError(f"desktop_{operation}_unavailable: {entry['reason']}")
        return str(status["xdotool"] or "")

    def _screenshot_program(self) -> tuple[str, list[str]] | None:
        candidates = [
            ("xfce4-screenshooter", ["-f", "-s"]),
            ("scrot", []),
            ("gnome-screenshot", ["-f"]),
        ]
        for name, args in candidates:
            path = self._which(name)
            if path:
                return path, args
        return None

    def windows(self, limit: int = 200) -> dict[str, Any]:
        self._require("windows")
        wmctrl = self._which("wmctrl")
        rows: list[dict[str, Any]] = []
        if wmctrl:
            proc = self._runner([wmctrl, "-lp"], text=True, capture_output=True, timeout=10)
            if proc.returncode:
                raise RuntimeError((proc.stderr or proc.stdout).strip())
            for line in proc.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                wid, desktop, pid, host, title = parts
                rows.append({
                    "window_id": wid,
                    "desktop": desktop,
                    "pid": int(pid) if pid.isdigit() else None,
                    "host": host,
                    "title": title,
                })
                if len(rows) >= max(1, min(int(limit), 1000)):
                    break
        else:
            xdotool = self._require("click")
            proc = self._runner(
                [xdotool, "search", "--onlyvisible", "--name", ".*"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            for raw in proc.stdout.splitlines()[: max(1, min(int(limit), 1000))]:
                if not raw.isdigit():
                    continue
                title = self._runner(
                    [xdotool, "getwindowname", raw],
                    text=True,
                    capture_output=True,
                    timeout=5,
                ).stdout.strip()
                rows.append({"window_id": raw, "title": title})
        return {"returned": len(rows), "windows": rows}

    def screenshot(self, output: Path) -> Path:
        self._require("screenshot")
        program = self._screenshot_program()
        if not program:
            raise RuntimeError("no supported screenshot utility found")
        exe, prefix = program
        output.parent.mkdir(parents=True, exist_ok=True)
        if Path(exe).name == "xfce4-screenshooter":
            args = [exe, *prefix, str(output)]
        elif Path(exe).name == "scrot":
            args = [exe, str(output)]
        else:
            args = [exe, *prefix, str(output)]
        proc = self._runner(args, text=True, capture_output=True, timeout=30)
        if proc.returncode or not output.exists():
            raise RuntimeError((proc.stderr or proc.stdout or "screenshot failed").strip())
        return output

    def click(self, x: int, y: int, button: int = 1) -> dict[str, Any]:
        exe = self._require("click")
        b = int(button)
        if b < 1 or b > 5:
            raise ValueError("button must be 1..5")
        proc = self._runner(
            [exe, "mousemove", "--sync", str(int(x)), str(int(y)), "click", str(b)],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"x": int(x), "y": int(y), "button": b}

    def type_text(self, text: str, delay_ms: int = 10) -> dict[str, Any]:
        exe = self._require("type")
        delay = max(0, min(int(delay_ms), 1000))
        proc = self._runner(
            [exe, "type", "--clearmodifiers", "--delay", str(delay), "--", text],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"typed_chars": len(text), "delay_ms": delay}

    def key(self, keys: str) -> dict[str, Any]:
        exe = self._require("key")
        value = keys.strip()
        if not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9_+\-.,:/ ]+", value):
            raise ValueError("unsupported key expression")
        proc = self._runner(
            [exe, "key", "--clearmodifiers", value],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"keys": value}

    def activate_window(self, window_id: str) -> dict[str, Any]:
        exe = self._require("activate_window")
        value = window_id.strip()
        if not re.fullmatch(r"(0x[0-9a-fA-F]+|[0-9]+)", value):
            raise ValueError("invalid window id")
        proc = self._runner(
            [exe, "windowactivate", "--sync", value],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"window_id": value, "activated": True}
