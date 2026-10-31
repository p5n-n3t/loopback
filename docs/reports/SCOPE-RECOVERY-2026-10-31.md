# Connector scope recovery — 31 October 2026

## Evidence and limits

The supplied [ChatGPT report](CHATGPT-TEST-2026-10-31.md) records successful reads and a reconnect prompt during `write_file`.
It reports the Custom policy with shell, process control, fleet, and filesystem writes enabled.
The supplied [Claude file](CLAUDE-TEST-2026-10-31.md) repeats the October 29 report and its original timestamps.
It records one transient authentication error in 29 calls, rather than authentication on every call.
The user's current per-call prompt report remains separate evidence. Its exact prompt type is not available here.

Read-only MCP diagnostics confirm the desktop currently uses the Custom profile and active Landlock.
The public unauthenticated MCP challenge still advertises only `scope="loopback:read"` before this candidate's deployment.
The current caller cannot inspect the owner's protected authentication database or provider settings.
No captured request proves which scopes ChatGPT's failing access token contains.

## Reproduced server defects

1. Initial discovery directs clients toward a read-only grant despite advertising read, write, execution, and fleet capabilities.
2. The tool catalog does not advertise each tool's OAuth scope through ChatGPT's `securitySchemes` metadata.
3. An authenticated tool call with insufficient scope returns only HTTP 403. It lacks ChatGPT's tool-level OAuth error result.

These defects reproduce the read-success/write-denial pattern with an isolated OAuth client.
They are confirmed server defects and possible contributors to the reported reconnect failure.
Actual ChatGPT reconnect acceptance remains unverified until deployment and a provider browser retry.

## Changes

- The initial challenge omits the restrictive read-only hint. Protected-resource metadata still lists the supported client scopes.
- Each visible tool carries its enforced scope in `_meta.securitySchemes`, the documented compatibility field.
- OAuth scope failures return an MCP `isError` result with `_meta["mcp/www_authenticate"]` before any tool executes.
- The challenge includes `insufficient_scope`, the required scope, a description, and the resource metadata URL.
- `permissions` returns the current caller's authentication kind and granted scopes without credentials.
- Scope-denial audit events record required and granted scopes without tokens.
- API tokens retain HTTP 403 scope failures. Missing or invalid transport credentials retain HTTP 401.
- Existing refresh grants, credentials, policies, SSH targets, hard denies, and approval checks retain their behavior.

Selecting every dashboard tool does not change an existing OAuth grant.
A read-only grant needs new consent for writes, execution, or fleet access.
Refresh cannot widen a grant. The candidate does not silently grant additional permissions.

## Verification

Nine reconnect contract tests pass in `tests/test_reconnect_contract.py`.
They cover discovery, catalog scopes, denied writes without side effects, consent escalation, file readback, and refresh continuity.
A coding test creates a Python module, rotates the token, reads the file, and executes its function under the Custom policy.
A separate test preserves the `approval_required` response without an OAuth challenge.
The original read-only grant stays read-only after another grant receives write consent.
These tests use isolated server fixtures. Client names are labels, not actual provider sessions.

The complete local suite passes 239 tests.
The verification set contains protocol flow charts, an expectation sheet, and refreshed sandbox consent views.
Sandbox consent screenshots do not prove the owner can complete ChatGPT's reconnect dialog.

## Owner rollout and acceptance

Use the supported updater from a normal terminal on the desktop:

```sh
cd "$HOME/loopback-continuation-review"
git pull --ff-only origin ls/16-review-handoff-and-update-dashboard-desi-1o9R
loopback upgrade --source "$HOME/loopback-continuation-review"
loopback doctor
```

Refresh Loopback's tool metadata in ChatGPT settings after the update.
Ask ChatGPT to call `permissions` and show only `authentication`.
Coding requires `loopback:read`, `loopback:write`, and `loopback:exec` in that result.
SSH requires `loopback:fleet` as well.
Authorize the existing connection with those scopes if its grant is narrower.
If its reconnect dialog cannot open authorization, recreate that connection with OAuth and CIMD, then start a new chat.
This fallback repairs provider connection state. It does not establish the exact cause of the provider's missing setup URL.

Repeat file creation, readback, Python execution, Git status, job and terminal operations, HTTPS, and the authorized ora8 command.
Use one new dedicated test directory. Do not reuse or overwrite the interrupted test's path.
Do not contact ora2 or ora4 or change existing services.
Repeat across a new conversation and after token refresh before declaring the provider issue resolved.

Claude and ChatGPT can require tool-use approval independently of Loopback OAuth and machine policy.
Use the client's approval preference when available. Do not classify shell execution as read-only to suppress prompts.
OpenAI's reported safety rejection originates outside the server. This candidate cannot guarantee that OpenAI accepts every authorized command.

## Sources

- [OpenAI authentication](https://developers.openai.com/plugins/build/auth): tool-level OAuth requires metadata and runtime authentication challenges.
- [OpenAI tool reference](https://developers.openai.com/plugins/reference): `_meta.securitySchemes` supports clients that read compatibility metadata.
- [Local authentication guide](../AUTHENTICATION.md): grant scopes, machine policy, and rollout instructions.
