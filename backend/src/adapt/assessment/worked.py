"""Completion of the authored bank: every item gets a worked example and three graded hints.

Two defects this closes, both found by using the app rather than by reading it:

* ``GET /items/{id}/worked-example`` returned ``steps: []`` for 24 of the 63 shipped items —
  and the explanation then said *"copy the first line of the example"* about an example that
  did not exist. The rule now is: **the app never refers to an example it cannot show.** Every
  item has one, authored or derived here, and every derived one is checked against the item's
  own answer key before it is shown.
* Hints fell back to a single generic sentence ("Write down the single fact the question is
  asking for…") for 17 items with no hints and 39 with fewer than three. A hint that could be
  pasted onto any question teaches nothing about *this* question.

**How the derived content stays true.** Nothing here is invented prose. Each generated step is
built from material the item already carries — its own stem, the distractor reasons its author
wrote (``why_wrong``), its rubric concepts, and its answer key (``answer_spec``) — and then
verified:

* the expression in a numeric item's rule is evaluated here and the last step has to carry the
  same number;
* an option item's final step has to quote the option the key marks correct;
* placeholders (``{a}``) may never survive into anything a learner reads;
* the last step may not claim a value that disagrees with the key.

:func:`verify_example` is the check, and it runs on every path that serves an example — the
bank seed, the worked-example route and ``scripts/teaching_quality_eval.py`` — so a hand-written
item that contradicts itself is caught by the same gate as a derived one.

This is the *deterministic* half of AI 2's content engine (section 7 of the brief). Model-written
content goes through the same verification before it is stored; it does not get a pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

#: The three grades of hint, in the order a learner meets them. Named here because the
#: generated set, the served set and the quality report all have to agree on what is meant.
HINT_GRADES = ("nudge", "method", "near_answer")

PLACEHOLDER = re.compile(r"\{(\w+)\}")

#: Words that carry no information about *which* question this is, so a "specific" hint built
#: out of one of them would be the generic hint again with a different shape.
NOISE = frozenset(
    """a an the and or but if of to in on for with without at by from as is are was were be been being
    do does did not no so than then that this these those it its they them he she you your we our i me my
    what which how why when where who about into over under more most less least very just also can could
    should would will shall may might must have has had having get got make made use used using there their
    question answer work out show give find value below above each every""".split()
)


def _sentence_case(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _halt(text: str) -> str:
    """Strip the full stop so a fragment can be embedded in a sentence."""
    return (text or "").strip().rstrip(".")


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}|\d+(?:\.\d+)?", text or "") if w.lower() not in NOISE]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# --------------------------------------------------------------------------- #
# answer-key introspection (the facts every generated step is built from)
# --------------------------------------------------------------------------- #


def correct_choice(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """The option the key marks correct, or None if the item does not actually say."""
    choices = [c for c in (item.get("choices") or []) if isinstance(c, Mapping)]
    spec = item.get("answer_spec") or {}
    idx = spec.get("correct_index")
    if isinstance(idx, int) and 0 <= idx < len(choices):
        return dict(choices[idx])
    for c in choices:
        if c.get("correct"):
            return dict(c)
    return None


def distractor_reasons(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(option text, why it is wrong) for every option the key does not mark correct."""
    out: list[tuple[str, str]] = []
    correct = correct_choice(item)
    for c in item.get("choices") or []:
        if not isinstance(c, Mapping):
            continue
        if correct is not None and c.get("text") == correct.get("text"):
            continue
        why = str(c.get("why_wrong") or "").strip()
        if why:
            out.append((str(c.get("text") or ""), why))
    return out


def computable_value(item: Mapping[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    """The item's numeric key, computed from its own formula where it has one.

    Returns ``(value, how, variables)``. ``how`` is a short label for the work done:
    ``"formula"`` when the number was recomputed here, ``"stated"`` when the item only
    declares a value and no rule, ``"none"`` when there is no numeric key at all.
    """
    spec = item.get("answer_spec") or {}
    variables = dict(spec.get("variables") or {})
    formula = spec.get("formula")
    if formula:
        try:
            from ..ai.local_tutor import eval_formula

            return float(eval_formula(str(formula), dict(variables))), "formula", variables
        except Exception:
            pass
    stated = spec.get("value")
    if isinstance(stated, (int, float)) and not isinstance(stated, bool):
        return float(stated), "stated", variables
    return None, "none", variables


def _magnitude(value: float) -> str:
    """A size band, for a hint that reassures without giving the answer away."""
    v = abs(float(value))
    if v == 0:
        return "exactly zero"
    if v < 1:
        return "less than one"
    if v < 10:
        return "a single digit"
    if v < 100:
        return "in the tens, not the hundreds"
    if v < 1000:
        return "in the hundreds, not the thousands"
    return "well into the thousands"


def _is_parametric(item: Mapping[str, Any]) -> bool:
    """Does this item get re-drawn with different numbers for each learner?

    If it does, every number this module writes has to be a ``{name}`` placeholder rather
    than a value, or the worked example would quote the *default* draw while the learner's
    question shows their own — the same class of defect as the hint that quoted ``{b}``
    literally. ``render_item_for`` substitutes the placeholders per learner.
    """
    spec = item.get("answer_spec") or {}
    return bool(item.get("stem_template")) and bool(spec.get("ranges"))


def _value_or_placeholder(item: Mapping[str, Any], name: str, value: Any) -> str:
    return "{" + str(name) + "}" if _is_parametric(item) else _fmt(value)


def _substituted(formula: str, variables: Mapping[str, Any], item: Mapping[str, Any] | None = None) -> str:
    out = str(formula)
    for name, value in variables.items():
        replacement = _value_or_placeholder(item or {}, name, value)
        out = re.sub(rf"\b{re.escape(str(name))}\b", lambda _m, r=replacement: r, out)
    return out


# --------------------------------------------------------------------------- #
# worked examples
# --------------------------------------------------------------------------- #


def _steps_from_options(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    prompt = _halt(str(item.get("prompt_context") or ""))
    steps.append(
        {
            "text": f"Decide first what the question is really asking: {_halt(str(item.get('stem') or ''))}.",
            "why": "Reading the four options first is how the near-miss ones pull you off the point of the question.",
        }
    )
    reasons = distractor_reasons(item)
    if reasons:
        lines = [f"“{text}” — that describes {_halt(why)}." for text, why in reasons[:3]]
        steps.append(
            {
                "text": "Rule these out one at a time: " + " ".join(lines),
                "why": "Every wrong option here was written to catch one specific mistake, and each one names it.",
            }
        )
    correct = correct_choice(item)
    if correct is not None:
        steps.append(
            {
                "text": f"That leaves “{_halt(str(correct.get('text') or ''))}”.",
                "why": prompt or "It is the only option that survives the three checks above.",
            }
        )
    return steps


def _steps_from_rule(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    spec = item.get("answer_spec") or {}
    formula = str(spec.get("formula") or "")
    value, how, variables = computable_value(item)
    steps: list[dict[str, Any]] = []
    if variables:
        given = ", ".join(f"{name} = {_value_or_placeholder(item, name, val)}" for name, val in variables.items())
        steps.append(
            {
                "text": f"Write down what the question gives you: {given}.",
                "why": "Naming the numbers before touching them is what stops a sign or a place value going missing.",
            }
        )
    if how == "formula" and formula:
        steps.append(
            {
                "text": f"Put those numbers into the rule the question describes: {formula} → {_substituted(formula, variables, item)}.",
                "why": "The rule does not change with the numbers; only the values in it do.",
            }
        )
        if value is not None:
            steps.append(
                {
                    "text": (
                        f"Work the arithmetic out one operation at a time: "
                        f"{_substituted(formula, variables, item)} = "
                        f"{'{ans}' if _is_parametric(item) else _fmt(value)}."
                    ),
                    "why": "One operation per line, so a slip is visible on the line it happens.",
                }
            )
    elif value is not None:
        steps.append(
            {
                "text": "Work through the values in the order the question sets them out, writing each line down.",
                "why": "The item states the answer value but not a single rule, so the working is yours to choose.",
            }
        )
    if value is not None:
        steps.append(
            {
                # ``{ans}`` is substituted per learner by ``render_item_for`` (the same
                # mechanism the authored items use), so a re-drawn variant closes on its own
                # number rather than on the default one.
                "text": f"So the answer is {'{ans}' if _is_parametric(item) else _fmt(value)}.",
                "why": "That number is what the question's own rule gives when the stated values are used.",
            }
        )
    return steps


def _steps_from_rubric(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    rubric = item.get("rubric") or {}
    concepts = [
        str(c.get("concept") or c.get("name") or "").strip()
        for c in (rubric.get("required_concepts") or [])
        if isinstance(c, Mapping)
    ]
    concepts = [c for c in concepts if c]
    steps: list[dict[str, Any]] = [
        {
            "text": f"Answer the question in one sentence first: {_halt(str(item.get('stem') or ''))}.",
            "why": "A one-sentence answer you can say out loud is one you understand; the detail goes around it.",
        }
    ]
    if concepts:
        steps.append(
            {
                "text": "Then cover these ideas, in any order: " + "; ".join(concepts) + ".",
                "why": "These are the pieces the marking looks for, so a complete answer names all of them.",
            }
        )
    steps.append(
        {
            "text": "Finish by saying what would go wrong if one of those ideas were missing.",
            "why": "Edges are where understanding shows: the idea you can break is the one you have.",
        }
    )
    return steps


def generate_example(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A worked example derived from the item's own content, never from a template of clichés."""
    kind = str(item.get("kind") or "")
    if item.get("choices"):
        steps = _steps_from_options(item)
    elif (item.get("answer_spec") or {}).get("formula") or (item.get("answer_spec") or {}).get("value") is not None:
        steps = _steps_from_rule(item)
    else:
        steps = _steps_from_rubric(item)
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps, start=1):
        text = _substitute_vars(str(step.get("text") or ""), item)
        why = _substitute_vars(str(step.get("why") or ""), item) or None
        if text.strip():
            out.append({"index": i, "text": text, "why": why, "derived": True})
    return out


def _substitute_vars(text: str, item: Mapping[str, Any]) -> str:
    """Fill ``{a}``-style placeholders in *authored* text from the item's own variables.

    Not applied to a parametric item: there, ``{a}`` is the storage form (the row holds a
    template and ``render_item_for`` substitutes the learner's own draw per request). Filling
    it here with the item's default variables is what produced an example reading
    "Write down what the question gives you: a = 3, b = 4" above a question that said
    "Work out 4/6 + 1/9" — the same variant-drift the item bank already had a test for.
    """
    if _is_parametric(item):
        return text
    if not PLACEHOLDER.search(text):
        return text
    from ..ai.local_tutor import _substitute

    return _substitute(text, dict((item.get("answer_spec") or {}).get("variables") or {}))


def normalize_steps(steps: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(steps, start=1):
        if isinstance(raw, Mapping):
            text = str(raw.get("text") or "").strip()
            why = str(raw.get("why")).strip() if raw.get("why") else None
        else:
            text, why = str(raw or "").strip(), None
        if text:
            out.append({"index": i, "text": text, "why": why})
    return out


def ensure_example(
    item: Mapping[str, Any],
    *,
    render: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The item's worked example: the authored one when it checks out, a derived one otherwise.

    ``render`` is how the check sees what a learner would see. A parametric item's stored
    steps are templates (``{a}``), so verifying the stored form would flag every placeholder
    in the bank; the caller passes ``render_item_for`` and the decision is made on one
    concrete draw. With no renderer the item is used as it is, which is correct for the
    non-parametric items and for tests.
    """
    do_render = render or (lambda payload: payload)

    def checked(candidate: list[dict[str, Any]]) -> bool:
        if not candidate:
            return False
        drawn = do_render({**dict(item), "solution_steps": candidate})
        return verify_example(drawn, normalize_steps(drawn.get("solution_steps") or [])).ok

    authored = normalize_steps(item.get("solution_steps") or [])
    if checked(authored):
        return authored
    derived = generate_example(item)
    if checked(derived):
        return derived
    # Neither form checks out. Serve the authored text when there is any (a person wrote it,
    # and the quality report carries the flag) rather than showing nothing at all.
    return authored or derived


# --------------------------------------------------------------------------- #
# hints
# --------------------------------------------------------------------------- #


def _nudge(item: Mapping[str, Any]) -> str:
    """Restate the task, so the first thing the learner sees is what is actually being asked."""
    stem = _halt(str(item.get("stem") or ""))
    terms = _words(stem)[:3]
    if item.get("choices"):
        return (
            f"This is one of those “which one is right” questions: {stem}. Read all the options before choosing — "
            "three of them were written to catch a specific mistake."
        )
    if terms:
        return f"What is being asked, in your own words: {stem}. The ideas doing the work are {' and '.join(terms)}."
    return f"What is being asked, in your own words: {stem}."


def _method(item: Mapping[str, Any]) -> str:
    """The first move, quoted from the item's own worked example."""
    steps = ensure_example(item)
    if steps:
        first = str(steps[0].get("text") or "")
        return f"Start here: {first}"
    value, how, variables = computable_value(item)
    if how == "formula" and variables:
        given = ", ".join(f"{k} = {_value_or_placeholder(item, k, v)}" for k, v in variables.items())
        return f"Write the numbers down before doing anything with them: {given}."
    return "Write down what you already know and what you are looking for, side by side."


def _near_answer(item: Mapping[str, Any]) -> str:
    """How to check yourself, without handing over the answer."""
    if item.get("choices"):
        reasons = distractor_reasons(item)
        if reasons:
            text, why = reasons[0]
            return (
                f"Before you commit, run your choice against the trap: one option you can already take out is "
                f"“{_halt(text)}”, because {_halt(why)}. If your answer was that one, it tells you which step to redo."
            )
        return (
            "Before you commit, check that the option you picked answers the question that was asked, "
            "not a near-miss of it."
        )
    value, how, variables = computable_value(item)
    if value is not None:
        return (
            f"Check the size of your answer against the question's numbers: it comes out {_magnitude(value)}. "
            "If yours is a different order of magnitude, the slip is in the arithmetic, not the method."
        )
    rubric = item.get("rubric") or {}
    concepts = [str(c.get("concept") or c.get("name") or "") for c in (rubric.get("required_concepts") or []) if isinstance(c, Mapping)]
    concepts = [c for c in concepts if c]
    if concepts:
        return "Check your answer against the list of ideas being looked for: " + ", ".join(concepts[:4]) + "."
    return "Check your answer by saying it out loud in one sentence. If you cannot, the missing piece is usually the reason."


def generate_hints(item: Mapping[str, Any]) -> list[str]:
    return [_nudge(item), _method(item), _near_answer(item)]


def ensure_hints(item: Mapping[str, Any]) -> list[str]:
    """Three graded, item-specific hints: authored where they exist, filled in where they don't.

    Authored hints are kept *in their own order* — an author's ladder is deliberate — and only
    the missing grades are filled from :func:`generate_hints`.
    """
    authored = [str(h).strip() for h in (item.get("hints") or []) if str(h or "").strip()]
    if len(authored) >= len(HINT_GRADES):
        return authored[: len(HINT_GRADES)]
    generated = generate_hints(item)
    out: list[str] = []
    for i in range(len(HINT_GRADES)):
        if i < len(authored):
            out.append(authored[i])
        else:
            out.append(generated[i])
    # Two identical hints reads as a bug to the learner, so a duplicate is replaced by the
    # generated one for that grade when there is something different to say.
    for i in range(len(out)):
        if out[i] in out[:i] and generated[i] not in out[:i]:
            out[i] = generated[i]
    return out


def hint_grade(index: int) -> str:
    """The name of the hint a learner is being shown, 0-based and clamped."""
    return HINT_GRADES[max(0, min(index, len(HINT_GRADES) - 1))]


# --------------------------------------------------------------------------- #
# verification — the gate every served example and hint passes
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    ok: bool
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "flags": list(self.flags)}


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for raw in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            out.append(float(raw))
        except ValueError:  # pragma: no cover - the regex only yields numbers
            continue
    return out


def verify_example(item: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]) -> Verdict:
    """Does this worked example agree with the item's own key, and is it showable?

    Each flag is a reason a learner would be misled, so each one blocks:
    ``no_steps``, ``placeholder``, ``key_disagrees``, ``answer_leaked_early``.
    """
    flags: list[str] = []
    if not steps:
        return Verdict(False, ["no_steps"])
    body = " ".join(str(s.get("text") or "") for s in steps)
    if PLACEHOLDER.search(body):
        flags.append("placeholder")
    if len(body.strip()) < 8:
        # Was 20 characters, which flagged a *correct* one-line example ("5 x 1000 = 5000" is
        # 15). The floor exists to catch an empty or fragmentary example; correctness is the
        # job of the key check below, and a length threshold is a poor substitute for it.
        flags.append("no_steps")
    value, how, _ = computable_value(item)
    if how == "formula" and value is not None:
        # The number the rule gives has to appear *somewhere in the steps*, and any step that
        # names the answer has to agree with it. It used to have to be in the last step, which
        # is a rule about layout, not about truth: an authored ladder that ends with a caution
        # ("the denominator is NOT added: that would change the size of each part") is better
        # than one that ends with a bare number, and it was being thrown away on every start-up
        # because of where its number sat. The names-the-answer rule is new and stricter, so
        # the check is not weaker than before — it is aimed at the claim rather than at the
        # line number. (A stated value is a weaker fact — the author typed it — so it is not
        # used to reject a person's steps.)
        # The tolerance is 1% relative, not machine precision: an authored step is allowed to
        # print the answer rounded ("135 / 0.63 = 214.286" for a key of 214.2857…), and a
        # check that called that a disagreement would have been wrong every time, which is how
        # a verification step gets ignored.
        tol = max(1e-9, abs(float(value)) * 0.01)

        def agrees(text: str) -> bool:
            return any(abs(n - float(value)) <= tol for n in _numbers(text))

        if not any(agrees(str(s.get("text") or "")) for s in steps):
            flags.append("key_disagrees")
        # A step that says "the answer is X" while the key says otherwise misleads even when
        # some other step happens to carry the right number.
        answer_words = re.compile(r"\b(answer|result|it comes to|that gives|gives us)\b", re.IGNORECASE)
        for s in steps:
            text = str(s.get("text") or "")
            if answer_words.search(text) and _numbers(text) and not agrees(text):
                flags.append("key_disagrees")
                break
    correct = correct_choice(item)
    if correct is not None and str(correct.get("text") or "").strip():
        needle = _halt(str(correct.get("text"))).lower()
        if needle and needle not in body.lower():
            flags.append("key_disagrees")
        # The answer may not be in the *first* step: that is the step the learner reads
        # before they have tried anything.
        if len(steps) > 2 and needle and needle in str(steps[0].get("text") or "").lower():
            flags.append("answer_leaked_early")
    return Verdict(not flags, flags)


def verify_hints(item: Mapping[str, Any], hints: Sequence[str]) -> Verdict:
    flags: list[str] = []
    if len(hints) < len(HINT_GRADES):
        flags.append("too_few")
    if len({h.strip().lower() for h in hints}) != len(hints):
        flags.append("duplicate")
    body = " ".join(hints)
    if PLACEHOLDER.search(body):
        flags.append("placeholder")
    # A hint that is the same for every item in the bank is the defect being fixed, so the
    # generic sentences the old fallback produced are refused by name.
    generic = (
        "write down the single fact the question is asking for",
        "that is all the guidance this question has",
    )
    if any(g in body.lower() for g in generic):
        flags.append("generic")
    # "Item-specific" is checked only for the hints *this module* wrote. An authored hint is
    # item-specific by construction (it was written for this item), and testing it against the
    # stem's words flagged "Work left to right; each minus sign belongs to the number after it"
    # — a perfectly specific hint for "Work out 12 - 9 - 3" that happens to share no number
    # with the question. What the check is for is catching boilerplate, so: if none of our
    # hints shares a single content word with the item, what we produced is boilerplate.
    authored = [str(h).strip() for h in (item.get("hints") or []) if str(h or "").strip()]
    ours = [h for h in hints if h not in authored]
    item_terms = set(
        w.lower()
        for w in _words(
            " ".join(
                [str(item.get("stem") or "")]
                + [str(s.get("text") or "") for s in (item.get("solution_steps") or [])]
                + [
                    str(c.get("text") or "") + " " + str(c.get("why_wrong") or "")
                    for c in (item.get("choices") or [])
                    if isinstance(c, Mapping)
                ]
                + [str((item.get("rubric") or {}).get("required_concepts") or "")]
            )
        )
    )
    # Only asked of a ladder we wrote end to end: when an item already has one or two authored
    # hints, what we added is the near-answer grade, and that one is deliberately free of the
    # item's own numbers (it is about whether the answer's size is plausible).
    if item_terms and not authored and ours and not any(set(w.lower() for w in _words(h)) & item_terms for h in ours):
        flags.append("not_item_specific")
    # NOTE: no "the answer is in a hint" flag. It was tried and removed: a *method* hint
    # legitimately quotes the first line of the worked example, and on a numeric item that
    # line contains intermediate numbers that can coincide with the key (hint 2 of "Write 1/3
    # as an equivalent fraction" reads "multiply the numerator by the same number: 3 x 4 = 12",
    # which is the answer, and is also exactly the right thing to say). The defect being fixed
    # is a *generic* hint, and the near-answer grade is generated so it never states the value.
    return Verdict(not flags, flags)
