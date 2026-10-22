---
name: loopback-admin
description: Administer a Loopback deployment through its dashboard and admin API: authentication, OAuth clients, policy profiles, ingress (Tailscale/Cloudflare), backups, and upgrades. Use when a task is about operating or configuring Loopback itself, not about operating the machines it exposes.
---

# Loopback administration

This skill is about managing Loopback as a service: who can authenticate,
which policy profile is active, how it is exposed to the internet, and how
to back it up or upgrade it. For using Loopback's tools to operate connected
machines, see `loopback-core` and `loopback-fleet`.

## Authentication model

Loopback is an MCP resource server and an OAuth 2.1 authorization server in
one process. It issues several distinct credential types, and they are not
interchangeable:

- Short-lived OAuth access/refresh tokens for MCP clients that completed the
  authorization code + PKCE flow (or Dynamic Client Registration / a Client
  ID Metadata Document).
- Dashboard-session-minted API tokens (`lbk_<id>_<secret>` format) for
  bearer-only MCP clients that cannot do OAuth — these are scoped and
  revocable, comparable to a GitHub personal access token.
- The machine token: a long-lived local bootstrap/recovery credential, never
  handed to a third-party AI product as its primary credential.

Never print, log, or return a token value anywhere except the one response
that is minting it for the user to copy. When a task involves rotation, use
the actual CLI subcommands (`loopback auth rotate-machine-token`,
`loopback auth rotate-signing-key`) — rotating the signing key invalidates
every outstanding OAuth access token immediately, so warn the user before
doing it on their behalf.

## Policy profiles

Profiles are locked, read-only, restricted (alias: standard), development,
admin (alias: trusted), and custom. Moving a machine to a looser profile is a
security-relevant action — confirm the user's intent before proposing
`loopback policy profile <name>`, and never recommend `admin`/`trusted` as a
default just to make a blocked tool call succeed.

## Ingress changes are exposure changes

Enabling Cloudflare or Tailscale ingress, and especially Tailscale Funnel
(public internet) or a Cloudflare `access_policy` of anything other than
`both`, changes who can reach the dashboard and MCP endpoint. Treat these as
you would a firewall change: confirm scope with the user, and never enable
Funnel or a public Cloudflare hostname silently as a side effect of an
unrelated request.

## See also

- `docs/AUTHENTICATION.md` — endpoints, scopes, token lifecycle.
- `docs/PERMISSIONS.md` — profile definitions and sandbox requirements.
- `docs/TAILSCALE.md`, `docs/CLOUDFLARE.md` — ingress setup and exposure model.
- `docs/DASHBOARD.md` — every dashboard section.
- `docs/RECOVERY.md`, `docs/UPGRADE.md` — backup, recovery, and upgrade flow.
