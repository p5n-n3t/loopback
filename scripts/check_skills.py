#!/usr/bin/env python3
"""Validate bundled Agent Skills against the SKILL.md conventions."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["loopback-core", "loopback-fleet", "loopback-admin", "loopback-troubleshooting", "loopback-mcp-development"]
FORBIDDEN = [re.compile(p) for p in (r"/home/[a-z]+/", r"lbk_[A-Za-z0-9]+_", r"eyJ[A-Za-z0-9_-]{20,}", r"ziopsyop", r"\bora[1-4]\b")]


def main() -> int:
    problems: list[str] = []
    skills_dir = ROOT / "skills"
    for name in REQUIRED:
        path = skills_dir / name / "SKILL.md"
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            problems.append(f"{name}: no YAML frontmatter")
            continue
        front = m.group(1)
        fm_name = re.search(r"^name:\s*(\S+)", front, re.M)
        fm_desc = re.search(r"^description:\s*(.+)", front, re.M)
        if not fm_name or fm_name.group(1).strip("'\"") != name:
            problems.append(f"{name}: frontmatter name must equal directory name")
        if not fm_desc or len(fm_desc.group(1).strip()) < 40:
            problems.append(f"{name}: description missing or too short to route on")
        if len(text.splitlines()) > 500:
            problems.append(f"{name}: SKILL.md exceeds 500 lines")
        for pattern in FORBIDDEN:
            if pattern.search(text):
                problems.append(f"{name}: contains forbidden content matching {pattern.pattern}")
        if re.search(r"bypass|circumvent|ignore (the )?(server|policy)", text, re.I) and "never" not in text.lower():
            problems.append(f"{name}: mentions bypassing policy without forbidding it")
    for p in problems:
        print("✗", p)
    if not problems:
        print(f"✓ {len(REQUIRED)} skills valid")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
