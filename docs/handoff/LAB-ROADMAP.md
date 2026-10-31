# Later isolated clean-install lab

The user's end goal is an isolated environment that can test new Loopback installations across settings and connectivity choices.
Prepare interfaces and fixtures during remediation. Build and operate the full lab in a later phase.
Do not use the owner's live gateway, tailnet or VMs as disposable test machines.
The existing ora8 lab script is a reference and regression asset, not permission for unrestricted production changes.

## Prepare now

1. Version installer settings and provider contracts. Separate plan, apply, verify and rollback.
2. Make settings deterministic and support explicit noninteractive inputs without hidden prompts.
3. Abstract provider commands, time, DNS, HTTP, SSH and filesystem probes so tests can use recorded sanitized fixtures.
4. Define immutable install and scan result manifests with build, platform, choices, owned resources and test outcomes.
5. Make cleanup idempotent and limited to resources created by the run. Include timeout and interrupted-run recovery.
6. Add fixture-driven checks for the discovery scenarios and Mesh failure states before provisioning a live fleet.
7. Publish test-only hooks through dependency injection, never production authentication bypasses.
8. Keep credentials, account identifiers and personal host inventories out of committed fixtures.

## Installation pathways and matrix

The two primary interaction pathways are the interactive install wizard and the unattended settings-driven install.
Also distinguish distribution entry points, because they can invoke the same underlying installer with different packaging.
Inspect `install.sh`, `install.ps1`, npm wrappers and `runtime/cli.py` before claiming support.

| Axis | Cases to model |
| --- | --- |
| Input pathway | Interactive wizard with supplied responses; noninteractive versioned settings file |
| Distribution | Clean source checkout; published release archive; supported npm and PowerShell wrappers |
| Machine | Fresh supported Linux x86_64/ARM64; supported macOS/Windows in suitable runners; unsupported version |
| Privilege/service | Supported user service; supported system service; missing privilege; missing service manager |
| Private connectivity | None/LAN; existing/new Tailscale; existing/new Mesh; external reachable SSH |
| Public ingress | Local-only; supported private serving; Cloudflare Tunnel; ngrok fixed/ephemeral; supported combinations |
| Policy | Restricted fresh defaults; reviewed custom policy; explicit admin; unavailable required sandbox |
| Authentication | Initial account setup, fresh full/read-only scopes, expiry/refresh/revoke, legacy migration |
| Fleet | No targets; alias/manual target; visible peer; no worker; existing worker; unreachable/excluded target |
| Lifecycle | Install, rerun, update same version, update new version, failed migration, rollback, uninstall owned resources |
| Failure | DNS/network loss, occupied port, invalid secret, disk pressure, interrupted process and partial provider enrollment |

Use pairwise coverage plus risk-focused combinations rather than the full Cartesian product.
Run the core file/exec/job/PTY/auth/discovery flows first. Browser/desktop extras have separate isolated fixtures.
Use Xvfb-based synthetic X11 desktop tests in an isolated CI runner, with output verification and cross-user negative cases.
Do not make the headless core install depend on a GUI stack or browser engine.

## Isolation architecture to evaluate later

Use disposable VMs where systemd, routing, kernel sandboxing, reboot and service installation are part of the claim.
Containers can cover protocol mocks and fast component tests. They do not prove a fresh VM, real Landlock support or every service-manager path.
Evaluate existing VM tooling and cloud-init images instead of writing a hypervisor manager.
Keep the lab runner separate from Loopback's production service and credentials.
Use dedicated test provider accounts or isolated organizations where live enrollment is necessary.
Do not join production networks automatically or publish an unauthenticated management endpoint.

A run should create isolated names, ports, credentials, disks and logs under a unique run ID.
Control provider endpoints through mocks first. Mark mock outcomes separately from live acceptance.
For live Mesh/Tailscale/Tunnel tests, enforce scoped credentials, budgets, resource tags, explicit ownership and expiry.
Snapshot before each installation path and restore between cases.
Never assume uninstall restores a machine sufficiently for a clean-install claim.

## Machine-readable run manifest

Use a schema such as the following. These are proposed fields, not a current CLI output contract.

```json
{
  "schema_version": 1,
  "run_id": "isolated-example",
  "source_commit": "<tested-commit>",
  "image_digest": "<verified-image>",
  "platform": {"os": "<supported-os>", "arch": "<arch>"},
  "install": {"input_mode": "settings", "distribution": "release"},
  "providers": {"private": "none", "primary_ingress": "local"},
  "policy_profile": "restricted",
  "checks": [{"id": "core.file", "outcome": "UNVERIFIED", "evidence": []}],
  "owned_resource_refs": [],
  "cleanup": {"status": "pending", "residual_refs": []}
}
```

Credentials are secret references supplied at runtime. They are not values in this manifest.
Record command exit status, bounded sanitized output, duration and exact build identity for each check.
Distinguish PASS, EXPECTED DENIAL, FAIL, BLOCKED, UNAVAILABLE, NOT TESTED and UNVERIFIED.
Retain failure diagnostics before cleanup, with a retention policy and no automatic external upload.

## Later phase exit criteria

Prove that every run starts from a known fresh image and leaves no unowned changes.
Exercise both input pathways with the same core acceptance suite and compare resulting state.
Test provider idempotence and rollback, including Mesh/Tailscale route overlap in isolation.
Confirm local and chosen public endpoints, OAuth lifecycle, restricted file/command boundaries and a selected fleet worker.
Publish the compatibility matrix and open platform limits without claiming universal coverage.
Until this phase runs, the handoff proves only that these seams and test plans exist.
