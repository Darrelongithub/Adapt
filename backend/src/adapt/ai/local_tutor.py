"""The local tutor: AI 2's offline fallback and its grounded retrieval layer.

Design goal from the blueprint: *"Transcription accuracy and teaching accuracy are
separate requirements"* and *"provide sensible fallbacks when a free service
reaches its limits."* A chatbot that stops when the API is down is not a demo.

So ADAPT ships a second teaching path that needs no network at all:

* BM25 retrieval over the learner's own course materials, item solutions and
  cached resource snippets -- an honest extractive RAG. It cannot invent a
  theorem, which is also why it never contradicts the syllabus.
* Template composition driven by the *measured* level (BKT trace) and the approved
  adaptations, so the fallback still behaves adaptively.
* Rubric-based teach-back scoring using required concepts + synonym sets.
* Practice generation by sampling the item bank and re-seeding numeric variants.

When Gemini answers, :mod:`adapt.ai.ai2` uses this output as grounding context and
lets the model rewrite/extend it. When Gemini is unavailable, this output *is* the
answer, flagged ``provider="local"`` in the UI so nobody mistakes it for LLM text.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

log = logging.getLogger("adapt.tutor")

STOPWORDS = frozenset(
    """a an the and or but if while of to in on for with without at by from as is are was were be been being
    do does did not no so than then that this these those it its they them he she you your we our i me my
    what which how why when where who whom whose about into over under more most less least very just also
    can could should would will shall may might must have has had having get got make made use used using""".split()
)

WORD = re.compile(r"[A-Za-z][A-Za-z'\u00c0-\u024f\-]{1,}|[0-9][0-9.,]*")


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text or "") if w.lower() not in STOPWORDS]


def sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 20]


@dataclass
class Doc:
    doc_id: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tokens = tokenize(self.text)


class BM25:
    """Okapi BM25 (k1=1.4, b=0.75) over an in-memory index."""

    def __init__(self, docs: Sequence[Doc], *, k1: float = 1.4, b: float = 0.75) -> None:
        self.docs = list(docs)
        self.k1, self.b = k1, b
        self.df: Counter[str] = Counter()
        self.avg_len = 0.0
        for d in self.docs:
            self.avg_len += len(d.tokens)
            for t in set(d.tokens):
                self.df[t] += 1
        self.avg_len = self.avg_len / max(1, len(self.docs))
        self.n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[tuple[float, Doc]]:
        q = tokenize(query)
        if not q:
            return []
        out: list[tuple[float, Doc]] = []
        for d in self.docs:
            tf = Counter(d.tokens)
            s = 0.0
            dl = max(1, len(d.tokens))
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                s += self._idf(term) * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / max(1e-6, self.avg_len)))
            if s > 0:
                out.append((s, d))
        return sorted(out, key=lambda x: x[0], reverse=True)

    def search(self, query: str, *, k: int = 4) -> list[Doc]:
        return [d for _, d in self.score(query)[:k]]


# --------------------------------------------------------------------------- #
# corpus building
# --------------------------------------------------------------------------- #


def build_index(
    *,
    materials: Iterable[tuple[str, str, str, dict[str, Any]]] = (),  # (material_id, topic_id, body, meta)
    items: Iterable[dict[str, Any]] = (),
    resources: Iterable[dict[str, Any]] = (),
) -> BM25:
    docs: list[Doc] = []
    for mid, topic_id, body, meta in materials:
        for i, sent in enumerate(sentences(body)):
            docs.append(
                Doc(f"mat:{mid}:{i}", sent, {"kind": "material", "topic_id": topic_id, "title": meta.get("title", "")})
            )
    for item in items:
        topic_id = item.get("topic_id", "")
        for i, step in enumerate(item.get("solution_steps") or []):
            text = step.get("text") if isinstance(step, dict) else str(step)
            if text:
                docs.append(Doc(f"sol:{item.get('id')}:{i}", text, {"kind": "solution", "topic_id": topic_id}))
        for i, hint in enumerate(item.get("hints") or []):
            docs.append(Doc(f"hint:{item.get('id')}:{i}", hint, {"kind": "hint", "topic_id": topic_id}))
        expl = item.get("explanation") or ""
        for i, sent in enumerate(sentences(expl)):
            docs.append(Doc(f"expl:{item.get('id')}:{i}", sent, {"kind": "explanation", "topic_id": topic_id}))
    for r in resources:
        text = r.get("snippet") or r.get("abstract") or r.get("title") or ""
        for i, sent in enumerate(sentences(text)[:8]):
            docs.append(
                Doc(
                    f"res:{r.get('provider','ext')}:{r.get('external_id','x')}:{i}",
                    sent,
                    {"kind": "resource", "provider": r.get("provider"), "url": r.get("url"), "title": r.get("title")},
                )
            )
    return BM25(docs)


# --------------------------------------------------------------------------- #
# level-aware text assembly
# --------------------------------------------------------------------------- #

LEVELS = ("new", "developing", "proficient", "mastered")


@dataclass
class Grounded:
    """An assembled answer plus its provenance (so the UI can show 'why this')."""

    text: str
    sections: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    directives_applied: list[str] = field(default_factory=list)
    level: str = "developing"
    confident: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.text,
            "sections": self.sections,
            "sources": self.sources,
            "adaptations_applied": self.directives_applied,
            "level": self.level,
            "provider": "local",
            "grounded": True,
            "note": None if self.confident else "I could not find this in your course materials.",
        }


def _level_from(p_know: float | None) -> str:
    if p_know is None:
        return "new"
    if p_know < 0.35:
        return "new"
    if p_know < 0.65:
        return "developing"
    if p_know < 0.85:
        return "proficient"
    return "mastered"


def _shorten(text: str, max_words: int) -> str:
    ws = text.split()
    return " ".join(ws[:max_words]) + ("…" if len(ws) > max_words else "")


def explain(
    query: str,
    *,
    index: BM25,
    topic_title: str | None = None,
    p_know: float | None = None,
    directives: Sequence[dict[str, Any]] = (),
    item: dict[str, Any] | None = None,
    prereq_note: str | None = None,
    max_words: int = 180,
) -> Grounded:
    """Compose a level-appropriate, source-cited explanation."""
    level = _level_from(p_know)
    dmap = {d.get("strategy") or d.get("action", ""): d for d in directives}
    payload_words = int((dmap.get("segmented_text") or {}).get("payload", {}).get("max_words", 0) or 0)
    budget = min(max_words, payload_words * 2) if payload_words else max_words
    want_example = bool(dmap.get("worked_examples")) or level in ("new", "developing")

    hits = index.score(query)[:10]
    if not hits and topic_title:
        hits = index.score(topic_title)[:6]

    by_kind: dict[str, list[tuple[float, Doc]]] = defaultdict(list)
    for s, d in hits:
        by_kind[d.meta.get("kind", "other")].append((s, d))

    sections: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    parts: list[str] = []
    # What the item's own variables say, for the last-ditch substitution below.
    item_vars: dict[str, Any] = dict(((item or {}).get("answer_spec") or {}).get("variables") or {})

    def add(text: str, label: str, src: Doc | None = None) -> None:
        if not text:
            return
        # Nothing a learner reads may contain the item's template. Handing ``explain`` a
        # stored row instead of the rendered variant used to put "{a} - {b} = {ab}" in the
        # middle of an answer -- and in the model's prompt in llm mode. AI 2 now renders
        # before it gets here; this is the guard that keeps the same mistake from coming
        # back through another door: substitute what the item declares, and if any
        # placeholder is still unresolved, leave the section out rather than show braces.
        if TEMPLATE_KEY.search(text):
            filled = _substitute(text, item_vars)
            if TEMPLATE_KEY.search(filled):
                log.warning("tutor: dropped the %r section - it still held unrendered placeholders", label)
                return
            text = filled
        parts.append(text)
        sections.append({"label": label, "text": text})
        if src is not None:
            entry: dict[str, Any] = {
                "kind": str(src.meta.get("kind", "")),
                "ref": src.doc_id,
                "title": str(src.meta.get("title", "")),
            }
            # A citation the learner cannot open is a decoration. External resources
            # carry their URL; the learner's own material carries which note it came from.
            if src.meta.get("url"):
                entry["url"] = str(src.meta["url"])
            if entry["kind"] == "material" and str(src.doc_id).startswith("mat:"):
                entry["material_id"] = str(src.doc_id).split(":")[1]
            sources.append(entry)

    if prereq_note:
        add(prereq_note, "first, the prerequisite")

    opener = {
        "new": "Let's start from one concrete case.",
        "developing": f"You have most of {topic_title or 'this'}; here is the part that is missing.",
        "proficient": "Short version, then the edge case.",
        "mastered": "You know this — so here is the harder question instead.",
    }[level]

    # Content: pick the most relevant material sentences, de-duplicated.
    seen: set[str] = set()
    chosen: list[Doc] = []
    for score, doc in hits:
        # Only declarative material becomes the body of an explanation. Hints are
        # opt-in pedagogy (hint_for serves them) and solution steps are the answer;
        # letting BM25 pull either into "the explanation" would spoil an unanswered
        # item and teach the recipe instead of the idea.
        if doc.meta.get("kind") not in ("material", "explanation"):
            continue
        key = re.sub(r"\W+", "", doc.text.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(doc)
        if len(chosen) >= (3 if level != "new" else 2):
            break
    if chosen:
        add(opener, "framing")
        body = " ".join(_shorten(d.text, budget // max(1, len(chosen)) + 8) for d in chosen)
        add(body, "explanation", chosen[0])
    else:
        add(
            f"Your notes do not cover {topic_title or 'that'} yet, so I will not guess at it. "
            "Ask me about a topic in your course, or add the material and I will read it again.",
            "explanation",
        )
        return Grounded(" ".join(parts), sections, sources, list(dmap), level, confident=False)

    # Worked example: prefer the item's own solution steps -- the ones for *this* item, not
    # the first row of whatever topic the caller happened to pass.
    if want_example and item:
        steps = item.get("solution_steps") or []
        lines = []
        for i, st in enumerate(steps[:5]):
            txt = st.get("text") if isinstance(st, dict) else str(st)
            if txt:
                lines.append(f"{i + 1}. {txt}")
        if lines:
            add("\n".join(lines), "worked example")
    elif by_kind.get("solution") and want_example:
        top = by_kind["solution"][0][1]
        add(top.text, "worked example", top)

    if by_kind.get("resource") and level in ("new", "developing"):
        r = by_kind["resource"][0][1]
        add(
            f"For a second explanation, see {r.meta.get('title') or 'this source'} "
            f"({r.meta.get('kind', 'resource')}).",
            "extra source",
            r,
        )

    next_step = {
        "new": "Try one: copy the first line of the example and change only the numbers.",
        "developing": "Now do the next one without looking at the example, then compare.",
        "proficient": "Do the next two; tell me which step you skipped and why that was safe.",
        "mastered": "Explain it back in two sentences as if to someone who has not seen it.",
    }[level]
    add(next_step, "your turn")

    applied = [k for k in dmap if k]
    return Grounded("\n\n".join(p for p in parts if p), sections, sources, applied, level)


def hint_for(item: dict[str, Any], *, level: str, hints_used: int = 0, max_hints: int | None = None) -> str:
    """Next hint, respecting an approved hint-fading adaptation."""
    hints: Sequence[str] = list(item.get("hints") or [])
    if max_hints is not None and hints_used >= max_hints:
        return (
            "You have your hint budget for this one, and that is deliberate: try the "
            "first line, then I will tell you exactly where it goes wrong."
        )
    if not hints:
        steps = item.get("solution_steps") or []
        if steps:
            first = steps[0].get("text") if isinstance(steps[0], dict) else str(steps[0])
            # Only the first step. The rest of the solution is the worked example, which
            # is a separate opt-in -- and on a short item the last step *is* the answer.
            lead = f"Start with: {first}"
        else:
            lead = "Write down the single fact the question is asking for, then what would give it to you."
        if hints_used:
            return (
                "That is all the guidance this question has without giving the answer away. "
                "I can show the worked example, or you try the line and I will say exactly where it goes wrong."
            )
        return lead
    if hints_used >= len(hints):
        return (
            "That is every hint this question has. The rest would give the answer away, so: try the "
            "line you are unsure of and I will tell you where it breaks, or ask for the worked example."
        )
    idx = min(len(hints) - 1, hints_used)
    if level == "mastered":
        return hints[-1]
    return hints[idx]


def feedback(item: dict[str, Any], answer: str | None, *, correct: bool, score: float | None = None) -> str:
    """Rule-based corrective feedback: name the misconception, don't just judge."""
    spec = item.get("answer_spec") or {}
    misc = spec.get("misconceptions") or {}
    a = (answer or "").strip()
    bits: list[str] = []
    if correct:
        bits.append("Correct.")
        alt = spec.get("why_right")
        if alt:
            bits.append(str(alt))
        steps = item.get("solution_steps") or []
        if steps and len(steps) > 1:
            bits.append("What you did is step general: the same move works on the next two.")
    else:
        why = None
        for key, text in misc.items():
            if key and key.lower() in a.lower():
                why = str(text)
                break
        if why:
            bits.append(f"Here is the specific trap: {why}")
        elif a:
            bits.append(f"Not yet. '{_shorten(a, 12)}' does not satisfy what the question asks for.")
        else:
            bits.append("Nothing was answered, so this is a free diagnostic: what was the first step you considered?")
        steps = item.get("solution_steps") or []
        if steps:
            first = steps[0].get("text") if isinstance(steps[0], dict) else str(steps[0])
            bits.append(f"Try starting here: {first}")
    if score is not None and 0 < score < 1:
        bits.append(f"Partial credit {score:.0%}: the working counted, the final value did not.")
    return " ".join(bits)


# --------------------------------------------------------------------------- #
# teach-back scoring (rubric coverage, deliberately not writing quality)
# --------------------------------------------------------------------------- #


def teachback_score(text: str, rubric: dict[str, Any] | None) -> dict[str, Any]:
    """Score an explanation against required concepts with synonym sets."""
    rubric = rubric or {}
    concepts: list[dict[str, Any]] = rubric.get("required_concepts") or []
    if not concepts:
        # No rubric: fall back to a coarse length/structure judgement, clearly
        # labelled as such instead of pretending to be a rubric score.
        n_words = len(tokenize(text))
        coverage = min(1.0, n_words / 60)
        return {
            "score": round(0.3 * coverage, 3),
            "coverage": round(coverage, 3),
            "method": "word_count_proxy",
            "missing": [],
            "note": "no rubric on this item, so this is a length proxy and is not used to judge understanding",
            "words": n_words,
        }
    words = set(tokenize(text))
    lower = (text or "").lower()
    matched: list[str] = []
    missing: list[str] = []
    for c in concepts:
        name = str(c.get("concept") or c.get("name") or "")
        needles = [str(x).lower() for x in (c.get("synonyms") or [name])]
        hit = any(n in words or n in lower for n in needles if n)
        (matched if hit else missing).append(name or needles[0])
    score = len(matched) / max(1, len(concepts))
    followup = None
    if missing:
        followup = f"Which step gives you {missing[0]}? Say it out loud and I will check it."
    return {
        "score": round(score, 3),
        "coverage": round(score, 3),
        "method": "rubric_concept_coverage",
        "matched": matched,
        "missing": missing,
        "followup": followup,
        "words": len([w for w in re.split(r"\s+", text or "") if w]),
        "note": "scored on ideas covered, not fluency or spelling",
    }


# --------------------------------------------------------------------------- #
# practice generation
# --------------------------------------------------------------------------- #

TEMPLATE_KEY = re.compile(r"\{(\w+)\}")


def _substitute(template: str, variables: dict[str, Any]) -> str:
    """Format ``{name}`` placeholders from the item's own variable table."""
    if not template:
        return ""
    out = template
    for name in TEMPLATE_KEY.findall(out):
        if name in variables:
            val = variables[name]
            text = f"{val:g}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val)
            out = out.replace("{" + name + "}", text)
    return out


def render_variant(item: dict[str, Any], *, rng: random.Random | None = None) -> dict[str, Any]:
    """Generate a fresh version of a parametric item and recompute its answer.

    Items declare ``stem_template`` + ``answer_spec.variables`` (fixed defaults)
    and optionally ``answer_spec.ranges`` (integer ranges). This is the same
    variable/expression model Numbas uses, so an exported item keeps its
    randomisation there and a rendered one stays answerable here.

    Non-parametric items (no formula) are returned with a ``variant_of`` marker
    and otherwise untouched -- inventing an answer for a fixed item would be worse
    than reusing it.
    """
    rng = rng or random.Random()
    spec = dict(item.get("answer_spec") or {})
    formula = spec.get("formula")
    template = item.get("stem_template")
    variables = dict(spec.get("variables") or {})
    ranges = spec.get("ranges") or {}
    if not formula or (not template and not ranges):
        return {**item, "variant_of": item.get("id") or item.get("numbas_ref")}

    sampled: list[str] = []
    for name, bound in ranges.items():
        try:
            lo, hi = float(bound[0]), float(bound[1])
        except (TypeError, ValueError, IndexError):
            continue
        for _ in range(8):
            val = (
                round(rng.uniform(lo, hi), 2)
                if (lo != int(lo) or hi != int(hi))
                else int(rng.randint(int(lo), int(hi)))
            )
            # Avoid degenerate variants (2/2, x+x, ...) by resampling collisions.
            if val in [variables.get(o) for o in sampled]:
                continue
            variables[name] = val
            sampled.append(name)
            break
        else:
            variables[name] = int(rng.randint(int(lo), int(hi)))
            sampled.append(name)
    # A denominator of 0 makes the item meaningless; clamp it away.
    for name, val in list(variables.items()):
        if isinstance(val, (int, float)) and val == 0 and name in ("b", "d", "h"):
            variables[name] = 2

    # Derived variables (e.g. bd = b*d) are recomputed from the sampled ones so a
    # stem can reference them without the author keeping them in sync.
    for name, expr in (spec.get("derived") or {}).items():
        try:
            variables[name] = round(eval_formula(str(expr), dict(variables)), 10)
        except Exception:
            continue
    try:
        answer = eval_formula(str(formula), {k: v for k, v in variables.items()})
    except Exception:
        return {**item, "variant_of": item.get("id") or item.get("numbas_ref")}

    text_vars = dict(variables)
    text_vars["ans"] = round(answer, 6)
    text_vars["answer"] = round(answer, 6)
    stem = _substitute(template or item.get("stem", ""), text_vars)
    out = {
        **item,
        "stem": stem,
        "variant_of": item.get("id") or item.get("numbas_ref"),
        "answer_spec": {**spec, "variables": variables, "value": answer},
        "solution_steps": [
            (
                dict(st, text=_substitute(st.get("text", ""), text_vars))
                if isinstance(st, dict)
                else {"text": _substitute(str(st), text_vars)}
            )
            for st in (item.get("solution_steps") or [])
        ],
        "hints": [_substitute(str(h), text_vars) for h in (item.get("hints") or [])],
    }
    # Distractor text may reference variables or the computed answer too.
    if out.get("choices"):
        out["choices"] = [
            dict(c, text=_substitute(str(c.get("text", "")), text_vars)) for c in out["choices"]
        ]
    return out


SAFE_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "int": int, "float": float}


def eval_formula(formula: str, mapping: dict[str, str]) -> float:
    """Evaluate a restricted arithmetic formula with only the item's variables."""
    allowed = re.compile(r"^[0-9a-zA-Z_+\-*/().,%\s]+$")
    if not allowed.match(formula):
        raise ValueError(f"formula contains disallowed characters: {formula!r}")
    ns: dict[str, Any] = {k: float(v) for k, v in mapping.items()}
    ns.update(SAFE_FUNCS)
    value = eval(formula, {"__builtins__": {}}, ns)  # noqa: S307
    if isinstance(value, bool):
        raise ValueError("formula must not evaluate to a bool")
    return float(value)


def practice_plan(
    *,
    bank: Sequence[dict[str, Any]],
    mastery: dict[str, float],
    due_topics: Sequence[str] = (),
    gap_topics: Sequence[str] = (),
    target_success: float = 0.78,
    count: int = 5,
    directives: Sequence[dict[str, Any]] = (),
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Pick the next ``count`` items: gaps and due reviews first, then the topic
    whose expected success rate is furthest from the target band.
    """
    rng = rng or random.Random()
    dmap = {d.get("strategy") or d.get("action", ""): d for d in directives}
    interleaving = float((dmap.get("interleaving") or {}).get("payload", {}).get("ratio", 0.0) or 0.0)
    need_examples = "worked_examples" in dmap

    def priority(item: dict[str, Any]) -> tuple[float, float]:
        topic = item.get("topic_id", "")
        m = mastery.get(topic, 0.2)
        base = 0.0
        if topic in gap_topics:
            base += 3.0
        if topic in due_topics:
            base += 2.5
        # prefer difficulty nearest target success
        fit = 1.0 - abs((m * (1 - float(item.get("difficulty", 0.5)))) - target_success)
        return (base + fit, rng.random())

    picked: list[dict[str, Any]] = []
    topics_used: list[str] = []
    pool = sorted([i for i in bank if i.get("active", True)], key=priority, reverse=True)
    for item in pool:
        if len(picked) >= count:
            break
        topic = item.get("topic_id", "")
        # Blocking guard: with interleaving enabled, avoid 3 in a row of the same topic.
        if interleaving <= 0 or len(topics_used) < 2 or topics_used[-1] != topics_used[-2] or topics_used[-1] != topic:
            chosen = item
        else:
            alt = next((p for p in pool if p.get("topic_id") != topic and p not in picked), None)
            if alt is None:
                continue
            chosen = alt
            picked.append(render_variant(chosen) | ({"attach_worked_example": need_examples} if need_examples else {}))
            topics_used.append(alt.get("topic_id", ""))
            continue
        v = render_variant(chosen)
        if need_examples:
            v["attach_worked_example"] = True
        picked.append(v)
        topics_used.append(topic)
    return picked


def simplify(text: str, *, max_words: int = 60) -> str:
    """Rewrite-free simplification: keep the highest-information sentences."""
    sents = sentences(text)
    if not sents:
        return _shorten(text, max_words)
    tokens = Counter(tokenize(text))
    scored = sorted(
        ((sum(min(3, tokens.get(t, 0)) for t in tokenize(s)) / max(6, len(tokenize(s))), s) for s in sents),
        key=lambda x: x[0],
        reverse=True,
    )
    out: list[str] = []
    n = 0
    for _, s in scored:
        w = len(s.split())
        if n + w > max_words and out:
            break
        out.append(s)
        n += w
    order = {s: i for i, s in enumerate(sents)}
    return " ".join(sorted(out, key=lambda s: order.get(s, 0)))


def unexpected_request(text: str) -> str | None:
    """Classify the common in-the-moment asks so they are never gated.

    Returns an intent id, or None (which means: treat it as a teaching question).
    """
    t = (text or "").lower()
    rules = (
        ("simpler", ("more simply", "simpler", "explain simply", "eli5", "i don't get it", "confusing")),
        ("shorter", ("shorter", "too long", "brief", "tl;dr")),
        ("another_example", ("another example", "one more example", "different example", "another one")),
        ("check_answer", ("is this right", "check my", "did i get", "correct or not", "mark this")),
        ("next_question", ("next question", "give me one", "practice", "quiz me", "harder", "easier")),
        ("why_me", ("why are you", "why did you", "what did you notice", "why this", "what makes you say")),
        ("show_work", ("show working", "worked example", "steps", "walk me through")),
        ("skip", ("skip", "move on", "next topic", "done with this")),
        ("voice", ("read it", "say it", "speak", "out loud", "listen")),
        ("review", ("revise", "review", "spaced", "come back to", "forgetting")),
    )
    for intent, needles in rules:
        if any(n in t for n in needles):
            return intent
    return None
