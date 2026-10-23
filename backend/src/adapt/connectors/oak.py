"""Oak National Academy — UK curriculum units as candidate objectives.

Verified live on 2026-10-23 (see ``docs/CONNECTORS.md``): base
``https://open-api.thenational.academy/api/v1``, ``Authorization: Bearer
<key>`` (free key, requested out of band; keys do not expire), content under
the Open Government Licence, 1000 requests/hour per key, and
``GET /rate-limit`` as a health probe that does not spend the allowance.
Without a key the API answers ``401 {"code":"UNAUTHORIZED"}`` — which is
exactly why this connector is *enabled when a key exists* and silent
otherwise. Kenya (CBC/KCSE/IGCSE) is ADAPT's primary audience, so Oak is a
secondary outline source behind the local syllabus map.

Only unit/lesson *titles* are read (what the curriculum requires, in order).
Lesson bodies, transcripts and quiz answers are never fetched: lessons are
written by AI 3 from the approved objectives, not copied.
"""

from __future__ import annotations

from typing import Any

from .http import Transport, guarded_get_json

HOST = "open-api.thenational.academy"
BASE = f"https://{HOST}/api/v1"
ATTRIBUTION = "Oak National Academy curriculum (Open Government Licence v3.0)"
LICENCE = "Open Government Licence v3.0"

#: ADAPT curriculum/grade words -> Oak key stage slugs. Oak serves key
#: stages 1-4 (ages ~5-16); anything older maps to ks4, the closest outline.
_KEY_STAGES = ("ks1", "ks2", "ks3", "ks4")

#: Learner subject words -> Oak subject slugs (from GET /subjects; the API's
#: own vocabulary). Unknown words return no objectives rather than a guess.
_SUBJECT_SLUGS: dict[str, str] = {
    "maths": "maths",
    "mathematics": "maths",
    "math": "maths",
    "geometry": "maths",
    "algebra": "maths",
    "english": "english",
    "science": "science",
    "biology": "science",
    "chemistry": "science",
    "physics": "science",
    "computing": "computing",
    "computer science": "computing",
    "coding": "computing",
    "history": "history",
    "geography": "geography",
    "religious education": "religious-education",
    "design technology": "design-technology",
    "art": "art",
    "music": "music",
}


def subject_slug(subject: str) -> str | None:
    text = " ".join(str(subject or "").lower().split())
    if text in _SUBJECT_SLUGS:
        return _SUBJECT_SLUGS[text]
    for word, slug in _SUBJECT_SLUGS.items():
        if word in text:
            return slug
    return None


def key_stage_for(grade: str | None, curriculum: str | None) -> str:
    """Best Oak key stage for a grade/year word. Defaults to ks3 (11-14)."""
    text = f"{grade or ''} {curriculum or ''}".lower()
    digits = "".join(ch for ch in text if ch.isdigit())
    year = int(digits) if digits else None
    if year is not None:
        if year <= 2:
            return "ks1"
        if year <= 6:
            return "ks2"
        if year <= 9:
            return "ks3"
        return "ks4"
    if "form" in text or "kcse" in text or "igcse" in text or "senior" in text:
        return "ks4"
    return "ks3"


def _headers(api_key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {api_key}"}


def fetch_objectives(
    *,
    api_key: str,
    subject: str,
    grade: str | None = None,
    curriculum: str | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Unit titles for one subject/key stage, as scanner objectives.

    Returns ``[]`` on any failure. Each row keeps Oak's unit slug as
    ``source_ref`` so the title stays traceable to the exact unit it came
    from, and the OGL attribution travels with every row.
    """
    slug = subject_slug(subject)
    if not slug or not api_key:
        return []
    key_stage = key_stage_for(grade, curriculum)
    payload, _source = guarded_get_json(
        connector="oak",
        allowed_host=HOST,
        url=f"{BASE}/key-stages/{key_stage}/subject/{slug}/units",
        headers=_headers(api_key),
        params={"limit": max(1, min(100, limit)), "offset": 0},
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("units", "results", "data", "items"):
            if isinstance(payload.get(key), list):
                rows = payload[key]  # type: ignore[assignment]
                break
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("unitTitle") or row.get("title") or "").strip()
        unit_slug = str(row.get("unitSlug") or row.get("slug") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title[:160],
                "strand": str(row.get("subjectTitle") or subject)[:80],
                "level": key_stage,
                "source": "oak",
                "source_ref": unit_slug[:120] or f"{key_stage}/{slug}",
                "attribution": ATTRIBUTION,
                "licence": LICENCE,
            }
        )
        if len(out) >= limit:
            break
    return out


def health(*, api_key: str, timeout_s: float = 8.0, transport: Transport | None = None) -> dict[str, Any]:
    """``GET /rate-limit`` — documented as not counting against the allowance."""
    if not api_key:
        return {"ok": False, "detail": "no key configured (adapt config set-key --service oak)"}
    payload, _source = guarded_get_json(
        connector="oak",
        allowed_host=HOST,
        url=f"{BASE}/rate-limit",
        headers=_headers(api_key),
        timeout_s=timeout_s,
        transport=transport,
    )
    if payload is None:
        return {"ok": False, "detail": "no answer (network, key rejected, or service down)"}
    return {"ok": True, "detail": "rate-limit probe answered", "rate_limit": payload if isinstance(payload, dict) else {}}
