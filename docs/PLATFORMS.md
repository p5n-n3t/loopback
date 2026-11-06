# Supported platforms

Loopback targets Linux, macOS, and Windows with Python 3.11+. The current
[GitHub CI workflow](../.github/workflows/ci.yml) runs on Ubuntu with Python
3.11 and 3.13. The table describes host capabilities. The Ubuntu test
matrix does not exercise native macOS or Windows installation.

| Platform | Status | Sandbox backend | Notes |
|---|---|---|---|
| Linux (glibc), x86_64/arm64 | Full | Landlock (5.13+) or bubblewrap fallback | Primary target platform. |
| Linux (musl, e.g. Alpine), x86_64/arm64 | Full | Landlock or bubblewrap, kernel-dependent | Requires a compatible Python and sandbox backend. |
| macOS 13+ | Full | `sandbox-exec`/Seatbelt | Requires `sandbox-exec` to be available on the host. |
| Windows 10/11 | Supported with limitations | None | See [WINDOWS.md](WINDOWS.md). |

For local end-to-end checks, the [install smoke harness](../scripts/install-smoke.sh)
uses an isolated configuration directory and checks health and MCP initialization.

## What differs per platform

- **OS sandbox** — see the table above and
  [PERMISSIONS.md](PERMISSIONS.md) for backend-specific caveats. File-tool
  policy (`read_file`/`write_file`/etc.) is enforced identically on every
  platform regardless of sandbox availability; only shell-command isolation
  differs.
- **Service management** — systemd user unit (Linux), launchd LaunchAgent
  (macOS), Task Scheduler at logon (Windows). `loopback autostart
  [on|off|status]` abstracts over whichever is in use.
- **Persistent terminals (`terminal_start`/PTY)** — require a real PTY;
  not available on Windows. One-shot `execute` and background `job_start`
  work everywhere.
- **Identity/config-dir resolution** — the same logic
  (`runtime/identity.py`) runs everywhere, but the actual paths differ:
  `~/.config/loopback` (XDG) on Linux/macOS vs. `%LOCALAPPDATA%\Loopback`
  on Windows. See [CONFIGURATION.md](CONFIGURATION.md).
- **Ingress** — Tailscale and Cloudflare Tunnel clients both ship for all
  three platforms; which you can realistically run as an unattended service
  depends on the platform's own service-manager support for it.

## Architecture support

x86_64 and arm64 are both covered for Linux and macOS (Apple Silicon).
Windows support is x86_64 only.

## See also

- [WINDOWS.md](WINDOWS.md) — the full Windows-specific limitation list.
- [INSTALL.md](INSTALL.md), [DEPLOYMENT.md](DEPLOYMENT.md)
- [PERMISSIONS.md](PERMISSIONS.md) — sandbox backend details.
