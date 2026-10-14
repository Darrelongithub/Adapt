"""Phase 4's gate (brief §7) as a test, so a content change cannot quietly break it.

``scripts/teaching_quality_eval.py`` is the deliverable the brief names: it walks every
deterministic teaching item — the authored maths bank, the subject round's pools for all five
subject families, and the pop-up lesson's three formats — and flags off-topic text, a missing
worked example, a generic hint, placeholder leakage and a key that disagrees with the example.
This test runs the same walks in-process and asserts the gate is clean, plus the coverage the
brief asks for (at least 40 questions across at least 6 subjects).

It is deliberately the *same code*: two implementations of "is this content good" would
disagree with each other within a week. If the script burns in a content edit, this test says so
before a learner does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _eval_module():
    """Load ``scripts/teaching_quality_eval.py`` as a module (it is a script, not a package)."""
    path = REPO / "scripts" / "teaching_quality_eval.py"
    spec = importlib.util.spec_from_file_location("teaching_quality_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["teaching_quality_eval"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def walk():
    """Run the three walks once and hand back ``(gate flags, notes, coverage)``."""
    module = _eval_module()
    coverage: dict = {}
    flags: list = []
    notes: list = []
    module.walk_bank(coverage, flags, notes)
    module.walk_subject_pools(coverage, flags, notes)
    module.walk_popup_lessons(coverage, flags, notes)
    gate = [f for f in flags if f.kind in module.GATE_FLAGS]
    return module, gate, notes, coverage


def test_the_deterministic_path_carries_no_gate_flags(walk):
    """The five flags the brief names, across every deterministic item in the repository.

    This is the claim ``QUALITY_REPORT.md`` makes ("zero flags on the deterministic path"), so it
    is asserted here rather than only printed there.
    """
    _, gate, _, coverage = walk
    if gate:
        lines = "\n".join(f"  {f.kind}: {f.where} -- {f.detail}" for f in gate[:20])
        pytest.fail(f"{len(gate)} content flag(s) on the deterministic path:\n{lines}")
    assert sum(c.questions for c in coverage.values()) > 0, "the walk found no questions at all"


def test_the_gate_covers_the_subjects_the_brief_names(walk):
    """At least 40 questions across at least 6 subjects (brief §7's own wording)."""
    _, _, _, coverage = walk
    questions = sum(c.questions for c in coverage.values())
    topics = {topic for c in coverage.values() for topic in c.topics}
    families = {name for name in coverage}
    assert questions >= 40, f"only {questions} questions were walked"
    # "Subjects" is what a learner picks: the named topics (calculus, government, biology, ...),
    # not the five internal families -- the brief asked for six subjects and the content covers
    # far more than six named ones.
    assert len(topics) >= 6, f"only {len(topics)} named topics across families {sorted(families)}"
    for family in ("maths", "languages", "sciences", "history", "generic"):
        assert family in coverage, f"{family} has no content at all"


def test_every_bank_item_has_three_hints_and_one_worked_example(walk):
    """§4.1.3 and §4.1.4: no item may be missing its example, and hints come in three grades."""
    from adapt.assessment import bank as BANK
    from adapt.assessment import worked as WORKED

    missing = []
    for topic in BANK.TOPICS:
        for raw in BANK.ITEMS.get(topic["code"], []):
            item = dict(raw)
            item.setdefault("topic_id", topic["code"])
            item.setdefault("topic_title", topic["title"])
            rendered = dict(BANK.render_item_for(item, seed=0))
            hints = WORKED.ensure_hints(rendered)
            steps = WORKED.ensure_example(rendered, render=lambda r: BANK.render_item_for(r, seed=0))
            if len(hints) < 3 or not steps:
                missing.append((topic["code"], item.get("stem", "")[:40], len(hints), len(steps)))
    assert not missing, f"items without a full hint ladder or an example: {missing[:6]}"


def test_the_checklist_graders_use_the_strict_three_point_format():
    """§6.2: a fixed-checklist grader, three points, strict output — the pool content has them."""
    from adapt.subjects import games as GAMES
    from adapt.subjects import taxonomy as TAX

    checklists = 0
    for family in TAX.FAMILY_IDS:
        for skill in TAX.skills_for(family):
            for task in GAMES.pool_for(skill.id):
                rubric = task.get("rubric") or {}
                if isinstance(rubric, dict) and rubric.get("required_concepts"):
                    checklists += 1
                    assert len(rubric["required_concepts"]) == 3, (
                        f"{skill.id}: a checklist with {len(rubric['required_concepts'])} points, not 3"
                    )
    assert checklists >= 3, "no fixed-checklist graded content was found to check"
