# Authentication

Loopback is both an MCP resource server and its own OAuth 2.1 authorization
server. This document covers how a client authenticates to the `/mcp`
endpoint and how the dashboard authenticates separately. For what an
authenticated caller is then allowed to do, see
[PERMISSIONS.md](PERMISSIONS.md).

## Credential types

| Credential | Who it's for | Minted by | Scope |
|---|---|---|---|
| OAuth access/refresh token | MCP clients that support OAuth 2.1 | `/oauth/token` after an authorization-code + PKCE flow | Whatever the client requested and the grant allows, never `loopback:admin` |
| API token (`lbk_<id>_<secret>`) | MCP clients that only support a static bearer header | Dashboard, or `loopback auth token create` | Chosen at creation, never `loopback:admin` |
| Connector setup key (`lbs_…`) | Human approval of additional OAuth clients | Installer or Authentication dashboard | Consent only, no direct dashboard or MCP access |
| Dashboard username/password | Human administrator | Installer or Authentication dashboard | Dashboard login and explicit OAuth consent |
| Machine token | Local bootstrap and recovery | Generated at install; `loopback auth rotate-machine-token` | Full local access; a recovery/admin-adjacent credential, not a per-client one |
| Dashboard session cookie | A human administrator | `/admin/login` | Admin API only, not the MCP endpoint |

Prefer an API token or OAuth for anything you hand to a third-party AI
product or agent. The machine token exists to get you into the dashboard on
a fresh install and to recover access if everything else is locked out —
treat it like a recovery code, not a day-to-day credential. Whether the
machine token is additionally accepted directly on `/mcp` is controlled by
`auth.legacy_machine_token_mcp` (off by default on a fresh v4 install).

## Dashboard account and connector setup

Fresh installation generates a random dashboard username and password and a separate
connector setup key. The installer displays these once. The database stores hashes,
not recoverable copies. Save them in your password manager. The machine token remains
available locally through `loopback token` for recovery.

Existing dashboard passwords keep working without a username. Upgrades do not reset
passwords, API tokens, or OAuth grants. In Authentication, use **Dashboard account**
to set or change the username and password. This action requires the current password
or machine token and signs out every dashboard session. The older password-change
and CLI enrollment paths retain an existing username.

The connector setup key (`lbs_…`) authorizes OAuth consent. It cannot sign in to the
dashboard, access its API, or call MCP tools as a bearer credential. To enroll an
OAuth client, add your Loopback MCP URL in the client, choose OAuth, check the client
name, redirect URI and scopes on Loopback's consent page, enter the setup key, and
approve. Repeat for additional clients with the same key. PKCE, resource checks,
request expiry, explicit consent, and per-client tokens still apply. You can also
approve with your dashboard username/password or the local recovery token.

In Authentication, **Create/Rotate setup key** requires the current password or
machine token and shows the new key once. The old key immediately stops authorizing
new approvals. Rotation does **not** revoke existing OAuth grants, API tokens, or
dashboard sessions. Revoke an OAuth grant to stop its access and refresh tokens, or
revoke a client to remove all its grants. For a compromised setup key, rotate it and
review/revoke unfamiliar clients and grants separately. For bearer-only clients,
create a separate scoped API token as before.

These flows have local contract tests. They do not establish that an actual ChatGPT
or Claude account completes its provider-specific connector flow.

## Scopes

- `loopback:read` — read files, list processes, read tool/job/audit state.
- `loopback:write` — create/modify/delete files, write documents.
- `loopback:exec` — run shell commands, manage jobs and PTY terminals.
- `loopback:fleet` — call tools against registered nodes and SSH targets.
- `loopback:admin` — dashboard/admin-API actions only. **Never granted to an
  OAuth client or API token**, regardless of what a registration request
  asks for.

The legacy bare `loopback` scope string (some older clients still request
exactly this) expands to `read+write+exec+fleet` — never `admin`.

A tool call is rejected if the token's scopes don't cover it, independent of
and prior to the policy-profile and sandbox checks in
[PERMISSIONS.md](PERMISSIONS.md). Both have to allow a call for it to
succeed.

## Discovery

```
GET /.well-known/oauth-protected-resource
GET /.well-known/oauth-protected-resource/mcp
GET /.well-known/oauth-authorization-server
GET /.well-known/openid-configuration
```

The protected-resource document (RFC 9728) advertises the resource
identifier(s) for `/mcp` and which authorization server(s) issue tokens for
it. The authorization-server metadata (RFC 8414-shaped) advertises
`/oauth/register`, `/oauth/authorize`, `/oauth/token`, and `/oauth/revoke`,
along with supported grant types, PKCE methods, and scopes.

## Registering a client

Two ways to get a `client_id`:

1. **Dynamic Client Registration** — `POST /oauth/register` with a standard
   DCR request body (`redirect_uris`, `client_name`, `token_endpoint_auth_method:
   "none"` for a public client, etc.). Returns a `client_id` (and, for a
   confidential client, a `client_secret`).
2. **Client ID Metadata Documents (CIMD)** — present an `https://` URL you
   control as the `client_id` itself; Loopback fetches and validates the
   metadata document at that URL instead of requiring a prior registration
   call.

Redirect URIs must be HTTPS, or a loopback HTTP address (RFC 8252 §7.3,
`http://127.0.0.1:<port>/...` or `http://[::1]:<port>/...`) for native/CLI
clients.

### Confidential clients

DCR accepts `token_endpoint_auth_method` of `none` (public), `client_secret_basic`
or `client_secret_post`. For the two confidential methods Loopback mints a
`client_secret` once, returns it in the registration response with
`client_secret_expires_at: 0`, and stores only its SHA-256 hash. CIMD clients are
always public. The token and revocation endpoints enforce the method the client
registered: a confidential client that sends no credential, the wrong credential,
or a `client_id` in the Basic header that differs from the form gets
`401 invalid_client` (with `WWW-Authenticate: Basic` for Basic clients). A public
client that sends a stray secret is still treated as public.

`grant_types` is narrowed at registration to the supported subset
(`authorization_code`, `refresh_token`); `authorization_code` is required and
`response_types` must include `code`. A token request for a grant the client did
not register is refused with `unauthorized_client`. The authorization server
metadata advertises all three auth methods for both endpoints. Rotate a secret
with `AuthStore.rotate_client_secret(client_id)`; the dashboard client list shows
each client's auth method and grants.

## Authorization code + PKCE

```
GET  /oauth/authorize?response_type=code&client_id=...&redirect_uri=...
     &scope=loopback:read+loopback:write&resource=https://loopback.example.com/mcp
     &code_challenge=...&code_challenge_method=S256
POST /oauth/authorize   (resource-owner approval)
POST /oauth/token       (grant_type=authorization_code, code, code_verifier)
```

PKCE with `S256` is mandatory — a request without it, or using `plain`, is
rejected. `resource` (RFC 8707) binds the issued token to the specific MCP
resource it's good for; a token minted for one resource is not valid against
another.

Request only the scopes you need. A client that asks for `loopback:exec`
without a clear reason is a reasonable thing for the approving human to
question on the consent screen.

## Refreshing

```
POST /oauth/token   (grant_type=refresh_token, refresh_token=...)
```

Refresh tokens rotate on every use — the response contains a new refresh
token and the old one stops working immediately. **Reusing an already-used
(and thus revoked) refresh token revokes the entire token family**, not just
that one token, and is recorded as a `refresh_token_reuse_detected` audit
event. If your client sees its refresh calls suddenly fail, re-run the
authorization flow rather than retrying the old token — something already
rotated it out from under you (a second client sharing the same stored
token, or token storage that didn't persist the rotation).

## Revocation

```
POST /oauth/revoke          # revoke a token (RFC 7009)
loopback auth clients                  # list registered OAuth clients
loopback auth revoke-client <id>       # revoke a client and all its grants
loopback auth token list / revoke <id> # manage dashboard-minted API tokens
```

## Calling the MCP endpoint

```
POST /mcp
Authorization: Bearer <access token or API token>
```

Streamable-HTTP transport, one path for every tool call. A 401 means the
token is missing/expired/invalid; a 403 means the token is valid but lacks
a required scope (fix by re-authorizing with a broader scope request) —
distinct from a policy-layer `approval_required` or hard-deny response,
which means the token was fine and the *action* was the problem.

## Dashboard authentication

Separate from everything above. `POST /admin/login` with the account
password (or the machine token as a recovery path) sets an HttpOnly,
SameSite=Strict `loopback_admin` session cookie; every mutating admin
request also needs the `X-Loopback-CSRF` header the dashboard reads from
that session. Dashboard sessions cannot call `/mcp`, and MCP tokens cannot
call the admin API. See [DASHBOARD.md](DASHBOARD.md).

## See also

- [PERMISSIONS.md](PERMISSIONS.md) — the policy/sandbox layer every
  authenticated call still has to pass.
- [OAUTH-TO-BEARER.md](OAUTH-TO-BEARER.md) — what dashboard-minted API
  tokens are (and are not) for.
- [RECOVERY.md](RECOVERY.md) — regaining access when every credential is
  lost.
- [ADMIN-API.md](ADMIN-API.md) — the full admin HTTP contract.
