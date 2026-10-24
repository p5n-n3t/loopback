<p align="center"><img src="https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/2026-10-24/kGRaI0JCrKrjv8NSFfcjZ.png" width="288" alt="Loopback"></p>

<p align="center">
  <img src="assets/loopback-logo.png" alt="Loopback" width="500">
</p>

[![M8ven Score](https://m8ven.ai/badge/mcp/p5n-n3t-loopback-1cyblg)](https://m8ven.ai/mcp/p5n-n3t-loopback-1cyblg?s=readme)

# Loopback

**A self-hostable MCP control plane for computers and servers.**

Loopback runs on a machine you control and exposes it to MCP-capable clients
(agentic IDEs, web agents, your own tooling) as a single authenticated
Streamable HTTP endpoint — with a real OAuth 2.1 authorization server,
machine-side policy enforcement, an OS-level sandbox where one is available,
and an operations dashboard, all in one process. There is no mandatory
hosted relay: you choose the ingress (local-only, Tailscale, Cloudflare
Tunnel, or your own reverse proxy).

```text
                 MCP client (IDE, web agent, your own code)
                              |
                 HTTPS: local-only / Tailscale / Cloudflare Tunnel
                              |
                 +-------------------------------+
                 |   Loopback  (one process)      |
                 |  OAuth 2.1 AS + MCP resource    |
                 |  server, policy, sandbox,       |
                 |  audit, dashboard                |
                 +-------------------------------+
                   |          |           |
                   v          v           v
              filesystem   shell /     registered
              + documents  jobs /      nodes & SSH
                           terminals    targets (fleet)
```

## Why Loopback?

- **Self-hosted by default** — no account with a third party is required to
  operate your own machine through MCP.
- **Standards-based auth** — OAuth 2.1 authorization code + mandatory PKCE,
  Dynamic Client Registration and Client ID Metadata Documents, RFC 8707
  resource indicators, refresh-token rotation with reuse detection. Scoped,
  revocable API tokens for bearer-only clients. See
  [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).
- **Machine-side policy, not just client-side trust** — filesystem
  allow/deny roots, per-tool allow/deny, command patterns that require
  approval or are hard-denied outright, and policy profiles from `locked`
  through `admin`. See [docs/PERMISSIONS.md](docs/PERMISSIONS.md).
- **Real OS sandboxing where the platform offers one** — Landlock on Linux,
  bubblewrap as a fallback, Seatbelt on macOS; file tools are always
  enforced server-side regardless of sandbox availability. See
  [docs/PERMISSIONS.md](docs/PERMISSIONS.md).
- **Real terminal workflows** — bounded one-shot execution, background jobs
  with incremental output, and persistent PTY terminals.
- **Fleet-capable** — register other Loopback nodes or plain SSH targets and
  route MCP tool calls to them, each with its own independent policy. See
  [docs/FLEET.md](docs/FLEET.md).
- **An operations dashboard** — topology, fleet, filesystem, shell, jobs,
  logs, security, backup/recovery and settings, behind its own session auth.
  See [docs/DASHBOARD.md](docs/DASHBOARD.md).
- **Document tools** — structured DOCX, XLSX and PDF read/write helpers
  instead of shelling out to convert office documents.
- **Bundled Agent Skills** — ready-to-package skill guidance for coding
  agents that use Loopback. See [docs/SKILLS.md](docs/SKILLS.md).

A dashboard screenshot lives at `docs/images/dashboard-overview.png` once you
have added one for your own deployment — Loopback ships no binary
screenshots by default.

## Install

One-liners (download, verify checksums, install):

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/p5n-n3t/loopback/main/install.sh | bash

# Windows (PowerShell)
irm https://github.com/p5n-n3t/loopback/releases/latest/download/install.ps1 | iex

# Any platform with Node.js 18+
npx loopback-mcp install
```

From a checkout:

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh --non-interactive --ingress local
```

Full options, service-manager details and platform notes are in
[docs/INSTALL.md](docs/INSTALL.md) and [docs/PLATFORMS.md](docs/PLATFORMS.md)
(Windows specifics: [docs/WINDOWS.md](docs/WINDOWS.md)).

Once installed:

```bash
loopback status       # is it running, on what port/ingress
loopback url          # the MCP endpoint, e.g. https://loopback.example.com/mcp
loopback dashboard --open
```

## MCP tools

Filesystem (`read_file`, `write_file`, `list_dir`, `patch_file`,
`search_text`, `tail_file`, `git_info`), execution (`execute`, `job_*`,
`terminal_*`, `process_list`, `process_kill`), documents (`docx_*`,
`xlsx_*`, `pdf_*`), optional browser and native-desktop automation, system
and policy inspection (`diagnostics`, `machine_info`, `policy_status`), and
fleet routing (`node_list`, `node_call`, `node_read_file`, `node_execute`,
`node_diagnostics`). Every tool's MCP scope and annotations are listed in
[docs/TOOLS.md](docs/TOOLS.md).

## Policy profiles

```bash
loopback policy show
loopback policy profile restricted   # default; alias: standard
loopback policy profile admin        # alias: trusted
loopback policy profile read-only
loopback policy profile development
loopback policy profile locked
```

Profiles set a baseline for filesystem mode, shell/process/fleet access,
approval strictness, and whether an OS sandbox is required for shell
execution. Full definitions, the default denied roots, and the sandbox
backend matrix are in [docs/PERMISSIONS.md](docs/PERMISSIONS.md).

## Dashboard

```bash
loopback dashboard --open
```

A vanilla-JS single-page app, served only to authenticated sessions, with a
strict Content-Security-Policy (no inline script or style). Covers Overview,
Topology, Local machine, Fleet, Filesystem, Shell, MCP tools, Authentication,
Cloudflare, Tailscale, SSH config, Jobs, Logs & diagnostics, Security,
Backup & recovery, Updates, and Settings, plus a first-run Welcome wizard.
See [docs/DASHBOARD.md](docs/DASHBOARD.md).

## Fleet

```bash
loopback node add build-server https://build-server.example.com/mcp
loopback ssh add nas alice@nas.example.com --identity ~/.ssh/id_ed25519_loopback
```

One gateway can route MCP calls to other Loopback nodes or to plain SSH
targets; each gets independent policy and never inherits the local machine's
allowed roots. See [docs/FLEET.md](docs/FLEET.md).

## Authentication

- OAuth 2.1 clients (agentic IDEs, web agents that support OAuth): discovery
  via `/.well-known/oauth-protected-resource` and
  `/.well-known/oauth-authorization-server`, authorization code + PKCE,
  scoped to `loopback:read` / `loopback:write` / `loopback:exec` /
  `loopback:fleet` (never `loopback:admin`).
- Bearer-only clients: a scoped, revocable API token minted from the
  dashboard (`loopback auth token create ...`), not the permanent machine
  token.
- The machine token is a local bootstrap/recovery credential, rotatable with
  `loopback auth rotate-machine-token`.

See [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) and, if you are looking
for a way to turn a consumer AI subscription's own login session into an API
key, [docs/OAUTH-TO-BEARER.md](docs/OAUTH-TO-BEARER.md) for why that specific
thing is intentionally not supported.

## Ingress and exposure

Local-only (127.0.0.1, nothing exposed), Tailscale (tailnet-only Serve, or
public Funnel), or Cloudflare Tunnel (named or token tunnel, with an access
policy of OAuth, Cloudflare Access, or both). See
[docs/TAILSCALE.md](docs/TAILSCALE.md), [docs/CLOUDFLARE.md](docs/CLOUDFLARE.md)
and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Agent Plugin / Skills package

```bash
python3 scripts/build_plugin.py \
  --mcp-url https://loopback.example.com/mcp \
  --output dist/loopback-plugin.zip
```

Bundles `plugin.json`, `mcp.json`, and the whole `skills/` directory
(`loopback-core`, `loopback-fleet`, `loopback-admin`,
`loopback-troubleshooting`, `loopback-mcp-development`). See
[docs/SKILLS.md](docs/SKILLS.md) and [docs/PLUGIN.md](docs/PLUGIN.md).

## Documentation

- [Install](docs/INSTALL.md) · [Upgrade](docs/UPGRADE.md) ·
  [Migrating from v3](docs/MIGRATION-v4.md) · [Recovery](docs/RECOVERY.md)
- [Configuration](docs/CONFIGURATION.md) · [Permissions & sandboxing](docs/PERMISSIONS.md)
- [Authentication](docs/AUTHENTICATION.md) · [OAuth vs. bearer tokens](docs/OAUTH-TO-BEARER.md)
- [Dashboard](docs/DASHBOARD.md) · [Fleet](docs/FLEET.md)
- [Tailscale](docs/TAILSCALE.md) · [Cloudflare](docs/CLOUDFLARE.md) · [Deployment](docs/DEPLOYMENT.md)
- [MCP tools](docs/TOOLS.md) · [Usage](docs/USAGE.md) · [Web agents](docs/WEB-AGENTS.md)
- [Agent Skills](docs/SKILLS.md) · [Plugin packaging](docs/PLUGIN.md)
- [Architecture](docs/ARCHITECTURE.md) · [Admin HTTP API](docs/ADMIN-API.md)
- [Platforms](docs/PLATFORMS.md) · [Windows notes](docs/WINDOWS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md) · [Benchmarks](docs/BENCHMARKS.md)
- [Releasing](docs/RELEASING.md) · [Open-source / hosted product boundary](docs/MONETIZATION.md)
- [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## Supported platforms

| Platform | Support | Sandbox |
|---|---|---|
| Linux (glibc), x86_64 / arm64 | Full | Landlock (kernel 5.13+) or bubblewrap fallback |
| Linux (musl, e.g. Alpine), x86_64 / arm64 | Full | bubblewrap fallback |
| macOS 13+ | Full | `sandbox-exec` (Seatbelt) |
| Windows 10/11 | Supported with limitations | None — see [docs/WINDOWS.md](docs/WINDOWS.md) |

See [docs/PLATFORMS.md](docs/PLATFORMS.md) for the complete matrix and
per-distribution installer notes.

## License

MIT. See [LICENSE](LICENSE).

### Independent workers and tunnels

Run HTTP or stdio MCP services on separate Linux workers from the central dashboard, with independent OAuth, Cloudflare or Tailscale exposure, and live process/host telemetry. See [Workers and independent MCP services](docs/WORKERS.md) for setup, safeguards and current limitations. The interface adapts the supplied [Rackwise Figma kit](https://www.figma.com/design/L1tPbHX8taPk3iRoMOsACC/Rackwise---Warehouse-dashboard); the symbol and wordmark use the supplied Loopback layers.
