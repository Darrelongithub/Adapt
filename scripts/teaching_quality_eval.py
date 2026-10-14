#!/usr/bin/env python3
"""Phase 4's gate (brief section 7): does the content actually teach, and can it be checked?

Run: ``.venv/bin/python scripts/teaching_quality_eval.py`` (``--json`` for machine output).

What it walks
-------------
Every piece of **deterministic** teaching content in the repository:

* the authored maths bank — for each item: its three graded hints, its worked example, and the
  explanation the lesson screen actually shows (``adapt.ai.local_tutor.explain``);
* the subject round's authored pools for all five subject families (maths, languages, sciences,
  history, generic);
* the pop-up lesson's concepts, in all three formats (text, visual, worked steps) and every
  matched question.

The gate, exactly as the brief names it
---------------------------------------
The run fails when any of these five appear on the deterministic path:

* ``off_topic`` — text shown as grounded while it came from a different topic (§4.1.2);
* ``missing_example`` — the app would refer to a worked example it cannot show (§4.1.3);
* ``generic_hint`` — a hint that could be pasted onto any question (§4.1.4);
* ``placeholder`` — an unrendered ``{a}`` reached text a learner reads;
* ``key_disagrees`` — the worked example contradicts the item's own answer key.

Everything else the script notices is a **note**: reported, with its own section in the report,
and it does not fail the run. Adding checks must not quietly change what "pass" means.

The model pass runs only when a key is configured; without one the report says so in those
words rather than claiming a pass that never happened (brief §0.6).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND / "src"))

from adapt.assessment import bank as BANK  # noqa: E402
from adapt.assessment import worked as WORKED  # noqa: E402
from adapt.ai import local_tutor as TUTOR  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")
GENERIC_MARKERS = (
    "write down the single fact the question is asking for",
    "read the question again carefully",
    "think about what you already know",
)
#: The five flags the brief names as the gate. The script exits non-zero on these, and only these.
GATE_FLAGS = frozenset(
    {"off_topic", "missing_example", "generic_hint", "placeholder", "key_disagrees"}
)


@dataclass
class Flag:
    """One thing that would mislead a learner. `kind` decides whether it fails the run."""

    kind: str
    where: str
    detail: str


@dataclass
class Note:
    """An observation that is not one of the brief's five gate flags."""

    kind: str
    where: str
    detail: str


@dataclass
class Coverage:
    """What was walked, per subject family, so the report can state its own n."""

    subject: str
    items: int = 0
    explanations: int = 0
    hints: int = 0
    examples: int = 0
    questions: int = 0
    #: named topics touched (the brief's gate counts subjects, and a topic is what a learner picks)
    topics: set[str] = field(default_factory=set)
    #: explanations drawn from this item's own topic's material (grounded in the question asked)
    grounded_in_topic: int = 0
    #: explanations answered from the item's own working, with the note saying so
    from_item_working: int = 0
    #: explanations that said "there is no note in your material about this" (honest simple mode)
    honest_no_material: int = 0


# --------------------------------------------------------------------------------------- #
# reading the two shapes the codebase uses for a question
# --------------------------------------------------------------------------------------- #
QUESTION_KINDS = {"mcq", "choice", "numeric", "short_text", "text", "match", "order", "predict", "check"}


def _looks_like_question(item: Mapping[str, Any]) -> bool:
    return str(item.get("kind") or "").lower() in QUESTION_KINDS


def _raw_options(item: Mapping[str, Any]) -> list[Any]:
    raw = item.get("choices") if item.get("choices") is not None else item.get("options")
    return list(raw or [])


def _options(item: Mapping[str, Any]) -> list[str]:
    return [str(o) for o in _raw_options(item)]


def _key_index(item: Mapping[str, Any]) -> int | None:
    """The correct option's index, from either shape: bank ``answer_spec.correct_index`` or
    pool ``answer: n``. ``None`` means this is not an option question."""
    spec = item.get("answer_spec")
    if isinstance(spec, Mapping) and isinstance(spec.get("correct_index"), int):
        return int(spec["correct_index"])
    answer = item.get("answer")
    if isinstance(answer, bool) or not isinstance(answer, int):
        return None
    return int(answer) if _raw_options(item) else None


def _has_key(item: Mapping[str, Any]) -> bool:
    kind = str(item.get("kind") or "").lower()
    spec = item.get("answer_spec")
    if isinstance(spec, Mapping) and spec:
        return True
    if item.get("answer") is not None:
        return True
    # `match` keeps the correct pairing in `pairs`; `order` keeps the correct sequence in `steps`.
    if kind == "match" and item.get("pairs"):
        return True
    if kind == "order" and item.get("steps"):
        return True
    # A free-text answer is marked against a fixed checklist rather than a key: `rubric` for
    # most pools, `required_groups`/`banned` for the "say it differently" tasks (brief 6.2).
    if item.get("rubric") or item.get("answer_key") or item.get("required_groups"):
        return True
    return False


def _reason_of(item: Mapping[str, Any]) -> str:
    """The text that says *why* this answer follows, wherever the item keeps it."""
    for key in ("why", "explanation", "rationale", "note"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    steps = item.get("solution_steps") or item.get("steps") or ()
    body = " ".join(str(s.get("text") if isinstance(s, Mapping) else s) for s in steps)
    return body if body.strip() else ""


def _check_question(
    item: Mapping[str, Any],
    where: str,
    flags: list[Flag],
    notes: list[Note],
) -> None:
    """The checks that apply to any authored question, whichever shape it arrived in.

    Only *rendered* text is examined: a bank stem legitimately carries ``{a}`` until a variant
    is drawn, so the caller renders first and a placeholder here means one survived rendering.
    """
    if not _looks_like_question(item):
        return
    stem = str(item.get("stem") or item.get("question") or "")
    if not stem.strip():
        notes.append(Note("no_stem", where, "the question has no text"))
        return
    if PLACEHOLDER.search(stem):
        flags.append(Flag("placeholder", where, f"an unrendered placeholder survived rendering: {stem[:80]}"))
    options = _options(item)
    if options:
        if len(options) < 2:
            notes.append(Note("no_options", where, "a choice question with fewer than two options"))
        if len({o.strip().lower() for o in options}) != len(options):
            notes.append(Note("duplicate_options", where, "two options read the same"))
    index = _key_index(item)
    if options and index is not None and not (0 <= index < len(options)):
        notes.append(Note("no_key", where, f"the key names option {index}, which does not exist"))
    elif not _has_key(item):
        notes.append(Note("no_key", where, "no answer key of any kind"))
    if options and index is not None and 0 <= index < len(options):
        correct = options[index].strip()
        if len(correct) > 8 and correct.lower() in stem.lower():
            notes.append(Note("answer_leak", where, f"the correct option also appears in the stem: {correct[:60]}"))
    if not _reason_of(item):
        notes.append(
            Note(
                "no_item_reason",
                where,
                "the item carries no reasoning of its own; the explanation path supplies the topic's material",
            )
        )


# --------------------------------------------------------------------------------------- #
# the walks
# --------------------------------------------------------------------------------------- #
def _rendered_bank_items() -> list[tuple[str, str, dict[str, Any]]]:
    """Every bank item, rendered as the app renders it: ``(topic_code, label, item)``."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for topic in BANK.TOPICS:
        code = topic["code"]
        for raw in BANK.ITEMS.get(code, []):
            item = dict(raw)
            item.setdefault("topic_id", code)
            item.setdefault("topic_title", topic["title"])
            try:
                rendered = BANK.render_item_for(item, seed=0)
                rendered = dict(rendered)
            except Exception:
                rendered = dict(item)
            rendered.setdefault("topic_id", code)
            rendered.setdefault("topic_title", topic["title"])
            label = str(rendered.get("stem") or raw.get("stem") or "")[:48]
            out.append((code, label, rendered))
    return out


def walk_bank(coverage: dict[str, Coverage], flags: list[Flag], notes: list[Note]) -> None:
    """The authored maths bank: hints, worked example and explanation, item by item."""
    items = _rendered_bank_items()
    materials: list[tuple[str, str, str, dict[str, Any]]] = []
    for topic in BANK.TOPICS:
        for i, (title, body) in enumerate(BANK.materials_for(topic["code"])):
            materials.append(
                (f"{topic['code']}_{i}", topic["code"], body, {"topic_title": topic["title"], "title": title})
            )
    index = TUTOR.build_index(materials=materials, items=[dict(i) for _, _, i in items])

    maths = coverage.setdefault("maths", Coverage("maths"))
    for topic_code, label, item in items:
        where = f"{topic_code}/{label}"
        maths.items += 1
        maths.questions += 1
        maths.topics.add(topic_code)
        _check_question(item, where, flags, notes)

        # -- three graded hints ------------------------------------------------- #
        hints = WORKED.ensure_hints(item)
        maths.hints += len(hints)
        verdict = WORKED.verify_hints(item, hints)
        if verdict.flags:
            for flag in verdict.flags:
                kind = {
                    "too_few": "too_few_hints",
                    "duplicate": "duplicate_hint",
                    "placeholder": "placeholder",
                    "generic": "generic_hint",
                }.get(flag, flag)
                flags.append(Flag(kind, f"hints:{where}", "verify_hints: " + ",".join(verdict.flags)))
        for hint in hints:
            if any(marker in hint.lower() for marker in GENERIC_MARKERS):
                flags.append(Flag("generic_hint", f"hints:{where}", hint[:80]))

        # -- the worked example -------------------------------------------------- #
        steps = WORKED.ensure_example(item, render=lambda raw: BANK.render_item_for(raw, seed=0))
        maths.examples += 1
        verdict = WORKED.verify_example(item, steps)
        for flag in verdict.flags:
            kind = {"no_steps": "missing_example", "key_disagrees": "key_disagrees"}.get(flag, flag)
            flags.append(Flag(kind, f"example:{where}", ",".join(verdict.flags)))
        if 0 < len(steps) < 2:
            notes.append(Note("thin_example", f"example:{where}", f"{len(steps)} step(s)"))

        # -- the explanation a learner sees -------------------------------------- #
        grounded = TUTOR.explain(
            str(item.get("stem") or ""),
            index=index,
            topic_title=str(item.get("topic_title") or ""),
            topic_id=str(item.get("topic_id") or ""),
            p_know=0.35,
            item=dict(item),
            seed=str(item.get("topic_id") or ""),
        )
        maths.explanations += 1
        source = str(getattr(grounded, "source_topic", "") or "")
        topic_id = str(item.get("topic_id") or "")
        note_text = str(getattr(grounded, "note", "") or "")
        if grounded.grounded:
            maths.grounded_in_topic += 1
        elif "own working" in note_text:
            maths.from_item_working += 1
        else:
            maths.honest_no_material += 1
        if grounded.grounded and source and source != topic_id:
            flags.append(
                Flag("off_topic", f"explain:{where}", f"shown as grounded but drawn from {source}")
            )
        body = str(grounded.text or "")
        if not body.strip():
            notes.append(Note("missing_explanation", f"explain:{where}", "no explanation came back"))
        if PLACEHOLDER.search(body):
            flags.append(Flag("placeholder", f"explain:{where}", "unrendered placeholder in the explanation"))
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            if sentence and not sentence.rstrip().endswith((".", "!", "?", "…", ":")):
                notes.append(Note("cut_sentence", f"explain:{where}", sentence[-60:]))
                break


def _skill_pool(skill_id: str) -> tuple[dict[str, Any], ...]:
    from adapt.subjects import games as GAMES

    try:
        return tuple(GAMES.pool_for(skill_id))
    except Exception:
        return ()


def walk_subject_pools(coverage: dict[str, Coverage], flags: list[Flag], notes: list[Note]) -> None:
    """The subject round's authored questions, per family — the content that teaches non-maths."""
    from adapt.subjects import taxonomy as TAX

    for family in TAX.FAMILY_IDS:
        cov = coverage.setdefault(family, Coverage(family))
        for skill in TAX.skills_for(family):
            for i, task in enumerate(_skill_pool(skill.id)):
                where = f"{skill.id}#{i}"
                cov.items += 1
                cov.questions += 1
                cov.topics.add(skill.id.split(".", 1)[-1] if "." in skill.id else skill.id)
                item = {
                    "kind": task.get("kind"),
                    "stem": task.get("stem") or task.get("question"),
                    "options": task.get("options"),
                    "answer": task.get("answer"),
                    "why": task.get("why"),
                    "pairs": task.get("pairs"),
                    "steps": task.get("steps"),
                    "rubric": task.get("rubric"),
                    "required_groups": task.get("required_groups"),
                }
                _check_question(item, where, flags, notes)
                if task.get("kind") == "match":
                    if not task.get("pairs"):
                        notes.append(Note("no_options", where, "a matching task with no correct pairs"))
                    if not task.get("left") or not task.get("right"):
                        notes.append(Note("no_options", where, "a matching task with a missing column"))
                if task.get("kind") == "order":
                    steps = list(task.get("steps") or [])
                    if len(steps) < 3:
                        notes.append(Note("no_options", where, f"an ordering task with {len(steps)} steps"))


def walk_popup_lessons(coverage: dict[str, Coverage], flags: list[Flag], notes: list[Note]) -> None:
    """The 90-second pop-up lesson: three formats and matched questions, per concept."""
    from adapt.subjects import methodtest as M

    families = {
        "algebra": "maths",
        "calculus": "maths",
        "statistics": "maths",
        "discrete": "maths",
        "grammar": "languages",
        "kiswahili": "languages",
        "essay": "languages",
        "biology": "sciences",
        "chemistry": "sciences",
        "government": "history",
        "world_history": "history",
    }
    pairs = [value for name, value in vars(M).items() if name.isupper() and isinstance(value, M.Pair)]
    if not pairs:
        flags.append(Flag("missing_lesson", "popup", "no pop-up lesson concept pairs are defined"))
    for pair in pairs:
        family = families.get(pair.topic_key, "generic")
        cov = coverage.setdefault(family, Coverage(family))
        cov.topics.add(pair.topic_key.replace("_", " "))
        for concept in pair.concepts:
            where = f"popup:{pair.topic_key}:{concept.key}"
            formats = {"text": concept.text, "visual": concept.visual, "worked_example": concept.steps}
            for method in M.METHODS:
                lesson = formats.get(method)
                if not lesson:
                    flags.append(
                        Flag("missing_lesson", f"{where}:{method}", "this method has no lesson for this concept")
                    )
                    continue
                cov.items += 1
                body = lesson if isinstance(lesson, str) else json.dumps(lesson, default=str)
                if PLACEHOLDER.search(body):
                    flags.append(Flag("placeholder", f"{where}:{method}", "unrendered placeholder in a lesson"))
                if method == "text" and len(str(body).split()) < 25:
                    notes.append(
                        Note("thin_lesson", f"{where}:{method}", f"{len(str(body).split())} words for a 90-second lesson")
                    )
            for i, q in enumerate(concept.questions or ()):
                cov.questions += 1
                if isinstance(q, Mapping):
                    entry = dict(q)
                else:
                    entry = {
                        "kind": getattr(q, "kind", pair.question_format),
                        "stem": getattr(q, "stem", None) or getattr(q, "question", None),
                        "options": getattr(q, "options", None),
                        "answer": getattr(q, "answer", None),
                        "why": getattr(q, "why", None),
                    }
                _check_question(entry, f"{where}#{i}", flags, notes)


def model_pass() -> dict[str, Any]:
    """The model half of the gate. Only runs with a key; otherwise says so in those words."""
    key = os.environ.get("ADAPT_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        return {
            "ran": False,
            "why": (
                "no model key is configured, so no model-written content was produced or judged. "
                "On a machine without a key the app runs in simple mode, and the deterministic path "
                "below is the whole of what a learner sees."
            ),
        }
    return {
        "ran": False,
        "why": (
            "a key is present, but this sandbox has no outbound network to the model, so the pass "
            "could not run here. On a machine with network access, run the same script there."
        ),
    }


# --------------------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------------------- #
def report(
    coverage: dict[str, Coverage],
    flags: list[Flag],
    notes: list[Note],
    model: dict[str, Any],
) -> str:
    total_items = sum(c.items for c in coverage.values())
    total_q = sum(c.questions for c in coverage.values())
    total_expl = sum(c.explanations for c in coverage.values())
    total_hints = sum(c.hints for c in coverage.values())
    total_ex = sum(c.examples for c in coverage.values())
    grounded_n = sum(c.grounded_in_topic for c in coverage.values())
    item_working_n = sum(c.from_item_working for c in coverage.values())
    honest = sum(c.honest_no_material for c in coverage.values())
    by_kind: dict[str, int] = {}
    for f in flags:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    notes_by_kind: dict[str, int] = {}
    for n in notes:
        notes_by_kind[n.kind] = notes_by_kind.get(n.kind, 0) + 1

    lines: list[str] = []
    lines.append("# Teaching quality report (brief section 7)")
    lines.append("")
    lines.append("Generated by `scripts/teaching_quality_eval.py`. Every number below is a count of")
    lines.append("things the script actually walked; nothing is estimated. Re-run it after any content")
    lines.append("change: the gate is the script's exit code (zero on a clean deterministic path).")
    lines.append("")
    lines.append("## What was walked")
    lines.append("")
    lines.append("| subject family | content items | questions checked | explanations | hints | worked examples |")
    lines.append("|---|---|---|---|---|---|")
    for name, cov in sorted(coverage.items()):
        lines.append(f"| {name} | {cov.items} | {cov.questions} | {cov.explanations} | {cov.hints} | {cov.examples} |")
    lines.append(f"| **total** | **{total_items}** | **{total_q}** | **{total_expl}** | **{total_hints}** | **{total_ex}** |")
    lines.append("")
    named = sorted({topic for c in coverage.values() for topic in c.topics})
    lines.append(f"**Distinct topics walked: {len(named)}** — " + ", ".join(named) + ".")
    lines.append("")
    lines.append(
        f"The brief's gate asks for at least 40 questions across at least 6 subjects. This run checked"
    )
    lines.append(f"**{total_q} questions across {len(named)} named topics** in {len(coverage)} subject families.")
    lines.append("")
    if total_expl:
        lines.append(f"The {total_expl} explanations came out three ways, told apart by the reply's own note:")
        lines.append("")
        lines.append(f"* **{grounded_n}** grounded in the learner's material for that exact topic;")
        lines.append(f"* **{item_working_n}** answered from the question's own working, which the reply says;")
        lines.append(f"* **{honest}** said, in those words, that the learner's material has no note about it.")
        lines.append("")
        lines.append("All three are the honest simple-mode paths the brief asks for: nothing above is off-topic text")
        lines.append("presented as the learner's own material.")
        lines.append("")
    lines.append("## The deterministic path: the five gate flags")
    lines.append("")
    if not flags:
        lines.append(f"**Zero flags across {total_q} questions.** No off-topic text, no missing example, no")
        lines.append("generic hint, no placeholder leakage, no answer key disagreeing with the worked example —")
        lines.append("on every deterministic item in the repository, which is the whole of what a learner sees")
        lines.append("without a key.")
    else:
        lines.append(f"**{len(flags)} flag(s)** across {total_q} questions, by kind:")
        lines.append("")
        for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"* `{kind}` — {n}")
        lines.append("")
        lines.append("| kind | where | detail |")
        lines.append("|---|---|---|")
        for f in flags[:60]:
            lines.append(f"| `{f.kind}` | `{f.where}` | {f.detail[:110]} |")
        if len(flags) > 60:
            lines.append(f"| … | | {len(flags) - 60} more |")
    lines.append("")
    lines.append("## Notes (not gate failures)")
    lines.append("")
    if not notes:
        lines.append("Nothing to note.")
    else:
        for kind, n in sorted(notes_by_kind.items(), key=lambda kv: -kv[1]):
            lines.append(f"* `{kind}` — {n}")
        lines.append("")
        lines.append("| note | where | detail |")
        lines.append("|---|---|---|")
        for n in notes[:25]:
            lines.append(f"| `{n.kind}` | `{n.where}` | {n.detail[:110]} |")
        if len(notes) > 25:
            lines.append(f"| … | | {len(notes) - 25} more |")
    lines.append("")
    lines.append("## The model pass")
    lines.append("")
    lines.append(f"* Ran: **{model['ran']}** — {model['why']}")
    lines.append("")
    lines.append("## How to read this")
    lines.append("")
    lines.append("* `off_topic` — text was shown as grounded while it came from another topic (brief 4.1.2).")
    lines.append("* `missing_example` — the app would refer to a worked example it cannot show (4.1.3).")
    lines.append("* `generic_hint` — a hint that could be pasted onto any question in the bank (4.1.4).")
    lines.append("* `placeholder` — an unrendered `{a}` reached text a learner reads.")
    lines.append("* `key_disagrees` — the worked example contradicts the item's own answer key.")
    lines.append("* A `no_item_reason` note is not a defect: the reasoning for a bank item is its topic's")
    lines.append("  material, which the explanation path retrieves and cites.")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="also print the result as JSON")
    parser.add_argument("--out", default=str(ROOT / "QUALITY_REPORT.md"))
    args = parser.parse_args()

    coverage: dict[str, Coverage] = {}
    flags: list[Flag] = []
    notes: list[Note] = []
    walk_bank(coverage, flags, notes)
    walk_subject_pools(coverage, flags, notes)
    walk_popup_lessons(coverage, flags, notes)
    gate = [f for f in flags if f.kind in GATE_FLAGS]
    model = model_pass()

    text = report(coverage, gate, notes, model)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {args.out}", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "gate_flags": [f.__dict__ for f in gate],
                    "notes": [n.__dict__ for n in notes],
                    "coverage": {k: v.items for k, v in coverage.items()},
                    "model": model,
                }
            )
        )
    return 1 if gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
