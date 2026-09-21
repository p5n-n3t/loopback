# Deployment

## Headless Cloudflare deployment

For servers, CI, and fleet provisioning, Loopback supports a remotely-managed Cloudflare Tunnel token file.

Create and configure the tunnel centrally, place its runtime token in a temporary file on the target host, then run:

```bash
./install.sh \
  --non-interactive \
  --ingress cloudflare \
  --host loopback-node.example.com \
  --cloudflare-tunnel-id <UUID> \
  --cloudflare-token-file /path/to/token
```

The installer copies the token to `~/.config/loopback/cloudflared.token` with mode `0600`. The CLI starts the tunnel using `cloudflared tunnel run --token-file`, so the credential is not placed directly in the process command line.

The Cloudflare-side tunnel configuration should route the public hostname to:

```
http://127.0.0.1:2026
```

The MCP connector URL is:

```
https://loopback-node.example.com/mcp
```

Each machine should use its own tunnel token. Do not reuse one tunnel token as a fleet-wide credential.
