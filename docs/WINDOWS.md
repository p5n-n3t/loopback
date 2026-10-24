# Windows

Loopback installs and runs on Windows 10/11 (x86_64), with several
capability differences from Linux/macOS that are worth understanding before
you rely on it for anything sensitive.

## Installing

```powershell
irm https://raw.githubusercontent.com/p5n-n3t/loopback/main/install.ps1 | iex
```

`install.ps1` offers to install Python via `winget` if no suitable Python
is found, then delegates to the same `cli.py install` every platform uses.
See [INSTALL.md](INSTALL.md) for shared flags.

## No OS sandbox for shell commands

This is the most important difference. There is no Landlock/bwrap/Seatbelt
equivalent wired up for Windows, so `NoneBackend` is always selected:
shell commands (`execute`, `job_start`, PTY input) run with the full rights
of the service account, with **no kernel-level containment** — only
Loopback's own server-side path/command policy applies.

Practically: do not run Loopback as an administrator account on Windows,
keep the policy profile tight (`restricted` rather than `admin`/`trusted`
unless you specifically intend to administer the machine this way), and
treat `allow_shell` on Windows as "this account's full reach," not "a
contained subset of it." File tools (`read_file`/`write_file`/`list_dir`/
etc.) are still fully enforced server-side regardless of sandbox
availability.

`sandbox_required` profiles (`read-only`, `restricted`, `development`) will
refuse to run shell commands on Windows unless you explicitly set
`sandbox.acknowledge_unenforced: true` — this is a deliberate opt-in risk
acceptance, not something to flip reflexively to silence the refusal.

## No persistent PTY terminals

`terminal_start` and friends are not available — there's no PTY to attach
on Windows. One-shot `execute` and background `job_start`/`job_output` work
normally.

## Autostart

Uses a Task Scheduler task named `Loopback`, triggered at logon, running
with the current user's rights (not SYSTEM). `loopback autostart
[on|off|status]` manages it.

## Config directory

`%LOCALAPPDATA%\Loopback` unless `LOOPBACK_CONFIG_DIR` is set. See
[CONFIGURATION.md](CONFIGURATION.md).

## Ingress

Tailscale and Cloudflare Tunnel both ship Windows clients and work the same
way as on other platforms; see [TAILSCALE.md](TAILSCALE.md) and
[CLOUDFLARE.md](CLOUDFLARE.md).

## See also

- [PLATFORMS.md](PLATFORMS.md) — the full platform comparison table.
- [PERMISSIONS.md](PERMISSIONS.md) — the `none` sandbox backend in the
  broader sandbox-backend context.
