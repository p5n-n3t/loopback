"""Shared secret redaction for tool output, approval presentation and support data.

The patterns mirror ``cli.redact`` but omit bare 64-hex matching, because job
output routinely contains checksums and commit hashes that are not secrets.
Redaction is a defence in depth: it never replaces path or principal isolation.
"""
from __future__ import annotations

import re
from typing import Any

MARK = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"lbs_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"lbk_[A-Za-z0-9]+_[A-Za-z0-9_\-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
]
# Patterns whose first group is kept (the label) while the value is hidden.
_LABELLED: list[re.Pattern[str]] = [
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)[^\s\"',]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=+/]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|client[_-]?secret)[\"']?\s*[=:]\s*[\"']?)[^\s\"',&]{6,}"),
    re.compile(r"(?i)(--(?:token|password|secret|api-key)[= ])[^\s\"']{6,}"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+(@)"),
]

_SECRET_KEY = re.compile(r"(?i)(token|secret|password|passwd|private_key|credential|api_key|apikey|authorization)$")


def redact_text(text: Any) -> tuple[str, int]:
    """Return ``(redacted, count)``; ``count`` is how many values were hidden."""
    out = "" if text is None else str(text)
    count = 0
    for rx in _PATTERNS:
        out, n = rx.subn(MARK, out)
        count += n
    for rx in _LABELLED:
        if rx.groups >= 2:
            out, n = rx.subn(lambda m: m.group(1) + MARK + m.group(2), out)
        else:
            out, n = rx.subn(lambda m: m.group(1) + MARK, out)
        count += n
    return out, count


def redact(text: Any) -> str:
    return redact_text(text)[0]


def redact_obj(obj: Any) -> Any:
    """Redact strings recursively and hide values stored under secret-looking keys."""
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SECRET_KEY.search(key) and value not in (None, "", False):
                result[key] = MARK
            else:
                result[key] = redact_obj(value)
        return result
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj
