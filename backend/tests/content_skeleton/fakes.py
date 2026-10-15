"""Scripted model responses for the frozen Pack callable interface."""

from __future__ import annotations

import json
import re
from typing import Any

FACT_TEXT = "A primary source is a firsthand account created at the time being studied."


def skeleton(*, language: str = "en", cyclic: bool = False) -> dict[str, Any]:
    concepts: list[dict[str, Any]] = []
    for index in range(4):
        concept_id = f"concept_{index + 1}"
        prerequisites = [f"concept_{index}"] if index else []
        if cyclic and index == 0:
            prerequisites = ["concept_4"]
        concepts.append(
            {
                "id": concept_id,
                "name": f"Source evidence concept {index + 1}",
                "key_idea": "A source provides evidence that helps explain an event.",
                "why_it_works": "Comparing sources helps readers check what happened and why.",
                "prerequisites": prerequisites,
                "rung": 3,
                "key_facts": [
                    {"id": f"fact_{index + 1}", "statement": FACT_TEXT},
                ],
                "misconceptions": [
                    {
                        "id": f"mis_{index + 1}_a",
                        "wrong_belief": "A later summary is always a firsthand account.",
                        "why_tempting": "A summary may sound confident and include many details.",
                        "correction": FACT_TEXT,
                    },
                    {
                        "id": f"mis_{index + 1}_b",
                        "wrong_belief": "A source needs no evidence to support its claim.",
                        "why_tempting": "A familiar claim can feel true without being checked.",
                        "correction": FACT_TEXT,
                    },
                ],
            }
        )
    return {
        "topic_title": "Sources in history",
        "subject_family": "history",
        "language": language,
        "concepts": concepts,
    }


def _untrusted_json(user: str, start: str, end: str) -> dict[str, Any]:
    match = re.search(re.escape(start) + r"\s*(.*?)\s*" + re.escape(end), user, re.DOTALL)
    if not match:
        raise AssertionError(f"expected {start} data delimiters")
    return json.loads(match.group(1))


class ScriptedModel:
    """Deterministic fake that records calls and independently solves its own MCQs."""

    def __init__(
        self,
        *,
        mode: str = "valid",
        skeleton_language: str = "en",
    ) -> None:
        self.mode = mode
        self.skeleton_language = skeleton_language
        self.calls: list[dict[str, Any]] = []
        self.pair_count = 0

    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "purpose": purpose,
                "system": system,
                "user": user,
                "schema": schema,
                "max_tokens": max_tokens,
            }
        )
        if purpose == "content_skeleton":
            if self.mode == "malformed":
                return {"unexpected": "no concepts"}
            use_language = "fr" if self.mode == "wrong_language" else self.skeleton_language
            result = skeleton(language=use_language, cyclic=self.mode == "cyclic")
            if self.mode == "malformed_extra_key":
                result["extra"] = "not allowed"
            return result
        if purpose == "content_items":
            request = _untrusted_json(
                user, "UNTRUSTED_CONCEPT_DATA_BEGIN", "UNTRUSTED_CONCEPT_DATA_END"
            )
            concept = request["concept"]
            self.pair_count += 1
            return self._pair(concept)
        if purpose == "content_item_verifier":
            request = _untrusted_json(
                user,
                "UNTRUSTED_VERIFICATION_DATA_BEGIN",
                "UNTRUSTED_VERIFICATION_DATA_END",
            )
            item = request["item"]
            facts = " ".join(fact["statement"] for fact in request["facts"])
            correct = [
                option["id"]
                for option in item["options"]
                if option["text"].casefold() in facts.casefold()
            ]
            if self.mode == "wrong_verifier_key" and correct:
                wrong = next(
                    option["id"] for option in item["options"] if option["id"] not in correct
                )
                return {
                    "answer": wrong,
                    "correct_option_ids": [wrong],
                    "fact_ids": item["fact_ids"],
                }
            return {
                "answer": correct[0] if len(correct) == 1 else "",
                "correct_option_ids": correct,
                "fact_ids": item["fact_ids"],
            }
        raise AssertionError(f"unexpected model purpose: {purpose}")

    def _pair(self, concept: dict[str, Any]) -> dict[str, Any]:
        fact_id = concept["key_facts"][0]["id"]
        correct_id = "opt_correct"
        wrong_key = "opt_later"
        selected_key = wrong_key if self.mode == "wrong_key" else correct_id
        correct_text = FACT_TEXT
        if self.mode == "leaked_answer":
            stem_a = f"Which statement is correct: {correct_text}?"
        else:
            concept_name = concept["name"].lower()
            stem_a = f"Which description best fits {concept_name} in this history task?"
        concept_name = concept["name"].lower()
        stem_b = f"What should a careful reader check before using {concept_name} as evidence?"
        misconception_a, misconception_b = concept["misconceptions"]
        options = [
            {
                "id": correct_id,
                "text": correct_text,
                "misconception_id": misconception_a["id"] if self.mode == "wrong_key" else "",
            },
            {
                "id": wrong_key,
                "text": "a later summary written by someone who was not there",
                "misconception_id": "" if self.mode == "wrong_key" else misconception_a["id"],
            },
            {
                "id": "opt_guess",
                "text": "a guess that has no source to support it",
                "misconception_id": misconception_b["id"],
            },
        ]
        hints = [
            "First, recall what the source tells you.",
            "Then compare its timing with the event.",
            "Finally, check who created it and what they witnessed.",
        ]
        if self.mode == "leaked_hint":
            hints[2] = FACT_TEXT
        teachback = [
            "Explain how timing can affect a source.",
            "Describe what makes an account firsthand.",
            "Give one reason to compare different sources.",
        ]
        items = []
        for variant, stem in (("A", stem_a), ("B", stem_b)):
            items.append(
                {
                    "variant": variant,
                    "kind": "mcq",
                    "rung": 3,
                    "difficulty": 0.60,
                    "stem": stem,
                    "options": options,
                    "key": selected_key,
                    "computation": {"operation": "none", "expression": ""},
                    "hints": hints,
                    "teachback": teachback,
                    "fact_ids": [fact_id],
                }
            )
        return {"language": "en", "items": items}


class ScriptedArithmeticModel:
    """Constrained numeric pair fake; symbolic verification is exercised in code, not mocked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(
        self, purpose: str, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        self.calls.append(purpose)
        if purpose == "content_skeleton":
            result = skeleton()
            result["subject_family"] = "maths"
            result["topic_title"] = "Arithmetic practice"
            return result
        if purpose == "content_items":
            request = _untrusted_json(
                user, "UNTRUSTED_CONCEPT_DATA_BEGIN", "UNTRUSTED_CONCEPT_DATA_END"
            )
            fact_id = request["concept"]["key_facts"][0]["id"]
            common = {
                "kind": "numeric",
                "rung": 3,
                "difficulty": 0.60,
                "options": [],
                "computation": {"operation": "evaluate", "expression": "6*7"},
                "hints": [
                    "First identify the operation in the expression.",
                    "Then multiply the two numbers carefully.",
                    "Finally check that the product fits the question.",
                ],
                "teachback": [
                    "Explain how multiplication combines equal groups.",
                    "Describe how you checked the product.",
                    "Give another expression with the same result.",
                ],
                "fact_ids": [fact_id],
            }
            concept_id = request["concept"]["id"]
            first = {
                **common,
                "variant": "A",
                "stem": f"Calculate 6 multiplied by 7 for {concept_id}. What result do you get?",
                "key": "42",
            }
            second = {
                **common,
                "variant": "B",
                "stem": f"Work out 8 plus 9 for {concept_id}. What result do you get?",
                "key": "17",
                "computation": {"operation": "evaluate", "expression": "8+9"},
            }
            return {"language": "en", "items": [first, second]}
        raise AssertionError(f"unexpected model purpose: {purpose}")
