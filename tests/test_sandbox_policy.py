"""Policy decisions and OS-level enforcement."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import RUNTIME  # noqa: F401

import sandbox
from policy import ApprovalRequired, PolicyError, PolicyStore, canonical_profile

LANDLOCK = sandbox.landlock_abi() > 0


class PolicyModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "cfg"
        self.home = Path(self.tmp.name) / "home dir"
        (self.home / "projects").mkdir(parents=True)
        (self.home / ".ssh").mkdir()
        (self.home / "shared").mkdir()
        self.policy = PolicyStore(self.cfg, home=str(self.home))
        self.policy.sandbox_active = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_home_rw_with_credential_dirs_denied(self):
        self.assertEqual(self.policy.profile, "restricted")
        self.assertEqual(self.policy.check_path(self.home / "projects" / "a.txt", write=True), (self.home / "projects" / "a.txt").resolve())
        with self.assertRaises(PolicyError):
            self.policy.check_path(self.home / ".ssh" / "id_ed25519")
        with self.assertRaises(PolicyError):
            self.policy.check_path("/etc/passwd")

    def test_tilde_expands_to_service_home_not_process_home(self):
        self.assertEqual(self.policy.check_path("~/projects"), (self.home / "projects").resolve())

    def test_read_only_root_mode(self):
        self.policy.save({"allowed_roots": [{"path": str(self.home / "projects"), "mode": "rw"}, {"path": str(self.home / "shared"), "mode": "ro"}]})
        self.policy.check_path(self.home / "shared" / "x", write=False)
        with self.assertRaises(PolicyError):
            self.policy.check_path(self.home / "shared" / "x", write=True)
        with self.assertRaises(PolicyError):
            self.policy.check_path(self.home / "elsewhere")

    def test_traversal_and_symlink_resolution(self):
        link = self.home / "projects" / "sneaky"
        link.symlink_to(self.home / ".ssh")
        with self.assertRaises(PolicyError):
            self.policy.check_path(link / "id_rsa")
        with self.assertRaises(PolicyError):
            self.policy.check_path(str(self.home / "projects" / ".." / ".ssh" / "id_rsa"))

    def test_profiles_and_aliases(self):
        self.assertEqual(canonical_profile("standard"), "restricted")
        self.assertEqual(canonical_profile("trusted"), "admin")
        self.policy.save({"profile": "trusted"})
        self.assertEqual(self.policy.profile, "admin")
        self.policy.check_command("sudo apt remove x")  # no soft approvals in admin
        with self.assertRaises(PolicyError):
            self.policy.check_command("rm -rf /")
        self.policy.save({"profile": "locked"})
        with self.assertRaises(PolicyError):
            self.policy.check_path(self.home / "projects")

    def test_restricted_requires_sandbox_unless_acknowledged(self):
        self.policy.sandbox_active = False
        ok, reason = self.policy.shell_enabled()
        self.assertFalse(ok)
        self.assertIn("sandbox", reason)
        self.policy.sandbox_acknowledged = True
        self.assertTrue(self.policy.shell_enabled()[0])
        self.policy.sandbox_acknowledged = False
        self.policy.save({"profile": "admin"})
        self.assertTrue(self.policy.shell_enabled()[0])

    def test_read_only_profile_shell_depends_on_sandbox(self):
        self.policy.save({"profile": "read-only"})
        self.assertTrue(self.policy.shell_enabled()[0])
        self.assertEqual(self.policy.file_mode(), "ro")
        self.policy.sandbox_active = False
        self.assertFalse(self.policy.shell_enabled()[0])

    def test_approval_roundtrip_is_target_bound(self):
        with self.assertRaises(ApprovalRequired) as ctx:
            self.policy.check_command("sudo systemctl stop demo")
        aid = ctx.exception.approval_id
        self.assertIn(f"approval_required:{aid}", str(ctx.exception))
        self.policy.approve(aid)
        self.policy.check_command("sudo systemctl stop demo", approval_id=aid)
        with self.assertRaises(ApprovalRequired):
            self.policy.check_command("sudo systemctl stop demo", approval_id=aid)  # single use
        with self.assertRaises(ApprovalRequired):
            self.policy.check_command("sudo systemctl stop demo", approval_id=aid, target="web-1")  # other target

    def test_per_target_roots_do_not_leak(self):
        self.policy.set_target_policy("web-1", {"allowed_roots": ["/srv/app", "/var/log/app"], "denied_roots": []})
        self.policy.check_path("/srv/app/compose.yml", target="web-1")
        with self.assertRaises(PolicyError):
            self.policy.check_path("/srv/app/compose.yml")  # local untouched
        with self.assertRaises(PolicyError):
            self.policy.check_path("/srv/app/compose.yml", target="web-2")  # other node untouched
        perms = self.policy.effective_permissions("web-1")
        self.assertEqual(perms["enforcement"], "gateway-path-check")
        self.assertTrue(self.policy.remove_target_policy("web-1"))

    def test_validation_rejects_bad_regex_and_values(self):
        with self.assertRaises(ValueError):
            self.policy.save({"deny_command_patterns": ["("]})
        with self.assertRaises(ValueError):
            self.policy.save({"approval_ttl_seconds": 1})
        with self.assertRaises(ValueError):
            self.policy.save({"allow_shell": "yes"})

    def test_effective_permissions_explains(self):
        perms = self.policy.effective_permissions()
        self.assertEqual(perms["other_locations"], "denied")
        self.assertEqual(perms["enforcement"], "os-sandbox")
        self.assertTrue(perms["allowed_roots"][0]["resolved"].startswith(str(self.home.resolve())))


class LandlockRuleTests(unittest.TestCase):
    def test_denied_subtree_forces_child_enumeration(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            (home / ".ssh").mkdir(parents=True)
            (home / "docs").mkdir()
            (home / "note.txt").write_text("x")
            plan = sandbox.SandboxPlan(allowed=[sandbox.RootRule(str(home), "rw")], denied=[(home / ".ssh").resolve()], allow_tmp=False)
            rules = dict(sandbox.landlock_rules(plan, abi=3))
            self.assertNotIn(str((home / ".ssh").resolve()), rules)
            self.assertIn(str((home / "docs").resolve()), rules)
            # the parent keeps directory-level rights only: no READ_FILE/WRITE_FILE through the ancestor
            parent_bits = rules[str(home.resolve())]
            self.assertFalse(parent_bits & sandbox.FS_READ_FILE)
            self.assertFalse(parent_bits & sandbox.FS_WRITE_FILE)
            self.assertTrue(parent_bits & sandbox.FS_READ_DIR)
            # a plain file directly inside gets file-compatible bits only
            self.assertFalse(rules[str((home / "note.txt").resolve())] & sandbox.FS_READ_DIR)

    def test_ro_root_has_no_write_bits(self):
        with tempfile.TemporaryDirectory() as td:
            plan = sandbox.SandboxPlan(allowed=[sandbox.RootRule(td, "ro")], denied=[], allow_tmp=False)
            bits = dict(sandbox.landlock_rules(plan, abi=3))[str(Path(td).resolve())]
            self.assertFalse(bits & sandbox.FS_WRITE_FILE)
            self.assertFalse(bits & sandbox.FS_MAKE_REG)
            self.assertTrue(bits & sandbox.FS_READ_FILE)

    def test_bwrap_args_mask_denied(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "h"
            (home / ".aws").mkdir(parents=True)
            be = sandbox.BwrapBackend()
            args = be.args(sandbox.SandboxPlan(allowed=[sandbox.RootRule(str(home), "rw")], denied=[(home / ".aws").resolve()], cwd=str(home)))
            self.assertIn("--tmpfs", args)
            self.assertIn(str((home / ".aws").resolve()), args)
            self.assertIn("--bind", args)
            self.assertNotIn("--unshare-net", args)

    def test_seatbelt_profile_has_deny_rules(self):
        be = sandbox.SeatbeltBackend()
        prof = be.profile(sandbox.SandboxPlan(allowed=[sandbox.RootRule("/Users/alice/My Work", "rw")], denied=[Path("/Users/alice/.ssh")]))
        self.assertIn('(deny default)', prof)
        self.assertIn('(allow file-read* file-write* (subpath "/Users/alice/My Work"))', prof)
        self.assertIn('(deny file-read* file-write* (subpath "/Users/alice/.ssh"))', prof)

    def test_none_backend_is_honest(self):
        st = sandbox.NoneBackend().status()
        self.assertFalse(st.active)
        self.assertFalse(st.enforces_denied_subpaths)
        mgr = sandbox.SandboxManager("none")
        self.assertFalse(mgr.enforced())
        self.assertFalse(mgr.status()["restricted_shell_permitted"])
        self.assertTrue(sandbox.SandboxManager("none", acknowledge_unenforced=True).status()["restricted_shell_permitted"])

    def test_wrap_strips_credential_env(self):
        os.environ["GITHUB_TOKEN"] = "ghp_secret"
        try:
            _, _, env = sandbox.SandboxManager("none").wrap(["/bin/sh", "-c", "true"], sandbox.SandboxPlan(allowed=[], denied=[]))
            self.assertNotIn("GITHUB_TOKEN", env)
        finally:
            os.environ.pop("GITHUB_TOKEN", None)


@unittest.skipUnless(LANDLOCK and sys.platform.startswith("linux"), "Landlock not available")
class LandlockEnforcementTests(unittest.TestCase):
    """Real kernel enforcement: these spawn /bin/sh under Landlock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home with space"
        (self.home / "work").mkdir(parents=True)
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "id").write_text("PRIVATE")
        (self.home / "work" / "file.txt").write_text("hello")
        (self.home / "top.txt").write_text("top")
        self.ro = self.root / "readonly"
        self.ro.mkdir()
        (self.ro / "r.txt").write_text("r")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "o.txt").write_text("o")
        self.mgr = sandbox.SandboxManager("landlock", allow_tmp=False)
        self.plan = sandbox.SandboxPlan(allowed=[sandbox.RootRule(str(self.home), "rw"), sandbox.RootRule(str(self.ro), "ro")],
                                        denied=[(self.home / ".ssh").resolve()], allow_tmp=False, cwd=str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, cmd: str, plan=None) -> subprocess.CompletedProcess:
        argv, preexec, env = self.mgr.wrap(["/bin/sh", "-c", cmd], plan or self.plan)
        return subprocess.run(argv, preexec_fn=preexec, env=env, capture_output=True, text=True, timeout=20, cwd=str(self.home))

    def test_allowed_rw_works_including_spaces(self):
        q = shlex.quote(str(self.home / "work" / "new file.txt"))
        r = self.run_sh(f"echo hi > {q} && cat {q}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "hi")

    def test_denied_subtree_blocked(self):
        r = self.run_sh(f"cat {shlex.quote(str(self.home / '.ssh' / 'id'))}")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Permission denied", r.stderr)

    def test_outside_blocked(self):
        r = self.run_sh(f"cat {shlex.quote(str(self.outside / 'o.txt'))}")
        self.assertNotEqual(r.returncode, 0)

    def test_read_only_root_rejects_writes(self):
        r = self.run_sh(f"cat {shlex.quote(str(self.ro / 'r.txt'))} && (echo x > {shlex.quote(str(self.ro / 'w.txt'))}; echo rc=$?)")
        self.assertIn("r", r.stdout)
        self.assertIn("rc=", r.stdout)
        self.assertFalse((self.ro / "w.txt").exists())

    def test_symlink_and_traversal_to_denied_blocked(self):
        (self.home / "work" / "link").symlink_to(self.home / ".ssh" / "id")
        r = self.run_sh(f"cat {shlex.quote(str(self.home / 'work' / 'link'))}; cat {shlex.quote(str(self.home / 'work' / '..' / '.ssh' / 'id'))}")
        self.assertNotIn("PRIVATE", r.stdout)

    def test_hard_link_of_denied_file_blocked(self):
        self.run_sh(f"ln {shlex.quote(str(self.home / '.ssh' / 'id'))} {shlex.quote(str(self.home / 'work' / 'stolen'))}; echo rc=$?")
        self.assertFalse((self.home / "work" / "stolen").exists())

    def test_existing_top_level_file_readable_and_root_listable(self):
        r = self.run_sh(f"cat {shlex.quote(str(self.home / 'top.txt'))} && ls {shlex.quote(str(self.home))}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("top", r.stdout)
        self.assertIn("work", r.stdout)

    def test_self_test_passes(self):
        result = self.mgr.self_test(self.plan, "/bin/sh")
        self.assertTrue(result["ok"], result)

    def test_sandboxed_shell_cannot_gain_privileges(self):
        r = self.run_sh("cat /proc/self/status | grep -i nonewprivs")
        self.assertIn("NoNewPrivs:\t1", r.stdout)


if __name__ == "__main__":
    unittest.main()
