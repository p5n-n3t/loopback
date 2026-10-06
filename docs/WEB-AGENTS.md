# Web-agent compatibility

Loopback is an MCP server, not a ChatGPT-specific remote-control protocol.

It should work with a client that supports **Streamable HTTP MCP** and one of
Loopback's supported authentication paths.

## Tested clients

The project has been used with:

- ChatGPT
- Perplexity

Do not interpret that as a guarantee that every web agent works. MCP clients
differ in transport support, OAuth behavior, CORS/browser architecture, tool
confirmation policy, and implementation quality.

## Authentication modes

### Static bearer token

Useful for MCP clients that let a user configure a bearer token directly.

```text
Authorization: Bearer <loopback-token>
```

The same token may also be supplied through the compatibility API-key headers
accepted by Loopback.

### OAuth + PKCE

Useful for clients such as ChatGPT that expect an OAuth authorization flow.

Loopback supports:

- protected-resource metadata
- authorization-server metadata
- dynamic client registration
- authorization code
- PKCE S256
- refresh tokens
- exact MCP resource audience checking

OAuth access tokens are short-lived and distinct from the permanent machine
token.

## Confirmation prompts

There are two independent approval systems:

1. **Client-side confirmation** — ChatGPT, Perplexity, or another MCP client may
   choose to ask before invoking a tool.
2. **Loopback policy approval** — the Loopback server can require an approval
   for sensitive commands in the standard profile.

Loopback publishes MCP tool annotations for read-only/destructive/idempotent
behavior so clients have enough metadata to make better confirmation decisions.
A client is still free to ask for confirmation every time.

Therefore Loopback cannot safely or standards-compliantly force Perplexity (or
another client) to skip a confirmation dialog controlled by that client.

If a client exposes its own trust/permission setting, configure that in the
client. Separately, use Loopback's `trusted` policy only when you intentionally
want to remove Loopback's soft approval gates.

## CORS

The packaged web transport allows the configured public host plus known browser
origins for ChatGPT and Perplexity. Server-to-server MCP clients do not rely on
browser CORS.
