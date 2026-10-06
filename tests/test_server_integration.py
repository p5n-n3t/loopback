from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class ServerImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_home = os.environ.get("HOME")
        cls.old_port = os.environ.get("LOOPBACK_LOCAL_PORT")
        os.environ["HOME"] = cls.tmp.name
        os.environ["LOOPBACK_LOCAL_PORT"] = "29999"
        cfg = Path(cls.tmp.name) / ".config" / "loopback"
        cfg.mkdir(parents=True)
        (cfg / "token").write_text("test-machine-token\n", encoding="utf-8")
        (cfg / "public_host").write_text("loopback.example.com\n", encoding="utf-8")
        runtime = Path(__file__).resolve().parents[1] / "runtime"
        sys.path.insert(0, str(runtime))
        cls.server = importlib.import_module("server")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("server", None)
        if cls.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls.old_home
        if cls.old_port is None:
            os.environ.pop("LOOPBACK_LOCAL_PORT", None)
        else:
            os.environ["LOOPBACK_LOCAL_PORT"] = cls.old_port
        cls.tmp.cleanup()

    def test_signed_oauth_access_token(self):
        token = self.server._issue_oauth_token(
            "access",
            "test-client",
            self.server._oauth_resource(),
            60,
        )
        claims = self.server._decode_oauth_token(
            token,
            "access",
            self.server._oauth_resource(),
        )
        self.assertEqual(claims["client_id"], "test-client")
        self.assertTrue(self.server._valid_mcp_credential("Bearer " + token))

    def test_static_bearer_compatibility(self):
        self.assertTrue(self.server._valid_mcp_credential("test-machine-token"))
        self.assertTrue(self.server._valid_mcp_credential("Bearer test-machine-token"))
        self.assertFalse(self.server._valid_mcp_credential("wrong"))

    def test_redirect_validation(self):
        self.assertTrue(self.server._redirect_uri_allowed("https://chatgpt.com/callback"))
        self.assertTrue(self.server._redirect_uri_allowed("http://127.0.0.1:17777/callback"))
        self.assertFalse(self.server._redirect_uri_allowed("http://example.com/callback"))
        self.assertFalse(self.server._redirect_uri_allowed("javascript:alert(1)"))

    def test_tool_registry_contains_v2_capabilities(self):
        # Importing server executes every MCP tool decorator; this test therefore
        # catches SDK/decorator API mismatches in addition to checking our names.
        expected = {
            "execute",
            "job_start",
            "job_output",
            "terminal_start",
            "terminal_write",
            "process_list",
            "process_kill",
            "policy_status",
            "node_list",
            "node_call",
            "node_read_file",
            "node_execute",
            "node_diagnostics",
        }
        # MCPServer exposes registered tools through its internal tool manager.
        manager = getattr(self.server.mcp, "_tool_manager", None)
        self.assertIsNotNone(manager)
        tools = getattr(manager, "_tools", {})
        self.assertTrue(expected.issubset(set(tools)), expected - set(tools))


if __name__ == "__main__":
    unittest.main()
