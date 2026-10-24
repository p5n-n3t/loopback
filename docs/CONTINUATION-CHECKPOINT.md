# Loopback continuation checkpoint

Updated: 2026-10-24. Task ITSCOM-1; PR https://github.com/p5n-n3t/loopback/pull/4.
Working branch: `ls/1-so-i-am-starting-this-prompt-task-with-t-_EO8`.

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
