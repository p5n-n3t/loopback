# LOOPBACK — CONSOLIDATED CAPABILITY TESTS & MARKET-READINESS REPORT
**Consolidated:** 2026-10-31  
**Host:** `jq-xubuntu1` · `jq` · Ubuntu 26.04.1 LTS · Loopback 4.0.0  
**Purpose:** Single, self-contained development handoff of all three saved ChatGPT Loopback test/audit reports, updated test status, exact noteworthy evidence and prioritized corrective actions.  
**Evidence cut-off:** The three saved source reports from 2026-10-31. This consolidation is **not a new capability retest**.  
**Evidence policy:** Every appendix below preserves an existing saved report verbatim; no claims of exhaustive system coverage, future authorization, deployment or verified dashboard UI behavior.

> **Final disposition: NOT MARKET-READY for general release advertising working browser and computer automation.** Core file/coding/process/document/fleet capabilities were directly exercised and mostly worked; browser and native desktop automation are not operational in the tested installation. The 52-tool inventory shows **36 PASS, 1 EXPECTED DENIAL, 2 UNAVAILABLE, 13 NOT TESTED**. “PASS” for an adapter's `*_status` tool confirms its diagnostic response only, not that automation works.

## 1. Evidence and timeline

| Phase | Original saved evidence | Scope |
|---|---|---|
| A — bounded capability test | `/home/jq/loopback-capability-test-ZsF7JZrX/FINAL_REPORT.md` | Live OAuth/policy, file/coding/Git, jobs/process, terminal, document, HTTPS, topology, SSH only to `ora8`, and 52-tool inventory |
| B — previously untested retest | `/home/jq/loopback-retest-sowVFYjM/RETEST_REPORT.md` | Admin-profile change, real browser_open error, X11 session vs service environment, zplug diagnosis, remote node_call expected denial |
| C — market-readiness code audit | `/home/jq/loopback-market-audit-peLeNTy4/chatgpt.md` | Real desktop_windows denial, source inspection, detached-HEAD Git conflict, four behavior reproductions, 11 isolated URL-validator tests, release blockers and acceptance gates |

**Source preservation:** Phase C's original audit file was saved as `FINAL_REPORT.md` during the run but was found under the name `chatgpt.md` at consolidation. The file's reported size was 18,201 bytes and its contents were read in full. The two earlier reports were read in full. Existing source and test files were not overwritten.

## 2. Authorization and baseline findings

| Field | Phase A original test | Phases B/C latest observation |
|---|---|---|
| Authentication | OAuth | OAuth |
| Granted OAuth scopes | `loopback:read`, `loopback:write`, `loopback:exec`, `loopback:fleet` | Same four scopes |
| Profile | `custom` | `admin` (changed between runs, not by these tests) |
| Shell approvals | `standard` | `hard-only` |
| Allowed root | `/home/jq` read/write | `/home/jq` read/write |
| Denied roots | Listed explicit protected credential/config paths | No denied roots configured in admin profile |
| Sandbox | Landlock ABI 8 active and required | Landlock ABI 8 active; profile does not require it |
| Local command enforcement | `os-sandbox`, `enforced=true` | Active on inspected local commands |
| Remote `ora8` enforcement | `gateway-path-check` | No new remote execution |
| Browser | Policy-enabled, adapter unavailable | Missing required `agent-browser` |
| Desktop | Policy-enabled, adapter unavailable | Live XFCE X11 session at `:0`; Loopback service lacks `DISPLAY` |
| OAuth refresh across expiration | Not verified | Still not verified |

No new-authentication or approval prompt was observed in these tool runs. That does **not** prove timed refresh, reconnect UI, or future authorization. Protected-path denial was not tested against credential files. No test contacted `ora2` or `ora4`.

## 3. Comprehensive 52-tool outcome matrix

The tool-count math intentionally distinguishes the real failures and expected refusals observed in later runs from previously untested status. PASS for `browser_status` or `desktop_status` means only that the **status endpoint** returned a valid response.

| Tool | Latest outcome | Evidence / limitation |
|---|---|---|
| `machine_info` | PASS | Identity and initial working directory confirmed |
| `permissions` | PASS | Actual OAuth scopes, permitted roots, approvals and sandbox reported |
| `policy_status` | PASS | Effective custom (first run), then admin (retest) profiles read |
| `diagnostics` | PASS | Loopback v4.0.0, local sandbox and machine diagnostics read |
| `connections` | PASS | Topology inventory obtained; cached/probed claims distinguished |
| `read_file` | PASS | UTF-8, Unicode, empty file, missing-file negative test |
| `write_file` | PASS | Create, overwrite, append, Unicode and filename containing spaces |
| `list_dir` | PASS | Controlled test directory contents and file types returned |
| `patch_file` | PASS | Precise edits, backups, missing-text negative test |
| `search_text` | PASS | Known anchor found, correct path and line |
| `tail_file` | PASS | Last lines matched expected appended text |
| `execute` | PASS | Unit tests, stdout/stderr, exit 7, timeout, large output, HTTPS request |
| `git_info` | PASS | Test repository branch and staged state; separate source conflict detected |
| `job_start` | PASS | Four test-run jobs and progress markers |
| `job_list` | PASS | Job inventory and final completed states |
| `job_output` | PASS | Progress/log markers, return codes and offsets |
| `job_stop` | PASS | Stop call succeeded; one job had already finished, so live stop not proven by this call |
| `terminal_start` | PASS | Test PTY opened in requested cwd |
| `terminal_write` | PASS | pwd and marker command accepted |
| `terminal_read` | PASS | pwd and delayed markers received; startup warnings observed |
| `terminal_resize` | PASS | PTY size changed |
| `terminal_close` | PASS | Test terminal reported alive=false |
| `process_list` | PASS | Test marker located exact PID 22205 |
| `process_kill` | PASS | TERM sent to test PID 22205; exit -15 verified |
| `node_list` | PASS | Only local MCP node registered |
| `ssh_target_list` | PASS | Registered target metadata listed; not execution on other hosts |
| `ssh_execute` | PASS | Only ora8: `hostname && id`, exit 0 via Tailscale SSH |
| `docx_text` | PASS | Original and edited synthetic fixture text read |
| `docx_replace_text` | PASS | One replacement saved to edited.docx and verified |
| `xlsx_read_range` | PASS | Fixture A1:B2 original and updated values read |
| `xlsx_write_range` | PASS | Two cells written in edited.xlsx, readback verified |
| `pdf_text` | PASS | One-page and merged two-page synthetic documents read |
| `pdf_merge` | PASS | Two one-page inputs produced a verified two-page output |
| `pdf_extract_pages` | PASS | Page 1 extracted to output file |
| `browser_status` | PASS | Status call worked; returned available=false, executable=null |
| `desktop_status` | PASS | Status call worked; returned available=false, display=null |
| `node_call` | EXPECTED DENIAL | Called with node=local; correctly rejected because tool is only for remote nodes |
| `node_read_file` | NOT TESTED | No registered remote Loopback MCP node and no additional remote authorization |
| `node_execute` | NOT TESTED | No registered remote Loopback MCP node and no additional remote authorization |
| `node_diagnostics` | NOT TESTED | No registered remote Loopback MCP node and no additional remote authorization |
| `browser_open` | UNAVAILABLE | Actual dedicated-session call failed: agent-browser executable not installed |
| `browser_snapshot` | NOT TESTED | Opening dedicated browser fixture failed |
| `browser_click` | NOT TESTED | No isolated browser session or element refs |
| `browser_fill` | NOT TESTED | No isolated browser session or element refs |
| `browser_get_url` | NOT TESTED | No isolated browser session |
| `browser_close` | NOT TESTED | No browser session was successfully created |
| `desktop_windows` | UNAVAILABLE | Actual call returned runtime error; no DISPLAY in service environment |
| `desktop_screenshot` | NOT TESTED | Desktop adapter unavailable; no personal screen capture authorized |
| `desktop_activate_window` | NOT TESTED | No isolated desktop test window |
| `desktop_click` | NOT TESTED | No isolated desktop test window |
| `desktop_type` | NOT TESTED | No isolated desktop test window |
| `desktop_key` | NOT TESTED | No isolated desktop test window |

## 4. Exact high-signal test results

### 4.1 File operations (Phase A)
- Created `/home/jq/loopback-capability-test-ZsF7JZrX` via `execute(command="mktemp -d /home/jq/loopback-capability-test-XXXXXXXX",cwd="/home/jq",timeout=20)` → exit `0`, Landlock `enforced=true`.
- Wrote `notes with spaces.txt` containing `Alpha café — Ελληνικά — مرحبا — 日本語` and marker `SEARCH_ANCHOR_7C29`; appended `Appended marker END_902`. Read/list/search/tail returned expected contents and file metadata. Wrote and read a zero-byte `empty.txt`.
- Patched `multiline.txt` from `Second line` to `Second line PATCHED` with `create_backup=true`; result `replacements=1` and backup `multiline.txt.bak.1793420878`.
- Negative read: `read_file(path="/home/jq/loopback-capability-test-ZsF7JZrX/no-such-test-file.txt")` → exact core error `Error executing tool read_file: not_found: /home/jq/loopback-capability-test-ZsF7JZrX/no-such-test-file.txt`.
- Negative patch: `patch_file(path=".../multiline.txt",old_text="A deliberately missing string ZZZ_901",new_text="SHOULD_NOT_APPEAR",create_backup=false)` → `ValueError: old_text was not found; read the file first and copy the exact text`. Readback confirmed no mutation. These are **expected input errors**, not authentication failures.

### 4.2 Coding, execution and Git (Phase A)
- Wrote `mathutil.py` and `test_mathutil.py` with numeric cases, negative, zero, fraction, huge integer and invalid `None`: `python3 -B -m unittest -v test_mathutil.py` → **6/6 passed**, exit `0`.
- Changed function to reject bool inputs; changed test file accordingly; rerun → **7/7 passed**, exit `0`. Two backup files remain within test directory.
- `execute` correctly separated `STDOUT_MARKER_C8` from `STDERR_MARKER_C8`, returning synthetic exit `7`. Space-containing filename read successfully.
- Short bounded sleep succeeded; `time.sleep(2)` with `timeout=1` returned `returncode=null`, empty stdout/stderr and `duration_ms=1572` — no explicit timeout flag in that response. Controlled output of **1,013 characters** was returned untruncated.
- Initialized test-only Git `main`; staged **only** `mathutil.py` and `test_mathutil.py`. Staged diff: **36 insertions across two files**. `git_info` showed no commits and no remotes. No commit/push.

### 4.3 Background jobs, process and terminal (Phase A)
| Identifier | Evidence | Final observed status |
|---|---|---|
| Job `6da64e72fb7c` | `PROGRESS_MARKER_0` … `PROGRESS_MARKER_3`, `PROGRESS_COMPLETE` | Finished, exit 0 |
| Job `1f8106aa123d` | Waiting marker, `job_stop(signal_name="TERM")` | Finished, reported exit 0; **may already have finished** before stop, not proof of live termination |
| Job `15b1b6b497fb` | `PROCESS_WAIT_MARKER_73C pid=19525` | Finished naturally, exit 0 |
| Job `cb8af8ff457d` | Exact created PID `22205`; `process_kill(pid=22205,signal_name="TERM",tree=true)` | Finished, exit **-15**, valid live process termination |
| Terminal `5bc07ae46ab8` | cwd, `TERMINAL_MARKER_7C29`, delayed marker, PTY resize | `terminal_close` reported `alive=false` |

The interactive terminal also printed exact startup warnings:
```text
jq/.zshrc:136: command not found: zplug
/home/jq/.zshrc:140: command not found: zplug
error: could not lock config file /home/jq/.gitconfig: File exists
```
(The first warning's path formatting differed slightly in the underlying PTY capture.) Those warnings were *not* an error in the tested `pwd` or marker commands. Shell configuration was not edited.

### 4.4 Synthetic document tools (Phase A)
| Operation | Actual input/result |
|---|---|
| DOCX fixture | `fixture.docx` text: `DOCX fixture hello` |
| DOCX replace | `docx_replace_text` produced `edited.docx`; reread: `DOCX edited successfully` |
| XLSX fixture | Sheet `Fixture`, `A1:B2` → `[["Hello",42],[null,null]]` |
| XLSX edit | Wrote `[["Edited",99]]` starting `A2`; reread `[["Hello",42],["Edited",99]]` |
| PDF text | `fixture.pdf`: one page containing `PDF fixture hello` |
| PDF merge | `merged.pdf`: two pages, both with expected text |
| PDF extract | `extracted.pdf`: page 1 successfully extracted |

Fixtures were constructed using Python's standard library without installing dependencies.

### 4.5 Network, fleet and topology (Phase A)
- `execute(command="python3 -B -c 'import urllib.request; r=urllib.request.urlopen(\"https://example.com\",timeout=8); print(\"HTTP_STATUS\",r.status)'",cwd=test_directory)` → `HTTP_STATUS 200`, command exit `0`.
- `ssh_target_list` listed `aws1`, `aws2`, `ora1`, `ora3`, `ora5`, `ora6`, `ora7`, `ora8` **as registered target metadata**. No command executed on these other than `ora8`.
- Authorized `ssh_execute(target="ora8",command="hostname && id",timeout=20)` → exit `0`; stdout:
```text
ora8
uid=1002(jq) gid=1002(jq) groups=1002(jq),27(sudo),106(docker)
```
Transport SSH via Tailscale; enforcement reported as `gateway-path-check`, **not remote OS sandbox**.
- `node_list`: only registered `local` MCP node; remote SSH targets are not remote MCP nodes.
- `connections`: local `127.0.0.1:2026/mcp`, Cloudflare public tunnel `loopback.ziopsyop.tech`, recent healthy ingress and SSH probes; statuses were observed metadata, not independent validation of every service. `ora1-system` indicated a local stdio MCP service with untested/stale status; neither listening port nor an observed heartbeat establishes verified endpoint reachability.

### 4.6 Browser and desktop retrials (Phases B and C)
```text
browser_status({}) -> {"available":false,"executable":null,"enabled_by_policy":true}
browser_open(url="https://example.com",session="loopback-retest-sowVFYjM")
-> INVALID_ARGUMENT / RuntimeException:
   Error executing tool browser_open: RuntimeError:
   agent-browser is not installed; install it separately before enabling browser tools
```
Chrome `/usr/bin/google-chrome` and Firefox `/snap/bin/firefox` were found; presence of browsers does not satisfy the missing `agent-browser` CLI.

```text
desktop_status({}) ->
  available=false; display=null; xdotool=/usr/bin/xdotool;
  wmctrl=null; screenshot_program=/usr/bin/xfce4-screenshooter;
  enabled_by_policy=true
desktop_windows(limit=1) ->
  INVALID_ARGUMENT / RuntimeException:
  Error executing tool desktop_windows: RuntimeError:
  native desktop automation currently requires Linux/X11 and xdotool
```
`loginctl show-session 2` separately showed `jq`, `Display=:0`, `Desktop=xfce`, `Type=x11`, `Active=yes`. The Loopback command environment lacked `DISPLAY`, `XDG_SESSION_TYPE` and `XAUTHORITY`. No private screen was captured and no personal window was controlled.

### 4.7 Zplug retest (Phase B)
- `/home/linuxbrew/.linuxbrew/opt/zplug` existed as a directory, but `zplug` was not in the tested `PATH`; `zsh -fc "whence -v zplug"` returned `zplug not found`.
- Clean-shell `ZPLUG_HOME` and `ZPLUG_ROOT` were unset. An attempt to inspect the installed Homebrew prefix returned **`PermissionError: [Errno 13] Permission denied: '/home/linuxbrew/.linuxbrew/opt/zplug'`** within Landlock. Inspection was stopped rather than changing permissions or rerouting.
- **Conclusion:** installation directory present, but the function/init cannot currently be resolved inside the tested environment. Do not claim zplug is uninstalled.

### 4.8 Remote-node expected denial (Phase B)
`node_call(node="local",tool="machine_info",arguments={},timeout=12)` →
`ValueError: node_call is for remote nodes; call local tools directly`. This is an **EXPECTED DENIAL**, not a connection failure. Other remote-node actions remain NOT TESTED.

### 4.9 Source-code audit and isolated tests (Phase C)
- `git_info(path="/home/jq/loopback")` during the audit showed detached `HEAD` at `38ee3e3` and **`UU runtime/server.py`**. No conflict resolution attempted; production compatibility remains blocked pending a clean source tree.
- Four isolated behavior reproductions passed, showing that existing browser prefix validation can accept `http://localhost.example.invalid/`, status checks can mistake executable presence for health, and desktop status can mistake a fictitious `DISPLAY` value for a working GUI. One normal external HTTP rejection was also confirmed. Passing these reproductions demonstrates **problems**, not safe behavior.
- Proposed replacement `url_validator.py` and `test_url_validator.py` in `/home/jq/loopback-market-audit-peLeNTy4`: **11/11 tests passed** after correcting the control-character test fixture. These files are **not integrated**, and no production/browser/runtime deployment was performed.
- The original source audit was saved with broader P0/P1/P2 recommendations, preserved verbatim in Appendix C.

## 5. Cleanup, artifacts and reporting limitations

**Phase A test directory:** `/home/jq/loopback-capability-test-ZsF7JZrX`. The original run's final inventory contained `.git`, source and test Python files, three timestamped backup files, Unicode/empty/multiline text fixtures, synthetic source and edited DOCX/XLSX/PDF files, `make_fixtures.py` and `FINAL_REPORT.md`. Four test-created jobs reported `running=false`; test PTY reported `alive=false`. Only the two Python source files remained Git-staged. Original files deliberately left for inspection.

**Phase B directory:** `/home/jq/loopback-retest-sowVFYjM`: `diagnostic.py`, `supplemental_diagnostics.py`, `RETEST_REPORT.md`; no active test job or terminal reported.

**Phase C directory:** `/home/jq/loopback-market-audit-peLeNTy4`: `adapter_behavior_repro.py`, `url_validator.py`, `test_url_validator.py`, report now named `chatgpt.md`. No test-created jobs, terminals, installation, Git changes or deployments.

No reconnect or approval prompt was observed in these tests. A prior reconnect/sign-up URL issue was reported in conversation, but was **not reproduced or verified here**. OAuth timed refresh, actual dashboard button flows, protected-subtree denial, malicious shell bypass attempts, full unattended browser/desktop automation, worker installation, tunnel changes, upgrades, backup/restore and destructive tests remain **unverified/manual/controlled**.

**Important evidence limit:** This document consolidates *all three saved report texts in full* plus the explicit high-signal call evidence preserved in the conversation. It is **not a full raw MCP event archive or a guarantee that every RPC request and response byte from prior turns was logged**. No such complete raw trace was available for this consolidation.

## 6. Prioritized market-readiness decision

**P0 — must fix before marketing browser/desktop automation**
1. Resolve the detached-HEAD/`runtime/server.py` conflict on a controlled branch, then verify a clean tagged release candidate, with CI green. Do not blindly reset, force overwrite or deploy an untested tree.
2. Make `agent-browser` an explicit pinned, verified optional install; validate its engine/runtime and capability with an **isolated, per-principal browser fixture**. Do not use a personal profile. Fix HTTP localhost prefix-check validation and review SSRF and session ownership.
3. Connect the Loopback service to an authorized interactive X11 user session through a supported broker, rather than trusting `DISPLAY` or guessing `:0`. Validate X11 accessibility, lock state and user authorization. Test click/type/key/windows/screenshot **only** on synthetic consented fixtures.
4. Correct zplug initialization in interactive zsh without disabling Landlock or broadly opening the Homebrew prefix. Keep noninteractive command execution independent of interactive shell plugin noise.
5. Change shipped secure defaults away from permissive `admin`; require least-privilege roots, protected subpaths and fail-closed sandbox operation. Verify fresh consent, OAuth reconnect, real expiry/refresh rotation and meaningful user-facing errors.

**P1 — harden**
- Live health probes (not executable-existence probes), structured error categories, explicit dependency remediation in dashboard, owned-session cleanup, per-principal quotas and isolation, audit/redaction, cross-platform tests, upgrade/rollback, threat review, support bundle and current-vs-cached topology signals.

**P2 — optimize**
- Measure p50/p95/p99 for full MCP/browser/GUI action chains, optimize shell initialization and Landlock policy building without weakening enforcement, lazy-load optional integrations, segment tools by actually usable capability, and improve dashboard usability and accessibility.

**Release decision:** A complete healthy MCP gateway is not equivalent to healthy desktop/browser automation. Hold marketing claims until the nine gates in Appendix C (clean source/CI, installer health, browser fixture, X11 fixture, zplug, OAuth lifecycle, regression coverage, threat review, and published compatibility/support docs) have evidence of passing.

---

# Appendix A — COMPLETE ORIGINAL PHASE A REPORT (VERBATIM)

**Origin:** `/home/jq/loopback-capability-test-ZsF7JZrX/FINAL_REPORT.md`

# Loopback Coding capability test — 2026-10-31
Test directory: /home/jq/loopback-capability-test-ZsF7JZrX
Machine: jq-xubuntu1; OS Ubuntu 26.04.1; Loopback 4.0.0; user jq; shell /usr/bin/zsh; initial cwd /home/jq.
OAuth kind: oauth; scopes: loopback:read, loopback:write, loopback:exec, loopback:fleet. Custom profile, standard shell approval, process/fleet enabled.
Sandbox: local landlock active, ABI 8, os-sandbox enforced=true on successful execute calls. Remote ora8 reports gateway-path-check, sandbox_active=null, NOT verified remote sandbox.
Permitted root: /home/jq (rw); outside denied. Denied roots: ~/.ssh ~/.gnupg ~/.aws ~/.azure ~/.kube ~/.docker/config.json ~/.config/1Password ~/.config/gh/hosts.yml ~/.config/loopback ~/.netrc. Protected-path enforcement not directly exercised.
Scoped tests: all newly written test files under the single directory. No changes to services, policy, tunnels, targets, workers, credentials, or existing data.
Files: Unicode/spaces, empty file, multiline, append, read, list, search, tail, exact patch and backups passed. Missing read produced not_found; nonmatching patch produced ValueError and no mutation. These were expected negative-test errors, not auth failures.
Code: python3 -B -m unittest -v test_mathutil.py => 6/6 then 7/7 passing. Updated double() and its test. stdout/stderr captured separately; synthetic exit 7 correctly returned 7. Short timed success returned 0; sleep(2) with timeout=1 returned returncode=null, stdout='', stderr='', duration_ms=1572 (timeout indication not explicitly named). 1013-char output returned untruncated.
Git: init local main, stage mathutil.py and test_mathutil.py only, staged diff 36 insertions, no commit, no remotes, no push.
Jobs: 6da64e72fb7c progress completed exit 0; 1f8106aa123d waiting job stopped (reported exit 0 by the time stop observed); 15b1b6b497fb completed naturally; cb8af8ff457d PID 22205 terminated with TERM exit -15. Process inspection scoped to marker. Terminal 5bc07ae46ab8 cwd and markers verified, resized, closed alive=false.
Terminal startup noise: /home/jq/.zshrc:136 and :140 command not found: zplug; error could not lock config file /home/jq/.gitconfig: File exists. Did not edit config.
Documents: generated synthetic fixtures via Python standard library. DOCX text/replacement readback passed. XLSX A1:B2 read and edited readback passed. PDF text 1 page, merge 2 pages, extraction 1 page passed. No dependencies installed.
Network: python urllib.request.urlopen('https://example.com',timeout=8) => HTTP 200; execute returncode 0.
Fleet: ssh_target_list reports aws1, aws2, ora1, ora3, ora5, ora6, ora7, ora8 registered (metadata only). node_list reports local node only (no registered remote MCP node). Only ora8 executed: ssh_execute(target='ora8',command='hostname && id',timeout=20) => rc0, hostname ora8, uid=1002(jq), gid=1002(jq), groups jq/sudo/docker; transport SSH via Tailscale; gateway-path-check. Did not contact ora2 or ora4.
Topology: local listener 127.0.0.1:2026/MCP, Cloudflare public tunnel loopback.ziopsyop.tech reported healthy last probe, not manually independently endpoint-tested. ssh links healthy last probe, not all live executed. ora1-system local stdio MCP-service status untested, not verified reachable MCP endpoint. Discovery generated_at null. Ownership not substantiated by reported kind where unknown.
Optional: browser_status available=false, policy enabled=true. desktop_status available=false, display null, Linux/X11 unavailable, policy enabled=true. No browser or desktop actions performed.
Dashboard: UI flows not tested; profile/custom permissions observable via policy_status, but buttons, validation, save, reconnect behavior, approval UI, OAuth refresh, onboarding, policy switching, tunnels, worker management, upgrades, rollback, rotation and destructive actions require separate manual or controlled tests.
Authentication/approval/safety: no new authentication or approval prompt observed by tool results; no auth/approval/safety denial observed. Timed refresh UNVERIFIED. Successful local calls do not disable OpenAI safety checks.

## PER-TOOL COVERAGE
- PASS machine_info — successful real call
- PASS permissions — successful real call
- PASS policy_status — successful real call
- PASS diagnostics — successful real call
- PASS connections — successful real call
- PASS read_file — successful real call
- PASS write_file — successful real call
- PASS list_dir — successful real call
- PASS patch_file — successful real call
- PASS search_text — successful real call
- PASS tail_file — successful real call
- PASS execute — successful real call
- PASS git_info — successful real call
- PASS job_start — successful real call
- PASS job_list — successful real call
- PASS job_output — successful real call
- PASS job_stop — successful real call
- PASS terminal_start — successful real call
- PASS terminal_write — successful real call
- PASS terminal_read — successful real call
- PASS terminal_resize — successful real call
- PASS terminal_close — successful real call
- PASS process_list — successful real call
- PASS process_kill — successful real call
- PASS node_list — successful real call
- PASS ssh_target_list — successful real call
- PASS ssh_execute — successful real call
- PASS docx_text — successful real call
- PASS docx_replace_text — successful real call
- PASS xlsx_read_range — successful real call
- PASS xlsx_write_range — successful real call
- PASS pdf_text — successful real call
- PASS pdf_merge — successful real call
- PASS pdf_extract_pages — successful real call
- PASS browser_status — status queried; capability itself unavailable
- PASS desktop_status — status queried; capability itself unavailable
- NOT TESTED node_call — no registered remote MCP node; only ora8 SSH command authorized
- NOT TESTED node_read_file — no registered remote MCP node; only ora8 SSH command authorized
- NOT TESTED node_execute — no registered remote MCP node; only ora8 SSH command authorized
- NOT TESTED node_diagnostics — no registered remote MCP node; only ora8 SSH command authorized
- NOT TESTED browser_open — optional adapter unavailable / personal session excluded
- NOT TESTED browser_snapshot — optional adapter unavailable / personal session excluded
- NOT TESTED browser_click — optional adapter unavailable / personal session excluded
- NOT TESTED browser_fill — optional adapter unavailable / personal session excluded
- NOT TESTED browser_get_url — optional adapter unavailable / personal session excluded
- NOT TESTED browser_close — optional adapter unavailable / personal session excluded
- NOT TESTED desktop_windows — adapter unavailable / desktop session excluded
- NOT TESTED desktop_screenshot — adapter unavailable / desktop session excluded
- NOT TESTED desktop_activate_window — adapter unavailable / desktop session excluded
- NOT TESTED desktop_click — adapter unavailable / desktop session excluded
- NOT TESTED desktop_type — adapter unavailable / desktop session excluded
- NOT TESTED desktop_key — adapter unavailable / desktop session excluded

## NEGATIVE TEST ARGUMENTS AND EXACT ERROR CONTENT
read_file(path='/home/jq/loopback-capability-test-ZsF7JZrX/no-such-test-file.txt') -> INVALID_ARGUMENT RuntimeException: Error executing tool read_file: not_found: /home/jq/loopback-capability-test-ZsF7JZrX/no-such-test-file.txt
patch_file(path='/home/jq/loopback-capability-test-ZsF7JZrX/multiline.txt',old_text='A deliberately missing string ZZZ_901',new_text='SHOULD_NOT_APPEAR',create_backup=false) -> INVALID_ARGUMENT RuntimeException: Error executing tool patch_file: ValueError: old_text was not found; read the file first and copy the exact text
Verification: multiline.txt unchanged by nonmatch patch. No OAuth/approval errors.

---

# Appendix B — COMPLETE PHASE B RETEST REPORT (VERBATIM)

**Origin:** `/home/jq/loopback-retest-sowVFYjM/RETEST_REPORT.md`

# Loopback unavailable-capability retest — 2026-10-31

Machine: jq-xubuntu1 (Ubuntu 26.04.1), jq. Loopback 4.0.0.
Test directory: /home/jq/loopback-retest-sowVFYjM
All test file writes confined to this directory.
No remote targets contacted. No dependencies installed. No policy, services, workers, tunnels or OAuth settings modified. No browser accounts or existing desktop windows accessed.

## Live authorization
Profile: admin (changed since prior custom-profile test).
Auth kind: oauth.
Granted scopes: loopback:read, loopback:write, loopback:exec, loopback:fleet.
Shell: enabled; approvals: hard-only. Process control/fleet: enabled.
Roots: /home/jq rw; other locations denied. Reported denied_roots list empty.
Sandbox: Landlock ABI 8 active; os-sandbox enforcement confirmed for local execute; sandbox not required under admin profile.
No reconnect or approval prompt observed. No timed token refresh verified.

## Browser adapter
browser_status result: available=false, executable=null, enabled_by_policy=true.
Dedicated browser attempt (no personal sessions):
browser_open(url="https://example.com",session="loopback-retest-sowVFYjM")
Exact tool error: INVALID_ARGUMENT: Error code: INVALID_ARGUMENT; Error: RuntimeException: Error calling MCP tool: [TextContent(type='text', text='Error executing tool browser_open: RuntimeError: agent-browser is not installed; install it separately before enabling browser tools', annotations=None, meta=None)]
Browser actions browser_snapshot, browser_click, browser_fill, browser_get_url and browser_close NOT TESTED, because no browser session was established. This is NOT an OAuth/safety error.
Observed /usr/bin/google-chrome exists, firefox /snap/bin/firefox; Python playwright and selenium modules absent. Existence of Chrome is not sufficient to activate the agent-browser adapter.

## Desktop adapter
desktop_status result: available=false, platform=Linux, display=null, xdotool=/usr/bin/xdotool, wmctrl=null, screenshot_program=/usr/bin/xfce4-screenshooter, enabled_by_policy=true; reason="native desktop automation currently requires Linux/X11 and xdotool".
Loopback process environment: DISPLAY=null, WAYLAND_DISPLAY=null, XDG_SESSION_TYPE=null, XAUTHORITY=null, XDG_RUNTIME_DIR=/run/user/1000, DBUS_SESSION_BUS_ADDRESS present.
Login-session metadata (read-only loginctl): session 2 Name=jq, Display=:0, Remote=no, Desktop=xfce, Type=x11, Active=yes, State=active.
Conclusion: GUI session exists, but Loopback's process lacks its display/session environment. Actual adapter automation unavailable; desktop_windows/screenshot/activate/click/type/key NOT TESTED to avoid accessing existing personal desktop without a dedicated fixture. No alternate direct GUI automation attempted.
Diagnose systemd process environment and user session integration separately, without assuming manually setting DISPLAY alone is sufficient.

## Zplug
Term startup in previous test reported: /home/jq/.zshrc:136 and :140 command not found: zplug.
Read-only checks: /home/linuxbrew/.linuxbrew/opt/zplug exists as a directory; ~/.zplug and common ~/ user-install paths absent; shutil.which("zplug")=None; zsh -fc "whence -v zplug" => "zplug not found"; ZPLUG_HOME and ZPLUG_ROOT unset in clean shell.
No claim that Homebrew zplug is uninstalled: installation directory exists; zplug may be a zsh function requiring initialization.
Inspection of package directory was denied by active sandbox:
PermissionError: [Errno 13] Permission denied: '/home/linuxbrew/.linuxbrew/opt/zplug'
This affected inspection stopped immediately; did not try another route to bypass permission.
No shell configuration was read or changed.

## Remote MCP-node tools
node_list result: only node "local" with url=http://127.0.0.1:2026/mcp, enabled=true, transport=local. No registered remote MCP node.
node_call(node="local",tool="machine_info",arguments={},timeout=12) returned:
INVALID_ARGUMENT: Error code: INVALID_ARGUMENT; Error: RuntimeException: Error calling MCP tool: [TextContent(type='text', text='Error executing tool node_call: ValueError: node_call is for remote nodes; call local tools directly', annotations=None, meta=None)]
EXPECTED DENIAL for local use; not an OAuth block. node_read_file, node_execute, node_diagnostics remain NOT TESTED because no registered remote MCP nodes and no authorized new target registration.
SSH targets are separate from remote MCP nodes. No remote commands issued in retest (prior ora8 test already passed).

## Remaining untested
Browser: snapshot, click, fill, get_url, close (prerequisite browser_open failed).
Desktop: windows, screenshot, activate_window, click, type, key (adapter unavailable).
Remote MCP node: read_file, execute, diagnostics (no remote node registered).
The unavailable browser and desktop tools have not been shown to work; browser_open failed with missing runtime, node_call(local) correctly rejected.
Dashboard UI, installation, graphical session configuration, OAuth timed refresh, worker/tunnel modifications, policy switching and destructive operations remain outside scope.

---

# Appendix C — COMPLETE PHASE C MARKET-READINESS AUDIT (VERBATIM)

**Origin:** `/home/jq/loopback-market-audit-peLeNTy4/chatgpt.md` (renamed from its original `FINAL_REPORT.md`)

# Loopback Coding — Market Readiness and Capability Remediation Audit
**Audit date:** 2026-10-31
**Observed host:** jq-xubuntu1, Ubuntu 26.04.1 LTS, XFCE on X11
**Loopback reported version:** 4.0.0
**Disposition:** NOT READY FOR GENERAL PUBLIC RELEASE in its current state, especially when promising functional browser/desktop automation. Core MCP tooling passed an earlier bounded smoke test; these two premium features remain unavailable.
**Audit directory:** /home/jq/loopback-market-audit-peLeNTy4

## Executive conclusion

The gateway responds and grants OAuth scopes loopback:read, loopback:write, loopback:exec, loopback:fleet. Local shell/file/Git/process/terminal/doc tools and SSH execution on user-authorized ora8 were successfully exercised in the preceding bounded test. **Neither browser nor desktop automation passed a functional live test.** Current profile is admin with hard-only approvals, no denied roots configured and sandbox_required=false; the actual local Landlock sandbox is active. The workstation has an active X11 XFCE session at :0, but the Loopback service has no DISPLAY environment variable. Chrome is installed, but the agent-browser CLI required by the Loopback browser wrapper is missing. Zplug exists under the Homebrew prefix, but is inaccessible to the home-root Landlock sandbox in the tested context and cannot be initialized in the clean shell.

The source checkout is in a detached-HEAD state (38ee3e3) with an unresolved merge conflict in runtime/server.py (git status 'UU runtime/server.py'). Do not apply or deploy code from this checkout until resolved. Running server version and source version may differ. No operational changes were made.

## Scope and immutable boundaries

- All new files written only under /home/jq/loopback-market-audit-peLeNTy4.
- Read-only inspection of /home/jq/loopback source, existing documentation and shell/session metadata.
- No credentials, tokens, protected files, private browser sessions, screenshots or personal documents accessed.
- No other machines contacted; no changes to registered targets, workers, tunnels, OAuth settings, policy, service files, Git, shell startup files or dependencies.
- No sudo/install/commit/push/upgrade/reboot performed.
- No reconnect, approval or safety prompt in this run.
- Historical proof of ora8 SSH operation is not a fresh remote test.

## Direct live test evidence

### Authentication, sandbox and policy
machine_info => user jq, physical workstation jq-xubuntu1; initial_cwd /home/jq; shell /usr/bin/zsh.
permissions(target='local') => auth oauth; scopes loopback:read, loopback:write, loopback:exec, loopback:fleet.
Current profile admin, approvals hard-only, allow shell/process/fleet true; root /home/jq rw; other_locations denied; denied_roots empty; sandbox_required false, actual sandbox active os-sandbox, Landlock ABI 8.
Local execute calls reported enforced=true, sandbox=landlock. This does not guarantee that every future invocation or subsystem is sandboxed.

### Browser
browser_status({}) => available:false, executable:null, enabled_by_policy:true.
Previous test: browser_open(url='https://example.com', session='loopback-retest-sowVFYjM') =>
"Error executing tool browser_open: RuntimeError: agent-browser is not installed; install it separately before enabling browser tools"
Source runtime/browser.py: BrowserAdapter.status uses shutil.which('agent-browser') and _run launches that CLI; installer apparently does not provision it by default (docs/TOOLS.md says separately installed). /usr/bin/google-chrome exists; no discovered executable named agent-browser. No isolated browser session was actually established.

### Native desktop
desktop_status({}) => available:false, display:null, xdotool:'/usr/bin/xdotool', wmctrl:null, screenshot_program:'/usr/bin/xfce4-screenshooter', enabled_by_policy:true; reason='native desktop automation currently requires Linux/X11 and xdotool'.
desktop_windows(limit=1) => "Error executing tool desktop_windows: RuntimeError: native desktop automation currently requires Linux/X11 and xdotool".
Prior read-only environment: DISPLAY, WAYLAND_DISPLAY, XDG_SESSION_TYPE and XAUTHORITY absent from Loopback execution environment; XDG_RUNTIME_DIR=/run/user/1000.
Read-only loginctl session 2 => Name=jq, Desktop=xfce, Type=x11, Display=:0, State=active, Active=yes, Remote=no. The actual desktop exists; Loopback cannot discover/connect to it.
Source runtime/desktop.py status currently tests only platform Linux, bool(DISPLAY), bool(shutil.which('xdotool')). It does not prove a live authenticated X server or readiness of window/screenshot dependencies.
No windows were enumerated, desktop screenshot taken, keystrokes sent or clicks issued.

### Zplug
Earlier PTY startup displayed /home/jq/.zshrc:136 and :140 'command not found: zplug'.
Read-only checks: /home/linuxbrew/.linuxbrew/opt/zplug is a directory; zplug is not in PATH; zsh -fc 'whence -v zplug' says 'zplug not found'; ZPLUG_HOME and ZPLUG_ROOT unset in clean shell.
Attempt to enumerate the Homebrew zplug directory from inside the active Landlock sandbox returned PermissionError: [Errno 13] Permission denied. The test stopped instead of bypassing the sandbox. Installation existence is not proof of correctly sourced zsh function; likewise a clean zsh -f not defining the function is not proof of uninstall.
Do NOT blindly install another zplug; verify Homebrew's initialization file and ensure the intended terminal mode can read that installed prefix without widening access indiscriminately.

### Registered MCP nodes and other coverage
node_list returned local MCP node only; remote MCP node tools are not operable without separately registered remote nodes, distinct from configured SSH targets.
Previous local call node_call(node='local', tool='machine_info', arguments={}) returned 'node_call is for remote nodes; call local tools directly' — expected denial, not an auth error.
No current remote node registration or SSH target mutation is justified.

### Source integrity and release blocker
git_info('/home/jq/loopback') => detached HEAD, commit 38ee3e3, 'UU runtime/server.py'. The working tree has unresolved conflicts. Leave this checkout untouched; repair and verify in a separate controlled change/review before any release.

## Isolated regression reproductions (NO live deployment)

Created adapter_behavior_repro.py in audit directory; executed with python3 -B:
1. CURRENT BUG: browser.Open plain HTTP localhost-lookalike hostname is accepted by startswith checks.
2. CURRENT WEAKNESS: browser_status returns available when executable path merely exists, without CLI/runtime probe.
3. CURRENT WEAKNESS: desktop_status returns available when DISPLAY is nonempty and xdotool resolves, even if display is fictitious/unprobed.
4. CURRENT EXPECTED RULE: browser_open rejects ordinary nonlocal plain HTTP.
All 4 behavior reproductions passed, meaning they demonstrate current implementation behavior, not that it is correct.

Created url_validator.py and test_url_validator.py as a PROPOSED isolated validation implementation; unit tests reject localhost-lookalikes, nonlocal HTTP, userinfo, invalid ports, control characters, and support HTTPS/exact localhost/127.0.0.1/[::1]. 11/11 tests passed after correcting a test fixture whose first control-character input accidentally contained a literal backslash-n.
No code from these prototypes was copied into /home/jq/loopback. The URL validator must be integrated and security-reviewed along with browser session ownership checks before release.

## P0 — release blockers, in recommended order

P0-1. Resolve/rebase the current detached-HEAD merge conflict (runtime/server.py). Ensure working tree clean, choose authoritative version and deploy only tested/tagged artifacts. No live cherry-picking or blind git reset.

P0-2. Ship optional browser automation as a complete dependency:
- Provide explicit opt-in installation for a supported, pinned and verified agent-browser version plus browser engine provisioning, without silently hijacking a personal Chrome profile.
- Expose 3 separate states: policy-enabled, runtime-installed, functional-health-checked; publish exact missing dependency/version error and remediation.
- Use a *dedicated ephemeral, per-principal profile*, not default shared 'loopback' across OAuth clients. Bind sessions to OAuth subject/client; require explicit sharing; automatically expire and clean abandoned sessions.
- Functional acceptance: open a new blank fixture profile against a synthetic local test page; snapshot, fill, click, read URL and close; prove cleanup and no access to personal browser profiles.
- Fix URL validation using parsed URL hostname, scheme, port, userinfo and control-character checks. Test SSRF/loopback/rebinding handling and error redaction.

P0-3. Correct X11 desktop integration:
- Provide an explicit *opt-in* interactive-session connection between the Loopback service and the authenticated local user's XFCE X11 session. Do not infer :0 automatically on a multiuser host, and do not read/export X11 authentication cookies over MCP.
- The current logind session is X11 Display=:0 but the service's DISPLAY is unset. Properly propagate the selected DISPLAY and XAUTHORITY via supported user-session service/broker lifecycle, with user consent and revocation.
- Move from bool(DISPLAY) to a bounded real X11 probe (e.g., xdotool getdisplaygeometry) in health checks, checking exit code and reporting distinct errors 'not configured', 'display unavailable', 'X11 authorization failed', and 'policy denied'. Cache bounded health checks.
- Create and use a dedicated synthetic GUI fixture/window with a test-run-specific identifier. Prove windows/activate/screenshot/click/type/key with explicit allowlist; never capture or interact with personal screens without separate consent. Implement lock-screen and session-switch denial.
- Distinguish X11 support from Wayland unsupported; do not claim cross-platform desktop automation without implementations and tests.

P0-4. Zplug and isolated shell initialization:
- Zplug's Homebrew prefix exists, but Landlock permitted file roots only include /home/jq; current sandbox correctly blocks direct inspection under /home/linuxbrew.
- For terminal use, explicitly initialize the appropriate zplug script in zsh *only after* confirming a narrow read-only executable/library access policy or an approved user-local install path. Do not disable sandbox or blindly grant broad /home/linuxbrew read/write.
- Prefer minimal, deterministic noninteractive shells for execute/jobs rather than loading interactive .zshrc, Starship, plugin managers and alias side effects.
- Acceptance: isolated interactive PTY starts with zplug function actually resolvable and no startup warnings; noninteractive command returns bounded clean stdout/stderr; sandbox is still effective.

P0-5. Secure default policy:
- 'admin' currently has no configured denied roots and sandbox_required=false. For market-ready defaults, ship restricted/standard, least-privilege writable roots, protected credential exclusions and sandbox_required=true where supported.
- Separate optional browser/desktop permissions from broad shell/fleet privileges, and use per-client scoped capabilities and confirmations. Never promise controls that can be bypassed by changing to admin profile.
- End-to-end auth tests must verify OAuth reconnect, expired access tokens, refresh rotation and error UI (including the previously observed 'no sign-up URL' reconnection issue), explicitly distinguishing app/client prompts from server policy approvals.
- Keep Windows/non-Landlock users honest about no OS sandbox rather than implying parity.

## P1 — production hardening and reliability

P1-1. Add first-run 'doctor --json' / dashboard capability matrix: detected executable, version, install path, runtime health, current OAuth scopes, policy enablement, sandbox state, session broker health and user-facing fix steps. Never label policy-enabled as ready.
P1-2. Structured error taxonomy (UNAVAILABLE_DEPENDENCY, NOT_AUTHORIZED, NEEDS_APPROVAL, SANDBOX_DENIED, GUI_SESSION_MISSING, GUI_AUTH_FAILED, REMOTE_INTERACTIVE_CHALLENGE, OPERATION_FAILED). Preserve correlation ID and exact underlying error only in privacy-safe diagnostic logs. Do not imply auth failures on shell nonzero returns.
P1-3. End-to-end per-platform CI matrix with Xvfb-based synthetic desktop tests in isolated runner, browser fixture tests, OAuth access-token and refresh lifecycle, reconnect, policy denies, multi-client isolation and crash recovery. Run jobs/terminal cleanup tests.
P1-4. Limit runtime tools by negotiated/selected capabilities. Keep 52-tool menu discoverable but don't inject unusable or irrelevant tools as if ready. Use task-context dynamic tool groups with stable IDs and documented scopes.
P1-5. Explicit resources ownership / cleanup: idle-timeouts, browser session TTL, terminal and process cleanup, per-principal job quotas; do not stop unrelated user processes. Mask secret-looking text in logs. Implement safe audit trails for UI/desktop actions.
P1-6. Release reproducibility: pinned optional dependencies and checksums/signatures, SBOM, vulnerability scans, unit/integration tests and signed/tagged release provenance. Validate all supported installers and upgrades, with rollback tests and cross-version migration fixtures.
P1-7. Dashboard: show current health separately from cached topology, broken dependency remediation, connection provenance, per-client scopes and actual sandbox enforcement; avoid reporting listening ports as externally reachable MCP endpoints.
P1-8. Quality assurance: release candidate tested on clean Ubuntu/Xubuntu, Debian, macOS and Windows, with explicit platform feature matrix. Public API docs and stable error contracts.
P1-9. Service security: restrict browser/desktop controls to explicit local user consent and session binding. Record screenshot/interaction events without saving sensitive screen pixels by default. Rate-limit potentially destructive actions.
P1-10. Add support bundle export with opt-in anonymization/redaction and inspectable content before sending; reproducible bugs should include an immutable capability snapshot and severity.

## P2 — performance, usability and product improvements

- Benchmark true browser automation and X11 GUI action latencies (p50/p95/p99), beyond the existing local MCP baseline. Track slow shell startup separately from command execution.
- Cache Landlock policy paths where safe; retain authorization checks and fail-closed behavior if cache invalid. Current project BENCHMARKS.md cites a prior local execute p50 of ~25ms and notes Landlock path enumeration as overhead; this is historical data, not measured during this audit.
- Lazy-load optional browser/document/UI dependencies; keep headless server footprint low. Break out optional extras into independently installable, versioned features.
- Improve accessibility and mobile dashboard; include explicit capability health and user-actionable state, not generic disabled buttons.
- Public docs: install, remote ingress, explicit permissions, limitations, privacy handling, troubleshooting and quick smoke test. Use precise examples for X11/Wayland and browser CLI provisioning.
- Product differentiation: transparent trust boundaries; audited fleet execution; per-client least-privilege OAuth; reliable desktop/browser fixtures; strong self-diagnostics and recovery UX.

## Proposed release acceptance gates (all required before 'market-ready')

G1: Source checkout clean; tagged candidate; full CI green; verified install/upgrade/rollback for claimed platforms.
G2: Fresh user install includes a preflight producing accurate available/unavailable statuses and actionable remediation.
G3: Browser end-to-end synthetic fixture passes on a dedicated profile, with per-principal isolation and cleanup; URL validation negative tests pass.
G4: Native desktop end-to-end synthetic X11 fixture passes in the *intended GUI session*, with explicit consent, lock state, output verification, no leakage across users.
G5: Zplug/interactive shell noise absent on supported install; shell execution remains clean and deterministic without widening sandbox.
G6: OAuth fresh consent/reconnect/refresh/revoke verified using real client(s); scope/approval semantics documented and accurately surfaced.
G7: File, command, Git, job/process, terminal, document, fleet and dashboard safety regressions pass; no unauthorized writes or remote contacts.
G8: Threat review covers browser/desktop privilege, credential boundaries, CSRF/SSRF, tunnel exposure, process kill, and service/broker authentication.
G9: Published support and observability documentation with versioned compatibility matrix and clear remediation paths.

## Current status and next handoff

- Browser automation: **FAIL / UNAVAILABLE** (agent-browser CLI absent).
- Desktop automation: **FAIL / UNAVAILABLE** (Loopback service display unset; separate active X11 session exists).
- Zplug: **PARTIAL / BLOCKED** (installed Homebrew prefix exists, executable/function not available in tested shell; sandbox denied inspected prefix).
- Browser URL hardening: **ISOLATED PROTOTYPE PASS** (11/11), NOT integrated, NOT deployed.
- Existing source compatibility: **BLOCKED** by unresolved merge conflict.
- Remote MCP-node controls: **NOT TESTED**, no remote MCP nodes registered; distinct from registered SSH targets.
- Shell/file/Git/job/process/terminal/document and authorized SSH ora8: earlier bounded test PASS; do not conflate with this run.
- Browser credential/session isolation, timed OAuth token refresh, UI dashboard buttons, full cross-platform support: **UNVERIFIED**.
- No dependency/service/policy/repo changes, no new authentication prompts, no test-created jobs or terminals.

## Files delivered in audit directory
adapter_behavior_repro.py — 4 behavior repro tests run.
url_validator.py — isolated proposed hardened URL validator; NOT integrated.
test_url_validator.py — 11 passing unit tests.
FINAL_REPORT.md — this report.

**Do not say this installation is market-ready until G1–G9 have been independently verified.**
