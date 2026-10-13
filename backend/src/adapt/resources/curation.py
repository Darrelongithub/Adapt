"""Master prompt 4 -- resource curation: pick for *this* learner, cite only what exists.

The search layer (``adapt.resources``) already fans out to OpenAlex, arXiv, Open
Library and YouTube, ranks and de-duplicates. What prompt 4 adds on top is the
curation judgement: of the results, which four are worth *this* learner's time,
and a one-line "why" addressed to them (<=140 chars), intro-level when mastery is
low and primary/academic when it is high.

The deterministic path here is not a stub: ranking has already happened, so the
rule pick is "the ranked results, with a level-appropriate sentence each". The
model path is the upgrade, and it is held to the same rule as every other model
surface in this app -- guardrail 4 ("do not invent citations") is structural here:
a pick whose ``url`` is not one of the results it was handed is refused wholesale,
because a curator that can add a plausible-looking URL is a curator that can
recommend something nobody ever wrote.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Master prompt 4 asks for "the 4 most useful items".
PICK_LIMIT = 4
#: And caps the learner-directed sentence.
WHY_MAX_CHARS = 140

#: Response schema handed to the model with the prompt (Gemini responseSchema shape).
CURATION_SPEC: dict[str, Any] = {
    "picks": (
        "array",
        {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "url": {"type": "STRING"},
                "source": {"type": "STRING"},
                "why": {"type": "STRING"},
            },
            "required": ["title", "url", "source", "why"],
        },
    )
}

_LOW = frozenset({"new", "early", "foundation", "beginner", "low", "developing"})
_HIGH = frozenset({"secure", "advanced", "high", "strong"})


def _cap(text: str, limit: int = WHY_MAX_CHARS) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-–—") + "…"


def level_of(level_hint: str) -> str:
    """low | high | mixed -- what the curation should lean towards."""
    hint = (level_hint or "").strip().lower()
    if hint in _LOW:
        return "low"
    if hint in _HIGH:
        return "high"
    return "mixed"


def _why_for(level: str, r: Mapping[str, Any], i: int) -> str:
    """One sentence to the learner. Second person, specific to what the item is."""
    kind = str(r.get("kind") or "").strip()
    year = str(r.get("year") or "").strip()
    tail = f" ({year})" if year and year != "n.d." else ""
    kind_note = f"A {kind} " if kind and kind not in ("page",) else "This one "
    if level == "low":
        return _cap(
            f"Start here: {kind_note.rstrip()}written to be approachable — a good first pass before the denser material.{tail}"
        )
    if level == "high":
        return _cap(f"Go deep: {kind_note.rstrip()}close to the source, for checking the claim where it was made.{tail}")
    # mixed: alternate the pull, so the four picks speak to both ends honestly
    if i % 2 == 0:
        return _cap(f"Readable first pass: {kind_note.rstrip()}clear enough to start with, sourced enough to trust.{tail}")
    return _cap(f"Go deeper: {kind_note.rstrip()}for when the first one leaves you wanting the detail.{tail}")


def deterministic_picks(
    results: Sequence[Mapping[str, Any]], *, level_hint: str = "", limit: int = PICK_LIMIT
) -> list[dict[str, str]]:
    """The ranked results, capped, each with a level-appropriate why.

    Ranking and de-duplication already happened upstream, so "the first four, in
    rank order" *is* the deterministic curation; the level hint only bends the
    sentence, never the order it did not measure.
    """
    level = level_of(level_hint)
    out: list[dict[str, str]] = []
    for i, r in enumerate(results[:limit]):
        out.append(
            {
                "title": str(r.get("title") or "")[:300],
                "url": str(r.get("url") or ""),
                "source": str(r.get("provider") or r.get("source") or ""),
                "why": _why_for(level, r, i),
            }
        )
    return out


def curation_task(query: str, level_hint: str, results: Sequence[Mapping[str, Any]]) -> str:
    """The instruction half of the curation call: the raw results, as data."""
    import json

    slim = [
        {
            "title": str(r.get("title") or "")[:300],
            "url": str(r.get("url") or ""),
            "source": str(r.get("provider") or r.get("source") or ""),
            "kind": str(r.get("kind") or ""),
            "year": str(r.get("year") or ""),
            "snippet": " ".join(str(r.get("snippet") or "").split())[:160],
        }
        for r in results[:12]
    ]
    return (
        "Topic query: "
        + (query or "").strip()[:180]
        + "\nLearner mastery level: "
        + (level_hint or "unknown")
        + "\n\nRAW SEARCH RESULTS (data — curate from these only):\n"
        + json.dumps(slim, ensure_ascii=False)
        + '\n\nReturn strict JSON: {"picks": [{"title", "url", "source", "why"}]} — at most 4 picks,'
        + " each url copied exactly from the results above, each why <=140 chars and addressed to the learner."
    )


def accept_picks(
    payload: Mapping[str, Any] | None,
    *,
    results: Sequence[Mapping[str, Any]],
    limit: int = PICK_LIMIT,
) -> tuple[list[dict[str, str]] | None, str]:
    """Accept a curation only when every pick cites a result it was actually given.

    Returns ``(picks, reason)``; ``picks is None`` means "use the deterministic
    picks". The title and source are taken from the matching result row rather than
    from the model's mouth -- the model's value is the *choice* and the *why*, not
    the bibliographic record, so the record cannot be quietly rewritten either.
    """
    if not isinstance(payload, Mapping):
        return (None, "not_an_object")
    raw = payload.get("picks")
    if not isinstance(raw, list) or not (1 <= len(raw) <= limit):
        return (None, "wrong_pick_count")
    by_url = {str(r.get("url") or "").strip(): r for r in results}
    out: list[dict[str, str]] = []
    for p in raw:
        if not isinstance(p, Mapping):
            return (None, "pick_not_an_object")
        url = str(p.get("url") or "").strip()
        if not url or url not in by_url:
            return (None, f"unknown_url:{url[:80]}")
        row = by_url[url]
        why = p.get("why")
        out.append(
            {
                "title": str(row.get("title") or "")[:300],
                "url": url,
                "source": str(row.get("provider") or row.get("source") or ""),
                "why": _cap(why) if isinstance(why, str) and why.strip() else _why_for(level_of(""), row, len(out)),
            }
        )
    return (out, "accepted")
