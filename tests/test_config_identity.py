from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import RUNTIME  # noqa: F401 - sets sys.path

import config as config_mod
import identity


class IdentityTests(unittest.TestCase):
    def test_detect_uses_account_database(self):
        ident = identity.detect()
        self.assertTrue(ident.username)
        self.assertTrue(Path(ident.home).is_absolute())
        self.assertTrue(os.path.isfile(ident.shell) and os.access(ident.shell, os.X_OK))
        self.assertIn(ident.platform, {"linux", "darwin", "windows"})
        self.assertIn(ident.service_manager, {"systemd-user", "launchd", "windows-task-scheduler", "none"})
        self.assertNotIn("/home/jq", ident.home)

    def test_config_dir_override_and_xdg(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"LOOPBACK_CONFIG_DIR": td}):
                self.assertEqual(identity.detect().config_dir, td)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": td}, clear=False):
                os.environ.pop("LOOPBACK_CONFIG_DIR", None)
                if identity.platform_name() != "windows":
                    self.assertEqual(identity.detect().config_dir, str(Path(td) / "loopback"))

    @unittest.skipUnless(identity.pwd is not None, "POSIX only")
    def test_sudo_user_is_preferred_over_root(self):
        import pwd

        me = pwd.getpwuid(os.getuid())

        class Root:
            pw_name, pw_uid, pw_gid, pw_dir, pw_shell = "root", 0, 0, "/root", "/bin/sh"

        with mock.patch.dict(os.environ, {"SUDO_USER": me.pw_name}), \
             mock.patch.object(identity.os, "geteuid", return_value=0), \
             mock.patch.object(identity.pwd, "getpwuid", return_value=Root()):
            ident = identity.detect(prefer_sudo_user=True)
            self.assertEqual(ident.username, me.pw_name)
            self.assertEqual(ident.home, me.pw_dir)
            self.assertTrue(ident.invoked_via_sudo)
            self.assertFalse(ident.is_root)
            runtime_ident = identity.detect(prefer_sudo_user=False)
            self.assertEqual(runtime_ident.username, "root")
            self.assertTrue(runtime_ident.is_root)

    def test_default_shell_fallbacks(self):
        self.assertTrue(os.path.isfile(identity.default_shell(preferred="/definitely/not/a/shell")))
        self.assertEqual(identity.default_shell(preferred="/bin/sh"), "/bin/sh") if os.path.exists("/bin/sh") else None

    def test_virtualization_detection_returns_known_value(self):
        self.assertIn(identity.detect_virtualization(), {"physical", "virtual-machine", "container", "unknown"})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.environ["LOOPBACK_CONFIG_DIR"] = self.td.name
        self.ident = identity.detect()

    def tearDown(self):
        self.td.cleanup()

    def test_defaults_validate_and_home_is_initial_cwd(self):
        store = config_mod.ConfigStore(self.ident)
        self.assertEqual(config_mod.validate(store.data), [])
        self.assertEqual(store.effective_initial_cwd(), self.ident.home)
        self.assertFalse(store.data["auth"]["legacy_machine_token_mcp"])
        self.assertEqual(store.resource_uris()[0], "http://127.0.0.1:2026/mcp")

    def test_validation_rejects_bad_values(self):
        store = config_mod.ConfigStore(self.ident)
        for bad in ({"port": 70000}, {"public_host": "not a host!"}, {"ingress": {"tailscale": {"enabled": True, "mode": "open"}}},
                    {"ingress": {"cloudflare": {"enabled": True, "hostname": "lb"}}}, {"sandbox": {"backend": "magic"}},
                    {"maintenance": {"update_channel": "nightly"}}, {"auth": {"access_ttl_seconds": 5}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                store.save(bad)
        self.assertEqual(store.data["port"], 2026)

    def test_save_backup_and_rollback(self):
        store = config_mod.ConfigStore(self.ident)
        store.save({"port": 2030})
        store.save({"port": 2031})
        self.assertEqual(store.data["port"], 2031)
        self.assertTrue(list(store.backup_dir.glob("config-*.json")))
        store.rollback()
        self.assertEqual(store.data["port"], 2030)
        env = config_mod.parse_env_file(store.legacy_env_path)
        self.assertEqual(env["LOOPBACK_PORT"], "2030")

    def test_legacy_env_migration_preserves_ingress(self):
        Path(self.td.name, "config.env").write_text(
            "LOOPBACK_PORT=2026\nLOOPBACK_PUBLIC_HOST=desk.tail1234.ts.net\nLOOPBACK_INGRESS=tailscale\nLOOPBACK_TAILSCALE_HTTPS_PORT=443\n"
            "LOOPBACK_TIMEOUT=300\nLOOPBACK_ACCESS_MODE=standard\n")
        store = config_mod.ConfigStore(self.ident)
        self.assertTrue(store.migrated)
        ts = store.data["ingress"]["tailscale"]
        self.assertTrue(ts["enabled"])
        self.assertEqual(ts["mode"], "funnel")  # v3 tailscale meant Funnel; preserve, do not silently privatise
        self.assertEqual(ts["dns_name"], "desk.tail1234.ts.net")
        self.assertEqual(store.data["command_timeout"], 300)
        self.assertTrue(store.data["auth"]["legacy_machine_token_mcp"])
        store.persist_migration()
        self.assertTrue(Path(self.td.name, "config.json").exists())
        self.assertIn("https://desk.tail1234.ts.net/mcp", store.resource_uris())

    def test_both_ingress_yield_two_resource_uris(self):
        store = config_mod.ConfigStore(self.ident)
        store.save({"ingress": {"cloudflare": {"enabled": True, "hostname": "lb.example.com", "tunnel_id": "abc"},
                                "tailscale": {"enabled": True, "mode": "serve", "dns_name": "desk.tail1234.ts.net"}}})
        uris = store.resource_uris()
        self.assertIn("https://lb.example.com/mcp", uris)
        self.assertIn("https://desk.tail1234.ts.net/mcp", uris)
        self.assertEqual(store.primary_host(), "lb.example.com")

    def test_secret_file_permissions(self):
        store = config_mod.ConfigStore(self.ident)
        store.save({"display_name": "Desk"})
        self.assertEqual(oct(store.path.stat().st_mode & 0o777), "0o600")

    def test_home_with_spaces_is_handled(self):
        weird = Path(self.td.name) / "home with spaces" / "ålice"
        weird.mkdir(parents=True)
        store = config_mod.ConfigStore(self.ident)
        store.save({"initial_cwd": str(weird)})
        self.assertEqual(store.effective_initial_cwd(), str(weird))
        data = json.loads(store.path.read_text())
        self.assertEqual(data["initial_cwd"], str(weird))


if __name__ == "__main__":
    unittest.main()
