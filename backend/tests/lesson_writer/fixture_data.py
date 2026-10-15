from __future__ import annotations

import pytest


@pytest.fixture(name="fixture_pack")
def _fixture_pack() -> dict:
    """A hand-built Pack matching the frozen Part 0 contract; never imports B's skeleton."""
    return {
        "topic_key": "fractions",
        "topic_title": "Equal shares",
        "subject_family": "maths",
        "source": "authored",
        "concepts": [
            {
                "id": "fraction-addition",
                "name": "Adding fractions",
                "key_idea": "Fractions name equal-sized parts of one whole.",
                "why_it_works": "A shared denominator names parts of the same size.",
                "prerequisites": [],
                "rung": 2,
                "key_facts": [
                    {
                        "id": "f-fractions",
                        "statement": "Fractions name equal-sized parts of one whole.",
                    },
                    {
                        "id": "f-denominator",
                        "statement": "A shared denominator names parts of the same size.",
                    },
                    {
                        "id": "f-add",
                        "statement": (
                            "When part sizes match, add the numerators and keep the denominator."
                        ),
                    },
                ],
                "misconceptions": [
                    {
                        "id": "m-add-denominators",
                        "wrong_belief": "Add the denominators too",
                        "why_tempting": (
                            "The numerator and denominator are both numbers in the fraction."
                        ),
                        "correction": (
                            "The denominator names the part size, so it stays the same "
                            "when equal-sized parts are added."
                        ),
                    }
                ],
            }
        ],
        "prereq_edges": [],
        "items": [
            {
                "id": "item-fraction-a",
                "concept_id": "fraction-addition",
                "kind": "mcq",
                "stem": "What does a shared denominator name?",
                "options": [
                    {"id": "same-size", "text": "The same part size", "misconception_id": None},
                    {
                        "id": "add-both",
                        "text": "Add the denominators too",
                        "misconception_id": "m-add-denominators",
                    },
                ],
                "key": "same-size",
                "pair_id": "fraction-pair-1",
                "hints": ["Look at the part size."],
                "teachback": ["Explain why the part size stays the same."],
                "fact_ids": ["f-denominator", "f-add"],
                "verification": "computed",
            },
            {
                "id": "item-fraction-b",
                "concept_id": "fraction-addition",
                "kind": "mcq",
                "stem": "When sizes match, what do you add?",
                "options": [
                    {"id": "numerators", "text": "The numerators", "misconception_id": None},
                    {
                        "id": "denominators",
                        "text": "The denominators",
                        "misconception_id": "m-add-denominators",
                    },
                ],
                "key": "numerators",
                "pair_id": "fraction-pair-1",
                "hints": [],
                "teachback": [],
                "fact_ids": ["f-add"],
                "verification": "cross_checked",
            },
        ],
    }


@pytest.fixture(name="snapshot")
def _snapshot() -> dict:
    return {
        "levers": {
            "step_size": {"value": "small", "source": "measured"},
            "lead_format_prior": {"value": "worked_example", "source": "stated"},
            "tone": {"value": "gentle", "source": "stated"},
        },
        "preferences": {"language": "en", "feedback_style": "gentle"},
        "learner_rung": 2,
        "recent_evidence": {"rows": [], "evidence_ids": [], "n": 0},
        "lesson_explanations": [],
    }
