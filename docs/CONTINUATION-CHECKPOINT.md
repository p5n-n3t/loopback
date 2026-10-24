# Loopback continuation checkpoint

Updated: 2026-10-24. Task ITSCOM-1; PR https://github.com/p5n-n3t/loopback/pull/4.
Working branch: `ls/1-so-i-am-starting-this-prompt-task-with-t-_EO8`.

## Latest state (2026-10-24, after owner applied updater)

### Startup regression follow-up

PR #5 is now merged into main (3b9f03b); the owner authorized follow-up PRs. Its
earlier delivery blockers below are historical. The desktop connector currently
returns Cloudflare HTTP 530 / error 1033, so no new live desktop checks are possible.

The next fix makes `loopback up` and `restart` explicitly reconcile configured
Cloudflare ingress even when the gateway is already running. Connector launch
failure returns nonzero, and upgrades check this gate too. An existing external
YAML can be imported once with `loopback up --cloudflare-config PATH --host HOST`;
subsequent starts need only `loopback up`. Linux reuses a same-user connector whose
exact saved config/token path matches, leaving other connectors alone. Existing
YAML, credentials, DNS and Tailscale routes are not rewritten.

The owner's existing connector config is
`/home/jq/.config/ai-bridge/cloudflared.yml`, hostname `loopback.ziopsyop.tech`.
After this fix is installed, import that file once. To update while an old tunnel
definition is incomplete, use the existing local source updater with
`upgrade --source . --yes --no-restart`, then the import command above. Runtime
updates must be run by the owner locally; do not weaken the MCP sandbox to do it.

Validation: 123 tests pass, including eight startup regressions with real detached
connector processes, recovery while the server is up, exact-config process reuse,
import persistence, migration and upgrade failure gating. The connector fixture
does not connect to Cloudflare; actual edge recovery remains pending owner action.

- Source branch is pushed; no new PR opened. PR #4 was already merged before this continuation, so these continuation commits are not on main.
- Desktop runs v4.0.0 from `729b791`, successfully upgraded from v2.1.0 with backup and automatic rollback available. Owner ran the follow-up updater. Local checkout with saved changes is preserved.
- All seven eligible workers are installed and report actual CPU/RAM: ora1, ora3, ora5, ora6, ora7, aws1, aws2. Targets use their existing SSH aliases with `use_ssh_config: true`. Ora2 and ora4 are untouched.
- A private read-only `ora1-system` stdio MCP binding on ora1:3031 is running. An actual authenticated MCP tool call returned hostname ora1, cpu_count 4. Its verification credential was revoked immediately. Other MCP services can be configured from Services & tunnels; no new public VM tunnel has been created.
- Desktop Tailscale Serve configuration remains `{}`; no RotoXY URL, listener, service or Cloudflare connector was changed. The existing Cloudflare path serves the upgraded dashboard.
- Desktop has no loopback.service startup unit. Owner was told to run `loopback autostart enable` locally before restarting if they want automatic dashboard startup. Remote ora1 service survives desktop restart; VM reboot auto-restart is not implemented.
- All 115 tests pass; ruff and JS syntax checks pass; bandit reports no medium/high issues. Standard screenshot proof now works by starting the actual Loopback app on the previously idle, declared preview port 5173. Preview data is isolated and explicitly seeded, not a representation of VM connectivity.
- Screenshots refreshed, plus reload-proven recordings of service creation and filesystem policy saving; three new logic flow charts cover workers/routes, per-service MCP authorization/forwarding, and attributed telemetry.
- Final visual inspection found and fixed an invisible mobile hamburger (flex gaps had collapsed the bars). This CSS-only correction is on the final branch after `729b791`; apply the same local updater again when convenient to deploy it. It does not change the running VM workers.

The historical notes below document the investigation and intermediate blockers; the latest state above supersedes their pending-work statements.

## Delivery state

Branch pulled clean at start. GitHub and Lightsprint both report PR #4 MERGED (2026-10-24), despite the continuation brief calling it open. Do not open another PR without resolving this with the user. Further branch pushes cannot change the already-merged main commit.

## Requested continuation

- Preserve existing v4 security, policy, migration and tests.
- Central dashboard on user's desktop; lightweight remote workers for arbitrary MCP services (HTTP and stdio), mixed Cloudflare/Tailscale routes, independently managed ports and endpoints without an artificial count cap.
- Remote shell access independent of local shell access; local access can be disabled without disabling VM workers.
- Live worker CPU/RAM (percent and absolute totals) and ingress/egress rates; distinguish Loopback process usage from host usage.
- Never overwrite the user's RotoXY Tailscale URL or `/mcp` handler. Do not deploy to ora2 or ora4.
- Read tailnet inventory through the connected Loopback MCP. User authorizes desktop/VM access and deployment, tests, push and merge.
- Redesign using Figma file `L1tPbHX8taPk3iRoMOsACC`, adapting the analytics kit to Loopback. Symbol in dashboard; symbol and wordmark in repo branding. Read provided 22 frame IDs from session prompt.

## Implementation checkpoint (12:49 UTC)

Implemented: `runtime/workers.py` central registry/control; `node_agent.py` SSH-only worker; `worker_bridge.py` headless per-binding OAuth/stdio/HTTP edge; dashboard Services & tunnels, worker telemetry and Figma-derived light design; symbol in dashboard and wordmark in README; legacy Tailscale collision protection; topology service/worker data. See `docs/WORKERS.md` for limits. Workers do not currently auto-restart after VM reboot and stdio callbacks beyond tools/resources/prompts are not forwarded.

Checks: 113 unit/integration tests pass (including real stdio MCP roundtrip, separate edge auth, CSRF/persistence, occupied RotoXY listener rejection, PID reuse, metric delta semantics); ruff clean; bandit no medium/high findings. No deployment yet.

Access: desktop `/home/jq/loopback` is clean on local `main` at `5fcf275` (local saved changes); preserve it. Live runtime is v3 under `~/.config/loopback`, port 2026, public host `loopback.ziopsyop.tech`, a separate existing cloudflared process under `~/.config/ai-bridge`. Desktop Tailscale Serve status is empty. SSH reachable as jq on ora1/3/5/6/7 and aws1/2; do not deploy ora2/4. ora1 and ora3 have Python 3.14 and no Serve handlers.

## Verification / delivery still pending

`/proof` loaded and routed to visual + logic proof. Loopback runs in this sandbox on port 3030 (`/tmp/loopback-preview`, process via exec session). Stack only declares `dev:5173` and `icvacation-dev:3000`. Sandbox config GET returns 403 (admin-only). Capture preflight `service:3030` returns `unknown_service`; do not substitute another app or build a mock. Real visual evidence needs an admin to declare Loopback, or external capture of the actual deployed desktop app. Cloudflare Browser Rendering connector is available; ziopsyop.tech belongs to account `0228e3fe174bbc185f30b1d4eaf5f166` (zone lookup confirms). No DNS/config changes made.

Next: deploy from an isolated source checkout on desktop using the existing upgrade/rollback mechanism, preserving local checkout and ingress; install workers to selected eligible VMs; exercise live calls. Complete /proof, update checkpoint with actual deployment and remaining items, final commit/push to same branch, list verification set after final edits and post one concise comment on PR #4. PR #4 remains already merged; branch pushes do not update main. No new PR is authorized.

## Live deployment checkpoint (13:03 UTC)

Desktop upgrade SUCCEEDED from 2.1.0-oauth to 4.0.0 using source `35ab185`. Backup: `~/.config/loopback/backups/pre-upgrade-2.1.0_oauth-20261024-154953.tar.gz`. Live health returns v4.0.0, admin policy, Landlock. Existing Loopback MCP still works through migrated legacy credential. Original Cloudflare connector left in place; `tailscale serve status --json` remains `{}`. No RotoXY service changed.

Actual dashboard captured through connected Cloudflare Browser Rendering (server-side) with a valid session at https://loopback.ziopsyop.tech/admin. Desktop image saved in sandbox `/tmp/loopback-live-overview.jpg` (not yet final evidence; needs upload/register under existing Overview label). Initial Cloudflare call timed out; valid call needs at least two `formats` entries, e.g. screenshot + markdown.

Worker installation was attempted on ora1/3/5/6/7 and aws1/2; none succeeded yet. Root cause: explicit port 22 in target registry overrides working SSH alias settings (ora1 resolves to shh-ora1.ziopsyop.tech in the desktop SSH config). Direct tailnet SSH requires interactive authentication. Do not keep retrying or change Tailscale policy. A Tailscale check URL was printed by `tailscale ssh jq@ora1`; refresh it if needed.

Follow-up implementation adds `use_ssh_config` to SSH targets, preserves the alias's own port/host/user, exposes a checkbox in SSH config, handles command timeouts, and polls Fleet/Topology every five seconds. It is tested but still needs re-deploying to the desktop before these settings work there. The live MCP shell is now correctly confined and cannot write its own runtime; do not disable its sandbox or loosen sensitive-root permissions to work around that.

The owner can apply final branch code from a local desktop terminal using the isolated checkout (not their original checkout):

```sh
cd ~/.local/share/loopback-deploy/source-35ab185 && git fetch origin ls/1-so-i-am-starting-this-prompt-task-with-t-_EO8 && git checkout --detach FETCH_HEAD && python3 runtime/cli.py upgrade --source . --yes
```

Then use the admin API/session or dashboard to save the seven SSH targets with `host` equal to their original working alias (`ora1`, etc.) and `use_ssh_config: true`, install workers, and check actual install logs/readiness. Do not use ora2 or ora4. Finish visual proof using real deployed dashboard captures and register current artifacts under existing labels; do not leave stale captures unaddressed. New flow diagrams are staged in `/tmp/flow-diff` (Mermaid CLI and headless browser installed solely for diagrams, not application screenshots). New runtime/tests/docs remain on the task branch; no new PR has been opened. PR #4 was already merged before this continuation.
