"""Provider ownership, rollback and stable-origin behavior without live agents."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import RUNTIME  # noqa: F401
from config import ConfigStore, default_config, validate
from ingress import IngressError, IngressManager
from topology import TopologyBuilder


class NgrokProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.credentials = self.root / "credentials.yml"
        self.credentials.write_text('version: "3"\nagent:\n  authtoken: NEVER-EXPOSE-THIS-SECRET\n')
        self.config = ConfigStore(SimpleNamespace(config_dir=str(self.root)))
        self.ctx = SimpleNamespace(config=self.config, cfg_dir=self.root,
                                   identity=SimpleNamespace(home=str(self.root)), refresh_auth_urls=Mock())
        self.manager = IngressManager(self.ctx)
        self.ctx.ingress = self.manager
        self.proc = Mock(pid=34567)
        self.proc.poll.return_value = None
        self.popen = self.enterContext(patch("ngrok_provider.subprocess.Popen", return_value=self.proc))
        self.enterContext(patch("ngrok_provider.shutil.which", return_value="/usr/bin/ngrok"))
        self.birth = self.enterContext(patch("ngrok_provider.process_identity", return_value="proc:12345"))
        self.enterContext(patch("ngrok_provider.time.sleep"))

    def enable(self):
        return self.manager.apply_ngrok(True, "fixed.ngrok.app", str(self.credentials), True)

    def test_topology_separates_live_process_from_unverified_public_route(self):
        builder = TopologyBuilder(self.ctx)
        result = builder._ingress_results({'ngrok': {'enabled': True, 'running': True}})
        self.assertEqual(result['ingress:ngrok->gateway']['status'], 'healthy')
        self.assertEqual(result['clients->ingress:ngrok']['status'], 'untested')
        result = builder._ingress_results({'ngrok': {'enabled': True, 'running': True, 'public_health': {'ok': True, 'status': 200}}})
        self.assertEqual(result['clients->ingress:ngrok']['status'], 'healthy')

    def test_public_confirmation_precedes_side_effects(self):
        with self.assertRaisesRegex(IngressError, "public internet"):
            self.manager.apply_ngrok(True, "fixed.ngrok.app", str(self.credentials))
        self.popen.assert_not_called()
        self.assertFalse(self.config.data["ingress"]["ngrok"]["enabled"])

    def test_fixed_origin_credentials_and_agent_controls(self):
        result = self.enable()
        self.assertTrue(result["ok"])
        self.assertEqual(self.config.primary_base_url(), "https://fixed.ngrok.app")
        self.assertIn("https://fixed.ngrok.app/mcp", self.config.resource_uris())
        args, options = self.popen.call_args
        command = args[0]
        self.assertIn("http://127.0.0.1:2026", command)
        self.assertIn("--inspect=false", command)
        self.assertIn("https://fixed.ngrok.app", command)
        self.assertEqual(command[command.index("--config") + 1].split(",")[0], str(self.credentials))
        overlay = json.loads((self.root / "ngrok-agent.json").read_text())
        self.assertFalse(overlay["agent"]["web_addr"])
        self.assertFalse(overlay["agent"]["remote_management"])
        self.assertEqual(options["stderr"], -3)
        for path in self.root.glob("*.json"):
            self.assertNotIn("NEVER-EXPOSE-THIS-SECRET", path.read_text())
        self.assertEqual(self.config.data["bind"], "127.0.0.1")
        self.assertEqual(result["ngrok"]["status"], "active-unverified")

    def test_restart_reuses_owned_agent_without_second_process(self):
        self.enable()
        other = IngressManager(self.ctx)
        self.assertEqual(other.start_ngrok()["action"], "already-running")
        self.assertEqual(self.popen.call_count, 1)

    def test_reused_pid_is_never_signaled(self):
        self.enable()
        self.birth.return_value = "proc:DIFFERENT"
        with patch("ngrok_provider.os.kill") as kill:
            result = IngressManager(self.ctx).stop_ngrok()
        kill.assert_not_called()
        self.assertFalse(result["stopped"])

    def test_owned_process_is_terminated_and_reaped(self):
        self.enable()
        self.manager.stop_ngrok()
        self.proc.terminate.assert_called_once()
        self.proc.wait.assert_called_once_with(timeout=5.0)
        self.assertFalse((self.root / "ngrok-process.json").exists())

    def test_config_failure_restores_origin_and_stops_new_process(self):
        old = self.config.primary_base_url()
        self.ctx.refresh_auth_urls.side_effect = RuntimeError("failed refresh")
        with self.assertRaises(IngressError):
            self.enable()
        self.assertEqual(self.config.primary_base_url(), old)
        self.assertFalse(self.config.data["ingress"]["ngrok"]["enabled"])
        self.proc.terminate.assert_called_once()

    def test_failed_agent_does_not_persist_public_origin(self):
        self.proc.poll.return_value = 1
        with self.assertRaisesRegex(IngressError, "exited during startup"):
            self.enable()
        self.assertEqual(self.config.primary_base_url(), "http://127.0.0.1:2026")
        self.assertFalse((self.root / "ngrok-process.json").exists())

    def test_url_and_wildcards_are_rejected_before_launch(self):
        for host in ["https://fixed.ngrok.app", "*.ngrok.app", "fixed.ngrok.app:443", "fixed.ngrok.app/mcp"]:
            with self.assertRaises(IngressError):
                self.manager.apply_ngrok(True, host, str(self.credentials), True)
        self.popen.assert_not_called()

    def test_unknown_birth_identity_terminates_just_created_child(self):
        self.birth.return_value = None
        with self.assertRaisesRegex(IngressError, "ownership"):
            self.enable()
        self.proc.terminate.assert_called_once()
        self.assertFalse(self.config.data["ingress"]["ngrok"]["enabled"])

    def test_local_and_external_routes_are_untouched(self):
        with patch.object(self.manager, "_ts_off") as off, patch("ingress._run") as run:
            self.enable()
            self.manager.apply_ngrok(False)
        off.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.config.data["ingress"]["tailscale"]["enabled"])
        self.assertFalse(self.config.data["ingress"]["cloudflare"]["enabled"])

    def test_config_validation_requires_fixed_host_and_file(self):
        cfg = default_config()
        cfg["ingress"]["ngrok"] = {"enabled": True}
        errors = validate(cfg)
        self.assertTrue(any("ngrok.hostname" in e for e in errors))
        self.assertTrue(any("ngrok.config_file" in e for e in errors))

    def test_api_rejects_public_exposure_and_invalid_types(self):
        from starlette.applications import Starlette
        from starlette.testclient import TestClient
        import fabric_api
        # Test the real route and shared validation. Authentication is enforced by server middleware.
        with TestClient(Starlette(routes=fabric_api.routes(self.ctx))) as client:
            response = client.put('/api/admin/ingress/ngrok', json={"enabled": True, "hostname": "fixed.ngrok.app", "config_file": str(self.credentials)})
            self.assertEqual(response.status_code, 400)
            response = client.put('/api/admin/ingress/ngrok', json={"enabled": "true"})
            self.assertEqual(response.status_code, 400)
            response = client.put('/api/admin/ingress/ngrok', json={"enabled": True, "hostname": "fixed.ngrok.app", "config_file": str(self.credentials), "confirm_public": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("NEVER-EXPOSE-THIS-SECRET", response.text)
        self.assertEqual(self.popen.call_count, 1)

    def test_legacy_bridge_reports_ngrok_instead_of_local(self):
        self.enable()
        env = (self.root / 'config.env').read_text()
        self.assertIn('LOOPBACK_INGRESS=ngrok', env)
        self.assertIn('LOOPBACK_PUBLIC_HOST=fixed.ngrok.app', env)

    def test_timeout_escalates_only_owned_child(self):
        import subprocess
        self.enable()
        self.proc.wait.side_effect = [subprocess.TimeoutExpired('ngrok', 5), 0]
        self.manager.stop_ngrok()
        self.proc.terminate.assert_called_once()
        self.proc.kill.assert_called_once()
        self.assertEqual(self.proc.wait.call_count, 2)

    def test_adopted_child_stop_checks_birth_until_exit(self):
        self.enable()
        other = IngressManager(self.ctx)
        self.birth.side_effect = ['proc:12345', 'proc:12345', None, None, None, None]
        with patch('ngrok_provider.os.kill') as kill:
            result = other.stop_ngrok()
        kill.assert_called_once_with(34567, __import__('signal').SIGTERM)
        self.assertTrue(result['stopped'])

    def test_startup_keeps_provider_errors_separate(self):
        self.config.data['ingress']['ngrok']['enabled'] = True
        with patch.object(self.manager, 'start_ngrok', side_effect=ValueError('ngrok unavailable')):
            result = self.manager.ensure_started()
        self.assertFalse(result['ok'])
        self.assertEqual(self.manager._last_error['ngrok'], 'ngrok unavailable')
        self.assertIsNone(self.manager._last_error['cloudflare'])
