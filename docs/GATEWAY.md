# Fleet gateway

A Loopback installation can act as the single MCP connector in front of several machines.

The recommended topology is:

```text
Web agent
   |
   v
gateway.example.com/mcp
   |
   +-- local
   +-- node1
   +-- node2
   +-- node3
```

Keep each node independently authenticated and reachable through its own Loopback endpoint. The gateway should identify targets explicitly and must not share one universal bearer token across every machine.

## Standard fleet operations

For a portable/public setup, prefer target-aware file, Git, search, and diagnostic operations. Keep arbitrary command execution as a separately administered trusted capability rather than the default public profile.

## Current development gateway

The development instance for this repository uses named targets and exposes target discovery plus an administrator-managed trusted execution path. Those machine-specific SSH aliases and credentials are intentionally not committed to the public repository.

## Direct node endpoints

Individual node endpoints remain useful for debugging, recovery, and clients that do not understand fleet routing. A gateway is an additional convenience layer, not a reason to remove per-node authentication.
