# Tailscale ingress

Tailscale integration gives you a stable HTTPS name for Loopback without
opening any inbound port on your network, and (in Serve mode) without
leaving your tailnet at all.

## Prerequisites

- The `tailscale` client installed and logged into a tailnet
  (`tailscale status` should show your machine).
- MagicDNS and HTTPS certificates enabled for the tailnet (Tailscale admin
  console → DNS), so `tailscale serve`/`funnel` can provision a cert.

## Modes

`config.json`'s `ingress.tailscale`:

```json
{
  "enabled": true,
  "mode": "serve",
  "https_port": 443,
  "dns_name": null
}
```

- **`serve`** — reachable only from devices on your tailnet, at
  `https://<machine>.<tailnet>.ts.net`. This is the safe default for a
  personal or team machine: nothing is exposed to the public internet.
- **`funnel`** — the same hostname, reachable from the public internet.
  Enabling Funnel requires explicitly confirming the public-exposure prompt
  (`confirm_public: true` on the ingress-change API call, or the matching
  dashboard confirmation) — it is never turned on silently as a side effect
  of enabling Tailscale.

`dns_name` overrides the auto-detected MagicDNS name if you need to pin it.

## Enabling

From the dashboard's Tailscale section, or:

```bash
loopback install --ingress tailscale --tailscale-mode serve
# or, after install, edit ingress.tailscale in config.json / the dashboard
# Settings page, then:
loopback restart
```

`loopback status` and `loopback url` show the resulting hostname once
Tailscale ingress is active; `loopback doctor` checks that `tailscale`
status, MagicDNS, and the serve/funnel configuration all agree with what
`config.json` expects.

## Access control

Serve mode's access control is Tailscale's own: only devices in your
tailnet (and ACL-permitted) can reach the hostname at all, on top of
Loopback's own OAuth/API-token/dashboard authentication underneath. Funnel
mode removes that network-level restriction, so Loopback's own
authentication becomes the only boundary — review
[AUTHENTICATION.md](AUTHENTICATION.md) and
[SECURITY.md](../SECURITY.md) before enabling it.

## Fleet classification

SSH targets and nodes reached over a tailnet address (`100.64.0.0/10`,
`fd7a:115c:a1e0::/48`, or a `*.ts.net` name) are automatically classified
`via: tailscale` in the topology view — see [FLEET.md](FLEET.md).

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#ingress) for the common
mismatch between what the dashboard shows and what `tailscale status`
reports, and [PLATFORMS.md](PLATFORMS.md) for per-OS Tailscale
availability.

## See also

- [CLOUDFLARE.md](CLOUDFLARE.md) — the alternative, public-by-design
  ingress path.
- [DEPLOYMENT.md](DEPLOYMENT.md) — choosing between ingress options.
