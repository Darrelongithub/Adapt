"""The team-supplied syllabus map: scanner source (1), fully local.

The file (``data/syllabus_map.json``) holds strand/sub-strand *titles*
compiled from public KICD/KNEC material with its provenance and compile
date stated inside it; the UI shows that provenance wherever the titles
appear. Matching is deliberately dull: same curriculum beats any, exact
subject word beats substring, and no match means the scanner moves on to
the next source — never a best-guess syllabus.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_MAP_PATH = Path(__file__).resolve().parent / "data" / "syllabus_map.json"


@lru_cache(maxsize=1)
def load_map() -> dict[str, Any]:
    try:
        return json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:  # a missing/corrupt map degrades to "no local source"
        return {"version": 0, "compiled_at": "", "provenance": "", "entries": []}


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split())


def find_entry(subject: str, curriculum: str | None) -> dict[str, Any] | None:
    """The best map entry for (subject, curriculum), or None.

    Scoring: a subject keyword must match (word-boundary). An entry whose
    curricula list contains the requested curriculum beats one that does
    not; a longer (more specific) keyword beats a shorter one, so
    "integrated science" wins over "science".
    """
    wanted = f" {_normalise(subject)} "
    best: tuple[int, dict[str, Any]] | None = None
    for entry in load_map().get("entries") or []:
        matched = 0
        for keyword in entry.get("subject_keywords") or []:
            if f" {_normalise(str(keyword))} " in wanted:
                matched = max(matched, len(str(keyword)))
        if not matched:
            continue
        score = matched + (100 if curriculum and curriculum in (entry.get("curricula") or []) else 0)
        # A subject-specific entry (geometry) beats the parent subject's
        # entry (mathematics) when the learner named the specific thing.
        if _normalise(subject) in [_normalise(str(k)) for k in entry.get("subject_keywords") or []]:
            score += 10
        if best is None or score > best[0]:
            best = (score, entry)
    return best[1] if best else None


def entry_objectives(entry: dict[str, Any], *, grade: str | None = None) -> list[dict[str, Any]]:
    """Flatten an entry's strands into ordered objectives with stable ids."""
    out: list[dict[str, Any]] = []
    for block in entry.get("strands") or []:
        strand = str(block.get("strand") or "")
        for title in block.get("objectives") or []:
            out.append(
                {
                    "id": f"{entry.get('id', 'map')}-{len(out) + 1:02d}",
                    "title": str(title)[:160],
                    "strand": strand[:80],
                    "level": str(grade or "")[:24],
                    "source": str(entry.get("source") or "kicd_map"),
                    "source_ref": str(entry.get("source_ref") or "")[:240],
                    "origin": "syllabus" if entry.get("source") == "kicd_map" else "outline",
                    "attribution": str(entry.get("attribution") or "")[:240],
                    "licence": str(entry.get("licence") or "")[:120],
                }
            )
    return out


def map_provenance() -> dict[str, str]:
    data = load_map()
    return {
        "compiled_at": str(data.get("compiled_at") or ""),
        "provenance": str(data.get("provenance") or ""),
    }
