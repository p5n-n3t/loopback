# MCP clients

Loopback is client-neutral. The requirement is not "a web browser"; it is a client capable of reaching a compatible Streamable HTTP MCP endpoint and completing one of Loopback's authentication paths.

## ChatGPT

Use the public HTTPS `/mcp` URL. Loopback advertises protected-resource and authorization-server metadata, DCR, Authorization Code, PKCE S256 and refresh support.

You can also generate a personal Agent Plugins package:

```bash
loopback plugin package ~/Desktop/loopback-plugin.zip
```

See [PLUGIN.md](PLUGIN.md).

## Perplexity

Perplexity supports custom remote MCP connectors and can authenticate custom connectors using supported connector authentication modes.

When the client offers bearer/API-key authentication, use the machine token from:

```bash
loopback token
```

### Repeated client approvals

Approval prompts belong to Perplexity's client policy, not the MCP server, so Loopback cannot ethically or technically force them off.

Loopback helps in two ways:

1. `task_run` can execute a bounded sequence as one tool invocation.
2. Long work uses `command_start` + `job_output` rather than many new command invocations.

If your Perplexity UI offers a per-thread or per-tool standing grant, use that setting when appropriate. Keep Loopback's own policy/approval layer enabled independently.

## Other agents

A different agent should work when all of the following are true:

- it supports remote Streamable HTTP MCP
- it can reach your HTTPS endpoint
- its authentication mode is compatible with bearer/API-key or standards-based OAuth
- it supports the tools' schemas/results

Loopback should therefore be described as **MCP-compatible**, not literally compatible with every web agent in existence.

## CORS

Known browser origins are included for ChatGPT and Perplexity. Add extra origins with:

```text
LOOPBACK_ALLOWED_ORIGINS=https://agent.example.com,https://other.example
```

A non-browser MCP client usually does not depend on browser CORS.
