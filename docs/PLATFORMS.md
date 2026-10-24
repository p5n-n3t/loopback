# Supported platforms

Loopback runs on Python 3.11+ on Linux, macOS, and Windows. CI
(`.github/workflows/ci.yml`) exercises Python 3.11 and 3.13 on all three,
plus install smoke tests on a spread of Linux distributions.

| Platform | Status | Sandbox backend | Notes |
|---|---|---|---|
| Linux (glibc), x86_64/arm64 | Full | Landlock (5.13+) or bubblewrap fallback | Primary target platform. |
| Linux (musl, e.g. Alpine), x86_64/arm64 | Full | Landlock or bubblewrap, kernel-dependent | Tested in CI (`alpine:3.20`). |
| macOS 13+ | Full | `sandbox-exec`/Seatbelt | Apple marks `sandbox-exec` deprecated; it still ships and enforces. |
| Windows 10/11 | Supported with limitations | None | See [WINDOWS.md](WINDOWS.md). |

Linux distributions smoke-tested in CI install jobs: Debian (bookworm),
Ubuntu (24.04), Fedora (40), Alpine (3.20), Arch, openSUSE Tumbleweed — each
tested with a non-root `alice` account doing the actual install, the same
way a real deployment should.

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
