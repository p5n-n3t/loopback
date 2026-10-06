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

The dashboard exposes the same profile switch plus bounded approval actions,
filesystem boundaries, per-tool allow/deny lists, and the optional browser toggle.

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

## Documents

Structured document tools are available for common machine-side workflows:

- DOCX text extraction and text replacement
- XLSX range reads and writes
- PDF text extraction, merge, and page extraction

All paths pass through Loopback's filesystem policy.

## Optional browser automation

`browser_status` reports whether the separate `agent-browser` executable is
installed. Browser actions are disabled by policy by default.

Enable them intentionally from the dashboard by turning on browser automation.
The adapter provides structured open/snapshot/click/fill/URL/close operations,
not arbitrary JavaScript evaluation.

## Optional native desktop automation

Native desktop control is disabled by default. On a Linux/X11 node, enable it
explicitly in the dashboard after confirming `desktop_status` reports the
required local tools.

Available operations include:

- list visible windows
- capture a desktop screenshot as MCP image content
- activate a window
- click coordinates
- type text
- send a constrained key expression

This is deliberately a separate capability from browser automation so a server
can allow one without allowing the other.

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
