"""Reconnect protocol diagnostics with an independent OAuth rate-limit budget."""
from __future__ import annotations

import json
import unittest

import test_auth_flows as auth_tests
from helpers import rpc


class ReconnectContractTests(unittest.TestCase):
    setUpClass = classmethod(auth_tests.OAuthFlowTests.setUpClass.__func__)
    tearDownClass = classmethod(auth_tests.OAuthFlowTests.tearDownClass.__func__)
    register = auth_tests.OAuthFlowTests.register
    authorize = auth_tests.OAuthFlowTests.authorize
    code_from = auth_tests.OAuthFlowTests.code_from
    exchange = auth_tests.OAuthFlowTests.exchange
    grant = auth_tests.OAuthFlowTests.grant

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
