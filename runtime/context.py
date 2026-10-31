"""Shared application context handed to every runtime module.

``server.py`` builds one ``AppContext`` and passes it to modules that expose
``routes(ctx) -> list[starlette.routing.BaseRoute]``. Modules must not import
``server`` and must not reach for globals; everything they need is here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from audit import AuditStore
from auth import AuthStore
from config import ConfigStore
from identity import RuntimeIdentity
from policy import PolicyStore
from sandbox import SandboxManager


@dataclass
class AppContext:
    identity: RuntimeIdentity
    config: ConfigStore
    policy: PolicyStore
    auth: AuthStore
    audit: AuditStore
    sandbox: SandboxManager
    jobs: Any
    terminals: Any
    fleet: Any
    started_at: float = field(default_factory=time.time)
    # Optional subsystems registered by their modules at import time.
    workers: Any = None
    ssh: Any = None
    topology: Any = None
    ingress: Any = None
    maintenance: Any = None
    browser: Any = None
    desktop: Any = None
    documents: Any = None
    discovery: Any = None
    scanner: Any = None
    shield: Any = None
    tool_enabled: Callable[[str], bool] | None = None
    tool_catalog: Callable[[], list[dict[str, Any]]] | None = None
    version: str = ""

    @property
    def cfg_dir(self) -> Path:
        return Path(self.identity.config_dir)

    def legacy_mcp_token_enabled(self) -> bool:
        return bool(self.config.data.get("auth", {}).get("legacy_machine_token_mcp", False))

    def refresh_auth_urls(self) -> None:
        self.auth.set_urls(self.config.resource_uris(), self.config.base_urls())

    def record_config_change(self, actor: str, updates: dict[str, Any]) -> None:
        keys = sorted(_flatten_keys(updates))
        self.audit.record("config.change", "ok", target=actor, detail={"keys": keys[:50]})


def _flatten_keys(data: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            keys.extend(_flatten_keys(value, name + "."))
        else:
            keys.append(name)
    return keys
