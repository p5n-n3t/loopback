#!/usr/bin/env python3
"""Measure Loopback's resource footprint against a throwaway install.

Usage: python scripts/benchmark.py [--python /path/to/venv/python] [--port 2061]
Prints a Markdown table (the one in docs/BENCHMARKS.md) plus JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"


def proc_stat(pid: int) -> tuple[int, float]:
    rss = 0
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) * 1024
    fields = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()
    cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    return rss, cpu


def request(method: str, url: str, body: dict | None = None, headers: dict | None = None, cookie: str | None = None) -> tuple[int, bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--port", type=int, default=2061)
    ap.add_argument("--idle-seconds", type=int, default=20)
    args = ap.parse_args()

    cfg = tempfile.mkdtemp(prefix="loopback-bench-")
    env = {**os.environ, "LOOPBACK_CONFIG_DIR": cfg, "PYTHONPATH": str(RUNTIME)}
    # Pre-create config with the benchmark port and a token.
    subprocess.run([args.python, "-c", f"import sys; sys.path.insert(0, r'{RUNTIME}'); from bootstrap import build_context; c=build_context(start_managers=False); c.config.save({{'port': {args.port}}})"], env=env, check=True)
    token = subprocess.run([args.python, "-c", f"import sys; sys.path.insert(0, r'{RUNTIME}'); from bootstrap import build_context; c=build_context(start_managers=False); v,_=c.auth.create_api_token('bench',['loopback:read','loopback:exec'],ttl_seconds=3600,created_by='bench'); print(v)"],
                           env=env, check=True, capture_output=True, text=True).stdout.strip()
    machine_token = Path(cfg, "token").read_text().strip()
    server = subprocess.Popen([args.python, "-m", "uvicorn", "server:app", "--app-dir", str(RUNTIME), "--host", "127.0.0.1", "--port", str(args.port), "--log-level", "warning"], env=env)
    base = f"http://127.0.0.1:{args.port}"
    try:
        for _ in range(100):
            try:
                if request("GET", f"{base}/healthz")[0] == 200:
                    break
            except Exception:
                time.sleep(0.2)
        results: dict[str, object] = {}
        time.sleep(3)
        rss0, cpu0 = proc_stat(server.pid)
        time.sleep(args.idle_seconds)
        rss1, cpu1 = proc_stat(server.pid)
        results["idle_rss_mb"] = round(rss1 / 1024 / 1024, 1)
        results["idle_cpu_percent"] = round((cpu1 - cpu0) / args.idle_seconds * 100, 2)

        static = sum(p.stat().st_size for p in (RUNTIME / "static").glob("*") if p.is_file())
        results["dashboard_static_kb"] = round(static / 1024, 1)

        mcp_h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}

        def call(name: str, arguments: dict) -> float:
            t = time.perf_counter()
            status, _, _ = request("POST", f"{base}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, mcp_h)
            assert status == 200, status
            return (time.perf_counter() - t) * 1000

        lat_info = [call("machine_info", {}) for _ in range(20)]
        lat_exec = [call("execute", {"command": "true"}) for _ in range(20)]
        t = time.perf_counter()
        for _ in range(20):
            subprocess.run(["/bin/sh", "-lc", "true"], check=False)  # baseline shell spawn
        raw_ms = (time.perf_counter() - t) * 1000 / 20
        results["tool_latency_machine_info_ms_p50"] = round(statistics.median(lat_info), 1)
        results["tool_latency_execute_true_ms_p50"] = round(statistics.median(lat_exec), 1)
        results["shell_spawn_baseline_ms"] = round(raw_ms, 1)
        results["execute_overhead_ms"] = round(statistics.median(lat_exec) - raw_ms, 1)

        # dashboard login + first load (no redirect following; we only need the cookie)
        import http.client
        import urllib.parse
        conn = http.client.HTTPConnection("127.0.0.1", args.port, timeout=10)
        conn.request("POST", "/admin/login", body=urllib.parse.urlencode({"credential": machine_token}),
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        cookie = (resp.getheader("Set-Cookie") or "").split(";")[0] or None
        conn.close()
        t = time.perf_counter()
        status, body, _ = request("GET", f"{base}/admin", cookie=cookie)
        html_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        for name in ("app.css", "app.js", "topology.js"):
            request("GET", f"{base}/admin/static/{name}", cookie=cookie)
        status, ov, _ = request("GET", f"{base}/api/admin/overview", cookie=cookie)
        first_load_ms = html_ms + (time.perf_counter() - t) * 1000
        results["dashboard_first_load_ms_server_side"] = round(first_load_ms, 1)
        status, sess, _ = request("GET", f"{base}/api/admin/session", cookie=cookie)
        csrf = json.loads(sess)["csrf"]
        t = time.perf_counter()
        request("POST", f"{base}/api/admin/topology/refresh", {}, {"X-Loopback-CSRF": csrf}, cookie=cookie)
        results["topology_refresh_ms_no_remotes"] = round((time.perf_counter() - t) * 1000, 1)

        # sqlite growth
        before = Path(cfg, "loopback.db").stat().st_size
        subprocess.run([args.python, "-c", f"import sys; sys.path.insert(0, r'{RUNTIME}'); from audit import AuditStore; a=AuditStore(r'{cfg}'); [a.record('execute','ok',duration_ms=5,target='execute',principal='bench',principal_kind='api_token',detail={{'user_agent':'bench/1.0'}}) for _ in range(1000)]"], env=env, check=True)
        subprocess.run([args.python, "-c", f"import sqlite3; db=sqlite3.connect(r'{cfg}/loopback.db'); db.execute('PRAGMA wal_checkpoint(TRUNCATE)'); db.close()"], check=True)
        after = Path(cfg, "loopback.db").stat().st_size
        results["sqlite_growth_kb_per_1000_audit_rows"] = round((after - before) / 1024, 1)

        rss2, _ = proc_stat(server.pid)
        results["rss_after_load_mb"] = round(rss2 / 1024 / 1024, 1)
        results["python"] = sys.version.split()[0]
        print("| Metric | Value |\n|---|---|")
        for k, v in results.items():
            print(f"| {k} | {v} |")
        print(json.dumps(results, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
