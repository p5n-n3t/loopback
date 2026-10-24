---
name: loopback-fleet
description: Route Loopback MCP tool calls to other registered nodes or SSH targets, inspect fleet topology and probe health, and work across more than one machine in a Loopback fleet. Use whenever a task names a specific machine other than the local one, or asks to compare multiple machines.
---

# Loopback fleet tools

A Loopback gateway can reach other machines two ways: direct Loopback nodes
(each running its own Loopback server) and managed SSH targets (any reachable
machine with an SSH server). Both appear in the fleet/topology view. This
skill covers choosing and calling the right one. For single-machine tools see
`loopback-core`; for dashboard/auth administration see `loopback-admin`.

## Efficient workflow

1. Call the node/target list tool first and read the names back to the user
   if there is any ambiguity — never guess a target name.
2. Pick the one named machine the user intended. Do not fan a command out to
   every registered node unless the user explicitly asked for that.
3. For a direct node, call the node-scoped wrapper tool for that node and
   tool name. For an SSH target, use the SSH execute/probe tools with that
   target's exact name.

## Health and topology

Before running a command on a target you have not used yet in this
conversation, consider probing it first (or checking its last known probe
result) so a stale or unreachable machine fails fast with a clear reason
instead of a confusing timeout. Edge/target statuses you will see include
healthy, degraded, unreachable, auth_failed, disconnected, untested, and
stale — read these literally rather than retrying blindly on auth_failed
(that means credentials, not transient network trouble).

## Per-target policy is independent

Each remote target has its own policy entry; it never inherits the local
machine's allowed roots or shell settings, and enforcement for SSH targets is
server-side path/command checking rather than an OS sandbox (there is no
sandbox on a machine Loopback only reaches over SSH). A command allowed
locally can still be denied, or need approval, on a given target — that is
expected and must never be worked around by running the equivalent command
locally or over a different target against the remote machine's files. Never
try to reach a denied remote path indirectly through a mounted share or a
local copy of the target's filesystem.

## See also

- `docs/FLEET.md` — node/SSH-target registration, topology schema, probing.
- `docs/PERMISSIONS.md` — per-target policy model.
- `docs/TOOLS.md` — exact fleet tool names and scopes.
