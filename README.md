<p align="center">
  <img src="assets/loopback-logo.svg" alt="Loopback" width="180">
</p>

[![M8ven Score](https://m8ven.ai/badge/mcp/p5n-n3t-loopback-1cyblg?v=68aeba56efe25452966def7110fda2d8)](https://m8ven.ai/mcp/p5n-n3t-loopback-1cyblg?s=readme)

# Loopback

**A self-hostable MCP control plane for computers and servers.**

Loopback lets MCP-capable web agents operate authorized machines without requiring
a vendor relay. Keep the MCP origin on localhost and choose the ingress that fits
your environment: Cloudflare Tunnel, Tailscale Funnel, your own reverse proxy, or
local-only operation.

Loopback has been used with **ChatGPT** and **Perplexity**. It is protocol-first,
not tied to either product: compatibility depends on the client supporting
Streamable HTTP MCP and a compatible authentication method.

```text
Web agent
   |
   v
Cloudflare / Tailscale / HTTPS
   |
   v
Loopback on 127.0.0.1
   |
   +-- files + Git
   +-- commands + background jobs
   +-- persistent PTY terminals
   +-- processes + diagnostics
   +-- policy + audit
   +-- optional fleet gateway
```

## Why Loopback?

- **No mandatory Loopback cloud** — direct self-hosting is the default.
- **Web-agent friendly** — static bearer auth for clients that support it and
  OAuth + PKCE for clients that expect OAuth.
- **Real terminal workflows** — one-shot commands, background jobs with
  incremental output, and persistent PTYs.
- **Machine-side policy** — filesystem boundaries, per-tool allow/deny rules,
  policy profiles, hard command denies, and bounded approval grants.
- **Observability** — authenticated dashboard plus local SQLite audit history.
- **Fleet capable** — one connector can route MCP calls to registered Loopback
  nodes, while each node retains its own identity and credential.
- **Document operations** — structured DOCX, XLSX, and PDF reading/editing helpers.
- **Optional browser automation** — narrow open/snapshot/click/fill tools through
  an installed `agent-browser`, disabled by policy by default.
- **Optional native desktop control** — Linux/X11 window discovery, screenshots,
  window activation, click/type/key tools, separately disabled by default.
- **MCP tool annotations** — safe/read-only tools advertise their intent so
  clients can make better confirmation decisions.

## Install

Linux and macOS:

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh
```

For unattended local-only installation:

```bash
./install.sh --non-interactive --ingress local
```

Cloudflare example:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

The installer creates `~/.config/loopback`, builds an isolated Python
environment, generates local credentials, installs the CLI at
`~/.local/bin/loopback`, and configures the chosen ingress.

The canonical MCP URL is:

```text
https://<host>/mcp
```

The admin dashboard is:

```text
https://<host>/admin
```

## MCP tools

### Files and repositories

- `read_file`
- `write_file`
- `list_dir`
- `patch_file`
- `search_text`
- `tail_file`
- `git_info`

### Execution

- `execute` — bounded one-shot shell command
- `job_start`, `job_list`, `job_output`, `job_stop`
- `terminal_start`, `terminal_write`, `terminal_read`, `terminal_resize`,
  `terminal_close`
- `process_list`, `process_kill`

### Documents and optional browser

- `docx_text`, `docx_replace_text`
- `xlsx_read_range`, `xlsx_write_range`
- `pdf_text`, `pdf_merge`, `pdf_extract_pages`
- `browser_status`, `browser_open`, `browser_snapshot`, `browser_click`,
  `browser_fill`, `browser_get_url`, `browser_close`

Browser automation is opt-in and requires the separate `agent-browser`
executable. It is not silently installed by Loopback.

Native desktop automation is also opt-in. On Linux/X11 it can use `xdotool`,
`wmctrl`, and an available screenshot utility. `desktop_screenshot` returns
actual MCP image content when the client supports it.

- `desktop_status`, `desktop_windows`, `desktop_screenshot`
- `desktop_activate_window`, `desktop_click`, `desktop_type`, `desktop_key`

### System, policy, and fleet

- `diagnostics`
- `machine_info`
- `policy_status`
- `node_list`
- `node_call`
- `node_read_file`, `node_execute`, `node_diagnostics`

## Policy profiles

```bash
loopback policy profile standard
loopback policy profile trusted
loopback policy profile read-only
loopback policy profile locked
```

- **standard** — normal operation; sensitive command patterns require a Loopback approval.
- **trusted** — removes soft Loopback approval prompts while retaining hard deny rules.
- **read-only** — disables shell/process mutations and filesystem writes.
- **locked** — emergency operational lock.

These are Loopback-side controls. A web agent may still enforce its own confirmation
UI. Loopback does not bypass client-side approvals.

## Dashboard

```bash
loopback dashboard
```

The dashboard shows system health, jobs, terminals, pending approvals, policy
profile, fleet nodes, and recent MCP activity. It can approve bounded sensitive
actions, stop jobs, close terminals, and manage nodes.

The CLI remains the bootstrap/recovery interface; the dashboard is additive.

## Fleet

Register another Loopback node:

```bash
loopback node add ora3 https://ora3.example.com/mcp
```

Then one gateway connector can discover nodes with `node_list` and route a
specific MCP tool call with `node_call`.

Node bearer credentials are stored separately from the public node registry.

## Authentication

Direct clients can continue using:

```text
Authorization: Bearer <machine-token>
```

OAuth clients use authorization code + PKCE. The permanent machine token is
entered at Loopback's authorization gate, but the OAuth client receives a
short-lived signed access token and a refresh token instead of the permanent
machine secret.

## ChatGPT plugin package

Build a portable Agent Plugin archive for your own HTTPS endpoint:

```bash
python3 scripts/build_plugin.py \
  --mcp-url https://loopback.example.com/mcp \
  --output dist/loopback-plugin.zip
```

See [docs/PLUGIN.md](docs/PLUGIN.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Dashboard](docs/DASHBOARD.md)
- [Web-agent compatibility](docs/WEB-AGENTS.md)
- [Fleet gateway](docs/GATEWAY.md)
- [Usage](docs/USAGE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Plugin packaging](docs/PLUGIN.md)
- [Open-source / hosted product boundary](docs/MONETIZATION.md)
- [Security](SECURITY.md)

## Supported platforms

- Linux: supported
- macOS: supported; PTY/process behavior uses Unix facilities
- Windows: use WSL for the current release

## License

MIT.
