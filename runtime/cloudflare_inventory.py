"""Read-only Cloudflare route catalog with explicit credentials or imported observations."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def safe_service(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("http_status:"):
        return value[:32]
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh", "tcp"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"{parsed.scheme}://{host}" + (f":{port}" if port else "") + (parsed.path or "")
    except ValueError:
        return None


def sanitize(tunnels: list, observed_at: float, source: str) -> dict:
    result = []
    for tunnel in tunnels[:1000]:
        if not isinstance(tunnel, dict) or not tunnel.get("id"):
            continue
        routes = []
        for route in (tunnel.get("ingress") or [])[:200]:
            if not isinstance(route, dict):
                continue
            host = route.get("hostname")
            service = safe_service(route.get("service"))
            if not isinstance(host, str) or not host or any(c in host for c in "/@\r\n") or not service:
                continue
            routes.append({"hostname": host[:253], "path": str(route.get("path") or "")[:256], "service": service})
        result.append(
            {
                "id": str(tunnel["id"])[:100],
                "name": str(tunnel.get("name") or "")[:100],
                "status": str(tunnel.get("status") or "unknown")[:40],
                "ingress": routes,
            }
        )
    return {"source": source, "observed_at": observed_at, "tunnels": result, "stale": time.time() - observed_at > 300, "host_association": "unknown"}


class CloudflareInventory:
    def __init__(self, config: dict, cfg_dir: Path):
        self.config = config
        self.path = cfg_dir / "cloudflare-discovery.json"
        self.lock = threading.Lock()
        self.deadline = 0.0

    def _request(self, path: str, token: str) -> dict:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider_budget_exhausted")
        request = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=min(10, remaining)) as response:
            data = json.loads(response.read(4 * 1024 * 1024))
        if not data.get("success"):
            raise ValueError("Cloudflare request rejected")
        return data

    def refresh(self) -> dict:
        if not self.lock.acquire(blocking=False):
            return {**self.cached(), "refresh_in_progress": True}
        try:
            return self._refresh()
        finally:
            self.lock.release()

    def _refresh(self) -> dict:
        settings = (self.config.get("discovery") or {}).get("cloudflare") or {}
        account = settings.get("account_id")
        env_name = settings.get("token_env")
        token = os.environ.get(env_name, "") if isinstance(env_name, str) else ""
        if not settings.get("enabled"):
            return self.cached()
        if not account or not token:
            return {**self.cached(), "error": "provider_credentials_unavailable"}
        if not isinstance(account, str) or len(account) != 32 or not all(c in "0123456789abcdef" for c in account):
            return {**self.cached(), "error": "invalid_account_id"}
        try:
            self.deadline = time.monotonic() + 30
            tunnels = []
            for page in range(1, 11):
                data = self._request(f"/accounts/{account}/cfd_tunnel?is_deleted=false&per_page=100&page={page}", token)
                rows = data.get("result") or []
                for row in rows:
                    tid = str(row.get("id") or "")
                    if not tid or any(c not in "0123456789abcdef-" for c in tid):
                        continue
                    configuration = self._request(f"/accounts/{account}/cfd_tunnel/{tid}/configurations", token)
                    tunnels.append({**row, "ingress": ((configuration.get("result") or {}).get("config") or {}).get("ingress") or []})
                if len(rows) < 100:
                    break
            if len(rows) == 100 and page == 10:
                raise ValueError("provider_page_limit_exceeded")
            result = sanitize(tunnels, time.time(), "cloudflare-api")
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(result))
            temporary.chmod(0o600)
            temporary.replace(self.path)
            return result
        except Exception:  # API response bodies and credentials never enter diagnostics.
            return {**self.cached(), "error": "provider_refresh_failed"}

    def cached(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return sanitize(data.get("tunnels") or [], float(data["observed_at"]), str(data.get("source") or "import"))
        except (OSError, ValueError, KeyError, TypeError):
            return {"source": "unconfigured", "observed_at": None, "stale": True, "tunnels": [], "host_association": "unknown"}
