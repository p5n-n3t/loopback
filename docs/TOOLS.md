# MCP tools

All tools are served from a single `/mcp` endpoint (see
[AUTHENTICATION.md](AUTHENTICATION.md)). Each tool requires a scope, is
subject to the active policy profile and (for shell/process tools) the OS
sandbox (see [PERMISSIONS.md](PERMISSIONS.md)), and carries MCP annotations
(`read_only_hint`/`destructive_hint`/`idempotent_hint`/`open_world_hint`)
that are hints to the calling client, not an enforcement mechanism.
`tool_catalog()` returns this same information live, including whether each
tool is currently reachable on this server.

Call `machine_info`, `permissions`, and `connections` first — they tell you
the local account, home directory, permitted locations, and which targets
are local versus remote before you try anything else.

## Files (`loopback:read` / `loopback:write`)

| Tool | Scope | Summary |
|---|---|---|
| `read_file` | read | Read a UTF-8 text file with a line range. |
| `write_file` | write | Write or append UTF-8 text; overwrites unless `append=true`. |
| `list_dir` | read | List a directory's entries with type/size. |
| `patch_file` | write | Exact-text replace, with a timestamped `.bak` by default. |
| `search_text` | read | Recursive text search under a path, skipping caches/deps. |
| `tail_file` | read | Last N lines of a file. |
| `git_info` | read | Branch, status, and recent log for a local git repo. |

Prefer these over shell equivalents (`cat`, `sed`, `grep`) — they return
structured results and are each individually scoped/auditable rather than
hidden inside one `execute` call.

## System (`loopback:read`)

| Tool | Target | Summary |
|---|---|---|
| `diagnostics` | local | Health snapshot: disk, sandbox status, ingress, recent errors. |
| `machine_info` | local | OS, account, shell, config dir, virtualization. |
| `policy_status` | local | Current profile and resolved policy. |
| `permissions` | local or remote | Fully resolved effective permissions for a target. |
| `connections` | local or remote | Active MCP/dashboard sessions and fleet reachability. |

## Execution (`loopback:exec`)

| Tool | Approval | Summary |
|---|---|---|
| `execute` | policy-gated | Run one command, capture stdout/stderr, bounded timeout and output. |
| `job_start` | policy-gated | Start a background job; returns a `job_id`. |
| `job_list` | — | List background jobs and their state. |
| `job_output` | — | Read incremental job output by byte offset. |
| `job_stop` | — | Signal a background job. |
| `terminal_start` | — | Open a persistent PTY. |
| `terminal_write` | policy-gated | Send input to a PTY (goes through the same command policy as `execute`). |
| `terminal_read` | — | Read and optionally clear PTY output. |
| `terminal_resize` | — | Resize a PTY. |
| `terminal_close` | — | Close a PTY. |
| `process_list` | — | List processes, optionally filtered. |
| `process_kill` | — | Signal a process (refuses PID 1 and the Loopback server itself). |

Every one of these returns `approval_required:<id>` or a hard-deny refusal
instead of running when policy requires it — see
[PERMISSIONS.md](PERMISSIONS.md). An `approval_required` response is not a
bug to work around; it means a human needs to approve the action from the
dashboard's Security → Approvals view, or re-issue the call with the
`approval_id` once approved.

## Fleet (`loopback:fleet`, remote targets)

| Tool | Summary |
|---|---|
| `node_list` | List registered Loopback nodes (no secrets). |
| `node_call` | Invoke an arbitrary tool on a named node. |
| `node_read_file` | Read a file on a named node. |
| `node_execute` | Run a command on a named node (policy-gated on that node). |
| `node_diagnostics` | Diagnostics for a named node. |
| `ssh_target_list` | List registered SSH targets. |
| `ssh_execute` | Run a command on an SSH target (policy-gated locally before anything is sent over SSH). |

See [FLEET.md](FLEET.md) for registering nodes/targets and per-target
policy. Don't fan a call out across every node/target unless the user
explicitly asked for that.

## Documents (`loopback:read` / `loopback:write`)

| Tool | Summary |
|---|---|
| `docx_text` | Extract text from a Word document. |
| `docx_replace_text` | Exact-text replace in a Word document. |
| `xlsx_read_range` | Read a cell range from a worksheet. |
| `xlsx_write_range` | Write values into a cell range. |
| `pdf_text` | Extract text from a page range. |
| `pdf_merge` | Merge multiple PDFs into one output file. |
| `pdf_extract_pages` | Extract specific pages into a new PDF. |

These obey the same filesystem policy as the plain file tools. Prefer an
explicit `output_path` over in-place edits when formatting fidelity
matters.

## Browser automation (`loopback:exec`, disabled by default)

`browser_status`, `browser_open`, `browser_snapshot`, `browser_click`,
`browser_fill`, `browser_get_url`, `browser_close`. Requires
`allow_browser: true` and a separately installed `agent-browser`
executable. Can operate authenticated web sessions — treat it with the same
caution as shell execution.

## Native desktop automation (`loopback:exec`, disabled by default, Linux/X11 only)

`desktop_status`, `desktop_windows`, `desktop_screenshot`,
`desktop_activate_window`, `desktop_click`, `desktop_type`, `desktop_key`.
Requires `allow_desktop: true`. Keep disabled on headless or
infrastructure-only machines.

## See also

- [PERMISSIONS.md](PERMISSIONS.md) — policy profiles, approvals, sandbox.
- [AUTHENTICATION.md](AUTHENTICATION.md) — scopes and credential types.
- [FLEET.md](FLEET.md) — node/SSH-target tools in depth.
- [skills/loopback-core/SKILL.md](../skills/loopback-core/SKILL.md),
  [skills/loopback-fleet/SKILL.md](../skills/loopback-fleet/SKILL.md) —
  workflow guidance for an agent using these tools.
