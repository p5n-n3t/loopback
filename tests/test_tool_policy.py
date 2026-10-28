"""Tool policy persistence: authoritative server-side merge, bulk controls, tools/list visibility and tools/call refusal.

Regression for the confirmed defect where sequential checkbox changes discarded earlier denials
(the UI cached the PUT envelope as the policy object and the second write lost the first).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import fresh_config_dir, load_server, rpc

from starlette.testclient import TestClient


class ToolPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()
        cls.c.post("/admin/login", data={"credential": cls.server.AUTH.machine_token})
        cls.csrf = {"X-Loopback-CSRF": cls.c.get("/api/admin/session").json()["csrf"]}
        value, _ = cls.server.AUTH.create_api_token("tests", ["loopback:read", "loopback:exec"], ttl_seconds=3600, created_by="tests")
        cls.h = {"Authorization": "Bearer " + value}

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def setUp(self):
        self.c.put("/api/admin/policy", json={"allowed_tools": ["*"], "denied_tools": []}, headers=self.csrf)

    def usable(self) -> list[str]:
        """Tools that policy alone controls (browser/desktop tools stay gated by their category switch)."""
        return sorted(n for n, on in self.tools().items() if on)

    def tools(self) -> dict[str, bool]:
        return {t["name"]: t["enabled"] for t in self.c.get("/api/admin/tools").json()}

    def change(self, body):
        r = self.c.post("/api/admin/policy/tools", json=body, headers=self.csrf)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def policy(self):
        return self.c.get("/api/admin/policy").json()

    def listed(self) -> set[str]:
        r = rpc(self.c, self.h, "tools/list")
        self.assertEqual(r.status_code, 200, r.text)
        return {t["name"] for t in r.json()["result"]["tools"]}

    def test_sequential_disables_keep_earlier_denials(self):
        names = self.usable()[:3]
        self.change({"changes": {names[0]: False}})
        self.change({"changes": {names[1]: False}})
        res = self.change({"changes": {names[2]: False}})
        self.assertEqual(sorted(self.policy()["denied_tools"]), names)
        self.assertEqual(self.policy()["allowed_tools"], ["*"])
        enabled = {t["name"]: t["enabled"] for t in res["tools"]}
        self.assertFalse(any(enabled[n] for n in names))
        # Re-enabling the middle one leaves the other two denied.
        self.change({"changes": {names[1]: True}})
        self.assertEqual(sorted(self.policy()["denied_tools"]), [names[0], names[2]])

    def test_response_carries_policy_and_catalog(self):
        name = self.usable()[0]
        res = self.change({"changes": {name: False}})
        self.assertIn("policy", res)
        self.assertIn("denied_tools", res["policy"])
        self.assertIn("tools", res)
        self.assertIn("warnings", res)

    def test_deselect_all_writes_explicit_deny_all_and_hides_everything(self):
        self.change({"all": False})
        self.assertEqual(self.policy()["denied_tools"], ["*"])
        self.assertFalse(any(self.tools().values()))
        self.assertEqual(self.listed(), set())
        r = rpc(self.c, self.h, "tools/call", {"name": "machine_info", "arguments": {}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("denied by policy", r.json()["error"]["message"])

    def test_enable_one_after_deny_all_expands_marker(self):
        self.change({"all": False})
        self.change({"changes": {"machine_info": True}})
        pol = self.policy()
        self.assertNotIn("*", pol["denied_tools"])
        self.assertNotIn("machine_info", pol["denied_tools"])
        self.assertTrue(self.tools()["machine_info"])
        self.assertEqual(sum(self.tools().values()), 1)
        self.assertIn("machine_info", self.listed())
        r = rpc(self.c, self.h, "tools/call", {"name": "machine_info", "arguments": {}})
        self.assertNotIn("error", r.json(), r.text)

    def test_select_all_restores_wildcard(self):
        self.change({"all": False})
        self.change({"all": True})
        self.assertEqual(self.policy()["denied_tools"], [])
        self.assertEqual(self.policy()["allowed_tools"], ["*"])
        self.assertTrue(all(t for n, t in self.tools().items() if n not in self.server.TOOL_META or self.server.TOOL_META[n]["category"] not in ("browser", "desktop")))

    def test_filtered_bulk_only_touches_named_tools(self):
        names = self.usable()
        subset = names[:2]
        self.change({"all": False, "names": subset})
        self.assertEqual(sorted(self.policy()["denied_tools"]), subset)
        self.assertTrue(self.tools()[names[3]])
        # Disabling the remaining usable tools by name denies exactly those names; category-gated tools
        # (browser/desktop) were never named, so the explicit deny-all marker is reserved for "all".
        self.change({"all": False, "names": names[2:]})
        self.assertEqual(sorted(self.policy()["denied_tools"]), names)
        self.assertEqual(self.listed(), set())

    def test_explicit_allow_list_is_preserved(self):
        names = self.usable()
        keep = names[:2]
        self.c.put("/api/admin/policy", json={"allowed_tools": keep, "denied_tools": []}, headers=self.csrf)
        self.change({"changes": {names[2]: True}})
        self.assertEqual(sorted(self.policy()["allowed_tools"]), sorted(keep + [names[2]]))
        self.change({"changes": {keep[0]: False}})
        pol = self.policy()
        self.assertNotIn(keep[0], pol["allowed_tools"])
        self.assertIn(keep[0], pol["denied_tools"])

    def test_unknown_or_malformed_requests_are_rejected(self):
        r = self.c.post("/api/admin/policy/tools", json={"changes": {"no_such_tool": True}}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no_such_tool", r.text)
        r = self.c.post("/api/admin/policy/tools", json={"changes": {"machine_info": "yes"}}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/admin/policy/tools", json={}, headers=self.csrf)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/admin/policy/tools", json={"changes": {"machine_info": False}})
        self.assertEqual(r.status_code, 403)  # CSRF

    def test_policy_survives_reload_from_disk(self):
        names = self.usable()[:2]
        self.change({"changes": {names[0]: False, names[1]: False}})
        on_disk = json.loads((Path(self.cfg) / "policy.json").read_text())
        self.assertEqual(sorted(on_disk["denied_tools"]), names)
        self.assertEqual(on_disk["allowed_tools"], ["*"])

    def test_browser_tools_warn_when_category_gate_is_off(self):
        browser = [t["name"] for t in self.c.get("/api/admin/tools").json() if t["category"] == "browser" and t["name"] != "browser_status"]
        if not browser:
            self.skipTest("no browser tools registered")
        res = self.change({"changes": {browser[0]: True}})
        self.assertTrue(any(browser[0] in w for w in res["warnings"]))
        self.assertFalse(self.tools()[browser[0]])


if __name__ == "__main__":
    unittest.main()
