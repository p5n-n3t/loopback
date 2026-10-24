"""Cross-platform service management for the Loopback gateway.

``ServiceManager`` installs, starts, stops and inspects the long-running
uvicorn process that serves ``server:app`` from the configuration directory.

Supported supervisors (``ServiceManager.detect()``):

* ``systemd-user``  - ``~/.config/systemd/user/loopback.service`` (Type=simple)
* ``launchd``       - ``~/Library/LaunchAgents/io.loopback.gateway.plist``
* ``windows-task-scheduler`` - an ONLOGON task named ``Loopback``
* ``none``          - a detached ("nohup") child tracked by ``<cfg>/server.pid``

A v3 install used a ``Type=oneshot`` systemd unit that called ``loopback up``
and ``loopback down``. While such a legacy unit is still installed, process
control falls back to the pid-file supervisor so that the unit's
``ExecStop=loopback down`` can never recurse into ``systemctl``.

The module only uses the standard library and has no import-time side
effects, so the CLI can import it with the system Python before the venv
exists.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

UNIT_NAME = "loopback.service"
LAUNCHD_LABEL = "io.loopback.gateway"
WINDOWS_TASK = "Loopback"
WINDOWS = sys.platform.startswith("win")
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
LOG_ROTATE_BYTES = 5 * 1024 * 1024


def _run(cmd: list[str], timeout: float = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a command, never raising for a non-zero exit or a missing binary."""
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s: {exc}")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", str(exc))


def atomic_write(path: Path, data: str | bytes, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` via a temp file + ``os.replace`` with ``mode``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def venv_python(cfg_dir: Path | str, windowed: bool = False) -> Path:
    """Path of the interpreter inside ``<cfg>/venv`` for this platform."""
    cfg = Path(cfg_dir)
    if WINDOWS:
        return cfg / "venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return cfg / "venv" / "bin" / "python"


def pid_alive(pid: int | None) -> bool:
    """True when a process with ``pid`` exists (and is not a zombie on Linux)."""
    if not pid or pid <= 0:
        return False
    if WINDOWS:
        res = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], timeout=10)
        return f'"{pid}"' in (res.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[-1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    return True


def process_cmdline(pid: int) -> str:
    """Best-effort command line of ``pid`` ('' when unknown)."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        pass
    if WINDOWS:
        res = _run(["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"], timeout=10)
        return res.stdout or ""
    res = _run(["ps", "-o", "command=", "-p", str(pid)], timeout=5)
    return (res.stdout or "").strip()


class ServiceManager:
    """Install and control the Loopback gateway process for one account.

    ``identity`` is a ``RuntimeIdentity`` (or any object with ``home``,
    ``uid``, ``username`` and ``platform`` attributes); ``cfg_dir`` is the
    Loopback configuration directory; ``port`` the local listener port.
    """

    def __init__(self, identity: Any, cfg_dir: Path | str, port: int = 2026):
        self.identity = identity
        self.cfg_dir = Path(cfg_dir)
        self.port = int(port)
        self.home = Path(getattr(identity, "home", None) or Path.home())
        self.platform = getattr(identity, "platform", None) or ("windows" if WINDOWS else sys.platform)
        self.uid = getattr(identity, "uid", None)
        if self.uid is None and hasattr(os, "getuid"):
            self.uid = os.getuid()
        self.username = getattr(identity, "username", "") or ""
        self.pid_path = self.cfg_dir / "server.pid"
        self.log_path = self.cfg_dir / "server.log"
        self.unit_backup_dir = self.cfg_dir / "backups" / "units"
        self._manager: str | None = None

    # ------------------------------------------------------------ paths
    @property
    def unit_path(self) -> Path:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else self.home / ".config"
        return base / "systemd" / "user" / UNIT_NAME

    @property
    def plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

    def python(self) -> Path:
        """Interpreter used to run the gateway.

        Order: ``LOOPBACK_PYTHON`` env, ``<cfg>/venv``, then the test-only
        ``<cfg>/python-override`` file written by ``cli.py install --skip-venv``.
        """
        override = os.environ.get("LOOPBACK_PYTHON")
        if override:
            return Path(override)
        venv = venv_python(self.cfg_dir)
        if not venv.exists():
            try:
                alt = (self.cfg_dir / "python-override").read_text(encoding="utf-8").strip()
                if alt and Path(alt).exists():
                    return Path(alt)
            except OSError:
                pass
        return venv

    def uvicorn_args(self, port: int | None = None) -> list[str]:
        """uvicorn arguments shared by every supervisor (v3-compatible layout)."""
        return ["-m", "uvicorn", "server:app", "--app-dir", str(self.cfg_dir), "--host", "127.0.0.1",
                "--port", str(port or self.port), "--proxy-headers", "--forwarded-allow-ips=127.0.0.1",
                "--log-level", "info"]

    # ------------------------------------------------------------ detection
    def detect(self) -> str:
        """Return systemd-user | launchd | windows-task-scheduler | none."""
        if self._manager is not None:
            return self._manager
        forced = os.environ.get("LOOPBACK_SERVICE_MANAGER")
        if forced in {"systemd-user", "launchd", "windows-task-scheduler", "none"}:
            self._manager = forced
            return forced
        manager = "none"
        if self.platform == "linux":
            if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
                res = _run(["systemctl", "--user", "show-environment"], timeout=10)
                if res.returncode == 0:
                    manager = "systemd-user"
        elif self.platform == "darwin":
            if shutil.which("launchctl"):
                manager = "launchd"
        elif self.platform == "windows":
            if shutil.which("schtasks"):
                manager = "windows-task-scheduler"
        self._manager = manager
        return manager

    def legacy_unit(self) -> bool:
        """True when the installed systemd unit is the v3 Type=oneshot wrapper."""
        try:
            text = self.unit_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return "Type=oneshot" in text and "loopback up" in text

    def installed(self) -> bool:
        manager = self.detect()
        if manager == "systemd-user":
            return self.unit_path.exists() and not self.legacy_unit()
        if manager == "launchd":
            return self.plist_path.exists()
        if manager == "windows-task-scheduler":
            return _run(["schtasks", "/Query", "/TN", WINDOWS_TASK], timeout=15).returncode == 0
        return False

    def effective_manager(self) -> str:
        """Supervisor actually controlling the process right now ('none' => pid file)."""
        manager = self.detect()
        if manager != "none" and self.installed():
            return manager
        return "none"

    # ------------------------------------------------------------ unit writing
    def backup_unit(self) -> Path | None:
        """Copy the current unit/plist into ``<cfg>/backups/units`` and return the copy."""
        src = self.unit_path if self.detect() == "systemd-user" else self.plist_path
        if not src.exists():
            return None
        self.unit_backup_dir.mkdir(parents=True, exist_ok=True)
        dest = self.unit_backup_dir / f"{src.name}.{time.strftime('%Y%m%d-%H%M%S')}"
        atomic_write(dest, src.read_bytes())
        return dest

    def restore_unit(self, backup: Path | str | None) -> bool:
        """Put a previously backed-up unit file back and reload the supervisor."""
        if not backup or not Path(backup).exists():
            return False
        manager = self.detect()
        if manager == "systemd-user":
            atomic_write(self.unit_path, Path(backup).read_bytes(), 0o644)
            _run(["systemctl", "--user", "daemon-reload"])
            return True
        if manager == "launchd":
            atomic_write(self.plist_path, Path(backup).read_bytes(), 0o644)
            return True
        return False

    def systemd_unit_text(self, python: str, app_dir: str, port: int, env: dict[str, str]) -> str:
        def spec(value: str) -> str:
            # '%' introduces systemd specifiers; escape it in every setting.
            return value.replace("%", "%%")

        def q(value: str) -> str:
            # ExecStart/Environment split on whitespace and expand '$'; quote and escape.
            value = spec(value).replace("$", "$$")
            if any(c in value for c in " \t\"'\\"):
                return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
            return value

        args = [python, "-m", "uvicorn", "server:app", "--app-dir", app_dir, "--host", "127.0.0.1", "--port",
                str(port), "--proxy-headers", "--forwarded-allow-ips=127.0.0.1", "--log-level", "info"]
        environment = {"LOOPBACK_CONFIG_DIR": app_dir, "PYTHONUNBUFFERED": "1"}
        environment.update({k: v for k, v in (env or {}).items() if v is not None})
        env_lines = "\n".join(f"Environment={q(f'{k}={v}')}" for k, v in sorted(environment.items()))
        log = spec(str(Path(app_dir) / "server.log"))
        return f"""# Managed by Loopback (loopback autostart / loopback repair). Local edits are replaced on upgrade.
[Unit]
Description=Loopback MCP gateway
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart={' '.join(q(a) for a in args)}
Restart=on-failure
RestartSec=3
{env_lines}
WorkingDirectory={spec(str(self.home))}
StandardOutput=append:{log}
StandardError=inherit
KillMode=mixed
TimeoutStopSec=20

[Install]
WantedBy=default.target
"""

    def launchd_plist(self, python: str, app_dir: str, port: int, env: dict[str, str]) -> bytes:
        environment = {"LOOPBACK_CONFIG_DIR": app_dir, "PYTHONUNBUFFERED": "1",
                       "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        environment.update({k: str(v) for k, v in (env or {}).items() if v is not None})
        log = str(Path(app_dir) / "server.log")
        data = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [python, *self.uvicorn_args(port)[:3], "--app-dir", app_dir,
                                 *self.uvicorn_args(port)[5:]],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "WorkingDirectory": str(self.home),
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "EnvironmentVariables": environment,
            "ProcessType": "Background",
        }
        return plistlib.dumps(data)

    def install_unit(self, python: str | Path | None = None, app_dir: str | Path | None = None,
                     port: int | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
        """Write and enable the supervisor definition, then start it.

        Returns ``{ok, manager, path, backup, detail}``. Never prompts.
        """
        python = str(python or self.python())
        app_dir = str(app_dir or self.cfg_dir)
        port = int(port or self.port)
        self.port = port
        env = dict(env or {})
        manager = self.detect()
        out: dict[str, Any] = {"ok": False, "manager": manager, "path": None, "backup": None, "detail": ""}
        if manager == "none":
            out["detail"] = "no supported service manager; use 'loopback up' (pid-file supervisor)"
            return out
        # Stop whatever runs now (v3 oneshot unit or a pid-file server) before swapping supervisors.
        legacy = self.legacy_unit()
        if manager == "systemd-user":
            backup = self.backup_unit()
            out["backup"] = str(backup) if backup else None
            if legacy:
                _run(["systemctl", "--user", "disable", "--now", UNIT_NAME], timeout=60)
            self._stop_pidfile()
            atomic_write(self.unit_path, self.systemd_unit_text(python, app_dir, port, env), 0o644)
            out["path"] = str(self.unit_path)
            reload = _run(["systemctl", "--user", "daemon-reload"])
            enable = _run(["systemctl", "--user", "enable", UNIT_NAME])
            restart = _run(["systemctl", "--user", "restart", UNIT_NAME], timeout=60)
            out["ok"] = reload.returncode == 0 and enable.returncode == 0 and restart.returncode == 0
            out["detail"] = (restart.stderr or enable.stderr or reload.stderr or "").strip()[:500]
            out["linger"] = self.enable_linger()
            return out
        if manager == "launchd":
            backup = self.backup_unit()
            out["backup"] = str(backup) if backup else None
            self._stop_pidfile()
            domain = f"gui/{self.uid}"
            _run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])
            atomic_write(self.plist_path, self.launchd_plist(python, app_dir, port, env), 0o644)
            out["path"] = str(self.plist_path)
            res = _run(["launchctl", "bootstrap", domain, str(self.plist_path)])
            if res.returncode != 0:  # older macOS
                res = _run(["launchctl", "load", "-w", str(self.plist_path)])
            out["ok"] = res.returncode == 0
            out["detail"] = (res.stderr or "").strip()[:500]
            return out
        # Windows Task Scheduler: the task runs "pythonw cli.py serve", which sets
        # LOOPBACK_CONFIG_DIR, writes server.pid and redirects output to server.log.
        self._stop_pidfile()
        pyw = Path(python)
        if pyw.name.lower() == "python.exe" and (pyw.parent / "pythonw.exe").exists():
            pyw = pyw.parent / "pythonw.exe"
        tr = f'"{pyw}" "{Path(app_dir) / "cli.py"}" serve'
        if len(tr) > 261:
            out["detail"] = "task command exceeds the Task Scheduler limit (261 characters); shorten the install path"
            return out
        res = _run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", WINDOWS_TASK, "/TR", tr, "/RL", "LIMITED"])
        out["path"] = f"Task Scheduler: {WINDOWS_TASK}"
        if res.returncode != 0:
            out["detail"] = (res.stderr or res.stdout or "").strip()[:500]
            return out
        run = _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])
        out["ok"] = run.returncode == 0
        out["detail"] = (run.stderr or "").strip()[:500]
        return out

    def enable_linger(self) -> str:
        """Best-effort ``loginctl enable-linger`` (only when sudo works without a prompt)."""
        if not shutil.which("loginctl") or not self.username:
            return "skipped: loginctl not available"
        show = _run(["loginctl", "show-user", self.username, "-p", "Linger", "--value"], timeout=10)
        if show.stdout.strip() == "yes":
            return "already enabled"
        if not shutil.which("sudo") or _run(["sudo", "-n", "true"], timeout=10).returncode != 0:
            return (f"not enabled (needs root): run 'sudo loginctl enable-linger {self.username}' "
                    "so Loopback starts at boot without a login")
        res = _run(["sudo", "-n", "loginctl", "enable-linger", self.username], timeout=20)
        return "enabled" if res.returncode == 0 else f"failed: {res.stderr.strip()[:200]}"

    def uninstall_unit(self) -> dict[str, Any]:
        """Stop and remove the supervisor definition (keeps a backup copy)."""
        manager = self.detect()
        out: dict[str, Any] = {"ok": True, "manager": manager, "backup": None}
        if manager == "systemd-user":
            if self.unit_path.exists():
                backup = self.backup_unit()
                out["backup"] = str(backup) if backup else None
                _run(["systemctl", "--user", "disable", "--now", UNIT_NAME], timeout=60)
                try:
                    self.unit_path.unlink()
                except OSError as exc:
                    out.update(ok=False, detail=str(exc))
                _run(["systemctl", "--user", "daemon-reload"])
        elif manager == "launchd":
            if self.plist_path.exists():
                backup = self.backup_unit()
                out["backup"] = str(backup) if backup else None
                _run(["launchctl", "bootout", f"gui/{self.uid}/{LAUNCHD_LABEL}"])
                try:
                    self.plist_path.unlink()
                except OSError as exc:
                    out.update(ok=False, detail=str(exc))
        elif manager == "windows-task-scheduler":
            _run(["schtasks", "/End", "/TN", WINDOWS_TASK])
            _run(["schtasks", "/Delete", "/F", "/TN", WINDOWS_TASK])
        self._stop_pidfile()
        return out

    # ------------------------------------------------------------ control
    def start(self, wait: bool = False, timeout: float = 20) -> dict[str, Any]:
        """Start the gateway with the effective supervisor."""
        manager = self.effective_manager()
        if manager == "systemd-user":
            res = _run(["systemctl", "--user", "start", UNIT_NAME], timeout=60)
            out = {"ok": res.returncode == 0, "manager": manager, "detail": res.stderr.strip()[:500]}
        elif manager == "launchd":
            domain = f"gui/{self.uid}"
            res = _run(["launchctl", "bootstrap", domain, str(self.plist_path)])
            if res.returncode != 0:
                res = _run(["launchctl", "kickstart", f"{domain}/{LAUNCHD_LABEL}"])
            out = {"ok": res.returncode == 0, "manager": manager, "detail": res.stderr.strip()[:500]}
        elif manager == "windows-task-scheduler":
            res = _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])
            out = {"ok": res.returncode == 0, "manager": manager, "detail": (res.stderr or "").strip()[:500]}
        else:
            out = self._start_pidfile()
        if wait and out.get("ok"):
            out["healthy"] = self.wait_healthy(timeout)
        return out

    def stop(self) -> dict[str, Any]:
        """Stop the gateway (also any stray pid-file server from v3)."""
        manager = self.effective_manager()
        detail = ""
        ok = True
        if manager == "systemd-user":
            res = _run(["systemctl", "--user", "stop", UNIT_NAME], timeout=60)
            ok, detail = res.returncode == 0, res.stderr.strip()[:500]
        elif manager == "launchd":
            _run(["launchctl", "bootout", f"gui/{self.uid}/{LAUNCHD_LABEL}"])
        elif manager == "windows-task-scheduler":
            _run(["schtasks", "/End", "/TN", WINDOWS_TASK])
        stopped = self._stop_pidfile()
        return {"ok": ok, "manager": manager, "detail": detail, "pidfile_stopped": stopped}

    def restart(self, wait: bool = False, timeout: float = 20) -> dict[str, Any]:
        manager = self.effective_manager()
        if manager == "systemd-user":
            self._stop_pidfile()
            res = _run(["systemctl", "--user", "restart", UNIT_NAME], timeout=60)
            out = {"ok": res.returncode == 0, "manager": manager, "detail": res.stderr.strip()[:500]}
            if wait and out["ok"]:
                out["healthy"] = self.wait_healthy(timeout)
            return out
        if manager == "launchd":
            self._stop_pidfile()
            res = _run(["launchctl", "kickstart", "-k", f"gui/{self.uid}/{LAUNCHD_LABEL}"])
            if res.returncode != 0:
                return self.start(wait=wait, timeout=timeout)
            out = {"ok": True, "manager": manager, "detail": ""}
            if wait:
                out["healthy"] = self.wait_healthy(timeout)
            return out
        self.stop()
        return self.start(wait=wait, timeout=timeout)

    def status(self) -> dict[str, Any]:
        """``{manager, installed, enabled, active, pid, legacy_unit}``."""
        manager = self.detect()
        installed = self.installed()
        enabled = False
        active = False
        pid: int | None = None
        if manager == "systemd-user" and installed:
            enabled = _run(["systemctl", "--user", "is-enabled", UNIT_NAME]).stdout.strip() == "enabled"
            active = _run(["systemctl", "--user", "is-active", UNIT_NAME]).stdout.strip() == "active"
            raw = _run(["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT_NAME]).stdout.strip()
            pid = int(raw) if raw.isdigit() and int(raw) > 0 else None
        elif manager == "launchd" and installed:
            enabled = True
            res = _run(["launchctl", "print", f"gui/{self.uid}/{LAUNCHD_LABEL}"])
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith("pid = "):
                    try:
                        pid = int(line.split("=", 1)[1])
                    except ValueError:
                        pid = None
            active = bool(pid)
        elif manager == "windows-task-scheduler" and installed:
            enabled = True
        if not active:
            fallback = self.pidfile_pid()
            if fallback:
                pid, active = fallback, True
        return {"manager": manager, "effective_manager": self.effective_manager(), "installed": installed,
                "enabled": enabled, "active": active, "pid": pid, "legacy_unit": self.legacy_unit()}

    def wait_healthy(self, timeout: float = 20, port: int | None = None) -> bool:
        """Poll ``http://127.0.0.1:<port>/healthz`` until it answers 200 or ``timeout``."""
        import urllib.request

        url = f"http://127.0.0.1:{port or self.port}/healthz"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310  # nosec B310 - fixed localhost URL
                    if resp.status == 200:
                        return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        return False

    # ------------------------------------------------------------ pid-file supervisor
    def _owned(self, pid: int | None) -> bool:
        """True when ``pid`` is a live uvicorn/cli serve process for this cfg dir."""
        if not pid_alive(pid):
            return False
        cmd = process_cmdline(int(pid))  # type: ignore[arg-type]
        if not cmd:
            # Unknown command line (restricted /proc, Windows without wmic): trust the pid file.
            return True
        return ("uvicorn" in cmd and "server:app" in cmd and str(self.cfg_dir) in cmd) or \
               ("cli.py" in cmd and " serve" in cmd)

    def pidfile_pid(self) -> int | None:
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if self._owned(pid) else None

    def _rotate_log(self) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_ROTATE_BYTES:
                os.replace(self.log_path, self.log_path.with_name("server.log.1"))
        except OSError:
            pass

    def _start_pidfile(self) -> dict[str, Any]:
        existing = self.pidfile_pid()
        if existing:
            return {"ok": True, "manager": "none", "pid": existing, "already_running": True}
        python = self.python()
        if not python.exists():
            return {"ok": False, "manager": "none", "detail": f"python not found: {python}"}
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_log()
        env = dict(os.environ)
        env["LOOPBACK_CONFIG_DIR"] = str(self.cfg_dir)
        env["PYTHONUNBUFFERED"] = "1"
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            try:
                os.chmod(self.log_path, 0o600)
            except OSError:
                pass
            kwargs: dict[str, Any] = {}
            if WINDOWS:
                kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
                argv = [str(python), str(self.cfg_dir / "cli.py"), "serve"]
            else:
                kwargs["start_new_session"] = True
                argv = [str(python), *self.uvicorn_args()]
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, cwd=str(self.home),
                                    env=env, close_fds=True, **kwargs)
        except OSError as exc:
            return {"ok": False, "manager": "none", "detail": f"failed to start: {exc}"}
        finally:
            os.close(fd)
        atomic_write(self.pid_path, f"{proc.pid}\n")
        time.sleep(0.3)
        if proc.poll() is not None:
            return {"ok": False, "manager": "none", "detail": f"server exited with code {proc.returncode}; see {self.log_path}"}
        return {"ok": True, "manager": "none", "pid": proc.pid}

    def _stop_pidfile(self, timeout: float = 20) -> bool:
        """Terminate the pid-file server if we own it. Returns True when one was stopped."""
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return False
        stopped = False
        if self._owned(pid):
            stopped = True
            if WINDOWS:
                _run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and pid_alive(pid):
                    time.sleep(0.1)
                if pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
        try:
            self.pid_path.unlink()
        except OSError:
            pass
        return stopped


# ---------------------------------------------------------------- ingress companion
def stop_legacy_tunnel(cfg_dir: Path | str, timeout: float = 8) -> bool:
    """Stop a v3 cloudflared tracked by ``<cfg>/tunnel.pid`` (v4 uses cloudflared.pid).

    Only a process whose command line mentions ``cloudflared`` is signalled.
    Returns True when a process was stopped.
    """
    path = Path(cfg_dir) / "tunnel.pid"
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return False
    stopped = False
    if pid_alive(pid) and "cloudflared" in process_cmdline(pid):
        stopped = True
        if WINDOWS:
            _run(["taskkill", "/PID", str(pid), "/T", "/F"])
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and pid_alive(pid):
                time.sleep(0.1)
            if pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    try:
        path.unlink()
    except OSError:
        pass
    return stopped


def start_ingress(ctx: Any) -> dict[str, Any]:
    """Start the managed cloudflared through ``ctx.ingress`` when Cloudflare is enabled."""
    ingress = getattr(ctx, "ingress", None)
    if ingress is None:
        return {"ok": True, "action": "none", "detail": "ingress module not installed"}
    try:
        return ingress.ensure_started()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


def stop_ingress(ctx: Any) -> dict[str, Any]:
    """Stop the managed cloudflared (Tailscale Serve/Funnel config is left untouched)."""
    ingress = getattr(ctx, "ingress", None)
    if ingress is None or not hasattr(ingress, "stop_cloudflare"):
        return {"ok": True, "action": "none"}
    try:
        return ingress.stop_cloudflare()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
