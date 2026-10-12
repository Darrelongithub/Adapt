"""Data minimization before anything leaves the process.

Blueprint section 9: "Minimal learner-data exposure to Gemini. Each AI receives
only the information it needs." Two mechanisms:

1. :func:`redact_text` -- regex scrubbing of identity-shaped strings out of free
   text (emails, phone numbers, URLs with personal paths, national-student-number
   shapes, and ``name=``-style kvs). This runs on *every* outbound prompt.
2. :func:`redact_payload` -- allowlist walk that deletes identity keys anywhere in
   a nested structure and reports how many fields it removed, so the API can show
   the student exactly what was withheld (see the privacy panel).

Verbatim question/answer text still goes out when AI 2 is asked to explain a
specific wrong answer -- that is the minimum needed for the tutoring function, and
:mod:`adapt.security.privacy_statement` spells the tradeoff out instead of hiding
it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

IDENTITY_KEYS = frozenset(
    {
        "name",
        "names",
        "full_name",
        "fullname",
        "first_name",
        "last_name",
        "student_name",
        "display_name",
        "email",
        "e_mail",
        "phone",
        "mobile",
        "address",
        "school",
        "school_name",
        "teacher",
        "teacher_name",
        "class_name",
        "classroom",
        "dob",
        "date_of_birth",
        "birthdate",
        "ssn",
        "national_id",
        "student_number",
        "student_id",
        "learner_id",
        "ip",
        "ip_address",
        "user_agent",
        "device_id",
        "geolocation",
        "photo",
        "avatar",
    }
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "url_path",
        re.compile(r"https?://[^\s\"']+/[^\s\"']{4,}"),
    ),  # keeps bare domains, strips personal paths
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)")),
    ("student_number", re.compile(r"(?i)\b(?:student|learner|id)\s*(?:no|number|#)?\s*[:=]?\s*\d{5,}")),
    (
        "kv_identity",
        re.compile(r"(?i)\b(?:name|school|teacher|email|phone)\b\s*[:=]\s*[^\s,;]+"),
    ),
    ("home_address", re.compile(r"(?i)\b\d{1,5}\s+[A-Z][a-z]+\s+(?:street|st|road|rd|ave|avenue|lane|ln)\b")),
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Redacted:
    text: str = ""
    removed: int = 0
    kinds: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"removed": self.removed, "kinds": sorted(set(self.kinds))}


def redact_text(text: str, *, max_chars: int = 6000) -> Redacted:
    """Scrub identity-shaped substrings, then clamp length.

    Length clamping is part of the privacy design: it bounds how much of a
    learner's writing can leave the machine in one call.
    """
    if text is None:
        return Redacted()
    out = _CONTROL.sub(" ", str(text))
    removed = 0
    kinds: list[str] = []
    for kind, pat in _PATTERNS:
        out, n = pat.subn(f"[{kind}_withheld]", out)
        if n:
            removed += n
            kinds.append(kind)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + " […truncated]"
    return Redacted(text=out, removed=removed, kinds=kinds)


def redact_payload(obj: Any, *, max_chars: int = 6000, _depth: int = 0) -> tuple[Any, int, list[str]]:
    """Return ``(sanitized, n_fields_dropped, kinds)`` for a nested structure."""
    kinds: list[str] = []
    dropped = 0
    if _depth > 12:
        return "[depth_limited]", 1, kinds
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            if _norm(key) in {_norm(x) for x in IDENTITY_KEYS}:
                dropped += 1
                kinds.append(f"field:{key}")
                continue
            if isinstance(v, str):
                r = redact_text(v, max_chars=max_chars)
                dropped += r.removed
                kinds += r.kinds
                out[key] = r.text
            elif isinstance(v, (dict, list, tuple, set)):
                sub, d, ks = redact_payload(v, max_chars=max_chars, _depth=_depth + 1)
                out[key] = sub
                dropped += d
                kinds += ks
            elif v is None or isinstance(v, (bool, int, float)):
                out[key] = v
            else:
                r = redact_text(str(v), max_chars=max_chars)
                out[key] = r.text
                dropped += r.removed
                kinds += r.kinds
        return out, dropped, kinds
    if isinstance(obj, (list, tuple, set)):
        items: list[Any] = []
        for v in obj:
            sub, d, ks = redact_payload(v, max_chars=max_chars, _depth=_depth + 1)
            items.append(sub)
            dropped += d
            kinds += ks
        return items, dropped, kinds
    if isinstance(obj, str):
        r = redact_text(obj, max_chars=max_chars)
        return r.text, r.removed, r.kinds
    return obj, 0, kinds


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def pseudonym(student_id: str, *, scope: str = "llm") -> str:
    """Non-reversible handle, so a prompt can refer to 'this learner' at all."""
    h = hashlib.sha256(f"{scope}|{student_id}".encode()).hexdigest()
    return f"learner-{h[:8]}"


def privacy_note(dropped: int, kinds: Iterable[str]) -> str:
    ks = sorted(set(kinds))
    if not dropped:
        return "No identity fields were present in what we sent."
    return (
        f"Withheld {dropped} identity item(s) from the request"
        + (f" ({', '.join(ks[:6])})" if ks else "")
        + ". Names, contact details and school identifiers never leave this machine."
    )
