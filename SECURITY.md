# Security

Loopback exposes machine capabilities to authenticated MCP clients. Treat every public Loopback endpoint as administrative infrastructure.

## Security model

Loopback separates four concerns:

1. **MCP authentication** — bearer/API-key clients or OAuth 2.0 + PKCE.
2. **operator policy** — which tools and filesystem paths are available.
3. **operator approvals** — one-time server-side approval for configured command patterns.
4. **client approvals** — confirmations imposed by ChatGPT, Perplexity, or another MCP client.

Neither Loopback policy nor a client approval replaces the other.

## Network defaults

- The MCP origin binds to `127.0.0.1`.
- Public ingress is a separate layer (Cloudflare Tunnel, Tailscale Funnel, or an operator-managed reverse proxy).
- DNS-rebinding protection remains enabled.
- CORS is restricted to the configured public host and known supported web clients, plus explicitly configured origins.

Do not bind the MCP server directly to an untrusted public interface unless you understand the consequences.

## Authentication

### Static bearer

The machine token is generated under:

```text
~/.config/loopback/token
```

with mode `0600`.

Rotate it after suspected disclosure:

```bash
loopback rotate-token
```

### OAuth

OAuth uses Authorization Code + mandatory PKCE S256. Redirects must be HTTPS or loopback HTTP URLs. Grants are bound to the canonical MCP `resource`.

The machine token authorizes the grant. OAuth clients receive a distinct short-lived access token and rotating refresh token rather than the long-lived machine bearer.

The authorization form submits the machine token via POST so it is not placed in the URL query string.

## Filesystem policy

The standard policy allows the home directory but denies common credential locations including:

- `~/.ssh`
- `~/.gnupg`
- `~/.aws`
- `~/.azure`
- `~/.oci`
- `~/.1password`
- `~/.config/1Password`
- `~/.git-credentials`

Operators should narrow the allowlist further for shared or semi-trusted deployments.

Symlinks are resolved before policy comparison.

## Command policy

Shell and PTY capabilities are disabled in standard/read-only profiles.

Configured command patterns can be:

- allowed
- denied outright
- held for one-time operator approval

The default deny list covers obvious destructive whole-system patterns. The approval list covers classes such as sudo, package removal, destructive Docker/systemd commands, and dangerous Git reset/clean/force operations.

Pattern matching is a guardrail, not a perfect shell-language sandbox. For strong isolation, run Loopback under a restricted OS account/container/VM and limit filesystem/network permissions at the operating-system layer.

## Process control

`process_kill` is trusted-only by default. Loopback refuses to signal itself or its immediate parent.

The dashboard's process controls are operator-authenticated and should be treated as administrative actions.

## Fleet

The self-hosted gateway currently uses existing SSH trust.

- `nodes.json` stores node names and SSH targets, not private keys/passwords.
- Prefer SSH aliases and per-host keys.
- Keep `BatchMode=yes` viable so Loopback never needs to scrape password prompts.
- Do not copy a universal Loopback bearer token to every host.

For high-assurance environments, use a dedicated gateway OS account and constrained SSH principals/commands.

## Browser adapter

Browser automation is optional and trusted-only. Loopback does not install `agent-browser` automatically.

A browser session may contain logged-in websites and sensitive page content. Enable browser control only on a machine/profile where that access is intended.

## Dashboard

The dashboard login uses the machine token to create a short-lived HttpOnly admin session. Secure cookies are used for public HTTPS hosts.

Do not expose the dashboard through an unauthenticated side route in your reverse proxy.

## Audit

Loopback appends structured local events to:

```text
~/.config/loopback/audit.jsonl
```

The local file is operational history, not an immutable external security log. A privileged local user can alter it. Export logs to an external sink if tamper resistance or long retention is required.

## Threat-model limits

Loopback is not a VM sandbox. In trusted profile, an authorized agent can execute commands as the Loopback OS user and can therefore do whatever that OS user is allowed to do.

Use defense in depth:

- least-privilege OS user
- filesystem allowlists
- constrained SSH keys
- no unnecessary passwordless sudo
- restricted network reachability
- separate test/production nodes
- frequent credential rotation
- external backups

## Reporting

Report vulnerabilities privately to the maintainers. Never open a public issue containing tokens, private keys, tunnel credentials, OAuth grants, or exploit details.
