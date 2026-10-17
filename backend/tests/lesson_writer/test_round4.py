"""Round 4: authored feedback completeness and plan-wide item identity."""

import pytest

from adapt.ai.lesson_writer import core, explain_after_wrong, write_lesson
from adapt.content.breakdown import breakdown_for
from adapt.content.distractors import authored_distractors
from adapt.content.skeleton.authored import authored_pack
from adapt.subjects.topics import TOPIC_SPECS


def rendered(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(rendered(child) for child in value.values())
    if isinstance(value, (tuple, list)):
        return " ".join(rendered(child) for child in value)
    return ""


def item_ids(value):
    return [
        block.body["item_id"]
        for block in value.blocks
        if block.type in {"check", "predict"}
        and isinstance(block.body, dict)
        and block.body.get("item_id")
    ]


@pytest.mark.parametrize(
    "topic", [spec.key for spec in TOPIC_SPECS if breakdown_for(spec.key).available]
)
def test_every_authored_distractor_has_distinct_feedback(topic):
    # Cover all authored choice rungs, not merely the two used in the lesson pair.
    for concept in breakdown_for(topic).concepts:
        rows = authored_distractors(concept)
        expected = sum(
            len(item["options"]) - 1 for item in concept.kit.values() if item["kind"] == "choice"
        )
        assert len(rows) == expected
        assert all(
            row["wrong_belief"] and row["why_tempting"] and row["correction"] for row in rows
        )
        assert len({row["why_tempting"] for row in rows}) == expected

    pack = authored_pack(topic, level=2, language="en")
    assert pack and pack.items
    concepts = {concept.id: concept for concept in pack.concepts}
    for item in pack.items:
        misconceptions = {row.id: row for row in concepts[item.concept_id].misconceptions}
        texts = []
        for option in item.options:
            if option.id == item.key:
                continue
            assert option.misconception_id in misconceptions
            snapshot = {"preferences": {"language": "mixed"}}
            if item.scorable:
                feedback = explain_after_wrong(pack, item.id, option.id, snapshot, {})
            else:
                # Short mathematical keys cannot pass the general quotation gate.
                # Preserve the public scoring refusal, while testing authored feedback copy.
                refusal = explain_after_wrong(pack, item.id, option.id, snapshot, {})
                assert "item:missing_or_unverified" in refusal.rejection_reasons
                context = core._context(pack, item.concept_id)
                feedback = core._wrong_fallback(
                    context,
                    core._profile(snapshot),
                    item=item.as_dict(),
                    answer_text=option.text,
                    misconception=misconceptions[option.misconception_id].as_dict(),
                )
            text = rendered([block.body for block in feedback.blocks])
            assert "no checked explanation" not in text
            assert "This is the checked answer." not in text
            assert option.text in text
            texts.append(text)
            ids = item_ids(feedback)
            assert item.id not in ids
            assert len(ids) == len(set(ids))
        assert len(texts) == len(set(texts))
    for concept in pack.concepts:
        plan = write_lesson(pack, concept.id, {})
        ids = item_ids(plan)
        assert len(ids) == len(set(ids))


def test_single_item_plan_never_repeats_its_mcq():
    pack = authored_pack("biology", level=2, language="en").as_dict()
    concept = pack["concepts"][1]
    pack["items"] = [item for item in pack["items"] if item["concept_id"] == concept["id"]][:1]
    plan = write_lesson(pack, concept["id"], {"levers": {"step_size": {"value": "small"}}})
    ids = item_ids(plan)
    assert ids
    assert len(ids) == len(set(ids))
