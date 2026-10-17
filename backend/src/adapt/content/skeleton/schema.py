"""Response schemas and budgets for the three constrained model calls.

These use the compact ``response_spec`` format accepted by ``adapt.ai.llm.GeminiLLM``. Array item
schemas are JSON-schema fragments passed through as Gemini's ``responseSchema``; application code
performs the stricter semantic validation after the provider returns.
"""

from __future__ import annotations

from typing import Any

MAX_SKELETON_TOKENS = 3_000
MAX_ITEM_TOKENS = 1_600
MAX_VERIFIER_TOKENS = 450


def _string(*, max_length: int | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "STRING"}
    if max_length is not None:
        spec["maxLength"] = max_length
    return spec


def _integer(minimum: int, maximum: int) -> dict[str, Any]:
    return {"type": "INTEGER", "minimum": minimum, "maximum": maximum}


def _number(minimum: float, maximum: float) -> dict[str, Any]:
    return {"type": "NUMBER", "minimum": minimum, "maximum": maximum}


def _array(
    items: dict[str, Any], *, minimum: int | None = None, maximum: int | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "ARRAY", "items": items}
    if minimum is not None:
        out["minItems"] = minimum
    if maximum is not None:
        out["maxItems"] = maximum
    return out


def _object(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # Keep to Gemini's OpenAPI-subset responseSchema fields. Every key is required here on
    # purpose: the skeleton call that uses these schemas either receives a complete pack or
    # nothing at all, and ``_require_keys`` re-checks the same contract after the provider
    # returns. Marking a field optional in the schema while rejecting its absence later would
    # only move the failure from generation to validation. (The compact response-spec format
    # used by the tutor calls does support ``?``-optional fields; this builder is for the
    # all-or-nothing skeleton.)
    return {
        "type": "OBJECT",
        "properties": properties,
        "required": list(properties),
    }


FACT_SCHEMA = _object({"id": _string(max_length=64), "statement": _string(max_length=400)})
MISCONCEPTION_SCHEMA = _object(
    {
        "id": _string(max_length=64),
        "wrong_belief": _string(max_length=220),
        "why_tempting": _string(max_length=300),
        # The correction must quote one key fact exactly (after whitespace folding). This is the
        # frozen contract's fact reference without adding an undocumented field to Misconception.
        "correction": _string(max_length=400),
    }
)
CONCEPT_SCHEMA = _object(
    {
        "id": _string(max_length=64),
        "name": _string(max_length=120),
        "key_idea": _string(max_length=400),
        "why_it_works": _string(max_length=500),
        "prerequisites": _array(_string(max_length=64), maximum=10),
        "rung": _integer(0, 5),
        "key_facts": _array(FACT_SCHEMA, minimum=1, maximum=8),
        "misconceptions": _array(MISCONCEPTION_SCHEMA, maximum=8),
    }
)

SKELETON_RESPONSE_SPEC: dict[str, Any] = {
    "topic_title": "str",
    "subject_family": "str",
    "language": "str",
    "concepts": ("array", CONCEPT_SCHEMA),
}

OPTION_SCHEMA = _object(
    {
        "id": _string(max_length=64),
        "text": _string(max_length=240),
        # Empty string for the correct option; every distractor must name a misconception id.
        "misconception_id": _string(max_length=64),
    }
)
COMPUTATION_SCHEMA = _object(
    {
        "operation": _string(max_length=24),
        "expression": _string(max_length=400),
    }
)
ITEM_SCHEMA = _object(
    {
        "variant": _string(max_length=1),
        "kind": _string(max_length=16),
        "rung": _integer(0, 5),
        "difficulty": _number(0.0, 1.0),
        "stem": _string(max_length=900),
        "options": _array(OPTION_SCHEMA, maximum=6),
        # For mcq, key is the id of exactly one option; for numeric/factual it is the answer text.
        "key": _string(max_length=240),
        # Use operation="none" and an empty expression for non-computational items.
        "computation": COMPUTATION_SCHEMA,
        "hints": _array(_string(max_length=300), minimum=3, maximum=3),
        "teachback": _array(_string(max_length=240), minimum=3, maximum=3),
        "fact_ids": _array(_string(max_length=64), minimum=1, maximum=8),
    }
)
ITEMS_RESPONSE_SPEC: dict[str, Any] = {
    "language": "str",
    "items": ("array", ITEM_SCHEMA),
}

VERIFIER_RESPONSE_SPEC: dict[str, Any] = {
    "answer": "str",
    # Must enumerate all options the facts support, not just the preferred one. This lets code
    # reject an MCQ with more than one independently correct option.
    "correct_option_ids": ("array", {"type": "STRING", "maxLength": 64}),
    "fact_ids": ("array", {"type": "STRING", "maxLength": 64}),
}

__all__ = [
    "COMPUTATION_SCHEMA",
    "ITEMS_RESPONSE_SPEC",
    "ITEM_SCHEMA",
    "MAX_ITEM_TOKENS",
    "MAX_SKELETON_TOKENS",
    "MAX_VERIFIER_TOKENS",
    "SKELETON_RESPONSE_SPEC",
    "VERIFIER_RESPONSE_SPEC",
]
