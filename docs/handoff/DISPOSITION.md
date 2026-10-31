# Recommendation disposition ledger

Branch `ls/26-continue-loopback-using-claude-opus-5-5--P5uJ`, based on `main` at `1071bb4`.
States: **implemented**, **partial**, **already present**, **deferred**, **rejected** (with reason), **blocked**.
"Implemented" means code plus tests in this branch. No row claims a production deployment or a live provider test.

## Work packages H01–H30

| ID | State | Evidence and remaining work |
| --- | --- | --- |
| H01 | partial | Clean task branch and checkpoint file (`docs/handoff/PROGRESS.md`). The owner's conflicted `/home/jq/loopback` checkout is not reachable from this sandbox: **blocked** for diagnosis. |
| H02 | implemented | Jobs and terminals carry a stable owner key (`kind:subject#client`). Every lifecycle call checks it. Cross-owner IDs read as not found. Admin view stays unscoped. `tests/test_ownership.py`. |
| H03 | implemented | Job dir 0700, logs and metadata 0600, per-owner quotas, output redaction (`runtime/redaction.py`), legacy records admin-only. The config directory is always denied to file and shell tools, even if a policy drops the defaults. |
| H04 | implemented | `runtime/url_policy.py`: parsed URLs, userinfo, controls, ports, ambiguous IPv4, IPv6, private, CGNAT, metadata, DNS-to-loopback, post-redirect re-check. `tests/test_browser_desktop_policy.py`. DNS rebinding between check and fetch is narrowed, not eliminated. |
| H05 | partial | Status now separates installed, functional, supported actions, TTL and remedy. Session TTL implemented. The pinned, verified opt-in `agent-browser` installer is **deferred** (needs a release artifact and hash). |
| H06 | partial | Owner-namespaced sessions and redirect rejection are tested with a fake runner. A real browser fixture with a synthetic page is **deferred** to the lab (needs the engine). |
| H07 | deferred | Authorized desktop session broker with consent and revocation needs a design review and an X11 lab. Status now labels the session source as unverified. |
| H08 | implemented | Bounded X11 probe, per-operation readiness including screenshots, explicit Wayland/macOS/headless states, injected runner. Xvfb CI fixture **deferred** to the lab. |
| H09 | already present | `default_policy` selects restricted with protected roots. This branch adds the config-dir invariant. Admin-risk migration notice: **deferred**. |
| H10 | deferred | Deterministic non-login exec shell changes command behaviour for existing users. Needs an explicit migration decision. zplug diagnosis needs the owner host. |
| H11 | partial | New stable prefixes: `quota_exceeded`, `url_denied`, `desktop_<op>_unavailable`. A full code/hint/correlation-ID contract is **deferred**. |
| H12 | deferred | Uniform timeout and output shape not changed in this branch. |
| H13 | deferred | ANSI/OSC stripping and wait-for-output not changed. |
| H14 | deferred | `patch_file` backup policy unchanged. |
| H15 | already present | Revoke controls exist (`auth revoke-client`, token revoke). Grant age and scope display: **deferred**. |
| H16 | deferred | Real-client OAuth lifecycle needs live providers. |
| H17 | partial | Browser and desktop status now distinguish installed, reachable and per-operation ready. Doctor capability matrix: **deferred**. |
| H18 | implemented | Discovery hosts keep separate timestamped axes (dns, ssh, bootstrap, process) with TTL and freshness. Missing hosts stay as stale. |
| H19 | implemented | `runtime/scan.py`, `runtime/scan_api.py`, `docs/DISCOVERY-API.md`. Scenario coverage: `tests/test_scan_scenarios.py` (30 run, 13 skipped with reasons below). |
| H20 | implemented | `runtime/mesh.py`, `loopback mesh …`, installer `--private-connectivity mesh`, `docs/MESH.md`, `tests/test_mesh.py`. Live enrollment **UNVERIFIED**. |
| H21 | implemented | Structured redacted approval records, fingerprint bound to cwd and caller, 409 on stale decisions, history API, dashboard detail panel, discovery table, Mesh card, stacked mobile tables. Screenshots in the verification set. |
| H22 | partial | Idle terminal reaping, PID start-time check, no default screenshot persistence. Job idle cleanup: **deferred**. |
| H23 | deferred | Per-target remote allowlists or remote sandbox. |
| H24 | deferred | Support bundle with capability snapshot. Existing redacted bundle unchanged. |
| H25 | deferred | Hash pinning, SBOM and dependency scan. No new dependency was added in this branch. |
| H26 | partial | Installer Mesh-choice test added. Cross-platform CI matrix: **deferred**. |
| H27 | deferred | No performance baseline captured. Discovery limits are initial budgets only. |
| H28 | deferred | Capability groups and remediation for hidden tools. |
| H29 | partial | README logo path fixed. New docs: `DISCOVERY-API.md`, `MESH.md`. X11/Wayland limits are stated in desktop status. Platform doc pass: **deferred**. |
| H30 | implemented | `runtime/lab.py` (manifest with secret guard, owned-resource cleanup, pairwise matrix, replay runner), `docs/schemas/lab-run-manifest.v1.json`, `tests/test_lab_seams.py`. No live lab was created. |

## Gates

| Gate | State |
| --- | --- |
| G1 Clean source, CI, install/update/rollback evidence | partial: local unittest and ruff pass; CI runs on the PR; platform evidence deferred |
| G2 Fresh-install capability preflight | partial: browser/desktop status only |
| G3 Browser fixture, URL negatives, ownership, cleanup | partial: URL negatives and session isolation with fake runner; real browser fixture deferred |
| G4 Desktop fixture and isolation | deferred |
| G5 Deterministic shells | deferred |
| G6 Real-client OAuth lifecycle | deferred (unchanged code) |
| G7 Core regression coverage | met locally: 355 Python tests, 24 dashboard JS tests |
| G8 Threat review | partial: SSRF and process-ownership addressed; CSRF uses the existing admin gate |
| G9 Compatibility and support docs | partial |
| D1 Discovery scenarios | partial: see skipped list |
| M1 Mesh idempotent, credential-safe, reversible | met with fixtures; live UNVERIFIED |
| U1 Approval readable at desktop and mobile | met: screenshots at both widths |
| L1 Lab inputs, cleanup and manifests with fixtures | met with fixtures |

## Discovery scenarios skipped (D1)

D05 (needs real ACLs), D15 (worker version handshake belongs to Fleet), D23–D27 and D34 (route correlation lives in `discovery.py` and `ingress.py`; not extended here), D29 (worker capability), D31 (no remote OS capability model). D35 is covered in `tests/test_browser_desktop_policy.py`. D18/D19 are covered in `tests/test_mesh.py`.

## Rejected

- `StrictHostKeyChecking=no` or silent key acceptance in discovery: rejected. Unknown keys go through fingerprint review.
- Applying Mesh from the web dashboard: rejected. Root changes run only through the owner-side CLI.
- Tabulator or a frontend framework: rejected for now. Native tables handle the 200-row requirement.
