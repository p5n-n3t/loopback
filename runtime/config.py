"""Versioned, validated Loopback configuration.

``config.json`` is the source of truth from schema version 2 onward. The v3
``config.env`` file is migrated on first load and then regenerated after every
save so older tooling keeps working.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from identity import RuntimeIdentity
from version import SCHEMA_VERSION, VERSION

INGRESS_MODES = {"serve", "funnel"}
UPDATE_CHANNELS = {"stable", "prerelease", "off"}
NODE_KINDS = {"local-computer", "remote-physical", "remote-vm", "container", "gateway", "unknown"}
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


def default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "port": 2026,
        "bind": "127.0.0.1",
        "public_host": "localhost",
        "display_name": None,
        "node_kind": "local-computer",
        "shell": None,
        "initial_cwd": None,
        "command_timeout": 120,
        "max_output_bytes": 1048576,
        "startup_timeout": 15,
        "ingress": {
            "local": {"enabled": True},
            "cloudflare": {
                "enabled": False,
                "hostname": None,
                "tunnel_id": None,
                "tunnel_name": None,
                "token_file": None,
                "config_file": None,
                "access_policy": "oauth",  # oauth | cloudflare-access | both
            },
            "tailscale": {
                "enabled": False,
                "mode": "serve",  # serve = tailnet only, funnel = public internet
                "https_port": 443,
                "dns_name": None,
            },
        },
        "auth": {
            "legacy_machine_token_mcp": False,
            "access_ttl_seconds": 3600,
            "refresh_ttl_seconds": 30 * 86400,
            "session_ttl_seconds": 12 * 3600,
            "code_ttl_seconds": 300,
            "allowed_origins": ["https://chatgpt.com", "https://www.perplexity.ai", "https://perplexity.ai"],
            "allow_dynamic_registration": True,
            "allow_client_id_metadata_documents": True,
            "require_password_for_consent": True,
        },
        "sandbox": {
            "backend": "auto",  # auto | landlock | bwrap | sandbox-exec | none
            "require_enforcement": True,
            "allow_tmp": True,
            "acknowledge_unenforced": False,
        },
        "maintenance": {
            "update_channel": "stable",
            "update_check_interval_hours": 24,
            "auto_apply_updates": False,
            "backup_retention": 10,
            "log_max_bytes": 5 * 1024 * 1024,
            "log_backups": 5,
            "probe_interval_seconds": 60,
            "history_retention_days": 7,
            "audit_retention_days": 90,
            "disk_warn_percent": 90,
            "job_log_max_bytes": 64 * 1024 * 1024,
            "max_completed_jobs": 200,
        },
    }


LEGACY_ENV_MAP = {
    "LOOPBACK_PORT": ("port", int),
    "LOOPBACK_PUBLIC_HOST": ("public_host", str),
    "LOOPBACK_TIMEOUT": ("command_timeout", int),
    "LOOPBACK_MAX_OUTPUT_BYTES": ("max_output_bytes", int),
    "LOOPBACK_STARTUP_TIMEOUT": ("startup_timeout", int),
}


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def migrate_legacy_env(env: dict[str, str], base: dict[str, Any]) -> dict[str, Any]:
    """Translate a v3 ``config.env`` into the v2 schema without losing settings."""
    cfg = copy.deepcopy(base)
    for key, (field, caster) in LEGACY_ENV_MAP.items():
        if key in env and env[key] != "":
            try:
                cfg[field] = caster(env[key])
            except ValueError:
                pass
    ingress = (env.get("LOOPBACK_INGRESS") or "local").strip().lower()
    host = (env.get("LOOPBACK_PUBLIC_HOST") or "localhost").strip().rstrip(".")
    if ingress == "cloudflare":
        cfg["ingress"]["cloudflare"].update({
            "enabled": True,
            "hostname": host,
            "tunnel_id": env.get("LOOPBACK_CLOUDFLARE_TUNNEL_ID") or None,
            "token_file": env.get("LOOPBACK_CLOUDFLARE_TOKEN_FILE") or None,
        })
    elif ingress == "tailscale":
        # v3 only knew Funnel (public). Preserve that behaviour explicitly.
        cfg["ingress"]["tailscale"].update({
            "enabled": True,
            "mode": "funnel",
            "https_port": int(env.get("LOOPBACK_TAILSCALE_HTTPS_PORT") or 443),
            "dns_name": host,
        })
    # Existing installs keep working with their current clients until the
    # administrator migrates them to per-client credentials in the dashboard.
    cfg["auth"]["legacy_machine_token_mcp"] = True
    cfg["_migrated_from"] = {"config_env": True, "access_mode": env.get("LOOPBACK_ACCESS_MODE")}
    return cfg


def validate(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    port = cfg.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append("port must be an integer between 1 and 65535")
    bind = cfg.get("bind")
    if bind not in {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"} and not isinstance(bind, str):  # nosec B104 - validated choice
        errors.append("bind must be an address string")
    host = str(cfg.get("public_host") or "")
    if host and host not in {"localhost", "127.0.0.1"} and not HOSTNAME_RE.match(host):
        errors.append(f"public_host is not a valid hostname: {host}")
    for key in ("command_timeout", "max_output_bytes", "startup_timeout"):
        value = cfg.get(key)
        if not isinstance(value, int) or value <= 0:
            errors.append(f"{key} must be a positive integer")
    if isinstance(cfg.get("command_timeout"), int) and cfg["command_timeout"] > 86400:
        errors.append("command_timeout must be at most 86400 seconds")
    if isinstance(cfg.get("max_output_bytes"), int) and cfg["max_output_bytes"] > 64 * 1024 * 1024:
        errors.append("max_output_bytes must be at most 64 MiB")
    shell = cfg.get("shell")
    if shell is not None and (not isinstance(shell, str) or not shell.strip()):
        errors.append("shell must be null or a non-empty path")
    cwd = cfg.get("initial_cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        errors.append("initial_cwd must be null or a non-empty path")
    if cfg.get("node_kind") not in NODE_KINDS:
        errors.append(f"node_kind must be one of {sorted(NODE_KINDS)}")
    ingress = cfg.get("ingress") or {}
    cf = ingress.get("cloudflare") or {}
    if cf.get("enabled"):
        if not cf.get("hostname") or not HOSTNAME_RE.match(str(cf["hostname"])) or "." not in str(cf["hostname"]):
            errors.append("cloudflare.hostname must be a fully-qualified hostname when Cloudflare is enabled")
        if not cf.get("tunnel_id") and not cf.get("token_file"):
            errors.append("cloudflare requires tunnel_id (named tunnel) or token_file")
        if cf.get("access_policy") not in {"oauth", "cloudflare-access", "both"}:
            errors.append("cloudflare.access_policy must be oauth, cloudflare-access or both")
    ts = ingress.get("tailscale") or {}
    if ts.get("enabled"):
        if ts.get("mode") not in INGRESS_MODES:
            errors.append("tailscale.mode must be 'serve' (tailnet only) or 'funnel' (public)")
        if not isinstance(ts.get("https_port"), int) or ts["https_port"] not in {443, 8443, 10000}:
            errors.append("tailscale.https_port must be 443, 8443 or 10000")
    auth = cfg.get("auth") or {}
    for key, lo, hi in (("access_ttl_seconds", 60, 86400), ("refresh_ttl_seconds", 300, 365 * 86400),
                        ("session_ttl_seconds", 300, 30 * 86400), ("code_ttl_seconds", 30, 600)):
        value = auth.get(key)
        if not isinstance(value, int) or not (lo <= value <= hi):
            errors.append(f"auth.{key} must be between {lo} and {hi}")
    origins = auth.get("allowed_origins")
    if not isinstance(origins, list) or any(not isinstance(o, str) or not o.startswith(("https://", "http://localhost", "http://127.0.0.1")) for o in origins):
        errors.append("auth.allowed_origins must be a list of https:// origins (or localhost http origins)")
    sb = cfg.get("sandbox") or {}
    if sb.get("backend") not in {"auto", "landlock", "bwrap", "sandbox-exec", "none"}:
        errors.append("sandbox.backend must be auto, landlock, bwrap, sandbox-exec or none")
    mt = cfg.get("maintenance") or {}
    if mt.get("update_channel") not in UPDATE_CHANNELS:
        errors.append("maintenance.update_channel must be stable, prerelease or off")
    for key in ("update_check_interval_hours", "backup_retention", "log_max_bytes", "log_backups",
                "probe_interval_seconds", "history_retention_days", "audit_retention_days", "disk_warn_percent",
                "job_log_max_bytes", "max_completed_jobs"):
        value = mt.get(key)
        if not isinstance(value, int) or value < 0:
            errors.append(f"maintenance.{key} must be a non-negative integer")
    if isinstance(mt.get("probe_interval_seconds"), int) and 0 < mt["probe_interval_seconds"] < 10:
        errors.append("maintenance.probe_interval_seconds must be 0 (off) or at least 10")
    if isinstance(mt.get("disk_warn_percent"), int) and mt["disk_warn_percent"] > 100:
        errors.append("maintenance.disk_warn_percent must be at most 100")
    return errors


class ConfigStore:
    """Loads, validates, migrates and atomically persists ``config.json``."""

    def __init__(self, identity: RuntimeIdentity, on_change: Callable[[str, dict[str, Any]], None] | None = None):
        self.identity = identity
        self.dir = Path(identity.config_dir)
        self.path = self.dir / "config.json"
        self.legacy_env_path = self.dir / "config.env"
        self.backup_dir = self.dir / "config.backups"
        self._lock = threading.RLock()
        self._on_change = on_change
        self.data: dict[str, Any] = default_config()
        self.migrated = False
        self.load()

    # -- loading -----------------------------------------------------------
    def load(self) -> dict[str, Any]:
        with self._lock:
            base = default_config()
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"config.json is unreadable: {exc}") from exc
                if not isinstance(raw, dict):
                    raise RuntimeError("config.json must contain a JSON object")
                self.data = self._upgrade(_deep_merge(base, raw))
            elif self.legacy_env_path.exists():
                self.data = migrate_legacy_env(parse_env_file(self.legacy_env_path), base)
                self.migrated = True
            else:
                self.data = base
            self.data["version"] = VERSION
            return self.data

    def _upgrade(self, cfg: dict[str, Any]) -> dict[str, Any]:
        version = int(cfg.get("schema_version") or 1)
        if version < 2:
            cfg["schema_version"] = 2
        return cfg

    # -- derived values ----------------------------------------------------
    @property
    def port(self) -> int:
        return int(self.data["port"])

    def public_hosts(self) -> list[str]:
        """Every hostname clients may use to reach this instance, most public first."""
        hosts: list[str] = []
        ing = self.data.get("ingress", {})
        cf = ing.get("cloudflare", {})
        ts = ing.get("tailscale", {})
        if cf.get("enabled") and cf.get("hostname"):
            hosts.append(str(cf["hostname"]).lower())
        if ts.get("enabled") and ts.get("dns_name"):
            hosts.append(str(ts["dns_name"]).lower().rstrip("."))
        legacy = str(self.data.get("public_host") or "").lower().rstrip(".")
        if legacy and legacy not in {"localhost", "127.0.0.1"} and legacy not in hosts:
            hosts.append(legacy)
        return hosts

    def base_urls(self) -> list[str]:
        urls = [f"https://{h}" for h in self.public_hosts()]
        urls.append(f"http://127.0.0.1:{self.port}")
        urls.append(f"http://localhost:{self.port}")
        return urls

    def resource_uris(self) -> list[str]:
        return [f"{b}/mcp" for b in self.base_urls()]

    def primary_base_url(self) -> str:
        return self.base_urls()[0]

    def primary_host(self) -> str:
        hosts = self.public_hosts()
        return hosts[0] if hosts else "localhost"

    def has_public_ingress(self) -> bool:
        return bool(self.public_hosts())

    def effective_shell(self) -> str:
        from identity import default_shell

        return default_shell(preferred=self.data.get("shell"))

    def effective_initial_cwd(self) -> str:
        cwd = self.data.get("initial_cwd")
        if cwd:
            candidate = Path(str(cwd)).expanduser()
            if candidate.is_dir():
                return str(candidate)
        return self.identity.home

    # -- persistence -------------------------------------------------------
    def save(self, updates: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with self._lock:
            merged = _deep_merge(self.data, updates)
            merged["schema_version"] = SCHEMA_VERSION
            merged["version"] = VERSION
            errors = validate(merged)
            if errors:
                raise ValueError("; ".join(errors))
            self._backup_current()
            self._write_atomic(self.path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
            self.data = merged
            self.write_legacy_env()
            if self._on_change:
                try:
                    self._on_change(actor, updates)
                except Exception:  # pragma: no cover - audit must never break saves
                    pass
            return copy.deepcopy(self.data)

    def persist_migration(self) -> None:
        """Write the migrated configuration if it was loaded from config.env."""
        with self._lock:
            if not self.path.exists():
                errors = validate(self.data)
                if errors:
                    raise ValueError("; ".join(errors))
                self._write_atomic(self.path, json.dumps(self.data, indent=2, sort_keys=True) + "\n")
                self.write_legacy_env()

    def write_legacy_env(self) -> None:
        cfg = self.data
        ing = cfg["ingress"]
        if ing["cloudflare"].get("enabled"):
            ingress = "cloudflare"
        elif ing["tailscale"].get("enabled"):
            ingress = "tailscale"
        else:
            ingress = "local"
        lines = [
            "# Generated by Loopback from config.json. Edit config.json or use the dashboard.",
            f"LOOPBACK_PORT={cfg['port']}",
            f"LOOPBACK_PUBLIC_HOST={self.primary_host()}",
            f"LOOPBACK_TIMEOUT={cfg['command_timeout']}",
            f"LOOPBACK_MAX_OUTPUT_BYTES={cfg['max_output_bytes']}",
            f"LOOPBACK_STARTUP_TIMEOUT={cfg['startup_timeout']}",
            f"LOOPBACK_INGRESS={ingress}",
            f"LOOPBACK_CLOUDFLARE_TUNNEL_ID={ing['cloudflare'].get('tunnel_id') or ''}",
            f"LOOPBACK_CLOUDFLARE_TOKEN_FILE={ing['cloudflare'].get('token_file') or ''}",
            f"LOOPBACK_TAILSCALE_HTTPS_PORT={ing['tailscale'].get('https_port') or 443}",
            f"LOOPBACK_TAILSCALE_MODE={ing['tailscale'].get('mode') or 'serve'}",
        ]
        self._write_atomic(self.legacy_env_path, "\n".join(lines) + "\n")
        self._write_atomic(self.dir / "public_host", self.primary_host() + "\n")

    def _backup_current(self) -> None:
        if not self.path.exists():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"config-{stamp}.json"
        try:
            shutil.copy2(self.path, target)
            os.chmod(target, 0o600)
        except OSError:
            return
        keep = int(self.data.get("maintenance", {}).get("backup_retention", 10) or 10)
        backups = sorted(self.backup_dir.glob("config-*.json"))
        for old in backups[:-keep] if keep else backups:
            try:
                old.unlink()
            except OSError:
                pass

    def rollback(self) -> dict[str, Any]:
        """Restore the newest configuration backup."""
        with self._lock:
            backups = sorted(self.backup_dir.glob("config-*.json"))
            if not backups:
                raise FileNotFoundError("no configuration backups available")
            raw = json.loads(backups[-1].read_text(encoding="utf-8"))
            errors = validate(_deep_merge(default_config(), raw))
            if errors:
                raise ValueError("backup is invalid: " + "; ".join(errors))
            self._write_atomic(self.path, json.dumps(raw, indent=2, sort_keys=True) + "\n")
            backups[-1].unlink()
            self.load()
            self.write_legacy_env()
            return copy.deepcopy(self.data)

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def public_view(self) -> dict[str, Any]:
        """Configuration without any secret-bearing fields."""
        view = copy.deepcopy(self.data)
        view.pop("_migrated_from", None)
        return view
