"""Runtime identity detection.

Answers "which operating-system account runs Loopback, and where do its files
live?" using OS account databases rather than string manipulation of paths.

Nothing in this module may embed a developer's username or machine path.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:  # POSIX only
    import pwd
    import grp
except ImportError:  # pragma: no cover - Windows
    pwd = None  # type: ignore[assignment]
    grp = None  # type: ignore[assignment]


def platform_name() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform in {"win32", "cygwin"}:
        return "windows"
    return sys.platform


@dataclass(frozen=True)
class RuntimeIdentity:
    username: str
    uid: int | None
    gid: int | None
    group: str | None
    home: str
    shell: str
    config_dir: str
    data_dir: str
    runtime_dir: str | None
    platform: str
    service_manager: str
    is_root: bool
    invoked_via_sudo: bool
    sudo_user: str | None
    hostname: str
    os_release: str
    arch: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def home_path(self) -> Path:
        return Path(self.home)

    @property
    def config_path(self) -> Path:
        return Path(self.config_dir)


def _posix_account(prefer_sudo_user: bool) -> tuple[Any, bool, str | None]:
    """Return (pw_entry, invoked_via_sudo, sudo_user)."""
    euid = os.geteuid()
    sudo_user = os.environ.get("SUDO_USER") or None
    invoked_via_sudo = bool(sudo_user) and euid == 0 and sudo_user != "root"
    if prefer_sudo_user and invoked_via_sudo:
        try:
            return pwd.getpwnam(sudo_user), True, sudo_user  # type: ignore[union-attr]
        except KeyError:
            pass
    try:
        return pwd.getpwuid(euid), invoked_via_sudo, sudo_user  # type: ignore[union-attr]
    except KeyError:
        # Containers sometimes run with a UID that has no passwd entry.
        class _Fallback:
            pw_name = os.environ.get("USER") or os.environ.get("LOGNAME") or f"uid{euid}"
            pw_uid = euid
            pw_gid = os.getegid()
            pw_dir = os.environ.get("HOME") or "/"
            pw_shell = os.environ.get("SHELL") or "/bin/sh"

        return _Fallback(), invoked_via_sudo, sudo_user


def _valid_shell(candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = shutil.which(candidate) if os.sep not in candidate else candidate
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return None


def default_shell(preferred: str | None = None, pw_shell: str | None = None) -> str:
    """Pick an executable shell: explicit config > account shell > sane fallback."""
    plat = platform_name()
    if plat == "windows":
        for name in (preferred, "pwsh.exe", "powershell.exe", "cmd.exe"):
            found = _valid_shell(name)
            if found:
                return found
        return os.environ.get("COMSPEC", "cmd.exe")
    for name in (preferred, pw_shell, os.environ.get("SHELL"), "/bin/bash", "/usr/bin/bash", "/bin/zsh", "/bin/sh"):
        found = _valid_shell(name)
        if found:
            return found
    return "/bin/sh"


def detect_service_manager() -> str:
    plat = platform_name()
    if plat == "linux":
        if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
            return "systemd-user"
        return "none"
    if plat == "darwin":
        return "launchd" if shutil.which("launchctl") else "none"
    if plat == "windows":
        return "windows-task-scheduler" if shutil.which("schtasks") else "none"
    return "none"


def _config_dir(home: Path, plat: str) -> Path:
    override = os.environ.get("LOOPBACK_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if plat == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / "Loopback"
    # Linux and macOS both use the XDG layout; existing macOS installs already
    # live in ~/.config/loopback, so we deliberately do not move them.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "loopback"
    return home / ".config" / "loopback"


def _runtime_dir(uid: int | None, plat: str) -> str | None:
    if plat != "linux":
        return None
    candidate = os.environ.get("XDG_RUNTIME_DIR")
    if candidate and os.path.isdir(candidate):
        try:
            if uid is None or os.stat(candidate).st_uid == uid:
                return candidate
        except OSError:
            return None
    if uid is not None and os.path.isdir(f"/run/user/{uid}"):
        return f"/run/user/{uid}"
    return None


def os_release_string() -> str:
    plat = platform_name()
    if plat == "linux":
        try:
            data: dict[str, str] = {}
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k] = v.strip().strip('"')
            return data.get("PRETTY_NAME") or data.get("NAME") or "Linux"
        except OSError:
            return "Linux"
    if plat == "darwin":
        return f"macOS {platform.mac_ver()[0] or ''}".strip()
    if plat == "windows":
        return f"Windows {platform.release()}"
    return platform.platform()


def detect(prefer_sudo_user: bool = True) -> RuntimeIdentity:
    """Detect the account that should own and run Loopback.

    When launched through ``sudo`` the *interactive* user is returned (unless
    ``prefer_sudo_user`` is False), so an installer never configures Loopback
    to run permanently as root by accident.
    """
    plat = platform_name()
    hostname = socket.gethostname()
    arch = platform.machine() or "unknown"

    if plat == "windows" or pwd is None:
        import getpass

        username = getpass.getuser()
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        shell = default_shell()
        cfg = _config_dir(home, plat)
        return RuntimeIdentity(  # nosec B604 - dataclass field named shell, not subprocess
            username=username, uid=None, gid=None, group=None, home=str(home), shell=shell,
            config_dir=str(cfg), data_dir=str(cfg), runtime_dir=None, platform=plat,
            service_manager=detect_service_manager(), is_root=False, invoked_via_sudo=False,
            sudo_user=None, hostname=hostname, os_release=os_release_string(), arch=arch,
        )

    pw, via_sudo, sudo_user = _posix_account(prefer_sudo_user)
    home = Path(pw.pw_dir or "/")
    group = None
    if grp is not None:
        try:
            group = grp.getgrgid(pw.pw_gid).gr_name
        except KeyError:
            group = None
    cfg = _config_dir(home, plat)
    return RuntimeIdentity(  # nosec B604 - dataclass field named shell, not subprocess
        username=pw.pw_name,
        uid=int(pw.pw_uid),
        gid=int(pw.pw_gid),
        group=group,
        home=str(home),
        shell=default_shell(pw_shell=getattr(pw, "pw_shell", None)),
        config_dir=str(cfg),
        data_dir=str(cfg),
        runtime_dir=_runtime_dir(int(pw.pw_uid), plat),
        platform=plat,
        service_manager=detect_service_manager(),
        is_root=os.geteuid() == 0 and not (prefer_sudo_user and via_sudo),
        invoked_via_sudo=via_sudo,
        sudo_user=sudo_user,
        hostname=hostname,
        os_release=os_release_string(),
        arch=arch,
    )


def describe_host() -> dict[str, Any]:
    """Non-secret host facts used by diagnostics and the topology view."""
    ident = detect()
    virt = detect_virtualization()
    return {
        "hostname": ident.hostname,
        "os": ident.os_release,
        "platform": ident.platform,
        "arch": ident.arch,
        "username": ident.username,
        "home": ident.home,
        "shell": ident.shell,
        "service_manager": ident.service_manager,
        "machine_kind": virt,
    }


def detect_virtualization() -> str:
    """Best-effort classification: physical | virtual-machine | container | unknown.

    Never guesses "VM" without evidence.
    """
    plat = platform_name()
    if plat == "linux":
        if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
            return "container"
        try:
            cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
            if any(tag in cgroup for tag in ("docker", "containerd", "kubepods", "lxc", "podman")):
                return "container"
        except OSError:
            pass
        if shutil.which("systemd-detect-virt"):
            import subprocess

            try:
                out = subprocess.run(["systemd-detect-virt"], text=True, capture_output=True, timeout=3).stdout.strip()
                if out and out != "none":
                    return "container" if out in {"docker", "podman", "lxc", "lxc-libvirt", "systemd-nspawn", "openvz", "wsl"} else "virtual-machine"
                if out == "none":
                    return "physical"
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            vendor = Path("/sys/class/dmi/id/sys_vendor").read_text(encoding="utf-8", errors="ignore").strip().lower()
            product = Path("/sys/class/dmi/id/product_name").read_text(encoding="utf-8", errors="ignore").strip().lower()
            if any(tag in vendor + product for tag in ("qemu", "kvm", "vmware", "virtualbox", "xen", "microsoft corporation", "amazon ec2", "oracle", "google", "openstack")):
                return "virtual-machine"
            if vendor:
                return "physical"
        except OSError:
            pass
        return "unknown"
    if plat == "darwin":
        return "physical"
    if plat == "windows":
        return "unknown"
    return "unknown"
