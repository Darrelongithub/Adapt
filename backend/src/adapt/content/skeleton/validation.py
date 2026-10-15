"""Strict, model-independent validation for skeletons and generated item drafts.

No model-generated text is trusted because it arrived under a response schema. This module checks
the full graph and language/content invariants in ordinary Python before constructing a typed Pack.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .types import Concept, KeyFact, Misconception, Option, PackError

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]{1,160}\]\([^)]{1,300}\)")
_MARKDOWN_RE = re.compile(r"```|`|\*\*|__|~~|^\s{0,3}#{1,6}\s", re.MULTILINE)
_LINK_RE = re.compile(r"(?i)(?:https?://|www\.)")
_PLACEHOLDER_RE = re.compile(
    r"\{\{.*?\}\}|\$\{.*?\}|\[(?:TODO|TBD|XXX|placeholder)\b|\{[A-Za-z_]\w*\}",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "sw": "sw",
    "swa": "sw",
    "kiswahili": "sw",
    "swahili": "sw",
    "fr": "fr",
    "fra": "fr",
    "french": "fr",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "de": "de",
    "ger": "de",
    "german": "de",
    "ru": "ru",
    "russian": "ru",
    "zh": "zh",
    "chinese": "zh",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
    "ar": "ar",
    "arabic": "ar",
    "hi": "hi",
    "hindi": "hi",
}

_LANGUAGE_MARKERS: dict[str, frozenset[str]] = {
    "en": frozenset(
        [
            "a",
            "an",
            "the",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "and",
            "or",
            "but",
            "if",
            "then",
            "of",
            "to",
            "in",
            "on",
            "at",
            "for",
            "with",
            "by",
            "from",
            "as",
            "that",
            "this",
            "these",
            "those",
            "it",
            "its",
            "they",
            "their",
            "what",
            "which",
            "when",
            "where",
            "why",
            "how",
            "because",
            "can",
            "could",
            "should",
            "would",
            "one",
            "two",
            "each",
            "both",
            "same",
            "different",
            "use",
            "using",
            "means",
            "makes",
            "gives",
            "keep",
            "compare",
            "check",
            "answer",
            "result",
            "value",
            "idea",
            "works",
            "into",
            "across",
            "first",
            "next",
        ]
    ),
    "sw": frozenset(
        [
            "na",
            "ya",
            "kwa",
            "ni",
            "wa",
            "hii",
            "hizi",
            "hiyo",
            "katika",
            "kama",
            "kuwa",
            "kwamba",
            "pia",
            "au",
            "lakini",
            "kwa",
            "sababu",
            "maana",
            "moja",
            "mbili",
            "yaweza",
            "unaweza",
            "tunatumia",
            "ina",
            "maana",
            "yake",
            "kutoka",
            "baada",
            "kabla",
            "hatua",
            "mfano",
            "jibu",
            "swali",
            "linganisha",
            "thibitisha",
        ]
    ),
    "fr": frozenset(
        [
            "le",
            "la",
            "les",
            "un",
            "une",
            "des",
            "du",
            "de",
            "est",
            "sont",
            "et",
            "ou",
            "mais",
            "si",
            "alors",
            "dans",
            "pour",
            "avec",
            "par",
            "ce",
            "cette",
            "ces",
            "qui",
            "que",
            "quoi",
            "quand",
            "pourquoi",
            "comment",
            "parce",
            "peut",
            "chaque",
            "même",
            "différent",
            "utiliser",
            "résultat",
            "valeur",
            "idée",
            "fonctionne",
            "étape",
            "réponse",
        ]
    ),
    "es": frozenset(
        [
            "el",
            "la",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "de",
            "del",
            "es",
            "son",
            "y",
            "o",
            "pero",
            "si",
            "entonces",
            "en",
            "para",
            "con",
            "por",
            "que",
            "qué",
            "cómo",
            "cuándo",
            "porque",
            "puede",
            "cada",
            "mismo",
            "diferente",
            "usar",
            "resultado",
            "valor",
            "idea",
            "funciona",
            "paso",
            "respuesta",
        ]
    ),
    "pt": frozenset(
        [
            "o",
            "a",
            "os",
            "as",
            "um",
            "uma",
            "de",
            "do",
            "da",
            "é",
            "são",
            "e",
            "ou",
            "mas",
            "se",
            "então",
            "em",
            "para",
            "com",
            "por",
            "que",
            "quando",
            "porque",
            "pode",
            "cada",
            "mesmo",
            "diferente",
            "usar",
            "resultado",
            "valor",
            "ideia",
            "funciona",
            "etapa",
            "resposta",
        ]
    ),
    "de": frozenset(
        [
            "der",
            "die",
            "das",
            "ein",
            "eine",
            "ist",
            "sind",
            "und",
            "oder",
            "aber",
            "wenn",
            "dann",
            "in",
            "für",
            "mit",
            "von",
            "zu",
            "als",
            "dass",
            "diese",
            "was",
            "welche",
            "warum",
            "wie",
            "weil",
            "kann",
            "jeder",
            "gleich",
            "verschieden",
            "verwenden",
            "Ergebnis",
            "Wert",
            "Idee",
            "funktioniert",
            "Schritt",
            "Antwort",
        ]
    ),
}

_SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "ru": ((0x0400, 0x052F),),
    "zh": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF)),
    "ja": ((0x3040, 0x30FF), (0x4E00, 0x9FFF)),
    "ko": ((0xAC00, 0xD7AF), (0x1100, 0x11FF)),
    "ar": ((0x0600, 0x06FF), (0x0750, 0x077F)),
    "hi": ((0x0900, 0x097F),),
}

SUBJECT_FAMILIES = frozenset(
    {
        "maths",
        "physics",
        "statistics",
        "science",
        "sciences",
        "language",
        "languages",
        "history",
        "humanities",
        "other",
        "generic",
    }
)


@dataclass(frozen=True)
class ItemDraft:
    variant: str
    kind: str
    rung: int
    difficulty: float
    stem: str
    options: tuple[Option, ...]
    key: str
    operation: str
    expression: str
    hints: tuple[str, str, str]
    teachback: tuple[str, str, str]
    fact_ids: tuple[str, ...]


def normalize_language(language: str) -> str:
    """Normalize a BCP-47 tag or common language name to a stable lowercase cache/prompt key."""
    raw = " ".join(str(language or "en").strip().split()).lower()
    if not raw:
        return "en"
    base = raw.replace("_", "-").split("-", 1)[0]
    return LANGUAGE_ALIASES.get(raw, LANGUAGE_ALIASES.get(base, base))


def _normalise_words(text: str) -> str:
    return " ".join(_WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def language_matches(texts: Sequence[str], language: str) -> bool:
    """A deterministic language gate for common output languages and non-Latin scripts.

    It is intentionally conservative. For Latin-script languages we require several distinct common
    function words across the complete pack, not a model's self-reported tag. For other supported
    scripts, the returned text must contain a substantial fraction of the requested script.
    """
    joined = " ".join(str(text) for text in texts if text)
    words = _normalise_words(joined).split()
    if len(words) < 12:
        return False
    lang = normalize_language(language)
    markers = _LANGUAGE_MARKERS.get(lang)
    if markers is not None:
        found = set(words) & markers
        return len(found) >= (3 if lang == "en" else 2)
    ranges = _SCRIPT_RANGES.get(lang)
    if ranges:
        letters = [char for char in joined if char.isalpha()]
        if not letters:
            return False
        matches = sum(any(start <= ord(char) <= end for start, end in ranges) for char in letters)
        return matches / len(letters) >= 0.45
    # Unknown Latin-script languages cannot be honestly checked with this small offline gate.
    return False


def plain_text(value: Any, *, field: str, maximum: int, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise PackError("text_type", f"{field} must be a string")
    text = unicodedata.normalize("NFC", " ".join(value.split()))
    if not minimum <= len(text) <= maximum:
        raise PackError("text_length", f"{field} must contain {minimum}-{maximum} characters")
    if _CONTROL_RE.search(text):
        raise PackError("control_character", f"{field} contains a control character")
    if (
        _HTML_RE.search(text)
        or _MARKDOWN_LINK_RE.search(text)
        or _LINK_RE.search(text)
        or _MARKDOWN_RE.search(text)
    ):
        raise PackError("markup_or_link", f"{field} contains markup or a link")
    if _PLACEHOLDER_RE.search(text):
        raise PackError("placeholder", f"{field} contains an unresolved placeholder")
    return text


def _require_keys(value: Any, keys: set[str], *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PackError("object_shape", f"{field} must be an object")
    if set(value) != keys:
        raise PackError("object_shape", f"{field} has missing or unexpected fields")
    return value


def _identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise PackError("identifier", f"{field} is not a valid short identifier")
    return value


def _validate_graph(concepts: Sequence[Concept]) -> tuple[tuple[str, str], ...]:
    by_id = {concept.id: concept for concept in concepts}
    if len(by_id) != len(concepts):
        raise PackError("duplicate_concept_id", "concept ids must be unique")
    for concept in concepts:
        if len(set(concept.prerequisites)) != len(concept.prerequisites):
            raise PackError("duplicate_prerequisite", "a concept repeats a prerequisite")
        for prerequisite in concept.prerequisites:
            if prerequisite not in by_id:
                raise PackError(
                    "unknown_prerequisite", "a prerequisite does not name a concept in the pack"
                )
            if prerequisite == concept.id:
                raise PackError("cyclic_prerequisites", "a concept cannot require itself")

    state: dict[str, int] = {}
    depth: dict[str, int] = {}

    def visit(concept_id: str) -> int:
        marker = state.get(concept_id, 0)
        if marker == 1:
            raise PackError("cyclic_prerequisites", "the prerequisite graph contains a cycle")
        if marker == 2:
            return depth[concept_id]
        state[concept_id] = 1
        prerequisites = by_id[concept_id].prerequisites
        current_depth = 1 + max((visit(value) for value in prerequisites), default=0)
        state[concept_id] = 2
        depth[concept_id] = current_depth
        if current_depth > 8:
            raise PackError(
                "prerequisite_depth", "the prerequisite graph is deeper than eight concepts"
            )
        return current_depth

    for concept in concepts:
        visit(concept.id)
    return tuple(
        (prerequisite, concept.id) for concept in concepts for prerequisite in concept.prerequisites
    )


def parse_skeleton(
    raw: Any, *, requested_language: str
) -> tuple[str, str, tuple[Concept, ...], tuple[tuple[str, str], ...]]:
    """Parse and validate the skeleton response against the frozen Concept contract."""
    root = _require_keys(
        raw,
        {"topic_title", "subject_family", "language", "concepts"},
        field="skeleton response",
    )
    topic_title = plain_text(root["topic_title"], field="topic_title", maximum=160)
    subject_family = str(root["subject_family"] or "").strip().lower()
    if subject_family not in SUBJECT_FAMILIES:
        raise PackError("subject_family", "subject_family is not one of the supported values")
    if normalize_language(str(root["language"])) != normalize_language(requested_language):
        raise PackError(
            "wrong_language", "the skeleton language tag did not match the requested language"
        )
    rows = root["concepts"]
    if not isinstance(rows, list) or not 4 <= len(rows) <= 10:
        raise PackError("concept_count", "a model skeleton must contain four to ten concepts")

    concepts: list[Concept] = []
    all_fact_ids: set[str] = set()
    for index, raw_concept in enumerate(rows):
        row = _require_keys(
            raw_concept,
            {
                "id",
                "name",
                "key_idea",
                "why_it_works",
                "prerequisites",
                "rung",
                "key_facts",
                "misconceptions",
            },
            field=f"concept {index}",
        )
        concept_id = _identifier(row["id"], field="concept id")
        name = plain_text(row["name"], field=f"{concept_id}.name", maximum=120)
        key_idea = plain_text(row["key_idea"], field=f"{concept_id}.key_idea", maximum=400)
        why_it_works = plain_text(
            row["why_it_works"], field=f"{concept_id}.why_it_works", maximum=500
        )
        rung = row["rung"]
        if isinstance(rung, bool) or not isinstance(rung, int) or not 0 <= rung <= 5:
            raise PackError("rung", f"{concept_id}.rung must be an integer from zero to five")
        prerequisites_raw = row["prerequisites"]
        if not isinstance(prerequisites_raw, list) or len(prerequisites_raw) > 10:
            raise PackError(
                "prerequisite_shape", f"{concept_id}.prerequisites must be a short list"
            )
        prerequisites = tuple(
            _identifier(value, field="prerequisite id") for value in prerequisites_raw
        )

        facts_raw = row["key_facts"]
        if not isinstance(facts_raw, list) or not 1 <= len(facts_raw) <= 8:
            raise PackError("fact_count", f"{concept_id} needs one to eight key facts")
        facts: list[KeyFact] = []
        for fact_index, raw_fact in enumerate(facts_raw):
            fact_row = _require_keys(
                raw_fact, {"id", "statement"}, field=f"{concept_id}.key_facts[{fact_index}]"
            )
            fact_id = _identifier(fact_row["id"], field="fact id")
            if fact_id in all_fact_ids:
                raise PackError("duplicate_fact_id", "fact ids must be unique across the pack")
            all_fact_ids.add(fact_id)
            statement = plain_text(fact_row["statement"], field=f"{fact_id}.statement", maximum=400)
            facts.append(KeyFact(id=fact_id, statement=statement))

        misconceptions_raw = row["misconceptions"]
        if not isinstance(misconceptions_raw, list) or len(misconceptions_raw) > 8:
            raise PackError(
                "misconception_shape", f"{concept_id}.misconceptions must be a short list"
            )
        misconceptions: list[Misconception] = []
        local_misconception_ids: set[str] = set()
        fact_statements = {_normalise_words(fact.statement) for fact in facts}
        for misconception_index, raw_misconception in enumerate(misconceptions_raw):
            misconception_row = _require_keys(
                raw_misconception,
                {"id", "wrong_belief", "why_tempting", "correction"},
                field=f"{concept_id}.misconceptions[{misconception_index}]",
            )
            misconception_id = _identifier(misconception_row["id"], field="misconception id")
            if misconception_id in local_misconception_ids:
                raise PackError(
                    "duplicate_misconception_id", "misconception ids must be unique per concept"
                )
            local_misconception_ids.add(misconception_id)
            wrong_belief = plain_text(
                misconception_row["wrong_belief"],
                field=f"{misconception_id}.wrong_belief",
                maximum=220,
            )
            why_tempting = plain_text(
                misconception_row["why_tempting"],
                field=f"{misconception_id}.why_tempting",
                maximum=300,
            )
            correction = plain_text(
                misconception_row["correction"],
                field=f"{misconception_id}.correction",
                maximum=400,
            )
            if _normalise_words(correction) not in fact_statements:
                raise PackError(
                    "misconception_fact_reference",
                    "each misconception correction must quote a key fact in its concept",
                )
            misconceptions.append(
                Misconception(
                    id=misconception_id,
                    wrong_belief=wrong_belief,
                    why_tempting=why_tempting,
                    correction=correction,
                )
            )
        concepts.append(
            Concept(
                id=concept_id,
                name=name,
                key_idea=key_idea,
                why_it_works=why_it_works,
                prerequisites=prerequisites,
                rung=rung,
                key_facts=tuple(facts),
                misconceptions=tuple(misconceptions),
            )
        )

    edges = _validate_graph(concepts)
    learner_text = [topic_title]
    for concept in concepts:
        learner_text.extend((concept.name, concept.key_idea, concept.why_it_works))
        learner_text.extend(fact.statement for fact in concept.key_facts)
        for misconception in concept.misconceptions:
            learner_text.extend(
                (misconception.wrong_belief, misconception.why_tempting, misconception.correction)
            )
    if not language_matches(learner_text, requested_language):
        raise PackError("wrong_language", "the skeleton text does not match the learner's language")
    return topic_title, subject_family, tuple(concepts), edges


def parse_item_pair(
    raw: Any,
    *,
    concept: Concept,
    requested_language: str,
) -> tuple[ItemDraft, ItemDraft]:
    """Strictly parse a concept's A/B pair before verification or cache insertion."""
    root = _require_keys(raw, {"language", "items"}, field="item response")
    if normalize_language(str(root["language"])) != normalize_language(requested_language):
        raise PackError("wrong_language", "the item response language tag did not match")
    rows = root["items"]
    if not isinstance(rows, list) or len(rows) != 2:
        raise PackError("matched_pair", "each concept must have exactly two paired items")
    drafts: dict[str, ItemDraft] = {}
    allowed_misconceptions = {misconception.id for misconception in concept.misconceptions}
    allowed_facts = {fact.id for fact in concept.key_facts}
    all_text: list[str] = []
    for index, raw_item in enumerate(rows):
        row = _require_keys(
            raw_item,
            {
                "variant",
                "kind",
                "rung",
                "difficulty",
                "stem",
                "options",
                "key",
                "computation",
                "hints",
                "teachback",
                "fact_ids",
            },
            field=f"items[{index}]",
        )
        variant = row["variant"]
        if variant not in ("A", "B") or variant in drafts:
            raise PackError(
                "matched_pair", "a pair must contain one item labelled A and one labelled B"
            )
        kind = row["kind"]
        if kind not in ("numeric", "mcq", "factual"):
            raise PackError("item_kind", "item kind must be numeric, mcq, or factual")
        rung = row["rung"]
        if isinstance(rung, bool) or not isinstance(rung, int) or not 0 <= rung <= 5:
            raise PackError("item_rung", "item rung must be an integer from zero to five")
        if abs(rung - concept.rung) > 1:
            raise PackError(
                "difficulty_far_from_rung", "item rung is more than one step from its concept rung"
            )
        difficulty = row["difficulty"]
        if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)):
            raise PackError("difficulty", "item difficulty must be numeric")
        difficulty = float(difficulty)
        if not 0.45 <= difficulty <= 0.95:
            raise PackError(
                "difficulty_far_from_rung", "item difficulty is outside the safe scoring band"
            )
        expected_difficulty = {0: 0.45, 1: 0.45, 2: 0.50, 3: 0.60, 4: 0.72, 5: 0.84}[rung]
        if abs(difficulty - expected_difficulty) > 0.19:
            raise PackError(
                "difficulty_far_from_rung", "item difficulty is far from its declared rung"
            )
        stem = plain_text(row["stem"], field=f"item {variant}.stem", maximum=900, minimum=12)
        key = plain_text(row["key"], field=f"item {variant}.key", maximum=240)

        options_raw = row["options"]
        if not isinstance(options_raw, list) or len(options_raw) > 6:
            raise PackError("options_shape", "item options must be a list of at most six options")
        options: list[Option] = []
        option_ids: set[str] = set()
        for option_index, raw_option in enumerate(options_raw):
            option_row = _require_keys(
                raw_option,
                {"id", "text", "misconception_id"},
                field=f"item {variant}.options[{option_index}]",
            )
            option_id = _identifier(option_row["id"], field="option id")
            if option_id in option_ids:
                raise PackError("duplicate_option_id", "option ids must be unique within an item")
            option_ids.add(option_id)
            option_text = plain_text(
                option_row["text"],
                field=f"item {variant}.option",
                maximum=240,
            )
            misconception_value = option_row["misconception_id"]
            misconception_id = str(misconception_value).strip() or None
            if misconception_id is not None and misconception_id not in allowed_misconceptions:
                raise PackError(
                    "unknown_misconception", "an option refers to a missing misconception"
                )
            options.append(
                Option(id=option_id, text=option_text, misconception_id=misconception_id)
            )
            all_text.append(option_text)

        if kind == "mcq":
            if not 2 <= len(options) <= 6:
                raise PackError("options_count", "an MCQ must have two to six options")
            if key not in option_ids:
                raise PackError("wrong_key", "an MCQ key must name one of its option ids")
            correct = next(option for option in options if option.id == key)
            if correct.misconception_id is not None:
                raise PackError(
                    "correct_option_misconception", "the correct option cannot name a misconception"
                )
            for option in options:
                if option.id != key and option.misconception_id not in allowed_misconceptions:
                    raise PackError(
                        "unmapped_distractor", "every distractor must name a real misconception"
                    )
            if len({_normalise_words(option.text) for option in options}) != len(options):
                raise PackError("duplicate_option", "an MCQ contains duplicate option text")
        else:
            if options:
                raise PackError(
                    "options_kind", "numeric and factual items must not have answer options"
                )
            if kind == "numeric" and not re.fullmatch(
                r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/\d+)?", key
            ):
                raise PackError("numeric_key", "a numeric key must be a plain number or fraction")

        computation = _require_keys(
            row["computation"],
            {"operation", "expression"},
            field=f"item {variant}.computation",
        )
        operation = str(computation["operation"] or "").strip().lower()
        expression = str(computation["expression"] or "").strip()
        if len(expression) > 400:
            raise PackError("computation_length", "the computation is too long")
        if operation not in ("none", "evaluate", "solve", "differentiate", "equivalent"):
            raise PackError("computation_operation", "the computation operation is unsupported")
        if operation == "none" and expression:
            raise PackError(
                "computation_operation", "a non-computational item must have an empty expression"
            )
        if operation != "none" and not expression:
            raise PackError("computation_missing", "a computational item needs an expression")
        if kind == "numeric" and operation == "none":
            raise PackError(
                "computation_missing", "numeric items must include a computable expression"
            )

        hints_raw = row["hints"]
        if not isinstance(hints_raw, list) or len(hints_raw) != 3:
            raise PackError("hint_count", "each item needs exactly three graded hints")
        hints = tuple(
            plain_text(value, field=f"item {variant}.hint", maximum=300) for value in hints_raw
        )
        teachback_raw = row["teachback"]
        if not isinstance(teachback_raw, list) or len(teachback_raw) != 3:
            raise PackError("teachback_count", "each item needs exactly three teach-back checks")
        teachback = tuple(
            plain_text(value, field=f"item {variant}.teachback", maximum=240)
            for value in teachback_raw
        )
        fact_ids_raw = row["fact_ids"]
        if not isinstance(fact_ids_raw, list) or not fact_ids_raw:
            raise PackError("missing_facts", "each item must cite at least one fact")
        fact_ids = tuple(_identifier(value, field="item fact id") for value in fact_ids_raw)
        if len(set(fact_ids)) != len(fact_ids):
            raise PackError("duplicate_item_fact", "an item repeats a fact id")
        if not set(fact_ids).issubset(allowed_facts):
            raise PackError("missing_facts", "an item cites a fact outside its concept")

        all_text.extend((stem, key, *hints, *teachback))
        drafts[variant] = ItemDraft(
            variant=variant,
            kind=kind,
            rung=rung,
            difficulty=difficulty,
            stem=stem,
            options=tuple(options),
            key=key,
            operation=operation,
            expression=expression,
            hints=hints,  # type: ignore[arg-type]
            teachback=teachback,  # type: ignore[arg-type]
            fact_ids=fact_ids,
        )

    if set(drafts) != {"A", "B"}:
        raise PackError(
            "matched_pair", "a pair must contain one item labelled A and one item labelled B"
        )
    first, second = drafts["A"], drafts["B"]
    if first.kind != second.kind:
        raise PackError("matched_pair_format", "A and B must use the same question format")
    if first.rung != second.rung:
        raise PackError("matched_pair_rung", "A and B must be declared at the same rung")
    if abs(first.difficulty - second.difficulty) > 0.12:
        raise PackError(
            "matched_pair_difficulty", "A and B are not close enough in declared difficulty"
        )
    if first.stem.casefold() == second.stem.casefold():
        raise PackError("duplicate_item", "A and B must have different question stems")
    if not language_matches(all_text, requested_language):
        raise PackError("wrong_language", "item text does not match the learner's language")
    return first, second


def normalize_answer(text: str) -> str:
    """Punctuation-insensitive form used only for verifier/key agreement."""
    return _normalise_words(text)


__all__ = [
    "ID_RE",
    "SUBJECT_FAMILIES",
    "ItemDraft",
    "language_matches",
    "normalize_answer",
    "normalize_language",
    "parse_item_pair",
    "parse_skeleton",
    "plain_text",
]
