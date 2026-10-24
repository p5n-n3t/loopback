"""Build the shared AppContext from on-disk state.

Used by ``server.py`` (long-running gateway) and ``cli.py`` (install, doctor,
upgrade) so both see identical configuration, policy and credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import identity as identity_mod
from audit import AuditStore
from auth import AuthStore
from config import ConfigStore
from context import AppContext
from policy import PolicyStore
from sandbox import SandboxManager
from version import VERSION


def build_context(config_dir: str | Path | None = None, *, start_managers: bool = True,
                  prefer_sudo_user: bool = False) -> AppContext:
    if config_dir:
        os.environ["LOOPBACK_CONFIG_DIR"] = str(config_dir)
    ident = identity_mod.detect(prefer_sudo_user=prefer_sudo_user)
    cfg_dir = Path(ident.config_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cfg_dir, 0o700)
    except OSError:
        pass

    audit = AuditStore(cfg_dir)
    config = ConfigStore(ident)
    if config.migrated:
        config.persist_migration()
        audit.record("config.migrate", "ok", target="config.env", detail={"to_schema": config.data.get("schema_version")})

    auth_cfg = config.data.get("auth", {})
    auth = AuthStore(
        cfg_dir,
        resource_uris=config.resource_uris(),
        base_urls=config.base_urls(),
        access_ttl=int(auth_cfg.get("access_ttl_seconds", 3600)),
        refresh_ttl=int(auth_cfg.get("refresh_ttl_seconds", 30 * 86400)),
        session_ttl=int(auth_cfg.get("session_ttl_seconds", 12 * 3600)),
        code_ttl=int(auth_cfg.get("code_ttl_seconds", 300)),
    )

    policy = PolicyStore(cfg_dir, home=ident.home)
    migrated = config.data.get("_migrated_from") or {}
    if not policy.path.exists() and migrated.get("access_mode"):
        policy.save({"profile": migrated["access_mode"]})

    sb_cfg = config.data.get("sandbox", {})
    sandbox = SandboxManager(
        str(sb_cfg.get("backend", "auto")),
        require_enforcement=bool(sb_cfg.get("require_enforcement", True)),
        allow_tmp=bool(sb_cfg.get("allow_tmp", True)),
        acknowledge_unenforced=bool(sb_cfg.get("acknowledge_unenforced", False)),
    )
    policy.sandbox_active = sandbox.enforced()
    policy.sandbox_acknowledged = bool(sb_cfg.get("acknowledge_unenforced", False)) or not bool(sb_cfg.get("require_enforcement", True))

    jobs: Any = None
    terminals: Any = None
    fleet: Any = None
    if start_managers:
        from fleet import FleetStore
        from jobs import JobManager, TerminalManager

        shell = config.effective_shell()
        jobs = JobManager(cfg_dir, shell)
        terminals = TerminalManager(shell)
        fleet = FleetStore(cfg_dir)

    ctx = AppContext(identity=ident, config=config, policy=policy, auth=auth, audit=audit, sandbox=sandbox,
                     jobs=jobs, terminals=terminals, fleet=fleet, version=VERSION)
    config._on_change = ctx.record_config_change  # noqa: SLF001 - wiring, not state mutation

    # Optional subsystems: present when their modules are installed.
    for attr, module_name, factory in (
        ("ssh", "ssh_targets", lambda m: m.SSHTargetStore(ctx)),
        ("workers", "workers", lambda m: m.WorkerManager(ctx)),
        ("ingress", "ingress", lambda m: m.IngressManager(ctx)),
        ("topology", "topology", lambda m: m.TopologyBuilder(ctx)),
        ("maintenance", "maintenance", lambda m: m.MaintenanceManager(ctx)),
    ):
        try:
            module = __import__(module_name)
            setattr(ctx, attr, factory(module))
        except ImportError:
            setattr(ctx, attr, None)
    return ctx
