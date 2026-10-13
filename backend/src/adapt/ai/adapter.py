"""AI 3 — the adaptation layer: the lesson *this* learner sees.

Round brief §8. Takes the learner's profile (as the baseline's levers), the
subject map, the topic ladder position, a content pack and recent evidence,
and produces one validated lesson plan: an ordered list of blocks
(``text``, ``visual``, ``worked_example``, ``check``, ``predict``, ``explain``,
``reflect``), each with content, a ``chunk_id``, and a ``why`` that references
evidence ids and sample sizes.

The governing rule is the brief's: **the AI proposes, code decides.**

* This module imports no other AI module (the architecture test's rule, and
  the reason a lesson can be rebuilt from a snapshot: every input arrives as
  plain data — levers, a pack, evidence rows — and every output is plain
  JSON). A model, when one is configured, is an *injected callable* whose
  proposal is validated by :func:`validate_model_plan` and rejected whole —
  never repaired silently — on any violation.
* The deterministic template adapter (:meth:`LessonAdapter.plan`) is not a
  degraded stub for when the model is away. In simple mode it *is* the
  adapter, so it is built to the same contract and tested to the same limits.
* Every limit is code: the word cap per block comes from the working-memory
  lever, every explanation chunk is followed by exactly one check question,
  no sentence is ever cut in half, and a model block that invents markup,
  contradicts the pack, or leans on an example the learner was never shown
  does not reach the learner at all.

A plan is a pure function of its inputs: the same levers, pack and evidence
produce the identical plan, chunk ids included. That determinism is what lets
the contrast demo (and its test) assert that two profiles get *different*
lessons rather than merely believing it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..learning import methods as METHODS_MOD
from ..learning import segments as SEG
from ..lesson import templates as TPL

# --------------------------------------------------------------------------- #
# the contract
# --------------------------------------------------------------------------- #

BLOCK_KINDS: tuple[str, ...] = ("text", "visual", "worked_example", "check", "predict", "explain", "reflect")

#: Word cap per explanation block, by the step-size lever (brief §5.4: the
#: working-memory estimate is what sizes a step). A single sentence longer than
#: the cap is kept whole and flagged — the cap groups sentences, it never
#: amputates one (brief §4.1.6, the defect this round exists to not repeat).
STEP_WORD_LIMITS: dict[str, int] = {"small": 30, "medium": 50, "large": 75}
DEFAULT_STEP_SIZE = "medium"

#: Lead formats a plan can open with, and the block kind each one opens with.
LEAD_BLOCK_KIND: dict[str, str] = {
    "visual": "visual",
    "worked_example": "worked_example",
    "explanation": "text",
    "text": "text",
    "written": "text",
    "retrieval": "predict",
}

#: How many evidence ids a ``why`` carries. Enough to audit the decision,
#: few enough to stay readable in the transparency panel.
MAX_EVIDENCE_REFS = 5

#: Hard ceilings no input — stated, measured or modelled — may raise.
MAX_BLOCKS = 24
MAX_TOTAL_WORDS = 1400

_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z!]|javascript:|data:text/html", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|\[(TODO|TBD|XXX|placeholder)\b", re.IGNORECASE)
_INJECTION = re.compile(
    r"ignore (all |the )?(previous|prior|the above|checklist|rubric|instructions|rules)"
    r"|answer yes to all|mark every answer|give (yourself )?full marks"
    r"|system\s*:|you are now|disregard|override the|reveal (the|your) (prompt|instructions)",
    re.IGNORECASE,
)
_EXAMPLE_REF = re.compile(r"\bexample\s+([A-Za-z0-9_-]+)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|never|no longer|n't|does not|do not|isn't|aren't|changes|changed|different)\b", re.IGNORECASE)

#: Scaffolding words, by explanation language. The *content* is never machine
#: translated by this module — pretending otherwise would be inventing a
#: translation — so when the pack is not in the learner's language the plan
#: says so in the block's ``note`` and the scaffolding still speaks their
#: language. ``mixed`` shows both, English first.
STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "check": "Quick check: {q}",
        "predict": "Before you see it: {q}",
        "predict_default": "Before you see it: what do you think happens next in {concept}?",
        "explain": "In your own words: {q}",
        "explain_default": "In your own words: explain {concept} so someone meeting it for the first time would follow you.",
        "reflect": "Look back before you move on.",
        "confidence": "How sure are you of that answer — guessing, fairly sure, or certain?",
        "break": "You have been working steadily. A short break now will hold better than pushing on tired.",
        "worked": "Work through this example step by step.",
        "recall_first": "You have met {concept} before, so start by recalling it rather than re-reading it.",
    },
    "sw": {
        "check": "Swali fupi: {q}",
        "predict": "Kabla ya kuona: {q}",
        "predict_default": "Kabla ya kuona: unafikiri nini kitatokea baadaye kuhusu {concept}?",
        "explain": "Kwa maneno yako mwenyewe: {q}",
        "explain_default": "Kwa maneno yako mwenyewe: eleza {concept} ili mtu anayekutana nacho mara ya kwanza akuelewe.",
        "reflect": "Tazama nyuma kabla ya kuendelea.",
        "confidence": "Una uhakika gani na jibu hilo — unakisia, una hakika kiasi, au una hakika kabisa?",
        "break": "Umefanya kazi kwa utulivu. Mapumziko mafupi sasa yatasaidia zaidi kuliko kuendelea ukiwa umechoka.",
        "worked": "Fuata mfano huu hatua kwa hatua.",
        "recall_first": "Umewahi kukutana na {concept} kabla, kwa hivyo anza kwa kukikumbuka badala ya kukisoma tena.",
    },
}


def _strings(language: str) -> dict[str, str]:
    if language in ("sw", "kiswahili", "mixed"):
        return STRINGS["sw"] if language != "mixed" else STRINGS["en"]
    return STRINGS["en"]


def _bilingual(language: str, key: str, **kw: Any) -> str:
    """One scaffolding line in the lesson's language (both, when ``mixed``)."""
    if language == "mixed":
        return _strings("en")[key].format(**kw) + " / " + _strings("sw")[key].format(**kw)
    return _strings(language)[key].format(**kw)


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #

def _words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def _sentences(text: str) -> list[str]:
    return SEG.sentences(text or "")


def _chunk_id(concept: str, kind: str, text: str) -> str:
    """Content-addressed chunk id: the same chunk is the same id in any plan.

    That stability is what teaching memory keys on — "this exact explanation
    was already shown" is an id lookup, not a similarity judgement.
    """
    digest = hashlib.sha256(f"{concept}\x00{kind}\x00{text}".encode("utf-8")).hexdigest()[:12]
    return f"chk_{digest}"


def _lever(levers: Mapping[str, Any] | None, name: str) -> tuple[Any, str, str]:
    """One lever as ``(value, why, source)`` — the frozen §5.4 shape.

    Accepts the lever dicts ``baseline.factors.levers`` produces
    (``{"value", "why", "source"}``) and plain values, because a caller
    holding a stored snapshot should not have to re-wrap what it stored.
    """
    raw = (levers or {}).get(name)
    if isinstance(raw, Mapping):
        return raw.get("value"), str(raw.get("why") or ""), str(raw.get("source") or "default")
    if raw is None:
        return None, "", "default"
    return raw, "", "stated"


def _as_rows(recent_evidence: Any) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Normalise recent evidence to ``(rows, evidence_ids, flags)``.

    Accepted shapes: a list of attempt rows; ``{"rows": [...]}``;
    ``{"evidence_ids": [...], "n": int, "methods": [...]}``; or a snapshot-ish
    ``{"ids": [...]}``. Anything else is treated as no evidence — a plan built
    on nothing measured must say so, not guess.
    """
    if recent_evidence is None:
        return [], [], {}
    if isinstance(recent_evidence, Mapping):
        rows = [dict(r) for r in (recent_evidence.get("rows") or recent_evidence.get("attempts") or []) if isinstance(r, Mapping)]
        ids = [str(i) for i in (recent_evidence.get("evidence_ids") or recent_evidence.get("ids") or [])]
        flags = dict(recent_evidence.get("flags") or {})
        for key in ("fatigued", "fatigue", "early_guessing", "predict_first", "overconfident", "underconfident"):
            if key in recent_evidence:
                flags.setdefault(key, recent_evidence[key])
        if not ids:
            ids = [str(r.get("id") or r.get("event_id") or r.get("attempt_id")) for r in rows if r.get("id") or r.get("event_id") or r.get("attempt_id")]
        return rows, ids, flags
    if isinstance(recent_evidence, Sequence):
        rows = [dict(r) for r in recent_evidence if isinstance(r, Mapping)]
        ids = [str(r.get("id") or r.get("event_id") or r.get("attempt_id")) for r in rows if r.get("id") or r.get("event_id") or r.get("attempt_id")]
        return rows, ids, {}
    return [], [], {}


def _subject_row(subject_map: Any, topic_id: str | None) -> dict[str, Any]:
    """The learner's row for this topic, from any subject-map shape."""
    if isinstance(subject_map, Mapping):
        topics = subject_map.get("topics") or subject_map.get("mastery")
        if isinstance(topics, Sequence):
            for row in topics:
                if isinstance(row, Mapping) and (not topic_id or str(row.get("topic_id")) == str(topic_id)):
                    return dict(row)
        if topic_id and isinstance(subject_map.get(topic_id), Mapping):
            return dict(subject_map[topic_id])  # type: ignore[arg-type]
        if subject_map.get("p_know") is not None:
            return dict(subject_map)
    if isinstance(subject_map, Sequence):
        for row in subject_map:
            if isinstance(row, Mapping) and (not topic_id or str(row.get("topic_id")) == str(topic_id)):
                return dict(row)
    return {}


# --------------------------------------------------------------------------- #
# the content pack, normalised
# --------------------------------------------------------------------------- #

@dataclass
class Pack:
    """One concept's checked content, in the single shape the adapter reads.

    Phase 4's engine, phase 4's predecessor (``learning.content``) and a
    hand-authored pack all arrive in slightly different envelopes; this is
    the normalisation that keeps the adapter honest about which envelope it
    got. ``source`` records it, because "written for you, not from your
    notes" is a claim the lesson has to be able to repeat.
    """

    concept: str = ""
    title: str = ""
    topic_id: str | None = None
    content_id: str | None = None
    source: str = "pack"
    language: str | None = None
    explanation: list[str] = field(default_factory=list)  # sentences, in order
    worked_steps: list[dict[str, Any]] = field(default_factory=list)
    visual_spec: dict[str, Any] | None = None
    questions: list[dict[str, Any]] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    answer_values: list[str] = field(default_factory=list)
    example_ids: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)

    def as_ref(self) -> dict[str, Any]:
        return {"concept": self.concept, "title": self.title, "topic_id": self.topic_id, "content_id": self.content_id, "source": self.source}


def _sentences_of(value: Any) -> list[str]:
    if isinstance(value, str):
        return _sentences(value)
    if isinstance(value, Mapping):
        steps = value.get("steps") or value.get("chunks") or []
        out: list[str] = []
        for step in steps:
            if isinstance(step, Mapping) and step.get("text"):
                out.extend(_sentences(str(step["text"])))
            elif isinstance(step, str):
                out.extend(_sentences(step))
        if out:
            return out
        return _sentences(str(value.get("text") or value.get("body") or ""))
    if isinstance(value, Sequence):
        out = []
        for item in value:
            out.extend(_sentences_of(item))
        return out
    return []


def _steps_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        steps = value.get("steps") or []
        out = []
        for i, step in enumerate(steps):
            if isinstance(step, Mapping) and step.get("text"):
                out.append({"index": i + 1, "text": str(step["text"]), "why": str(step.get("why") or "")})
            elif isinstance(step, str):
                out.append({"index": i + 1, "text": step, "why": ""})
        return out
    return []


def _questions_of(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw: Any = pack.get("questions") or pack.get("matched_pair") or pack.get("question_pair") or []
    if isinstance(raw, Mapping):
        raw = raw.get("pair") or raw.get("questions") or ([raw] if raw.get("question") or raw.get("stem") else [])
    out: list[dict[str, Any]] = []
    for q in raw if isinstance(raw, Sequence) else []:
        if not isinstance(q, Mapping):
            continue
        question = q.get("question") or q.get("stem") or q.get("prompt")
        if question:
            out.append(
                {
                    "question": str(question),
                    "hint": str(q.get("hint") or ""),
                    "id": str(q.get("id") or ""),
                    "format": str(q.get("format") or q.get("kind") or ""),
                }
            )
    # A pack's checks may live with its text version (learning.content shape).
    versions = pack.get("versions") or pack.get("variants") or {}
    if not out and isinstance(versions, Mapping):
        text_version = versions.get("text") or {}
        for step in (text_version.get("steps") or []) if isinstance(text_version, Mapping) else []:
            check = step.get("check") if isinstance(step, Mapping) else None
            if isinstance(check, Mapping) and check.get("question"):
                out.append({"question": str(check["question"]), "hint": str(check.get("hint") or ""), "id": "", "format": "recall"})
    return out


def normalise_pack(pack: Mapping[str, Any] | None, *, concept: str = "", title: str = "") -> Pack:
    """Read any supported pack envelope into a :class:`Pack`.

    With no pack at all, returns an empty pack for the concept: the adapter
    then builds the honest lesson — a recall/predict skeleton that says there
    is no checked content yet — rather than inventing prose. The route layer
    normally fills the pack from the authored bank first (see
    ``api/routes/lesson_plan.py``); this path exists so the adapter is
    testable, and truthful, before phase 4 merges.
    """
    raw: Mapping[str, Any] = pack or {}
    versions = raw.get("versions") or raw.get("variants") or {}
    if not isinstance(versions, Mapping):
        versions = {}

    explanation: list[str] = []
    for candidate in (
        raw.get("explanation"),
        raw.get("source_text"),
        raw.get("text"),
        raw.get("body"),
        versions.get("written"),
        versions.get("explanation"),
        versions.get("text"),
    ):
        explanation = _sentences_of(candidate)
        if explanation:
            break

    worked = _steps_of(raw.get("worked_example") or versions.get("worked_example") or {})
    if not worked and raw.get("example_steps"):
        worked = _steps_of({"steps": raw.get("example_steps")})

    visual_raw = raw.get("visual") or versions.get("visual")
    visual_spec: dict[str, Any] | None = None
    if isinstance(visual_raw, Mapping) and (visual_raw.get("kind") or visual_raw.get("steps") or visual_raw.get("nodes") or visual_raw.get("rows")):
        candidate = dict(visual_raw)
        # learning.visuals calls its table "comparison_table" already, but its
        # node list is called `nodes`; the template schema accepts both.
        candidate.setdefault("title", str(raw.get("title") or title or concept))
        visual_spec = candidate

    hints_raw = raw.get("hints") or []
    hints = [str(h.get("text") if isinstance(h, Mapping) else h) for h in hints_raw if h]

    facts = [str(f) for f in (raw.get("facts") or raw.get("key_facts") or [])]
    if not facts and raw.get("objectives"):
        facts = []  # objectives are aims, not facts; conflating them invents claims
    answers: list[str] = []
    for key in ("answer", "answer_value", "answer_values"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            answers.append(value)
        elif isinstance(value, Sequence):
            answers.extend(str(v) for v in value if v)
    for q in _questions_of(raw):
        pass  # question answers are deliberately not harvested: a check is not a key

    example_ids = [str(e) for e in (raw.get("example_ids") or [])]
    if worked and raw.get("example_id"):
        example_ids.append(str(raw["example_id"]))

    return Pack(
        concept=str(raw.get("concept") or concept or raw.get("topic_id") or ""),
        title=str(raw.get("title") or title or raw.get("concept") or concept or "This concept"),
        topic_id=raw.get("topic_id"),
        content_id=raw.get("content_id") or raw.get("id") or raw.get("pack_id"),
        source=str(raw.get("source") or ("empty" if not raw else "pack")),
        language=raw.get("language"),
        explanation=explanation,
        worked_steps=worked,
        visual_spec=visual_spec,
        questions=_questions_of(raw),
        hints=hints,
        facts=facts,
        answer_values=answers,
        example_ids=example_ids,
        misconceptions=[str(m) for m in (raw.get("misconceptions") or [])],
    )


def pack_from_item(item: Mapping[str, Any], *, worked_steps: Sequence[Mapping[str, Any]] = (), source_text: str = "") -> dict[str, Any]:
    """A pack envelope built from one authored bank item (the no-pack fallback).

    The route uses this when phase 4 has no pack for the concept: the bank's
    own item, its verified worked example and the topic's material become the
    pack, labelled ``authored_bank`` so nobody mistakes it for generated
    content.
    """
    return {
        "concept": str(item.get("topic_id") or ""),
        "title": str(item.get("topic_title") or item.get("title") or "This concept"),
        "topic_id": item.get("topic_id"),
        "source": "authored_bank",
        "explanation": source_text,
        "worked_example": {"steps": [dict(s) for s in worked_steps]},
        "questions": [{"question": str(item.get("stem") or ""), "id": str(item.get("id") or ""), "format": str(item.get("kind") or "")}] if item.get("stem") else [],
    }


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Limits:
    """The code-enforced limits for one lesson, derived — never negotiated."""

    max_words_per_block: int
    step_size: str
    language: str
    tone: str
    reading_level: str
    max_blocks: int = MAX_BLOCKS
    timers_visible: bool = True
    pace: str = "steady"

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_words_per_block": self.max_words_per_block,
            "step_size": self.step_size,
            "language": self.language,
            "tone": self.tone,
            "reading_level": self.reading_level,
            "max_blocks": self.max_blocks,
            "timers_visible": self.timers_visible,
            "pace": self.pace,
            "rules": [
                "every explanation chunk is followed by exactly one check question",
                "no sentence is cut in half to fit a block; a long sentence travels whole and is flagged",
                "a picture that fails validation is shown in words, with the reason",
            ],
        }


def derive_limits(*, levers: Mapping[str, Any] | None, preferences: Mapping[str, Any] | None, constraints: Mapping[str, Any] | None) -> Limits:
    step_value, _why, _src = _lever(levers, "step_size")
    step_size = str(step_value or DEFAULT_STEP_SIZE).lower()
    if step_size not in STEP_WORD_LIMITS:
        step_size = DEFAULT_STEP_SIZE
    cap = STEP_WORD_LIMITS[step_size]
    constraints = constraints or {}
    if constraints.get("max_words_per_block"):
        # A constraint may tighten the cap; it may not loosen it past the
        # learner's measured step size. That asymmetry is the limit being a
        # limit rather than a suggestion.
        cap = min(cap, max(10, int(constraints["max_words_per_block"])))
    language_value, _w, _s = _lever(levers, "language")
    language = str((preferences or {}).get("language") or language_value or "en").lower()
    if language not in ("en", "sw", "kiswahili", "mixed"):
        language = "en"
    tone_value, _w2, _s2 = _lever(levers, "tone")
    tone = str((preferences or {}).get("feedback_style") or tone_value or "straight").lower()
    reading = str(constraints.get("reading_level") or (preferences or {}).get("reading_level") or "standard").lower()
    if reading not in ("plain", "standard", "advanced"):
        reading = "standard"
    if reading == "plain":
        cap = min(cap, 30)
    timers_value, _w3, _s3 = _lever(levers, "timers_visible")
    timers_visible = True
    if timers_value is not None:
        timers_visible = bool(timers_value)
    if (preferences or {}).get("timers_help") is not None:
        timers_visible = bool((preferences or {})["timers_help"])
    pace_value, _w4, _s4 = _lever(levers, "pace")
    pace = str(pace_value or "steady").lower()
    if pace not in ("steady", "short_blocks", "measured"):
        pace = "steady"
    return Limits(max_words_per_block=cap, step_size=step_size, language=language, tone=tone, reading_level=reading,
                  timers_visible=timers_visible, pace=pace)


def _reading_ok(text: str, level: str) -> tuple[bool, str]:
    """A small, honest reading-level gate.

    Not a Flesch score dressed up as science: two transparent measures
    (sentence length, long-word share) with stated thresholds, used only to
    *reject* model prose that plainly ignores a declared level — the
    deterministic adapter's own text comes from the checked pack.
    """
    if level == "advanced":
        return True, ""
    sentences = _sentences(text)
    if not sentences:
        return True, ""
    avg = sum(_words(s) for s in sentences) / len(sentences)
    words = re.findall(r"[A-Za-z']+", text)
    long_share = (sum(1 for w in words if len(w) >= 10) / len(words)) if words else 0.0
    if level == "plain" and (avg > 18 or long_share > 0.18):
        return False, f"reading level 'plain' was asked for, but this averages {avg:.0f} words a sentence with {long_share:.0%} long words"
    if level == "standard" and avg > 30:
        return False, f"this averages {avg:.0f} words a sentence, past the standard-level ceiling of 30"
    return True, ""


def _language_ok(text: str, language: str) -> tuple[bool, str]:
    """Refuse prose in plainly the wrong language (stopword heuristic).

    Only fires on a clear signal — at least three marker words of the other
    language and none of the requested one — because a short block may
    legitimately carry none. ``mixed`` accepts either.
    """
    if language == "mixed" or not text:
        return True, ""
    low = f" {text.lower()} "
    en_markers = sum(low.count(f" {w} ") for w in ("the", "and", "you", "this", "with", "because"))
    sw_markers = sum(low.count(f" {w} ") for w in ("na", "ya", "wa", "kwa", "ni", "au", "kabla", "maneno"))
    if language in ("sw", "kiswahili") and en_markers >= 3 and sw_markers == 0:
        return False, "Kiswahili was asked for, but this reads as English"
    if language == "en" and sw_markers >= 3 and en_markers == 0:
        return False, "English was asked for, but this reads as Kiswahili"
    return True, ""


# --------------------------------------------------------------------------- #
# why
# --------------------------------------------------------------------------- #

def _why(text: str, *, lever: str, source: str, evidence_ids: Sequence[str], n: int) -> dict[str, Any]:
    """The provenance every block carries: a sentence, and its receipts."""
    return {
        "text": text,
        "lever": lever,
        "source": source,
        "evidence_ids": list(evidence_ids)[:MAX_EVIDENCE_REFS],
        "n": int(n),
    }


# --------------------------------------------------------------------------- #
# the deterministic adapter
# --------------------------------------------------------------------------- #

class AdaptationError(ValueError):
    """Raised for inputs no adapter may build a lesson from (never for taste)."""


def _choose_lead(
    *,
    levers: Mapping[str, Any] | None,
    subject_row: Mapping[str, Any],
    rows: list[dict[str, Any]],
    evidence_ids: list[str],
    constraints: Mapping[str, Any],
    pack: Pack,
) -> tuple[str, dict[str, Any]]:
    """Which format opens the lesson, and the receipts for opening with it.

    Priority, highest first: an approved strategy (the learner said yes to a
    change, and approving it must visibly change the lesson — §8); measured
    method evidence with enough observations to rank; the stated format
    choice; the default worked example.
    """
    directives = constraints.get("directives") or constraints.get("approved_strategies") or constraints.get("active_adaptations")
    if isinstance(directives, (list, tuple)):
        pinned = METHODS_MOD.method_for_directives([d if isinstance(d, Mapping) else {"strategy": str(d)} for d in directives])
        if pinned:
            lead = {"explanation": "explanation", "worked_example": "worked_example", "visual": "visual", "retrieval": "retrieval"}[pinned]
            return lead, _why(
                f"you approved teaching this way, so {lead.replace('_', ' ')} leads",
                lever="approved_strategy", source="stated", evidence_ids=evidence_ids, n=len(rows),
            )
    # Measured method evidence, ranked only at or past the early-signal line.
    if rows and any(r.get("method") for r in rows):
        report = METHODS_MOD.method_report(rows)
        ranked = [r for r in report["methods"] if int(r.get("n") or 0) >= METHODS_MOD.EARLY_SIGNAL_N]
        if ranked:
            best = ranked[0]
            method = str(best["method"])
            if method in LEAD_BLOCK_KIND:
                lead = {"explanation": "explanation", "worked_example": "worked_example", "visual": "visual", "retrieval": "retrieval"}[method]
                return lead, _why(
                    f"{METHODS_MOD.METHODS[method].label.lower()} has served you best so far "
                    f"({float(best.get('residual') or 0):+.2f} against expectation over {int(best['n'])} questions), so it leads",
                    lever="method_evidence", source="measured", evidence_ids=evidence_ids, n=int(best["n"]),
                )
    stated, why_text, source = _lever(levers, "lead_format_prior")
    stated_s = str(stated or "").lower()
    alias = {"diagram": "visual", "picture": "visual", "visual": "visual", "written": "explanation", "text": "explanation", "explanation": "explanation", "worked_example": "worked_example", "example": "worked_example"}
    if stated_s in alias:
        return alias[stated_s], _why(
            why_text or f"you chose {stated_s.replace('_', ' ')} for a new topic (stated, not measured)",
            lever="lead_format_prior", source=source, evidence_ids=evidence_ids, n=len(rows),
        )
    return "worked_example", _why(
        "no stated or measured lead yet: a worked example is the default first look at a new idea",
        lever="lead_format_prior", source="default", evidence_ids=[], n=0,
    )


class LessonAdapter:
    """AI 3. Stateless: every call is a function of the data it is handed."""

    # -- the deterministic plan ------------------------------------------- #

    def plan(
        self,
        *,
        levers: Mapping[str, Any] | None = None,
        subject_map: Any = None,
        ladder: Mapping[str, Any] | None = None,
        content_pack: Mapping[str, Any] | None = None,
        recent_evidence: Any = None,
        preferences: Mapping[str, Any] | None = None,
        constraints: Mapping[str, Any] | None = None,
        concept: str = "",
        title: str = "",
        teaching_memory: Mapping[str, Any] | None = None,
        snapshot: Any = None,
        model_version: int | None = None,
    ) -> dict[str, Any]:
        """Build the lesson. The deterministic path — simple mode's adapter.

        ``snapshot`` (a ``LearnerSnapshot`` or its ``as_dict``) may supply
        preferences, teaching memory and the model version in one consistent
        view; explicit arguments win over it, because a caller holding fresh
        levers should not have to rebuild a snapshot to use them.
        """
        constraints = dict(constraints or {})
        preferences = dict(preferences or {})
        memory: dict[str, list[str]] = {"explained": [], "examples_shown": [], "asked": []}
        if snapshot is not None:
            snap_dict = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(snapshot)
            preferences = {**dict(snap_dict.get("preferences") or {}), **preferences}
            tm = snap_dict.get("teaching_memory") or {}
            memory = {
                "explained": [str(x) for x in (tm.get("explained") or [])],
                "examples_shown": [str(x) for x in (tm.get("examples_shown") or [])],
                "asked": [str(x) for x in (tm.get("asked") or [])],
            }
            if model_version is None:
                model_version = int(snap_dict.get("model_version") or 0) or None
        if teaching_memory:
            for key in memory:
                memory[key] = [str(x) for x in (teaching_memory.get(key) or memory[key])]

        pack = normalise_pack(content_pack, concept=concept, title=title)
        if not pack.concept and not pack.title:
            raise AdaptationError("a lesson needs a concept: pass a content pack, a concept, or a title")
        limits = derive_limits(levers=levers, preferences=preferences, constraints=constraints)
        rows, evidence_ids, flags = _as_rows(recent_evidence)
        subject_row = _subject_row(subject_map, pack.topic_id)
        rung = _ladder_rung(ladder, subject_row)
        strings_lang = limits.language

        blocks: list[dict[str, Any]] = []
        fallbacks: list[str] = []
        panel_rows: list[dict[str, Any]] = []

        def add(kind: str, content: dict[str, Any], why: dict[str, Any]) -> dict[str, Any]:
            chunk_id = _chunk_id(pack.concept or pack.title, kind, json.dumps(content, sort_keys=True, default=str))
            used = {b["chunk_id"] for b in blocks}
            if chunk_id in used:
                # The same check can honestly follow two chunks (both are about
                # the denominator). Ids still name one chunk each, so a repeat
                # carries its occurrence — deterministically, from plan order.
                suffix = 2
                while f"{chunk_id}-{suffix}" in used:
                    suffix += 1
                chunk_id = f"{chunk_id}-{suffix}"
            block = {
                "block": len(blocks) + 1,
                "chunk_id": chunk_id,
                "kind": kind,
                "content": content,
                "why": why,
            }
            blocks.append(block)
            return block

        lead, lead_why = _choose_lead(levers=levers, subject_row=subject_row, rows=rows, evidence_ids=evidence_ids, constraints=constraints, pack=pack)
        panel_rows.append({"decision": f"opens with {lead.replace('_', ' ')}", **lead_why})
        step_value, step_why, step_source = _lever(levers, "step_size")
        panel_rows.append({
            "decision": f"steps of at most {limits.max_words_per_block} words",
            **_why(step_why or "no working-memory measurement yet, so the middle step size is used",
                   lever="step_size", source=step_source, evidence_ids=evidence_ids, n=len(rows)),
        })
        if subject_row:
            panel_rows.append({
                "decision": f"pitched at ladder rung {rung} for this topic",
                **_why(
                    f"your current estimate on this topic rests on {int(subject_row.get('n') or subject_row.get('observations') or 0)} answers"
                    + (f", range {subject_row.get('ci')}" if subject_row.get("ci") else ""),
                    lever="subject_map", source="measured",
                    evidence_ids=evidence_ids, n=int(subject_row.get("n") or subject_row.get("observations") or 0),
                ),
            })

        already_explained = pack.concept in memory["explained"] or pack.title in memory["explained"]
        predict_first = bool(
            flags.get("predict_first") or flags.get("early_guessing") or constraints.get("predict_first") or rung >= 5
        )
        conf_value, conf_why, conf_source = _lever(levers, "confidence_prompting")
        hint_value, hint_why, hint_source = _lever(levers, "hint_policy")
        halflife_value, halflife_why, halflife_source = _lever(levers, "review_halflife_prior")

        def check_block(question: str, *, why: dict[str, Any], hint: str = "") -> dict[str, Any]:
            content: dict[str, Any] = {
                "question": _bilingual(strings_lang, "check", q=question),
                "kind": "recall",
                "questions": 1,
                "hint_policy": str(hint_value or "on request"),
            }
            chosen_hint = hint or (pack.hints[0] if pack.hints else "")
            if chosen_hint:
                content["hint"] = chosen_hint
                content["hint_shown"] = str(hint_value or "") in ("offer early", "offer after a first wrong attempt")
            return add("check", content, why)

        # 1. Predict first, for learners who guess early (and at the top rung,
        #    where saying it before seeing it *is* the exercise).
        if predict_first:
            question = pack.questions[0]["question"] if pack.questions else _bilingual(strings_lang, "predict_default", concept=pack.title)
            add("predict", {"question": _bilingual(strings_lang, "predict", q=question), "questions": 1},
                _why("you tend to commit to an answer early, so the lesson asks for the prediction before it teaches — the guess is what the teaching gets measured against",
                     lever="confidence_prompting" if flags.get("early_guessing") else "ladder", source="measured" if rows else "derived",
                     evidence_ids=evidence_ids, n=len(rows)))

        # 2. The lead format.
        if lead == "visual":
            visual_block, visual_fallback = self._visual_block(pack, limits=limits, evidence_ids=evidence_ids, n=len(rows))
            visual_block["why"] = lead_why  # the receipts are the lead decision's, whoever drew the block
            if visual_fallback:
                fallbacks.append(visual_fallback)
            _re_add(blocks, visual_block)
        if lead == "worked_example" and pack.worked_steps:
            add("worked_example",
                {"title": pack.title, "steps": pack.worked_steps, "intro": _bilingual(strings_lang, "worked")},
                lead_why)

        # 3. The explanation, chunked at sentence boundaries, one check each.
        #    A concept already explained is not re-read the same way: it is
        #    recalled first, and the text itself is not repeated (teaching
        #    memory is the promise that nothing is explained twice the same
        #    way in one stretch — §2A).
        if pack.explanation and already_explained:
            add("check",
                {"question": _bilingual(strings_lang, "recall_first", concept=pack.title), "kind": "recall", "questions": 1},
                _why("this concept has been explained to you before, so the lesson starts by recalling it instead of repeating the same words",
                     lever="teaching_memory", source="measured", evidence_ids=evidence_ids, n=len(rows)))
        chunks = [] if already_explained else (_chunk_sentences(pack.explanation, limits.max_words_per_block) if pack.explanation else [])
        for i, chunk_text in enumerate(chunks):
            oversize = _words(chunk_text) > limits.max_words_per_block
            content: dict[str, Any] = {"text": chunk_text, "part": i + 1, "of": len(chunks)}
            if oversize:
                content["oversize_sentence"] = True
                content["note"] = "this sentence is longer than one step, so it travels whole rather than being cut in half"
            if pack.language and pack.language != limits.language:
                content["note"] = (content.get("note") or "") + f" this explanation is in {pack.language}; the questions around it are in your chosen language"
            add("text", content,
                _why(f"step {i + 1} of {len(chunks)}: sized to at most {limits.max_words_per_block} words before a check",
                     lever="step_size", source=step_source, evidence_ids=evidence_ids, n=len(rows)))
            terms = SEG.key_terms(chunk_text, limit=3)
            focus = terms[i % len(terms)] if terms else "this step"
            check_block(
                f"what does this step say about {focus}?",
                why=_why("a one-question check after every step, so a step that did not land is found here and not three steps later",
                         lever="step_size", source=step_source, evidence_ids=evidence_ids, n=len(rows)),
            )
            if limits.pace == "short_blocks" and i == 0 and len(chunks) > 1:
                # Pace is a lever, so it has to be visible in the lesson's
                # shape, not only in its metadata: a learner who drifts gets
                # a planned pause, not a longer wall.
                add("reflect", {"text": _bilingual(strings_lang, "break"), "kind": "planned_break"},
                    _why("your pace drifted across the baseline session, so this lesson plans a pause instead of lengthening the wall",
                         lever="pace", source="measured", evidence_ids=evidence_ids, n=len(rows)))

        if lead == "worked_example" and not pack.worked_steps and pack.explanation:
            fallbacks.append("a worked example was to lead, but the pack has none, so the explanation leads instead")
        if lead != "worked_example" and pack.worked_steps and not already_explained:
            add("worked_example",
                {"title": pack.title, "steps": pack.worked_steps, "intro": _bilingual(strings_lang, "worked")},
                _why("after the explanation, the same idea worked end to end, so the procedure can be seen whole",
                     lever="lead_format_prior", source="derived", evidence_ids=evidence_ids, n=len(rows)))

        # 4. Confidence, calibrated to the learner rather than decorative.
        if str(conf_value or "") == "before_each_answer":
            add("check", {"question": _bilingual(strings_lang, "confidence"), "kind": "confidence", "questions": 1},
                _why(conf_why or "you have tended to feel surer than your answers, so you are asked to say how sure you are before answering",
                     lever="confidence_prompting", source=conf_source, evidence_ids=evidence_ids, n=len(rows)))
            panel_rows.append({"decision": "asks how sure you are before you answer", **_why(conf_why or "", lever="confidence_prompting", source=conf_source, evidence_ids=evidence_ids, n=len(rows))})

        # 5. The matched question, then (at higher rungs) teach it back.
        if pack.questions:
            q = pack.questions[-1] if predict_first and len(pack.questions) > 1 else pack.questions[0]
            add("check", {"question": q["question"], "kind": "matched_question", "questions": 1, "format": q.get("format") or None},
                _why("the question this teaching is measured by, in the same format whichever way the concept was taught",
                     lever="method_evidence", source="measured" if rows else "default", evidence_ids=evidence_ids, n=len(rows)))
        if rung >= 4:
            add("explain", {"question": _bilingual(strings_lang, "explain_default", concept=pack.title), "kind": "teachback"},
                _why(f"at rung {rung} the test is whether you can say why, not only whether you can do it",
                     lever="ladder", source="measured", evidence_ids=evidence_ids, n=int(subject_row.get("n") or subject_row.get("observations") or 0)))

        # 6. Fatigue and success-evidence, then the close.
        if flags.get("fatigued") or flags.get("fatigue"):
            add("reflect", {"text": _bilingual(strings_lang, "break"), "kind": "break"},
                _why("your last answers slowed and slipped together, which reads as tiredness rather than not knowing — so the lesson stops instead of marking you down for it",
                     lever="pace", source="measured", evidence_ids=evidence_ids, n=len(rows)))
        reflect_text = _bilingual(strings_lang, "reflect")
        if limits.tone == "gentle":
            # Tone is the learner's stated choice about how to hear hard
            # news; it changes the words around the work, never the work.
            reflect_text += " Take the time you need — a wrong answer here is information, not a verdict."
        reflect: dict[str, Any] = {"text": reflect_text, "kind": "review"}
        if halflife_value:
            reflect["review_in_hours"] = halflife_value
            reflect["review_note"] = halflife_why or "review timing from your forgetting estimate"
        if str(conf_value or "") == "evidence_of_success" and rows:
            correct = sum(1 for r in rows if r.get("correct"))
            reflect["evidence_of_success"] = f"you were right {correct} of the last {len(rows)} times, including when you felt unsure"
        add("reflect", reflect,
            _why("the close: what to look back at, and when this comes round again",
                 lever="review_halflife_prior", source=halflife_source, evidence_ids=evidence_ids, n=len(rows)))

        if not pack.explanation and not pack.worked_steps and not pack.questions:
            fallbacks.append("no checked content exists for this concept yet, so the lesson is a recall-and-reflect skeleton and says so")
            add("reflect", {"text": f"There is no checked material for {pack.title} yet. Nothing has been invented to fill this lesson; when material is added, it will be taught here.", "kind": "no_content"},
                _why("honesty limit: no pack, no prose", lever="content", source="default", evidence_ids=[], n=0))

        plan = self._assemble(
            pack=pack, blocks=blocks, lead=lead, limits=limits,
            model_version=model_version, evidence_ids=evidence_ids, n=len(rows),
            panel_rows=panel_rows, fallbacks=fallbacks, mode="deterministic",
            rung=rung, subject_row=subject_row, constraints=constraints,
        )
        _enforce_invariants(plan, limits)
        return plan

    def _visual_block(self, pack: Pack, *, limits: Limits, evidence_ids: list[str], n: int) -> tuple[dict[str, Any], str | None]:
        """The visual block for a visual lead — validated, or words instead."""
        why = _why("a picture leads because the relations in this concept are the content, and drawing them takes them out of working memory",
                   lever="lead_format_prior", source="measured" if n else "stated", evidence_ids=evidence_ids, n=n)
        spec = pack.visual_spec
        if spec is None and pack.explanation:
            spec = TPL.derive_spec("flow", title=pack.title, sentences=pack.explanation)
            if spec is not None:
                return ({"block": 0, "chunk_id": _chunk_id(pack.concept or pack.title, "visual", json.dumps(spec, sort_keys=True)),
                         "kind": "visual", "content": {"spec": spec, "svg": TPL.render_svg(spec)}, "why": why},
                        None)
        if spec is not None:
            validated, errors = TPL.validate_visual_spec(spec)
            if validated is not None:
                return ({"block": 0, "chunk_id": _chunk_id(pack.concept or pack.title, "visual", json.dumps(validated, sort_keys=True)),
                         "kind": "visual", "content": {"spec": validated, "svg": TPL.render_svg(validated)}, "why": why},
                        None)
            fallback = TPL.fallback_text(spec, errors)
            return ({"block": 0, "chunk_id": _chunk_id(pack.concept or pack.title, "text", fallback["text"]),
                     "kind": "text", "content": {"text": fallback["text"], "note": fallback["note"], "fallback_for": "visual"}, "why": why},
                    f"the planned picture could not be drawn safely, so it is shown in words ({fallback['note']})")
        return ({"block": 0, "chunk_id": _chunk_id(pack.concept or pack.title, "text", pack.title),
                 "kind": "text", "content": {"text": " ".join(pack.explanation) or pack.title, "note": "a picture was to lead, but there is no material to draw from yet, so the words lead instead", "fallback_for": "visual"}, "why": why},
                "a picture was to lead, but there is no material to draw from yet, so the words lead instead")

    # -- model proposals ---------------------------------------------------- #

    def plan_from_model(
        self,
        proposal: Any,
        **inputs: Any,
    ) -> dict[str, Any]:
        """A model's proposed plan, validated whole or replaced whole.

        Rejection is not edited into acceptance: the deterministic plan is
        built from the same inputs and the reasons travel with it, so the
        transparency panel can say a suggestion was not used, and why.
        """
        pack = normalise_pack(inputs.get("content_pack"), concept=str(inputs.get("concept") or ""), title=str(inputs.get("title") or ""))
        limits = derive_limits(levers=inputs.get("levers"), preferences=inputs.get("preferences"), constraints=inputs.get("constraints"))
        _rows, evidence_ids, _flags = _as_rows(inputs.get("recent_evidence"))
        validated, reasons = validate_model_plan(proposal, pack=pack, limits=limits, evidence_ids=evidence_ids)
        if validated is not None:
            plan = self._assemble(
                pack=pack, blocks=validated, lead="model",
                limits=limits, model_version=inputs.get("model_version"),
                evidence_ids=evidence_ids, n=len(_rows), panel_rows=[], fallbacks=[], mode="model_validated",
                rung=_ladder_rung(inputs.get("ladder"), _subject_row(inputs.get("subject_map"), pack.topic_id)),
                subject_row=_subject_row(inputs.get("subject_map"), pack.topic_id),
                constraints=dict(inputs.get("constraints") or {}),
            )
            _enforce_invariants(plan, limits)
            return plan
        plan = self.plan(**inputs)
        plan["mode"] = "deterministic"
        plan["fallbacks"] = list(plan.get("fallbacks") or []) + [f"a suggested lesson was not used: {r}" for r in reasons]
        plan["rejected_proposal_reasons"] = reasons
        plan["why_panel"]["fallbacks"] = plan["fallbacks"]
        return plan

    def adapt_with_model(self, model_fn: Callable[..., Any], **inputs: Any) -> dict[str, Any]:
        """Run an injected model callable through the same validation gate.

        The adapter holds no client and no key: wiring a model is the
        caller's decision, and its absence is simple mode, not an error.
        A model that raises is treated exactly like a model that proposes
        nonsense — the deterministic lesson is built and the reason recorded.
        """
        try:
            proposal = model_fn(**inputs)
        except Exception as exc:  # a model outage must never cost a lesson
            plan = self.plan(**inputs)
            plan["fallbacks"] = list(plan.get("fallbacks") or []) + [f"no suggestion was available ({type(exc).__name__}), so the template lesson was used"]
            plan["why_panel"]["fallbacks"] = plan["fallbacks"]
            return plan
        return self.plan_from_model(proposal, **inputs)

    # -- contrast ----------------------------------------------------------- #

    def contrast(
        self,
        *,
        content_pack: Mapping[str, Any] | None = None,
        concept: str = "",
        title: str = "",
        profiles: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """One concept, two stored profiles, two lessons — and the differences.

        This is the headline demo, so the differences are *computed from the
        two plans*, never narrated: lead format, step size, block count,
        tone, language, hint policy, whether a picture is present. If two
        profiles happen to produce the same lesson, ``differences`` says so
        plainly instead of inventing a contrast.
        """
        if len(profiles) != 2:
            raise AdaptationError("the contrast demo takes exactly two profiles")
        lessons = []
        for profile in profiles:
            plan = self.plan(
                levers=profile.get("levers"),
                subject_map=profile.get("subject_map"),
                ladder=profile.get("ladder"),
                content_pack=content_pack,
                recent_evidence=profile.get("recent_evidence"),
                preferences=profile.get("preferences"),
                constraints=profile.get("constraints"),
                concept=concept,
                title=title,
                teaching_memory=profile.get("teaching_memory"),
            )
            lessons.append({"profile": str(profile.get("name") or "Profile"), "plan": plan})
        a, b = lessons[0]["plan"], lessons[1]["plan"]
        differences: list[dict[str, Any]] = []
        aspects = [
            ("opens with", lambda p: p["lead_format"]),
            ("step size", lambda p: p["limits"]["step_size"]),
            ("words per step, at most", lambda p: p["limits"]["max_words_per_block"]),
            ("number of blocks", lambda p: len(p["blocks"])),
            ("first block", lambda p: p["blocks"][0]["kind"] if p["blocks"] else "none"),
            ("has a picture", lambda p: any(blk["kind"] == "visual" for blk in p["blocks"])),
            ("tone", lambda p: p["limits"]["tone"]),
            ("language", lambda p: p["limits"]["language"]),
        ]
        for label, pick in aspects:
            va, vb = pick(a), pick(b)
            if va != vb:
                differences.append({"aspect": label, "a": va, "b": vb})
        return {
            "concept": a["concept"],
            "title": a["title"],
            "lessons": lessons,
            "differences": differences,
            "visibly_different": bool(differences),
            "note": (
                "both lessons teach the same checked content; what differs is how it is laid out, "
                "and every difference above is read off the two plans, not written about them"
                if differences
                else "these two profiles produced the same lesson for this concept — the evidence behind them does not separate them here"
            ),
        }

    # -- assembly ----------------------------------------------------------- #

    def _assemble(
        self,
        *,
        pack: Pack,
        blocks: list[dict[str, Any]],
        lead: str,
        limits: Limits,
        model_version: int | None,
        evidence_ids: list[str],
        n: int,
        panel_rows: list[dict[str, Any]],
        fallbacks: list[str],
        mode: str,
        rung: int,
        subject_row: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> dict[str, Any]:
        for i, block in enumerate(blocks):
            block["block"] = i + 1
        panel = {
            "title": "Why this lesson looks like this",
            "rows": panel_rows,
            "what_would_change_it": _what_would_change(lead=lead, limits=limits, rung=rung),
            "fallbacks": list(fallbacks),
            "mode": mode,
            "mode_note": (
                "built by the template adapter from your measured profile (simple mode: no model was involved)"
                if mode == "deterministic"
                else "a suggested lesson passed every code check and is shown as suggested"
            ),
        }
        return {
            "concept": pack.concept or pack.title,
            "title": pack.title,
            "topic_id": pack.topic_id,
            "content": pack.as_ref(),
            "lead_format": lead,
            "ladder_rung": rung,
            "subject": {
                "p_know": subject_row.get("p_know"),
                "ci": subject_row.get("ci"),
                "n": int(subject_row.get("n") or subject_row.get("observations") or 0),
                "band": subject_row.get("band"),
            } if subject_row else None,
            "blocks": blocks,
            "limits": limits.as_dict(),
            "why_panel": panel,
            "fallbacks": list(fallbacks),
            "mode": mode,
            "provenance": {
                "model_version": model_version,
                "content_id": pack.content_id,
                "content_source": pack.source,
                "evidence_ids": list(evidence_ids)[:MAX_EVIDENCE_REFS],
                "evidence_n": n,
            },
        }


def _re_add(blocks: list[dict[str, Any]], block: dict[str, Any]) -> None:
    block = dict(block)
    block["block"] = len(blocks) + 1
    blocks.append(block)


def _ladder_rung(ladder: Mapping[str, Any] | None, subject_row: Mapping[str, Any]) -> int:
    """Rung 0–5 (brief §6.4). No ladder yet → rung 2, the staircase's start."""
    for source in (ladder or {}, subject_row):
        value = source.get("rung", source.get("ladder_rung", source.get("steady_rung")))
        if value is not None:
            try:
                return max(0, min(5, int(value)))
            except (TypeError, ValueError):
                continue
    return 2


def _what_would_change(*, lead: str, limits: Limits, rung: int) -> list[str]:
    out = [
        "more answers under a different teaching method, measured against expectation, would change what leads",
        "a working-memory measure would resize the steps (they are at most "
        f"{limits.max_words_per_block} words now, from the {limits.step_size} step size)",
    ]
    if lead != "visual":
        out.append("approving 'show the structure visually' would put a picture first")
    if rung < 4:
        out.append("reaching rung 4 would replace some teaching with explaining it back in your own words")
    out.append("telling us a different language, tone or timer choice would restyle the scaffolding at once — those are stated, not measured")
    return out


def _chunk_sentences(sentences: Sequence[str], max_words: int) -> list[str]:
    """Group whole sentences under the cap. A sentence is never split."""
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        words = _words(sentence)
        if current and current_words + words > max_words:
            chunks.append(" ".join(current))
            current, current_words = [], 0
        current.append(sentence)
        current_words += words
    if current:
        chunks.append(" ".join(current))
    return chunks


def _enforce_invariants(plan: dict[str, Any], limits: Limits) -> None:
    """The last gate, applied to deterministic and model plans alike.

    A violation here is a bug in this module, so it raises rather than
    shipping: word caps hold (flagged oversize single sentences excepted),
    every text/explain block is followed by its check, chunk ids are unique,
    and no block carries markup.
    """
    blocks = plan["blocks"]
    ids = [b["chunk_id"] for b in blocks]
    assert len(ids) == len(set(ids)), "duplicate chunk_id in plan"
    for i, block in enumerate(blocks):
        # The ``svg`` field is drawn by code from a validated spec; it is the
        # one place markup is allowed to exist, and it is not learner/model text.
        text = json.dumps({k: v for k, v in block["content"].items() if k != "svg"}, default=str)
        assert not _MARKUP.search(text), f"markup reached block {block['chunk_id']}"
        if block["kind"] in ("text", "explain"):
            body = str(block["content"].get("text") or block["content"].get("question") or "")
            if _words(body) > limits.max_words_per_block:
                assert block["content"].get("oversize_sentence") or len(_sentences(body)) <= 1, "over-cap multi-sentence block"
            if block["kind"] == "text" and not block["content"].get("fallback_for"):
                following = blocks[i + 1] if i + 1 < len(blocks) else None
                assert following is not None and following["kind"] == "check", "explanation chunk without its check"


# --------------------------------------------------------------------------- #
# model-plan validation (code decides)
# --------------------------------------------------------------------------- #

def validate_model_plan(
    proposal: Any,
    *,
    pack: Pack,
    limits: Limits,
    evidence_ids: Sequence[str] = (),
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Validate a proposed plan whole. Returns ``(blocks, [])`` or ``(None, reasons)``.

    Every rejection is a reason a person can read, and the list is complete
    for the violations found — a proposer (or a test) learns all of what was
    wrong at once. Nothing is repaired: a plan that needed repair was not
    the plan that was validated.
    """
    reasons: list[str] = []
    if not isinstance(proposal, Mapping):
        return None, ["the suggestion was not a lesson plan at all (it was not an object)"]
    raw_blocks = proposal.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)) or not raw_blocks:
        return None, ["the suggestion carried no blocks"]

    shown_examples: set[str] = set(pack.example_ids)
    if pack.worked_steps:
        shown_examples.add("worked")
    seen_formats: list[str] = []
    blocks: list[dict[str, Any]] = []
    previous_kind: str | None = None

    for i, raw in enumerate(raw_blocks):
        where = f"block {i + 1}"
        if not isinstance(raw, Mapping):
            reasons.append(f"{where} is not an object")
            continue
        kind = raw.get("kind")
        if kind not in BLOCK_KINDS:
            reasons.append(f"{where} has kind {kind!r}; the seven block kinds are {', '.join(BLOCK_KINDS)}")
            continue
        content = raw.get("content")
        if isinstance(content, str):
            content = {"text": content}
        if not isinstance(content, Mapping):
            reasons.append(f"{where} carries no content")
            continue
        content = dict(content)
        body = str(content.get("text") or content.get("question") or "")
        joined = json.dumps(content, default=str)

        if _MARKUP.search(joined):
            reasons.append(f"{where} invents markup; blocks are drawn by code from words, never from supplied markup")
        if _PLACEHOLDER.search(joined):
            reasons.append(f"{where} contains an unrendered placeholder")
        if _INJECTION.search(joined):
            reasons.append(f"{where} contains instruction-like text; learner and model text is data here, never instructions")
        if kind in ("text", "explain", "worked_example") and _words(body) > limits.max_words_per_block and len(_sentences(body)) > 1:
            reasons.append(f"{where} runs to {_words(body)} words, past the {limits.max_words_per_block}-word step limit for this learner")
        if kind == "visual":
            spec = content.get("spec")
            if spec is None:
                reasons.append(f"{where} is a picture with no spec; pictures are drawn from validated specs")
            else:
                _validated, errs = TPL.validate_visual_spec(spec)
                if errs:
                    reasons.append(f"{where}'s picture fails validation: {'; '.join(e['message'] for e in errs)}")
        if kind == "check":
            questions = content.get("questions")
            count = int(questions) if isinstance(questions, int) else body.count("?")
            if count != 1:
                reasons.append(f"{where} is a check with {count} questions; a check asks exactly one")
        if previous_kind in ("text",) and kind != "check":
            reasons.append(f"block {i} teaches and block {i + 1} does not check it; every explanation chunk is followed by a one-question check")
        # An example may only be leaned on once it exists for this learner.
        for ref in _EXAMPLE_REF.findall(joined):
            if ref.lower() not in {e.lower() for e in shown_examples} and ref.lower() not in {str(b.get("_example_ref") or "").lower() for b in blocks}:
                reasons.append(f"{where} refers to example {ref!r}, which this lesson has not shown")
        if kind == "worked_example":
            shown_examples.add(str(content.get("example_ref") or "worked"))
        # Contradiction: the pack's stated answer, restated differently.
        for answer in pack.answer_values:
            for stated in re.findall(r"(?:answer|equals?|gives?|makes?)\s*(?:is|:)?\s*([A-Za-z0-9/\.\-]+)", joined, re.IGNORECASE):
                if stated and answer and stated.rstrip(".") != answer.rstrip(".") and stated.lower() not in answer.lower():
                    reasons.append(f"{where} states the answer as {stated!r}; the checked pack says {answer!r}")
        for fact in pack.facts:
            fact_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", fact)} - {"the", "and", "must", "with", "this", "that"}
            if fact_words and _NEGATION.search(body):
                overlap = fact_words & {w.lower() for w in re.findall(r"[A-Za-z]{3,}", body)}
                if len(overlap) >= max(2, len(fact_words) // 2):
                    reasons.append(f"{where} appears to contradict the pack's checked fact: {fact!r}")
        if body:
            ok_lang, why_lang = _language_ok(body, limits.language)
            if not ok_lang:
                reasons.append(f"{where}: {why_lang}")
            ok_read, why_read = _reading_ok(body, limits.reading_level)
            if not ok_read:
                reasons.append(f"{where}: {why_read}")
        seen_formats.append(kind)
        previous_kind = kind
        text_key = str(content.get("text") or content.get("question") or kind)
        blocks.append({
            "block": i + 1,
            "chunk_id": str(raw.get("chunk_id") or _chunk_id(pack.concept or pack.title, kind, json.dumps(content, sort_keys=True, default=str))),
            "kind": kind,
            "content": content,
            "why": raw.get("why") if isinstance(raw.get("why"), Mapping) else _why(
                str((raw.get("why") or {}).get("text") if isinstance(raw.get("why"), Mapping) else raw.get("why") or "suggested, and passed every code check"),
                lever="model_proposal", source="model", evidence_ids=evidence_ids, n=len(evidence_ids),
            ),
            "_example_ref": content.get("example_ref"),
        })

    if previous_kind == "text":
        reasons.append("the last block teaches and is never checked; every explanation chunk is followed by a one-question check")
    if len(blocks) > limits.max_blocks:
        reasons.append(f"the suggestion has {len(blocks)} blocks; the ceiling is {limits.max_blocks}")
    total = sum(_words(json.dumps(b["content"], default=str)) for b in blocks)
    if total > MAX_TOTAL_WORDS:
        reasons.append(f"the suggestion runs to about {total} words; the ceiling is {MAX_TOTAL_WORDS}")
    chunk_ids = [b["chunk_id"] for b in blocks]
    if len(chunk_ids) != len(set(chunk_ids)):
        reasons.append("two blocks share a chunk_id; a chunk id names one chunk")

    if reasons:
        return None, reasons
    for block in blocks:
        block.pop("_example_ref", None)
    return blocks, []


#: The one adapter instance routes and tests share. Stateless, so sharing is safe.
adapter = LessonAdapter()


def adapt_lesson(**inputs: Any) -> dict[str, Any]:
    """Build a lesson plan from plain inputs. See :meth:`LessonAdapter.plan`."""
    return adapter.plan(**inputs)


def contrast_demo(**inputs: Any) -> dict[str, Any]:
    """One concept under two profiles. See :meth:`LessonAdapter.contrast`."""
    return adapter.contrast(**inputs)


__all__ = [
    "BLOCK_KINDS",
    "MAX_BLOCKS",
    "AdaptationError",
    "LessonAdapter",
    "Limits",
    "Pack",
    "adapt_lesson",
    "adapter",
    "contrast_demo",
    "derive_limits",
    "normalise_pack",
    "pack_from_item",
    "validate_model_plan",
]
