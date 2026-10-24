#!/usr/bin/env python3
"""Loopback command-line interface (``loopback``).

This file is the single implementation behind every entry point: the bash
wrapper ``bin/loopback``, the Windows wrappers ``bin/loopback.ps1`` /
``bin/loopback.cmd``, ``install.sh`` / ``install.ps1`` and the npm package.

Two execution contexts are supported:

* **Source checkout** - ``python3 runtime/cli.py install`` runs with the
  system interpreter and only the standard library. Heavy modules (auth, jwt,
  starlette, ...) are imported lazily, after the venv exists, by re-running
  this file from the configuration directory with the venv interpreter
  (internal ``install --stage2``).
* **Installed** - ``<cfg>/cli.py`` next to the other flat runtime modules,
  executed by ``<cfg>/venv/bin/python`` (``<cfg>\\venv\\Scripts\\python.exe``).

Exit codes: 0 success, 1 failure, 2 usage / refused (root, sudo, bad input).

Secrets are never printed except by ``loopback token`` (machine recovery
credential) and ``loopback auth token create`` (new API token, shown once).

Test-only flags: ``install --skip-venv --python-override PY`` reuse an
existing interpreter instead of building ``<cfg>/venv``; the interpreter path
is recorded in ``<cfg>/python-override``.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MIN_PY = (3, 11)
WINDOWS = sys.platform.startswith("win")
RUNTIME_DIRS = ("static", "skills")
RUNTIME_FILES = ("VERSION", "requirements.txt")
BACKUP_EXCLUDE_TOP = {"venv", "backups", "diagnostics", "__pycache__", "node_modules"}
MCP_PROTOCOL = "2025-06-18"
GITHUB_REPO = "p5n-n3t/loopback"


# =============================================================== output helpers
def _supports_unicode() -> bool:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


OK_MARK = "✓" if _supports_unicode() else "OK"
FAIL_MARK = "✗" if _supports_unicode() else "X"
WARN_MARK = "!"


def say(msg: str = "", *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    try:
        print(msg, file=stream, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), file=stream, flush=True)


def die(msg: str, code: int = 1) -> NoReturn:  # type: ignore[name-defined]  # noqa: F821
    say(f"{FAIL_MARK} {msg}", err=True)
    raise SystemExit(code)


_SECRET_RES = [
    re.compile(r"lbk_[A-Za-z0-9]+_[A-Za-z0-9_\-]+"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
    re.compile(r"(?i)(bearer\s+)[^\s\"',]+"),
    re.compile(r"(?i)((?:token|secret|password|code)[\"']?\s*[=:]\s*[\"']?)[^\s\"',&]{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
_EXTRA_SECRETS: set[str] = set()


def redact(text: str) -> str:
    """Remove credentials (API tokens, JWTs, 64-hex secrets, bearer values) from text."""
    if not isinstance(text, str):
        return text
    for secret in _EXTRA_SECRETS:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    for rx in _SECRET_RES:
        if rx.groups:
            text = rx.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = rx.sub("[REDACTED]", text)
    return text


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and re.search(r"(?i)(token|secret|password|private_key|credential)$", k) \
                    and isinstance(v, str) and v:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def atomic_write(path: Path, data: str | bytes, mode: int = 0o600) -> None:
    """Atomic write (tmp + os.replace) with explicit file mode."""
    from service import atomic_write as _aw

    _aw(Path(path), data, mode)


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def emit_json(data: Any) -> None:
    say(json.dumps(redact_obj(data), indent=2, sort_keys=True, default=str))


def confirm(question: str, *, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes or os.environ.get("LOOPBACK_INSTALL_YES") == "1":
        return True
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


class Report:
    """Collects install/upgrade steps, prints them, and saves a sanitized JSON copy."""

    def __init__(self, kind: str, path: Path | None = None, *, quiet: bool = False):
        self.path = path
        self.quiet = quiet
        self.data: dict[str, Any] = {"kind": kind, "started_at": time.time(), "steps": [], "ok": None,
                                     "cli": cli_version()}

    @classmethod
    def load(cls, kind: str, path: Path) -> Report:
        rep = cls(kind, path)
        old = read_json(path, None)
        if isinstance(old, dict) and old.get("kind") == kind and old.get("phase") == "stage1":
            rep.data.update(old)
        return rep

    def step(self, name: str, ok: bool | None, detail: str = "", *, level: str = "error", fix: str = "") -> bool:
        mark = OK_MARK if ok else (WARN_MARK if ok is None or level != "error" else FAIL_MARK)
        line = f"{mark} {name}"
        if detail:
            line += f" - {redact(detail)}"
        if not self.quiet:
            say(line)
            if fix and not ok:
                say(f"    fix: {fix}")
        self.data["steps"].append({"name": name, "ok": ok, "level": level, "detail": redact(detail)[:2000],
                                   "fix": fix, "at": time.time()})
        return bool(ok)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def failed(self) -> list[dict[str, Any]]:
        return [s for s in self.data["steps"] if s["ok"] is False and s["level"] == "error"]

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, redact_obj(self.data))
        except OSError as exc:
            say(f"{WARN_MARK} could not save report {self.path}: {exc}", err=True)


# =============================================================== environment
def cli_version() -> str:
    try:
        from version import VERSION

        return VERSION
    except Exception:  # noqa: BLE001
        return "unknown"


def source_root(explicit: str | None = None) -> Path | None:
    """The checkout / extracted release that contains runtime/ and requirements.txt."""
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.append(HERE.parent)
    for cand in candidates:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if (cand / "runtime" / "server.py").is_file() and (cand / "requirements.txt").is_file():
            return cand
    return None


def get_identity():
    import identity

    return identity.detect(prefer_sudo_user=False)


def cfg_dir(ident=None) -> Path:
    ident = ident or get_identity()
    return Path(ident.config_dir)


def read_port(cfg: Path) -> int:
    data = read_json(cfg / "config.json", None)
    if isinstance(data, dict) and isinstance(data.get("port"), int):
        return int(data["port"])
    env = parse_env(cfg / "config.env")
    try:
        return int(env.get("LOOPBACK_PORT") or 2026)
    except ValueError:
        return 2026


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


def venv_python(cfg: Path) -> Path:
    from service import venv_python as _vp

    return _vp(cfg)


def runtime_python(cfg: Path) -> Path:
    """Interpreter that runs the installed runtime (venv or test-only override)."""
    venv = venv_python(cfg)
    if venv.exists():
        return venv
    try:
        alt = (cfg / "python-override").read_text(encoding="utf-8").strip()
        if alt and Path(alt).exists():
            return Path(alt)
    except OSError:
        pass
    return venv


def service_manager(cfg: Path, ident=None, port: int | None = None):
    from service import ServiceManager

    ident = ident or get_identity()
    return ServiceManager(ident, cfg, port or read_port(cfg))


def deps_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("jwt", "starlette", "uvicorn"))


def load_ctx(cfg: Path | None = None):
    """Build the AppContext (no managers). Requires the venv dependencies."""
    if not deps_available():
        die(f"this command needs the Loopback runtime environment; run it via 'loopback' "
            f"(interpreter: {runtime_python(cfg or cfg_dir())})")
    from bootstrap import build_context

    ctx = build_context(cfg, start_managers=False)
    _EXTRA_SECRETS.add(ctx.auth.machine_token)
    return ctx


def installed_version(cfg: Path) -> str | None:
    """Version of the runtime installed in ``cfg`` (None when nothing is installed)."""
    vfile = cfg / "VERSION"
    if vfile.is_file():
        value = vfile.read_text(encoding="utf-8").strip()
        if value:
            return value
    vpy = cfg / "version.py"
    if vpy.is_file():
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', vpy.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    server = cfg / "server.py"
    if server.is_file():
        text = server.read_text(encoding="utf-8", errors="replace")
        for rx in (r'version\s*=\s*"(\d+\.\d+\.\d+[^"]*)"', r'"version"\s*:\s*"(\d+\.\d+\.\d+[^"]*)"'):
            m = re.search(rx, text)
            if m:
                return m.group(1)
        return "unknown/3.x"
    return None


def source_version(src: Path) -> str:
    text = (src / "runtime" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "unknown"


def version_tuple(v: str | None) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def os_info() -> dict[str, Any]:
    import identity

    plat = identity.platform_name()
    info: dict[str, Any] = {"platform": plat, "arch": platform.machine() or "unknown", "distro": None,
                            "id_like": [], "pretty": identity.os_release_string(), "pkg_manager": None}
    if plat == "linux":
        rel = parse_env(Path("/etc/os-release"))
        info["distro"] = rel.get("ID")
        info["id_like"] = (rel.get("ID_LIKE") or "").split()
    for name, binary in (("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"), ("zypper", "zypper"),
                         ("pacman", "pacman"), ("apk", "apk"), ("brew", "brew"), ("winget", "winget")):
        if shutil.which(binary):
            info["pkg_manager"] = name
            break
    return info


VENV_HINTS = {
    "apt": "sudo apt-get install -y python3-venv   (Debian/Ubuntu; for a versioned python use python3.X-venv)",
    "dnf": "sudo dnf install -y python3-libs   (Fedora)",
    "yum": "sudo yum install -y python3-libs",
    "zypper": "sudo zypper install -y python3   (openSUSE)",
    "pacman": "sudo pacman -S --needed python   (Arch)",
    "apk": "sudo apk add python3 py3-virtualenv   (Alpine)",
    "brew": "brew install python@3.12   (macOS)",
    "winget": "winget install Python.Python.3.12   (Windows)",
}


def python_version_of(python: Path | str) -> tuple[int, int] | None:
    res = run([str(python), "-c", "import sys;print('%d.%d'%sys.version_info[:2])"], timeout=30)
    if res.returncode != 0:
        return None
    try:
        major, minor = res.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def run(cmd: list[str], timeout: float = 600, env: dict[str, str] | None = None,
        cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=env, cwd=cwd)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", str(exc))


def tail_text(text: str, lines: int = 15) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


# =============================================================== HTTP helpers
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def http(url: str, *, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None,
         timeout: float = 5, follow: bool = True) -> tuple[int, dict[str, str], bytes]:
    """Small urllib wrapper returning (status, headers, body); status 0 on connection error."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - caller controls URL
            return resp.status, dict(resp.headers), resp.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read(64 * 1024)
        except Exception:  # noqa: BLE001
            data = b""
        return exc.code, dict(exc.headers or {}), data
    except Exception as exc:  # noqa: BLE001
        return 0, {}, str(exc).encode()


def healthz(port: int, timeout: float = 3) -> dict[str, Any] | None:
    status, _, body = http(f"http://127.0.0.1:{port}/healthz", timeout=timeout)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return {"ok": True}


def wait_health(port: int, timeout: float = 30) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = healthz(port, timeout=2)
        if data:
            return data
        time.sleep(0.3)
    return None


def _parse_mcp_body(body: bytes) -> dict[str, Any] | None:
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    for line in text.splitlines():  # text/event-stream
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                continue
    return None


def mcp_post(port: int, token: str, payload: dict[str, Any], timeout: float = 20) -> tuple[int, dict[str, Any] | None]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": MCP_PROTOCOL}
    status, _, body = http(f"http://127.0.0.1:{port}/mcp", method="POST", body=json.dumps(payload).encode(),
                           headers=headers, timeout=timeout)
    return status, _parse_mcp_body(body)


def verify_mcp(auth: Any, port: int, *, exec_command: str | None = None) -> dict[str, Any]:
    """Issue a temporary API token, run MCP initialize (and optionally execute), then revoke it."""
    scopes = ["loopback:read"] + (["loopback:exec"] if exec_command else [])
    token, record = auth.create_api_token("installer-check", scopes, ttl_seconds=300, created_by="cli",
                                          note="temporary token used by loopback install/upgrade/doctor")
    _EXTRA_SECRETS.add(token)
    out: dict[str, Any] = {"ok": False}
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                           "clientInfo": {"name": "loopback-cli", "version": cli_version()}}}
        status, data = mcp_post(port, token, init)
        out["status"] = status
        result = (data or {}).get("result") or {}
        if status == 200 and result.get("serverInfo"):
            out["ok"] = True
            out["server"] = result.get("serverInfo")
            out["protocol"] = result.get("protocolVersion")
        else:
            out["detail"] = f"HTTP {status}: {json.dumps(data)[:300] if data else 'no JSON-RPC body'}"
        if out["ok"] and exec_command:
            call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "execute", "arguments": {"command": exec_command}}}
            status, data = mcp_post(port, token, call, timeout=60)
            res = (data or {}).get("result") or {}
            structured = res.get("structuredContent") or {}
            if not structured:
                for item in res.get("content") or []:
                    if item.get("type") == "text":
                        try:
                            structured = json.loads(item.get("text") or "{}")
                        except ValueError:
                            structured = {"stdout": item.get("text")}
                        break
            if isinstance(structured, dict) and "result" in structured and isinstance(structured["result"], dict):
                structured = structured["result"]
            out["exec"] = {"status": status, "is_error": bool(res.get("isError")),
                           "stdout": str(structured.get("stdout", ""))[:2000],
                           "returncode": structured.get("returncode"), "cwd": structured.get("cwd")}
    finally:
        try:
            auth.revoke_api_token(record["id"])
        except Exception:  # noqa: BLE001
            pass
    return out


def helper_json(cfg: Path, *args: str, timeout: float = 120) -> dict[str, Any]:
    """Run ``<runtime python> <cfg>/cli.py _helper ...`` and parse its JSON output."""
    py = runtime_python(cfg)
    env = dict(os.environ, LOOPBACK_CONFIG_DIR=str(cfg))
    res = run([str(py), str(cfg / "cli.py"), "_helper", *args], timeout=timeout, env=env)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": redact(tail_text(res.stderr or res.stdout, 8)) or f"exit {res.returncode}"}


# =============================================================== runtime deployment
def _py_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.is_file())


def stage_runtime(src: Path, cfg: Path) -> Path:
    """Copy runtime/*.py, runtime/static/**, skills/** and metadata into ``<cfg>/.staging-<ts>``."""
    staging = cfg / f".staging-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    for py in _py_files(src / "runtime"):
        if py.name == "__init__.py":
            continue
        shutil.copy2(py, staging / py.name)
    if (src / "runtime" / "static").is_dir():
        shutil.copytree(src / "runtime" / "static", staging / "static",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (src / "skills").is_dir():
        shutil.copytree(src / "skills", staging / "skills", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(src / "requirements.txt", staging / "requirements.txt")
    (staging / "VERSION").write_text(source_version(src) + "\n", encoding="utf-8")
    return staging


def compile_check(directory: Path) -> list[str]:
    """py_compile every staged module (bytecode goes to a throwaway temp dir)."""
    import py_compile
    import tempfile

    errors = []
    with tempfile.TemporaryDirectory(prefix="loopback-compile-") as tmp:
        for py in _py_files(directory):
            try:
                py_compile.compile(str(py), cfile=os.path.join(tmp, py.name + "c"), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"{py.name}: {exc.msg.strip()[:300]}")
            except OSError as exc:
                errors.append(f"{py.name}: {exc}")
    return errors


def runtime_items(directory: Path) -> list[Path]:
    """Every runtime artefact (as opposed to state) at the top of ``directory``."""
    items = [p for p in _py_files(directory)]
    items += [directory / d for d in RUNTIME_DIRS if (directory / d).exists()]
    items += [directory / f for f in RUNTIME_FILES if (directory / f).exists()]
    return items


def move_runtime(src_dir: Path, dest_dir: Path) -> list[str]:
    """Move runtime items from ``src_dir`` to ``dest_dir`` (same filesystem renames)."""
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    moved = []
    for item in runtime_items(src_dir):
        target = dest_dir / item.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        os.replace(item, target)
        moved.append(item.name)
    pycache = src_dir / "__pycache__"
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)
    return moved


def copy_runtime(src_dir: Path, dest_dir: Path) -> list[str]:
    """Copy runtime items (used by rollback so the backup stays intact)."""
    copied = []
    for item in runtime_items(src_dir):
        target = dest_dir / item.name
        if target.is_dir():
            shutil.rmtree(target)
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        copied.append(item.name)
    shutil.rmtree(dest_dir / "__pycache__", ignore_errors=True)
    return copied


def deploy_staged(staging: Path, cfg: Path) -> None:
    """Swap staged runtime into ``cfg``: existing items go to a scratch dir, then are deleted."""
    scratch = staging.with_name(staging.name + ".old")
    move_runtime(cfg, scratch)  # only runtime files, never state
    try:
        move_runtime(staging, cfg)
    except OSError:
        move_runtime(scratch, cfg)
        raise
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)


def snapshot_runtime(cfg: Path, svc: Any, label: str) -> Path:
    """Move the current runtime to ``backups/runtime-<label>`` plus config/unit/CLI snapshots."""
    dest = cfg / "backups" / f"runtime-{label}"
    move_runtime(cfg, dest)
    snap = dest / "_config"
    snap.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("config.json", "config.env", "policy.json", "public_host"):
        if (cfg / name).is_file():
            shutil.copy2(cfg / name, snap / name)
    try:
        unit = svc.unit_path if svc.detect() == "systemd-user" else svc.plist_path
        if unit.is_file():
            (dest / "_unit").mkdir(exist_ok=True)
            shutil.copy2(unit, dest / "_unit" / unit.name)
    except Exception:  # noqa: BLE001
        pass
    wrapper = cli_wrapper_path()
    if wrapper.is_file():
        (dest / "_bin").mkdir(exist_ok=True)
        shutil.copy2(wrapper, dest / "_bin" / wrapper.name)
    fix_permissions(dest)
    return dest


def cli_wrapper_path() -> Path:
    """Where the thin CLI wrapper lives (LOOPBACK_BIN_DIR overrides, e.g. for tests)."""
    override = os.environ.get("LOOPBACK_BIN_DIR")
    if override:
        return Path(override).expanduser() / ("loopback.cmd" if WINDOWS else "loopback")
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Loopback" / "bin" / "loopback.cmd"
    return Path(get_identity().home) / ".local" / "bin" / "loopback"


def install_cli_wrapper(src: Path, cfg: Path) -> str:
    """Install the thin CLI wrapper; keeps a copy of any previous (e.g. v3) wrapper."""
    target = cli_wrapper_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        bdir = cfg / "backups" / "bin"
        bdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(bdir / f"{target.name}.{time.strftime('%Y%m%d-%H%M%S')}", target.read_bytes())
    if WINDOWS:
        for name in ("loopback.cmd", "loopback.ps1"):
            if (src / "bin" / name).is_file():
                shutil.copy2(src / "bin" / name, target.parent / name)
        path_env = os.environ.get("PATH", "")
        if str(target.parent).lower() not in path_env.lower():
            return f"{target} (add {target.parent} to your user PATH: setx PATH \"%PATH%;{target.parent}\")"
        return str(target)
    data = (src / "bin" / "loopback").read_bytes()
    atomic_write(target, data, 0o755)
    if str(target.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        return f"{target} (not on PATH yet: export PATH=\"{target.parent}:$PATH\" in your shell profile)"
    return str(target)


def fix_permissions(root: Path) -> int:
    """Directories 0700, files 0600 (scripts keep no exec bit; venv is skipped)."""
    if WINDOWS:
        return 0
    changed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) == root and d == "venv")]
        try:
            if os.stat(dirpath).st_mode & 0o777 != 0o700:
                os.chmod(dirpath, 0o700)
                changed += 1
        except OSError:
            pass
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
                if not os.path.islink(p) and st.st_mode & 0o777 != 0o600:
                    os.chmod(p, 0o600)
                    changed += 1
            except OSError:
                pass
    return changed


# =============================================================== backups
def _backup_filter(cfg: Path):
    def keep(rel: str, is_dir: bool) -> bool:
        parts = Path(rel).parts
        if not parts:
            return True
        top = parts[0]
        if top in BACKUP_EXCLUDE_TOP or top.startswith(".staging-") or top.startswith("venv.old"):
            return False
        if "__pycache__" in parts:
            return False
        name = parts[-1]
        if name.endswith((".tmp", ".pid")) or name.startswith("."):
            return name in {".gitkeep"}
        if top == "jobs" and name.endswith(".log"):
            return False
        return True
    return keep


def make_backup(cfg: Path, name: str | None = None, output: Path | None = None) -> Path:
    """Create a verified gzip tarball of every state file (mode 0600). Raises on failure."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(output).expanduser() if output else cfg / "backups" / f"{name or 'backup-' + stamp}.tar.gz"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    keep = _backup_filter(cfg)
    count = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(cfg):
                rel_dir = os.path.relpath(dirpath, cfg)
                rel_dir = "" if rel_dir == "." else rel_dir
                dirnames[:] = sorted(d for d in dirnames if keep(os.path.join(rel_dir, d), True)
                                     and not os.path.islink(os.path.join(dirpath, d)))
                for fname in sorted(filenames):
                    rel = os.path.join(rel_dir, fname)
                    full = os.path.join(dirpath, fname)
                    if os.path.islink(full) or not keep(rel, False):
                        continue
                    if Path(full).resolve() == tmp.resolve():
                        continue
                    try:
                        tar.add(full, arcname=Path(rel).as_posix(), recursive=False)
                        count += 1
                    except OSError:
                        continue
            meta = json.dumps({"product": "loopback", "created_at": time.time(), "version": installed_version(cfg),
                               "files": count}).encode()
            info = tarfile.TarInfo("loopback-backup.json")
            info.size = len(meta)
            info.mtime = int(time.time())
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(meta))
            raw.flush()
            os.fsync(raw.fileno())
        # Verify: the archive must be readable end to end.
        with tarfile.open(tmp, "r:gz") as tar:
            members = tar.getnames()
        if "loopback-backup.json" not in members:
            raise OSError("backup verification failed")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if not output:
        prune_backups(cfg)
    return target


def backup_retention(cfg: Path) -> int:
    data = read_json(cfg / "config.json", {}) or {}
    try:
        return max(1, int((data.get("maintenance") or {}).get("backup_retention") or 10))
    except (TypeError, ValueError):
        return 10


def prune_backups(cfg: Path) -> None:
    keep = backup_retention(cfg)
    bdir = cfg / "backups"
    for prefix in ("pre-upgrade-", "pre-install-", "pre-restore-", "pre-rollback-", "backup-"):
        files = sorted(bdir.glob(f"{prefix}*.tar.gz"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    for prefix in ("runtime-", "replaced-"):
        dirs = sorted((p for p in bdir.glob(f"{prefix}*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for old in dirs[:-keep]:
            shutil.rmtree(old, ignore_errors=True)


def safe_extract(archive: Path, dest: Path) -> int:
    """Extract a Loopback backup, refusing absolute paths, '..', links and devices."""
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        names = {m.name for m in members}
        if "loopback-backup.json" not in names and "config.json" not in names and "config.env" not in names:
            raise ValueError("archive does not look like a Loopback backup")
        safe = []
        for m in members:
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or m.name.startswith(("/", "\\")):
                raise ValueError(f"unsafe path in archive: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"unsupported member type in archive: {m.name}")
            if p.parts and p.parts[0] in BACKUP_EXCLUDE_TOP:
                continue
            if m.name == "loopback-backup.json":
                continue
            safe.append(m)
        for m in safe:
            m.mode = 0o700 if m.isdir() else 0o600
        kwargs: dict[str, Any] = {}
        if hasattr(tarfile, "data_filter"):
            kwargs["filter"] = "data"
        tar.extractall(dest, members=safe, **kwargs)  # noqa: S202  # nosec B202 - members validated by safe_extract
    fix_permissions(dest)
    return len(safe)


# =============================================================== doctor
def builtin_doctor(cfg: Path, deep: bool = False, ctx: Any = None) -> list[dict[str, Any]]:
    """Minimal checks used when maintenance.doctor is not installed."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", level: str = "error", fix: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "level": level, "detail": detail, "fix": fix or None})

    add("config directory", cfg.is_dir(), str(cfg), fix="loopback install")
    if not WINDOWS and cfg.is_dir():
        mode = cfg.stat().st_mode & 0o777
        add("config directory permissions", mode == 0o700, oct(mode), level="warning", fix="loopback repair")
    py = runtime_python(cfg)
    add("python runtime", py.exists(), str(py), fix="loopback repair")
    add("runtime files", (cfg / "server.py").is_file() and (cfg / "cli.py").is_file(), "server.py, cli.py",
        fix="loopback repair --source <checkout>")
    ver = installed_version(cfg)
    add("installed version", ver == cli_version(), f"installed {ver}, cli {cli_version()}", level="warning",
        fix="loopback upgrade --source <checkout>")
    cfg_ok = True
    detail = "config.json"
    try:
        from config import ConfigStore, validate  # stdlib-only module
        import identity

        if (cfg / "config.json").exists():
            store = ConfigStore(identity.detect(prefer_sudo_user=False))
            errs = validate(store.data)
            cfg_ok, detail = not errs, "; ".join(errs) or "valid"
        else:
            cfg_ok, detail = False, "config.json missing"
    except Exception as exc:  # noqa: BLE001
        cfg_ok, detail = False, str(exc)[:300]
    add("configuration", cfg_ok, detail, fix="loopback repair, or restore with loopback rollback")
    token = cfg / "token"
    add("machine token", token.is_file() and token.stat().st_size > 0, "present" if token.exists() else "missing",
        fix="loopback repair")
    if not WINDOWS:
        loose = []
        for name in ("token", "oauth-signing.key", "config.json", "policy.json", "nodes.secrets.json", "auth.db",
                     "cloudflared.token", "ssh_targets.json"):
            p = cfg / name
            if p.exists() and p.stat().st_mode & 0o077:
                loose.append(name)
        add("state file permissions", not loose, ", ".join(loose) or "0600", fix="loopback repair")
    port = read_port(cfg)
    health = healthz(port)
    add("local health", bool(health), f"http://127.0.0.1:{port}/healthz" + (f" v{health.get('version')}" if health else ""),
        fix="loopback up; loopback logs")
    try:
        svc = service_manager(cfg)
        st = svc.status()
        add("service", st["active"], f"{st['effective_manager']} installed={st['installed']} pid={st['pid']}",
            level="warning", fix="loopback autostart on")
        if st.get("legacy_unit"):
            add("legacy v3 unit", False, "Type=oneshot unit still installed", level="warning", fix="loopback repair")
    except Exception as exc:  # noqa: BLE001
        add("service", False, str(exc)[:200], level="warning")
    usage = shutil.disk_usage(str(cfg if cfg.exists() else Path.home()))
    pct = int(100 * usage.used / max(usage.total, 1))
    add("disk space", pct < 95, f"{pct}% used", level="warning", fix="free disk space")
    if ctx is not None:
        try:
            sb = ctx.sandbox.status()
            add("OS sandbox", bool(sb.get("active")), f"{sb.get('backend')} {sb.get('reason') or ''}".strip(),
                level="warning", fix="see SECURITY.md (sandbox)")
        except Exception:  # noqa: BLE001
            pass
        cfgd = ctx.config.data.get("ingress", {})
        if cfgd.get("cloudflare", {}).get("enabled"):
            add("cloudflared binary", bool(shutil.which("cloudflared")), "", fix="install cloudflared")
        if cfgd.get("tailscale", {}).get("enabled"):
            add("tailscale binary", bool(shutil.which("tailscale")), "", fix="install tailscale")
        if deep and health:
            res = verify_mcp(ctx.auth, port)
            add("MCP initialize", res.get("ok", False), res.get("detail") or str(res.get("server") or ""),
                fix="loopback logs")
            for host in ctx.config.public_hosts():
                status, _, _ = http(f"https://{host}/healthz", timeout=10)
                add(f"public health {host}", status == 200, f"HTTP {status}", level="warning",
                    fix="check ingress in the dashboard")
    return checks


def run_doctor(cfg: Path, deep: bool) -> list[dict[str, Any]]:
    ctx = load_ctx(cfg) if deps_available() else None
    try:
        import maintenance  # type: ignore

        fn = getattr(maintenance, "doctor", None)
        if fn and ctx is not None:
            res = fn(ctx, deep)
            checks = res.get("checks") if isinstance(res, dict) else res
            if isinstance(checks, list):
                return checks
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        say(f"{WARN_MARK} maintenance.doctor failed ({exc}); using built-in checks", err=True)
    return builtin_doctor(cfg, deep, ctx)


def print_checks(checks: list[dict[str, Any]]) -> int:
    errors = 0
    for c in checks:
        ok = c.get("ok")
        level = c.get("level", "error")
        mark = OK_MARK if ok else (FAIL_MARK if level == "error" else WARN_MARK)
        say(f"{mark} {c.get('name')}" + (f" - {redact(str(c.get('detail')))}" if c.get("detail") else ""))
        if not ok and c.get("fix"):
            say(f"    fix: {c['fix']}")
        if not ok and level == "error":
            errors += 1
    return errors


# =============================================================== install
INGRESS_CHOICES = ("local", "tailscale", "cloudflare", "both")


def refuse_root(allow_root: bool) -> None:
    if WINDOWS or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        say(f"{FAIL_MARK} Loopback was started through sudo (SUDO_USER={sudo_user}).", err=True)
        say("  Loopback runs as your normal account and installs into your home directory;", err=True)
        say("  running it as root would give every MCP client root access. Rerun without sudo:", err=True)
        say("    ./install.sh   (or: loopback install)", err=True)
        raise SystemExit(2)
    if not allow_root:
        say(f"{FAIL_MARK} refusing to install as root. Run as the account that should own Loopback,", err=True)
        say("  or pass --allow-root if this machine really has only a root account (containers).", err=True)
        raise SystemExit(2)


def ask_install_questions(args: argparse.Namespace) -> None:
    """Interactive mode asks at most: ingress, hostname (cloudflare), funnel confirmation."""
    interactive = not args.non_interactive and sys.stdin.isatty()
    if not args.ingress:
        if interactive:
            say("Choose ingress:")
            say("  1) Local only        (default; reachable from this machine)")
            say("  2) Tailscale         (tailnet-only Serve, or public Funnel)")
            say("  3) Cloudflare Tunnel (custom domain)")
            say("  4) Both Cloudflare and Tailscale")
            try:
                choice = input("> ").strip() or "1"
            except EOFError:
                choice = "1"
            args.ingress = {"1": "local", "2": "tailscale", "3": "cloudflare", "4": "both"}.get(choice, choice)
            if args.ingress not in INGRESS_CHOICES:
                die(f"invalid choice: {choice}", 2)
        else:
            args.ingress = "local"
    if args.ingress in ("cloudflare", "both") and not args.host and interactive:
        try:
            args.host = input("Public hostname for Cloudflare (e.g. loopback.example.com): ").strip() or None
        except EOFError:
            args.host = None
    if args.ingress in ("tailscale", "both") and args.tailscale_mode == "funnel":
        msg = "Tailscale Funnel exposes Loopback to the PUBLIC internet (OAuth still required). Continue?"
        if not confirm(msg, default=False, assume_yes=args.yes):
            if interactive:
                say("Using tailnet-only Serve instead.")
                args.tailscale_mode = "serve"
            else:
                die("--tailscale-mode funnel needs --yes in non-interactive mode", 2)


def create_or_update_venv(cfg: Path, src: Path, report: Report, os_meta: dict[str, Any]) -> Path | None:
    venv = cfg / "venv"
    py = venv_python(cfg)
    current = sys.version_info[:2]
    if py.exists():
        have = python_version_of(py)
        if have is None or have != current:
            old = cfg / f"venv.old-{time.strftime('%Y%m%d-%H%M%S')}"
            os.replace(venv, old)
            report.step("existing venv replaced", True, f"python {have} -> {current[0]}.{current[1]}")
    if not py.exists():
        res = run([sys.executable, "-m", "venv", str(venv)], timeout=600)
        if res.returncode != 0 or not py.exists():
            hint = VENV_HINTS.get(os_meta.get("pkg_manager") or "", "install the python3 venv/ensurepip package")
            report.step("create virtualenv", False, tail_text(res.stderr, 5), fix=hint)
            return None
    report.step("virtualenv", True, str(venv))
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], timeout=600)
    res = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
               str(src / "requirements.txt")], timeout=1800)
    if res.returncode != 0:
        report.step("install Python dependencies", False, tail_text(res.stderr or res.stdout, 10),
                    fix="check network access to PyPI, then rerun the installer")
        return None
    report.step("Python dependencies", True, "requirements.txt")
    for old in cfg.glob("venv.old-*"):
        shutil.rmtree(old, ignore_errors=True)
    return py


def cmd_install(args: argparse.Namespace) -> int:
    if args.stage2:
        return install_stage2(args)
    say(f"Loopback installer {cli_version()}")
    refuse_root(args.allow_root)
    ident = get_identity()
    cfg = Path(ident.config_dir)
    report = Report("install", None)
    report.step("identity", True, f"{ident.username} home={ident.home} config={cfg}")
    meta = os_info()
    report.set("os", meta)
    report.step("platform", True, f"{meta['pretty']} ({meta['platform']}/{meta['arch']}) pkg={meta['pkg_manager'] or 'none'}")
    if sys.version_info[:2] < MIN_PY:
        report.step("python", False, platform.python_version(), fix=VENV_HINTS.get(meta.get("pkg_manager") or "", "install Python 3.11+"))
        return 1
    if not args.skip_venv:
        missing = [m for m in ("venv", "ensurepip") if importlib.util.find_spec(m) is None]
        if missing:
            report.step("python venv support", False, f"missing modules: {', '.join(missing)}",
                        fix=VENV_HINTS.get(meta.get("pkg_manager") or "", "install python3-venv"))
            return 1
    report.step("python", True, f"{platform.python_version()} ({sys.executable})")
    src = source_root(args.source)
    if not src:
        report.step("source", False, f"no runtime/ found (looked at {args.source or HERE.parent})",
                    fix="pass --source <checkout or extracted release>")
        return 1
    report.step("source", True, f"{src} (v{source_version(src)})")
    ask_install_questions(args)

    cfg.mkdir(parents=True, exist_ok=True)
    if not WINDOWS:
        os.chmod(cfg, 0o700)
    report.path = cfg / "install-report.json"
    report.step("config directory", True, str(cfg))
    previous = installed_version(cfg)
    has_state = any((cfg / n).exists() for n in ("config.json", "config.env", "token", "policy.json"))
    if has_state:
        try:
            label = re.sub(r"[^A-Za-z0-9.]+", "_", previous or "state")
            path = make_backup(cfg, f"pre-install-{label}-{time.strftime('%Y%m%d-%H%M%S')}")
            report.step("backup of existing installation", True, str(path))
        except Exception as exc:  # noqa: BLE001
            report.step("backup of existing installation", False, str(exc), fix="free disk space and rerun")
            report.save()
            return 1
    report.set("previous_version", previous)

    if args.skip_venv:
        py = Path(args.python_override or sys.executable)
        res = run([str(py), "-c", "import uvicorn, starlette, jwt, mcp, httpx"], timeout=60)
        if res.returncode != 0:
            report.step("python override (test-only)", False, tail_text(res.stderr, 3))
            report.save()
            return 1
        atomic_write(cfg / "python-override", str(py) + "\n")
        report.step("python override (test-only)", True, str(py))
    else:
        try:
            (cfg / "python-override").unlink()
        except OSError:
            pass
        py = create_or_update_venv(cfg, src, report, meta)  # type: ignore[assignment]
        if py is None:
            report.save()
            say(f"\nResume: fix the problem above, then rerun: {sys.executable} {src / 'runtime' / 'cli.py'} install")
            return 1

    try:
        staging = stage_runtime(src, cfg)
        errors = compile_check(staging)
        if errors:
            shutil.rmtree(staging, ignore_errors=True)
            report.step("runtime compile check", False, "; ".join(errors)[:600])
            report.save()
            return 1
        if (cfg / "server.py").exists():
            # Keep the previous runtime (v3 or v4) so 'loopback rollback' can return to it.
            label = re.sub(r"[^A-Za-z0-9.]+", "_", previous or "unknown")
            saved = snapshot_runtime(cfg, service_manager(cfg, ident), f"{label}-{time.strftime('%Y%m%d-%H%M%S')}")
            report.step("previous runtime saved", True, str(saved))
            move_runtime(staging, cfg)
            shutil.rmtree(staging, ignore_errors=True)
        else:
            deploy_staged(staging, cfg)
        fix_permissions(cfg)
        report.step("runtime files", True, f"v{source_version(src)} -> {cfg}")
    except Exception as exc:  # noqa: BLE001
        report.step("runtime files", False, str(exc))
        report.save()
        return 1
    try:
        where = install_cli_wrapper(src, cfg)
        report.step("CLI", True, where)
    except Exception as exc:  # noqa: BLE001
        report.step("CLI", False, str(exc), level="warning")

    report.set("phase", "stage1")
    report.save()
    argv = [str(py), str(cfg / "cli.py"), "install", "--stage2", "--non-interactive", "--ingress", args.ingress,
            "--tailscale-mode", args.tailscale_mode, "--source", str(src)]
    for flag, value in (("--host", args.host), ("--port", args.port),
                        ("--cloudflare-token-file", args.cloudflare_token_file),
                        ("--cloudflare-tunnel-id", args.cloudflare_tunnel_id)):
        if value:
            argv += [flag, str(value)]
    for flag, value in (("--no-service", args.no_service), ("--no-autostart", args.no_autostart),
                        ("--yes", args.yes), ("--allow-root", args.allow_root), ("--skip-venv", args.skip_venv)):
        if value:
            argv.append(flag)
    env = dict(os.environ, LOOPBACK_CONFIG_DIR=str(cfg))
    try:
        return subprocess.call(argv, env=env)
    except OSError as exc:
        say(f"{FAIL_MARK} could not run stage 2 with {py}: {exc}", err=True)
        say("Resume with: loopback repair", err=True)
        return 1


def apply_ingress(ctx: Any, args: argparse.Namespace, report: Report) -> None:
    want_ts = args.ingress in ("tailscale", "both")
    want_cf = args.ingress in ("cloudflare", "both")
    if not (want_ts or want_cf):
        report.step("ingress", True, "local only")
        return
    im = getattr(ctx, "ingress", None)
    if want_ts:
        if im is None:
            report.step("tailscale ingress", None, "configured, apply from dashboard", level="warning")
        else:
            try:
                im.apply_tailscale(True, args.tailscale_mode, 443, confirm_public=args.tailscale_mode == "funnel")
                report.step("tailscale ingress", True, f"mode={args.tailscale_mode}")
            except Exception as exc:  # noqa: BLE001
                report.step("tailscale ingress", None, f"not applied: {exc}", level="warning",
                            fix="run 'tailscale up', then enable Tailscale in the dashboard (Ingress)")
    if want_cf:
        if not args.host:
            report.step("cloudflare ingress", None, "no hostname given; finish in the dashboard (Ingress)",
                        level="warning")
        elif im is None or not (args.cloudflare_token_file or args.cloudflare_tunnel_id):
            report.step("cloudflare ingress", None,
                        f"{args.host}: configured, apply from dashboard (needs a tunnel token file or tunnel id)",
                        level="warning", fix="rerun with --cloudflare-token-file / --cloudflare-tunnel-id or use the dashboard")
        else:
            token_file = str(Path(args.cloudflare_token_file).expanduser().resolve()) if args.cloudflare_token_file else None
            try:
                im.apply_cloudflare(True, hostname=args.host, tunnel_id=args.cloudflare_tunnel_id,
                                    token_file=token_file)
                # Hand the tunnel process over to the supervised gateway (it starts cloudflared itself).
                try:
                    im.stop_cloudflare()
                except Exception:  # noqa: BLE001
                    pass
                report.step("cloudflare ingress", True, args.host)
            except Exception as exc:  # noqa: BLE001
                report.step("cloudflare ingress", None, f"not applied: {exc}", level="warning",
                            fix="check the tunnel in the dashboard (Ingress -> Cloudflare -> Diagnostics)")


def start_gateway(cfg: Path, ident: Any, port: int, args: argparse.Namespace, report: Report) -> Any:
    from service import stop_legacy_tunnel

    if stop_legacy_tunnel(cfg):
        report.step("v3 cloudflared stopped", True, "the gateway now manages the tunnel (cloudflared.pid)")
    svc = service_manager(cfg, ident, port)
    want_unit = not (args.no_service or args.no_autostart)
    if want_unit and svc.detect() != "none":
        res = svc.install_unit(svc.python(), cfg, port, {})
        report.step("service", res.get("ok"), f"{res.get('manager')}: {res.get('path')} {res.get('detail') or ''}".strip(),
                    fix="loopback logs; loopback autostart on")
        if res.get("backup"):
            report.step("previous unit backed up", True, res["backup"])
        if res.get("linger"):
            report.step("start at boot (linger)", "enabled" in res["linger"], res["linger"], level="warning")
        if not res.get("ok"):
            res2 = svc.start()
            report.step("fallback start (pid file)", res2.get("ok"), res2.get("detail", ""))
    else:
        why = "--no-service" if not want_unit else "no supported service manager"
        svc.stop()  # an older gateway (e.g. v3 pid-file server) must not keep serving stale code
        res = svc.start()
        report.step("start gateway (loopback up)", res.get("ok"), f"{why}; pid={res.get('pid')} {res.get('detail', '')}".strip(),
                    fix=f"see {cfg / 'server.log'}")
    return svc


def print_recovery(cfg: Path) -> None:
    say("\nIf something is wrong:")
    say("  loopback doctor        checks everything and prints fixes")
    say("  loopback logs -n 200   recent server log (redacted)")
    say("  loopback rollback      restore the previous runtime")
    say(f"  config + backups:      {cfg}")


def install_stage2(args: argparse.Namespace) -> int:
    ident = get_identity()
    cfg = Path(ident.config_dir)
    report = Report.load("install", cfg / "install-report.json")
    report.set("phase", "stage2")
    try:
        ctx = load_ctx(cfg)
    except Exception as exc:  # noqa: BLE001
        report.step("load configuration / migrate", False, str(exc),
                    fix="restore the pre-install backup in backups/ or run 'loopback repair'")
        report.save()
        return 1
    migrated = bool(ctx.config.data.get("_migrated_from"))
    report.step("configuration", True, "migrated from v3 config.env" if migrated else "config.json")
    try:
        updates: dict[str, Any] = {}
        if args.port:
            updates["port"] = int(args.port)
        if args.ingress in ("cloudflare", "both") and args.host:
            updates["public_host"] = args.host
        ctx.config.save(updates, actor="installer")
        ctx.refresh_auth_urls()
        report.step("config.json", True, f"port={ctx.config.port}")
    except Exception as exc:  # noqa: BLE001
        report.step("config.json", False, str(exc), fix="fix the option and rerun the installer")
        report.save()
        return 1
    if not ctx.policy.path.exists():
        try:
            ctx.policy.save({})
        except Exception as exc:  # noqa: BLE001
            report.step("policy defaults", False, str(exc))
    report.step("policy", True, f"profile={ctx.policy.profile} (home workspace {ident.home})")
    report.step("credentials", True, "machine token + OAuth signing key (stored 0600, not shown)")
    if ctx.legacy_mcp_token_enabled():
        report.step("legacy machine-token MCP access", None,
                    "kept ON for existing clients; migrate them to OAuth/API tokens, then 'loopback auth legacy off'",
                    level="warning")
    apply_ingress(ctx, args, report)
    port = ctx.config.port
    start_gateway(cfg, ident, port, args, report)
    timeout = max(20, int(ctx.config.data.get("startup_timeout") or 15) * 2)
    health = wait_health(port, timeout)
    if not report.step("health", bool(health), f"http://127.0.0.1:{port}/healthz" + (f" v{health.get('version')}" if health else ""),
                       fix="loopback logs -n 100"):
        log = cfg / "server.log"
        if log.exists():
            say(redact(tail_text(log.read_text(encoding="utf-8", errors="replace"), 25)))
        report.set("ok", False)
        report.save()
        say("\nThe runtime is installed but the gateway did not start. After fixing the error above run:")
        say("  loopback up    (or: loopback repair)")
        print_recovery(cfg)
        return 1
    errors = [c for c in builtin_doctor(cfg, False, ctx) if not c["ok"]]
    report.step("doctor (quick)", not any(c["level"] == "error" for c in errors),
                ", ".join(c["name"] for c in errors) or "all checks passed", level="warning")
    res = verify_mcp(ctx.auth, port)
    report.step("MCP initialize", res.get("ok"), res.get("detail") or f"{(res.get('server') or {}).get('name', '')} protocol={res.get('protocol')}",
                fix="loopback logs; loopback doctor --deep")
    for host in ctx.config.public_hosts():
        status, _, _ = http(f"https://{host}/healthz", timeout=10)
        report.step(f"public health https://{host}", status == 200, f"HTTP {status}", level="warning",
                    fix="DNS/tunnel can take a minute; check the dashboard (Ingress)")
    code = ctx.auth.create_enrollment(600)
    url = f"http://127.0.0.1:{port}/admin/enroll#code={code}"
    report.set("enrollment_url", f"http://127.0.0.1:{port}/admin/enroll#code=[REDACTED]")
    report.set("version", cli_version())
    report.set("config_dir", str(cfg))
    report.set("finished_at", time.time())
    ok = not report.failed()
    report.set("ok", ok)
    report.set("phase", "done")
    report.save()
    say("")
    say(f"{OK_MARK if ok else FAIL_MARK} Loopback {cli_version()} {'installed' if ok else 'installed with errors'}")
    say(f"  MCP endpoint (local): http://127.0.0.1:{port}/mcp")
    for host in ctx.config.public_hosts():
        say(f"  MCP endpoint (public): https://{host}/mcp")
    say(f"  Dashboard:            http://127.0.0.1:{port}/admin")
    say("")
    say("Open this one-time link on this machine within 10 minutes to set the dashboard password:")
    say(f"  {url}")
    say("  (new link any time: loopback auth enroll)")
    say(f"\nReport: {cfg / 'install-report.json'}  (loopback report)")
    print_recovery(cfg)
    return 0 if ok else 1


# =============================================================== upgrade / rollback
def detect_ingress(cfg: Path) -> list[str]:
    data = read_json(cfg / "config.json", None)
    out = []
    if isinstance(data, dict):
        ing = data.get("ingress") or {}
        if (ing.get("cloudflare") or {}).get("enabled"):
            out.append("cloudflare")
        if (ing.get("tailscale") or {}).get("enabled"):
            out.append(f"tailscale-{(ing.get('tailscale') or {}).get('mode', 'serve')}")
    else:
        env = parse_env(cfg / "config.env")
        ingress = (env.get("LOOPBACK_INGRESS") or "local").lower()
        if ingress == "tailscale":
            out.append("tailscale-funnel")
        elif ingress != "local":
            out.append(ingress)
    return out or ["local"]


def ensure_venv_for_upgrade(cfg: Path, src: Path, report: Report) -> bool:
    if (cfg / "python-override").exists() and not venv_python(cfg).exists():
        report.step("python dependencies", True, "test-only python override; skipped")
        return True
    py = venv_python(cfg)
    base = shutil.which("python3") or shutil.which("python") or sys.executable
    base_ver = python_version_of(base) if base else None
    have = python_version_of(py) if py.exists() else None
    if have is None or (base_ver and base_ver >= MIN_PY and base_ver != have):
        old = cfg / f"venv.old-{time.strftime('%Y%m%d-%H%M%S')}"
        if (cfg / "venv").exists():
            os.replace(cfg / "venv", old)
        res = run([base, "-m", "venv", str(cfg / "venv")], timeout=600)
        if res.returncode != 0:
            if old.exists():
                shutil.rmtree(cfg / "venv", ignore_errors=True)
                os.replace(old, cfg / "venv")
            report.step("recreate venv", False, tail_text(res.stderr, 5))
            return False
        report.step("venv recreated", True, f"python {have} -> {base_ver}")
    res = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
               str(src / "requirements.txt")], timeout=1800)
    if not report.step("python dependencies", res.returncode == 0, tail_text(res.stderr, 8) if res.returncode else "ok"):
        return False
    for old in cfg.glob("venv.old-*"):
        shutil.rmtree(old, ignore_errors=True)
    return True


def post_upgrade_checks(cfg: Path, new_version: str, report: Report, timeout: float = 40) -> list[str]:
    """Critical checks after starting the new runtime; returns failure reasons."""
    port = read_port(cfg)
    failures = []
    health = wait_health(port, timeout)
    if not report.step("health", bool(health), f"version {health.get('version') if health else 'n/a'}"):
        failures.append("gateway did not answer /healthz")
        return failures
    if health.get("version") and health.get("version") != new_version:
        report.step("health version", False, f"expected {new_version}, got {health.get('version')}")
        failures.append("old version still running")
    status, _, _ = http(f"http://127.0.0.1:{port}/admin/login", timeout=5)
    if not report.step("dashboard login page", status == 200, f"HTTP {status}"):
        failures.append(f"/admin/login returned {status}")
    res = helper_json(cfg, "verify-mcp", "--port", str(port))
    if not report.step("MCP initialize", res.get("ok"), res.get("detail") or res.get("error") or "ok"):
        failures.append("MCP initialize failed")
    for host in res.get("public_hosts") or []:
        st, _, _ = http(f"https://{host}/healthz", timeout=10)
        report.step(f"public health https://{host}", st == 200, f"HTTP {st}", level="warning")
    return failures


def restart_after_swap(cfg: Path, svc: Any, report: Report, replace_unit: bool) -> Any:
    from service import ServiceManager

    svc = ServiceManager(svc.identity, cfg, read_port(cfg))
    if replace_unit:
        res = svc.install_unit(svc.python(), cfg, svc.port, {})
        report.step("service unit", res.get("ok"), f"{res.get('manager')} {res.get('path')} backup={res.get('backup')}")
        if not res.get("ok"):
            svc.start()
    else:
        res = svc.start()
        report.step("start gateway", res.get("ok"), res.get("detail", ""))
    return svc


def restore_runtime_backup(cfg: Path, backup: Path, svc: Any, report: Report, *, restore_config: bool) -> None:
    """Copy runtime (and optionally config/unit/CLI snapshot) from ``backup`` back into ``cfg``."""
    svc.stop()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    replaced = cfg / "backups" / f"replaced-{installed_version(cfg) or 'unknown'}-{stamp}".replace("/", "_")
    move_runtime(cfg, replaced)
    copy_runtime(backup, cfg)
    if restore_config and (backup / "_config").is_dir():
        snap = replaced / "_config"
        snap.mkdir(parents=True, exist_ok=True)
        for f in (backup / "_config").iterdir():
            if (cfg / f.name).exists():
                shutil.copy2(cfg / f.name, snap / f.name)
            shutil.copy2(f, cfg / f.name)
            if not WINDOWS:
                os.chmod(cfg / f.name, 0o600)
    unit_dir = backup / "_unit"
    if unit_dir.is_dir():
        for f in unit_dir.iterdir():
            svc.restore_unit(f)
    else:
        # The backup had no unit (it ran via pid file): remove a unit that points at the new layout.
        pass
    bin_dir = backup / "_bin"
    if bin_dir.is_dir():
        for f in bin_dir.iterdir():
            target = cli_wrapper_path().parent / f.name
            atomic_write(target, f.read_bytes(), 0o755)
    fix_permissions(cfg)
    report.step("runtime restored", True, f"from {backup.name} (replaced runtime kept in {replaced.name})")


def start_any(cfg: Path, report: Report) -> None:
    """Start whatever runtime is installed now (v4 via ServiceManager, v3 via its unit or wrapper)."""
    from service import ServiceManager

    svc = ServiceManager(get_identity(), cfg, read_port(cfg))
    if (cfg / "cli.py").exists() or svc.installed():
        res = svc.start()
        report.step("start", res.get("ok"), res.get("detail", ""))
        return
    if svc.legacy_unit():
        res = run(["systemctl", "--user", "restart", "loopback.service"], timeout=120)
        report.step("start (v3 unit)", res.returncode == 0, tail_text(res.stderr, 3))
        return
    wrapper = cli_wrapper_path()
    if wrapper.exists():
        res = run([str(wrapper), "up"], timeout=120)
        if res.returncode == 0:
            report.step("start (v3 wrapper)", True, tail_text(res.stdout, 2))
            return
        report.step("start (v3 wrapper)", False, tail_text(res.stderr or res.stdout, 3))
    # No usable v3 wrapper: launch the restored v3 runtime directly through its venv,
    # reproducing what the v3 'loopback up' did (environment from config.env, pid file).
    ok, detail = start_v3_direct(cfg, svc)
    report.step("start (v3 runtime, direct)", ok, detail)


def start_v3_direct(cfg: Path, svc: Any) -> tuple[bool, str]:
    """Start a v3 (pre-4.0) runtime without the v3 CLI wrapper."""
    from service import venv_python

    legacy: dict[str, str] = {}
    env_file = cfg / "config.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                legacy[key.strip()] = value.strip().strip("'\"")
    try:
        port = int(legacy.get("LOOPBACK_PORT") or read_port(cfg))
    except (TypeError, ValueError):
        port = read_port(cfg)
    env = dict(os.environ)
    env.update({
        "LOOPBACK_LOCAL_PORT": str(port),
        "LOOPBACK_COMMAND_TIMEOUT": legacy.get("LOOPBACK_TIMEOUT", "120"),
        "LOOPBACK_MAX_OUTPUT_BYTES": legacy.get("LOOPBACK_MAX_OUTPUT_BYTES", "1048576"),
        "LOOPBACK_ACCESS_MODE": legacy.get("LOOPBACK_ACCESS_MODE", "standard"),
    })
    (cfg / "public_host").write_text((legacy.get("LOOPBACK_PUBLIC_HOST") or "localhost") + "\n", encoding="utf-8")
    python = venv_python(cfg)
    if not Path(python).exists():
        return False, f"venv interpreter missing: {python}"
    argv = [str(python), "-m", "uvicorn", "server:app", "--app-dir", str(cfg), "--host", "127.0.0.1", "--port", str(port),
            "--proxy-headers", "--forwarded-allow-ips=127.0.0.1", "--log-level", "info"]
    with open(cfg / "server.log", "ab") as log:
        kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if WINDOWS else {"start_new_session": True}
        proc = subprocess.Popen(argv, cwd=str(cfg), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    (cfg / "server.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
    healthy = svc.wait_healthy(30, port)
    return healthy, (f"pid {proc.pid} on port {port}" if healthy else "did not become healthy within 30s; see server.log")


def cmd_upgrade(args: argparse.Namespace) -> int:
    refuse_root(args.allow_root)
    ident = get_identity()
    cfg = Path(ident.config_dir)
    old = installed_version(cfg)
    if not old:
        die(f"no Loopback installation found in {cfg}; run 'loopback install' (or ./install.sh)")
    src = source_root(args.source)
    if not src:
        die("--source DIR is required (a checkout or extracted release containing runtime/)", 2)
    new = source_version(src)
    from service import ServiceManager

    svc = ServiceManager(ident, cfg, read_port(cfg))
    st = svc.status()
    legacy = st["legacy_unit"]
    replace_unit = svc.detect() != "none" and (st["installed"] or legacy)
    say(f"Loopback upgrade plan ({cfg})")
    say(f"  installed version: {old}")
    say(f"  new version:       {new}  ({src})")
    say(f"  ingress:           {', '.join(detect_ingress(cfg))}")
    say(f"  service manager:   {svc.detect()}" + (" (v3 oneshot unit -> will be replaced by Type=simple)" if legacy else
                                                  (" (unit will be rewritten)" if replace_unit else " (pid-file supervisor)")))
    say("  steps: backup -> stage + compile -> stop -> swap runtime -> pip -> migrate -> unit -> start -> verify")
    if version_tuple(new) < version_tuple(old) and not old.startswith("unknown"):
        say(f"{WARN_MARK} {new} is older than {old}; use 'loopback rollback' to go back instead.")
    elif new == old:
        say("  (same version: files are re-deployed)")
    if args.check:
        return 0
    if not confirm("Proceed with the upgrade?", default=True, assume_yes=args.yes):
        say("aborted")
        return 1
    report = Report("upgrade", cfg / "upgrade-report.json")
    report.set("from_version", old)
    report.set("to_version", new)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9.]+", "_", old)
    try:
        archive = make_backup(cfg, f"pre-upgrade-{label}-{stamp}")
        report.step("full backup", True, str(archive))
    except Exception as exc:  # noqa: BLE001
        report.step("full backup", False, str(exc), fix="free disk space; nothing was changed")
        report.save()
        return 1
    try:
        staging = stage_runtime(src, cfg)
    except Exception as exc:  # noqa: BLE001
        report.step("stage new runtime", False, str(exc))
        report.save()
        return 1
    errors = compile_check(staging)
    if errors:
        shutil.rmtree(staging, ignore_errors=True)
        report.step("compile check", False, "; ".join(errors)[:600], fix="the release is broken; nothing was changed")
        report.save()
        return 1
    report.step("stage + compile check", True, staging.name)
    svc.stop()
    from service import stop_legacy_tunnel

    stop_legacy_tunnel(cfg)
    if deps_available():
        try:
            from service import stop_ingress

            stop_ingress(load_ctx(cfg))
        except BaseException:  # noqa: BLE001
            pass
    runtime_backup = snapshot_runtime(cfg, svc, f"{label}-{stamp}")
    report.step("previous runtime saved", True, str(runtime_backup))
    report.set("runtime_backup", str(runtime_backup))
    try:
        move_runtime(staging, cfg)
        shutil.rmtree(staging, ignore_errors=True)
        fix_permissions(cfg)
        report.step("runtime swapped", True, f"v{new}")
    except Exception as exc:  # noqa: BLE001
        report.step("runtime swap", False, str(exc))
        return _upgrade_abort(cfg, runtime_backup, svc, report, "runtime swap failed")
    try:
        install_cli_wrapper(src, cfg)
    except Exception as exc:  # noqa: BLE001
        report.step("CLI wrapper", False, str(exc), level="warning")
    if not args.skip_venv and not ensure_venv_for_upgrade(cfg, src, report):
        return _upgrade_abort(cfg, runtime_backup, svc, report, "dependency installation failed")
    mig = helper_json(cfg, "migrate")
    if not report.step("migrations", mig.get("ok"), mig.get("detail") or mig.get("error") or ""):
        return _upgrade_abort(cfg, runtime_backup, svc, report, "migration failed")
    if mig.get("legacy_machine_token_mcp"):
        say("")
        say("NOTICE: existing MCP clients that use the machine token keep working")
        say("        (auth.legacy_machine_token_mcp = true). Migrate them to OAuth or per-client")
        say("        API tokens (dashboard -> Auth, or 'loopback auth token create NAME --scopes ...'),")
        say("        then disable the legacy path with 'loopback auth legacy off'.")
        say("")
    if args.no_restart:
        report.step("restart", None, "skipped (--no-restart); run 'loopback restart'", level="warning")
        report.set("ok", True)
        report.save()
        return 0
    svc = restart_after_swap(cfg, svc, report, replace_unit)
    failures = post_upgrade_checks(cfg, new, report)
    if failures:
        return _upgrade_abort(cfg, runtime_backup, svc, report, "; ".join(failures))
    report.set("ok", True)
    report.set("finished_at", time.time())
    report.save()
    say(f"\n{OK_MARK} upgraded {old} -> {new}. Backup: {archive}")
    say("  undo: loopback rollback")
    return 0


def _upgrade_abort(cfg: Path, runtime_backup: Path, svc: Any, report: Report, reason: str) -> int:
    say(f"\n{FAIL_MARK} upgrade failed: {reason}. Restoring the previous runtime...", err=True)
    try:
        restore_runtime_backup(cfg, runtime_backup, svc, report, restore_config=True)
        start_any(cfg, report)
        health = wait_health(read_port(cfg), 40)
        report.step("previous version healthy", bool(health), f"version {health.get('version') if health else 'n/a'}")
    except Exception as exc:  # noqa: BLE001
        report.step("restore previous runtime", False, str(exc),
                    fix=f"restore manually: loopback restore {cfg / 'backups'}/pre-upgrade-*.tar.gz")
    report.set("ok", False)
    report.set("reason", reason)
    report.save()
    say(f"report: {cfg / 'upgrade-report.json'}", err=True)
    return 1


def list_runtime_backups(cfg: Path) -> list[Path]:
    bdir = cfg / "backups"
    if not bdir.is_dir():
        return []
    items = [p for p in bdir.iterdir() if p.is_dir() and p.name.startswith(("runtime-", "replaced-"))]
    return sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)


def cmd_rollback(args: argparse.Namespace) -> int:
    ident = get_identity()
    cfg = Path(ident.config_dir)
    backups = list_runtime_backups(cfg)
    if args.list:
        if args.json:
            emit_json([{"name": b.name, "version": installed_version(b), "created_at": b.stat().st_mtime} for b in backups])
            return 0
        if not backups:
            say("no runtime backups")
        for b in backups:
            say(f"{b.name}  version={installed_version(b)}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(b.stat().st_mtime))}")
        return 0
    if args.to:
        chosen = cfg / "backups" / args.to
        if not chosen.is_dir():
            die(f"no runtime backup named {args.to} (see loopback rollback --list)", 2)
    else:
        runtime_only = [b for b in backups if b.name.startswith("runtime-")]
        if not runtime_only:
            die("no runtime backups to roll back to")
        chosen = runtime_only[0]
    current = installed_version(cfg)
    say(f"Rolling back {current} -> {installed_version(chosen)} ({chosen.name})")
    if not confirm("Continue?", default=True, assume_yes=args.yes):
        return 1
    report = Report("rollback", cfg / "rollback-report.json")
    try:
        archive = make_backup(cfg, f"pre-rollback-{time.strftime('%Y%m%d-%H%M%S')}")
        report.step("safety backup", True, str(archive))
    except Exception as exc:  # noqa: BLE001
        report.step("safety backup", False, str(exc))
        report.save()
        return 1
    if deps_available():
        try:
            from service import stop_ingress

            stop_ingress(load_ctx(cfg))
        except BaseException:  # noqa: BLE001
            pass
    svc = service_manager(cfg, ident)
    restore_runtime_backup(cfg, chosen, svc, report, restore_config=True)
    req = cfg / "requirements.txt"
    if req.exists() and venv_python(cfg).exists():
        res = run([str(venv_python(cfg)), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)],
                  timeout=1800)
        report.step("python dependencies", res.returncode == 0, tail_text(res.stderr, 5), level="warning")
    start_any(cfg, report)
    health = wait_health(read_port(cfg), 40)
    ok = report.step("health", bool(health), f"version {health.get('version') if health else 'n/a'}",
                     fix="loopback logs; loopback restore <pre-rollback archive>")
    report.set("ok", ok)
    report.save()
    return 0 if ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    if not cfg.is_dir():
        die(f"{cfg} does not exist")
    try:
        path = make_backup(cfg, output=Path(args.output) if args.output else None)
    except Exception as exc:  # noqa: BLE001
        die(f"backup failed: {exc}")
    if args.json:
        emit_json({"path": str(path), "size": path.stat().st_size})
    else:
        say(f"{OK_MARK} backup written: {path} ({path.stat().st_size} bytes, mode 0600)")
        say("  it contains credentials (token, auth.db): store it like a password")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    ident = get_identity()
    cfg = Path(ident.config_dir)
    archive = Path(args.archive).expanduser()
    if not archive.is_file():
        die(f"archive not found: {archive}", 2)
    try:
        with tarfile.open(archive, "r:*") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError) as exc:
        die(f"cannot read archive: {exc}", 2)
    say(f"Restore {archive} over {cfg} ({len(names)} entries). The current state is backed up first.")
    if not confirm("Continue?", default=False, assume_yes=args.yes):
        say("aborted")
        return 1
    report = Report("restore", cfg / "restore-report.json")
    svc = service_manager(cfg, ident)
    if deps_available():
        try:
            from service import stop_ingress

            stop_ingress(load_ctx(cfg))
        except BaseException:  # noqa: BLE001
            pass
    svc.stop()
    try:
        safety = make_backup(cfg, f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}")
        report.step("safety backup", True, str(safety))
    except Exception as exc:  # noqa: BLE001
        report.step("safety backup", False, str(exc))
        svc.start()
        report.save()
        return 1
    try:
        n = safe_extract(archive, cfg)
        report.step("extract", True, f"{n} files")
    except Exception as exc:  # noqa: BLE001
        report.step("extract", False, str(exc), fix=f"loopback restore {safety}")
        svc.start()
        report.save()
        return 1
    svc = service_manager(cfg, ident)
    res = svc.start()
    report.step("start", res.get("ok"), res.get("detail", ""))
    health = wait_health(read_port(cfg), 40)
    ok = report.step("health", bool(health), "", fix=f"loopback logs; undo: loopback restore {safety}")
    report.set("ok", ok)
    report.save()
    return 0 if ok else 1


# =============================================================== day-to-day commands
def urls(cfg: Path) -> dict[str, Any]:
    port = read_port(cfg)
    data = read_json(cfg / "config.json", {}) or {}
    hosts = []
    ing = data.get("ingress") or {}
    if (ing.get("cloudflare") or {}).get("enabled") and ing["cloudflare"].get("hostname"):
        hosts.append(ing["cloudflare"]["hostname"])
    if (ing.get("tailscale") or {}).get("enabled") and ing["tailscale"].get("dns_name"):
        hosts.append(str(ing["tailscale"]["dns_name"]).rstrip("."))
    if not data:
        env = parse_env(cfg / "config.env")
        if (env.get("LOOPBACK_INGRESS") or "local") != "local" and env.get("LOOPBACK_PUBLIC_HOST"):
            hosts.append(env["LOOPBACK_PUBLIC_HOST"])
    local = f"http://127.0.0.1:{port}"
    return {"local": f"{local}/mcp", "admin": f"{local}/admin", "public": [f"https://{h}/mcp" for h in hosts],
            "public_admin": [f"https://{h}/admin" for h in hosts], "port": port}


def require_installed(cfg: Path) -> None:
    if not (cfg / "server.py").exists():
        die(f"Loopback is not installed in {cfg}. Run: ./install.sh  (or python3 runtime/cli.py install)")


def cmd_up(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    svc = service_manager(cfg)
    res = svc.start()
    if not res.get("ok"):
        say(f"{FAIL_MARK} start failed: {res.get('detail')}", err=True)
        return 1
    health = wait_health(svc.port, 40)
    if not health:
        log = cfg / "server.log"
        if log.exists():
            say(redact(tail_text(log.read_text(encoding="utf-8", errors="replace"), 40)), err=True)
        say(f"{FAIL_MARK} server failed to start (see loopback logs)", err=True)
        return 1
    u = urls(cfg)
    say(f"{OK_MARK} Loopback is up ({res.get('manager')}, v{health.get('version')})")
    say(f"  local:  {u['local']}")
    say(f"  admin:  {u['admin']}")
    for p in u["public"]:
        say(f"  public: {p}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    if deps_available() and (cfg / "config.json").exists():
        try:
            from service import stop_ingress

            stop_ingress(load_ctx(cfg))
        except BaseException:  # noqa: BLE001
            pass
    res = service_manager(cfg).stop()
    if res.get("ok"):
        say(f"{OK_MARK} Loopback is down")
        return 0
    say(f"{FAIL_MARK} stop failed: {res.get('detail')}", err=True)
    return 1


def cmd_restart(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    svc = service_manager(cfg)
    res = svc.restart()
    health = wait_health(svc.port, 40) if res.get("ok") else None
    if health:
        say(f"{OK_MARK} restarted (v{health.get('version')})")
        return 0
    say(f"{FAIL_MARK} restart failed: {res.get('detail') or 'no health response'} (see loopback logs)", err=True)
    return 1


def restart_if_active(cfg: Path, reason: str) -> None:
    svc = service_manager(cfg)
    if svc.status()["active"]:
        svc.restart()
        say(f"  gateway restarted to apply {reason}" if wait_health(svc.port, 30) else
            f"{WARN_MARK} gateway did not come back; see loopback logs")
    else:
        say(f"  applies at next start ({reason})")


def cmd_status(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    st = service_manager(cfg).status() if cfg.exists() else {}
    health = healthz(read_port(cfg)) if cfg.exists() else None
    data = {"config_dir": str(cfg), "installed_version": installed_version(cfg), "service": st,
            "health": health, "urls": urls(cfg) if cfg.exists() else {}, "ingress": detect_ingress(cfg)}
    if args.json:
        emit_json(data)
        return 0 if health else 1
    say(f"loopback: {'UP' if health else 'DOWN'}" + (f" (pid {st.get('pid')})" if st.get("pid") else ""))
    say(f"  version:  {data['installed_version']}" + (f" (running {health.get('version')})" if health else ""))
    say(f"  service:  {st.get('effective_manager')} installed={st.get('installed')} enabled={st.get('enabled')}")
    if health:
        say(f"  profile:  {health.get('policy_profile')}   sandbox: {health.get('sandbox')}")
    say(f"  ingress:  {', '.join(data['ingress'])}")
    u = data["urls"]
    if u:
        say(f"  local:    {u['local']}")
        say(f"  admin:    {u['admin']}")
        for p in u["public"]:
            say(f"  public:   {p}")
    return 0 if health else 1


def cmd_logs(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    name = {"server": "server.log", "cloudflared": "cloudflared.log", "tailscale": "tailscale.log"}[args.file]
    log = cfg / name
    if not log.exists():
        die(f"{log} does not exist yet")
    lines = max(1, min(args.n, 100000))
    with open(log, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - lines * 400 - 4096))
        text = handle.read().decode("utf-8", "replace")
    for line in text.splitlines()[-lines:]:
        say(redact(line))
    if args.follow:
        try:
            with open(log, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                buf = b""
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        if log.exists() and log.stat().st_size < handle.tell():
                            handle.seek(0)  # rotated/truncated
                        time.sleep(0.5)
                        continue
                    buf += chunk
                    *full, buf = buf.split(b"\n")
                    for line in full:
                        say(redact(line.decode("utf-8", "replace")))
        except KeyboardInterrupt:
            return 0
    return 0


def cmd_url(args: argparse.Namespace) -> int:
    u = urls(cfg_dir())
    if args.json:
        emit_json(u)
    else:
        say(u["public"][0] if u["public"] else u["local"])
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    u = urls(cfg_dir())
    url = u["admin"]
    say(url)
    for p in u["public_admin"]:
        say(p)
    if args.open:
        import webbrowser

        webbrowser.open(url)
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    path = cfg / "token"
    if not path.is_file():
        die("no machine token yet (run the installer)")
    say("WARNING: this is the machine recovery credential. Do not paste it into web agents;", err=True)
    say("         use 'loopback auth token create' or OAuth for clients.", err=True)
    say(path.read_text(encoding="utf-8").strip())
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    health = healthz(read_port(cfg)) if cfg.exists() else None
    data = {"cli": cli_version(), "installed": installed_version(cfg), "running": (health or {}).get("version")}
    if args.json:
        emit_json(data)
    else:
        say(f"loopback {data['cli']} (installed: {data['installed']}, running: {data['running'] or 'not running'})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    out = {}
    for name in ("install-report.json", "upgrade-report.json"):
        data = read_json(cfg / name, None)
        if data is not None:
            out[name] = data
    if not out:
        die("no install report found")
    emit_json(out if len(out) > 1 else next(iter(out.values())))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    checks = run_doctor(cfg, args.deep)
    errors = sum(1 for c in checks if not c.get("ok") and c.get("level", "error") == "error")
    if args.json:
        emit_json({"checks": checks, "errors": errors})
    else:
        say(f"== Loopback doctor ({cfg}) ==")
        print_checks(checks)
        say(f"\n{errors} error(s)" if errors else f"\n{OK_MARK} no errors")
    return 1 if errors else 0


def parse_ttl(value: str | None) -> int | None:
    if value is None or str(value).lower() in {"", "never", "none", "0"}:
        return None
    m = re.fullmatch(r"(\d+)\s*([smhdw]?)", str(value).strip().lower())
    if not m:
        die(f"invalid TTL: {value} (use e.g. 3600, 12h, 30d, never)", 2)
    mult = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    return int(m.group(1)) * mult


def cmd_auth(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    ctx = load_ctx(cfg)
    auth = ctx.auth
    action = args.auth_cmd
    if action == "enroll":
        code = auth.create_enrollment(int(args.ttl))
        say(f"One-time dashboard setup link (valid {int(args.ttl) // 60} min, open on this machine):")
        say(f"  http://127.0.0.1:{ctx.config.port}/admin/enroll#code={code}")
        return 0
    if action == "token":
        sub = args.token_cmd
        if sub == "create":
            scopes = [s.strip() for s in (args.scopes or "").split(",") if s.strip()]
            try:
                value, record = auth.create_api_token(args.name, scopes, ttl_seconds=parse_ttl(args.ttl),
                                                      created_by="cli", note=args.note or "")
            except Exception as exc:  # noqa: BLE001
                die(str(getattr(exc, "description", "") or exc), 2)
            if args.json:
                say(json.dumps({"token": value, "record": record}, indent=2, default=str))
            else:
                say(f"{OK_MARK} API token '{record['name']}' (id {record['id']}, scopes {record['scope']})")
                say("  shown once - store it now:")
                say(value)
            return 0
        if sub == "list":
            rows = auth.list_api_tokens()
            if args.json:
                emit_json(rows)
            else:
                for r in rows:
                    state = "revoked" if r.get("revoked_at") else ("expired" if r.get("expires_at") and r["expires_at"] < time.time() else "active")
                    say(f"{r['id']:<10} {r['name']:<24} {r['scope']:<40} {state}")
            return 0
        if sub == "revoke":
            ok = auth.revoke_api_token(args.id)
            say(f"{OK_MARK} revoked {args.id}" if ok else f"{FAIL_MARK} no active token {args.id}")
            return 0 if ok else 1
    if action == "clients":
        rows = auth.list_clients()
        if args.json:
            emit_json(rows)
        else:
            for c in rows:
                say(f"{c.get('client_id')}  {c.get('client_name') or c.get('name') or ''}  "
                    f"{'revoked' if c.get('revoked_at') else 'active'}")
        return 0
    if action == "revoke-client":
        ok = auth.revoke_client(args.id)
        say(f"{OK_MARK} revoked client {args.id}" if ok else f"{FAIL_MARK} unknown client {args.id}")
        return 0 if ok else 1
    if action == "rotate-machine-token":
        auth.rotate_machine_token()
        say(f"{OK_MARK} machine token rotated (view it with 'loopback token')")
        restart_if_active(cfg, "the new machine token")
        return 0
    if action == "rotate-signing-key":
        auth.rotate_signing_key()
        say(f"{OK_MARK} OAuth signing key rotated; clients must refresh their access tokens")
        restart_if_active(cfg, "the new signing key")
        return 0
    if action == "password":
        if not sys.stdin.isatty():
            die("auth password needs an interactive terminal", 2)
        first = getpass.getpass("New dashboard password (10+ characters): ")
        second = getpass.getpass("Repeat: ")
        if first != second:
            die("passwords do not match", 2)
        try:
            auth.set_password(first)
        except Exception as exc:  # noqa: BLE001
            die(str(getattr(exc, "description", "") or exc), 2)
        say(f"{OK_MARK} dashboard password set")
        return 0
    if action == "legacy":
        enabled = args.state == "on"
        ctx.config.save({"auth": {"legacy_machine_token_mcp": enabled}}, actor="cli")
        say(f"{OK_MARK} legacy machine-token MCP access {'enabled' if enabled else 'disabled'}")
        restart_if_active(cfg, "the auth change")
        return 0
    die("unknown auth command", 2)
    return 2


def cmd_policy(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    from policy import PROFILE_ALIASES, PROFILES, PolicyStore

    import identity

    store = PolicyStore(cfg, home=identity.detect(prefer_sudo_user=False).home)
    if args.policy_cmd == "profile":
        name = args.name.lower()
        if name not in PROFILES and name not in PROFILE_ALIASES:
            die(f"unknown profile {name}; choose from {', '.join(sorted(set(PROFILES) | set(PROFILE_ALIASES)))}", 2)
        try:
            data = store.save({"profile": name})
        except ValueError as exc:
            die(str(exc), 2)
        say(f"{OK_MARK} policy profile: {data['profile']}" + (f" (alias {name})" if name in PROFILE_ALIASES else ""))
        restart_if_active(cfg, "the policy change")
        return 0
    emit_json(store.data)
    return 0


def cmd_node(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    from fleet import FleetStore

    store = FleetStore(cfg)
    if args.node_cmd == "add":
        token = None
        if args.token_env:
            token = os.environ.get(args.token_env)
            if not token:
                die(f"environment variable {args.token_env} is empty", 2)
        elif sys.stdin.isatty():
            token = getpass.getpass("Bearer token (optional, hidden): ") or None
        if not re.match(r"^https?://", args.url):
            die("URL must start with https:// (or http:// for local testing)", 2)
        try:
            store.upsert(args.name, args.url, token)
        except ValueError as exc:
            die(str(exc), 2)
        say(f"{OK_MARK} node added: {args.name}" + (" (token stored separately, 0600)" if token else ""))
        return 0
    if args.node_cmd == "remove":
        ok = store.remove(args.name)
        if store.path.exists() and not WINDOWS:
            os.chmod(store.path, 0o600)
        say(f"{OK_MARK} node removed: {args.name}" if ok else f"{FAIL_MARK} no node named {args.name}")
        return 0 if ok else 1
    emit_json(store.list())
    return 0


def cmd_ssh(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    ctx = load_ctx(cfg)
    store = getattr(ctx, "ssh", None)
    if store is None:
        die("SSH targets are not available in this installation (ssh_targets module missing)")
    if args.ssh_cmd == "add":
        m = re.fullmatch(r"(?:([^@]+)@)?(\[[^\]]+\]|[^:]+)(?::(\d+))?", args.target)
        if not m:
            die("target must be user@host[:port]", 2)
        fields: dict[str, Any] = {"name": args.name, "host": m.group(2).strip("[]"), "user": m.group(1)}
        if m.group(3):
            fields["port"] = int(m.group(3))
        for key in ("identity_file", "kind", "group"):
            value = getattr(args, key)
            if value:
                fields[key] = value
        try:
            row = store.upsert(**fields)
        except ValueError as exc:
            die(str(exc), 2)
        say(f"{OK_MARK} SSH target saved: {row.get('name')} ({row.get('user')}@{row.get('host')}:{row.get('port')})")
        return 0
    if args.ssh_cmd == "remove":
        ok = store.remove(args.name)
        say(f"{OK_MARK} removed {args.name}" if ok else f"{FAIL_MARK} no target {args.name}")
        return 0 if ok else 1
    if args.ssh_cmd == "probe":
        try:
            res = store.probe(args.name)
        except (KeyError, ValueError) as exc:
            die(str(exc), 2)
        emit_json(res)
        return 0 if res.get("status") == "healthy" else 1
    emit_json(store.list())
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    require_installed(cfg)
    svc = service_manager(cfg)
    if args.state == "on":
        if svc.detect() == "none":
            die("no supported service manager on this system; use 'loopback up' (consider your OS's own autostart)")
        res = svc.install_unit(svc.python(), cfg, svc.port, {})
        say(f"{OK_MARK if res['ok'] else FAIL_MARK} autostart via {res['manager']}: {res.get('path')} {res.get('detail') or ''}")
        if res.get("linger"):
            say(f"  linger: {res['linger']}")
        return 0 if res["ok"] else 1
    if args.state == "off":
        res = svc.uninstall_unit()
        say(f"{OK_MARK} autostart disabled ({res['manager']}); Loopback is stopped - run 'loopback up' to start it manually")
        return 0 if res.get("ok") else 1
    st = svc.status()
    if args.json:
        emit_json(st)
    else:
        say(f"autostart: {'enabled' if st['installed'] and st['enabled'] else 'disabled'} ({st['manager']})"
            + (" - v3 unit present, run 'loopback repair'" if st["legacy_unit"] else ""))
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    ident = get_identity()
    cfg = Path(ident.config_dir)
    say("This stops Loopback, removes its service and CLI wrapper" +
        ("." if args.keep_config else f", and deletes {cfg} (after a final backup)."))
    if not confirm("Uninstall Loopback?", default=False, assume_yes=args.yes):
        say("aborted")
        return 1
    if deps_available() and (cfg / "config.json").exists():
        try:
            from service import stop_ingress

            stop_ingress(load_ctx(cfg))
        except BaseException:  # noqa: BLE001
            pass
    svc = service_manager(cfg, ident)
    svc.uninstall_unit()
    svc.stop()
    say(f"{OK_MARK} service removed")
    if not args.keep_config and cfg.is_dir():
        final = Path(ident.home) / f"loopback-final-backup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
        try:
            make_backup(cfg, output=final)
        except Exception as exc:  # noqa: BLE001
            die(f"final backup failed ({exc}); nothing deleted. Use --keep-config or free disk space.")
        say(f"{OK_MARK} final backup: {final} (contains credentials; delete it when no longer needed)")
        shutil.rmtree(cfg, ignore_errors=True)
        say(f"{OK_MARK} removed {cfg}")
    wrapper = cli_wrapper_path()
    for p in ([wrapper, wrapper.parent / "loopback.ps1"] if WINDOWS else [wrapper]):
        try:
            p.unlink()
        except OSError:
            pass
    say(f"{OK_MARK} CLI wrapper removed")
    return 0


def cmd_update_check(args: argparse.Namespace) -> int:
    cfg = cfg_dir()
    result: dict[str, Any] | None = None
    if deps_available() and (cfg / "config.json").exists():
        try:
            import maintenance  # type: ignore

            ctx = load_ctx(cfg)
            checker_cls = getattr(maintenance, "UpdateChecker", None)
            if checker_cls is not None:
                checker = checker_cls(ctx)
                fn = getattr(checker, "check", None)
                if fn:
                    try:
                        result = fn(force=True)
                    except TypeError:
                        result = fn()
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            say(f"{WARN_MARK} update checker failed: {exc}", err=True)
    if result is None:
        status, _, body = http(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                               headers={"Accept": "application/vnd.github+json", "User-Agent": "loopback-cli"}, timeout=10)
        latest = None
        if status == 200:
            try:
                latest = str(json.loads(body).get("tag_name") or "").lstrip("v") or None
            except ValueError:
                latest = None
        current = installed_version(cfg) or cli_version()
        result = {"current": current, "latest": latest, "checked_at": time.time(),
                  "available": bool(latest and version_tuple(latest) > version_tuple(current)),
                  "notes_url": f"https://github.com/{GITHUB_REPO}/releases" if latest else None,
                  "error": None if status == 200 else f"HTTP {status}"}
    if args.json:
        emit_json(result)
    else:
        if result.get("available"):
            say(f"Update available: {result.get('current')} -> {result.get('latest')}  ({result.get('notes_url')})")
            say("  upgrade: curl -fsSL https://github.com/p5n-n3t/loopback/releases/latest/download/install.sh | bash")
            say("       or: loopback upgrade --source <extracted release>")
        else:
            say(f"Loopback {result.get('current')} is up to date" +
                (f" (check failed: {result['error']})" if result.get("error") else ""))
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    refuse_root(args.allow_root)
    ident = get_identity()
    cfg = Path(ident.config_dir)
    if not cfg.is_dir():
        die(f"{cfg} does not exist; run the installer")
    report = Report("repair", cfg / "repair-report.json")
    if not WINDOWS:
        report.step("permissions", True, f"{fix_permissions(cfg)} entries fixed (dirs 0700, files 0600)")
    src = source_root(args.source) if args.source else None
    svc = service_manager(cfg, ident)
    if src:
        try:
            archive = make_backup(cfg, f"pre-install-repair-{time.strftime('%Y%m%d-%H%M%S')}")
            report.step("backup", True, str(archive))
            staging = stage_runtime(src, cfg)
            errors = compile_check(staging)
            if errors:
                shutil.rmtree(staging, ignore_errors=True)
                report.step("compile check", False, "; ".join(errors)[:400])
                report.save()
                return 1
            svc.stop()
            snapshot_runtime(cfg, svc, f"{re.sub(r'[^A-Za-z0-9.]+', '_', installed_version(cfg) or 'unknown')}-{time.strftime('%Y%m%d-%H%M%S')}")
            move_runtime(staging, cfg)
            shutil.rmtree(staging, ignore_errors=True)
            install_cli_wrapper(src, cfg)
            fix_permissions(cfg)
            report.step("runtime re-copied", True, f"v{source_version(src)} from {src}")
        except Exception as exc:  # noqa: BLE001
            report.step("runtime re-copy", False, str(exc))
            report.save()
            return 1
    override = (cfg / "python-override").exists() and not venv_python(cfg).exists()
    if not override:
        py = venv_python(cfg)
        res = run([str(py), "-c", "import uvicorn, starlette, jwt, mcp, httpx"], timeout=60) if py.exists() else None
        if res is None or res.returncode != 0:
            req = cfg / "requirements.txt"
            if not req.exists() and src:
                req = src / "requirements.txt"
            base = shutil.which("python3") or sys.executable
            shutil.rmtree(cfg / "venv", ignore_errors=True)
            r1 = run([base, "-m", "venv", str(cfg / "venv")], timeout=600)
            r2 = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)], timeout=1800) \
                if r1.returncode == 0 and req.exists() else r1
            if not report.step("venv recreated", r2.returncode == 0, tail_text(r2.stderr, 5)):
                report.save()
                return 1
        else:
            report.step("venv", True, "healthy")
    mig = helper_json(cfg, "migrate")
    report.step("configuration", mig.get("ok"), mig.get("detail") or mig.get("error") or "")
    st = svc.status()
    if svc.detect() != "none" and (st["installed"] or st["legacy_unit"]):
        res = svc.install_unit(svc.python(), cfg, svc.port, {})
        report.step("service unit", res.get("ok"), f"{res.get('manager')} {res.get('path')}")
    else:
        svc.restart()
    health = wait_health(read_port(cfg), 40)
    ok = report.step("health", bool(health), f"version {health.get('version') if health else 'n/a'}", fix="loopback logs")
    report.set("ok", ok)
    report.save()
    return 0 if ok else 1


# =============================================================== hidden: serve / helper
def cmd_serve(args: argparse.Namespace) -> int:
    """Run the gateway in the foreground (Windows Task Scheduler entry; also handy in containers)."""
    cfg = cfg_dir()
    os.environ["LOOPBACK_CONFIG_DIR"] = str(cfg)
    os.chdir(get_identity().home)
    port = args.port or read_port(cfg)
    if args.log:
        log = cfg / "server.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        stream = os.fdopen(fd, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = stream
    atomic_write(cfg / "server.pid", f"{os.getpid()}\n")
    try:
        import uvicorn

        uvicorn.run("server:app", app_dir=str(cfg), host="127.0.0.1", port=port, proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1", log_level="info")
    finally:
        try:
            if (cfg / "server.pid").read_text().strip() == str(os.getpid()):
                (cfg / "server.pid").unlink()
        except OSError:
            pass
    return 0


def cmd_helper(args: argparse.Namespace) -> int:
    """Internal JSON helpers executed with the runtime interpreter from ``<cfg>/cli.py``."""
    cfg = cfg_dir()
    out: dict[str, Any] = {"ok": False}
    try:
        ctx = load_ctx(cfg)
        if args.helper_cmd == "migrate":
            cfg_data = ctx.config.data
            if not (cfg / "config.json").exists():
                ctx.config.save({}, actor="upgrade")
            if not ctx.policy.path.exists():
                ctx.policy.save({})
            out = {"ok": True, "legacy_machine_token_mcp": ctx.legacy_mcp_token_enabled(),
                   "profile": ctx.policy.profile, "migrated": bool(cfg_data.get("_migrated_from")),
                   "detail": f"schema {cfg_data.get('schema_version')}, profile {ctx.policy.profile}"}
        elif args.helper_cmd == "verify-mcp":
            res = verify_mcp(ctx.auth, int(args.port or ctx.config.port), exec_command=args.exec)
            res["public_hosts"] = ctx.config.public_hosts()
            res["home"] = ctx.identity.home
            res["sandbox"] = ctx.sandbox.status()
            out = res
        elif args.helper_cmd == "enroll":
            code = ctx.auth.create_enrollment(int(args.ttl))
            out = {"ok": True, "url": f"http://127.0.0.1:{ctx.config.port}/admin/enroll#code={code}"}
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        out = {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:500]}
    say(json.dumps(out, default=str))
    return 0 if out.get("ok") else 1


# =============================================================== argument parsing
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loopback", description="Loopback MCP control plane")
    p.add_argument("--version", action="store_true", help="print the CLI version")
    sub = p.add_subparsers(dest="cmd")

    def add(name: str, fn, help_text: str, **kw) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = add("install", cmd_install, "install or update this machine's Loopback")
    sp.add_argument("--non-interactive", action="store_true")
    sp.add_argument("--ingress", choices=INGRESS_CHOICES)
    sp.add_argument("--host", help="public FQDN (Cloudflare)")
    sp.add_argument("--port", type=int)
    sp.add_argument("--tailscale-mode", choices=("serve", "funnel"), default="serve")
    sp.add_argument("--cloudflare-token-file")
    sp.add_argument("--cloudflare-tunnel-id")
    sp.add_argument("--no-service", action="store_true", help="do not install a service unit (use 'loopback up')")
    sp.add_argument("--no-autostart", action="store_true", help="same as --no-service")
    sp.add_argument("--source", help="checkout / extracted release to install from")
    sp.add_argument("--yes", "-y", action="store_true")
    sp.add_argument("--allow-root", action="store_true")
    sp.add_argument("--skip-venv", action="store_true", help=argparse.SUPPRESS)  # test-only
    sp.add_argument("--python-override", help=argparse.SUPPRESS)  # test-only
    sp.add_argument("--stage2", action="store_true", help=argparse.SUPPRESS)

    sp = add("upgrade", cmd_upgrade, "upgrade an existing install (backup, swap, verify, auto-rollback)")
    sp.add_argument("--source")
    sp.add_argument("--check", action="store_true", help="only print the plan")
    sp.add_argument("--yes", "-y", action="store_true")
    sp.add_argument("--no-restart", action="store_true")
    sp.add_argument("--allow-root", action="store_true")
    sp.add_argument("--skip-venv", action="store_true", help=argparse.SUPPRESS)

    sp = add("rollback", cmd_rollback, "restore the previous runtime")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--to")
    sp.add_argument("--yes", "-y", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("backup", cmd_backup, "write a backup archive of all state")
    sp.add_argument("--output")
    sp.add_argument("--json", action="store_true")
    sp = add("restore", cmd_restore, "restore a backup archive")
    sp.add_argument("archive")
    sp.add_argument("--yes", "-y", action="store_true")

    sp = add("doctor", cmd_doctor, "diagnose the installation")
    sp.add_argument("--deep", action="store_true")
    sp.add_argument("--json", action="store_true")

    add("up", cmd_up, "start the gateway")
    add("start", cmd_up, "alias of up")
    add("down", cmd_down, "stop the gateway")
    add("stop", cmd_down, "alias of down")
    add("restart", cmd_restart, "restart the gateway")
    sp = add("status", cmd_status, "show status")
    sp.add_argument("--json", action="store_true")
    sp = add("logs", cmd_logs, "show logs (redacted)")
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("-n", type=int, default=100)
    sp.add_argument("--file", choices=("server", "cloudflared", "tailscale"), default="server")
    sp = add("url", cmd_url, "print the MCP URL")
    sp.add_argument("--json", action="store_true")
    sp = add("dashboard", cmd_dashboard, "print (or open) the dashboard URL")
    sp.add_argument("--open", action="store_true")
    add("token", cmd_token, "print the machine recovery token")
    sp = add("version", cmd_version, "print versions")
    sp.add_argument("--json", action="store_true")
    add("report", cmd_report, "print the sanitized install report")

    sp = add("auth", cmd_auth, "authentication management")
    asub = sp.add_subparsers(dest="auth_cmd", required=True)
    a = asub.add_parser("enroll")
    a.add_argument("--ttl", type=int, default=600)
    a = asub.add_parser("token")
    tsub = a.add_subparsers(dest="token_cmd", required=True)
    t = tsub.add_parser("create")
    t.add_argument("name")
    t.add_argument("--scopes", default="loopback:read")
    t.add_argument("--ttl", default="90d")
    t.add_argument("--note")
    t.add_argument("--json", action="store_true")
    t = tsub.add_parser("list")
    t.add_argument("--json", action="store_true")
    t = tsub.add_parser("revoke")
    t.add_argument("id")
    a = asub.add_parser("clients")
    a.add_argument("--json", action="store_true")
    a = asub.add_parser("revoke-client")
    a.add_argument("id")
    asub.add_parser("rotate-machine-token")
    asub.add_parser("rotate-signing-key")
    asub.add_parser("password")
    a = asub.add_parser("legacy")
    a.add_argument("state", choices=("on", "off"))

    sp = add("policy", cmd_policy, "show or change the policy profile")
    psub = sp.add_subparsers(dest="policy_cmd")
    psub.add_parser("show")
    pp = psub.add_parser("profile")
    pp.add_argument("name")

    sp = add("node", cmd_node, "manage Loopback fleet nodes")
    nsub = sp.add_subparsers(dest="node_cmd")
    nsub.add_parser("list")
    n = nsub.add_parser("add")
    n.add_argument("name")
    n.add_argument("url")
    n.add_argument("--token-env")
    n = nsub.add_parser("remove")
    n.add_argument("name")

    sp = add("ssh", cmd_ssh, "manage SSH targets")
    ssub = sp.add_subparsers(dest="ssh_cmd")
    ssub.add_parser("list")
    s = ssub.add_parser("add")
    s.add_argument("name")
    s.add_argument("target", help="user@host[:port]")
    s.add_argument("--identity", dest="identity_file")
    s.add_argument("--kind")
    s.add_argument("--group")
    s = ssub.add_parser("remove")
    s.add_argument("name")
    s = ssub.add_parser("probe")
    s.add_argument("name")

    sp = add("autostart", cmd_autostart, "enable/disable start at login/boot")
    sp.add_argument("state", choices=("on", "off", "status"), nargs="?", default="status")
    sp.add_argument("--json", action="store_true")
    sp = add("uninstall", cmd_uninstall, "remove Loopback")
    sp.add_argument("--keep-config", action="store_true")
    sp.add_argument("--yes", "-y", action="store_true")
    sp = add("update-check", cmd_update_check, "check for a newer release")
    sp.add_argument("--json", action="store_true")
    sp = add("repair", cmd_repair, "fix permissions, venv, service unit; re-copy runtime with --source")
    sp.add_argument("--source")
    sp.add_argument("--allow-root", action="store_true")

    sp = add("serve", cmd_serve, argparse.SUPPRESS)
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-log", dest="log", action="store_false")
    sp = add("_helper", cmd_helper, argparse.SUPPRESS)
    hsub = sp.add_subparsers(dest="helper_cmd", required=True)
    hsub.add_parser("migrate")
    h = hsub.add_parser("verify-mcp")
    h.add_argument("--port", type=int)
    h.add_argument("--exec")
    h = hsub.add_parser("enroll")
    h.add_argument("--ttl", type=int, default=600)
    return p


def main(argv: list[str] | None = None) -> int:
    if sys.version_info[:2] < MIN_PY:
        say(f"Loopback needs Python {MIN_PY[0]}.{MIN_PY[1]}+ (found {platform.python_version()}).", err=True)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version and not args.cmd:
        say(cli_version())
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        say("\ninterrupted", err=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
