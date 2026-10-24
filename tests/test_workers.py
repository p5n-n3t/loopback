from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import call_tool, fresh_config_dir, load_server, rpc, tool_text
from starlette.testclient import TestClient

import node_agent
from workers import WorkerManager


def binding(**kw):
    return {'name': 'echo', 'target': 'local', 'transport': 'stdio', 'command': ['echo', 'ok'],
            'port': 3031, 'exposure': 'private', **kw}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ctx = SimpleNamespace(cfg_dir=self.root, ssh=SimpleNamespace(get=lambda name: {'name': name}))
        self.manager = WorkerManager(self.ctx)

    def test_many_bindings_and_port_conflicts(self):
        for n in range(40):
            self.manager.save(binding(name=f'service-{n}', port=4000 + n))
        self.assertEqual(len(self.manager.bindings()), 40)
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            self.manager.save(binding(name='duplicate', port=4001))

    def test_validation_and_public_confirmation(self):
        for values in ({'name': '../bad'}, {'port': True}, {'port': 80}, {'command': 'sh -c bad'},
                       {'exposure': 'tailscale-funnel', 'https_port': 1234, 'confirm_public': True},
                       {'exposure': 'cloudflare', 'hostname': 'service.example', 'token_file': '/tmp/token'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                node_agent.validate(binding(**values))
        node_agent.validate(binding(exposure='tailscale-serve', https_port=9443))

    def test_refuses_existing_rotoxy_listener_without_mutation(self):
        config = {'Web': {'desktop.ts.net:8443': {'Handlers': {'/mcp': {'Proxy': 'http://127.0.0.1:2026'}}}}}
        with patch('node_agent.serve_config', return_value=config), patch('node_agent.launch') as launch:
            with self.assertRaisesRegex(ValueError, 'already has a listener'):
                node_agent.start_binding(self.root, binding(exposure='tailscale-serve', https_port=8443))
            launch.assert_not_called()

    def test_stop_refuses_changed_or_mixed_listener(self):
        b = binding(exposure='tailscale-serve', https_port=8443)
        config = {'Web': {'desk:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3031'},
                                                          '/mcp': {'Proxy': 'http://127.0.0.1:2026'}}}}}
        with patch('node_agent.serve_config', return_value=config), patch('node_agent.run') as run:
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                node_agent.stop_binding(self.root, {'binding': b, 'tailscale_owned': True})
            run.assert_not_called()

    def test_pid_reuse_never_kills_unrelated_process(self):
        with patch('node_agent.pid_identity', return_value='new'), patch('node_agent.os.killpg') as kill:
            node_agent.terminate({'pid': 54321, 'start': 'old'})
            kill.assert_not_called()

    def test_metrics_separate_host_and_worker_and_first_sample_unknown(self):
        self.manager.call = lambda *a, **kw: {'sampled_at': 10, 'host': {'cpu_count': 2, 'cpu_seconds': 100,
            'cpu_idle_seconds': 80, 'memory_total': 1000, 'memory_used': 500, 'rx_bytes': 100, 'tx_bytes': 100},
            'worker': {'cpu_seconds': 2, 'rss_bytes': 100, 'rx_bytes': 10, 'tx_bytes': 20}, 'bindings': []}
        a = self.manager._sample('local')
        self.assertIsNone(a['worker']['cpu_percent'])
        self.manager.call = lambda *a, **kw: {'sampled_at': 12, 'host': {'cpu_count': 2, 'cpu_seconds': 104,
            'cpu_idle_seconds': 82, 'memory_total': 1000, 'memory_used': 500, 'rx_bytes': 500, 'tx_bytes': 700},
            'worker': {'cpu_seconds': 3, 'rss_bytes': 100, 'rx_bytes': 110, 'tx_bytes': 220}, 'bindings': []}
        b = self.manager._sample('local')
        self.assertEqual(b['host']['cpu_percent'], 50)
        self.assertEqual(b['worker']['cpu_percent'], 25)
        self.assertEqual(b['worker']['memory_percent'], 10)
        self.assertEqual(b['worker']['rx_per_second'], 50)
        self.manager.call = lambda *a, **kw: (_ for _ in ()).throw(ValueError('secret'))
        c = self.manager._sample('local')
        self.assertEqual(c['status'], 'unreachable')
        self.assertNotIn('worker', c)
        self.assertNotIn('secret', json.dumps(c))

    def test_local_state_outside_runtime(self):
        self.manager.install('local')
        self.assertTrue((self.root / 'worker/worker_bridge.py').exists())
        self.assertTrue((self.root / 'workers.json').exists())


class WorkerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fresh_config_dir()
        cls.server = load_server()
        cls.cm = TestClient(cls.server.app, base_url='http://127.0.0.1:2026')
        cls.c = cls.cm.__enter__()
        cls.c.post('/admin/login', data={'credential': cls.server.AUTH.machine_token})
        cls.csrf = {'X-Loopback-CSRF': cls.c.get('/api/admin/session').json()['csrf']}

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)

    def test_binding_persists_after_reload_and_requires_csrf(self):
        self.assertEqual(self.c.post('/api/admin/bindings', json=binding()).status_code, 403)
        response = self.c.post('/api/admin/bindings', json=binding(), headers=self.csrf)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.c.get('/api/admin/workers').json()['bindings'][0]['name'], 'echo')
        self.assertEqual(self.c.post('/api/admin/bindings/echo/delete', headers=self.csrf).status_code, 200)


class BridgeTests(unittest.TestCase):
    def test_real_stdio_roundtrip_with_separate_auth_and_no_local_shell_tools(self):
        from auth import AuthStore
        from worker_bridge import create_app
        root = Path(tempfile.mkdtemp())
        server = root / 'echo.py'
        server.write_text("from mcp.server import MCPServer\ns=MCPServer('worker-test')\n@s.tool()\ndef echo(message: str) -> str:\n return message\ns.run(transport='stdio')\n")
        b = binding(command=[sys.executable, str(server)])
        node_agent.write_json(root / 'binding.json', b)
        node_agent.write_json(root / 'config.json', {'port': 3031, 'public_host': 'localhost', 'maintenance': {'update_channel': 'off'}})
        auth = AuthStore(root, resource_uris=['http://127.0.0.1:3031/mcp'], base_urls=['http://127.0.0.1:3031'])
        token, _ = auth.create_api_token('edge-test', ['loopback:exec'], ttl_seconds=3600, created_by='tests')
        ro, _ = auth.create_api_token('read-only', ['loopback:read'], ttl_seconds=3600, created_by='tests')
        with patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR': str(root)}), TestClient(create_app(root)) as client:
            self.assertEqual(rpc(client, {}, 'tools/list').status_code, 401)
            self.assertEqual(rpc(client, {'Authorization': 'Bearer ' + ro}, 'tools/list').status_code, 403)
            headers = {'Authorization': 'Bearer ' + token}
            response = rpc(client, headers, 'tools/list')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('result', response.json(), response.text)
            self.assertEqual([t['name'] for t in response.json()['result']['tools']], ['echo'])
            self.assertEqual(tool_text(call_tool(client, headers, 'echo', {'message': 'remote-only'})), 'remote-only')
            self.assertEqual(client.get('/.well-known/oauth-protected-resource/mcp').status_code, 200)
            self.assertGreater(node_agent.read_json(root / 'traffic.json')['tx_bytes'], 0)
