# Web-client capabilities and local CLI boundaries

Reviewed 28 October 2026. This is an integration investigation, not a successful browser-client acceptance record.
The active Lightsprint session reaches the owner's desktop through Loopback with Landlock enforcement.
That connection does not prove a fresh ChatGPT, Claude, or Perplexity registration succeeds.

## Client and account constraints

| Client | Official documentation checked | Consequence |
| --- | --- | --- |
| ChatGPT | Current Help documentation describes custom MCP connections, authentication, tool discovery, permissions, and write-action confirmations. The current page names Business and Enterprise/Edu availability. | Do not promise universal Free/Plus eligibility. Check the actual account's available controls and permissions. Loopback cannot grant a missing product entitlement. |
| Claude | Remote custom connectors are documented for Free, Pro, Max, Team, and Enterprise. Free accounts have one custom connector. Remote requests originate from Anthropic infrastructure. | A private tailnet-only route is insufficient unless that infrastructure can reach it. Check organization permissions and use the supported authentication flow. |
| Perplexity | Official MCP documentation describes external connectors on Pro, Max, and Enterprise and distinguishes client-side MCP from its own provider tools. | Do not promise universal free-account support or assume every client imports the same server primitives. Verify the actual product surface. |

Sources:

- [OpenAI custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [Claude remote custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Perplexity MCP tools and client behavior](https://www.perplexity.ai/en-GB/hub/blog/mcp-tools)

Product entitlements and labels can change. The exact accounts and fresh browser sign-ins remain acceptance items.
MCP tools do not automatically install every provider's native skills, plugins, local files, or UI extensions.
The client decides which MCP tools, resources, prompts, and attachments it supports.
Use Loopback's existing packaged skills where the client accepts them. Do not imply an automatic cross-provider skills transfer.

## What Loopback can provide

Loopback exposes typed filesystem operations, shell execution, jobs, terminals, SSH/fleet operations, and the enabled document/browser/desktop adapters.
Actual capability depends on installed adapters, granted OAuth scopes, tool policy, allowed roots, OS permissions, and sandbox enforcement.
A document/PDF or system-setting task needs the relevant application, executable, or adapter on the target host.
An enabled tool is not proof that every application-specific dependency exists.

A web agent can use these tools to perform many local CLI workflows.
It does not inherit the CLI product's model, context management, patch protocol, approval system, network policy, or local integrations.
"Identical to Codex CLI" is therefore not a testable universal promise.
Measure a requested workflow instead: read an authorized fixture, write a reversible file, execute a permitted command, and verify its result.

## Default account recommendation

Keep the current normal-user installation and existing allowed roots. Do not create a root service or disable Landlock to imitate a local agent.
A dedicated account can separate unrelated user data, but needs explicit project access and installed tools.
It is an optional deployment choice, not a compatibility fix for an OAuth error or provider refusal.
Expand individual authorized roots or capabilities through the existing policy UI when the task needs them.
Keep credential and private-key directories protected.

## Diagnose the reported intermittent refusal

The owner reports refusals in roughly 5% of ChatGPT tasks. No exact failed prompt or redacted response is available.
The frequency is an owner estimate. No evidence attributes it to a particular Loopback permission or model behavior.
Do not remove scopes, approvals, or sandbox rules to suppress an unexplained refusal.

Use this classification for a reproducible case:

1. No request reaches Loopback: check client entitlement, selected tools, account policy, and provider refusal text.
2. Registration fails: retain non-secret method/grant/response metadata and the exact HTTP error. Never retain a client secret.
3. Consent or token exchange fails: inspect client authentication, redirect, PKCE, scopes, resource, and issuer behavior.
4. A tool request returns 401/403: compare granted scope, effective tool policy, and session/revocation state.
5. The tool runs but returns an OS denial: inspect allowed roots, sandbox status, and application dependencies.
6. Shield returns 429 or blocks a peer: inspect the recorded reason and trusted-proxy attribution before changing limits.
7. A successful tool response is followed by a model refusal: preserve redacted client-side evidence. Loopback cannot override provider decisions.

The new confidential/public OAuth tests cover server contracts. Actual ChatGPT and Claude payloads and final consent/refresh journeys remain unverified.
The setup key authorizes consent on Loopback. It is never submitted as a workaround for token-endpoint client authentication.
