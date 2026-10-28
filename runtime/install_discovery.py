"""Bounded, read-only installer inventory. Never contacts an SSH target."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def inventory(home: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "binaries": {name: bool(shutil.which(name)) for name in ("tailscale", "cloudflared", "ngrok", "ssh")},
        "tailscale": {"state": "not-installed", "routes": "unknown"},
        "ssh_aliases": [], "ssh_config": "not-found", "scope": "local read-only; no remote connections",
    }
    if result["binaries"]["tailscale"]:
        for command, field in ((["status", "--json"], "state"), (["serve", "status", "--json"], "routes")):
            try:
                proc = subprocess.run(["tailscale", *command], capture_output=True, text=True, timeout=4)
                if proc.returncode:
                    result["tailscale"][field] = "unavailable"
                    continue
                data = json.loads(proc.stdout)
                if field == "state":
                    result["tailscale"].update(state=str(data.get("BackendState") or "unknown"),
                                               peers=len(data.get("Peer") or {}))
                else:
                    result["tailscale"]["routes"] = "present" if data.get("Web") or data.get("TCP") else "none observed"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                result["tailscale"][field] = "unavailable"
    try:
        cfg = home / ".ssh" / "config"
        # Only Host aliases enter the report. No keys, options, addresses or proxy commands are copied.
        with cfg.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream):
                if number > 10000:
                    break
                match = re.match(r"^\s*Host\s+(.+?)(?:\s+#.*)?$", line, re.I)
                if match:
                    result["ssh_aliases"].extend(alias for alias in match[1].split()
                                                 if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", alias))
        result["ssh_aliases"] = sorted(set(result["ssh_aliases"]))[:100]
        result["ssh_config"] = "read"
    except FileNotFoundError:
        pass
    except OSError:
        result["ssh_config"] = "unavailable"
    return result
