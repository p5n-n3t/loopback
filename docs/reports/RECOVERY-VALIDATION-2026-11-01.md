# Recovery validation, 2026-11-01

Source root: `/home/jq/loopback`, branch `codex/recovery-validation-20261101`.

## Implemented and verified

- Restored discovery on seven older remote workers by updating only `node_agent.py`, with `node_agent.py.pre-discovery-20261101` backups. All nine registered hosts (including local) subsequently returned discovery snapshots.
- Removed phantom Tailscale TCP routes produced by HTTPS termination metadata.
- Topology Refresh now refreshes read-only discovery before rebuilding the graph. Live authenticated API verification after deployment returned 19 nodes and seven discovered route nodes.
- Backend-port parsing now retains ports when a proxy URL includes a path. This final parser change is locally tested, not yet deployed.
- Replaced the identity test's developer-home rejection with an account-database assertion.
- Eleven discovery tests and 24 dashboard tests pass locally.

## Oracle lab evidence

Host: `ora8`. Directory: `/home/jq/.local/share/loopback-lab/20261101`.
Image: `loopback-lab:20261101`, built from a source snapshot preceding the final backend-port parsing change.

The regression container runs as UID 10001, without host mounts, published ports, capabilities, or networking. Initial run failed at reconnect setup with `module server has no attribute app`; the reconnect class passed independently, and a verbose full rerun passed 357 tests with 17 skipped. This is an unresolved intermittent test-order/initialization failure, not a clean repeated-pass claim.

The separate fresh-install container allowed outbound package downloads but had no host mounts, published ports or provider credentials. `scripts/install-smoke.sh` passed installation, startup, deep doctor, MCP initialization and tools listing, sandboxed execute/pwd, and shutdown. Docker reported no remaining running lab containers afterward.

Local recipe: `lab/oracle/Dockerfile` and `lab/oracle/README.md`.

## External provider observations

Connected read-only Cloudflare APIs returned 13 tunnel records and their route configurations. The sanitized snapshot is private under `.local-recovery/inventory-20261101/cloudflare-routes.json`. It is not yet a runtime provider adapter or a proven association between every tunnel and a physical host.

Bounded unauthenticated initialize attempts to selected known URLs returned 401 with authentication challenges for Nebius, Shodan, Pentest and Loopback; search `/mcp` returned 404; Umma returned 302. These checks do not establish authenticated MCP readiness. The separate public Loopback health check returned 403.

## Remaining work

1. Add a durable runtime Cloudflare metadata adapter with explicit credential references, cache freshness, provenance and tunnel-to-host matching supported by evidence.
2. Complete service/listener/MCP correlation and expose candidates separately from protocol-verified endpoints.
3. Investigate the intermittent suite initialization failure.
4. Extend the Oracle lab to upgrade/rollback, packaging paths and real multi-host topology scenarios. Existing fixture coverage is not a real network lab.
5. Deploy and recheck the final backend-port parser fix.

No claim is made that all discovery scenarios, platforms or live Mesh enrollment are complete.

## Coordinated completion pass

Parent integration adds a 30-second provider refresh budget, concurrent-refresh exclusion, credential-availability diagnostics, exact observed tunnel UUID correlation, explicit-owner mapping support, declared MCP endpoint inventory and selected verification. Declarations are separated from hosting evidence; stdio commands are never executed by discovery. Cross-namespace container backends remain unverified.

A source snapshot returned nine reachable hosts, 19 MCP declarations and four UUID-matched provider tunnels (local Loopback, plus AIORNOT/Nebius/Pentest on ora7). The deployed authenticated topology API returned 55 nodes and 19 declared endpoint nodes. A selected ROTOXY endpoint initialization check returned `auth_required` with HTTP 200 from the admin action. This establishes discovery-action operation, not authenticated ROTOXY MCP verification.

Local full suite passed 373 tests with 13 documented skips after correcting the protected-interpreter test fixture; the 25 dashboard tests and repository lint passed. This pass precedes Claude's lab additions, which require separate integration verification.

Claude Code companion job: `task-mvy4czdr-pgty43`, requested/default model alias `opus`; model receipt not yet verified. It owns Oracle lab additions and initialization diagnosis. ROTOXY Fleet project `token2oauth` reports zero eligible coding accounts/workers. Two self-hosted Oracle jobs executed on ora8 with model receipts: `loopback-correlation-review-20261101` (qwen2.5-coder:3b) and `loopback-install-matrix-review-20261101` (qwen3.5:2b). Outputs were rejected as inadequate (marker-only and inaccurate/truncated advice). Neither counts as an accepted review.

## Resume audit

The Claude companion process died and was automatically reaped; no active Claude Code process remained at resume. Its output was not accepted as completed work. Partial fixture/reload tests were lint-corrected and independently included in integration verification. The assertion that cached modules caused the historical initialization failure was removed: it remains an unconfirmed hypothesis.

The parent real-network runner passed five cases across separate containers on an internal Oracle Docker bridge: MCP initialization, authentication challenge, redirect refusal, ordinary JSON and offline listener. All run-owned resources were removed and cleanup was clean. `lab/network/run.py` is the reproducible runner; `lab/oracle/topology-lab.py` is fixture coverage only.

The actual lifecycle runner passed `install`, `upgrade`, `rollback` and shutdown. It verified code-marker restoration, preserved configuration and compared machine-token/signing-key bytes without printing them. This establishes same-version code change with preinstalled dependencies, not cross-release migration. The earlier Claude script that called `install` for all phases was replaced with the actual lifecycle runner.

Public health returned 403 to the default urllib User-Agent and 200 to an identified `loopback-cli` client. The CLI now identifies its requests; no external access policy was changed. The authenticated live dashboard showed 55 rendered topology nodes with no page errors in headless Chrome. Services offered selected verification controls; desktop and mobile screenshots are private recovery evidence. Large graph layers now wrap rather than stretching into one unreadable row.

A zero-duration discovery cache previously recursed between `refresh` and `inventory`. The refreshed snapshot now returns directly with an unlimited age for that immediate return, and a zero-cache regression test passes.

### Release gates still open

- Automatic Cloudflare refresh is implemented and fixture-tested but requires an owner-provided runtime token environment reference. The imported observation retains its original timestamp and becomes stale; it is not represented as automatic live refresh.
- Container-only connectors on ora3/ora6 lack observed tunnel UUIDs in host process inspection. Their physical-host association remains unknown; no tunnel-name guesses were introduced.
- Authenticated MCP checks require explicit endpoint credentials and scope; anonymous 401 is not a failed service or authenticated readiness proof.
- Live Cloudflare Mesh enrollment, real new-provider networking, cross-release migrations, reboot/systemd inside a disposable VM, Windows/macOS and browser/desktop broker gates remain unverified. Existing implementation ledger deferred rows remain open.

Final combined verification at resume: 398 Python tests pass locally (13 skips) and in the rebuilt Oracle Python 3.11 image (17 skips); 26 JavaScript tests pass. Oracle image digest: `sha256:016efe18b3b9be1535e46cb86fbe3fc25fe71a8a9195d3a9b9a60707e4c7b37b`. The final subsequent cold-cache first-load patch passes all 14 focused discovery/API tests; that small patch is not included in the 398-test image. Repository lint, shell syntax and whitespace checks pass. The service upgrade reports local health, dashboard, ingress, MCP initialization and public health all passing.
