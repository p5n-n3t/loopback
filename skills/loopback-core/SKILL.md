---
name: loopback-core
description: Use Loopback MCP tools to inspect files, run shell commands, manage background jobs and persistent terminals, and read process/system state on the local machine connected through Loopback. Use for everyday one-machine filesystem, shell, job, terminal, and document tasks.
---

# Loopback core tools

Loopback is a self-hosted MCP control plane. This skill covers the tools you
reach for on a single connected machine: filesystem, shell, background jobs,
persistent terminals, process inspection, and structured document editing.
For routing work to other registered machines, see the `loopback-fleet`
skill. For dashboard, auth, and policy administration, see `loopback-admin`.

## Before you start

Every tool call is evaluated against the server's own policy, independent of
whatever the calling client allows. A denied path, blocked command, or
approval requirement is not a bug to work around — it is the machine owner's
decision. Never suggest editing Loopback's config or policy files as a way to
get past a denial inside the same task that hit it; tell the user what was
blocked and why, and let them change policy deliberately through the
dashboard if they choose to.

## Filesystem

Prefer read-only inspection tools before any write. Structured document tools
(DOCX text read/replace, XLSX range read/write, PDF text/merge/page-extract)
exist so you do not have to shell out to convert office documents — use them
when the file type matches.

## Shell execution

- One-shot execution is for short, bounded commands with a timeout.
- Use a background job for anything long-running or non-interactive; poll
  its output with an offset instead of re-fetching the entire log each time.
- Use a persistent terminal when state (an open REPL, an interactive
  wizard, environment built up across calls) must survive between calls.
  Read terminal output after writing to it rather than polling in a tight
  loop.
- Treat shell execution and process signaling as side-effecting actions:
  confirm the exact command before running anything destructive, and prefer
  the narrowest command that answers the question.

## Handling policy responses

A tool error carrying `approval_required:<id>` means the command matched a
pattern that needs a human decision. Explain what the command would do and
that it is pending in the dashboard's Security → Approvals view; do not
retry with a rephrased command to see if it slips through, and never advise
disabling the sandbox or policy to make the error disappear.

A hard-denied command (destructive patterns like recursive delete of a root,
disk-wipe utilities) has no approval path at all — it is refused
unconditionally. Do not attempt an equivalent command through a different
tool or encoding.

## See also

- `docs/TOOLS.md` — full MCP tool catalog with scopes and annotations.
- `docs/PERMISSIONS.md` — profiles, sandbox backends, and approval model.
- `docs/USAGE.md` — day-to-day CLI and tool usage walkthrough.
