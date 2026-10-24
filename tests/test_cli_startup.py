"""Regression coverage for one-command gateway + connector startup."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import RUNTIME  # noqa: F401
from bootstrap import build_context
import cli
from config import ConfigStore
import identity


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.env = mock.patch.dict(os.environ, {"LOOPBACK_CONFIG_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ctx = build_context(start_managers=False)
        self.conf = self.root / 'original tunnel.yml'
        self.conf.write_text('tunnel: original-tunnel\ningress: []\n')
        self.ctx.config.save({'ingress': {'cloudflare': {'enabled': True, 'hostname': 'loopback.example.com',
                                                       'config_file': str(self.conf)}}})
        self.svc = mock.Mock(port=2026)
        self.svc.start.return_value = {'ok': True, 'manager': 'pid-file'}
        self.svc.restart.return_value = self.svc.start.return_value

    def up(self, result, args=None):
        output = io.StringIO()
        with mock.patch.object(cli, 'require_installed'), mock.patch.object(cli, 'service_manager', return_value=self.svc), \
             mock.patch.object(cli, 'wait_health', return_value={'version': '4.0.0'}), \
             mock.patch.object(cli, 'helper_json', side_effect=result), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cli.cmd_up(args or cli.build_parser().parse_args(['up']))
        return status, output.getvalue()

    def test_up_recovers_stopped_connector_when_server_already_running(self):
        """A real detached process is started, recovered and reused across `up` calls."""
        fake = self.root / 'cloudflared'
        fake.write_text(f'#!{sys.executable}\nimport os,sys\nos.execv(sys.executable, '
                        "['cloudflared', '-c', 'import time; time.sleep(90)', *sys.argv[1:]])\n")
        fake.chmod(0o700)
        self.addCleanup(self.ctx.ingress.stop_cloudflare)
        def start(cfg, command, **kwargs):
            self.assertEqual(command, 'start-ingress')
            return self.ctx.ingress.ensure_started()
        with mock.patch('ingress.shutil.which', return_value=str(fake)):
            status, output = self.up(start)
            self.assertEqual(status, 0, output)
            pid = self.ctx.ingress.cloudflared_pid()
            self.assertIsNotNone(pid)
            self.assertIn('Cloudflare connector running', output)
            self.assertEqual(self.up(start)[0], 0)
            self.assertEqual(self.ctx.ingress.cloudflared_pid(), pid)
            self.ctx.ingress.stop_cloudflare()
            self.assertEqual(self.up(start)[0], 0)
            self.assertNotEqual(self.ctx.ingress.cloudflared_pid(), pid)

    def test_up_does_not_claim_success_when_connector_fails(self):
        status, output = self.up(lambda *a, **k: {'ok': False, 'error': 'cloudflared is not installed'})
        self.assertEqual(status, 1)
        self.assertIn('local server is running, but configured ingress failed', output)
        self.assertNotIn('Loopback is up', output)
        self.assertIn('loopback logs --file cloudflared', output)

    def test_restart_reconciles_ingress_and_reports_failure(self):
        with mock.patch.object(cli, 'require_installed'), mock.patch.object(cli, 'service_manager', return_value=self.svc), \
             mock.patch.object(cli, 'wait_health', return_value={'version': '4.0.0'}), \
             mock.patch.object(cli, 'helper_json', return_value={'ok': False, 'error': 'connector exited'}), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_restart(cli.build_parser().parse_args(['restart'])), 1)

    def test_explicit_import_is_persisted_and_does_not_rewrite_yaml(self):
        self.ctx.config.save({'ingress': {'cloudflare': {'enabled': False, 'config_file': None}}})
        original = self.conf.read_bytes()
        args = cli.build_parser().parse_args(['_helper', 'import-cloudflare', '--config-file', str(self.conf),
                                             '--host', 'loopback.example.com'])
        with mock.patch.object(cli, 'load_ctx', return_value=self.ctx), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_helper(args), 0)
        saved = ConfigStore(identity.detect()).data['ingress']['cloudflare']
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['config_file'], str(self.conf))
        self.assertEqual(self.conf.read_bytes(), original)
        with mock.patch('ingress.shutil.which', return_value='/usr/bin/cloudflared'):
            self.assertEqual(self.ctx.ingress._cloudflared_cmd(saved)[-1], 'run')

    def test_import_restarts_gateway_before_reconciling_tunnel(self):
        calls = []
        def helper(cfg, command, *args, **kwargs):
            calls.append(command)
            return {'ok': True}
        status, _ = self.up(helper, cli.build_parser().parse_args(['up', '--cloudflare-config', str(self.conf)]))
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['import-cloudflare', 'start-ingress'])
        self.svc.restart.assert_called_once()

    def test_external_config_migrates_and_roundtrips(self):
        old = self.root / 'old'
        old.mkdir()
        (old / 'config.env').write_text('LOOPBACK_INGRESS=cloudflare\nLOOPBACK_PUBLIC_HOST=loopback.example.com\n'
                                      f'LOOPBACK_CLOUDFLARE_CONFIG_FILE={self.conf}\n')
        with mock.patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR': str(old)}):
            store = ConfigStore(identity.detect())
            store.persist_migration()
            self.assertEqual(store.data['ingress']['cloudflare']['config_file'], str(self.conf))
            self.assertIn(str(self.conf), (old / 'config.env').read_text())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process adoption')
    def test_reuse_exact_existing_connector_but_leave_other_config_alone(self):
        other = self.root / 'other.yml'
        other.write_text('tunnel: another-tunnel\n')
        processes = []
        try:
            for path in (other, self.conf):
                proc = subprocess.Popen(['cloudflared', '-c', 'import time; time.sleep(90)',
                                         'tunnel', '--config', str(path), 'run'], executable=sys.executable)
                processes.append(proc)
            # Synchronize with exec by reading the process argument list.
            import time
            for _ in range(100):
                if self.ctx.ingress._matching_cloudflared_pid() == processes[1].pid:
                    break
                time.sleep(.01)
            self.assertEqual(self.ctx.ingress.cloudflared_pid(), processes[1].pid)
            self.ctx.ingress.stop_cloudflare()
            processes[1].wait(timeout=5)
            self.assertIsNone(processes[0].poll())
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)

    def test_upgrade_fails_when_configured_connector_cannot_start(self):
        report = cli.Report('upgrade', self.root / 'report.json')
        def helper(cfg, command, *args, **kwargs):
            return {'ok': False, 'error': 'connector exited'} if command == 'start-ingress' else {'ok': True}
        with mock.patch.object(cli, 'wait_health', return_value={'version': '4.0.0'}), \
             mock.patch.object(cli, 'http', return_value=(200, None, None)), \
             mock.patch.object(cli, 'helper_json', side_effect=helper), contextlib.redirect_stdout(io.StringIO()):
            self.assertIn('configured ingress failed to start', cli.post_upgrade_checks(self.root, '4.0.0', report))
