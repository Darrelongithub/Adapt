"""Round 2 / Part A regressions: closing prompts, MCQ hints, key ideas.

Task 4: the closing "your turn" used to tell every learner to copy the
first line of the worked example and change only the numbers — nonsense
for a multiple-choice or recall item. The prompt is now chosen by item
kind, and kinds nothing fits get none.

Task 5: generated MCQ hints wrapped the stem ("which one is right:
<stem>"), identical across items once the stem was removed. They now
come from each distractor's own misconception, and the teaching-quality
gate flags any residual hint text shared by three or more items.

Task 6: every bank concept has an authored one-sentence key idea and
why-it-works, shown before the worked steps in simple-mode explanations.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from adapt.ai import local_tutor as LT
from adapt.assessment import bank, worked as W

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _eval_module():
    spec = importlib.util.spec_from_file_location("teaching_quality_eval", ROOT / "scripts" / "teaching_quality_eval.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rendered(code: str, kind: str):
    for row_code, item in bank.as_rows():
        if row_code == code and item.get("kind") == kind:
            rendered = bank.render_item_for(item, seed=3)
            rendered["topic_id"] = code
            rendered["topic_title"] = next(t["title"] for t in bank.TOPICS if t["code"] == code)
            return rendered
    raise AssertionError(f"no {kind} item in {code}")


def _index_for(code: str, item):
    materials = [
        (f"m{i}", code, body, {"topic_title": title})
        for i, (title, body) in enumerate(bank.materials_for(code))
    ]
    return LT.build_index(materials=materials, items=[item])


# --------------------------------------------------------------------- task 4


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"kind": "numeric"}, "change only the numbers"),
        ({"kind": "mcq"}, "cover the options"),
        ({"kind": "factual"}, "without looking"),
    ],
)
def test_closing_prompt_matches_item_kind(item, expected):
    prompt = LT.closing_prompt(item, has_example=True)
    assert prompt and expected in prompt


@pytest.mark.parametrize("item", [None, {}, {"kind": "teachback"}, {"kind": "worked_example"}, {"kind": "simulation"}])
def test_closing_prompt_is_none_when_nothing_fits(item):
    assert LT.closing_prompt(item, has_example=True) is None


def test_explanation_for_mcq_does_not_say_change_the_numbers():
    item = _rendered("integer_ops", "mcq")
    out = LT.explain(
        "why do multiplying and dividing come before adding and subtracting?",
        index=_index_for("integer_ops", item),
        topic_title=item["topic_title"],
        topic_id="integer_ops",
        item=item,
        seed="a-test",
    )
    turns = [s["text"] for s in out.sections if s["label"] == "your turn"]
    assert turns, "an mcq explanation should still close with a try-it prompt"
    assert "cover the options" in turns[0]
    assert "change only the numbers" not in turns[0]


# --------------------------------------------------------------------- task 6


def test_every_bank_concept_has_a_one_sentence_key_idea_and_why():
    codes = {t["code"] for t in bank.TOPICS}
    assert codes, "the bank has topics"
    for code in codes:
        entry = LT.concept_idea_for(code)
        assert entry is not None, f"no authored key idea for {code}"
        for field in ("key_idea", "why_it_works"):
            text = entry[field].strip()
            assert text.endswith("."), (code, field, text)
            assert text.count(".") == 1, f"{code}.{field} must be one sentence: {text!r}"


def test_key_idea_comes_before_the_worked_example():
    item = _rendered("fraction_add", "numeric")
    out = LT.explain(
        item["stem"],
        index=_index_for("fraction_add", item),
        topic_title=item["topic_title"],
        topic_id="fraction_add",
        item=item,
        seed="a-test",
    )
    labels = [s["label"] for s in out.sections]
    assert "key idea" in labels and "why it works" in labels
    if "worked example" in labels:
        assert labels.index("key idea") < labels.index("worked example")
        assert labels.index("why it works") < labels.index("worked example")
    turns = [s["text"] for s in out.sections if s["label"] == "your turn"]
    assert turns and "change only the numbers" in turns[0]


# --------------------------------------------------------------------- task 5


def test_generated_mcq_hints_come_from_distractor_misconceptions():
    checked = 0
    for code, raw in bank.as_rows():
        if raw.get("kind") != "mcq":
            continue
        item = bank.render_item_for(raw, seed=3)
        reasons = W.distractor_reasons(item)
        if not reasons:
            continue
        hints = W.ensure_hints(item)
        joined = " ".join(hints).lower()
        assert "which one is right" not in joined, (code, hints)
        assert item["stem"].lower() not in joined, (code, hints)
        assert any(why.lower().rstrip(".") in joined for _text, why in reasons), (code, hints, reasons)
        checked += 1
    assert checked >= 10, f"only {checked} mcq items with misconceptions were checked"


def test_no_residual_hint_is_shared_by_three_or_more_items():
    """The strengthened gate, run as a unit test over the whole bank."""
    quality = _eval_module()
    entries = []
    for n, (code, raw) in enumerate(bank.as_rows()):
        item = bank.render_item_for(raw, seed=3)
        options = [str(o.get("text") or "") for o in item.get("options") or [] if isinstance(o, dict)]
        authored = [str(h).strip() for h in (item.get("hints") or []) if str(h or "").strip()]
        for hint in W.ensure_hints(item):
            # Like the gate: authored ladders are item-specific by
            # construction; only generated hints are checked for repeats.
            if hint not in authored:
                entries.append((f"item-{n}", hint, item.get("stem"), options))
    assert quality.repeated_hint_flags(entries) == []


def test_repeated_hint_detection_ignores_stems_and_quoted_options():
    quality = _eval_module()
    entries = [
        (
            f"item-{n}",
            f"Stem number {n}? Look again at “alpha” — think about the whole thing first.",
            f"Stem number {n}?",
            ["alpha", "beta"],
        )
        for n in range(3)
    ]
    flags = quality.repeated_hint_flags(entries)
    assert flags and flags[0].kind == "generic_hint"
    # Two items sharing a residual is not yet a pattern.
    assert quality.repeated_hint_flags(entries[:2]) == []
