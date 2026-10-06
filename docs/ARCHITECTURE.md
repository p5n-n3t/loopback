# Architecture

Loopback is a self-hostable MCP control plane for computers and servers.

Its core principle is that **the Loopback cloud is optional**. A single-machine
installation can be reached directly through Cloudflare Tunnel, Tailscale
Funnel, a reverse proxy, or localhost. A fleet installation can additionally
route calls through one Loopback gateway.

## Direct mode

```text
MCP-capable agent
      |
      v
Cloudflare / Tailscale / HTTPS
      |
      v
127.0.0.1:2026
      |
      v
Loopback
```

The MCP origin remains bound to localhost. The ingress layer is replaceable.

## Gateway mode

```text
                   MCP-capable agent
                          |
                          v
                    Loopback gateway
                   /       |        \
                  v        v         v
               desktop    ora3      homelab
```

Each node keeps its own endpoint and authentication credential. The gateway
stores node credentials separately from the public node registry and exposes
`node_list` and `node_call`.

A gateway is additive. Direct node endpoints remain available for recovery,
debugging, and clients that prefer one connector per machine.

## Capability layers

### Files

Bounded reads, writes, exact patches, recursive search, tailing, Git status,
and machine diagnostics.

### Execution

- bounded one-shot commands
- background jobs with incremental output offsets
- persistent PTY terminals
- process inspection and signaling

### Policy

Loopback enforces policy on the machine, independently of the MCP client.
Profiles are:

- `standard` — normal access; sensitive command patterns require a Loopback approval
- `trusted` — skips soft Loopback approvals but retains hard deny rules
- `read-only` — filesystem reads/status only; shell and mutations are disabled
- `locked` — emergency stop for operational capabilities

Allowed and denied filesystem roots are checked by the server, not merely
suggested to the model.

### Client hints

Loopback publishes MCP tool annotations such as read-only, destructive, and
idempotent hints. Clients may use these to reduce unnecessary confirmation
prompts. They are advisory metadata; they are not authorization and cannot
override a client's own approval policy.

### Authentication

Direct clients can use the local bearer token.

OAuth clients use authorization code + PKCE. The permanent machine token is
entered only at the Loopback authorization gate. OAuth clients receive a
short-lived signed access token and a refresh token; they do not receive the
permanent machine bearer secret.

### Observability

MCP activity is written to a local SQLite audit store. The audit layer records
tool/method name, timing, status, target name, and a bounded user-agent string.
It does not persist bearer credentials or MCP request bodies.

## Product boundary

The open-source architecture does not require a vendor relay. An optional
hosted gateway can later provide device enrollment, discovery, policy sync,
team RBAC, alerting, and a universal public-plugin endpoint without changing
the local MCP protocol.
