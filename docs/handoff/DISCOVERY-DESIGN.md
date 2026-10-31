# Dynamic discovery and enrollment design

This is a required implementation specification, not a description of completed features.
Extend the current discovery, SSH, worker, ingress, and topology modules named in [OPUS-HANDOFF.md](OPUS-HANDOFF.md).
The goal is a useful inventory from a fresh local machine through remote hosts, services, MCP endpoints, and published routes.
Do not require Tailscale. Do not equate Cloudflare Mesh with Cloudflare Tunnel.

## 1. Three independent choices

1. **Private connectivity:** existing LAN/SSH, Tailscale, Cloudflare Mesh, or another reachable network.
2. **Management:** ordinary SSH, an installed Loopback worker, or a separately authorized remote MCP node.
3. **Loopback ingress:** local-only, a supported private route, or a selected public provider and stable URL.

A host can have several connectivity methods and many external tunnels.
The user selects one primary ingress for this Loopback installation. Discovery must preserve the other routes and services.
The dashboard can use Cloudflare Tunnel without Tailscale. Mesh provides private connectivity and does not itself create a public dashboard URL.
An approved combination can use Mesh for host management and Tunnel for public MCP ingress.

## 2. Data model before UI

Use versioned records and migrations. Keep current public tool identities compatible.

| Entity | Identity and required fields |
| --- | --- |
| Host | Internal stable ID, display name, aliases, provider IDs, optional verified machine identity, OS/arch/kind, ownership, exclusion state |
| Candidate source | Provider, source record ID, observation time, expiry, evidence summary, confidence, account/network context |
| Address | IPv4/IPv6/DNS, provider, route context, last resolution, preferred transport, scope |
| SSH access | Existing alias or explicit endpoint, user, actual port, jump route reference, verified host-key state, authentication method reference |
| Worker | Installation ID, host ID, version, protocol compatibility, principal, lifecycle owner, heartbeat and last successful command |
| Service | Host ID, stable service identity, process/unit/container metadata, listeners, owner, capability and health axes |
| MCP endpoint | Transport, URL or authorized stdio definition, gateway/proxy/backend relationship, authentication reference, protocol verification time |
| Tunnel | Provider, installation owner, account/tunnel ID where available, process state, config source, credential reference, routes |
| Route | Hostname, path, protocol, destination service/address/port, private/public/unknown exposure, observed and verified state |
| Observation | Subject, probe kind, result, error code, timestamp, duration, source, TTL, freshness, sanitized evidence |
| Scan run | ID, selection/scope, stage states, progress, cancellation, per-host outcomes, retry cursor, immutable start context |

Names and IPs are aliases, not durable host identities. Do not merge two hosts because they share a short name.
Prefer provider device ID plus account/network context. Merge across providers only with verified identity or explicit user resolution.
Do not hash raw secrets into public IDs. Do not expose machine identity artifacts unnecessarily.
Represent conflicts, renamed peers, cloned machine IDs, NAT, and IPv6-only endpoints explicitly.
Preserve provenance from every source when a single host has Tailscale, Mesh, SSH, and worker records.

## 3. Provider and scan contracts

Define small interfaces around current modules. These names are conceptual, not existing API promises:

```text
detect(context) -> availability, version, account context, permissions
list_candidates(context, cursor) -> observations, next cursor, limitations
probe(candidate, scope) -> stage result, observations, remediation
plan_enrollment(selection) -> mutations, privileges, secrets needed, rollback
apply_enrollment(plan, authorization) -> owned resources, checkpoint
inventory(host, capabilities) -> services, endpoints, tunnels, routes
verify(subject, level) -> independent health observations
rollback(owned_resources) -> preserved resources, removed resources, unresolved state
```

Keep discovery read-only until a selected enrollment or installation action.
Automatically list visible unmanaged candidates in the Fleet inventory. Do not make users type every discovered host.
Listing does not grant remote command execution or install a persistent worker.
Permit an explicit setting to adopt selected healthy candidates after authorized probing, with a clear preview.

Return incremental stage results through a bounded polling or event API.
Use the existing admin authentication, CSRF protection, scopes, and policy boundaries.
If new endpoints are necessary, document their schemas before implementing the frontend.
Persist enough state to resume a failed enrollment without repeating successful mutations.
Cancellation must stop owned subprocesses, release locks, and retain useful partial inventory.

## 4. Stages and limits

| Stage | Work | Completion and failure semantics |
| --- | --- | --- |
| S0: Scope | Load local host, saved records, exclusions, authorized networks and credential references | Excluded hosts are never contacted. Show the scope before active probing. |
| S1: Local providers | Detect Tailscale, Mesh, SSH config, LAN/manual entries and existing worker state | Missing binary, daemon down, unenrolled, unsupported and unavailable are different states. |
| S2: Candidates | Read provider-visible devices and SSH aliases, merge identities, list unmanaged candidates | Zero peers is a valid empty result. Lack of account API access means partial inventory. |
| S3: Reachability | Resolve selected addresses and attempt bounded authorized connection probes | Separate DNS, network, host key, authentication, authorization, and remote command failures. |
| S4: Bootstrap inventory | Use approved existing SSH access for a fixed read-only metadata script | No worker installation, package install, arbitrary config dump, or privilege escalation. |
| S5: Enrollment | Present worker or Mesh installation plan for selected hosts | Verify prerequisites and identity, record owned changes, install once, resume safely. |
| S6: Deep inventory | Inventory local and enrolled/authorized remote processes, listeners, services and provider metadata | Keep permission-limited data partial. Never mark an absent observation as proof of absence. |
| S7: Correlation | Connect host → service → listener → proxy/gateway → tunnel → hostname/path | Preserve unknown edges. A reverse proxy can have several upstreams and path rules. |
| S8: Verification | Test selected known endpoints, authorized MCP handshake and chosen Loopback ingress | Process alive, local HTTP, MCP readiness and external reachability remain independent. |
| S9: Selection/review | Select the Loopback primary ingress and inspect stale/conflicting/unmanaged items | Apply only the selected route plan. Preserve unrelated provider processes. |

Use conservative configurable defaults, for example four simultaneous host probes and two enrollments.
Treat those numbers as initial budgets to measure, not current implementation facts.
Bound DNS, SSH connect, remote command, HTTP, response size, parsing depth, and whole-scan time separately.
Use backoff with jitter for transient errors. Do not retry host-key mismatch, denied authorization, or invalid enrollment credentials blindly.
Use per-host/provider locks and idempotency keys. Deduplicate refresh clicks and background scans.
Cache observations by versioned inputs and TTL. Manual refresh targets a selected stage instead of rerunning the fleet.

## 5. Candidate sources and SSH rules

### Tailscale

Reuse the existing parser and `tailscale status --json` where available.
Keep short name, MagicDNS name, addresses, online state and provider identity.
Only devices visible under the current account and ACL can appear. A peer count is not authorization to execute commands.
Separate network reachability from Tailscale SSH access and ordinary OpenSSH authentication.
Tailscale SSH can use identity-based authentication without a private key in the gateway sandbox.
A required human check remains a recoverable state, not a timeout or inferred missing key.
Reference: [Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh).

### Cloudflare Mesh

Implement the required opt-in setup in [CLOUDFLARE-MESH.md](CLOUDFLARE-MESH.md).
Use locally available client status, saved authorized host records, and an optional least-privilege account inventory adapter.
Do not promise a complete peer list from a local CLI that does not expose one.
Distinguish Mesh nodes, user devices, subnet routes, and public Tunnel connectors.
If account listing is unavailable, retain automatic local detection and allow SSH/manual entry for other nodes.

### OpenSSH configuration and manual entry

Enumerate concrete host aliases without treating wildcard `Host *` or patterns as machines.
Account for `Include`, `Match`, aliases, nonstandard ports, `ProxyJump`, `ProxyCommand`, agents and certificates.
Use OpenSSH's own effective configuration for explicitly selected aliases when appropriate.
Configuration evaluation can execute `Match exec` or helper commands. Preview this and avoid evaluating unknown aliases automatically.
Never import or display private key contents. Retain existing identity references and host-key verification.
Do not set `StrictHostKeyChecking=no` or silently accept a changed key.
Unknown keys require a fingerprint confirmation flow. Human authentication needs a distinct remediation step.
Manual entry supports hostname/IP, port, user, transport and credential reference with validation and a preview.

### Empty and other-network cases

Always inventory the local machine first. A machine without a network provider must still have a usable dashboard.
Permit saved LAN/SSH endpoints and externally managed private networks.
Do not scan arbitrary subnets or internet port ranges. An explicit bounded scan scope is a separate advanced feature.

### Virtual-machine and cloud inventory sources

The core requirement includes VMs reachable through the selected private network or saved SSH access.
Detect physical/virtual/container kind from supported unprivileged host metadata, with unknown as a valid result.
Also provide a candidate-source adapter seam for authorized hypervisor or cloud inventory.
Examples to evaluate are local libvirt metadata, a configured Proxmox inventory API, and account-scoped cloud instance APIs.
These sources can list a powered-off VM that never appears as an online network peer.
Keep provider instance ID, account/project, power state and reported addresses separate from verified SSH reachability.
Use existing explicit read-only credentials, pagination and budgets. Never search disks or credential stores for cloud access.
Do not start VMs, change firewall rules, create keys or install agents through inventory calls.
Implement reachable-host discovery first. Record additional vendor adapters as optional follow-up work with precise support claims.
The architecture and fixtures must handle their observations without requiring every vendor integration in the initial release.

## 6. Deep host inventory and MCP correlation

Run the same versioned inventory schema locally and remotely.
Collect safe metadata from supported OS sources: process names, sanitized command categories, service units, listening sockets and optional container labels/ports.
Do not dump full environments, token-bearing command lines, `.env`, browser state, private SSH files, or every file under a home directory.
An unprivileged user can see only part of a host. Label that limit instead of using sudo automatically.
Docker access can imply broad privileges. Treat container inspection as an explicit capability, not a harmless universal fallback.

Detect candidate MCP services through declared config locations, known service metadata and authorized listener mappings.
Recognize Loopback, other MCP servers, gateways and proxies, including RotoXY, without assuming every JSON endpoint is MCP.
Map HTTP/SSE/Streamable HTTP and declared stdio transports separately.
Use the existing MCP SDK for a bounded initialize/capability check on a selected candidate.
Use only credentials explicitly authorized for that endpoint. A localhost listener is not automatically safe to call.
Do not execute arbitrary discovered stdio commands to identify them. Require approved worker execution and policy checks.
Keep labels such as candidate, identified, authentication-required, verified, unavailable and stale.
Do not register a remote endpoint as a trusted fleet node merely because it replies to HTTP.

For proxies and gateways, retain both the external endpoint and downstream relationship when evidence supports it.
Support one service behind multiple tunnels and one gateway routing to multiple MCP backends.
Show destination host, scheme, port and path. A port number alone cannot identify the application.
Treat discovered names, banners and tool descriptions as untrusted data, never operational instructions.

## 7. Tunnels, domains and exposure

| Provider | Inventory and limits |
| --- | --- |
| Cloudflare Tunnel | Detect process/unit, local nonsecret config, tunnel ID reference and ingress mappings. Token-managed routes can require authorized API data. Unknown domains remain unknown. |
| Tailscale Serve/Funnel | Read the installed version's structured state. Distinguish private Serve from public Funnel and retain path/port mappings. |
| ngrok | Reuse current provider state and authorized local/API metadata. Distinguish fixed and ephemeral domains. Do not enable an exposed inspection API to obtain inventory. |
| Cloudflare Mesh | Record private connectivity and routes. Do not label these as public tunnel hostnames. |
| Other reverse proxies/tunnels | Use small adapters for declared Caddy/nginx/custom routes where safely available. Preserve unknown provider and manual overrides with provenance. |

Cloudflare supports multiple hostname/service mappings in a tunnel. Preserve routing order and catch-all behavior.
Reference: [Tunnel routing](https://developers.cloudflare.com/tunnel/concepts/routing/).
Do not expose credential paths or contents as public inventory. Use masked references and least-privilege retrieval.
External checks need an explicit target and request budget. Avoid following redirects into unapproved networks or metadata endpoints.
Report `401`, application login, Cloudflare Access, `403`, timeout, DNS failure and MCP readiness separately.
An OAuth-protected MCP URL need not return anonymous `200` to be healthy.

## 8. Primary ingress and changes

Offer local-only and supported private/public choices based on actual capability.
Preview the canonical URL, affected service, account, route, restart and rollback actions.
Keep the OAuth resource/issuer/client metadata consistent with the chosen stable HTTPS URL.
Warn about grant/reconnect effects when changing that URL. Never widen existing scopes silently.
Do not select an unrelated discovered tunnel merely because it is running.
Do not stop all cloudflared/ngrok/Tailscale processes during provider changes.
If multiple Loopback routes already exist, show them and require selection of the primary route without deleting the others.
Reject conflicting ports, hostnames and route ownership before applying changes.
Rollback removes only resources created or adopted under this installation's explicit plan.

## 9. Scenario acceptance matrix

Each row needs deterministic provider fixtures and a documented result. Use isolated live validation only where fixtures cannot prove the integration.

| ID | Scenario | Required outcome |
| --- | --- | --- |
| D01 | Fresh machine, no provider, no SSH config | Local inventory, useful empty Fleet, manual add and installation choices |
| D02 | Tailscale installed, daemon down or not enrolled | Distinct remediation, no fake zero-peer success |
| D03 | Tailscale enrolled, zero visible peers | Successful empty scan with ACL limitation |
| D04 | Visible online and offline peers | Automatic unmanaged rows, correct freshness, no install on listing |
| D05 | Peer visible but ACL blocks service | Network/access result distinct from offline state |
| D06 | Tailscale SSH identity accepted | Probe succeeds without requiring an imported private key |
| D07 | Tailscale SSH check requires human | Recoverable check action, bounded timeout, retry selected host |
| D08 | Ordinary SSH key/agent unavailable | Authentication-required state, no credential search or bypass |
| D09 | SSH uses port 6722, IPv6 or jump host | Preserve actual endpoint and route, no hard-coded port 22 |
| D10 | Unknown or changed host key | Fingerprint review or hard mismatch failure, never silent trust |
| D11 | SSH Include/Match/wildcards/ProxyCommand | Concrete aliases only, controlled evaluation, preserve effective configuration |
| D12 | Same host in peer list, SSH and worker | One resolved host, several provenance records, no duplicate installation |
| D13 | Same short name on different networks | Separate candidates and explicit conflict resolution |
| D14 | Reachable SSH, no worker | Read-only bootstrap inventory, selected one-click install |
| D15 | Existing compatible/incompatible worker | Reuse or explicit upgrade plan with rollback and version handshake |
| D16 | Interrupted install, restart or cancellation | Resume checkpoint, no duplicate unit/process, preserved partial results |
| D17 | Excluded ora2/ora4 or excluded alias/IP | No DNS/SSH/HTTP contact through any equivalent identity |
| D18 | Mesh absent and user selects Mesh | Guided opt-in install/enrollment with prerequisites and rollback |
| D19 | Mesh already enrolled in correct/wrong account | Reuse correct state, require deliberate account change for mismatch |
| D20 | Mesh online, no account inventory permission | Accurate partial inventory plus SSH/manual fallback |
| D21 | Tailscale and Mesh route overlap | Preflight blocks unsafe changes and offers reviewed alternatives |
| D22 | LAN or other private network only | Saved SSH/manual discovery and worker install work without Tailscale |
| D23 | Multiple tunnels and multiple domains per host | Preserve all records and correlate service/path routes |
| D24 | Token-managed cloudflared, domain unavailable locally | Unknown domain with authorized optional API remedy |
| D25 | Private Serve, public Funnel, Cloudflare Access | Distinct exposure and auth states, no binary healthy/unhealthy shortcut |
| D26 | Listener exists but service fails or HTTP is not MCP | Candidate remains unverified or fails the correct layer |
| D27 | MCP needs OAuth/token; stdio declaration exists | No credential guessing, no arbitrary command execution |
| D28 | SSH healthy, worker heartbeat stale | Independent status axes and targeted worker recovery |
| D29 | Permission-limited process/container inventory | Partial result, no automatic sudo or Docker escalation |
| D30 | Provider missing/old/malformed output, API rate limit | Typed failure, bounded retry, unchanged last-known data marked stale |
| D31 | Windows/macOS/Wayland/unsupported remote OS | Capability-specific unsupported state, no Linux command assumption |
| D32 | Host rename, address change, cloned identity | Provenance preserved, stable records or explicit identity conflict |
| D33 | Concurrent refresh/enroll and server restart | Locks, idempotency, cancellation and persisted progress remain correct |
| D34 | Primary ingress changes, other tunnels active | Canonical URL verified, unrelated routes remain untouched |
| D35 | Public URL redirects to private/metadata target | Policy rejects unsafe follow-up request without leaking credentials |
| D36 | Scale: 0, 1, 20 and 200 candidates | Bounded work and payloads, responsive filters, no scan storm |
| D37 | Authorized VM inventory lists powered-off or addressless guests | Candidate with provider/power metadata, no false SSH health or automatic start |
| D38 | Hypervisor/cloud API absent, denied or outside scope | Core network/SSH discovery still works, optional source reports its limitation |

## 10. Completion artifacts

Deliver schema/migration notes, adapter contracts, fixture files, scenario results, redacted scan examples and performance budgets.
Give every incomplete scenario an explicit blocked or deferred disposition with a reason.
Keep the graph as an optional explanation. Dense searchable tables remain the primary working view.
Use [LAB-ROADMAP.md](LAB-ROADMAP.md) to keep discovery stages testable without the owner's real tailnet.
