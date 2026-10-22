# Benchmarks

Numbers below were produced by `scripts/benchmark.py`, which installs a
throwaway Loopback into a temporary config directory, starts the gateway,
and measures it over HTTP exactly as a client would. Re-run it on your own
hardware; the script prints this table plus JSON.

```bash
python scripts/benchmark.py --python ~/.config/loopback/venv/bin/python --idle-seconds 20
```

**Measured on:** Debian 12 container, Linux 6.1, x86_64, Python 3.11.6,
Loopback 4.0.0, Landlock ABI 2 active, no public ingress (direct
`127.0.0.1` origin, no TLS), no remote nodes registered.

| Metric | Value | Notes |
|---|---|---|
| Idle RSS (resident memory) | 76 MB | Gateway up, maintenance loop running, no jobs/terminals/connections. 96 MB before document libraries were made lazy-loaded; they add ~20 MB only after the first DOCX/XLSX/PDF tool call. |
| Idle CPU | 0.2 % | Averaged over 20 s with the 30-second maintenance tick running and probing disabled. |
| Dashboard static payload | 135 KB | `index.html` + `app.css` + `app.js` + `topology.js`, uncompressed, no external assets. |
| Dashboard first load (server side) | 16 ms | Shell HTML + three static files + `/api/admin/overview`, measured from a local client. Browser render time is additional. |
| Topology refresh | 64 ms | `POST /api/admin/topology/refresh` with no SSH targets or nodes: local health probe + state persistence. Each SSH target adds one `ssh` round-trip (ConnectTimeout 5 s, four in parallel). |
| SQLite growth per 1,000 audit rows | 108 KB | `loopback.db` after a WAL checkpoint. Audit retention defaults to 90 days / 200,000 rows. |
| Tool latency, `machine_info` (p50) | 11 ms | Full HTTP + JSON-RPC + auth + policy + audit path. |
| Tool latency, `execute "true"` (p50) | 25 ms | Of which 4.5 ms is the bare `sh -lc true` spawn; the remaining ~20 ms is Landlock rule construction (child-by-child enumeration of the home directory), the login shell, and audit. |
| RSS after the benchmark load | 81 MB | After ~60 tool calls and a 1,000-row audit insert. |
| Multi-node probe overhead | not measured here | No reachable remote hosts in the benchmark environment; probes run with a concurrency of 4 and a 5 s connect timeout, so a sweep of N unreachable hosts takes at most ~5·⌈N/4⌉ s. |

## Method notes

- Memory and CPU come from `/proc/<pid>/status` (VmRSS) and `/proc/<pid>/stat`
  (utime+stime) of the uvicorn process.
- Latency figures are medians of 20 sequential requests from the same host.
- The sandbox backend was Landlock for all latency rows. With the `none`
  backend the `execute` overhead drops to roughly the spawn cost plus
  audit, but file restrictions are then not OS-enforced.
- No ingress (Tailscale/Cloudflare) was in the path. Expect TLS and tunnel
  round-trips to dominate end-to-end latency for remote clients.

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — what each of these numbers is
  actually measuring.
- [PERMISSIONS.md](PERMISSIONS.md) — sandbox backends referenced above.
