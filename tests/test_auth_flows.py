"""OAuth 2.1 authorization server, API tokens, sessions and enforcement at the MCP boundary."""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from helpers import call_tool, consent_fields, fresh_config_dir, load_server, pkce_pair, rpc, tool_data

from starlette.testclient import TestClient

RESOURCE = "http://127.0.0.1:2026/mcp"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


class OAuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = fresh_config_dir()
        cls.server = load_server()
        cls.client_cm = TestClient(cls.server.app, base_url="http://127.0.0.1:2026")
        cls.c = cls.client_cm.__enter__()
        cls.machine_token = cls.server.AUTH.machine_token

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    # ---------------------------------------------------------------- helpers
    def register(self, name="ChatGPT", redirect=REDIRECT):
        r = self.c.post("/oauth/register", json={"redirect_uris": [redirect], "client_name": name})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def authorize(self, client_id, challenge, *, scope="loopback:read loopback:exec", redirect=REDIRECT, credential=None, resource=RESOURCE, state="s1"):
        r = self.c.get("/oauth/authorize", params={
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "resource": resource, "scope": scope, "state": state}, follow_redirects=False)
        if r.status_code != 200:
            return r
        rid, csrf = consent_fields(r.text)
        return self.c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "approve",
                                                     "credential": credential or self.machine_token}, follow_redirects=False)

    def code_from(self, response) -> str:
        self.assertEqual(response.status_code, 302, response.text)
        loc = response.headers["location"]
        self.assertIn("iss=", loc)
        import urllib.parse as up

        q = up.parse_qs(up.urlparse(loc).query)
        self.assertNotIn("error", q, loc)
        return q["code"][0]

    def exchange(self, client_id, code, verifier, *, redirect=REDIRECT, resource=RESOURCE):
        return self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                                 "client_id": client_id, "redirect_uri": redirect, "resource": resource})

    def grant(self, scope="loopback:read loopback:exec"):
        reg = self.register()
        verifier, challenge = pkce_pair()
        code = self.code_from(self.authorize(reg["client_id"], challenge, scope=scope))
        tok = self.exchange(reg["client_id"], code, verifier).json()
        self.assertIn("access_token", tok, tok)
        return reg, tok

    # ------------------------------------------------------------------ tests
    def test_metadata_documents(self):
        prm = self.c.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(prm["resource"], RESOURCE)
        self.assertEqual(prm["bearer_methods_supported"], ["header"])
        meta = self.c.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(meta["authorization_response_iss_parameter_supported"])
        self.assertIn("revocation_endpoint", meta)
        self.assertEqual(self.c.get("/.well-known/openid-configuration").json()["issuer"], meta["issuer"])

    def test_unauthenticated_mcp_gets_resource_metadata_challenge(self):
        r = rpc(self.c, {}, "tools/list")
        self.assertEqual(r.status_code, 401)
        self.assertIn('resource_metadata="http://127.0.0.1:2026/.well-known/oauth-protected-resource/mcp"', r.headers["www-authenticate"])

    def test_registration_rejects_bad_redirects(self):
        for bad in ("http://example.com/cb", "javascript:alert(1)", "https://x.example/cb#frag", "https://user:pw@x.example/cb"):
            r = self.c.post("/oauth/register", json={"redirect_uris": [bad]})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.c.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1:1234/cb"]}).status_code, 201)

    def test_full_code_flow_and_mcp_call(self):
        reg, tok = self.grant()
        self.assertEqual(tok["token_type"], "Bearer")
        self.assertEqual(set(tok["scope"].split()), {"loopback:read", "loopback:exec"})
        headers = {"Authorization": "Bearer " + tok["access_token"]}
        init = rpc(self.c, headers, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(init.status_code, 200)
        self.assertEqual(init.json()["result"]["serverInfo"]["name"], "Loopback")
        info = tool_data(call_tool(self.c, headers, "machine_info"))
        self.assertEqual(info["home"], self.server.CTX.identity.home)
        self.assertEqual(info["initial_cwd"], self.server.CTX.identity.home)

    def test_pkce_mismatch_and_code_single_use(self):
        reg = self.register()
        verifier, challenge = pkce_pair()
        code = self.code_from(self.authorize(reg["client_id"], challenge))
        bad = self.exchange(reg["client_id"], code, "wrong-verifier-" * 4)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["error"], "invalid_grant")
        again = self.exchange(reg["client_id"], code, verifier)
        self.assertEqual(again.json()["error"], "invalid_grant")

    def test_code_bound_to_client_and_redirect(self):
        reg_a = self.register("A")
        reg_b = self.register("B", redirect="https://b.example/cb")
        verifier, challenge = pkce_pair()
        code = self.code_from(self.authorize(reg_a["client_id"], challenge))
        stolen = self.exchange(reg_b["client_id"], code, verifier, redirect="https://b.example/cb")
        self.assertEqual(stolen.json()["error"], "invalid_grant")

    def test_unregistered_redirect_is_not_followed(self):
        reg = self.register()
        _, challenge = pkce_pair()
        r = self.authorize(reg["client_id"], challenge, redirect="https://evil.example/cb")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("location", r.headers)

    def test_wrong_resource_is_rejected(self):
        reg = self.register()
        _, challenge = pkce_pair()
        r = self.authorize(reg["client_id"], challenge, resource="https://other.example/mcp")
        self.assertEqual(r.status_code, 302)
        self.assertIn("error=invalid_target", r.headers["location"])

    def test_admin_scope_cannot_be_requested(self):
        reg = self.register()
        _, challenge = pkce_pair()
        r = self.authorize(reg["client_id"], challenge, scope="loopback:admin")
        self.assertIn("error=invalid_scope", r.headers["location"])

    def test_consent_requires_valid_credential_and_csrf(self):
        reg = self.register()
        _, challenge = pkce_pair()
        r = self.c.get("/oauth/authorize", params={"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
                                                   "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE})
        rid, csrf = consent_fields(r.text)
        bad = self.c.post("/oauth/authorize", data={"request_id": rid, "csrf": "nope", "decision": "approve", "credential": self.machine_token}, follow_redirects=False)
        self.assertEqual(bad.status_code, 400)
        r = self.c.get("/oauth/authorize", params={"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
                                                   "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE})
        rid, csrf = consent_fields(r.text)
        bad = self.c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "approve", "credential": "wrong"}, follow_redirects=False)
        self.assertEqual(bad.status_code, 401)
        deny = self.c.get("/oauth/authorize", params={"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
                                                      "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE, "state": "d"})
        rid, csrf = consent_fields(deny.text)
        r = self.c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "deny"}, follow_redirects=False)
        self.assertIn("error=access_denied", r.headers["location"])
        self.assertIn("state=d", r.headers["location"])

    def test_refresh_rotation_and_reuse_detection(self):
        reg, tok = self.grant()
        first = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": reg["client_id"], "resource": RESOURCE}).json()
        self.assertIn("access_token", first)
        self.assertNotEqual(first["refresh_token"], tok["refresh_token"])
        reuse = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": reg["client_id"], "resource": RESOURCE})
        self.assertEqual(reuse.status_code, 400)
        self.assertIn("reuse", reuse.json()["error_description"])
        # the whole family, including the latest access token, is dead
        r = rpc(self.c, {"Authorization": "Bearer " + first["access_token"]}, "tools/list")
        self.assertEqual(r.status_code, 401)
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": reg["client_id"], "resource": RESOURCE})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_refresh_cannot_widen_scope_or_change_client(self):
        reg, tok = self.grant(scope="loopback:read")
        widen = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": reg["client_id"], "resource": RESOURCE, "scope": "loopback:read loopback:exec"})
        self.assertEqual(widen.json()["error"], "invalid_scope")
        other = self.register("Other")
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": other["client_id"], "resource": RESOURCE})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_revocation_endpoint(self):
        reg, tok = self.grant()
        self.assertEqual(self.c.post("/oauth/revoke", data={"token": tok["refresh_token"], "token_type_hint": "refresh_token"}).status_code, 200)
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + tok["access_token"]}, "tools/list").status_code, 401)

    def test_scope_enforced_at_tool_boundary(self):
        reg, tok = self.grant(scope="loopback:read")
        headers = {"Authorization": "Bearer " + tok["access_token"]}
        self.assertEqual(call_tool(self.c, headers, "list_dir", {"path": "~"}).status_code, 200)
        r = call_tool(self.c, headers, "execute", {"command": "true"})
        self.assertEqual(r.status_code, 403)
        self.assertIn('error="insufficient_scope"', r.headers["www-authenticate"])
        self.assertIn('scope="loopback:exec"', r.headers["www-authenticate"])

    def test_legacy_machine_token_rejected_on_fresh_install(self):
        r = rpc(self.c, {"Authorization": "Bearer " + self.machine_token}, "tools/list")
        self.assertEqual(r.status_code, 401)

    def test_legacy_token_toggle_from_dashboard(self):
        self.c.post("/admin/login", data={"credential": self.machine_token})
        csrf = self.c.get("/api/admin/session").json()["csrf"]
        r = self.c.post("/api/admin/auth/legacy-token", json={"enabled": True}, headers={"X-Loopback-CSRF": csrf})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + self.machine_token}, "tools/list").status_code, 200)
        self.c.post("/api/admin/auth/legacy-token", json={"enabled": False}, headers={"X-Loopback-CSRF": csrf})
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + self.machine_token}, "tools/list").status_code, 401)
        self.c.get("/admin/logout")

    def test_api_token_lifecycle(self):
        self.c.post("/admin/login", data={"credential": self.machine_token})
        csrf = self.c.get("/api/admin/session").json()["csrf"]
        self.assertEqual(self.c.post("/api/admin/auth/tokens", json={"name": "x", "scopes": ["loopback:read"]}).status_code, 403)  # no CSRF
        created = self.c.post("/api/admin/auth/tokens", json={"name": "Perplexity", "scopes": ["loopback:read"], "ttl_seconds": 3600},
                              headers={"X-Loopback-CSRF": csrf})
        self.assertEqual(created.status_code, 201, created.text)
        token = created.json()["token"]
        self.assertTrue(token.startswith("lbk_"))
        listing = self.c.get("/api/admin/auth").json()
        self.assertTrue(any(t["name"] == "Perplexity" for t in listing["api_tokens"]))
        self.assertNotIn(token, json.dumps(listing))
        headers = {"Authorization": "Bearer " + token}
        self.assertEqual(call_tool(self.c, headers, "list_dir", {"path": "~"}).status_code, 200)
        self.assertEqual(call_tool(self.c, headers, "execute", {"command": "true"}).status_code, 403)
        tid = created.json()["record"]["id"]
        self.assertEqual(self.c.delete(f"/api/admin/auth/tokens/{tid}", headers={"X-Loopback-CSRF": csrf}).status_code, 200)
        self.assertEqual(call_tool(self.c, headers, "list_dir", {"path": "~"}).status_code, 401)
        self.c.get("/admin/logout")

    def test_expired_api_token(self):
        value, record = self.server.AUTH.create_api_token("short", ["loopback:read"], ttl_seconds=1, created_by="test")
        time.sleep(1.2)
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + value}, "tools/list").status_code, 401)

    def test_token_from_other_instance_audience_rejected(self):
        import jwt

        claims = {"iss": "https://other.example", "sub": "x", "aud": "https://other.example/mcp", "iat": int(time.time()),
                  "exp": int(time.time()) + 60, "jti": "abc", "typ": "access", "client_id": "x", "scope": "loopback"}
        forged = jwt.encode(claims, self.server.AUTH.signing_key, algorithm="HS256")
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + forged}, "tools/list").status_code, 401)

    def test_dashboard_session_and_csrf(self):
        self.assertEqual(self.c.get("/api/admin/overview").status_code, 401)
        r = self.c.post("/admin/login", data={"credential": "wrong"})
        self.assertEqual(r.status_code, 401)
        r = self.c.post("/admin/login", data={"credential": self.machine_token}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        session = self.c.get("/api/admin/session").json()
        self.assertTrue(session["authenticated"])
        self.assertEqual(self.c.put("/api/admin/policy", json={"profile": "development"}).status_code, 403)
        ok = self.c.put("/api/admin/policy", json={"profile": "development"}, headers={"X-Loopback-CSRF": session["csrf"]})
        self.assertEqual(ok.status_code, 200)
        self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={"X-Loopback-CSRF": session["csrf"]})
        self.c.get("/admin/logout")
        self.assertEqual(self.c.get("/api/admin/overview").status_code, 401)

    def test_csrf_origin_check_is_same_origin_not_allowlist(self):
        self.c.post("/admin/login", data={"credential": self.machine_token})
        csrf = self.c.get("/api/admin/session").json()["csrf"]
        headers = {"X-Loopback-CSRF": csrf}
        # Reached through a reverse proxy / tunnel hostname that is not a configured public host: allowed when Origin == Host.
        r = self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={**headers, "Origin": "https://proxy.example.net", "Host": "proxy.example.net"})
        self.assertEqual(r.status_code, 200, r.text)
        # Behind a proxy that rewrites Host to a different name: the browser's Sec-Fetch-Site vouches for same-origin.
        r = self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={**headers, "Origin": "https://tunnel.example.net", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 200, r.text)
        # A browser-marked cross-site request is refused even with a (somehow obtained) token.
        r = self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={**headers, "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        # An unexplained Origin mismatch with a valid token is allowed (proxies rewrite Host) but audited.
        r = self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={**headers, "Origin": "https://odd-proxy.example"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.server.AUTH.recent_events(1)[0]["event"], "csrf_origin_mismatch")
        # Without the CSRF header nothing passes, whatever the Origin says.
        r = self.c.put("/api/admin/policy", json={"profile": "restricted"}, headers={"Origin": "http://127.0.0.1:2026", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 403)
        self.c.get("/admin/logout")

    def test_enrollment_sets_password(self):
        code = self.server.AUTH.create_enrollment(ttl=60)
        r = self.c.post("/admin/enroll", data={"code": "bogus", "password": "correct horse battery", "password2": "correct horse battery"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/admin/enroll", data={"code": code, "password": "correct horse battery", "password2": "correct horse battery"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(self.server.AUTH.has_password())
        self.assertEqual(self.c.post("/admin/enroll", data={"code": code, "password": "another password!", "password2": "another password!"}).status_code, 400)
        self.c.get("/admin/logout")
        self.assertEqual(self.c.post("/admin/login", data={"credential": "correct horse battery"}, follow_redirects=False).status_code, 303)
        self.c.get("/admin/logout")

    def test_login_lockout(self):
        limiter = self.server.AUTH.limiter
        for _ in range(10):
            self.c.post("/admin/login", data={"credential": "bad"})
        r = self.c.post("/admin/login", data={"credential": self.machine_token})
        self.assertEqual(r.status_code, 429)
        limiter._locks.clear()  # noqa: SLF001 - reset for the remaining tests

    def test_secrets_never_in_state_files(self):
        cfg = Path(self.cfg)
        for name in ("config.json", "policy.json"):
            text = (cfg / name).read_text() if (cfg / name).exists() else ""
            self.assertNotIn(self.machine_token, text)
        self.assertEqual(oct((cfg / "token").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct((cfg / "oauth-signing.key").stat().st_mode & 0o777), "0o600")

    def test_audit_records_principal_not_secret(self):
        reg, tok = self.grant(scope="loopback:read")
        call_tool(self.c, {"Authorization": "Bearer " + tok["access_token"]}, "machine_info")
        recent = self.server.AUDIT.recent(1)[0]
        self.assertEqual(recent["tool"], "machine_info")
        self.assertEqual(recent["principal"], "ChatGPT")
        self.assertNotIn(tok["access_token"], json.dumps(recent))


class LegacyInstallTests(unittest.TestCase):
    """A v3 config.env install keeps its clients working after upgrade."""

    def test_v3_layout_is_migrated_and_legacy_token_kept(self):
        cfg = fresh_config_dir()
        Path(cfg, "config.json").unlink()  # a v3 install has no config.json; config.env must be migrated
        Path(cfg, "token").write_text("legacy-machine-token-value\n")
        Path(cfg, "public_host").write_text("loopback.example.com\n")
        Path(cfg, "config.env").write_text("LOOPBACK_PORT=2026\nLOOPBACK_PUBLIC_HOST=loopback.example.com\nLOOPBACK_TIMEOUT=120\n"
                                           "LOOPBACK_MAX_OUTPUT_BYTES=1048576\nLOOPBACK_STARTUP_TIMEOUT=15\nLOOPBACK_ACCESS_MODE=trusted\n"
                                           "LOOPBACK_INGRESS=cloudflare\nLOOPBACK_CLOUDFLARE_TUNNEL_ID=11111111-2222\nLOOPBACK_CLOUDFLARE_TOKEN_FILE=\nLOOPBACK_TAILSCALE_HTTPS_PORT=443\n")
        Path(cfg, "nodes.json").write_text(json.dumps([{"name": "homelab", "url": "https://homelab.example.net/mcp", "enabled": True, "description": ""}]))
        Path(cfg, "nodes.secrets.json").write_text(json.dumps({"homelab": "node-secret"}))
        server = load_server()
        try:
            cfg_data = json.loads(Path(cfg, "config.json").read_text())
            self.assertTrue(cfg_data["ingress"]["cloudflare"]["enabled"])
            self.assertEqual(cfg_data["ingress"]["cloudflare"]["hostname"], "loopback.example.com")
            self.assertTrue(cfg_data["auth"]["legacy_machine_token_mcp"])
            self.assertEqual(server.POLICY.profile, "admin")  # trusted -> admin alias
            self.assertEqual(server.FLEET.get("homelab")[1], "node-secret")
            self.assertIn("https://loopback.example.com/mcp", server.AUTH.resource_uris)
            with TestClient(server.app, base_url="http://127.0.0.1:2026") as c:
                r = rpc(c, {"Authorization": "Bearer legacy-machine-token-value"}, "tools/list")
                self.assertEqual(r.status_code, 200)
                ov_login = c.post("/admin/login", data={"credential": "legacy-machine-token-value"}, follow_redirects=False)
                self.assertEqual(ov_login.status_code, 303)
                alerts = [a["code"] for a in c.get("/api/admin/overview").json()["alerts"]]
                self.assertIn("legacy_token", alerts)
        finally:
            import sys

            sys.modules.pop("server", None)


class ConnectorSetupTests(unittest.TestCase):
    setUpClass = classmethod(OAuthFlowTests.setUpClass.__func__)
    tearDownClass = classmethod(OAuthFlowTests.tearDownClass.__func__)
    register = OAuthFlowTests.register
    authorize = OAuthFlowTests.authorize
    code_from = OAuthFlowTests.code_from
    exchange = OAuthFlowTests.exchange

    def test_connector_setup_key_is_consent_only_and_rotation_preserves_grants(self):
        key = self.server.AUTH.rotate_connector_key()
        self.assertTrue(self.server.AUTH.verify_connector_key(key))
        self.assertEqual(self.c.post("/admin/login", data={"credential": key}, follow_redirects=False).status_code, 401)
        self.assertEqual(rpc(self.c, {"Authorization": "Bearer " + key}, "tools/list").status_code, 401)
        issued = []
        for _ in range(2):
            reg = self.register()
            verifier, challenge = pkce_pair()
            code = self.code_from(self.authorize(reg["client_id"], challenge, credential=key))
            response = self.exchange(reg["client_id"], code, verifier)
            self.assertEqual(response.status_code, 200, response.text)
            issued.append(response.json())
        new = self.server.AUTH.rotate_connector_key()
        self.assertNotEqual(key, new)
        self.assertFalse(self.server.AUTH.verify_connector_key(key))
        self.assertTrue(self.server.AUTH.verify_connector_key(new))
        reg = self.register()
        _, challenge = pkce_pair()
        self.assertEqual(self.authorize(reg["client_id"], challenge, credential=key).status_code, 401)
        for token in issued:
            self.server.AUTH.verify_access_token(token["access_token"])
        self.assertIsNone(self.server.AUTH.ensure_connector_key())

    def test_dashboard_account_and_key_endpoints_require_csrf_and_reauthentication(self):
        self.c.post("/admin/login", data={"credential": self.machine_token}, follow_redirects=False)
        csrf = self.c.get("/api/admin/session").json()["csrf"]
        headers = {"X-Loopback-CSRF": csrf}
        self.assertEqual(self.c.post("/api/admin/auth/connector-key", json={"current": self.machine_token}).status_code, 403)
        self.assertEqual(self.c.post("/api/admin/auth/connector-key", json={"current": "wrong"}, headers=headers).status_code, 403)
        r = self.c.post("/api/admin/auth/connector-key", json={"current": self.machine_token}, headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.server.AUTH.verify_connector_key(r.json()["key"]))
        self.assertFalse(r.json()["grants_revoked"])
        self.assertNotIn(r.json()["key"], self.c.get("/api/admin/auth").text)
        r = self.c.post("/api/admin/auth/account", json={"current": self.machine_token, "username": "owner", "password": "new dashboard password"}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/admin/session").status_code, 401)
        self.assertEqual(self.c.post("/admin/login", data={"username": "wrong", "credential": "new dashboard password"}, follow_redirects=False).status_code, 401)
        self.assertEqual(self.c.post("/admin/login", data={"username": "owner", "credential": "new dashboard password"}, follow_redirects=False).status_code, 303)


class DashboardAccountTests(unittest.TestCase):
    def test_old_database_adds_username_without_resetting_password(self):
        import sqlite3
        import tempfile
        from auth import AuthStore, hash_password
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / "auth.db") as db:
                db.execute("CREATE TABLE admin_credential (id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
                db.execute("INSERT INTO admin_credential VALUES(1, ?, 1, 1)", (hash_password("old account password"),))
            store = AuthStore(Path(directory), resource_uris=[RESOURCE], base_urls=[RESOURCE[:-4]])
            self.assertIsNone(store.ensure_dashboard_account())
            self.assertIsNone(store.dashboard_username())
            self.assertEqual(store.verify_dashboard_login("", "old account password"), "password")

    def test_bootstrap_username_migration_and_session_revocation(self):
        import tempfile
        from auth import AuthStore
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(Path(directory), resource_uris=[RESOURCE], base_urls=[RESOURCE[:-4]])
            account = store.ensure_dashboard_account()
            self.assertIsNotNone(account)
            self.assertIsNone(store.ensure_dashboard_account())
            self.assertIsNone(store.verify_dashboard_login("", account["password"]))
            self.assertIsNone(store.verify_dashboard_login("wrong", account["password"]))
            self.assertEqual(store.verify_dashboard_login(account["username"], account["password"]), "password")
            self.assertEqual(store.verify_dashboard_login("", store.machine_token), "machine-token")
            sid, _ = store.create_session()
            store.set_dashboard_account("new-user", "replacement password")
            self.assertIsNone(store.get_session(sid))
            self.assertIsNone(store.verify_dashboard_login(account["username"], account["password"]))
            self.assertEqual(store.verify_dashboard_login("new-user", "replacement password"), "password")
            key = store.ensure_connector_key()
            reopened = AuthStore(Path(directory), resource_uris=[RESOURCE], base_urls=[RESOURCE[:-4]])
            self.assertIsNone(reopened.ensure_dashboard_account())
            self.assertIsNone(reopened.ensure_connector_key())
            self.assertTrue(reopened.verify_connector_key(key))
            self.assertEqual(reopened.verify_dashboard_login("new-user", "replacement password"), "password")
            raw = (Path(directory) / "auth.db").read_bytes()
            self.assertNotIn(key.encode(), raw)
            self.assertNotIn(b"replacement password", raw)
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(Path(directory), resource_uris=[RESOURCE], base_urls=[RESOURCE[:-4]])
            store.set_password("legacy password")
            self.assertIsNone(store.ensure_dashboard_account())
            self.assertEqual(store.verify_dashboard_login("", "legacy password"), "password")
            self.assertEqual(store.verify_admin_credential(store.machine_token), "machine-token")


if __name__ == "__main__":
    unittest.main()
