"""The mandatory code check before any learner sees a generated item (§7.3).

§7.3, verbatim: "**Validation (code, mandatory before any learner sees an item):**

* Maths and computational content: verify keys by computing (extend ``assessment/generated.py`` with a
  symbolic checker, e.g. SymPy, for calculus and algebra; keep the existing ``safe_arithmetic`` approach
  for simple items).
* Non-computational subjects: the answer must be quotable from supplied or retrieved source text, or
  from a second independent model pass that agrees; otherwise reject or flag \"unverified\" and never
  use it for scoring.
* Reject items with unrendered placeholders, multiple correct options, answer leaks in the stem, or
  difficulty far from its declared band; log every rejection with a reason."

**What this module does not apply.** ``assessment.generated.validate_generated`` carries two families
of rule, and only one of them is §7.3's:

* rules about whether a *learner may see* an item — placeholders, a key that matches two options, an
  answer printed in the stem, a difficulty outside its declared band. Those are here, and they block.
* rules about whether an item may be *inserted into the bank* — a stem already in the bank for that
  topic, every distractor carrying a ``why_wrong`` reason. Those are bank-insertion rules: they keep
  one question from being counted twice and keep a distractor explainable. An authored chain kit task
  is neither new to the bank nor missing its explanations (it has none per option, by design, and the
  ladder has always served it that way), so applying them here would reject content the app already
  serves and would be a gate that fails on material nobody asked it to police.

The split is named by :func:`validate_for_bank`, which adds the bank rules for the one caller that
actually inserts rows. Nothing is weakened: both functions run the full §7.3 list, and
:data:`GATE_CODES` is the set that blocks a learner from seeing an item.

**Every rejection carries a reason** — the brief asks for it, and it is what makes a rejection
actionable rather than a red build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..assessment import generated as G
from . import symbolic as S

__all__ = [
    "GATE_CODES",
    "Rejection",
    "Verdict",
    "validate_item",
    "validate_for_bank",
    "validate_pack",
]

#: The §7.3 codes that stop an item being shown. Anything not in this set is a note.
GATE_CODES: frozenset[str] = frozenset(
    {
        "placeholder",
        "multiple_correct_options",
        "answer_leak_in_stem",
        "difficulty_far_from_band",
        "key_disagrees",
        "no_key",
        "kind_unsupported",
    }
)

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|\[(?:TODO|TBD|XXX|placeholder)\b|\{[a-zA-Z_]\w*\}", re.IGNORECASE)

#: Spans a question quotes *as material to analyse* — a sentence the learner is asked about, a
#: passage, a phrase. A key that appears inside one of these is not a leak: "in the sentence
#: 'Wanafunzi wazuri wanasoma kwa bidii', which word is the adjective?" necessarily contains its own
#: answer, and a rule that rejected it would reject every "find the part" question in the app. A key
#: that appears in the stem's *own* words is a leak, and still is.
_QUOTED = re.compile("['\u2018\u201c\"].*?['\u2019\u201d\"]", re.DOTALL)


def _unquoted(text: str) -> str:
    """The stem with every quoted span removed — the part the question says in its own voice."""
    return _QUOTED.sub(" ", str(text or ""))

#: The floor on a declared difficulty, quoted from ``assessment.selector.MIN_EXPECTED_SUCCESS``
#: (0.45): §4.1 item 7's rule is that nothing under 45% expected success is queued unless it is a
#: scaffolded remediation item with a worked example shown. That is the repo's own number, so a
#: generated item and a practice item are held to the same floor rather than to one invented here.
DIFFICULTY_FLOOR = G.MIN_EXPECTED_SUCCESS if hasattr(G, "MIN_EXPECTED_SUCCESS") else 0.45


@dataclass
class Rejection:
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass
class Verdict:
    ok: bool
    verification: str = "unverified"
    scorable: bool = False
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "verification": self.verification,
            "scorable": self.scorable,
            "rejections": [r.as_dict() for r in self.rejections],
            "notes": list(self.notes),
            "checks": list(self.checks),
        }


def _stem_and_choices(item: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    stem = str(item.get("stem") or "").strip()
    raw = item.get("choices") or []
    choices = [c for c in raw if isinstance(c, Mapping)] if isinstance(raw, list) else []
    return stem, choices


def _correct_texts(item: Mapping[str, Any], choices: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in choices:
        if c.get("correct"):
            out.append(str(c.get("text") or "").strip())
    spec = item.get("answer_spec") or {}
    index = spec.get("correct_index")
    if isinstance(index, int) and isinstance(choices, list) and 0 <= index < len(choices):
        out.append(str(choices[index].get("text") or "").strip())
    values = spec.get("values") or spec.get("value")
    if isinstance(values, list):
        out.extend(str(v).strip() for v in values if str(v or "").strip())
    elif values not in (None, ""):
        out.append(str(values).strip())
    seen: list[str] = []
    for text in out:
        if text and text not in seen:
            seen.append(text)
    return seen


def _difficulty_ok(item: Mapping[str, Any], verdict: Verdict) -> None:
    """A declared difficulty must sit inside the practice band; an absent one is a note, not a rejection.

    The floor is the repo's own 45% rule (``selector.MIN_EXPECTED_SUCCESS``) and it is a *rejection*
    below that: an item declared easier than the floor is either mislabelled or is the "practising
    failure" case §4.1 item 7 exists to stop. Above the target band it is a *note*, not a rejection,
    because keeping hard items out of the queue is ``selector.enforce_target_band``'s job — that
    function sees the learner's belief and this one sees only the label. A generated item with a
    declared difficulty of 0.9 is not wrong; it is an item the selector will not pick yet, and saying
    so here would be this module doing the selector's job with a tenth of its information.

    A scaffolded remediation item is exempt from the floor, which is the brief's own carve-out: the
    worked example is shown with it, so the item is not being attempted cold.
    """
    declared = item.get("difficulty")
    if declared is None:
        verdict.notes.append("no declared difficulty, so the band rule has nothing to check")
        return
    try:
        value = float(declared)
    except (TypeError, ValueError):
        verdict.rejections.append(
            Rejection("difficulty_far_from_band", f"difficulty {declared!r} is not a number")
        )
        return
    if not 0.0 <= value <= 1.0:
        verdict.rejections.append(
            Rejection("difficulty_far_from_band", f"difficulty {value} is outside [0, 1]")
        )
        return
    if value < DIFFICULTY_FLOOR:
        scaffolded = bool(
            item.get("solution_steps")
            or item.get("worked_example")
            or "scaffolded" in [str(t) for t in (item.get("tags") or [])]
        )
        if not scaffolded:
            verdict.rejections.append(
                Rejection(
                    "difficulty_far_from_band",
                    f"difficulty {value} is below the {DIFFICULTY_FLOOR} floor and no worked example "
                    "is shown with it, so a learner would be practising failure",
                )
            )
        else:
            verdict.notes.append(
                f"difficulty {value} is below the {DIFFICULTY_FLOOR} floor but a worked example is shown with it"
            )
    elif value > 0.85:
        verdict.notes.append(
            f"difficulty {value} is above the 0.70-0.85 practice band; the selector will not queue it yet"
        )


def _raw_view(item: Mapping[str, Any]) -> dict[str, Any]:
    """The stored item shape flattened to the *candidate* shape ``generated`` reads.

    ``generated.key_verification`` is written for candidates, where ``correct_index`` sits at the top
    level; the bank's stored items carry it inside ``answer_spec``. Feeding it the stored shape made
    every mcq key come back "no answer text to check" -- the key was there, the reader was looking one
    level up. Flattening here rather than editing ``generated`` keeps one definition of "what a
    candidate looks like" in the module that owns it.
    """
    out = dict(item)
    spec = item.get("answer_spec") or {}
    if isinstance(spec, Mapping):
        for key in ("correct_index", "value", "values", "answer_value", "tolerance"):
            if key in spec and key not in out:
                out[key] = spec[key]
    return out


def _check_key(item: Mapping[str, Any], *, source_text: str | None, verdict: Verdict) -> None:
    """Verify the key by computing, or by quoting, or refuse to vouch for it.

    Three routes, tried in the brief's order. ``unverified`` is a *flag*, not a rejection: the item is
    kept out of scoring (``scorable=False``) and tagged, which is what §7.3 asks for — "reject or flag
    'unverified' and never use it for scoring" — and what ``generated.key_verification`` already does
    for the same reason.
    """
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in G.KINDS:
        verdict.rejections.append(Rejection("kind_unsupported", f"{kind!r} is not one of {', '.join(G.KINDS)}"))
        return
    spec = item.get("answer_spec") or {}
    expression = str(item.get("check_expr") or spec.get("check_expr") or spec.get("formula") or "").strip()
    values = spec.get("values") or spec.get("value")

    if expression:
        # A declared expression is a claim the code can settle exactly. ``variables`` are the item's
        # own defaults; ``equals`` is used when the author gave the key as a second expression.
        status, how = S.check_answer(
            expression=expression,
            answer=values if values not in (None, "") else spec.get("value"),
            variables=spec.get("variables") if isinstance(spec.get("variables"), Mapping) else None,
            equals=item.get("equals") or spec.get("equals"),
        )
        if status == "symbolic":
            verdict.verification = "symbolic"
            verdict.checks.append(f"key_verified_by_computing: {how}")
            return
        if status == "symbolic_mismatch":
            verdict.rejections.append(Rejection("key_disagrees", how))
            return
        # symbolic_error: this checker cannot vouch for it. Say so and fall through rather than
        # treating "we could not check it" as "it is wrong" — the two are different failures and only
        # one of them should stop a build.
        verdict.notes.append(f"symbolic checker could not be applied ({how}); falling back")

    mode, note = G.key_verification(kind, _raw_view(item), source_text=source_text)
    if mode == "arithmetic":
        verdict.verification = "arithmetic"
        verdict.scorable = True
        verdict.checks.append("key_verified_by_computing")
        return
    if mode == "quotable":
        verdict.verification = "quotable"
        verdict.scorable = True
        verdict.checks.append("key_quotable_in_source")
        return
    verdict.verification = "unverified"
    verdict.notes.append(f"key unverified: {note or 'no source and no computable key'}")
    if source_text:
        verdict.rejections.append(
            Rejection("key_not_in_source", note or "the key is not quotable from the supplied source text")
        )


def validate_item(item: Mapping[str, Any], *, source_text: str | None = None) -> Verdict:
    """The §7.3 check for one item. ``ok`` is True when a learner may see it.

    ``scorable`` is the separate question of whether the key can be trusted for grading, and it is
    deliberately not the same flag: an unverified item may be shown (with its tag) and must not be
    scored, which is the brief's own wording.
    """
    verdict = Verdict(ok=True)
    if not isinstance(item, Mapping) or not item:
        verdict.ok = False
        verdict.rejections.append(Rejection("kind_unsupported", "no item"))
        return verdict

    kind = str(item.get("kind") or "").strip().lower()
    if kind not in G.KINDS:
        verdict.ok = False
        verdict.rejections.append(Rejection("kind_unsupported", f"{kind!r} is not one of {', '.join(G.KINDS)}"))
        return verdict

    stem, choices = _stem_and_choices(item)
    texts: list[str] = [stem]
    for key in ("prompt_context", "stem_template"):
        if item.get(key):
            texts.append(str(item[key]))
    body = " ".join(texts)
    if _PLACEHOLDER.search(body):
        verdict.ok = False
        verdict.rejections.append(
            Rejection("placeholder", f"an unrendered placeholder is in the learner-facing text: {_PLACEHOLDER.search(body).group(0)!r}")
        )

    correct = _correct_texts(item, choices)
    if kind == "mcq":
        marked = [c for c in choices if c.get("correct")]
        spec_index = (item.get("answer_spec") or {}).get("correct_index")
        if len(marked) > 1:
            verdict.ok = False
            verdict.rejections.append(
                Rejection("multiple_correct_options", f"{len(marked)} choices are marked correct")
            )
        elif not marked and spec_index is None:
            verdict.ok = False
            verdict.rejections.append(Rejection("no_key", "no choice is marked correct and no correct_index is given"))
        elif spec_index is not None:
            # Two ways of naming the same key have to agree. An item whose choices say one thing and
            # whose correct_index says another has a key nobody can resolve, and the learner is marked
            # by whichever one the grader happens to read first.
            try:
                index = int(spec_index)
            except (TypeError, ValueError):
                index = -1
            if not 0 <= index < len(choices):
                verdict.ok = False
                verdict.rejections.append(
                    Rejection("no_key", f"correct_index {spec_index!r} is not one of the {len(choices)} choices")
                )
            elif marked and index != choices.index(marked[0]):
                verdict.ok = False
                verdict.rejections.append(
                    Rejection(
                        "multiple_correct_options",
                        f"correct_index says {index} but the choice marked correct is {choices.index(marked[0])}",
                    )
                )
            elif not marked:
                verdict.notes.append(
                    "the key is carried by correct_index alone (no choice has correct=True); the grader reads correct_index"
                )
        # A stem that prints the answer is a free item: the learner reads the key off the question.
        # Only the stem's own words count — see :data:`_QUOTED`.
        own_words = _unquoted(stem)
        for text in correct:
            if len(text) >= 4 and text.lower() in own_words.lower():
                verdict.ok = False
                verdict.rejections.append(
                    Rejection("answer_leak_in_stem", f"the key {text[:60]!r} appears in the stem")
                )
    elif kind == "numeric":
        if not correct:
            verdict.ok = False
            verdict.rejections.append(Rejection("no_key", "a numeric item with no answer value"))
    else:
        if not correct:
            verdict.ok = False
            verdict.rejections.append(Rejection("no_key", "a text item with nothing that counts as correct"))

    _difficulty_ok(item, verdict)
    _check_key(item, source_text=source_text, verdict=verdict)

    verdict.ok = verdict.ok and not any(r.code in GATE_CODES for r in verdict.rejections)
    verdict.scorable = verdict.ok and verdict.verification in ("symbolic", "arithmetic", "quotable")
    return verdict


def validate_for_bank(item: Mapping[str, Any], *, source_text: str | None = None, existing_stems: Sequence[str] = ()) -> Verdict:
    """:func:`validate_item` plus the rules for inserting a row into the item bank.

    The two extra rules are ``generated.validate_generated``'s own, and they are borrowed rather than
    reimplemented so there is exactly one definition of "already in the bank" and "every distractor
    explained". A caller that is not inserting a row should use :func:`validate_item`.
    """
    verdict = validate_item(item, source_text=source_text)
    if not verdict.ok:
        return verdict
    validated, rejections = G.validate_generated(
        item, existing_stems=list(existing_stems or ()), source_text=source_text
    )
    for rejection in rejections:
        verdict.rejections.append(Rejection(rejection.code, rejection.detail))
    # Inserting a row needs the *whole* check to pass, so any rejection blocks — including codes that
    # are notes for a learner-facing item (``topic_missing``, ``distractor_reason_missing``). Leaving
    # this as the GATE_CODES filter made the function report ``ok=True`` for a row
    # ``validate_generated`` had just refused, which is a validator agreeing with a rejection.
    verdict.ok = not verdict.rejections
    verdict.scorable = verdict.ok and verdict.verification in ("symbolic", "arithmetic", "quotable")
    return verdict


def validate_pack(payload: Mapping[str, Any], *, source_text: str | None = None) -> dict[str, Any]:
    """Validate every item in a pack payload. Returns the orchestrator's verdict shape.

    ``ok`` is true when nothing in the pack is blocked. The verdict never *rejects* a pack for a note,
    and it never upgrades an unverified key: the pack is returned with its items' statuses attached so
    a caller can decide, and ``notes`` carries everything the caller needs to decide with.
    """
    if not isinstance(payload, Mapping):
        return {"ok": False, "notes": ["the pack is not a mapping"]}
    items: list[Mapping[str, Any]] = []
    pair = payload.get("pair") or {}
    for leg in ("A", "B"):
        if isinstance(pair, Mapping) and isinstance(pair.get(leg), Mapping):
            items.append(pair[leg])
    for key in ("items", "questions"):
        extra = payload.get(key)
        if isinstance(extra, list):
            items.extend(i for i in extra if isinstance(i, Mapping))
    notes: list[str] = []
    ok = True
    for item in items:
        verdict = validate_item(item, source_text=source_text)
        label = str(item.get("id") or item.get("stem") or "?")[:60]
        for rejection in verdict.rejections:
            ok = False
            notes.append(f"{label}: {rejection.code} — {rejection.detail}")
        for note in verdict.notes:
            notes.append(f"{label}: {note}")
        if not verdict.scorable:
            notes.append(f"{label}: key is {verdict.verification}, so it is not used for scoring")
    worked = (payload.get("variants") or {}).get("worked_example") or {}
    if not worked.get("steps"):
        ok = False
        notes.append("the pack has no worked example")
    elif not worked.get("verified"):
        notes.append(f"the worked example failed its own gate: {', '.join(worked.get('flags') or [])}")
    return {"ok": ok, "notes": notes}
