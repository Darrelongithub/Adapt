"""Concepts of any subject, taught three ways, and an approved visual that actually shows up.

Two defects live in this file.

**The visual strategy changed nothing.** ``visual_representation`` was in the catalogue,
had a mechanism, had citations — and approving it altered the screen not at all, because
the only drawable things were four hand-authored specs on four maths items. Here: approving
it moves every served question onto a generic template filled from that concept's own
material, and the templates are the four structural ones (flow, comparison table, timeline,
labelled diagram) — not subject-specific drawings.

**There was one paragraph per topic and a maths-only bank.** For any subject, a concept now
offers three versions of itself (text, visual, worked example) and a matched pair of
questions, and the answer key is checked by *quotability from the source* rather than by
arithmetic — so a history or biology item can pass the same rulebook, and anything that
cannot be checked is reported as unverified instead of being served as if it had been.
"""

from __future__ import annotations

import pytest

from adapt.db.models import AdaptationProposal, Student

#: A paragraph with no arithmetic in it anywhere: if anything in this path needed to
#: evaluate an expression, this text could not produce a question at all.
BIOLOGY = (
    "Photosynthesis happens in the chloroplast, which contains the green pigment chlorophyll. "
    "The plant takes in carbon dioxide through the stomata of its leaves and water through its roots. "
    "Light energy splits the water, releasing oxygen as a by-product. "
    "The plant then uses the energy to build glucose from the carbon dioxide. "
    "At night the plant respires and gives out carbon dioxide."
)

HISTORY = (
    "The Industrial Revolution began in Britain in the 18th century. Before it, most cloth was "
    "woven at home by hand, whereas after it factories used machines driven by steam. "
    "The enclosure of common land pushed rural workers towards the towns. "
    "Working conditions in the early mills were dangerous, and children were often employed "
    "because their hands were small enough to reach between the moving parts."
)


def test_a_biology_concept_gets_all_three_versions_with_no_mathematics_anywhere():
    from adapt.learning import content as C

    out = C.concept_versions(topic_id="top_bio", title="Photosynthesis", source_text=BIOLOGY, learner_id="stu_bio")
    d = out.as_dict()

    # text: short steps, and *nothing dropped* — the old behaviour cut at N words and could
    # throw the key sentence away, so coverage is the assertion, not the length.
    steps = d["versions"]["text"]["steps"]
    assert len(steps) >= 3
    assert d["versions"]["text"]["dropped"] == []
    assert d["versions"]["text"]["covered_words"] == d["versions"]["text"]["source_words"]
    assert all(step.get("check") for step in steps), "a step with no quick check is just a shorter paragraph"

    # visual: a generic template, filled, with no slot left blank
    visual = d["versions"]["visual"]
    assert visual["kind"] in {"flow", "comparison_table", "timeline", "labelled_diagram"}
    assert visual["template"] is True
    assert visual["unfilled"] == []
    assert visual["nodes"] or visual["rows"] or visual["labels"]

    # worked example: the material's own sentences, labelled by what they do
    example = d["versions"]["worked_example"]
    assert len(example["steps"]) >= 4
    assert all(step["text"] in BIOLOGY for step in example["steps"]), "a step was invented"

    # questions: two, same format, same difficulty, and the key is quotable from the source
    pair = d["questions"]["pair"]
    assert len(pair) == 2, d["questions"]
    assert d["questions"]["matched"] is True
    assert d["questions"]["same_format"] and d["questions"]["same_difficulty"]
    for q in pair:
        assert q["verification"] == "quotable"
        assert "key_quotable_in_source" in q["checks"]
        answer = next(iter(q["answer_spec"].values()))[0]
        assert answer in BIOLOGY, f"{answer!r} is not in the material, so it is not checkable"
        assert q["source_quote"] and answer in q["source_quote"]


def test_a_history_concept_is_checked_by_quotability_not_by_arithmetic():
    from adapt.learning import content as C

    d = C.concept_versions(topic_id="top_hist", title="The Industrial Revolution", source_text=HISTORY, learner_id="s").as_dict()
    pair = d["questions"]["pair"]
    assert pair, "no question could be written from a history paragraph"
    for q in pair:
        assert q["kind"] == "text"
        assert "formula" not in (q.get("answer_spec") or {}), "a history answer has no expression to evaluate"
        answer = next(iter(q["answer_spec"].values()))[0]
        assert answer in HISTORY
        assert q["verification"] == "quotable"
    # Nothing claims to have been checked that was not.
    assert d["questions"]["verification"] == ["quotable"]
    assert d["unverified"] == []


def test_an_uncheckable_key_is_reported_rather_than_served():
    from adapt.assessment import generated as GEN

    # The answer key is not a word the source uses. Under the old equality check this would
    # have been rejected silently; the point now is that it is rejected *and* the reason is
    # readable, and that a learner never sees an item whose key cannot be verified.
    raw = {
        "kind": "text",
        "stem": "Fill the gap: the pigment in the chloroplast is ________.",
        "answer_value": ["melanin"],
        "difficulty": 0.4,
    }
    validated, rejections = GEN.validate_generated(raw, topic_id="top_bio", source_text=BIOLOGY, require_source=True)
    assert validated is None
    codes = {r.code for r in rejections}
    assert "key_not_in_source" in codes, codes
    assert all(r.detail for r in rejections), "a rejection with no reason is a rejection nobody can act on"
    assert any("melanin" in r.detail or "source" in r.detail for r in rejections), [r.detail for r in rejections]


def test_all_four_visual_templates_can_be_filled_for_any_subject():
    from adapt.learning import visuals as V

    samples = {
        "flow": "First heat the water to boiling. Then add the powder and stir. Finally remove it from the heat.",
        "comparison_table": "Before the change, cloth was woven at home, whereas after it machines did the work.",
        "timeline": "The revolution began in the 18th century. Then came the railway in 1825 and the telegraph later.",
        "labelled_diagram": "The cell consists of a nucleus, a membrane and many smaller parts, and it contains mitochondria.",
    }
    for kind, text in samples.items():
        visual = V.build_visual(title="Sample", text=text, kind=kind)
        assert visual.kind == kind
        assert visual.unfilled == [], f"{kind} has a slot it could not fill from a text that fills it"
        assert visual.alt, "a diagram with no alt text is invisible to a screen reader"
        # and the same text, left to the chooser, must still produce a drawable template
        chosen = V.build_visual(title="Sample", text=text)
        assert chosen.kind in V.TEMPLATES


def test_approving_the_visual_strategy_changes_what_is_served(services, learner, client, items):
    """The defect: approving it changed nothing. The property: after approving, every served
    question is taught through the visual template, and the payload says why."""
    student_id = learner["student_id"]
    headers = learner["headers"]

    before = client.get("/api/v1/practice?count=2", headers=headers).json()["items"]
    assert before, "no practice items to compare against"

    with services.scope() as s:
        # A proposal with a student decision is the only route to an active adaptation, so the
        # fixture creates the proposal and the API records the decision -- the real path.
        row = AdaptationProposal(
            student_id=student_id,
            strategy="visual_representation",
            action="attach_visual",
            action_payload={"visual_kind": "auto"},
            facts_text="items that showed a diagram went better",
            pattern_text="read-only questions lose you",
            hypothesis_text="holding the relations in your head is the cost",
            change_text="add a diagram beside the text",
            ask_text="add diagrams to these topics?",
            review_metric="accuracy_visual_vs_read",
        )
        s.add(row)
        s.commit()
        proposal_id = row.id

    decided = client.post(
        f"/api/v1/adaptations/{proposal_id}/decision",
        json={"decision": "accept", "answered_by": "student"},
        headers=headers,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"

    after = client.get("/api/v1/practice?count=3", headers=headers).json()["items"]
    assert after
    served = [(entry.get("presentation") or {}) for entry in after]
    methods = {(p.get("method_assignment") or {}).get("method") or p.get("method") for p in served}
    assert methods == {"visual"}, f"approving the visual strategy did not change the teaching: {methods}"

    # And the teaching is a *filled template from the concept's own material*, not a placeholder.
    # A concept with no source material is the one honest exception: it keeps the method (so the
    # comparison still counts it) and says in words that there is nothing to draw. Other tests
    # create such topics in this shared database, and which of them a learner is served depends on
    # their id, so the test allows the exception and still demands a real diagram somewhere.
    drawn = 0
    for entry in after:
        teaching = (entry.get("presentation") or {}).get("teaching") or {}
        kind = teaching.get("kind")
        if kind == "no_material":
            assert "no course material" in str(teaching.get("text", "")).lower(), teaching
            continue
        assert kind == "visual", teaching
        visual = teaching.get("visual") or {}
        assert visual.get("template") is True
        assert visual.get("kind") in {"flow", "comparison_table", "timeline", "labelled_diagram"}
        assert visual.get("nodes") or visual.get("rows") or visual.get("labels"), "an empty diagram is not teaching"
        assert visual.get("why"), "the learner can ask why this picture; the answer has to exist"
        drawn += 1
    assert drawn, f"no concept was actually drawn from its material: {[e.get('topic_id') for e in after]}"

    # Revoking gives the learner their old lessons back — an approval is not a trap.
    revoked = client.post("/api/v1/adaptations/strategy/visual_representation/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    restored = client.get("/api/v1/practice?count=3", headers=headers).json()["items"]
    restored_methods = {
        ((e.get("presentation") or {}).get("method_assignment") or {}).get("method") or (e.get("presentation") or {}).get("method")
        for e in restored
    }
    assert restored_methods != {"visual"}, f"revoking did not take effect: {restored_methods}"
