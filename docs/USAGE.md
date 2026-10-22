# Loopback usage

## Lifecycle

```bash
loopback up       # alias: start
loopback down     # alias: stop
loopback restart
loopback status
loopback doctor
```

`up` starts the local MCP origin and then the configured ingress; `down`
reverses the sequence.

## Endpoints

```bash
loopback url
loopback dashboard --open
```

The MCP path is `/mcp`; the admin UI is `/admin`. See
[AUTHENTICATION.md](AUTHENTICATION.md) for what authenticates each.

## Credentials

```bash
loopback token                          # print the machine recovery token
loopback auth token create NAME --scopes loopback:read,loopback:write --ttl 90d
loopback auth token list
loopback auth token revoke <id>
loopback auth rotate-machine-token       # new machine token; OAuth untouched
loopback auth rotate-signing-key         # invalidates every OAuth access token now
```

There is no single `loopback rotate-token` command — machine-token,
signing-key, and per-API-token rotation are separate operations with
different blast radii. See [SECURITY.md](../SECURITY.md) and
[AUTHENTICATION.md](AUTHENTICATION.md). Print the machine token only when
you actually need it for login/recovery.

## Policy

```bash
loopback policy show
loopback policy profile restricted   # alias: standard
loopback policy profile admin        # alias: trusted
loopback policy profile read-only
loopback policy profile development
loopback policy profile locked
```

The dashboard's Security page exposes the same profile switch plus the
pending-approvals queue, filesystem boundaries, per-tool allow/deny lists,
and the browser/desktop automation toggles. Full profile definitions:
[PERMISSIONS.md](PERMISSIONS.md).

## Choosing an execution tool

### Short command

Use `execute` for commands that finish within a bounded request.

### Long-running non-interactive task

```text
job_start -> job_output(offset=...) -> job_stop
```

Output offsets let a client fetch only new log bytes.

### Stateful/interactive shell

```text
terminal_start -> terminal_write -> terminal_read -> terminal_close
```

The PTY stays alive across MCP calls. Not available on Windows — see
[WINDOWS.md](WINDOWS.md).

### Process inspection

Use `process_list` for structured process metadata; `process_kill` only
when the intended process and its side effect are clear (it refuses to
target PID 1 or the Loopback server itself).

Full tool reference: [TOOLS.md](TOOLS.md).

## Documents

DOCX text extraction/replacement, XLSX range read/write, and PDF
text-extraction/merge/page-extraction tools are available for common
machine-side workflows. All paths pass through the same filesystem policy
as the plain file tools.

## Optional browser automation

`browser_status` reports whether the separately installed `agent-browser`
executable is present. Disabled by policy by default
(`allow_browser: false`); enable it intentionally from the dashboard. The
adapter provides structured open/snapshot/click/fill/URL/close operations,
not arbitrary JavaScript evaluation.

## Optional native desktop automation

Disabled by default (`allow_desktop: false`), Linux/X11 only. Enable it
explicitly after confirming `desktop_status` reports the required local
tools. Deliberately a separate capability from browser automation so a
server can allow one without the other.

## Fleet

```bash
loopback node list
loopback node add build-server https://build-server.example.com/mcp --token-env BUILD_SERVER_TOKEN
loopback node remove build-server
loopback ssh add build-server alice@build.internal.example.com --identity ~/.ssh/id_ed25519_loopback
```

Agents use `node_list`/`node_call`/`ssh_target_list`/`ssh_execute` to route
work across a fleet; see [FLEET.md](FLEET.md).

## Dashboard

Covers system health, live jobs/terminals, topology, policy, pending
approvals, fleet inventory, and recent audit history. See
[DASHBOARD.md](DASHBOARD.md). The CLI remains the right interface for
bootstrap, headless automation, and recovery when ingress itself is down.

## Boot persistence

```bash
loopback autostart on
loopback autostart off
loopback autostart status
```

Fresh installs enable autostart via the platform's service manager
(systemd user unit / launchd / Task Scheduler) unless `--no-autostart` was
passed at install time. See [PLATFORMS.md](PLATFORMS.md).

## Configuration

There is no `loopback config` subcommand. Edit `config.json` directly, or
use the dashboard's Settings page — see [CONFIGURATION.md](CONFIGURATION.md)
for the full schema and the config-directory resolution order. Restart
after a direct edit (`loopback restart`); dashboard/admin-API changes
restart only what's needed.

## Runtime state

Generated state lives under the resolved config directory (see
[CONFIGURATION.md](CONFIGURATION.md) — typically `~/.config/loopback` on
Linux/macOS, `%LOCALAPPDATA%\Loopback` on Windows, or `LOOPBACK_CONFIG_DIR`
if set): credentials, policy, fleet registry/secrets, audit and auth
databases, job logs, service logs, and the installed runtime. Do not commit
it; back it up with `loopback backup` instead (see
[RECOVERY.md](RECOVERY.md)).
