# Migrating from v3 to v4

`loopback upgrade` applies everything in this document automatically on
first v4 startup. This page exists for anyone with scripts, automations, or
mental models built on v3's CLI/config surface, since several names and
files genuinely changed rather than just moved.

## Configuration: `config.env` → `config.json`

v3 configured Loopback through a flat `LOOPBACK_*=value` env file.
`migrate_legacy_env` translates it into the new structured, validated
`config.json` the first time v4 starts and finds an old `config.env` with
no `config.json` yet. The old file is renamed aside, not deleted.

| v3 `config.env` key | v4 `config.json` field |
|---|---|
| `LOOPBACK_PORT` | `port` |
| `LOOPBACK_PUBLIC_HOST` | `public_host` |
| `LOOPBACK_TIMEOUT` | `command_timeout` |
| `LOOPBACK_MAX_OUTPUT_BYTES` | `max_output_bytes` |
| `LOOPBACK_STARTUP_TIMEOUT` | `startup_timeout` |
| `LOOPBACK_INGRESS=cloudflare` + `LOOPBACK_CLOUDFLARE_TUNNEL_ID`/`_TOKEN_FILE` | `ingress.cloudflare.{enabled,hostname,tunnel_id,token_file}` |
| `LOOPBACK_INGRESS=tailscale` + `LOOPBACK_TAILSCALE_HTTPS_PORT` | `ingress.tailscale.{enabled,mode:"funnel",https_port,dns_name}` — v3 only had the public (Funnel) behavior, so a migrated install keeps that explicitly rather than silently narrowing to Serve. |

Any `config.env` key not in this table (there weren't many) has no v4
equivalent and is dropped; check `config.json`'s `_migrated_from` field
after upgrading to confirm the migration ran, and diff your old
`config.env` against the new `config.json` if you relied on something
obscure. See [CONFIGURATION.md](CONFIGURATION.md) and
[examples/config.example.json](../examples/config.example.json).

## Policy profile names

v3's `standard` and `trusted` profiles are now named `restricted` and
`admin`. The old names still work everywhere — `standard`/`trusted` are
permanent aliases (`PROFILE_ALIASES` in `runtime/policy.py`), not a
deprecated shim scheduled for removal — but `loopback policy show` and the
dashboard display the canonical name. If a script greps for `"profile":
"standard"` in `policy.json` or API output, update it to expect
`"restricted"` (and `"trusted"` → `"admin"`).

## Machine token is no longer the primary MCP credential

A v3 install only had one credential: the machine token, accepted directly
on the MCP endpoint. On migration, `auth.legacy_machine_token_mcp` is set
to `true` automatically so existing clients keep working unchanged — **you
are not locked out and nothing breaks at upgrade time.** But v4's
recommended model is per-client credentials (OAuth or a dashboard-minted
API token, see [AUTHENTICATION.md](AUTHENTICATION.md)); migrate each
existing client to its own token at your own pace, then turn
`legacy_machine_token_mcp` off (`loopback auth legacy off`) once nothing
depends on the shared machine token anymore.

## CLI surface changes

- **There is no `loopback config` subcommand in v3 or v4** — if you were
  editing `config.env` directly, keep doing the equivalent for
  `config.json` (direct edit, or the dashboard's Settings page).
- **There is no `loopback rotate-token` command.** If you scripted that
  name against v3, it maps to one of: `loopback auth rotate-machine-token`,
  `loopback auth rotate-signing-key`, or `loopback auth token revoke <id>`
  depending on which credential you meant — these are deliberately
  separate commands now because they have different blast radii. See
  [SECURITY.md](../SECURITY.md).
- `docs/GATEWAY.md` moved to [FLEET.md](FLEET.md); update any bookmarked
  links.

## Dashboard

The dashboard was substantially rewritten (see [DASHBOARD.md](DASHBOARD.md))
— section names and the first-run flow changed, but the login model
(password or machine-token recovery → session cookie) is the same shape as
v3.

## Verifying a migration

```bash
loopback doctor --deep
loopback policy show
loopback auth legacy  # shows whether legacy_machine_token_mcp is on
```

## See also

- [UPGRADE.md](UPGRADE.md) — the upgrade procedure itself and rollback.
- [RECOVERY.md](RECOVERY.md) — if an upgrade leaves the server unreachable.
- [CHANGELOG.md](../CHANGELOG.md) — the full v4.0.0 change list.
