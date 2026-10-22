# Configuration

Loopback's runtime configuration lives in one validated `config.json` under
its config directory. There is no CLI subcommand for editing it field by
field — edit the file directly, use the dashboard's Settings page, or script
`PATCH`-style calls against the admin API (see
[ADMIN-API.md](ADMIN-API.md)).

## Config directory resolution

In order:

1. `LOOPBACK_CONFIG_DIR` environment variable, if set.
2. Windows: `%LOCALAPPDATA%\Loopback`.
3. `XDG_CONFIG_HOME/loopback`, if `XDG_CONFIG_HOME` is set.
4. `~/.config/loopback`.

This resolution is shared by the CLI, the installers, and the runtime
(`runtime/identity.py`), so `loopback status`, the `bin/loopback` wrapper,
and the running server always agree on where state lives. Under that
directory: `config.json`, `policy.json`, `nodes.json`/`nodes.secrets.json`,
`ssh_targets.json`/`probe_state.json`, `loopback.db` (MCP audit),
`auth.db` (OAuth/session state), `venv/`, and rotated logs — every file
Loopback writes there is created mode `0600`.

## config.json

See [examples/config.example.json](../examples/config.example.json) for a
complete, commented example matching the real schema. Top-level fields:

| Field | Meaning |
|---|---|
| `schema_version` | Internal migration marker; don't hand-edit. |
| `port`, `bind` | MCP/dashboard listen address. Default `127.0.0.1:2026`. |
| `public_host` | Hostname used in metadata documents and the dashboard URL once ingress is configured. |
| `display_name`, `node_kind` | How this machine labels itself in topology views. |
| `shell`, `initial_cwd` | Overrides for the configured-shell precedence (see [ARCHITECTURE.md](ARCHITECTURE.md)). |
| `command_timeout`, `max_output_bytes`, `startup_timeout` | Shell execution bounds. |
| `ingress.local/.tailscale/.cloudflare` | See [TAILSCALE.md](TAILSCALE.md), [CLOUDFLARE.md](CLOUDFLARE.md). |
| `auth.*` | Token lifetimes, `allowed_origins`, DCR/CIMD toggles, `legacy_machine_token_mcp`. See [AUTHENTICATION.md](AUTHENTICATION.md). |
| `sandbox.*` | Backend selection and `acknowledge_unenforced`. See [PERMISSIONS.md](PERMISSIONS.md). |
| `maintenance.*` | Update channel, backup retention, log rotation, audit/history retention, disk-warning threshold. |

Every field is optional — anything omitted falls back to the built-in
default in `runtime/config.py`'s `default_config()`. Applying changes
through the dashboard or admin API restarts only what's needed; hand-editing
`config.json` directly needs `loopback restart`.

## policy.json

Profile, allowed/denied roots, tool allow/deny lists, approval/deny command
patterns, and per-target overrides. See
[examples/policy.example.json](../examples/policy.example.json) and
[PERMISSIONS.md](PERMISSIONS.md) for the full schema — this file is large
enough to warrant its own document rather than being repeated here.

## Environment variables

| Variable | Effect |
|---|---|
| `LOOPBACK_CONFIG_DIR` | Override the config directory (see resolution order above). |
| `LOOPBACK_VERSION` | Pin the version the installer fetches/stages. |
| `LOOPBACK_TOKEN` / `LOOPBACK_API_TOKEN` | Stripped from every sandboxed child process's environment before exec — never rely on these being visible to a shell command Loopback runs. |

## Legacy config.env migration

v3 installs configured Loopback through a flat `config.env` file. On first
v4 startup, `migrate_legacy_env` reads any existing `config.env`, maps its
keys onto the new `config.json` schema, writes `config.json`, and renames
the old file aside rather than deleting it. See
[MIGRATION-v4.md](MIGRATION-v4.md) for the full before/after key mapping.

## Validating

```bash
loopback doctor
```

checks `config.json` and `policy.json` both parse, that `ingress` settings
are internally consistent (e.g. Funnel only set alongside Tailscale
enabled), and that referenced files (`token_file`, `identity_file` entries,
`config_file`) actually exist and are readable.

## See also

- [PERMISSIONS.md](PERMISSIONS.md), [AUTHENTICATION.md](AUTHENTICATION.md)
- [INSTALL.md](INSTALL.md), [UPGRADE.md](UPGRADE.md)
- [ADMIN-API.md](ADMIN-API.md)
