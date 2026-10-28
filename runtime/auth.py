"""Loopback authentication and authorization store.

Loopback acts as both an OAuth 2.1 authorization server and the protected MCP
resource. Everything that previously lived in process memory (clients, codes,
sessions) is persisted in SQLite so restarts do not invalidate client state and
so refresh-token reuse can be detected.

Credential kinds
----------------
* **OAuth access token** – signed JWT (HS256, independent random signing key),
  audience-bound to one canonical resource URI, short-lived, revocable by JTI.
* **OAuth refresh token** – opaque, stored hashed, rotated on every use,
  family-revoked on reuse.
* **API token (personal access token)** – opaque ``lbk_<id>_<secret>``,
  stored hashed, scoped, optionally expiring, revocable. Issued only from an
  authenticated dashboard session or the local CLI.
* **Machine token** – the legacy shared secret. From v4 it is an *administrative
  recovery credential* (dashboard/CLI login). MCP access with it is disabled on
  fresh installs and shown as a migration warning on upgraded ones.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import jwt

SCOPES: dict[str, str] = {
    "loopback:read": "Read files, list directories, inspect processes, diagnostics and topology.",
    "loopback:write": "Create, modify and delete files inside permitted locations.",
    "loopback:exec": "Run shell commands, background jobs and terminals under the shell policy.",
    "loopback:fleet": "Route calls to registered remote Loopback nodes and SSH targets.",
    "loopback:admin": "Change Loopback policy and configuration (dashboard-level power).",
}
LEGACY_SCOPE = "loopback"
LEGACY_EXPANSION = {"loopback:read", "loopback:write", "loopback:exec", "loopback:fleet"}
DEFAULT_CLIENT_SCOPES = ["loopback:read", "loopback:write", "loopback:exec", "loopback:fleet"]
# One contract for registration, metadata, token and revocation. Public clients stay the default.
CLIENT_AUTH_METHODS = ["none", "client_secret_basic", "client_secret_post"]
SUPPORTED_GRANT_TYPES = ["authorization_code", "refresh_token"]


class AuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status = status


def normalize_scopes(scope: str | Iterable[str] | None) -> set[str]:
    if scope is None:
        return set()
    parts = scope.split() if isinstance(scope, str) else list(scope)
    out: set[str] = set()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part == LEGACY_SCOPE:
            out |= LEGACY_EXPANSION
        elif part in SCOPES:
            out.add(part)
        else:
            raise AuthError("invalid_scope", f"unknown scope: {part}")
    return out


def scope_satisfied(granted: set[str], required: str | None) -> bool:
    if not required:
        return True
    if "loopback:admin" in granted:
        return True
    return required in granted


def scope_string(scopes: Iterable[str]) -> str:
    return " ".join(sorted(set(scopes)))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${b64url(salt)}${b64url(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt_b64, digest_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        salt = base64.urlsafe_b64decode(pad(salt_b64))
        expected = base64.urlsafe_b64decode(pad(digest_b64))
    except (ValueError, TypeError):
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, expected)


def redirect_uri_allowed(uri: str) -> bool:
    """Exact-match registration policy: HTTPS, or loopback HTTP, no fragment/userinfo."""
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment or parsed.username or parsed.password or not parsed.netloc:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    return False


def redirect_uri_matches(registered: list[str], supplied: str) -> bool:
    """RFC 8252 §7.3: loopback redirects may vary the port; everything else is exact."""
    if supplied in registered:
        return True
    try:
        s = urlparse(supplied)
    except ValueError:
        return False
    if s.scheme != "http" or s.hostname not in {"127.0.0.1", "::1", "localhost"}:
        return False
    for reg in registered:
        r = urlparse(reg)
        if r.scheme == "http" and r.hostname == s.hostname and r.path == s.path and r.query == s.query:
            return True
    return False


@dataclass
class Principal:
    kind: str  # oauth | api_token | machine | session
    subject: str
    display: str
    scopes: set[str] = field(default_factory=set)
    token_id: str | None = None
    client_id: str | None = None
    resource: str | None = None

    def has(self, scope: str | None) -> bool:
        return scope_satisfied(self.scopes, scope)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "display": self.display,
            "scopes": sorted(self.scopes),
            "token_id": self.token_id,
            "client_id": self.client_id,
        }


class RateLimiter:
    """Small in-memory sliding-window limiter with lockouts for credential endpoints."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._locks: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str, key: str, limit: int, window: float) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits.setdefault((bucket, key), deque())
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def locked(self, bucket: str, key: str) -> bool:
        with self._lock:
            until = self._locks.get((bucket, key), 0)
            if until > time.time():
                return True
            self._locks.pop((bucket, key), None)
            return False

    def failure(self, bucket: str, key: str, threshold: int = 10, window: float = 900, lock_for: float = 900) -> bool:
        """Record a failed credential attempt. Returns True if a lockout started."""
        now = time.time()
        with self._lock:
            q = self._failures.setdefault((bucket, key), deque())
            while q and q[0] <= now - window:
                q.popleft()
            q.append(now)
            if len(q) >= threshold:
                self._locks[(bucket, key)] = now + lock_for
                q.clear()
                return True
            return False

    def success(self, bucket: str, key: str) -> None:
        with self._lock:
            self._failures.pop((bucket, key), None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'dcr',
    metadata TEXT,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);
CREATE TABLE IF NOT EXISTS authz_requests (
    id TEXT PRIMARY KEY,
    params TEXT NOT NULL,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    resource TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    rotated_at REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_refresh_family ON refresh_tokens(family_id);
CREATE TABLE IF NOT EXISTS access_tokens (
    jti TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    resource TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked_at REAL,
    family_id TEXT
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    last_used_at REAL,
    revoked_at REAL,
    created_by TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    sid_hash TEXT PRIMARY KEY,
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    user_agent TEXT,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS admin_credential (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_key (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    key_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollment_codes (
    code_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    principal TEXT,
    ip TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_events_ts ON auth_events(ts DESC);
"""


class AuthStore:
    def __init__(self, config_dir: Path, *, resource_uris: list[str], base_urls: list[str],
                 access_ttl: int = 3600, refresh_ttl: int = 30 * 86400, session_ttl: int = 12 * 3600,
                 code_ttl: int = 300):
        self.dir = Path(config_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "auth.db"
        self.machine_token_path = self.dir / "token"
        self.signing_key_path = self.dir / "oauth-signing.key"
        self.resource_uris = [r.rstrip("/") for r in resource_uris]
        self.base_urls = [b.rstrip("/") for b in base_urls]
        self.access_ttl = int(access_ttl)
        self.refresh_ttl = int(refresh_ttl)
        self.session_ttl = int(session_ttl)
        self.code_ttl = int(code_ttl)
        self._lock = threading.RLock()
        self.limiter = RateLimiter()
        self.machine_token = self._load_or_create_machine_token()
        self.signing_key = self._load_or_create_signing_key()
        if not self.db_path.exists():
            os.close(os.open(self.db_path, os.O_CREAT | os.O_WRONLY, 0o600))
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        with self._connect() as db:
            db.executescript(SCHEMA)
            cols = {row[1] for row in db.execute("PRAGMA table_info(oauth_clients)").fetchall()}
            if "secret_hash" not in cols:
                db.execute("ALTER TABLE oauth_clients ADD COLUMN secret_hash TEXT")

        with self._connect() as db:
            cols = {row[1] for row in db.execute("PRAGMA table_info(admin_credential)")}
            if "username" not in cols:
                db.execute("ALTER TABLE admin_credential ADD COLUMN username TEXT")

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _load_or_create_machine_token(self) -> str:
        if self.machine_token_path.exists():
            value = self.machine_token_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_hex(32)
        self._write_secret(self.machine_token_path, value + "\n")
        return value

    def _load_or_create_signing_key(self) -> bytes:
        if self.signing_key_path.exists():
            raw = self.signing_key_path.read_text(encoding="utf-8").strip()
            if len(raw) >= 64:
                return bytes.fromhex(raw)
        key = secrets.token_bytes(32)
        self._write_secret(self.signing_key_path, key.hex() + "\n")
        return key

    @staticmethod
    def _write_secret(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def rotate_signing_key(self) -> None:
        """Invalidate every OAuth access token immediately (refresh tokens survive)."""
        key = secrets.token_bytes(32)
        self._write_secret(self.signing_key_path, key.hex() + "\n")
        self.signing_key = key
        self.record_event("signing_key_rotated", "admin")

    def rotate_machine_token(self) -> str:
        value = secrets.token_hex(32)
        self._write_secret(self.machine_token_path, value + "\n")
        self.machine_token = value
        self.record_event("machine_token_rotated", "admin")
        return value

    def set_urls(self, resource_uris: list[str], base_urls: list[str]) -> None:
        self.resource_uris = [r.rstrip("/") for r in resource_uris]
        self.base_urls = [b.rstrip("/") for b in base_urls]

    def issuer_for(self, resource: str) -> str:
        for base in self.base_urls:
            if resource.startswith(base + "/") or resource == base:
                return base
        return self.base_urls[0]

    def canonical_resource(self, value: str | None) -> str:
        """Validate a resource indicator (RFC 8707) against this instance's URIs."""
        if not value:
            return self.resource_uris[0]
        candidate = value.strip().rstrip("/")
        parsed = urlparse(candidate)
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        if parsed.fragment:
            raise AuthError("invalid_target", "resource must not contain a fragment")
        if normalized in self.resource_uris:
            return normalized
        raise AuthError("invalid_target", "resource is not served by this Loopback instance")

    # --------------------------------------------------------------- events
    def record_event(self, event: str, principal: str | None = None, ip: str | None = None,
                     detail: dict[str, Any] | None = None) -> None:
        payload = json.dumps(detail or {}, separators=(",", ":"))[:4000]
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO auth_events(ts,event,principal,ip,detail) VALUES(?,?,?,?,?)",
                       (time.time(), event, principal, ip, payload))
            db.execute("DELETE FROM auth_events WHERE id NOT IN (SELECT id FROM auth_events ORDER BY id DESC LIMIT 5000)")

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT ts,event,principal,ip,detail FROM auth_events ORDER BY id DESC LIMIT ?",
                              (max(1, min(int(limit), 1000)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"] or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            out.append(item)
        return out

    # -------------------------------------------------------------- clients
    @staticmethod
    def _string_list(metadata: dict[str, Any], key: str, default: list[str]) -> list[str]:
        """Return a metadata array as a list of strings; absent/empty → *default*, malformed → error."""
        value = metadata.get(key)
        if value in (None, [], ""):
            return list(default)
        if isinstance(value, str):
            value = value.split()  # tolerate space-separated strings sent by some clients
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise AuthError("invalid_client_metadata", f"{key} must be an array of strings")
        return value

    def register_client(self, metadata: dict[str, Any], *, kind: str = "dcr", client_id: str | None = None) -> dict[str, Any]:
        """Register (or refresh) a client per RFC 7591.

        Contract, consistent with the metadata document and the token endpoint:

        * ``token_endpoint_auth_method``: ``none`` (public, default when omitted so existing
          MCP clients keep working), ``client_secret_basic`` or ``client_secret_post``.
          Confidential methods are only available to DCR clients (``kind == "dcr"``): the
          server mints the secret, discloses it once in the registration response and stores
          only its hash. Client ID Metadata Documents cannot receive a secret, so they stay
          public.
        * ``grant_types``: narrowed to the supported subset (RFC 7591 §3.2.1 permits the
          server to substitute requested metadata). ``authorization_code`` must survive the
          narrowing. ``client_credentials``, ``implicit`` and ``password`` are never enabled.
        * ``response_types``: must include ``code``; everything else is dropped.
        """
        redirect_uris = metadata.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > 10:
            raise AuthError("invalid_client_metadata", "redirect_uris must list between 1 and 10 URIs")
        for uri in redirect_uris:
            if not isinstance(uri, str) or not redirect_uri_allowed(uri):
                raise AuthError("invalid_redirect_uri", f"redirect URI not permitted: {uri!r}")
        auth_method = metadata.get("token_endpoint_auth_method") or "none"
        if auth_method not in CLIENT_AUTH_METHODS:
            raise AuthError("invalid_client_metadata",
                            "token_endpoint_auth_method must be one of: " + ", ".join(CLIENT_AUTH_METHODS))
        if auth_method != "none" and kind != "dcr":
            raise AuthError("invalid_client_metadata",
                            "client ID metadata documents describe public clients; use token_endpoint_auth_method=none")
        requested_grants = self._string_list(metadata, "grant_types", ["authorization_code", "refresh_token"])
        grant_types = [g for g in requested_grants if g in SUPPORTED_GRANT_TYPES]
        if "authorization_code" not in grant_types:
            raise AuthError("invalid_client_metadata",
                            "unsupported grant type requested: the authorization_code grant is required "
                            f"(supported: {', '.join(SUPPORTED_GRANT_TYPES)})")
        requested_responses = self._string_list(metadata, "response_types", ["code"])
        if "code" not in requested_responses:
            raise AuthError("invalid_client_metadata", "only response_type=code is supported")
        name = str(metadata.get("client_name") or "MCP client")[:200]
        cid = client_id or secrets.token_urlsafe(18)
        record = {
            "client_id": cid,
            "client_name": name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": auth_method,
            "grant_types": grant_types,
            "response_types": ["code"],
            "client_id_issued_at": int(time.time()),
        }
        for key in ("client_uri", "logo_uri", "software_id", "software_version"):
            if isinstance(metadata.get(key), str):
                record[key] = metadata[key][:500]
        secret = secrets.token_urlsafe(32) if auth_method != "none" else None
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO oauth_clients(client_id,client_name,redirect_uris,kind,metadata,created_at,last_used_at,revoked_at,secret_hash)"
                " VALUES(?,?,?,?,?,?,NULL,NULL,?)",
                (cid, name, json.dumps(redirect_uris), kind, json.dumps(record), time.time(),
                 sha256_hex(secret) if secret else None),
            )
        self.record_event("client_registered", cid, detail={"kind": kind, "name": name, "auth_method": auth_method,
                                                            "grant_types": grant_types,
                                                            "narrowed": sorted(set(requested_grants) - set(grant_types))})
        if secret:
            # One-time disclosure (RFC 7591 §3.2.1). The hash is the only copy Loopback keeps.
            return {**record, "client_secret": secret, "client_secret_expires_at": 0}
        return record

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM oauth_clients WHERE client_id=? AND revoked_at IS NULL", (client_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["redirect_uris"] = json.loads(item["redirect_uris"])
        try:
            meta = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            meta = {}
        item["token_endpoint_auth_method"] = meta.get("token_endpoint_auth_method") or "none"
        item["grant_types"] = meta.get("grant_types") or ["authorization_code", "refresh_token"]
        item["confidential"] = item["token_endpoint_auth_method"] != "none" and bool(item.get("secret_hash"))
        item.pop("secret_hash", None)
        return item

    def verify_client_secret(self, client_id: str, secret: str) -> bool:
        """Constant-time check of a confidential client's secret. Public clients always fail."""
        with self._connect() as db:
            row = db.execute("SELECT secret_hash FROM oauth_clients WHERE client_id=? AND revoked_at IS NULL", (client_id,)).fetchone()
        if not row or not row["secret_hash"] or not secret:
            # Burn the same hashing cost so timing does not reveal whether the client exists.
            hmac.compare_digest(sha256_hex(secret or ""), sha256_hex("x"))
            return False
        return hmac.compare_digest(row["secret_hash"], sha256_hex(secret))

    def rotate_client_secret(self, client_id: str) -> str:
        """Mint a new secret for a confidential client; returns it once."""
        client = self.get_client(client_id)
        if not client or client["token_endpoint_auth_method"] == "none":
            raise AuthError("invalid_client", "client is not a confidential client", 404)
        secret = secrets.token_urlsafe(32)
        with self._lock, self._connect() as db:
            db.execute("UPDATE oauth_clients SET secret_hash=? WHERE client_id=?", (sha256_hex(secret), client_id))
        self.record_event("client_secret_rotated", client_id)
        return secret

    def touch_client(self, client_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE oauth_clients SET last_used_at=? WHERE client_id=?", (time.time(), client_id))

    def list_clients(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM oauth_clients ORDER BY created_at DESC").fetchall()
            counts = {r["client_id"]: r["c"] for r in db.execute(
                "SELECT client_id, COUNT(*) c FROM refresh_tokens WHERE revoked_at IS NULL AND rotated_at IS NULL AND expires_at>? GROUP BY client_id",
                (time.time(),)).fetchall()}
        out = []
        for row in rows:
            item = {k: row[k] for k in ("client_id", "client_name", "kind", "created_at", "last_used_at", "revoked_at")}
            item["redirect_uris"] = json.loads(row["redirect_uris"])
            try:
                meta = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            item["token_endpoint_auth_method"] = meta.get("token_endpoint_auth_method") or "none"
            item["grant_types"] = meta.get("grant_types") or list(SUPPORTED_GRANT_TYPES)
            item["active_grants"] = counts.get(row["client_id"], 0)
            out.append(item)
        return out

    def revoke_client(self, client_id: str) -> bool:
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE oauth_clients SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL", (time.time(), client_id))
            db.execute("UPDATE refresh_tokens SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL", (time.time(), client_id))
            db.execute("UPDATE access_tokens SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL", (time.time(), client_id))
        self.record_event("client_revoked", client_id)
        return cur.rowcount > 0

    # ------------------------------------------------- authorization requests
    def stash_authz_request(self, params: dict[str, Any]) -> tuple[str, str]:
        rid = secrets.token_urlsafe(18)
        csrf = secrets.token_urlsafe(24)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO authz_requests(id,params,csrf,created_at,expires_at) VALUES(?,?,?,?,?)",
                       (rid, json.dumps(params), csrf, time.time(), time.time() + 600))
        return rid, csrf

    def pop_authz_request(self, rid: str, csrf: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT params,csrf,expires_at FROM authz_requests WHERE id=?", (rid,)).fetchone()
            db.execute("DELETE FROM authz_requests WHERE id=? OR expires_at<?", (rid, time.time()))
        if not row or row["expires_at"] < time.time() or not hmac.compare_digest(row["csrf"], csrf):
            return None
        return json.loads(row["params"])

    # ------------------------------------------------------------- auth codes
    def create_code(self, *, client_id: str, redirect_uri: str, code_challenge: str, resource: str, scope: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO auth_codes(code_hash,client_id,redirect_uri,code_challenge,resource,scope,created_at,expires_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (sha256_hex(code), client_id, redirect_uri, code_challenge, resource, scope, time.time(), time.time() + self.code_ttl),
            )
        return code

    def consume_code(self, code: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM auth_codes WHERE code_hash=?", (sha256_hex(code),)).fetchone()
            if not row:
                raise AuthError("invalid_grant", "unknown authorization code")
            if row["used_at"] is not None:
                # Replay of a used code: revoke everything derived from this client.
                db.execute("UPDATE refresh_tokens SET revoked_at=? WHERE client_id=? AND created_at>=? AND revoked_at IS NULL",
                           (time.time(), row["client_id"], row["used_at"] - 1))
                self.record_event("auth_code_replayed", row["client_id"])
                raise AuthError("invalid_grant", "authorization code already used")
            if row["expires_at"] < time.time():
                raise AuthError("invalid_grant", "authorization code expired")
            db.execute("UPDATE auth_codes SET used_at=? WHERE code_hash=?", (time.time(), row["code_hash"]))
        return dict(row)

    # ---------------------------------------------------------------- tokens
    def issue_access_token(self, *, client_id: str, resource: str, scopes: set[str], kind: str = "oauth",
                           family_id: str | None = None, ttl: int | None = None) -> tuple[str, dict[str, Any]]:
        now = int(time.time())
        exp = now + int(ttl or self.access_ttl)
        jti = secrets.token_urlsafe(16)
        claims = {
            "iss": self.issuer_for(resource),
            "sub": client_id,
            "aud": resource,
            "iat": now,
            "exp": exp,
            "jti": jti,
            "typ": "access",
            "client_id": client_id,
            "scope": scope_string(scopes),
        }
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO access_tokens(jti,client_id,kind,scope,resource,created_at,expires_at,family_id) VALUES(?,?,?,?,?,?,?,?)",
                       (jti, client_id, kind, claims["scope"], resource, now, exp, family_id))
        return jwt.encode(claims, self.signing_key, algorithm="HS256"), claims

    def verify_access_token(self, token: str) -> Principal:
        try:
            claims = jwt.decode(token, self.signing_key, algorithms=["HS256"], audience=self.resource_uris,
                                options={"require": ["exp", "iat", "aud", "iss", "jti", "sub"]})
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", f"access token rejected: {exc.__class__.__name__}", 401) from exc
        if claims.get("typ") != "access":
            raise AuthError("invalid_token", "wrong token type", 401)
        if claims.get("iss") not in self.base_urls:
            raise AuthError("invalid_token", "unexpected issuer", 401)
        with self._connect() as db:
            row = db.execute("SELECT revoked_at FROM access_tokens WHERE jti=?", (claims["jti"],)).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AuthError("invalid_token", "access token revoked", 401)
        client = self.get_client(str(claims["client_id"]))
        display = client["client_name"] if client else str(claims["client_id"])
        return Principal(kind="oauth", subject=str(claims["sub"]), display=display,
                         scopes=normalize_scopes(claims.get("scope")), token_id=str(claims["jti"]),
                         client_id=str(claims["client_id"]), resource=str(claims["aud"]))

    def issue_refresh_token(self, *, client_id: str, resource: str, scopes: set[str], family_id: str | None = None) -> tuple[str, str]:
        token = "lbr_" + secrets.token_urlsafe(40)
        family = family_id or secrets.token_urlsafe(12)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO refresh_tokens(token_hash,family_id,client_id,scope,resource,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                       (sha256_hex(token), family, client_id, scope_string(scopes), resource, time.time(), time.time() + self.refresh_ttl))
        return token, family

    def rotate_refresh_token(self, token: str, *, client_id: str, resource: str) -> tuple[str, set[str], str]:
        """Validate + rotate. Reuse of a rotated token revokes the whole family."""
        h = sha256_hex(token)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM refresh_tokens WHERE token_hash=?", (h,)).fetchone()
            if not row:
                raise AuthError("invalid_grant", "unknown refresh token")
            if row["revoked_at"] is not None:
                raise AuthError("invalid_grant", "refresh token revoked")
            if row["rotated_at"] is not None:
                db.execute("UPDATE refresh_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), row["family_id"]))
                db.execute("UPDATE access_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), row["family_id"]))
                self.record_event("refresh_token_reuse_detected", row["client_id"], detail={"family": row["family_id"]})
                raise AuthError("invalid_grant", "refresh token reuse detected; grant revoked")
            if row["expires_at"] < time.time():
                raise AuthError("invalid_grant", "refresh token expired")
            if row["client_id"] != client_id:
                raise AuthError("invalid_grant", "refresh token belongs to a different client")
            if row["resource"] != resource:
                raise AuthError("invalid_target", "refresh token was issued for a different resource")
            db.execute("UPDATE refresh_tokens SET rotated_at=? WHERE token_hash=?", (time.time(), h))
        scopes = normalize_scopes(row["scope"])
        new_token, family = self.issue_refresh_token(client_id=client_id, resource=resource, scopes=scopes, family_id=row["family_id"])
        return new_token, scopes, family

    def revoke_token(self, token: str, hint: str | None = None) -> bool:
        """RFC 7009: revoke a refresh token (and its family) or an access token."""
        if token.startswith("lbr_") or hint == "refresh_token":
            with self._lock, self._connect() as db:
                row = db.execute("SELECT family_id FROM refresh_tokens WHERE token_hash=?", (sha256_hex(token),)).fetchone()
                if not row:
                    return False
                db.execute("UPDATE refresh_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), row["family_id"]))
                db.execute("UPDATE access_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), row["family_id"]))
            return True
        if token.startswith("lbk_"):
            return self.revoke_api_token_by_value(token)
        try:
            claims = jwt.decode(token, self.signing_key, algorithms=["HS256"], options={"verify_aud": False, "verify_exp": False})
        except jwt.PyJWTError:
            return False
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE access_tokens SET revoked_at=? WHERE jti=? AND revoked_at IS NULL", (time.time(), claims.get("jti")))
        return cur.rowcount > 0

    def revoke_family(self, family_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE refresh_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), family_id))
            db.execute("UPDATE access_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL", (time.time(), family_id))

    def active_grants(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT family_id, client_id, scope, resource, MIN(created_at) created_at, MAX(expires_at) expires_at"
                " FROM refresh_tokens WHERE revoked_at IS NULL AND expires_at>? GROUP BY family_id ORDER BY created_at DESC",
                (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ API tokens
    def create_api_token(self, name: str, scopes: Iterable[str], *, ttl_seconds: int | None, created_by: str,
                         note: str = "") -> tuple[str, dict[str, Any]]:
        name = name.strip()[:80]
        if not name:
            raise AuthError("invalid_request", "token name is required")
        scope_set = normalize_scopes(list(scopes))
        if not scope_set:
            raise AuthError("invalid_scope", "at least one scope is required")
        tid = secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")
        secret = secrets.token_urlsafe(32)
        value = f"lbk_{tid}_{secret}"
        expires = time.time() + int(ttl_seconds) if ttl_seconds else None
        record = {
            "id": tid, "name": name, "scope": scope_string(scope_set), "created_at": time.time(),
            "expires_at": expires, "last_used_at": None, "revoked_at": None, "created_by": created_by, "note": note[:500],
        }
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO api_tokens(id,name,token_hash,scope,created_at,expires_at,last_used_at,revoked_at,created_by,note)"
                       " VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                       (tid, name, sha256_hex(secret), record["scope"], record["created_at"], expires, created_by, record["note"]))
        self.record_event("api_token_created", created_by, detail={"id": tid, "name": name, "scope": record["scope"]})
        return value, record

    def list_api_tokens(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,name,scope,created_at,expires_at,last_used_at,revoked_at,created_by,note FROM api_tokens ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def revoke_api_token(self, token_id: str) -> bool:
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (time.time(), token_id))
        if cur.rowcount:
            self.record_event("api_token_revoked", "admin", detail={"id": token_id})
        return cur.rowcount > 0

    def revoke_api_token_by_value(self, value: str) -> bool:
        parts = value.split("_", 2)
        if len(parts) != 3:
            return False
        return self.revoke_api_token(parts[1])

    def verify_api_token(self, value: str) -> Principal:
        parts = value.strip().split("_", 2)
        if len(parts) != 3 or parts[0] != "lbk":
            raise AuthError("invalid_token", "malformed API token", 401)
        tid, secret = parts[1], parts[2]
        with self._connect() as db:
            row = db.execute("SELECT * FROM api_tokens WHERE id=?", (tid,)).fetchone()
        if not row or not hmac.compare_digest(row["token_hash"], sha256_hex(secret)):
            raise AuthError("invalid_token", "unknown API token", 401)
        if row["revoked_at"] is not None:
            raise AuthError("invalid_token", "API token revoked", 401)
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            raise AuthError("invalid_token", "API token expired", 401)
        if not row["last_used_at"] or time.time() - row["last_used_at"] > 60:
            with self._lock, self._connect() as db:
                db.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (time.time(), tid))
        return Principal(kind="api_token", subject=f"api:{tid}", display=row["name"],
                         scopes=normalize_scopes(row["scope"]), token_id=tid)

    # ------------------------------------------------------------ principals
    def authenticate(self, authorization: str | None, api_key: str | None = None, *, legacy_machine_token: bool = False) -> Principal | None:
        """Resolve an HTTP credential to a Principal, or None if absent/invalid."""
        candidates: list[str] = []
        if authorization:
            raw = authorization.strip()
            if raw.lower().startswith("bearer "):
                raw = raw[7:].strip()
            if raw:
                candidates.append(raw)
        if api_key and api_key.strip():
            candidates.append(api_key.strip())
        for value in candidates:
            if value.startswith("lbk_"):
                try:
                    return self.verify_api_token(value)
                except AuthError:
                    continue
            if value.count(".") == 2:
                try:
                    return self.verify_access_token(value)
                except AuthError:
                    continue
            if legacy_machine_token and hmac.compare_digest(value, self.machine_token):
                return Principal(kind="machine", subject="machine-token", display="legacy machine token",
                                 scopes=set(LEGACY_EXPANSION), token_id=None)
        return None

    # ------------------------------------------------------- admin sessions
    def has_password(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM admin_credential WHERE id=1").fetchone() is not None

    def dashboard_username(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT username FROM admin_credential WHERE id=1").fetchone()
        return row["username"] if row else None

    def ensure_dashboard_account(self) -> dict[str, str] | None:
        """Generate first-install credentials once. Never replace an existing password."""
        with self._lock:
            if self.has_password():
                return None
            username = "loopback-" + secrets.token_hex(4)
            password = secrets.token_urlsafe(24)
            self.set_dashboard_account(username, password)
            return {"username": username, "password": password}

    def set_dashboard_account(self, username: str, password: str) -> None:
        username = username.strip()
        if not username or len(username) > 80 or any(c.isspace() or ord(c) < 32 for c in username):
            raise AuthError("invalid_request", "username must contain 1 to 80 characters without spaces")
        if len(password) < 10:
            raise AuthError("invalid_request", "password must be at least 10 characters")
        with self._lock, self._connect() as db:
            now = time.time()
            db.execute("INSERT INTO admin_credential(id,password_hash,created_at,updated_at,username) VALUES(1,?,?,?,?)"
                       " ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,username=excluded.username,updated_at=excluded.updated_at",
                       (hash_password(password), now, now, username))
            db.execute("DELETE FROM admin_sessions")
        self.record_event("admin_account_changed", "admin")

    def verify_dashboard_login(self, username: str, supplied: str) -> str | None:
        method = self.verify_admin_credential(supplied)
        if method == "machine-token":
            return method
        expected = self.dashboard_username()
        if method == "password" and (expected is None or hmac.compare_digest(username.encode("utf-8"), expected.encode("utf-8"))):
            return method
        return None

    def has_connector_key(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM connector_key WHERE id=1").fetchone() is not None

    def ensure_connector_key(self) -> str | None:
        with self._lock:
            return None if self.has_connector_key() else self.rotate_connector_key()

    def rotate_connector_key(self) -> str:
        """Replace the consent secret. Existing per-client grants remain valid."""
        value = "lbs_" + secrets.token_urlsafe(32)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO connector_key(id,key_hash,created_at) VALUES(1,?,?)"
                       " ON CONFLICT(id) DO UPDATE SET key_hash=excluded.key_hash,created_at=excluded.created_at",
                       (sha256_hex(value), time.time()))
        self.record_event("connector_key_rotated", "admin")
        return value

    def verify_connector_key(self, supplied: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT key_hash FROM connector_key WHERE id=1").fetchone()
        return bool(row and hmac.compare_digest(sha256_hex(supplied), row["key_hash"]))

    def set_password(self, password: str) -> None:
        if len(password) < 10:
            raise AuthError("invalid_request", "password must be at least 10 characters")
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO admin_credential(id,password_hash,created_at,updated_at) VALUES(1,?,?,?)"
                       " ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash, updated_at=excluded.updated_at",
                       (hash_password(password), now, now))
        self.record_event("admin_password_set", "admin")

    def verify_admin_credential(self, supplied: str) -> str | None:
        """Return 'password' or 'machine-token' when the credential is valid."""
        supplied = supplied.strip()
        if not supplied:
            return None
        with self._connect() as db:
            row = db.execute("SELECT password_hash FROM admin_credential WHERE id=1").fetchone()
        if row and verify_password(supplied, row["password_hash"]):
            return "password"
        if hmac.compare_digest(supplied, self.machine_token):
            return "machine-token"
        return None

    def create_session(self, *, user_agent: str = "", ip: str = "", ttl: int | None = None) -> tuple[str, str]:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO admin_sessions(sid_hash,csrf,created_at,expires_at,last_seen_at,user_agent,ip) VALUES(?,?,?,?,?,?,?)",
                       (sha256_hex(sid), csrf, now, now + int(ttl or self.session_ttl), now, user_agent[:300], ip[:64]))
        return sid, csrf

    def get_session(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        h = sha256_hex(sid)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM admin_sessions WHERE sid_hash=?", (h,)).fetchone()
            if not row:
                return None
            if row["expires_at"] < time.time():
                db.execute("DELETE FROM admin_sessions WHERE sid_hash=?", (h,))
                return None
            if time.time() - row["last_seen_at"] > 30:
                db.execute("UPDATE admin_sessions SET last_seen_at=? WHERE sid_hash=?", (time.time(), h))
        return dict(row)

    def destroy_session(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM admin_sessions WHERE sid_hash=?", (sha256_hex(sid),))

    def destroy_all_sessions(self) -> int:
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM admin_sessions")
        return cur.rowcount

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT created_at,expires_at,last_seen_at,user_agent,ip FROM admin_sessions WHERE expires_at>? ORDER BY last_seen_at DESC",
                              (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ enrollment
    def create_enrollment(self, ttl: int = 600) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM enrollment_codes WHERE expires_at<?", (time.time(),))
            db.execute("INSERT INTO enrollment_codes(code_hash,created_at,expires_at) VALUES(?,?,?)",
                       (sha256_hex(code), time.time(), time.time() + ttl))
        self.record_event("enrollment_created", "cli")
        return code

    def consume_enrollment(self, code: str) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT expires_at,used_at FROM enrollment_codes WHERE code_hash=?", (sha256_hex(code),)).fetchone()
            if not row or row["used_at"] is not None or row["expires_at"] < time.time():
                return False
            db.execute("UPDATE enrollment_codes SET used_at=? WHERE code_hash=?", (time.time(), sha256_hex(code)))
        return True

    # ----------------------------------------------------------- maintenance
    def prune(self) -> dict[str, int]:
        now = time.time()
        with self._lock, self._connect() as db:
            out = {
                "codes": db.execute("DELETE FROM auth_codes WHERE expires_at<?", (now - 3600,)).rowcount,
                "authz_requests": db.execute("DELETE FROM authz_requests WHERE expires_at<?", (now,)).rowcount,
                "access_tokens": db.execute("DELETE FROM access_tokens WHERE expires_at<?", (now - 86400,)).rowcount,
                "refresh_tokens": db.execute("DELETE FROM refresh_tokens WHERE expires_at<? OR (revoked_at IS NOT NULL AND revoked_at<?)", (now, now - 7 * 86400)).rowcount,
                "sessions": db.execute("DELETE FROM admin_sessions WHERE expires_at<?", (now,)).rowcount,
                "enrollments": db.execute("DELETE FROM enrollment_codes WHERE expires_at<?", (now,)).rowcount,
            }
        return out

    def summary(self, *, legacy_enabled: bool) -> dict[str, Any]:
        clients = self.list_clients()
        tokens = self.list_api_tokens()
        return {
            "password_set": self.has_password(),
            "dashboard_username": self.dashboard_username(),
            "connector_key_set": self.has_connector_key(),
            "legacy_machine_token_mcp": legacy_enabled,
            "oauth_clients": len([c for c in clients if not c["revoked_at"]]),
            "active_oauth_grants": len(self.active_grants()),
            "api_tokens": len([t for t in tokens if not t["revoked_at"]]),
            "admin_sessions": len(self.list_sessions()),
            "resource_uris": self.resource_uris,
            "issuers": self.base_urls,
            "scopes": SCOPES,
        }
