# Implementation brief and prioritized backlog

## 1. Outcome and efficient execution contract

Finish the missing capabilities and the user's dynamic discovery and dashboard requirements.
Treat the current gateway as working infrastructure. Extend it instead of replacing it.
Cloudflare Mesh installation and configuration are required features, with an explicit opt-in setup path.
The later clean-install lab must be feasible from the interfaces built now.

Use Opus 5.5 for planning, trust boundaries, integration decisions, and final review.
Always delegate suitable tedious or low-complexity work to the cheapest available competent agents.
Use the actual model names available in the new account. Do not invent agent capabilities or assume subscriptions.
Limit concurrent agents to the available slots. Assign one file set and one concrete deliverable per agent.
Require each agent to return files changed, evidence, tests, and unresolved questions.
Do not ask every agent to reread the whole repository or regenerate the same screenshots.
Use sequential work if the runtime has no delegation tools. State that limitation once.

Work in small reviewable commits. Run targeted checks after each module, then the relevant integration suite.
Do not repeatedly run full suites or capture unchanged screens without a reason.
Do not weaken security to turn a failing test green.

## 2. What is working and what is not established

Both owner-supplied reports show working OAuth with `read`, `write`, `exec`, and `fleet` scopes.
Core files, coding, Git inspection, jobs, processes, terminals, documents, HTTPS, and the authorized ora8 command pass bounded tests.
The ChatGPT consolidated inventory reports **52 tools: 36 PASS, 1 EXPECTED DENIAL, 2 UNAVAILABLE, 13 NOT TESTED**.
Status-tool passes do not mean browser or desktop actions work.
Claude provides per-tool outcomes but no aggregate total. Do not combine report totals into one invented score.

Code inspection and ChatGPT's action test identify the missing `agent-browser` executable.
Claude's status test reports a missing browser executable without naming it.
Desktop evidence shows a GUI session exists, but the service lacks a usable connection to it.
The proposed authenticated session broker is a design requirement, not an established diagnosis from Claude's status call.
An X11 session exists according to the report. A nonempty `DISPLAY` is not proof of a usable session.
Timed OAuth refresh, the old reconnect dialog, protected-path denial, and dashboard button flows remain incompletely verified.
There is no registered remote MCP node in the reports. SSH targets and SSH workers are different objects.

Earlier fixes are merged through PRs 10–13:

- CIMD public-method negotiation, preserving PKCE and rejecting unsupported assertions.
- Invalid-token transport challenges and refresh scope validation before rotation.
- Per-tool OAuth scope metadata and tool-level scope challenges.
- Caller scopes in `permissions`.
- Fresh `Loopback Coding` authorization restoring ChatGPT writes and execution.

Do not regress these fixes or silently widen an existing grant. Dashboard policy is not OAuth consent.
The earlier runtime check passes 239 local tests and CI on Python 3.11 and 3.13.
That result predates this documentation package and is not proof that future code passes.

## 3. Report corrections and evidence discipline

| Report claim | Interpretation for implementation |
| --- | --- |
| Detached HEAD and `UU runtime/server.py` | Reported at `/home/jq/loopback`, commit `38ee3e3`. Inspect the actual checkout before repair. The reviewed branch is separate. |
| Ship secure defaults instead of admin | Current `runtime/policy.py:default_policy` already selects `restricted` and protected roots. Test fresh defaults and migrations. Do not claim the source ships admin by default. |
| All job logs are readable | The reported installation has empty denied roots and home-wide access. Inspect actual path policy and ownership. Default protected roots and per-principal API isolation are separate issues. |
| `wmctrl` missing causes all desktop failure | The current adapter has an xdotool fallback for enumeration. Fix per-operation readiness and the GUI-session boundary, not merely one missing binary. |
| `doctor --json`, revoke controls, safe sandbox self-test | Inspect existing CLI/admin implementations first. Several primitives already exist. Add missing surfaces and tests instead of duplicating them. |
| Truncation tested | Claude exercised 20 KB. ChatGPT exercised about 1 KB. Neither establishes behavior at the configured 16 MB limit. |
| Proposed URL validator passes 11 tests | It is an isolated owner-side prototype. It is not integrated, reviewed, or deployed. |
| No prompts occurred | This establishes only the reported test runs. It does not disable provider safety checks or establish timed refresh. |

Do not access credential files to prove a denial. Use synthetic protected fixtures and the existing self-test seam.
Do not read personal browser profiles or capture a real desktop to prove adapter availability.
Use PASS, EXPECTED DENIAL, FAIL, BLOCKED, UNAVAILABLE, NOT TESTED, and UNVERIFIED accurately.

## 4. Code map and existing primitives

| Area | Existing files and behavior |
| --- | --- |
| MCP tools, gates, caller context | `runtime/server.py`, `runtime/auth.py`, `runtime/auth_http.py` |
| Policy and synthetic sandbox check | `runtime/policy.py`, `runtime/sandbox.py`, `runtime/admin_api.py` |
| Jobs and terminals | `runtime/jobs.py`; tool boundaries in `runtime/server.py` |
| Browser and desktop | `runtime/browser.py`, `runtime/desktop.py`, `runtime/service.py` |
| SSH identity, aliases and peers | `runtime/ssh_targets.py`; system OpenSSH preserves existing agent/config/ProxyCommand behavior |
| Workers versus remote MCP nodes | `runtime/workers.py`, `runtime/node_agent.py`, `runtime/fleet.py` |
| Current discovery | `runtime/discovery.py` inventories local and registered workers. It does not onboard every visible peer. |
| Installer inventory | `runtime/install_discovery.py` detects binaries, peer count, and simple SSH aliases without remote contact |
| Ingress and topology | `runtime/ingress.py`, `runtime/ngrok_provider.py`, `runtime/topology.py`, `runtime/fabric_api.py` |
| Dashboard | `runtime/static/app.js`, `app.css`, `index.html`, `topology.js`, `dotrix.js` |
| Install and maintenance | `install.sh`, `install.ps1`, `runtime/cli.py`, `runtime/service.py`, `runtime/maintenance.py` |
| Existing lab and evidence | `scripts/remote-lab-smoke.sh`, `docs/REMOTE-LAB.md`, `docs/proof/`, `tests/test_remote_lab_harness.py` |

The frontend is vanilla JavaScript. Do not introduce React or a second gateway to obtain a polished table.
The runtime already uses the official MCP Python SDK. Match its installed v2 APIs rather than copying v1 examples.
The repository license is MIT. Review every reused file and dependency license before copying or bundling it.
Expose optional browser and desktop permissions separately from broad shell/fleet access where the authorization model supports it.
Design any finer capability migration explicitly. Do not break the four existing OAuth scopes or imply current grants already contain new scopes.

## 5. Prioritized implementation ledger

The extraction documents are the complete source-level recommendation ledger.
The rows below consolidate related recommendations into actionable work packages without removing their requirements.

| ID | Priority and work package | Required result and acceptance |
| --- | --- | --- |
| H01 | P0: Source and release identity | Keep an authoritative clean checkout. Diagnose the owner's separate conflicted checkout without reset. Record commit, installed build, migrations and rollback path. |
| H02 | P0: Job and terminal ownership | Bind every operation to stable principal identity, not display name. Scope list/read/write/stop/resize/close to the owner. Reject cross-principal IDs. Preserve explicit admin inspection separately. |
| H03 | P0: Log privacy and quotas | Protect log storage from shell-readable roots, redact secrets, cap per-principal jobs/terminals/output, and migrate old metadata safely. A display label is not an authorization key. |
| H04 | P0: Browser URL and session safety | Replace prefix validation with parsed URL policy. Cover userinfo, controls, ports, lookalike localhost, IPv4/IPv6, redirects, private targets and rebinding. Specify permitted private-network use rather than claiming all HTTPS is safe. |
| H05 | P0: Optional browser runtime | Offer a pinned, verified opt-in `agent-browser` and engine install. Report policy enabled, dependency installed, functional health and supported actions separately. Use isolated profiles and session TTL. |
| H06 | P0: Browser end-to-end fixture | Prove open, snapshot, fill, click, URL and close on a synthetic page with a dedicated profile. Reject session theft across clients. Never use a personal profile. |
| H07 | P0: Authorized desktop broker | Connect to a selected, authenticated user session with consent and revocation. Rate-limit potentially destructive actions. Do not guess `:0`, export cookies, or trust environment strings. Deny locked, switched or unauthorized sessions. |
| H08 | P0: Desktop capability checks | Probe X11 access with bounded commands. Check dependencies per operation, including screenshot support. Keep unsupported Wayland/macOS/Windows states explicit. Use an isolated Xvfb CI fixture and verify action output and cross-user isolation. |
| H09 | P0/P1: Policy defaults and enforcement | Preserve restricted fresh defaults, denied credential subpaths and fail-closed sandbox modes. Make admin risks explicit without silently changing an existing owner's policy. Test migrations and platform limitations. |
| H10 | P1: Shell initialization | Provide deterministic execute/job shells and an explicit interactive PTY mode. Diagnose zplug without reinstalling blindly or granting broad Homebrew write access. Do not delete a lock file without identifying its owner and state. |
| H11 | P1: Error contract | Standardize codes, messages, hints and correlation IDs across tools. Preserve MCP `isError` and OAuth challenges. Keep nonzero process exits distinct from authorization failures. Avoid leaking raw Python exception names or secrets. |
| H12 | P1: Timeout and output shape | Return consistent timeout, truncation, byte count, sandbox, target and enforcement fields. Test boundaries without sending megabytes into the model. Check Unicode byte offsets and partial output. |
| H13 | P1: Terminal efficiency | Add plain/ANSI-stripped output and optional bounded wait-for-output. Preserve a raw mode where needed. Correctly remove OSC sequences and keep offsets or buffer semantics documented. |
| H14 | P1: Patch backup policy | Offer managed backups or explicit opt-in behavior, retention and a clear output path. Preserve compatibility and recovery. Avoid adjacent backup clutter and unexpected Git staging. |
| H15 | P1: OAuth and legacy migration | Show client scopes, grant age/expiry and revocation. Inspect existing revoke controls before adding more. Migrate legacy users deliberately with an expiry plan. Do not disable the owner's current agent path automatically. |
| H16 | P1: OAuth/provider acceptance | Test fresh consent, read-only scope rejection, explicit escalation, actual expiry/refresh/rotation/revoke and restart persistence. Keep old broken provider reconnect UI separate from successful fresh connection recovery. |
| H17 | P1: Capability matrix and self-test | Extend existing doctor/self-test seams with JSON results and actionable remedies. Distinguish enabled, installed, reachable, healthy and verified. Add bounded file/exec/job/PTY/document/network tests and safe approval preflight. |
| H18 | P1: Freshness and topology | Separate peer visibility, DNS, SSH, worker heartbeat, process liveness, local endpoint and external endpoint health. Timestamp every observation. Provide targeted verification and retain stale data as stale. |
| H19 | Required: Staged discovery | Implement [DISCOVERY-DESIGN.md](DISCOVERY-DESIGN.md), including automatic candidate listing, safe probing, one-click worker enrollment and deep route-to-service mapping. |
| H20 | Required: Mesh setup | Implement [CLOUDFLARE-MESH.md](CLOUDFLARE-MESH.md) as a first-class opt-in install/config path. Distinguish Mesh networking from public Tunnel ingress. |
| H21 | Required: Dashboard polish | Implement [DASHBOARD-UX.md](DASHBOARD-UX.md). Fix approval/denial readability, full action details, useful density, accessibility, mobile layout and stage-specific recovery. |
| H22 | P1: Ownership and process lifecycle | Add idle cleanup, restart recovery and PID identity checks. Do not stop another principal's resources or unrelated processes after PID reuse. Audit browser/desktop actions without storing screen pixels by default. |
| H23 | P1: Remote enforcement | Label raw SSH as gateway checks only. Offer explicit per-target command allowlists or an optional supported remote sandbox. Do not imply sudo/docker membership means passwordless privilege. |
| H24 | P1: Support and privacy | Export an inspectable, opt-in redacted support bundle with build, immutable capability snapshot, timestamps, correlation IDs and severity. Do not upload or message it automatically. |
| H25 | P1: Reproducible optional features | Pin verified dependencies and artifact hashes/signatures. Publish SBOM/provenance and scan dependencies. Keep optional browser/GUI/network extras separate from the small headless install. |
| H26 | P1: Cross-platform and migration tests | Cover supported Linux and Debian paths, macOS and Windows claims, install/update/rollback, crash cleanup, schema migration, OAuth, multi-client isolation and mocked provider failures. |
| H27 | P2: Measured performance | Establish p50/p95/p99 for protocol, shell startup, commands, browser, desktop, discovery stages and dashboard updates. Lazy-load optional browser, document and UI dependencies where safe. Cache Landlock paths only with safe invalidation and fail-closed behavior. |
| H28 | P2: Tool presentation | Offer stable capability groups and scopes. Show unavailable tools with remediation. Avoid removing stable identities or silently changing metadata on every probe. Never mark write/exec actions read-only to suppress prompts. |
| H29 | P2: Documentation and compatibility | Publish precise install, ingress, privacy, permissions, troubleshooting and smoke-test docs. State X11/Wayland and platform limits. Fix the README reference to the deleted `assets/loopback-logo.png`. |
| H30 | Prepare now, lab later | Implement the seams in [LAB-ROADMAP.md](LAB-ROADMAP.md). Do not create the large live lab or enroll production hosts during this handoff's initial remediation. |

H02 extends the reported job exposure to terminal lifecycle checks after code inspection.
H29's missing logo reference is a handoff code-reading observation, not a new runtime failure.
Keep all report recommendations in a tracking table with an implemented, already present, deferred, rejected-with-reason, or blocked disposition.
Do not silently discard a recommendation because it overlaps an existing primitive.

## 6. Suggested work sequence and delegation

1. Verify checkout identity, current policy and the capability baseline without changing production.
2. Assign a cheap agent to map recommendations to existing code and tests.
3. Assign isolated implementation agents to error/output contracts and terminal/backup cleanup.
4. Keep principal isolation, browser URL policy and desktop consent design under primary review.
5. Stabilize provider and discovery contracts before parallelizing provider adapters.
6. Build Tailscale/SSH/manual candidate stages and deep inventory fixtures.
7. Add the explicit Mesh installer/provider with route conflict and rollback tests.
8. Delegate dashboard tables, approval details and accessibility checks against the stable APIs.
9. Integrate capability diagnostics, support export and measured caching.
10. Run targeted integration checks, required proof, and a final regression pass.

Use separate bounded agents for source extraction, fixtures, docs, repetitive table changes, and license checks.
Prevent shared-file conflicts with explicit ownership. Review cheap-agent output before integration.
Use [REUSE-RESEARCH.md](REUSE-RESEARCH.md) to select small adapters and existing libraries.
Do not transplant a whole remote-management product to save a few dashboard components.

## 7. Verification, screenshot and production rules

Reuse all existing media through [EVIDENCE.md](EVIDENCE.md). It records current and recoverable historical originals.
Do not capture unchanged login/auth screens merely to show progress.
Take only the changed approval-detail, discovery-stage and representative mobile views required by the final diff.
Required `/proof` still applies on implementation branches. Efficiency does not waive stale or missing evidence.
Prefer assertions, protocol fixtures and source checks for logic. Use a video only for a transient state or necessary motion.

Do not contact ora2 or ora4. Preserve RotoXY and all unrelated routes and services.
Do not read `/home/jq/.ssh`, credential stores, or protected Loopback state through an alternate tool.
Use supported owner-side install/upgrade paths for production. Do not bypass a sandbox denial with another execution route.
The source checkout and installed runtime are separate. An updated Git checkout does not deploy the runtime.
Tailscale SSH may need a human check. Report that state rather than inferring a missing private key.

## 8. Completion and release gates

Retain the report's G1–G9 gates, with current evidence attached to each:

1. Clean authoritative source, tested tagged candidate, full required CI passing, and platform install/update/rollback evidence.
2. Accurate fresh-install capability preflight and actionable remedies.
3. Isolated browser fixture, URL negatives, ownership and cleanup.
4. Consented synthetic desktop fixture, verified action output, session/lock isolation and no cross-user leakage.
5. Deterministic command shells and a supported warning-free interactive terminal mode.
6. Real-client OAuth lifecycle and correct separation of scopes and approvals.
7. Core tool, fleet, dashboard and safety regression coverage.
8. Threat review for SSRF, CSRF, secrets, tunnels, process ownership and GUI privileges.
9. Versioned compatibility, support, observability and recovery documentation.

Add D1: every discovery scenario has a tested outcome, including partial failure and exclusions.
Add M1: opt-in Mesh setup is idempotent, credential-safe, reversible and service-tested in isolation.
Add U1: approval/denial details remain readable and operable at desktop and mobile widths.
Add L1: later lab inputs, cleanup contracts and machine-readable manifests are defined and exercised with fixtures.

The final implementation report must name changed files, actual tests, artifact reuse, new evidence, and remaining open items.
Do not call the installation market-ready or promise universal OpenAI approval without the necessary evidence.
