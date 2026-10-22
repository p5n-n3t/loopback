---
name: loopback-troubleshooting
description: Diagnose a Loopback server that is unreachable, failing health checks, denying tool calls unexpectedly, or behaving differently after an upgrade. Use for connection failures, auth errors, sandbox/policy errors, and service/log investigation on the Loopback host itself.
---

# Loopback troubleshooting

Use this skill when something about Loopback itself is broken or confusing —
not when a connected machine's own application has a problem.

## First steps

1. `loopback status` — is the service running, what port/bind/ingress is it
   using.
2. `loopback doctor` (add `--deep` for a slower, more thorough pass) — runs
   the built-in self-checks: config/policy validity, sandbox self-test,
   database integrity, ingress reachability.
3. `loopback logs` — recent server log lines.
4. `loopback report` — a redacted diagnostic bundle safe to attach to an
   issue; it never includes secrets, but still treat its contents as
   sensitive (hostnames, usernames, paths) and confirm with the user before
   sharing it anywhere outside their own ticket.

## Common symptoms

- **401/403 from an MCP client**: check which credential type is in play
  (OAuth access token vs. dashboard-minted API token vs. machine token — see
  `loopback-admin`). An expired access token should be refreshed by the
  client, not worked around with the legacy static machine-token mode unless
  the user explicitly wants that compatibility mode on.
- **`approval_required` on a command the user expected to just run**: this is
  policy working as designed, not a fault. Point at Security → Approvals in
  the dashboard; do not suggest a rephrasing of the command to dodge the
  pattern match.
- **Shell tool works but reports "sandbox not enforced"**: the policy
  profile in use requires sandbox enforcement and the host has no working
  Landlock/bwrap/Seatbelt backend (common on Windows, or an unsupported
  kernel). The fix is a platform/profile decision for the user, not
  something to silence by flipping `acknowledge_unenforced` without telling
  them what it means.
- **A remote SSH target shows `auth_failed`**: this is a credentials problem
  (identity file, authorized_keys) not a network problem — probing again
  immediately will not change the result.
- **Dashboard loads but the MCP endpoint 404s, or vice versa**: check ingress
  config: local-only, Cloudflare, and Tailscale each expose the dashboard and
  `/mcp` at different combinations of host/path depending on `access_policy`
  and `mode`.
- **Behavior changed after an upgrade**: check `loopback status` for the
  running version against what you expect, and see `docs/MIGRATION-v4.md` if
  the install predates v4 — profile names changed (standard→restricted,
  trusted→admin) and the on-disk config format changed from `config.env` to
  `config.json`.

## See also

- `docs/TROUBLESHOOTING.md` — expanded symptom/cause/fix tables.
- `docs/RECOVERY.md` — what to do when the dashboard or auth itself is
  locked out.
- `docs/MIGRATION-v4.md`, `docs/UPGRADE.md` — upgrade-specific issues.
