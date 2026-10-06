# Architecture

Loopback v3 separates protocol, policy, execution and presentation.

```text
MCP clients
   |
   +-- bearer/API key
   +-- OAuth + PKCE
   |
TokenGate
   |
MCP tools ---------------- Admin REST/UI
   |                           |
Policy / approvals ------------+
   |
   +-- filesystem + Git
   +-- jobs
   +-- PTYs
   +-- processes/metrics
   +-- optional browser
   +-- node registry
           |
           +-- local
           +-- SSH nodes
```

## Runtime modules

- `server.py` — MCP tools, OAuth, admin HTTP routes, auth middleware
- `policy.py` — profiles, filesystem rules, command decisions, one-time approvals
- `jobs.py` — background process groups and PTY sessions
- `metrics.py` — psutil-backed process/host telemetry
- `nodes.py` — local/SSH fleet registry and routing
- `audit.py` — JSONL operational audit
- `dashboard.py` — operator UI
- `browser.py` — optional structured agent-browser adapter
- `plugin_packager.py` — private portable Agent Plugin ZIP generator

## Invariants

- origin binds localhost
- ingress is replaceable
- machine token never belongs in Git
- OAuth clients do not receive the long-lived machine bearer
- dangerous capabilities are policy-gated
- policy is enforced by the server, not only documented to the model
- fleet configuration does not contain SSH private keys
- client-side approval behavior remains under the client
