# Loopback admin API contract (v4)

The dashboard talks to the runtime only through these JSON endpoints. Every
endpoint under `/api/admin/` requires a dashboard session cookie
(`loopback_admin`). State-changing requests (`POST`, `PUT`, `DELETE`) must also
send the header `X-Loopback-CSRF: <csrf>` obtained from `GET /api/admin/session`.
Unauthenticated requests receive `401 {"detail": "admin authentication required"}`.
Validation failures receive `400 {"detail": "<message>", "errors": [..]}`.

Secrets are never returned: no machine token, no API-token plaintext after
creation, no node bearer tokens, no tunnel credentials, no SSH key material.

## Session and identity

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/session` | `{authenticated, csrf, expires_at, password_set}` |
| GET | `/api/admin/overview` | Everything the Overview page needs (see below) |
| GET | `/api/admin/identity` | `RuntimeIdentity.as_dict()` + `host_facts` + `effective_shell`, `initial_cwd` |

`GET /api/admin/overview` returns:

```json
{
  "version": "4.0.0",
  "identity": {"username":"alice","home":"/home/alice","hostname":"desk","os":"Ubuntu 24.04","platform":"linux","machine_kind":"physical","service_manager":"systemd-user"},
  "system": {"uptime_seconds":1,"load":[0,0,0],"memory_total":0,"memory_used":0,"disk_total":0,"disk_used":0,"python":"3.12.1"},
  "policy": {"profile":"restricted", "...": "policy.json without secrets"},
  "permissions": {"...": "PolicyStore.effective_permissions('local')"},
  "sandbox": {"backend":"landlock","active":true,"reason":null,"caveats":[]},
  "auth": {"password_set":true,"legacy_machine_token_mcp":false,"oauth_clients":1,"active_oauth_grants":1,"api_tokens":2,"admin_sessions":1,"resource_uris":[],"issuers":[]},
  "ingress": {"local":{"enabled":true,"url":"http://127.0.0.1:2026"},"cloudflare":{"enabled":false},"tailscale":{"enabled":false}},
  "jobs": [], "terminals": [], "approvals": [], "nodes": [], "ssh_targets": [],
  "audit": {"window_seconds":86400,"total":0,"failures":0,"top_tools":[]},
  "alerts": [{"level":"warning","code":"legacy_token","message":"...","action":"/#/auth"}],
  "update": {"current":"4.0.0","latest":null,"channel":"stable","checked_at":null,"available":false}
}
```

## Configuration

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/config` | `ConfigStore.public_view()` |
| PUT | `/api/admin/config` | Partial JSON merged + validated; returns new config. `{"detail": ..., "errors": [...]}` on 400 |
| POST | `/api/admin/config/rollback` | Restores newest `config.backups/` entry |
| GET | `/api/admin/config/validate` | `?` — POST a partial body, returns `{ "errors": [] }` without saving |

## Policy and permissions

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/policy` | policy.json (`allowed_roots` are `{path, mode}` objects) |
| PUT | `/api/admin/policy` | Partial updates (`profile`, `allowed_roots`, `denied_roots`, `allow_*`, `allowed_tools`, `denied_tools`, patterns, `approval_ttl_seconds`, `sandbox_required`) |
| GET | `/api/admin/policy/profiles` | `{profiles: {name: {title, summary, ...}}, aliases: {...}}` |
| GET | `/api/admin/permissions?target=local` | `effective_permissions(target)` + `sandbox` status |
| PUT | `/api/admin/permissions/{target}` | For remote targets: `{allowed_roots, denied_roots, allow_shell, profile}` |
| DELETE | `/api/admin/permissions/{target}` | Remove a per-target override |
| POST | `/api/admin/permissions/validate-path` | `{path}` → `{exists, is_dir, resolved, symlink_target, within_home, warnings:[]}` |
| POST | `/api/admin/sandbox/self-test` | Runs `SandboxManager.self_test`; returns its result |
| GET | `/api/admin/approvals` | pending approvals |
| POST | `/api/admin/approvals/{id}/approve` | `{uses, ttl_seconds}` |
| POST | `/api/admin/approvals/{id}/deny` | |

## Authentication and clients

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/auth` | `AuthStore.summary()` + `clients`, `api_tokens`, `grants`, `sessions`, `events(50)`, `methods` (list describing OAuth / API token / legacy) |
| POST | `/api/admin/auth/tokens` | `{name, scopes:[..], ttl_seconds|null, note}` → `{token:"lbk_...", record}` (plaintext shown once) |
| DELETE | `/api/admin/auth/tokens/{id}` | revoke |
| DELETE | `/api/admin/auth/clients/{client_id}` | revoke client + its grants |
| DELETE | `/api/admin/auth/grants/{family_id}` | revoke one OAuth grant |
| POST | `/api/admin/auth/password` | `{current, new}` (current = password or machine token) |
| POST | `/api/admin/auth/legacy-token` | `{enabled: bool}` toggles `auth.legacy_machine_token_mcp` |
| POST | `/api/admin/auth/rotate-machine-token` | Returns `{"rotated": true}`; new value is **not** returned, use `loopback token` locally |
| POST | `/api/admin/auth/rotate-signing-key` | Invalidates OAuth access tokens |
| POST | `/api/admin/auth/sessions/revoke-all` | Logs out every dashboard session (including the caller) |

## Tools

| Method | Path | Result |
|---|---|---|
| GET | `/api/admin/tools` | `[{name, title, description, annotations:{readOnlyHint,...}, scope, category, target:"local|remote|both", network:bool, approval:"none|policy|dashboard", enabled:bool}]` |

## Fleet, SSH targets and topology (module `fabric`)

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/nodes` | Loopback nodes (no tokens) + `last_probe` |
| POST | `/api/admin/nodes` | `{name,url,token?,enabled,description,display_name,kind,group}` |
| DELETE | `/api/admin/nodes/{name}` | |
| POST | `/api/admin/nodes/{name}/probe` | `{status, latency_ms, error, checked_at, remote:{hostname,os,...}}` |
| GET | `/api/admin/ssh` | SSH targets: `[{name,host,port,user,identity_file,display_name,kind,group,last_probe}]` (no key material) |
| POST | `/api/admin/ssh` | create/update target; `identity_file` must be an existing path readable by the service user |
| DELETE | `/api/admin/ssh/{name}` | |
| POST | `/api/admin/ssh/{name}/probe` | BatchMode probe: `{status: healthy|auth_failed|unreachable|degraded|error, latency_ms, remote:{hostname,os,kernel,machine_kind,user,home}, error}` |
| GET | `/api/admin/topology` | See schema below |
| POST | `/api/admin/topology/refresh` | Re-probe everything (bounded concurrency), returns topology |
| PUT | `/api/admin/topology/nodes/{id}` | `{display_name, group}` (persisted in `topology.json`) |

### Topology schema

```json
{
  "generated_at": 1730000000.0,
  "stale_after_seconds": 120,
  "nodes": [
    {"id":"gateway","type":"gateway","kind":"local-computer","label":"desk","display_name":null,"group":"local",
     "detail":{"hostname":"desk","os":"Ubuntu 24.04","user":"alice","machine_kind":"physical","port":2026}},
    {"id":"clients","type":"external-client","label":"MCP clients","detail":{"oauth_clients":1,"api_tokens":2,"recent_principals":[{"display":"ChatGPT","kind":"oauth","last_seen":0}]}},
    {"id":"ingress:cloudflare","type":"tunnel","label":"Cloudflare Tunnel","detail":{"hostname":"lb.example.com","exposure":"public","tunnel_id":"...redacted prefix"}},
    {"id":"ingress:tailscale","type":"network","label":"Tailscale","detail":{"dns_name":"desk.tailnet.ts.net","mode":"serve","exposure":"tailnet"}},
    {"id":"ssh:web-1","type":"remote-host","kind":"remote-vm","label":"web-1","display_name":"Mattermost VM","group":"cloud","detail":{"hostname":"web-1","os":"Oracle Linux 9","user":"opc","machine_kind":"virtual-machine","transport":"ssh","via":"tailscale|public|lan|unknown"}},
    {"id":"node:homelab","type":"loopback-node","kind":"unknown","label":"homelab","detail":{"url":"https://homelab.example.net/mcp"}}
  ],
  "edges": [
    {"id":"clients->ingress:cloudflare","from":"clients","to":"ingress:cloudflare","transport":"https","protocol":"mcp","exposure":"public","status":"healthy|degraded|unreachable|auth_failed|disconnected|untested|stale","latency_ms":42,"checked_at":0,"proxied":true,"detail":{}},
    {"id":"ingress:cloudflare->gateway","from":"ingress:cloudflare","to":"gateway","transport":"cloudflare-tunnel","protocol":"http","status":"healthy","proxied":true},
    {"id":"gateway->ssh:web-1","from":"gateway","to":"ssh:web-1","transport":"ssh","protocol":"ssh","exposure":"private","status":"healthy","latency_ms":88,"direct":true,"initiator":"gateway","jobs_running":1}
  ],
  "legend": {"statuses": ["healthy","degraded","unreachable","auth_failed","disconnected","untested","stale"]}
}
```

Edge `status` must come from a real probe result stored with a timestamp; `untested`
is the status for anything configured but never probed; `stale` when the last
probe is older than `stale_after_seconds`.

## Ingress

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/ingress` | `{local:{...}, cloudflare:{enabled,hostname,tunnel_id_prefix,installed,running,status,last_error,dns_ok,public_health}, tailscale:{enabled,mode,installed,backend_state,dns_name,serve_active,funnel_active,peers_count,public_health}}` |
| PUT | `/api/admin/ingress/cloudflare` | `{enabled, hostname, tunnel_id, token_file?, access_policy}` → validates, saves config, (re)starts tunnel, probes health; on failure restores previous config and returns 400 with diagnostics |
| PUT | `/api/admin/ingress/tailscale` | `{enabled, mode: serve|funnel, https_port}` → same contract; switching to `funnel` requires `{"confirm_public": true}` |
| POST | `/api/admin/ingress/{name}/restart` | |
| GET | `/api/admin/ingress/{name}/diagnostics` | Human-readable checks list `[{name, ok, detail}]` |

## Jobs, logs, diagnostics, maintenance

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/admin/jobs` | jobs + terminals with `target` (`local` or node/ssh name) |
| POST | `/api/admin/jobs/{id}/stop` | |
| POST | `/api/admin/terminals/{id}/close` | |
| GET | `/api/admin/audit?limit=100` | audit rows (principal display, tool, status, duration, target) |
| GET | `/api/admin/logs?lines=200&file=server` | tail of `server.log` / `cloudflared.log` / `tailscale.log` (redacted) |
| GET | `/api/admin/doctor` | `{checks:[{name, ok, level, detail, fix}]}` (same engine as `loopback doctor`) |
| POST | `/api/admin/diagnostics/bundle` | Creates redacted zip under `diagnostics/`, returns `{path, size, redactions}` |
| GET | `/api/admin/backups` | list of backups `{name, created_at, size}` |
| POST | `/api/admin/backups` | create backup |
| POST | `/api/admin/backups/{name}/restore` | restore (requires `{"confirm": true}`) |
| GET | `/api/admin/updates` | `{current, latest, channel, checked_at, notes_url, available}` |
| POST | `/api/admin/updates/check` | force a check |
| GET | `/api/admin/alerts` | current alerts list |
| GET | `/api/admin/metrics` | lightweight resource numbers `{rss_bytes, cpu_percent, db_bytes, auth_db_bytes, job_logs_bytes, probe_cost_ms}` |

Modules implement their routes as `routes(ctx) -> list[Route]` and the server
wraps every `/api/admin/*` route with the shared session+CSRF guard, so
individual handlers do not re-check authentication.
