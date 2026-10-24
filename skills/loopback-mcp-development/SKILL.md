---
name: loopback-mcp-development
description: Build or extend MCP clients and integrations against a Loopback server, including OAuth 2.1 client registration, protected-resource discovery, scopes, and the admin HTTP API. Use when writing code that talks to Loopback's endpoints, not when operating machines through an existing client.
---

# Developing against Loopback

Use this skill when the task is writing or debugging code/config that
integrates with Loopback's HTTP surface: an MCP client, an OAuth consumer, or
a script against the admin API. For using an already-connected Loopback
server's tools, see `loopback-core` / `loopback-fleet`.

## Discovery and auth flow

1. Fetch `/.well-known/oauth-protected-resource` (or the MCP-specific
   `/.well-known/oauth-protected-resource/mcp`) to learn the resource's
   authorization servers and scopes.
2. Fetch `/.well-known/oauth-authorization-server` (RFC 8414-shaped; an
   `/.well-known/openid-configuration` alias also exists) for the
   authorization/token/registration/revocation endpoints.
3. Register a client with `POST /oauth/register` (Dynamic Client
   Registration) or use a Client ID Metadata Document URL as the `client_id`
   — do not hardcode a client secret for a public client.
4. Run authorization code + mandatory PKCE (S256). A plain or missing code
   challenge must be rejected by a correct client implementation, not just
   by the server.
5. Request only the scopes the integration needs:
   `loopback:read`, `loopback:write`, `loopback:exec`, `loopback:fleet`. Never
   request `loopback:admin` from an OAuth client — it is not grantable that
   way and asking for it signals a design mistake. The legacy `loopback`
   scope string expands server-side to read+write+exec+fleet (not admin).
6. Include a resource indicator (RFC 8707) naming the Loopback resource URI
   so the authorization server audience-binds the token correctly, and
   rotate refresh tokens (RFC 7009-style revocation is also available).

## Calling the MCP endpoint

The MCP server is mounted at `/mcp` as a streamable-HTTP endpoint. Treat
tool annotations (read-only/destructive/idempotent) as real hints for your
own retry and confirmation logic, not decoration.

## Admin API

The dashboard's HTTP contract (`docs/ADMIN-API.md`) is a separate,
session-cookie-authenticated API (`loopback_admin` cookie plus an
`X-Loopback-CSRF` header on mutating requests) — it is not reachable with an
OAuth bearer token, and your integration should not try to reuse one there.

## What not to build

Do not build an integration whose purpose is turning a consumer AI
subscription's own sign-in session (e.g. a ChatGPT/Claude/Gemini web login)
into a portable API key that spends that subscription's quota outside its
own product — the MCP spec forbids token passthrough, provider terms
prohibit it, and Loopback will never support it as a feature. See
`docs/OAUTH-TO-BEARER.md` for what is and is not implemented, and why.

## See also

- `docs/AUTHENTICATION.md` — full endpoint and scope reference.
- `docs/ADMIN-API.md` — admin/dashboard HTTP contract.
- `docs/TOOLS.md` — MCP tool catalog.
- `docs/OAUTH-TO-BEARER.md` — token-type boundaries and rationale.
