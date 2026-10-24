# Web-agent compatibility

Loopback is an MCP server, not a product-specific remote-control protocol.
It works with any client that supports **Streamable HTTP MCP** and one of
Loopback's authentication paths.

## Tested clients

The project has been used with ChatGPT and Perplexity's web MCP
connectors. Do not read that as a guarantee every web agent works — MCP
clients differ in transport support, OAuth behavior, CORS/browser
architecture, and tool-confirmation UI.

## Authentication modes

### Dashboard-minted API token

For a client that only lets you configure a static bearer header:

```text
Authorization: Bearer lbk_<id>_<secret>
```

Create one from the dashboard or `loopback auth token create`, scoped to
only what that client needs. Prefer this over the shared machine token for
any third-party client — see [AUTHENTICATION.md](AUTHENTICATION.md).

### OAuth + PKCE

For a client (ChatGPT and similar products included) that drives a full
OAuth authorization flow: protected-resource and authorization-server
metadata, Dynamic Client Registration or Client ID Metadata Documents,
authorization code + mandatory PKCE S256, rotating refresh tokens, and
RFC 8707 resource binding so an issued token is only valid for the exact
`/mcp` resource it was granted against. See
[AUTHENTICATION.md](AUTHENTICATION.md) for the full flow.

Access tokens are short-lived and are a completely separate credential
from the machine token or any API token.

## Confirmation prompts

Two independent approval layers exist, and only one of them is Loopback's:

1. **Client-side confirmation** — ChatGPT, Perplexity, or another MCP
   client may choose to ask its user before invoking a tool at all, based
   on the MCP annotations (`read_only_hint`/`destructive_hint`/etc.)
   Loopback publishes for every tool. This is the client's own UI decision.
2. **Loopback policy approval** — the server can independently require a
   bounded dashboard approval for a sensitive command, regardless of
   whether the client already asked. See [PERMISSIONS.md](PERMISSIONS.md).

Loopback cannot make a client skip its own confirmation dialog — that
would require trusting a client-side signal as an authorization decision,
which [SECURITY.md](../SECURITY.md) explicitly does not do. If a client
exposes its own trust/auto-approve setting, configure that in the client.
Separately, moving the *server-side* profile to `admin`/`trusted` removes
Loopback's own soft-approval gate — only do that for a machine you
genuinely intend to administer through AI, and it still doesn't touch the
client's own confirmation behavior.

## CORS

`auth.allowed_origins` in `config.json` lists the browser origins permitted
to complete an OAuth flow against this server (e.g.
`https://chatgpt.com`). Server-to-server MCP clients don't rely on browser
CORS at all. See [examples/config.example.json](../examples/config.example.json).

## See also

- [AUTHENTICATION.md](AUTHENTICATION.md), [PERMISSIONS.md](PERMISSIONS.md)
- [OAUTH-TO-BEARER.md](OAUTH-TO-BEARER.md) — what Loopback deliberately does
  not do with a consumer product's own sign-in session.
- [TOOLS.md](TOOLS.md) — the annotations referenced above.
