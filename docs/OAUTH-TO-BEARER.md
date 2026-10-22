# OAuth to bearer: what is and isn't supported

This document answers a question that comes up in two very different
flavors, and gives opposite answers to each.

## Supported: minting a scoped bearer token for a bearer-only MCP client

Some MCP clients only support a static `Authorization: Bearer <token>`
header and don't implement an OAuth client at all. For those, Loopback's
dashboard (or `loopback auth token create <name> --scopes ... --ttl ...`)
mints an **API token** (`lbk_<id>_<secret>`) that is:

- independently scoped (never `loopback:admin`),
- independently revocable (`loopback auth token revoke <id>`),
- never the account password, the machine token, or an upstream OAuth
  token — it is its own credential, created specifically for this purpose.

This is the same idea as a GitHub personal access token: a narrowly scoped,
long-lived bearer credential you create on purpose for a client that can't
do a browser-based authorization flow. It is a normal, supported, documented
part of Loopback. See [AUTHENTICATION.md](AUTHENTICATION.md).

## Not supported, and not something Loopback will add

A different request sometimes shows up: turning the OAuth sign-in a
consumer AI product already holds — your ChatGPT, Claude.ai, or Grok web
session — into a portable API key that spends *that subscription's quota*
from outside the product, e.g. so a script or a different agent can call
"your ChatGPT" directly using the session your browser is logged into.

Loopback does not implement this, and should not:

- **The MCP specification prohibits token passthrough.** An MCP server must
  not accept a token that was issued for a different audience and treat it
  as its own credential, and must not forward a caller's token to a third
  party as if it were the server's own. Minting a bearer credential out of
  a provider's consumer-product session would be exactly that pattern,
  just performed by Loopback instead of by the MCP client.
- **It violates the provider's terms of service.** Consumer subscription
  sign-in is scoped to that product's own first-party surface (web app,
  official mobile app, official CLI). Extracting it for use as a general
  API key is exactly the kind of automated/unauthorized-access clause most
  consumer AI terms of service prohibit.
- **The tokens are audience-bound and rotate under the provider's control,
  not yours.** A consumer session token is issued for that product's own
  frontend, typically short-lived, and subject to the provider silently
  rotating, revoking, or binding it more tightly at any time. Software built
  on top of it breaks without warning and with no stable contract to debug
  against.
- **It risks the account**, not just the integration. Providers actively
  detect and act on this pattern; the plausible outcome of automating a
  consumer session outside the official client is the account being flagged
  or banned, which is a worse outcome than the integration simply not
  existing.

### What to use instead

- **A provider's official API key**, used the way the provider intends —
  through a gateway if you want centralized routing, budgets, or multi-key
  failover across providers. That's a separate concern from Loopback; the
  maintainer's ROTOXY project addresses exactly that gateway layer and
  deliberately does not overlap with it here.
- **An official CLI agent** (Codex CLI, Claude Code, Gemini CLI, etc.) as a
  delegated local worker. These are first-party tools that already handle
  their own provider authentication correctly; Loopback can give such an
  agent filesystem/shell/fleet capability on a machine without needing to
  touch its model-provider credential at all.
- **Loopback staying what it is** — an MCP tool server (and, orthogonally,
  an OAuth 2.1 authorization server for its *own* resource) — rather than
  growing into a credential-laundering layer for someone else's product.

If you're building an MCP client or integration against Loopback itself,
see [docs/MCP-DEVELOPMENT](../skills/loopback-mcp-development/SKILL.md) and
[AUTHENTICATION.md](AUTHENTICATION.md) for the parts of this that are fully
supported.
