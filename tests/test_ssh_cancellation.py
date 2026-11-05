"""SSH executor cancellation cleanup, using only local child processes."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import RUNTIME

if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from ssh_targets import SSHTargetStore


class _Policy:
    def check_command(self, *args, **kwargs):
        return None

    def check_path(self, *args, **kwargs):
        return None


class SSHCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loopback-ssh-cancel-")
        self.addCleanup(self.temp.cleanup)
        cfg = Path(self.temp.name)
        (cfg / "ssh_targets.json").write_text(json.dumps([
            {"name": "unit-test", "host": "localhost", "port": 22, "user": "test"}
        ]), encoding="utf-8")
        self.store = SSHTargetStore(SimpleNamespace(cfg_dir=cfg, policy=_Policy()))

    async def test_cancellation_stops_and_reaps_local_ssh_process_group(self):
        original_spawn = asyncio.create_subprocess_exec
        import ssh_targets

        original_stop = ssh_targets._stop_process_group
        spawned = asyncio.Event()
        stopping = asyncio.Event()
        children = []

        async def record_spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            spawned.set()
            return child

        async def delayed_stop(child):
            stopping.set()
            await asyncio.sleep(0.05)
            await original_stop(child)

        with patch.object(self.store, "_ssh_args", return_value=[
            sys.executable, "-c", "import time; time.sleep(60)"
        ]), patch("asyncio.create_subprocess_exec", side_effect=record_spawn), \
                patch("ssh_targets._stop_process_group", side_effect=delayed_stop):
            call = asyncio.create_task(self.store.execute("unit-test", "echo benign"))
            await asyncio.wait_for(spawned.wait(), timeout=2)
            child = children[0]
            call.cancel()
            await asyncio.wait_for(stopping.wait(), timeout=2)
            call.cancel()  # a repeated disconnect must not interrupt cleanup
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(call, timeout=5)

        self.assertIsNotNone(child.returncode, "cancelled SSH subprocess must be reaped")

    async def test_success_collects_output(self):
        with patch.object(self.store, "_ssh_args", return_value=[
            sys.executable, "-c", "print('local test output')"
        ]):
            result = await self.store.execute("unit-test", "echo benign")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("local test output", result["stdout"])
        self.assertFalse(result["timed_out"])

    async def test_timeout_stops_child_and_reports_timeout(self):
        with patch.object(self.store, "_ssh_args", return_value=[
            sys.executable, "-c", "import time; time.sleep(60)"
        ]):
            result = await self.store.execute("unit-test", "echo benign", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNotNone(result["returncode"])


if __name__ == "__main__":
    unittest.main()
