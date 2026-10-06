# Fleet gateway

Loopback can be the single MCP connector in front of several machines.

```text
Web agent
   |
   v
gateway.example.com/mcp
   |
   +-- local
   +-- ora3
   +-- ora4
   +-- homelab
```

## v3 transport

The initial self-hosted fleet transport is SSH. This is intentional:

- mature host-key verification
- per-host keys and policies
- no new fleet-wide secret
- works with existing aliases, jump hosts and VPN routes
- easy to understand and recover without Loopback

`~/.config/loopback/nodes.json` stores node labels/transports/targets. It does not store private keys or passwords.

## Configure

```bash
loopback node add ora3 ora3
loopback node add ora4 jq@ora4.example.net
loopback node list
loopback node health ora3
```

From MCP:

- `node_list`
- `node_health`
- `node_command`
- `node_copy`

## Stateful work

Use `node_command` for discrete remote commands. For complex stateful remote workflows, either:

- use a direct Loopback endpoint on that machine, or
- execute a remote tmux/systemd/container workflow through SSH.

A future node-agent transport can add outbound rendezvous without removing SSH or direct endpoints.

## Hosted relay direction

A hosted Loopback service is **optional**, not a requirement of the local agent.

Potential topology:

```text
ChatGPT / Perplexity
        |
        v
managed Loopback gateway
        |
        +-- outbound-connected laptop
        +-- outbound-connected VPS
        +-- outbound-connected home server
```

The same gateway protocol should also be self-hostable. The commercial convenience layer must not make Cloudflare/Tailscale/direct self-hosting second-class.

## Security

Prefer:

- dedicated gateway account
- SSH config aliases
- per-host keys
- constrained sudo
- distinct production/test nodes
- no universal bearer copied across hosts
