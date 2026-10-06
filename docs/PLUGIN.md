# ChatGPT / Agent Plugin

Loopback is already an MCP server. A ChatGPT plugin is packaging around that capability, not a replacement protocol.

## Private plugin

For a public HTTPS Loopback endpoint:

```bash
loopback plugin package ~/Desktop/loopback-plugin.zip
```

The ZIP uses the portable Agent Plugins format:

```text
plugin.json
mcp.json
skills/loopback/SKILL.md
```

`mcp.json` points to the installation's current HTTPS `/mcp` URL.

The packaged skill tells the agent to:

- prefer structured tools
- respect policy/denied paths
- use background jobs for long-running work
- use bounded `task_run` when several shell steps belong together
- identify fleet nodes explicitly
- honor Loopback operator approvals

## No mandatory Loopback relay

A private plugin can point directly at:

```text
ChatGPT
   |
   v
https://your-loopback-host/mcp
   |
Cloudflare / Tailscale / reverse proxy
   |
Loopback on your machine
```

You do not need a Loopback-operated cloud relay.

## Public directory

A public one-click plugin is a different distribution problem. A directory listing normally works best with one stable MCP service origin for all users. Per-customer/template MCP origins have additional platform requirements.

That is the main reason a future managed gateway may be useful—not because MCP itself requires a relay.

Loopback should preserve these deployment choices:

1. direct Cloudflare
2. direct Tailscale
3. local/private connection
4. self-hosted fleet gateway
5. optional managed gateway

## Public-review preparation

Before public submission, provide a stable production domain and the required publisher/support/privacy/terms/review materials. Keep review credentials outside the ZIP.

The public plugin should present a managed or otherwise stable MCP origin. The per-machine package generator is primarily for private/personal installations.

## Origin stability

Treat the plugin's MCP origin as durable once publicly published. Moving a published MCP server to a different scheme/host/port is a product migration, not an ordinary tool update.
