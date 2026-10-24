# Fleet

A Loopback gateway can route MCP tool calls to other machines two ways:

- **Loopback nodes** — another machine running its own Loopback server,
  reached over HTTP(S) with a bearer token.
- **SSH targets** — any machine reachable with the system `ssh` client in
  non-interactive `BatchMode`. No separate Loopback install is required on
  the target.

Both appear together in the dashboard's Fleet and Topology views and in the
`/api/admin/topology` graph (see [ADMIN-API.md](ADMIN-API.md)). `"local"` is
a reserved name on both registries — it always refers to the machine
Loopback itself is running on.

## Loopback nodes

```bash
loopback node list
loopback node add build-server https://build-server.example.com/mcp --token-env BUILD_SERVER_TOKEN
loopback node remove build-server
```

Each node's bearer credential is stored separately from its metadata:
`nodes.json` (name, url, enabled, description) and `nodes.secrets.json`
(name → token), both under the config directory, mode `0600`. The public
node list and the dashboard never return a node's token.

From MCP, discover and call a node:

1. `node_list` — enumerate registered nodes (no secrets).
2. `node_call` — invoke an arbitrary tool on a named node.
3. `node_read_file`, `node_execute`, `node_diagnostics` — ergonomic
   wrappers around the most common node operations.

Do not fan a call out to every node unless the user explicitly asked for
that; pick the one the user named.

## SSH targets

```bash
loopback ssh list
loopback ssh add build-server alice@build.internal.example.com --identity ~/.ssh/id_ed25519_loopback --kind remote-vm --group infra
loopback ssh probe build-server
loopback ssh remove build-server
```

Target fields: `name` (`^[A-Za-z0-9_.-]{1,40}$`), `host`, `port` (default
22), `user` (defaults to the local account name), `identity_file` (an
existing, readable path — Loopback never reads or stores its contents),
`display_name`, `kind` (`remote-physical` / `remote-vm` / `container` /
`unknown`), `group`, and `via` (`auto` / `tailscale` / `lan` / `public` — how
the target is reached, used for topology classification). See
[examples/ssh_targets.example.json](../examples/ssh_targets.example.json)
for the on-disk shape.

Probing runs a small POSIX `sh` script over SSH to collect hostname, OS,
kernel, user, home, and virtualization facts without exposing any
credential, and classifies the result:

| Status | Meaning |
|---|---|
| `healthy` | Probe succeeded with every expected field present. |
| `degraded` | Probe succeeded but some expected fields were missing. |
| `auth_failed` | SSH rejected the credential — fix the identity file or `authorized_keys`, retrying will not help. |
| `unreachable` | Network-level failure (refused, timed out, DNS failure). |
| `error` | Anything else (e.g. no local `ssh` client installed). |

Executing a command on an SSH target (`ssh_execute`-style tools) runs
Loopback's own `check_command`/`check_path` policy for that target **before**
anything is sent over SSH, using `shlex.quote` to build the remote command
safely around paths and arguments that contain spaces. Every per-target
execution reports `"enforcement": "gateway-path-check"` — SSH targets have
no OS sandbox of their own, so this server-side check is the only
enforcement layer; it is not equivalent to Landlock/bwrap/Seatbelt and
should not be treated as one.

## Per-target policy

Both node and SSH-target policy entries live under `policy.json`'s
`targets` key, independent of the local machine's policy and of each other.
A remote target **never inherits the local machine's `allowed_roots`** — it
starts from `{"path": "~", "mode": "rw"}` on that target's own home
directory unless you configure otherwise. See
[examples/policy.example.json](../examples/policy.example.json) for a
worked example, and [PERMISSIONS.md](PERMISSIONS.md) for the full policy
model.

```bash
# via the dashboard: Fleet -> target -> Permissions
# or directly in policy.json:
```

```json
{
  "targets": {
    "build-server": {
      "profile": "restricted",
      "allowed_roots": [{"path": "~/deploys", "mode": "rw"}],
      "allow_shell": true,
      "allow_process_control": false,
      "enforcement": "gateway-path-check"
    }
  }
}
```

## Topology

`GET /api/admin/topology` (and the dashboard's Topology page) renders every
node, SSH target, ingress path, and external-client bucket as one graph.
Node types: `gateway`, `local-computer`, `remote-physical`, `remote-vm`,
`container`, `external-client`, `tunnel`, `loopback-node`. Edge statuses:
`healthy`, `degraded`, `unreachable`, `auth_failed`, `disconnected`,
`untested` (configured but never probed), `stale` (last probe older than
`stale_after_seconds`). See [ADMIN-API.md](ADMIN-API.md) for the exact JSON
schema.

## See also

- [PERMISSIONS.md](PERMISSIONS.md) — profiles, sandbox backends, per-target
  policy.
- [TOOLS.md](TOOLS.md) — fleet tool names, scopes, and annotations.
- [TAILSCALE.md](TAILSCALE.md), [CLOUDFLARE.md](CLOUDFLARE.md) — how a
  target is classified `via` tailscale/lan/public.
