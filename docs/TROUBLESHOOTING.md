# Troubleshooting

## First steps

```bash
loopback status            # is the service up, what profile/ingress
loopback doctor             # quick health checks
loopback doctor --deep      # also runs the sandbox self-test and a full config validate
loopback logs -f             # tail the server log
loopback logs --file cloudflared   # or tailscale
loopback report              # redacted diagnostic bundle to attach to an issue
```

`loopback report` redacts tokens/secrets before writing the bundle, but
review it yourself before sharing — it still contains hostnames, paths, and
policy configuration that may be sensitive to your environment.

## 401 vs 403 vs policy responses

These three look similar from an agent's point of view but mean different
things and need different fixes:

- **401** — the bearer token is missing, expired, or invalid. Re-run the
  OAuth flow or re-issue the API token; this is an authentication problem,
  not a permissions one.
- **403** — the token is valid but lacks a scope the tool needs (e.g. an
  `loopback:read`-only token calling `execute`). Re-authorize with the
  broader scope; granting `loopback:admin` to fix this is never the answer
  — that scope cannot be granted to a token at all. See
  [AUTHENTICATION.md](AUTHENTICATION.md).
- **`approval_required:<id>`** — authentication and scope were both fine;
  Loopback's policy wants a human to approve this specific action from the
  dashboard's Security → Approvals view before it runs. Approve it there,
  or re-issue the call with the resulting `approval_id`.
- **A plain refusal with no approval id** — a hard-deny command pattern.
  No approval path exists for these in any profile; the fix is to not run
  that command, not to retry it differently.

See [PERMISSIONS.md](PERMISSIONS.md) for the full policy model.

## Sandbox not enforced

`loopback doctor` reports the active sandbox backend and whether it's
actually enforcing (via a real self-test, not just a name lookup). If it
reports `none` on Linux, check kernel version (Landlock needs 5.13+) and
whether `bwrap` is installed. If a profile that requires enforcement
refuses to run shell commands, that's the intended fail-closed behavior —
don't silently flip `sandbox.acknowledge_unenforced` to make the message go
away without understanding what you're accepting; see
[PERMISSIONS.md](PERMISSIONS.md#os-sandbox-backends).

## SSH target shows `auth_failed`

This means SSH itself rejected the credential (bad key, key not in the
target's `authorized_keys`, wrong user) — it is a credentials problem, not
a network one, and retrying the same probe will not help. `unreachable`
means a network-level failure instead (refused, timed out, DNS). See
[FLEET.md](FLEET.md#ssh-targets) for the full status table.

## Ingress

If the dashboard and the MCP endpoint seem to disagree about what's
reachable (e.g. the dashboard loads over Tailscale Serve but `loopback url`
prints a different host, or a client reports it can't reach `/mcp` on a
hostname that loads fine in a browser), check that `config.json`'s
`ingress` block matches what `tailscale status` / `cloudflared tunnel info`
actually report — `loopback doctor` checks this agreement directly rather
than you having to compare them by hand. See [TAILSCALE.md](TAILSCALE.md),
[CLOUDFLARE.md](CLOUDFLARE.md).

## After an upgrade, something that used to work doesn't

Check [MIGRATION-v4.md](MIGRATION-v4.md) first — profile names
(`standard`→`restricted`, `trusted`→`admin`), the config file format
(`config.env`→`config.json`), and a couple of CLI command names changed
between v3 and v4. A script or automation written against the old names is
the most common cause of "this broke after upgrading," not a regression.

## Still stuck

[RECOVERY.md](RECOVERY.md) covers backup/restore/rollback and credential
recovery if the server is unhealthy rather than just misconfigured.

## See also

- [PERMISSIONS.md](PERMISSIONS.md), [AUTHENTICATION.md](AUTHENTICATION.md)
- [RECOVERY.md](RECOVERY.md), [MIGRATION-v4.md](MIGRATION-v4.md)
- [skills/loopback-troubleshooting/SKILL.md](../skills/loopback-troubleshooting/SKILL.md)
