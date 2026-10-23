"""Psychologie et Sérénité — wellbeing *reading links* only.

Verified live on 2026-10-23 (see ``docs/CONNECTORS.md``): a public,
keyless JSON API (``GET /api/articles?theme=…&lang=en``), content under
CC-BY 4.0, stated allowance 100 requests/hour without a key. It is a
single-author CBT practice site (Gildas Garrec) — general wellbeing
reading, not a clinical authority, and ADAPT treats it exactly that way:

* used **only** to offer 1-2 "Read more" article links after a check-in
  where the learner reports feeling frustrated or overloaded;
* its psychological *tests* endpoints are never called (enforced below by
  fetching the articles path only) and nothing from it scores, labels, or
  describes the learner;
* timeout 3s, cached, failure silent — a hard moment must never wait on,
  or be worsened by, a slow third party;
* no learner data is sent: the query is a fixed theme word.

Off by default (``ADAPT_CONNECTOR_WELLBEING_ENABLED=true`` to enable).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .http import Transport, guarded_get_json

HOST = "psychologieetserenite.com"
BASE = f"https://{HOST}/api/articles"
ATTRIBUTION = "Psychologie et Sérénité (CC BY 4.0)"
LICENCE = "CC BY 4.0"
TIMEOUT_S = 3.0

#: The only themes this connector may ask about — study-stress reading.
_THEMES: tuple[str, ...] = ("Anxiety",)


def _safe_url(value: Any) -> str | None:
    """An https link back to the publisher's own site, or nothing."""
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in (HOST, f"www.{HOST}"):
        return None
    if "/tests" in parsed.path or parsed.netloc.startswith("tests."):
        return None  # reading only — never link a learner to a "test"
    return url[:400]


def wellbeing_links(
    *,
    limit: int = 2,
    timeout_s: float = TIMEOUT_S,
    cache_session: Any = None,
    transport: Transport | None = None,
) -> list[dict[str, Any]]:
    """Up to ``limit`` general reading links about coping with stress.

    Returns ``[]`` on any failure. Rows carry title + link + attribution
    only; no article body is stored or shown inside ADAPT.
    """
    out: list[dict[str, Any]] = []
    for theme in _THEMES:
        payload, _source = guarded_get_json(
            connector="wellbeing",
            allowed_host=HOST,
            url=BASE,
            params={"theme": theme, "lang": "en", "limit": max(1, min(5, limit))},
            timeout_s=min(float(timeout_s), TIMEOUT_S),
            cache_session=cache_session,
            cache_ttl_hours=24.0,
            transport=transport,
        )
        articles = (payload or {}).get("articles") if isinstance(payload, dict) else None
        for row in articles or []:
            if not isinstance(row, dict):
                continue
            url = _safe_url(row.get("url"))
            title = str(row.get("title") or "").strip()
            if not url or not title:
                continue
            out.append(
                {
                    "title": title[:160],
                    "url": url,
                    "attribution": ATTRIBUTION,
                    "licence": LICENCE,
                }
            )
            if len(out) >= limit:
                return out
    return out


def health(*, timeout_s: float = TIMEOUT_S, transport: Transport | None = None) -> dict[str, Any]:
    rows = wellbeing_links(limit=1, timeout_s=timeout_s, transport=transport)
    if rows:
        return {"ok": True, "detail": "articles probe answered"}
    return {"ok": False, "detail": "no answer within 3s (network or service down)"}
