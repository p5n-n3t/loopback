from __future__ import annotations

import base64
import hashlib
import importlib
import logging
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

logging.disable(logging.CRITICAL)

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


def fresh_config_dir() -> str:
    """Point the runtime at an empty config dir and drop cached runtime modules."""
    td = tempfile.mkdtemp(prefix="loopback-test-")
    os.environ["LOOPBACK_CONFIG_DIR"] = td
    # Keep tests offline and quiet: no update checks, no periodic probes.
    import json

    with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 2, "maintenance": {"update_channel": "off", "probe_interval_seconds": 0}}, handle)
    for name in ("server", "bootstrap", "fabric_api", "maintenance_api", "dashboard"):
        sys.modules.pop(name, None)
    return td


def load_server():
    return importlib.import_module("server")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def consent_fields(html: str) -> tuple[str, str]:
    rid = re.search(r'name="request_id" value="([^"]+)"', html).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    return rid, csrf


def rpc(client, headers: dict, method: str, params: dict | None = None, rpc_id: int = 1):
    return client.post("/mcp", headers={**MCP_HEADERS, **headers},
                       json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}})


def call_tool(client, headers: dict, name: str, arguments: dict | None = None):
    return rpc(client, headers, "tools/call", {"name": name, "arguments": arguments or {}})


def tool_text(response) -> str:
    body = response.json()
    result = body.get("result") or {}
    content = result.get("content") or []
    return content[0]["text"] if content else ""


def tool_data(response) -> dict:
    return (response.json().get("result") or {}).get("structuredContent") or {}
