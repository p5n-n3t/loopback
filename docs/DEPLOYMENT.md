# Deployment

Loopback keeps the origin on `127.0.0.1`. The ingress layer is replaceable.

## Local only

```bash
./install.sh --non-interactive --ingress local
```

Useful for development, a private MCP tunnel, or a locally running MCP client.

## Tailscale Funnel

```bash
./install.sh --ingress tailscale
```

The installer reuses an existing Tailscale installation when present.

## Cloudflare Tunnel

Interactive:

```bash
./install.sh --ingress cloudflare --host loopback.example.com
```

Headless token-file deployment:

```bash
./install.sh \
  --non-interactive \
  --ingress cloudflare \
  --host loopback-node.example.com \
  --cloudflare-tunnel-id <UUID> \
  --cloudflare-token-file /path/to/token
```

The token is copied to `~/.config/loopback/cloudflared.token` with mode `0600`.

The Cloudflare-side route should target:

```text
http://127.0.0.1:2026
```

## Reverse proxy

You can manage your own HTTPS proxy instead of Cloudflare/Tailscale. Proxy both `/mcp` and Loopback's OAuth/admin discovery routes to the same localhost origin. Preserve normal forwarding headers and do not add an unauthenticated bypass to `/admin` or `/mcp`.

## Profiles

Install with an explicit policy profile:

```bash
./install.sh --ingress cloudflare --host loopback.example.com --profile standard
```

Use `trusted` only when you intentionally want shell/process/fleet/browser control.

## Upgrade

For a source checkout upgrade:

```bash
git pull
./install.sh --non-interactive --ingress <existing-ingress> --host <existing-host>
```

Before upgrading a production node, keep a copy of `~/.config/loopback`. The installer preserves the existing machine token but refreshes runtime code/dependencies.

A future dedicated `loopback upgrade` command can automate source retrieval while preserving this same state model.
