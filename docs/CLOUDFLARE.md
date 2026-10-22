# Cloudflare Tunnel ingress

Cloudflare Tunnel gives Loopback a public HTTPS hostname backed by
`cloudflared`, without opening an inbound port, and lets you layer
Cloudflare Access in front of it.

## Prerequisites

- A domain on Cloudflare and the `cloudflared` client installed.
- Either a **named tunnel** (created with `cloudflared tunnel create`, with
  a `config_file` and credentials file) or a **token tunnel** (a tunnel
  token from the Cloudflare dashboard, stored in a `token_file`).

## Configuration

`config.json`'s `ingress.cloudflare`:

```json
{
  "enabled": true,
  "hostname": "loopback.example.com",
  "tunnel_id": null,
  "tunnel_name": null,
  "token_file": null,
  "config_file": null,
  "access_policy": "oauth"
}
```

Use `tunnel_id`/`tunnel_name`/`config_file` for a named tunnel, or
`token_file` for a token tunnel — not both. `token_file` should point at a
file containing only the tunnel token; never put the token directly in
`config.json`.

## Access policy

`access_policy` controls what sits in front of Loopback's own
authentication:

- `"oauth"` (default) — Cloudflare just proxies; Loopback's OAuth/API-token
  auth is the only gate.
- `"cloudflare-access"` — Cloudflare Access (its own SSO/identity check) is
  required before a request reaches Loopback at all; Loopback's own auth
  still applies on top of that for `/mcp` and the admin API.
- `"both"` — both are active simultaneously. This is the strictest option:
  useful when the hostname needs to be reachable by a user through a
  browser (gated by Access) while an MCP client reaches the same hostname
  with its own OAuth/API token.

A resource exposed through `"both"` or `"cloudflare-access"` can have two
valid resource URIs/audiences in play (the Cloudflare-fronted one and
Loopback's own) — see [AUTHENTICATION.md](AUTHENTICATION.md) for how
protected-resource metadata advertises this.

## Enabling

```bash
loopback install --ingress cloudflare --cloudflare-token-file /path/to/token
# or, after install, edit ingress.cloudflare in config.json / the
# dashboard's Cloudflare section, then:
loopback restart
```

`loopback doctor` checks that `cloudflared` is installed, authenticated,
and that the tunnel named in `config.json` is actually running.
`loopback logs --file cloudflared` tails its log.

## Fleet classification

A target reached only through a Cloudflare-fronted hostname is classified
`via: public` in the topology view (see [FLEET.md](FLEET.md)) — it's
treated the same as any other public-internet path for probing purposes.

## See also

- [TAILSCALE.md](TAILSCALE.md) — the tailnet-first alternative.
- [SECURITY.md](../SECURITY.md) — network-boundary guidance before
  exposing anything publicly.
- [DEPLOYMENT.md](DEPLOYMENT.md)
