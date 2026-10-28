# Tunnel provider evaluation

Reviewed 2026-10-28 against the official sources linked below. No live provider
account, credential, or public tunnel was used in this evaluation. No benchmark
supports a latency or memory ranking. All public providers add a relay hop,
provider trust, and a local agent process. Limits depend on the account and plan.

| Provider | MCP transport and origin | Setup and overhead | Loopback decision |
| --- | --- | --- | --- |
| Named Cloudflare Tunnel | Persistent configured hostname. Existing Loopback support remains available. | cloudflared plus account, tunnel credentials, and hostname routing. | Keep the existing managed path. |
| Cloudflare Quick Tunnel | Official docs explicitly exclude SSE. Hostname changes on each launch. Loopback's streamable MCP can return SSE. | No account or domain required, but temporary relay and process. | Do not offer it as a supported MCP provider. |
| ngrok | Explicit HTTPS URL and HTTP forwarding. OpenAI documents ngrok for Streamable HTTP MCP. Use an account-assigned or reserved domain. | Official agent, account credential file, one process. No purchased domain required for an ngrok hostname. | Add managed support with a fixed hostname. Live authenticated MCP testing remains necessary. |
| Localtunnel | Official client accepts a requested subdomain, but says the assigned name depends on availability. | Node.js client, public relay or a separately operated server. | No managed integration. The public service does not guarantee the persisted OAuth origin. This is not a claim that HTTP streaming cannot work. |
| Tunnelto | Official site offers reserved domains. Reviewed documentation does not establish Loopback MCP/SSE behavior. | Rust client and hosted account for reserved domains, or separately operated server. | Defer managed support until streaming, auth headers, cancellation, and stable-origin behavior pass an end-to-end test. Do not call it incompatible. |
| Tailscale Serve / Funnel | Existing fixed tailnet hostname. Serve is private to the tailnet. Funnel is public. | Existing Tailscale daemon and configuration. | Preserve existing behavior and refuse to replace another application's handler. |

## Configure ngrok

Install the official ngrok agent yourself and create a **version 3** configuration
file with your account authtoken. Use ngrok's credential setup flow outside the
Loopback dashboard. Never paste a credential into Loopback's hostname or path
fields. Select a hostname assigned or reserved to your account. Do not use a
random endpoint, URL path, port, or wildcard.

In **Public tunnels**, use the **ngrok · fixed public URL** card. Enter the
fixed hostname and absolute or home-relative v3 config file path. Confirm public
exposure, then apply. CLI integration uses `loopback install --ingress ngrok --host
HOST --ngrok-config PATH --yes`.

Loopback keeps the local target at `http://127.0.0.1:<configured port>`. It does
not change listener bindings or disable other ingress paths. Its configuration
stores only the hostname and file path. A separate credential-free v3 overlay
disables the agent web interface, traffic inspection, remote agent management,
and update checks. Credentials are never command-line arguments. Agent output
is discarded because provider errors can contain secrets. Use the official CLI
outside Loopback to diagnose an account or config error.

Loopback records a PID and OS process birth identity. It can recover ownership
across helper-process and service restarts. It never terminates a process from a
PID alone. Stop and restart affect only the owned ngrok process. Existing
Tailscale routes, including external orchestration routes, remain unchanged.
The agent starts without claiming remote readiness. The dashboard can check
`/healthz`, but a health response does not prove OAuth or MCP transport works.
After configuration, connect an MCP client to `https://HOST/mcp` and complete
OAuth before relying on the route. Keep the hostname unchanged across restarts.

A failed apply restores the previous ngrok configuration and attempts to restore
its previous owned process. If the restored agent cannot start, status reports
that it is stopped. Changing account credentials remains an operator action.

## Official sources

- [Cloudflare Quick Tunnel limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [ngrok CLI URL and inspection flags](https://ngrok.com/docs/gateway/agent/cli)
- [ngrok v3 agent configuration](https://ngrok.com/docs/gateway/agent/config/v3)
- [ngrok HTTP endpoints and domain selection](https://ngrok.com/docs/gateway/endpoints/http)
- [OpenAI MCP quickstart using ngrok](https://developers.openai.com/plugins/build/app-quickstart)
- [Localtunnel official client and subdomain availability](https://github.com/localtunnel/localtunnel)
- [Tunnelto official site and reserved domains](https://console.tunnelto.dev/)
- [Tunnelto official client](https://github.com/agrinman/tunnelto)
