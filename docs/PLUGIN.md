# ChatGPT plugin packaging

Loopback's MCP server is the capability layer. A ChatGPT plugin is a packaging
and discovery layer around that capability.

The repository intentionally does not hard-code one maintainer's Tailscale or
Cloudflare endpoint into a reusable plugin package.

## Build a private plugin package

Generate a plugin archive for an installed Loopback endpoint:

```bash
python3 scripts/build_plugin.py \
  --mcp-url https://loopback.example.com/mcp \
  --output dist/loopback-plugin.zip
```

The generated archive contains:

- `plugin.json`
- `mcp.json`
- `skills/loopback/SKILL.md`

The MCP URL must be an HTTPS Streamable HTTP Loopback endpoint.

## Public directory direction

A public one-click plugin normally benefits from one stable MCP endpoint shared
by all installs. That can be an optional Loopback-hosted or self-hosted gateway
that routes authenticated users to their enrolled machines.

That gateway should remain optional. Users who prefer direct Cloudflare,
Tailscale, or private infrastructure should continue to be able to operate
Loopback without a vendor relay.
