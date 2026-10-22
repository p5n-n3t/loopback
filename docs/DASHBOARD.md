# Dashboard

Loopback ships a browser-based operations dashboard: a vanilla-JS single
page app served only to authenticated sessions, with a strict
Content-Security-Policy (no inline script or style). It is a separate
authentication surface from the MCP endpoint — see
[AUTHENTICATION.md](AUTHENTICATION.md).

## Opening it

```bash
loopback dashboard --open
loopback url
```

Reachable at `https://<public-host>/admin` once ingress is configured, or
`http://127.0.0.1:<port>/admin` locally. `login`, `logout`, `enroll`,
`static`, and `api` are reserved first path segments — you can't name a
fleet node or SSH target any of those without it colliding with the
dashboard's own routes.

## First run: the Welcome wizard

On a fresh install, logging in for the first time lands on a setup wizard
instead of the Overview page: set the account password, pick a policy
profile, choose ingress (local-only, Tailscale, or Cloudflare), and
optionally register the first fleet node or SSH target. You can re-run any
one step later from its own section without going through the wizard again.

## Sections

- **Overview** — hostname, uptime, load/memory/disk, Python version,
  current policy profile, sandbox status at a glance.
- **Topology** — the fleet/ingress graph: nodes, SSH targets, ingress
  paths, and connected MCP clients as one view, with real probe-derived
  edge statuses.
- **Local machine** — account, shell, config directory, virtualization,
  detected service manager.
- **Fleet** — Loopback nodes and SSH targets: add/remove, probe, per-target
  policy. See [FLEET.md](FLEET.md).
- **Filesystem** — browse allowed/denied roots, edit `allowed_roots`.
- **Shell** — run one-off commands and manage background jobs/terminals
  from the browser, subject to the same policy as any MCP client.
- **MCP tools** — the live tool catalog: which tools are reachable, their
  scope and annotations. See [TOOLS.md](TOOLS.md).
- **Authentication** — OAuth clients, dashboard-minted API tokens, machine
  token rotation, legacy-MCP machine-token toggle.
- **Cloudflare** — named/token tunnel configuration and access policy. See
  [CLOUDFLARE.md](CLOUDFLARE.md).
- **Tailscale** — Serve/Funnel mode, tailnet status. See
  [TAILSCALE.md](TAILSCALE.md).
- **SSH config** — manage `ssh_targets.json` entries without the CLI.
- **Jobs** — background job list, output, stop.
- **Logs & diagnostics** — `server`/`cloudflared`/`tailscale` log tails,
  `doctor`-equivalent checks.
- **Security** — policy profile, approvals queue, sandbox self-test,
  session revocation.
- **Backup & recovery** — trigger `backup`/`restore`, view rollback
  targets. See [RECOVERY.md](RECOVERY.md).
- **Updates** — `update-check`/`upgrade` from the browser. See
  [UPGRADE.md](UPGRADE.md).
- **Settings** — the rest of `config.json`, editable through validated
  forms rather than raw JSON.

## Authentication model

Login (`POST /admin/login`) accepts the account password, or the machine
token as a recovery path, and sets an HttpOnly, SameSite=Strict
`loopback_admin` session cookie (default 12-hour TTL). Every
state-changing request additionally carries an `X-Loopback-CSRF` header the
page reads from its own session — a cookie alone is not sufficient to
mutate anything. Revoke every dashboard session at once from Security →
Sessions, or `POST /api/admin/auth/sessions/revoke-all`.

The dashboard cannot call `/mcp`, and an OAuth/API bearer token cannot call
the admin API — they are deliberately separate credential spaces. See
[AUTHENTICATION.md](AUTHENTICATION.md).

## Static assets

Served from a fixed allowlist of extensions with path-traversal and
symlink protection (`_safe_static_path` in `runtime/dashboard.py`); there is
no general static-file passthrough.

## See also

- [AUTHENTICATION.md](AUTHENTICATION.md), [PERMISSIONS.md](PERMISSIONS.md)
- [ADMIN-API.md](ADMIN-API.md) — the HTTP contract behind every dashboard
  action, for scripting the same operations directly.
