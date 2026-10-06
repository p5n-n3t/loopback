# Policy and approvals

Loopback deliberately ships powerful capabilities and keeps the most sensitive ones disabled by default.

## Profiles

### read-only

Designed for inspection. Process listing and information are available; shell, PTY, process mutation, fleet execution and browser control are disabled.

### standard

The portable default. File/Git/diagnostic tools are available within the filesystem policy. Structured process visibility is available. Arbitrary commands, background command starts, PTY mutation, process killing, remote node execution and browser control remain off.

### trusted

For a machine you intentionally want an agent to operate. Enables commands, bounded tasks, background jobs, PTYs, process mutation, fleet execution and optional browser control.

Switch profiles:

```bash
loopback policy profile standard
loopback policy profile trusted
```

## Per-tool overrides

```bash
loopback policy tool process_kill off
loopback policy tool browser_control on
```

The dashboard exposes the same toggles.

## Filesystem policy

`policy.json` contains `allow` and `deny` lists. Paths are expanded and resolved before comparison.

The dashboard can edit these lists without editing JSON manually.

A useful development profile might allow only:

```text
~/Desktop
~/Projects
~/docker
```

and deny credential directories even if they sit below an allowed parent.

## Command classification

Loopback evaluates a command before execution:

- **allow** — may run if the relevant tool is enabled
- **confirm** — requires a one-time Loopback approval ID
- **deny** — rejected

For a confirmation, the tool returns an approval ID. The human operator opens the dashboard, reviews the exact command/task and chooses **Approve once**. The client retries with that approval ID; it cannot be reused.

## Why server-side approvals exist

A web agent's own approval dialog answers "does this client allow the tool call?"

Loopback's approval answers a different question:

> "Does the machine operator allow this action on this machine?"

Both may be active.

## Reducing approval fatigue

Loopback will not try to bypass mandatory client safeguards. Instead:

- use structured tools instead of shell when possible
- use `task_run` to group a coherent 1–50 step workflow into one MCP call
- use `command_start` and `job_output` for long jobs
- use client thread/session grants where available
- keep only the capabilities you actually want enabled

This reduces noisy approvals while preserving meaningful security boundaries.
