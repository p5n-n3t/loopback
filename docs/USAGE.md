# Loopback usage

## Lifecycle

```bash
loopback up
loopback down
loopback restart
loopback status
loopback doctor
```

`loopback up` starts the localhost MCP runtime and then the configured ingress.

## Endpoints

```bash
loopback url
loopback dashboard
```

The MCP path is always `/mcp`. The operator dashboard is `/admin`.

## Autostart

```bash
loopback autostart on
loopback autostart off
loopback autostart status
```

On supported Linux systems, the installer can enable a user systemd unit and user lingering.

## Logs

```bash
loopback logs 200
loopback logs -f
```

Structured audit events are separate from the server log:

```text
~/.config/loopback/audit.jsonl
```

## Credentials

```bash
loopback token
loopback rotate-token
```

Rotating the machine token restarts the service and invalidates in-memory OAuth/admin sessions.

## Policy

```bash
loopback policy show
loopback policy profile read-only
loopback policy profile standard
loopback policy profile trusted
loopback policy tool process_kill off
```

See [POLICY.md](POLICY.md).

## Fleet

```bash
loopback node list
loopback node add ora3 ora3
loopback node health ora3
loopback node remove ora3
```

See [GATEWAY.md](GATEWAY.md).

## Plugin package

```bash
loopback plugin package ~/Desktop/loopback-plugin.zip
```

Requires a public HTTPS active Loopback URL.

## Runtime state

Generated state lives under `~/.config/loopback`. The source checkout can be moved or deleted after installation because the installer copies the runtime into the config directory.
