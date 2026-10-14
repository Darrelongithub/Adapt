"""A stand-in for Phases B and C, so Part E can be built and reviewed before they land.

Part E owns the live-model quality check (`scripts/live_model_check.py`). The pipeline it has to
drive is Phase B's (skeleton -> items -> verification) followed by Phase C's (one lesson per
profile, then the explanation moments). Neither `adapt.content.skeleton` nor
`adapt.ai.lesson_writer` exists in this checkout yet, so the harness codes against the **frozen
contract** in `ADAPT_R2_ALL_PHASES.md` and loads this file instead. When B and C merge,
`--pipeline real` (or the automatic choice) picks the real modules up and nothing else changes.

Shape of the four functions -- copied from the frozen interface, not invented here::

    get_pack(topic, *, level, language, model=None, force_simple=False) -> Pack
    write_lesson(pack, concept_id, snapshot, *, model=None) -> LessonPlan
    explain_after_wrong(pack, item_id, learner_answer, snapshot, memory, *, model=None) -> Explanation
    reteach(pack, concept_id, snapshot, memory, *, model=None) -> Explanation

Everything here is deliberately thin. It calls the model with the frozen five-argument shape
(`model(purpose, system, user, schema, max_tokens) -> dict`), checks only that the reply has the
*shape* the contract names, and otherwise falls back to something it can honestly label
`simple`. It does no quality work of its own: validation, rejection and difficulty measurement
are Phase B's job, and this file must never be mistaken for them.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

#: The block types the frozen Lesson contract allows. Anything else is dropped rather than shown.
BLOCK_TYPES = ("text", "visual", "worked_example", "check", "predict", "explain", "reflect")

#: Methods the stub rotates through for "I don't get it". Phase C's job is to choose the
#: next-best method from the profile; the rotation here exists so a second call is *provably* a
#: different explanation, which is what the harness checks.
RET_EACH_METHODS = ("worked_example", "visual", "analogy", "smaller_steps", "everyday_example")

SKELETON_SYSTEM = (
    "You build a checked knowledge skeleton for one topic. Answer only with JSON that matches "
    "the requested fields. The sentences you write may be read by a learner: never name a model, "
    "a provider, an API or a file, and never state a fact you cannot support."
)
ITEMS_SYSTEM = (
    "You write practice items for one concept. Answer only with JSON. Each item must have one "
    "unambiguous answer, and the answer must not appear in the stem."
)
VERIFY_SYSTEM = (
    "You verify one item. Work the answer out for yourself, then say whether the stated key is "
    "the one you reached and whether the item has exactly one correct option. Answer only with JSON."
)
LESSON_SYSTEM = (
    "You write what one learner reads. Follow the profile: its step size sets how long each "
    "block may be, its lead format decides what opens the lesson, its tone sets the register, "
    "and its language is the only language you may answer in. Follow every explanation block "
    "with exactly one check question. Answer only with JSON."
)
EXPLAIN_SYSTEM = (
    "A learner answered a question incorrectly. Start from what they actually answered, say why "
    "that answer is tempting, say why it fails, then show the idea that does work. Answer only "
    "with JSON."
)
RETEACH_SYSTEM = (
    "The learner did not understand the last explanation. Explain the same idea again, in a "
    "different way, using the method you are given. Do not repeat the earlier wording. Answer "
    "only with JSON."
)

#: Compact field specs, in the shape `adapt.ai.llm._schema_for` consumes (Phase 0's frozen
#: entry point), so a real run constrains the model the same way a stub run pretends to.
SKELETON_SCHEMA = {
    "topic_title": "str",
    "subject_family": "str",
    "concepts": ("array", {"type": "OBJECT"}),
}
ITEMS_SCHEMA = {"items": ("array", {"type": "OBJECT"})}
VERIFY_SCHEMA = {"agrees": "bool", "key": "str", "reason": "str"}
LESSON_SCHEMA = {"blocks": ("array", {"type": "OBJECT"}), "concept_id": "str"}
EXPLANATION_SCHEMA = {"blocks": ("array", {"type": "OBJECT"}), "method": "str", "analogy_id": "str"}


# --------------------------------------------------------------------------- model plumbing --


def _ask(
    model: Callable[..., dict[str, Any]] | None,
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any] | None:
    """One model call, or ``None`` when there is no model or it did not answer.

    A raising model is a model that is not available -- the harness's wrapper records *why* on
    its own call log, so this function does not need to keep the error to report it.
    """
    if model is None:
        return None
    try:
        reply = model(purpose, system, user, schema, max_tokens)
    except Exception:
        return None
    return reply if isinstance(reply, dict) else None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [t for t in (_text(v) for v in value) if t]


# ------------------------------------------------------------------------------- the pack --


def get_pack(
    topic: str,
    *,
    level: int,
    language: str,
    model: Callable[..., dict[str, Any]] | None = None,
    force_simple: bool = False,
) -> dict[str, Any]:
    """Skeleton + items + verification, in one call per concept plus one per item."""
    topic = _text(topic)
    if force_simple:
        return _simple_pack(topic, level, language)
    reply = _ask(
        model,
        "skeleton",
        SKELETON_SYSTEM,
        (
            f"Topic: {topic}\nLearner level: {level} of 5\nLanguage: {language}\n"
            "Write 2 key facts and one common misconception, then the key idea and why it works "
            "for this topic."
        ),
        SKELETON_SCHEMA,
        1400,
    )
    concepts = _concepts(reply, topic, level) if reply else []
    if not concepts:
        return _simple_pack(topic, level, language)
    items = _items(topic, language, concepts, model)
    return {
        "topic_key": _slug(topic),
        "topic_title": _text((reply or {}).get("topic_title")) or topic,
        "subject_family": _text((reply or {}).get("subject_family")) or "general",
        "source": "model",
        "concepts": concepts,
        "prereq_edges": [[concepts[i]["id"], concepts[i + 1]["id"]] for i in range(len(concepts) - 1)],
        "items": items,
        "language": language,
        "level": level,
    }


def _slug(topic: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in topic]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "topic"


def _concepts(reply: Mapping[str, Any], topic: str, level: int) -> list[dict[str, Any]]:
    raw = reply.get("concepts")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:4]):
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name")) or f"{topic} part {index + 1}"
        idea = _text(item.get("key_idea"))
        why = _text(item.get("why_it_works"))
        if not idea:
            continue
        facts = []
        for fact_index, fact in enumerate(item.get("key_facts") or []):
            statement = _text(fact.get("statement") if isinstance(fact, Mapping) else fact)
            if statement:
                facts.append({"id": f"f{index + 1}{fact_index + 1}", "statement": statement})
        misconceptions = []
        for word_index, word in enumerate(item.get("misconceptions") or []):
            if not isinstance(word, Mapping):
                continue
            misconceptions.append(
                {
                    "id": f"m{index + 1}{word_index + 1}",
                    "wrong_belief": _text(word.get("wrong_belief")),
                    "why_tempting": _text(word.get("why_tempting")),
                    "correction": _text(word.get("correction")),
                }
            )
        out.append(
            {
                "id": _text(item.get("id")) or f"c{index + 1}",
                "name": name,
                "key_idea": idea,
                "why_it_works": why,
                "prerequisites": _strings(item.get("prerequisites")),
                "rung": int(item.get("rung") or level),
                "key_facts": facts,
                "misconceptions": misconceptions,
            }
        )
    return out


def _items(
    topic: str,
    language: str,
    concepts: Sequence[Mapping[str, Any]],
    model: Callable[..., dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """One item per concept, then Phase B's verification rule: unverified is never scoring."""
    items: list[dict[str, Any]] = []
    for concept in concepts:
        reply = _ask(
            model,
            "items",
            ITEMS_SYSTEM,
            (
                f"Topic: {topic}\nConcept: {concept['name']}\nKey idea: {concept['key_idea']}\n"
                f"Language: {language}\n"
                "Write one item. Give the stem, the kind (numeric|mcq|factual), the options with "
                "an id and text for each, the key, and where each wrong option goes wrong."
            ),
            ITEMS_SCHEMA,
            900,
        )
        item = _first_item(reply, concept, topic)
        item["verification"] = _verified(model, topic, item)
        items.append(item)
    return items


def _first_item(
    reply: Mapping[str, Any] | None,
    concept: Mapping[str, Any],
    topic: str,
) -> dict[str, Any]:
    raw = (reply or {}).get("items")
    candidate = raw[0] if isinstance(raw, Sequence) and raw and isinstance(raw[0], Mapping) else None
    if candidate is not None:
        stem = _text(candidate.get("stem"))
        key = _text(candidate.get("key"))
        kind = _text(candidate.get("kind")).lower()
        options = []
        for option_index, option in enumerate(candidate.get("options") or []):
            if not isinstance(option, Mapping):
                continue
            options.append(
                {
                    "id": _text(option.get("id")) or f"o{option_index + 1}",
                    "text": _text(option.get("text")),
                    "misconception_id": _text(option.get("misconception_id")) or None,
                }
            )
        if stem and key and kind in ("numeric", "mcq", "factual"):
            return {
                "id": _text(candidate.get("id")) or f"{concept['id']}_i1",
                "concept_id": concept["id"],
                "kind": kind,
                "stem": stem,
                "options": options,
                "key": key,
                "pair_id": _text(candidate.get("pair_id")) or f"{concept['id']}_pair",
                "hints": _strings(candidate.get("hints"))[:3],
                "teachback": _strings(candidate.get("teachback"))[:3],
                "fact_ids": _strings(candidate.get("fact_ids")),
                "verification": "unverified",
            }
    # The honest fallback: a factual item made from the concept's own first fact. It is marked
    # unverified above and Phase B's rule (never used for scoring) is checked by the harness.
    fact = (concept.get("key_facts") or [{"id": "", "statement": ""}])[0]
    return {
        "id": f"{concept['id']}_i1",
        "concept_id": concept["id"],
        "kind": "factual",
        "stem": f"In one sentence: what is the key idea behind {topic}?",
        "options": [],
        "key": concept["key_idea"],
        "pair_id": f"{concept['id']}_pair",
        "hints": [],
        "teachback": [concept["name"]],
        "fact_ids": [fact.get("id")] if fact.get("id") else [],
        "verification": "unverified",
    }


def _verified(
    model: Callable[..., dict[str, Any]] | None,
    topic: str,
    item: Mapping[str, Any],
) -> str:
    """``cross_checked`` only when a *separate* call independently reached the same answer."""
    reply = _ask(
        model,
        "verify",
        VERIFY_SYSTEM,
        f"Topic: {topic}\nItem: {item['stem']}\nStated answer: {item['key']}\nWork it out and compare.",
        VERIFY_SCHEMA,
        400,
    )
    if not reply:
        return "unverified"
    agrees = bool(reply.get("agrees"))
    independently_reached = _text(reply.get("key"))
    if agrees and independently_reached and independently_reached.strip().lower() == item["key"].strip().lower():
        return "cross_checked"
    return "unverified"


def _simple_pack(topic: str, level: int, language: str) -> dict[str, Any]:
    """What is honest to show when no skeleton could be built: one concept, no pretend facts."""
    concept = {
        "id": "c1",
        "name": topic,
        "key_idea": f"This copy could not build a knowledge skeleton for {topic}, so it says only "
        f"that the topic is {topic}.",
        "why_it_works": "Nothing here pretends to know more than the topic's name.",
        "prerequisites": [],
        "rung": level,
        "key_facts": [],
        "misconceptions": [],
    }
    return {
        "topic_key": _slug(topic),
        "topic_title": topic,
        "subject_family": "general",
        "source": "simple",
        "concepts": [concept],
        "prereq_edges": [],
        "items": [],
        "language": language,
        "level": level,
    }


# ----------------------------------------------------------------------------- the lesson --


def _blocks(reply: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    raw = (reply or {}).get("blocks")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[dict[str, Any]] = []
    for block in raw:
        if not isinstance(block, Mapping):
            continue
        kind = _text(block.get("type")).lower()
        body = _text(block.get("body"))
        if kind not in BLOCK_TYPES or not body:
            continue
        why = block.get("why") if isinstance(block.get("why"), Mapping) else {}
        clean = {
            "type": kind,
            "body": body,
            "fact_ids": _strings(block.get("fact_ids")),
            "why": {
                "evidence_ids": _strings((why or {}).get("evidence_ids")),
                "n": int((why or {}).get("n") or 0),
            },
        }
        for extra in ("answer", "options", "item_id"):
            if block.get(extra) not in (None, "", []):
                clean[extra] = block[extra]
        out.append(clean)
    return out


def write_lesson(
    pack: Mapping[str, Any],
    concept_id: str,
    snapshot: Mapping[str, Any],
    *,
    model: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One lesson plan for one concept and one profile."""
    concept = _concept(pack, concept_id)
    profile = _profile(snapshot)
    lead = _text(profile.get("lead_format")) or "text"
    budget = _int(profile.get("max_words_per_block"), 70)
    item = _first_verified(pack)
    # The item travels with the prompt: a check block has to ask a real question and carry the
    # answer the lesson believes, which is what the harness compares against the item's own key.
    item_lines = f"Item: {item['stem']}\nItem answer: {item['key']}\n" if item is not None else ""
    reply = _ask(
        model,
        "lesson",
        LESSON_SYSTEM,
        (
            f"Topic: {pack.get('topic_title')}\nConcept: {concept['name']}\n"
            f"Key idea: {concept['key_idea']}\nWhy it works: {concept['why_it_works']}\n"
            f"Language: {profile.get('language')}\nTone: {profile.get('tone')}\n"
            f"Open with: {lead}\nAt most {budget} words per block.\n"
            f"Facts you may use: {[f['statement'] for f in concept.get('key_facts') or []]}\n"
            + item_lines
            + "Write the blocks, and follow each explanation block with one check question."
        ),
        LESSON_SCHEMA,
        1500,
    )
    blocks = _blocks(reply)
    if blocks:
        _remember(snapshot, "methods", _text((reply or {}).get("method")) or "text")
        return {
            "source": "written_for_you",
            "concept_id": concept_id,
            "blocks": blocks,
            "language": profile.get("language"),
            "profile": profile.get("name"),
        }
    fallback = [
        {
            "type": lead if lead in BLOCK_TYPES else "text",
            "body": f"{concept['key_idea']} {concept['why_it_works']}".strip(),
            "fact_ids": [f["id"] for f in concept.get("key_facts") or []],
            "why": {"evidence_ids": [], "n": 0},
        }
    ]
    item = _first_verified(pack)
    if item is not None:
        fallback.append(
            {
                "type": "check",
                "body": item["stem"],
                "item_id": item["id"],
                "fact_ids": list(item.get("fact_ids") or []),
                "why": {"evidence_ids": [], "n": 0},
            }
        )
    return {
        "source": "simple",
        "concept_id": concept_id,
        "blocks": fallback,
        "language": profile.get("language"),
        "profile": profile.get("name"),
    }


def _profile(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The profile a lesson is written for, as the harness states it.

    Shape assumed until Phase C lands (see ``docs/round2/handoff-e.md``): a mapping with
    ``name``, ``language``, ``tone``, ``lead_format``, ``max_words_per_block``, ``step_size``
    and ``levers``/``prefs``/``memory`` for the real implementation to read.
    """
    out = {k: snapshot.get(k) for k in ("name", "language", "tone", "lead_format", "step_size")}
    out["max_words_per_block"] = _int(snapshot.get("max_words_per_block"), 70)
    return out


def _remember(snapshot: Mapping[str, Any], key: str, value: str) -> None:
    memory = snapshot.get("memory")
    if isinstance(memory, dict):
        memory.setdefault(key, []).append(value)


# ------------------------------------------------------------------------ the explanations --


def explain_after_wrong(
    pack: Mapping[str, Any],
    item_id: str,
    learner_answer: str,
    snapshot: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    model: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The second of Phase C's three moments: start from what *this* learner answered."""
    item = _item(pack, item_id)
    concept = _concept(pack, str(item.get("concept_id") or ""))
    profile = _profile(snapshot)
    reply = _ask(
        model,
        "explain_wrong",
        EXPLAIN_SYSTEM,
        (
            f"Topic: {pack.get('topic_title')}\nQuestion: {item.get('stem')}\n"
            f"The learner answered: {learner_answer}\nThe answer that works: {item.get('key')}\n"
            f"Common misconception: {_misconception_text(concept, item)}\n"
            f"Language: {profile.get('language')}\nAt most "
            f"{_int(profile.get('max_words_per_block'), 70)} words per block.\n"
            "Explain it back to them."
        ),
        EXPLANATION_SCHEMA,
        1200,
    )
    blocks = _blocks(reply)
    method = _text((reply or {}).get("method")) or "misconception_first"
    if blocks:
        return {
            "source": "written_for_you",
            "blocks": blocks,
            "method": method,
            "analogy_id": _text((reply or {}).get("analogy_id")) or None,
        }
    correction = _misconception_text(concept, item) or concept.get("why_it_works", "")
    return {
        "source": "simple",
        "blocks": [
            {
                "type": "explain",
                "body": (
                    f'You answered "{learner_answer}". That answer is a common one, and it is '
                    f"tempting because it fits the way the question is usually asked. It fails "
                    f"here: {correction}"
                ),
                "fact_ids": list(item.get("fact_ids") or []),
                "why": {"evidence_ids": [], "n": 0},
            }
        ],
        "method": "misconception_first",
        "analogy_id": None,
    }


def reteach(
    pack: Mapping[str, Any],
    concept_id: str,
    snapshot: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    model: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The third moment: the same idea, a different way. Never repeating the earlier wording."""
    concept = _concept(pack, concept_id)
    profile = _profile(snapshot)
    used = list(memory.get("methods", [])) if isinstance(memory, Mapping) else []
    method = next((m for m in RET_EACH_METHODS if m not in used), RET_EACH_METHODS[-1])
    reply = _ask(
        model,
        "reteach",
        RETEACH_SYSTEM,
        (
            f"Topic: {pack.get('topic_title')}\nConcept: {concept['name']}\n"
            f"Key idea: {concept['key_idea']}\nMethod to use: {method}\n"
            f"Methods already used: {used or 'none'}\nLanguage: {profile.get('language')}\n"
            f"At most {_int(profile.get('max_words_per_block'), 70)} words per block."
        ),
        EXPLANATION_SCHEMA,
        1200,
    )
    blocks = _blocks(reply)
    if isinstance(memory, dict):
        memory.setdefault("methods", []).append(method)
    if blocks:
        return {
            "source": "written_for_you",
            "blocks": blocks,
            "method": method,
            "analogy_id": _text((reply or {}).get("analogy_id")) or None,
        }
    return {
        "source": "simple",
        "blocks": [
            {
                "type": "explain",
                "body": (
                    f"Here is the same idea by {method.replace('_', ' ')}: "
                    f"{concept['key_idea']} {concept['why_it_works']}"
                ),
                "fact_ids": [],
                "why": {"evidence_ids": [], "n": 0},
            }
        ],
        "method": method,
        "analogy_id": None,
    }


# --------------------------------------------------------------------------------- shapes --


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _concept(pack: Mapping[str, Any], concept_id: str) -> Mapping[str, Any]:
    for concept in pack.get("concepts") or []:
        if concept.get("id") == concept_id:
            return concept
    concepts = pack.get("concepts") or [{"id": "c1", "name": "", "key_idea": "", "why_it_works": ""}]
    return concepts[0]


def _item(pack: Mapping[str, Any], item_id: str) -> Mapping[str, Any]:
    for item in pack.get("items") or []:
        if item.get("id") == item_id:
            return item
    items = pack.get("items") or []
    return items[0] if items else {"id": item_id, "stem": "", "key": "", "hints": [], "options": []}


def _first_verified(pack: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for item in pack.get("items") or []:
        if item.get("verification") in ("computed", "cross_checked"):
            return item
    return None


def _misconception_text(concept: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    wrong_ids = {
        str(option.get("misconception_id"))
        for option in item.get("options") or []
        if option.get("misconception_id")
    }
    for word in concept.get("misconceptions") or []:
        if word.get("id") in wrong_ids or not wrong_ids:
            return f"{word.get('wrong_belief', '')} {word.get('correction', '')}".strip()
    return ""
