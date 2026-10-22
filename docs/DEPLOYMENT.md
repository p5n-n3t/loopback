# Deployment

Loopback deliberately supports more than one network topology, all built
from the same single-process server (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Local only

```bash
./install.sh --non-interactive --ingress local
```

For development, desktop MCP clients that can reach localhost, or a
machine fronted by some other private transport you manage yourself.

## Tailscale Serve / Funnel

```bash
loopback install --ingress tailscale --tailscale-mode serve   # tailnet-only
loopback install --ingress tailscale --tailscale-mode funnel  # public
```

Loopback keeps the MCP/dashboard origin on `127.0.0.1` and asks Tailscale
to publish it over HTTPS at `https://<machine>.<tailnet>.ts.net`. See
[TAILSCALE.md](TAILSCALE.md) for the Serve-vs-Funnel exposure trade-off.

## Cloudflare Tunnel

Interactive:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

Headless, with a pre-provisioned tunnel token:

```bash
./install.sh \
  --non-interactive \
  --ingress cloudflare \
  --host loopback-node.example.com \
  --cloudflare-tunnel-id <UUID> \
  --cloudflare-token-file /path/to/token
```

The installer copies the token file into the config directory at mode
`0600`. Configure Cloudflare to route the hostname to
`http://127.0.0.1:2026`. `access_policy` lets you additionally require
Cloudflare Access in front of Loopback's own auth — see
[CLOUDFLARE.md](CLOUDFLARE.md).

## Existing reverse proxy

Equally valid: keep Loopback in local mode and point an existing nginx,
Caddy, Traefik, or other HTTPS-terminating proxy at `127.0.0.1:2026`.
Preserve the MCP request method/headers verbatim and do not strip the
`Mcp-Session-Id` response header — streamable-HTTP MCP depends on it.

## URLs

```text
MCP:       https://host.example/mcp
Dashboard: https://host.example/admin
Health:    https://host.example/healthz
```

OAuth discovery lives under the standard well-known paths on the same
origin — see [AUTHENTICATION.md](AUTHENTICATION.md).

## Fleet deployment

Install Loopback independently on each node you want reachable as a
Loopback node (as opposed to an SSH target, which needs no Loopback
install at all — see [FLEET.md](FLEET.md)). Give each node its own ingress
and its own bearer credential, then register the node's public endpoint
from the gateway:

```bash
loopback node add build-server https://build-server.example.com/mcp --token-env BUILD_SERVER_TOKEN
```

Do not distribute one bearer credential across an entire fleet — each
node's credential is independent and independently revocable.

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — what's actually running behind each
  of these ingress choices.
- [TAILSCALE.md](TAILSCALE.md), [CLOUDFLARE.md](CLOUDFLARE.md)
- [FLEET.md](FLEET.md), [PERMISSIONS.md](PERMISSIONS.md)
