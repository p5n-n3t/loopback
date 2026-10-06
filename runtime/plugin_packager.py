from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse


SKILL = """---
name: loopback
description: Operate authorized computers and servers through the Loopback MCP machine fabric.
---

Use Loopback when the user asks you to inspect, diagnose, edit, operate, or monitor a computer or server that they have connected.

Operating principles:
- Start with `system_snapshot`, `machine_info`, `diagnostics`, `node_list`, or other read-only tools when you need context.
- Respect the server policy. Never try to work around disabled tools, denied paths, or operator approvals.
- Prefer structured file, process, Git, job, and node tools over ad-hoc shell commands.
- When a coherent workflow needs several shell steps and `task_run` is enabled, group bounded steps into one task. This reduces needless round trips and client approval prompts without bypassing either the client or Loopback safety policy.
- Use `command_start` for long-running commands, then poll with `job_output` instead of repeatedly launching the same command.
- Use persistent terminal sessions only when interactivity is genuinely needed.
- For fleet work, name the target node explicitly. Do not assume the local machine is the intended target.
- Before destructive or service-impacting actions, inspect the current state and explain the intended change unless the user's instruction already makes the action unambiguous.
- If Loopback returns an approval ID, tell the user that the Loopback operator approval is pending; retry with that ID only after approval is granted.
- Never reveal stored credentials, machine tokens, SSH keys, or unrelated secrets.
"""


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MCP URL must be a public HTTPS URL")
    if not parsed.path.endswith("/mcp"):
        raise ValueError("MCP URL must end in /mcp")
    return value.rstrip("/")


def build(mcp_url: str, output: Path, version: str) -> Path:
    mcp_url = validate_url(mcp_url)
    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "loopback",
        "version": version,
        "description": "Self-hosted MCP machine fabric for securely operating authorized computers and servers from web agents.",
        "author": {"name": "p5n-n3t"},
        "repository": "https://github.com/p5n-n3t/loopback",
        "license": "MIT",
        "keywords": ["mcp", "remote-computer", "servers", "self-hosted", "automation"],
        "extensions": {
            "com.openai": {
                "onboardingSkill": "./skills/loopback/SKILL.md",
                "interface": {
                    "displayName": "Loopback",
                    "shortDescription": "Operate your machines",
                    "longDescription": "Connect ChatGPT to your self-hosted Loopback endpoint to inspect and operate authorized computers and servers. Loopback supports files, Git, processes, background jobs, persistent terminals, policy controls, observability, and named fleet nodes while keeping the MCP origin on your own machine.",
                    "developerName": "p5n-n3t",
                    "capabilities": [
                        "Read and edit allowed files",
                        "Inspect Git repositories",
                        "Monitor host resources and processes",
                        "Run policy-authorized tasks and background jobs",
                        "Operate named computers and servers",
                    ],
                    "defaultPrompt": [
                        "Check my connected machine and summarize anything that needs attention.",
                        "Inspect this project, run its checks, and fix the problem using Loopback.",
                        "Compare the health of my connected nodes and flag anything unusual.",
                    ],
                    "brandColor": "#FFD600",
                },
            }
        },
    }
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "loopback": {
                "type": "streamable-http",
                "url": mcp_url,
            }
        },
    }

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "loopback"
        (root / "skills" / "loopback").mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (root / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
        (root / "skills" / "loopback" / "SKILL.md").write_text(SKILL, encoding="utf-8")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(root))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a private Agent Plugin package for this Loopback endpoint.")
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default="3.0.0")
    args = parser.parse_args()
    print(build(args.mcp_url, args.output, args.version))


if __name__ == "__main__":
    main()
