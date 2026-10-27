"""Cached worker telemetry must not invent a live service status."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from helpers import fresh_config_dir, load_server


class WorkerTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fresh_config_dir()
        cls.server = load_server()
        cls.manager = cls.server.CTX.workers
        cls.topology = cls.server.CTX.topology
        cls.manager.save({'name': 'sample', 'target': 'local', 'transport': 'stdio',
                          'command': ['echo', 'ok'], 'port': 3031, 'exposure': 'private'})

    def tearDown(self):
        self.manager.cache = None

    def graph(self, worker=None, age=0):
        self.manager.cache = None if worker is None else {
            'sampled_at': time.time() - age, 'workers': [{'target': 'local', **worker}]}
        with patch.object(self.manager, 'snapshot', side_effect=AssertionError('build must not probe')):
            graph = self.topology.build()
        service = next(n for n in graph['nodes'] if n['id'] == 'service:sample')
        hosting = next(e for e in graph['edges'] if e['id'] == 'host->service:sample')
        gateway = next(n for n in graph['nodes'] if n['id'] == 'gateway')
        return service, hosting, gateway

    def test_cold_cache_is_untested_not_unreachable(self):
        service, hosting, _ = self.graph()
        self.assertEqual(service['status'], 'untested')
        self.assertEqual(hosting['status'], 'untested')

    def test_fresh_process_state_controls_service_and_hosting_status(self):
        for running, expected in [(True, 'healthy'), (False, 'disconnected')]:
            with self.subTest(running=running):
                service, hosting, _ = self.graph({'status': 'healthy', 'bindings': [{'name': 'sample', 'running': running}]})
                self.assertEqual(service['status'], expected)
                self.assertEqual(hosting['status'], expected)

    def test_old_sample_is_stale_and_metrics_are_not_presented_as_live(self):
        service, hosting, gateway = self.graph({
            'status': 'healthy', 'heartbeat_at': 123, 'host': {'cpu_count': 4},
            'worker': {'rss_bytes': 1024}, 'bindings': [{'name': 'sample', 'running': True}]},
            age=self.topology.stale_after_seconds + 1)
        self.assertEqual(service['status'], 'stale')
        self.assertEqual(hosting['status'], 'stale')
        self.assertEqual(service['detail']['heartbeat_at'], 123)
        self.assertEqual(gateway['detail']['worker_status'], 'stale')
        self.assertIsNone(gateway['detail']['worker_metrics'])
        self.assertIsNone(gateway['detail']['host_metrics'])

    def test_failed_fresh_sample_is_unreachable_even_with_old_heartbeat(self):
        service, hosting, _ = self.graph({'status': 'unreachable', 'heartbeat_at': 123})
        self.assertEqual(service['status'], 'unreachable')
        self.assertEqual(hosting['status'], 'unreachable')
        self.assertEqual(service['detail']['heartbeat_at'], 123)

    def test_fresh_sample_with_no_binding_is_disconnected(self):
        service, hosting, _ = self.graph({'status': 'healthy', 'bindings': []})
        self.assertEqual(service['status'], 'disconnected')
        self.assertEqual(hosting['status'], 'disconnected')


if __name__ == '__main__':
    unittest.main()
