# Upgrading

```bash
loopback update-check          # is a newer release available
loopback upgrade --check       # same, machine-readable
loopback upgrade                # back up, stage, swap, health-check, restart
```

## What `loopback upgrade` does

1. Takes a backup (equivalent to `loopback backup`) before touching
   anything.
2. Downloads and stages the new release alongside the current one rather
   than overwriting it in place.
3. Swaps the staged release in and restarts the service.
4. Runs a health check (`loopback doctor`-equivalent) against the newly
   started server.
5. **Automatically rolls back to the previous release** if the health
   check fails, rather than leaving a half-upgraded, unhealthy install
   running.

Flags: `--source <dir>` (upgrade from a local checkout instead of a
release), `--yes`/`-y`, `--no-restart` (stage only, restart yourself later),
`--allow-root`.

## Upgrading from v3.x to v4.0.0

v4 is a significant version with real schema and behavior changes —
`config.env` → `config.json`, profile name changes, machine-token
repositioning. `loopback upgrade` applies the needed migrations
automatically (the same `migrate_legacy_env`/profile-alias logic the
server runs on first v4 startup either way), but read
[MIGRATION-v4.md](MIGRATION-v4.md) first if you have scripts or
automations that assume v3's CLI surface, config file, or profile names —
several of those names and commands genuinely changed, and the migration
is forward-only.

## If something goes wrong

```bash
loopback rollback --list
loopback rollback --to <version>
```

Every upgrade's pre-upgrade backup is retained (`maintenance.backup_retention`
in `config.json`, default 10) specifically so this stays available well
after the upgrade completed successfully. See
[RECOVERY.md](RECOVERY.md) for the full backup/restore/rollback model,
and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for diagnosing a failed
upgrade before reaching for rollback.

## Verifying after an upgrade

`loopback up` starts the gateway **and its configured Cloudflare connector**.
Running it again repairs a stopped connector even if the gateway is already up.
It exits with an error if the connector cannot start; local server health alone
does not count as success. `loopback restart` performs the same ingress check.

If an older installation ran Cloudflare from a separate YAML file, import that
existing configuration once (use your actual saved path and hostname):

```bash
loopback up --cloudflare-config "$HOME/.config/old-bridge/cloudflared.yml" --host mcp.example.com
```

This saves the path without rewriting the YAML, tunnel, DNS, credentials or
Tailscale routes. Subsequent starts use only `loopback up`. On Linux, an existing
connector using that exact config is reused instead of starting a duplicate.
Stop Loopback before importing a different active tunnel. Inspect startup errors
with `loopback logs --file cloudflared`.

```bash
loopback doctor --deep
loopback version --json
loopback policy show
```

Confirm the profile name shown is what you expect (`standard`/`trusted`
from a v3 config now read back as `restricted`/`admin` — these are aliases
for the same profile, not a demotion) and that ingress/auth settings
migrated as expected.

## See also

- [MIGRATION-v4.md](MIGRATION-v4.md) — the detailed v3→v4 change list.
- [RECOVERY.md](RECOVERY.md) — backup/restore/rollback in depth.
- [RELEASING.md](RELEASING.md) — how a release is built and signed, if
  you're auditing what you're upgrading to.
