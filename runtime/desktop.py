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

    def status(self) -> dict[str, Any]:
        system = platform.system()
        display = os.environ.get("DISPLAY", "")
        xdotool = shutil.which("xdotool")
        wmctrl = shutil.which("wmctrl")
        screenshot = self._screenshot_program()
        available = system == "Linux" and bool(display) and bool(xdotool)
        return {
            "available": available,
            "platform": system,
            "display": display or None,
            "xdotool": xdotool,
            "wmctrl": wmctrl,
            "screenshot_program": screenshot[0] if screenshot else None,
            "reason": None if available else "native desktop automation currently requires Linux/X11 and xdotool",
        }

    def _require(self) -> str:
        status = self.status()
        if not status["available"]:
            raise RuntimeError(status["reason"])
        return str(status["xdotool"])

    def _screenshot_program(self) -> tuple[str, list[str]] | None:
        candidates = [
            ("xfce4-screenshooter", ["-f", "-s"]),
            ("scrot", []),
            ("gnome-screenshot", ["-f"]),
        ]
        for name, args in candidates:
            path = shutil.which(name)
            if path:
                return path, args
        return None

    def windows(self, limit: int = 200) -> dict[str, Any]:
        self._require()
        wmctrl = shutil.which("wmctrl")
        rows: list[dict[str, Any]] = []
        if wmctrl:
            proc = subprocess.run([wmctrl, "-lp"], text=True, capture_output=True, timeout=10)
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
            xdotool = self._require()
            proc = subprocess.run(
                [xdotool, "search", "--onlyvisible", "--name", ".*"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            for raw in proc.stdout.splitlines()[: max(1, min(int(limit), 1000))]:
                if not raw.isdigit():
                    continue
                title = subprocess.run(
                    [xdotool, "getwindowname", raw],
                    text=True,
                    capture_output=True,
                    timeout=5,
                ).stdout.strip()
                rows.append({"window_id": raw, "title": title})
        return {"returned": len(rows), "windows": rows}

    def screenshot(self, output: Path) -> Path:
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
        proc = subprocess.run(args, text=True, capture_output=True, timeout=30)
        if proc.returncode or not output.exists():
            raise RuntimeError((proc.stderr or proc.stdout or "screenshot failed").strip())
        return output

    def click(self, x: int, y: int, button: int = 1) -> dict[str, Any]:
        exe = self._require()
        b = int(button)
        if b < 1 or b > 5:
            raise ValueError("button must be 1..5")
        proc = subprocess.run(
            [exe, "mousemove", "--sync", str(int(x)), str(int(y)), "click", str(b)],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"x": int(x), "y": int(y), "button": b}

    def type_text(self, text: str, delay_ms: int = 10) -> dict[str, Any]:
        exe = self._require()
        delay = max(0, min(int(delay_ms), 1000))
        proc = subprocess.run(
            [exe, "type", "--clearmodifiers", "--delay", str(delay), "--", text],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"typed_chars": len(text), "delay_ms": delay}

    def key(self, keys: str) -> dict[str, Any]:
        exe = self._require()
        value = keys.strip()
        if not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9_+\-.,:/ ]+", value):
            raise ValueError("unsupported key expression")
        proc = subprocess.run(
            [exe, "key", "--clearmodifiers", value],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"keys": value}

    def activate_window(self, window_id: str) -> dict[str, Any]:
        exe = self._require()
        value = window_id.strip()
        if not re.fullmatch(r"(0x[0-9a-fA-F]+|[0-9]+)", value):
            raise ValueError("invalid window id")
        proc = subprocess.run(
            [exe, "windowactivate", "--sync", value],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout).strip())
        return {"window_id": value, "activated": True}
