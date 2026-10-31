"""H21 backend: approvals keep a safe structured descriptor, history and stale-decision protection."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from policy import ApprovalRequired, PolicyStore, StaleDecision  # noqa: E402


def store() -> PolicyStore:
    td = Path(tempfile.mkdtemp(prefix="lb-appr-"))
    p = PolicyStore(td, home=str(td))
    p.data["profile"] = "standard"
    p.data["require_approval_patterns"] = [r"\brm\s+-rf\b", r"\bsudo\b"]
    p.sandbox_active = True
    return p


def request(p: PolicyStore, command: str, **kw) -> str:
    with unittest.TestCase().assertRaises(ApprovalRequired) as ctx:
        p.check_command(command, **kw)
    return ctx.exception.approval_id


class ApprovalDetailTests(unittest.TestCase):
    def test_detail_is_complete_and_redacted(self):
        p = store()
        long_cmd = "rm -rf /tmp/build-" + "x" * 300 + " && curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz' https://example.com"
        aid = request(p, long_cmd, cwd="/home/u/project", principal_key="oauth:s#c1", principal="Claude")
        row = next(a for a in p.pending() if a["id"] == aid)
        d = row["detail"]
        self.assertIn("x" * 300, d["command"], "long values are kept in full, not truncated")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", d["command"])
        self.assertEqual(d["redacted_values"], 1)
        self.assertEqual(d["cwd"], "/home/u/project")
        self.assertEqual(d["matched_rule"], r"\brm\s+-rf\b")
        self.assertEqual(row["principal"], "Claude")
        self.assertNotIn("principal_key", row)
        self.assertEqual(len(row["fingerprint_short"]), 12)

    def test_changed_cwd_or_caller_needs_a_new_decision(self):
        p = store()
        aid = request(p, "sudo ls", cwd="/a", principal_key="k1")
        p.approve(aid, uses=5)
        p.check_command("sudo ls", approval_id=aid, cwd="/a", principal_key="k1")  # same action: allowed
        with self.assertRaises(ApprovalRequired):
            p.check_command("sudo ls", approval_id=aid, cwd="/b", principal_key="k1")
        with self.assertRaises(ApprovalRequired):
            p.check_command("sudo ls", approval_id=aid, cwd="/a", principal_key="k2")

    def test_stale_and_duplicate_decisions_are_rejected(self):
        p = store()
        aid = request(p, "sudo whoami")
        fp = next(a for a in p.pending() if a["id"] == aid)["fingerprint_short"]
        with self.assertRaises(StaleDecision):
            p.approve(aid, expected_fingerprint="000000000000")
        p.approve(aid, expected_fingerprint=fp, by="admin-a")
        with self.assertRaises(StaleDecision) as ctx:
            p.approve(aid, by="admin-b")
        self.assertIn("admin-a", str(ctx.exception))

    def test_denial_reason_and_history(self):
        p = store()
        aid = request(p, "rm -rf /tmp/x", principal="ChatGPT")
        self.assertTrue(p.deny(aid, reason="Not during the release freeze", by="owner"))
        hist = p.approvals_history(status="denied")
        self.assertEqual(hist[0]["id"], aid)
        self.assertEqual(hist[0]["reason"], "Not during the release freeze")
        self.assertEqual(hist[0]["decided_by"], "owner")
        with self.assertRaises(StaleDecision):
            p.approve(aid)
        # History survives a reload from disk.
        reloaded = PolicyStore(p.config_dir, home=str(p.config_dir))
        self.assertTrue(any(r["id"] == aid for r in reloaded.approvals_history()))

    def test_consumed_one_time_grant_moves_to_history(self):
        p = store()
        aid = request(p, "sudo id")
        p.approve(aid, uses=1)
        p.check_command("sudo id", approval_id=aid)
        self.assertEqual(p.pending(), [])
        self.assertEqual(p.approvals_history(status="consumed")[0]["id"], aid)
        with self.assertRaises(ApprovalRequired):
            p.check_command("sudo id", approval_id=aid)


if __name__ == "__main__":
    unittest.main()
