"""Actual CLI runtime upgrade/rollback in a disposable container; preinstalled Python dependencies."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory(prefix="loopback-lifecycle-") as td:
    root = Path(td)
    cfg = root / "config with spaces"
    source = root / "next-source"
    shutil.copytree("/src", source, ignore=shutil.ignore_patterns("__pycache__", ".git", ".local-recovery", ".claude", ".codex"))
    marker = source / "runtime/lab_lifecycle_marker.py"
    marker.write_text('VALUE = "next-source"\n')
    env = {**os.environ, "LOOPBACK_CONFIG_DIR": str(cfg), "LOOPBACK_BIN_DIR": str(root / "bin"), "LOOPBACK_SERVICE_MANAGER": "none"}
    checks = []

    def run(*args):
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
        if p.returncode:
            print("CLI operation failed with exit", p.returncode)
            raise RuntimeError("CLI operation failed")
        return p

    def cli(*args):
        return run(sys.executable, str(cfg / "cli.py"), *args)

    def health():
        with urllib.request.urlopen("http://127.0.0.1:3147/healthz", timeout=5) as r:
            assert json.load(r)["ok"]

    try:
        run(
            sys.executable,
            "/src/runtime/cli.py",
            "install",
            "--source",
            "/src",
            "--non-interactive",
            "--ingress",
            "local",
            "--no-service",
            "--yes",
            "--port",
            "3147",
            "--skip-venv",
            "--python-override",
            sys.executable,
        )
        cli("up")
        health()
        secrets = {name: digest(cfg / name) for name in ["token", "oauth-signing.key"]}
        config = json.loads((cfg / "config.json").read_text())
        config["command_timeout"] = 73
        (cfg / "config.json").write_text(json.dumps(config))
        checks.append({"case": "install-preinstalled-dependencies", "outcome": "PASS"})
        cli("upgrade", "--source", str(source), "--yes")
        health()
        assert (cfg / "lab_lifecycle_marker.py").read_text() == marker.read_text()
        assert json.loads((cfg / "config.json").read_text())["command_timeout"] == 73
        assert all(digest(cfg / name) == sha for name, sha in secrets.items())
        checks.append({"case": "actual-upgrade-code-and-secret-preservation", "outcome": "PASS"})
        cli("rollback", "--yes")
        health()
        assert not (cfg / "lab_lifecycle_marker.py").exists()
        assert all(digest(cfg / name) == sha for name, sha in secrets.items())
        assert json.loads((cfg / "config.json").read_text())["command_timeout"] == 73
        checks.append({"case": "actual-rollback-restores-code-and-preserves-config", "outcome": "PASS"})
    finally:
        if (cfg / "cli.py").exists():
            cli("down")
    try:
        urllib.request.urlopen("http://127.0.0.1:3147/healthz", timeout=2)
        raise AssertionError("listener survives shutdown")
    except OSError:
        pass
    print(
        json.dumps(
            {
                "kind": "actual-runtime-lifecycle",
                "outcome": "PASS",
                "checks": checks,
                "cleanup": "listener-stopped",
                "limitation": "same-version code change with preinstalled dependencies, not cross-release migration",
            }
        )
    )
