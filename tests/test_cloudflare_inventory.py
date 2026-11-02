import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from helpers import RUNTIME  # noqa: F401
from cloudflare_inventory import CloudflareInventory, sanitize


class ProviderTests(unittest.TestCase):
    def test_import_sanitizes_credentials_and_preserves_all_routes(self):
        data = sanitize(
            [
                {
                    "id": "abc",
                    "ingress": [
                        {"hostname": "one.example", "service": "http://127.0.0.1:8000/mcp?token=secret"},
                        {"hostname": "two.example", "service": "http://gateway:9000"},
                        {"hostname": "bad.example", "service": "http://user:secret@gateway:9000"},
                        {"service": "http_status:404"},
                    ],
                }
            ],
            time.time() - 400,
            "import",
        )
        self.assertTrue(data["stale"])
        self.assertEqual(len(data["tunnels"][0]["ingress"]), 2)
        self.assertNotIn("secret", str(data))
        self.assertEqual(data["host_association"], "unknown")

    def test_api_refresh_persists_and_failure_retains_last_observation(self):
        with tempfile.TemporaryDirectory() as td, patch.dict("os.environ", {"LB_TEST_CF": "test-only"}):
            provider = CloudflareInventory({"discovery": {"cloudflare": {"enabled": True, "account_id": "a" * 32, "token_env": "LB_TEST_CF"}}}, Path(td))
            responses = [
                {"result": [{"id": "a-b", "name": "gateway"}]},
                {"result": {"config": {"ingress": [{"hostname": "mcp.example", "service": "http://gateway:8000"}]}}},
            ]
            with patch.object(provider, "_request", side_effect=responses):
                result = provider.refresh()
            self.assertEqual(len(result["tunnels"]), 1)
            self.assertEqual(provider.path.stat().st_mode & 0o777, 0o600)
            with patch.object(provider, "_request", side_effect=RuntimeError("secret")):
                failed = provider.refresh()
            self.assertEqual(failed["tunnels"], result["tunnels"])
            self.assertNotIn("secret", str(failed))

    def test_concurrent_refresh_does_not_overwrite_and_missing_credentials_reported(self):
        with tempfile.TemporaryDirectory() as td:
            provider = CloudflareInventory({"discovery": {"cloudflare": {"enabled": True}}}, Path(td))
            self.assertEqual(provider.refresh()["error"], "provider_credentials_unavailable")
            provider.lock.acquire()
            try:
                self.assertTrue(provider.refresh()["refresh_in_progress"])
            finally:
                provider.lock.release()

    def test_budget_expires_before_network_request(self):
        with tempfile.TemporaryDirectory() as td:
            provider = CloudflareInventory({}, Path(td))
            provider.deadline = time.monotonic() - 1
            with patch("urllib.request.urlopen") as request:
                with self.assertRaises(TimeoutError):
                    provider._request("/accounts/abc", "secret")
                request.assert_not_called()
