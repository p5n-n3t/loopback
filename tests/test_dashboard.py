from __future__ import annotations

import unittest

from helpers import fresh_config_dir, load_server

from starlette.testclient import TestClient


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def setUp(self):
        self.c.cookies.clear()

    def test_shell_requires_session(self):
        r = self.c.get("/admin", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/admin/login"))

    def test_shell_has_csp_and_no_inline_script(self):
        self.c.post("/admin/login", data={"credential": self.server.AUTH.machine_token})
        r = self.c.get("/admin")
        self.assertEqual(r.status_code, 200)
        csp = r.headers.get("content-security-policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertNotIn("<script>", r.text)
        self.assertNotIn("onclick=", r.text)
        self.assertNotIn(self.server.AUTH.machine_token, r.text)
        for needle in ("Recent MCP activity", "Persistent terminals", "Nodes", "Topology", "Filesystem", "Tailscale", "Public tunnels"):
            self.assertIn(needle, r.text)
        self.c.get("/admin/logout")

    def test_static_assets_cached_and_traversal_refused(self):
        r = self.c.get("/admin/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])
        self.assertIn("max-age", r.headers.get("cache-control", ""))
        etag = r.headers.get("etag")
        self.assertTrue(etag)
        self.assertEqual(self.c.get("/admin/static/app.js", headers={"If-None-Match": etag}).status_code, 304)
        for bad in ("/admin/static/../server.py", "/admin/static/..%2fserver.py", "/admin/static/.hidden", "/admin/static/app.py", "/admin/static/%2e%2e/auth.py"):
            r = self.c.get(bad)
            self.assertNotIn("def tool_catalog", r.text, bad)
            self.assertNotIn("class AuthStore", r.text, bad)
            if "%2" in bad or ".hidden" in bad or bad.endswith(".py") and ".." not in bad:
                self.assertIn(r.status_code, {400, 404}, bad)
        total = sum(len(self.c.get(f"/admin/static/{n}").content) for n in ("index.html", "app.css", "app.js", "topology.js", "dotrix.js"))
        self.assertLess(total, 256 * 1024, "dashboard bundle must stay small")

    def test_login_page_mentions_enrollment_when_no_password(self):
        r = self.c.get("/admin/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("loopback auth enroll", r.text)
        self.assertNotIn("loopback_token", r.text)  # no credential in query strings anywhere


if __name__ == "__main__":
    unittest.main()
