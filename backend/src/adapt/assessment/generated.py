"""The contract a model-written question has to satisfy before anyone can answer it.

Three sources feed ADAPT content — stored, retrieved, generated — and this module is the
boundary for the third one. The difference matters because a wrong *explanation* is
recoverable (the learner notices, says so, and that is itself evidence) while a wrong
*question* is not: the app marks the answer, the trace records it, and the band on the
dashboard moves. A learner cannot tell that a wrong answer key is wrong. They will simply
believe the app.

So a generated item is checked here before it is stored, gets an identity derived from its
own content (so re-generating the same question lands on the same row and its difficulty
accumulates instead of resetting), and keeps a difficulty that comes from responses rather
than from the writing model's guess. The five rules this implements:

1. **Identity before display.** The id is a hash of the content, so two generations of the
   same question are one row. ``Item.id`` for generated items is ``gen_<checksum>``.
2. **A key that was checked, not asserted.** See :func:`validate_generated`.
3. **Difficulty: declared, then observed.** ``prior_difficulty`` keeps what the writer
   claimed; ``difficulty`` becomes the shrunk estimate as attempts arrive
   (:func:`observed_difficulty`).
4. **Nothing is silently dropped.** Rejections come back with a code and a reason for the
   caller to log, because "generation produced nothing" and "generation produced something
   we refused" are very different operational facts.
5. **Retirement.** :func:`should_retire` — an item whose observed difficulty walks away
   from its declared band, or whose key fails a re-check, stops being served.

There are no sessions and no queries in this module. It is the rulebook: ``ai.ai2``
writes rows with it, ``services.attempts`` calibrates them with it, and anything that
later generates content (AI 3 included) should hold its output to the same rules rather
than growing a second, weaker path.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Generated items whose difficulty has been replaced by observed performance keep a
#: weight for the writer's declared value. 8 attempts of prior means the two sources
#: are equally weighted once a question has been answered eight times.
OBSERVATION_PRIOR = 8.0
#: A question nobody has ever answered correctly is not a hard question. At four attempts
#: with zero correct, the likeliest explanations are a wrong answer key, a stem with two
#: readings, or material the learner has not been taught -- none of which is fixed by
#: asking more people.
UNANSWERABLE_ATTEMPTS = 4
#: Early retirement for a question that is not where it claims to be: six attempts is
#: enough for a large gap to be about the item rather than about the learner, and a gap
#: this size at this many attempts is worth acting on before the exposure cap.
DRIFT_ATTEMPTS = 6
RETIRE_DRIFT = 0.35
#: Exposure control. Twelve is deliberately above the drift threshold (so drift can fire
#: first) and above a single learner's seen-it count: past this many answers the item's
#: responses stop being independent observations, and a fresh question is the better use
#: of the learner's time.
RETIRE_AFTER_ATTEMPTS = 12

#: Tag put on a stored item whose answer key this build could not check. The learner
#: surface reads it and says so; nothing anywhere should treat such an item's marks
#: as equal to a verified one.
UNVERIFIED_TAG = "unverified_answer_key"

MIN_STEM_CHARS = 12
MAX_STEM_CHARS = 900
MAX_CHOICES = 6
MIN_CHOICES = 2
MAX_NODES = 80
MAX_POW_EXPONENT = 64.0
MAX_ABS_VALUE = 1e15

#: The grader's default is 1e-6, which is right for an authored answer and wrong for a
#: generated one: school answers get rounded ("0.43" for 3/7). The validator checks the
#: expression with this tolerance and writes it into ``answer_spec`` so the learner is
#: marked by the same rule the question was accepted under.
TOLERANCE_FLOOR = 0.005
TOLERANCE_RELATIVE = 0.005

KINDS = ("numeric", "mcq", "text")

#: Answer keys that can only be *checked* rather than computed come in two kinds:
#: arithmetic (verified by evaluating the expression, which works for maths and
#: nothing else) and **quotable** (verified by finding the key in the source
#: material, which is what a history or biology answer key actually is). A key that
#: is neither is not rejected outright -- it is flagged ``unverified`` and carries a
#: tag, because the honest failure mode for a model-written history question is
#: "nobody can check this automatically", not "this is wrong".
VERIFICATION_MODES = ("arithmetic", "quotable", "unverified")
#: A quotable answer must appear in the source with at most this much whitespace and
#: punctuation difference. Prose answers are normalised before comparing.
_QUOTE_NORMALISE = re.compile(r"[^a-z0-9]+")

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sqrt": math.sqrt,
    "int": int,
    "float": float,
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Rejection:
    """Why one candidate item was refused. Logged, never invented away."""

    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class Validated:
    """A candidate that passed every check, as ``Item`` column values."""

    payload: dict[str, Any]
    checks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def verification(self) -> str:
        return str(self.payload.get("verification") or "unverified")

    @property
    def unverified(self) -> bool:
        return self.verification == "unverified"


def safe_arithmetic(expression: str) -> float:
    """Evaluate a pure arithmetic expression. Raises ``ValueError`` on anything else.

    Written with ``ast`` rather than a character allowlist around ``eval``: letters,
    dots and parentheses are all it takes to reach attribute access (``().__class__``),
    and the expression here is *model output*, so the trusted-input assumption that
    ``local_tutor.eval_formula`` makes for authored bank formulas does not hold. Names
    are limited to a fixed function table, and ``**`` is bounded because ``2 ** 10**9``
    is a denial of service, not a mistake.
    """
    text = (expression or "").strip()
    if not text or len(text) > 400:
        raise ValueError("empty or oversized expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not an arithmetic expression: {exc.msg}") from exc
    if len(list(ast.walk(tree))) > MAX_NODES:
        raise ValueError("expression too large")
    value = _eval_node(tree.body)
    return float(_bounded(value))


def _bounded(value: int | float) -> int | float:
    """Reject anything whose magnitude is already absurd, at every step.

    Checked after each operation rather than only at the end: the end is too late. A
    nested ``**`` can hand the next operation an integer with thousands of digits, and
    the cost of the *check* is irrelevant next to the cost of carrying it around.
    """
    if not math.isfinite(value) or abs(value) > MAX_ABS_VALUE:
        raise ValueError("expression is not a finite, reasonable number")
    return value


def _eval_node(node: ast.AST) -> int | float:
    """Evaluate one node, preserving int-ness (``round(x, 2)`` needs the 2 to be an int)."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _bounded(_UNARYOPS[type(node.op)](_eval_node(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POW_EXPONENT:
            raise ValueError("exponent out of range")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ValueError("division by zero")
        return _bounded(_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("only abs, min, max, round, sqrt, int and float may be called")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        if node.func.id in ("min", "max") and len(node.args) > 8:
            raise ValueError("too many arguments")
        return _bounded(_FUNCS[node.func.id](*[_eval_node(a) for a in node.args]))
    raise ValueError(f"disallowed syntax: {type(node).__name__}")


def numbers_in(text: str) -> set[float]:
    """Every number written in a piece of text, normalised for comparison."""
    out: set[float] = set()
    for match in _NUMBER.findall(text or ""):
        try:
            out.add(round(abs(float(match)), 6))
        except ValueError:  # pragma: no cover - the regex only matches numbers
            continue
    return out


def normalise_stem(stem: str) -> str:
    """A stem with its variable whitespace and punctuation folded, for deduplication."""
    return re.sub(r"[^a-z0-9]+", " ", (stem or "").lower()).strip()


def normalise_quote(text: str) -> str:
    """Letters and digits only, lowercased: the form in which a quote is quotable."""
    return _QUOTE_NORMALISE.sub("", (text or "").lower())


def quote_found(answer: str, source_text: str | None) -> bool:
    """Is this answer (or a sentence saying it) present in the source material?"""
    if not source_text:
        return False
    needle = normalise_quote(answer)
    if len(needle) < 3:
        return False
    return needle in normalise_quote(source_text)


def key_verification(kind: str, raw: Mapping[str, Any], *, source_text: str | None = None) -> tuple[str, str]:
    """Which way this item's key was checked, and what that leaves open.

    ``arithmetic``: the expression was evaluated (maths, physics, anything numeric).
    ``quotable``: the key's text is in the source material, so a learner can be
    shown the sentence it came from.
    ``unverified``: neither, so the item says so rather than being shown as checked.
    """
    if kind == "numeric":
        return ("arithmetic" if str(raw.get("check_expr") or "").strip() else "unverified", "")
    values: list[str] = []
    value = raw.get("answer_value")
    if isinstance(value, list):
        values = [str(v) for v in value if str(v or "").strip()]
    elif value is not None and str(value).strip():
        values = [str(value)]
    if kind == "mcq":
        choices = raw.get("choices") or []
        index = _to_float(raw.get("correct_index"))
        if isinstance(choices, list) and index is not None and 0 <= int(index) < len(choices):
            chosen = choices[int(index)]
            text = str((chosen or {}).get("text") if isinstance(chosen, Mapping) else chosen or "").strip()
            if text:
                values = [text, *values]
    if not values:
        return ("unverified", "no answer text to check")
    if source_text:
        if any(quote_found(v, source_text) for v in values):
            return ("quotable", "")
        return (
            "unverified",
            "the key is not found in the source material, so this build cannot show anyone the sentence it came from",
        )
    return (
        "unverified",
        "no source material was supplied, so the key is flagged rather than trusted",
    )


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        parsed = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def tolerance_for(expected: float) -> float:
    """The band a submitted answer is allowed to sit in."""
    return max(TOLERANCE_FLOOR, TOLERANCE_RELATIVE * abs(expected))


def content_id(*, topic_id: str, kind: str, stem: str, answer_spec: Mapping[str, Any]) -> str:
    """``gen_<checksum>``: identity derived from the question, not from the call.

    A fresh uuid per generation (what this replaced) means the same question written
    twice is two rows, so neither accumulates attempts, neither can be retired, and the
    selector treats one question as two. Hashing the content makes re-generation
    idempotent for free.
    """
    import hashlib
    import json

    canonical = json.dumps(
        {
            "topic_id": topic_id,
            "kind": kind,
            "stem": normalise_stem(stem),
            "answer_spec": {k: answer_spec[k] for k in sorted(answer_spec)},
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"gen_{digest}"


def validate_generated(
    raw: Mapping[str, Any],
    *,
    topic_id: str | None = None,
    topic_ids: Iterable[str] | None = None,
    existing_stems: Iterable[str] = (),
    source_text: str | None = None,
    require_source: bool = False,
) -> tuple[Validated | None, list[Rejection]]:
    """Check one candidate item. Returns ``(None, rejections)`` if it may not be shown.

    ``existing_stems`` is the set of stems already in the bank for this topic; a
    generated question that duplicates an authored one is refused rather than stored,
    because two rows with one question means the same question counted twice.

    ``source_text`` is the material this question was written from. Every rule below
    still applies, but for a non-arithmetic key the *only* available check is
    quotability, so:

    * with ``source_text`` and a key that cannot be found in it, the item is rejected
      (``key_not_in_source``) -- a history answer key nobody can point at is exactly
      the wrong-question class this module exists to stop;
    * without ``source_text`` the item is accepted but *flagged*
      ``verification="unverified"`` and tagged ``unverified_answer_key``, so the
      learner-facing record can say the key was not checked rather than implying it
      was. ``require_source=True`` (the content generator's mode) turns that flag into
      a rejection instead.
    """
    bad: list[Rejection] = []

    def reject(code: str, detail: str) -> None:
        bad.append(Rejection(code, detail))

    resolved_topic = topic_id or str(raw.get("topic_id") or "")
    known = set(topic_ids) if topic_ids is not None else None
    if not resolved_topic:
        reject("topic_missing", "no topic_id on the candidate and none supplied by the caller")
    elif known is not None and resolved_topic not in known:
        reject("topic_unknown", f"{resolved_topic!r} is not a topic we can measure")

    kind = str(raw.get("kind") or "text").strip().lower()
    if kind not in KINDS:
        reject("kind_unsupported", f"{kind!r} is not one of {', '.join(KINDS)}")

    stem = str(raw.get("stem") or "").strip()
    if len(stem) < MIN_STEM_CHARS:
        reject("stem_missing", "the question has no usable stem")
    elif len(stem) > MAX_STEM_CHARS:
        reject("stem_too_long", f"{len(stem)} characters; the limit is {MAX_STEM_CHARS}")
    if stem and normalise_stem(stem) in {normalise_stem(s) for s in existing_stems}:
        reject("stem_already_in_bank", "this question is already in the bank for this topic")

    difficulty = _to_float(raw.get("difficulty"))
    if difficulty is None or not 0.0 <= difficulty <= 1.0:
        # Absent is fine -- it means "as written", not "average". Out of range is not.
        if raw.get("difficulty") is None:
            difficulty = 0.5
        else:
            reject("difficulty_out_of_range", f"{raw.get('difficulty')!r} is not in [0, 1]")
            difficulty = 0.5

    est_seconds = _to_float(raw.get("est_seconds"))
    est = int(est_seconds) if est_seconds and 15 <= est_seconds <= 900 else 90

    answer_spec: dict[str, Any] = {}
    choices: list[dict[str, Any]] | None = None
    checks: list[str] = []

    if kind == "numeric":
        declared = _to_float(raw.get("answer_value"))
        expression = str(raw.get("check_expr") or "").strip()
        if declared is None:
            reject("answer_not_numeric", "a numeric question with no numeric answer")
        else:
            if not expression:
                reject("check_expr_missing", "no arithmetic offered for the answer")
            else:
                try:
                    computed = safe_arithmetic(expression)
                except ValueError as exc:
                    reject("check_expr_invalid", f"{expression!r}: {exc}")
                else:
                    if not (numbers_in(expression) & numbers_in(stem)):
                        # The two ways an answer goes wrong: arithmetic that does not
                        # produce it, and arithmetic that has nothing to do with the
                        # question. This is the second one.
                        reject(
                            "check_expr_ungrounded",
                            f"{expression!r} never uses a number from the stem",
                        )
                    elif abs(declared - computed) > tolerance_for(computed):
                        reject(
                            "check_expr_disagrees",
                            f"the answer says {declared:g}, the arithmetic gives {computed:g}",
                        )
                    else:
                        checks.append("check_expr_matches_answer")
                        answer_spec = {"value": declared, "tolerance": tolerance_for(computed)}
                        answer_spec["check_expr"] = expression

    elif kind == "mcq":
        raw_choices = raw.get("choices")
        if not isinstance(raw_choices, list) or not MIN_CHOICES <= len(raw_choices) <= MAX_CHOICES:
            reject("choices_missing", f"needs {MIN_CHOICES}-{MAX_CHOICES} choices")
        else:
            texts = [str((c or {}).get("text") if isinstance(c, dict) else c or "").strip() for c in raw_choices]
            if any(not t for t in texts):
                reject("choices_missing", "a choice has no text")
            elif len({t.lower() for t in texts}) != len(texts):
                reject("duplicate_choices", "two choices say the same thing")
            index = _to_float(raw.get("correct_index"))
            if index is None or not 0 <= index < len(texts):
                reject("key_out_of_range", f"correct_index {raw.get('correct_index')!r} is not one of the choices")
            else:
                key = int(index)
                built: list[dict[str, Any]] = []
                for i, text in enumerate(texts):
                    why = ""
                    if i != key and isinstance(raw_choices[i], dict):
                        why = str(raw_choices[i].get("why_wrong") or "").strip()
                    if i != key and not why:
                        # The misconception behind a distractor is the reason to write
                        # one at all; without it the item can only say "wrong".
                        reject("distractor_reason_missing", f"choice {i} has no why_wrong")
                    built.append(
                        {"text": text, "correct": i == key, **({"why_wrong": why} if why else {})}
                    )
                if not any(r.code == "distractor_reason_missing" for r in bad):
                    checks.append("mcq_key_in_range")
                    checks.append("distractors_explained")
                    choices = built
                    answer_spec = {"correct_index": key}

    else:  # text
        value = raw.get("answer_value")
        accepted = [str(v).strip() for v in (value if isinstance(value, list) else [value]) if str(v or "").strip()]
        if not accepted:
            reject("answer_values_missing", "a text question with nothing that counts as correct")
        else:
            keywords = [str(k).strip() for k in (raw.get("keywords") or []) if str(k or "").strip()]
            checks.append("text_answer_present")
            answer_spec = {"values": accepted[:6], **({"keywords": keywords[:8]} if keywords else {})}

    verification, verification_note = key_verification(kind, raw, source_text=source_text)
    if verification != "arithmetic":
        if verification == "unverified":
            if require_source or source_text:
                # With the material in hand and the key not in it, this is not a
                # style question: the key is unchecked and cannot be checked here.
                reject("key_not_in_source", verification_note)
            else:
                checks.append("key_unverified")
        else:
            checks.append("key_quotable_in_source")

    if bad:
        return None, bad

    payload: dict[str, Any] = {
        "topic_id": resolved_topic,
        "kind": kind,
        "stem": stem,
        "experience_mode": "practice",
        "choices": choices,
        "answer_spec": answer_spec,
        "solution_steps": [
            {"text": str((s or {}).get("text") if isinstance(s, dict) else s)[:400]}
            for s in (raw.get("solution_steps") or [])[:5]
            if str((s or {}).get("text") if isinstance(s, dict) else s or "").strip()
        ]
        or None,
        "hints": [str(h)[:300] for h in (raw.get("hints") or [])[:3] if str(h or "").strip()] or None,
        "difficulty": round(float(difficulty), 4),
        "prior_difficulty": round(float(difficulty), 4),
        "est_seconds": est,
        "verification": verification,
    }
    tags = list(payload.get("tags") or []) if isinstance(payload.get("tags"), list) else []
    if verification == "unverified":
        tags.append("unverified_answer_key")
    if verification == "quotable":
        tags.append("key_quotable")
    if tags:
        payload["tags"] = tags
    payload["id"] = content_id(
        topic_id=resolved_topic, kind=kind, stem=stem, answer_spec=answer_spec
    )
    return Validated(payload=payload, checks=tuple(checks)), []


def observed_difficulty(
    prior: float | None,
    *,
    declared: float,
    n_correct: int,
    n_attempts: int,
    observation_prior: float = OBSERVATION_PRIOR,
) -> float:
    """Difficulty as evidence about the question rather than a claim about it.

    A mean of the declared value (weighted ``observation_prior``) and the observed
    success rate, clamped to [0.05, 0.99]. ``prior`` is ignored: two successive updates
    must not raise the prior's weight, and passing one of them in is how a running
    average turns into a runaway one.
    """
    if n_attempts <= 0:
        return round(float(declared), 4)
    rate = max(0.0, min(1.0, n_correct / n_attempts))
    blended = (observation_prior * declared + n_attempts * rate) / (observation_prior + n_attempts)
    return round(max(0.05, min(0.99, blended)), 4)


def should_retire(
    *,
    declared: float,
    observed: float,
    n_attempts: int,
    n_correct: int = 0,
    min_attempts: int = RETIRE_AFTER_ATTEMPTS,
    drift_attempts: int = DRIFT_ATTEMPTS,
    drift: float = RETIRE_DRIFT,
    unanswerable_attempts: int = UNANSWERABLE_ATTEMPTS,
) -> tuple[bool, str | None]:
    """Should this item stop being served? Returns ``(retire, reason)``.

    Three ways out, each with its own evidence bar, so a caller can log *why* an item
    left the pool rather than only that it did:

    * ``never_answered_correctly`` -- ``unanswerable_attempts`` with zero correct;
    * ``difficulty_drifted`` -- the observed rate is far from the declared difficulty
      after ``drift_attempts``, so the question is not measuring the band it claims;
    * ``exposure_cap`` -- answered ``min_attempts`` times, past which responses stop
      being independent observations of the skill.
    """
    if n_attempts <= 0:
        return False, None
    if n_correct <= 0 and n_attempts >= unanswerable_attempts:
        return True, "never_answered_correctly"
    if n_attempts >= drift_attempts and abs(float(observed) - float(declared)) > drift:
        return True, "difficulty_drifted"
    if n_attempts >= min_attempts:
        return True, "exposure_cap"
    return False, None


#: The ``Item`` columns anything reading or writing a generated item has to agree on.
#: Kept here, next to the contract, so a second generation path (AI 3, an importer, a
#: script) cannot quietly use a different subset.
ITEM_COLUMNS = (
    "id",
    "topic_id",
    "kind",
    "stem",
    "stem_template",
    "experience_mode",
    "prompt_context",
    "visual_spec",
    "choices",
    "answer_spec",
    "solution_steps",
    "hints",
    "rubric",
    "difficulty",
    "prior_difficulty",
    "est_seconds",
    "tags",
    "numbas_ref",
    "source",
    "active",
    "generated_by",
    "n_attempts",
    "n_correct",
    "calibrated_at",
    "verification",
)


def row_dict(row: Any) -> dict[str, Any]:
    """An ``Item`` row as the plain dict the selector and the renderers expect."""
    return {column: getattr(row, column, None) for column in ITEM_COLUMNS}


def summarise(rejections: Sequence[Rejection]) -> str:
    """One line for the log: counts per code, with the first detail of each."""
    seen: dict[str, str] = {}
    for r in rejections:
        seen.setdefault(r.code, r.detail)
    return "; ".join(f"{code} x{sum(1 for r in rejections if r.code == code)} ({detail})" for code, detail in seen.items())
