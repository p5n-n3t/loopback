# Isolated ora8 smoke run

The owner must provision a dedicated Linux account named `loopback-lab` on
`ora8`. Give it its own home directory, no sudo or doas privileges, no provider
credentials, no production files, and no background Loopback installation.
Do not reuse `jq`. An administrator performs account creation separately.
The harness does not create accounts or modify the host configuration.

Provision Python 3.11+ with `venv` and `ensurepip`, Bash, curl, and tar before
running. Allow package downloads from the Python package index for the private
virtual environment. Copy the reviewed source checkout to a directory readable
by this account. Log in directly as `loopback-lab` with a clean environment.
Do not run the harness with sudo, `bash -x`, or a terminal recorder.

From that checkout, run:

```sh
LOOPBACK_LAB_USER=loopback-lab bash scripts/remote-lab-smoke.sh
```

The runner refuses root, `jq`, any account other than `loopback-lab`, a host
name other than `ora8`, and a supplied `SUDO_USER`. It checks port 3147 before
installation and stops if it is occupied. It never selects another port or
stops the current occupant. The free-port check cannot reserve the port across
installer startup. A concurrent process can still claim it and cause a failure.
Do not run multiple copies concurrently.

The run uses a new private temporary directory for configuration, state, data,
cache, runtime files, CLI wrappers, backups, and a real virtual environment.
It passes local ingress, no-service, and no-autostart to the installer, and
forces the existing `none` service manager for all CLI subprocesses. It clears
inherited environment variables except PATH and sets a minimal lab environment.
The account's real home remains its workspace because that is how Loopback's
identity and execution policy operate. This is account isolation, not a VM or
a filesystem sandbox. Use a disposable account with no valuable home content.

The following paths and checks run against the actual installed gateway:

- Fresh checkout installation through `install.sh`, with dependency installation.
- HTTP health and an IPv4 listener check for the lab UID on `127.0.0.1:3147`.
- Authenticated MCP initialization and a real `pwd` tool call.
- HTTP dynamic client registration, consent with PKCE, token exchange, refresh,
  and MCP tools listing for public, Basic-secret, and form-secret clients.
- Wrong Basic secret rejection, mixed-grant narrowing, and unsupported-only
  grant rejection.
- One-time HTTP password enrollment, password login, and enrollment replay rejection.
- Repeat installation through `runtime/cli.py`, preserving the machine recovery
  token and OAuth signing key.
- Deep doctor and sanitized report commands.
- Same-source `upgrade` through the installed CLI, checking its archive backup.
- Actual runtime `rollback`, checking the same two credentials remain intact.
- Shutdown and a check that the lab listener releases its port.

Each CLI command has a four-minute limit. The run has a twenty-minute alarm.
Slow package downloads can exceed a limit and fail the run. Command output,
HTTP bodies, credentials, enrollment links, and exception details are suppressed.
The console prints check names and the path of a private redacted JSON result
in the lab account's home directory. The JSON records checks completed before
failure, overall success, failure class, and cleanup status. It contains no
credential values or hashes. Hand this JSON to the owner for review.

Cleanup checks both the process UID and its command line against the private
configuration directory before sending a signal. It removes only its own run
directory. It never uses `pkill`, system services, Tailscale, Funnel, Serve,
ngrok, cloudflared, or route configuration. If cleanup cannot verify ownership,
it leaves the private run directory and identifies it in the result for the
owner to inspect. SIGKILL, a host crash, and storage failure can prevent cleanup
and result writing.

This does not test release-download bootstrap, archive or PowerShell installers,
older-version migrations, provider connector acceptance, a browser UI, public
TLS, tunnels, or cross-host access. The updater redeploys the same source and
rollback restores that runtime snapshot. It does not prove compatibility with
another release. No provider account or external route is required.
