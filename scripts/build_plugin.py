#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse


PLUGIN_JSON = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "loopback",
    "version": "3.0.0",
    "description": "Operate authorized computers and servers through a self-hosted MCP control plane.",
    "author": {
        "name": "Loopback",
        "url": "https://github.com/p5n-n3t/loopback"
    },
    "homepage": "https://github.com/p5n-n3t/loopback",
    "repository": "https://github.com/p5n-n3t/loopback",
    "license": "MIT",
    "keywords": ["mcp", "remote-computer", "server", "self-hosted", "automation"],
    "extensions": {
        "com.openai": {
            "interface": {
                "displayName": "Loopback",
                "shortDescription": "Operate your authorized machines",
                "longDescription": "Use a self-hosted Loopback MCP endpoint to inspect files, run approved commands, manage jobs and terminals, observe system state, and route work to registered machines.",
                "developerName": "Loopback",
                "category": "Developer Tools",
                "capabilities": [
                    "Inspect files and repositories",
                    "Run commands and background jobs",
                    "Manage persistent terminal sessions",
                    "Inspect processes and system health",
                    "Route work to registered Loopback nodes"
                ],
                "defaultPrompt": [
                    "Use Loopback to inspect the connected machine and help me complete this task.",
                    "Check the connected machine for anything that needs my attention.",
                    "Use Loopback to inspect my registered nodes and compare their state."
                ]
            },
            "onboardingSkill": "./skills/loopback/SKILL.md"
        }
    }
}

SKILL = """---
name: loopback
description: Operate an authorized computer or server through the Loopback MCP server. Use for filesystem, Git, shell, process, background-job, persistent-terminal, diagnostics, policy, and registered-node tasks.
---

# Loopback

Use the Loopback MCP tools when the user asks to inspect or operate the machine(s)
connected through Loopback.

## Safety and intent

- Respect Loopback policy errors and approval requirements.
- Do not try to bypass a denied path, blocked command, client confirmation, or
  Loopback approval gate.
- Prefer read-only tools for inspection.
- Use one-shot execution for short commands, background jobs for long-running
  non-interactive work, and persistent terminals when state or interaction must
  survive across multiple calls.
- Treat process signaling and shell execution as side-effecting.
- Never expose credentials, tokens, private keys, recovery codes, or unrelated
  secrets in user-visible output.

## Efficient workflows

For local work, call the local tool directly.

For fleet work:
1. call node_list;
2. choose the named node the user intended;
3. use node_call only for that node and tool.

Use job_output offsets rather than repeatedly returning the entire log.

Use terminal_read after terminal_write and avoid tight polling loops.

When a command is blocked with approval_required, explain the requested action
and let the user approve it through Loopback rather than attempting a workaround.
"""


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise argparse.ArgumentTypeError("MCP URL must be an https:// URL")
    if not parsed.path.rstrip("/").endswith("/mcp"):
        raise argparse.ArgumentTypeError("MCP URL must point to the Loopback /mcp endpoint")
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable Loopback Agent Plugin archive.")
    parser.add_argument("--mcp-url", required=True, type=validate_url)
    parser.add_argument("--output", default="dist/loopback-plugin.zip")
    parser.add_argument("--version", default=PLUGIN_JSON["version"])
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(json.dumps(PLUGIN_JSON))
    manifest["version"] = args.version
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "loopback": {
                "type": "streamable-http",
                "url": args.mcp_url
            }
        }
    }

    with tempfile.TemporaryDirectory(prefix="loopback-plugin-") as td:
        root = Path(td) / "loopback"
        (root / "skills" / "loopback").mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (root / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
        (root / "skills" / "loopback" / "SKILL.md").write_text(SKILL, encoding="utf-8")

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
