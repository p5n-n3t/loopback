# Continue Loopback here

## Scope recovery — 31 October 2026

PR 11 is merged. This branch continues from main `3e71531`.
The new ChatGPT report records successful reads and a write-triggered reconnect.
The supplied Claude file repeats the October 29 report, including its original date.
The candidate removes the read-only discovery hint and publishes each tool's OAuth scope.
Insufficient OAuth scopes return a tool-level authentication challenge before execution.
API token denials and machine policy approvals retain their separate behavior.
`permissions` reports the caller's grant scopes without credentials.
Read [the investigation](docs/reports/SCOPE-RECOVERY-2026-10-31.md).
Desktop deployment and an actual ChatGPT browser retry remain required.

## Reconnect investigation — 29 October 2026

PR 10 is merged. This branch continues from main `38c280b`.
Claude reports seven passing test groups. ChatGPT reconnect remains unresolved.
The candidate corrects invalid-token challenges and validates refresh scope before rotation.
All 234 tests pass. Both fixture client catalogs contain the same 40 tools.
Read [the investigation](docs/reports/RECONNECT-INVESTIGATION-2026-10-29.md) and [protocol evidence](docs/proof/RECONNECT-2026-10-29.json).
The candidate needs the supported desktop updater, then a ChatGPT metadata refresh and a fresh conversation.
The older rollout sections below retain their historical scope.

## Current rollout — 28 October 2026

PR [10](https://github.com/p5n-n3t/loopback/pull/10) is open on `ls/16-review-handoff-and-update-dashboard-desi-1o9R`.
The owner deployed source `41a7c0f` on the desktop through the supported updater.
The running desktop uses Python 3.13.14, retains Landlock, and advertises public, Basic-secret, and form-secret OAuth clients.
The owner recovered dashboard access and installed the ora8 worker through Fleet.
All 12 ora8 worker runtime hashes match the reviewed source. Setup, telemetry, and read-only discovery pass.
Ora8 has no configured bindings. Zero managed processes is expected.
Read [the rollout evidence](docs/proof/ROLLOUT-2026-10-28.json) and [the current checkpoint](docs/CONTINUATION-2026-10-28-RESUMED.md).

## Validation

The owner reports `invalid_client_metadata` during ChatGPT web authorization.
ChatGPT's published CIMD document offers `none` but prefers `private_key_jwt`.
The candidate fix selects the shared public method. It preserves PKCE and rejects unsupported JWT assertions.
All 230 tests pass. Real HTTP consent, exchange, refresh, MCP access, and revocation pass in an isolated gateway.
Read [the CIMD evidence](docs/proof/CHATGPT-CIMD-2026-10-28.json).
This fix still needs the desktop updater and an actual ChatGPT browser retry.

- All 230 local tests pass with the CIMD correction. The prior revision passes PR 10 CI.
- The separate ora8 lab run passes all 13 checks, including real OAuth/MCP, recovery, upgrade, rollback, and cleanup.
- Public health and OAuth metadata return 200 with a descriptive Loopback user-agent.
- Cloudflare rejects Python's default user-agent with error 1010. No Cloudflare setting changes occur.
- Existing RotoXY routes remain unchanged. ora2 and ora4 remain excluded.

## Remaining acceptance

Actual ChatGPT/Claude browser authorization and refresh, live ngrok connectivity, and rollout verification for other workers remain open.
Ora8 worker setup does not create or expose an MCP service. Do not invent a service or public route.
The current MCP connection proves existing agent access, not fresh provider browser authorization.
Use supported account access. Keep credentials and enrollment links out of Git and chat.
The historical checkpoint sections retain earlier blockers. The current rollout evidence supersedes their undeployed-desktop and missing-ora8 statements.
