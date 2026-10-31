"""Test seams for the later isolated clean-install lab (LAB-ROADMAP.md, "Prepare now").

Nothing here touches the network, a VM tool or a production resource. The module offers:

* a versioned run manifest with a validator and a secret guard (credentials are references,
  never values);
* an ``OwnedResources`` registry so cleanup is idempotent, limited to the run that created the
  resources, bounded by a timeout and recoverable after an interrupted run;
* a deterministic pairwise matrix generator plus explicit risk cases;
* ``replay_runner``, a ``scan.Runner`` that answers from a recorded, sanitized fixture.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import redaction
from scan import CommandResult, Runner

SCHEMA_VERSION = 1
OUTCOMES = ("PASS", "EXPECTED DENIAL", "FAIL", "BLOCKED", "UNAVAILABLE", "NOT TESTED", "UNVERIFIED")
INPUT_MODES = ("interactive", "settings")
DISTRIBUTIONS = ("checkout", "release", "npm", "powershell")
PRIVATE_PROVIDERS = ("none", "lan", "tailscale", "mesh")
INGRESS_PROVIDERS = ("local", "tailscale-serve", "tailscale-funnel", "cloudflare", "ngrok")
CLEANUP_STATUSES = ("pending", "running", "clean", "residual", "interrupted")
MANIFEST_KEYS = ("schema_version", "run_id", "source_commit", "image_digest", "platform", "install",
                 "providers", "policy_profile", "checks", "owned_resource_refs", "cleanup")
MAX_OUTPUT = 4096
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
CHECK_ID = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- manifest
def new_manifest(run_id: str, *, source_commit: str, platform: dict[str, str], input_mode: str,
                 distribution: str, private: str, primary_ingress: str, policy_profile: str,
                 image_digest: str | None = None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "source_commit": source_commit,
        "image_digest": image_digest,
        "platform": {"os": platform.get("os"), "arch": platform.get("arch")},
        "install": {"input_mode": input_mode, "distribution": distribution},
        "providers": {"private": private, "primary_ingress": primary_ingress},
        "policy_profile": policy_profile,
        "checks": [],
        "owned_resource_refs": [],
        "cleanup": {"status": "pending", "residual_refs": []},
    }
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("invalid manifest: " + "; ".join(errors))
    return manifest


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _scan_secrets(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        # Redacted text is a fixed point of redaction, so the marker itself is not a leak.
        if redaction.redact_text(value)[0] != value:
            errors.append(f"secret-like value at {path}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _scan_secrets(item, f"{path}.{key}", errors)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_secrets(item, f"{path}[{index}]", errors)


def _str_list(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(f"{path} must be a list of strings")


def _enum_field(obj: dict[str, Any], key: str, allowed: tuple[str, ...], path: str, errors: list[str]) -> None:
    if obj.get(key) not in allowed:
        errors.append(f"{path}.{key} must be one of {', '.join(allowed)}")


def _validate_check(check: Any, path: str, errors: list[str]) -> None:
    if not isinstance(check, dict):
        errors.append(f"{path} must be an object")
        return
    cid = check.get("id")
    if not isinstance(cid, str) or not CHECK_ID.match(cid):
        errors.append(f"{path}.id must be dotted lowercase")
    if check.get("outcome") not in OUTCOMES:
        errors.append(f"{path}.outcome must be one of {', '.join(OUTCOMES)}")
    if "evidence" not in check:
        errors.append(f"{path}.evidence is required")
    else:
        _str_list(check["evidence"], f"{path}.evidence", errors)
    if "duration_ms" in check and check["duration_ms"] is not None and (
            not _is_int(check["duration_ms"]) or check["duration_ms"] < 0):
        errors.append(f"{path}.duration_ms must be an integer >= 0")
    if "exit_status" in check and check["exit_status"] is not None and not _is_int(check["exit_status"]):
        errors.append(f"{path}.exit_status must be an integer")
    if "output" in check:
        out = check["output"]
        if not isinstance(out, str):
            errors.append(f"{path}.output must be a string")
        elif len(out) > MAX_OUTPUT:
            errors.append(f"{path}.output exceeds {MAX_OUTPUT} characters")
    extra = set(check) - {"id", "outcome", "evidence", "duration_ms", "exit_status", "output"}
    if extra:
        errors.append(f"{path} has unknown keys: {', '.join(sorted(extra))}")


def validate_manifest(data: Any) -> list[str]:
    """Return a list of problems; an empty list means the manifest is valid."""
    if not isinstance(data, dict):
        return ["manifest must be an object"]
    errors: list[str] = []
    for key in MANIFEST_KEYS:
        if key not in data:
            errors.append(f"missing key: {key}")
    for key in sorted(set(data) - set(MANIFEST_KEYS)):
        errors.append(f"unknown key: {key}")
    if "schema_version" in data and data["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if "run_id" in data and (not isinstance(data["run_id"], str) or not RUN_ID.match(data["run_id"])):
        errors.append("run_id must match ^[a-z0-9][a-z0-9-]{2,62}$")
    if "source_commit" in data and (not isinstance(data["source_commit"], str) or not data["source_commit"]):
        errors.append("source_commit must be a non-empty string")
    if "image_digest" in data and data["image_digest"] is not None and not isinstance(data["image_digest"], str):
        errors.append("image_digest must be a string or null")
    if "policy_profile" in data and (not isinstance(data["policy_profile"], str) or not data["policy_profile"]):
        errors.append("policy_profile must be a non-empty string")
    for key, fields in (("platform", ("os", "arch")), ("install", ("input_mode", "distribution")),
                        ("providers", ("private", "primary_ingress"))):
        if key not in data:
            continue
        obj = data[key]
        if not isinstance(obj, dict):
            errors.append(f"{key} must be an object")
            continue
        for field in fields:
            if field in ("os", "arch"):
                if not isinstance(obj.get(field), str) or not obj.get(field):
                    errors.append(f"{key}.{field} must be a non-empty string")
        if key == "install":
            _enum_field(obj, "input_mode", INPUT_MODES, key, errors)
            _enum_field(obj, "distribution", DISTRIBUTIONS, key, errors)
        if key == "providers":
            _enum_field(obj, "private", PRIVATE_PROVIDERS, key, errors)
            _enum_field(obj, "primary_ingress", INGRESS_PROVIDERS, key, errors)
    if "checks" in data:
        if not isinstance(data["checks"], list):
            errors.append("checks must be a list")
        else:
            for i, check in enumerate(data["checks"]):
                _validate_check(check, f"checks[{i}]", errors)
    if "owned_resource_refs" in data:
        _str_list(data["owned_resource_refs"], "owned_resource_refs", errors)
    if "cleanup" in data:
        cleanup = data["cleanup"]
        if not isinstance(cleanup, dict):
            errors.append("cleanup must be an object")
        else:
            _enum_field(cleanup, "status", CLEANUP_STATUSES, "cleanup", errors)
            _str_list(cleanup.get("residual_refs"), "cleanup.residual_refs", errors)
    _scan_secrets(data, "$", errors)
    return errors


def record_check(manifest: dict[str, Any], check_id: str, outcome: str, *, evidence: Iterable[str] = (),
                 exit_status: int | None = None, output: str = "",
                 duration_ms: int | None = None) -> dict[str, Any]:
    """Append (or replace by id) a check; output is redacted and clipped to 4096 characters."""
    check: dict[str, Any] = {
        "id": check_id, "outcome": outcome, "evidence": [str(e) for e in evidence],
        "output": redaction.redact(output)[:MAX_OUTPUT],
    }
    if exit_status is not None:
        check["exit_status"] = exit_status
    if duration_ms is not None:
        check["duration_ms"] = duration_ms
    errors: list[str] = []
    _validate_check(check, "check", errors)
    if errors:
        raise ValueError("; ".join(errors))
    checks = manifest.setdefault("checks", [])
    for i, existing in enumerate(checks):
        if existing.get("id") == check_id:
            checks[i] = check
            break
    else:
        checks.append(check)
    return check


# --------------------------------------------------------------------------- owned resources
_REQUIRED = object()


def _sanitize_error(text: Any) -> str:
    return redaction.redact(str(text)).strip().replace("\n", " ")[:300]


class OwnedResources:
    """JSON registry of resources a lab run created, so cleanup touches only those."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    # -- persistence
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("cleanup", {"status": "idle"})
        data.setdefault("resources", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- public API
    def add(self, kind: str, ref: str, cleanup_argv: list[str], *, run_id: str,
            now: Callable[[], float] = time.time) -> bool:
        """Register a resource. Returns False when kind+ref is already registered."""
        if not isinstance(kind, str) or not KIND.match(kind):
            raise ValueError("invalid resource kind")
        if not isinstance(ref, str) or not ref or len(ref) > 256:
            raise ValueError("invalid resource ref")
        if not isinstance(run_id, str) or not RUN_ID.match(run_id):
            raise ValueError("invalid run_id")
        if (not isinstance(cleanup_argv, list) or not cleanup_argv
                or not all(isinstance(a, str) and a for a in cleanup_argv)):
            raise ValueError("cleanup_argv must be a non-empty list of strings")
        data = self._load()
        for res in data["resources"]:
            if res["kind"] == kind and res["ref"] == ref:
                return False
        data["resources"].append({"kind": kind, "ref": ref, "cleanup_argv": list(cleanup_argv),
                                  "run_id": run_id, "added_at": now()})
        self._save(data)
        return True

    def list(self, run_id: Any = _REQUIRED) -> list[dict[str, Any]]:
        items = self._load()["resources"]
        if run_id is not _REQUIRED and run_id is not None:
            items = [r for r in items if r["run_id"] == run_id]
        return copy.deepcopy(items)

    def status(self) -> str:
        """'idle', or 'interrupted' when a previous cleanup stopped while 'running'."""
        state = self._load()["cleanup"].get("status", "idle")
        return "interrupted" if state == "running" else state

    def cleanup(self, runner: Runner, *, run_id: Any = _REQUIRED, timeout: float = 60,
                now: Callable[[], float] = time.time) -> dict[str, Any]:
        """Run cleanup_argv newest-first. ``run_id=None`` explicitly selects every run."""
        if run_id is _REQUIRED:
            raise TypeError("cleanup() requires run_id (use run_id=None to clean every run)")
        data = self._load()
        resumed = data["cleanup"].get("status") == "running"
        data["cleanup"] = {"status": "running", "started_at": now(), "resumed": resumed}
        self._save(data)
        removed: list[str] = []
        residual: list[dict[str, str]] = []
        targets = [r for r in data["resources"] if run_id is None or r["run_id"] == run_id]
        # Registry is append-only, so reversed() is newest-first even when timestamps tie.
        order = list(reversed(targets))
        for res in order:
            error = ""
            try:
                result = runner(list(res["cleanup_argv"]), timeout)
                if result.timed_out:
                    error = "cleanup timed out"
                elif result.returncode != 0:
                    error = _sanitize_error(result.stderr or result.stdout or f"exit {result.returncode}")
            except Exception as exc:  # a failing provider command must not stop other cleanups
                error = _sanitize_error(f"{type(exc).__name__}: {exc}")
            if error:
                res["last_error"] = error
                residual.append({"kind": res["kind"], "ref": res["ref"], "error": error})
            else:
                data["resources"].remove(res)
                removed.append(res["ref"])
            self._save(data)
        data["cleanup"] = {"status": "idle", "finished_at": now()}
        self._save(data)
        return {"status": "residual" if residual else "clean", "removed": removed, "residual": residual}


def finish_manifest(manifest: dict[str, Any], registry_result: dict[str, Any]) -> dict[str, Any]:
    """Record owned resources and cleanup outcome in the manifest."""
    residual = [f"{r['kind']}:{r['ref']}" for r in registry_result.get("residual", [])]
    manifest["owned_resource_refs"] = sorted(set(manifest.get("owned_resource_refs", []))
                                             | set(registry_result.get("removed", [])) | set(residual))
    manifest["cleanup"] = {"status": registry_result["status"], "residual_refs": residual}
    return manifest


# --------------------------------------------------------------------------- matrix
LAB_AXES: dict[str, list[str]] = {
    "input": ["interactive", "settings"],
    "distribution": ["checkout", "release", "npm", "powershell"],
    "machine": ["linux-x86_64", "linux-arm64", "macos", "windows", "unsupported"],
    "privilege": ["user-service", "system-service", "no-privilege", "no-service-manager"],
    "private": ["none", "lan", "tailscale-existing", "tailscale-new", "mesh-existing", "mesh-new", "ssh-external"],
    "ingress": ["local", "private-serve", "cloudflare", "ngrok-fixed", "ngrok-ephemeral"],
    "policy": ["restricted", "custom-reviewed", "admin", "sandbox-unavailable"],
    "fleet": ["none", "alias", "visible-peer", "no-worker", "existing-worker", "unreachable"],
    "lifecycle": ["install", "rerun", "update-same", "update-new", "failed-migration", "rollback", "uninstall"],
}

# Failure injection is deliberately outside the pairwise axes; these combinations are named risks.
RISK_CASES: list[dict[str, str]] = [
    {"name": "mesh-tailscale-route-overlap", "input": "settings", "distribution": "release",
     "machine": "linux-x86_64", "privilege": "system-service", "private": "mesh-new", "ingress": "private-serve",
     "policy": "restricted", "fleet": "visible-peer", "lifecycle": "install",
     "note": "Tailscale and Mesh advertise overlapping routes on one host"},
    {"name": "ngrok-ephemeral-oauth-url-change", "input": "settings", "distribution": "checkout",
     "machine": "linux-arm64", "privilege": "user-service", "private": "none", "ingress": "ngrok-ephemeral",
     "policy": "restricted", "fleet": "none", "lifecycle": "rerun",
     "note": "public URL changes between runs while OAuth clients hold the old issuer"},
    {"name": "interrupted-install", "input": "interactive", "distribution": "release",
     "machine": "linux-x86_64", "privilege": "user-service", "private": "lan", "ingress": "local",
     "policy": "restricted", "fleet": "none", "lifecycle": "failed-migration",
     "note": "process killed mid-install; rerun and rollback must recover owned resources only"},
    {"name": "required-sandbox-unavailable", "input": "settings", "distribution": "release",
     "machine": "linux-x86_64", "privilege": "no-privilege", "private": "none", "ingress": "local",
     "policy": "sandbox-unavailable", "fleet": "none", "lifecycle": "install",
     "note": "kernel sandbox missing; install must refuse rather than weaken the policy"},
    {"name": "unsupported-machine-refusal", "input": "interactive", "distribution": "npm",
     "machine": "unsupported", "privilege": "no-service-manager", "private": "none", "ingress": "local",
     "policy": "restricted", "fleet": "none", "lifecycle": "install",
     "note": "unsupported OS must be refused with a clear message"},
]


def pairwise_matrix(axes: dict[str, list[str]]) -> list[dict[str, str]]:
    """Deterministic greedy all-pairs rows: every value pair of any two axes appears in some row."""
    names = sorted(axes)
    values = {n: list(axes[n]) for n in names}
    uncovered: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for a, b in itertools.combinations(names, 2):
        for va in values[a]:
            for vb in values[b]:
                uncovered.add(((a, va), (b, vb)))
    rows: list[dict[str, str]] = []
    if not uncovered and names:
        return [{n: values[n][0] for n in names}]
    while uncovered:
        (a, va), (b, vb) = min(uncovered)
        row = {a: va, b: vb}
        for name in names:
            if name in row:
                continue
            best, best_score = values[name][0], -1
            for candidate in values[name]:
                score = 0
                for other, ov in row.items():
                    pair = ((name, candidate), (other, ov)) if name < other else ((other, ov), (name, candidate))
                    score += pair in uncovered
                if score > best_score:
                    best, best_score = candidate, score
            row[name] = best
        for x, y in itertools.combinations(sorted(row), 2):
            uncovered.discard(((x, row[x]), (y, row[y])))
        rows.append({n: row[n] for n in names})
    rows.sort(key=lambda r: tuple(r[n] for n in names))
    return rows


# --------------------------------------------------------------------------- replay
class _ReplayRunner:
    def __init__(self, fixture: dict[str, dict[str, Any]]):
        self.fixture = fixture
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], timeout: float, stdin: str | None = None) -> CommandResult:
        key = " ".join(argv)
        self.calls.append({"argv": list(argv), "timeout": timeout, "stdin": stdin})
        rec = self.fixture.get(key)
        if rec is None:
            return CommandResult(127, "", "not recorded")
        return CommandResult(int(rec.get("returncode", 0)), str(rec.get("stdout", "")), str(rec.get("stderr", "")),
                             bool(rec.get("timed_out", False)))


def replay_runner(fixture: dict[str, dict[str, Any]]) -> _ReplayRunner:
    """Scan-style runner answering from sanitized recorded output; every call lands in ``.calls``."""
    return _ReplayRunner(fixture)
