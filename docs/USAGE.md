# Loopback usage

## Lifecycle

```bash
loopback up
loopback down
loopback restart
loopback status
loopback doctor
```

`up` starts the localhost MCP origin and then the selected ingress. `down`
reverses the sequence.

## Endpoints

```bash
loopback url
loopback dashboard
```

The MCP path is `/mcp`; the admin UI is `/admin`.

## Credentials

```bash
loopback token
loopback rotate-token
```

Print the token only when explicitly needed. Rotation restarts the service and
invalidates OAuth credentials derived from the previous secret.

## Policy

```bash
loopback policy show
loopback policy profile standard
loopback policy profile trusted
loopback policy profile read-only
loopback policy profile locked
```

The dashboard exposes the same profile switch plus bounded approval actions.

## Choosing an execution tool

### Short command

Use `execute` for commands that should finish within a bounded request.

### Long-running non-interactive task

Use:

```text
job_start -> job_output(offset=...) -> job_stop
```

Output offsets let a client fetch only new log bytes.

### Stateful/interactive shell

Use:

```text
terminal_start -> terminal_write -> terminal_read -> terminal_close
```

The PTY remains alive across MCP calls.

### Process inspection

Use `process_list` for structured process metadata. Use `process_kill` only
when the intended process and side effect are clear.

## Fleet

```bash
loopback node list
loopback node add NAME https://host.example/mcp
loopback node remove NAME
```

Agents use `node_list` and `node_call` to route work.

## Dashboard

The dashboard is optimized for:

- system health and Loopback uptime
- live jobs/terminals
- policy profile
- pending approvals
- node inventory
- recent audit history

The CLI remains the best interface for bootstrap, headless automation, and
recovery when public ingress is unavailable.

## Boot persistence

Fresh installs enable user-systemd autostart when systemd is available.

```bash
loopback autostart on
loopback autostart off
loopback autostart status
```

Pass `--no-autostart` to the installer for session-only operation.

## Configuration

```bash
loopback config list
loopback config get LOOPBACK_PORT
loopback config set LOOPBACK_TIMEOUT 300
```

Restart after changing settings that affect runtime or transport.

## Runtime state

Generated state is kept under:

```text
~/.config/loopback/
```

This includes machine credentials, policy, node registry/secrets, audit database,
job logs, service logs, PID files, and the installed runtime. Do not commit it.
