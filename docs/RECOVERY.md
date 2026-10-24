# Recovery

What to do when the dashboard, the MCP endpoint, or the server itself is
unreachable or misbehaving, from least to most drastic.

## Locked out of the dashboard

The dashboard login accepts the local **machine token** as a recovery path
in addition to the account password:

```bash
loopback token           # prints the machine recovery token
```

Run this on the machine itself (or via its service account) — it is not
something the dashboard or MCP endpoint ever prints back to a remote
caller. Use it on the login page, then set a new password from Security.

If you suspect the machine token itself is compromised:

```bash
loopback auth rotate-machine-token
```

This mints a new machine token and invalidates the old one; it does not
affect any OAuth client or API token already in use.

## Server won't start / is unhealthy

```bash
loopback status
loopback doctor --deep
loopback logs -f
loopback report      # redacted diagnostic bundle — review before sharing
```

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common causes before
reaching for `repair`/`rollback` below.

```bash
loopback repair       # re-stage the current release's files, fix permissions
```

## Rolling back a bad upgrade

```bash
loopback rollback --list
loopback rollback --to <version>
```

Every upgrade takes a backup first (see [UPGRADE.md](UPGRADE.md)), and a
failed post-upgrade health check triggers this automatically — manual
rollback is for when the health check passed but something is still wrong
in practice.

## Backup and restore

```bash
loopback backup --output /path/to/backup.tar.gz
loopback restore /path/to/backup.tar.gz
```

A backup captures the config directory: `config.json`, `policy.json`,
`nodes.json`/`nodes.secrets.json`, `ssh_targets.json`, and the audit/auth
databases — enough to fully reconstitute an install on the same or a
replacement machine. `maintenance.backup_retention` (default 10) bounds how
many automatic pre-upgrade backups are kept; `loopback backup` run manually
is not subject to that cap in the same way — manage your own retention for
manual backups.

Restoring overwrites the current config directory state — take a fresh
backup first if you want to be able to undo the restore itself.

## Everything OAuth is suspect

```bash
loopback auth rotate-signing-key
```

Invalidates **every** outstanding OAuth access token immediately; clients
re-authenticate (refresh tokens are also invalidated, since they're issued
under the same key material). This does not touch the machine token or any
dashboard-minted API token — rotate those separately if they're also in
scope of the incident (`loopback auth token revoke <id>`,
`loopback auth revoke-client <id>`).

## Starting over

```bash
loopback uninstall --keep-config   # remove the service/runtime, keep state
loopback uninstall                 # remove everything
```

`--keep-config` is the one to use if you intend to reinstall and pick up
where you left off; a plain `uninstall` removes the config directory too.

## See also

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — diagnosing before you recover.
- [UPGRADE.md](UPGRADE.md), [MIGRATION-v4.md](MIGRATION-v4.md)
- [SECURITY.md](../SECURITY.md) — what each credential rotation actually
  invalidates.
