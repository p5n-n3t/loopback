"""Reconnect protocol diagnostics with an independent OAuth rate-limit budget."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
import sys
import shlex
from pathlib import Path
from unittest.mock import patch

import test_auth_flows as auth_tests
from helpers import call_tool, pkce_pair, rpc, tool_data, tool_text


class ReconnectContractTests(unittest.TestCase):
    setUpClass = classmethod(auth_tests.OAuthFlowTests.setUpClass.__func__)
    tearDownClass = classmethod(auth_tests.OAuthFlowTests.tearDownClass.__func__)
    register = auth_tests.OAuthFlowTests.register
    authorize = auth_tests.OAuthFlowTests.authorize
    code_from = auth_tests.OAuthFlowTests.code_from
    exchange = auth_tests.OAuthFlowTests.exchange
    grant = auth_tests.OAuthFlowTests.grant

    def test_catalog_declares_the_scope_that_each_tool_enforces(self):
        _, tokens = self.grant(scope="loopback:read")
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        tools = rpc(self.c, headers, "tools/list").json()["result"]["tools"]
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertEqual(tool["_meta"]["securitySchemes"], [{"type": "oauth2", "scopes": [
                    self.server.TOOL_META[tool["name"]]["scope"]]}])
        self.assertTrue(next(t for t in tools if t["name"] == "execute")["annotations"]["destructiveHint"])

    def test_discovery_does_not_force_a_read_only_grant(self):
        response = rpc(self.c, {}, "tools/list")
        challenge = response.headers["www-authenticate"]
        self.assertNotIn('scope="loopback:read"', challenge)
        advertised = re.search(r' scope="([^"]+)"', challenge)
        scopes = advertised[1] if advertised else " ".join(
            self.c.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"])
        _, tokens = self.grant(scope=scopes)
        self.assertEqual(set(tokens["scope"].split()), {"loopback:read", "loopback:write", "loopback:exec", "loopback:fleet"})
        self.assertNotIn("loopback:admin", tokens["scope"])

    def test_write_scope_challenge_and_reconsent_preserve_existing_grant(self):
        reg, tokens = self.grant(scope="loopback:read")
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(self.server.POLICY.data, {
            "allowed_roots": [{"path": directory, "mode": "rw"}], "denied_roots": []
        }):
            path = Path(directory) / "mathutil.py"
            arguments = {"path": str(path), "content": "def double(n):\n    return n * 2\n"}
            response = call_tool(self.c, headers, "write_file", arguments)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertTrue(result["isError"])
            challenge = result["_meta"]["mcp/www_authenticate"][0]
            for expected in ('error="insufficient_scope"', 'scope="loopback:write"', 'resource_metadata=', 'error_description='):
                self.assertIn(expected, challenge)
            self.assertFalse(path.exists())
            verifier, code_challenge = pkce_pair()
            scope = tokens["scope"] + " " + re.search(r'scope="([^"]+)"', challenge)[1]
            code = self.code_from(self.authorize(reg["client_id"], code_challenge, scope=scope))
            upgraded = self.exchange(reg["client_id"], code, verifier).json()
            write_headers = {"Authorization": "Bearer " + upgraded["access_token"]}
            written = call_tool(self.c, write_headers, "write_file", arguments)
            self.assertFalse(written.json()["result"].get("isError", False), written.text)
            self.assertEqual(path.read_text(), arguments["content"])
            self.assertIn("def double", tool_text(call_tool(self.c, write_headers, "read_file", {"path": str(path)})))
            perms = tool_data(call_tool(self.c, headers, "permissions"))
            self.assertEqual(perms["authentication"], {"kind": "oauth", "scopes": ["loopback:read"]})
            self.assertTrue(call_tool(self.c, headers, "write_file", arguments).json()["result"]["isError"])

    def test_policy_approval_is_not_an_oauth_challenge(self):
        _, tokens = self.grant(scope="loopback:read loopback:exec")
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        with patch.dict(self.server.POLICY.data, {"profile": "custom", "allow_shell": True,
                                                 "sandbox_required": False, "require_approval_patterns": ["approval-marker"]}):
            response = call_tool(self.c, headers, "execute", {"command": "echo approval-marker"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["result"]["isError"])
            self.assertIn("approval_required:", tool_text(response))
            self.assertNotIn("mcp/www_authenticate", json.dumps(response.json()))

    def test_coding_access_survives_refresh_and_policy_selection(self):
        reg, tokens = self.grant(scope="loopback:read loopback:write loopback:exec loopback:fleet")
        with tempfile.TemporaryDirectory() as directory, patch.dict(self.server.POLICY.data, {
            "profile": "custom", "allow_shell": True, "sandbox_required": False,
            "allowed_roots": [{"path": directory, "mode": "rw"}], "denied_roots": [],
            "allowed_tools": list(self.server.TOOL_META), "denied_tools": [],
        }):
            path = str(Path(directory) / "mathutil.py")
            headers = {"Authorization": "Bearer " + tokens["access_token"]}
            written = call_tool(self.c, headers, "write_file", {"path": path, "content": "def double(n):\n    return n * 2\n"})
            self.assertFalse(written.json()["result"].get("isError", False), written.text)
            form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                    "client_id": reg["client_id"], "resource": auth_tests.RESOURCE}
            response = self.c.post("/oauth/token", data=form)
            self.assertEqual(response.status_code, 200, response.text)
            refreshed = response.json()
            self.assertEqual(refreshed["scope"], tokens["scope"])
            headers = {"Authorization": "Bearer " + refreshed["access_token"]}
            self.assertIn("def double", tool_text(call_tool(self.c, headers, "read_file", {"path": path})))
            command = shlex.quote(sys.executable) + ' -c "from mathutil import double; assert double(4) == 8; print(8)"'
            result = tool_data(call_tool(self.c, headers, "execute", {"command": command, "cwd": directory}))
            self.assertEqual(result.get("returncode"), 0, result)
            self.assertEqual(result["stdout"].strip(), "8")

    def test_invalid_credentials_have_an_explicit_bearer_error(self):
        missing = rpc(self.c, {}, "tools/list")
        self.assertEqual(missing.status_code, 401)
        self.assertNotIn('error="invalid_token"', missing.headers["www-authenticate"])
        reg, tokens = self.grant()
        original_ttl = self.server.AUTH.access_ttl
        try:
            self.server.AUTH.access_ttl = -1
            expired, _ = self.server.AUTH.issue_access_token(client_id=reg["client_id"], resource=auth_tests.RESOURCE,
                                                            scopes={"loopback:read"})
        finally:
            self.server.AUTH.access_ttl = original_ttl
        self.server.AUTH.revoke_token(tokens["access_token"])
        for value in ("invalid-diagnostic-token", expired, tokens["access_token"]):
            with self.subTest(kind=value[:8]):
                response = rpc(self.c, {"Authorization": "Bearer " + value}, "tools/list")
                self.assertEqual(response.status_code, 401)
                self.assertIn('error="invalid_token"', response.headers["www-authenticate"])
                self.assertIn('resource_metadata=', response.headers["www-authenticate"])

    def test_catalog_is_equal_across_client_names_scopes_and_session_headers(self):
        catalogs = []
        for name, scope in (("ChatGPT", {"loopback:read"}),
                            ("Claude", {"loopback:read", "loopback:write", "loopback:exec", "loopback:fleet"})):
            reg = self.register(name)
            access, _ = self.server.AUTH.issue_access_token(client_id=reg["client_id"], resource=auth_tests.RESOURCE, scopes=scope)
            headers = {"Authorization": "Bearer " + access, "Mcp-Session-Id": "stale-diagnostic-session"}
            response = rpc(self.c, headers, "tools/list")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("mcp-session-id", response.headers)
            tools = response.json()["result"]["tools"]
            catalogs.append({tool["name"] for tool in tools})
            self.assertIn("write_file", catalogs[-1])
            self.assertIn("execute", catalogs[-1])
        self.assertEqual(catalogs[0], catalogs[1])
        self.assertGreater(len(catalogs[0]), 17)

    def test_refresh_events_record_outcomes_without_credentials(self):
        reg, tokens = self.grant()
        form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": reg["client_id"], "resource": auth_tests.RESOURCE}
        refreshed = self.c.post("/oauth/token", data=form)
        self.assertEqual(refreshed.status_code, 200)
        next_tokens = refreshed.json()
        self.server.AUTH.revoke_token(next_tokens["refresh_token"])
        rejected = self.c.post("/oauth/token", data={**form, "refresh_token": next_tokens["refresh_token"]})
        self.assertEqual(rejected.status_code, 400)
        events = [e for e in self.server.AUTH.recent_events() if e["principal"] == reg["client_id"]]
        self.assertTrue(any(e["event"] == "oauth_tokens_issued" and e["detail"].get("grant") == "refresh_token" for e in events))
        self.assertTrue(any(e["event"] == "oauth_refresh_failed" and e["detail"].get("error") == "invalid_grant" for e in events))
        encoded = json.dumps(events)
        for value in (*tokens.values(), *next_tokens.values()):
            if isinstance(value, str) and (value.startswith("lbr_") or value.count(".") == 2):
                self.assertNotIn(value, encoded)

    def test_rejected_refresh_scope_does_not_consume_the_refresh_token(self):
        for scope in ("loopback:read loopback:admin", "unknown:scope"):
            with self.subTest(scope=scope):
                reg, tokens = self.grant(scope="loopback:read")
                form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                        "client_id": reg["client_id"], "resource": auth_tests.RESOURCE}
                rejected = self.c.post("/oauth/token", data={**form, "scope": scope})
                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(rejected.json()["error"], "invalid_scope")
                retried = self.c.post("/oauth/token", data=form)
                self.assertEqual(retried.status_code, 200, retried.text)
                self.assertEqual(retried.json()["scope"], "loopback:read")
