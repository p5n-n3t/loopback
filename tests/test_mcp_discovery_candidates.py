import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from helpers import RUNTIME  # noqa: F401
import node_agent


class EndpointTests(unittest.TestCase):
    def test_declared_config_never_returns_headers_tokens_or_commands(self):
        with tempfile.TemporaryDirectory() as td, patch.object(node_agent.Path, "home", return_value=Path(td)):
            path = Path(td) / ".cursor/mcp.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "gateway": {"url": "https://mcp.example/mcp?secret=private", "headers": {"Authorization": "secret"}},
                            "stdio": {"command": "sh", "args": ["secret"]},
                            "userinfo": {"url": "http://user:secret@host/mcp"},
                        }
                    }
                )
            )
            result = node_agent.declared_mcp_endpoints()
            self.assertEqual(len(result), 2)
            self.assertNotIn("secret", str(result))
            self.assertEqual(result[0]["url"], "https://mcp.example/mcp")
            self.assertNotIn("command", result[1])

    def test_selected_endpoint_verification_states(self):
        for status, body, expected in [
            (401, {}, "auth_required"),
            (403, {}, "access_denied"),
            (302, {}, "redirect_requires_review"),
            (
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fixture"}}},
                "mcp_verified",
            ),
            (200, {"hello": "world"}, "unverified"),
        ]:
            with self.subTest(status=status, expected=expected), tempfile.TemporaryDirectory() as td:
                transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
                real_client = httpx.Client
                with (
                    patch.object(node_agent, "declared_mcp_endpoints", return_value=[{"url": "http://127.0.0.1:8000/mcp"}]),
                    patch("httpx.Client", side_effect=lambda **kw: real_client(transport=transport, **kw)),
                ):
                    r = node_agent.verify_mcp_candidate(Path(td), "http://127.0.0.1:8000/mcp")
                self.assertEqual(r["state"], expected)

    def test_undeclared_and_metadata_endpoint_denied(self):
        with tempfile.TemporaryDirectory() as td, patch.object(node_agent, "declared_mcp_endpoints", return_value=[]):
            with self.assertRaises(ValueError):
                node_agent.verify_mcp_candidate(Path(td), "http://localhost/mcp")
        with tempfile.TemporaryDirectory() as td, patch.object(node_agent, "declared_mcp_endpoints", return_value=[{"url": "http://169.254.169.254/mcp"}]):
            with self.assertRaises(ValueError):
                node_agent.verify_mcp_candidate(Path(td), "http://169.254.169.254/mcp")
