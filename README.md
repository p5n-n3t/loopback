<p align="center">
  <img src="assets/loopback-logo.svg" alt="Loopback" width="180">
</p>

# Loopback

Loopback is a portable MCP bridge that lets authenticated web agents reach a machine through an MCP endpoint.

It is designed around a localhost MCP origin plus an optional public ingress layer:

- Cloudflare Tunnel for custom domains
- Tailscale Funnel for a public `*.ts.net` endpoint
- Local-only mode for testing and private workflows

The canonical MCP URL is:

```
https://<host>/mcp
```

## Install

Linux and macOS:

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh
```

The installer creates `~/.config/loopback`, builds an isolated Python environment, generates local credentials, installs the CLI to `~/.local/bin/loopback`, and walks through ingress configuration.

For unattended local-only installation:

```bash
./install.sh --non-interactive --ingress local
```

Cloudflare example:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

## Standard MCP tools

The packaged runtime currently provides:

- `read_file` — bounded text-file reads
- `write_file` — create, replace, or append UTF-8 files
- `list_dir` — directory listing with metadata
- `patch_file` — exact text replacement with optional backup
- `search_text` — recursive text search that skips dependency/cache forests
- `tail_file` — efficient log/file tailing
- `git_info` — branch, remotes, status, and unborn-repo support
- `diagnostics` — runtime/dependency/disk diagnostics
- `machine_info` — non-secret machine identity and Loopback context

The development machine can use a separately administered trusted profile with additional local capabilities. That profile is intentionally not silently published as the default portable runtime.

## CLI

```text
loopback up
loopback down
loopback restart
loopback status
loopback doctor
loopback logs [N|-f]
loopback url
loopback token
loopback rotate-token
loopback config list|get|set
loopback help
```

`loopback up` starts the MCP server first and then starts the selected ingress. `loopback down` reverses that sequence.

## Cloudflare

The installer can:

1. Install `cloudflared` only when Cloudflare is selected.
2. Open Cloudflare CLI/browser authentication.
3. Create or locate a named tunnel.
4. Route the requested hostname to `127.0.0.1:2026`.
5. Save tunnel configuration outside Git under `~/.config/loopback`.

## Tailscale

When Tailscale is selected, the installer installs Tailscale only if needed, authenticates the node if necessary, and configures Funnel to publish the local MCP origin over HTTPS.

## Authentication

Loopback keeps generated credentials under:

```
~/.config/loopback
```

Tokens, Cloudflare credentials, OAuth state, logs, PID files, and machine-specific configuration are not intended to be committed.

The MCP server keeps its origin on `127.0.0.1`; the ingress layer is what makes it reachable externally.

## Fleet / gateway direction

For multiple machines, the recommended direction is one authenticated gateway MCP connector that routes to named nodes such as `local`, `ora1`, `ora2`, and so on, rather than copying one universal bearer credential onto every machine.

## Supported platforms

- Linux: supported
- macOS: supported by the installer path, with Homebrew used for optional ingress dependencies
- Windows: use WSL for the current release

Native Windows service management can be added separately without changing the MCP protocol or configuration model.

## Security

Read [SECURITY.md](SECURITY.md) before exposing Loopback publicly.

The short version: this is administrative infrastructure. Keep localhost binding, keep authentication enabled, rotate leaked credentials, and do not commit runtime secrets.

## License

MIT.
