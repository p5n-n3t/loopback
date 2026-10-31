"""H02/H03/H22: jobs and terminals are bound to a stable owner key, not a display name."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from jobs import JobManager, QuotaExceeded, TerminalManager, process_start_ticks
from redaction import redact_text

ALICE = "oauth:user-1#client-claude"
BOB = "oauth:user-1#client-chatgpt"


def _wait(job_manager: JobManager, job_id: str, owner: str | None = None) -> dict:
    for _ in range(100):
        info = job_manager.info(job_id, owner=owner)
        if not info["running"]:
            return info
        time.sleep(0.05)
    return info


class JobOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="lb-own-")
        self.jobs = JobManager(Path(self.td), "/bin/sh")

    def test_cross_owner_access_is_reported_as_not_found(self):
        job = self.jobs.start("echo alice-secret-output", self.td, principal="Same Display", owner=ALICE)
        _wait(self.jobs, job["id"], ALICE)
        self.assertEqual([j["id"] for j in self.jobs.list(owner=ALICE)], [job["id"]])
        self.assertEqual(self.jobs.list(owner=BOB), [])
        for call in (lambda: self.jobs.info(job["id"], owner=BOB),
                     lambda: self.jobs.output(job["id"], owner=BOB),
                     lambda: self.jobs.stop(job["id"], owner=BOB)):
            with self.assertRaises(KeyError) as ctx:
                call()
            self.assertIn("job not found", str(ctx.exception))
        self.assertIn("alice-secret-output", self.jobs.output(job["id"], owner=ALICE)["content"])
        # The administrator view (owner=None) still inspects everything.
        self.assertEqual(len(self.jobs.list()), 1)

    def test_display_name_is_not_an_authorization_key(self):
        job = self.jobs.start("true", self.td, principal="shared-name", owner=ALICE)
        with self.assertRaises(KeyError):
            self.jobs.info(job["id"], owner="shared-name")

    def test_legacy_unowned_metadata_is_admin_only_after_migration(self):
        jobs_dir = Path(self.td) / "jobs"
        (jobs_dir / "legacy1.json").write_text(json.dumps({
            "id": "legacy1", "command": "echo old", "cwd": self.td, "pid": 999999, "started_at": time.time(),
            "log_path": str(jobs_dir / "legacy1.log"), "principal": "Claude", "finished_at": time.time(), "returncode": 0,
        }), encoding="utf-8")
        reloaded = JobManager(Path(self.td), "/bin/sh")
        self.assertEqual(reloaded.list(owner=ALICE), [])
        self.assertEqual([j["id"] for j in reloaded.list()], ["legacy1"])
        self.assertFalse(reloaded.info("legacy1")["owned"])

    def test_running_quota_per_owner(self):
        self.jobs.max_running_per_owner = 1
        self.jobs.start("sleep 5", self.td, owner=ALICE)
        with self.assertRaises(QuotaExceeded):
            self.jobs.start("sleep 5", self.td, owner=ALICE)
        other = self.jobs.start("true", self.td, owner=BOB)  # another owner is unaffected
        self.assertTrue(other["id"])
        for j in self.jobs.list():
            self.jobs.stop(j["id"], 9)

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX permissions")
    def test_log_and_metadata_files_are_owner_only(self):
        job = self.jobs.start("echo hi", self.td, owner=ALICE)
        _wait(self.jobs, job["id"], ALICE)
        jobs_dir = Path(self.td) / "jobs"
        self.assertEqual(stat.S_IMODE(os.stat(jobs_dir).st_mode), 0o700)
        for suffix in (".log", ".json"):
            mode = stat.S_IMODE(os.stat(jobs_dir / f"{job['id']}{suffix}").st_mode)
            self.assertEqual(mode, 0o600, suffix)

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "needs /proc")
    def test_recovered_job_with_reused_pid_is_not_signalled(self):
        jobs_dir = Path(self.td) / "jobs"
        # PID 1 is alive but its start time will not match the recorded one.
        (jobs_dir / "reused.json").write_text(json.dumps({
            "id": "reused", "command": "sleep 100", "cwd": self.td, "pid": os.getpid(), "started_at": time.time(),
            "log_path": str(jobs_dir / "reused.log"), "owner": ALICE, "start_ticks": "1", "finished_at": None,
        }), encoding="utf-8")
        reloaded = JobManager(Path(self.td), "/bin/sh")
        info = reloaded.info("reused", owner=ALICE)
        self.assertFalse(info["running"], "a PID whose start time differs is a different process")
        self.assertIsNotNone(process_start_ticks(os.getpid()))
        reloaded.stop("reused", owner=ALICE)  # must not signal this test process
        self.assertTrue(True)


@unittest.skipUnless(TerminalManager.available(), "needs a POSIX pty")
class TerminalOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.terms = TerminalManager("/bin/sh")
        self.cwd = tempfile.mkdtemp(prefix="lb-term-")

    def tearDown(self):
        self.terms.close_all()

    def test_every_lifecycle_call_checks_owner(self):
        term = self.terms.start(self.cwd, owner=ALICE)
        tid = term["id"]
        self.assertEqual(self.terms.list(owner=BOB), [])
        for call in (lambda: self.terms.write(tid, "id\n", owner=BOB),
                     lambda: self.terms.read(tid, owner=BOB),
                     lambda: self.terms.resize(tid, 10, 10, owner=BOB),
                     lambda: self.terms.close(tid, owner=BOB),
                     lambda: self.terms.info(tid, owner=BOB)):
            with self.assertRaises(KeyError):
                call()
        self.assertTrue(self.terms.info(tid, owner=ALICE)["alive"])
        self.terms.write(tid, "echo ok\n", owner=ALICE)
        self.terms.close(tid, owner=ALICE)

    def test_terminal_quota_and_idle_reaping(self):
        self.terms.max_per_owner = 1
        first = self.terms.start(self.cwd, owner=ALICE)
        with self.assertRaises(QuotaExceeded):
            self.terms.start(self.cwd, owner=ALICE)
        self.terms.idle_timeout = 60
        closed = self.terms.reap_idle(now=time.time() + 120)
        self.assertEqual(closed, [first["id"]])
        self.assertFalse(self.terms.info(first["id"])["alive"])


class StateProtectionTests(unittest.TestCase):
    def test_config_dir_is_denied_even_when_policy_drops_defaults(self):
        from policy import PolicyError, PolicyStore

        home = Path(tempfile.mkdtemp(prefix="lb-home-"))
        cfg = home / ".config" / "loopback-custom"
        cfg.mkdir(parents=True)
        (cfg / "jobs").mkdir()
        policy = PolicyStore(cfg, home=str(home))
        policy.save({"profile": "standard", "allowed_roots": [str(home)], "denied_roots": []})
        self.assertEqual(policy.check_path(home), home.resolve())
        for path in (cfg / "approvals.json", cfg / "jobs" / "abc.log"):
            with self.assertRaises(PolicyError):
                policy.check_path(path)
        self.assertIn(str(cfg.resolve()), policy.target_policy("local")["denied_roots"])


class RedactionTests(unittest.TestCase):
    def test_known_token_shapes_are_hidden_and_counted(self):
        text = ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
                "GITHUB=ghp_abcdefghijklmnopqrstuvwxyz0123\n"
                "password=hunter22secret https://user:pa55word@example.com/x\n"
                "sha256 " + "a" * 64)
        out, hidden = redact_text(text)
        self.assertGreaterEqual(hidden, 4)
        for secret in ("abcdefghijklmnopqrstuvwxyz", "ghp_abcdef", "hunter22secret", "pa55word"):
            self.assertNotIn(secret, out)
        self.assertIn("a" * 64, out, "checksums are not secrets")


if __name__ == "__main__":
    unittest.main()
