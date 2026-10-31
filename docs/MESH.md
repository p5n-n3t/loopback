# Cloudflare Mesh (opt-in private connectivity)

Cloudflare Mesh connects machines over a private network through the Cloudflare One Client (`cloudflare-warp`, `warp-svc`, `warp-cli`).
It is not Cloudflare Tunnel. Tunnel (`cloudflared`) publishes a public hostname for this gateway and is configured under **Public tunnels**.
You can use both. They keep separate settings, state and diagnostics. Mesh does not need Tailscale.

Mesh is Beta at Cloudflare. Loopback's live enrollment path is **UNVERIFIED** until the isolated lab runs it.
Detection, preflight, planning, apply, resume and rollback are covered by fixture tests (`tests/test_mesh.py`).

## Choose it

- Interactive install: answer **3) Cloudflare Mesh** to the private-connectivity question.
- Unattended install: `--private-connectivity mesh` and, optionally, a `mesh_setup` block in `--settings-file`.

The installer only records the choice. It installs and enrolls nothing, because those steps need root.

## Settings file (version 1)

```json
{
  "schema_version": 1,
  "mesh": {
    "enabled": true,
    "role": "node",
    "token_ref": "file:/root/.config/loopback-mesh/connector-token",
    "organization": "example-team",
    "mesh_range": "100.96.0.0/12",
    "acknowledge_tailscale_overlap": false,
    "overlap_mitigation": null,
    "package_version": null,
    "verify_tcp": "build-vm.internal:22"
  }
}
```

- `token_ref` is `env:NAME` or `file:/absolute/path`. The settings file never holds the token itself.
- A token file must be mode 0600. Create one connector token per independent host. Reusing a token creates replicas, not separate nodes.
- `role: subnet_router` also enables IPv4 forwarding. Choose it only to route a reviewed private subnet.

## Commands

```sh
loopback mesh status --settings mesh.json      # detection, platform, preflight findings
loopback mesh plan --settings mesh.json        # every step, skipped steps, manual account steps
sudo loopback mesh apply --settings mesh.json --yes
loopback mesh verify --settings mesh.json      # registration, connection, organization, TCP service
sudo loopback mesh rollback --yes              # removes only journaled resources
```

`apply` journals each completed step (`/var/lib/loopback-mesh/journal.json` under sudo).
A rerun skips converged and completed steps. A failed run tells you which step to fix.
`warp-cli connector new` takes the token as an argument. Local root and the invoking user can briefly see it in the process table.
Loopback never writes the token to settings, logs, the journal or command output.

## Coexistence with Tailscale

Cloudflare documents the default Mesh range `100.96.0.0/12`. Tailscale uses `100.64.0.0/10`, which contains it.
With Tailscale running, preflight blocks the plan until you choose a reviewed mitigation:

- `custom_range`: set a Mesh range outside `100.64.0.0/10` in your Cloudflare account (account-wide change).
- `route_exclusions_reviewed`: keep the default range with reviewed split-tunnel exclusions.
- `isolated_namespace`: run the client on a host or network namespace without Tailscale.

Set `acknowledge_tailscale_overlap: true` and `overlap_mitigation` together. A custom range that still overlaps stays blocked.

## What Mesh does not do

- It does not create a public URL for the dashboard or MCP endpoint.
- It does not grant shell access. Discovery lists the host, and worker installation still uses SSH and an enrollment plan.
- `warp-cli` shows only this device. Add other Mesh nodes through SSH config or manual entry in Discovery.
- Loopback never unregisters an enrollment it did not create. An enrollment in another organization blocks the plan.

## Manual account steps

Create the Mesh node and its connector token in the Cloudflare dashboard (Networks → Mesh).
Loopback has no Cloudflare API credential for account changes.
