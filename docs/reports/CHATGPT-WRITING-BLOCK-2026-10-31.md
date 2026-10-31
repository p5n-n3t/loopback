# Loopback MCP — ChatGPT OAuth Reconnection Failure

**Incident date:** 2026-10-31  
**Affected client:** ChatGPT Web  
**Other client:** Claude (reported working)  
**Loopback host:** `jq-xubuntu1`  
**Public endpoint:** `loopback.ziopsyop.tech`  
**Status:** Unresolved — tool execution blocked by connection/authorization flow.

## 1. Problem summary

Loopback is installed, connected, and exposing its tools in ChatGPT's Plugins interface.

ChatGPT can successfully invoke read-only metadata tools, but attempts to execute shell commands cause ChatGPT to interrupt the conversation with a reconnect prompt.

The prompt states:

> Reconnect Loopback  
> Your Loopback connection has expired. Reconnect it before ChatGPT can use it for this request.

Clicking **Reconnect** opens an authentication/connection interface that reports that the app does not have a sign-up URL.

There is no usable way to complete the reconnect process.

The user is consequently forced to select **Not now**, which ChatGPT subsequently reports as an approval/login denial.

This is reproducible across conversations.

**Crucially, the user is not intentionally rejecting shell commands. The authorization interface is preventing reconnection.**

Claude reportedly accesses Loopback successfully, so investigate ChatGPT's integration separately from the general Loopback service.

## 2. Actual MCP tool results

The following calls were executed successfully through ChatGPT on 2026-10-31.

### machine_info — SUCCESS

```json
{
  "user": "jq",
  "home": "/home/jq",
  "hostname": "jq-xubuntu1",
  "os": "Ubuntu 26.04.1 LTS",
  "platform": "linux",
  "arch": "x86_64",
  "machine_kind": "physical",
  "shell": "/usr/bin/zsh",
  "initial_cwd": "/home/jq",
  "local_port": 2026,
  "public_hosts": ["loopback.ziopsyop.tech"],
  "profile": "custom",
  "target": "local"
}
```

### permissions — SUCCESS

Relevant returned fields:

```json
{
  "target": "local",
  "profile": "custom",
  "allowed_roots": [
    {
      "path": "~",
      "resolved": "/home/jq",
      "mode": "rw",
      "exists": true
    }
  ],
  "other_locations": "denied",
  "shell": {
    "enabled": true,
    "reason": null,
    "approvals": "standard"
  },
  "process_control": true,
  "fleet": true,
  "sandbox_required": true,
  "sandbox_active": true,
  "enforcement": "os-sandbox",
  "authentication": {
    "kind": "oauth",
    "scopes": ["loopback:read"]
  }
}
```

Additional denied locations include `.ssh`, `.gnupg`, `.aws`, `.azure`, `.kube`, credential stores, and Loopback configuration files.

### policy_status — SUCCESS

Confirmed:

- Profile: `custom`
- Shell enabled: `true`
- Process control enabled: `true`
- Fleet enabled: `true`
- Sandbox backend: `landlock`
- Sandbox active: `true`
- Landlock ABI: `8`
- OS sandbox enforcement active
- Pending approvals: `0`

### execute — BLOCKED

Attempted command:

```bash
mktemp -d /home/jq/loopback-connection-test-20261031-XXXXXX
```

Actual tool error, verbatim:

```text
This particular path of the tool requires approval or login
from the user, and the user has declined. Do not try to
invoke this particular path again. You may try invoking a
different path of the tool.
```

This error appears after the reconnect interaction fails or is dismissed.

It does not distinguish between OAuth authorization failure and ordinary tool-use approval rejection.

**No successful command execution was observed.**

## 3. Critical finding: OAuth scope mismatch

The connection reports:

```text
authentication.kind   = oauth
authentication.scopes = ["loopback:read"]

policy.profile        = custom
allowed_roots         = /home/jq (rw)
shell.enabled         = true
sandbox.active        = true
```

The configured machine policy allows shell execution and read/write access, but the authenticated connection only advertises `loopback:read`.

Investigate whether:

1. ChatGPT has a read-only token that cannot authorize execution.
2. ChatGPT needs additional OAuth scopes for execution and file modification.
3. The server advertises incorrect or incomplete OAuth scopes.
4. ChatGPT has an expired, stale, or invalid authorization grant.
5. The reconnect process incorrectly redirects to a missing app sign-up URL.

**This discrepancy is evidence, not proof of root cause.** The scope design and enforcement need to be verified against the implementation.

## 4. Distinguish the three authorization layers

These must be diagnosed separately.

**A. OAuth connection/reconnection**

The ChatGPT-level prompt says the Loopback connection has expired. This concerns the app's connection to the MCP service and OAuth authorization.

**B. MCP tool-use approval**

Loopback's own policy may require approval for executing certain shell commands. This should produce an actionable approval request for the specific operation.

**C. Local sandbox and machine policy**

Landlock limits filesystem access independently of OAuth and approval. The reported sandbox is active and the home directory is allowed.

Do not assume changing policy switches in the dashboard resolves OAuth errors. The user has already enabled all available custom tool permissions without resolving the problem.

## 5. Required investigation

### Priority 1 — Repair ChatGPT OAuth reconnection

Inspect the server's OAuth implementation, including:

- OAuth authorization-server metadata and protected-resource metadata, where applicable.
- Authorization, token, and supported client registration endpoints.
- Dynamic client registration support and advertised metadata.
- Scope declarations and scope enforcement.
- Refresh-token handling and expiration behavior.
- OAuth redirect/callback URI handling for ChatGPT.
- Authorization-server issuer and resource audience validation.
- Any client-specific difference between ChatGPT and Claude.

Determine exactly why ChatGPT shows an unavailable sign-up URL instead of a working reconnect flow.

Do not assume that a sign-up URL is required by OAuth itself. Identify which application metadata or onboarding flow is actually missing or invalid.

### Priority 2 — Diagnose read versus execute authorization

Trace successful `machine_info` and `permissions` requests and compare them against the blocked `execute` request.

Determine whether the failure is generated by:

- ChatGPT's connector authorization layer.
- OAuth token validation or scope enforcement.
- The MCP gateway.
- Loopback's tool approval middleware.
- The local shell policy.

Identify the exact denial point and relevant HTTP/MCP error code.

If the request never reaches the Loopback server, report that explicitly.

### Priority 3 — Inspect logs safely

Correlate the following events:

1. Successful metadata requests.
2. Attempted `execute` invocation.
3. OAuth reconnect initiation.
4. Failed reconnect/sign-up URL transition.
5. Resulting approval/login rejection.

Report timestamps, request IDs, HTTP statuses, and sanitized error details.

Never expose access tokens, refresh tokens, client secrets, private keys, or authorization codes.

### Priority 4 — Implement the minimum necessary fix

Correct the OAuth metadata, client registration, token scope handling, reconnect logic, or approval routing as appropriate.

Do not disable OAuth, weaken the sandbox, expose credentials, or globally bypass command approvals.

Preserve the existing working Claude integration.

## 6. Acceptance criteria

After applying the fix, verify directly from ChatGPT:

1. `machine_info` and `permissions` continue working.
2. An execution request does not cause a broken reconnect flow.
3. If fresh OAuth authorization is required, ChatGPT can complete it successfully.
4. A command requiring approval presents an actual tool-use approval prompt.
5. Approved shell commands execute within the permitted sandbox.
6. A temporary directory can be created under `/home/jq`.
7. Python files can be written, read, edited, and tested within that directory.
8. Git initialization, staging, and staged diff inspection work without commits or pushes.
9. Background jobs and persistent terminal sessions function correctly.
10. HTTPS access to `https://example.com` returns a real HTTP status.
11. The registered `ora8` SSH target can run only `hostname && id` when authorized.
12. The connection remains usable across subsequent tool calls without unexpected reconnect prompts.

Do not contact `ora2` or `ora4`, access credentials, or modify unrelated services.

## 7. Required final report from the repairing agent

Provide:

- Confirmed root cause, with supporting log evidence.
- Whether the failure was server-side, OAuth configuration, ChatGPT integration, tool approval middleware, or a combination.
- Files/configuration changed.
- Exact fix implemented.
- Tests actually executed and their results.
- Any remaining ChatGPT-side limitation that cannot be fixed on the server.
- Whether the repair affects the working Claude connection.

**Do not report this resolved merely because health checks or read-only MCP tools succeed. The required result is successful authenticated execution from ChatGPT without the broken reconnect sequence.**

