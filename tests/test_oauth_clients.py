"""OAuth client contract: registration metadata, confidential clients, grant narrowing, revocation auth.

These reproduce the two rejection strings the owner reported from ChatGPT and Claude
registration (`only public clients ... are supported` and `unsupported grant type requested`)
and verify that the replacement contract is consistent across registration, the metadata
document, the token endpoint and the revocation endpoint. They use synthetic payloads:
real ChatGPT/Claude registration bodies are not available offline.
"""
from __future__ import annotations

import base64
import unittest

from helpers import consent_fields, fresh_config_dir, load_server, pkce_pair

from starlette.testclient import TestClient

RESOURCE = "http://127.0.0.1:2026/mcp"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


def basic(cid: str, secret: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()}


class OAuthClientContractTests(unittest.TestCase):
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

    def register(self, **extra):
        return self.c.post("/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "Test", **extra})

    def code_for(self, client_id: str, challenge: str, *, redirect=REDIRECT) -> str:
        r = self.c.get("/oauth/authorize", params={
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "resource": RESOURCE, "scope": "loopback:read", "state": "s"}, follow_redirects=False)
        self.assertEqual(r.status_code, 200, r.text)
        rid, csrf = consent_fields(r.text)
        r = self.c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "approve",
                                                  "credential": self.machine_token}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        import urllib.parse as up

        return up.parse_qs(up.urlparse(r.headers["location"]).query)["code"][0]

    # ------------------------------------------------------------ metadata document
    def test_metadata_advertises_one_consistent_contract(self):
        meta = self.c.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(meta["token_endpoint_auth_methods_supported"], ["none", "client_secret_basic", "client_secret_post"])
        self.assertEqual(meta["revocation_endpoint_auth_methods_supported"], meta["token_endpoint_auth_methods_supported"])
        self.assertEqual(meta["grant_types_supported"], ["authorization_code", "refresh_token"])
        self.assertNotIn("client_credentials", meta["grant_types_supported"])

    # ------------------------------------------------------------ registration
    def test_public_client_defaults_are_unchanged(self):
        r = self.register()
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["token_endpoint_auth_method"], "none")
        self.assertEqual(body["grant_types"], ["authorization_code", "refresh_token"])
        self.assertNotIn("client_secret", body)

    def test_reported_chatgpt_rejection_is_gone_for_secret_basic(self):
        r = self.register(token_endpoint_auth_method="client_secret_basic")
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["token_endpoint_auth_method"], "client_secret_basic")
        self.assertTrue(body["client_secret"])
        self.assertEqual(body["client_secret_expires_at"], 0)
        # The secret is disclosed once; the admin listing never shows it.
        listed = self.server.AUTH.list_clients()
        self.assertFalse(any("client_secret" in c for c in listed))
        self.assertEqual(next(c for c in listed if c["client_id"] == body["client_id"])["token_endpoint_auth_method"], "client_secret_basic")

    def test_secret_post_registration(self):
        r = self.register(token_endpoint_auth_method="client_secret_post")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["token_endpoint_auth_method"], "client_secret_post")

    def test_unsupported_auth_method_is_rejected_with_supported_list(self):
        r = self.register(token_endpoint_auth_method="private_key_jwt")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_metadata")
        self.assertIn("client_secret_basic", r.json()["error_description"])

    def test_reported_claude_rejection_is_gone_by_narrowing_extra_grants(self):
        r = self.register(grant_types=["authorization_code", "refresh_token", "client_credentials"])
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["grant_types"], ["authorization_code", "refresh_token"])
        r = self.register(grant_types=["authorization_code", "implicit"], response_types=["code", "token"])
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["grant_types"], ["authorization_code"])
        self.assertEqual(r.json()["response_types"], ["code"])

    def test_grant_list_without_authorization_code_is_still_rejected(self):
        r = self.register(grant_types=["client_credentials"])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_metadata")
        self.assertIn("authorization_code", r.json()["error_description"])

    def test_malformed_metadata_arrays_are_reported_not_misclassified(self):
        r = self.register(grant_types={"not": "a list"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("grant_types must be an array", r.json()["error_description"])
        r = self.register(grant_types="authorization_code refresh_token")  # space separated string is tolerated
        self.assertEqual(r.status_code, 201, r.text)

    def test_client_without_refresh_grant_cannot_refresh(self):
        reg = self.register(grant_types=["authorization_code"]).json()
        verifier, challenge = pkce_pair()
        code = self.code_for(reg["client_id"], challenge)
        tok = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                                "client_id": reg["client_id"], "redirect_uri": REDIRECT, "resource": RESOURCE})
        self.assertEqual(tok.status_code, 200, tok.text)
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok.json()["refresh_token"],
                                              "client_id": reg["client_id"], "resource": RESOURCE})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "unauthorized_client")

    # ------------------------------------------------------------ token endpoint authentication
    def test_confidential_basic_client_full_flow_and_wrong_secret(self):
        reg = self.register(token_endpoint_auth_method="client_secret_basic").json()
        cid, secret = reg["client_id"], reg["client_secret"]
        verifier, challenge = pkce_pair()
        code = self.code_for(cid, challenge)
        form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT, "resource": RESOURCE}
        # Wrong secret: 401 with a Basic challenge and the code is NOT consumed.
        r = self.c.post("/oauth/token", data=form, headers=basic(cid, "wrong"))
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.json()["error"], "invalid_client")
        self.assertIn("Basic", r.headers.get("www-authenticate", ""))
        # Missing credentials entirely.
        r = self.c.post("/oauth/token", data={**form, "client_id": cid})
        self.assertEqual(r.status_code, 401)
        # Method mismatch: secret in the body for a basic client is refused.
        r = self.c.post("/oauth/token", data={**form, "client_id": cid, "client_secret": secret})
        self.assertEqual(r.status_code, 401)
        # Right secret: exchange, refresh and MCP call work.
        r = self.c.post("/oauth/token", data=form, headers=basic(cid, secret))
        self.assertEqual(r.status_code, 200, r.text)
        tokens = r.json()
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "resource": RESOURCE},
                        headers=basic(cid, secret))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotEqual(r.json()["refresh_token"], tokens["refresh_token"])
        rpc = self.c.post("/mcp", headers={"Authorization": "Bearer " + r.json()["access_token"], "Accept": "application/json, text/event-stream",
                                           "MCP-Protocol-Version": "2025-06-18"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertEqual(rpc.status_code, 200, rpc.text)

    def test_confidential_post_client_and_basic_mismatch(self):
        reg = self.register(token_endpoint_auth_method="client_secret_post").json()
        cid, secret = reg["client_id"], reg["client_secret"]
        verifier, challenge = pkce_pair()
        code = self.code_for(cid, challenge)
        form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT,
                "resource": RESOURCE, "client_id": cid}
        r = self.c.post("/oauth/token", data=form, headers=basic(cid, secret))
        self.assertEqual(r.status_code, 401)  # registered for post, sent basic
        r = self.c.post("/oauth/token", data={**form, "client_secret": "nope"})
        self.assertEqual(r.status_code, 401)
        r = self.c.post("/oauth/token", data={**form, "client_secret": secret})
        self.assertEqual(r.status_code, 200, r.text)

    def test_public_client_ignores_stray_secret_and_basic_id_mismatch_is_rejected(self):
        reg = self.register().json()
        verifier, challenge = pkce_pair()
        code = self.code_for(reg["client_id"], challenge)
        form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT,
                "resource": RESOURCE, "client_id": reg["client_id"], "client_secret": "ignored"}
        r = self.c.post("/oauth/token", data={**form}, headers=basic("someone-else", "x"))
        self.assertEqual(r.status_code, 401)
        r = self.c.post("/oauth/token", data=form)
        self.assertEqual(r.status_code, 200, r.text)

    def test_secret_rotation_invalidates_old_secret(self):
        reg = self.register(token_endpoint_auth_method="client_secret_basic").json()
        new_secret = self.server.AUTH.rotate_client_secret(reg["client_id"])
        self.assertNotEqual(new_secret, reg["client_secret"])
        self.assertFalse(self.server.AUTH.verify_client_secret(reg["client_id"], reg["client_secret"]))
        self.assertTrue(self.server.AUTH.verify_client_secret(reg["client_id"], new_secret))
        with self.assertRaises(Exception):
            self.server.AUTH.rotate_client_secret(self.register().json()["client_id"])

    # ------------------------------------------------------------ revocation
    def test_revocation_requires_authentication_for_confidential_clients(self):
        reg = self.register(token_endpoint_auth_method="client_secret_basic").json()
        cid, secret = reg["client_id"], reg["client_secret"]
        verifier, challenge = pkce_pair()
        code = self.code_for(cid, challenge)
        tokens = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                                   "redirect_uri": REDIRECT, "resource": RESOURCE}, headers=basic(cid, secret)).json()
        r = self.c.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": cid})
        self.assertEqual(r.status_code, 401)
        r = self.c.post("/oauth/revoke", data={"token": tokens["refresh_token"]}, headers=basic(cid, secret))
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "resource": RESOURCE},
                        headers=basic(cid, secret))
        self.assertEqual(r.status_code, 400)

    def test_cimd_documents_stay_public(self):
        with self.assertRaises(Exception) as ctx:
            self.server.AUTH.register_client({"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_post"},
                                             kind="cimd", client_id="https://example.com/client")
        self.assertIn("public clients", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
