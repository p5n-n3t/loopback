"""Reliability services: doctor, backups, diagnostic bundles with redaction, log rotation, update checks."""
from __future__ import annotations

import json
import tarfile
import unittest
import zipfile
from pathlib import Path

from helpers import fresh_config_dir

from bootstrap import build_context
import maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Path(fresh_config_dir())
        self.ctx = build_context(start_managers=True)
        self.mgr = maintenance.MaintenanceManager(self.ctx)
        self.ctx.maintenance = self.mgr

    def test_doctor_reports_structured_checks(self):
        checks = maintenance.doctor(self.ctx, deep=False)
        self.assertTrue(checks)
        for check in checks:
            self.assertEqual({"name", "ok", "level", "detail", "fix"} & set(check), {"name", "ok", "level", "detail", "fix"})
            self.assertIn(check["level"], {"ok", "warning", "error"})
        names = {c["name"] for c in checks}
        self.assertTrue({"dashboard_password", "sandbox", "config_valid"} & names, names)
        # a fresh install without a password must say so, with a fix
        pw = next(c for c in checks if c["name"] == "dashboard_password")
        self.assertFalse(pw["ok"])
        self.assertIn("enroll", (pw["fix"] or "") + pw["detail"])

    def test_backup_contains_state_but_never_venv_or_logs(self):
        (self.cfg / "venv").mkdir()
        (self.cfg / "venv" / "big.bin").write_bytes(b"x" * 1024)
        (self.cfg / "server.log").write_text("log line\n")
        self.ctx.policy.save({"display_name": None} if False else {"profile": "development"})
        bm = maintenance.BackupManager(self.ctx)
        result = bm.create(reason="test")
        path = Path(result["path"])
        self.assertTrue(path.exists())
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        with tarfile.open(path) as tar:
            names = tar.getnames()
        joined = "\n".join(names)
        self.assertIn("token", joined)
        self.assertIn("policy.json", joined)
        self.assertIn("auth.db", joined)
        self.assertNotIn("venv/", joined)
        self.assertNotIn("server.log", joined)
        self.assertEqual(len(bm.list()), 1)
        # restore requires confirmation, then round-trips the policy file
        self.ctx.policy.save({"profile": "read-only"})
        backup_name = bm.list()[0]["name"]
        with self.assertRaises(Exception):
            bm.restore(backup_name, confirm=False)
        bm.restore(backup_name, confirm=True)
        self.assertEqual(json.loads((self.cfg / "policy.json").read_text())["profile"], "development")
        # a safety backup was taken before restoring
        self.assertGreaterEqual(len(bm.list()), 2)

    def test_backup_restore_rejects_path_traversal(self):
        bm = maintenance.BackupManager(self.ctx)
        evil = self.cfg / "backups" / "evil.tar.gz"
        evil.parent.mkdir(exist_ok=True)
        outside = self.cfg.parent / "loopback-escaped.txt"
        with tarfile.open(evil, "w:gz") as tar:
            data = b"owned"
            info = tarfile.TarInfo(name="../loopback-escaped.txt")
            info.size = len(data)
            import io

            tar.addfile(info, io.BytesIO(data))
        try:
            try:
                bm.restore("evil.tar.gz", confirm=True)
            except Exception:
                pass
            self.assertFalse(outside.exists())
        finally:
            if outside.exists():
                outside.unlink()

    def test_redactor_and_bundle_have_no_secrets(self):
        red = maintenance.Redactor(extra_secrets=[self.ctx.auth.machine_token])
        sample = (f"Authorization: Bearer lbk_abc12345_{'s' * 40} and refresh lbr_{'r' * 44} jwt eyJ{'a' * 30}.eyJ{'b' * 30}.{'c' * 30} "
                  f"machine {self.ctx.auth.machine_token} key tskey-auth-abcdef123456 password=hunter22 -----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----")
        out = red.redact(sample)
        for needle in ("lbk_abc12345", "lbr_", "eyJ", self.ctx.auth.machine_token, "tskey-auth", "hunter22", "MIIE"):
            self.assertNotIn(needle, out, needle)
        self.assertGreater(red.count, 0)
        value, _ = self.ctx.auth.create_api_token("bundle-test", ["loopback:read"], ttl_seconds=60, created_by="test")
        (self.cfg / "server.log").write_text(f"GET /mcp Authorization: Bearer {value}\n")
        bundle = maintenance.DiagnosticBundle(self.ctx).create()
        with zipfile.ZipFile(bundle["path"]) as z:
            blob = b"".join(z.read(n) for n in z.namelist())
            names = z.namelist()
        self.assertNotIn(value.encode(), blob)
        self.assertNotIn(self.ctx.auth.machine_token.encode(), blob)
        self.assertNotIn(self.ctx.auth.signing_key.hex().encode(), blob)
        self.assertFalse(any(n.endswith(("token", "oauth-signing.key", "auth.db", "nodes.secrets.json")) for n in names))
        self.assertIn("summary.json", names)

    def test_log_rotation_is_size_based_and_bounded(self):
        log = self.cfg / "server.log"
        log.write_bytes(b"x" * 5000)
        rot = maintenance.LogRotator(self.ctx)
        self.assertTrue(rot.rotate(log, max_bytes=1000, backups=2))
        self.assertTrue((self.cfg / "server.log.1").exists())
        self.assertLessEqual(log.stat().st_size, 1000)
        self.assertFalse(rot.rotate(log, max_bytes=1000, backups=2))
        for _ in range(4):
            log.write_bytes(b"y" * 5000)
            rot.rotate(log, max_bytes=1000, backups=2)
        self.assertFalse((self.cfg / "server.log.3").exists())

    def test_update_checker_respects_off_channel(self):
        self.ctx.config.save({"maintenance": {"update_channel": "off"}})
        uc = maintenance.UpdateChecker(self.ctx)
        result = uc.check(force=True)
        self.assertEqual(result["current"], self.ctx.version)
        self.assertFalse(result["available"])
        self.assertIsNone(result.get("latest"))
        self.assertIn("channel", uc.cached())

    def test_alerts_and_metrics_shape(self):
        alerts = self.mgr.alerts()
        self.assertIsInstance(alerts, list)
        codes = {a["code"] for a in alerts}
        self.assertIn("no_password", codes)
        for a in alerts:
            self.assertTrue(a["action"].startswith("#/"), a)
        metrics = self.mgr.metrics()
        for key in ("rss_bytes", "db_bytes", "auth_db_bytes", "job_logs_bytes", "uptime_seconds"):
            self.assertIn(key, metrics)

    def test_background_loop_runs_and_stops(self):
        import asyncio

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(self.mgr.run_forever(stop))
            await asyncio.sleep(0.5)
            stop.set()
            await asyncio.wait_for(task, timeout=10)

        asyncio.run(run())
        status = self.mgr.status()
        self.assertIn("last_run", status)
        self.assertEqual(status["last_errors"], [])


if __name__ == "__main__":
    unittest.main()
