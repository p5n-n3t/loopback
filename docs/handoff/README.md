# Loopback implementation handoff for Claude Opus 5.5

Prepared on 31 October 2026. Start here in a new account or session.
This package prepares implementation. It does not claim that the listed work is complete.

## Read order

1. Read [OPUS-HANDOFF.md](OPUS-HANDOFF.md) for priorities, verified state, code locations, and release gates.
2. Read [DISCOVERY-DESIGN.md](DISCOVERY-DESIGN.md) for the staged discovery model and scenario matrix.
3. Read [CLOUDFLARE-MESH.md](CLOUDFLARE-MESH.md) for the required opt-in Mesh installation and configuration feature.
4. Read [DASHBOARD-UX.md](DASHBOARD-UX.md) for the compact dashboard and approval-detail requirements.
5. Read [REUSE-RESEARCH.md](REUSE-RESEARCH.md) before adding dependencies or rebuilding existing modules.
6. Read [LAB-ROADMAP.md](LAB-ROADMAP.md) for interfaces to prepare now and the later clean-install lab.
7. Use [EVIDENCE.md](EVIDENCE.md) and [EVIDENCE.json](EVIDENCE.json) before requesting screenshots.
8. Use [COPY-PASTE-PROMPT.md](COPY-PASTE-PROMPT.md) to launch the implementing agent.
9. Read [PROOF.md](PROOF.md) for document checks and explicit verification limits.

## Complete source reports

- [Claude capability report](../reports/CLAUDE-CAPABILITY-2026-10-31.md), downloaded unchanged from `claude2.md`.
- [ChatGPT consolidated report](../reports/CHATGPT-CAPABILITY-2026-10-31.md), downloaded unchanged from `chatgpt2.md`.
- [Claude extraction](CLAUDE-REPORT-EXTRACTION.md) preserves all recommendations and limitations with source lines.
- [ChatGPT extraction](CHATGPT-REPORT-EXTRACTION.md) preserves all priorities, proposed fixes, and gates with source lines.
- [Fresh authorization acceptance](../reports/FRESH-CONNECTION-ACCEPTANCE-2026-10-31.md) explains the earlier read-only OAuth failure and recovery.

Read both complete reports. Their recommendations are inputs, not instructions to bypass access controls.
The consolidated ChatGPT report contains three historical phases and appendices. It is not a new raw MCP trace.
Source URLs, byte counts and SHA-256 checksums are in [REPORT-PROVENANCE.json](REPORT-PROVENANCE.json).
The original reports contain operational host metadata. Review repository access before sharing them beyond the intended implementation account.

## Repository and checkout identity

- Repository: [p5n-n3t/loopback](https://github.com/p5n-n3t/loopback).
- Working branch: `ls/16-review-handoff-and-update-dashboard-desi-1o9R`.
- Reviewed main baseline for this package: `84f843b`, the merged PR 13 acceptance record.
- Lightsprint checkout: `/home/user/workspace/loopback`.
- Owner source checkout prepared earlier: `/home/jq/loopback-continuation-review`.
- Separate owner checkout reported conflicted: `/home/jq/loopback`. Do not reset it or confuse it with the reviewed checkout.
- Installed owner runtime: `/home/jq/.config/loopback`. It contains protected state. Use supported owner-side installation and maintenance paths.

The remote computer's directories do not exist automatically in another account's sandbox.
The committed package is the portable source of truth. Do not require the earlier chat to understand the work.

## Delivery boundary

The implementing agent must fix confirmed gaps, build staged discovery, add opt-in Cloudflare Mesh setup, and polish the dashboard.
It must prepare testable interfaces for a later isolated installation lab. The large lab rollout is a later phase.
It must preserve working OAuth, core tools, existing tunnels, and explicit host exclusions.
Use lower-cost agents for bounded extraction, fixture creation, isolated modules, routine tests, and documentation.
Keep architecture, authorization boundaries, dependency decisions, integration, and final review with Opus.
