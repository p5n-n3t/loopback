> Historical migration snapshot. See [the resumed engineering checkpoint](../CONTINUATION-2026-10-28-RESUMED.md) for implementation after PR 9.

# Owner requirements and acceptance map — 28 October 2026

This is the full scope to preserve. PR 8 fixes a smaller subset. Its merge does not complete these requirements.
Source: task P0Y50N-16 original `prompt`, its refined description, and the owner's latest migration request.
The authoritative original text remains in the task when account permissions allow access.

## Original 13 instructions

| # | Requirement and nuances | Current status and acceptance |
|---|---|---|
| 1 | Use appropriate DOTRIX dot-matrix SVG icons, recolored to the Loopback symbol theme. Keep accents subtle. Owner source directory: `/home/jq/Downloads/Telegram Desktop/dotrix-v1-interface-essentials-part-2-figma-svg-png/SVG · Interface Essentials · Part 2 · DOTRIX v1.0/`. | The larger icon upgrade is absent from current source. Verify asset access and license. Preserve recognizable icon meaning, contrast, focus, active state, and both themes. |
| 2 | Evaluate ngrok, localtunnel, and tunnelto/tunnel.to for MCP exposure. Consider ease, overhead, limitations, persistence, and installation complexity. Implement viable options in installer and dashboard. | Current baseline has Cloudflare and Tailscale paths. Do not invent provider support from a dropdown. Validate current official provider behavior and actual MCP transport compatibility. Explain rejected options. |
| 3 | Distinguish infrastructure mesh access through Tailscale, Cloudflare, and SSH from public exposure of an MCP URL. Discover existing infrastructure before a contextual installer menu. Ask essential information only. | Managed-route protection exists. Broad existing-route discovery and adaptive installation remain incomplete. Support heterogeneous hosts and explain optional config/env values. |
| 4 | Investigate practical Codex CLI parity for ChatGPT, Claude, and Perplexity web clients, including supported free/Plus tiers. Owner wants files, code, troubleshooting, documents/PDFs, and system tasks. Compare tools, skills, capabilities, permissions, normal-user vs dedicated-user installation. Investigate intermittent ChatGPT refusal, reported at about 5%. | No supported basis to promise every client or suppress provider safety decisions. Preserve Landlock and explicit access scopes. Broad authorized tools do not imply root or bypasses. State client limitations and external policy uncertainty. Determine defaults from actual user needs and OS permissions. |
| 5 | Make dashboard configuration adapt to the exposure provider. Support own-domain and provider-domain choices, including Cloudflare quick tunnels without an owned domain. | Missing provider-aware UI in current baseline. Check stable OAuth origin requirements and URL changes, ingress routes, transport timeouts, and persistence. Surface provider-specific fields only when relevant. |
| 6 | Add unobtrusive endpoint protection against floods, brute force, scanners, exploits, and rotating-IP abuse. Record timestamp, attack type, IP, and country when reliably available. Surface analytics and topology markers. | Old Shield code and screenshots exist only in historical evidence. `runtime/shield.py` is absent here. Preserve legitimate OAuth, SSE, and tool bursts. Handle trusted proxies safely. Do not claim perfect protection or fabricate geolocation. No aggressive public security scans. |
| 7 | Generate a separate random dashboard username and password during installation. Permit later changes to both. Separate dashboard credentials from API keys and OAuth grants. | Current baseline has dashboard password/machine-token recovery. The dedicated username/password upgrade is absent. Verify credential migration, sessions, CSRF, reset, and redaction. Never put generated credentials in Git or reports. |
| 8 | Replace file-based setup with a generated API key. Reuse it to enroll additional clients. Permit rotation. Keep API-key rotation distinct from per-client grant revocation. Provide a clear consent bridge for OAuth-only clients. | Machine tokens, per-client tokens, enrollment codes, and OAuth exist. They do not establish the requested complete setup-key workflow. Design explicit consent, rotation, revocation, and recovery semantics. Test the real web-client journey. |
| 9 | Make Services Edit visibly load the editor. Connect must reach the correct public/tailnet endpoint. | PR 8 repairs enrollment links and worker connection behavior. Full Edit persistence and remote reachability remain unproven. For private listeners, provide explicit SSH forwarding. Do not replace loopback with a tailnet IP or expose an unauthenticated listener to force reachability. |
| 10 | Discover existing Cloudflare tunnels, Tailscale Serve/Funnel handlers, and MCP gateways across the controller and all eligible remote machines. Show them in Services and Topology with ownership/provenance. Document manual SSH/config/env options with use cases. | Registry-backed topology is not complete discovery. Seven installed workers do not imply seven bindings. Inventory must tolerate partial hosts and distinguish unknown from absent. Preserve external routes and never adopt them silently. |
| 11 | Provide aaPanel-style live circular whole-host CPU/RAM gauges with centered percentages and threshold colors. Show absolute memory and separate Loopback resource use. Show ingress/egress bars with KB/s. | Current overview emphasizes managed Loopback processes. Tiny values round to 0%. Initial samples and idle rates need distinct states. Avoid double-counting stacked network adapters. A throughput percentage needs a real denominator. |
| 12 | Show bold Loopback branding instead of Personal workspace, with current dashboard URL beneath. Use restrained symbol/wordmark gradient accents. | New Oct 28 Figma assets supersede earlier specific assets. Preserve the behavior while adapting layout, icons, typography, and buttons to the new references. |
| 13 | Preserve CLI verbs such as up/down/status/token/help. Provide readable colored installer output, progress, and tables. Restore token-to-OAuth consent UX for OAuth-only web clients. | Existing CLI remains. Do not redesign command contracts. Keep machine-readable output behind an explicit option. Verify installer upgrade, help, startup, persistence, and consent compatibility. |

## Latest owner observations and added requirements

1. Replace both sidebar symbol and wordmark from the current Figma file. Use matching navigation icons and restrained gradients across the dashboard.
2. Adapt the reference button shapes, navigation, typography, layout, and spacing to the dashboard architecture. Preserve functionality.
   The owner also reports that the login screen is off brand. Include it in the redesign.
   Use the current Figma symbol, wordmark, typography, button treatment, and restrained gradients on the login screen.
   Preserve authentication behavior. Verify error states, keyboard focus, mobile layout, and supported themes.
3. Add **Select all** and **Deselect all** to MCP tools. Define whether bulk action applies globally, by category, or to a filtered set.
4. Fix existing checkbox policy persistence first. Sequential changes can discard previous denials. See `AUTH-INVESTIGATION.md`.
5. Discover unmanaged and managed tunnels/gateways locally and remotely. Topology currently shows only the registered `ora1-system` service.
6. Correct whole-host gauges, traffic attribution, missing data states, probes, and stale/unknown indicators.
7. Display the existing RotoXY Tailscale Funnel route. The Oct 28 read-only sample confirms Funnel 443 proxies to port 2030.
8. Reproduce and fix real ChatGPT and Claude registration/sign-in. Synthetic local requests explain the reported rejection strings, not actual client payloads.

Exact owner errors:

```json
{"error":"invalid_client_metadata","error_description":"only public clients (token_endpoint_auth_method=none) are supported"}
```

```json
{"error":"invalid_client_metadata","error_description":"unsupported grant type requested"}
```

## Current Figma references

- [Wordmark, 31:188](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=31-188&m=dev)
- [Dark home reference, 6:223](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=6-223&m=dev)
- [Symbol, 31:189](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=31-189&m=dev)
- [Dark 404 reference, 6:2646](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=6-2646&m=dev)
- [Light 404 reference, 6:1836](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=6-1836&m=dev)
- [Light home reference, 1:882](https://www.figma.com/design/r7Rdu2N3ttXa1K30PDfcnQ/Twin---MicroSaaS-Template?node-id=1-882&m=dev)

`FIGMA.json` includes fetched version, tokens, and four durable exports. Load it before another Figma fetch.
These roots are landing/404 references with an embedded dashboard illustration. They are not a complete application specification.

Earlier Figma file `L1tPbHX8taPk3iRoMOsACC` and its 22 nodes remain historical context in `docs/HANDOFF-2026-10-25.md`.
Their IDs are 285:603, 144:1740, 144:1712, 131:4879, 101:278, 83:3894, 111:2325, 131:5171, 189:1732, 185:1365, 150:2702, 146:1781, 150:3114, 181:1264, 144:593, 144:1627, 15:149, 242:472, 144:951, 131:5716, 179:4881, 111:2468.

## Acceptance beyond a green unit suite

- Separate implemented, tested, deployed, and verified status for each requirement.
- Test OAuth registration, consent, code exchange, refresh, rotation, revocation, redirects, PKCE, and resource enforcement.
- Verify actual ChatGPT and Claude connections when owner interaction is available. Do not equate local public-client tests with these clients.
- Test multi-change policy persistence, rapid edits, bulk actions, reload, tools/list, and tools/call enforcement.
- Inventory all eligible hosts, external routes, partial failures, and provider-specific state without mutations.
- Verify host metrics under an authorized, bounded workload. Preserve actual idle zero and unknown samples.
- Verify Edit/save/reload, Connect reachability, themes, mobile, keyboard focus, and branded assets.
- Preserve existing working Loopback tools, SSH access, RotoXY routes, updater, CLI, and sandbox guarantees.
- Finish `/proof` after final rendering-source edits. Reuse valid evidence. Capture only changed or missing views.
- Do not claim deployed success until the owner-approved update is applied and verified on each intended host.
