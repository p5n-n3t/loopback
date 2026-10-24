"""OS-level filesystem enforcement for shell execution.

The policy layer decides *what* is permitted. This module makes the operating
system enforce it for subprocesses (``execute``, background jobs, terminals).

Backends, in preference order:

* ``landlock`` – Linux ≥ 5.13, pure ctypes, no helper binary. Applied in the
  child before ``exec``. Cannot express "deny this subtree inside an allowed
  tree", so when a denied root sits inside an allowed root the allowed root's
  *children* are granted individually instead (see ``LandlockBackend``).
* ``bwrap`` – bubblewrap user namespaces; masks denied subtrees with tmpfs.
* ``sandbox-exec`` – macOS Seatbelt profiles (deprecated by Apple, still
  functional); supports explicit deny rules.
* ``none`` – no enforcement. The status honestly says so; the policy layer
  refuses "restricted" shell profiles unless the administrator acknowledges it.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SYSTEM_RO_DIRS = ["/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt", "/snap", "/nix", "/run", "/var/lib", "/var/cache", "/sys", "/proc"]  # nosec B108 - sandbox mount plan, reviewed
SYSTEM_RW_DIRS = ["/dev"]
WINDOWS = sys.platform.startswith("win")


@dataclass
class RootRule:
    path: str
    mode: str  # rw | ro

    def resolved(self) -> Path:
        return Path(self.path).expanduser().resolve(strict=False)


@dataclass
class SandboxPlan:
    allowed: list[RootRule]
    denied: list[Path]
    allow_tmp: bool = True
    cwd: str | None = None
    extra_ro: list[str] = field(default_factory=list)


@dataclass
class SandboxStatus:
    backend: str
    available: bool
    active: bool
    reason: str | None
    abi: int | None = None
    enforces_denied_subpaths: bool = False
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "available": self.available,
            "active": self.active,
            "reason": self.reason,
            "abi": self.abi,
            "enforces_denied_subpaths": self.enforces_denied_subpaths,
            "caveats": list(self.caveats),
        }


# --------------------------------------------------------------------------- landlock
LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
_SYSCALLS = {"create_ruleset": 444, "add_rule": 445, "restrict_self": 446}

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5

_ABI1 = (FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_READ_DIR | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_MAKE_CHAR
         | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK | FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM)
_READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
_FILE_ACCESS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc() -> ctypes.CDLL | None:
    try:
        return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except OSError:
        return None


def landlock_abi() -> int:
    """Return the kernel's Landlock ABI version, or 0 when unavailable."""
    if not sys.platform.startswith("linux"):
        return 0
    libc = _libc()
    if libc is None:
        return 0
    try:
        version = libc.syscall(_SYSCALLS["create_ruleset"], None, ctypes.c_size_t(0), ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION))
    except (AttributeError, OSError):
        return 0
    return int(version) if version > 0 else 0


def handled_fs_for_abi(abi: int) -> int:
    handled = _ABI1
    if abi >= 2:
        handled |= FS_REFER
    if abi >= 3:
        handled |= FS_TRUNCATE
    if abi >= 5:
        handled |= FS_IOCTL_DEV
    return handled


def rw_access_for_abi(abi: int) -> int:
    return handled_fs_for_abi(abi)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def landlock_rules(plan: SandboxPlan, abi: int) -> list[tuple[str, int]]:
    """Translate a plan into ``(path, access_bits)`` rules.

    Denied roots cannot be subtracted from an allowed root, so allowed roots
    that contain denied roots are expanded into their children, recursively,
    skipping the denied entries. The containing directory keeps only directory
    level rights (list / create / remove) so files directly inside it cannot
    be read or written through an ancestor rule.
    """
    rw_bits = rw_access_for_abi(abi)
    denied = [d for d in plan.denied]
    rules: list[tuple[str, int]] = []

    def dir_level_bits(bits: int) -> int:
        keep = FS_READ_DIR | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SYM | FS_MAKE_SOCK | FS_MAKE_FIFO | FS_REMOVE_DIR | FS_REMOVE_FILE
        if abi >= 2:
            keep |= FS_REFER
        return bits & keep

    def add(path: Path, bits: int, depth: int = 0) -> None:
        if not path.exists():
            return
        contained = [d for d in denied if d != path and _is_within(d, path)]
        if any(d == path for d in denied):
            return
        if not contained or depth > 6:
            rules.append((str(path), bits if path.is_dir() else bits & _FILE_ACCESS))
            return
        rules.append((str(path), dir_level_bits(bits)))
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_symlink():
                # Symlinks resolve at open time; the target's own rules apply.
                continue
            add(child, bits, depth + 1)

    for rule in plan.allowed:
        add(rule.resolved(), rw_bits if rule.mode == "rw" else _READ)
    for sysdir in SYSTEM_RO_DIRS + plan.extra_ro:
        p = Path(sysdir)
        if p.exists() and not any(_is_within(p, d) or _is_within(d, p) for d in denied):
            rules.append((sysdir, _READ))
    for sysdir in SYSTEM_RW_DIRS:
        if Path(sysdir).exists():
            rules.append((sysdir, FS_READ_FILE | FS_WRITE_FILE | FS_READ_DIR | (FS_IOCTL_DEV if abi >= 5 else 0)))
    if plan.allow_tmp and Path("/tmp").exists():  # nosec B108
        rules.append(("/tmp", rw_bits))  # nosec B108
    if plan.allow_tmp and Path("/var/tmp").exists():  # nosec B108
        rules.append(("/var/tmp", rw_bits))  # nosec B108
    return rules


def apply_landlock(rules: list[tuple[str, int]], abi: int) -> None:
    """Restrict the *current* process. Call only inside a forked child."""
    libc = _libc()
    if libc is None:
        raise OSError("libc unavailable")
    attr = _RulesetAttr(handled_fs_for_abi(abi), 0, 0)
    size = 8 if abi < 4 else (16 if abi < 6 else 24)
    fd = libc.syscall(_SYSCALLS["create_ruleset"], ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    try:
        for path, bits in rules:
            try:
                pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                pb = _PathBeneathAttr(bits, pfd)
                rc = libc.syscall(_SYSCALLS["add_rule"], fd, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(pb), ctypes.c_uint32(0))
                if rc < 0:
                    err = ctypes.get_errno()
                    if err not in (2, 13):  # ENOENT/EACCES on a vanished path is fine
                        raise OSError(err, f"landlock_add_rule failed for {path}")
            finally:
                os.close(pfd)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")
        if libc.syscall(_SYSCALLS["restrict_self"], fd, ctypes.c_uint32(0)) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(fd)


class LandlockBackend:
    name = "landlock"

    def __init__(self) -> None:
        self.abi = landlock_abi()

    def status(self, plan: SandboxPlan | None = None) -> SandboxStatus:
        if self.abi <= 0:
            return SandboxStatus(self.name, False, False, "Landlock is not available in this kernel (needs Linux 5.13+ with the LSM enabled)")
        caveats = ["Landlock cannot subtract a denied subtree from an allowed tree; such trees are granted child-by-child, so a file created directly in that top-level directory becomes accessible at the next command."]
        if self.abi < 2:
            caveats.append("ABI 1: renames and hard links across directory rules are always refused.")
        return SandboxStatus(self.name, True, True, None, abi=self.abi, enforces_denied_subpaths=True, caveats=caveats)

    def wrap(self, argv: list[str], plan: SandboxPlan, env: dict[str, str]) -> tuple[list[str], Callable[[], None] | None, dict[str, str]]:
        rules = landlock_rules(plan, self.abi)
        abi = self.abi

        def preexec() -> None:
            apply_landlock(rules, abi)

        return argv, preexec, env


# --------------------------------------------------------------------------- bwrap
class BwrapBackend:
    name = "bwrap"

    def __init__(self) -> None:
        self.binary = shutil.which("bwrap")

    def status(self, plan: SandboxPlan | None = None) -> SandboxStatus:
        if not self.binary:
            return SandboxStatus(self.name, False, False, "bubblewrap (bwrap) is not installed")
        return SandboxStatus(self.name, True, True, None, enforces_denied_subpaths=True,
                             caveats=["Runs the shell in a new mount namespace; tools that inspect host mounts see the sandbox view."])

    def args(self, plan: SandboxPlan) -> list[str]:
        args = [self.binary or "bwrap", "--die-with-parent", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev"]
        for sysdir in SYSTEM_RO_DIRS + plan.extra_ro:
            if sysdir in {"/proc", "/sys"}:
                continue
            if Path(sysdir).exists():
                args += ["--ro-bind", sysdir, sysdir]
        for rule in plan.allowed:
            p = str(rule.resolved())
            if Path(p).exists():
                args += ["--bind" if rule.mode == "rw" else "--ro-bind", p, p]
        for d in plan.denied:
            if d.exists():
                args += ["--tmpfs", str(d)] if d.is_dir() else ["--ro-bind", "/dev/null", str(d)]
        if plan.allow_tmp:
            args += ["--tmpfs", "/tmp"]  # nosec B108
        if plan.cwd:
            args += ["--chdir", plan.cwd]
        args += ["--unshare-user-try", "--new-session", "--"]
        return args

    def wrap(self, argv: list[str], plan: SandboxPlan, env: dict[str, str]) -> tuple[list[str], Callable[[], None] | None, dict[str, str]]:
        return self.args(plan) + argv, None, env


# --------------------------------------------------------------------------- sandbox-exec (macOS)
class SeatbeltBackend:
    name = "sandbox-exec"

    def __init__(self) -> None:
        self.binary = shutil.which("sandbox-exec") if sys.platform == "darwin" else None

    def status(self, plan: SandboxPlan | None = None) -> SandboxStatus:
        if sys.platform != "darwin":
            return SandboxStatus(self.name, False, False, "sandbox-exec exists only on macOS")
        if not self.binary:
            return SandboxStatus(self.name, False, False, "sandbox-exec binary not found")
        return SandboxStatus(self.name, True, True, None, enforces_denied_subpaths=True,
                             caveats=["Apple marks sandbox-exec deprecated; it still ships and enforces Seatbelt profiles."])

    @staticmethod
    def _q(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def profile(self, plan: SandboxPlan) -> str:
        lines = ["(version 1)", "(deny default)", "(allow process*)", "(allow signal)", "(allow sysctl-read)",
                 "(allow mach-lookup)", "(allow network*)", "(allow ipc-posix*)", "(allow system-socket)",
                 "(allow file-read-metadata)",
                 "(allow file-read* (subpath \"/usr\") (subpath \"/bin\") (subpath \"/sbin\") (subpath \"/System\") (subpath \"/Library\") (subpath \"/private/etc\") (subpath \"/etc\") (subpath \"/opt\") (subpath \"/dev\") (subpath \"/var/db\") (subpath \"/Applications\"))",
                 "(allow file-write* (subpath \"/dev\"))"]
        if plan.allow_tmp:
            lines.append("(allow file-read* file-write* (subpath \"/tmp\") (subpath \"/private/tmp\") (subpath \"/private/var/folders\"))")
        for rule in plan.allowed:
            p = str(rule.resolved())
            verb = "file-read* file-write*" if rule.mode == "rw" else "file-read*"
            lines.append(f"(allow {verb} (subpath {self._q(p)}))")
        for d in plan.denied:
            lines.append(f"(deny file-read* file-write* (subpath {self._q(str(d))}))")
        return "\n".join(lines) + "\n"

    def wrap(self, argv: list[str], plan: SandboxPlan, env: dict[str, str]) -> tuple[list[str], Callable[[], None] | None, dict[str, str]]:
        return [self.binary or "sandbox-exec", "-p", self.profile(plan)] + argv, None, env


class NoneBackend:
    name = "none"

    def status(self, plan: SandboxPlan | None = None) -> SandboxStatus:
        if WINDOWS:
            reason = "No supported filesystem sandbox for arbitrary shells on Windows; path rules are enforced for file tools only."
        else:
            reason = "No OS sandbox backend is available; path rules are enforced for file tools only."
        return SandboxStatus(self.name, True, False, reason, enforces_denied_subpaths=False,
                             caveats=["Shell commands can reach any path the service account can reach."])

    def wrap(self, argv: list[str], plan: SandboxPlan, env: dict[str, str]) -> tuple[list[str], Callable[[], None] | None, dict[str, str]]:
        return argv, None, env


BACKENDS: dict[str, Any] = {
    "landlock": LandlockBackend,
    "bwrap": BwrapBackend,
    "sandbox-exec": SeatbeltBackend,
    "none": NoneBackend,
}


class SandboxManager:
    """Chooses and drives a backend according to ``config.sandbox``."""

    def __init__(self, backend: str = "auto", *, require_enforcement: bool = True, allow_tmp: bool = True,
                 acknowledge_unenforced: bool = False):
        self.requested = backend
        self.require_enforcement = require_enforcement
        self.allow_tmp = allow_tmp
        self.acknowledge_unenforced = acknowledge_unenforced
        self.backend = self._select(backend)

    def _select(self, requested: str):
        if requested != "auto":
            return BACKENDS.get(requested, NoneBackend)()
        if sys.platform.startswith("linux"):
            ll = LandlockBackend()
            if ll.status().available:
                return ll
            bw = BwrapBackend()
            if bw.status().available:
                return bw
        elif sys.platform == "darwin":
            sb = SeatbeltBackend()
            if sb.status().available:
                return sb
        return NoneBackend()

    def status(self) -> dict[str, Any]:
        st = self.backend.status()
        out = st.as_dict()
        out["requested"] = self.requested
        out["require_enforcement"] = self.require_enforcement
        out["acknowledge_unenforced"] = self.acknowledge_unenforced
        out["restricted_shell_permitted"] = st.active or self.acknowledge_unenforced or not self.require_enforcement
        out["platform"] = platform.system()
        return out

    def enforced(self) -> bool:
        return bool(self.backend.status().active)

    def plan_from_policy(self, allowed_roots: list[Any], denied_roots: list[str], cwd: str | None) -> SandboxPlan:
        allowed: list[RootRule] = []
        for item in allowed_roots:
            if isinstance(item, str):
                allowed.append(RootRule(item, "rw"))
            elif isinstance(item, dict) and item.get("path"):
                allowed.append(RootRule(str(item["path"]), "ro" if str(item.get("mode", "rw")).lower() in {"ro", "read", "read-only"} else "rw"))
        denied = [Path(d).expanduser().resolve(strict=False) for d in denied_roots]
        return SandboxPlan(allowed=allowed, denied=denied, allow_tmp=self.allow_tmp, cwd=cwd)

    def wrap(self, argv: list[str], plan: SandboxPlan, env: dict[str, str] | None = None) -> tuple[list[str], Callable[[], None] | None, dict[str, str]]:
        env = dict(env or os.environ)
        # Child processes should not inherit the gateway's own credential locations.
        for key in ("LOOPBACK_TOKEN", "LOOPBACK_API_TOKEN", "LOOPBACK_CONFIG_DIR", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        env["LOOPBACK_SANDBOX"] = self.backend.name
        return self.backend.wrap(argv, plan, env)

    def self_test(self, plan: SandboxPlan, shell: str) -> dict[str, Any]:
        """Prove the backend blocks a denied path and permits an allowed one."""
        result: dict[str, Any] = {"backend": self.backend.name, "enforced": self.enforced(), "checks": []}
        if WINDOWS or not self.enforced():
            result["ok"] = False
            result["reason"] = self.backend.status().reason
            return result
        allowed_dir = next((r.resolved() for r in plan.allowed if r.resolved().is_dir()), None)
        with tempfile.TemporaryDirectory(prefix="loopback-sbx-") as td:
            secret = Path(td) / "secret.txt"
            secret.write_text("deny-me\n", encoding="utf-8")
            test_plan = SandboxPlan(allowed=list(plan.allowed), denied=list(plan.denied) + [Path(td).resolve()],
                                    allow_tmp=False, cwd=plan.cwd)
            checks = [("denied_read", f"cat {shlex.quote(str(secret))}", False)]
            if allowed_dir is not None:
                checks.append(("allowed_list", f"ls {shlex.quote(str(allowed_dir))} >/dev/null", True))
            for name, cmd, expect_ok in checks:
                argv, preexec, env = self.wrap([shell, "-c", cmd], test_plan)
                try:
                    proc = subprocess.run(argv, preexec_fn=preexec, env=env, capture_output=True, text=True, timeout=20,
                                          cwd=plan.cwd or None)
                    ok = (proc.returncode == 0) == expect_ok
                    result["checks"].append({"name": name, "passed": ok, "returncode": proc.returncode,
                                             "stderr": proc.stderr.strip()[:300]})
                except (OSError, subprocess.SubprocessError) as exc:
                    result["checks"].append({"name": name, "passed": False, "error": str(exc)[:300]})
        result["ok"] = all(c["passed"] for c in result["checks"]) and bool(result["checks"])
        return result
