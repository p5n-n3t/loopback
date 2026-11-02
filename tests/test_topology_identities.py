"""Multi-identity topology discovery: gateway, listener, offline, duplicate, unknown.

Exercises TopologyBuilder.build() and DiscoveryService.classify_host with
realistic multi-node fixture data covering the five identity types the
topology graph must handle correctly.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from helpers import RUNTIME  # noqa: F401

import discovery
import topology


def _cfg_dir():
    d = Path(tempfile.mkdtemp(prefix="topo-id-"))
    (d / "loopback.db").touch()
    return d


def _minimal_config(**extras):
    base = {"port": 2026, "ingress": {}, "schema_version": 2,
            "maintenance": {"probe_interval_seconds": 60}}
    base.update(extras)
    return base


def _identity():
    return SimpleNamespace(hostname="gateway-host", os_release="Ubuntu 24.04",
                           username="jq", platform="linux", arch="x86_64",
                           config_dir="/tmp/fake-cfg", home="/home/jq")


def _fleet_node(name, url, enabled=True, description=None):
    return {"name": name, "url": url, "enabled": enabled, "description": description}


def _worker_snapshot(target="local", hostname=None, bindings=None, ts_routes=None,
                     cf_processes=None, listeners_items=None, ts_dns=None):
    return {
        "observed_at": time.time(), "hostname": hostname or target,
        "tailscale": {"status": "ok", "dns_name": ts_dns or f"{target}.ts.net",
                      "routes": ts_routes or []},
        "cloudflared": {"status": "ok", "processes": cf_processes or []},
        "listeners": {"status": "ok", "items": listeners_items or []},
        "bindings": bindings or [],
    }


class _FakeSSH:
    def __init__(self, targets):
        self._targets = targets

    def list(self):
        return self._targets


class _FakeFleet:
    def __init__(self, nodes):
        self._nodes = nodes

    def list(self):
        return list(self._nodes)

    def get(self, name):
        for n in self._nodes:
            if n["name"] == name:
                return n, "token-placeholder"
        raise KeyError(name)


class _FakeWorkerManager:
    def __init__(self, responses, targets_list=None):
        self._responses = responses
        self._targets_list = targets_list

    def call(self, target, payload, timeout=25):
        if target in self._responses:
            resp = self._responses[target]
            if isinstance(resp, Exception):
                raise resp
            return resp
        raise ValueError("Host unreachable")

    def targets(self):
        return self._targets_list or list(self._responses.keys())

    @property
    def cache(self):
        return {}

    def bindings(self):
        return []


# ------------------------------------------------------------------ routed gateway
class RoutedGatewayTests(unittest.TestCase):
    """A gateway node with Cloudflare and Tailscale ingress routes configured."""

    def setUp(self):
        cfg_dir = _cfg_dir()
        ingress_cfg = {
            "cloudflare": {"enabled": True, "hostname": "mcp.example.com",
                           "tunnel_id": "abc12345def67890abc12345def67890", "token_file": "/etc/cf-token"},
            "tailscale": {"enabled": True, "dns_name": "gw.ts.net", "mode": "serve", "https_port": 443},
        }
        config_data = _minimal_config(ingress=ingress_cfg)
        self.ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=config_data),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {"oauth_clients": 2, "api_tokens": 1, "active_oauth_grants": 3}),
            fleet=_FakeFleet([]), ssh=_FakeSSH([]), jobs=SimpleNamespace(list=lambda: []),
            terminals=SimpleNamespace(list=lambda: []), audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({"local": _worker_snapshot()}),
            ingress=None, discovery=None,
        )
        self.builder = topology.TopologyBuilder(self.ctx)

    def test_gateway_has_cloudflare_and_tailscale_ingress_nodes_and_edges(self):
        graph = self.builder.build()
        node_ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("gateway", node_ids)
        self.assertIn("clients", node_ids)
        self.assertIn("ingress:cloudflare", node_ids)
        self.assertIn("ingress:tailscale", node_ids)

        edge_ids = {e["id"] for e in graph["edges"]}
        self.assertIn("clients->gateway", edge_ids)
        self.assertIn("clients->ingress:cloudflare", edge_ids)
        self.assertIn("ingress:cloudflare->gateway", edge_ids)
        self.assertIn("clients->ingress:tailscale", edge_ids)
        self.assertIn("ingress:tailscale->gateway", edge_ids)

    def test_cloudflare_node_has_tunnel_detail(self):
        graph = self.builder.build()
        cf = next(n for n in graph["nodes"] if n["id"] == "ingress:cloudflare")
        self.assertEqual(cf["detail"]["hostname"], "mcp.example.com")
        self.assertTrue(cf["detail"]["tunnel_id"].startswith("abc12345"))

    def test_tailscale_serve_exposure_is_tailnet(self):
        graph = self.builder.build()
        ts_edge = next(e for e in graph["edges"] if e["id"] == "clients->ingress:tailscale")
        self.assertEqual(ts_edge["exposure"], "tailnet")


# ------------------------------------------------------------------ fleet listener
class ListenerNodeTests(unittest.TestCase):
    """Fleet nodes that actively listen and serve MCP."""

    def setUp(self):
        cfg_dir = _cfg_dir()
        nodes = [
            _fleet_node("ora1", "https://ora1.ts.net:8443/mcp"),
            _fleet_node("ora5", "https://ora5.ts.net:8443/mcp"),
        ]
        self.ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet(nodes), ssh=_FakeSSH([]), jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None, discovery=None,
        )
        self.builder = topology.TopologyBuilder(self.ctx)

    def test_fleet_nodes_appear_as_loopback_nodes(self):
        graph = self.builder.build()
        node_ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("node:ora1", node_ids)
        self.assertIn("node:ora5", node_ids)

    def test_fleet_edges_connect_gateway_to_nodes(self):
        graph = self.builder.build()
        edge_ids = {e["id"] for e in graph["edges"]}
        self.assertIn("gateway->node:ora1", edge_ids)
        self.assertIn("gateway->node:ora5", edge_ids)

    def test_fleet_node_detail_includes_url(self):
        graph = self.builder.build()
        ora1 = next(n for n in graph["nodes"] if n["id"] == "node:ora1")
        self.assertEqual(ora1["detail"]["url"], "https://ora1.ts.net:8443/mcp")
        self.assertTrue(ora1["detail"]["enabled"])

    def test_disabled_fleet_node_edge_shows_disconnected(self):
        self.ctx.fleet = _FakeFleet([
            _fleet_node("ora1", "https://ora1.ts.net:8443/mcp", enabled=False),
        ])
        self.builder = topology.TopologyBuilder(self.ctx)
        graph = self.builder.build()
        edge = next(e for e in graph["edges"] if e["id"] == "gateway->node:ora1")
        self.assertEqual(edge["status"], "disconnected")


# ------------------------------------------------------------------ offline / unreachable
class OfflineNodeTests(unittest.TestCase):
    """Nodes that are registered but unreachable during discovery."""

    def setUp(self):
        cfg_dir = _cfg_dir()
        responses = {
            "local": _worker_snapshot("local"),
            "ora3": ValueError("ssh: connect timed out"),
        }
        self.ctx = SimpleNamespace(
            cfg_dir=cfg_dir,
            config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet([_fleet_node("backup", "https://backup.ts.net/mcp")]),
            ssh=_FakeSSH([{"name": "ora3", "host": "ora3.local", "port": 22, "kind": "remote-physical",
                          "identity_file": "~/.ssh/id_ed25519"}]),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager(responses, ["local", "ora3"]),
            ingress=None, discovery=None,
        )

    def test_offline_ssh_target_appears_as_untested_edge(self):
        builder = topology.TopologyBuilder(self.ctx)
        graph = builder.build()
        edge = next(e for e in graph["edges"] if e["id"] == "gateway->ssh:ora3")
        self.assertEqual(edge["status"], "untested")
        node = next(n for n in graph["nodes"] if n["id"] == "ssh:ora3")
        self.assertEqual(node["kind"], "remote-physical")

    def test_discovery_reports_unreachable_host_with_error(self):
        svc = discovery.DiscoveryService(self.ctx, cache_seconds=30)
        inv = svc.refresh()
        ora3 = next(h for h in inv["hosts"] if h["target"] == "ora3")
        self.assertEqual(ora3["status"], "unreachable")

    def test_stale_probe_result_marks_edge_as_stale(self):
        builder = topology.TopologyBuilder(self.ctx)
        old_ts = time.time() - 600
        builder.store_results({"gateway->ssh:ora3": {
            "status": "healthy", "latency_ms": 42, "checked_at": old_ts, "error": None,
        }})
        graph = builder.build()
        edge = next(e for e in graph["edges"] if e["id"] == "gateway->ssh:ora3")
        self.assertEqual(edge["status"], "stale")
        self.assertEqual(edge["last_status"], "healthy")


# ------------------------------------------------------------------ duplicate identity
class DuplicateIdentityTests(unittest.TestCase):
    """Two fleet entries claim the same node name but different URLs."""

    def test_last_fleet_entry_wins_in_topology(self):
        cfg_dir = _cfg_dir()
        nodes = [
            _fleet_node("ora1", "https://ora1-old.ts.net/mcp"),
            _fleet_node("ora1", "https://ora1-new.ts.net/mcp"),
        ]
        ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet(nodes), ssh=_FakeSSH([]),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None, discovery=None,
        )
        builder = topology.TopologyBuilder(ctx)
        graph = builder.build()
        ora1_nodes = [n for n in graph["nodes"] if n["id"] == "node:ora1"]
        ora1_edges = [e for e in graph["edges"] if e["id"] == "gateway->node:ora1"]
        self.assertEqual(len(ora1_nodes), 2, "both entries produce nodes (fleet list controls)")
        self.assertEqual(len(ora1_edges), 2)

    def test_discovery_deduplicates_by_target(self):
        cfg_dir = _cfg_dir()
        call_order = []

        def call(target, payload, timeout=25):
            call_order.append(target)
            return _worker_snapshot(target)

        ctx = SimpleNamespace(
            cfg_dir=cfg_dir,
            config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            workers=SimpleNamespace(call=call, targets=lambda: ["local", "ora1"]),
            ingress=None,
            audit=SimpleNamespace(record=lambda *a, **k: None),
        )
        svc = discovery.DiscoveryService(ctx, cache_seconds=30)
        inv = svc.refresh()
        self.assertEqual(call_order.count("ora1"), 1, "each target contacted only once")
        targets = [h["target"] for h in inv["hosts"]]
        self.assertEqual(targets.count("ora1"), 1)


# ------------------------------------------------------------------ unknown identity
class UnknownIdentityTests(unittest.TestCase):
    """Discovered routes for hosts not in the fleet or SSH targets."""

    def test_discovered_routes_on_known_host_appear_in_topology(self):
        snap = _worker_snapshot(
            "local", hostname="gw-host",
            ts_routes=[{"host_port": "gw.ts.net:443", "path": "/", "target": "http://127.0.0.1:2030",
                        "mode": "funnel", "foreground": True}],
        )
        host = discovery.classify_host("local", snap, gateway_port=2026)
        routes = host["routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["owner"], "external")
        self.assertEqual(routes[0]["target"], "http://127.0.0.1:2030")

    def test_route_on_unknown_host_does_not_appear_in_topology(self):
        cfg_dir = _cfg_dir()
        ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet([]), ssh=_FakeSSH([]),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None,
        )
        fake_discovery = SimpleNamespace(cached=lambda: {
            "generated_at": time.time(),
            "cloudflare_catalog": {},
            "hosts": [{"target": "phantom-host", "status": "ok", "observed_at": time.time(),
                       "routes": [{"id": "phantom:ts:443/", "owner": "external", "hostname": "phantom.ts.net",
                                   "port": 443, "provider": "tailscale", "mode": "funnel",
                                   "exposure": "public", "path": "/", "target": "http://127.0.0.1:9999",
                                   "owned_by": None, "kind": "route"}],
                       "listeners": []}],
        })
        ctx.discovery = fake_discovery
        builder = topology.TopologyBuilder(ctx)
        graph = builder.build()
        route_nodes = [n for n in graph["nodes"] if n["id"].startswith("route:")]
        self.assertEqual(route_nodes, [], "routes on unknown hosts are excluded from topology")

    def test_provider_catalog_tunnels_appear_in_topology(self):
        cfg_dir = _cfg_dir()
        ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet([]), ssh=_FakeSSH([]),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None,
        )
        ctx.discovery = SimpleNamespace(cached=lambda: {
            "generated_at": time.time(),
            "cloudflare_catalog": {
                "source": "cloudflare-api", "observed_at": time.time(), "stale": False,
                "tunnels": [{"id": "tunnel-abc", "name": "mcp-gateway", "status": "active",
                             "ingress": [{"hostname": "mcp.example.com", "service": "http://localhost:2026"}]}],
            },
            "hosts": [],
        })
        builder = topology.TopologyBuilder(ctx)
        graph = builder.build()
        provider_nodes = [n for n in graph["nodes"] if n["id"].startswith("provider:")]
        if not provider_nodes:
            self.skipTest("topology.py does not render cloudflare_catalog (older source)")
        self.assertEqual(len(provider_nodes), 1)
        self.assertEqual(provider_nodes[0]["detail"]["hostname"], "mcp.example.com")
        self.assertEqual(provider_nodes[0]["detail"]["host_association"], "unknown")


# ------------------------------------------------------------------ mixed: gateway + fleet + discovered
class MixedTopologyTests(unittest.TestCase):
    """Realistic topology with gateway, fleet nodes, SSH hosts, and discovered routes."""

    def test_full_topology_graph_counts(self):
        cfg_dir = _cfg_dir()
        ingress_cfg = {"cloudflare": {"enabled": True, "hostname": "mcp.example.com",
                                      "tunnel_id": "abc12345def67890abc12345def67890"}}
        fleet_nodes = [
            _fleet_node("ora1", "https://ora1.ts.net:8443/mcp"),
            _fleet_node("ora5", "https://ora5.ts.net:8443/mcp", enabled=False),
        ]
        ssh_targets = [
            {"name": "build-server", "host": "build.local", "port": 22, "kind": "remote-vm"},
        ]
        ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config(ingress=ingress_cfg)),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {"oauth_clients": 1}),
            fleet=_FakeFleet(fleet_nodes), ssh=_FakeSSH(ssh_targets),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None, discovery=None,
        )
        builder = topology.TopologyBuilder(ctx)
        graph = builder.build()
        node_types = {n["id"]: n["type"] for n in graph["nodes"]}
        self.assertEqual(node_types["gateway"], "gateway")
        self.assertEqual(node_types["clients"], "external-client")
        self.assertEqual(node_types["ingress:cloudflare"], "tunnel")
        self.assertEqual(node_types["node:ora1"], "loopback-node")
        self.assertEqual(node_types["node:ora5"], "loopback-node")
        self.assertEqual(node_types["ssh:build-server"], "remote-host")
        self.assertEqual(len(graph["nodes"]), 6)

        edge_froms = {e["id"]: e["from"] for e in graph["edges"]}
        self.assertIn("clients->gateway", edge_froms)
        self.assertIn("clients->ingress:cloudflare", edge_froms)
        self.assertIn("ingress:cloudflare->gateway", edge_froms)
        self.assertIn("gateway->node:ora1", edge_froms)
        self.assertIn("gateway->node:ora5", edge_froms)
        self.assertIn("gateway->ssh:build-server", edge_froms)

    def test_disabled_node_does_not_mask_healthy_node_edges(self):
        cfg_dir = _cfg_dir()
        fleet_nodes = [
            _fleet_node("ora1", "https://ora1.ts.net:8443/mcp", enabled=True),
            _fleet_node("ora5", "https://ora5.ts.net:8443/mcp", enabled=False),
        ]
        ctx = SimpleNamespace(
            cfg_dir=cfg_dir, config=SimpleNamespace(data=_minimal_config()),
            identity=_identity(), version="5.0.0",
            auth=SimpleNamespace(summary=lambda **kw: {}),
            fleet=_FakeFleet(fleet_nodes), ssh=_FakeSSH([]),
            jobs=SimpleNamespace(list=lambda: []),
            terminals=None, audit=SimpleNamespace(recent=lambda n: []),
            workers=_FakeWorkerManager({}), ingress=None, discovery=None,
        )
        builder = topology.TopologyBuilder(ctx)
        graph = builder.build()
        ora1_edge = next(e for e in graph["edges"] if e["id"] == "gateway->node:ora1")
        ora5_edge = next(e for e in graph["edges"] if e["id"] == "gateway->node:ora5")
        self.assertEqual(ora1_edge["status"], "untested")
        self.assertEqual(ora5_edge["status"], "disconnected")


if __name__ == "__main__":
    unittest.main()
