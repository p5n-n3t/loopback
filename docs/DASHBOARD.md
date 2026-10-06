# Dashboard

The Loopback dashboard is the human operations console.

```text
https://<loopback-host>/admin
```

Authenticate with the machine Loopback token. The dashboard creates an HttpOnly admin session; it does not put the token into browser localStorage.

## Host and resources

The overview shows:

- hostname / user / platform
- CPU
- memory
- home filesystem
- process count
- active jobs
- PTY sessions
- observed MCP clients
- ingress and MCP URL

## Jobs and PTYs

Background jobs started by `command_start` appear with PID, status, command and stop control. The dashboard can display accumulated job output.

Persistent PTYs are shown separately from jobs and can be closed by the operator.

## Processes

The process view is structured and filterable. It includes PID, process name, CPU, memory and command line.

The dashboard can send SIGTERM. Use this carefully; dashboard actions are administrative even when the active MCP policy profile is read-only.

## Policy

The policy panel controls:

- read-only / standard / trusted profile
- individual powerful-tool toggles
- filesystem allowed paths
- filesystem denied paths

Changes are persisted under `~/.config/loopback/policy.json`.

## Nodes

The fleet panel can add/remove SSH targets and run a health check.

Examples:

```text
ora3 -> ora3
ora4 -> jq@203.0.113.10
```

Using an SSH config alias is preferred because host keys, ports, jump hosts and identity files remain in SSH's own configuration.

## Connected clients

Loopback records a small in-memory activity summary for authenticated MCP traffic: hashed connection identity, user agent, request count and last-seen time. It does not display bearer/OAuth credentials.

## Approvals

Commands/tasks classified as `confirm` appear with **Approve once**. Approvals expire and are consumed once.

## Audit

The dashboard displays recent structured entries from `audit.jsonl`: tool/action, status, timestamp and non-secret details.

## CLI parity

The dashboard complements rather than replaces the CLI. Headless servers can be managed entirely with `loopback ...` commands and configuration files.
