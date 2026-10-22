#!/usr/bin/env bash
# End-to-end install smoke test (CI: Debian, Fedora, Alpine, Arch containers + macOS).
#
# 1. non-interactive, local-only install into a temporary LOOPBACK_CONFIG_DIR
#    whose path contains a space, with --no-service (the gateway runs via 'up')
# 2. wait for /healthz
# 3. loopback doctor --deep           (must report no errors)
# 4. MCP initialize with a temporary API token (revoked afterwards)
# 5. execute 'pwd' through MCP; when the OS sandbox is active it must equal the
#    service account's home directory
# 6. loopback down                    (healthz must stop answering)
#
# Environment:
#   PYTHON               interpreter for the installer (default: python3)
#   LOOPBACK_SMOKE_PORT  port to use (default 2026)
#   SMOKE_INSTALL_ARGS   extra installer flags, e.g. "--skip-venv --python-override /opt/venv/bin/python"
#   KEEP_SMOKE_DIR=1     keep the temporary directory for inspection
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
PORT="${LOOPBACK_SMOKE_PORT:-2026}"
WORK="$(mktemp -d 2>/dev/null || mktemp -d -t loopback-smoke)"
export LOOPBACK_CONFIG_DIR="$WORK/loopback config"
export LOOPBACK_BIN_DIR="$WORK/bin"
CLI="$LOOPBACK_BIN_DIR/loopback"
FAILED=1

say(){ printf '\n== %s\n' "$*"; }
die(){ printf '✗ %s\n' "$*" >&2; exit 1; }

finish(){
  if [ -x "$CLI" ]; then "$CLI" down >/dev/null 2>&1 || true; fi
  if [ "$FAILED" = 1 ] && [ -f "$LOOPBACK_CONFIG_DIR/server.log" ]; then
    echo "---- server.log (tail) ----" >&2
    tail -n 60 "$LOOPBACK_CONFIG_DIR/server.log" >&2 || true
  fi
  if [ "${KEEP_SMOKE_DIR:-0}" = 1 ]; then
    echo "kept: $WORK" >&2
  else
    rm -rf "$WORK"
  fi
}
trap finish EXIT

EXTRA=()
if [ "$(id -u)" = 0 ]; then EXTRA+=(--allow-root); fi
if [ -n "${SMOKE_INSTALL_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  EXTRA+=(${SMOKE_INSTALL_ARGS})
fi

say "install ($("$PY" --version 2>&1), config dir: $LOOPBACK_CONFIG_DIR)"
"$PY" "$ROOT/runtime/cli.py" install --non-interactive --ingress local --no-service --yes \
  --port "$PORT" --source "$ROOT" ${EXTRA[@]+"${EXTRA[@]}"}

[ -x "$CLI" ] || die "CLI wrapper was not installed at $CLI"

say "health"
ok=0
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 0.5
done
[ "$ok" = 1 ] || die "/healthz did not answer on port $PORT"
curl -fsS "http://127.0.0.1:$PORT/healthz"; echo

say "doctor --deep"
"$CLI" doctor --deep

say "MCP initialize + execute pwd"
RESULT="$("$CLI" _helper verify-mcp --port "$PORT" --exec pwd)"
LB_RESULT="$RESULT" "$PY" - <<'PY'
import json, os
data = json.loads(os.environ["LB_RESULT"].strip().splitlines()[-1])
assert data.get("ok"), f"MCP initialize failed: {data}"
print("initialize: ok", data.get("server"), data.get("protocol"))
exe = data.get("exec") or {}
sandbox = data.get("sandbox") or {}
home = data.get("home")
print("execute pwd:", repr(exe.get("stdout")), "returncode", exe.get("returncode"), "sandbox", sandbox.get("backend"), sandbox.get("active"))
if sandbox.get("active"):
    assert exe.get("returncode") == 0 and not exe.get("is_error"), f"execute failed: {exe}"
    cwd = (exe.get("stdout") or "").strip()
    assert os.path.realpath(cwd) == os.path.realpath(home), f"pwd {cwd!r} != service home {home!r}"
    if os.environ.get("HOME") and os.path.realpath(os.environ["HOME"]) != os.path.realpath(home):
        print(f"note: $HOME={os.environ['HOME']} differs from the account home {home}")
else:
    print("note: OS sandbox inactive here (" + str(sandbox.get("reason")) + "); execute check skipped")
PY

say "status / report"
"$CLI" status
"$CLI" report >/dev/null

say "down"
"$CLI" down
sleep 1
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then die "gateway still answers after 'down'"; fi

FAILED=0
printf '\n✓ install smoke test passed\n'
