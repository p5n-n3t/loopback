# Loopback

**A self-hosted MCP machine fabric for web agents.**

Loopback lets an authenticated MCP client inspect and operate computers and servers you control. The MCP origin stays on `127.0.0.1`; you choose how it becomes reachable—Cloudflare Tunnel, Tailscale Funnel, local-only access, or a gateway machine.

Loopback is deliberately client-neutral. It is not a ChatGPT-only bridge. Any client that supports compatible remote **Streamable HTTP MCP** and one of Loopback's authentication modes can use the same server. ChatGPT can use OAuth 2.0 + PKCE; clients such as Perplexity can use bearer/API-key authentication when their connector UI supports it.

> Loopback controls real machines. Treat a public endpoint as administrative infrastructure.

## Why Loopback

Many "remote computer" agents depend on a vendor-operated relay. Loopback takes a different approach:

- **works without our cloud** — direct self-hosting is the default architecture
- **bring your own ingress** — Cloudflare, Tailscale, local-only, or your own reverse proxy
- **MCP-first** — the machine interface is an open protocol, not a proprietary chat surface
- **servers are first-class** — laptops, VPSs, Docker hosts, home servers and cloud VMs can be named nodes
- **policy before power** — sensitive paths and mutating tools are controlled by the Loopback operator
- **one connector, many machines** — an optional self-hosted gateway can route work to named SSH nodes
- **human control plane** — dashboard and CLI expose policy, jobs, processes, clients, approvals, nodes and audit history

## Architecture

### Direct node

```text
Web agent
   |
   | Streamable HTTP MCP
   v
Cloudflare / Tailscale / HTTPS reverse proxy
   |
   v
127.0.0.1:2026
   |
   v
Loopback
   |
   +-- filesystem / Git
   +-- jobs / PTYs
   +-- processes / metrics
   +-- optional browser adapter
```

### Self-hosted fleet gateway

```text
                   Web agent
                       |
                       v
                Loopback gateway
                  /     |      \
                 /      |       \
              local    ora3     ora4
                |       |        |
              files    SSH      SSH
              jobs    Docker    nginx
```

The gateway stores node names and SSH targets—not SSH passwords or private keys. It reuses the operator's existing SSH trust.

## Capabilities

### Portable file and Git tools

- `read_file`
- `write_file`
- `list_dir`
- `patch_file`
- `search_text`
- `tail_file`
- `git_info`

All filesystem tools pass through the configured allow/deny policy.

### Commands, jobs and streaming output

- `command_run` — bounded synchronous command
- `command_start` — long-running command with a durable output log
- `job_list`
- `job_output` — incremental output using byte offsets
- `job_stop`
- `task_run` — 1–50 bounded shell steps in one MCP call

`task_run` is useful for clients that ask for approval per tool invocation: a coherent workflow can be expressed as one tool call. It does **not** bypass a client's own approval policy.

### Persistent terminals

- `terminal_start`
- `terminal_list`
- `terminal_write`
- `terminal_read`
- `terminal_close`

These are real PTY sessions. A development server, REPL, installer or interactive CLI can stay alive between MCP requests.

### Processes and observability

- `process_list`
- `process_info`
- `process_kill`
- `system_snapshot`
- `diagnostics`
- `machine_info`

Host telemetry includes CPU, memory, home-disk usage, network counters, uptime and process count.

### Fleet

- `node_list`
- `node_health`
- `node_command`
- `node_copy`

Remote nodes currently use operator-configured SSH targets/aliases. Direct per-node MCP endpoints can still coexist with a gateway.

### Optional browser adapter

If `agent-browser` is installed separately, trusted policy can expose structured browser tools:

- `browser_status`
- `browser_open`
- `browser_snapshot`
- `browser_click`
- `browser_fill`
- `browser_get`
- `browser_close`

Loopback does not auto-install or auto-enable browser control.

## Policy profiles

Loopback ships with three operator-selectable profiles:

| Profile | Purpose |
| --- | --- |
| `read-only` | inspection-oriented use |
| `standard` | safe portable default; file/Git/diagnostics plus read-only process visibility |
| `trusted` | shell, jobs, PTYs, process mutation, fleet commands and browser control |

The standard policy also denies sensitive paths such as `~/.ssh`, `~/.gnupg`, cloud credential directories and `.git-credentials`.

High-risk command patterns can be denied outright or require a one-time **Loopback operator approval** in the dashboard. This server-side approval is independent from any approval dialog shown by ChatGPT, Perplexity, or another client.

CLI examples:

```bash
loopback policy show
loopback policy profile trusted
loopback policy tool process_kill off
```

## Dashboard

The dashboard is the human control plane:

```text
https://<your-loopback-host>/admin
```

It includes:

- live CPU, RAM, disk and process state
- running background jobs and PTY sessions
- incremental job output
- process filtering and operator kill controls
- active policy profile and per-tool toggles
- filesystem allow/deny paths
- configured fleet nodes and health checks
- connected MCP client activity
- pending one-time approvals
- structured audit history
- ingress, MCP URL and auth status

The CLI remains fully supported for scripting and headless hosts.

## Authentication

Loopback supports two paths without creating separate builds.

### Static bearer/API key

For MCP clients that support it:

```text
Authorization: Bearer <machine-token>
```

`X-API-Key` and `Api-Key` are accepted for compatible clients.

### OAuth 2.0 + PKCE

For clients such as ChatGPT that expect OAuth:

- protected-resource metadata
- authorization-server metadata
- dynamic client registration
- Authorization Code flow
- mandatory PKCE S256
- exact redirect validation
- MCP `resource` binding
- short-lived access tokens
- rotating refresh tokens

The machine bearer token is used to approve the OAuth grant but is **not returned to the OAuth client**.

## Install

Linux and macOS:

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh
```

Unattended local-only install:

```bash
./install.sh --non-interactive --ingress local
```

Cloudflare example:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

Tailscale Funnel:

```bash
./install.sh --ingress tailscale
```

Explicit trusted machine:

```bash
./install.sh --ingress tailscale --profile trusted
```

The installer builds an isolated Python environment under `~/.config/loopback`, generates local credentials, installs `~/.local/bin/loopback`, and can enable a user systemd service.

## CLI

```text
loopback up|down|restart|status|doctor
loopback logs [N|-f]
loopback url
loopback dashboard
loopback token
loopback rotate-token
loopback config list|get|set

loopback policy show
loopback policy profile read-only|standard|trusted
loopback policy tool NAME on|off

loopback node list
loopback node add NAME SSH_TARGET
loopback node remove NAME
loopback node health NAME

loopback plugin package [OUTPUT.zip]
```

## Agent Plugin package

A public HTTPS Loopback installation can generate a portable Agent Plugins package:

```bash
loopback plugin package ~/Desktop/loopback-plugin.zip
```

The ZIP contains:

```text
plugin.json
mcp.json
skills/loopback/SKILL.md
```

The MCP configuration points at that installation's current HTTPS `/mcp` endpoint. This makes it suitable for a private/personal plugin without requiring a Loopback-operated cloud relay.

## Client approval behavior

Loopback can reduce approval fatigue but cannot override a client's security UI.

Use these patterns:

- prefer one structured tool over several shell calls
- use `task_run` for a bounded multi-step operation
- use `command_start` + `job_output` instead of repeatedly relaunching a long command
- grant only the Loopback tools you actually want enabled
- use client-side thread/session grants when the client offers them

See [docs/CLIENTS.md](docs/CLIENTS.md).

## Data and secrets

Runtime state lives under:

```text
~/.config/loopback/
```

Notable files include:

- `token` — machine bearer secret
- `policy.json` — local operator policy
- `nodes.json` — node names/transports/targets
- `audit.jsonl` — local audit history
- `state/jobs/` — background job output

Never commit runtime credentials.

## Project direction

Loopback's core promise is:

> **One open MCP connection to the machines you authorize, without requiring our cloud.**

The open-source agent and self-hosted gateway are intended to remain useful on their own. An optional managed service can later add zero-config rendezvous, team identity, fleet policy, long-term audit retention, alerts and support without turning the local agent into crippleware.

## Documentation

- [Usage](docs/USAGE.md)
- [Policy and approvals](docs/POLICY.md)
- [Dashboard](docs/DASHBOARD.md)
- [Clients](docs/CLIENTS.md)
- [Fleet gateway](docs/GATEWAY.md)
- [Deployment](docs/DEPLOYMENT.md)
- [ChatGPT / Agent Plugin](docs/PLUGIN.md)
- [Security](SECURITY.md)

## Supported platforms

- Linux — supported
- macOS — supported by installer/runtime paths
- Windows — WSL for the current release

## License

MIT.
