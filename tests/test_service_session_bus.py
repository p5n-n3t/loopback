import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from helpers import RUNTIME  # noqa: F401
import service


class SessionBusTests(unittest.TestCase):
    def test_headless_systemctl_uses_only_current_users_owned_bus(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(service.os, "getuid", return_value=1234),
            patch.object(service.Path, "stat", return_value=SimpleNamespace(st_uid=1234)),
            patch.object(service.Path, "is_socket", return_value=True),
            patch.object(service.subprocess, "run") as run,
        ):
            service._run(["systemctl", "--user", "is-active", "loopback.service"])
            env = run.call_args.kwargs["env"]
            self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1234")
            self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1234/bus")

    def test_foreign_owned_runtime_directory_is_rejected(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(service.os, "getuid", return_value=1234),
            patch.object(service.Path, "stat", return_value=SimpleNamespace(st_uid=999)),
            patch.object(service.subprocess, "run") as run,
        ):
            service._run(["systemctl", "--user", "status"])
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", run.call_args.kwargs["env"])

    def test_explicit_session_environment_is_preserved(self):
        with patch.object(service.subprocess, "run") as run:
            service._run(["systemctl", "--user", "status"], env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/custom"})
            self.assertEqual(run.call_args.kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/custom")
