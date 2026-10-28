#!/usr/bin/env bash
# Run locally on ora8, as the dedicated owner-provisioned lab account.
set -Eeuo pipefail
exec python3 - "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" <<'PY'
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse as up


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


uid = os.getuid()
account = pwd.getpwuid(uid)
require(uid != 0 and account.pw_name != "jq", "Refusing root or jq")
require(account.pw_name == os.environ.get("LOOPBACK_LAB_USER") == "loopback-lab",
        "Run as the dedicated loopback-lab account with LOOPBACK_LAB_USER=loopback-lab")
require(socket.gethostname().split(".")[0] == "ora8", "This harness is restricted to ora8")
require(not os.environ.get("SUDO_USER"), "Log in directly as loopback-lab, without sudo environment")
source = Path(sys.argv[1]).resolve()
require((source / "runtime/cli.py").is_file(), "Source checkout is missing")
for tool in ("bash", "curl", "tar"):
    require(shutil.which(tool), "Owner must provision " + tool)
import venv, ensurepip  # require prerequisites before install.sh can offer package installation
require(sys.version_info >= (3, 11), "Owner must provision Python 3.11+")
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 3147))  # fail closed, never select another port or stop its occupant

os.umask(0o077)
work = Path(tempfile.mkdtemp(prefix="loopback-ora8-lab-"))
result_fd, result_name = tempfile.mkstemp(prefix="loopback-ora8-result-", suffix=".json", dir=account.pw_dir)
os.close(result_fd)
result = {"host": "ora8", "account": account.pw_name, "port": 3147, "checks": [], "ok": False}
env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": account.pw_dir,
       "USER": account.pw_name, "LOGNAME": account.pw_name, "LANG": "C.UTF-8",
       "LOOPBACK_SERVICE_MANAGER": "none", "PIP_NO_INPUT": "1", "PIP_DEFAULT_TIMEOUT": "30",
       "LOOPBACK_CONFIG_DIR": str(work / "config/loopback"), "LOOPBACK_BIN_DIR": str(work / "bin")}
for key, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
                       ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "run")):
    env[key] = str(work / directory)
    (work / directory).mkdir(exist_ok=True, mode=0o700)
cfg = Path(env["LOOPBACK_CONFIG_DIR"])
cli = str(work / "bin/loopback")


def check(label):
    result["checks"].append(label)
    print("PASS " + label, flush=True)


def run(argv):
    # Discard installer/account/enrollment output. It can contain credentials.
    with tempfile.TemporaryFile(dir=work) as output:
        process = subprocess.Popen(argv, env=env, cwd=work, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = process.wait(timeout=240)
            require(rc == 0, "Command failed: " + Path(argv[0]).name + " (output suppressed)")
            output.seek(0)
            return output.read().decode("utf-8", "replace")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def stop_owned():
    pid_file = cfg / "server.pid"
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text().strip())
    proc = Path("/proc") / str(pid)
    if not proc.exists():
        return
    try:
        if ") Z " in (proc / "stat").read_text():
            return
    except FileNotFoundError:
        return
    require(proc.stat().st_uid == uid and str(cfg).encode() in (proc / "cmdline").read_bytes(),
            "Refusing cleanup of a process without matching UID and config path")
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        try:
            if ") Z " in (proc / "stat").read_text():
                return
        except FileNotFoundError:
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)


def interrupted(*_):
    raise RuntimeError("Harness deadline or interruption")


signal.signal(signal.SIGALRM, interrupted)
signal.signal(signal.SIGHUP, interrupted)
signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
signal.alarm(1200)
try:
    flags = ["--non-interactive", "--ingress", "local", "--no-service", "--no-autostart", "--yes", "--port", "3147"]
    run(["bash", str(source / "install.sh"), *flags, "--source", str(source)])
    require((cfg / "venv/bin/python").is_file(), "Installer did not create a virtual environment")
    check("checkout install.sh with private venv")
    # Use the installed dependencies for actual network requests, without provider credentials.
    sys.path.insert(0, str(next((cfg / "venv/lib").glob("python*/site-packages"))))
    import httpx
    base = "http://127.0.0.1:3147"
    with httpx.Client(base_url=base, timeout=15, trust_env=False, follow_redirects=False) as client:
        def request(method, path, status=200, **kw):
            response = client.request(method, path, **kw)
            require(response.status_code == status, "Unexpected HTTP status at " + path.split("?")[0])
            return response
        request("GET", "/healthz")
        listeners = [line.split() for line in Path("/proc/net/tcp").read_text().splitlines()[1:]
                     if line.split()[1].endswith(":0C4B") and line.split()[3] == "0A"]
        require(listeners and all(row[1] == "0100007F:0C4B" and int(row[7]) == uid for row in listeners),
                "Port 3147 listener is not private to the lab UID on IPv4 loopback")
        require(not any(line.split()[1].endswith(":0C4B") and line.split()[3] == "0A"
                        for line in Path("/proc/net/tcp6").read_text().splitlines()[1:]),
                "Unexpected IPv6 listener on lab port")
        require(not (work / "config/systemd/user/loopback.service").exists(), "Unexpected service unit")
        check("local HTTP health and private loopback listener")
        mcp = json.loads(run([cli, "_helper", "verify-mcp", "--port", "3147", "--exec", "pwd"]).strip().splitlines()[-1])
        require(mcp.get("ok"), "MCP initialization failed")
        execution = mcp.get("exec") or {}
        require(execution.get("returncode") == 0 and not execution.get("is_error"), "MCP pwd did not execute")
        check("authenticated MCP initialize and pwd")
        recovery_token = (cfg / "token").read_text().strip()
        redirect = "http://127.0.0.1:3147/lab-callback"
        resource = base + "/mcp"
        for auth_method in ("none", "client_secret_basic", "client_secret_post"):
            reg = request("POST", "/oauth/register", 201, json={"redirect_uris": [redirect],
                "client_name": "ora8 isolated lab", "token_endpoint_auth_method": auth_method,
                "grant_types": ["authorization_code", "refresh_token", "client_credentials"]}).json()
            require(reg["grant_types"] == ["authorization_code", "refresh_token"], "Grant narrowing failed")
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            html = request("GET", "/oauth/authorize", params={"client_id": reg["client_id"],
                "redirect_uri": redirect, "response_type": "code", "code_challenge": challenge,
                "code_challenge_method": "S256", "resource": resource, "scope": "loopback:read", "state": "lab"}).text
            fields = {name: re.search(r'name="' + name + r'"[^>]*value="([^"]+)"', html).group(1)
                      for name in ("request_id", "csrf")}
            consent = request("POST", "/oauth/authorize", 302,
                data={**fields, "decision": "approve", "credential": recovery_token})
            code = up.parse_qs(up.urlparse(consent.headers["location"]).query)["code"][0]
            form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                    "redirect_uri": redirect, "resource": resource}
            credentials = {}
            if auth_method == "client_secret_basic":
                credentials["auth"] = (reg["client_id"], reg["client_secret"])
                request("POST", "/oauth/token", 401, data=form, auth=(reg["client_id"], "wrong"))
            else:
                form["client_id"] = reg["client_id"]
                if auth_method == "client_secret_post":
                    form["client_secret"] = reg["client_secret"]
            tokens = request("POST", "/oauth/token", data=form, **credentials).json()
            require(tokens.get("access_token") and tokens.get("refresh_token"), "OAuth exchange did not return tokens")
            refresh = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "resource": resource}
            if auth_method != "client_secret_basic":
                refresh["client_id"] = reg["client_id"]
                if auth_method == "client_secret_post":
                    refresh["client_secret"] = reg["client_secret"]
            tokens = request("POST", "/oauth/token", data=refresh, **credentials).json()
            mcp_response = request("POST", "/mcp", headers={"Authorization": "Bearer " + tokens["access_token"],
                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            if "text/event-stream" in mcp_response.headers.get("content-type", ""):
                payload = json.loads(next(line[5:].strip() for line in mcp_response.text.splitlines() if line.startswith("data:")))
            else:
                payload = mcp_response.json()
            require("error" not in payload and isinstance(payload.get("result", {}).get("tools"), list)
                    and payload["result"]["tools"], "Authenticated MCP tool result is missing")
            check("HTTP DCR, PKCE exchange and MCP: " + auth_method)
        rejected = request("POST", "/oauth/register", 400, json={"redirect_uris": [redirect],
            "grant_types": ["client_credentials"]}).json()
        require(rejected.get("error") == "invalid_client_metadata", "Unsupported grant contract differs")
        check("unsupported-only grants rejected")
        enrollment = run([cli, "auth", "enroll"])
        match = re.search(r"#code=([^\s]+)", enrollment)
        require(match, "Enrollment command did not return a link")
        code = match.group(1)
        password = secrets.token_urlsafe(24)
        request("GET", "/admin/enroll")
        request("POST", "/admin/enroll", 303, data={"code": code, "password": password, "password2": password})
        client.cookies.clear()
        # Recovery resets the password and preserves the generated username.
        with sqlite3.connect((cfg / "auth.db").as_uri() + "?mode=ro", uri=True) as db:
            username = db.execute("SELECT username FROM admin_credential WHERE id=1").fetchone()[0]
        request("POST", "/admin/login", 303, data={"username": username or "", "credential": password})
        client.cookies.clear()
        request("POST", "/admin/enroll", 400, data={"code": code, "password": password, "password2": password})
        check("HTTP password enrollment, login and one-time code replay rejection")
    # Compare secret bytes in memory, never include hashes or values in the handoff.
    preserved = {name: (cfg / name).read_bytes() for name in ("token", "oauth-signing.key")}
    stop_owned()
    run([sys.executable, str(source / "runtime/cli.py"), "install", *flags, "--source", str(source)])
    require(all((cfg / name).read_bytes() == value for name, value in preserved.items()), "Repeat install changed credentials")
    check("direct CLI repeat install preserves machine token and signing key")
    run([cli, "doctor", "--deep"])
    run([cli, "report", "--json"])
    check("doctor --deep and report")
    run([cli, "upgrade", "--source", str(source), "--yes"])
    require(list((cfg / "backups").glob("pre-upgrade-*.tar.gz")), "Updater backup is missing")
    check("same-source actual updater and backup")
    run([cli, "rollback", "--yes"])
    require(all((cfg / name).read_bytes() == value for name, value in preserved.items()), "Rollback changed credentials")
    check("runtime rollback preserves credentials")
    stop_owned()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 3147))
    check("owned gateway shutdown frees port")
    result["ok"] = True
except Exception as error:
    # Exception text can include credentials from libraries. Record only its class.
    result["failure_type"] = type(error).__name__
    print("FAIL (details suppressed to prevent credential disclosure)", file=sys.stderr)
finally:
    signal.alarm(0)
    try:
        stop_owned()
        shutil.rmtree(work)
        result["cleanup"] = "owned process stopped and run directory removed"
    except Exception:
        result["ok"] = False
        result["cleanup"] = "incomplete; owner must inspect private run directory"
        result["private_run_directory"] = str(work)
    Path(result_name).write_text(json.dumps(result, indent=2) + "\n")
    print("Redacted result: " + result_name)
sys.exit(0 if result["ok"] else 1)
PY
