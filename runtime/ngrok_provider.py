"""An explicitly configured ngrok endpoint, with process ownership and no raw logs."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


def process_identity(pid: int) -> str | None:
    """Return a process birth identity. A PID alone is never permission to kill."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return "proc:" + fields[19]
    except (OSError, IndexError):
        pass
    try:
        if os.name == "nt":
            command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                       f"(Get-Process -Id {int(pid)} -ErrorAction Stop).StartTime.ToUniversalTime().Ticks"]
        else:
            command = ["ps", "-p", str(pid), "-o", "lstart="]
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL)
        return "birth:" + result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class NgrokMixin:
    """Methods use IngressManager's lock, config transaction helpers and context."""

    @contextlib.contextmanager
    def _ngrok_guard(self):
        """Serialize helpers and dashboard requests, including across processes."""
        with self._lock:
            if getattr(self, "_ngrok_lock_depth", 0):
                self._ngrok_lock_depth += 1
                try:
                    yield
                finally:
                    self._ngrok_lock_depth -= 1
                return
            self.cfg_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.cfg_dir / "ngrok.lock", os.O_CREAT | os.O_RDWR, 0o600)
            locked = False
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                deadline = time.monotonic() + 5
                while not locked:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError("Another ngrok operation is still in progress") from None
                        time.sleep(0.05)
                self._ngrok_lock_depth = 1
                try:
                    yield
                finally:
                    self._ngrok_lock_depth = 0
            finally:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _ngrok_marker(self) -> Path:
        return self.cfg_dir / "ngrok-process.json"

    def _owned_ngrok_pid(self) -> int | None:
        proc = getattr(self, "_ngrok_proc", None)
        if proc is not None and proc.poll() is not None:
            self._ngrok_proc = None
        try:
            marker = json.loads(self._ngrok_marker().read_text())
            pid = marker["pid"]
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                return None
            birth = process_identity(pid)
            if birth and birth == marker.get("birth"):
                return pid
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def ngrok_status(self, check_health: bool = True) -> dict[str, Any]:
        from ingress import http_health
        cfg = self._cfg("ngrok")
        pid = self._owned_ngrok_pid()
        enabled = bool(cfg.get("enabled"))
        host = cfg.get("hostname")
        out = {"enabled": enabled, "hostname": host, "installed": bool(shutil.which("ngrok")),
               "running": bool(pid), "exposure": "public", "public_health": None,
               "last_error": self._last_error.get("ngrok"),
               "status": "active-unverified" if pid else "stopped" if enabled else "disabled"}
        if enabled and pid and host and check_health:
            out["public_health"] = http_health(f"https://{host}/healthz")
            # A successful health response is not proof of an authenticated MCP session.
            out["status"] = "active" if out["public_health"].get("ok") else "degraded"
        return out

    def start_ngrok(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        from ingress import FQDN_RE, _atomic_write
        cfg = config if config is not None else self._cfg("ngrok")
        with self._ngrok_guard():
            if self._owned_ngrok_pid():
                return {"ok": True, "action": "already-running"}
            if not cfg.get("enabled"):
                return {"ok": True, "action": "disabled"}
            host = cfg.get("hostname")
            if not isinstance(host, str) or not FQDN_RE.fullmatch(host):
                raise ValueError("ngrok requires a fixed fully-qualified hostname")
            binary = shutil.which("ngrok")
            if not binary:
                raise ValueError("ngrok is not installed; install the official ngrok agent first")
            path = self._expand(str(cfg.get("config_file") or "")).resolve()
            if not path.is_file() or "," in str(path):
                raise ValueError("ngrok config_file must be a readable v3 configuration file without commas in its path")
            # A second config overrides inspection and remote agent control. It contains no credentials.
            overlay = (self.cfg_dir / "ngrok-agent.json").resolve()
            if path == overlay or "," in str(overlay):
                raise ValueError("Choose a separate ngrok credentials file and a configuration directory without commas")
            _atomic_write(overlay, json.dumps({"version": "3", "agent": {"web_addr": False,
                          "console_ui": False, "remote_management": False, "update_check": False,
                          "log": "stderr", "log_level": "warn"}}))
            args = [binary, "http", f"http://127.0.0.1:{self.local_port}", "--url", f"https://{host}",
                    "--config", f"{path},{overlay}", "--inspect=false", "--log", "stderr"]
            options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                       "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                options["start_new_session"] = True
            proc = subprocess.Popen(args, **options)
            self._ngrok_proc = proc
            try:
                birth = process_identity(proc.pid)
                if not birth:
                    raise ValueError("Cannot verify ngrok process ownership on this platform")
                _atomic_write(self._ngrok_marker(), json.dumps({"pid": proc.pid, "birth": birth}))
                time.sleep(0.2)
                if proc.poll() is not None:
                    raise ValueError("ngrok exited during startup. Check your v3 config, account and reserved hostname with the official CLI")
            except Exception:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                self._ngrok_proc = None
                self._ngrok_marker().unlink(missing_ok=True)
                raise
            self._last_error["ngrok"] = None
            return {"ok": True, "action": "started", "verification": "Process started. Test the authenticated MCP connection before use."}

    def stop_ngrok(self, timeout: float = 5.0) -> dict[str, Any]:
        with self._ngrok_guard():
            pid = self._owned_ngrok_pid()
            if pid:
                proc = getattr(self, "_ngrok_proc", None)
                if proc is not None and proc.pid == pid:
                    proc.terminate()
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                else:
                    birth = json.loads(self._ngrok_marker().read_text())["birth"]
                    if process_identity(pid) != birth:
                        raise ValueError("ngrok process ownership changed; refusing to signal it")
                    try:
                        os.kill(pid, signal.SIGTERM)
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline and process_identity(pid) == birth:
                            time.sleep(0.1)
                        if process_identity(pid) == birth:
                            os.kill(pid, signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and process_identity(pid) == birth:
                        time.sleep(0.1)
                    if process_identity(pid) == birth:
                        raise ValueError("Owned ngrok process did not stop; ownership record retained")
            self._ngrok_proc = None
            self._ngrok_marker().unlink(missing_ok=True)
            return {"ok": True, "stopped": bool(pid)}

    def apply_ngrok(self, enabled: bool, hostname: str | None = None, config_file: str | None = None,
                    confirm_public: bool = False) -> dict[str, Any]:
        from ingress import FQDN_RE, IngressError
        if not isinstance(enabled, bool):
            raise IngressError("enabled must be a boolean")
        if enabled and not confirm_public:
            raise IngressError("ngrok exposes Loopback to the public internet. Set confirm_public=true to confirm")
        prev = self._cfg("ngrok")
        updates = {**prev, "enabled": enabled}
        if hostname is not None:
            updates["hostname"] = hostname.strip().lower().rstrip(".")
        if config_file is not None:
            updates["config_file"] = str(self._expand(config_file).resolve())
        if enabled and (not isinstance(updates.get("hostname"), str) or not FQDN_RE.fullmatch(updates["hostname"])):
            raise IngressError("ngrok requires a fixed fully-qualified hostname from your account")
        with self._ngrok_guard():
            marker = self._backup_marker()
            try:
                self.stop_ngrok()
                if enabled:
                    self.start_ngrok(updates)
                self.ctx.config.save({"ingress": {"ngrok": updates}}, actor="dashboard")
                self.ctx.refresh_auth_urls()
            except Exception as exc:
                self.stop_ngrok()
                self._restore_section("ngrok", prev, marker)
                if prev.get("enabled"):
                    try:
                        self.start_ngrok()
                    except Exception:
                        pass
                # Provider output is deliberately discarded. It can contain credentials.
                message = str(exc) if isinstance(exc, ValueError) else "ngrok configuration failed; previous settings were restored"
                self._last_error["ngrok"] = message
                raise IngressError(message) from None
        return {"ok": True, "ngrok": self.ngrok_status(check_health=False)}

    def stop_all(self) -> dict[str, Any]:
        """Stop only Loopback-owned processes. Existing Serve/Funnel routes remain intact."""
        ngrok = self.stop_ngrok()
        cloudflare = self.stop_cloudflare()
        return {"ok": bool(ngrok.get("ok") and cloudflare.get("ok")), "ngrok": ngrok, "cloudflare": cloudflare}
