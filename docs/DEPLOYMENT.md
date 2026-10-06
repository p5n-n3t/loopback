# Deployment

Loopback deliberately supports more than one network topology.

## Local only

```bash
./install.sh --non-interactive --ingress local
```

Useful for development, desktop clients that can reach localhost, or a machine
behind another private transport.

## Cloudflare Tunnel

Interactive:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

For headless servers, use a centrally managed tunnel token file:

```bash
./install.sh \
  --non-interactive \
  --ingress cloudflare \
  --host loopback-node.example.com \
  --cloudflare-tunnel-id <UUID> \
  --cloudflare-token-file /path/to/token
```

The installer copies the token into the Loopback config directory with mode
0600. Configure Cloudflare to route the hostname to:

```text
http://127.0.0.1:2026
```

## Tailscale Funnel

Select Tailscale during installation. Loopback keeps the MCP service on
localhost and asks Tailscale to publish it over HTTPS.

## Existing reverse proxy

It is also valid to keep Loopback in local mode and point an existing nginx,
Caddy, Traefik, or other HTTPS reverse proxy at `127.0.0.1:2026`.

Preserve the MCP method/headers and do not strip the `Mcp-Session-Id` response
header.

## URLs

```text
MCP:       https://host.example/mcp
Dashboard: https://host.example/admin
Health:    https://host.example/healthz
```

OAuth discovery lives under the standard well-known paths on the same origin.

## Fleet deployment

Install Loopback independently on each node. Give each node its own ingress and
credential, then register selected node endpoints on the gateway.

Do not distribute a universal fleet bearer token.

## Hosted relay direction

A future hosted Loopback gateway can let agents use one universal endpoint while
nodes make outbound connections. That is an optional convenience topology, not
a requirement for direct/self-hosted Loopback.
