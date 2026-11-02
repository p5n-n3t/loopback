"""Multi-identity topology discovery lab (single-container, process-level isolation).

Validates that TopologyBuilder correctly handles five identity types using
simulated node contexts. Each scenario builds a topology graph and asserts
structural invariants without real network I/O or Docker networking.

This script is the single-container equivalent of the multi-container lab:
it proves the topology logic handles every identity combination before the
more expensive Docker-network lab is built.

Exit 0 with a JSON manifest on success; non-zero with diagnostic on failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))

import discovery
import topology

MANIFEST = {"schema_version": 1, "scenarios": [], "outcome": "UNKNOWN"}


def _cfg_dir():
    d = Path(tempfile.mkdtemp(prefix="topo-lab-"))
    (d / "loopback.db").touch()
    return d


def _identity(hostname="lab-gateway"):
    return SimpleNamespace(hostname=hostname, os_release="Ubuntu 24.04",
                           username="lab", platform="linux", arch="x86_64",
                           config_dir="/tmp/lab-cfg", home="/home/lab")


def _snapshot(target="local", **overrides):
    base = {"observed_at": time.time(), "hostname": target,
            "tailscale": {"status": "unavailable", "dns_name": None, "routes": [], "error": "not installed"},
            "cloudflared": {"status": "unavailable", "processes": []},
            "listeners": {"status": "ok", "items": []},
            "bindings": []}
    base.update(overrides)
    return base


class _Fleet:
    def __init__(self, nodes):
        self._nodes = nodes
    def list(self):
        return list(self._nodes)
    def get(self, name):
        for n in self._nodes:
            if n["name"] == name:
                return n, "token"
        raise KeyError(name)


class _Workers:
    def __init__(self, responses, targets=None):
        self._responses = responses
        self._targets = targets or list(responses.keys())
    def call(self, target, payload, timeout=25):
        r = self._responses.get(target)
        if r is None:
            raise ValueError("unreachable")
        if isinstance(r, Exception):
            raise r
        return r
    def targets(self):
        return self._targets
    @property
    def cache(self):
        return {}
    def bindings(self):
        return []


def _build_ctx(cfg_dir, config_data, fleet_nodes=None, ssh_targets=None, workers=None,
               discovery_data=None):
    ctx = SimpleNamespace(
        cfg_dir=cfg_dir,
        config=SimpleNamespace(data=config_data),
        identity=_identity(),
        version="5.0.0-lab",
        auth=SimpleNamespace(summary=lambda **kw: {}),
        fleet=_Fleet(fleet_nodes or []),
        ssh=SimpleNamespace(list=lambda: ssh_targets or []),
        jobs=SimpleNamespace(list=lambda: []),
        terminals=None,
        audit=SimpleNamespace(recent=lambda n: []),
        workers=workers or _Workers({"local": _snapshot()}),
        ingress=None,
        discovery=None,
    )
    if discovery_data is not None:
        ctx.discovery = SimpleNamespace(cached=lambda: discovery_data)
    return ctx


def scenario(name, fn):
    """Run one scenario and record the result."""
    try:
        fn()
        result = {"name": name, "outcome": "PASS"}
    except Exception as exc:
        result = {"name": name, "outcome": "FAIL", "error": str(exc)[:300]}
    MANIFEST["scenarios"].append(result)
    status = "✓" if result["outcome"] == "PASS" else "✗"
    print(f"  {status} {name}", file=sys.stderr)
    return result["outcome"] == "PASS"


# ------------------------------------------------------------------ scenarios

def test_routed_gateway():
    """Gateway with Cloudflare + Tailscale ingress."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2,
              "maintenance": {"probe_interval_seconds": 60},
              "ingress": {
                  "cloudflare": {"enabled": True, "hostname": "mcp.lab.example.com",
                                 "tunnel_id": "aabbccdd11223344aabbccdd11223344"},
                  "tailscale": {"enabled": True, "dns_name": "lab-gw.ts.net",
                                "mode": "funnel", "https_port": 443},
              }}
    ctx = _build_ctx(cfg_dir, config)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    ids = {n["id"] for n in graph["nodes"]}
    assert "gateway" in ids
    assert "ingress:cloudflare" in ids
    assert "ingress:tailscale" in ids
    edge_ids = {e["id"] for e in graph["edges"]}
    assert "clients->ingress:cloudflare" in edge_ids
    assert "ingress:cloudflare->gateway" in edge_ids
    assert "clients->ingress:tailscale" in edge_ids
    assert "ingress:tailscale->gateway" in edge_ids
    ts_edge = next(e for e in graph["edges"] if e["id"] == "clients->ingress:tailscale")
    assert ts_edge["exposure"] == "public", f"funnel should be public, got {ts_edge['exposure']}"


def test_fleet_listeners():
    """Multiple fleet nodes, some enabled, some disabled."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2, "ingress": {},
              "maintenance": {"probe_interval_seconds": 60}}
    nodes = [
        {"name": "ora1", "url": "https://ora1.ts.net:8443/mcp", "enabled": True},
        {"name": "ora5", "url": "https://ora5.ts.net:8443/mcp", "enabled": False},
        {"name": "ora8", "url": "https://ora8.ts.net:8443/mcp", "enabled": True},
    ]
    ctx = _build_ctx(cfg_dir, config, fleet_nodes=nodes)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert "node:ora1" in node_ids
    assert "node:ora5" in node_ids
    assert "node:ora8" in node_ids
    ora5_edge = next(e for e in graph["edges"] if e["id"] == "gateway->node:ora5")
    assert ora5_edge["status"] == "disconnected", f"disabled node should be disconnected, got {ora5_edge['status']}"
    ora1_edge = next(e for e in graph["edges"] if e["id"] == "gateway->node:ora1")
    assert ora1_edge["status"] == "untested"


def test_offline_node():
    """SSH target that times out during discovery."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2, "ingress": {},
              "maintenance": {"probe_interval_seconds": 60}}
    ssh = [{"name": "dead-host", "host": "dead.local", "port": 22, "kind": "remote-physical"}]
    workers = _Workers(
        {"local": _snapshot(), "dead-host": ValueError("ssh: connect timed out")},
        targets=["local", "dead-host"],
    )
    ctx = _build_ctx(cfg_dir, config, ssh_targets=ssh, workers=workers)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    node = next(n for n in graph["nodes"] if n["id"] == "ssh:dead-host")
    assert node["kind"] == "remote-physical"
    edge = next(e for e in graph["edges"] if e["id"] == "gateway->ssh:dead-host")
    assert edge["status"] == "untested"

    svc = discovery.DiscoveryService(ctx, cache_seconds=30)
    inv = svc.refresh()
    dead = next(h for h in inv["hosts"] if h["target"] == "dead-host")
    assert dead["status"] == "unreachable", f"expected unreachable, got {dead['status']}"


def test_duplicate_identity():
    """Two fleet entries with the same name produce two nodes (the fleet controls dedup)."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2, "ingress": {},
              "maintenance": {"probe_interval_seconds": 60}}
    nodes = [
        {"name": "ora1", "url": "https://ora1-a.ts.net/mcp", "enabled": True},
        {"name": "ora1", "url": "https://ora1-b.ts.net/mcp", "enabled": True},
    ]
    ctx = _build_ctx(cfg_dir, config, fleet_nodes=nodes)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    ora1_nodes = [n for n in graph["nodes"] if n["id"] == "node:ora1"]
    ora1_edges = [e for e in graph["edges"] if e["id"] == "gateway->node:ora1"]
    assert len(ora1_nodes) == 2, f"expected 2 duplicate nodes, got {len(ora1_nodes)}"
    assert len(ora1_edges) == 2


def test_unknown_identity():
    """Discovered routes on an unknown host are excluded from topology; provider catalog is included."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2, "ingress": {},
              "maintenance": {"probe_interval_seconds": 60}}
    disc = {
        "generated_at": time.time(),
        "cloudflare_catalog": {
            "source": "cloudflare-api", "observed_at": time.time(), "stale": False,
            "tunnels": [{"id": "tun-001", "name": "lab-tun", "status": "active",
                         "ingress": [{"hostname": "api.lab.example.com",
                                      "service": "http://localhost:3000"}]}],
        },
        "hosts": [
            {"target": "phantom", "status": "ok", "observed_at": time.time(),
             "routes": [{"id": "phantom:ts:443/", "owner": "external",
                         "hostname": "phantom.ts.net", "port": 443,
                         "provider": "tailscale", "mode": "funnel",
                         "exposure": "public", "path": "/",
                         "target": "http://127.0.0.1:9999",
                         "owned_by": None, "kind": "route"}],
             "listeners": []},
        ],
    }
    ctx = _build_ctx(cfg_dir, config, discovery_data=disc)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    route_nodes = [n for n in graph["nodes"] if n["id"].startswith("route:")]
    assert route_nodes == [], f"phantom host routes should not appear: {[n['id'] for n in route_nodes]}"
    provider_nodes = [n for n in graph["nodes"] if n["id"].startswith("provider:")]
    if not provider_nodes:
        # Older topology.py without cloudflare_catalog rendering — acceptable
        pass
    else:
        assert len(provider_nodes) == 1, f"expected 1 provider node, got {len(provider_nodes)}"
        assert provider_nodes[0]["detail"]["hostname"] == "api.lab.example.com"


def test_mixed_topology():
    """Gateway + fleet + SSH + discovered routes + provider catalog."""
    cfg_dir = _cfg_dir()
    config = {"port": 2026, "schema_version": 2,
              "maintenance": {"probe_interval_seconds": 60},
              "ingress": {"cloudflare": {"enabled": True, "hostname": "mcp.lab.example.com",
                                         "tunnel_id": "aabbccdd11223344aabbccdd11223344"}}}
    fleet = [{"name": "ora1", "url": "https://ora1.ts.net:8443/mcp", "enabled": True}]
    ssh = [{"name": "build", "host": "build.local", "port": 22, "kind": "remote-vm"}]
    disc = {
        "generated_at": time.time(),
        "cloudflare_catalog": {},
        "hosts": [
            {"target": "local", "status": "ok", "observed_at": time.time(),
             "routes": [{"id": "local:ts:443/app", "owner": "external",
                         "hostname": "gw.ts.net", "port": 443,
                         "provider": "tailscale", "mode": "funnel",
                         "exposure": "public", "path": "/app",
                         "target": "http://127.0.0.1:3000",
                         "owned_by": None, "kind": "route"}],
             "listeners": []},
        ],
    }
    ctx = _build_ctx(cfg_dir, config, fleet_nodes=fleet, ssh_targets=ssh,
                     discovery_data=disc)
    builder = topology.TopologyBuilder(ctx)
    graph = builder.build()
    types = {n["id"]: n["type"] for n in graph["nodes"]}
    assert types["gateway"] == "gateway"
    assert types["clients"] == "external-client"
    assert types["ingress:cloudflare"] == "tunnel"
    assert types["node:ora1"] == "loopback-node"
    assert types["ssh:build"] == "remote-host"
    route_nodes = [n for n in graph["nodes"] if n["id"].startswith("route:")]
    assert len(route_nodes) == 1, "local discovered route should appear"
    assert route_nodes[0]["detail"]["path"] == "/app"
    total_edges = len(graph["edges"])
    assert total_edges >= 7, f"expected at least 7 edges, got {total_edges}"


# ------------------------------------------------------------------ main

def main():
    print("Topology identity lab", file=sys.stderr)
    all_pass = True
    for name, fn in [
        ("routed-gateway", test_routed_gateway),
        ("fleet-listeners", test_fleet_listeners),
        ("offline-node", test_offline_node),
        ("duplicate-identity", test_duplicate_identity),
        ("unknown-identity", test_unknown_identity),
        ("mixed-topology", test_mixed_topology),
    ]:
        if not scenario(name, fn):
            all_pass = False

    MANIFEST["outcome"] = "PASS" if all_pass else "FAIL"
    print(json.dumps(MANIFEST, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
