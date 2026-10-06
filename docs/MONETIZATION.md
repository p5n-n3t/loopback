# Open-source and hosted product boundary

Loopback is strongest when the local agent and self-hosted path remain open
source.

## Community / open-source core

A sensible permanent free core includes:

- local MCP server
- bearer + OAuth compatibility
- Cloudflare/Tailscale/local ingress
- filesystem and process capabilities
- background jobs and PTYs
- policy profiles and filesystem boundaries
- local audit log and dashboard
- direct multi-node gateway
- CLI and self-hosted deployment

This preserves the project's strongest differentiator: **Loopback works without
a Loopback-operated cloud**.

## Optional paid service

A hosted service can monetize convenience and fleet operations rather than
locking basic machine access behind a subscription.

Potential paid capabilities:

- universal hosted MCP gateway for one-click plugin onboarding
- outbound device enrollment with no user-managed public URL
- node discovery and connection health
- encrypted managed device identity
- team RBAC and workspace sharing
- centralized policy distribution
- longer audit retention, search, and exports
- alerts for offline nodes, failed tasks, and policy events
- SSO and enterprise identity
- managed updates
- mobile approval workflows
- hosted dashboards across many machines
- enterprise/self-hosted gateway support

## Positioning

Desktop-oriented tools are commonly framed as:

> AI operates my computer.

Loopback should be framed as:

> AI securely operates my machines.

That includes laptops, workstations, VPS instances, Docker hosts, Raspberry Pi
systems, NAS/home-lab machines, and cloud VMs.

The commercial value is less in exposing a shell and more in making remote AI
operations easy to enroll, govern, observe, and share safely.
