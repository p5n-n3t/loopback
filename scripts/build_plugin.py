#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from version import VERSION  # noqa: E402

SKILLS_DIR = ROOT / "skills"

PLUGIN_JSON = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "loopback",
    "version": VERSION,
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
                    "Route work to registered Loopback nodes",
                    "Work with DOCX, XLSX, and PDF files",
                    "Use optional structured browser automation",
                    "Use optional native desktop automation"
                ],
                "defaultPrompt": [
                    "Use Loopback to inspect the connected machine and help me complete this task.",
                    "Check the connected machine for anything that needs my attention.",
                    "Use Loopback to inspect my registered nodes and compare their state."
                ]
            },
            "onboardingSkill": "./skills/loopback-core/SKILL.md"
        }
    }
}


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

    if not SKILLS_DIR.is_dir():
        print(f"skills directory not found: {SKILLS_DIR}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="loopback-plugin-") as td:
        root = Path(td) / "loopback"
        root.mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (root / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(SKILLS_DIR, root / "skills")
        # Backwards-compatible alias: v3 plugin consumers looked for skills/loopback/SKILL.md.
        legacy = root / "skills" / "loopback"
        legacy.mkdir(exist_ok=True)
        shutil.copy2(SKILLS_DIR / "loopback-core" / "SKILL.md", legacy / "SKILL.md")

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
