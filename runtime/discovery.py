"""Read-only fleet discovery: existing tunnels, Serve/Funnel handlers, listeners and MCP gateways.

The inventory answers "what is already exposed on each machine?" without adopting, rewriting
or probing anything. It asks the worker agent on the controller and every *registered* worker
for a snapshot (``node_agent.discover``), then classifies each record:

* ``managed`` — a Loopback binding or the controller's configured ingress owns it;
* ``external`` — something else owns it (an existing route such as a RotoXY Funnel is shown
  exactly as configured and is never touched);
* ``unknown`` — the owner could not be determined (another user's process, no PID).

Hosts that cannot be reached are reported as ``unreachable`` with their last known
inventory, never as "nothing found". Targets listed in ``config.discovery.excluded_targets``
are reported as ``excluded`` and are never contacted.
"""
from __future__ import annotations

import concurrent.futures
import copy
import re
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

MCP_HINT = re.compile(r"mcp|loopback|rotoxy|gateway", re.I)
OLD_WORKER = "Worker is older than the controller; install or update it from Fleet to enable discovery"


def _bindings_by_port(bindings: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(b["port"]): b for b in bindings if isinstance(b.get("port"), int)}


def classify_host(target: str, raw: dict[str, Any], *, gateway_port: int | None = None,
                  controller_ingress: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn one worker snapshot into route/listener records with ownership and provenance."""
    bindings = raw.get("bindings") or []
    by_port = _bindings_by_port(bindings)
    ingress = controller_ingress or {}
    routes: list[dict[str, Any]] = []
    ts = raw.get("tailscale") or {}
    dns = ts.get("dns_name")
    for r in ts.get("routes") or []:
        target_url = str(r.get("target") or "")
        port = None
        m = re.search(r":(\d+)/?$", target_url)
        if m:
            port = int(m.group(1))
        https_port = None
        hp = str(r.get("host_port") or "")
        if ":" in hp:
            try:
                https_port = int(hp.rsplit(":", 1)[1])
            except ValueError:
                https_port = None
        owner = "unknown"
        owned_by = None
        if port is not None and port in by_port and by_port[port].get("exposure", "").startswith("tailscale") and \
                (by_port[port].get("https_port") or 443) == https_port and by_port[port].get("tailscale_owned"):
            owner, owned_by = "managed", by_port[port]["name"]
        elif target == "local" and gateway_port and port == gateway_port and (ingress.get("tailscale") or {}).get("enabled"):
            owner, owned_by = "managed", "controller"
        elif target_url:
            owner = "external"
        mode = r.get("mode") or "serve"
        routes.append({
            "id": f"{target}:tailscale:{hp}{r.get('path') or ''}",
            "host": target, "provider": "tailscale", "mode": mode,
            "exposure": "public" if mode == "funnel" else "tailnet",
            "hostname": hp.rsplit(":", 1)[0] if ":" in hp else (dns or hp), "port": https_port,
            "path": r.get("path"), "target": target_url or None, "target_port": port,
            "owner": owner, "owned_by": owned_by, "foreground": bool(r.get("foreground")),
            "kind": "mcp-gateway" if (r.get("path") or "").rstrip("/").endswith("/mcp") or (port in by_port) else "route",
            "note": "Existing route. Loopback shows it and never changes it." if owner == "external" else None,
        })
    cf = raw.get("cloudflared") or {}
    tunnel_pids = {b.get("tunnel_pid") for b in bindings if b.get("tunnel_pid")}
    for p in cf.get("processes") or []:
        owner, owned_by = "external", None
        if p.get("pid") in tunnel_pids:
            owner = "managed"
            owned_by = next((b["name"] for b in bindings if b.get("tunnel_pid") == p.get("pid")), None)
        elif target == "local" and (ingress.get("cloudflare") or {}).get("enabled") and ingress.get("cloudflare_pid") == p.get("pid"):
            owner, owned_by = "managed", "controller"
        hostnames = [i["hostname"] for i in p.get("ingress") or [] if "hostname" in i]
        services = [i["service"] for i in p.get("ingress") or [] if "service" in i]
        routes.append({
            "id": f"{target}:cloudflared:{p.get('pid')}", "host": target, "provider": "cloudflare", "mode": "tunnel",
            "exposure": "public", "hostname": hostnames[0] if hostnames else None, "hostnames": hostnames, "port": None,
            "path": None, "target": services[0] if services else None, "targets": services, "target_port": None,
            "owner": owner, "owned_by": owned_by, "pid": p.get("pid"), "args": p.get("args"), "config_file": p.get("config_file"),
            "kind": "mcp-gateway" if any(MCP_HINT.search(x) for x in hostnames + services) else "route",
            "note": None if p.get("config_file") else "Token-based tunnel: its public hostnames are configured in Cloudflare, not on this host.",
        })
    listeners: list[dict[str, Any]] = []
    for item in (raw.get("listeners") or {}).get("items") or []:
        port = item.get("port")
        owner, owned_by = "unknown", None
        if port in by_port:
            owner, owned_by = "managed", by_port[port]["name"]
        elif target == "local" and gateway_port and port == gateway_port:
            owner, owned_by = "managed", "controller"
        elif item.get("process"):
            owner = "external"
        listeners.append({**item, "owner": owner, "owned_by": owned_by,
                          "mcp_candidate": port in by_port or (target == "local" and port == gateway_port) or bool(MCP_HINT.search(str(item.get("process") or "")))})
    return {
        "target": target, "status": "ok", "observed_at": raw.get("observed_at"), "hostname": raw.get("hostname"),
        "tailscale": {"status": ts.get("status", "unavailable"), "dns_name": dns, "error": ts.get("error")},
        "cloudflared": {"status": cf.get("status", "unavailable"), "error": cf.get("error")},
        "listeners_status": (raw.get("listeners") or {}).get("status", "unavailable"),
        "routes": routes, "listeners": listeners, "bindings": bindings,
        "partial": any(x.get("status") != "ok" for x in (ts, cf, raw.get("listeners") or {})),
    }


class DiscoveryService:
    """Caches one inventory per host and never performs mutations."""

    def __init__(self, ctx: Any, *, cache_seconds: float = 30.0):
        self.ctx = ctx
        self.cache_seconds = cache_seconds
        self.hosts: dict[str, dict[str, Any]] = {}
        self.generated_at: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ config
    def excluded_targets(self) -> list[str]:
        cfg = (getattr(self.ctx.config, "data", {}) or {}).get("discovery") or {}
        return [str(x) for x in (cfg.get("excluded_targets") or [])]

    def targets(self) -> list[str]:
        workers = getattr(self.ctx, "workers", None)
        registered = list(workers.targets()) if workers else ["local"]
        return sorted({"local", *registered})

    def _controller_ingress(self) -> dict[str, Any]:
        cfg = (getattr(self.ctx.config, "data", {}) or {}).get("ingress") or {}
        out = {"tailscale": cfg.get("tailscale") or {}, "cloudflare": cfg.get("cloudflare") or {}}
        ingress = getattr(self.ctx, "ingress", None)
        try:
            out["cloudflare_pid"] = ingress.cloudflared_pid() if ingress else None
        except Exception:  # noqa: BLE001
            out["cloudflare_pid"] = None
        return out

    # ------------------------------------------------------------------ collection
    def _collect(self, target: str) -> dict[str, Any]:
        previous = self.hosts.get(target)
        try:
            raw = self.ctx.workers.call(target, {"action": "discover"}, timeout=20)
            gateway_port = int(self.ctx.config.data.get("port") or 2026) if target == "local" else None
            return classify_host(target, raw, gateway_port=gateway_port, controller_ingress=self._controller_ingress() if target == "local" else None)
        except Exception as exc:  # noqa: BLE001 - SSH output is never echoed
            message = str(exc)
            error = OLD_WORKER if "Unknown binding action" in message else "Host unreachable; check SSH and worker setup"
            if "not installed" in message:
                error = "Worker is not installed on this host; discovery needs the worker"
            stale = copy.deepcopy(previous) if previous and previous.get("observed_at") else None  # only a real inventory counts
            return {"target": target, "status": "unreachable", "error": error, "observed_at": (stale or {}).get("observed_at"),
                    "routes": (stale or {}).get("routes", []), "listeners": (stale or {}).get("listeners", []),
                    "bindings": (stale or {}).get("bindings", []), "partial": True, "last_known": bool(stale)}

    def refresh(self) -> dict[str, Any]:
        excluded = set(self.excluded_targets())
        targets = [t for t in self.targets() if t not in excluded]
        results: dict[str, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(targets)))) as pool:
            for host in pool.map(self._collect, targets):
                results[host["target"]] = host
        for name in sorted(excluded):
            results[name] = {"target": name, "status": "excluded", "error": "Excluded by discovery policy; never contacted",
                             "routes": [], "listeners": [], "bindings": [], "partial": False}
        with self._lock:
            self.hosts = results
            self.generated_at = time.time()
        return self.inventory()

    def inventory(self, *, max_age: float | None = None) -> dict[str, Any]:
        max_age = self.cache_seconds if max_age is None else max_age
        with self._lock:
            fresh = self.generated_at is not None and time.time() - self.generated_at <= max_age
            snapshot = copy.deepcopy(self.hosts) if fresh else None
            generated_at = self.generated_at
        if snapshot is None:
            return self.refresh()
        hosts = [snapshot[k] for k in sorted(snapshot, key=lambda k: (k != "local", k))]
        return {"generated_at": generated_at, "cache_seconds": self.cache_seconds, "hosts": hosts,
                "excluded_targets": self.excluded_targets(),
                "summary": {"hosts": len(hosts), "reachable": sum(1 for h in hosts if h["status"] == "ok"),
                            "routes": sum(len(h.get("routes") or []) for h in hosts),
                            "external": sum(1 for h in hosts for r in h.get("routes") or [] if r.get("owner") == "external"),
                            "managed": sum(1 for h in hosts for r in h.get("routes") or [] if r.get("owner") == "managed"),
                            "gateways": sum(1 for h in hosts for r in h.get("routes") or [] if r.get("kind") == "mcp-gateway")},
                "note": "Read-only inventory from the worker agent. A listener is a candidate, not proof of an MCP endpoint or its policy."}

    def cached(self) -> dict[str, Any]:
        """The last inventory without any I/O (for topology builds)."""
        with self._lock:
            hosts = copy.deepcopy(self.hosts)
            generated_at = self.generated_at
        return {"generated_at": generated_at, "hosts": [hosts[k] for k in sorted(hosts, key=lambda k: (k != "local", k))]}


def routes(ctx: Any) -> list[Route]:
    async def get_inventory(request: Request):
        svc = getattr(ctx, "discovery", None)
        if svc is None:
            return JSONResponse({"detail": "discovery is not available in this runtime"}, status_code=404)
        import asyncio

        return JSONResponse(await asyncio.to_thread(svc.inventory))

    async def refresh(request: Request):
        svc = getattr(ctx, "discovery", None)
        if svc is None:
            return JSONResponse({"detail": "discovery is not available in this runtime"}, status_code=404)
        import asyncio

        data = await asyncio.to_thread(svc.refresh)
        ctx.audit.record("discovery.refresh", "ok", target="dashboard", detail={"hosts": data["summary"]["hosts"]})
        return JSONResponse(data)

    return [Route("/api/admin/discovery", get_inventory, methods=["GET"]),
            Route("/api/admin/discovery/refresh", refresh, methods=["POST"])]


__all__ = ["DiscoveryService", "classify_host", "routes"]
