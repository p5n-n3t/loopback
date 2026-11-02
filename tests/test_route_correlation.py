import unittest
from helpers import RUNTIME  # noqa: F401
from route_correlation import correlate, listener_for_route


class CorrelationTests(unittest.TestCase):
    def test_no_name_or_port_inference_and_duplicate_uuid_ambiguous(self):
        catalog = {"tunnels": [{"id": "abc", "name": "ora7", "ingress": []}]}
        hosts = [{"target": "ora7", "status": "ok", "routes": []}]
        self.assertIsNone(correlate(catalog, hosts)["tunnels"][0]["host"])
        hosts[0]["routes"] = [{"tunnel_id": "abc"}]
        self.assertEqual(correlate(catalog, hosts)["tunnels"][0]["host"], "ora7")
        hosts.append({"target": "ora8", "status": "ok", "routes": [{"tunnel_id": "abc"}]})
        self.assertEqual(correlate(catalog, hosts)["tunnels"][0]["host_association"], "ambiguous")

    def test_offline_observation_does_not_prove_current_host(self):
        c = {"tunnels": [{"id": "abc", "ingress": []}]}
        self.assertIsNone(correlate(c, [{"target": "ora7", "status": "unreachable", "routes": [{"tunnel_id": "abc"}]}])["tunnels"][0]["host"])

    def test_explicit_mapping_still_requires_registered_reachable_host(self):
        c = {"tunnels": [{"id": "abc", "ingress": []}]}
        self.assertIsNone(correlate(c, [], {"abc": "missing"})["tunnels"][0]["host"])
        self.assertEqual(correlate(c, [{"target": "local", "status": "ok"}], {"abc": "local"})["tunnels"][0]["host"], "local")

    def test_local_backend_requires_address_and_unique_listener(self):
        route = {"target": "http://127.0.0.1:2030/mcp"}
        self.assertEqual(listener_for_route(route, [{"port": 2030, "address": "10.0.0.1"}])["state"], "not_observed")
        listener = {"port": 2030, "address": "127.0.0.1", "process": "node"}
        self.assertEqual(listener_for_route(route, [listener])["process"], "node")
        self.assertEqual(listener_for_route(route, [listener, listener])["state"], "ambiguous")
