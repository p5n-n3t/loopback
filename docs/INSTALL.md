# Installation

Three equivalent install paths all end up running the same cross-platform
implementation: `python3 runtime/cli.py install`.

## Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/p5n-n3t/loopback/main/install.sh | bash
```

`install.sh` detects your package manager (`apt`, `dnf`, `yum`, `zypper`,
`pacman`, `apk` on Linux; `brew` on macOS) to install a Python 3.11+
interpreter if needed, creates a private virtualenv under the config
directory, and then delegates everything else to `cli.py install`. It
accepts the same flags as `loopback install` (below), e.g.:

```bash
curl -fsSL https://raw.githubusercontent.com/p5n-n3t/loopback/main/install.sh \
  | bash -s -- --non-interactive --ingress local
```

`LOOPBACK_VERSION` and `LOOPBACK_CONFIG_DIR` environment variables are
honored by the installer the same way they are by the running server (see
[CONFIGURATION.md](CONFIGURATION.md)).

## Windows

```powershell
irm https://raw.githubusercontent.com/p5n-n3t/loopback/main/install.ps1 | iex
```

`install.ps1` offers to install Python via `winget` if none is found, then
delegates to `cli.py install`. Windows has no OS sandbox backend for
arbitrary shell commands and no persistent-PTY terminal support; autostart
uses a Task Scheduler entry at logon with the current user's rights. See
[WINDOWS.md](WINDOWS.md).

## npm wrapper (any platform with Node 18+)

```bash
npx loopback-mcp install
```

Downloads the release tarball (`loopback-v<version>.tar.gz`) and its
`SHA256SUMS` from the matching GitHub release over HTTPS (redirects
followed, capped at 10, non-HTTPS refused), **verifies the SHA-256 before
extracting anything**, extracts with the system `tar`, then runs
`install.sh`/`install.ps1` for you. No npm dependencies are installed.
`LOOPBACK_SOURCE_DIR` points it at a local checkout instead of downloading
a release (mainly useful for testing a checkout before it's tagged).

## From a source checkout

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh --non-interactive --ingress local
```

## Common install flags

`loopback install` (and `install.sh`/`install.ps1`, which pass through to
it):

| Flag | Effect |
|---|---|
| `--non-interactive` | No prompts; requires the other flags needed to fully configure ingress/profile up front. |
| `--ingress {local,tailscale,cloudflare,both}` | Ingress to configure immediately. |
| `--host`, `--port` | Override the listen address. |
| `--tailscale-mode {serve,funnel}` | See [TAILSCALE.md](TAILSCALE.md). |
| `--cloudflare-token-file`, `--cloudflare-tunnel-id` | See [CLOUDFLARE.md](CLOUDFLARE.md). |
| `--no-service` | Don't register a systemd/launchd/Task Scheduler unit. |
| `--no-autostart` | Install the service but don't enable it at boot/login. |
| `--source <dir>` | Install from a local checkout rather than the packaged release. |
| `--yes` / `-y` | Assume yes on confirmation prompts. |
| `--allow-root` | Permit running the installer as root (not recommended; Loopback is meant to run as your own account). |

## After installing

```bash
loopback status
loopback url
loopback dashboard --open
```

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
