# Loopback usage

## Lifecycle

`loopback up` starts the local MCP server and then the configured ingress.

`loopback down` stops ingress first and then stops the MCP server.

`loopback restart` performs a clean down/up cycle.

`loopback status` reports profile, ingress, endpoint, timeout, and output limits.

## Diagnostics

Run:

```bash
loopback doctor
```

It checks the Python environment, runtime file, auth token, Git, the chosen ingress dependency, and local/public health endpoints.

For server logs:

```bash
loopback logs 200
loopback logs -f
```

## Endpoint

Print the active MCP URL with:

```bash
loopback url
```

The MCP path is always `/mcp`.

## Credentials

Print the current local token only when you explicitly need it:

```bash
loopback token
```

Rotate it with:

```bash
loopback rotate-token
```

Rotation restarts the service so the new credential is active immediately.

## Configuration

Show configuration:

```bash
loopback config list
```

Read one value:

```bash
loopback config get LOOPBACK_PORT
```

Set one value:

```bash
loopback config set LOOPBACK_TIMEOUT 300
```

Restart Loopback after settings that affect the running server or transport.

## Files

Generated runtime state is under:

```
~/.config/loopback/
```

The repository itself should remain free of machine credentials and generated runtime state.
