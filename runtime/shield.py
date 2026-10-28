"""Shield: best-effort, unobtrusive endpoint protection for the Loopback gateway.

``Shield`` is an ASGI middleware placed in front of the whole app. It mitigates
(it does not eliminate) request floods, credential brute force, vulnerability
scanners, exploit probes and abuse spread across rotating addresses within one
network prefix. Every decision is recorded in a private SQLite file
(``cfg_dir/shield.db``) with timestamp, attack kind, client IP and — only when a
trusted local proxy supplied it — the visitor country.

Design rules:

* Legitimate bursts survive: ``/mcp`` gets a generous budget (tool bursts, one
  token per SSE stream open), discovery/static/health GETs are never limited.
* Proxy headers (``CF-Connecting-IP``/``X-Forwarded-For``/``CF-IPCountry``) are
  trusted only when the TCP peer is in ``trusted_proxies`` — the same rule as
  ``auth_http.client_ip``. Country is never inferred: no header, no country.
* Trusted proxy addresses themselves are never throttled or blocked.
* No outbound network calls; all in-memory state is bounded by ``max_tracked_ips``.
* A bug in Shield must never take the gateway down: failures fall through to the
  wrapped app and are written to the audit log.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import sqlite3
import threading
import time
import urllib.parse
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

try:  # reuse the audit store's private-file helper when available
    from audit import _create_private_file
except ImportError:  # pragma: no cover - standalone use
    import os

    def _create_private_file(path: Path) -> None:
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        os.chmod(path, 0o600)

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "trusted_proxies": ["127.0.0.1", "::1"],
    "block_minutes": 15,
    "retention_days": 30,
    "max_tracked_ips": 5000,
    "mcp_per_minute": 600, "mcp_burst": 120,
    "auth_per_minute": 30, "auth_burst": 15,
    "default_per_minute": 240, "default_burst": 60,
    "rotating_per_minute": 20, "rotating_burst": 10,
    "flood_block_hits": 5,          # throttles within 60 s before a block
    "scanner_block_hits": 3,        # scanner/exploit hits within 10 min before a block
    "brute_force_failures": 10,     # 401/403 on auth paths within 10 min before a block
    "rotating_prefix_events": 25,   # failures + scanner hits per /24 or /48 within 10 min
}

FLOOD_WINDOW = 60.0
ABUSE_WINDOW = 600.0
BLOCK_LOG_INTERVAL = 60.0
PRUNE_EVERY = 500

SCANNER_RE = re.compile(
    r"^/(?:wp-login\.php|wp-admin|wp-content|wp-includes|xmlrpc\.php|phpmyadmin|pma/|myadmin|\.env|\.git(?:/|$)|\.svn|\.hg"
    r"|\.aws/|\.ssh/|\.ds_store|cgi-bin/|vendor/phpunit|actuator|etc/passwd|boaform|hnap1|solr/admin|server-status|"
    r"owa/|autodiscover/|\.vscode/|config\.json$|telescope|_ignition)|\.(?:php|asp|aspx|jsp|cgi)$"
)
EXPLOIT_RAW_RE = re.compile(r"%2e%2e|\.\.%2f|%2e\./|%00|%252e|\x00", re.IGNORECASE)
EXPLOIT_TEXT_RE = re.compile(
    r"\.\./|\.\.\\|<script|javascript:|union(?:\s|\+|/\*.*?\*/)+select|\bor\s+1\s*=\s*1\b|'\s*or\s*'1'\s*=\s*'1|information_schema"
    r"|sleep\s*\(\s*\d|benchmark\s*\(|\$\{jndi:|/etc/passwd|;\s*(?:cat|wget|curl)\s",
    re.IGNORECASE,
)
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
EXEMPT_PREFIXES = ("/.well-known/", "/admin/static/")
AUTH_PATHS = ("/oauth/", "/admin/login", "/admin/enroll")
LOGIN_POSTS = {"/oauth/token", "/oauth/authorize", "/admin/login", "/admin/enroll"}


def shield_defaults() -> dict[str, Any]:
    """Return a fresh copy of the default ``shield`` config section."""
    return json.loads(json.dumps(DEFAULTS))


def network_prefix(ip: str) -> str | None:
    """Aggregate an address into its /24 (IPv4) or /48 (IPv6) network, or None."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


class ShieldStore:
    """Private SQLite log of shield events (``cfg_dir/shield.db``, mode 0600, WAL)."""

    def __init__(self, config_dir: Path, retention_days: int = 30):
        self.path = Path(config_dir) / "shield.db"
        self.retention_days = retention_days
        self.blocklist_provider = lambda: []
        self._lock = threading.RLock()
        self._inserts = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _create_private_file(self.path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS shield_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,"
                " ip TEXT NOT NULL, country TEXT, path TEXT, method TEXT, action TEXT NOT NULL, detail TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_shield_ts ON shield_events(ts DESC)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
        except sqlite3.DatabaseError:
            pass
        return db

    def record(self, kind: str, ip: str, *, country: str | None = None, path: str = "", method: str = "",
               action: str = "observed", detail: dict[str, Any] | None = None) -> None:
        payload = json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False)[:2000]
        with self._lock:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO shield_events(ts,kind,ip,country,path,method,action,detail) VALUES(?,?,?,?,?,?,?,?)",
                    (time.time(), kind, ip[:64], country, path[:300], method[:16], action, payload),
                )
            self._inserts += 1
            if self._inserts % PRUNE_EVERY == 0:
                self.prune(self.retention_days)

    def events(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql, args = "SELECT * FROM shield_events", []
        if kind:
            sql, args = sql + " WHERE kind=?", [kind]
        with self._lock, self._connect() as db:
            rows = db.execute(sql + " ORDER BY ts DESC, id DESC LIMIT ?", [*args, limit]).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"] or "{}")
            except ValueError:
                item["detail"] = {}
            out.append(item)
        return out

    def prune(self, retention_days: int) -> int:
        cutoff = time.time() - max(1, int(retention_days)) * 86400
        with self._lock, self._connect() as db:
            return db.execute("DELETE FROM shield_events WHERE ts < ?", (cutoff,)).rowcount

    def summary(self, window_seconds: int = 86400) -> dict[str, Any]:
        since = time.time() - window_seconds
        with self._lock, self._connect() as db:
            kinds = dict(db.execute("SELECT kind, COUNT(*) FROM shield_events WHERE ts>=? GROUP BY kind", (since,)).fetchall())
            actions = dict(db.execute("SELECT action, COUNT(*) FROM shield_events WHERE ts>=? GROUP BY action", (since,)).fetchall())
            top = db.execute("SELECT ip, COUNT(*) AS n, MAX(country) AS country FROM shield_events WHERE ts>=? GROUP BY ip"
                             " ORDER BY n DESC LIMIT 10", (since,)).fetchall()
            last = db.execute("SELECT MAX(ts) FROM shield_events").fetchone()[0]
        return {"window_seconds": window_seconds, "by_kind": kinds, "by_action": actions,
                "top_ips": [dict(r) for r in top], "last_event_at": last, "blocked_now": self.blocklist_provider()}


class _Windows:
    """Bounded map of key -> deque of timestamps with lazy window expiry and LRU eviction."""

    def __init__(self, window: float):
        self.window = window
        self.data: OrderedDict[str, deque[float]] = OrderedDict()

    def add(self, key: str, now: float, limit: int) -> int:
        q = self.data.pop(key, None) or deque()
        while q and q[0] < now - self.window:
            q.popleft()
        q.append(now)
        self.data[key] = q
        while len(self.data) > limit:
            self.data.popitem(last=False)
        return len(q)

    def clear(self, key: str) -> None:
        self.data.pop(key, None)


class Shield:
    """ASGI middleware: ``app = Shield(AdminGate(Starlette(...)), CTX)``. Sets ``ctx.shield = self``."""

    def __init__(self, wrapped, ctx):
        self.wrapped = wrapped
        self.ctx = ctx
        cfg = self.cfg()
        self.store = ShieldStore(Path(ctx.cfg_dir), int(cfg["retention_days"]))
        self.store.blocklist_provider = self.blocked_now
        self.buckets: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self.blocked: dict[str, float] = {}
        self.block_reasons: dict[str, tuple[str, str]] = {}
        self.rotating: dict[str, float] = {}
        self.floods, self.scans = _Windows(FLOOD_WINDOW), _Windows(ABUSE_WINDOW)
        self.failures, self.prefixes = _Windows(ABUSE_WINDOW), _Windows(ABUSE_WINDOW)
        self.last_block_log: OrderedDict[str, float] = OrderedDict()
        ctx.shield = self

    # ------------------------------------------------------------------ config / identity
    def cfg(self) -> dict[str, Any]:
        config = getattr(self.ctx, "config", None)
        data = getattr(config, "data", None) or {}
        section = data.get("shield") if isinstance(data, dict) else None
        return {**DEFAULTS, **(section if isinstance(section, dict) else {})}

    @staticmethod
    def identify(scope, trusted: set[str]) -> tuple[str, str | None, bool]:
        """Return (client_ip, country, peer_is_trusted) using proxy headers only from trusted peers."""
        peer = (scope.get("client") or ("unknown", 0))[0] or "unknown"
        if peer not in trusted:
            return peer[:64], None, False
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        ip = peer
        for candidate in (headers.get("cf-connecting-ip", ""), headers.get("x-forwarded-for", "").split(",")[0]):
            candidate = candidate.strip()
            try:
                ip = str(ipaddress.ip_address(candidate))
                break
            except ValueError:
                continue
        country = headers.get("cf-ipcountry", "").strip().upper()
        return ip[:64], (country if COUNTRY_RE.match(country) and country != "XX" else None), True

    # ------------------------------------------------------------------ state helpers
    def blocked_now(self) -> list[dict[str, Any]]:
        now = time.time()
        for ip in [ip for ip, until in self.blocked.items() if until <= now]:
            del self.blocked[ip]
            self.block_reasons.pop(ip, None)
        return [{"ip": ip, "until": until, "kind": self.block_reasons.get(ip, ("", ""))[0], "reason": self.block_reasons.get(ip, ("", ""))[1]}
                for ip, until in sorted(self.blocked.items())]

    def unblock(self, ip: str) -> bool:
        found = self.blocked.pop(ip, None) is not None
        self.block_reasons.pop(ip, None)
        for windows in (self.floods, self.scans, self.failures):
            windows.clear(ip)
        return found

    def _block(self, ip: str, kind: str, cfg: dict[str, Any], meta: dict[str, Any], reason: str) -> None:
        if ip in set(cfg["trusted_proxies"]):
            return
        self.blocked[ip] = time.time() + float(cfg["block_minutes"]) * 60
        self.block_reasons[ip] = (kind, reason)
        while len(self.blocked) > int(cfg["max_tracked_ips"]):
            oldest = min(self.blocked, key=self.blocked.get)
            del self.blocked[oldest]
            self.block_reasons.pop(oldest, None)
        self.store.record(kind, ip, action="blocked", detail={"reason": reason, "minutes": cfg["block_minutes"]}, **meta)

    def _take(self, key: tuple[str, str], per_minute: float, burst: float, now: float, limit: int) -> float:
        """Consume one token; return 0 on success or the seconds until a token is available."""
        rate = max(per_minute, 0.001) / 60.0
        tokens, last = self.buckets.pop(key, None) or [float(burst), now]
        tokens = min(float(burst), tokens + (now - last) * rate)
        wait = 0.0
        if tokens >= 1:
            tokens -= 1
        else:
            wait = (1 - tokens) / rate
        self.buckets[key] = [tokens, now]
        while len(self.buckets) > limit * 2:
            self.buckets.popitem(last=False)
        return wait

    def _prefix_event(self, ip: str, cfg: dict[str, Any], meta: dict[str, Any], now: float) -> None:
        prefix = network_prefix(ip)
        if not prefix:
            return
        count = self.prefixes.add(prefix, now, int(cfg["max_tracked_ips"]))
        if count >= int(cfg["rotating_prefix_events"]) and self.rotating.get(prefix, 0) <= now:
            self.rotating[prefix] = now + float(cfg["block_minutes"]) * 60
            self.store.record("rotating_ip", ip, action="limited", detail={"prefix": prefix, "events": count}, **meta)
        for stale in [p for p, until in self.rotating.items() if until <= now]:
            del self.rotating[stale]

    # ------------------------------------------------------------------ request decision
    def _decide(self, scope) -> tuple[Response | None, dict[str, Any] | None]:
        cfg = self.cfg()
        if not cfg.get("enabled", True):
            return None, None
        trusted = set(cfg["trusted_proxies"])
        ip, country, _ = self.identify(scope, trusted)
        path, method = scope.get("path", "") or "/", (scope.get("method") or "GET").upper()
        if ip in trusted:  # a local caller or a proxy that sent no client address: never limit it
            return None, None
        if method in {"GET", "HEAD", "OPTIONS"} and (path == "/healthz" or path.startswith(EXEMPT_PREFIXES)):
            return None, None
        now, limit = time.time(), int(cfg["max_tracked_ips"])
        meta = {"country": country, "path": path, "method": method}
        if self.blocked.get(ip, 0) > now:
            if now - self.last_block_log.get(ip, 0) >= BLOCK_LOG_INTERVAL:
                self.last_block_log[ip] = now
                self.last_block_log.move_to_end(ip)
                while len(self.last_block_log) > limit:
                    self.last_block_log.popitem(last=False)
                self.store.record("blocked", ip, action="denied", **meta)
            return JSONResponse({"error": "blocked"}, status_code=403, headers=NO_STORE), None
        kind = self._signature(scope, path)
        if kind:
            self.store.record(kind, ip, action="rejected", **meta)
            self._prefix_event(ip, cfg, meta, now)
            if self.scans.add(ip, now, limit) >= int(cfg["scanner_block_hits"]):
                self._block(ip, kind, cfg, meta, "repeated probes")
            return PlainTextResponse("Not Found", status_code=404), None
        cls = "mcp" if path in {"/mcp", "/mcp/"} or path.startswith("/mcp/") else "auth" if path.startswith(AUTH_PATHS) else "default"
        wait = self._take((ip, cls), float(cfg[f"{cls}_per_minute"]), float(cfg[f"{cls}_burst"]), now, limit)
        prefix = network_prefix(ip)
        if not wait and prefix and self.rotating.get(prefix, 0) > now:
            wait = self._take((prefix, "rotating"), float(cfg["rotating_per_minute"]), float(cfg["rotating_burst"]), now, limit)
        if wait:
            self.store.record("flood", ip, action="throttled", detail={"class": cls}, **meta)
            if self.floods.add(ip, now, limit) >= int(cfg["flood_block_hits"]):
                self._block(ip, "flood", cfg, meta, "repeated rate limit hits")
            headers = {**NO_STORE, "Retry-After": str(max(1, math.ceil(wait)))}
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers=headers), None
        watch = cls == "mcp" or (method == "POST" and path in LOGIN_POSTS)
        return None, ({"ip": ip, "cfg": cfg, "meta": meta, "login": path in LOGIN_POSTS} if watch else None)

    @staticmethod
    def _signature(scope, path: str) -> str | None:
        raw = (scope.get("raw_path") or path.encode("latin1", "replace")).decode("latin1", "replace")
        query = scope.get("query_string", b"").decode("latin1", "replace")
        if SCANNER_RE.search(path.lower()):
            return "scanner"
        if EXPLOIT_RAW_RE.search(raw) or EXPLOIT_RAW_RE.search(query) or "\x00" in path:
            return "exploit"
        if EXPLOIT_TEXT_RE.search(path) or EXPLOIT_TEXT_RE.search(urllib.parse.unquote_plus(query)):
            return "exploit"
        return None

    def _observe(self, watch: dict[str, Any], status: int) -> None:
        ip, cfg, meta, now = watch["ip"], watch["cfg"], watch["meta"], time.time()
        if status in (401, 403):
            self._prefix_event(ip, cfg, meta, now)
            if self.failures.add(ip, now, int(cfg["max_tracked_ips"])) >= int(cfg["brute_force_failures"]):
                self.failures.clear(ip)
                self._block(ip, "brute_force", cfg, meta, "repeated authentication failures")
        elif watch["login"] and 200 <= status < 400:
            self.failures.clear(ip)

    def _report(self, exc: Exception) -> None:
        audit = getattr(self.ctx, "audit", None)
        if audit is not None:
            try:
                audit.record("shield", "error", detail={"error": f"{type(exc).__name__}: {exc}"[:300]})
            except Exception:
                pass

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.wrapped(scope, receive, send)
        try:
            response, watch = self._decide(scope)
        except Exception as exc:  # never let shield take the gateway down
            self._report(exc)
            response, watch = None, None
        if response is not None:
            return await response(scope, receive, send)
        if watch is None:
            return await self.wrapped(scope, receive, send)

        async def observing_send(message):
            if message.get("type") == "http.response.start":
                try:
                    self._observe(watch, int(message.get("status", 0)))
                except Exception as exc:
                    self._report(exc)
            await send(message)

        return await self.wrapped(scope, receive, observing_send)

    # ------------------------------------------------------------------ analytics
    def summary(self) -> dict[str, Any]:
        s = self.store.summary(86400)
        now = time.time()
        return {
            "enabled": bool(self.cfg().get("enabled", True)),
            "blocked_now": s["blocked_now"],
            "rate_limited_prefixes": sorted(p for p, until in self.rotating.items() if until > now),
            "events_24h": s["by_kind"],
            "throttled_24h": s["by_action"].get("throttled", 0),
            "top_ips_24h": s["top_ips"],
            "last_event_at": s["last_event_at"],
            "note": "best-effort mitigation; not a guarantee against determined or distributed attacks",
        }

    def topology_marker(self) -> dict[str, Any]:
        """Compact marker for the topology view: state is off, ok or alert (an IP is currently blocked)."""
        s = self.summary()
        state = "off" if not s["enabled"] else "alert" if s["blocked_now"] else "ok"
        return {"id": "shield", "label": "Shield", "state": state, "blocked": len(s["blocked_now"]),
                "events_24h": sum(s["events_24h"].values()), "last_event_at": s["last_event_at"]}

    def public_config(self) -> dict[str, Any]:
        return self.cfg()


def routes(ctx) -> list[Route]:
    """Admin JSON routes (``/api/admin`` is session-gated by AdminGate in server.py)."""

    def _shield():
        return getattr(ctx, "shield", None)

    def _missing() -> JSONResponse:
        return JSONResponse({"detail": "shield not active"}, status_code=404, headers=NO_STORE)

    async def get_shield(request: Request) -> JSONResponse:
        shield = _shield()
        if shield is None:
            return _missing()
        kind = request.query_params.get("kind") or None
        return JSONResponse({"summary": shield.summary(), "marker": shield.topology_marker(),
                             "events": shield.store.events(200, kind), "config": shield.public_config()}, headers=NO_STORE)

    async def post_unblock(request: Request) -> JSONResponse:
        shield = _shield()
        if shield is None:
            return _missing()
        try:
            body = await request.json()
        except Exception:
            body = {}
        ip = str(body.get("ip") or "").strip() if isinstance(body, dict) else ""
        if not ip:
            return JSONResponse({"detail": "ip is required"}, status_code=400, headers=NO_STORE)
        return JSONResponse({"ip": ip, "unblocked": shield.unblock(ip)}, headers=NO_STORE)

    async def post_prune(request: Request) -> JSONResponse:
        shield = _shield()
        if shield is None:
            return _missing()
        days = int(shield.cfg()["retention_days"])
        return JSONResponse({"deleted": shield.store.prune(days), "retention_days": days}, headers=NO_STORE)

    return [
        Route("/api/admin/shield", get_shield, methods=["GET"]),
        Route("/api/admin/shield/unblock", post_unblock, methods=["POST"]),
        Route("/api/admin/shield/prune", post_prune, methods=["POST"]),
    ]
