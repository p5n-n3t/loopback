# Security

Loopback exposes machine capabilities to authenticated MCP clients. Treat every
public Loopback endpoint as administrative infrastructure.

## Network boundary

- The MCP origin binds to `127.0.0.1`.
- Public access should come through Cloudflare Tunnel, Tailscale Funnel, or a
  deliberately configured HTTPS reverse proxy.
- Do not bind the MCP origin directly to all interfaces unless you understand the
  network exposure.
- DNS-rebinding protection remains enabled.

## Authentication

Loopback supports two paths:

1. **Static machine bearer token** for MCP clients that support bearer/API-key
   configuration.
2. **OAuth authorization code + PKCE** for clients that expect OAuth.

OAuth clients do **not** receive the permanent machine token. They receive a
short-lived signed access token and a refresh token scoped to the Loopback MCP
resource.

Dynamic client registration only accepts HTTPS redirect URIs or loopback HTTP
redirect URIs. PKCE S256 is mandatory.

Rotate the machine token after suspected disclosure:

```bash
loopback rotate-token
```

Token rotation also invalidates OAuth tokens derived from the previous machine
secret.

## Filesystem policy

Loopback evaluates filesystem paths on the server.

Default denied roots include common credential locations such as SSH, GnuPG,
AWS, 1Password, and the GitHub CLI hosts file. Customize `allowed_roots` and
`denied_roots` in the dashboard or policy file.

A model instruction is not a security boundary; the server-side path check is.

## Command policy

The standard profile can require a separate Loopback approval for sensitive
command patterns. Approvals are bounded by expiry and use count.

The trusted profile skips these *soft* Loopback approval patterns but still
enforces hard deny patterns for obviously catastrophic commands.

The read-only and locked profiles disable execution/mutation capabilities.

Do not confuse Loopback policy approvals with a client's confirmation UI.
ChatGPT, Perplexity, or another MCP client may still ask the user to approve a
tool call. Tool annotations are hints, not a bypass.

## PTYs, jobs, and process control

Persistent terminals and process signaling are powerful administrative
capabilities.

- PTY input goes through the same command policy when it contains textual input.
- Process control refuses to target PID 1 or the Loopback process/parent directly.
- Background jobs are started in their own process groups so they can be stopped
  as a unit.
- Job output is stored under the Loopback config directory.

A sophisticated shell command can still have side effects that a regex policy
cannot fully understand. Use filesystem boundaries, Unix permissions,
containers/VMs, and least privilege as additional layers.

## Dashboard

The dashboard login accepts the machine token and issues a 12-hour HttpOnly,
SameSite=Strict session cookie. The token is not stored in browser local storage.

Keep the dashboard behind the same authenticated HTTPS origin as the MCP server.

## Audit data

Loopback stores local audit events in `~/.config/loopback/loopback.db`.

The audit middleware records method/tool name, timing, status, target name, and a
bounded user-agent string. It intentionally does not store Authorization headers,
bearer tokens, or MCP request bodies.

Audit logs can still reveal operational metadata such as tool names and timing;
protect the config directory accordingly.

## Fleet credentials

Each remote node keeps its own credential. Gateway node secrets are stored
separately from `nodes.json` in `nodes.secrets.json`, both under the Loopback
config directory.

Do not reuse one bearer credential across a fleet.

## MCP annotations

Read-only/destructive/idempotent annotations help clients understand tool intent.
They are untrusted hints in the MCP specification and are not authorization.
Loopback's own policy must remain the enforcement boundary.

## Reporting

Do not open public issues containing credentials, tokens, private keys, recovery
codes, or exploit details. Report security issues privately to the maintainers.
