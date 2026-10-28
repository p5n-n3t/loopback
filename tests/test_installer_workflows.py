"""Installer discovery, credential preservation, and human-readable CLI contracts."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import RUNTIME  # noqa: F401
import cli
from bootstrap import build_context
from install_discovery import inventory


class InventoryTests(unittest.TestCase):
    def test_inventory_reads_aliases_without_connecting_or_copying_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / '.ssh').mkdir()
            (home / '.ssh/config').write_text('Host ora1 ora3 *\n IdentityFile TOPSECRET\n ProxyCommand secret-value\n')
            def command(argv, **kwargs):
                self.assertEqual(argv[0], 'tailscale')
                self.assertIn('--json', argv)
                return subprocess.CompletedProcess(argv, 0, '{"BackendState":"Running"}' if argv[1] == 'status' else '{"Web":{"existing":{}}}', '')
            with mock.patch('install_discovery.shutil.which', return_value='/bin/available'), \
                    mock.patch('install_discovery.subprocess.run', side_effect=command) as run:
                result = inventory(home)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(result['ssh_aliases'], ['ora1', 'ora3'])
            self.assertEqual(result['tailscale']['routes'], 'present')
            self.assertNotIn('secret', json.dumps(result).lower())

    def test_failed_probe_is_unknown_not_absence(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('install_discovery.shutil.which', return_value='/bin/available'), \
                mock.patch('install_discovery.subprocess.run', side_effect=subprocess.TimeoutExpired('tailscale', 4)):
            result = inventory(Path(tmp))
        self.assertEqual(result['tailscale']['routes'], 'unavailable')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR': str(self.root)})
        patch.start()
        self.addCleanup(patch.stop)
        self.ctx = build_context(start_managers=False)

    def install(self, extra=()):
        args = cli.build_parser().parse_args(['install', '--stage2', '--ingress', 'local', '--yes', *extra])
        out = io.StringIO()
        with mock.patch.object(cli, 'get_identity', return_value=self.ctx.identity), \
                mock.patch.object(cli, 'load_ctx', return_value=self.ctx), \
                mock.patch.object(cli, 'start_gateway'), mock.patch.object(cli, 'wait_health', return_value={'version': '4.0.0'}), \
                mock.patch.object(cli, 'builtin_doctor', return_value=[]), mock.patch.object(cli, 'verify_mcp', return_value={'ok': True}), \
                mock.patch.object(cli, 'print_recovery'), contextlib.redirect_stdout(out):
            rc = cli.install_stage2(args)
        return rc, out.getvalue()

    def test_first_install_discloses_credentials_once_without_report_secrets(self):
        rc, output = self.install()
        self.assertEqual(rc, 0, output)
        self.assertIn('Generated dashboard account', output)
        self.assertIn('Generated connector setup key', output)
        secret = next(line.strip() for line in output.splitlines() if line.strip().startswith('lbs_'))
        self.assertNotIn(secret, (self.root / 'install-report.json').read_text())
        username = self.ctx.auth.dashboard_username()
        rc, again = self.install()
        self.assertEqual(rc, 0, again)
        self.assertNotIn('Generated dashboard account', again)
        self.assertNotIn('Generated connector setup key', again)
        self.assertEqual(self.ctx.auth.dashboard_username(), username)

    def test_existing_password_is_preserved(self):
        self.ctx.auth.set_password('existing-test-password')
        rc, output = self.install()
        self.assertEqual(rc, 0, output)
        self.assertNotIn('Generated dashboard account', output)
        self.assertEqual(self.ctx.auth.verify_admin_credential('existing-test-password'), 'password')

    def test_settings_file_validated_and_saved(self):
        settings = self.root / 'settings.json'
        settings.write_text('{"display_name":"Test machine","discovery":{"excluded_targets":["ora2","ora4"]}}')
        rc, output = self.install(['--settings-file', str(settings)])
        self.assertEqual(rc, 0, output)
        self.assertEqual(self.ctx.config.data['display_name'], 'Test machine')
        self.assertEqual(self.ctx.config.data['discovery']['excluded_targets'], ['ora2', 'ora4'])
        settings.write_text('{"sandbox":{"backend":"none"}}')
        self.assertEqual(self.install(['--settings-file', str(settings)])[0], 1)

    def test_report_human_default_and_explicit_json(self):
        (self.root / 'install-report.json').write_text('{"ok":true,"steps":[{"name":"health","ok":true,"detail":"ready"}]}')
        for args, machine in ((['report'], False), (['report', '--json'], True)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_report(cli.build_parser().parse_args(args)), 0)
            if machine:
                self.assertTrue(json.loads(out.getvalue())['ok'])
            else:
                self.assertIn('STATE', out.getvalue())
                self.assertIn('health', out.getvalue())

    def test_ngrok_requires_explicit_public_confirmation(self):
        args = cli.build_parser().parse_args(['install', '--non-interactive', '--ingress', 'ngrok', '--host', 'fixed.ngrok.app', '--ngrok-config', '/tmp/test-ngrok.yml'])
        with mock.patch.object(cli, 'confirm', return_value=False), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.ask_install_questions(args)

    def test_help_verb_remains_available(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['help']), 0)
        self.assertIn('Loopback MCP control plane', out.getvalue())


if __name__ == '__main__':
    unittest.main()
