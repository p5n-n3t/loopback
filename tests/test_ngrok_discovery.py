import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from helpers import RUNTIME  # noqa: F401
import node_agent
import discovery


class NgrokDiscoveryTests(unittest.TestCase):
    def test_disabled_by_default_and_remote_api_denied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(node_agent.ngrok_routes(root)["status"], "not_configured")
            (root / "discovery-providers.json").write_text(json.dumps({"ngrok_api": "http://remote.example:4040"}))
            self.assertEqual(node_agent.ngrok_routes(root)["status"], "invalid_configuration")

    def test_routes_are_read_only_and_credentials_never_returned(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "discovery-providers.json").write_text(json.dumps({"ngrok_api": "http://127.0.0.1:4040"}))
            real = httpx.Client
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "tunnels": [
                            {"public_url": "https://fixture.ngrok.app", "config": {"addr": "http://127.0.0.1:8000/mcp?secret=private"}},
                            {"public_url": "https://bad.ngrok.app", "config": {"addr": "http://user:private@localhost:9000"}},
                        ]
                    },
                )
            )
            with patch("httpx.Client", side_effect=lambda **kw: real(transport=transport, **kw)):
                result = node_agent.ngrok_routes(root)
            self.assertEqual(len(result["routes"]), 1)
            self.assertEqual(result["routes"][0]["target"], "http://127.0.0.1:8000/mcp")
            self.assertNotIn("private", str(result))

    def test_routes_enter_host_inventory_without_claiming_mcp_verification(self):
        host = discovery.classify_host("local", {"ngrok": {"status": "ok", "routes": [
            {"hostname": "fixture.ngrok.app", "port": None, "path": "/", "target": "http://127.0.0.1:8000/mcp"}]}})
        route = host["routes"][0]
        self.assertEqual(route["provider"], "ngrok")
        self.assertEqual(route["owner"], "external")
        self.assertEqual(route["kind"], "route")
        self.assertEqual(host["ngrok"]["status"], "ok")
