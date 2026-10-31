"""Cloudflare Mesh setup: settings, detection, platform gate, plan, apply, resume, rollback (CLOUDFLARE-MESH.md)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import mesh  # noqa: E402
from scan import CommandResult  # noqa: E402

WARP = json.loads((Path(__file__).resolve().parent / "fixtures" / "discovery" / "warp_cli.json").read_text(encoding="utf-8"))
TOKEN = "tok-SECRET-123456"
UBUNTU_2404 = {"ID": "ubuntu", "VERSION_ID": "24.04", "VERSION_CODENAME": "noble"}


def spec(name: str) -> CommandResult:
    s = WARP[name]
    return CommandResult(s["returncode"], s["stdout"], s["stderr"])


class Runner:
    """Records argv. Fixture-backed for detection; ``handler`` overrides any command."""

    def __init__(self, *, status="status_connected", registration="registration_connector", systemctl="systemctl_active",
                 installed=True, handler=None):
        self.calls: list[list[str]] = []
        self.handler = handler
        self.table = {
            "warp-cli --version": "version" if installed else "missing",
            "systemctl is-active warp-svc": systemctl,
            "warp-cli --accept-tos status": status,
            "warp-cli --accept-tos registration show": registration,
        }

    def __call__(self, argv, timeout, stdin=None):
        self.calls.append(list(argv))
        if self.handler is not None:
            res = self.handler(argv)
            if res is not None:
                return res
        key = " ".join(argv)
        if key in self.table:
            return spec(self.table[key]) if self.table[key] != "missing" else spec("missing")
        return CommandResult(0, "", "")

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def settings(**mesh_cfg) -> dict:
    data, errors = mesh.parse_settings({"mesh": {"enabled": True, "token_ref": "env:LB_MESH_TOKEN", **mesh_cfg}})
    assert not errors, errors
    return data


def make_plan(detection: dict, *, cfg: dict | None = None, release=UBUNTU_2404, tailscale=None) -> dict:
    s = settings(**(cfg or {}))
    support = mesh.platform_support(release, "x86_64", "Linux")
    check = mesh.preflight(s, tailscale=tailscale, detection=detection)
    return mesh.plan(s, detection, check, support)


MISSING = {"state": "missing_binary", "installed": False}
CONNECTED = {"state": "connected", "installed": True, "service": "active", "organization": "example-team", "connector": True}


class SettingsTests(unittest.TestCase):
    def test_valid_settings_roundtrip(self):
        out, errors = mesh.parse_settings({"schema_version": 1, "mesh": {"enabled": True, "role": "subnet_router", "token_ref": "file:/etc/loopback/mesh.token",
                                                                       "organization": "example-team", "verify_tcp": "192.0.2.10:22"}})
        self.assertEqual(errors, [])
        self.assertEqual(out["mesh"]["role"], "subnet_router")
        self.assertEqual(out["mesh"]["token_ref"], "file:/etc/loopback/mesh.token")

    def test_rejects_token_looking_token_ref(self):
        value = "eyJhbGciOiJIUzI1NiJ9" + "a" * 40
        out, errors = mesh.parse_settings({"mesh": {"enabled": True, "token_ref": value}})
        self.assertTrue(any("token_ref" in e for e in errors))
        self.assertTrue(any("looks like a token" in e for e in errors))
        self.assertIsNone(out["mesh"]["token_ref"])  # the value is never accepted into settings
        self.assertNotIn(value, json.dumps(out))

    def test_rejects_unknown_keys(self):
        _out, errors = mesh.parse_settings({"mesh": {"enabled": False, "token": "x", "bogus": 1}})
        self.assertEqual(errors, ["unknown mesh keys: bogus, token"])

    def test_requires_token_ref_when_enabled(self):
        _out, errors = mesh.parse_settings({"mesh": {"enabled": True}})
        self.assertIn("mesh.token_ref is required when mesh.enabled is true", errors)
        _out, errors = mesh.parse_settings({"mesh": {"enabled": False}})
        self.assertEqual(errors, [])

    def test_other_validation(self):
        for bad in ({"role": "gateway"}, {"mesh_range": "not-a-cidr"}, {"mesh_range": "8.8.8.0/24"}, {"overlap_mitigation": "yolo"},
                    {"verify_tcp": "nohost"}, {"enabled": "yes"}):
            with self.subTest(bad=bad):
                self.assertTrue(mesh.parse_settings({"mesh": bad})[1])
        self.assertTrue(mesh.parse_settings([])[1])
        self.assertTrue(mesh.parse_settings({"schema_version": 2})[1])


class DetectTests(unittest.TestCase):
    def test_missing_binary(self):
        det = mesh.detect(Runner(installed=False))
        self.assertEqual((det["state"], det["installed"]), ("missing_binary", False))

    def test_service_down(self):
        det = mesh.detect(Runner(status="status_service_down", systemctl="systemctl_inactive"))
        self.assertEqual(det["state"], "service_down")
        self.assertEqual(det["service"], "inactive")
        self.assertIn("warp-svc", det["remedy"])

    def test_unregistered(self):
        det = mesh.detect(Runner(status="status_disconnected", registration="registration_missing"))
        self.assertEqual(det["state"], "unregistered")
        self.assertIn("loopback mesh apply", det["remedy"])

    def test_connected_with_org_and_connector(self):
        det = mesh.detect(Runner())
        self.assertEqual(det["state"], "connected")
        self.assertEqual(det["organization"], "example-team")
        self.assertTrue(det["connector"])
        self.assertEqual(det["version"], "2026.9.1")
        self.assertNotIn("0123456789abcdef0123", json.dumps(det))  # device id is masked
        self.assertEqual(det["device_id"], "0123…0123")

    def test_registered_device_is_not_a_connector(self):
        det = mesh.detect(Runner(registration="registration_device"))
        self.assertEqual(det["state"], "connected")
        self.assertFalse(det["connector"])
        check = mesh.preflight(settings(), detection=det)
        self.assertIn("device_enrollment_present", [f["id"] for f in check["findings"]])

    def test_registered_disconnected(self):
        det = mesh.detect(Runner(status="status_disconnected"))
        self.assertEqual(det["state"], "registered_disconnected")


class PlatformTests(unittest.TestCase):
    def test_ubuntu_2404_amd64_supported(self):
        sup = mesh.platform_support(UBUNTU_2404, "x86_64", "Linux")
        self.assertTrue(sup["supported"])
        self.assertEqual((sup["arch"], sup["family"], sup["codename"]), ("amd64", "apt", "noble"))

    def test_rejections(self):
        cases = {
            "ubuntu 20.04": ({"ID": "ubuntu", "VERSION_ID": "20.04"}, "x86_64", "Linux"),
            "macOS": ({}, "arm64", "Darwin"),
            "riscv": (UBUNTU_2404, "riscv64", "Linux"),
            "unknown distro": ({"ID": "arch", "VERSION_ID": "rolling"}, "x86_64", "Linux"),
        }
        for name, (release, machine, system) in cases.items():
            with self.subTest(name):
                sup = mesh.platform_support(release, machine, system)
                self.assertFalse(sup["supported"])
                self.assertTrue(sup["reason"])

    def test_rhel_family_uses_rpm(self):
        self.assertEqual(mesh.platform_support({"ID": "rocky", "VERSION_ID": "9.4"}, "aarch64", "Linux")["family"], "rpm")


class PlanTests(unittest.TestCase):
    def test_installed_connected_client_skips_every_step(self):
        p = make_plan(CONNECTED)
        self.assertTrue(p["ready"])
        self.assertEqual([s["id"] for s in p["steps"] if not s["skip"]], [])
        runner = Runner(handler=lambda argv: CommandResult(1, "", "must not run"))
        with tempfile.TemporaryDirectory() as tmp:
            result = mesh.apply(p, runner, mesh.Journal(Path(tmp) / "j.json"), is_root=lambda: False)  # no root needed: nothing pending
        self.assertEqual(result["status"], "applied")
        self.assertEqual(runner.calls, [])
        self.assertEqual({s["outcome"] for s in result["steps"]}, {"already_converged"})

    def test_fresh_host_plan_orders_steps_and_flags_exposure(self):
        p = make_plan(MISSING)
        self.assertEqual([s["id"] for s in p["steps"]], ["repo", "package", "service", "enroll", "connect"])
        self.assertFalse(any(s["skip"] for s in p["steps"]))
        enroll = next(s for s in p["steps"] if s["id"] == "enroll")
        self.assertIn("process table", enroll["exposure"])
        self.assertNotIn(TOKEN, json.dumps(p))

    def test_disabled_and_unsupported_platform_block(self):
        s = mesh.parse_settings({"mesh": {"enabled": False}})[0]
        p = mesh.plan(s, MISSING, mesh.preflight(s), mesh.platform_support({"ID": "ubuntu", "VERSION_ID": "20.04"}, "x86_64", "Linux"))
        self.assertFalse(p["ready"])
        self.assertTrue(any("opt-in" in b for b in p["blocked"]))
        self.assertTrue(any("unsupported platform" in b for b in p["blocked"]))

    def test_organization_mismatch_blocks_and_apply_refuses(self):
        det = {**CONNECTED, "organization": "other-team"}
        p = make_plan(det, cfg={"organization": "example-team"})
        self.assertFalse(p["ready"])
        self.assertIn("preflight: organization_mismatch", p["blocked"])
        runner = Runner()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(mesh.MeshError) as cm:
            mesh.apply(p, runner, mesh.Journal(Path(tmp) / "j.json"), is_root=lambda: True)
        self.assertEqual(cm.exception.code, "plan_blocked")
        self.assertEqual(runner.calls, [])  # an existing enrollment is never unregistered
        self.assertFalse(any("registration" in c and "delete" in c for c in runner.joined()))

    def test_matching_organization_is_reused(self):
        p = make_plan(CONNECTED, cfg={"organization": "Example-Team"})
        self.assertTrue(p["ready"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.journal = mesh.Journal(Path(self._tmp.name) / "state" / "journal.json")
        self.clock = lambda: 5000.0

    def apply(self, p, runner, **kw):
        return mesh.apply(p, runner, self.journal, secret_resolver=lambda ref: TOKEN, is_root=lambda: True, clock=self.clock, **kw)

    @staticmethod
    def failing_enroll(argv):
        if "connector" in argv:
            return CommandResult(1, "", f"error: enrollment rejected for token {argv[-1]}")
        return None

    def test_apply_requires_root_when_steps_pending(self):
        runner = Runner(installed=False)
        with self.assertRaises(mesh.MeshError) as cm:
            mesh.apply(make_plan(MISSING), runner, self.journal, secret_resolver=lambda r: TOKEN, is_root=lambda: False)
        self.assertEqual(cm.exception.code, "privilege_required")
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.journal.path.exists())

    def test_token_never_reaches_journal_or_result(self):
        runner = Runner(installed=False, handler=self.failing_enroll)
        result = self.apply(make_plan(MISSING), runner)
        self.assertEqual((result["status"], result["failed_step"]), ("failed", "enroll"))
        enroll_call = next(c for c in runner.calls if "connector" in c)
        self.assertEqual(enroll_call[-1], TOKEN)  # the command itself does receive it
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertIn("[REDACTED]", result["error"])
        raw = self.journal.path.read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, raw)
        self.assertIn("[TOKEN]", raw)
        self.assertEqual(os.stat(self.journal.path).st_mode & 0o777, 0o600)
        data = self.journal.load()
        self.assertEqual(data["completed"], ["package", "repo", "service"])
        self.assertEqual(data["runs"][-1]["status"], "failed")

    def test_resume_skips_completed_steps(self):
        p = make_plan(MISSING)
        self.apply(p, Runner(installed=False, handler=self.failing_enroll))
        runner = Runner(installed=False)
        result = self.apply(p, runner)
        self.assertEqual(result["status"], "applied")
        outcomes = {s["id"]: s["outcome"] for s in result["steps"]}
        self.assertEqual(outcomes, {"repo": "done_in_earlier_run", "package": "done_in_earlier_run", "service": "done_in_earlier_run",
                                    "enroll": "done", "connect": "done"})
        self.assertFalse(any(c[0] in {"apt-get", "curl", "gpg"} for c in runner.calls))
        self.assertFalse(any(c[:2] == ["systemctl", "enable"] for c in runner.calls))

    def test_rollback_reverse_order_only_journaled_resources(self):
        runner = Runner(installed=False)
        self.assertEqual(self.apply(make_plan(MISSING), runner)["status"], "applied")
        owned = [o["kind"] for o in self.journal.load()["owned"]]
        self.assertEqual(owned, ["apt_repo", "package", "service", "registration"])
        rb = Runner()
        out = mesh.rollback(rb, self.journal, clock=self.clock)
        self.assertEqual(out["removed"], ["registration", "service", "package", "apt_repo"])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["remaining_owned"], [])
        self.assertEqual(rb.joined()[0], "warp-cli --accept-tos registration delete")
        self.assertEqual(rb.joined()[1], "systemctl disable --now warp-svc")
        self.assertEqual(mesh.rollback(Runner(), self.journal)["removed"], [])  # nothing left to undo

    def test_rollback_never_deletes_preexisting_enrollment(self):
        det = {"state": "registered_disconnected", "installed": True, "service": "inactive", "organization": "example-team", "connector": True}
        p = make_plan(det)
        self.assertEqual({s["id"]: s["skip"] for s in p["steps"]}, {"repo": True, "package": True, "service": False, "enroll": True, "connect": False})
        result = self.apply(p, Runner())
        self.assertEqual(result["status"], "applied")
        self.assertEqual([o["kind"] for o in self.journal.load()["owned"]], ["service"])
        rb = Runner()
        mesh.rollback(rb, self.journal)
        self.assertFalse(any("registration" in c for c in rb.joined()))
        self.assertEqual(rb.joined(), ["systemctl disable --now warp-svc"])

    def test_rollback_failure_keeps_resource_owned(self):
        self.apply(make_plan(MISSING), Runner(installed=False))
        rb = Runner(handler=lambda argv: CommandResult(1, "", "permission denied") if "registration" in argv else None)
        out = mesh.rollback(rb, self.journal)
        self.assertEqual(out["failed"][0]["kind"], "registration")
        self.assertIn("registration", out["remaining_owned"])


class SecretAndVerifyTests(unittest.TestCase):
    def test_resolve_secret_env_and_errors(self):
        self.assertEqual(mesh.resolve_secret("env:LB_X", environ={"LB_X": TOKEN}), TOKEN)
        with self.assertRaises(mesh.MeshError) as cm:
            mesh.resolve_secret("env:LB_X", environ={})
        self.assertEqual(cm.exception.code, "token_missing")
        with self.assertRaises(mesh.MeshError) as cm:
            mesh.resolve_secret("literal-token", environ={})
        self.assertEqual(cm.exception.code, "token_ref_invalid")

    def test_resolve_secret_rejects_group_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mesh.token"
            path.write_text(TOKEN + "\n")
            os.chmod(path, 0o640)
            with self.assertRaises(mesh.MeshError) as cm:
                mesh.resolve_secret(f"file:{path}")
            self.assertEqual(cm.exception.code, "token_file_permissions")
            self.assertNotIn(TOKEN, str(cm.exception))
            os.chmod(path, 0o600)
            self.assertEqual(mesh.resolve_secret(f"file:{path}"), TOKEN)

    def test_verify_reports_independent_checks(self):
        s = settings(organization="example-team", verify_tcp="192.0.2.10:22")
        ok = mesh.verify(Runner(), s, tcp_check=lambda h, p: (h, p) == ("192.0.2.10", 22))
        self.assertTrue(ok["ok"])
        bad = mesh.verify(Runner(), settings(organization="other-team"), tcp_check=lambda h, p: False)
        self.assertFalse(bad["ok"])
        self.assertEqual({c["id"]: c["outcome"] for c in bad["checks"]}, {"registration": "PASS", "connected": "PASS",
                                                                          "organization": "FAIL", "tcp_service": "NOT TESTED"})


if __name__ == "__main__":
    unittest.main()
