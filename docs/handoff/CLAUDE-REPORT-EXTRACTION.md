# Claude Capability Report: Implementation Handoff

Source: [`docs/reports/CLAUDE-CAPABILITY-2026-10-31.md`](../reports/CLAUDE-CAPABILITY-2026-10-31.md). Line references below refer to that report.

## Observed evidence

- **Scope and coverage limits:** The run covers only tools available in the conversation and does not claim exhaustive coverage or future authorization. Blocked and untested operations are not passes. No third-party safety check was shown disabled. The summary explicitly lists untested areas and says dashboard flows were not tested. Policy uses hard-only approvals, with no soft prompts; no approval, reconnect, auth, or interactive Tailscale prompt appeared. (lines 6, 17–22, 48–63)
- **Unavailable automation:** Browser and desktop automation report unavailable although policy enables them. Browser status has no executable; desktop status has no display and lacks `wmctrl`. Action tools were not called to avoid using an existing browser or personal desktop session. (lines 24–30, 117–120, 232–243)
- **Sandbox and shell behavior:** Landlock is active and enforced even though `sandbox required` is false in policy data. The report notes Landlock cannot subtract a denied subtree from an allowed tree; it grants such trees child-by-child, so a file created directly in a top-level directory can become accessible to a later command. `/home/linuxbrew` is outside the allowed root; shell startup reports permission errors for `brew` and `zplug`, and `zplug` is not found. Shell startup also reports a `.gitconfig` lock error. (lines 48–63, 181–190)
- **Job visibility and stored logs:** `job_list` returned all principals’ jobs and full command text. Job logs reside under the allowed home tree. (lines 95–98, 247–263)
- **Remote execution enforcement:** The `ora8` command succeeded and reported `gateway-path-check`; the report explicitly describes this as a gateway policy check, not an OS sandbox. The account is in `sudo` and `docker` groups. Only `ora8` was exercised. (lines 208–215, 247–263, 297–305)
- **Status freshness:** The `connections` snapshot has a 120-second staleness window. Worker heartbeats were about 80 minutes old while SSH probes were seconds old and healthy. The report distinguishes worker process status from MCP endpoint reachability; the endpoint was not verified. (lines 65–74, 86, 217–230)
- **Topology and identity surface:** The snapshot reports 36 active OAuth grants and an enabled legacy machine-token path. Tunnel metadata has null ID and credential fields. No discovery run was observed, and no node reported explicit ownership. The `ora1-system` MCP service record measures worker process status, not endpoint reachability. Registered targets are distinct from installed Loopback nodes. (lines 65–74, 217–230)
- **File and command behavior:** File operations passed, including Unicode, append, exact patching, and expected missing-file and mismatched-patch errors. `patch_file` created adjacent backups by default. A timeout result omitted `sandbox` and `enforced` fields that appeared on other results. A direct 20,000-byte output was complete; larger truncation was not exercised. (lines 87–93, 129–155)
- **Terminal output:** Terminal operations passed, but `terminal_read` included raw OSC/ANSI sequences. Delayed output appeared on a later read. (lines 101–105, 173–190)
- **Document operations:** DOCX, XLSX, and PDF read/write/extract/merge operations in the table passed. The report says Loopback used bundled libraries; system Python had no listed third-party document libraries and none were installed. (lines 106–112, 192–204)
- **Network and SSH:** `curl` to the example domain returned HTTP 200. Eight registered SSH targets had fresh healthy probes; unregistered targets were not contacted. `ora8` execution returned successfully. (lines 206–215)
- **Cleanup:** The three test jobs and test process were stopped, the terminal was closed, and the report says no process remained. Test files were left in the test directory. No commit, push, or global configuration change was made. (lines 157–162, 163–179, 324–345)

## Defects and product limitations reported

1. Browser automation is unusable on this machine because no executable was found, despite policy enablement. (lines 251, 271)
2. Desktop automation is unusable because the gateway sees no display and `wmctrl` is missing. (lines 252, 271–272)
3. Sandboxed shell startup cannot access `brew` or `zplug` under `/home/linuxbrew`; `zplug` plugins therefore do not load in the observed shell. Outside-sandbox behavior is unknown. (lines 181–190, 253, 287)
4. The `.gitconfig` startup lock error occurred; its cause was not investigated. (lines 181–188, 254)
5. `job_list` exposes every principal’s jobs and full command text, including remote command text. (lines 97, 255)
6. Job logs are readable by any `execute`-capable principal because they sit inside the allowed root. (lines 256)
7. Stale worker heartbeats alongside healthy SSH probes can make status ambiguous; worker liveness and host reachability differ. (lines 71–72, 257)
8. The report flags a wide authorization surface: 36 active OAuth grants, a legacy machine-token path, and incomplete tunnel metadata. (lines 67–68, 258)
9. Remote commands have no OS sandbox in the observed execution path; the account’s `sudo` and `docker` memberships increase their reach. (lines 210–215, 259)
10. `patch_file` exposes a raw Python `ValueError`; automatic adjacent backups can clutter directories and appear as untracked files. (lines 90, 135–145, 260)
11. `terminal_read` includes raw ANSI/OSC sequences, making output harder to read and increasing token use. (lines 103, 190, 261)
12. Timeout responses omit sandbox-enforcement fields returned by other results. (lines 154, 262)
13. Tool error result shapes vary between strings and structured objects, complicating uniform client handling. (lines 263)

## Recommendations and proposed follow-ups

### Reliability and clarity

1. Make browser and desktop status explain concrete causes and fixes. Report adapter installation, policy enablement, and usability as separate states. (lines 269–272)
2. Add guided automation setup: detect X11/Wayland, offer service display-variable configuration, install or identify a browser adapter, and provide a one-click self-test. (line 272)
3. Standardize failures as `{code, message, hint}`, hide Python exception names, and keep response shapes consistent, including timeout results. (line 273)
4. Make `patch_file` backups opt-in or managed in a dedicated location with retention limits. (line 274)
5. Add a `strip_ansi`/`plain` option to `terminal_read` and suppress shell-integration sequences by default. (line 275)

### Security and privacy

6. Scope jobs and logs to the calling principal by default, redact other principals’ command text, and keep logs outside sandbox-writable trees. (line 279)
7. Retire the legacy machine-token path or gate it with an explicit expiring toggle. Add grant expiry, revoke-all, and an audit view for current grants. (line 280)
8. Label remote execution as gateway-check-only in dashboard and docs; offer an optional remote sandbox or per-target command allowlist. (line 281)
9. Populate tunnel ID and credential reference metadata, and show timestamped tunnel health. (line 282)
10. Add a safe protected-path self-test that proves denial without accessing real credential files. (line 283)

### Developer experience

11. Allow users to add read-only sandbox roots such as `/home/linuxbrew`; warn when shell initialization fails under the sandbox. (line 287)
12. Show status age everywhere, provide a verify-now probe, and report worker heartbeat, SSH reachability, and MCP endpoint verification separately. (line 288)
13. Add a built-in `selftest` covering file, exec, job, terminal, document, and network paths, with standard PASS / DENIED / UNAVAILABLE results. (line 289)
14. Document and test output limits; return truncation state and byte counts consistently. The report states a 16 MB maximum and only verifies a 20 KB output. (lines 63, 155, 290)
15. Add a safe approval-path preflight for `approval_id` in a non-admin profile. (lines 121, 291)

### Explicitly untested or manual follow-up

- Test protected-path enforcement with a safe self-test; no credential files were read in this run. (lines 146, 299)
- Test output truncation above 20 KB. (lines 155, 300)
- Exercise `approval_id` flows; the admin profile did not produce an approval request. (lines 121, 301)
- Exercise remote execution on targets other than `ora8`, and test `node_call`, `node_diagnostics`, `node_execute`, and `node_read_file` when a remote Loopback node is available. (lines 115–116, 302)
- Exercise `browser_*` and `desktop_*` action tools after their adapters/environment become available. They were unavailable and not called. (lines 119–120, 303)
- Check whether `zplug` works outside the sandbox. (line 304)
- Verify token refresh over time; this short run provides no evidence about refresh. (line 305)
- Manually test dashboard policy switching, tool selection, scope grants, worker installation/adoption, tunnel changes, upgrades/rollback, credential rotation, and all dashboard buttons or flows. (lines 307–314)
- Run separate controlled tests for destructive/high-impact operations: data deletion, service restart, reboot, deployment, commit/push, target/worker/tunnel registration or reconfiguration, and OAuth or machine-policy changes. These were not run on the live installation. (lines 316–320)

## Hypotheses and qualified inferences

- The report says desktop automation likely needs the service to run with the session’s `DISPLAY` / `XAUTHORITY`; this is a proposed cause/fix, not a verified remediation. (line 252)
- The report identifies the allowed-root boundary as the reason `brew` and `zplug` fail in sandboxed shells. It explicitly leaves outside-sandbox `zplug` behavior untested. (lines 253, 287, 304)
- The report characterizes broad grant count and legacy token availability as a wide auth surface, and null tunnel metadata as incomplete; it does not establish that a credential was exposed or that the tunnel is unhealthy. (lines 67–68, 258)
- The report infers cross-principal exposure risk from observed job listing and log placement. The listing behavior was observed; readability of other principals’ log files is stated as an implication, not separately demonstrated in the test details. (lines 97, 255–256)

## Coverage totals

The report provides individual tool statuses in its coverage table (lines 78–121), but does not state aggregate pass/fail totals. Several rows group multiple tools, so this extraction does not present a derived total as a report result.

## Sensitive information handling

The source contains infrastructure and identity details, including a machine/user identity, public gateway hostname and address, internal service/target names, OAuth scopes and grant count, remote account/group membership, job/process/terminal identifiers, and absolute test paths. This handoff does not repeat their literal values. The report explicitly states that no credential files were read and shows null credential metadata; it contains no credential secret in the inspected text. (lines 4–9, 41–46, 67–74, 146, 210–215, 328–345)
