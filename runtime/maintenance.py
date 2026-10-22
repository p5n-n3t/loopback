"""Reliability, recovery and self-diagnosis for a running Loopback instance.

This module is deliberately self-contained: it only depends on the other flat
top-level runtime modules through the shared ``AppContext`` (``context.py``)
and never imports ``server``. It has no import-time side effects, so
``bootstrap.build_context`` can import it unconditionally (see the optional
subsystem loader in ``bootstrap.py``).

Provided building blocks:

* :func:`doctor` -- a battery of read-mostly health checks, each
  ``{name, ok, level, detail, fix}``. ``deep=True`` adds slower checks
  (sandbox self-test, an end-to-end MCP round trip, SSH probes, TLS/DNS,
  time skew) that are skipped by default.
* :class:`LogRotator` -- size-based copy/truncate log rotation plus job log
  pruning.
* :class:`BackupManager` -- creates/lists/restores/prunes a tar.gz backup of
  the state that defines a Loopback install (config, policy, credentials,
  databases, skills).
* :class:`DiagnosticBundle` -- a redacted zip of system/runtime state for
  bug reports, built through :class:`Redactor`.
* :class:`UpdateChecker` -- polls the GitHub releases API for a newer
  version; never downloads or applies anything.
* :class:`MaintenanceManager` -- wires the above together, exposes
  ``alerts()``/``status()``/``metrics()`` and drives a single background
  loop (``run_forever``) that keeps logs, backups, probes and the update
  cache tidy without ever crashing the process.

Every state file this module writes under ``ctx.cfg_dir`` is created mode
0600 and written atomically (temp file + ``os.replace``). No function here
ever returns a secret (machine token, API token values, node bearer tokens,
tunnel credentials, SSH keys, signing key) -- see :class:`Redactor`.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import ssl
import sys
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is a declared dependency
    httpx = None  # type: ignore[assignment]

WINDOWS = sys.platform.startswith("win")

# --------------------------------------------------------------------------- small helpers


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write *data* to *path* atomically with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=2, sort_keys=True).encode("utf-8"), mode)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _check(name: str, ok: bool, level: str, detail: str, fix: str | None = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "level": level, "detail": detail, "fix": fix}


def _ok(name: str, detail: str) -> dict[str, Any]:
    return _check(name, True, "ok", detail, None)


def _warn(name: str, detail: str, fix: str | None = None) -> dict[str, Any]:
    return _check(name, False, "warning", detail, fix)


def _error(name: str, detail: str, fix: str | None = None) -> dict[str, Any]:
    return _check(name, False, "error", detail, fix)


def _version_tuple(value: str) -> tuple[int, ...]:
    value = (value or "").strip()
    if value.lower().startswith("v"):
        value = value[1:]
    value = value.split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for piece in value.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _dir_mode_ok(path: Path, expected: int) -> bool:
    if WINDOWS:
        return True
    try:
        return (path.stat().st_mode & 0o777) == expected
    except OSError:
        return False


def _disk_usage_percent(path: Path) -> float | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return round((usage.used / usage.total) * 100, 1)


def _sqlite_integrity_check(path: Path) -> str | None:
    """Return None if OK, else a short error string. Never raises."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0]) if row else "unknown"
            return None if result == "ok" else result[:300]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return str(exc)[:300]


# --------------------------------------------------------------------------- doctor


def doctor(ctx: Any, deep: bool = False) -> list[dict[str, Any]]:
    """Run health checks and return ``[{name, ok, level, detail, fix}, ...]``.

    Non-deep checks are all local, fast and side-effect free (besides a
    couple of localhost HTTP probes). ``deep=True`` additionally exercises
    the sandbox, performs a full MCP round trip with a throwaway token, and
    probes SSH targets / certificates / DNS / time sync -- anything that can
    take real wall-clock time or touch the network.
    """
    checks: list[dict[str, Any]] = []
    cfg_dir = Path(ctx.cfg_dir)

    # -- python version -----------------------------------------------------
    py_ok = sys.version_info >= (3, 11)
    checks.append(_check("python_version", py_ok, "ok" if py_ok else "warning",
                          f"Running Python {platform.python_version()}",
                          None if py_ok else "Loopback targets Python 3.11+; upgrade the interpreter"))

    # -- config.json valid ----------------------------------------------------
    try:
        import config as config_module

        errors = config_module.validate(ctx.config.data)
        if errors:
            checks.append(_error("config_valid", "; ".join(errors)[:400], "Fix config.json or re-run the installer"))
        else:
            checks.append(_ok("config_valid", "config.json passes validation"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_error("config_valid", f"could not validate config: {exc}", None))

    # -- policy valid ---------------------------------------------------------
    try:
        import policy as policy_module

        errors = policy_module.validate_policy(ctx.policy.data)
        if errors:
            checks.append(_error("policy_valid", "; ".join(errors)[:400], "Fix policy.json in the dashboard"))
        else:
            checks.append(_ok("policy_valid", f"policy.json valid (profile: {ctx.policy.profile})"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_error("policy_valid", f"could not validate policy: {exc}", None))

    # -- config dir / secret file permissions ---------------------------------
    if WINDOWS:
        checks.append(_ok("config_dir_permissions", "permission checks skipped on Windows"))
    else:
        dir_ok = _dir_mode_ok(cfg_dir, 0o700)
        checks.append(_check("config_dir_permissions", dir_ok, "ok" if dir_ok else "warning",
                              f"{cfg_dir} mode {'0700' if dir_ok else oct(cfg_dir.stat().st_mode & 0o777) if cfg_dir.exists() else 'missing'}",
                              None if dir_ok else f"chmod 700 {cfg_dir}"))
        for secret_name in ("token", "oauth-signing.key", "auth.db", "nodes.secrets.json", "ssh_targets.json"):
            secret_path = cfg_dir / secret_name
            if not secret_path.exists():
                continue
            try:
                mode_ok = (secret_path.stat().st_mode & 0o777) == 0o600
            except OSError:
                mode_ok = False
            checks.append(_check(f"secret_perms:{secret_name}", mode_ok, "ok" if mode_ok else "warning",
                                  f"{secret_name} is {'0600' if mode_ok else 'not 0600'}",
                                  None if mode_ok else f"chmod 600 {secret_path}"))

    # -- machine token present --------------------------------------------
    has_token = bool(getattr(ctx.auth, "machine_token", None))
    checks.append(_check("machine_token", has_token, "ok" if has_token else "error",
                          "machine token present" if has_token else "no machine token",
                          None if has_token else "loopback rotate-token"))

    # -- dashboard password --------------------------------------------------
    try:
        has_password = bool(ctx.auth.has_password())
    except Exception:  # noqa: BLE001
        has_password = True
    checks.append(_check("dashboard_password", has_password, "ok" if has_password else "warning",
                          "dashboard password set" if has_password else "no dashboard password set",
                          None if has_password else "loopback auth enroll"))

    # -- legacy machine-token MCP auth ---------------------------------------
    try:
        legacy_enabled = bool(ctx.legacy_mcp_token_enabled())
    except Exception:  # noqa: BLE001
        legacy_enabled = False
    checks.append(_check("legacy_machine_token_mcp", not legacy_enabled, "ok" if not legacy_enabled else "warning",
                          "legacy machine-token MCP auth is " + ("enabled" if legacy_enabled else "disabled"),
                          None if not legacy_enabled else "Migrate MCP clients to scoped API tokens, then disable legacy_machine_token_mcp"))

    # -- sandbox status -------------------------------------------------------
    try:
        sandbox_status = ctx.sandbox.status()
        enforced = bool(sandbox_status.get("active"))
        target_policy = ctx.policy.target_policy("local")
        required = bool(target_policy.get("sandbox_required"))
        acknowledged = bool(sandbox_status.get("acknowledge_unenforced"))
        if enforced:
            checks.append(_ok("sandbox", f"sandbox enforced via {sandbox_status.get('backend')}"))
        elif required and not acknowledged:
            checks.append(_error("sandbox", "policy requires a sandbox but none is active and it is not acknowledged",
                                  "Install a sandbox backend (landlock/bwrap/seatbelt) or acknowledge the risk in settings"))
        else:
            checks.append(_warn("sandbox", f"sandbox inactive (backend {sandbox_status.get('backend')})",
                                 "Consider enabling OS-level sandboxing for shell/file access"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_warn("sandbox", f"could not read sandbox status: {exc}"))

    # -- service manager / unit -----------------------------------------------
    try:
        from service import ServiceManager

        sm = ServiceManager(ctx.identity, cfg_dir, ctx.config.port)
        svc_status = sm.status()
        if svc_status.get("installed"):
            active = bool(svc_status.get("active"))
            checks.append(_check("service", active, "ok" if active else "warning",
                                  f"{svc_status.get('manager')} unit installed, active={active}",
                                  None if active else "loopback start"))
        else:
            checks.append(_ok("service", f"no managed unit installed ({svc_status.get('manager')}); running standalone"))
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        checks.append(_warn("service", f"could not read service status: {exc}"))

    # -- local health ----------------------------------------------------------
    local_health_ok = False
    if httpx is not None:
        try:
            resp = httpx.get(f"http://127.0.0.1:{ctx.config.port}/healthz", timeout=5)
            local_health_ok = resp.status_code == 200
            checks.append(_check("local_health", local_health_ok, "ok" if local_health_ok else "error",
                                  f"GET /healthz -> {resp.status_code}",
                                  None if local_health_ok else "Check server.log; restart the service"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_error("local_health", f"GET /healthz failed: {exc}", "Check server.log; restart the service"))
    else:
        checks.append(_warn("local_health", "httpx unavailable; skipped"))

    # -- dashboard login reachable ---------------------------------------------
    if httpx is not None:
        try:
            resp = httpx.get(f"http://127.0.0.1:{ctx.config.port}/admin/login", timeout=5)
            login_ok = resp.status_code == 200
            checks.append(_check("dashboard_login", login_ok, "ok" if login_ok else "warning",
                                  f"GET /admin/login -> {resp.status_code}", None))
        except Exception as exc:  # noqa: BLE001
            checks.append(_warn("dashboard_login", f"GET /admin/login failed: {exc}"))

    # -- ingress diagnostics -----------------------------------------------
    if ctx.ingress is None:
        checks.append(_warn("ingress", "ingress module missing"))
    else:
        ingress_cfg = (ctx.config.data or {}).get("ingress", {}) or {}
        for name in ("cloudflare", "tailscale"):
            if not bool((ingress_cfg.get(name) or {}).get("enabled")):
                continue
            try:
                for item in ctx.ingress.diagnostics(name):
                    checks.append(_check(f"ingress:{name}:{item.get('name')}", bool(item.get("ok")),
                                          "ok" if item.get("ok") else "warning",
                                          str(item.get("detail") or ""), item.get("fix")))
            except KeyError:
                checks.append(_warn(f"ingress:{name}", "unknown ingress diagnostics target"))
            except Exception as exc:  # noqa: BLE001
                checks.append(_warn(f"ingress:{name}", f"diagnostics failed: {exc}"))

    # -- public health ----------------------------------------------------------
    if httpx is not None:
        for host in ctx.config.public_hosts():
            try:
                resp = httpx.get(f"https://{host}/healthz", timeout=8)
                ok = resp.status_code == 200
                checks.append(_check(f"public_health:{host}", ok, "ok" if ok else "warning",
                                      f"GET https://{host}/healthz -> {resp.status_code}", None))
            except Exception as exc:  # noqa: BLE001
                checks.append(_warn(f"public_health:{host}", f"unreachable: {exc}"))

    # -- disk free ----------------------------------------------------------
    warn_pct = int((ctx.config.data.get("maintenance", {}) or {}).get("disk_warn_percent", 90) or 90)
    for label, path in (("home", Path(ctx.identity.home)), ("config_dir", cfg_dir)):
        pct = _disk_usage_percent(path)
        if pct is None:
            continue
        ok = pct < warn_pct
        checks.append(_check(f"disk_free:{label}", ok, "ok" if ok else "warning",
                              f"{path} is {pct}% full", None if ok else "Free up disk space"))

    # -- sqlite integrity -----------------------------------------------------
    for db_name in ("loopback.db", "auth.db"):
        db_path = cfg_dir / db_name
        if not db_path.exists():
            continue
        err = _sqlite_integrity_check(db_path)
        checks.append(_check(f"sqlite_integrity:{db_name}", err is None, "ok" if err is None else "error",
                              "integrity_check passed" if err is None else f"integrity_check failed: {err}",
                              None if err is None else "Restore from the most recent backup"))

    # -- log sizes ------------------------------------------------------------
    mt = ctx.config.data.get("maintenance", {}) or {}
    log_max = int(mt.get("log_max_bytes", 5 * 1024 * 1024) or 0)
    if log_max > 0:
        for log_name in ("server.log", "cloudflared.log", "tailscale.log"):
            log_path = cfg_dir / log_name
            if not log_path.exists():
                continue
            try:
                size = log_path.stat().st_size
            except OSError:
                continue
            ok = size <= log_max * 2
            checks.append(_check(f"log_size:{log_name}", ok, "ok" if ok else "warning",
                                  f"{log_name} is {size} bytes (limit {log_max})",
                                  None if ok else "Rotation is overdue; it runs automatically every 10 minutes"))

    # -- skills directory -----------------------------------------------------
    skill_file = cfg_dir / "skills" / "loopback-core" / "SKILL.md"
    skills_ok = skill_file.is_file()
    checks.append(_check("skills_directory", skills_ok, "ok" if skills_ok else "warning",
                          str(skill_file) + (" present" if skills_ok else " missing"),
                          None if skills_ok else "Re-run the installer to restore the built-in skill"))

    # -- version file matches runtime ------------------------------------------
    try:
        import version as version_module

        version_file = cfg_dir / "VERSION"
        if version_file.exists():
            on_disk = version_file.read_text(encoding="utf-8").strip()
            matches = on_disk == version_module.VERSION
            checks.append(_check("version_file", matches, "ok" if matches else "warning",
                                  f"VERSION file says {on_disk}, runtime is {version_module.VERSION}",
                                  None if matches else "Re-run the installer/updater"))
        else:
            checks.append(_warn("version_file", "no VERSION file in config dir"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_warn("version_file", f"could not compare versions: {exc}"))

    # -- python packages importable --------------------------------------------
    for pkg in ("mcp", "starlette", "uvicorn", "jwt", "httpx"):
        try:
            __import__(pkg)
            checks.append(_ok(f"package:{pkg}", f"{pkg} importable"))
        except ImportError as exc:
            checks.append(_error(f"package:{pkg}", f"{pkg} not importable: {exc}", "Reinstall the venv (loopback install)"))

    if deep:
        checks.extend(_doctor_deep(ctx))

    return checks


def _doctor_deep(ctx: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # -- sandbox self-test -------------------------------------------------
    try:
        plan = ctx.sandbox.plan_from_policy(
            ctx.policy.root_entries("local"),
            ctx.policy.target_policy("local")["denied_roots"],
            ctx.identity.home,
        )
        result = ctx.sandbox.self_test(plan, ctx.config.effective_shell())
        checks.append(_check("sandbox_self_test", bool(result.get("ok")), "ok" if result.get("ok") else "warning",
                              json.dumps(result.get("checks", []))[:400] or result.get("reason", "n/a"),
                              None if result.get("ok") else "Investigate sandbox backend configuration"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_warn("sandbox_self_test", f"self-test failed: {exc}"))

    # -- MCP end-to-end test --------------------------------------------------
    checks.append(_mcp_e2e_check(ctx))

    # -- SSH targets probe ------------------------------------------------
    if ctx.ssh is not None:
        try:
            targets = ctx.ssh.list()
        except Exception as exc:  # noqa: BLE001
            targets = []
            checks.append(_warn("ssh_targets", f"could not list targets: {exc}"))
        for target in targets:
            name = target.get("name")
            if not name:
                continue
            try:
                probe = ctx.ssh.probe(name)
                ok = str(probe.get("status")) == "ok"
                checks.append(_check(f"ssh_probe:{name}", ok, "ok" if ok else "warning",
                                      f"status={probe.get('status')} latency_ms={probe.get('latency_ms')}",
                                      None if ok else str(probe.get("error") or "")[:200] or None))
            except KeyError:
                continue
            except Exception as exc:  # noqa: BLE001
                checks.append(_warn(f"ssh_probe:{name}", f"probe failed: {exc}"))

    # -- certificate expiry + DNS for public hosts --------------------------
    for host in ctx.config.public_hosts():
        checks.append(_cert_expiry_check(host))
        checks.append(_dns_check(host))

    # -- time skew --------------------------------------------------------
    checks.append(_time_skew_check())

    return checks


def _mcp_e2e_check(ctx: Any) -> dict[str, Any]:
    if httpx is None:
        return _warn("mcp_e2e", "httpx unavailable; skipped")
    token_id = None
    try:
        value, record = ctx.auth.create_api_token(
            "doctor-self-test", ["loopback:read"], ttl_seconds=120, created_by="maintenance.doctor",
            note="transient token created by deep doctor; auto-revoked",
        )
        token_id = record.get("id") if isinstance(record, dict) else None
        headers = {
            "Authorization": f"Bearer {value}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
            "Content-Type": "application/json",
        }
        base = f"http://127.0.0.1:{ctx.config.port}/mcp"
        with httpx.Client(timeout=10) as client:
            init_resp = client.post(base, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "doctor", "version": "1"}},
            })
            if init_resp.status_code >= 400:
                return _error("mcp_e2e", f"initialize -> HTTP {init_resp.status_code}", "Check server.log")
            list_resp = client.post(base, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            if list_resp.status_code >= 400:
                return _error("mcp_e2e", f"tools/list -> HTTP {list_resp.status_code}", "Check server.log")
            tools = (list_resp.json().get("result") or {}).get("tools") or []
            if len(tools) < 10:
                return _error("mcp_e2e", f"tools/list returned only {len(tools)} tools", "Check server.log")
            call_resp = client.post(base, headers=headers, json={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "machine_info", "arguments": {}},
            })
            if call_resp.status_code >= 400:
                return _error("mcp_e2e", f"machine_info call -> HTTP {call_resp.status_code}", "Check server.log")
            payload = call_resp.json().get("result") or {}
            content = payload.get("content") or payload.get("structuredContent") or {}
            home_value = None
            if isinstance(content, dict):
                home_value = content.get("home")
            if home_value is None and isinstance(payload.get("structuredContent"), dict):
                home_value = payload["structuredContent"].get("home")
            if home_value is None:
                for item in content if isinstance(content, list) else []:
                    if isinstance(item, dict) and item.get("type") == "text":
                        try:
                            parsed = json.loads(item.get("text", "{}"))
                            home_value = parsed.get("home")
                        except ValueError:
                            pass
            if home_value is not None and home_value != ctx.identity.home:
                return _error("mcp_e2e", f"machine_info home mismatch: {home_value!r}", None)
            return _ok("mcp_e2e", f"initialize + tools/list ({len(tools)} tools) + machine_info round-tripped")
    except Exception as exc:  # noqa: BLE001
        return _error("mcp_e2e", f"MCP round trip failed: {exc}", "Check server.log")
    finally:
        if token_id:
            try:
                ctx.auth.revoke_api_token(token_id)
            except Exception:  # noqa: BLE001
                pass


def _cert_expiry_check(host: str) -> dict[str, Any]:
    try:
        ctx_ssl = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=6) as sock:
            with ctx_ssl.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        not_after = cert.get("notAfter") if cert else None
        if not not_after:
            return _warn(f"tls_cert:{host}", "could not read certificate expiry")
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        days_left = (expires - datetime.now(UTC)).days
        ok = days_left > 14
        return _check(f"tls_cert:{host}", ok, "ok" if ok else "warning",
                      f"certificate expires in {days_left} days", None if ok else "Renew the certificate")
    except Exception as exc:  # noqa: BLE001
        return _warn(f"tls_cert:{host}", f"could not check certificate: {exc}")


def _dns_check(host: str) -> dict[str, Any]:
    try:
        socket.getaddrinfo(host, 443)
        return _ok(f"dns:{host}", f"{host} resolves")
    except socket.gaierror as exc:
        return _warn(f"dns:{host}", f"{host} did not resolve: {exc}", "Check the DNS record for this host")


def _time_skew_check() -> dict[str, Any]:
    if httpx is None:
        return _warn("time_skew", "httpx unavailable; skipped")
    try:
        resp = httpx.head("https://github.com", timeout=6)
    except Exception:  # noqa: BLE001
        return _warn("time_skew", "offline or unreachable; skipped")
    date_header = resp.headers.get("date")
    if not date_header:
        return _warn("time_skew", "no Date header returned; skipped")
    try:
        from email.utils import parsedate_to_datetime

        remote = parsedate_to_datetime(date_header)
        if remote.tzinfo is None:
            remote = remote.replace(tzinfo=UTC)
        skew = abs((datetime.now(UTC) - remote).total_seconds())
        ok = skew <= 120
        return _check("time_skew", ok, "ok" if ok else "warning",
                      f"system clock differs from remote by {skew:.0f}s", None if ok else "Sync the system clock (NTP)")
    except Exception as exc:  # noqa: BLE001
        return _warn("time_skew", f"could not parse remote time: {exc}")


# --------------------------------------------------------------------------- log rotation


class LogRotator:
    """Size-based copy/truncate log rotation, safe while the writer keeps the fd open."""

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def rotate(self, path: Path, max_bytes: int, backups: int) -> bool:
        """Rotate *path* if it exceeds *max_bytes*. Returns True if rotated."""
        path = Path(path)
        if max_bytes <= 0 or backups < 0 or not path.exists():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size <= max_bytes:
            return False
        try:
            for index in range(backups - 1, 0, -1):
                src = path.with_name(f"{path.name}.{index}")
                dst = path.with_name(f"{path.name}.{index + 1}")
                if src.exists():
                    try:
                        os.replace(src, dst)
                    except OSError:
                        pass
            if backups > 0:
                try:
                    shutil.copy2(path, path.with_name(f"{path.name}.1"))
                except OSError:
                    pass
            # Truncate in place (copy-truncate) so a process with the file
            # already open keeps writing to the same inode.
            with open(path, "r+b") as handle:
                handle.truncate(0)
            return True
        except OSError:
            return False

    def rotate_all(self) -> dict[str, bool]:
        mt = self.ctx.config.data.get("maintenance", {}) or {}
        max_bytes = int(mt.get("log_max_bytes", 5 * 1024 * 1024) or 0)
        backups = int(mt.get("log_backups", 5) or 0)
        cfg_dir = Path(self.ctx.cfg_dir)
        results: dict[str, bool] = {}
        for name in ("server.log", "cloudflared.log", "tailscale.log"):
            results[name] = self.rotate(cfg_dir / name, max_bytes, backups)
        results["job_logs_pruned"] = self._prune_job_logs()
        return results

    def _prune_job_logs(self) -> bool:
        cfg_dir = Path(self.ctx.cfg_dir)
        jobs_dir = cfg_dir / "jobs"
        if not jobs_dir.is_dir():
            return False
        mt = self.ctx.config.data.get("maintenance", {}) or {}
        max_completed = int(mt.get("max_completed_jobs", 200) or 0)
        max_bytes = int(mt.get("job_log_max_bytes", 64 * 1024 * 1024) or 0)
        try:
            running_ids = {str(job.get("id")) for job in self.ctx.jobs.list() if job.get("running")}
        except Exception:  # noqa: BLE001
            running_ids = set()

        entries: list[tuple[float, str, Path, Path | None]] = []
        for meta_path in jobs_dir.glob("*.json"):
            job_id = meta_path.stem
            if job_id in running_ids:
                continue
            log_path = jobs_dir / f"{job_id}.log"
            try:
                mtime = meta_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append((mtime, job_id, meta_path, log_path if log_path.exists() else None))
        entries.sort(key=lambda item: item[0], reverse=True)

        changed = False
        if max_completed > 0 and len(entries) > max_completed:
            for _, _, meta_path, log_path in entries[max_completed:]:
                changed = True
                for p in (meta_path, log_path):
                    if p is not None:
                        try:
                            p.unlink()
                        except OSError:
                            pass
            entries = entries[:max_completed]

        if max_bytes > 0:
            total = 0
            for _, _, _, log_path in entries:
                if log_path is not None:
                    try:
                        total += log_path.stat().st_size
                    except OSError:
                        pass
            if total > max_bytes:
                for _, _, meta_path, log_path in reversed(entries):
                    if total <= max_bytes:
                        break
                    if log_path is None:
                        continue
                    try:
                        size = log_path.stat().st_size
                        log_path.unlink()
                        meta_path.unlink()
                        total -= size
                        changed = True
                    except OSError:
                        pass
        return changed


# --------------------------------------------------------------------------- backups

_BACKUP_MEMBERS = (
    "config.json", "config.env", "policy.json", "approvals.json", "token", "oauth-signing.key",
    "auth.db", "loopback.db", "nodes.json", "nodes.secrets.json", "ssh_targets.json", "node_meta.json",
    "topology.json", "topology_state.json", "cloudflared.yml", "cloudflared.token", "VERSION",
)
_BACKUP_EXCLUDE_DIRS = {"venv", "jobs", "backups", "diagnostics"}
_BACKUP_EXCLUDE_SUFFIXES = (".log", ".pid")


def _should_skip_skill_path(rel_parts: tuple[str, ...]) -> bool:
    name = rel_parts[-1] if rel_parts else ""
    if name.endswith(_BACKUP_EXCLUDE_SUFFIXES):
        return True
    if any(part.startswith(".staging-") for part in rel_parts):
        return True
    return False


class BackupManager:
    """Creates, lists, restores and prunes tar.gz backups of install state."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.backup_dir = Path(ctx.cfg_dir) / "backups"

    def create(self, name: str | None = None, reason: str = "manual") -> dict[str, Any]:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if name is None:
            import uuid

            name = f"backup-{stamp}-{uuid.uuid4().hex[:6]}"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
        archive_path = self.backup_dir / f"{safe_name}.tar.gz"
        cfg_dir = Path(self.ctx.cfg_dir)
        included: list[str] = []

        with tempfile.TemporaryDirectory(prefix="loopback-backup-") as tmpdir:
            tmp_db_dir = Path(tmpdir)
            fd, tmp_path = tempfile.mkstemp(dir=str(self.backup_dir))
            os.close(fd)
            try:
                os.chmod(tmp_path, 0o600)
                with tarfile.open(tmp_path, "w:gz") as tar:
                    for member in _BACKUP_MEMBERS:
                        src = cfg_dir / member
                        if not src.exists():
                            continue
                        if member in ("auth.db", "loopback.db"):
                            snapshot = tmp_db_dir / member
                            if self._sqlite_snapshot(src, snapshot):
                                tar.add(snapshot, arcname=member)
                                included.append(member)
                            continue
                        tar.add(src, arcname=member)
                        included.append(member)
                    skills_dir = cfg_dir / "skills"
                    if skills_dir.is_dir():
                        for file_path in skills_dir.rglob("*"):
                            if not file_path.is_file():
                                continue
                            rel = file_path.relative_to(cfg_dir)
                            if _should_skip_skill_path(rel.parts):
                                continue
                            tar.add(file_path, arcname=str(rel))
                            included.append(str(rel))
                os.replace(tmp_path, archive_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        os.chmod(archive_path, 0o600)
        created_at = _utc_now_iso()
        meta = {"name": safe_name, "reason": reason, "created_at": created_at, "files": included}
        _atomic_write_json(self.backup_dir / f"{safe_name}.meta.json", meta)
        try:
            self.ctx.audit.record("admin.backup_create", "ok", detail={"name": safe_name, "reason": reason})
        except Exception:  # noqa: BLE001
            pass
        size = archive_path.stat().st_size
        return {"path": str(archive_path), "size": size, "created_at": created_at, "files": included}

    @staticmethod
    def _sqlite_snapshot(src: Path, dest: Path) -> bool:
        """Consistent copy of a live sqlite db via the backup API."""
        try:
            source_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error:
            try:
                shutil.copy2(src, dest)
                return True
            except OSError:
                return False
        try:
            dest_conn = sqlite3.connect(dest)
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
            return True
        except sqlite3.Error:
            return False
        finally:
            source_conn.close()

    def list(self) -> list[dict[str, Any]]:
        if not self.backup_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for archive in sorted(self.backup_dir.glob("*.tar.gz")):
            name = archive.name[: -len(".tar.gz")]
            meta = _read_json(self.backup_dir / f"{name}.meta.json", {})
            try:
                size = archive.stat().st_size
                mtime = archive.stat().st_mtime
            except OSError:
                continue
            out.append({
                "name": name,
                "size": size,
                "created_at": meta.get("created_at") or datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "reason": meta.get("reason", "manual"),
            })
        out.sort(key=lambda item: item["created_at"], reverse=True)
        return out

    def restore(self, name: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ValueError("restore requires confirm=true")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
        archive_path = self.backup_dir / f"{safe_name}.tar.gz"
        if not archive_path.is_file():
            raise KeyError(f"no backup named {name!r}")
        cfg_dir = Path(self.ctx.cfg_dir)
        safety = self.create(name=f"pre-restore-{int(time.time())}", reason="pre-restore-safety")
        restored: list[str] = []
        extract_filter = getattr(tarfile, "data_filter", None)
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    continue
                if member.name not in _BACKUP_MEMBERS and not member.name.startswith("skills/"):
                    continue
                if not (member.isreg() or member.isdir()):
                    continue
                if extract_filter is not None:
                    tar.extract(member, path=cfg_dir, filter=extract_filter)
                else:
                    tar.extract(member, path=cfg_dir)
                restored.append(member.name)
                target = cfg_dir / member.name
                if target.is_file() and member.name in _BACKUP_MEMBERS:
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
        try:
            self.ctx.audit.record("admin.backup_restore", "ok", detail={"name": safe_name, "files": len(restored)})
        except Exception:  # noqa: BLE001
            pass
        return {"restored": restored, "safety_backup": safety["path"], "name": safe_name}

    def prune(self, keep: int | None = None) -> list[str]:
        mt = self.ctx.config.data.get("maintenance", {}) or {}
        keep = int(keep if keep is not None else mt.get("backup_retention", 10) or 10)
        entries = self.list()
        removed: list[str] = []
        protected: list[dict[str, Any]] = []
        regular: list[dict[str, Any]] = []
        for entry in entries:
            is_pre_upgrade = entry["name"].startswith("pre-upgrade-")
            age_days = None
            try:
                created = datetime.strptime(entry["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                age_days = (datetime.now(UTC) - created).total_seconds() / 86400
            except (ValueError, TypeError):
                pass
            if is_pre_upgrade and (age_days is None or age_days < 30):
                protected.append(entry)
            else:
                regular.append(entry)
        if keep > 0 and len(regular) > keep:
            for entry in regular[keep:]:
                deleted_any = False
                for suffix in (".tar.gz", ".meta.json"):
                    path = self.backup_dir / f"{entry['name']}{suffix}"
                    try:
                        path.unlink()
                        deleted_any = True
                    except OSError:
                        pass
                if deleted_any:
                    removed.append(entry["name"])
        return removed


# --------------------------------------------------------------------------- redaction


class Redactor:
    """Strips secret-shaped values from text before it leaves the machine."""

    _PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"lb[kr]_[A-Za-z0-9_\-]{8,}"),
        re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        re.compile(r"\b[0-9a-fA-F]{64}\b"),
        re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.=]{8,}"),
        re.compile(r"(?i)authorization\s*:\s*\S+"),
        re.compile(r"(?i)password\s*=\s*\S+"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b[A-Za-z0-9]{32,}-[A-Za-z0-9]{8,}\b\.cfargotunnel\.com"),
        re.compile(r"\btskey-[A-Za-z0-9-]{10,}\b"),
    )

    def __init__(self, extra_secrets: list[str] | None = None):
        self.extra_secrets = [s for s in (extra_secrets or []) if s]
        self.count = 0

    def redact(self, text: str) -> str:
        if not text:
            return text
        for secret in self.extra_secrets:
            if secret and secret in text:
                occurrences = text.count(secret)
                self.count += occurrences
                text = text.replace(secret, "[REDACTED]")
        for pattern in self._PATTERNS:
            text, n = pattern.subn("[REDACTED]", text)
            self.count += n
        return text


# --------------------------------------------------------------------------- diagnostics bundle

_FORBIDDEN_DIAGNOSTIC_FILES = {"token", "oauth-signing.key", "auth.db", "nodes.secrets.json"}


class DiagnosticBundle:
    """Builds a redacted zip of system/runtime state for bug reports."""

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def create(self) -> dict[str, Any]:
        ctx = self.ctx
        out_dir = Path(ctx.cfg_dir) / "diagnostics"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive_path = out_dir / f"diagnostics-{stamp}.zip"

        extra_secrets: list[str] = []
        token_value = getattr(ctx.auth, "machine_token", None)
        if token_value:
            extra_secrets.append(token_value)
        redactor = Redactor(extra_secrets)

        sections: dict[str, Any] = {}
        sections["identity"] = self._safe(lambda: ctx.identity.as_dict())
        sections["config_public_view"] = self._safe(lambda: ctx.config.public_view())
        sections["policy"] = self._safe(lambda: {k: v for k, v in ctx.policy.data.items()})
        sections["effective_permissions"] = self._safe(lambda: ctx.policy.effective_permissions("local"))
        sections["sandbox_status"] = self._safe(lambda: ctx.sandbox.status())
        sections["doctor"] = self._safe(lambda: doctor(ctx, deep=False))
        sections["topology_state"] = self._safe(lambda: ctx.topology.state()) if ctx.topology is not None else None
        sections["ingress_status"] = self._safe(lambda: ctx.ingress.status(check_health=False)) if ctx.ingress is not None else None
        sections["auth_summary"] = self._safe(lambda: ctx.auth.summary(legacy_enabled=ctx.legacy_mcp_token_enabled()))
        sections["auth_events"] = self._safe(lambda: ctx.auth.recent_events(limit=100))
        sections["service_status"] = self._safe(self._service_status)
        sections["pip_freeze"] = self._safe(self._pip_freeze)
        sections["tailscale_status"] = self._safe(lambda: self._run_cli(["tailscale", "status"]))
        sections["cloudflared_version"] = self._safe(lambda: self._run_cli(["cloudflared", "--version"]))

        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(out_dir))
        os.close(tmp_fd)
        try:
            os.chmod(tmp_path, 0o600)
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                summary_text = json.dumps(sections, indent=2, sort_keys=True, default=str)
                zf.writestr("summary.json", redactor.redact(summary_text))
                for log_name in ("server.log", "cloudflared.log", "tailscale.log"):
                    log_path = Path(ctx.cfg_dir) / log_name
                    if not log_path.exists():
                        continue
                    tail_text = _tail_text(log_path, 500)
                    zf.writestr(f"logs/{log_name}", redactor.redact(tail_text))
            os.replace(tmp_path, archive_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.chmod(archive_path, 0o600)

        # _FORBIDDEN_DIAGNOSTIC_FILES are never read or written above: the bundle
        # only includes derived summaries (identity/config/policy/doctor/...) and
        # log tails, never token/oauth-signing.key/auth.db/nodes.secrets.json.
        try:
            ctx.audit.record("admin.diagnostics_bundle", "ok", detail={"redactions": redactor.count})
        except Exception:  # noqa: BLE001
            pass
        return {"path": str(archive_path), "size": archive_path.stat().st_size, "redactions": redactor.count}

    @staticmethod
    def _safe(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:300]}

    def _service_status(self) -> dict[str, Any]:
        from service import ServiceManager

        sm = ServiceManager(self.ctx.identity, self.ctx.cfg_dir, self.ctx.config.port)
        return sm.status()

    @staticmethod
    def _pip_freeze() -> list[str]:
        import subprocess

        python = sys.executable
        proc = subprocess.run([python, "-m", "pip", "freeze"], capture_output=True, text=True, timeout=20)
        return sorted(proc.stdout.splitlines())

    @staticmethod
    def _run_cli(argv: list[str]) -> str:
        import subprocess

        if not shutil.which(argv[0]):
            return "not installed"
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return (proc.stdout or proc.stderr or "").strip()[:2000]


def _tail_text(path: Path, lines: int) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    chunk = 8192
    data = b""
    with open(path, "rb") as handle:
        remaining = size
        while remaining > 0 and data.count(b"\n") <= lines:
            read_size = min(chunk, remaining)
            remaining -= read_size
            handle.seek(remaining)
            data = handle.read(read_size) + data
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


# --------------------------------------------------------------------------- update checker


class UpdateChecker:
    """Polls GitHub releases for a newer Loopback version. Never installs anything."""

    RELEASES_URL = "https://api.github.com/repos/p5n-n3t/loopback/releases"

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.state_path = Path(ctx.cfg_dir) / "update-state.json"

    def cached(self) -> dict[str, Any]:
        """Return the last cached result without making a network call."""
        data = _read_json(self.state_path, None)
        if isinstance(data, dict):
            return data
        return self._empty_result()

    def _empty_result(self) -> dict[str, Any]:
        mt = self.ctx.config.data.get("maintenance", {}) or {}
        return {
            "current": self._current_version(),
            "latest": None,
            "channel": mt.get("update_channel", "stable"),
            "checked_at": None,
            "notes_url": None,
            "available": False,
            "error": None,
        }

    @staticmethod
    def _current_version() -> str:
        try:
            import version as version_module

            return version_module.VERSION
        except Exception:  # noqa: BLE001
            return "0.0.0"

    def check(self, force: bool = False) -> dict[str, Any]:
        mt = self.ctx.config.data.get("maintenance", {}) or {}
        channel = str(mt.get("update_channel", "stable"))
        current = self._current_version()
        if channel == "off":
            result = {"current": current, "latest": None, "channel": channel, "checked_at": _utc_now_iso(),
                      "notes_url": None, "available": False, "error": None}
            _atomic_write_json(self.state_path, result)
            return result

        if not force:
            cached = self.cached()
            interval_hours = int(mt.get("update_check_interval_hours", 24) or 24)
            checked_at = cached.get("checked_at")
            if checked_at:
                try:
                    last = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                    if (datetime.now(UTC) - last).total_seconds() < interval_hours * 3600:
                        return cached
                except ValueError:
                    pass

        if httpx is None:
            result = self._empty_result()
            result["error"] = "httpx unavailable"
            return result

        try:
            headers = {"User-Agent": f"loopback/{current}", "Accept": "application/vnd.github+json"}
            resp = httpx.get(self.RELEASES_URL, headers=headers, timeout=8)
            resp.raise_for_status()
            releases = resp.json()
            candidates = [r for r in releases if isinstance(r, dict) and not r.get("draft")]
            if channel == "stable":
                candidates = [r for r in candidates if not r.get("prerelease")]
            candidates.sort(key=lambda r: _version_tuple(str(r.get("tag_name", ""))), reverse=True)
            latest_release = candidates[0] if candidates else None
            latest = str(latest_release.get("tag_name")) if latest_release else None
            available = bool(latest) and _version_tuple(latest) > _version_tuple(current)
            result = {
                "current": current,
                "latest": latest,
                "channel": channel,
                "checked_at": _utc_now_iso(),
                "notes_url": latest_release.get("html_url") if latest_release else None,
                "available": available,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            result = self._empty_result()
            result["error"] = str(exc)[:300]
            result["checked_at"] = _utc_now_iso()
        _atomic_write_json(self.state_path, result)
        return result


# --------------------------------------------------------------------------- maintenance manager


@dataclass
class _LastRun:
    probe_refresh: float = 0.0
    log_rotation: float = 0.0
    prune_hourly: float = 0.0
    backup_prune: float = 0.0
    update_check: float = 0.0
    disk_check: float = 0.0
    history_prune: float = 0.0


class MaintenanceManager:
    """Wires rotation/backup/diagnostics/update checking into one background loop."""

    LOOP_INTERVAL_SECONDS = 30
    LOG_ROTATION_INTERVAL_SECONDS = 600
    HOURLY_INTERVAL_SECONDS = 3600

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.logs = LogRotator(ctx)
        self.backups = BackupManager(ctx)
        self.diagnostics = DiagnosticBundle(ctx)
        self.updates = UpdateChecker(ctx)
        self.last_errors: list[dict[str, Any]] = []
        self._last_run = _LastRun()
        self._start_time = time.time()
        self._max_errors = 50

    # ------------------------------------------------------------ alerts
    def alerts(self) -> list[dict[str, Any]]:
        ctx = self.ctx
        out: list[dict[str, Any]] = []
        try:
            if ctx.legacy_mcp_token_enabled():
                out.append({"level": "warning", "code": "legacy_token",
                            "message": "Legacy machine-token MCP auth is enabled",
                            "action": "#/auth"})
        except Exception:  # noqa: BLE001
            pass
        try:
            if not ctx.auth.has_password():
                out.append({"level": "warning", "code": "no_password",
                            "message": "No dashboard password set",
                            "action": "#/auth"})
        except Exception:  # noqa: BLE001
            pass
        try:
            sandbox_status = ctx.sandbox.status()
            required = bool(ctx.policy.target_policy("local").get("sandbox_required"))
            if required and not sandbox_status.get("active") and not sandbox_status.get("acknowledge_unenforced"):
                out.append({"level": "error", "code": "sandbox_required_inactive",
                            "message": "Policy requires a sandbox but none is active",
                            "action": "#/local"})
            elif not sandbox_status.get("active"):
                out.append({"level": "warning", "code": "sandbox_inactive",
                            "message": "Sandbox is inactive",
                            "action": "#/local"})
        except Exception:  # noqa: BLE001
            pass
        try:
            warn_pct = int((ctx.config.data.get("maintenance", {}) or {}).get("disk_warn_percent", 90) or 90)
            for path in (Path(ctx.identity.home), Path(ctx.cfg_dir)):
                pct = _disk_usage_percent(path)
                if pct is not None and pct >= warn_pct:
                    out.append({"level": "warning", "code": "disk_high",
                                "message": f"{path} is {pct}% full",
                                "action": "#/logs"})
                    break
        except Exception:  # noqa: BLE001
            pass
        try:
            cached = self.updates.cached()
            if cached.get("available"):
                out.append({"level": "warning", "code": "update_available",
                            "message": f"Loopback {cached.get('latest')} is available (current {cached.get('current')})",
                            "action": "#/updates"})
        except Exception:  # noqa: BLE001
            pass
        try:
            if ctx.ingress is not None:
                status = ctx.ingress.status(check_health=False)
                for name in ("cloudflare", "tailscale"):
                    info = status.get(name) or {}
                    if info.get("enabled") and info.get("error"):
                        out.append({"level": "warning", "code": f"ingress_unhealthy:{name}",
                                    "message": f"{name} ingress reports an error",
                                    "action": "#/topology"})
        except Exception:  # noqa: BLE001
            pass
        try:
            recent = ctx.auth.recent_events(limit=50)
            cutoff = time.time() - 3600
            lockouts = [e for e in recent if str(e.get("event", "")).endswith("locked_out")
                        and float(e.get("ts", 0) or 0) >= cutoff]
            if lockouts:
                out.append({"level": "warning", "code": "auth_lockouts",
                            "message": f"{len(lockouts)} auth lockout event(s) in the last hour",
                            "action": "#/security"})
        except Exception:  # noqa: BLE001
            pass
        for err in self.last_errors[-5:]:
            out.append({"level": "warning", "code": "maintenance_job_error",
                        "message": f"{err.get('job')}: {err.get('error')}", "action": "#/logs"})
        return out

    # ------------------------------------------------------------ loop
    async def run_forever(self, stop_event: Any) -> None:
        import asyncio

        try:
            while not stop_event.is_set():
                now = time.time()
                await self._run_due_jobs(now)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.LOOP_INTERVAL_SECONDS)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _run_due_jobs(self, now: float) -> None:
        import asyncio

        mt = self.ctx.config.data.get("maintenance", {}) or {}

        probe_interval = int(mt.get("probe_interval_seconds", 60) or 0)
        if probe_interval > 0 and self.ctx.topology is not None and now - self._last_run.probe_refresh >= probe_interval:
            self._last_run.probe_refresh = now
            await self._guarded_async("probe_refresh", self.ctx.topology.refresh())

        if now - self._last_run.log_rotation >= self.LOG_ROTATION_INTERVAL_SECONDS:
            self._last_run.log_rotation = now
            await asyncio.to_thread(self._guarded_sync, "log_rotation", self.logs.rotate_all)

        if now - self._last_run.prune_hourly >= self.HOURLY_INTERVAL_SECONDS:
            self._last_run.prune_hourly = now
            await asyncio.to_thread(self._guarded_sync, "auth_prune", self._prune_auth)
            await asyncio.to_thread(self._guarded_sync, "audit_prune", self._prune_audit)
            await asyncio.to_thread(self._guarded_sync, "backup_prune", self.backups.prune)

        history_days = int(mt.get("history_retention_days", 7) or 0)
        if self.ctx.topology is not None and now - self._last_run.history_prune >= self.HOURLY_INTERVAL_SECONDS:
            self._last_run.history_prune = now
            await asyncio.to_thread(self._guarded_sync, "history_prune",
                                     lambda: self.ctx.topology.history.prune(history_days))

        interval_hours = int(mt.get("update_check_interval_hours", 24) or 24)
        if mt.get("update_channel") != "off" and now - self._last_run.update_check >= interval_hours * 3600:
            self._last_run.update_check = now
            await asyncio.to_thread(self._guarded_sync, "update_check", self.updates.check)

        if now - self._last_run.disk_check >= self.HOURLY_INTERVAL_SECONDS:
            self._last_run.disk_check = now
            self._guarded_sync("disk_check", lambda: _disk_usage_percent(Path(self.ctx.cfg_dir)))

    def _prune_auth(self) -> Any:
        if hasattr(self.ctx.auth, "prune"):
            return self.ctx.auth.prune()
        return None

    def _prune_audit(self) -> Any:
        if hasattr(self.ctx.audit, "prune"):
            mt = self.ctx.config.data.get("maintenance", {}) or {}
            days = int(mt.get("audit_retention_days", 90) or 90)
            return self.ctx.audit.prune(days)
        return None

    async def _guarded_async(self, job: str, coro: Any) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            self._record_error(job, exc)

    def _guarded_sync(self, job: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self._record_error(job, exc)

    def _record_error(self, job: str, exc: Exception) -> None:
        self.last_errors.append({"job": job, "error": str(exc)[:300], "at": _utc_now_iso()})
        if len(self.last_errors) > self._max_errors:
            self.last_errors = self.last_errors[-self._max_errors:]

    # ------------------------------------------------------------ status / metrics
    def status(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "last_run": {
                "probe_refresh": self._last_run.probe_refresh or None,
                "log_rotation": self._last_run.log_rotation or None,
                "prune_hourly": self._last_run.prune_hourly or None,
                "backup_prune": self._last_run.backup_prune or None,
                "update_check": self._last_run.update_check or None,
                "disk_check": self._last_run.disk_check or None,
                "history_prune": self._last_run.history_prune or None,
            },
            "last_errors": list(self.last_errors[-10:]),
        }

    def metrics(self) -> dict[str, Any]:
        cfg_dir = Path(self.ctx.cfg_dir)
        rss_bytes = _process_rss_bytes()
        cpu_percent = _process_cpu_percent()
        db_bytes = _safe_size(cfg_dir / "loopback.db")
        auth_db_bytes = _safe_size(cfg_dir / "auth.db")
        job_logs_bytes = _dir_size(cfg_dir / "jobs")
        static_bytes = _dir_size(cfg_dir / "skills")
        last_probe_cost_ms = None
        if self.ctx.topology is not None:
            last_probe_cost_ms = getattr(self.ctx.topology, "last_refresh_ms", None)
        return {
            "rss_bytes": rss_bytes,
            "cpu_percent": cpu_percent,
            "db_bytes": db_bytes,
            "auth_db_bytes": auth_db_bytes,
            "job_logs_bytes": job_logs_bytes,
            "static_bytes": static_bytes,
            "last_probe_cost_ms": last_probe_cost_ms,
            "probe_cost_ms": last_probe_cost_ms,
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _process_rss_bytes() -> int | None:
    if not WINDOWS:
        try:
            status_path = Path("/proc/self/status")
            if status_path.exists():
                for line in status_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return kb * 1024
        except (OSError, ValueError, IndexError):
            pass
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return usage * 1024 if sys.platform != "darwin" else usage
    except (ImportError, OSError):
        return None


_CPU_SAMPLE: dict[str, float] = {}


def _process_cpu_percent() -> float | None:
    try:
        import resource

        now = time.monotonic()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_time = usage.ru_utime + usage.ru_stime
        last_time = _CPU_SAMPLE.get("time")
        last_cpu = _CPU_SAMPLE.get("cpu")
        _CPU_SAMPLE["time"] = now
        _CPU_SAMPLE["cpu"] = cpu_time
        if last_time is None or last_cpu is None:
            return 0.0
        elapsed = now - last_time
        if elapsed <= 0:
            return 0.0
        return round(max(0.0, (cpu_time - last_cpu) / elapsed * 100), 1)
    except (ImportError, OSError):
        return None
