"""Diagnose and guard against the intermittent 'module server has no attribute 'app'``. The cause remains unconfirmed: repeated module loading
is a hypothesis, not an established fix. These checks characterize reload behavior.

This file exercises the double-reload sequence directly and verifies the module
contract: every ``load_server()`` call must return a module that has ``app``.
"""
from __future__ import annotations

import sys
import unittest

from helpers import fresh_config_dir, load_server, RUNTIME  # noqa: F401


class ServerReloadTests(unittest.TestCase):
    """Verify server module consistency across repeated fresh_config_dir + load_server cycles."""

    def _reload_once(self, **overrides):
        fresh_config_dir(**overrides)
        server = load_server()
        self.assertTrue(hasattr(server, "app"),
                        f"server module at {server.__file__} lacks 'app' after reload")
        self.assertTrue(hasattr(server, "CTX"),
                        f"server module at {server.__file__} lacks 'CTX' after reload")
        self.assertTrue(hasattr(server, "AUTH"),
                        f"server module at {server.__file__} lacks 'AUTH' after reload")
        return server

    def test_first_load_has_app(self):
        server = self._reload_once()
        self.assertTrue(callable(server.app))

    def test_double_reload_retains_app(self):
        server1 = self._reload_once()
        server2 = self._reload_once()
        self.assertTrue(callable(server2.app))
        self.assertIsNot(server1.CTX, server2.CTX,
                         "second load must create a fresh CTX, not reuse the first")

    def test_triple_reload_with_shield_toggle(self):
        self._reload_once(shield={"enabled": False})
        self._reload_once(shield={"enabled": True, "auth_per_minute": 100,
                                        "auth_burst": 50, "default_per_minute": 1000,
                                        "default_burst": 100})
        s3 = self._reload_once(shield={"enabled": False})
        self.assertTrue(callable(s3.app))

    def test_modules_are_cleared_by_fresh_config_dir(self):
        """After fresh_config_dir, the five core modules must not be in sys.modules."""
        fresh_config_dir()
        for name in ("server", "bootstrap", "fabric_api", "maintenance_api", "dashboard"):
            self.assertNotIn(name, sys.modules,
                             f"{name} should have been popped from sys.modules")


class HelperIntegrityTests(unittest.TestCase):
    """Verify that fresh_config_dir and load_server helper functions are correct."""

    def test_fresh_config_dir_creates_config_json(self):
        import json
        import os
        cfg = fresh_config_dir()
        config_path = os.path.join(cfg, "config.json")
        self.assertTrue(os.path.isfile(config_path))
        with open(config_path) as f:
            data = json.load(f)
        self.assertEqual(data["schema_version"], 2)
        self.assertFalse(data["shield"]["enabled"])

    def test_load_server_returns_module_not_none(self):
        fresh_config_dir()
        server = load_server()
        self.assertIsNotNone(server)
        self.assertEqual(server.__name__, "server")


if __name__ == "__main__":
    unittest.main()
