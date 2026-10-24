"""Fleet nodes, SSH targets, topology and ingress endpoints (no real remote hosts)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import call_tool, fresh_config_dir, load_server, tool_text

from starlette.testclient import TestClient


class FabricApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()
        cls.c.post("/admin/login", data={"credential": cls.server.AUTH.machine_token})
        cls.csrf = {"X-Loopback-CSRF": cls.c.get("/api/admin/session").json()["csrf"]}
        value, _ = cls.server.AUTH.create_api_token("tests", ["loopback:read", "loopback:fleet", "loopback:exec"], ttl_seconds=3600, created_by="tests")
        cls.h = {"Authorization": "Bearer " + value}

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def test_nodes_crud_never_returns_tokens(self):
        r = self.c.post("/api/admin/nodes", json={"name": "lab-vm", "url": "https://lab.example/mcp", "token": "super-secret-node-token",
                                                  "display_name": "Lab VM", "kind": "remote-vm", "group": "lab"}, headers=self.csrf)
        self.assertEqual(r.status_code, 201, r.text)
        listing = self.c.get("/api/admin/nodes").json()
        self.assertNotIn("super-secret-node-token", json.dumps(listing))
        self.assertTrue(any(n["name"] == "lab-vm" for n in (listing if isinstance(listing, list) else listing.get("nodes", []))))
        self.assertEqual(self.server.FLEET.get("lab-vm")[1], "super-secret-node-token")
        topo = self.c.get("/api/admin/topology").json()
        self.assertNotIn("super-secret-node-token", json.dumps(topo))
        node = next(n for n in topo["nodes"] if n["id"] == "node:lab-vm")
        self.assertEqual(node["type"], "loopback-node")
        edge = next(e for e in topo["edges"] if e["to"] == "node:lab-vm")
        self.assertEqual(edge["from"], "gateway")
        self.assertEqual(edge["status"], "untested")
        self.assertEqual(self.c.delete("/api/admin/nodes/lab-vm", headers=self.csrf).status_code, 200)
        self.assertNotIn("node:lab-vm", [n["id"] for n in self.c.get("/api/admin/topology").json()["nodes"]])

    def test_ssh_target_lifecycle_probe_and_classification(self):
        bad = self.c.post("/api/admin/ssh", json={"name": "local", "host": "127.0.0.1", "user": "alice"}, headers=self.csrf)
        self.assertEqual(bad.status_code, 400)
        bad = self.c.post("/api/admin/ssh", json={"name": "x", "host": "127.0.0.1", "user": "alice", "identity_file": "/nonexistent/key"}, headers=self.csrf)
        self.assertEqual(bad.status_code, 400)
        r = self.c.post("/api/admin/ssh", json={"name": "vm one", "host": "127.0.0.1", "user": "alice"}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)  # name validation
        r = self.c.post("/api/admin/ssh", json={"name": "vm1", "host": "127.0.0.1", "port": 1, "user": "alice", "kind": "remote-vm",
                                                "display_name": "Mattermost VM", "group": "oracle"}, headers=self.csrf)
        self.assertEqual(r.status_code, 201, r.text)
        topo = self.c.get("/api/admin/topology").json()
        node = next(n for n in topo["nodes"] if n["id"] == "ssh:vm1")
        self.assertEqual(node["type"], "remote-host")
        self.assertEqual(node["kind"], "remote-vm")
        self.assertEqual(node["display_name"], "Mattermost VM")
        edge = next(e for e in topo["edges"] if e["to"] == "ssh:vm1")
        self.assertEqual(edge["from"], "gateway")
        self.assertEqual(edge["transport"], "ssh")
        self.assertEqual(edge["status"], "untested")
        probe = self.c.post("/api/admin/ssh/vm1/probe", headers=self.csrf).json()
        self.assertIn(probe["status"], {"unreachable", "error", "auth_failed"})
        topo = self.c.get("/api/admin/topology").json()
        edge = next(e for e in topo["edges"] if e["to"] == "ssh:vm1")
        self.assertIn(edge["status"], {"unreachable", "error", "auth_failed"})
        self.assertIsNotNone(edge.get("checked_at"))
        # MCP side: targets are listed as remote, execution is policy-checked at the gateway first
        self.assertEqual(self.c.get("/api/admin/ssh").json()[0]["name"] if isinstance(self.c.get("/api/admin/ssh").json(), list) else "vm1", "vm1")
        text = tool_text(call_tool(self.c, self.h, "ssh_execute", {"target": "vm1", "command": "rm -rf /"}))
        self.assertIn("policy_denied", text)
        # per-target permissions do not leak to local
        r = self.c.put("/api/admin/permissions/vm1", json={"allowed_roots": [{"path": "/srv/app", "mode": "rw"}], "denied_roots": []}, headers=self.csrf)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/admin/permissions?target=vm1").json()["allowed_roots"][0]["path"], "/srv/app")
        self.assertNotEqual(self.c.get("/api/admin/permissions?target=local").json()["allowed_roots"][0]["path"], "/srv/app")
        self.assertEqual(self.c.delete("/api/admin/ssh/vm1", headers=self.csrf).status_code, 200)

    def test_topology_rename_persists(self):
        r = self.c.put("/api/admin/topology/nodes/gateway", json={"display_name": "Alice's desk", "group": "home"}, headers=self.csrf)
        self.assertEqual(r.status_code, 200, r.text)
        topo = self.c.get("/api/admin/topology").json()
        gw = next(n for n in topo["nodes"] if n["id"] == "gateway")
        self.assertEqual(gw["display_name"], "Alice's desk")
        self.assertEqual(gw["type"], "gateway")
        self.assertIn("os", gw["detail"])
        self.assertTrue(Path(self.cfg, "topology.json").exists())
        self.assertIn("legend", topo)

    def test_ingress_status_and_guarded_mutations(self):
        status = self.c.get("/api/admin/ingress").json()
        self.assertTrue(status["local"]["enabled"])
        self.assertIn("exposure", status["tailscale"])
        self.assertIn("exposure", status["cloudflare"])
        r = self.c.put("/api/admin/ingress/tailscale", json={"enabled": True, "mode": "funnel", "https_port": 443}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)  # needs confirm_public (and tailscale is not installed here)
        r = self.c.put("/api/admin/ingress/tailscale", json={"enabled": True, "mode": "funnel", "https_port": 443, "confirm_public": True}, headers=self.csrf)
        self.assertIn(r.status_code, {400, 409, 503})
        self.assertFalse(self.server.CTX.config.data["ingress"]["tailscale"]["enabled"])
        r = self.c.put("/api/admin/ingress/cloudflare", json={"enabled": True, "hostname": "not a host"}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.server.CTX.config.data["ingress"]["cloudflare"]["enabled"])
        diag = self.c.get("/api/admin/ingress/tailscale/diagnostics").json()
        checks = diag if isinstance(diag, list) else diag.get("checks", [])
        self.assertTrue(all({"name", "ok"} <= set(c) for c in checks))

    def test_validate_path_endpoint(self):
        home = self.server.CTX.identity.home
        r = self.c.post("/api/admin/permissions/validate-path", json={"path": "~/projects"}, headers=self.csrf).json()
        self.assertTrue(r["resolved"].startswith(home))
        self.assertTrue(r["within_home"])
        r = self.c.post("/api/admin/permissions/validate-path", json={"path": "/"}, headers=self.csrf).json()
        self.assertTrue(any("entire filesystem" in w for w in r["warnings"]))
        r = self.c.post("/api/admin/permissions/validate-path", json={"path": self.cfg}, headers=self.csrf).json()
        self.assertTrue(any("configuration directory" in w for w in r["warnings"]))


if __name__ == "__main__":
    unittest.main()
