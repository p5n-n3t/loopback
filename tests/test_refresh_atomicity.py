"""Regression tests for atomic OAuth refresh-token rotation."""
from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
from auth import AuthError, AuthStore


RESOURCE = "https://loopback.example.test/mcp"


class RefreshAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="loopback-refresh-")
        self.addCleanup(self.tmp.cleanup)
        self.auth = AuthStore(Path(self.tmp.name), resource_uris=[RESOURCE], base_urls=["https://loopback.example.test"])
        self.client_id = "test-client"

    def issue(self):
        return self.auth.issue_refresh_token(client_id=self.client_id, resource=RESOURCE,
                                             scopes={"loopback:read", "loopback:exec"})

    def test_failed_access_row_insert_rolls_back_refresh_consumption(self):
        refresh, family = self.issue()
        digest = hashlib.sha256(refresh.encode()).hexdigest()
        original = self.auth._store_access_token

        def fail_after_refresh_insert(*_args, **_kwargs):
            raise RuntimeError("simulated access-token storage failure")

        self.auth._store_access_token = fail_after_refresh_insert
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            self.auth.rotate_refresh_token(refresh, client_id=self.client_id, resource=RESOURCE)
        self.auth._store_access_token = original

        with self.auth._connect() as db:
            row = db.execute("SELECT rotated_at FROM refresh_tokens WHERE token_hash=?", (digest,)).fetchone()
            self.assertIsNone(row["rotated_at"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM refresh_tokens WHERE family_id=?", (family,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM access_tokens WHERE family_id=?", (family,)).fetchone()[0], 0)

        rotated = self.auth.rotate_refresh_token(refresh, client_id=self.client_id, resource=RESOURCE)
        self.assertEqual(rotated[2], {"loopback:read", "loopback:exec"})
        self.assertEqual(rotated[3], family)
        self.auth.verify_access_token(rotated[1])

    def test_cross_connection_replay_revokes_successor_atomically(self):
        refresh, _family = self.issue()
        # A separate AuthStore instance models independent server workers/processes.
        peer = AuthStore(Path(self.tmp.name), resource_uris=[RESOURCE], base_urls=["https://loopback.example.test"])
        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, object]] = []
        lock = threading.Lock()

        def rotate(store):
            barrier.wait()
            try:
                value = store.rotate_refresh_token(refresh, client_id=self.client_id, resource=RESOURCE)
                result = ("ok", value)
            except AuthError as exc:
                result = ("error", exc)
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=rotate, args=(store,)) for store in (self.auth, peer)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "refresh rotation stalled waiting for SQLite writer lock")

        successes = [value for status, value in outcomes if status == "ok"]
        errors = [value for status, value in outcomes if status == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(errors), 1, outcomes)
        self.assertEqual(errors[0].error, "invalid_grant")
        # Replay policy revokes the returned successor and access token as one family.
        with self.assertRaises(AuthError):
            self.auth.verify_access_token(successes[0][1])
        with self.assertRaises(AuthError):
            self.auth.rotate_refresh_token(successes[0][0], client_id=self.client_id, resource=RESOURCE)


if __name__ == "__main__":
    unittest.main()
