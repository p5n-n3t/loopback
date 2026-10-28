# Continue Loopback here

## Current acceptance — 28 October, 10:56 UTC

The dedicated ora8 lab run now passes all 13 checks at source `9c19148262ada84be1f5d1345afbf8bb43ef9f43`.
Installation, real OAuth/MCP, password recovery, repeat installation, upgrade, rollback, and cleanup pass.
All 225 local tests, Ruff, syntax checks, and Bandit medium/high checks pass.
Read [the saved result](docs/proof/ORA8-LAB-RESULT-2026-10-28.json).
The owner authorizes a continuation PR on the existing branch. Earlier instructions against a new PR are superseded.
Desktop deployment, permanent ora8 registration, actual ChatGPT/Claude browser acceptance, and live ngrok acceptance remain open.
The access-blocker notes below describe the earlier state. Tailscale SSH now works for `loopback-lab`.

Latest source branch: `ls/16-review-handoff-and-update-dashboard-desi-1o9R`.
Read [the current checkpoint](docs/CONTINUATION-2026-10-28-RESUMED.md) first.
Read [current proof links](docs/proof/RESUMED-EVIDENCE.json) only when needed.

- PR 9 is merged. Its source is imported and preserved here. Do not rebuild it.
- New account/setup-key, ngrok/provider, installer, DOTRIX, and Edit work is also committed and pushed.
- 223 Python tests pass. Installer smoke, lint, security checks, and six focused Services Node tests pass.
- Six current UI captures, three flow charts, and one CLI expectation sheet are complete. Do not repeat them merely because the account changes.
- Code and sandbox proof are complete with external acceptance items. Continue with owner deployment/client acceptance, not another implementation sweep.
- The owner desktop remains on `609158a`. No continuation changes are deployed there.
- Real ChatGPT/Claude browser sign-in and live ngrok acceptance remain external checks.
- Latest Figma wordmark export succeeds and replaces the login, consent, and navigation assets. All six affected views are refreshed.
- New VM ora8 is online on Tailscale at 100.105.169.121 but is not a registered Loopback worker.

Fetch this branch to read these files. Preserve the new session's assigned working branch and any local changes.
Use supported account access. Personal subscriptions, sandbox files, and private session memory do not transfer through Git.
Use the cheapest adequate available model. Use Sol for integration and Luna for routine work. Astra needs a concrete unresolved problem.
Save validated milestones to Git and keep this checkpoint current. Do not reread every historical handoff or image.

The continuation is pushed on this branch after PRs 8 and 9 merged. It is not on main or deployed. No replacement PR is opened.

## Active follow-up: ora8 live test request

The owner now authorizes ora8 registration, SSH through the desktop, and a small separate test environment.
Do not stop at the earlier handoff. Finish all work available here before desktop continuation.
Read `docs/REMOTE-LAB.md` and `docs/proof/LIVE-WORKER-INVENTORY-2026-10-28.json` for this follow-up.
The new runner is implemented and syntax/guard checked, but it has not run on ora8 yet.
Tailscale SSH requires an owner identity check. Ordinary SSH has no trusted ora8 key in this sandbox.
The desktop's protected configuration prevents registry writes and password recovery through the MCP shell.
The owner can run `loopback auth enroll` locally to set a password, without sending secrets to the agent.
The existing ora1 worker lacks discovery. Six real host snapshots succeed. ora7 times out on the bounded attempt.
A newly found split-argument credential-redaction defect is fixed. Eight discovery tests pass.
