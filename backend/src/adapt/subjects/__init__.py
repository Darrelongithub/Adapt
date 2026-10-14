"""Phase 3: the subject round (round brief section 6).

Tier 2 of the brief — the skills of a whole subject, valid for any branch inside it — and Tier 3, the
topic ladder. What lives here:

* :mod:`adapt.subjects.taxonomy` — the versioned skill sets (maths, languages, sciences,
  history/humanities/government, plus the generic fallback).
* :mod:`adapt.subjects.topics` — the authored topic table and its lean ratings.
* :mod:`adapt.subjects.leanings` — ``rate_leanings``: authored first, a constrained model second,
  the labelled generic profile last.
* :mod:`adapt.subjects.resolve` — free text to subject, topic and level hint.
* :mod:`adapt.subjects.plan` — which skills the round tests, the cross-check, the time cap, and the
  ``not_measured`` record of everything it skipped.
* :mod:`adapt.subjects.games` — the authored items and the deterministic scorers.
* :mod:`adapt.subjects.ladder` — rungs 0-5, the prerequisite chain and the staircase.
* :mod:`adapt.subjects.curriculum` — the authored chains.
* :mod:`adapt.subjects.methodtest` — the 90-second pop-up lesson and its seeded counterbalance.
* :mod:`adapt.subjects.service` — the lifecycle the API calls.

Nothing here computes a learner's mastery. That is AI 1's job and it is already done: this package
reads ``p_know`` from a store snapshot and writes evidence back through ``state.ingest(...,
module="code")``.
"""

from .leanings import LEANING_SCALE, Leanings, rate_leanings
from .plan import NOT_MEASURED, PlannedSkill, SkippedSkill, TestPlan, plan_tests
from .resolve import Resolved, resolve_topic
from .service import Subjects, SubjectsError
from .taxonomy import (
    FAMILY_IDS,
    SKILLS,
    TAXONOMY_VERSION,
    Skill,
    skills_for,
    taxonomy_payload,
    validate_taxonomy,
)
from .topics import AUTHORED_TOPICS, TOPIC_SPECS, TopicSpec

__all__ = [
    "AUTHORED_TOPICS",
    "FAMILY_IDS",
    "LEANING_SCALE",
    "NOT_MEASURED",
    "PlannedSkill",
    "Resolved",
    "SKILLS",
    "Skill",
    "Subjects",
    "SubjectsError",
    "TAXONOMY_VERSION",
    "TOPIC_SPECS",
    "TestPlan",
    "TopicSpec",
    "Leanings",
    "SkippedSkill",
    "plan_tests",
    "rate_leanings",
    "resolve_topic",
    "skills_for",
    "taxonomy_payload",
    "validate_taxonomy",
]
