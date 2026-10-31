# Copy-paste prompt for the implementing account

Copy the block below. It contains no credentials and does not depend on the old chat.
The repository must be accessible to the new account. Give that account repository access separately if needed.
If the handoff PR has merged, read these files from current main. Otherwise fetch the named branch.
The remote owner's `/home/jq` directories are not paths in a new sandbox.

```text
Continue Loopback from the committed handoff. Use Claude Opus 5.5 if it is available.

Repository: https://github.com/p5n-n3t/loopback
Handoff branch: ls/16-review-handoff-and-update-dashboard-desi-1o9R
Handoff entry: docs/handoff/README.md
Direct branch link: https://github.com/p5n-n3t/loopback/blob/ls/16-review-handoff-and-update-dashboard-desi-1o9R/docs/handoff/README.md
Original Lightsprint checkout: /home/user/workspace/loopback

Read the repository's applicable AGENTS.md files. Inspect branch/PR state before editing. Use the existing task branch and open PR when present. Do not replace working functionality or duplicate an existing PR. If the handoff is already merged, follow the new environment's task and branch rules.

Read all of these files, including both complete source reports:
- docs/handoff/OPUS-HANDOFF.md
- docs/handoff/CLAUDE-REPORT-EXTRACTION.md
- docs/handoff/CHATGPT-REPORT-EXTRACTION.md
- docs/reports/CLAUDE-CAPABILITY-2026-10-31.md
- docs/reports/CHATGPT-CAPABILITY-2026-10-31.md
- docs/handoff/DISCOVERY-DESIGN.md
- docs/handoff/CLOUDFLARE-MESH.md
- docs/handoff/DASHBOARD-UX.md
- docs/handoff/REUSE-RESEARCH.md
- docs/handoff/LAB-ROADMAP.md
- docs/handoff/EVIDENCE.md and EVIDENCE.json
- docs/reports/FRESH-CONNECTION-ACCEPTANCE-2026-10-31.md

Implement confirmed missing patches and recommendations. Track every recommendation as implemented, already present, deferred, rejected with a reason, or blocked. H01-H30 and report gates G1-G9 are the main ledger. The reports establish bounded successes, not universal readiness.

Always delegate suitable tedious and low-complexity work to the cheapest available competent agents. Give each agent a bounded deliverable and exclusive file ownership. Keep architecture, security boundaries, integration and final review with the primary agent. Inspect cheap-agent output. Do not spend Opus tokens repeatedly extracting reports or rewriting existing modules. If delegation tools are unavailable, state that limitation once and work sequentially.

Prioritize principal isolation, browser URL/session safety, accurate browser/desktop capability checks, deterministic shells, consistent errors and lifecycle cleanup. Preserve working OAuth, PKCE, caller scopes and explicit consent. Current source already defaults to restricted policy. The owner's separate /home/jq/loopback checkout was reported conflicted. Do not reset it or confuse it with /home/jq/loopback-continuation-review or the installed runtime.

Build multistage discovery from local providers to automatically listed unmanaged hosts, approved probes, selected one-click worker enrollment, remote services/listeners, MCP gateways/proxies/backends, tunnels and hostname/path mappings. Support Tailscale, Cloudflare Mesh, SSH config, LAN/manual and empty-machine cases. Keep peer visibility, SSH authentication, worker heartbeat and endpoint health separate. Preserve exclusions, including ora2 and ora4, and unrelated RotoXY services.

Cloudflare Mesh installation and configuration are required, with an explicit opt-in path. Use current official Mesh documentation and the Cloudflare One Client. cloudflared provides Tunnel, not Mesh. Treat server-node enrollment separately from device enrollment. Check the Mesh/Tailscale address-range overlap before any routing change. Do not enroll production machines without an authorized plan and secure owner-side credentials.

Polish the existing vanilla JavaScript dashboard. Use compact tables, full safe approval/denial action details, clear stage recovery, accessible controls and useful mobile layouts. Backend approval records need safe structured detail, not only CSS changes. Do not truncate safety-critical values or add a frontend framework for styling alone.

Use the GitHub reuse research before introducing dependencies. Prefer existing OpenSSH, MCP SDK and Loopback adapters. Evaluate small permissively licensed components and record exact versions/notices. Do not import a second management plane or copy copyleft code without review.

Minimize screenshots. Read EVIDENCE.md first: it contains current image thumbnails and historical originals. Reuse unchanged views. Capture only changed states needed by the final diff and mandatory proof. Prefer assertions and fixtures for logic. Do not install or script a browser to photograph the Lightsprint preview. Never treat historical media as evidence for new behavior. Run /proof if available and finish with current verification coverage.

Prepare schemas, provider injection, deterministic settings, manifests and owned-resource cleanup for the later isolated clean-install lab. The large lab rollout is later. Its scope includes interactive and unattended installs, packaging entry points, core functionality, tunnels, settings, update/rollback and fresh VM state. Do not use production as a disposable lab.

Do not read protected credentials, personal browser profiles or desktop contents. Do not bypass sandbox denials, broaden OAuth scopes silently, or promise to disable provider safety checks. Do not deploy to production or merge automatically. Deliver reviewed code, targeted test evidence, current proof, a recommendation disposition ledger and explicit open limitations. Commit and push according to the active task's branch rules.
```
