# Fleet gateway

A Loopback installation can act as one MCP connector in front of several machines.

```text
MCP-capable agent
        |
        v
 Loopback gateway
   /     |      \
local   ora3    ora4
```

A gateway is optional. Every node can still be used directly.

## Register nodes

From the CLI:

```bash
loopback node add ora3 https://ora3.example.com/mcp
loopback node add ora4 https://ora4.example.com/mcp
loopback node list
```

If a node uses a bearer token, enter it at the hidden prompt or supply it from an
environment variable:

```bash
export ORA3_LOOPBACK_TOKEN='...'
loopback node add ora3 https://ora3.example.com/mcp --token-env ORA3_LOOPBACK_TOKEN
unset ORA3_LOOPBACK_TOKEN
```

The dashboard can also add/remove nodes.

## MCP routing

`node_list` returns the local gateway plus registered nodes without credentials.

`node_call` takes:

- `node` — registered node name
- `tool` — tool name exposed by that node
- `arguments` — the remote tool arguments
- `timeout` — bounded network/tool timeout

Example logical flow:

```text
node_list()
node_call(node="ora3", tool="diagnostics", arguments={})
node_call(node="ora4", tool="read_file", arguments={"path":"~/docker/app/compose.yml"})
```

Remote calls use the MCP protocol rather than SSH aliases. This keeps a node's
transport/authentication independent from the gateway host.

## Security model

- nodes have independent credentials
- credentials are stored in `nodes.secrets.json`, not returned by `node_list`
- node metadata lives in `nodes.json`
- the gateway honors its own fleet policy before routing
- the remote node enforces its own policy again

That produces two enforcement boundaries for routed operations: gateway policy
and destination-node policy.

## Future hosted relay

The direct-node registry is intentionally compatible with a future optional
hosted or self-hosted rendezvous layer. Such a service can add outbound device
enrollment and discovery, but it should not replace direct Cloudflare/Tailscale
operation for users who want a no-vendor-cloud topology.
