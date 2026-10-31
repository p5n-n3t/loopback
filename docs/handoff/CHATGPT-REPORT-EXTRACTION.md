# ChatGPT capability report extraction

**Source:** [`docs/reports/CHATGPT-CAPABILITY-2026-10-31.md`](../reports/CHATGPT-CAPABILITY-2026-10-31.md)
**Source date:** 2026-10-31
**Purpose:** Implementation handoff extracted from the complete consolidated report. This document does not add runtime findings or claim a new retest.

Line references below point to the source report, not this extraction. The source says it preserves three saved reports and is not a new retest or complete raw MCP event archive (source lines 5–6, 165).

## Executive finding

The report's disposition is **not market-ready** for claims of working browser and computer automation. The gateway and several core capabilities passed bounded tests, but browser and desktop automation were unavailable. The consolidated inventory totals 52 tools: **36 PASS, 1 EXPECTED DENIAL, 2 UNAVAILABLE, 13 NOT TESTED**. A PASS for `browser_status` or `desktop_status` confirms only that the status call returned, not that the feature works (source lines 8, 39–41).

## Observed evidence

These are reported observations or test results, not proposed behavior.

### Authorization and environment

- OAuth reported read, write, exec, and fleet scopes. The profile changed from `custom` to `admin` between the earlier test and retest; shell approval changed from `standard` to `hard-only`. The allowed root was `/home/jq`; the admin profile had no denied roots and did not require Landlock, although local Landlock ABI 8 enforcement was active in inspected commands (source lines 20–37, 398–402).
- No new authentication or approval prompt was observed. Timed OAuth refresh remains unverified. Protected-path denial was not tested against credential files (source lines 35–37).
- A live XFCE X11 session existed at display `:0`; the Loopback service process lacked `DISPLAY` and related GUI session environment. Browser policy was enabled while its adapter was unavailable (source lines 32–35, 404–416).

### Passing and partially passing capabilities

- File operations passed create, overwrite, append, Unicode, empty-file, list, search, tail, patch, backup, and negative-input cases. Missing reads and unmatched patches returned expected input errors without unauthorized mutation (source lines 100–106, 229–247, 302–307).
- Coding/execution passed 6 then 7 unit cases, stdout/stderr separation, synthetic exit status 7, bounded execution, and a 1,013-character output. A one-second timeout returned `returncode=null` after 1,572 ms without an explicit timeout flag. A test-only Git repository staged only two files and was not committed or pushed (source lines 107–113, 228–247).
- Jobs, process inspection/termination, and PTY operations were exercised. One `job_stop` call did not prove live termination because that job may already have ended. The separate PID termination returned `-15`; the PTY closed with `alive=false` (source lines 114–130, 256–263).
- Synthetic DOCX, XLSX, and PDF read/edit/merge/extract cases passed. Fixtures used the Python standard library and no dependencies were installed (source lines 131–143).
- HTTPS to `example.com` returned HTTP 200. SSH execution was performed only on authorized `ora8`; it used Tailscale SSH and `gateway-path-check`, not a verified remote OS sandbox. Other listed SSH targets are metadata only. Only the local MCP node was registered (source lines 144–155, 269–280).
- `connections` returned topology metadata, but cached/probed status was distinguished from independent reachability. The `ora1-system` stdio MCP endpoint was untested/stale; a listener or heartbeat does not prove endpoint reachability (source lines 49, 144–155, 286–287).

### Browser, desktop, and shell observations

- `browser_status` returned `available=false`, `executable=null`, `enabled_by_policy=true`. `browser_open` failed because `agent-browser` was absent. Chrome and Firefox existed, but neither satisfied the wrapper's CLI dependency. No dedicated browser session was established (source lines 41, 47–52, 156–177, 404–408).
- `desktop_status` returned `available=false` with no display. `desktop_windows` failed because the service could not access X11. No windows were enumerated, screenshot taken, or input actions sent (source lines 42, 53–58, 156–177, 410–416).
- Interactive zsh startup reported `zplug` not found. Its Homebrew prefix existed but was outside the active Landlock readable roots; inspection was denied and stopped. This establishes that zplug could not be resolved in the tested shell, not that it was uninstalled (source lines 85–91, 178–182, 344–352, 418–423).
- `node_call` on `local` correctly returned an expected denial because it is for remote MCP nodes. Remote node read, execute, and diagnostics were not tested because no remote MCP node was registered (source lines 43–46, 183–185, 353–359, 424–427).

### Source audit reproductions

- The inspected source checkout was detached at `38ee3e3` with unresolved conflict `UU runtime/server.py`; the report says not to apply or deploy that checkout before controlled repair (source lines 384, 429–430).
- Four isolated reproductions demonstrated existing behavior: a localhost-lookalike HTTP hostname passed prefix validation; browser status could treat executable-path existence as health; desktop status could treat a fictitious `DISPLAY` as health; ordinary nonlocal HTTP was rejected. Passing reproductions prove the reproduced behavior, not correctness (source lines 432–439).
- A proposed isolated URL validator passed 11/11 tests for localhost-lookalikes, nonlocal HTTP, userinfo, invalid ports, control characters, HTTPS, and exact loopback hosts. It is a prototype only and was not integrated or deployed (source lines 441–442, 513).

## Defects and limitations to address

1. **Release/source integrity:** detached HEAD and unresolved `runtime/server.py` conflict block source compatibility and release confidence (source lines 384, 429–430, 446).
2. **Browser dependency/readiness:** optional wrapper dependency `agent-browser` is missing. Policy-enabled status is misleading as a readiness signal. Existing URL prefix validation accepts a localhost-lookalike. Browser session isolation, ownership, cleanup, and functional actions have no live evidence (source lines 405–408, 435–442).
3. **Desktop readiness/session binding:** current status logic checks Linux, nonempty `DISPLAY`, and `xdotool` presence; it does not prove a live authenticated X server or required window/screenshot dependencies. The actual service lacks display/session environment. X11 exists in the user session, but the service cannot discover/connect to it (source lines 411–416).
4. **Shell initialization:** zplug initialization fails in the interactive terminal. Landlock blocks inspection of the Homebrew prefix, and the report cautions against broadening access indiscriminately (source lines 418–421, 462–466).
5. **Policy defaults and boundaries:** the observed `admin` profile has no denied roots and `sandbox_required=false`; local commands were sandboxed in inspected cases, but that does not guarantee every invocation/subsystem is sandboxed. Protected-path enforcement was not directly exercised (source lines 29–37, 401–402, 468–472).
6. **Health and error reporting:** status checks can report availability without functional health. The source recommends explicit distinction among policy, installed runtime, and checked health; it also calls for structured error classes and remediation (source lines 41, 435–439, 476–477).
7. **Ownership and cleanup:** the report calls out browser profiles shared across OAuth clients, abandoned sessions, terminal/process cleanup, job quotas, and avoiding termination of unrelated processes as risks requiring explicit ownership and lifecycle controls (source lines 451, 459, 480, 484).
8. **Platform and product evidence:** Windows/non-Landlock behavior, Wayland, cross-platform desktop, dashboard behavior, and public compatibility claims lack sufficient evidence (source lines 37, 163, 460, 472, 482–483, 517).
9. **Reporting limits:** source consolidation is not a raw event archive. No reconnect/approval prompt was observed, but a prior reconnect/sign-up URL issue was not reproduced. Several tests were manual/controlled or explicitly excluded (source lines 163–165).

## Untested or unverified capability inventory

### 52-tool outcome totals

The latest consolidated matrix (source lines 39–59) reports:

| Outcome | Count | Coverage |
|---|---:|---|
| PASS | 36 | Includes status-call success for browser and desktop, which does not establish feature readiness |
| EXPECTED DENIAL | 1 | `node_call` against the local node |
| UNAVAILABLE | 2 | `browser_open`, `desktop_windows` |
| NOT TESTED | 13 | 3 remote-node actions, 5 browser follow-on actions, 5 desktop actions |
| **Total** | **52** | |

The 13 untested tools are `node_read_file`, `node_execute`, `node_diagnostics`; `browser_snapshot`, `browser_click`, `browser_fill`, `browser_get_url`, `browser_close`; and `desktop_screenshot`, `desktop_activate_window`, `desktop_click`, `desktop_type`, `desktop_key` (source lines 44–59). The initial Phase A per-tool inventory called `node_call` not tested; later Phase B/C exercised it and classified the local-node rejection as expected (source lines 291–300, 353–359).

### Other explicitly unverified items

- OAuth timed refresh, token expiry/rotation, reconnect UI, fresh consent/revoke lifecycle, and the reported sign-up URL reconnect issue (not reproduced) (source lines 35–37, 163, 471, 503).
- Protected-root/subtree denial, malicious shell bypass attempts, policy switching, and safety enforcement across all subsystems (source lines 37, 163, 401–402).
- Browser snapshot/fill/click/URL/close, functional browser runtime, session ownership/isolation/expiry, personal-profile exclusion, SSRF/rebinding defenses, and error redaction (source lines 47–52, 151–153, 451–453).
- Desktop windows/screenshot/activate/click/type/key, authenticated X11 access, lock-screen/session-switch denial, multiuser separation, and Wayland behavior (source lines 53–58, 138, 159, 456–461).
- Dashboard buttons, validation, save, reconnect, approvals, onboarding, tunnels, worker management, installation, upgrades, rollback, backup/restore, rotation, and destructive operations (source lines 63, 163, 183).
- Remote MCP-node actions, any new target registration, and SSH execution on targets other than the authorized `ora8` (source lines 37, 108–116, 175–176).
- Live termination via the specific `job_stop` call; its target may already have exited (source lines 23, 79–83).
- Full raw MCP request/response history and independent validation of all reported topology, listening ports, or public ingress (source lines 116, 165).

## Recommendations and proposed implementation follow-ups

This section enumerates every recommendation and follow-up in the consolidated priorities and the full Phase C audit. Items retain their source priority labels.

### P0 — release blockers

- **P0-1, source integrity:** Resolve or rebase the detached-HEAD merge conflict on a controlled branch; select the authoritative version; leave a clean tree; deploy only tested/tagged artifacts. Avoid live cherry-picking and blind reset (source line 446; also 170, 498).
- **P0-2, browser dependency:** Offer explicit opt-in installation for a supported, pinned, verified `agent-browser` version and browser engine. Do not silently take over a personal Chrome profile (source line 449).
- **P0-2, browser readiness:** Surface separate states for policy enabled, runtime installed, and functional health checked. Report the missing dependency/version and remediation precisely (source line 450; also 476).
- **P0-2, browser isolation:** Use a dedicated ephemeral profile per principal. Bind sessions to OAuth subject/client; require explicit sharing; expire and clean abandoned sessions (source line 451).
- **P0-2, browser acceptance:** Run open/snapshot/fill/click/read-URL/close against a synthetic local fixture; prove cleanup and no access to personal browser profiles (source line 452; also 500).
- **P0-2, URL safety:** Replace prefix checks with parsed hostname, scheme, port, userinfo, and control-character validation. Test SSRF, loopback/rebinding, and error redaction. Integrate and security-review the isolated prototype (source lines 442–453; also 171, 500).
- **P0-3, X11 connection:** Provide explicit opt-in connection between Loopback and the authenticated local user's XFCE X11 session. Do not infer `:0` on multiuser hosts or expose X11 authentication cookies over MCP (source line 456).
- **P0-3, session lifecycle:** Propagate selected `DISPLAY` and `XAUTHORITY` through a supported user-session service/broker lifecycle with consent and revocation (source line 457).
- **P0-3, GUI health:** Replace boolean `DISPLAY` checks with a bounded real X11 probe such as `xdotool getdisplaygeometry`; check exit status; report distinct not-configured, display-unavailable, X11-auth-failed, and policy-denied states; cache bounded probes (source line 458).
- **P0-3, GUI acceptance/safety:** Use a dedicated synthetic window with a test-specific identifier. Verify windows, activation, screenshot, click, type, and key using an explicit allowlist. Require separate consent for personal screens and deny lock-screen/session-switch access (source line 459).
- **P0-3, platform claims:** Distinguish X11 support from unsupported Wayland and do not claim cross-platform desktop automation without implementations and tests (source line 460).
- **P0-4, zplug:** Confirm Homebrew initialization and allow only narrow read-only executable/library access or an approved user-local install. Do not disable Landlock or grant broad `/home/linuxbrew` read/write (source lines 463–464).
- **P0-4, shell isolation:** Use minimal deterministic noninteractive shells for execute/jobs instead of loading interactive zsh startup, Starship, plugin managers, or alias side effects (source line 465).
- **P0-4, shell acceptance:** Prove an interactive PTY resolves zplug without startup warnings; prove noninteractive output is bounded and clean; retain effective sandboxing (source line 466; also 173, 502).
- **P0-5, secure defaults:** Ship restricted/standard least-privilege roots, protected credential exclusions, and `sandbox_required=true` where supported (source line 469; also 174).
- **P0-5, capability authorization:** Separate optional browser/desktop permission from broad shell/fleet privileges. Use per-client scoped capabilities and confirmations; ensure admin-profile changes cannot bypass promised controls (source line 470).
- **P0-5, OAuth acceptance:** Verify reconnect, expired access tokens, refresh rotation, and useful error UI, including the previously reported but unverified sign-up URL issue. Distinguish app/client prompts from server policy approvals (source line 471; also 174, 503).
- **P0-5, sandbox claims:** State clearly that Windows/non-Landlock users do not receive OS sandbox parity (source line 472).

### P1 — production hardening and reliability

- **P1-1:** Add first-run `doctor --json` or dashboard capability matrix for executable/version/path, runtime health, OAuth scopes, policy, sandbox, session broker, and user-facing fixes. Never equate policy enablement with readiness (source line 476).
- **P1-2:** Add structured error categories for unavailable dependency, authorization, approval, sandbox denial, missing/unauthorized GUI session, remote interactive challenge, and operation failure. Preserve correlation IDs; keep underlying errors only in privacy-safe diagnostic logs. Do not label shell nonzero exits as auth failures (source line 477).
- **P1-3:** Add per-platform end-to-end CI with isolated Xvfb desktop fixtures, browser fixtures, OAuth access/refresh lifecycle, reconnect, policy denies, multi-client isolation, crash recovery, jobs, and terminal cleanup (source line 478).
- **P1-4:** Expose runtime tools according to negotiated/selected capabilities. Keep discovery while avoiding unusable tools presented as ready. Use task-context groups with stable IDs and documented scopes (source line 479).
- **P1-5:** Define resource ownership and cleanup: idle timeouts, browser TTL, terminal/process cleanup, per-principal job quotas. Do not stop unrelated processes. Mask secret-looking log text and audit UI/desktop actions safely (source line 480).
- **P1-6:** Make releases reproducible with pinned optional dependencies, checksums/signatures, SBOM, vulnerability scans, unit/integration tests, signed/tagged provenance, installer/upgrade validation, rollback tests, and cross-version migration fixtures (source line 481).
- **P1-7:** Show current health separately from cached topology, broken-dependency remediation, connection provenance, per-client scopes, and actual sandbox enforcement. Do not present listening ports as externally reachable MCP endpoints (source line 482).
- **P1-8:** Test clean release candidates on Ubuntu/Xubuntu, Debian, macOS, and Windows. Publish a platform feature matrix, API documentation, and stable error contracts (source line 483).
- **P1-9:** Require explicit local consent and session binding for browser/desktop controls. Record action events without storing sensitive screen pixels by default. Rate-limit potentially destructive actions (source line 484).
- **P1-10:** Add opt-in anonymized/redacted support-bundle export with inspectable contents before sending. Include immutable capability snapshot and severity for reproducible bugs (source line 485).

### P2 — performance, usability, and product improvements

- Measure p50/p95/p99 for full browser and X11 action chains; separately measure shell startup and command execution (source lines 489, 180).
- Cache Landlock policy paths where safe, preserve authorization checks, and fail closed if the cache is invalid. Treat the cited ~25 ms execute p50 and path-enumeration cost as historical benchmark data, not an audit measurement (source line 490).
- Lazy-load optional browser/document/UI dependencies and split optional features into independently installable, versioned extras to keep the headless footprint low (source line 491).
- Improve dashboard accessibility and mobile usability. Show actionable capability health instead of generic disabled buttons (source line 492).
- Publish install, remote ingress, permissions, limitations, privacy, troubleshooting, smoke-test, X11/Wayland, and browser CLI provisioning guidance (source line 493).
- Differentiate the product through explicit trust boundaries, audited fleet execution, per-client least-privilege OAuth, reliable synthetic browser/desktop fixtures, and diagnostics/recovery UX (source line 494).
- Consolidated P1/P2 recap also calls for live health checks, structured errors, dependency remediation, owned-session cleanup, per-principal quotas/isolation, audit/redaction, cross-platform tests, upgrade/rollback, threat review, support bundles, topology freshness, usable-capability tool selection, shell/Landlock optimization, lazy integrations, and dashboard accessibility (source lines 214–221).

## Proposed release acceptance gates

All nine gates are stated as required before the product is called market-ready:

1. **G1:** Clean checkout, tagged candidate, green CI, verified install/upgrade/rollback for claimed platforms (source line 498).
2. **G2:** Fresh-install preflight reports accurate availability and actionable remediation (source line 499).
3. **G3:** Dedicated-profile synthetic browser fixture passes with per-principal isolation, cleanup, and URL validation negatives (source line 500).
4. **G4:** Synthetic X11 fixture passes in the intended GUI session with consent, lock-state checks, output verification, and no cross-user leakage (source line 501).
5. **G5:** Supported interactive shell has no zplug startup noise; command execution remains deterministic without weakening the sandbox (source line 502).
6. **G6:** Real-client fresh consent, reconnect, refresh, and revoke pass; scope and approval semantics are documented and visible (source line 503).
7. **G7:** File, command, Git, jobs/process, terminal, documents, fleet, and dashboard safety regressions pass without unauthorized writes or remote contact (source line 504).
8. **G8:** Threat review covers browser/desktop privilege, credentials, CSRF/SSRF, tunnel exposure, process termination, and service/broker authentication (source line 505).
9. **G9:** Published support and observability docs include a versioned compatibility matrix and remediation paths (source line 506).

## Secret and sensitive-data handling

The source says no credentials, tokens, protected files, private browser sessions, screenshots, or personal documents were accessed (source lines 37, 390–393). It contains no credential/token values to reproduce. It does mention credential locations and operational identifiers such as user/host names, a public tunnel hostname, remote target names, and a remote account's group membership. Treat these as sensitive operational metadata and do not copy them into public materials (source lines 3, 14–18, 51, 108–116, 112, 286–287). The source itself recommends masking secret-looking logs and reviewing credential boundaries (source lines 480, 505).
