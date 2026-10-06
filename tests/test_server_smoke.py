from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient


class ServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name)
        (cfg / "token").write_text("test-loopback-secret\n", encoding="utf-8")
        (cfg / "public_host").write_text("loopback.example.com\n", encoding="utf-8")
        os.environ["LOOPBACK_CONFIG_DIR"] = str(cfg)
        os.environ["LOOPBACK_LOCAL_PORT"] = "2026"
        runtime = Path(__file__).resolve().parents[1] / "runtime"
        sys.path.insert(0, str(runtime))

        # policy/jobs modules may have been imported by test_runtime with a
        # different temporary config. Reload them before importing server.
        for name in ["policy", "audit", "jobs", "nodes", "metrics", "browser", "dashboard", "server"]:
            sys.modules.pop(name, None)
        cls.server = importlib.import_module("server")
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_health(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], "3.0.0")

    def test_protected_resource_metadata(self) -> None:
        response = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resource"], "https://loopback.example.com/mcp")

    def test_oauth_metadata(self) -> None:
        response = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("S256", body["code_challenge_methods_supported"])
        self.assertIn("refresh_token", body["grant_types_supported"])

    def test_dynamic_registration_and_authorize_form(self) -> None:
        registration = self.client.post(
            "/oauth/register",
            json={"redirect_uris": ["https://chat.example.com/callback"]},
        )
        self.assertEqual(registration.status_code, 201)
        client_id = registration.json()["client_id"]
        auth = self.client.get(
            "/oauth/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "https://chat.example.com/callback",
                "state": "abc",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
                "resource": "https://loopback.example.com/mcp",
            },
        )
        self.assertEqual(auth.status_code, 200)
        self.assertIn('method="post"', auth.text)
        self.assertNotIn("test-loopback-secret", auth.text)

    def test_mcp_requires_auth(self) -> None:
        response = self.client.post("/mcp", content=b"{}")
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata", response.headers.get("www-authenticate", ""))

    def test_admin_api_requires_session(self) -> None:
        response = self.client.get("/api/policy")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
