# Loopback continuation checkpoint — 2026-10-27

**Verified with deployment open items.** The integration fixes work in the real
sandbox application. They are not installed on the owner's desktop or VMs yet.
Read this checkpoint before the historical `HANDOFF-2026-10-25.md`.

## Branch and saved revisions

- Branch: `ls/16-review-handoff-and-update-dashboard-desi-1o9R`.
- Base: `d3e00db` (`origin/main`). `57f7ba3` saves recovery controls. `dbfa761`
  saves the first proof checkpoint. `83ea1ca` saves the enrollment, origins and
  topology fixes. The latest commit adds the standard test-run integration and
  this refreshed checkpoint. Use `git log -1` for its exact revision.
- Pushes use this same branch. After the final push, the working tree is clean.
- The assigned remote branch initially did not exist. It now exists on origin.
- GitHub lists PRs #1–#7 as merged. Lightsprint returns no linked PR for task
  `U0KwdNIId2CEBU7bMC8S5`. No new PR was created because the launch explicitly
  prohibited it. Resolve the PR destination with the owner before creating one.

## Completed changes

- Show recovery Start beside Stop when healthy worker telemetry confirms that a
  desired-running service lost its bridge. Polling updates those controls.
- Include the one-time code fragment in worker enrollment links. Reject
  credential creation for stopped bridges.
- End worker enrollment on an authenticated connection page with the service's
  MCP URL, OAuth instructions and sign out. Preserve gateway enrollment behavior.
- Escape connection-page content and retain CSP, no-store and nosniff headers.
- Ignore retained public hostnames when a service changes to private exposure.
  Its advertised endpoint and OAuth origin remain local.
- Apply the validated default Tailscale HTTPS port before service startup.
- Distinguish cold, stale, stopped and unreachable worker states in topology.
  Hide expired CPU/RAM values and retain the last heartbeat.
- Run the five browser-free Node regressions through normal unittest discovery
  when Node.js is available. The existing CI therefore exercises them too.

## Checks and runtime evidence

- **137 Python tests pass**, including the wrapper for **five Node tests**.
- Ruff passes across runtime, tests and scripts. Bandit finds no medium/high
  issues. JavaScript syntax and `git diff --check` pass.
- Actual HTTP integration: missing MCP credential returns 401, enrollment reaches
  the final connection page, PKCE exchange succeeds, and the real stdio `echo`
  tool returns `worker-oauth-roundtrip`. Revocation makes that token return 401.
  No bearer token or password is stored in this checkpoint.
- This runtime test uses isolated sandbox data and actual processes. It does not
  prove a ChatGPT/Perplexity browser integration or a new public VM endpoint.
- Fresh read-only SSH metrics succeed on ora1, ora3, ora5, ora6, ora7, aws1 and
  aws2. `ora1-system` is running. Ora2/ora4 remain untouched.
- Desktop observations: systemd user service is enabled and active. Its
  Cloudflare child runs. The isolated deployment source is clean at `05bbd87`.
  Local health succeeds. Current Tailscale Serve configuration is `{}`.
- No live runtime, worker, credential, DNS or tunnel was changed. RotoXY remains
  untouched. No reboot or deliberate live disconnection was attempted.

## Proof

`/proof` routes the full branch diff to visual and logic proof. It charts four
changed flows. Every flow has passing test or runtime evidence. Tests and
checkpoint text add no separate runtime flow.

The task has seven real UI captures and four flow charts. The earlier missing
service capture failures were replaced. The final set must have zero failed
entries and `staleCount: 0`. Source hashes, not capture age, determine freshness.

- [Recovery before start](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/screenshots/U0KwdNIId2CEBU7bMC8S5/EOoU2l1llDdIUf-5Sdewb.png)
- [Recovery after start and reload](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/screenshots/U0KwdNIId2CEBU7bMC8S5/DObN2Bc_KcTn7osyLHZYv.png)
- [Usable Connect link](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/screenshots/U0KwdNIId2CEBU7bMC8S5/ZTshZIJGLyqPJKbMId4-G.png)
- [Worker enrollment complete](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/screenshots/U0KwdNIId2CEBU7bMC8S5/SesTV_vAE-MeP_BDzagoZ.png)
- [Recovery flow](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/2026-10-27/G_3JBMKSAjTs5u68nOXdT.png)
- [Enrollment flow](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/2026-10-27/w9XIeWGrIb8JTJFI6GyDu.png)
- [Service origins flow](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/2026-10-27/ueI5urqJDOglB2LjcAdJM.png)
- [Worker freshness flow](https://lightsprint-image-uploads.s3.us-east-1.amazonaws.com/uploads/images/2026-10-27/EcuLzaE8HU6sG91o5AZvE.png)

The worker desktop capture returned a valid image while the listing later marked
it timed out. The returned image was downloaded and visually inspected, then
registered again under the same label. No failed image was substituted.

## Preview and reproducibility

- The real controller runs at `dev:3000`, route `/admin#/services`, with isolated
  data at `/tmp/loopback-recovery-proof`. A real test worker listens on 3031.
- Port 3000 was free. The configured RotoXY process listens separately on 2030.
  No stack configuration or unrelated app was changed. The earlier 3030 preview
  was stopped. The controller was restored after worker-page captures.
- Python dependencies: `/tmp/loopback-dashboard-venv`. Recreate a venv and install
  `requirements.txt` after sandbox replacement. Run:
  `python -m unittest discover -s tests -v` and
  `node --test tests/dashboard_services.test.cjs`.
- Stop the isolated preview worker before the full suite. An existing listener
  protection test expects its fixture port 3031 to be free. Restore it afterward.
- Diagram rendering used a browser only for Mermaid. Application screenshots ran
  server-side through Lightsprint. No screenshot browser ran in the sandbox.

## Remaining work and access limits

1. Resolve the missing PR destination with the owner. Do not claim that these
   pushes updated an existing PR. Do not mark the task Done.
2. Install this branch from the owner's local desktop terminal. The connected
   MCP shell deliberately denies its own runtime. Do not weaken its sandbox.
   The known clean source checkout is
   `~/.local/share/loopback-deploy/source-35ab185`. Fetch the assigned branch,
   detach at FETCH_HEAD, then use the existing updater. Preserve the original
   `/home/jq/loopback` checkout and all credentials.
3. After desktop deployment, update the seven eligible worker installations and
   restart only the chosen service when needed. Existing processes do not load
   new code merely because worker files change.
4. Verify the installed dashboard, idempotent `loopback up`, existing connector,
   and one real worker enrollment/call. Do not interrupt the only remote link.
5. A real public MCP client browser flow remains unverified. Creating new public
   endpoints needs an intended hostname, origin and target-local credentials.

The existing design is preserved. A broad redesign needs concrete owner
requirements. Automatic VM reboot recovery and advanced stdio callbacks remain
existing limitations in `WORKERS.md`, not newly implemented features.

## Model and recovery policy

Explicit subagent model and reasoning selection work. Switching the active parent
model programmatically is unavailable. Sol with medium reasoning handles the
engineering changes. No Astra escalation is needed so far.

Use Luna for routine tasks and Sol for engineering. Dispatch `gpt-6-astra` only for
an unresolved security/architecture risk or after two distinct evidence-based
approaches fail. Give it relevant files, errors, attempted solutions and a narrow
deliverable. Return to Sol afterward. Never change provider credentials.
