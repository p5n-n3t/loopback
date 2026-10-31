# Loopback Coding Capability Test Report

> **Prepared by Claude (Anthropic)** from live tool calls made through the user's Loopback Coding connection.
> Date of run: 2026-10-31 · Host: `jq-xubuntu1` · Loopback version 4.0.0 · Principal: `Claude` (OAuth)

**Scope note.** This report covers only the tools available in the conversation. It does not claim exhaustive coverage and does not guarantee future authorization. A blocked or untested operation is not counted as a pass. Nothing here shows that any third-party safety check was disabled.

**Test directory:** `/home/jq/loopback-captest-20261031-a7f3`
(A copy of the short on-host report is at `REPORT.md` inside that directory.)

---

## 1. Summary

| Result | Count / detail |
|---|---|
| Tool families fully exercised | Inventory, file ops, command execution, git, jobs, processes, terminals, DOCX / XLSX / PDF, SSH targets, one ora8 command |
| Unavailable | Browser automation and desktop automation (both report `available: false`) |
| Not applicable | `node_*` remote tools (only the local node is registered) |
| Not tested | Protected-path denial, output truncation above 20 KB, `approval_id` flows, token refresh timing, all dashboard UI flows |
| Approval, reconnect or auth prompts | None appeared during the run |
| Tailscale interactive check | None appeared |

**Headline findings**

1. Browser and desktop automation are enabled by policy but **unavailable** on this machine.
2. The Landlock sandbox blocks `/home/linuxbrew`, so **`brew` and `zplug` fail in sandboxed shells**.
3. `job_list` exposes **all principals' jobs**, including full command text.
4. **Remote execution has no OS sandbox**, only a gateway path check.
5. All worker heartbeats are **stale** while SSH probes are fresh, so status reporting is easy to misread.

---

## 2. Authorization and capability inventory

### Authentication and identity

| Item | Value |
|---|---|
| Authentication kind | `oauth` |
| Granted scopes | `loopback:exec`, `loopback:fleet`, `loopback:read`, `loopback:write` |
| Machine | `jq-xubuntu1` (physical), Ubuntu 26.04.1 LTS, x86_64 |
| User / shell | `jq` / `/usr/bin/zsh` |
| Initial working directory | `/home/jq` |
| Gateway | `127.0.0.1:2026`, public host `loopback.ziopsyop.tech` (Cloudflare tunnel, access policy `oauth`) |
| Runtime | Python 3.14.7, git 2.53.0, cloudflared 2026.10.7, tailscale 1.104.4 |

### Policy and sandbox

| Item | Value |
|---|---|
| Profile | `admin` ("Authorized server administration") |
| Approval mode | `hard-only` (no soft approval prompts; hard-deny rules still apply) |
| Allowed roots | `~` → `/home/jq`, read/write |
| Denied roots | none; all other locations denied |
| Shell | enabled |
| Process control | enabled |
| Fleet | enabled |
| Sandbox backend | Landlock, ABI 8, available and **active** |
| Sandbox required | `false` (but command results reported `sandbox: landlock`, `enforced: true`) |
| Documented caveat | Landlock cannot subtract a denied subtree from an allowed tree; such trees are granted child-by-child, so a file created directly in that top-level directory becomes accessible at the next command |
| Pending approvals | 0 |
| Limits | command timeout 86400 s, max output 16,777,216 bytes |

### Topology snapshot (from `connections`)

- Clients: 2 OAuth clients, 0 API tokens, **36 active OAuth grants**, legacy machine-token MCP path **still enabled**. Recent principals: Claude, ChatGPT, legacy machine token.
- Ingress: Cloudflare Tunnel, exposure `public`, `tunnel_id: null`, `credential: null`.
- Registered SSH targets (all via Tailscale, `key_configured: false`): aws1, aws2, ora1, ora3, ora5, ora6, ora7, ora8 (port 6722).
- MCP service: `ora1-system` (stdio, port 3031, status `stale`).
- Worker status for the gateway and every SSH host: `stale` (heartbeats about 80 minutes old at snapshot time).
- SSH edges: all `healthy`, latency about 2.2–3.5 s.
- `discovery_generated_at: null`, meaning no discovery run was observed.
- Shield: state `ok`, 0 blocked events in 24 h.

---

## 3. Per-tool coverage table

| Tool | Status | Notes |
|---|---|---|
| `machine_info` | PASS | |
| `permissions` | PASS | |
| `policy_status` | PASS | |
| `diagnostics` | PASS | |
| `connections` | PASS | Observation snapshot with stated 120 s staleness window |
| `execute` | PASS | cwd, stdout/stderr split, exit 1, timeout, 20 KB output, space in filename, curl |
| `write_file` | PASS | Create, empty file, append, Unicode, space in filename |
| `read_file` | PASS | Missing file returned `not_found: <path>` (EXPECTED ERROR) |
| `patch_file` | PASS | Precise patch OK; non-matching patch rejected (EXPECTED ERROR); backup `.bak.<epoch>` created next to the file |
| `list_dir` | PASS | |
| `search_text` | PASS | Found known text in file and its backup |
| `tail_file` | PASS | |
| `git_info` | PASS | Branch `main`, no commits, no remotes |
| `job_start` | PASS | |
| `job_output` | PASS | Incremental `offset` / `next_offset` worked |
| `job_list` | PASS | Returned jobs from all principals (see findings) |
| `job_stop` | PASS | `returncode: -15` |
| `process_list` | PASS | Filter by substring |
| `process_kill` | PASS | Only against a PID created by this test |
| `terminal_start` | PASS | |
| `terminal_write` | PASS | |
| `terminal_read` | PASS | Includes raw escape / OSC sequences |
| `terminal_resize` | PASS | `stty size` returned `40 120` |
| `terminal_close` | PASS | `alive: false` |
| `docx_text` | PASS | |
| `docx_replace_text` | PASS | 1 replacement into a new output file |
| `xlsx_read_range` | PASS | |
| `xlsx_write_range` | PASS | Wrote to a new copy |
| `pdf_text` | PASS | |
| `pdf_extract_pages` | PASS | |
| `pdf_merge` | PASS | |
| `ssh_target_list` | PASS | Read-only |
| `ssh_execute` | PASS | ora8 only |
| `node_list` | PASS | Only the `local` node registered |
| `node_call`, `node_diagnostics`, `node_execute`, `node_read_file` | NOT TESTED | No remote Loopback node registered to target |
| `browser_status` | PASS (reports unavailable) | `available: false`, `executable: null`, `enabled_by_policy: true` |
| `desktop_status` | PASS (reports unavailable) | `available: false`, `display: null`, `wmctrl: null`, `xdotool` and screenshot program present |
| `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_get_url`, `browser_close` | UNAVAILABLE | Adapter not available; not called to avoid any existing session |
| `desktop_screenshot`, `desktop_windows`, `desktop_activate_window`, `desktop_click`, `desktop_key`, `desktop_type` | UNAVAILABLE | No display visible to the gateway; not called to avoid a personal session |
| `execute` / `job_start` / `terminal_write` / `ssh_execute` with `approval_id` | NOT TESTED | `admin` profile never produced an approval request |

---

## 4. Test details

### 4.1 File operations

- Created the directory with plain `mkdir` (fails if the name exists, so uniqueness was verified).
- Created `notes file.txt` (space in name) with Unicode content, an empty `empty.txt`, appended a line, read both back.
- Patched `Line two: second line` to `Line two: PATCHED line ✎`; content verified afterwards.
- Backups were created inside the test directory automatically (`create_backup` default true).
- Search found `marker-needle-42` in the file and in its backup; `tail_file` returned the last 2 lines.

**Exact error messages**

```
patch_file (non-matching):
Error executing tool patch_file: ValueError: old_text was not found; read the file first and copy the exact text

read_file (missing):
Error executing tool read_file: not_found: /home/jq/loopback-captest-20261031-a7f3/does-not-exist.txt
```

Existing content was confirmed unchanged after the failed patch.
**Protected-path enforcement: NOT TESTED.** No credential files were read and no supported safe self-test was available.

### 4.2 Coding and command execution

- Created `mathutil.py` and `test_mathutil.py`. First run: 7 tests OK. After editing `clamp` (inverted-bounds `ValueError`) and adding a test: 8 tests OK.
- Explicit cwd confirmed (`pwd` matched the test directory).
- stdout and stderr were returned separately; `unittest` writes its report to stderr by design.
- A harmless nonzero exit (`false`) returned `returncode: 1` and was not treated as an auth failure.
- Timeout: `sleep 30` with `timeout: 2` returned `timed_out: true`, `returncode: null`, about 2 s. This result omitted the `sandbox` and `enforced` fields present on other results.
- Output handling: a 20,000-byte output returned in full with `truncated: false`. (Two earlier attempts piped through `tail` or `wc` and did not exercise this; only the direct `cat` counts.) Truncation behaviour above 20 KB is untested.

### 4.3 Git (inside the test directory only)

- `git init`, staged only `mathutil.py` and `test_mathutil.py`, status and staged diff shown (2 files, 55 insertions).
- `git_info` reported branch `main`, `head: null`, no remotes.
- No commit, no global configuration change, no push.

### 4.4 Jobs and processes

| Job ID | Purpose | Outcome |
|---|---|---|
| `6f25f92a02a2` | Four progress markers, then `exit 7` | Finished; all markers retrieved; `returncode: 7` |
| `2ca3828167a5` | Waiting job (`sleep 300`) | Output retrieved incrementally; stopped with `job_stop`; `returncode: -15` |
| `3c8b7f36e9d1` | `exec sleep 297` (PID 56460) | Located with `process_list`, ended with `process_kill` (TERM); `job_output` later confirmed `running: false`, `returncode: -15` |

Only processes created by this test were signalled. The Loopback server and unrelated processes were not touched.

### 4.5 Terminals

- Terminal `8df498f1bfe1` (zsh) started in the test directory.
- `pwd` matched, marker `TERM-MARKER-42` printed, `ls "notes file.txt"` worked.
- Delayed output (`sleep 3; echo DELAYED-MARKER-OK`) was absent on the first read and present on the next.
- Resize to 120×40 was confirmed by `stty size` → `40 120`.
- Closed; `alive: false`; no process remained.

**Shell startup noise (recorded separately; shell config was not edited):**

```
/home/jq/.zshrc:108: permission denied: /home/linuxbrew/.linuxbrew/bin/brew
/home/jq/.zshrc:source:127: permission denied: /home/linuxbrew/.linuxbrew/opt/zplug/init.zsh
/home/jq/.zshrc:130..140: command not found: zplug   (repeated)
error: could not lock config file /home/jq/.gitconfig: File exists
```

The remaining startup output was a banner from the user's own prompt configuration. `terminal_read` also returned raw OSC/ANSI sequences (OSC 133 / 1337 shell integration, colour codes).

### 4.6 Document tools

No third-party libraries (`docx`, `openpyxl`, `reportlab`, `pypdf`, `PyPDF2`, `fitz`) were installed in system Python, and nothing was installed. Fixtures were built with the Python standard library (`zipfile` and a hand-written PDF). The Loopback tools themselves worked regardless, so they use their own bundled libraries.

| Tool | Verification |
|---|---|
| `docx_text` | Returned both paragraphs of `fixture.docx` |
| `docx_replace_text` | `OLDWORD` → `NEWWORD`, 1 replacement, written to `fixture_replaced.docx`; re-read confirmed; original preserved |
| `xlsx_read_range` | `A1:C2` returned `1, 2, "=A1+B1"` and `"alpha", "beta"` |
| `xlsx_write_range` | Wrote 2×3 block at `A3` (including `"ünï"` and `=B3*2`) to `fixture_written.xlsx`; re-read matched |
| `pdf_text` | 2 pages, text correct |
| `pdf_extract_pages` | Page 2 extracted to `fixture_page2.pdf` |
| `pdf_merge` | `[page2, original]` → 3 pages, order verified by text |

### 4.7 Network and remote access

- `curl https://example.com` through `execute`: **HTTP 200**, 577 bytes, **curl exit 0**, title `Example Domain`.
- `ssh_target_list` (read-only): 8 registered targets, all `healthy`, probes fresh. `ora2` and `ora4` are not registered and were never contacted.
- `ssh_execute` on `ora8`, command exactly `hostname && id`:
  - Result: `ora8` / `uid=1002(jq) gid=1002(jq) groups=1002(jq),27(sudo),106(docker)`
  - `returncode: 0`, `duration_ms: 2647`, `timed_out: false`
  - Reported enforcement: **`gateway-path-check`** (gateway policy check, not an OS sandbox)
  - Transport: SSH over Tailscale (from the registered target record). No interactive Tailscale check was requested.
  - A cached healthy status was not treated as success; the command itself ran and returned output.

### 4.8 Topology, discovery and optional capabilities

Distinctions observed from the actual data:

| Distinction | Observation |
|---|---|
| Discovered candidate vs registered target | No discovery data (`discovery_generated_at: null`). Only registered targets observed |
| Registered target vs installed worker | 8 SSH targets registered; only the local node is registered as a Loopback node. Worker status for all is `stale` |
| Listening port vs verified MCP endpoint | `ora1-system` lists port 3031 but its note says status measures the worker process, **not** endpoint reachability. Not verified |
| Local service vs tunnel route | Gateway listens on `127.0.0.1:2026`; public route is a Cloudflare tunnel with `healthy` edge status |
| Ownership | The legend defines managed / external / unknown. No node reported an explicit ownership value in this snapshot |
| Current vs cached | `connections` is a cached snapshot (stale after 120 s). `ssh_target_list` probes were seconds old; worker heartbeats were about 80 minutes old |

No scanning, adoption, restart, reconfiguration or exposure of any service was performed.

**Browser and desktop**

```json
browser_status: { "available": false, "executable": null, "enabled_by_policy": true }
desktop_status: { "available": false, "platform": "Linux", "display": null,
                  "xdotool": "/usr/bin/xdotool", "wmctrl": null,
                  "screenshot_program": "/usr/bin/xfce4-screenshooter",
                  "reason": "native desktop automation currently requires Linux/X11 and xdotool",
                  "enabled_by_policy": true }
```

Neither was used. No existing personal session was accessed.

---

## 5. Findings and issues

| # | Finding | Impact |
|---|---|---|
| 1 | **Browser automation unavailable**: no executable found, although policy enables it | User expects browser automation; it cannot work as installed |
| 2 | **Desktop automation unavailable**: gateway service sees no `display`; `wmctrl` missing | Same. Likely needs the service to run with the session's `DISPLAY` / `XAUTHORITY` (a service change, not made here) |
| 3 | **`brew` and `zplug` fail under the sandbox** (`/home/linuxbrew` is outside the allowed root) | Sandboxed shells start with permission errors and no zplug plugins. Whether zplug works outside the sandbox was not tested |
| 4 | `.gitconfig` lock error during shell startup | Cause not investigated |
| 5 | `job_list` returns every principal's jobs with full command text (Claude, ChatGPT, legacy machine token) | Cross-principal information exposure, including remote command text |
| 6 | Job logs live under `~/.config/loopback/jobs/`, inside the allowed root | Any `execute`-capable principal can read other principals' logs |
| 7 | Worker heartbeats stale (about 80 min) while SSH probes are healthy | Status can mislead; "worker alive" and "reachable" are different things |
| 8 | 36 active OAuth grants; legacy machine-token path still enabled; tunnel node shows `tunnel_id: null` | Wide auth surface and incomplete tunnel metadata |
| 9 | Remote execution has no OS sandbox (`gateway-path-check` only); `jq` on ora8 is in `sudo` and `docker` groups | Remote commands carry far more reach than local ones |
| 10 | `patch_file` surfaces a raw Python `ValueError`; backups are created by default | Unpolished errors; clutter (and shows up as untracked files in git) |
| 11 | `terminal_read` returns raw ANSI/OSC sequences | Token waste and hard-to-read output |
| 12 | Timeout result lacks `sandbox` / `enforced` fields | Inconsistent result shape |
| 13 | Result shapes differ across tools (`error` strings vs structured objects) | Harder for clients to handle errors uniformly |

---

## 6. Recommendations to make Loopback market-ready

**Reliability and clarity**

1. **Explain "unavailable".** `browser_status` and `desktop_status` should state the concrete cause and fix (for example "service has no `DISPLAY`", "no Chromium or Playwright executable") and report whether the adapter is installed, enabled and usable as three separate states.
2. **Guided setup for automation.** A dashboard check that detects X11 / Wayland, offers to run the service with the right display variables, and installs or points to a browser adapter, with a one-click self-test.
3. **Consistent, structured errors.** Return `{code, message, hint}` for every failure; never leak Python exception names. Keep result shapes uniform (including timeout results).
4. **Backups opt-in or managed.** Store `patch_file` backups in a dedicated location, or default them off, with a retention limit.
5. **Clean terminal output.** Add a `strip_ansi` / `plain` option to `terminal_read` and drop shell-integration sequences by default.

**Security and privacy**

6. **Per-principal job isolation.** `job_list` and logs should be scoped to the calling principal by default, with command text redacted for others. Keep logs outside any sandbox-writable tree.
7. **Retire the legacy machine-token path** or gate it behind an explicit toggle with an expiry. Add grant expiry, a "revoke all" control and an audit view of the 36 current grants.
8. **Be explicit about remote enforcement.** Label remote execution as "gateway check only" in the dashboard and docs, and offer an optional remote sandbox or command allowlist per target.
9. **Fill in tunnel metadata** (tunnel ID, credential reference) and show tunnel health with timestamps.
10. **Run a safe protected-path self-test** that proves denial without touching real credential files.

**Developer experience**

11. **Sandbox allowlist for tool installs.** Let users add read-only roots such as `/home/linuxbrew` so shells, `brew`, and plugin managers like zplug load cleanly. Warn when shell init fails under the sandbox.
12. **Freshness everywhere.** Show the age of every status and provide a "verify now" probe; separate worker heartbeat, SSH reachability and MCP endpoint verification.
13. **Built-in `selftest` tool.** One command that exercises file, exec, job, terminal, document and network paths and reports PASS / DENIED / UNAVAILABLE in a standard format, so users do not need long manual scripts.
14. **Truncation transparency.** Document and test the output limits (stated max is 16 MB) and return `truncated` plus byte counts consistently.
15. **Pre-flight for approvals.** Provide a way to test the approval path (`approval_id`) safely in a non-admin profile.

---

## 7. Not tested and dashboard-only items

**Not tested in this run**

- Protected-path enforcement (no safe self-test available)
- Output truncation above 20 KB
- `approval_id` flows (the `admin` profile never prompted)
- Remote execution on any target other than `ora8`; `node_call`, `node_diagnostics`, `node_execute`, `node_read_file`
- `browser_*` and `desktop_*` action tools (unavailable)
- Whether zplug works outside the sandbox
- Token refresh over time: **UNVERIFIED** (this short run passing says nothing about refresh)

**Dashboard-only (MANUAL TEST), not claimed as verified by these MCP tests**

- Policy switching, tool selection and scope granting in the dashboard
- Worker installation or adoption
- Tunnel changes
- Upgrades and rollback
- Credential rotation
- Any dashboard button or UI flow

**Destructive or high-impact operations (separate controlled tests, not run on this live installation)**

- Deleting data, service restarts, reboots, deployments, commits or pushes
- Registering, removing or reconfiguring targets, workers or tunnels
- Changing OAuth settings or machine policy

---

## 8. Resources created and cleanup status

**Directory:** `/home/jq/loopback-captest-20261031-a7f3`

**Identifiers**

| Type | ID | State at end |
|---|---|---|
| Job | `6f25f92a02a2` | Finished, exit 7 |
| Job | `2ca3828167a5` | Stopped (-15) |
| Job | `3c8b7f36e9d1` | Process killed (-15), `running: false` |
| Terminal | `8df498f1bfe1` | Closed (`alive: false`) |
| Process | PID 56460 (`sleep 297`) | Terminated |
| Remote | `ora8` (single command) | Completed, no residue created |

**Files left for inspection (19 entries at last listing, plus `REPORT.md`)**

`.git/`, `__pycache__/`, `big_output.txt`, `empty.txt`, `example_body.html`, `fixture.docx`, `fixture.pdf`, `fixture.xlsx`, `fixture_merged.pdf`, `fixture_page2.pdf`, `fixture_replaced.docx`, `fixture_written.xlsx`, `make_fixtures.py`, `mathutil.py`, `mathutil.py.bak.1793428073`, `notes file.txt`, `notes file.txt.bak.1793428052`, `test_mathutil.py`, `test_mathutil.py.bak.1793428076`, and `REPORT.md`.

**Confirmation:** a process search for the test directory returned nothing, all three jobs report not running, and the terminal is closed. No test process remains.

**Final state:** OAuth scopes unchanged (`loopback:exec`, `loopback:fleet`, `loopback:read`, `loopback:write`); Landlock sandbox active and enforced; no reconnect or approval prompt appeared.

---

*Report written by Claude (Anthropic). All results come from actual tool calls in this conversation; items marked NOT TESTED, UNAVAILABLE or UNVERIFIED were not verified.*
