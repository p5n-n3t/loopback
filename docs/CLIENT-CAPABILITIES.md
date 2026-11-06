# Web-client capabilities and local CLI boundaries

Plan eligibility reviewed 6 November 2026 against provider documentation.
Actual connector access depends on your client, workspace, and rollout.

## Client and account constraints

| Client | Official documentation checked | Consequence |
| --- | --- | --- |
| ChatGPT Web | The Plugins directory is available across plans. OpenAI documents **Add custom MCP server** in Plugins, and separately documents plan restrictions for developer mode. | Check the actual account and the intended Chat or Work surface. Seeing the add option, completing OAuth, and successfully invoking a tool are separate checks. |
| Claude | Anthropic documents remote custom connectors on Free, Pro, Max, Ultra, Team, and Enterprise. Free includes one custom connector. | Anthropic must be able to reach the remote endpoint. Team and Enterprise may require an authorized admin. |
| Perplexity | Official MCP documentation describes external connectors on Pro, Max, and Enterprise and distinguishes client-side MCP from its own provider tools. | Do not promise universal free-account support or assume every client imports the same server primitives. Verify the actual product surface. |
| VS Code or your own MCP client | VS Code supports configured HTTP MCP servers. A compatible local MCP client can connect directly over localhost. | The model or agent used by that client has its own plan, limits, and permissions. |

Sources:

- [OpenAI custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [OpenAI Plugins availability](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt) and [custom MCP setup](https://developers.openai.com/plugins/quickstart)
- [Claude remote custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [VS Code MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [Perplexity MCP tools and client behavior](https://www.perplexity.ai/en-GB/hub/blog/mcp-tools)

Product entitlements and labels can change. The exact accounts and fresh browser sign-ins remain acceptance items.
MCP tools do not automatically install every provider's native skills, plugins, local files, or UI extensions.
The client decides which MCP tools, resources, prompts, and attachments it supports.
Use Loopback's existing packaged skills where the client accepts them. Do not imply an automatic cross-provider skills transfer.

## ChatGPT Web vs. Codex CLI

On an account that supports the required custom MCP actions, a normal
ChatGPT Web conversation can ask Loopback to inspect a project, run its
tests, or edit authorized files. This covers parts of a coding workflow.
ChatGPT Web still provides the model picker, thinking controls, tool
approvals, and usage limits. Codex CLI has its own execution environment,
model and reasoning controls, and allowances.

The **Add custom MCP server** entry is a documented feature of the Plugins
interface. OpenAI's quickstart demonstrates a read-only tool in **Work**.
If the entry appears on your Free account, confirm authorization and try a
read-only call in ordinary **Chat** before relying on that workflow. We have
not verified full Loopback tool access in ordinary Chat on all plans.

OpenAI documents unlimited everyday text chat for Free and Go subject to
abuse safeguards. Tools have separate limits, and custom MCP access is a
separate entitlement. Loopback connects through the provider's supported
interface and does not convert a chat allowance into a tool or Codex quota.
See [OpenAI's MCP availability](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt).

As documented on 6 November 2026, Free and Go use GPT-5.6 Luna, including
Think where available. Plus offers Medium and High thinking in Chat and
GPT-6 Sol as the rollout reaches eligible accounts. Eligible Pro accounts
also have Extra High and Pro options, subject to usage limits. GPT-6.1 Sol
is listed separately for Work and Codex. Check
[OpenAI's current model and usage guide](https://help.openai.com/en/articles/20001354-gpt-6-and-other-models-in-chatgpt)
for changes to those controls.

Use only systems you own or are authorized to manage. Review the permissions
and operations you approve, and follow the provider's terms, including limits
and safety controls. Loopback is supplied as-is under the
[MIT license](../LICENSE). See [OpenAI's Terms of Use](https://openai.com/policies/terms-of-use/).

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

Start with [Execution traces](EXECUTION-TRACES.md) to identify the last recorded stage.
A refusal before dispatch may have no gateway receipt.

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
