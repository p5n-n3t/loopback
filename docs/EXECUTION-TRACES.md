# Execution traces and incident reports

Loopback records the lifecycle of requests that arrive at its gateway. Every
authenticated MCP `tools/call` receives a gateway receipt and stage events,
including policy decisions, SSH dispatch, and the final local SSH result. This
server-side trace survives process restarts in the owner-only SQLite audit
database.

## What the gateway can observe

A successful receipt shows that the request reached the authenticated gateway.
Policy denials, scope denials, transport outcomes, timeouts, cancellations, and
remote exit codes are separate events. SSH exit code zero confirms that the
remote shell returned zero; it does not prove the intended application state
changed. Exit 255 can arise from SSH transport or the remote command and must be
read with that ambiguity in mind. A cancelled client may not know whether a
remote operation already took effect.

If no matching gateway trace exists, Loopback can say only that it has no
gateway evidence for that call. Network loss, client failure, a missing
preflight record, or an upstream rejection can all produce that condition.
Loopback cannot observe a ChatGPT Web safety decision made before dispatch,
identify the rule that produced it, or guarantee that such a decision is
captured. ChatGPT Web does not expose a supported per-call preflight hook to a
self-hosted MCP server. A Loopback upgrade cannot add one to that client.

An explicit client report containing the exact known OpenAI safety message is
recorded as a client-reported event. This preserves evidence supplied by a
client that supports the adapter below; it does not constitute independent
verification by Loopback. Missing arrival alone is never labelled an OpenAI
rejection.

## Python MCP client adapter

For a Python client that already uses the official MCP `ClientSession`, wrap
its existing `call_tool` method. Provide the Loopback origin (without `/mcp`)
and an OAuth/API access token scoped for `loopback:read`. The adapter creates a
persistent intent before invoking the existing MCP call, attaches its id to
that same JSON-RPC request, then reports the result once. It never stores tool
arguments or output and never retries an operation.

```python
from mcp import ClientSession
from trace_client import TracedMcpClient

# `session` is the already-authorized MCP ClientSession connected to
# https://loopback.example/mcp through the application's normal transport.
traced = TracedMcpClient(
    "https://loopback.example",
    access_token,
    session.call_tool,
)
try:
    result = await traced.call_tool(
        "ssh_execute",
        {"target": "ora1", "command": "hostname"},
    )
finally:
    await traced.aclose()
```

If intent creation fails, the adapter stops before calling the MCP tool. After
execution begins, failure to upload the completion report does not retry or
hide the original result. The gateway trace remains authoritative for stages
it observed. The adapter stores only the canonical safety-block text when an
exception or MCP result contains that exact message; other client errors are
reported by category without their arbitrary text. Keep the bearer token in
private process configuration and use HTTPS for non-local gateways.

This adapter covers only clients that explicitly use it. It does not instrument
ChatGPT Web, Codex, Claude, or another application automatically. It never
switches execution routes to recover from a blocked call.

## Dashboard and export

The administrator's **Execution traces** page shows gateway and client events,
searches target/tool/receipt/time, and exports a redacted JSON or Markdown
incident report. A preflight trace still awaiting a gateway event is shown as
unobserved or pending according to its deadline; it is not inferred to be a
safety block. Exports include UTC timestamps, trace and receipt ids, stages,
exit status where known, and the last successful related receipt when present.
Raw commands, credentials, and command output are not trace fields.

The feature preserves existing command policy, hard-deny rules, scope checks,
approval requirements, and stop-on-block behavior. It does not provide an
upstream OpenAI incident feed or a permanent fix for OpenAI's own safety
decisions.
