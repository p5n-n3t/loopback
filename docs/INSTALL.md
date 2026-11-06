---
title: Install Loopback
description: Install the current Loopback MCP gateway from source on Linux, macOS, or Windows.
keywords: [mcp installer, linux, macos, windows, python]
---

# Install Loopback

Use the current source checkout. Install as the account that should run Loopback.
Start with local ingress and verify the gateway before enabling a tunnel.

> [!IMPORTANT]
> The current runtime is on `main`. The older GitHub release does not contain
> the current installer assets, and `loopback-mcp` is unavailable on the public
> npm registry. The release and npm installers in this repo are packaging
> options for a future published distribution.

## Linux and macOS

Install Git, Bash, and Python 3.11+ with `venv` and `ensurepip`.
For example, on Debian 12 or Ubuntu 24.04, install `git`, `python3`,
`python3-venv`, `curl`, and `tar` using your package manager. On macOS,
use a Python installation that includes `venv`, such as Homebrew Python.
Run from a normal terminal as your own user.

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh --non-interactive --ingress local
```

Run `./install.sh --ingress local` for interactive prerequisite prompts.
The installer creates a dedicated virtual environment in the Loopback
configuration directory and registers a user service where supported.

## Windows

Install Git and Python 3.11+ from python.org or via `winget`. Open a normal
PowerShell window. Run the script from the checkout so it uses local source.

```powershell
git clone https://github.com/p5n-n3t/loopback.git
Set-Location loopback
.\install.ps1 --non-interactive --ingress local
```

Omit `--non-interactive` to allow the installer to offer Python installation
through `winget` when available. If PowerShell blocks the script, see the
[Windows guide](WINDOWS.md#installing).

## Common install flags

`loopback install` (and `install.sh`/`install.ps1`, which pass through to
it):

| Flag | Effect |
|---|---|
| `--non-interactive` | No prompts; requires the other flags needed to fully configure ingress/profile up front. |
| `--ingress {local,tailscale,cloudflare,ngrok,both}` | Ingress to configure immediately. |
| `--host`, `--port` | Override the listen address. |
| `--tailscale-mode {serve,funnel}` | See [TAILSCALE.md](TAILSCALE.md). |
| `--cloudflare-token-file`, `--cloudflare-tunnel-id` | See [CLOUDFLARE.md](CLOUDFLARE.md). |
| `--no-service` | Don't register a systemd/launchd/Task Scheduler unit. |
| `--no-autostart` | Install the service but don't enable it at boot/login. |
| `--source <dir>` | Install from a local checkout rather than the packaged release. |
| `--yes` / `-y` | Assume yes on confirmation prompts. |
| `--allow-root` | Permit running the installer as root (not recommended; Loopback is meant to run as your own account). |

## After installing

Save the generated dashboard username and password and the separate OAuth
connector setup key in a password manager. The dashboard credentials sign
you in to the admin UI. The connector setup key approves a client on
Loopback's OAuth consent page. See [Authentication](AUTHENTICATION.md).

```bash
loopback status
loopback url
loopback dashboard --open
loopback doctor --deep
```

Open a new terminal if the launcher directory was just added to your PATH.
The default MCP URL is `http://127.0.0.1:2026/mcp`. For remote clients, see
[Deployment](DEPLOYMENT.md) and [Web agents](WEB-AGENTS.md).

The `bin/loopback` wrapper resolves the config directory the same way the
server does (see [CONFIGURATION.md](CONFIGURATION.md)) and locates
`<config dir>/venv/bin/python` + `<config dir>/cli.py`; if it finds no
installed runtime yet, it falls back to running `runtime/cli.py install`
directly from a source checkout, and it warns if it finds a stale v3
runtime (a `server.py` with no accompanying `cli.py`) so you know to
upgrade rather than silently fail. See
[UPGRADE.md](UPGRADE.md) if that's your situation, and
[MIGRATION-v4.md](MIGRATION-v4.md) for what changed from v3.

## See also

- [PLATFORMS.md](PLATFORMS.md) — supported OS/arch matrix and what differs
  per platform.
- [WINDOWS.md](WINDOWS.md) — Windows-specific limitations.
- [CONFIGURATION.md](CONFIGURATION.md), [DEPLOYMENT.md](DEPLOYMENT.md)
