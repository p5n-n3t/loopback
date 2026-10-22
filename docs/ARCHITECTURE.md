# Architecture

Loopback is one Python process combining an MCP resource server, an OAuth
2.1 authorization server, an operations dashboard, and the policy/sandbox
layer that enforces what either of those is allowed to do.

## Process layout

```
                         ┌─────────────────────────────────────────┐
                         │               Loopback                  │
  MCP client ──HTTPS───▶ │  Mount("/") → TokenGate → CORS → McpGate │
  (OAuth/API token)      │       → MCP streamable-HTTP app (/mcp)   │
                         │                                          │
  Browser ──HTTPS──────▶ │  /admin (session cookie + CSRF)          │
  (dashboard session)    │       dashboard.py SPA + /api/admin/*    │
                         │                                          │
                         │  /oauth/*  (DCR, authorize, token,       │
                         │             revoke)        auth_http.py  │
                         │  /.well-known/*  (metadata)              │
                         │  /healthz                                │
                         └───────────────┬──────────────────────────┘
                                         │
                      policy.py  sandbox.py  audit.py  context.py
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
            local filesystem     local shell/PTY/jobs    fleet.py / ssh_targets.py
             (this machine)       (OS sandbox-wrapped)    → other nodes / SSH hosts
```

Ingress (Tailscale Serve/Funnel, Cloudflare Tunnel, a reverse proxy, or
nothing beyond `127.0.0.1`) sits in front of this one process; see
[TAILSCALE.md](TAILSCALE.md), [CLOUDFLARE.md](CLOUDFLARE.md),
[DEPLOYMENT.md](DEPLOYMENT.md).

## Modules (flat, top-level under `runtime/`)

- **`server.py`** — the MCP tool definitions (`@mcp.tool`), the Starlette
  app assembly (`TokenGate` → CORS → `McpGate` → MCP app), `/healthz`, and
  the top-level route wiring that mounts `auth_http.py` and
  `dashboard.py`'s routes alongside it.
- **`auth.py` / `auth_http.py`** — the OAuth 2.1 authorization server:
  client registration (DCR + CIMD), authorize/token/revoke endpoints,
  metadata documents, refresh-token rotation and family revocation.
- **`admin_api.py`** — the `/api/admin/*` HTTP contract behind the
  dashboard (see [ADMIN-API.md](ADMIN-API.md)).
- **`policy.py`** — profiles, allowed/denied roots, approval/deny command
  patterns, per-target policy. Pure decision logic; doesn't know about
  HTTP, OAuth, or the sandbox mechanism — just "is this path/command
  allowed, and does it need an approval."
- **`sandbox.py`** — OS-level enforcement for shell execution (Landlock,
  bwrap, Seatbelt, or none), independent of and underneath `policy.py`'s
  decision. See [PERMISSIONS.md](PERMISSIONS.md).
- **`identity.py`** — account/shell/config-dir/service-manager detection,
  shared by the CLI, installers, and the running server so they never
  disagree about "who is this" or "where does state live."
- **`context.py`** — the per-request/per-tool-call context object that
  threads identity, policy, and target resolution through a tool call.
- **`bootstrap.py`** — first-run setup: generating the machine token,
  signing key, default `config.json`/`policy.json`, local self-registration
  as a node.
- **`audit.py`** — the local SQLite audit trail (`loopback.db`): tool name,
  status, duration, target, bounded detail string — never credentials or
  request/response bodies.
- **`jobs.py`** — background job and PTY-terminal process management.
- **`fleet.py`** — the Loopback-node registry (`nodes.json` +
  `nodes.secrets.json`).
- **`ssh_targets.py`** — the SSH-target registry, probing, and per-target
  command execution (`ssh_targets.json`, `probe_state.json`).
- **`config.py`** — `config.json` schema, defaults, legacy-`config.env`
  migration.
- **`cli.py`** — the `loopback` command surface: install/upgrade/rollback,
  service control, `doctor`/`report`/`repair`, auth/policy/node/ssh
  subcommands.
- **`dashboard.py`** — the SPA shell, static asset serving, session
  cookie/CSRF plumbing for `/admin`.

Modules import each other directly by name (`from config import
ConfigStore`), not as a package — there is no `runtime/__init__.py`
re-export surface to keep in sync.

## Request paths

- **MCP tool call**: `POST /mcp` → `TokenGate` (resolve bearer/API/machine
  token → scopes) → `McpGate` (scope check) → tool function → `policy.py`
  (`check_path`/`check_command`/`check_tool`) → optionally `sandbox.py`
  (shell only) → `audit.py` records the call → response.
- **Dashboard action**: browser → session cookie + CSRF header validated →
  `admin_api.py` handler → the same `policy.py`/config/fleet modules used
  by the MCP path (no separate "admin-only" copy of policy logic).
- **Fleet call**: `node_call`/`node_execute`/`ssh_execute` → local
  `policy.py` check for *that target* (never the local machine's roots) →
  outbound HTTP (to another Loopback node) or SSH (to an SSH target) → the
  remote side applies its own policy independently.

## Where the plugin/skills packaging fits

`scripts/build_plugin.py` is a release-time step, not part of the running
server: it zips `skills/` plus a generated `plugin.json`/`mcp.json` for
harnesses that consume an Agent Plugin bundle. See
[SKILLS.md](SKILLS.md).

## See also

- [PERMISSIONS.md](PERMISSIONS.md) — the policy/sandbox decision logic in
  depth.
- [AUTHENTICATION.md](AUTHENTICATION.md) — the OAuth/token layer in depth.
- [FLEET.md](FLEET.md) — the node/SSH-target layer in depth.
- [ADMIN-API.md](ADMIN-API.md) — the dashboard's HTTP contract.
- [DEPLOYMENT.md](DEPLOYMENT.md) — how this process is actually run
  (service manager, ingress) on each platform.
