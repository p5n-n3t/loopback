"""Evidence-only provider/worker correlation. Never infer hosts from tunnel names."""

from __future__ import annotations

import copy
from urllib.parse import urlsplit


def correlate(catalog: dict, hosts: list[dict], mappings: dict | None = None) -> dict:
    result = copy.deepcopy(catalog)
    mappings = mappings or {}
    known = {h["target"]: h for h in hosts if h.get("status") == "ok"}
    for tunnel in result.get("tunnels") or []:
        tid = str(tunnel.get("id") or "").lower()
        observed = sorted({h["target"] for h in hosts if h.get("status") == "ok" for route in h.get("routes") or [] if route.get("tunnel_id") == tid})
        mapped = mappings.get(tid)
        if isinstance(mapped, str) and mapped in known:
            observed = sorted(set(observed + [mapped]))
        tunnel["host_candidates"] = observed
        tunnel["host_association"] = "matched" if len(observed) == 1 else "ambiguous" if observed else "unknown"
        tunnel["host"] = observed[0] if len(observed) == 1 else None
        tunnel["host_evidence"] = "owner-mapping" if mapped in observed else "observed-tunnel-id" if observed else None
        for route in tunnel.get("ingress") or []:
            route["backend"] = {"state": "unverified", "namespace": "connector"}
            # A connector may run in a container. Port-only matching is deliberately prohibited.
    return result


def listener_for_route(route: dict, listeners: list[dict]) -> dict:
    """Only correlate observed host-local routes to exact or wildcard listener addresses."""
    try:
        u = urlsplit(route.get("target") or "")
        port = u.port or (443 if u.scheme == "https" else 80 if u.scheme == "http" else None)
    except ValueError:
        return {"state": "unverified"}
    if u.hostname not in {"127.0.0.1", "::1", "localhost"} or port is None:
        return {"state": "unverified"}
    addresses = {"127.0.0.1", "0.0.0.0"} if u.hostname == "127.0.0.1" else {"::1", "::"} if u.hostname == "::1" else {"127.0.0.1", "::1", "0.0.0.0", "::"}
    matches = [listener for listener in listeners if listener.get("port") == port and listener.get("address") in addresses]
    if len(matches) != 1:
        return {"state": "ambiguous" if matches else "not_observed", "port": port}
    match = matches[0]
    return {
        "state": "listener_observed",
        "port": port,
        "address": match["address"],
        "process": match.get("process"),
        "pid": match.get("pid"),
        "mcp_state": "candidate",
    }
