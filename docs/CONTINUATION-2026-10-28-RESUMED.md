# Resumed engineering checkpoint — 28 October 2026

## Latest acceptance result — 10:56 UTC

The owner authorizes a continuation PR from the existing branch.
The reviewed source `9c19148262ada84be1f5d1345afbf8bb43ef9f43` passes the dedicated ora8 lab run.
The saved [redacted result](proof/ORA8-LAB-RESULT-2026-10-28.json) records all 13 successful checks.
Fresh installation, real OAuth/MCP for three client methods, recovery login, repeat installation, upgrade, rollback, and shutdown pass.
The test uses `loopback-lab`, private temporary directories, and IPv4 loopback port 3147.
An independent check confirms that the listener is closed and the temporary source checkout is removed.
No production services, routes, credentials, or worker registrations change.
The first wrapper attempt rejects an incorrect expected revision before installation. The corrected exact-revision run succeeds.

All 225 local tests pass in a fresh Python 3.11 environment.
Ruff, Python compilation, JavaScript and Bash syntax, and diff checks pass.
Bandit reports no medium or high findings.
The previous ora8 identity blocker is resolved for the dedicated lab account.
Permanent ora8 registration and production rollout remain separate work.
Actual ChatGPT/Claude browser acceptance and a live ngrok account remain unverified.
Desktop deployment still needs the owner's normal terminal because the MCP sandbox denies its runtime configuration directory.
The earlier checkpoint sections below retain their historical validation and limits.

Branch: `ls/16-review-handoff-and-update-dashboard-desi-1o9R`.
PR 9 is merged as main `66c4891`. This branch imports it in merge commit `e696322`.
Use `git log -1` for the latest saved continuation commit.

## Completed in this continuation

- Confirmed desktop MCP access works with Landlock. The desktop still runs `609158a`, before PR 9.
- Read the requested DOTRIX directory through authorized access. Imported eight relevant vector geometries with source attribution.
- Added separate generated dashboard username/password, compatible migration, and account editing with session revocation.
- Added reusable consent-only connector setup keys, hash storage, rotation, and explicit separate grant revocation semantics.
- Integrated fresh-install credential generation and optional validated settings files.
- Added read-only installer inventory, contextual ngrok choice, numbered/elapsed/color progress, readable reports, and the help verb.
- Evaluated provider viability against primary documentation. Added managed ngrok with stable origin, explicit exposure confirmation, and process ownership.
- Added ngrok dashboard, wizard, topology, startup, and shutdown integration.
- Services Edit now scrolls to the populated form and focuses the port field.
- Documented actual client/account constraints and a bounded refusal diagnosis. No provider safety bypass is claimed.

## Validation and limits

Focused auth tests: 30 pass. OAuth client tests: 15 pass. Provider tests: 17 pass.
Installer tests: 8 pass. CLI startup tests: 8 pass. Dashboard Node wrappers: 4 pass.
All 223 Python tests pass, including Node wrappers. The later Edit change passes all six focused Services Node tests.
The isolated install/health/MCP-execute/status/report/shutdown smoke test passes.
Ruff passes. Bandit reports no medium/high findings. JavaScript syntax and diff checks pass.
Six server-side screenshots succeed in one batch. No screenshot retries or videos occur.
The images are visually inspected as one contact sheet. They cover login mobile, account/setup keys, public tunnels, wizard choices, consent, and Services Edit.
The later final milestone below supersedes this earlier proof-progress note.

No desktop/VM deployment, service restart, route edit, or credential rotation occurred.
Real browser ChatGPT/Claude consent and ngrok provider-account connectivity remain unverified.
The current Lightsprint MCP connection does not establish fresh browser registration success.
Only the eight matching DOTRIX assets replace corresponding glyphs. Other icons keep the existing fallback.

## Next steps

1. Review the saved continuation branch. PRs 8 and 9 are merged, but these later commits are not on main.
2. Arrange owner deployment and real client acceptance. Use the owner terminal and supported updater below.
3. Register ora8 only after its SSH account and access details are confirmed.

## Remaining external acceptance

- A fresh ChatGPT/Claude browser sign-in, consent, refresh, and reconnect requires the owner's actual clients and their redacted metadata.
- A live ngrok route requires its installed agent and an authorized account/configuration. No credentials are available here.
- Cloudflare quick tunnels are intentionally unsupported for MCP because official documentation excludes SSE.
- Localtunnel and tunnelto remain evaluated options, with documented stable-origin or streaming acceptance limits.
- Desktop and worker rollout require the documented owner-approved updater. MCP access protects the runtime credential directory.
- ora2 and ora4 remain excluded. This continuation contacts neither host.

Use Sol for remaining integration. Astra is not needed on current evidence.

## Recovery checkpoint — 07:05 UTC

Latest verified implementation revision: `5ebbbfd51ea13fba44c10a213c07d2e0cf185532`.
All code is committed and pushed. This checkpoint update contains documentation only.
The current screenshot index is `docs/proof/RESUMED-EVIDENCE.json`. It contains hosted image links, not inline image data.
Do not infer deployment from these captures. They render the isolated sandbox.

Sandbox-only runtime: `/tmp/loopback-recovery-proof`, real server on declared `dev` port 3000.
The current server is the process identified by that directory's `server.pid`, started by this run.
Port 3031's isolated recovery-demo worker is intentionally stopped for port-sensitive tests. Do not stop unrelated processes.
The proof browser session is temporary. A new account must mint its own sandbox session by runtime/data means.
Do not copy auth databases, tokens, or cookies into Git.

The test interpreter is `/tmp/loopback-dashboard-venv/bin/python`.
Ruff/Bandit run with `uv tool run ruff ...` and `uv tool run --from bandit bandit ...`.
The local Mermaid renderer already exists in `/tmp/flow-diff`. Reuse it if available. Never install a browser for preview captures.

A new account needs legitimate repository access. Git preserves this state. It does not transfer personal subscriptions or private workspace sessions.
Read `CONTINUE-HERE.md` first, then this checkpoint. Avoid replaying the old migration documents or screenshot sweep.

## Owner follow-ups after the 07:05 checkpoint

- The owner requests the latest wordmark from Figma file `r7Rdu2N3ttXa1K30PDfcnQ`, node `31:188`.
  The initial export returned HTTP 403 `Invalid token`. The owner's reconnection restores access.
  A fresh PNG export now succeeds. The hosted asset replaces navigation, login, and consent wordmarks.
  Source raster size is 1029 by 377. The raster stays outside Git. The hosted URL is in the Figma manifest.
  The new 32px display height preserves readability. All six affected views are refreshed and visually inspected.
- The owner adds a new VM, `ora8`, to the intended fleet scope.
  Fresh read-only Tailscale status confirms it is online at `100.105.169.121`, DNS `ora8.bigeye-escalator.ts.net.`.
  Its IPv6 is `fd7a:115c:a1e0::8d29:a97a`.
  The managed SSH target list still contains only ora1/3/5/6/7/aws1/aws2. ora8 is not registered or installed as a worker.
  Obtain or confirm SSH access details before registration or rollout. Do not guess the login account or deploy automatically.
  This observation contacts no service on ora8. ora2 and ora4 remain excluded.

## Final local milestone — 07:23 UTC

Implementation revision: `e9dbb73`. Later documentation commits update this ledger and evidence links only.
Two explicit GPT-6 Luna agents complete independent, bounded audits of discovery/metrics and requirements 2/4/5/7/8/13.
Neither audit identifies a confirmed remaining in-scope code defect. No Astra escalation is necessary.
This result does not establish unavailable provider-client or production deployment acceptance.

The latest asset passes 30 auth and four dashboard tests. An initial test command uses an invalid module path.
The corrected discovery commands pass. This is a test invocation issue, not an application failure.
The earlier 223-test full suite and real isolated smoke remain valid for the unchanged integration logic.

Proof runs `/visual-proof`, `/logic-proof`, and `/expect-proof`.
The set contains six current UI stills, three source flow charts, and one CLI expectation sheet.
The wordmark requires one six-view refresh after the initial six captures. There are no retries or videos.
All images are visually inspected. The CLI sheet has six Met expectations and one Unverified owner-terminal/deployment expectation.
Provider process tests use mocks. Consent uses a sandbox client. Real terminal output uses labelled sample report data.
Every changed logic flow has named local test/runtime evidence. Real provider-account and browser acceptance remain open.
Thirteen superseded artifacts leave the active set. Their historical URLs remain in the evidence files.
No new Page is published. Read `docs/proof/RESUMED-EVIDENCE.json` for current hosted links.

### Current requirement status

| Requirement | Saved state and remaining boundary |
| --- | --- |
| 1 DOTRIX | Eight licensed directory geometries are imported. Remaining unmatched icons retain existing code-native fallbacks. |
| 2 Tunnel evaluation | Primary-source evaluation and fixed-host ngrok support complete. Live account acceptance remains open. |
| 3 Discovery before setup | Bounded local inventory and contextual setup choices complete. Inventory does not connect to SSH hosts. |
| 4 Client parity/refusal | Capability investigation documented. Actual refusal payloads and browser sessions remain unavailable. No parity or refusal fix is claimed. |
| 5 Provider controls | Cloudflare and fixed-host ngrok controls complete. Localtunnel/tunnelto acceptance is not established. |
| 6 Shield | Preserved from merged PR 9 with its regression coverage. No production activation changes occur here. |
| 7 Dashboard account | Separate username/password, compatible migration, guarded edits and session revocation complete. |
| 8 Setup key | Hashed reusable consent-only key, rotation and separate grant semantics complete. External consent acceptance remains open. |
| 9 Services | Existing Connect behavior preserved. Edit scroll/focus corrected and tested. |
| 10 Discovery | Preserved PR 9 registered-target discovery and ownership. ora8 must first become a registered target/worker. |
| 11 Metrics | Preserved PR 9 whole-host sampling and explicit unavailable states. Owner desktop is still undeployed. |
| 12 Branding | Latest Figma wordmark, symbol, gradients, login and eight DOTRIX glyphs integrated. |
| 13 CLI | Help, human reports, explicit JSON, progress, preflight and settings validation complete. |

### Owner-controlled deployment path

Run these commands only in the owner's normal terminal when ready to deploy. They are not executed in this session.
Use a new checkout directory. If the chosen directory exists, inspect it rather than overwrite it.

```bash
git clone --single-branch --branch ls/16-review-handoff-and-update-dashboard-desi-1o9R https://github.com/p5n-n3t/loopback.git "$HOME/loopback-continuation-review"
loopback upgrade --source "$HOME/loopback-continuation-review" --check
```

The `--check` path prints the plan and returns before backup, stop, or runtime replacement.
Inspect the source revision and displayed configuration path. Preserve the existing desktop account and state.
When the owner approves that plan, the supported updater is:

```bash
loopback upgrade --source "$HOME/loopback-continuation-review"
loopback doctor
loopback report
```

The updater requests confirmation, creates a state backup, stages the runtime, verifies it, and rolls back a failed upgrade.
These commands do not install workers on ora8 or other hosts. Do not reset Tailscale or RotoXY routes.
If the executable or configuration path differs on the owner machine, inspect that installation before any write.
The protected runtime directory is not accessible through this MCP shell. Do not weaken its sandbox to deploy.

## Active owner follow-up — live ora8 testing

The owner authorizes ora8 registration and an isolated test environment separate from jq.
A real desktop metadata request still advertises only `token_endpoint_auth_methods_supported: [none]`.
The desktop therefore still lacks PR 9's confidential-client fix. Do not report that deployed authentication is fixed.
The candidate source supports public, Basic-secret, form-secret, PKCE, refresh and mixed-grant narrowing.
Browser-specific payloads remain unknown until the actual clients run the new source.

Read-only snapshots run candidate collector functions in memory on registered SSH hosts, without installing files.
Successful hosts: ora1, ora3, ora5, ora6, aws1, aws2. ora7 times out in a 35-second bounded attempt.
Cloudflared processes appear on ora3 and ora6. No visible Tailscale Serve/Funnel routes appear on those six hosts.
This does not establish that a different network namespace or unreadable account has no route.
The detailed, non-secret port inventory is `docs/proof/LIVE-WORKER-INVENTORY-2026-10-28.json`.
The ora1 installed worker has no `discover` function. Existing installations must be updated to use new inventory.
The first diagnostic harness exceeds SSH's argument limit. A smaller extraction then omits a function and fails.
The corrected dependency-complete extraction succeeds on six hosts. These are diagnostic harness failures, not worker product failures.

A concrete product defect is corrected: split Cloudflare token and credential-path arguments are now redacted.
Eight discovery tests pass, including fake `/proc` split/inline argument regression coverage.
The isolated runner at `scripts/remote-lab-smoke.sh` covers local installation, OAuth HTTP exchanges, recovery,
repeat installation, updater backup, rollback and cleanup. Bash, Python syntax and the non-lab guard pass.
No ora8 run is claimed. A Tailscale owner identity check and registration remain required.
The access-check background job expires without approval. It makes no host changes.
Never preserve the temporary Tailscale approval URL or any enrollment code in Git.

Login recovery requires the owner's normal desktop terminal because `~/.config/loopback` is hard-denied to this MCP shell.
Use `loopback auth enroll`, then open the generated localhost link to choose a password. Do not paste that link or password into chat.
Older installed builds have password-only login. The new account username is not retroactively created by an upgrade.
The isolated Lightsprint preview has its own test account and is not the owner's installed dashboard.

Final follow-up code revision: `7d1df70`. The strengthened lab runner verifies actual nonempty MCP tool results, not HTTP status alone.
Independent real HTTP checks in the existing Lightsprint instance pass public, Basic-secret and form-secret registration,
PKCE consent/exchange, refresh and nonempty tool results. Mixed grants narrow correctly. Unsupported-only grants fail.
The proof refresh adds one combined flow chart and one source-based ora8 expectation sheet. Existing unchanged UI captures remain current.
The lab sheet has four Met code/guard expectations and two Unverified runtime/account expectations. No ora8 success is claimed.
Next: the owner completes the Tailscale identity check and uses the normal local CLI to register ora8 and recover dashboard access.
Then confirm the SSH user, provision the dedicated lab account, and execute the saved runner. Do not repeat the earlier screenshot sweep.
