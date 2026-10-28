# Validation ledger

## Previous verified application baseline

Source baseline: PR 8, merged main `609158af085f2fff06b3e96250b33e088f0a211b`.
Detailed commands and coverage are in `docs/CONTINUATION-2026-10-27.md`.

- 137 Python tests passed. One wrapper runs five Node VM tests.
- Ruff passed across runtime, tests, and scripts.
- Bandit found no medium/high issues.
- JavaScript syntax and Git diff checks passed.
- Isolated real HTTP covered enrollment, authenticated connection page, PKCE, stdio echo tool, and revoked token rejection.
- Seven remote metrics paths worked through authorized SSH. This did not verify arbitrary external tunnels or gateways.
- No real ChatGPT, Claude, or Perplexity connector journey passed in that evidence.

These results do not imply the original 13-item upgrade is implemented.
The historical 206-test claim belongs to unpublished recovered source in a stopped sandbox.

## Current migration investigation

No application source changed. No full suite rerun is warranted solely for these documents.

- Synthetic local DCR requests reproduce the two registration rejection strings. Real client payloads remain unknown.
- Shipped JavaScript handler execution and real isolated PUT response confirm the policy envelope defect.
- Read-only desktop Git, process, service, and served-asset checks establish the current source deployment.
- Public metadata and health GETs return 200. These do not establish connector registration compatibility.
- Read-only SSH snapshots cover seven eligible hosts. Sampling is idle and limited. No routes or services change.
- Figma targeted nodes and exports are fetched once. Hosted export URLs are preserved for reuse.

## Handoff document checks

JSON parsing, required-file checks, original 13-item presence, relative-link checks, hosted-image references, and `git diff --check` validate this package.
No test result is invented for the remaining implementation.
`/proof` routes the new Markdown and migration prompt to `/expect-proof` because GitHub/new-account rendering has no application preview route.
The expectation sheet verifies content and references. Actual cross-account access, provider availability, fallback routing, and resumed session behavior remain unverified.
A separate requested report Page presents the investigation. It does not replace application proof.

## Reproduction environment notes

The prior isolated runtime uses `/tmp/loopback-dashboard-venv` and `/tmp/loopback-recovery-proof`.
It runs the real app on declared `dev` port 3000 and a demo worker on 3031.
These are sandbox-only state. Do not copy their auth databases, passwords, or tokens.
The original nominal dev command belongs to token2oauth. RotoXY uses port 2030. Do not alter those services.
Before a later full suite, stop only the demo process that owns 3031 if the existing test needs that port. Restore it afterward if needed.
A new sandbox must use its own declared service and isolated data. Do not assume these `/tmp` files survive.

## Remaining acceptance

See OWNER-REQUIREMENTS.md. Highest priorities: supported source recovery, OAuth contract, tool policy, discovery, host telemetry, and Figma branding.
Capture changed views only after implementation. Preserve current evidence where its covered source remains equivalent.
