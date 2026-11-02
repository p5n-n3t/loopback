"""Run from the Oracle Docker host; clean only exact resources created by this run."""

import argparse
import json
import subprocess
import uuid
from pathlib import Path


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=180).stdout.strip()


parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
run = "loopback-net-" + uuid.uuid4().hex[:10]
containers = []
network_created = False
report = {"run_id": run, "image": args.image, "image_digest": command("docker", "image", "inspect", "--format", "{{.Id}}", args.image), "outcome": "FAIL", "cleanup": "pending"}
try:
    command("docker", "network", "create", "--internal", "--label", "loopback.lab.run=" + run, run)
    network_created = True
    for mode in ["mcp", "auth", "redirect", "ordinary", "offline"]:
        name = run + "-" + mode
        command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            run,
            "--network-alias",
            mode,
            "--label",
            "loopback.lab.run=" + run,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-e",
            "LAB_MODE=" + mode,
            args.image,
            "python",
            "/src/lab/network/fixture.py" if mode != "offline" else "-c",
            *([] if mode != "offline" else ["import time; time.sleep(120)"]),
        )
        containers.append(name)
    # Real readiness checks run from an isolated observer, not host-side published ports.
    readiness = "import socket,time; end=time.monotonic()+30\nfor host in ['mcp','auth','redirect','ordinary']:\n while True:\n  try:\n   s=socket.create_connection((host,8765),timeout=1);s.close();break\n  except OSError:\n   if time.monotonic()>end:raise\n   time.sleep(.2)"
    containers.append(run + "-ready")
    command(
        "docker",
        "run",
        "--rm",
        "--name",
        run + "-ready",
        "--label",
        "loopback.lab.run=" + run,
        "--network",
        run,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        args.image,
        "python",
        "-c",
        readiness,
    )
    containers.append(run + "-observer")
    result = command(
        "docker",
        "run",
        "--rm",
        "--name",
        run + "-observer",
        "--label",
        "loopback.lab.run=" + run,
        "--network",
        run,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        args.image,
        "python",
        "/src/lab/network/observer.py",
    )
    report["result"] = json.loads(result)
    report["outcome"] = "PASS"
except Exception as exc:
    report["error_class"] = type(exc).__name__
    if isinstance(exc, subprocess.CalledProcessError):
        report["diagnostic"] = exc.stderr[-2000:]
finally:
    failures = []
    for name in containers:
        try:
            if not command("docker", "ps", "-aq", "--filter", "name=^/" + name + "$"):
                continue
            labels = json.loads(command("docker", "inspect", "--format", "{{json .Config.Labels}}", name))
            assert labels.get("loopback.lab.run") == run
            command("docker", "rm", "-f", name)
        except Exception:
            failures.append(name)
    if network_created:
        try:
            labels = json.loads(command("docker", "network", "inspect", "--format", "{{json .Labels}}", run))
            assert labels.get("loopback.lab.run") == run
            command("docker", "network", "rm", run)
        except Exception:
            failures.append(run)
    report["cleanup"] = "clean" if not failures else "residual"
    report["residual_resources"] = failures
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
if report["outcome"] != "PASS" or report["cleanup"] != "clean":
    raise SystemExit(1)
