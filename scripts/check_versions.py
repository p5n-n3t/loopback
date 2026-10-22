#!/usr/bin/env python3
"""Fail when runtime/version.py, packaging/npm/package.json, pyproject.toml and CHANGELOG.md disagree."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", help="version the tag claims (without leading v)")
    args = parser.parse_args()

    runtime = re.search(r'^VERSION = "([^"]+)"', (ROOT / "runtime" / "version.py").read_text(encoding="utf-8"), re.M).group(1)
    npm = json.loads((ROOT / "packaging" / "npm" / "package.json").read_text(encoding="utf-8"))["version"]
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    problems = []
    if npm != runtime:
        problems.append(f"packaging/npm/package.json has {npm}, runtime has {runtime}")
    if pyproject != runtime:
        problems.append(f"pyproject.toml has {pyproject}, runtime has {runtime}")
    if not re.search(rf"^## {re.escape(runtime)}\b", changelog, re.M):
        problems.append(f"CHANGELOG.md has no '## {runtime}' section")
    if args.expect and args.expect != runtime:
        problems.append(f"tag says {args.expect}, runtime says {runtime}")
    for p in problems:
        print("✗", p)
    if not problems:
        print(f"✓ versions agree: {runtime}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
