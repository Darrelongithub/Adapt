"""Wikipedia "Outline of …" pages as fallback course outlines.

Verified live on 2026-10-23 (see ``docs/CONNECTORS.md``): the Wikimedia
Action API needs no key, ``action=parse&prop=tocdata`` on an outline page
returns its section headings in order, and text is CC BY-SA 4.0 (attribution
shown wherever the titles appear). Only *headings* are read — an outline of
what the subject contains, never article prose for lessons.

This is the third-choice scanner source, behind the local syllabus map and
Oak, and it is off until an operator turns it on
(``ADAPT_CONNECTOR_WIKIMEDIA_ENABLED=true``): a default install makes no new
outbound calls.
"""

from __future__ import annotations

import re
from typing import Any

from .http import Transport, guarded_get_json

HOST = "en.wikipedia.org"
BASE = f"https://{HOST}/w/api.php"
LICENCE = "CC BY-SA 4.0"

#: Headings that are furniture, not curriculum.
_SKIP = frozenset(
    {
        "see also",
        "references",
        "external links",
        "notes",
        "further reading",
        "bibliography",
        "contents",
    }
)
_TAG = re.compile(r"<[^>]+>")


def _outline_page(subject: str) -> str:
    text = " ".join(str(subject or "").split()).strip()
    return "Outline of " + text[:80]


def attribution(page_title: str) -> str:
    return f'Wikipedia contributors, "{page_title}" (CC BY-SA 4.0)'


def fetch_objectives(
    *,
    subject: str,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    transport: Transport | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Section headings of ``Outline of <subject>`` as objectives, or ``[]``."""
    page = _outline_page(subject)
    if len(page) <= len("Outline of "):
        return []
    payload, _source = guarded_get_json(
        connector="wikimedia",
        allowed_host=HOST,
        url=BASE,
        params={
            "action": "parse",
            "page": page,
            "prop": "tocdata",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        },
        timeout_s=timeout_s,
        cache_session=cache_session,
        transport=transport,
    )
    if not isinstance(payload, dict):
        return []
    parse = payload.get("parse") or {}
    toc = (parse.get("tocdata") or {}).get("sections") or []
    title = str(parse.get("title") or page)
    out: list[dict[str, Any]] = []
    strand = subject
    for section in toc:
        if not isinstance(section, dict):
            continue
        line = _TAG.sub("", str(section.get("line") or "")).strip()
        level = int(section.get("tocLevel") or 99) if str(section.get("tocLevel") or "").isdigit() else 99
        if not line or line.lower() in _SKIP or level > 2:
            continue
        if level == 1:
            strand = line
            continue  # a strand heading names the group; its children are the objectives
        out.append(
            {
                "title": line[:160],
                "strand": str(strand)[:80],
                "level": "",
                "source": "wikimedia",
                "source_ref": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}#{str(section.get('anchor') or '')}"[:240],
                "attribution": attribution(title),
                "licence": LICENCE,
            }
        )
        if len(out) >= limit:
            break
    return out


def health(*, timeout_s: float = 8.0, transport: Transport | None = None) -> dict[str, Any]:
    payload, _source = guarded_get_json(
        connector="wikimedia",
        allowed_host=HOST,
        url=BASE,
        params={"action": "query", "meta": "siteinfo", "format": "json", "formatversion": "2"},
        timeout_s=timeout_s,
        transport=transport,
    )
    if payload is None:
        return {"ok": False, "detail": "no answer (network or service down)"}
    return {"ok": True, "detail": "siteinfo probe answered"}
