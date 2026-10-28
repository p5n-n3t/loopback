"""Read-only discovery: classification, partial hosts, exclusions, topology nodes and the admin API."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import fresh_config_dir, load_server

from starlette.testclient import TestClient

import discovery
import node_agent

# The desktop's existing RotoXY route (Oct 28 read-only sample): Funnel 443, "/" → 127.0.0.1:2030.
ROTOXY_SERVE = {"AllowFunnel": {"jq-xubuntu1.bigeye-escalator.ts.net:443": True},
                "Foreground": {"abc": {"Web": {"jq-xubuntu1.bigeye-escalator.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:2030"}}}},
                                       "AllowFunnel": {"jq-xubuntu1.bigeye-escalator.ts.net:443": True}}},
                "Web": {"jq-xubuntu1.bigeye-escalator.ts.net:8443": {"Handlers": {"/mcp": {"Proxy": "http://127.0.0.1:3031"}}}}}


def snapshot(**over):
    base = {"observed_at": time.time(), "hostname": "jq-xubuntu1",
            "tailscale": {"status": "ok", "dns_name": "jq-xubuntu1.bigeye-escalator.ts.net", "routes": node_agent.tailscale_routes(ROTOXY_SERVE)},
            "cloudflared": {"status": "ok", "processes": [{"pid": 4242, "args": "tunnel run --token=<redacted>", "config_file": None, "ingress": []},
                                                           {"pid": 4343, "args": "tunnel --config /etc/cloudflared/config.yml run", "config_file": "/etc/cloudflared/config.yml",
                                                            "ingress": [{"tunnel": "mcp-gw"}, {"hostname": "mcp.example.com"}, {"service": "http://127.0.0.1:3000"}]}]},
            "listeners": {"status": "ok", "items": [{"address": "127.0.0.1", "port": 2030, "pid": None, "process": None},
                                                      {"address": "127.0.0.1", "port": 3031, "pid": 77, "process": "python3"},
                                                      {"address": "0.0.0.0", "port": 443, "pid": None, "process": None}]},
            "bindings": [{"name": "ora1-system", "port": 3031, "exposure": "tailscale-serve", "https_port": 8443, "hostname": None,
                          "running": True, "tunnel_pid": None, "tailscale_owned": True}]}
    base.update(over)
    return base


class ClassificationTests(unittest.TestCase):
    def test_tailscale_routes_flatten_background_and_foreground_handlers(self):
        routes = node_agent.tailscale_routes(ROTOXY_SERVE)
        funnel = next(r for r in routes if r["host_port"].endswith(":443"))
        self.assertEqual(funnel, {"provider": "tailscale", "mode": "funnel", "host_port": "jq-xubuntu1.bigeye-escalator.ts.net:443",
                                  "path": "/", "target": "http://127.0.0.1:2030", "foreground": True})
        self.assertEqual(next(r for r in routes if r["path"] == "/mcp")["mode"], "serve")

    def test_rotoxy_is_external_managed_binding_is_managed_and_nothing_is_mutated(self):
        host = discovery.classify_host("local", snapshot(), gateway_port=2026, controller_ingress={"tailscale": {"enabled": False}, "cloudflare": {}})
        rotoxy = next(r for r in host["routes"] if r["path"] == "/" and r["port"] == 443)
        self.assertEqual(rotoxy["owner"], "external")
        self.assertEqual(rotoxy["hostname"], "jq-xubuntu1.bigeye-escalator.ts.net")
        self.assertEqual(rotoxy["target"], "http://127.0.0.1:2030")
        self.assertEqual(rotoxy["exposure"], "public")
        self.assertIn("never changes it", rotoxy["note"])
        managed = next(r for r in host["routes"] if r["path"] == "/mcp")
        self.assertEqual((managed["owner"], managed["owned_by"], managed["kind"], managed["exposure"]), ("managed", "ora1-system", "mcp-gateway", "tailnet"))
        cf = [r for r in host["routes"] if r["provider"] == "cloudflare"]
        self.assertEqual({r["owner"] for r in cf}, {"external"})
        self.assertEqual(next(r for r in cf if r["pid"] == 4343)["hostname"], "mcp.example.com")
        self.assertEqual(next(r for r in cf if r["pid"] == 4343)["kind"], "mcp-gateway")
        self.assertIn("configured in Cloudflare", next(r for r in cf if r["pid"] == 4242)["note"])
        self.assertNotIn("token=", str(host).replace("token=<redacted>", ""))
        listeners = {item["port"]: item for item in host["listeners"]}
        self.assertEqual(listeners[2030]["owner"], "unknown")
        self.assertEqual((listeners[3031]["owner"], listeners[3031]["owned_by"]), ("managed", "ora1-system"))
        self.assertTrue(listeners[3031]["mcp_candidate"])
        self.assertFalse(host["partial"])

    def test_partial_host_when_tailscale_is_missing(self):
        host = discovery.classify_host("ora5", snapshot(tailscale={"status": "unavailable", "routes": [], "dns_name": None, "error": "tailscale not available"}))
        self.assertTrue(host["partial"])
        self.assertEqual(host["tailscale"]["status"], "unavailable")
        self.assertEqual([r["provider"] for r in host["routes"]], ["cloudflare", "cloudflare"])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.calls: list[str] = []
        root = Path(tempfile.mkdtemp())

        def call(target, payload, timeout=25):
            self.calls.append(target)
            self.assertEqual(payload, {"action": "discover"})
            if target == "ora3":
                raise ValueError("Worker did not respond")
            if target == "ora7":
                raise ValueError("Unknown binding action")
            return snapshot(hostname=target)

        self.ctx = SimpleNamespace(cfg_dir=root, config=SimpleNamespace(data={"port": 2026, "ingress": {}, "discovery": {"excluded_targets": ["ora2", "ora4"]}}),
                                   workers=SimpleNamespace(call=call, targets=lambda: ["local", "ora1", "ora3", "ora7", "ora2"]), ingress=None,
                                   audit=SimpleNamespace(record=lambda *a, **k: None))
        self.svc = discovery.DiscoveryService(self.ctx, cache_seconds=30)

    def test_excluded_targets_are_never_contacted_and_failures_are_partial_not_absent(self):
        inv = self.svc.inventory()
        self.assertNotIn("ora2", self.calls)
        self.assertNotIn("ora4", self.calls)
        by = {h["target"]: h for h in inv["hosts"]}
        self.assertEqual(by["ora2"]["status"], "excluded")
        self.assertEqual(by["ora4"]["status"], "excluded")
        self.assertEqual(by["ora3"]["status"], "unreachable")
        self.assertFalse(by["ora3"]["last_known"])
        self.assertEqual(by["ora7"]["error"], discovery.OLD_WORKER)
        self.assertEqual(by["ora1"]["status"], "ok")
        self.assertEqual(inv["hosts"][0]["target"], "local")
        self.assertEqual(inv["summary"]["external"], 6)  # 3 external routes on each of local and ora1
        self.assertEqual(inv["summary"]["managed"], 2)
        self.assertEqual(inv["excluded_targets"], ["ora2", "ora4"])

    def test_cache_and_last_known_on_later_failure(self):
        self.svc.inventory()
        n = len(self.calls)
        self.svc.inventory()
        self.assertEqual(len(self.calls), n, "second call within the cache window performs no I/O")
        original = self.ctx.workers.call

        def flaky(target, payload, timeout=25):
            if target == "ora1":
                raise ValueError("ssh: connect timed out")
            return original(target, payload, timeout)

        self.ctx.workers.call = flaky
        inv = self.svc.refresh()
        ora1 = next(h for h in inv["hosts"] if h["target"] == "ora1")
        self.assertEqual(ora1["status"], "unreachable")
        self.assertTrue(ora1["last_known"])
        self.assertTrue(ora1["routes"], "last known routes are kept, not reported as none")
        self.assertNotIn("ssh:", str(inv))


class TopologyAndApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()
        cls.c.post("/admin/login", data={"credential": cls.server.AUTH.machine_token})
        cls.csrf = {"X-Loopback-CSRF": cls.c.get("/api/admin/session").json()["csrf"]}

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def test_api_and_topology_show_external_routes_without_probing(self):
        with patch.object(self.server.CTX.workers, "call", lambda target, payload, timeout=25: snapshot()):
            r = self.c.get("/api/admin/discovery")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(data["hosts"][0]["target"], "local")
            self.assertTrue(any(x["path"] == "/" and x["owner"] == "external" for x in data["hosts"][0]["routes"]))
            r = self.c.post("/api/admin/discovery/refresh", headers=self.csrf)
            self.assertEqual(r.status_code, 200, r.text)
        graph = self.server.CTX.topology.build()
        routes = [n for n in graph["nodes"] if n["id"].startswith("route:")]
        self.assertTrue(routes)
        rotoxy = next(n for n in routes if n["detail"]["path"] == "/")
        self.assertEqual(rotoxy["ownership"], "external")
        self.assertEqual(rotoxy["label"], "jq-xubuntu1.bigeye-escalator.ts.net/")
        self.assertEqual(rotoxy["detail"]["target"], "http://127.0.0.1:2030")
        self.assertTrue(any(e["from"] == "clients" and e["to"] == rotoxy["id"] for e in graph["edges"]))
        self.assertTrue(any(e["from"] == rotoxy["id"] and e["to"] == "gateway" for e in graph["edges"]))
        # The managed binding route belongs to a service node, not a second route node.
        self.assertFalse(any(n["detail"].get("owned_by") == "ora1-system" for n in routes))
        self.assertEqual(graph["legend"]["ownership"], ["managed", "external", "unknown"])

    def test_refresh_requires_csrf(self):
        self.assertEqual(self.c.post("/api/admin/discovery/refresh").status_code, 403)


if __name__ == "__main__":
    unittest.main()
