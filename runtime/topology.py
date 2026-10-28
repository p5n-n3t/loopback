"""Connection topology model for the Loopback dashboard.

``TopologyBuilder.build()`` assembles the graph documented in
``docs/ADMIN-API.md`` ("Topology schema") purely from local state – it never
touches the network. ``TopologyBuilder.refresh()`` is the only prober: it
re-checks SSH targets, Loopback nodes, ingress paths and the local listener
with bounded concurrency and stores timestamped results.

State files (under ``ctx.cfg_dir``, mode 0600, atomic writes):

* ``topology_state.json`` – last probe result per edge id.
* ``topology.json`` – operator display names / groups per node id.
* ``node_meta.json`` – display_name / kind / group for fleet nodes (the
  ``FleetStore`` schema is fixed, so this metadata lives beside it).

Probe history is kept in the ``probe_history`` table of ``loopback.db`` (the
same SQLite file as the audit log), bounded per edge and by retention days.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

STATUSES = ["healthy", "degraded", "unreachable", "auth_failed", "disconnected", "untested", "stale"]
MAX_ROWS_PER_EDGE = 2000
NODE_KINDS = ("remote-physical", "remote-vm", "container", "local-computer", "gateway", "unknown")
MCP_PROTOCOL_VERSION = "2025-06-18"


# --------------------------------------------------------------------------- helpers
def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def safe_url(url: str) -> str:
    """Strip userinfo, query and fragment from a URL so it can be displayed."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _clip(text: Any, limit: int = 300) -> str | None:
    if text in (None, ""):
        return None
    s = str(text).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# --------------------------------------------------------------------------- history
class ProbeHistory:
    """Bounded probe history in the shared ``loopback.db`` SQLite file.

    Table ``probe_history(id, ts, edge_id, status, latency_ms, error)``; at most
    ``MAX_ROWS_PER_EDGE`` rows per edge, older rows removed by :meth:`prune`.
    """

    def __init__(self, db_path: str | Path, retention_days: int = 7):
        self.path = Path(db_path)
        self.retention_days = int(retention_days)
        self._lock = threading.RLock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.execute(
                "CREATE TABLE IF NOT EXISTS probe_history (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                "edge_id TEXT NOT NULL, status TEXT NOT NULL, latency_ms INTEGER, error TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_probe_edge_ts ON probe_history(edge_id, id DESC)")
            db.commit()
            self._ready = True
        return db

    def record(self, edge_id: str, status: str, latency_ms: Any = None, error: Any = None,
               ts: float | None = None) -> None:
        """Append one result and trim the edge to ``MAX_ROWS_PER_EDGE`` rows."""
        lat = int(latency_ms) if isinstance(latency_ms, (int, float)) else None
        with self._lock:
            db = self._connect()
            try:
                with db:
                    db.execute("INSERT INTO probe_history(ts,edge_id,status,latency_ms,error) VALUES(?,?,?,?,?)",
                               (ts or time.time(), str(edge_id)[:200], str(status)[:40], lat, _clip(error, 500)))
                    db.execute(
                        "DELETE FROM probe_history WHERE edge_id=? AND id <= (SELECT id FROM probe_history "
                        "WHERE edge_id=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
                        (edge_id, edge_id, MAX_ROWS_PER_EDGE),
                    )
            finally:
                db.close()

    def prune(self, retention_days: int | None = None) -> int:
        """Delete rows older than *retention_days* (default: constructor value). Returns rows removed."""
        days = self.retention_days if retention_days is None else int(retention_days)
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._lock:
            db = self._connect()
            try:
                with db:
                    cur = db.execute("DELETE FROM probe_history WHERE ts < ?", (cutoff,))
                    return int(cur.rowcount or 0)
            finally:
                db.close()

    def history(self, edge_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first results for one edge."""
        n = max(1, min(int(limit), MAX_ROWS_PER_EDGE))
        with self._lock:
            db = self._connect()
            try:
                rows = db.execute("SELECT ts,status,latency_ms,error FROM probe_history WHERE edge_id=? "
                                  "ORDER BY id DESC LIMIT ?", (edge_id, n)).fetchall()
            finally:
                db.close()
        return [dict(r) for r in rows]

    def delete_edge(self, edge_id: str) -> None:
        with self._lock:
            db = self._connect()
            try:
                with db:
                    db.execute("DELETE FROM probe_history WHERE edge_id=?", (edge_id,))
            finally:
                db.close()


# --------------------------------------------------------------------------- node meta
class NodeMetaStore:
    """``node_meta.json``: ``{name: {display_name, kind, group}}`` for fleet nodes."""

    def __init__(self, cfg_dir: str | Path):
        self.path = Path(cfg_dir) / "node_meta.json"
        self._lock = threading.RLock()

    def all(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def get(self, name: str) -> dict[str, Any]:
        value = self.all().get(name)
        return value if isinstance(value, dict) else {}

    def set(self, name: str, *, display_name: str | None = None, kind: str | None = None,
            group: str | None = None) -> dict[str, Any]:
        if kind is not None and kind not in NODE_KINDS:
            raise ValueError(f"kind must be one of {', '.join(NODE_KINDS)}")
        entry = {
            "display_name": (str(display_name).strip()[:80] or None) if display_name is not None else None,
            "kind": kind or "unknown",
            "group": (str(group).strip()[:40] or None) if group is not None else None,
        }
        with self._lock:
            data = self.all()
            data[name] = entry
            _atomic_write_json(self.path, data)
        return entry

    def remove(self, name: str) -> None:
        with self._lock:
            data = self.all()
            if name in data:
                data.pop(name)
                _atomic_write_json(self.path, data)


# --------------------------------------------------------------------------- builder
class TopologyBuilder:
    """Builds and refreshes the gateway's connection topology."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._lock = threading.RLock()
        self._virt: str | None = None
        retention = int(self._maintenance().get("history_retention_days", 7) or 7)
        self.history = ProbeHistory(Path(ctx.cfg_dir) / "loopback.db", retention)
        self.node_meta = NodeMetaStore(ctx.cfg_dir)
        self._last_prune = 0.0
        self.last_refresh_ms: int | None = None

    # ---------------------------------------------------------------- paths / config
    @property
    def state_path(self) -> Path:
        return Path(self.ctx.cfg_dir) / "topology_state.json"

    @property
    def meta_path(self) -> Path:
        return Path(self.ctx.cfg_dir) / "topology.json"

    def _maintenance(self) -> dict[str, Any]:
        return (getattr(self.ctx.config, "data", {}) or {}).get("maintenance", {}) or {}

    @property
    def stale_after_seconds(self) -> int:
        interval = int(self._maintenance().get("probe_interval_seconds", 60) or 0)
        return 2 * (interval if interval > 0 else 60)

    def _machine_kind(self) -> str:
        if self._virt is None:
            try:
                from identity import detect_virtualization

                self._virt = detect_virtualization()
            except Exception:  # noqa: BLE001
                self._virt = "unknown"
        return self._virt

    # ---------------------------------------------------------------- persisted state
    def state(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self.state_path, {})
        return data if isinstance(data, dict) else {}

    def store_results(self, results: dict[str, dict[str, Any]]) -> None:
        """Merge ``{edge_id: result}`` into topology_state.json and probe history."""
        if not results:
            return
        with self._lock:
            data = self.state()
            data.update(results)
            # Bound the file: drop edges that no longer exist in the graph (keep current results).
            known = {e["id"] for e in self._edge_skeletons()} | set(results)
            data = {k: v for k, v in data.items() if k in known}
            _atomic_write_json(self.state_path, data)
        for edge_id, res in results.items():
            try:
                self.history.record(edge_id, res.get("status", "error"), res.get("latency_ms"), res.get("error"),
                                    ts=res.get("checked_at"))
            except Exception:  # noqa: BLE001
                pass

    def forget(self, edge_id: str) -> None:
        with self._lock:
            data = self.state()
            if edge_id in data:
                data.pop(edge_id)
                _atomic_write_json(self.state_path, data)

    def overrides(self) -> dict[str, dict[str, Any]]:
        data = _read_json(self.meta_path, {})
        nodes = data.get("nodes") if isinstance(data, dict) else None
        return nodes if isinstance(nodes, dict) else {}

    def rename(self, node_id: str, display_name: str | None = None, group: str | None = None) -> dict[str, Any]:
        """Persist an operator label for *node_id* in topology.json; returns the updated node.

        Raises KeyError for unknown node ids and ValueError for invalid values.
        """
        if not any(n["id"] == node_id for n in self.build()["nodes"]):
            raise KeyError(f"topology node not found: {node_id}")
        if display_name is not None and not isinstance(display_name, str):
            raise ValueError("display_name must be a string or null")
        if group is not None and not isinstance(group, str):
            raise ValueError("group must be a string or null")
        dn = (display_name or "").strip() or None
        gr = (group or "").strip() or None
        if dn and len(dn) > 80:
            raise ValueError("display_name must be at most 80 characters")
        if gr and (len(gr) > 40 or not re.match(r"^[\w .:-]+$", gr)):
            raise ValueError("group must be at most 40 characters of letters, digits, space, '.', ':', '_' or '-'")
        with self._lock:
            raw = _read_json(self.meta_path, {})
            if not isinstance(raw, dict):
                raw = {}
            nodes = raw.get("nodes") if isinstance(raw.get("nodes"), dict) else {}
            nodes[node_id] = {"display_name": dn, "group": gr, "updated_at": time.time()}
            raw["nodes"] = nodes
            _atomic_write_json(self.meta_path, raw)
        return next(n for n in self.build()["nodes"] if n["id"] == node_id)

    # ---------------------------------------------------------------- inputs
    def _ingress_cfg(self) -> dict[str, Any]:
        return (getattr(self.ctx.config, "data", {}) or {}).get("ingress", {}) or {}

    def _ssh_targets(self) -> list[dict[str, Any]]:
        ssh = getattr(self.ctx, "ssh", None)
        if ssh is None:
            return []
        try:
            return ssh.list()
        except Exception:  # noqa: BLE001
            return []

    def _shield_marker(self) -> dict[str, Any] | None:
        """Endpoint-protection marker for the gateway node (None when Shield is not mounted)."""
        shield = getattr(self.ctx, "shield", None)
        if shield is None:
            return None
        try:
            return shield.topology_marker()
        except Exception:  # noqa: BLE001
            return None

    def _fleet_nodes(self) -> list[dict[str, Any]]:
        fleet = getattr(self.ctx, "fleet", None)
        if fleet is None:
            return []
        try:
            return [n for n in fleet.list() if isinstance(n, dict) and n.get("name")]
        except Exception:  # noqa: BLE001
            return []

    def _jobs_by_target(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        jobs = getattr(self.ctx, "jobs", None)
        if jobs is None:
            return counts
        try:
            rows = jobs.list()
        except Exception:  # noqa: BLE001
            return counts
        for job in rows or []:
            if isinstance(job, dict) and job.get("running"):
                target = str(job.get("target") or "local")
                counts[target] = counts.get(target, 0) + 1
        return counts

    def _recent_principals(self) -> list[dict[str, Any]]:
        audit = getattr(self.ctx, "audit", None)
        if audit is None:
            return []
        try:
            rows = audit.recent(200)
        except Exception:  # noqa: BLE001
            return []
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            detail = row.get("detail") if isinstance(row, dict) else None
            if not isinstance(detail, dict) or not detail.get("principal"):
                continue
            p = detail["principal"]
            if isinstance(p, dict):
                display, kind = str(p.get("display") or p.get("subject") or "unknown"), str(p.get("kind") or "unknown")
            else:
                display, kind = str(p), str(detail.get("principal_kind") or "unknown")
            key = (display[:80], kind[:20])
            ts = float(row.get("ts") or 0)
            if key not in seen or seen[key]["last_seen"] < ts:
                seen[key] = {"display": key[0], "kind": key[1], "last_seen": ts}
        return sorted(seen.values(), key=lambda p: -p["last_seen"])[:10]

    def _status(self, result: dict[str, Any] | None, *, disabled: bool = False) -> dict[str, Any]:
        if disabled:
            return {"status": "disconnected", "latency_ms": None, "checked_at": (result or {}).get("checked_at"),
                    "last_status": (result or {}).get("status"), "error": None}
        if not result or not result.get("checked_at"):
            return {"status": "untested", "latency_ms": None, "checked_at": None, "last_status": None, "error": None}
        checked = float(result.get("checked_at") or 0)
        status = str(result.get("status") or "untested")
        if status not in STATUSES:
            status = "degraded" if status == "error" else "untested"
        if time.time() - checked > self.stale_after_seconds:
            return {"status": "stale", "latency_ms": result.get("latency_ms"), "checked_at": checked,
                    "last_status": status, "error": _clip(result.get("error"))}
        return {"status": status, "latency_ms": result.get("latency_ms"), "checked_at": checked,
                "last_status": status, "error": _clip(result.get("error"))}

    @staticmethod
    def _via_exposure(via: str) -> str:
        return {"tailscale": "private", "lan": "private", "public": "public"}.get(via, "unknown")

    def _edge_skeletons(self) -> list[dict[str, Any]]:
        """Edge ids that exist in the current configuration (used to bound state files)."""
        ing = self._ingress_cfg()
        edges = [{"id": "clients->gateway"}]
        if (ing.get("cloudflare") or {}).get("enabled"):
            edges += [{"id": "clients->ingress:cloudflare"}, {"id": "ingress:cloudflare->gateway"}]
        if (ing.get("ngrok") or {}).get("enabled"):
            edges += [{"id": "clients->ingress:ngrok"}, {"id": "ingress:ngrok->gateway"}]
        if (ing.get("tailscale") or {}).get("enabled"):
            edges += [{"id": "clients->ingress:tailscale"}, {"id": "ingress:tailscale->gateway"}]
        edges += [{"id": f"gateway->ssh:{t['name']}"} for t in self._ssh_targets()]
        edges += [{"id": f"gateway->node:{n['name']}"} for n in self._fleet_nodes()]
        return edges

    # ---------------------------------------------------------------- build
    def build(self, probe: bool = False) -> dict[str, Any]:  # noqa: ARG002 - probing lives in refresh()
        """Return the topology graph from stored state. Never performs network I/O.

        *probe* is accepted for API compatibility; use :meth:`refresh` to probe.
        """
        from ssh_targets import infer_via  # local import keeps modules independent at import time

        ctx = self.ctx
        cfg = getattr(ctx.config, "data", {}) or {}
        ident = ctx.identity
        overrides = self.overrides()
        state = self.state()
        jobs = self._jobs_by_target()
        ing = self._ingress_cfg()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        def label_for(node_id: str, display: Any, group: Any) -> tuple[Any, Any]:
            ov = overrides.get(node_id) or {}
            return (ov.get("display_name") if "display_name" in ov else display,
                    ov.get("group") if "group" in ov else group)

        # Gateway --------------------------------------------------------
        machine_kind = self._machine_kind()
        kind = cfg.get("node_kind") or "local-computer"
        if kind == "local-computer" and machine_kind == "container":
            kind = "container"
        dn, grp = label_for("gateway", cfg.get("display_name"), "local")
        terminals = 0
        try:
            terminals = len([t for t in (ctx.terminals.list() if ctx.terminals else []) if t.get("alive")])
        except Exception:  # noqa: BLE001
            pass
        nodes.append({
            "id": "gateway", "type": "gateway", "kind": kind, "label": ident.hostname,
            "display_name": dn, "group": grp or "local",
            "detail": {"hostname": ident.hostname, "os": ident.os_release, "user": ident.username,
                       "platform": ident.platform, "arch": ident.arch, "machine_kind": machine_kind,
                       "port": int(cfg.get("port") or 2026), "bind": cfg.get("bind"), "version": ctx.version,
                       "jobs_running": jobs.get("local", 0), "terminals_open": terminals,
                       "shield": self._shield_marker()},
        })

        # Clients ---------------------------------------------------------
        try:
            summary = ctx.auth.summary(legacy_enabled=ctx.legacy_mcp_token_enabled())
        except Exception:  # noqa: BLE001
            summary = {}
        dn, grp = label_for("clients", None, None)
        nodes.append({
            "id": "clients", "type": "external-client", "kind": "unknown", "label": "MCP clients",
            "display_name": dn, "group": grp,
            "detail": {"oauth_clients": summary.get("oauth_clients", 0), "api_tokens": summary.get("api_tokens", 0),
                       "active_oauth_grants": summary.get("active_oauth_grants", 0),
                       "legacy_machine_token_mcp": bool(summary.get("legacy_machine_token_mcp", False)),
                       "recent_principals": self._recent_principals()},
        })

        # Local listener ----------------------------------------------------
        local_enabled = bool((ing.get("local") or {}).get("enabled", True))
        st = self._status(state.get("clients->gateway"), disabled=not local_enabled)
        edges.append({"id": "clients->gateway", "from": "clients", "to": "gateway", "transport": "http",
                      "protocol": "mcp", "exposure": "local", "initiator": "clients", "direct": True,
                      "proxied": False, **st,
                      "detail": {"url": f"http://127.0.0.1:{int(cfg.get('port') or 2026)}/mcp",
                                 "note": "loopback listener; reachable only from this machine"}})

        # Cloudflare --------------------------------------------------------
        cf = ing.get("cloudflare") or {}
        if cf.get("enabled"):
            exposure = "access-protected" if cf.get("access_policy") == "cloudflare-access" else "public"
            tid = str(cf.get("tunnel_id") or "")
            dn, grp = label_for("ingress:cloudflare", None, "ingress")
            nodes.append({
                "id": "ingress:cloudflare", "type": "tunnel", "kind": "unknown", "label": "Cloudflare Tunnel",
                "display_name": dn, "group": grp,
                "detail": {"hostname": cf.get("hostname"), "exposure": exposure,
                           "access_policy": cf.get("access_policy"),
                           "tunnel_id": (tid[:8] + "…") if tid else None,
                           "credential": "token-file" if cf.get("token_file") else ("named-tunnel" if tid else None)},
            })
            edges.append({"id": "clients->ingress:cloudflare", "from": "clients", "to": "ingress:cloudflare",
                          "transport": "https", "protocol": "mcp", "exposure": exposure, "initiator": "clients",
                          "proxied": True, "direct": False, **self._status(state.get("clients->ingress:cloudflare")),
                          "detail": {"url": f"https://{cf.get('hostname')}/mcp"}})
            edges.append({"id": "ingress:cloudflare->gateway", "from": "ingress:cloudflare", "to": "gateway",
                          "transport": "cloudflare-tunnel", "protocol": "http", "exposure": exposure,
                          "initiator": "gateway", "proxied": True, "direct": False,
                          **self._status(state.get("ingress:cloudflare->gateway")),
                          "detail": {"note": "outbound tunnel opened by cloudflared on the gateway; requests are "
                                             "forwarded to the local listener"}})

        # ngrok: process liveness and public health remain separate observations.
        ng = ing.get("ngrok") or {}
        if ng.get("enabled"):
            dn, grp = label_for("ingress:ngrok", None, "ingress")
            nodes.append({"id": "ingress:ngrok", "type": "tunnel", "kind": "unknown", "label": "ngrok",
                          "display_name": dn, "group": grp,
                          "detail": {"hostname": ng.get("hostname"), "exposure": "public", "ownership": "managed"}})
            edges.append({"id": "clients->ingress:ngrok", "from": "clients", "to": "ingress:ngrok",
                          "transport": "https", "protocol": "mcp", "exposure": "public", "initiator": "clients",
                          "proxied": True, "direct": False, **self._status(state.get("clients->ingress:ngrok")),
                          "detail": {"url": f"https://{ng.get('hostname')}/mcp"}})
            edges.append({"id": "ingress:ngrok->gateway", "from": "ingress:ngrok", "to": "gateway",
                          "transport": "ngrok-tunnel", "protocol": "http", "exposure": "public", "initiator": "gateway",
                          "proxied": True, "direct": False, **self._status(state.get("ingress:ngrok->gateway")),
                          "detail": {"note": "Process liveness does not prove authenticated MCP connectivity"}})

        # Tailscale ---------------------------------------------------------
        ts = ing.get("tailscale") or {}
        if ts.get("enabled"):
            mode = ts.get("mode") or "serve"
            exposure = "public" if mode == "funnel" else "tailnet"
            dn, grp = label_for("ingress:tailscale", None, "ingress")
            nodes.append({
                "id": "ingress:tailscale", "type": "network", "kind": "unknown", "label": "Tailscale",
                "display_name": dn, "group": grp,
                "detail": {"dns_name": ts.get("dns_name"), "mode": mode, "exposure": exposure,
                           "https_port": ts.get("https_port")},
            })
            port_sfx = "" if int(ts.get("https_port") or 443) == 443 else f":{ts.get('https_port')}"
            edges.append({"id": "clients->ingress:tailscale", "from": "clients", "to": "ingress:tailscale",
                          "transport": "https", "protocol": "mcp", "exposure": exposure, "initiator": "clients",
                          "proxied": True, "direct": False, **self._status(state.get("clients->ingress:tailscale")),
                          "detail": {"url": f"https://{ts.get('dns_name')}{port_sfx}/mcp" if ts.get("dns_name") else None}})
            edges.append({"id": "ingress:tailscale->gateway", "from": "ingress:tailscale", "to": "gateway",
                          "transport": f"tailscale-{mode}", "protocol": "http", "exposure": exposure,
                          "initiator": "ingress:tailscale", "proxied": True, "direct": False,
                          **self._status(state.get("ingress:tailscale->gateway")),
                          "detail": {"note": "tailscaled terminates TLS and proxies to the local listener"}})

        # SSH targets -------------------------------------------------------
        for t in self._ssh_targets():
            name = t["name"]
            node_id, edge_id = f"ssh:{name}", f"gateway->ssh:{name}"
            probe = t.get("last_probe") or {}
            stored = state.get(edge_id)
            if stored and float(stored.get("checked_at") or 0) > float(probe.get("checked_at") or 0):
                probe = {**probe, **stored}
            remote = probe.get("remote") or {}
            via = t.get("via") if t.get("via") not in (None, "auto") else (
                probe.get("via") or infer_via(str(t.get("host") or ""), None, resolve=False))
            dn, grp = label_for(node_id, t.get("display_name"), t.get("group"))
            nodes.append({
                "id": node_id, "type": "remote-host", "kind": t.get("kind") or "unknown", "label": name,
                "display_name": dn, "group": grp,
                "detail": {"hostname": remote.get("hostname") or t.get("host"), "host": t.get("host"),
                           "port": t.get("port"), "os": remote.get("os"), "kernel": remote.get("kernel"),
                           "arch": remote.get("arch"), "user": remote.get("user") or t.get("user"),
                           "machine_kind": remote.get("machine_kind") or "unknown", "transport": "ssh", "via": via,
                           "key_configured": bool(t.get("identity_file"))},
            })
            edges.append({"id": edge_id, "from": "gateway", "to": node_id, "transport": "ssh", "protocol": "ssh",
                          "exposure": self._via_exposure(via), "initiator": "gateway", "direct": True,
                          "proxied": False, "jobs_running": jobs.get(name, 0), **self._status(probe or None),
                          "detail": {"via": via}})

        # Loopback nodes ----------------------------------------------------
        meta_all = self.node_meta.all()
        for n in self._fleet_nodes():
            name = str(n["name"])
            node_id, edge_id = f"node:{name}", f"gateway->node:{name}"
            meta = meta_all.get(name) if isinstance(meta_all.get(name), dict) else {}
            url = safe_url(n.get("url") or "")
            parts = urlsplit(url)
            via = infer_via(parts.hostname or "", None, resolve=False)
            stored = state.get(edge_id) or {}
            dn, grp = label_for(node_id, meta.get("display_name"), meta.get("group"))
            nodes.append({
                "id": node_id, "type": "loopback-node", "kind": meta.get("kind") or "unknown", "label": name,
                "display_name": dn, "group": grp,
                "detail": {"url": url, "enabled": bool(n.get("enabled", True)), "description": n.get("description"),
                           "via": via, **{k: v for k, v in (stored.get("remote") or {}).items()
                                          if k in ("server_name", "server_version", "protocol_version")}},
            })
            edges.append({"id": edge_id, "from": "gateway", "to": node_id,
                          "transport": parts.scheme or "https", "protocol": "mcp",
                          "exposure": self._via_exposure(via), "initiator": "gateway", "direct": True,
                          "proxied": False, "jobs_running": jobs.get(name, 0),
                          **self._status(stored or None, disabled=not bool(n.get("enabled", True))),
                          "detail": {"via": via}})

        # Workers report process health and payload counters separately from SSH control.
        manager = getattr(ctx, "workers", None)
        snapshot = getattr(manager, "cache", None) or {}
        workers = {w["target"]: w for w in snapshot.get("workers", [])}

        def worker_status(worker: dict[str, Any]) -> str:
            checked = snapshot.get("sampled_at") or worker.get("sampled_at")
            if not worker or not checked:
                return "untested"
            if time.time() - float(checked) > self.stale_after_seconds:
                return "stale"
            return worker.get("status", "untested")

        for node in nodes:
            target = "local" if node["id"] == "gateway" else node["id"].removeprefix("ssh:")
            if target in workers:
                worker = workers[target]
                fresh = worker_status(worker) == "healthy"
                node["detail"]["worker_metrics"] = worker.get("worker") if fresh else None
                node["detail"]["host_metrics"] = worker.get("host") if fresh else None
                node["detail"]["worker_status"] = worker_status(worker)
                node["detail"]["heartbeat_at"] = worker.get("heartbeat_at")
        for binding in manager.bindings() if manager else []:
            target = binding["target"]
            worker = workers.get(target, {})
            runtime = next((x for x in worker.get("bindings", []) if x["name"] == binding["name"]), {})
            health = worker_status(worker)
            service_status = ("healthy" if runtime.get("running") else "disconnected") if health == "healthy" else health
            nid = "service:" + binding["name"]
            exposure = {"private": "local", "tailscale-serve": "tailnet", "tailscale-funnel": "public", "cloudflare": "public"}[binding["exposure"]]
            nodes.append({"id": nid, "type": "mcp-service", "kind": "unknown", "label": binding["name"], "group": target,
                          "status": service_status,
                          "detail": {"target": target, "transport": binding["transport"], "exposure": exposure,
                                     "port": binding["port"], "url": runtime.get("url") or binding.get("url"),
                                     "heartbeat_at": worker.get("heartbeat_at"),
                                     "note": "Status measures the worker process, not public endpoint reachability"}})
            edges.append({"id": "host->" + nid, "from": "gateway" if target == "local" else "ssh:" + target,
                          "to": nid, "transport": binding["transport"], "protocol": "mcp", "exposure": "local",
                          "status": service_status, "direct": True, "proxied": False,
                          "detail": {"note": "Service runs here; this edge is hosting, not SSH data forwarding"}})
            if exposure != "local":
                edges.append({"id": "clients->" + nid, "from": "clients", "to": nid,
                              "transport": binding["exposure"], "protocol": "mcp", "exposure": exposure,
                              "status": "untested", "direct": True, "proxied": False,
                              "detail": {"note": "Direct to worker; public reachability has not been probed"}})

        # Discovered routes (read-only inventory, cached; never probed here) ---------
        discovery = getattr(ctx, "discovery", None)
        inventory = discovery.cached() if discovery is not None else {"hosts": []}
        known_hosts = {n["id"] for n in nodes}
        for host in inventory.get("hosts") or []:
            target = host.get("target")
            host_node = "gateway" if target == "local" else f"ssh:{target}"
            if host_node not in known_hosts:
                continue
            if host.get("status") == "unreachable":
                for n in nodes:
                    if n["id"] == host_node:
                        n["detail"]["discovery"] = {"status": "unreachable", "error": host.get("error"), "last_known": host.get("last_known", False)}
            for r in host.get("routes") or []:
                if r.get("owner") == "managed" and r.get("owned_by") not in (None, "controller"):
                    continue  # already drawn as a managed service / ingress node
                if r.get("owned_by") == "controller":
                    continue  # the configured ingress nodes above cover it
                rid = "route:" + str(r["id"])
                label = (r.get("hostname") or r.get("provider") or "route") + (f":{r['port']}" if r.get("port") not in (None, 443) else "") + (r.get("path") or "")
                status = "stale" if host.get("status") == "unreachable" else ("healthy" if r.get("target") else "untested")
                nodes.append({"id": rid, "type": "tunnel", "kind": "unknown", "label": label[:60], "group": target,
                              "status": status, "ownership": r.get("owner"),
                              "detail": {"provider": r.get("provider"), "mode": r.get("mode"), "exposure": r.get("exposure"),
                                         "hostname": r.get("hostname"), "path": r.get("path"), "target": r.get("target"),
                                         "owner": r.get("owner"), "owned_by": r.get("owned_by"), "kind": r.get("kind"),
                                         "host": target, "note": r.get("note") or "Discovered route; Loopback does not manage it",
                                         "observed_at": host.get("observed_at")}})
                edges.append({"id": f"clients->{rid}", "from": "clients", "to": rid, "transport": r.get("provider"), "protocol": "https",
                              "exposure": r.get("exposure"), "initiator": "clients", "direct": False, "proxied": True, "status": status,
                              "detail": {"note": "External route observed on the host; reachability not probed"}})
                edges.append({"id": f"{rid}->{host_node}", "from": rid, "to": host_node, "transport": r.get("provider"), "protocol": "http",
                              "exposure": "local", "initiator": rid, "direct": True, "proxied": False, "status": status,
                              "detail": {"target": r.get("target"), "note": "Handler target on the host"}})

        return {
            "generated_at": time.time(),
            "stale_after_seconds": self.stale_after_seconds,
            "nodes": nodes,
            "edges": edges,
            "legend": {"statuses": list(STATUSES), "ownership": ["managed", "external", "unknown"]},
            "discovery_generated_at": inventory.get("generated_at"),
        }

    # ---------------------------------------------------------------- probing
    async def probe_node(self, name: str, timeout: float = 8.0, *, store: bool = True) -> dict[str, Any]:
        """Probe a Loopback node with an MCP ``initialize`` request.

        Returns ``{status, latency_ms, error, checked_at, remote:{server_name,
        server_version, protocol_version, url}}``. Raises KeyError for unknown nodes.
        The node's bearer token is sent but never returned or logged.
        """
        import httpx

        node, token = self.ctx.fleet.get(name)
        url = str(node.get("url") or "")
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": MCP_PROTOCOL_VERSION, "User-Agent": f"loopback-gateway/{self.ctx.version}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                           "clientInfo": {"name": "loopback-gateway-probe", "version": self.ctx.version or "4"}}}
        result: dict[str, Any] = {"status": "error", "latency_ms": None, "error": None, "checked_at": None,
                                  "remote": {"url": safe_url(url)}}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
                                         follow_redirects=False) as client:
                info: dict[str, Any] | None = None
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    status_code = resp.status_code
                    session_id = resp.headers.get("mcp-session-id")
                    raw = b""
                    if 200 <= status_code < 300:
                        async for chunk in resp.aiter_bytes():
                            raw += chunk
                            if b"serverInfo" in raw:
                                info = _parse_initialize(raw.decode("utf-8", errors="replace"))
                                if info is not None:
                                    break
                            if len(raw) > 262144:
                                break
                result["latency_ms"] = int((time.monotonic() - started) * 1000)
                if session_id:
                    try:  # politely end the probe session; failures are irrelevant
                        await client.delete(url, headers={**headers, "Mcp-Session-Id": session_id}, timeout=3.0)
                    except httpx.HTTPError:
                        pass
            if status_code in (401, 403):
                result.update(status="auth_failed", error=f"HTTP {status_code}: node rejected the gateway credential")
            elif 200 <= status_code < 300:
                if info is not None:
                    result["status"] = "healthy"
                    result["remote"].update(info)
                else:
                    result.update(status="degraded", error="2xx response without MCP serverInfo")
            else:
                result.update(status="unreachable" if status_code in (502, 503, 504) else "degraded",
                              error=f"HTTP {status_code}")
        except httpx.TimeoutException:
            result.update(status="degraded", error=f"timed out after {timeout}s")
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            result.update(status="unreachable", error=_clip(str(exc) or exc.__class__.__name__))
        except (httpx.HTTPError, ValueError) as exc:
            result.update(status="error", error=_clip(str(exc) or exc.__class__.__name__))
        result["checked_at"] = time.time()
        if store:
            self.store_results({f"gateway->node:{name}": result})
        return result

    async def _probe_local(self) -> dict[str, Any]:
        import httpx

        port = int((getattr(self.ctx.config, "data", {}) or {}).get("port") or 2026)
        started = time.monotonic()
        res: dict[str, Any] = {"status": "error", "latency_ms": None, "error": None}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/healthz")
            res["latency_ms"] = int((time.monotonic() - started) * 1000)
            res["status"] = "healthy" if resp.status_code < 400 else "degraded"
            if resp.status_code >= 400:
                res["error"] = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            res.update(status="degraded", error="local listener timed out")
        except httpx.HTTPError as exc:
            res.update(status="unreachable", error=_clip(str(exc) or exc.__class__.__name__))
        res["checked_at"] = time.time()
        return res

    def _ingress_results(self, status: dict[str, Any]) -> dict[str, dict[str, Any]]:
        now = time.time()
        out: dict[str, dict[str, Any]] = {}
        cf = status.get("cloudflare") or {}
        ng = status.get("ngrok") or {}
        if ng.get("enabled"):
            ph = ng.get("public_health") or {}
            out["clients->ingress:ngrok"] = {
                "status": "healthy" if ph.get("ok") else ("degraded" if ph.get("status") else "untested"),
                "latency_ms": ph.get("latency_ms"), "error": _clip(ph.get("error")), "checked_at": now}
            out["ingress:ngrok->gateway"] = {
                "status": "healthy" if ng.get("running") else "disconnected", "latency_ms": None,
                "error": None if ng.get("running") else _clip(ng.get("last_error")), "checked_at": now}
        if cf.get("enabled"):
            ph = cf.get("public_health") or {}
            out["clients->ingress:cloudflare"] = {
                "status": "healthy" if ph.get("ok") else ("unreachable" if not ph.get("status") else "degraded"),
                "latency_ms": ph.get("latency_ms"), "error": _clip(ph.get("error")), "checked_at": now}
            running = bool(cf.get("running"))
            out["ingress:cloudflare->gateway"] = {
                "status": "healthy" if running else "disconnected", "latency_ms": None,
                "error": None if running else (_clip(cf.get("last_error")) or "cloudflared is not running"),
                "checked_at": now}
        ts = status.get("tailscale") or {}
        if ts.get("enabled"):
            ph = ts.get("public_health") or {}
            mode = ts.get("mode") or "serve"
            if ph.get("ok"):
                st = "healthy"
            elif mode == "serve" and not ph.get("status"):
                # Serve is only reachable inside the tailnet; the gateway may not reach itself.
                st = "healthy" if ts.get("serve_active") and ts.get("backend_state") == "Running" else "unreachable"
            else:
                st = "degraded" if ph.get("status") else "unreachable"
            out["clients->ingress:tailscale"] = {"status": st, "latency_ms": ph.get("latency_ms"),
                                                 "error": None if st == "healthy" else _clip(ph.get("error")),
                                                 "checked_at": now}
            active = ts.get("funnel_active") if mode == "funnel" else ts.get("serve_active")
            backend_ok = ts.get("backend_state") == "Running"
            out["ingress:tailscale->gateway"] = {
                "status": "healthy" if active and backend_ok else ("disconnected" if not backend_ok else "degraded"),
                "latency_ms": None, "checked_at": now,
                "error": None if active and backend_ok else
                f"backend={ts.get('backend_state')}, {mode} {'active' if active else 'not configured'} for this port"}
        return out

    async def refresh(self) -> dict[str, Any]:
        """Probe every configured edge (concurrency 4), persist results, return :meth:`build`."""
        started = time.monotonic()
        sem = asyncio.Semaphore(4)
        results: dict[str, dict[str, Any]] = {}

        async def guarded(edge_id: str, factory: Any) -> None:
            async with sem:
                try:
                    res = await factory()
                except KeyError:
                    return
                except Exception as exc:  # noqa: BLE001 - one failing probe must not abort the refresh
                    res = {"status": "error", "latency_ms": None, "error": _clip(str(exc)), "checked_at": time.time()}
                if isinstance(res, dict) and edge_id:
                    results[edge_id] = res
                elif isinstance(res, dict) and not edge_id:
                    results.update(res)

        tasks = [guarded("clients->gateway", self._probe_local)]
        ssh = getattr(self.ctx, "ssh", None)
        if ssh is not None:
            for t in self._ssh_targets():
                # ssh.probe stores probe_state.json + history itself; mirror into topology state only.
                tasks.append(guarded(f"gateway->ssh:{t['name']}",
                                     lambda n=t["name"]: asyncio.to_thread(ssh.probe, n)))
        for n in self._fleet_nodes():
            if n.get("enabled", True):
                tasks.append(guarded(f"gateway->node:{n['name']}",
                                     lambda nm=n["name"]: self.probe_node(nm, store=False)))
        ingress = getattr(self.ctx, "ingress", None)
        if ingress is not None:
            async def ingress_probe() -> dict[str, Any]:
                status = await asyncio.to_thread(ingress.status)
                return self._ingress_results(status)
            tasks.append(guarded("", ingress_probe))
        await asyncio.gather(*tasks)

        ssh_ids = {k for k in results if k.startswith("gateway->ssh:")}
        with self._lock:
            data = self.state()
            data.update(results)
            known = {e["id"] for e in self._edge_skeletons()}
            data = {k: v for k, v in data.items() if k in known}
            _atomic_write_json(self.state_path, data)
        for edge_id, res in results.items():
            if edge_id in ssh_ids:
                continue  # already recorded by SSHTargetStore.probe
            try:
                self.history.record(edge_id, res.get("status", "error"), res.get("latency_ms"), res.get("error"),
                                    ts=res.get("checked_at"))
            except Exception:  # noqa: BLE001
                pass
        if time.time() - self._last_prune > 3600:
            self._last_prune = time.time()
            try:
                self.history.prune(int(self._maintenance().get("history_retention_days", 7) or 7))
            except Exception:  # noqa: BLE001
                pass
        self.last_refresh_ms = int((time.monotonic() - started) * 1000)
        return self.build()


def _parse_initialize(text: str) -> dict[str, Any] | None:
    """Extract serverInfo from a JSON or SSE ``initialize`` response."""
    candidates = [text]
    if "data:" in text:
        candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")] + candidates
    for chunk in candidates:
        try:
            doc = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        res = doc.get("result") if isinstance(doc, dict) else None
        if isinstance(res, dict) and isinstance(res.get("serverInfo"), dict):
            info = res["serverInfo"]
            return {"server_name": _clip(info.get("name"), 80), "server_version": _clip(info.get("version"), 40),
                    "protocol_version": _clip(res.get("protocolVersion"), 20)}
    return None
