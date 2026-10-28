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

    def test_ssh_alias_settings_are_preserved_when_selected(self):
        from ssh_targets import SSHTargetStore
        ctx = SimpleNamespace(cfg_dir=self.root, identity=SimpleNamespace(home=str(self.root), username='alice'), topology=None)
        store = SSHTargetStore(ctx)
        store.upsert(name='alias', host='ora1', use_ssh_config=True)
        target = store.get('alias')
        self.assertTrue(target['use_ssh_config'])
        with patch('ssh_targets.shutil.which', return_value='/usr/bin/ssh'):
            argv = store._ssh_args(target)
        self.assertNotIn('-p', argv)
        self.assertEqual(argv[-1], 'ora1')
        self.assertNotIn('alice@ora1', argv)
        manager = WorkerManager(SimpleNamespace(cfg_dir=self.root, ssh=store))
        with patch('workers.subprocess.run', side_effect=__import__('subprocess').TimeoutExpired('ssh', 30)):
            with self.assertRaises(__import__('subprocess').TimeoutExpired):
                manager.install('alias')

    def test_private_endpoint_ignores_retained_public_hostname(self):
        private = binding(hostname='old.example.com', https_port=8443)
        self.assertEqual(node_agent.endpoint(private), 'http://127.0.0.1:3031/mcp')
        self.assertEqual(node_agent.endpoint(binding(exposure='cloudflare', hostname='tools.example.com')),
                         'https://tools.example.com/mcp')
        self.assertEqual(node_agent.endpoint(binding(exposure='tailscale-serve', hostname='worker.ts.net',
                                                    https_port=8443)), 'https://worker.ts.net:8443/mcp')

    def test_credential_refuses_stopped_binding(self):
        directory = self.root / 'bindings' / 'echo'
        node_agent.write_json(directory / 'state.json', {'binding': binding(), 'bridge': None})
        with self.assertRaisesRegex(ValueError, 'Start this binding before enrolling'):
            node_agent.dispatch(self.root, {'action': 'credential', 'binding': binding()})
        self.assertFalse((directory / 'auth.db').exists())

    def test_tailscale_start_uses_the_validated_default_https_port(self):
        b = binding(exposure='tailscale-serve')
        with patch('node_agent.alive', side_effect=lambda p: bool(p)), \
                patch('node_agent.launch', return_value={'pid': 123, 'start': 'fixture'}), \
                patch('node_agent.socket.socket'), patch('node_agent.socket.create_connection'), \
                patch('node_agent.serve_config', return_value={}), \
                patch('node_agent.run', return_value=json.dumps({'Self': {'DNSName': 'worker.ts.net.'}})) as run:
            result = node_agent.start_binding(self.root, b)
        self.assertEqual(result['url'], 'https://worker.ts.net:8443/mcp')
        run.assert_any_call(['tailscale', 'serve', '--bg', '--https=8443', 'http://127.0.0.1:3031'])
        state = node_agent.read_json(self.root / 'bindings' / 'echo' / 'state.json')
        self.assertEqual(state['binding']['https_port'], 8443)

    def test_private_start_does_not_advertise_a_retained_public_hostname(self):
        b = binding(hostname='old.example.com')
        with patch('node_agent.alive', side_effect=lambda p: bool(p)), \
                patch('node_agent.launch', return_value={'pid': 123, 'start': 'fixture'}), \
                patch('node_agent.socket.socket'), patch('node_agent.socket.create_connection'):
            result = node_agent.start_binding(self.root, b)
        config = node_agent.read_json(self.root / 'bindings' / 'echo' / 'config.json')
        self.assertEqual(config['public_host'], 'localhost')
        self.assertEqual(result['url'], 'http://127.0.0.1:3031/mcp')

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

    def test_credential_api_returns_usable_link_without_caching(self):
        b = binding(name='enroll-api', port=3032)
        self.assertEqual(self.c.post('/api/admin/bindings', json=b, headers=self.csrf).status_code, 201)
        directory = self.server.CTX.workers.local_root / 'bindings' / b['name']
        node_agent.write_json(directory / 'state.json', {'binding': b, 'bridge': {'pid': 123, 'start': 'fixture'}})
        route = '/api/admin/bindings/enroll-api/credential'
        self.assertEqual(self.c.post(route).status_code, 403)
        with patch('node_agent.alive', return_value=True):
            result = self.c.post(route, headers=self.csrf)
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(data['url'], 'http://127.0.0.1:3032/admin/enroll#code=' + data['code'])
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertNotIn(self.server.AUTH.machine_token, result.text)
        self.assertEqual(self.c.post('/api/admin/bindings/enroll-api/delete', headers=self.csrf).status_code, 200)

    def test_binding_persists_after_reload_and_requires_csrf(self):
        self.assertEqual(self.c.post('/api/admin/bindings', json=binding()).status_code, 403)
        response = self.c.post('/api/admin/bindings', json=binding(), headers=self.csrf)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.c.get('/api/admin/workers').json()['bindings'][0]['name'], 'echo')
        self.assertEqual(self.c.post('/api/admin/bindings/echo/delete', headers=self.csrf).status_code, 200)


class BridgeTests(unittest.TestCase):
    def test_dashboard_credential_link_enrolls_the_worker(self):
        from urllib.parse import urlsplit
        from auth import AuthStore
        from worker_bridge import create_app
        root = Path(tempfile.mkdtemp())
        directory = root / 'bindings' / 'echo'
        b = binding(transport='http', upstream='http://127.0.0.1:9080/mcp')
        node_agent.write_json(directory / 'state.json', {'binding': b, 'bridge': {'pid': 123, 'start': 'fixture'}})
        node_agent.write_json(directory / 'binding.json', b)
        node_agent.write_json(directory / 'config.json', {'port': 3031, 'maintenance': {'update_channel': 'off'}})
        with patch('node_agent.alive', return_value=True):
            result = node_agent.dispatch(root, {'action': 'credential', 'binding': b})
        url = urlsplit(result['url'])
        self.assertEqual(url.path, '/admin/enroll')
        self.assertEqual(url.query, '')
        self.assertEqual(url.fragment, 'code=' + result['code'])
        with patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR': str(directory)}), \
                TestClient(create_app(directory), base_url='http://127.0.0.1:3031') as client:
            page = client.get(result['url'])
            self.assertEqual(page.status_code, 200)
            self.assertIn("location.hash", page.text)
            response = client.post(url.path, data={'code': url.fragment.removeprefix('code='),
                'password': 'worker-password-123', 'password2': 'worker-password-123'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.url.path, '/admin')
            self.assertEqual([hop.status_code for hop in response.history], [303])
            self.assertIn('Service access is ready', response.text)
            self.assertIn('value="http://127.0.0.1:3031/mcp"', response.text)
            self.assertNotIn('Create the dashboard password', response.text)
            self.assertNotIn(result['code'], response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
            self.assertIn("default-src 'none'", response.headers['content-security-policy'])
            self.assertNotIn('<script', response.text)
            self.assertEqual(client.get('/api/admin/session').status_code, 200)
        auth = AuthStore(directory, resource_uris=[node_agent.endpoint(b)],
                         base_urls=['http://127.0.0.1:3031'])
        self.assertTrue(auth.verify_admin_credential('worker-password-123'))
        self.assertFalse(auth.consume_enrollment(result['code']))

    def test_worker_connection_page_requires_a_session(self):
        from worker_bridge import create_app
        root = Path(tempfile.mkdtemp())
        b = binding(transport='http', upstream='http://127.0.0.1:9080/mcp')
        node_agent.write_json(root / 'binding.json', b)
        node_agent.write_json(root / 'config.json', {'port': 3031, 'maintenance': {'update_channel': 'off'}})
        with patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR': str(root)}), \
                TestClient(create_app(root), base_url='http://127.0.0.1:3031') as client:
            response = client.get('/admin', follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/admin/login')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('/mcp', response.text)
            self.assertEqual(client.get('/admin').url.path, '/admin/login')

    def test_worker_connection_content_escapes_values(self):
        from auth_http import render_worker_connection
        page = render_worker_connection('<script>bad</script>', 'https://example.com/" onfocus="bad')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', page)
        self.assertIn('value="https://example.com/&quot; onfocus=&quot;bad" readonly', page)

    def test_http_proxy_streams_without_forwarding_client_credentials(self):
        import httpx
        from auth import AuthStore
        from worker_bridge import create_app
        root = Path(tempfile.mkdtemp())
        b = binding(transport='http', upstream='http://127.0.0.1:9080/mcp')
        node_agent.write_json(root / 'binding.json', b)
        node_agent.write_json(root / 'config.json', {'port':3031,'maintenance':{'update_channel':'off'}})
        auth = AuthStore(root, resource_uris=['http://127.0.0.1:3031/mcp'], base_urls=['http://127.0.0.1:3031'])
        token, _ = auth.create_api_token('test', ['loopback:exec'], ttl_seconds=60, created_by='tests')
        def respond(request):
            self.assertNotIn('authorization', request.headers)
            self.assertNotIn('cookie', request.headers)
            self.assertNotIn('x-api-key', request.headers)
            return httpx.Response(200, stream=httpx.ByteStream(b'proxied-body'), headers={'content-type':'application/json'})
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.dict(os.environ, {'LOOPBACK_CONFIG_DIR':str(root)}), patch('worker_bridge.httpx.AsyncClient', return_value=upstream):
            with TestClient(create_app(root)) as client:
                response = client.post('/mcp', headers={'Authorization':'Bearer '+token,'Cookie':'foreign=credential','X-Api-Key':'foreign'}, json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'proxied-body')
                self.assertGreater(node_agent.read_json(root/'traffic.json')['rx_bytes'], 0)

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


class HostMetricsTests(unittest.TestCase):
    def test_host_network_excludes_stacked_virtual_adapters(self):
        text = '''Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  eth0: 5000 50 0 0 0 0 0 0 7000 70 0 0 0 0 0 0
tailscale0: 4000 40 0 0 0 0 0 0 6000 60 0 0 0 0 0 0
docker0: 300 3 0 0 0 0 0 0 400 4 0 0 0 0 0 0
 wlan0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0
'''
        rx, tx, counted, excluded = node_agent.host_network(text)
        self.assertEqual((rx, tx), (5100, 7200))
        self.assertEqual(counted, ['eth0', 'wlan0'])
        self.assertEqual(excluded, ['lo', 'tailscale0', 'docker0'])

    def test_sample_states_distinguish_first_measured_and_unavailable(self):
        root = Path(tempfile.mkdtemp())
        manager = WorkerManager(SimpleNamespace(cfg_dir=root, ssh=SimpleNamespace(get=lambda name: {'name': name})))
        base = {'cpu_count': 2, 'cpu_seconds': 100, 'cpu_idle_seconds': 80, 'memory_total': 1000, 'memory_used': 500,
                'rx_bytes': 100, 'tx_bytes': 100, 'net_interfaces': ['eth0'], 'net_excluded': ['lo', 'tailscale0']}
        manager.call = lambda *a, **kw: {'sampled_at': 10, 'host': dict(base), 'worker': {'cpu_seconds': 2, 'rss_bytes': 1, 'rx_bytes': 0, 'tx_bytes': 0, 'process_count': 1}, 'bindings': []}
        first = manager._sample('local')
        self.assertEqual(first['rates'], {'state': 'first', 'interval_seconds': None})
        self.assertIsNone(first['host']['rx_per_second'])
        self.assertEqual(first['host']['memory_available'], 500)
        # Second sample with unchanged counters is a *measured* zero, not an unknown.
        manager.call = lambda *a, **kw: {'sampled_at': 12, 'host': {**base, 'cpu_seconds': 104, 'cpu_idle_seconds': 84},
                                         'worker': {'cpu_seconds': 2, 'rss_bytes': 1, 'rx_bytes': 0, 'tx_bytes': 0, 'process_count': 1}, 'bindings': []}
        second = manager._sample('local')
        self.assertEqual(second['rates']['state'], 'measured')
        self.assertEqual(second['rates']['interval_seconds'], 2.0)
        self.assertEqual(second['host']['rx_per_second'], 0)
        self.assertEqual(second['host']['cpu_percent'], 0)
        self.assertEqual(second['worker']['cpu_percent'], 0)
        # Tiny Loopback memory keeps two decimals instead of collapsing to zero.
        self.assertEqual(second['worker']['memory_percent'], 0.1)
        # Missing host counters are reported as unavailable, never as zero.
        manager.call = lambda *a, **kw: {'sampled_at': 14, 'host': {**base, 'cpu_seconds': None, 'cpu_idle_seconds': None},
                                         'worker': {'cpu_seconds': 2, 'rss_bytes': 1, 'rx_bytes': 0, 'tx_bytes': 0, 'process_count': 1}, 'bindings': []}
        third = manager._sample('local')
        self.assertEqual(third['rates']['state'], 'unavailable')
        self.assertIsNone(third['host']['cpu_percent'])
