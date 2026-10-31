"""H04/H06/H08: parsed URL policy, owner-namespaced browser sessions and per-operation desktop readiness.

No real browser, X server or personal profile is used: the adapters receive fake runners.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from browser import BrowserAdapter  # noqa: E402
from desktop import DesktopAdapter  # noqa: E402
from url_policy import UrlPolicy, UrlPolicyError  # noqa: E402

DNS = {
    "example.com": ["93.184.216.34"],
    "localhost.attacker.example": ["203.0.113.9"],
    "rebind.attacker.example": ["127.0.0.1"],
    "intranet.example": ["10.0.0.5"],
    "tailnet.example": ["100.101.1.2"],
    "metadata.attacker.example": ["169.254.169.254"],
    "v6only.example": ["2606:4700::1111"],
    "mapped.example": ["::ffff:10.1.2.3"],
}


def resolver(host: str) -> list[str]:
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


class UrlPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = UrlPolicy(resolver=resolver)

    def deny(self, url: str, code: str):
        with self.assertRaises(UrlPolicyError, msg=url) as ctx:
            self.policy.check(url)
        self.assertEqual(ctx.exception.code, code, url)

    def test_permitted_urls(self):
        self.assertEqual(self.policy.check("https://example.com/path?q=1")["exposure"], "public")
        self.assertEqual(self.policy.check("https://v6only.example/")["addresses"], ["2606:4700::1111"])
        self.assertEqual(self.policy.check("http://localhost:3000/")["exposure"], "loopback")
        self.assertEqual(self.policy.check("http://127.0.0.1:8080/x")["exposure"], "loopback")
        self.assertEqual(self.policy.check("http://[::1]:9000/")["exposure"], "loopback")
        self.assertEqual(self.policy.check("https://example.com:8443/")["port"], 8443)

    def test_prefix_lookalikes_are_rejected(self):
        # The old prefix check accepted all of these.
        self.deny("http://localhost.attacker.example/", "scheme_denied")
        self.deny("http://127.0.0.1.attacker.example/", "scheme_denied")
        self.deny("http://localhost@attacker.example/", "userinfo_denied")
        self.deny("https://user:pass@example.com/", "userinfo_denied")

    def test_scheme_controls_and_encoding(self):
        self.deny("file:///etc/passwd", "scheme_denied")
        self.deny("javascript:alert(1)", "scheme_denied")
        self.deny("http://example.com/", "scheme_denied")
        self.deny("https://exa mple.com/", "invalid_url")
        self.deny("https://example.com/\x00", "invalid_url")
        self.deny("https://example.com\\@evil/", "invalid_url")
        self.deny("https://example.com:99999/", "invalid_url")
        self.deny("https://example.com:0/", "port_denied")
        self.deny("https://example.com:22/", "port_denied")

    def test_ambiguous_ipv4_forms(self):
        for host in ("2130706433", "0x7f.1", "0177.0.0.1", "127.1"):
            self.deny(f"https://{host}/", "ambiguous_host")

    def test_private_metadata_and_rebinding(self):
        self.deny("https://intranet.example/", "private_denied")
        self.deny("https://tailnet.example/", "private_denied")  # CGNAT: Tailscale and Mesh ranges
        self.deny("https://rebind.attacker.example/", "private_denied")
        self.deny("https://metadata.attacker.example/", "metadata_denied")
        self.deny("https://169.254.169.254/latest/meta-data", "metadata_denied")
        self.deny("https://[fe80::1]/", "private_denied")
        self.deny("https://mapped.example/", "private_denied")
        self.deny("https://nxdomain.example/", "dns_failure")

    def test_operator_can_permit_named_private_networks(self):
        policy = UrlPolicy(resolver=resolver, private_networks=["10.0.0.0/24"])
        self.assertEqual(policy.check("https://intranet.example/")["exposure"], "private")
        with self.assertRaises(UrlPolicyError):
            policy.check("https://tailnet.example/")


class FakeBrowserRunner:
    """Records agent-browser argv and simulates per-session current URLs."""

    def __init__(self, redirect_to: str | None = None):
        self.calls: list[list[str]] = []
        self.urls: dict[str, str] = {}
        self.redirect_to = redirect_to

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        session, action = argv[2], argv[3]
        out = ""
        if action == "open":
            self.urls[session] = self.redirect_to or argv[4]
        elif action == "get":
            out = self.urls.get(session, "about:blank")
        return subprocess.CompletedProcess(argv, 0, out, "")


class BrowserAdapterTests(unittest.TestCase):
    def make(self, runner):
        b = BrowserAdapter("agent-browser", url_policy=lambda: UrlPolicy(resolver=resolver), runner=runner)
        b._which = lambda name: "/usr/bin/agent-browser"
        return b

    def test_sessions_are_namespaced_per_owner(self):
        runner = FakeBrowserRunner()
        b = self.make(runner)
        b.open("https://example.com/", "work", owner="oauth:a#claude")
        self.assertTrue(b.url("work", owner="oauth:a#claude")["url"].startswith("https://example.com"))
        with self.assertRaises(KeyError):
            b.url("work", owner="oauth:a#chatgpt")
        with self.assertRaises(KeyError):
            b.close("work", owner="oauth:a#chatgpt")
        names = {argv[2] for argv in runner.calls}
        self.assertEqual(len(names), 1)
        self.assertNotIn("work", names, "the raw session name is never shared across owners")

    def test_redirect_outside_policy_closes_session(self):
        runner = FakeBrowserRunner(redirect_to="http://169.254.169.254/latest")
        b = self.make(runner)
        with self.assertRaises(UrlPolicyError):
            b.open("https://example.com/", "s1", owner="o1")
        self.assertEqual(runner.calls[-1][3], "close")
        self.assertEqual(b.status()["open_sessions"], 0)

    def test_denied_url_never_reaches_the_browser(self):
        runner = FakeBrowserRunner()
        b = self.make(runner)
        with self.assertRaises(UrlPolicyError):
            b.open("http://localhost.attacker.example/", owner="o1")
        self.assertEqual(runner.calls, [])

    def test_status_separates_installed_and_functional(self):
        b = BrowserAdapter("definitely-not-installed-loopback-browser")
        status = b.status()
        self.assertFalse(status["dependency_installed"])
        self.assertIsNone(status["functional"])
        self.assertEqual(status["supported_actions"], [])
        self.assertTrue(status["remedy"])

    def test_idle_sessions_expire(self):
        runner = FakeBrowserRunner()
        b = self.make(runner)
        b.session_ttl = 10
        b.open("https://example.com/", "s", owner="o")
        import time
        expired = b.expire_sessions(now=time.time() + 60)
        self.assertEqual(len(expired), 1)
        with self.assertRaises(KeyError):
            b.snapshot("s", owner="o")


def fake_which(present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


class DesktopReadinessTests(unittest.TestCase):
    def runner_ok(self, argv, **kwargs):
        if argv[1:] == ["getdisplaygeometry"]:
            return subprocess.CompletedProcess(argv, 0, "1920 1080\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def test_display_string_alone_is_not_enough(self):
        def refused(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "Error: Can't open display: :0")
        d = DesktopAdapter(runner=refused, which=fake_which({"xdotool"}), environ={"DISPLAY": ":0"}, system="Linux")
        status = d.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["x11"]["state"], "refused")
        self.assertFalse(status["operations"]["click"]["ready"])
        with self.assertRaises(RuntimeError) as ctx:
            d.click(1, 1)
        self.assertIn("desktop_click_unavailable", str(ctx.exception))

    def test_screenshot_readiness_is_separate(self):
        d = DesktopAdapter(runner=self.runner_ok, which=fake_which({"xdotool"}), environ={"DISPLAY": ":5"}, system="Linux")
        status = d.status()
        self.assertTrue(status["available"])
        self.assertTrue(status["operations"]["click"]["ready"])
        self.assertTrue(status["operations"]["windows"]["ready"], "xdotool enumeration fallback")
        self.assertFalse(status["operations"]["screenshot"]["ready"])
        self.assertIn("scrot", status["operations"]["screenshot"]["reason"])

    def test_wayland_and_other_platforms_are_explicit(self):
        d = DesktopAdapter(runner=self.runner_ok, which=fake_which({"xdotool"}),
                           environ={"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, system="Linux")
        self.assertIn("Wayland", d.status()["reason"])
        mac = DesktopAdapter(runner=self.runner_ok, which=fake_which({"xdotool"}), environ={}, system="Darwin")
        self.assertIn("Darwin", mac.status()["reason"])
        headless = DesktopAdapter(runner=self.runner_ok, which=fake_which({"xdotool"}), environ={}, system="Linux")
        self.assertIn("DISPLAY", headless.status()["reason"])

    def test_action_output_is_verified_argv(self):
        calls = []

        def record(argv, **kwargs):
            calls.append(argv)
            return self.runner_ok(argv, **kwargs)
        d = DesktopAdapter(runner=record, which=fake_which({"xdotool"}), environ={"DISPLAY": ":5"}, system="Linux")
        self.assertEqual(d.click(10, 20, 1), {"x": 10, "y": 20, "button": 1})
        self.assertEqual(calls[-1], ["/usr/bin/xdotool", "mousemove", "--sync", "10", "20", "click", "1"])
        with self.assertRaises(ValueError):
            d.key("ctrl+alt+$(reboot)")


if __name__ == "__main__":
    unittest.main()
