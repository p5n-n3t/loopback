# Staged discovery and Mesh admin API

These endpoints implement [DISCOVERY-DESIGN.md](handoff/DISCOVERY-DESIGN.md) stages S0–S5.
They live under `/api/admin/` and need the dashboard session cookie and the `X-Loopback-CSRF` header.
The older read-only route inventory (`GET /api/admin/discovery`, `POST /api/admin/discovery/refresh`) is unchanged.

Code: `runtime/scan.py` (engine), `runtime/scan_api.py` (routes), `runtime/mesh.py` (Mesh).
State: `<config>/discovery_state.json` (mode 0600, `schema_version` 1). Loopback-confirmed host keys: `<config>/known_hosts`.

## Safety rules

- Listing (S0–S2) reads local provider state only. It contacts no remote host.
- Probing (S3/S4) runs only for host IDs in the request. It uses OpenSSH `BatchMode=yes` and `StrictHostKeyChecking=yes`.
- An excluded host is never resolved, probed or enrolled. Exclusions match aliases, FQDNs, short names, addresses and CIDR ranges.
- Enrollment changes nothing until a stored plan is applied. Rollback removes only SSH targets that the plan created.
- Mesh routes are read-only. Applying Mesh needs root on the host: `sudo loopback mesh apply --settings FILE --yes`.

## Endpoints

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /api/admin/discovery/hosts` | — | Snapshot (below). No I/O except reading state. |
| `POST /api/admin/discovery/hosts/list` | — | Runs S0–S2 and returns the snapshot. |
| `POST /api/admin/discovery/probe` | `{host_ids: [id, …]}` (1–200) | `202` run. Repeating the same selection while it runs returns the same run. |
| `GET /api/admin/discovery/runs/{run_id}` | — | Run with stage states and per-host outcomes. |
| `POST /api/admin/discovery/runs/{run_id}/cancel` | — | Run. Cancellation stops new probes and keeps finished results. |
| `POST /api/admin/discovery/manual` | `{host, port, user, label, jump}` | `201` manual entry. |
| `DELETE /api/admin/discovery/manual/{entry_id}` | — | `{removed}`. |
| `POST /api/admin/discovery/hostkey/scan` | `{host_id}` | `{fingerprints: ["256 SHA256:… (ED25519)"], note}`. |
| `POST /api/admin/discovery/hostkey/confirm` | `{host_id, fingerprint: "SHA256:…"}` | `{trusted}`. Rescans and trusts only keys that still match. |
| `POST /api/admin/discovery/enroll/plan` | `{host_ids, install_worker}` | Plan with steps, privileges, rollback and blocked hosts. |
| `POST /api/admin/discovery/enroll/apply` | `{plan_id}` | Plan with per-step `done` and `error`. Completed steps are skipped on retry. |
| `POST /api/admin/discovery/enroll/rollback` | `{plan_id}` | `{removed, preserved}`. Workers are preserved for manual removal. |
| `GET /api/admin/mesh` | — | Mesh detection, preflight, platform support and plan. |

## Snapshot

```json
{
  "schema_version": 1,
  "generated_at": 1761900000.0,
  "empty_reason": null,
  "counts": {"local": 1, "new": 2, "excluded": 1},
  "excluded": ["ora2", "ora4"],
  "providers": {"tailscale": {"state": "running", "account": "example.ts.net", "candidates": 3,
                              "limitation": "Only devices visible to this account under its ACL appear."}},
  "hosts": [{
    "id": "h_4ebdbed241ad7891", "display_name": "build-vm", "aliases": ["build-vm", "build-vm.example.ts.net"],
    "addresses": ["100.101.2.3"], "state": "new", "next_action": "Select the host and run Probe to check SSH access.",
    "excluded": false, "managed": false,
    "ssh": {"alias": "build-vm", "endpoint": "100.101.2.3", "port": 6722, "user": "dev", "jump": null},
    "sources": [{"provider": "tailscale", "record_id": "n1", "observed_at": 1761900000.0, "evidence": "visible in tailscale status"}],
    "axes": {"ssh": {"result": "ok", "freshness": "fresh", "observed_at": 1761900000.0, "error": null}}
  }],
  "conflicts": [{"kind": "same_short_name", "name": "db", "host_ids": ["h_…", "h_…"]}],
  "runs": [], "manual": [], "stages": [{"id": "S0", "title": "Scope"}]
}
```

`empty_reason` is `not_scanned`, `no_provider`, `provider_unavailable` or `no_peers` when only the local host exists.

### Host states

`local`, `new`, `existing`, `excluded`, `offline`, `stale`, `auth_required`, `host_key_review`, `human_check`, `enrollment_required`, `failed`.

### Provider states

| Provider | States |
| --- | --- |
| `tailscale` | `missing_binary`, `daemon_down`, `unenrolled`, `stopped`, `malformed`, `unavailable`, `running` |
| `mesh` | `missing_binary`, `service_down`, `unregistered`, `registered_disconnected`, `connected` (`peer_listing: unsupported_locally`) |
| `ssh_config` | `not_found`, `unavailable`, `read` |
| `ssh_target`, `worker` | `configured`, `unavailable` |

### Axis results

Each axis is an observation with `observed_at`, `expires_at`, `freshness` and `source`.
The SSH axis result is one of `ok`, `auth_required`, `host_key_unknown`, `host_key_mismatch`, `human_check_required`, `dns_failure`, `unreachable`, `timeout`, `no_access_route` or `error`.
Peer visibility, DNS, SSH, worker heartbeat and endpoint health stay separate axes.

## Limits

`config.discovery` sets `probe_concurrency` (4), `enroll_concurrency` (2), `command_timeout` (8 s) and `scan_timeout` (120 s).
These are initial budgets, not measured performance claims.
