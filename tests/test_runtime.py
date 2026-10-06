from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))

_tmp = tempfile.TemporaryDirectory()
os.environ["LOOPBACK_CONFIG_DIR"] = _tmp.name

from policy import ApprovalStore, Policy
from jobs import JobManager, TerminalManager
from nodes import NodeRegistry
from metrics import host_metrics, process_info, process_list
from browser import BrowserController
from plugin_packager import build as build_plugin


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.policy = Policy(Path(self.dir.name) / "policy.json")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_profiles_and_tool_override(self) -> None:
        self.assertFalse(self.policy.tool_allowed("command_run"))
        self.policy.set_profile("trusted")
        self.assertTrue(self.policy.tool_allowed("command_run"))
        self.policy.set_tool("command_run", False)
        self.assertFalse(self.policy.tool_allowed("command_run"))

    def test_filesystem_allow_and_deny(self) -> None:
        home = Path(self.dir.name) / "home"
        safe = home / "project" / "file.txt"
        denied = home / ".ssh" / "id_ed25519"
        safe.parent.mkdir(parents=True)
        denied.parent.mkdir(parents=True)
        value = self.policy.load()
        value["filesystem"] = {"allow": [str(home)], "deny": [str(home / ".ssh")]}
        self.policy.save(value)
        self.assertEqual(self.policy.path_allowed(safe), safe.resolve())
        with self.assertRaises(PermissionError):
            self.policy.path_allowed(denied)

    def test_command_classification(self) -> None:
        self.assertEqual(self.policy.command_decision("echo hi"), "allow")
        self.assertEqual(self.policy.command_decision("sudo apt update"), "confirm")
        self.assertEqual(self.policy.command_decision("rm -rf /"), "deny")


class ApprovalTests(unittest.TestCase):
    def test_one_time_approval(self) -> None:
        store = ApprovalStore()
        item = store.request("sudo true", ttl=30)
        self.assertFalse(store.consume(item["id"], "sudo true"))
        self.assertTrue(store.approve(item["id"]))
        self.assertTrue(store.consume(item["id"], "sudo true"))
        self.assertFalse(store.consume(item["id"], "sudo true"))


class JobTests(unittest.TestCase):
    def test_background_job_output(self) -> None:
        manager = JobManager()
        job = manager.start("printf 'hello-loopback'", os.getcwd(), "/bin/sh")
        deadline = time.time() + 5
        while manager.get(job["id"]).process.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        output = manager.output(job["id"])
        self.assertIn("hello-loopback", output["content"])
        self.assertEqual(output["returncode"], 0)

    def test_terminal_round_trip(self) -> None:
        manager = TerminalManager()
        term = manager.start("/bin/sh", os.getcwd())
        manager.write(term["id"], "printf 'pty-loopback\\n'\n")
        content = ""
        deadline = time.time() + 3
        while "pty-loopback" not in content and time.time() < deadline:
            time.sleep(0.05)
            content += manager.read(term["id"])["content"]
        self.assertIn("pty-loopback", content)
        manager.close(term["id"])


class NodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.registry = NodeRegistry(Path(self.dir.name) / "nodes.json")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_local_node(self) -> None:
        nodes = self.registry.list()
        self.assertEqual(nodes[0]["name"], "local")
        result = self.registry.command("local", "printf node-ok", shell="/bin/sh")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "node-ok")

    def test_ssh_node_config(self) -> None:
        node = self.registry.upsert({"name": "ora3", "transport": "ssh", "target": "jq@ora3"})
        self.assertEqual(node["name"], "ora3")
        self.assertEqual(self.registry.get("ora3")["target"], "jq@ora3")
        self.registry.remove("ora3")
        with self.assertRaises(KeyError):
            self.registry.get("ora3")


class BrowserTests(unittest.TestCase):
    def test_browser_status_is_structured(self) -> None:
        browser = BrowserController()
        status = browser.status()
        self.assertIn("available", status)
        self.assertIn("command", status)


class PluginPackageTests(unittest.TestCase):
    def test_portable_plugin_package(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "loopback.zip"
            build_plugin("https://loopback.example.com/mcp", out, "3.0.0")
            import zipfile
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                self.assertIn("plugin.json", names)
                self.assertIn("mcp.json", names)
                self.assertIn("skills/loopback/SKILL.md", names)
                manifest = json.loads(archive.read("plugin.json"))
                self.assertEqual(manifest["name"], "loopback")
                mcp = json.loads(archive.read("mcp.json"))
                self.assertEqual(mcp["mcpServers"]["loopback"]["url"], "https://loopback.example.com/mcp")

    def test_plugin_rejects_non_https(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                build_plugin("http://127.0.0.1:2026/mcp", Path(td) / "bad.zip", "3.0.0")


class MetricsTests(unittest.TestCase):
    def test_metrics_and_processes(self) -> None:
        metrics = host_metrics()
        self.assertTrue(metrics["available"])
        self.assertIn("cpu_percent", metrics)
        rows = process_list(10)
        self.assertGreater(len(rows), 0)
        current = process_info(os.getpid())
        self.assertEqual(current["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
