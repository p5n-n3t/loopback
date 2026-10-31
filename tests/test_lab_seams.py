"""Lab seams: run manifest, owned-resource cleanup, pairwise matrix and replay runner."""
from __future__ import annotations

import copy
import itertools
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import lab  # noqa: E402
from scan import CommandResult  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "lab-run-manifest.v1.json"


def _manifest(**over):
    args = dict(source_commit="abc1234", platform={"os": "linux", "arch": "x86_64"}, input_mode="settings",
                distribution="release", private="none", primary_ingress="local", policy_profile="restricted")
    args.update(over)
    return lab.new_manifest("run-test-01", **args)


class ManifestTests(unittest.TestCase):
    def test_round_trip_validates(self):
        m = _manifest(image_digest="sha256:" + "a" * 64)
        lab.record_check(m, "core.file", "PASS", evidence=["log:1"], exit_status=0, duration_ms=12, output="ok")
        lab.record_check(m, "core.exec", "EXPECTED DENIAL")
        again = json.loads(json.dumps(m))
        self.assertEqual(lab.validate_manifest(again), [])
        self.assertEqual(again["cleanup"], {"status": "pending", "residual_refs": []})
        self.assertEqual(again["schema_version"], 1)

    def test_bad_values_rejected(self):
        for bad in ("Bad_ID", "ab", "-lead", "x" * 64):
            with self.assertRaises(ValueError):
                lab.new_manifest(bad, source_commit="c", platform={"os": "l", "arch": "a"}, input_mode="settings",
                                 distribution="release", private="none", primary_ingress="local",
                                 policy_profile="restricted")
        for field, value in (("input_mode", "wizard"), ("distribution", "deb"), ("private", "vpn"),
                             ("primary_ingress", "tor")):
            with self.assertRaises(ValueError):
                _manifest(**{field: value})
        m = _manifest()
        m["checks"].append({"id": "core.file", "outcome": "MAYBE", "evidence": []})
        m["checks"].append({"id": "Core File", "outcome": "PASS", "evidence": []})
        m["checks"].append({"id": "core.x", "outcome": "PASS", "evidence": [], "duration_ms": -1, "output": "x" * 5000})
        m["cleanup"]["status"] = "weird"
        m["extra"] = 1
        errors = " | ".join(lab.validate_manifest(m))
        for needle in ("checks[0].outcome", "checks[1].id", "checks[2].duration_ms", "checks[2].output",
                       "cleanup.status", "unknown key: extra"):
            self.assertIn(needle, errors)
        self.assertTrue(lab.validate_manifest({}))
        self.assertEqual(lab.validate_manifest([]), ["manifest must be an object"])

    def test_secret_guard(self):
        token = "ghp_" + "A1b2C3d4E5" * 3
        for mutate, path in (
            (lambda m: m["checks"].append({"id": "a.b", "outcome": "PASS", "evidence": [token]}), "checks[0].evidence[0]"),
            (lambda m: m["providers"].update(note="Bearer abcdefghijklmnop1234"), "providers.note"),
            (lambda m: m.update(owned_resource_refs=["x", "password=hunter22hunter"]), "owned_resource_refs[1]"),
        ):
            m = _manifest()
            mutate(m)
            errors = lab.validate_manifest(m)
            self.assertIn(f"secret-like value at $.{path}", errors)
        ok = _manifest()
        ok["providers"]["credential_ref"] = "secretref:MESH_TOKEN"
        self.assertEqual(lab.validate_manifest(ok), [])

    def test_record_check_redacts_and_clips(self):
        m = _manifest()
        check = lab.record_check(m, "auth.token", "FAIL", output="Authorization: Bearer abcdefghijklmnopqrstuv\n"
                                 + "ghp_" + "Z" * 30 + "\n" + "y" * 6000)
        self.assertNotIn("abcdefghijklmnopqrstuv", check["output"])
        self.assertNotIn("ghp_", check["output"])
        self.assertIn("[REDACTED]", check["output"])
        self.assertLessEqual(len(check["output"]), 4096)
        self.assertEqual(lab.validate_manifest(m), [])
        lab.record_check(m, "auth.token", "PASS")  # same id replaces
        self.assertEqual(len(m["checks"]), 1)
        with self.assertRaises(ValueError):
            lab.record_check(m, "auth.token", "NOPE")

    def test_schema_file_matches(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(set(schema["required"]), set(_manifest()))
        self.assertEqual(schema["properties"]["checks"]["items"]["$ref"], "#/$defs/check")
        self.assertEqual(tuple(schema["$defs"]["check"]["properties"]["outcome"]["enum"]), lab.OUTCOMES)
        self.assertEqual(set(schema["properties"]) , set(lab.MANIFEST_KEYS))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "owned.json"
        self.reg = lab.OwnedResources(self.path)
        self.clock = itertools.count(1000)

    def _add(self, kind, ref, run="run-a1"):
        return self.reg.add(kind, ref, ["rm", ref], run_id=run, now=lambda: float(next(self.clock)))

    def _runner(self, fail=()):
        calls = []

        def runner(argv, timeout, stdin=None):
            calls.append(argv[1])
            if argv[1] in fail:
                return CommandResult(1, "", "boom token=supersecretvalue1")
            return CommandResult(0)
        runner.calls = calls
        return runner

    def test_add_idempotent_and_mode(self):
        self.assertTrue(self._add("disk", "d1"))
        self.assertFalse(self._add("disk", "d1"))
        self.assertTrue(self._add("net", "d1"))
        self.assertEqual(len(self.reg.list()), 2)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        with self.assertRaises(ValueError):
            self.reg.add("disk", "x", [], run_id="run-a1")
        with self.assertRaises(ValueError):
            self.reg.add("disk", "x", ["rm"], run_id="BAD")

    def test_cleanup_newest_first_and_idempotent(self):
        for ref in ("one", "two", "three"):
            self._add("disk", ref)
        runner = self._runner()
        result = self.reg.cleanup(runner, run_id="run-a1")
        self.assertEqual(runner.calls, ["three", "two", "one"])
        self.assertEqual(result, {"status": "clean", "removed": ["three", "two", "one"], "residual": []})
        again = self.reg.cleanup(runner, run_id="run-a1")
        self.assertEqual(again, {"status": "clean", "removed": [], "residual": []})
        self.assertEqual(len(runner.calls), 3)

    def test_partial_failure_then_recovery(self):
        for ref in ("one", "two", "three"):
            self._add("disk", ref)
        result = self.reg.cleanup(self._runner(fail={"two"}), run_id="run-a1")
        self.assertEqual(result["status"], "residual")
        self.assertEqual(result["removed"], ["three", "one"])
        self.assertEqual([r["ref"] for r in result["residual"]], ["two"])
        self.assertNotIn("supersecretvalue1", json.dumps(result))
        self.assertNotIn("supersecretvalue1", self.path.read_text())
        self.assertEqual(self.reg.status(), "idle")
        fixed = self._runner()
        self.assertEqual(self.reg.cleanup(fixed, run_id="run-a1")["status"], "clean")
        self.assertEqual(fixed.calls, ["two"])
        m = lab.finish_manifest(_manifest(), result)
        self.assertEqual(m["cleanup"], {"status": "residual", "residual_refs": ["disk:two"]})
        self.assertEqual(m["owned_resource_refs"], ["disk:two", "one", "three"])
        self.assertEqual(lab.validate_manifest(m), [])

    def test_timeout_and_exception_are_residual(self):
        self._add("vm", "v1")
        self._add("vm", "v2")

        def runner(argv, timeout, stdin=None):
            if argv[1] == "v2":
                return CommandResult(124, "", "timed out", timed_out=True)
            raise OSError("no such tool")
        result = self.reg.cleanup(runner, run_id="run-a1", timeout=1)
        self.assertEqual(result["status"], "residual")
        self.assertEqual(len(result["residual"]), 2)

    def test_other_run_untouched(self):
        self._add("disk", "mine", "run-a1")
        self._add("disk", "theirs", "run-b2")
        runner = self._runner()
        self.reg.cleanup(runner, run_id="run-a1")
        self.assertEqual(runner.calls, ["mine"])
        self.assertEqual([r["ref"] for r in self.reg.list()], ["theirs"])
        with self.assertRaises(TypeError):
            self.reg.cleanup(runner)
        self.reg.cleanup(runner, run_id=None)
        self.assertEqual(runner.calls, ["mine", "theirs"])

    def test_interrupted_recovery(self):
        self._add("disk", "one")
        self._add("disk", "two")
        data = json.loads(self.path.read_text())
        data["cleanup"] = {"status": "running"}
        self.path.write_text(json.dumps(data))
        fresh = lab.OwnedResources(self.path)
        self.assertEqual(fresh.status(), "interrupted")
        runner = self._runner()
        self.assertEqual(fresh.cleanup(runner, run_id="run-a1")["status"], "clean")
        self.assertEqual(runner.calls, ["two", "one"])
        self.assertEqual(fresh.status(), "idle")


class MatrixTests(unittest.TestCase):
    def test_all_pairs_covered_deterministic_small(self):
        rows = lab.pairwise_matrix(lab.LAB_AXES)
        seen = set()
        for row in rows:
            self.assertEqual(set(row), set(lab.LAB_AXES))
            for a, b in itertools.combinations(sorted(row), 2):
                seen.add(((a, row[a]), (b, row[b])))
        for a, b in itertools.combinations(sorted(lab.LAB_AXES), 2):
            for va in lab.LAB_AXES[a]:
                for vb in lab.LAB_AXES[b]:
                    self.assertIn(((a, va), (b, vb)), seen)
        self.assertEqual(rows, lab.pairwise_matrix(copy.deepcopy(lab.LAB_AXES)))
        cartesian = 1
        for values in lab.LAB_AXES.values():
            cartesian *= len(values)
        self.assertLess(len(rows) * 100, cartesian)
        self.assertEqual(rows, sorted(rows, key=lambda r: tuple(r[n] for n in sorted(r))))

    def test_small_axes_and_risk_cases(self):
        rows = lab.pairwise_matrix({"a": ["1", "2"], "b": ["x", "y"], "c": ["p", "q"]})
        self.assertLessEqual(len(rows), 6)
        self.assertEqual(lab.pairwise_matrix({"a": ["1"]}), [{"a": "1"}])
        for case in lab.RISK_CASES:
            for axis, value in case.items():
                if axis in lab.LAB_AXES:
                    self.assertIn(value, lab.LAB_AXES[axis])
        self.assertTrue(any("tailscale" in c["name"] for c in lab.RISK_CASES))


class ReplayTests(unittest.TestCase):
    def test_replay(self):
        runner = lab.replay_runner({"tailscale status --json": {"returncode": 0, "stdout": "{}"}})
        ok = runner(["tailscale", "status", "--json"], 5)
        self.assertEqual((ok.returncode, ok.stdout), (0, "{}"))
        miss = runner(["cloudflared", "tunnel", "list"], 5, None)
        self.assertEqual((miss.returncode, miss.stdout, miss.stderr), (127, "", "not recorded"))
        self.assertEqual([c["argv"][0] for c in runner.calls], ["tailscale", "cloudflared"])


if __name__ == "__main__":
    unittest.main()
