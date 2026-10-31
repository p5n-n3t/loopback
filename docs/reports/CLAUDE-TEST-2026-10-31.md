# Loopback Connector: Claude Test Report and ChatGPT Reconnect Brief

Prepared by Claude (Sonnet 5.5, claude.ai) for the Loopback developer AI. Date: October 29, 2026. Machine: jq-xubuntu1, user jq. Endpoint Claude used: https://loopback.ziopsyop.tech/mcp

## 1. Summary

- All 7 authorized tests passed in Claude. Two events were not clean: one transient client-side authentication error, and one Tailscale SSH check on ora8 that cleared on a second attempt.
- The Claude connection stayed up for the whole session: 29 Loopback tool calls (27 clean, 1 transient auth error, 1 Tailscale timeout), with no reconnect prompt at any point.
- That is the useful contrast for ChatGPT. In Claude the connection is stable and every tool class worked. In ChatGPT the plugin page says Connected with 17 tools, but the chat asks to reconnect and the reconnect fails. The gateway and tools are healthy for Claude, so the fault most likely sits in how ChatGPT authenticates, stores or refreshes its connection, or in what the gateway returns to ChatGPT.
- Claude sees 40 Loopback tools while ChatGPT lists 17, so the two clients are not getting the same catalog.

## 2. Test results (Claude)

| # | Test | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Machine, user, roots, restrictions | PASS | machine\_info: jq-xubuntu1, user jq, Ubuntu 26.04.1, zsh, profile admin. permissions: sole allowed root is \~ (rw); denied roots include \~/.ssh, \~/.gnupg, \~/.aws, \~/.config/loopback; approvals hard-only; Landlock sandbox active. |
| 2 | Unique test directory | PASS | Created /home/jq/loopback\_test\_20261029\_102452\_473612, returncode 0. |
| 3 | Python function and unittest, edit, rerun | PASS | Both files read back. Run 1: 2 tests OK. After editing function and test with patch\_file: 3 tests OK. The first patch\_file call hit a client-side auth error (section 3); the identical retry succeeded. |
| 4 | Git init, stage, diff, status | PASS | Branch main, 2 files staged, staged diff and status shown, no commit, no push. |
| 5 | Background job and terminal | PASS | Job 5ea2909e7d36 printed its marker and exited 0. Terminal pwd returned the test directory; terminal closed (alive false). |
| 6 | HTTPS to example.com | PASS | HTTP 200, curl exit 0, 0.38 s. |
| 7 | hostname and id on ora8 | PASS (2nd attempt) | First attempt timed out (returncode -9) on a Tailscale SSH check. Second returned ora8 and uid=1002(jq) gid=1002(jq) groups=1002(jq),27(sudo),106(docker) in 5.4 s. |

## 3. Error log (exact text, classified)

| Event | Exact text | Classification |
| --- | --- | --- |
| patch\_file, first call | The user didn't complete authentication. They can try again. | Client-side (Claude app) authentication-style error. Not an OS error, not a Loopback policy denial. The text is a plain sentence, unlike Loopback's structured JSON results, which is why I attribute it to the client. The identical retry succeeded with no new login. |
| ssh\_execute on ora8, first attempt | `# Tailscale SSH requires an additional check.` then `# To authenticate, visit: https://login.tailscale.com/a/[redacted]` | Interactive Tailscale check on the remote host. The gateway allowed the command (enforcement gateway-path-check). Not bypassed; the link was not opened. |
| ora8 in ssh\_target\_list | `ssh probe timed out after 8s` | Health probe status `unreachable`, probably the same Tailscale check (inference). |

## 4. What Claude observed about connection and auth

- Stability: 29 calls across roughly 22 minutes of server timestamps (10:24 to 10:47 local), plus a later ora8 retry. No re-authentication beyond the one transient error.
- Tools exercised (14): machine\_info, permissions, execute, write\_file, read\_file, patch\_file, job\_start, job\_output, terminal\_start, terminal\_write, terminal\_read, terminal\_close, ssh\_target\_list, ssh\_execute.
- Server-side approvals are off in this profile: permissions reports profile admin, approvals `hard-only` (*No soft approval prompts; hard-deny rules still apply.*). The one auth-style error therefore did not come from Loopback policy.
- The gateway identifies the caller: job and terminal results include a `principal` field set to Claude. Loopback has a per-client principal concept, so check how ChatGPT is mapped (hypothesis C below).
- Catalog mismatch: this Claude session exposes 40 Loopback tools (browser\_status, connections, desktop\_status, diagnostics, docx\_*, execute, git\_info, job\_*, list\_dir, machine\_info, node\_*, patch\_file, pdf\_*, permissions, policy\_status, process\_*, read\_file, search\_text, ssh\_*, tail\_file, terminal\_*, write\_file, xlsx\_*). ChatGPT reportedly lists 17.
- Not visible from Claude: OAuth scopes, token lifetime and refresh behavior, tool annotations, gateway logs, Loopback version.

## 5. ChatGPT reconnect failure

Symptoms (reported by the user, not observed by Claude):

- Authentication completes normally. The plugin page shows Connected and 17 tools.
- In a chat, the prompt triggers a reconnect request, as if the connection dropped inside the conversation.
- The plugin page still says Connected. Clicking Reconnect in the chat produces an error to the effect that the app has no sign-up URL (exact wording still to be captured).
- Earlier Loopback v1/v2 connectors worked in ChatGPT without per-chat reauthorization.

Hypotheses, unverified, in the order I would check them:

- A. Discovery metadata. The first connect works, so discovery works at least once. If the in-chat reconnect relies on stored metadata that lacks an authorization or sign-in URL, the stored app record may be incomplete. Verify that an unauthenticated request to /mcp returns 401 with a WWW-Authenticate header carrying resource\_metadata; that /.well-known/oauth-protected-resource (and the path-suffixed /.well-known/oauth-protected-resource/mcp) names the authorization server; and that the authorization server metadata (RFC 8414) lists authorization\_endpoint, token\_endpoint, registration\_endpoint if dynamic registration is used, code\_challenge\_methods\_supported including S256, and grant\_types\_supported including refresh\_token. The issuer must match exactly.
- B. Token lifetime and refresh. Connected on the plugin page but failing in chat is the classic shape of an expired or non-refreshable access token. Check whether ChatGPT receives a refresh token, whether the refresh grant works, and the access-token TTL. Claude's session held for 22+ minutes with no re-auth.
- C. Principal handling. If ChatGPT requests arrive under a client\_id or principal the gateway does not map, or the gateway re-checks the principal on each tools/call, an authenticated account could still get 401 or 403, which ChatGPT would surface as a reconnect request.
- D. Session handling. If the gateway issues an Mcp-Session-Id and ChatGPT resumes a chat with a stale one, a 400, 401 or 404 on resume would look like a disconnect. Check how unknown or expired session IDs are handled (clients are expected to re-initialize on 404).
- E. Redirect URI and client registration. If the app record dates from an older registration (v1/v2 or an earlier attempt), the redirect\_uri ChatGPT presents on reconnect may not be on the allowlist, or the stored client\_id may be stale.
- F. Scope-gated catalog (17 vs 40). A narrower token could explain tools failing outside the granted scope.

Read-only diagnostics to run against the gateway:

```
curl -si -X POST https://loopback.ziopsyop.tech/mcp -H 'content-type: application/json' -d '{}'
curl -s https://loopback.ziopsyop.tech/.well-known/oauth-protected-resource
curl -s https://loopback.ziopsyop.tech/.well-known/oauth-protected-resource/mcp
curl -s https://loopback.ziopsyop.tech/.well-known/oauth-authorization-server
```

Then trigger the failing ChatGPT reconnect once and read the gateway log for that window: which path returned the 4xx, the status, the WWW-Authenticate value, client\_id, redirect\_uri, User-Agent and any Mcp-Session-Id.

## 6. Approving every tool call

What Claude saw: one authentication-style error in 29 calls, cleared by an identical retry. Per-call authorization was not needed in this session. A per-tool prompt is still plausible if patch\_file sits in a permission category different from the read-only and write-only groups already set to Always allow, since it edits existing content and a client may treat it as destructive. Unconfirmed.

Recommendations (none of these bypass a client control):

- Client side: set Always allow per tool for everything comfortable to automate, and check whether edit or destructive tools form a separate category. The ChatGPT equivalent should be checked in its current settings UI.
- Server side: publish correct MCP tool annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) and keep them stable across versions, since a client may re-prompt when a tool's classification changes.
- Auth: issue refresh tokens so a user authenticates once per client rather than per chat.
- Safety: this is the admin profile with shell tools (execute, ssh\_execute, node\_execute), and the ora8 account is in the sudo and docker groups. Auto-approving everything makes the model a shell with sudo and docker on those machines. Auto-allow read-only tools; keep a deliberate approval for execute-class tools. Landlock and the denied roots protect the local machine, but SSH targets rely on gateway-path-check alone.

## 7. Other findings for the Loopback developer

- terminal\_read is non-blocking. Reads right after a write returned empty or only the prompt, and the pwd output appeared only after a later write. A wait or timeout option would help.
- A trivial terminal session (two pwd and one echo) produced about 8 KB of ANSI and OSC output (theme and shell-integration sequences such as OSC 1337 and OSC 133). Offer escape stripping and a minimal shell mode (for example TERM=dumb with no rc files).
- Terminal startup printed .zshrc errors under the sandbox: permission denied for /home/linuxbrew/.linuxbrew/bin/brew and the zplug init, and `could not lock config file /home/jq/.gitconfig: Permission denied`. Harmless but noisy.
- ora8 shows as unreachable in ssh\_target\_list because the Tailscale check needs interactive approval. Consider a distinct state such as needs\_check, with the check URL returned as a structured field. Its record also differs from the others (use\_ssh\_config false, kind unknown, group null).
- patch\_file creates a timestamped .bak by default. I passed create\_backup false to keep the test directory clean.

## 8. Constraints honored

All file changes stayed inside the test directory. No credentials were read, nothing was pushed, and no existing files, services, authentication or permissions were changed. ora2 and ora4 were not contacted, the Tailscale link was not opened, and no reconnection was requested. The test directory was left in place for inspection.

## 9. Asks for the Loopback developer AI

1. Capture the exact ChatGPT reconnect error string and the gateway log for that request.
2. Diff the current auth implementation against v1/v2: well-known metadata, token TTL and refresh, session IDs, principal mapping, tool catalog.
3. Explain the 17 vs 40 tool difference (scope filter, per-client allowlist, or a ChatGPT-side limit).
4. Set tool annotations and a default approval stance per tool class.
5. Optional: run diagnostics, policy\_status and connections (read-only) and attach versions. Claude did not run them because they were outside the authorized test list.
