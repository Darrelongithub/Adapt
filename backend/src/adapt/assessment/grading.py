"""Grading for the item subset ADAPT runs in-app.

No LLM in the loop for objective items: a graded quiz that occasionally asks a
model to judge "7/8" would be both slower and less reliable than a parser. AI 2 is
used where it adds real value -- teach-back coverage commentary and feedback prose
(:func:`adapt.ai.ai2.feedback_for`) -- and every grade records *how* it was
produced so the result is auditable.

Partial credit is explicit and coarse (0.0-1.0) because BKT consumes a binary
observation; the fractional score is kept for the dashboard and for
rubric-scored teach-backs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Sequence

from ..ai.local_tutor import eval_formula
from . import teachback as TB

NUM_RE = re.compile(r"-?\d+(?:[.,/]\d+)?(?:\s*[a-z]{1,4})?\b", re.IGNORECASE)
FRACTION_RE = re.compile(r"^\s*(-?\d+)\s*/\s*(-?\d+)\s*$")
UNIT_RE = re.compile(r"(cm|mm|m|km|g|kg|litre|liter|l|ml)\b", re.IGNORECASE)


@dataclass
class Grade:
    correct: bool
    score: float
    method: str  # mcq_index | numeric | fraction | expression | text_match | teachback_rubric | none
    detail: str = ""
    partial: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "score": round(self.score, 3),
            "method": self.method,
            "detail": self.detail,
            "partial": self.partial,
        }


def to_float(text: Any) -> float | None:
    """Parse a learner's numeric answer: int, decimal (dot or comma), fraction, percent."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text).strip().lower().replace(",", ".")
    # Drop units/currency and surrounding words, then normalise spacing.
    m = re.search(r"-?\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?", s)
    if m:
        s = m.group(0).replace(" ", "")
    s = s.replace(" ", "")
    if not s:
        return None
    m = FRACTION_RE.match(s)
    if m:
        den = int(m.group(2))
        return None if den == 0 else int(m.group(1)) / den
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        v = float(s)
    except ValueError:
        mm = re.match(r"^(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$", s)
        if mm and float(mm.group(2)) != 0:
            v = float(mm.group(1)) / float(mm.group(2))
        else:
            return None
    return v / 100.0 if pct else v


def grade(item: dict[str, Any], answer: Any, *, student_text: str | None = None) -> Grade:
    """Grade one response against an item's ``answer_spec``."""
    kind = item.get("kind", "text")
    spec = item.get("answer_spec") or {}

    if kind == "worked_example":
        return Grade(True, 1.0, "none", "Examples are not graded; they carry no score.")

    if kind == "mcq":
        choices = item.get("choices") or []
        correct_idx = spec.get("correct_index")
        if correct_idx is None:
            correct_idx = next((i for i, c in enumerate(choices) if c.get("correct")), None)
        picked = answer if isinstance(answer, int) else _choice_index(choices, answer)
        if correct_idx is None or picked is None:
            return Grade(False, 0.0, "mcq_index", "no recognisable choice selected")
        ok = int(picked) == int(correct_idx)
        why = None
        if not ok and 0 <= int(picked) < len(choices):
            why = choices[int(picked)].get("why_wrong")
        return Grade(
            ok,
            1.0 if ok else 0.0,
            "mcq_index",
            (str(why) if why else ("Correct choice." if ok else "Wrong choice.")),
            {"picked": int(picked), "correct_index": int(correct_idx)},
        )

    if kind == "numeric":
        value = to_float(answer if not isinstance(answer, dict) else (answer or {}).get("value"))
        expected = spec.get("value")
        if expected is None and spec.get("formula"):
            try:
                expected = eval_formula(str(spec["formula"]), {k: v for k, v in (spec.get("variables") or {}).items()})
            except Exception as exc:  # pragma: no cover
                return Grade(False, 0.0, "expression", f"item formula unusable: {exc}")
        if value is None or expected is None:
            return Grade(False, 0.0, "numeric", "could not parse an answer")
        tol = float(spec.get("tolerance") if spec.get("tolerance") is not None else 1e-6)
        ok = abs(value - float(expected)) <= max(tol, abs(float(expected)) * 1e-9)
        # Unit checking is opt-in per item: "36" for "perimeter in cm" is a valid
        # answer, so enforcing units by default would manufacture errors.
        wanted_unit = spec.get("unit") if spec.get("require_unit") else None
        missing_unit = bool(wanted_unit) and not re.search(str(wanted_unit), str(answer or ""), re.IGNORECASE)
        if ok and missing_unit:
            # Right number, no unit: half credit and a precise note. Marking it
            # wrong outright would teach nothing; marking it right would be false.
            return Grade(
                False,
                0.5,
                "numeric",
                f"value matches ({expected:g}) but the unit {wanted_unit!r} is missing",
                {"expected": float(expected), "given": value, "tolerance": tol, "missing_unit": True},
            )
        return Grade(
            ok,
            1.0 if ok else 0.0,
            "numeric",
            f"expected {expected:g}, got {value:g}",
            {"expected": float(expected), "given": value, "tolerance": tol},
        )

    if kind == "text":
        accepted = [str(a) for a in (spec.get("values") or ([spec["value"]] if "value" in spec else []))]
        raw = str(answer or "").strip().lower()
        norm = re.sub(r"[^a-z0-9./% ]+", "", raw).strip()
        if spec.get("formula"):
            try:
                expected = eval_formula(str(spec["formula"]), {k: v for k, v in (spec.get("variables") or {}).items()})
                got = to_float(answer)
                if got is not None:
                    ok = abs(got - float(expected)) <= float(spec.get("tolerance") or 1e-6)
                    return Grade(ok, 1.0 if ok else 0.0, "expression", f"evaluated {expected:g}; you wrote {got:g}")
            except Exception:
                pass
        if not accepted and not spec.get("keywords"):
            # Nothing left to compare against. Saying "expected one of: " here marked a
            # correct answer wrong with an empty sentence; the honest answer is that this
            # item has no local grader -- a Numbas part type, or a formula-only item
            # answered with an expression we cannot evaluate.
            return Grade(
                False,
                0.0,
                "none",
                "this item carries nothing this build can grade against; it needs the hosted Numbas runtime",
            )
        exact = any(_norm(a) == norm or (len(_norm(a)) > 3 and _norm(a) in norm) for a in accepted if a)
        keywords = [k.lower() for k in (spec.get("keywords") or [])]
        if not exact and keywords:
            hit = sum(1 for k in keywords if k in norm)
            frac = hit / max(1, len(keywords))
            return Grade(
                frac >= float(spec.get("min_keyword_fraction", 0.6)),
                round(frac, 3),
                "text_match",
                f"{hit}/{len(keywords)} key ideas present",
                {"matched": [k for k in keywords if k in norm]},
            )
        return Grade(
            exact,
            1.0 if exact else 0.0,
            "text_match",
            "matches the expected answer" if exact else f"expected one of: {', '.join(accepted[:3])}",
        )

    if kind == "teachback":
        text = str(answer or student_text or "")
        result = TB.grade_checklist(text, TB.checklist_from_rubric(item.get("rubric")))
        threshold = float((item.get("rubric") or {}).get("pass_fraction", 0.6))
        partial = _checklist_payload(result)
        return Grade(
            result.score >= threshold and len([w for w in re.split(r"\s+", text) if w]) >= TB.MIN_ANSWER_WORDS,
            round(float(result.score), 3),
            "teachback_checklist",
            result.followup or "Covers the points on the checklist.",
            partial,
        )

    return Grade(False, 0.0, "none", f"no grader for kind {kind!r}; needs the hosted Numbas runtime")


def _checklist_payload(result: TB.ChecklistResult) -> dict[str, Any]:
    """The checklist result in the shape the API, the trace and the UI already read.

    ``matched``/``missing``/``coverage`` stay (they are what the progress screen and
    the detectors read), but they now mean *points evidenced in the learner's own
    words* rather than *keywords present*. ``points`` carries the strict form: what
    each check was, whether it was met, and the quote that decided it.
    """
    return {
        **result.as_dict(),
        "method": "checklist_3_point",
        "matched": [p.statement for p in result.points if p.met],
        "missing": [p.statement for p in result.points if not p.met],
        "coverage": round(result.score, 3),
        "note": (
            result.note
            or "scored on the ideas, not on fluency, spelling or length"
        ),
    }


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9./%]+", "", str(s).lower().strip())


def _choice_index(choices: Sequence[dict[str, Any]], answer: Any) -> int | None:
    if answer is None:
        return None
    if isinstance(answer, dict):
        if "index" in answer:
            return int(answer["index"])
        answer = answer.get("text")
    s = _norm(str(answer))
    for i, c in enumerate(choices):
        if _norm(str(c.get("text", ""))) == s:
            return i
    if isinstance(answer, str) and answer.isdigit() and 0 < int(answer) <= len(choices):
        return int(answer) - 1
    return None

