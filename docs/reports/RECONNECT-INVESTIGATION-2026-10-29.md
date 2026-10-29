# Connector investigation — 29 October 2026

## Result

ChatGPT reconnect remains unresolved. Two server defects are reproduced and corrected in this candidate.
All 234 tests pass. Real HTTP checks confirm both corrections in an isolated gateway.
The owner must deploy the candidate before the next ChatGPT test.

The complete [Claude report](CONNECTOR-TEST-2026-10-29.md) is preserved as supplied.
Claude reports seven passing test groups, one transient authentication error, and a successful second ora8 SSH attempt.
These are owner-supplied observations. This investigation does not independently replay that provider session.
A 22-minute session does not establish a successful one-hour token refresh.

## Shared ChatGPT response

The [shared response](https://chatgpt.com/s/t_6ae313e7112881918e098e7a575b9d88) contains this browser-inspection error:

> Browser access requires the originating browser context.

The response says its diagnosis is incomplete. It provides no raw Loopback tool call, OAuth exchange, or gateway failure trace.
This browser error is separate from the earlier owner-reported missing browser setup URL.
It does not prove that Loopback rejected an OAuth token or that the request reached the gateway.

## Confirmed corrections

### Invalid credential challenge

The deployed server returns HTTP 401 for a rejected credential, but omits `error="invalid_token"` from `WWW-Authenticate`.
The candidate includes that error when a credential was supplied. Missing credentials retain the initial discovery challenge.
This gives clients the [RFC 6750 invalid-token signal](https://www.rfc-editor.org/rfc/rfc6750#section-3.1).
Expired, revoked, and malformed credentials remain rejected. No credential receives additional access.
The audit event records credential presence and the error name. It never records the credential value.

### Rejected refresh scope consumes the grant

The previous handler rotates a refresh token before it validates the requested scope.
An unknown or wider scope returns `invalid_scope` after rotation.
A corrected retry then triggers replay detection and revokes the grant family.
The regression reproduces this sequence for both unknown and wider scopes.

The candidate validates the scope inside the existing rotation lock before it marks the token as rotated.
A corrected retry succeeds. Genuine reuse of a successfully rotated token still revokes its family.
Refresh success and validation failure now produce token-free authentication events.
This does not establish that ChatGPT sent either invalid scope in the failing session.

## Checks against the report's hypotheses

| Hypothesis | Evidence and conclusion |
| --- | --- |
| Discovery | Health and all three public metadata routes return 200 with the descriptive diagnostic user-agent. Issuer and public resource match. This does not prove every provider-origin request passes Cloudflare. |
| Token refresh | The candidate passes authorization, exchange, rejected-scope retry, refresh, and revoked-token checks over real HTTP. The actual failing ChatGPT refresh remains uncaptured. |
| Principal mapping | Fixture clients named ChatGPT and Claude authenticate with different scopes and receive the same 40-tool catalog. The names do not represent actual provider sessions. |
| MCP session IDs | The transport is stateless. Both fixture calls tolerate a stale `Mcp-Session-Id` and issue no session header. |
| Redirect and registration | Existing CIMD, DCR, exact redirect, PKCE, resource and replay tests pass. The failed provider request's actual client and redirect remain unknown. |
| 17 versus 40 tools | Catalog filtering uses the shared machine policy, not the caller name or OAuth scopes. Tool invocation still enforces scope. The two fixture catalogs contain 40 identical names. ChatGPT's stored catalog, tool toggles and connection identity need inspection. |

[Sanitized protocol evidence](../proof/RECONNECT-2026-10-29.json) records the results and source hashes.
The public invalid-credential probe comes from this investigation. It is not the owner's failing ChatGPT request.

## Historical authentication comparison

The available `v0.2.0` tag contains runtime health version `2.2.0-standard`.
Its OAuth exchange returns the shared machine token with `expires_in: 315360000` and no refresh token.
Its refresh branch returns that same machine token without validating a refresh token.
The current server uses distinct scoped access tokens, rotating refresh tokens, resource binding, and revocation.
Current source defaults are one hour for access and 30 days for refresh. Effective desktop settings are not read.
This difference makes refresh handling important. It does not justify restoring the historical credential behavior.
The exact revisions of the owner's other v1/v2 deployments are not available.

## Tool annotations and approvals

The current descriptors already declare read-only, destructive, idempotent, and external-effect hints.
Reads, writes, execution, jobs, and terminals use different annotations based on their behavior.
The candidate preserves these annotations. The host still controls approval prompts independently.
The admin profile does not disable ChatGPT or Claude approval controls.
The ora8 account's sudo and Docker group membership does not establish passwordless sudo access.

## Next owner check

1. Apply the candidate with the supported updater from the prepared source checkout.
2. Refresh the existing custom MCP connection's metadata in ChatGPT.
3. Confirm its endpoint is `https://loopback.ziopsyop.tech/mcp` and inspect the enabled tool names.
4. Start a new conversation and call `machine_info` before a write test.
5. If it fails, record the exact tool error and UTC time. Do not repeatedly reconnect.
6. Inspect Authentication events and Logs/Audit in the owner dashboard for that time window.

The authenticated owner endpoints `/api/admin/auth` and `/api/admin/audit?limit=100` expose existing event views.
Use the owner's normal authenticated browser. Do not share cookies, tokens, passwords, codes, or unredacted request headers.
Relevant events include `mcp.auth`, `client_auth_failed`, `oauth_refresh_failed`, `oauth_tokens_issued`, and `refresh_token_reuse_detected`.
Protected desktop runtime files remain inaccessible to this MCP session. This investigation does not bypass that policy.

[OpenAI's refresh procedure](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata) requires a fresh conversation after metadata changes.
A successful server test cannot prove that ChatGPT has the correct connection record or current tool catalog.

## Deferred findings

- Terminal output timing, escape stripping, and shell startup noise require a separate terminal change.
- A Tailscale check is a separate host authentication requirement. This candidate does not change SSH policy or approve a check.
- `patch_file` retains its documented backup behavior.
- No existing routes, services, credentials, denied roots, or approval settings change. ora2 and ora4 are not contacted.
