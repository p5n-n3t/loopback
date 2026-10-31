"""Fixture-driven discovery scenarios D01-D38 (docs/handoff/DISCOVERY-DESIGN.md section 9).

Every external command goes through a recording fake runner; nothing here touches a real
network, tailnet, SSH agent or DNS. Fixtures live in tests/fixtures/discovery.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import scan  # noqa: E402
from scan import CommandResult, ScanEnv, StagedScanner  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discovery"


def fx(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


TS_RUNNING = fx("tailscale_running.json")
TS_ZERO = fx("tailscale_zero_peers.json")
TS_LOGIN = fx("tailscale_needs_login.json")
TS_FAIL = fx("tailscale_failures.json")
SSH_CFG = fx("ssh_config_cases.json")
PROBE = fx("ssh_probe.json")


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def result(spec: dict) -> CommandResult:
    return CommandResult(spec["returncode"], spec.get("stdout", ""), spec.get("stderr", ""))


class FakeRunner:
    """Records every argv. ``tailscale`` and ``ssh`` behaviour is swappable per test."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.tailscale: CommandResult = CommandResult(127, "", "tailscale: command not found")
        self.ssh = lambda argv: CommandResult(0, PROBE["ok_stdout"])
        self.other = lambda argv, stdin: CommandResult(127, "", "not found")
        self._lock = threading.Lock()

    def __call__(self, argv, timeout, stdin=None):
        with self._lock:
            self.calls.append(list(argv))
        if argv[0] == "tailscale":
            return self.tailscale
        if argv[0] == "ssh":
            return self.ssh(argv)
        return self.other(argv, stdin)

    def of(self, binary: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == binary]

    def set_tailscale(self, data) -> None:
        self.tailscale = CommandResult(0, json.dumps(data), "")


class FakeResolver:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        return ["192.0.2.55"]


def fake_read_text(files: dict[str, str]):
    def read(path: Path) -> str:
        try:
            return files[str(path)]
        except KeyError:
            raise FileNotFoundError(str(path)) from None
    return read


class FakeSshStore:
    def __init__(self, fail_once: set[str] | None = None):
        self.fail_once = set(fail_once or ())
        self.upserts: list[dict] = []
        self.removed: list[str] = []
        self.names: list[str] = []

    def list(self):
        return [{"name": n} for n in self.names]

    def upsert(self, **fields):
        if fields["name"] in self.fail_once:
            self.fail_once.discard(fields["name"])
            raise RuntimeError("simulated registry write failure")
        self.upserts.append(fields)
        self.names.append(fields["name"])

    def remove(self, name):
        self.removed.append(name)
        return True


class FakeWorkers:
    def __init__(self):
        self.installed: list[str] = []

    def install(self, name):
        self.installed.append(name)


class ScenarioBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.clock = Clock()
        self.runner = FakeRunner()
        self.resolver = FakeResolver()
        self.files: dict[str, str] = {}

    def env(self) -> ScanEnv:
        return ScanEnv(runner=self.runner, resolver=self.resolver, clock=self.clock,
                       read_text=fake_read_text(self.files), home=Path(SSH_CFG["home"]))

    def scanner(self, providers=None, *, excluded=(), limits=None, state="state.json") -> StagedScanner:
        return StagedScanner(self.tmp / state, env=self.env(), providers=providers if providers is not None else [],
                             excluded=lambda: list(excluded), limits={"probe_concurrency": 2, **(limits or {})},
                             known_hosts=self.tmp / "known_hosts", local_host=lambda: {"hostname": "localbox"})

    @staticmethod
    def by_name(snapshot: dict) -> dict[str, dict]:
        return {h["display_name"]: h for h in snapshot["hosts"]}

    def probe_one(self, sc: StagedScanner, name: str) -> dict:
        host = self.by_name(sc.snapshot())[name]
        run = sc.start_probe([host["id"]], background=False)
        self.assertEqual(run["status"], "done")
        return self.by_name(sc.snapshot())[name]


# ------------------------------------------------------------------------------ S0-S2 listing
class ListingScenarios(ScenarioBase):
    def test_D01_fresh_machine_no_provider_no_ssh_config(self):
        providers = [scan.TailscaleProvider(), scan.MeshProvider(detector=lambda e: {"state": "missing_binary"}),
                     scan.SshConfigProvider()]
        sc = self.scanner(providers)
        snap = sc.list_hosts()
        self.assertEqual([h["id"] for h in snap["hosts"]], ["local"])
        self.assertEqual(snap["empty_reason"], "no_provider")
        self.assertEqual({k: v["state"] for k, v in snap["providers"].items()},
                         {"tailscale": "missing_binary", "mesh": "missing_binary", "ssh_config": "not_found"})
        self.assertEqual(self.runner.of("ssh"), [])
        # Manual add works and appears as a new host with no remote contact.
        sc.add_manual(host="192.0.2.10", label="lab1")
        snap = sc.list_hosts()
        self.assertEqual(self.by_name(snap)["lab1"]["state"], "new")
        self.assertEqual(self.runner.of("ssh"), [])

    def test_D02_tailscale_daemon_down_or_not_enrolled(self):
        sc = self.scanner([scan.TailscaleProvider()])
        self.runner.tailscale = result(TS_FAIL["daemon_down"])
        snap = sc.list_hosts()
        self.assertEqual(snap["providers"]["tailscale"]["state"], "daemon_down")
        self.assertIn("tailscaled", snap["providers"]["tailscale"]["remedy"])
        self.assertEqual(snap["empty_reason"], "provider_unavailable")
        self.runner.set_tailscale(TS_LOGIN)
        snap = sc.list_hosts()
        self.assertEqual(snap["providers"]["tailscale"]["state"], "unenrolled")
        self.assertIn("tailscale up", snap["providers"]["tailscale"]["remedy"])
        self.assertEqual(snap["empty_reason"], "provider_unavailable")  # never a fake zero-peer success

    def test_D03_tailscale_enrolled_zero_peers(self):
        self.runner.set_tailscale(TS_ZERO)
        snap = self.scanner([scan.TailscaleProvider()]).list_hosts()
        self.assertEqual(snap["providers"]["tailscale"]["state"], "running")
        self.assertEqual(snap["providers"]["tailscale"]["candidates"], 0)
        self.assertEqual(snap["empty_reason"], "no_peers")
        self.assertEqual([h["id"] for h in snap["hosts"]], ["local"])

    def test_D04_visible_online_and_offline_peers(self):
        self.runner.set_tailscale(TS_RUNNING)
        snap = self.scanner([scan.TailscaleProvider()]).list_hosts()
        hosts = self.by_name(snap)
        self.assertEqual({n: hosts[n]["state"] for n in ("lab1", "build1", "laptop")},
                         {"lab1": "new", "build1": "new", "laptop": "offline"})
        self.assertFalse(any(h["managed"] for n, h in hosts.items() if n != "localbox"))
        src = hosts["lab1"]["sources"][0]
        self.assertEqual(src["observed_at"], self.clock.now)
        self.assertEqual(snap["providers"]["tailscale"]["observed_at"], self.clock.now)
        # Listing is read-only: the only command ever run is `tailscale status --json`.
        self.assertEqual({c[0] for c in self.runner.calls}, {"tailscale"})
        self.assertEqual(self.resolver.calls, [])

    def test_D12_same_host_in_peer_list_ssh_and_worker(self):
        self.runner.set_tailscale(TS_RUNNING)
        self.files["/home/tester/.ssh/config"] = "Host lab1\n    HostName 100.101.0.10\n    Port 6722\n"
        env = self.env()
        worker = scan.candidate("worker", "lab1", name="lab1", env=env, addresses=["100.101.0.10"],
                                confidence="registered", extra={"worker": {"installed": True, "target": "lab1"}})
        providers = [scan.TailscaleProvider(), scan.SshConfigProvider(), scan.StaticProvider("worker", lambda e: [worker])]
        snap = self.scanner(providers).list_hosts()
        matches = [h for h in snap["hosts"] if "100.101.0.10" in h["addresses"]]
        self.assertEqual(len(matches), 1)
        host = matches[0]
        self.assertEqual(set(host["provider_ids"]), {"worker", "tailscale", "ssh_config"})
        self.assertTrue(host["managed"])
        self.assertEqual(host["state"], "existing")  # already managed: no second installation offered
        self.assertEqual(host["ssh"]["port"], 6722)

    def test_D13_same_short_name_on_different_networks(self):
        self.runner.set_tailscale(TS_RUNNING)
        self.files["/home/tester/.ssh/config"] = "Host build1\n    HostName 192.0.2.50\n"
        snap = self.scanner([scan.TailscaleProvider(), scan.SshConfigProvider()]).list_hosts()
        build = [h for h in snap["hosts"] if h["display_name"] == "build1"]
        self.assertEqual(len(build), 2)  # separate candidates: different networks, no shared address or FQDN
        self.assertEqual(len(snap["conflicts"]), 1)
        conflict = snap["conflicts"][0]
        self.assertEqual((conflict["kind"], conflict["name"]), ("same_short_name", "build1"))
        self.assertEqual(set(conflict["host_ids"]), {h["id"] for h in build})
        for h in build:
            self.assertEqual(h["conflicts"][0]["kind"], "same_short_name")

    def test_D20_mesh_provider_reports_peer_listing_unsupported(self):
        provider = scan.MeshProvider(detector=lambda e: {"state": "connected", "organization": "example-team"})
        info = provider.detect(self.env())
        self.assertEqual(info["peer_listing"], "unsupported_locally")
        self.assertIn("manual entry", info["limitation"])
        self.assertEqual(provider.list_candidates(self.env()), [])
        # Without an injected detector it uses mesh.detect through the runner (binary missing here).
        plain = scan.MeshProvider()
        self.assertEqual(plain.detect(self.env())["state"], "missing_binary")
        self.assertEqual(plain.detect(self.env())["peer_listing"], "unsupported_locally")

    def test_D22_manual_entry_works_without_tailscale(self):
        sc = self.scanner([scan.TailscaleProvider()])  # tailscale binary missing
        sc.add_manual(host="192.0.2.10", port=22, user="alice", label="lan-box")
        snap = sc.list_hosts()
        self.assertEqual(snap["providers"]["tailscale"]["state"], "missing_binary")
        host = self.probe_one(sc, "lan-box")
        self.assertEqual(host["state"], "enrollment_required")
        store = FakeSshStore()
        plan = sc.plan_enrollment([host["id"]], ssh_store=store)
        self.assertEqual([s["action"] for s in plan["steps"]], ["register_ssh_target", "install_worker"])

    def test_D30_malformed_provider_output_keeps_previous_hosts_stale(self):
        sc = self.scanner([scan.TailscaleProvider()])
        self.runner.set_tailscale(TS_RUNNING)
        first = self.by_name(sc.list_hosts())
        self.assertEqual(first["lab1"]["state"], "new")
        self.clock.now += 60
        self.runner.tailscale = result(TS_FAIL["malformed"])
        snap = sc.list_hosts()
        self.assertEqual(snap["providers"]["tailscale"]["state"], "malformed")
        hosts = self.by_name(snap)
        for name in ("lab1", "build1", "laptop"):
            self.assertEqual(hosts[name]["state"], "stale")  # last-known data kept, not deleted
            self.assertFalse(hosts[name]["listed"])
        self.assertEqual(snap["empty_reason"], None)

    def test_D32_host_disappears_becomes_stale(self):
        sc = self.scanner([scan.TailscaleProvider()])
        self.runner.set_tailscale(TS_RUNNING)
        sc.list_hosts()
        trimmed = json.loads(json.dumps(TS_RUNNING))
        del trimmed["Peer"]["nodekey:aaaa03"]
        self.runner.set_tailscale(trimmed)
        hosts = self.by_name(sc.list_hosts())
        self.assertEqual(hosts["laptop"]["state"], "stale")
        self.assertEqual(hosts["lab1"]["state"], "new")

    def test_D36_scale_0_1_20_200_candidates(self):
        for count in (0, 1, 20, 200):
            with self.subTest(candidates=count):
                data = json.loads(json.dumps(TS_RUNNING))
                data["Peer"] = {f"nodekey:{i:04d}": {"ID": f"n{i}", "HostName": f"peer{i}", "DNSName": f"peer{i}.example.ts.net.",
                                                    "TailscaleIPs": [f"100.101.{i // 250}.{i % 250 + 1}"], "OS": "linux", "Online": i % 2 == 0}
                                for i in range(count)}
                self.runner.calls.clear()
                self.runner.set_tailscale(data)
                snap = self.scanner([scan.TailscaleProvider()], state=f"scale-{count}.json").list_hosts()
                self.assertEqual(len(snap["hosts"]), count + 1)
                self.assertEqual({c[0] for c in self.runner.calls}, {"tailscale"})  # no scan storm
        with self.subTest(max_candidates=50):
            self.runner.set_tailscale(data)
            snap = self.scanner([scan.TailscaleProvider()], limits={"max_candidates": 50}, state="capped.json").list_hosts()
            self.assertEqual(len(snap["hosts"]), 51)  # 50 candidates plus local

    def test_D37_vm_inventory_candidate_has_no_false_ssh_health(self):
        env = self.env()
        guests = [scan.candidate("libvirt", "vm-1", name="vm-off", env=env, kind="virtual-machine",
                                 evidence="hypervisor inventory", extra={"power_state": "shut_off", "instance_id": "uuid-1"}),
                  scan.candidate("libvirt", "vm-2", name="vm-noaddr", env=env, kind="virtual-machine", online=None,
                                 extra={"power_state": "running", "instance_id": "uuid-2"})]
        sc = self.scanner([scan.StaticProvider("libvirt", lambda e: guests, state="authorized")])
        snap = sc.list_hosts()
        hosts = self.by_name(snap)
        for name in ("vm-off", "vm-noaddr"):
            self.assertEqual(hosts[name]["state"], "new")
            self.assertEqual(hosts[name]["axes"], {})  # no SSH observation implied
            self.assertIsNone(hosts[name]["ssh"])
        self.assertEqual(hosts["vm-off"]["sources"][0]["provider"], "libvirt")
        self.assertEqual(self.runner.calls, [])  # nothing started or contacted

    def test_D38_optional_inventory_source_denied_core_discovery_continues(self):
        def denied(env):
            raise PermissionError("hypervisor API denied")

        self.files["/home/tester/.ssh/config"] = "Host lab1\n    HostName 192.0.2.10\n"
        sc = self.scanner([scan.StaticProvider("libvirt", denied), scan.SshConfigProvider()])
        snap = sc.list_hosts()
        self.assertEqual(snap["providers"]["libvirt"]["state"], "unavailable")
        self.assertIn("denied", snap["providers"]["libvirt"]["error"])
        self.assertEqual(snap["providers"]["ssh_config"]["state"], "read")
        self.assertIn("lab1", self.by_name(snap))


# ------------------------------------------------------------------------------ SSH config parsing
class SshConfigScenarios(ScenarioBase):
    def parse(self) -> dict:
        self.files.update(SSH_CFG["files"])
        base = Path(SSH_CFG["home"]) / ".ssh"
        return scan.parse_ssh_config(self.files[str(base / "config")], base=base, env=self.env())

    def test_D11_include_match_wildcards_proxycommand(self):
        parsed = self.parse()
        hosts = parsed["hosts"]
        self.assertEqual(sorted(hosts), ["aftermatch", "cmdhost", "included1", "lab1", "v6box", "viajump", "web1", "web2"])
        for pattern in ("*", "bastion-*", "secret", "!secret"):
            self.assertNotIn(pattern, hosts)
        self.assertEqual(hosts["included1"]["port"], 2222)  # Include resolved through the fake read_text
        self.assertTrue(hosts["cmdhost"]["proxy_command"])
        self.assertNotIn("bastion.example.net", json.dumps(hosts["cmdhost"]))  # the command is flagged, never stored
        self.assertTrue(any(n.startswith("Match block present") for n in parsed["notes"]))
        for entry in hosts.values():
            self.assertTrue(entry["match_blocks"])
            self.assertNotEqual(entry["user"], "evil")  # Match body is never applied
            self.assertNotEqual(entry["user"], "wildcarduser")
        self.assertEqual(self.runner.calls, [])  # parsing runs nothing
        provider = scan.SshConfigProvider()
        self.assertEqual(provider.detect(self.env())["state"], "read")
        cands = {c["name"]: c for c in provider.list_candidates(self.env())}
        self.assertTrue(cands["lab1"]["ssh"]["needs_effective_config"])
        self.assertTrue(cands["lab1"]["ssh"]["identity_ref"])
        self.assertNotIn("id_example", json.dumps(cands["lab1"]))  # identity paths are recorded as present only

    def test_D09_port_ipv6_and_jump_preserved(self):
        hosts = self.parse()["hosts"]
        self.assertEqual(hosts["lab1"]["port"], 6722)
        self.assertEqual(hosts["lab1"]["user"], "alice")
        self.assertEqual(hosts["v6box"]["hostname"], "2001:db8::5")
        self.assertEqual(hosts["viajump"]["proxy_jump"], "bastion.example.net")
        argv = scan.ssh_probe_argv({"alias": None, "endpoint": "2001:db8::5", "port": 6722, "user": "alice", "jump": "bastion.example.net"},
                                   known_hosts=None)
        self.assertEqual(argv[argv.index("-p") + 1], "6722")
        self.assertEqual(argv[argv.index("-J") + 1], "bastion.example.net")
        self.assertEqual(argv[-2:], ["--", "alice@2001:db8::5"])
        # An OpenSSH alias keeps its own routing: no -p / -J injected, so ssh applies Port 6722 itself.
        alias_argv = scan.ssh_probe_argv({"alias": "lab1", "endpoint": "192.0.2.10", "port": 6722}, known_hosts=None)
        self.assertNotIn("-p", alias_argv)
        self.assertEqual(alias_argv[-2:], ["--", "lab1"])
        # No hard-coded 22 for a manual endpoint with no port recorded other than the default.
        self.assertEqual(scan.ssh_probe_argv({"endpoint": "192.0.2.10", "port": 6722}, known_hosts=None).count("22"), 0)
        with self.assertRaises(ValueError):
            scan.ssh_probe_argv({"endpoint": "-oProxyCommand=evil"}, known_hosts=None)


# ------------------------------------------------------------------------------ probing and exclusion
class ProbeScenarios(ScenarioBase):
    def manual_scanner(self, **kw) -> StagedScanner:
        sc = self.scanner(**kw)
        sc.add_manual(host="192.0.2.10", port=6722, user="alice", label="lab1")
        sc.list_hosts()
        return sc

    def test_D06_tailscale_ssh_identity_accepted_without_imported_key(self):
        sc = self.manual_scanner()
        host = self.probe_one(sc, "lab1")
        self.assertEqual(host["axes"]["ssh"]["result"], "ok")
        self.assertEqual(host["state"], "enrollment_required")
        argv = self.runner.of("ssh")[0]
        self.assertNotIn("-i", argv)
        self.assertFalse(any("IdentityFile" in a for a in argv))
        self.assertEqual(argv[argv.index("-p") + 1], "6722")
        self.assertEqual(host["inventory"]["os"], "Ubuntu 24.04 LTS")

    def test_D07_tailscale_ssh_check_requires_human(self):
        self.assertEqual(scan.classify_probe_failure(CommandResult(255, "", PROBE["stderr"]["human_check"])),
                         ("human_check_required", "human_check"))
        self.assertEqual(scan.classify_probe_failure(CommandResult(255, "", PROBE["stderr"]["auth_denied"]))[0], "auth_required")
        self.assertEqual(scan.classify_probe_failure(CommandResult(124, "", "timed out", timed_out=True)), ("timeout", "offline"))
        sc = self.manual_scanner()
        self.runner.ssh = lambda argv: CommandResult(255, "", PROBE["stderr"]["human_check"])
        host = self.probe_one(sc, "lab1")
        self.assertEqual(host["state"], "human_check")
        self.assertIn("browser check", host["next_action"])
        self.runner.ssh = lambda argv: CommandResult(0, PROBE["ok_stdout"])  # retry the selected host after the check
        self.assertEqual(self.probe_one(sc, "lab1")["state"], "enrollment_required")

    def test_D08_ssh_key_or_agent_unavailable(self):
        sc = self.manual_scanner()
        self.runner.ssh = lambda argv: CommandResult(255, "", PROBE["stderr"]["auth_denied"])
        host = self.probe_one(sc, "lab1")
        self.assertEqual(host["state"], "auth_required")
        self.assertEqual(host["axes"]["ssh"]["result"], "auth_required")
        self.assertIn("BatchMode=yes", self.runner.calls[0])
        self.assertEqual({c[0] for c in self.runner.calls}, {"ssh"})  # no key search, ssh-add or ssh-agent probing
        self.assertTrue(all("-i" not in c for c in self.runner.calls))

    def test_D10_unknown_vs_changed_host_key_never_silent_trust(self):
        sc = self.manual_scanner()
        self.runner.ssh = lambda argv: CommandResult(255, "", PROBE["stderr"]["host_key_unknown"])
        unknown = self.probe_one(sc, "lab1")
        self.assertEqual((unknown["axes"]["ssh"]["result"], unknown["state"]), ("host_key_unknown", "host_key_review"))
        self.runner.ssh = lambda argv: CommandResult(255, "", PROBE["stderr"]["host_key_mismatch"])
        changed = self.probe_one(sc, "lab1")
        self.assertEqual((changed["axes"]["ssh"]["result"], changed["state"]), ("host_key_mismatch", "failed"))
        for argv in self.runner.of("ssh"):
            self.assertNotIn("StrictHostKeyChecking=no", argv)
            self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertNotIn("StrictHostKeyChecking=no", scan.ssh_probe_argv({"alias": "lab1"}, known_hosts=self.tmp / "kh"))

    def test_D10_confirm_host_key_requires_matching_fingerprint(self):
        def other(argv, stdin):
            if argv[0] == "ssh-keyscan":
                return CommandResult(0, PROBE["keyscan_stdout"])
            if argv[0] == "ssh-keygen":
                return CommandResult(0, PROBE["keygen_stdout"])
            return CommandResult(127, "", "")

        self.runner.other = other
        sc = self.manual_scanner()
        hid = self.by_name(sc.snapshot())["lab1"]["id"]
        scanned = sc.host_key_scan(hid)
        fingerprint = scanned["fingerprints"][0].split()[1]
        with self.assertRaises(ValueError):
            sc.confirm_host_key(hid, "SHA256:" + "B" * 43)
        self.assertFalse((self.tmp / "known_hosts").exists())  # nothing trusted on mismatch
        self.assertEqual(sc.confirm_host_key(hid, fingerprint)["trusted"], 1)
        self.assertEqual(len((self.tmp / "known_hosts").read_text().splitlines()), 1)

    def test_D14_reachable_ssh_no_worker_enrollment_plan(self):
        sc = self.manual_scanner()
        host = self.probe_one(sc, "lab1")
        self.assertEqual(host["inventory"]["arch"], "x86_64")
        self.assertEqual(host["axes"]["bootstrap"]["result"], "ok")
        store = FakeSshStore()
        plan = sc.plan_enrollment([host["id"]], ssh_store=store)
        self.assertEqual(plan["blocked"], [])
        self.assertEqual([s["action"] for s in plan["steps"]], ["register_ssh_target", "install_worker"])
        self.assertEqual(plan["steps"][0]["fields"]["port"], 6722)
        self.assertEqual(store.upserts, [])  # planning changes nothing
        # The observation expires: the plan is blocked until the host is probed again.
        self.clock.now += scan.DEFAULT_LIMITS["observation_ttl"] + 1
        stale = sc.plan_enrollment([host["id"]], ssh_store=store)
        self.assertEqual(stale["steps"], [])
        self.assertIn("probe the host successfully first", stale["blocked"][0]["reason"])
        # A host that was never probed is blocked too.
        sc.add_manual(host="192.0.2.11", label="fresh")
        sc.list_hosts()
        fresh_id = self.by_name(sc.snapshot())["fresh"]["id"]
        self.assertEqual(len(sc.plan_enrollment([fresh_id], ssh_store=store)["blocked"]), 1)

    def test_D16_apply_checkpoint_and_restart_recovery(self):
        sc = self.scanner()
        sc.add_manual(host="192.0.2.10", label="alpha")
        sc.add_manual(host="192.0.2.11", label="beta")
        sc.list_hosts()
        ids = [self.probe_one(sc, n)["id"] for n in ("alpha", "beta")]
        store, workers = FakeSshStore(fail_once={"beta"}), FakeWorkers()
        plan = sc.plan_enrollment(ids, ssh_store=store)
        self.assertEqual(len(plan["steps"]), 4)
        first = sc.apply_enrollment(plan["id"], ssh_store=store, workers=workers)
        self.assertEqual(first["status"], "partial")
        self.assertEqual([r["outcome"] for r in first["results"]], ["done", "done", "failed", "skipped"])
        self.assertIn("simulated", first["results"][2]["error"])
        self.assertEqual(first["steps"][3].get("skipped_reason"), "an earlier step for this host failed")
        self.assertFalse(first["steps"][3].get("done"))
        # Restart: a new scanner on the same state file resumes; completed steps are skipped.
        sc2 = self.scanner()
        second = sc2.apply_enrollment(plan["id"], ssh_store=store, workers=workers)
        self.assertEqual(second["status"], "applied")
        self.assertEqual([r["outcome"] for r in second["results"]], ["already_done", "already_done", "done", "done"])
        self.assertEqual([u["name"] for u in store.upserts], ["alpha", "beta"])  # alpha registered exactly once
        self.assertEqual(workers.installed, ["alpha", "beta"])
        self.assertEqual({r["kind"] for r in sc2.state["owned_resources"]}, {"ssh_target", "worker"})
        rolled = sc2.rollback_plan(plan["id"], ssh_store=store)
        self.assertEqual(sorted(store.removed), ["alpha", "beta"])
        self.assertEqual(len(rolled["preserved"]), 2)  # workers are listed, never auto-deleted

    def test_D16_restart_marks_running_run_interrupted(self):
        state = scan.empty_state()
        state["runs"] = [{"id": "run_x", "kind": "probe", "status": "running", "started_at": 1.0, "finished_at": None,
                          "scope": {"host_ids": ["h_1"]}, "hosts": {"h_1": {"outcome": "ok"}},
                          "stages": {"S3": {"state": "running"}, "S4": {"state": "pending"}}}]
        (self.tmp / "state.json").write_text(json.dumps(state))
        sc = self.scanner()
        run = sc.run("run_x")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["stages"]["S3"]["state"], "interrupted")
        self.assertEqual(run["hosts"], {"h_1": {"outcome": "ok"}})  # partial results preserved
        self.assertEqual(run["finished_at"], self.clock.now)
        persisted = json.loads((self.tmp / "state.json").read_text())
        self.assertEqual(persisted["runs"][0]["status"], "interrupted")

    def test_D17_excluded_identities_are_never_contacted(self):
        excluded = ["ora2", "ora4.example.ts.net", "192.0.2.99", "198.51.100.0/24", "aliased-secret"]
        self.files["/home/tester/.ssh/config"] = "Host aliased-secret\n    HostName 192.0.2.77\n    Port 6722\n"
        sc = self.scanner([scan.SshConfigProvider()], excluded=excluded)
        sc.add_manual(host="ora2.example.net", label="by-short-alias")   # matches the "ora2" entry through its short name
        sc.add_manual(host="ora4.example.ts.net", label="by-fqdn")
        sc.add_manual(host="192.0.2.99", label="by-ip")
        sc.add_manual(host="198.51.100.7", label="by-cidr")
        sc.add_manual(host="ok.example.net", label="allowed-name")
        sc.add_manual(host="192.0.2.10", label="allowed-ip")
        snap = sc.list_hosts()
        hosts = self.by_name(snap)
        for name in ("by-short-alias", "by-fqdn", "by-ip", "by-cidr", "aliased-secret"):
            self.assertEqual(hosts[name]["state"], "excluded", name)
            self.assertIsNone(hosts[name]["ssh"], name)
        for name in ("allowed-name", "allowed-ip"):
            self.assertEqual(hosts[name]["state"], "new")
        self.assertEqual(self.runner.calls, [])
        ids = [h["id"] for h in snap["hosts"] if h["id"] != "local"]
        run = sc.start_probe(ids, background=False)
        self.assertEqual(run["status"], "done")
        self.assertEqual(sorted(o["outcome"] for o in run["hosts"].values()), ["excluded"] * 5 + ["ok"] * 2)
        self.assertEqual(len(self.runner.of("ssh")), 2)  # only the two allowed hosts were probed
        forbidden = ("ora2", "ora4", "192.0.2.99", "198.51.100.", "192.0.2.77", "aliased-secret")
        for call in self.runner.calls:
            self.assertFalse(any(f in " ".join(call) for f in forbidden), call)
        self.assertEqual(self.resolver.calls, ["ok.example.net"])
        # Enrollment refuses an excluded host and host-key scanning has no endpoint to use.
        plan = sc.plan_enrollment([hosts["by-fqdn"]["id"]], ssh_store=FakeSshStore())
        self.assertEqual(plan["blocked"][0]["reason"], "excluded by discovery policy")
        with self.assertRaises(ValueError):
            sc.host_key_scan(hosts["by-fqdn"]["id"])

    def test_D17_is_excluded_matches_every_equivalent_identity(self):
        tokens = scan.identity_tokens({"name": "ora2", "dns_name": "ora2.example.net", "addresses": ["192.0.2.20"], "provider": "manual"})
        for entry in ("ora2", "ORA2.", "ora2.example.net", "192.0.2.20", "192.0.2.0/24"):
            self.assertEqual(scan.is_excluded(tokens, [entry]), entry)
        self.assertIsNone(scan.is_excluded(tokens, ["ora4", "198.51.100.0/24", ""]))

    def test_D28_status_axes_stay_independent(self):
        sc = self.scanner()
        sc.add_manual(host="named.example.net", label="named")
        sc.list_hosts()
        ok = self.probe_one(sc, "named")
        self.assertEqual({k: ok["axes"][k]["result"] for k in ("dns", "ssh", "bootstrap")}, {"dns": "ok", "ssh": "ok", "bootstrap": "ok"})
        self.runner.ssh = lambda argv: CommandResult(255, "", PROBE["stderr"]["auth_denied"])
        again = self.probe_one(sc, "named")
        self.assertEqual(again["axes"]["ssh"]["result"], "auth_required")
        self.assertEqual(again["axes"]["dns"]["result"], "ok")          # name resolution is a separate fact
        self.assertEqual(again["axes"]["bootstrap"]["result"], "ok")    # last-known inventory is kept, not erased
        self.assertEqual(again["state"], "auth_required")

    def test_D33_duplicate_probe_returns_same_run_while_running(self):
        entered, release = threading.Event(), threading.Event()

        def blocking(argv):
            entered.set()
            release.wait(5)
            return CommandResult(0, PROBE["ok_stdout"])

        sc = self.manual_scanner()
        self.runner.ssh = blocking
        hid = self.by_name(sc.snapshot())["lab1"]["id"]
        first = sc.start_probe([hid])
        self.assertTrue(entered.wait(5))
        second = sc.start_probe([hid, hid])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["status"], "running")
        release.set()
        final = sc.wait(first["id"], timeout=5)
        self.assertEqual(final["status"], "done")
        self.assertEqual(len(self.runner.of("ssh")), 1)  # no duplicate probe
        # After completion a new request starts a new run.
        third = sc.start_probe([hid], background=False)
        self.assertNotEqual(third["id"], first["id"])


# ------------------------------------------------------------------------------ mesh coexistence (D21)
class CoexistenceScenarios(unittest.TestCase):
    @staticmethod
    def settings(**mesh):
        import mesh as mesh_mod

        data, errors = mesh_mod.parse_settings({"mesh": {"enabled": True, "token_ref": "env:LB_MESH_TOKEN", **mesh}})
        assert not errors, errors
        return data

    def test_D21_overlap_blocked_with_alternatives_then_mitigations(self):
        import mesh as mesh_mod

        ts = {"state": "running", "self_addresses": ["100.101.0.1"]}
        blocked = mesh_mod.preflight(self.settings(), tailscale=ts)
        self.assertFalse(blocked["ok"])
        self.assertIn("range_overlap", blocked["blocking"])
        self.assertIn("tailscale_address_in_mesh_range", blocked["blocking"])
        self.assertEqual({a["id"] for a in blocked["alternatives"]}, {"custom_range", "route_exclusions_reviewed", "isolated_namespace"})
        for mitigation in ("route_exclusions_reviewed", "isolated_namespace"):
            with self.subTest(mitigation=mitigation):
                ok = mesh_mod.preflight(self.settings(acknowledge_tailscale_overlap=True, overlap_mitigation=mitigation), tailscale=ts)
                self.assertTrue(ok["ok"])
        # A flag alone is not enough: acknowledgement and a mitigation are both required.
        self.assertFalse(mesh_mod.preflight(self.settings(acknowledge_tailscale_overlap=True), tailscale=ts)["ok"])
        self.assertFalse(mesh_mod.preflight(self.settings(overlap_mitigation="isolated_namespace"), tailscale=ts)["ok"])

    def test_D21_custom_range_that_still_overlaps_stays_blocked(self):
        import mesh as mesh_mod

        ts = {"state": "running", "self_addresses": ["100.101.0.1"]}
        still = mesh_mod.preflight(self.settings(acknowledge_tailscale_overlap=True, overlap_mitigation="custom_range",
                                                 mesh_range="100.96.0.0/12"), tailscale=ts)
        self.assertFalse(still["ok"])
        inside_cgnat = mesh_mod.preflight(self.settings(acknowledge_tailscale_overlap=True, overlap_mitigation="custom_range",
                                                        mesh_range="100.64.0.0/16"), tailscale=ts)
        self.assertFalse(inside_cgnat["ok"])
        outside = mesh_mod.preflight(self.settings(mesh_range="10.200.0.0/16"), tailscale=ts)
        self.assertTrue(outside["ok"])
        self.assertNotIn("range_overlap", [f["id"] for f in outside["findings"]])

    def test_D21_overlap_is_informational_when_tailscale_not_running(self):
        import mesh as mesh_mod

        check = mesh_mod.preflight(self.settings(), tailscale={"state": "stopped"})
        self.assertTrue(check["ok"])
        self.assertEqual(check["findings"][0]["severity"], "info")


# ------------------------------------------------------------------------------ ledger of unproven scenarios
class DeferredScenarios(unittest.TestCase):
    def test_D05_peer_visible_but_acl_blocks_service(self):
        self.skipTest("deferred: needs a lab tailnet with ACLs; fixtures cannot distinguish ACL denial from a closed port")

    def test_D15_existing_compatible_or_incompatible_worker(self):
        self.skipTest("deferred: worker version handshake and upgrade plans belong to Fleet/worker tests, not the scan seam")

    def test_D18_mesh_absent_and_user_selects_mesh(self):
        self.skipTest("covered elsewhere: tests/test_mesh.py (plan, apply, rollback); live enrollment is UNVERIFIED until the lab")

    def test_D19_mesh_already_enrolled_correct_or_wrong_account(self):
        self.skipTest("covered elsewhere: tests/test_mesh.py (idempotent plan and organization mismatch block)")

    def test_D23_multiple_tunnels_and_domains_per_host(self):
        self.skipTest("deferred: route correlation lives in discovery.py/ingress.py, outside runtime/scan.py")

    def test_D24_token_managed_cloudflared_domain_unavailable(self):
        self.skipTest("deferred: needs cloudflared token-mode fixtures and the optional Cloudflare API remedy (not in scan.py)")

    def test_D25_serve_funnel_and_access_states(self):
        self.skipTest("deferred: exposure/auth state classification is not implemented in scan.py; needs Serve/Funnel fixtures")

    def test_D26_listener_exists_but_not_mcp(self):
        self.skipTest("deferred: S6/S7 route classification is delegated to DiscoveryService (tests/test_discovery.py)")

    def test_D27_mcp_needs_oauth_or_stdio_declared(self):
        self.skipTest("deferred: MCP classification belongs to DiscoveryService; no credential guessing seam exists in scan.py")

    def test_D29_permission_limited_process_or_container_inventory(self):
        self.skipTest("deferred: process/container inventory is a worker capability, not part of the SSH bootstrap script")

    def test_D31_windows_macos_wayland_unsupported_remote_os(self):
        self.skipTest("deferred: scan.py has no capability model for non-Linux remotes yet; needs remote-OS fixtures")

    def test_D34_primary_ingress_change_other_tunnels_active(self):
        self.skipTest("deferred: ingress behaviour is covered by ingress tests, not the discovery scanner")

    def test_D35_public_url_redirects_to_private_target(self):
        self.skipTest("covered elsewhere: tests/test_browser_desktop_policy.py")


if __name__ == "__main__":
    unittest.main()
