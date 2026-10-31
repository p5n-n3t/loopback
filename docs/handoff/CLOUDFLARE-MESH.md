# Cloudflare Mesh: required opt-in installation path

Implement this feature. It is not merely a research suggestion.
No Mesh client is installed or enrolled by this documentation change.
Consult current official documentation before implementation because Mesh is currently described as Beta.

## Product distinction and prerequisites

Cloudflare Mesh uses the Cloudflare One Client and private network routes.
Linux setup uses `cloudflare-warp`, `warp-svc`, and `warp-cli`.
Cloudflare Tunnel uses `cloudflared` and can publish a dashboard hostname.
Installing Tunnel does not enroll a Mesh node. Mesh does not require Tailscale.
Source: [Mesh getting started](https://developers.cloudflare.com/mesh/get-started/).

Offer Mesh as a **private-connectivity choice** in the install wizard and unattended settings.
Offer Cloudflare Tunnel separately as a **public-ingress choice**.
The user can choose both, with separate identities, ownership and diagnostics.
Do not add a misleading single `cloudflare` switch that hides these differences.

Check supported OS, architecture, package repository, client version, privileges, account/organization, enrollment authorization and route policy.
Retrieve the current supported-platform list. Do not infer support from a Debian-compatible package alone.
The implementation must work on supported Linux hosts such as the reported Ubuntu environment or state a precise unsupported-version reason.
Sources: [Linux Mesh installation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-mesh/guides/run-mesh-on-linux/), [Mesh setup](https://developers.cloudflare.com/mesh/get-started/).

## Server enrollment is not ordinary device enrollment

The server-node path uses a Mesh connector token and `warp-cli connector new` before connection.
The token is sensitive. Do not paste it into source, shell history, tool output, logs, support bundles or screenshots.
Validate a supported secret input path against the exact client version. Do not invent a token-from-stdin flag.
If upstream requires an argument containing a token, document the local process-argument exposure and use a narrowly controlled owner-side helper.
Never ask a model to read the token from a protected store.
User-device/headless-client enrollment can use a different organization or service-token flow.
Do not substitute it for server-node enrollment without checking the intended topology.
Sources: [Mesh setup](https://developers.cloudflare.com/mesh/get-started/), [headless Linux client deployment](https://developers.cloudflare.com/cloudflare-one/tutorials/deploy-client-headless-linux/).

Use a distinct server-node identity for each independent host.
Reusing a connector token can create replicas rather than independent nodes.
Enable forwarding only for an explicitly selected subnet-routing role, not merely to connect the host itself.
Source: [Connect servers](https://developers.cloudflare.com/mesh/guides/connect-servers/).

## Required setup sequence

1. Detect the current client/service, registration, account, organization, routes and existing ownership.
2. Show a plan that separates package install, service setup, enrollment, profile changes and optional subnet routing.
3. Collect missing authorization through a masked owner-side interface or external account setup step.
4. Install a pinned supported package from the official source with integrity validation and required privilege handling.
5. Enroll the selected host without logging credentials. Record only secure references and sanitized identity metadata.
6. Apply only the reviewed account/profile and routing choices. Preserve existing registrations and unrelated routes.
7. Verify service state, registration and intended connectivity with bounded commands and a known authorized TCP service.
8. Add the host as an inventory record and offer selected Loopback worker installation through existing SSH access.
9. Record created resources and rollback instructions. Re-running setup must converge without creating duplicate nodes.

These are required implementation steps, not new CLI flags that already exist.
Add a versioned settings schema and parser tests before documenting exact flags.
Examples may use placeholder secret references, never actual credentials.
Some Cloudflare account configuration needs a dashboard or documented API operation.
Identify those steps and provide a resumable flow instead of claiming complete unattended setup without evidence.
Keep enrollment and remote worker installation separate. A Mesh connection does not grant remote shell access.

## Routing and coexistence gate

Cloudflare documents a default Mesh range of `100.96.0.0/12`.
Tailscale uses `100.64.0.0/10`, which contains that range.
Sources: [Mesh concepts](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-mesh/concepts/), [Tailscale reserved addresses](https://tailscale.com/docs/reference/reserved-ip-addresses).

**Design inference:** simultaneous use can produce competing routes or address interpretation.
The owner's reported ora8 Tailscale address is inside the default Mesh range. The literal address is unnecessary here.
This is a preflight risk, not proof that this host currently has a collision.
Inspect effective routes, split-tunnel settings, DNS ownership and the proposed Mesh range before connection.
Do not enable WARP blindly on the current production gateway or replace Tailscale routes automatically.
Evaluate a supported custom Mesh range, reviewed route exclusions or an isolated host/network namespace where appropriate.
Changing an account-wide range needs an explicit impact plan and current platform support.
Reference: [Mesh routes](https://developers.cloudflare.com/mesh/features/routes/).

## Discovery and health

Detect locally available state without assuming `warp-cli` exposes a full peer inventory or stable JSON for every operation.
Use an optional least-privilege account adapter only where a current documented API supports the required records.
Record unsupported API features, pagination, rate limits and permission limits.
Merge saved SSH/manual endpoints when peer enumeration is unavailable.
Show registration, private route, DNS, SSH authentication, worker heartbeat and service reachability separately.
An Online status alone does not prove that the intended service works.
Source: [Mesh troubleshooting](https://developers.cloudflare.com/mesh/troubleshooting/).

## Failure and rollback acceptance

Cover absent package, unsupported platform, missing privilege, wrong organization, expired token, offline service, account policy denial and route conflict.
Cover existing client enrollment, separate devices versus replicas, cancel/retry, interrupted installation and gateway restart.
Never unregister or delete an existing owner-managed node during a failed Loopback setup.
Remove only resources that this installation created and has permission to remove.
Restore changed local service/config state with recorded backups and a safe order.
Explain any non-reversible account action before execution.
Exercise successful and failing enrollment in an isolated account/network during the later lab phase.
Until that evidence exists, label live Mesh enrollment integration UNVERIFIED while keeping fixture-tested behavior precise.
