# Cloudflare route discovery

Topology includes a separate Cloudflare provider group for observed hostname/service mappings. Provider records do not imply a verified MCP service or a known physical host. Connector health, endpoint health and protocol readiness are different observations.

For automatic read-only refresh, set these keys under `discovery.cloudflare` in the owner configuration:

```json
{"enabled": true, "account_id": "YOUR_32_CHARACTER_ACCOUNT_ID", "token_env": "LOOPBACK_CLOUDFLARE_READ_TOKEN"}
```

Provide a least-privilege token with tunnel configuration read access through that environment variable in the gateway's launch environment. The configuration stores only the variable name. The provider never returns or persists the token. A connected app's OAuth credentials are not automatically available to the gateway process.

Explicit topology/discovery Refresh reads at most ten pages of 100 tunnels and their configurations, within a 30-second total provider budget and a maximum ten-second timeout per HTTP operation. Failed refresh preserves the last observation and reports `provider_refresh_failed` without response bodies. Observations older than five minutes are marked stale.

Alternatively, an owner can import a sanitized observation into `<config>/cloudflare-discovery.json` with `source`, numeric Unix `observed_at`, and `tunnels` records containing `id`, `name`, `status` and `ingress` entries (`hostname`, `path`, `service`). Imports retain their observation time. They are not automatic refresh. Userinfo, URL query strings and fragments are excluded from stored service targets. Catch-all rules without a hostname are not drawn as public hostname routes.

Physical-host correlation and authenticated MCP verification remain separate work. Never infer a host from the tunnel name alone.

Host correlation now matches an exact tunnel UUID observed in a reachable worker's process arguments or declared cloudflared configuration. An optional `host_mappings` object maps a tunnel UUID to a registered target name. Conflicting observations stay ambiguous. A tunnel's backend address is in the connector's network namespace; a container connector's loopback address must not be assigned to a host listener by port alone.

MCP declarations are read from known Cursor, Claude Desktop and Codex client configuration locations. Only names, sanitized HTTP URLs and transport type are inventoried; headers, environments, stdio commands and arguments are not returned. Topology links mean “declares”, not “hosts”. The Services page offers selected HTTP endpoint verification (`POST /api/admin/discovery/verify`, `{target, url}`); the worker revalidates that the URL is currently declared, denies metadata/link-local endpoints, does not follow redirects and does not discover credentials. Authentication requirements, denial, redirects and valid MCP initialization are separate timestamped results. Stdio declarations require explicit execution outside discovery.

Refresh uses a 30-second overall provider deadline (each HTTP operation is limited to the remaining budget, maximum ten seconds). Concurrent refresh attempts return the cached observation. Missing runtime credentials are explicitly reported.
