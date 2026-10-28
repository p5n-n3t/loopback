"""Tests for runtime/shield.py: floods, scanners, brute force, rotating IPs, proxy trust and admin routes."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: F401  (puts runtime/ on sys.path)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import shield as shield_mod
from shield import Shield, network_prefix, routes, shield_defaults

SMALL = {
    "mcp_per_minute": 60, "mcp_burst": 20,
    "auth_per_minute": 6, "auth_burst": 3,
    "default_per_minute": 60, "default_burst": 10,
    "rotating_per_minute": 6, "rotating_burst": 2,
    "flood_block_hits": 5, "scanner_block_hits": 3,
    "brute_force_failures": 10, "rotating_prefix_events": 4,
}


async def mcp(request: Request):
    return JSONResponse({"ok": True})


async def token(request: Request):
    form = await request.form()
    if form.get("ok") == "1":
        return JSONResponse({"access_token": "x"})
    return JSONResponse({"error": "invalid_client"}, status_code=401)


async def healthz(request: Request):
    return PlainTextResponse("ok")


async def other(request: Request):
    return PlainTextResponse("page")


class ShieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loopback-shield-")
        self.ctx = SimpleNamespace(cfg_dir=Path(self.tmp), config=SimpleNamespace(data={"shield": dict(SMALL)}), audit=None)
        inner = Starlette(routes=[
            Route("/mcp", mcp, methods=["GET", "POST"]),
            Route("/oauth/token", token, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/page", other, methods=["GET"]),
            *routes(self.ctx),
        ])
        self.app = Shield(inner, self.ctx)
        self.shield = self.ctx.shield

    def client(self, ip="203.0.113.7"):
        return TestClient(self.app, client=(ip, 1234))

    def blocked_ips(self):
        return [b["ip"] for b in self.shield.summary()["blocked_now"]]

    def test_defaults_and_private_db(self):
        self.assertTrue(shield_defaults()["enabled"])
        self.assertIs(self.ctx.shield, self.shield)
        mode = stat.S_IMODE(os.stat(Path(self.tmp) / "shield.db").st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(network_prefix("198.51.100.77"), "198.51.100.0/24")
        self.assertEqual(network_prefix("2001:db8:1:2::5"), "2001:db8:1::/48")
        self.assertIsNone(network_prefix("not-an-ip"))

    def test_healthz_never_throttled(self):
        c = self.client()
        for _ in range(100):
            self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(self.blocked_ips(), [])

    def test_mcp_burst_allowed(self):
        c = self.client()
        codes = [c.post("/mcp", json={}).status_code for _ in range(20)]
        self.assertEqual(set(codes), {200})

    def test_oauth_budget_trips_with_retry_after(self):
        c = self.client()
        codes = [c.post("/oauth/token", data={"ok": "1"}) for _ in range(4)]
        self.assertEqual([r.status_code for r in codes[:3]], [200, 200, 200])
        self.assertEqual(codes[3].status_code, 429)
        self.assertEqual(codes[3].json(), {"error": "rate_limited"})
        self.assertGreaterEqual(int(codes[3].headers["retry-after"]), 1)

    def test_repeated_floods_block(self):
        c = self.client()
        for _ in range(3 + 5):
            c.post("/oauth/token", data={"ok": "1"})
        self.assertIn("203.0.113.7", self.blocked_ips())
        r = c.get("/page")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "blocked"})
        actions = {(e["kind"], e["action"]) for e in self.shield.store.events()}
        self.assertIn(("flood", "throttled"), actions)
        self.assertIn(("flood", "blocked"), actions)
        self.assertEqual(self.client("203.0.113.8").get("/page").status_code, 200)
        # healthz stays reachable even for a blocked client
        self.assertEqual(c.get("/healthz").status_code, 200)

    def test_blocked_events_are_deduplicated(self):
        c = self.client()
        for _ in range(8):
            c.post("/oauth/token", data={"ok": "1"})
        for _ in range(10):
            c.get("/page")
        self.assertEqual(len(self.shield.store.events(kind="blocked")), 1)

    def test_scanner_probe_404_then_block(self):
        c = self.client()
        for _ in range(3):
            r = c.get("/wp-login.php")
            self.assertEqual(r.status_code, 404)
        self.assertIn("203.0.113.7", self.blocked_ips())
        self.assertEqual(c.get("/page").status_code, 403)
        kinds = [e["kind"] for e in self.shield.store.events()]
        self.assertIn("scanner", kinds)

    def test_exploit_query_rejected(self):
        c = self.client()
        r = c.get("/page", params={"q": "1 union select password from users"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.shield.store.events()[0]["kind"], "exploit")
        r = c.get("/page", params={"q": "<script>alert(1)</script>"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(c.get("/page", params={"redirect_uri": "https://example.com/cb?x=1"}).status_code, 200)

    def test_brute_force_blocks_and_success_resets(self):
        self.ctx.config.data["shield"]["auth_burst"] = 100
        self.ctx.config.data["shield"]["auth_per_minute"] = 1000
        self.ctx.config.data["shield"]["rotating_prefix_events"] = 1000
        c = self.client()
        for _ in range(9):
            self.assertEqual(c.post("/oauth/token", data={}).status_code, 401)
        self.assertEqual(c.post("/oauth/token", data={"ok": "1"}).status_code, 200)  # resets
        for _ in range(9):
            self.assertEqual(c.post("/oauth/token", data={}).status_code, 401)
        self.assertEqual(self.blocked_ips(), [])
        self.assertEqual(c.post("/oauth/token", data={}).status_code, 401)  # tenth consecutive failure
        self.assertIn("203.0.113.7", self.blocked_ips())
        self.assertEqual(c.post("/oauth/token", data={"ok": "1"}).status_code, 403)
        self.assertTrue(self.shield.store.events(kind="brute_force"))

    def test_forwarded_for_only_from_trusted_peer(self):
        trusted = self.client("127.0.0.1")
        for _ in range(3):
            trusted.get("/wp-login.php", headers={"X-Forwarded-For": "198.51.100.9, 10.1.1.1", "CF-IPCountry": "de"})
        self.assertIn("198.51.100.9", self.blocked_ips())
        self.assertNotIn("127.0.0.1", self.blocked_ips())
        event = self.shield.store.events(kind="scanner")[0]
        self.assertEqual(event["ip"], "198.51.100.9")
        self.assertEqual(event["country"], "DE")
        self.assertEqual(event["path"], "/wp-login.php")
        self.assertGreater(event["ts"], 0)
        # the trusted proxy itself keeps working for other clients
        self.assertEqual(trusted.get("/page", headers={"X-Forwarded-For": "198.51.100.10"}).status_code, 200)

        untrusted = self.client("10.0.0.5")
        untrusted.get("/wp-login.php", headers={"X-Forwarded-For": "192.0.2.1", "CF-IPCountry": "US"})
        event = self.shield.store.events(kind="scanner")[0]
        self.assertEqual(event["ip"], "10.0.0.5")
        self.assertIsNone(event["country"])

    def test_country_rejects_unknown_values(self):
        trusted = self.client("127.0.0.1")
        trusted.get("/.env", headers={"CF-Connecting-IP": "192.0.2.50", "CF-IPCountry": "XX"})
        trusted.get("/.env", headers={"CF-Connecting-IP": "192.0.2.51", "CF-IPCountry": "T1"})
        for event in self.shield.store.events(kind="scanner"):
            self.assertIsNone(event["country"])
        self.assertEqual({e["ip"] for e in self.shield.store.events(kind="scanner")}, {"192.0.2.50", "192.0.2.51"})

    def test_rotating_ip_detection_limits_prefix(self):
        for n in range(4):
            self.client(f"198.51.100.{n + 10}").get("/xmlrpc.php")
        events = self.shield.store.events(kind="rotating_ip")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["detail"]["prefix"], "198.51.100.0/24")
        self.assertIn("198.51.100.0/24", self.shield.summary()["rate_limited_prefixes"])
        fresh = [self.client(f"198.51.100.{n + 100}").get("/page").status_code for n in range(4)]
        self.assertEqual(fresh[:2], [200, 200])
        self.assertIn(429, fresh[2:])
        self.assertEqual(self.client("192.0.2.200").get("/page").status_code, 200)
        self.assertEqual(self.blocked_ips(), [])

    def test_unblock(self):
        c = self.client()
        for _ in range(3):
            c.get("/.git/config")
        self.assertIn("203.0.113.7", self.blocked_ips())
        self.assertTrue(self.shield.unblock("203.0.113.7"))
        self.assertEqual(c.get("/page").status_code, 200)
        self.assertFalse(self.shield.unblock("203.0.113.7"))

    def test_disabled_passes_everything(self):
        self.ctx.config.data["shield"]["enabled"] = False
        c = self.client()
        for _ in range(5):
            self.assertEqual(c.get("/wp-login.php").status_code, 404)  # the app's own 404, nothing recorded
        self.assertEqual(self.shield.store.events(), [])

    def test_internal_error_falls_through(self):
        audit = []
        self.ctx.audit = SimpleNamespace(record=lambda *a, **k: audit.append((a, k)))
        self.shield._decide = lambda scope: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertEqual(self.client().get("/page").status_code, 200)
        self.assertEqual(audit[0][0], ("shield", "error"))

    def test_admin_routes(self):
        c = self.client()
        for _ in range(3):
            c.get("/wp-admin/")
        admin = self.client("127.0.0.1")
        data = admin.get("/api/admin/shield").json()
        self.assertIn("203.0.113.7", [b["ip"] for b in data["summary"]["blocked_now"]])
        self.assertGreaterEqual(data["summary"]["events_24h"]["scanner"], 3)
        self.assertEqual(data["marker"]["state"], "alert")
        self.assertTrue(data["events"])
        self.assertIn("block_minutes", data["config"])
        r = admin.post("/api/admin/shield/unblock", json={"ip": "203.0.113.7"})
        self.assertEqual(r.json(), {"ip": "203.0.113.7", "unblocked": True})
        self.assertEqual(admin.post("/api/admin/shield/unblock", json={}).status_code, 400)
        self.assertIn("deleted", admin.post("/api/admin/shield/prune").json())

    def test_routes_404_without_shield(self):
        ctx = SimpleNamespace()
        app = Starlette(routes=routes(ctx))
        self.assertEqual(TestClient(app).get("/api/admin/shield").status_code, 404)

    def test_memory_is_bounded(self):
        self.ctx.config.data["shield"]["max_tracked_ips"] = 10
        for n in range(40):
            self.client(f"192.0.2.{n}").get("/page")
            self.client(f"192.0.2.{n}").get("/phpmyadmin")
        self.assertLessEqual(len(self.shield.buckets), 20)
        self.assertLessEqual(len(self.shield.scans.data), 10)
        self.assertLessEqual(len(self.shield.prefixes.data), 10)

    def test_prune_bounds_table(self):
        store = self.shield.store
        store.record("flood", "192.0.2.1", action="throttled")
        with store._connect() as db:
            db.execute("UPDATE shield_events SET ts = ts - 40 * 86400")
        store.record("flood", "192.0.2.2", action="throttled")
        self.assertEqual(store.prune(30), 1)
        self.assertEqual(len(store.events()), 1)
        self.assertEqual(shield_mod.PRUNE_EVERY > 0, True)


if __name__ == "__main__":
    unittest.main()
