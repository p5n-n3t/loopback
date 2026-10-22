from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from helpers import call_tool, fresh_config_dir, load_server, rpc, tool_data, tool_text

from starlette.testclient import TestClient


class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()
        value, _ = cls.server.AUTH.create_api_token("tests", ["loopback:read", "loopback:write", "loopback:exec", "loopback:fleet"], ttl_seconds=3600, created_by="tests")
        cls.h = {"Authorization": "Bearer " + value}

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def test_tool_registry_and_catalog(self):
        expected = {"read_file", "write_file", "list_dir", "patch_file", "search_text", "tail_file", "git_info", "diagnostics",
                    "machine_info", "policy_status", "permissions", "connections", "execute", "job_start", "job_list", "job_output",
                    "job_stop", "terminal_start", "terminal_write", "terminal_read", "terminal_resize", "terminal_close",
                    "process_list", "process_kill", "node_list", "node_call", "node_read_file", "node_execute", "node_diagnostics",
                    "ssh_target_list", "ssh_execute", "docx_text", "docx_replace_text", "xlsx_read_range", "xlsx_write_range",
                    "pdf_text", "pdf_merge", "pdf_extract_pages", "browser_status", "desktop_status"}
        catalog = {t["name"]: t for t in self.server.tool_catalog()}
        self.assertTrue(expected.issubset(catalog), expected - set(catalog))
        self.assertEqual(catalog["read_file"]["scope"], "loopback:read")
        self.assertTrue(catalog["read_file"]["annotations"]["readOnlyHint"])
        self.assertEqual(catalog["execute"]["scope"], "loopback:exec")
        self.assertEqual(catalog["execute"]["approval"], "policy")
        self.assertTrue(catalog["execute"]["annotations"]["destructiveHint"])
        self.assertEqual(catalog["ssh_execute"]["target"], "remote")
        self.assertTrue(catalog["node_call"]["network"])
        self.assertFalse(catalog["browser_open"]["enabled"])  # opt-in capability
        listed = rpc(self.c, self.h, "tools/list").json()["result"]["tools"]
        self.assertEqual({t["name"] for t in listed}, set(catalog))
        for name in catalog:
            self.assertIn(name, self.server.TOOL_META, f"{name} lacks scope metadata")

    def test_healthz_has_no_secrets(self):
        body = self.c.get("/healthz").json()
        self.assertEqual(body["version"], self.server.VERSION)
        self.assertNotIn(self.server.AUTH.machine_token, json.dumps(body))

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX shell semantics')
    def test_execute_starts_in_home_and_is_sandboxed(self):
        data = tool_data(call_tool(self.c, self.h, "execute", {"command": "pwd"}))
        self.assertEqual(data["stdout"].strip(), self.server.CTX.identity.home)
        self.assertEqual(data["target"], "local")
        if self.server.CTX.sandbox.enforced():
            self.assertTrue(data["enforced"])
            home = Path(self.server.CTX.identity.home)
            (home / ".ssh").mkdir(exist_ok=True)
            (home / ".ssh" / "loopback-test-secret").write_text("nope")
            try:
                out = tool_data(call_tool(self.c, self.h, "execute", {"command": "cat ~/.ssh/loopback-test-secret 2>&1"}))
                self.assertNotIn("nope", out["stdout"])
            finally:
                (home / ".ssh" / "loopback-test-secret").unlink()

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX shell semantics')
    def test_policy_errors_reach_the_agent(self):
        text = tool_text(call_tool(self.c, self.h, "execute", {"command": "sudo whoami"}))
        self.assertIn("approval_required:", text)
        text = tool_text(call_tool(self.c, self.h, "read_file", {"path": "/etc/shadow"}))
        self.assertIn("policy_denied", text)
        text = tool_text(call_tool(self.c, self.h, "job_output", {"job_id": "missing"}))
        self.assertIn("not_found", text)

    def test_tool_policy_denylist(self):
        self.server.POLICY.save({"denied_tools": ["process_kill"]})
        try:
            r = call_tool(self.c, self.h, "process_kill", {"pid": 999999})
            self.assertEqual(r.json()["error"]["code"], -32001)
        finally:
            self.server.POLICY.save({"denied_tools": []})

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX shell semantics')
    def test_background_job_roundtrip_with_principal(self):
        job = tool_data(call_tool(self.c, self.h, "job_start", {"command": "printf loopback-job-ok"}))
        self.assertEqual(job["principal"], "tests")
        self.assertEqual(job["target"], "local")
        import time

        for _ in range(40):
            out = tool_data(call_tool(self.c, self.h, "job_output", {"job_id": job["id"]}))
            if "loopback-job-ok" in out["content"]:
                break
            time.sleep(0.05)
        self.assertIn("loopback-job-ok", out["content"])
        self.assertTrue(Path(self.cfg, "jobs", job["id"] + ".json").exists())

    def test_permissions_and_connections_tools(self):
        perms = tool_data(call_tool(self.c, self.h, "permissions", {"target": "local"}))
        self.assertEqual(perms["other_locations"], "denied")
        self.assertIn("sandbox", perms)
        conn = tool_data(call_tool(self.c, self.h, "connections"))
        self.assertIn("nodes", conn)
        for node in conn.get("nodes", []):
            self.assertNotIn(self.server.AUTH.machine_token, json.dumps(node))

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX shell semantics')
    def test_output_is_bounded(self):
        data = tool_data(call_tool(self.c, self.h, "execute", {"command": "head -c 3000000 /dev/zero | tr '\\0' 'a'"}))
        self.assertTrue(data["truncated"])
        self.assertLessEqual(len(data["stdout"].encode()), self.server.max_output() + 64)

    def test_dns_rebinding_protection(self):
        r = rpc(self.c, {**self.h, "Host": "evil.example"}, "tools/list")
        self.assertEqual(r.status_code, 421)


if __name__ == "__main__":
    unittest.main()
