"""Read-only adapter from the repository's authored topic chains into the Pack contract."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any

from ...assessment import generated as G
from ...assessment.bank import topic_catalog
from ...subjects.resolve import resolve_topic
from .. import breakdown as B
from .. import pack as authored_pack_builder
from ..validation import validate_item
from .types import Concept, Item, KeyFact, Misconception, Option, Pack
from .validation import normalize_language, plain_text

log = logging.getLogger("adapt.content.skeleton.authored")


def _slug(value: str, *, prefix: str, maximum: int = 58) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "")).strip("_.:-")
    if not text or not text[0].isalpha():
        text = f"{prefix}_{text}"
    return text[:maximum]


def _concept_text(value: Any, *, field: str, maximum: int) -> str:
    return plain_text(str(value or ""), field=field, maximum=maximum)


def _fact_id(concept_id: str, suffix: str) -> str:
    return _slug(f"fact_{concept_id}_{suffix}", prefix="fact")


def _misconception_id(concept_id: str, index: int) -> str:
    return _slug(f"mis_{concept_id}_{index + 1}", prefix="mis")


def _resolved_topic(topic: str):
    try:
        return resolve_topic(topic, bank=topic_catalog())
    except Exception:
        return resolve_topic(topic)


def authored_pack(topic: str, *, level: int, language: str) -> Pack | None:
    """Return an authored chain pack where one exists.

    Never call a model or mutate authored data.
    """
    if normalize_language(language) != "en":
        # Authored material is in English. Do not silently claim that it matches another language
        # or let a translation model rewrite its keys; use the localized generic fallback instead.
        return None
    resolved = _resolved_topic(topic)
    if not resolved.topic_key:
        return None
    breakdown = B.breakdown_for(resolved.topic_key)
    if not breakdown.available or not breakdown.concepts:
        return None

    concepts: list[Concept] = []
    facts_by_id: dict[str, KeyFact] = {}
    fact_ids_by_concept: dict[str, set[str]] = {}
    misconceptions_by_concept: dict[str, tuple[Misconception, ...]] = {}
    authored_concepts: dict[str, Any] = {}
    for source_concept in breakdown.concepts:
        concept_id = _slug(source_concept.id, prefix="concept")
        kit = dict(source_concept.kit or {})
        description = _concept_text(
            source_concept.description, field="authored concept description", maximum=400
        )
        key_facts: list[KeyFact] = []
        fact_ids_by_concept[concept_id] = set()
        overview_id = _fact_id(concept_id, "overview")
        overview = KeyFact(id=overview_id, statement=description)
        key_facts.append(overview)
        facts_by_id[overview.id] = overview
        fact_ids_by_concept[concept_id].add(overview.id)
        for rung, raw_item in sorted(kit.items()):
            if not isinstance(raw_item, Mapping):
                continue
            explanation = str(raw_item.get("why") or "").strip()
            if not explanation:
                continue
            statement = _concept_text(explanation, field="authored item rationale", maximum=400)
            fact = KeyFact(id=_fact_id(concept_id, f"r{int(rung)}"), statement=statement)
            if fact.id not in facts_by_id and len(key_facts) < 8:
                key_facts.append(fact)
                facts_by_id[fact.id] = fact
                fact_ids_by_concept[concept_id].add(fact.id)

        rationale = str(
            (kit.get(2) or {}).get("why") or (kit.get(5) or {}).get("why") or description
        )
        why_it_works = _concept_text(rationale, field="authored concept explanation", maximum=500)
        misconceptions: list[Misconception] = []
        for index, raw_misconception in enumerate(source_concept.misconceptions or ()):
            if not isinstance(raw_misconception, Mapping):
                continue
            wrong = str(raw_misconception.get("claim") or "").strip()
            correction = str(raw_misconception.get("contrast") or "").strip()
            if not wrong or not correction:
                continue
            try:
                wrong_text = _concept_text(wrong, field="authored near-miss", maximum=220)
                correction_text = _concept_text(correction, field="authored contrast", maximum=400)
            except Exception:
                continue
            fact_id = _fact_id(concept_id, f"near_miss_{index + 1}")
            fact = KeyFact(id=fact_id, statement=correction_text)
            if len(key_facts) >= 8 or fact_id in facts_by_id:
                continue
            key_facts.append(fact)
            facts_by_id[fact_id] = fact
            fact_ids_by_concept[concept_id].add(fact_id)
            misconceptions.append(
                Misconception(
                    id=_misconception_id(concept_id, index),
                    wrong_belief=wrong_text,
                    # The authored source records this as a near-miss but does not claim why a
                    # learner chose it. Preserve that uncertainty instead of inventing a motive.
                    why_tempting=(
                        "The authored check includes this as a near-miss; "
                        "no further reason was recorded."
                    ),
                    correction=correction_text,
                )
            )
        prerequisites = tuple(
            _slug(value, prefix="concept") for value in source_concept.prerequisites
        )
        concepts.append(
            Concept(
                id=concept_id,
                name=_concept_text(
                    source_concept.title, field="authored concept name", maximum=120
                ),
                key_idea=description,
                why_it_works=why_it_works,
                prerequisites=prerequisites,
                rung=3,
                key_facts=tuple(key_facts),
                misconceptions=tuple(misconceptions),
            )
        )
        misconceptions_by_concept[concept_id] = tuple(misconceptions)
        authored_concepts[concept_id] = source_concept

    # Validate the authored graph with the same rules as a model skeleton. Existing source data is
    # read-only; if it drifts outside the contract, fall back rather than rewriting it.
    from .validation import _validate_graph

    edges = _validate_graph(concepts)
    items: list[Item] = []
    used_stems: list[str] = []
    for concept in concepts:
        source_concept = authored_concepts[concept.id]
        assembled = authored_pack_builder.build_concept_pack(
            source_concept,
            topic_key=breakdown.topic_key,
            topic_title=breakdown.title,
        )
        hints = tuple(str(row.get("text") or "").strip() for row in assembled.hints[:3])
        teachback = tuple(
            str(row.get("statement") or row.get("idea") or "").strip()
            for row in assembled.checklist[:3]
        )
        if len(hints) != 3 or len(teachback) != 3 or any(not text for text in (*hints, *teachback)):
            continue
        pair_rows = [assembled.pair.get("A"), assembled.pair.get("B")]
        if not all(isinstance(row, Mapping) for row in pair_rows):
            continue
        converted: list[Item] = []
        pair_stems: list[str] = []
        for leg, row in zip(("A", "B"), pair_rows, strict=True):
            raw_choices = row.get("choices") or []
            answer_spec = row.get("answer_spec") or {}
            answer_index = answer_spec.get("correct_index")
            if not isinstance(answer_index, int) or not 0 <= answer_index < len(raw_choices):
                converted = []
                break
            stem = str(row.get("stem") or "").strip()
            rationale_fact_id = _fact_id(concept.id, f"r{3 if leg == 'A' else 4}")
            if rationale_fact_id not in fact_ids_by_concept.get(concept.id, set()):
                converted = []
                break
            source_text = "\n".join(fact.statement for fact in concept.key_facts)
            gate_payload = {
                "id": str(row.get("id") or ""),
                "kind": "mcq",
                "stem": stem,
                "choices": [
                    {"text": str(choice.get("text") or ""), "correct": index == answer_index}
                    for index, choice in enumerate(raw_choices)
                ],
                "answer_spec": {"correct_index": answer_index},
            }
            verdict = validate_item(gate_payload, source_text=source_text)
            if not verdict.ok:
                converted = []
                break
            normalised_stem = G.normalise_stem(stem)
            if normalised_stem in {G.normalise_stem(old) for old in (*used_stems, *pair_stems)}:
                converted = []
                break
            options: list[Option] = []
            for index, choice in enumerate(raw_choices):
                text = str(choice.get("text") or "").strip()
                linked = next(
                    (
                        misconception.id
                        for misconception in misconceptions_by_concept[concept.id]
                        if G.normalise_stem(misconception.wrong_belief) == G.normalise_stem(text)
                    ),
                    None,
                )
                options.append(
                    Option(
                        id=f"{leg.lower()}_{index + 1}",
                        text=text,
                        misconception_id=None if index == answer_index else linked,
                    )
                )
            item_id = G.content_id(
                topic_id=breakdown.topic_key,
                kind="mcq",
                stem=stem,
                answer_spec={"correct_index": answer_index},
            )
            verification = (
                "cross_checked"
                if verdict.verification in {"quotable", "arithmetic", "symbolic"}
                else "unverified"
            )
            if verification == "unverified":
                log.warning(
                    "authored_item_unverified topic_key=%s concept_id=%s leg=%s "
                    "code=key_not_independently_checked",
                    breakdown.topic_key,
                    concept.id,
                    leg,
                )
            converted.append(
                Item(
                    id=item_id,
                    concept_id=concept.id,
                    kind="mcq",
                    stem=stem,
                    options=tuple(options),
                    key=f"{leg.lower()}_{answer_index + 1}",
                    pair_id="pair_"
                    + hashlib.sha256(f"{breakdown.topic_key}:{concept.id}".encode()).hexdigest()[
                        :16
                    ],
                    hints=hints,  # type: ignore[arg-type]
                    teachback=teachback,  # type: ignore[arg-type]
                    fact_ids=(rationale_fact_id,),
                    verification=verification,
                )
            )
            pair_stems.append(stem)
        if len(converted) == 2:
            items.extend(converted)
            used_stems.extend(pair_stems)

    return Pack(
        topic_key=breakdown.topic_key,
        topic_title=breakdown.title,
        subject_family=str(resolved.family or "other"),
        source="authored",
        concepts=tuple(concepts),
        prereq_edges=edges,
        items=tuple(items),
    )
