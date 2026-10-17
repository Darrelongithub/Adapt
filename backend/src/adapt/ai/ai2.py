"""AI 2 -- the Educational Intelligence.

Everything AI 2 does is conditioned on AI 1's profile, and it reads *only* what it
needs:

* topic traces -> altitude (concrete example vs "prove it to me")
* approved adaptations -> the presentation rules for this session (worked example
  first, <=90-word explanations, one hint, teach-back before advancing...)
* knowledge gaps -> teach the prerequisite first, and say why
* retention schedule -> prefer retrieval practice on what is about to be forgotten
* resource cache -> cite an outside source when one is genuinely on-topic

Two paths, one interface: :meth:`EducationalIntelligence._generate` asks Gemini and
falls back to the local tutor (BM25 over the learner's own materials + rubric
scoring). The reply always says which produced it, because a fluent wrong answer
and an honest extractive answer are not interchangeable -- the blueprint treats
teaching accuracy as its own requirement.

In-the-moment requests ("explain that more simply") bypass every approval gate on
purpose; only *persistent* strategy changes are gated. That distinction is encoded
in :func:`adapt.learning.strategies.Strategy.persistent` and asserted by the tests.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from ..assessment import generated as GEN
from ..assessment import selector as SEL
from ..assessment.grading import grade as grade_item
from ..config import Settings, get_settings
from ..db.models import (
    Attempt,
    ChatMessage,
    Item,
    LearningMaterial,
    LearningSession,
    LlmCall,
    ResourceReference,
    Topic,
    VoiceTurn,
    utcnow,
)
from ..learning.profile import to_llm_payload
from ..security.redact import redact_payload
from . import local_tutor as LT
from . import static_context as SC

log = logging.getLogger("adapt.ai2")

REPLY_SPEC = {
    "answer": "str",
    "sections": ("array", {"type": "OBJECT"}),
    "next_action": "str",
    "adaptations_applied": ("array", {"type": "STRING"}),
    "ask_teachback": "str",
    "resource_hint": "str",
}

_MODEL_MARKUP = re.compile(
    r"<\s*/?\s*[A-Za-z!]|```|`|\*\*|__|(?:https?://|www\.|javascript:)",
    re.IGNORECASE,
)


def _clean_model_text(value: Any, *, maximum: int) -> str:
    """Plain learner-facing text only; malformed model prose falls back locally."""
    text = " ".join(str(value or "").split()).strip()
    if not text or _MODEL_MARKUP.search(text):
        return ""
    return text[:maximum].strip()


def _accepted_reply_sections(raw: Any, fallback: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Keep the local grounded sections unless the model returned the same safe shape."""
    if not isinstance(raw, list) or not raw:
        return [dict(row) for row in fallback if isinstance(row, Mapping)]
    accepted: list[dict[str, str]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            return [dict(item) for item in fallback if isinstance(item, Mapping)]
        label = _clean_model_text(row.get("label"), maximum=48)
        text = _clean_model_text(row.get("text"), maximum=1200)
        if not label or not text:
            return [dict(item) for item in fallback if isinstance(item, Mapping)]
        accepted.append({"label": label, "text": text})
    return accepted


def _accepted_next_action(value: Any, fallback: str | None) -> str | None:
    """Enforce the contract's one-next-action rule instead of trusting a string field."""
    candidate = _clean_model_text(value, maximum=320)
    if candidate and len(LT.sentences(candidate)) == 1:
        return candidate
    return fallback


def _accepted_adaptations(value: Any, directives: Sequence[Mapping[str, Any]], fallback: Sequence[str]) -> list[str]:
    allowed = {str(row.get("strategy") or "") for row in directives if isinstance(row, Mapping)}
    if not isinstance(value, list):
        return list(fallback)
    return [str(item) for item in value if isinstance(item, str) and str(item) in allowed]


def bank_seed(student_id: str | None, item_id: str | None) -> int:
    """Indirection so every AI 2 render asks bank.variant_seed for the same number."""
    from ..assessment.bank import variant_seed

    return variant_seed(student_id, item_id)


def _tutor_item_view(row: Any, student_id: str | None) -> dict[str, Any]:
    """The item as the tutor should talk about it: rendered for this learner, not as stored.

    A bank row is a template -- its stem, hints and solution steps say ``{a} - {b} = {ab}``.
    The plan, the hint and the reveal all substitute the learner's own variant before showing
    anything; the tutor's context used to carry the row itself, so its "worked example"
    section quoted the template at the learner (and the raw template went into the model's
    prompt in llm mode). Same seed as everywhere else, so the numbers in the explanation are
    the numbers on the learner's screen.
    """
    from ..assessment.bank import render_item_for

    raw = {
        "id": row.id,
        "topic_id": row.topic_id,
        "kind": row.kind,
        "stem": row.stem,
        "stem_template": row.stem_template if hasattr(row, "stem_template") else None,
        "experience_mode": row.experience_mode,
        "prompt_context": row.prompt_context,
        "visual_spec": row.visual_spec,
        "choices": row.choices,
        "answer_spec": row.answer_spec,
        "solution_steps": row.solution_steps,
        "hints": row.hints,
        "rubric": row.rubric,
        "difficulty": row.difficulty,
        "numbas_ref": row.numbas_ref if hasattr(row, "numbas_ref") else None,
        "tags": row.tags if hasattr(row, "tags") else None,
        "explanation": row.prompt_context or "",
    }
    return render_item_for(raw, seed=bank_seed(student_id, row.id))


def _remediation_row(p, student_id: str, *, render_item_for, public_item) -> dict[str, Any]:
    """One render per item: the stem, the hints and the attached worked example all have to
    come from the *same* sampled variant, or the example walks through different numbers
    than the question the learner is reading.
    """
    rendered = render_item_for(p.item, seed=bank_seed(student_id, (p.item or {}).get("id")))
    return {
        **p.as_dict(),
        "item": public_item(rendered),
        "worked_example": (rendered.get("solution_steps") or []) if p.attach_worked_example else None,
    }



@dataclass
class Reply:
    text: str
    provider: str = "local"
    sections: list[dict[str, Any]] = field(default_factory=list)
    next_action: str | None = None
    adaptations_applied: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    teachback_prompt: str | None = None
    resource_hint: str | None = None
    level: str = "developing"
    #: The explanation as short steps, each with a check question (``learning.segments``).
    #: Empty on replies that are not built from a body of text (a hint, a refusal).
    steps: list[dict[str, Any]] = field(default_factory=list)
    model_ms: int = 0
    llm: dict[str, Any] = field(default_factory=dict)
    message_id: str | None = None
    intent: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.text,
            "provider": self.provider,
            "sections": self.sections,
            "next_action": self.next_action,
            "adaptations_applied": self.adaptations_applied,
            "sources": self.sources,
            "steps": self.steps,
            "teachback_prompt": self.teachback_prompt,
            "resource_hint": self.resource_hint,
            "level": self.level,
            "model_ms": self.model_ms,
            "llm": self.llm,
            "message_id": self.message_id,
            "intent": self.intent,
            "meta": self.meta,
        }


class EducationalIntelligence:
    def __init__(
        self,
        session_factory,
        *,
        ai1: Any,
        settings: Settings | None = None,
        llm: Any = None,
        resources: Any = None,
        state: Any = None,
    ) -> None:
        self.sf = session_factory
        self.ai1 = ai1
        self.settings = settings or get_settings()
        self.llm = llm
        self.resources = resources
        #: The shared Learner State Store (round brief 2A). This is how AI 2 reads the learner's
        #: level: one immutable snapshot that says which ``model_version`` it came from, instead of
        #: assembling a private view of the database. ``None`` in tests that build AI 2 on its own.
        self.state = state

    # ------------------------------------------------------------------ #
    # context assembly
    # ------------------------------------------------------------------ #

    def context(self, student_id: str, *, topic_id: str | None = None) -> dict[str, Any]:
        with self.sf() as s:
            profile = self.ai1.profile(s, student_id)
            topic = s.get(Topic, topic_id) if topic_id else None
            materials = s.execute(
                select(LearningMaterial)
                .where(
                    LearningMaterial.course_id == (topic.course_id if topic else "%"),
                )
                .limit(40)
            ).scalars().all() if topic else s.execute(select(LearningMaterial).limit(40)).scalars().all()
            titles = {row.id: row.title for row in s.execute(select(Topic)).scalars().all()}
            items = []
            if topic:
                for it in s.execute(select(Item).where(Item.topic_id == topic.id).limit(30)).scalars().all():
                    view = _tutor_item_view(it, student_id)
                    view["topic_title"] = topic.title
                    items.append(view)
            resources = s.execute(
                select(ResourceReference).where(ResourceReference.topic_id == (topic.id if topic else "%")).limit(24)
            ).scalars().all() if topic else []
            directives = [d.as_dict() for d in profile.directives]
            p_know = None
            if topic:
                tr = next((t for t in profile.traces if t.topic_id == topic.id), None)
                p_know = tr.p_know if tr else None
            prereq_note = None
            # A prerequisite claim is only allowed when the learner asked about a
            # specific topic we have a measured gap for. Taking "the first gap we
            # have" instead would attach a confident number to the wrong skill --
            # which is precisely what the facts-vs-hypotheses split exists to stop.
            gap = next((g for g in profile.gaps if topic and g.get("topic_id") == topic.id), None)
            if gap and gap.get("prerequisite_mastery") is not None:
                prereq_note = (
                    f"Before this: we trace {gap.get('blocking_prerequisite_title')} at "
                    f"{float(gap.get('prerequisite_mastery', 0)):.0%}, and that is what these errors point at. "
                    "Two sentences on it, then back to the main thread."
                )
            # Was this learner's last attempt on this topic wrong? The explanation's opening
            # line is chosen from this, so a repeat question after a miss does not read like a
            # first attempt (and vice versa).
            last_attempt_correct: bool | None = None
            if topic:
                row = s.execute(
                    select(Attempt)
                    .where(Attempt.student_id == student_id, Attempt.topic_id == topic.id, Attempt.correct.is_not(None))
                    .order_by(Attempt.submitted_at.desc())
                ).scalars().first()
                last_attempt_correct = None if row is None else bool(row.correct)
            # The store's view, built once, immutable, and stamped with its version. A caller that
            # wants "what did the tutor know when it taught this?" reads this rather than the
            # profile above, which is a live query and can move under it.
            snapshot = self.state.get_snapshot(student_id) if self.state is not None else None
            return {
                "profile": profile,
                "profile_dict": profile.as_dict(),
                "snapshot": snapshot,
                "model_version": (snapshot.model_version if snapshot is not None else None),
                "topic": topic,
                "last_attempt_correct": last_attempt_correct,
                "p_know": p_know,
                "directives": directives,
                "materials": [
                    (m.id, m.topic_id or "", m.body, {"title": m.title, "topic_title": titles.get(m.topic_id or "", "")})
                    for m in materials
                ],
                "items": items,
                "resources": [
                    {
                        "provider": r.provider,
                        "external_id": r.external_id,
                        "kind": r.kind,
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                        "abstract": r.snippet,
                        "year": r.year,
                        "license": r.license,
                    }
                    for r in resources
                ],
                "prereq_note": prereq_note,
                "presentation": SEL.presentation_from_directives(directives),
            }

    def index_for(self, ctx: dict[str, Any]):
        return LT.build_index(
            materials=ctx.get("materials") or [],
            items=ctx.get("items") or [],
            resources=ctx.get("resources") or [],
        )

    # ------------------------------------------------------------------ #
    # main entry points
    # ------------------------------------------------------------------ #

    async def explain(
        self,
        student_id: str,
        question: str,
        *,
        topic_id: str | None = None,
        session_id: str | None = None,
        history: Sequence[tuple[str, str]] = (),
        want_spoken: bool = False,
        item_id: str | None = None,
    ) -> Reply:
        """Answer an educational question, adapted to the learner."""
        ctx = self.context(student_id, topic_id=topic_id)
        idx = self.index_for(ctx)
        intent = LT.unexpected_request(question)
        # The learner asked about the question in front of them. Taking items[0] meant the
        # worked example in the reply was for some other item of the topic -- chosen by
        # whichever row the database happened to return first.
        pool = ctx["items"] or []
        item = next((i for i in pool if item_id and i.get("id") == item_id), pool[0] if pool else None)
        # What is the learner asking *from*? A repeat question after a wrong answer reads
        # differently from a first attempt, and the same opener every time is how a tutor
        # sounds like a form ("Let's start from one concrete case." was every reply's first
        # line). The context is derived from the evidence, not from the phrasing.
        ask_context = "first_attempt"
        if intent == "show_work":
            ask_context = "after_hint"
        elif intent == "check_answer":
            ask_context = "after_reveal"
        elif ctx.get("last_attempt_correct") is False:
            ask_context = "repeat_error"
        local = LT.explain(
            question,
            index=idx,
            topic_title=ctx["topic"].title if ctx.get("topic") else None,
            topic_id=(item or {}).get("topic_id") or topic_id,
            p_know=ctx.get("p_know"),
            directives=ctx["directives"],
            item=item,
            prereq_note=ctx.get("prereq_note"),
            max_words=600 if intent in ("simpler", "shorter") else 320,
            context=ask_context,
            seed=f"{student_id}:{item_id or ''}:{ask_context}",
        )
        reply = Reply(
            text=local.text,
            sections=local.sections,
            sources=local.sources,
            steps=local.steps,
            adaptations_applied=local.directives_applied,
            level=local.level,
            intent=intent,
            # ``grounded`` means "these words came from your material, for this topic". The
            # old value was ``local.confident``, which was about something else.
            meta={
                "grounded": local.grounded,
                "note": local.note,
                "topic_id": local.source_topic,
                "provider_detail": "local BM25 over course materials, filtered to this item's topic",
            },
        )
        if ctx["presentation"].get("require_teachback"):
            reply.teachback_prompt = (
                "Before the next one: say in your own words what you just read, out loud if you can. "
                "I will tell you which piece you left out."
            )
        if self.resources is not None and ctx.get("topic") is not None and (local.confident is False or "example" not in question.lower()):
            reply.resource_hint = await self._resource_hint(ctx)
        # Gemini pass (optional): rewrite/extend the grounded material, never replace it.
        if self.llm is not None and getattr(self.llm, "configured", False):
            llm_reply, meta = await self._generate(
                question=question,
                ctx=ctx,
                local=local,
                student_id=student_id,
                history=history,
                purpose="ai2_explain",
                want_spoken=want_spoken,
                intent=intent,
            )
            reply.llm = meta
            if llm_reply:
                reply.text = llm_reply["answer"]
                reply.provider = "gemini"
                reply.sections = llm_reply.get("sections") or reply.sections
                reply.next_action = llm_reply.get("next_action")
                reply.adaptations_applied = llm_reply.get("adaptations_applied") or reply.adaptations_applied
            else:
                reply.provider = "local"
                reply.meta["llm_fallback"] = meta.get("status", "unavailable")
        await self._record(student_id=student_id, session_id=session_id, question=question, reply=reply, topic_id=topic_id)
        return reply

    async def chat(self, student_id: str, message: str, **kw: Any) -> Reply:
        """Conversational learning. Same path as explain; kept separate because the
        API surface and the persistence differ slightly."""
        return await self.explain(student_id, message, **kw)

    async def turn(self, text: str, *, student_id: str, session_id: str | None = None, **kw: Any) -> tuple[str, dict[str, Any]]:
        """Adapter used by the voice pipeline: (reply_text, meta)."""
        reply = await self.explain(student_id, text, session_id=session_id, want_spoken=True, **kw)
        # The voice frame says where the words came from in the learner's own terms, never the
        # service's name or its spend (brief §3, and the canary test walks this frame).
        meta = {
            "served_by": "your own material" if reply.provider != "gemini" else "written for you",
            "intent": reply.intent,
            "level": reply.level,
            "adaptations": reply.adaptations_applied,
            "message_id": reply.message_id,
            "next_action": reply.next_action,
        }
        # Spoken replies must survive being heard once: shorter, one action.
        return spoken_form(reply), meta

    def hint(self, student_id: str, item: dict[str, Any], *, hints_used: int = 0) -> dict[str, Any]:
        ctx = self.context(student_id, topic_id=item.get("topic_id"))
        pres = ctx["presentation"]
        level = _level(ctx.get("p_know"))
        text = LT.hint_for(
            item,
            level=level,
            hints_used=hints_used,
            max_hints=pres.get("hints_allowed"),
        )
        available = len(item.get("hints") or [])
        budget = pres.get("hints_allowed")
        return {
            "hint": text,
            "hint_no": hints_used + 1,
            "max_hints": budget,
            "level": level,
            # So the client can stop offering a button that gives the same sentence.
            "hints_available": available,
            "exhausted": hints_used + 1 >= min(available, budget) if budget is not None else hints_used + 1 >= available,
        }

    def feedback(self, student_id: str, item: dict[str, Any], answer: Any, *, correct: bool, score: float | None = None) -> str:
        base = LT.feedback(item, answer if isinstance(answer, str) else json.dumps(answer), correct=correct, score=score)
        ctx = self.context(student_id, topic_id=item.get("topic_id"))
        if ctx["presentation"].get("require_teachback") and not correct:
            base += " Before the next question, say out loud what the first step is and why."
        gap = next((g for g in ctx["profile_dict"].get("knowledge_gaps", []) if not item.get("topic_id") or g.get("topic_id") == item.get("topic_id")), None)
        if gap and not correct:
            base += f" This looks like {gap.get('blocking_prerequisite_title')} underneath, not {gap.get('topic_title')}."
        return base

    def grade_and_feedback(self, student_id: str, item: dict[str, Any], answer: Any) -> dict[str, Any]:
        g = grade_item(item, answer)
        g_dict = g.as_dict()
        g_dict["feedback"] = self.feedback(student_id, item, answer, correct=g.correct, score=g.score)
        if item.get("kind") == "teachback":
            g_dict["rubric_detail"] = g.partial
        return g_dict

    async def generate_practice(
        self,
        student_id: str,
        *,
        topic_id: str | None = None,
        count: int = 4,
        session_id: str | None = None,
        note: str = "",
    ) -> list[dict[str, Any]]:
        """Fresh practice items: bank selection + parametric variants + AI-written extras."""
        with self.sf() as s:
            topics = {t.id: t for t in s.execute(select(Topic)).scalars().all()}
            items_by_topic: dict[str, list[dict[str, Any]]] = {}
            for tid, t in topics.items():
                rows = s.execute(select(Item).where(Item.topic_id == tid, Item.active.is_(True))).scalars().all()
                if not rows:
                    continue
                items_by_topic[tid] = [
                    {
                        "id": it.id,
                        "topic_id": it.topic_id,
                        "kind": it.kind,
                        "stem": it.stem,
                        "stem_template": it.stem_template if hasattr(it, "stem_template") else None,
                        "experience_mode": it.experience_mode,
                        "choices": it.choices,
                        "answer_spec": it.answer_spec,
                        "solution_steps": it.solution_steps,
                        "hints": it.hints,
                        "rubric": it.rubric,
                        "difficulty": it.difficulty,
                        "prompt_context": it.prompt_context,
                        "visual_spec": it.visual_spec,
                        "numbas_ref": it.numbas_ref,
                        "active": True,
                        "tags": it.tags,
                    }
                    for it in rows
                ]
            prof = self.ai1.profile(s, student_id)
        wanted = [topic_id] if topic_id else None
        mastery = {t.topic_id: t.p_know for t in prof.traces}
        due = [d["topic_id"] for d in (prof.retention or {}).get("due_now", [])]
        gaps = [g.get("topic_id") for g in prof.gaps]
        severity = {g.get("topic_id"): float(g.get("severity", 0.5)) for g in prof.gaps}
        pool = items_by_topic
        if wanted:
            pool = {k: v for k, v in items_by_topic.items() if k in wanted or not wanted[0]}
        with self.sf() as s:
            teaching_text, titles = self._teaching_material(s)
        picks = SEL.next_items(
            items_by_topic=pool,
            mastery=mastery,
            due_topics=due,
            gap_topics=[g for g in gaps if g],
            gap_severity=severity,
            directives=[d.as_dict() for d in prof.directives],
            count=count,
            seed=student_id,
            teaching_text=teaching_text,
            teaching_titles=titles,
        )
        from ..assessment.bank import render_item_for

        out = []
        for pick in picks:
            # bank_seed, not hash(): the grader, the hints and the reveal all re-derive the
            # variant from (student, item) with this seed, and hash() of a string is salted
            # per process -- so an item generated here would be shown with numbers that the
            # submission then marked against a different variant, and would change again
            # after a restart.
            rendered = render_item_for(pick.item, seed=bank_seed(student_id, pick.item.get("id")))
            out.append({**pick.as_dict(), "item": public_item(rendered)})
        if len(out) < count:
            # Reuse first, write second. A stored generated item that this learner has
            # not answered is a better answer to "the bank ran out" than a fresh call:
            # it already has attempts behind it, so its difficulty is measured rather
            # than declared, and the same request twice does not produce new rows.
            short = count - len(out)
            out.extend(self._reusable_generated(student_id, topic_id=topic_id, want=short))
        if len(out) < count and self.llm is not None and getattr(self.llm, "configured", False):
            extra = await self._llm_practice(
                student_id=student_id,
                topics=topics,
                want=count - len(out),
                topic_id=topic_id,
                note=note,
            )
            out.extend(extra)
        # One question, one slot in the session. Reuse and generation can both reach the
        # same stored row -- the model is asked only for the remainder, and it may well
        # rewrite a question that is already stored -- and showing it twice in one session
        # would take two answers that are not independent observations and count them as
        # if they were.
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for entry in out:
            item_id = str((entry.get("item") or {}).get("id") or "")
            if item_id and item_id in seen:
                continue
            seen.add(item_id)
            unique.append(entry)
        return unique

    # ------------------------------------------------------------------ #
    # generated items: reuse, validate, store
    # ------------------------------------------------------------------ #

    def _generated_pick(self, item: Mapping[str, Any], *, reason: str, meta: dict[str, Any]) -> dict[str, Any]:
        """The learner-facing shape of a generated item, answer key stripped.

        The payload that goes to the browser is built by ``public_item`` like every other
        pick. Handing back the internal dict -- which is what this replaced -- shipped
        ``answer_spec`` (the expected value, the correct choice) to the client, so the
        one question set the app could not vouch for was also the only one that came with
        its own answer key attached.
        """
        return {
            "item": public_item(item),
            "topic_id": item.get("topic_id"),
            "reason": reason,
            "utility": 0.4,
            "presentation": {"experience_mode": item.get("experience_mode", "practice")},
            "decision_trace": {**meta, "source": "ai2"},
        }

    def _reusable_generated(self, student_id: str, *, topic_id: str | None, want: int) -> list[dict[str, Any]]:
        """Stored, validated, not-yet-answered-by-this-learner generated items.

        "Not answered by this learner" is the memorisation guard and it is per learner on
        purpose: a question this learner has already seen measures their memory of it, so
        it is not a second observation of the skill. Item-level counters would say the item
        is fresh while this learner is answering it for the third time.
        """
        if want <= 0:
            return []
        from ..db.models import Attempt

        with self.sf() as s:
            answered = select(Attempt.item_id).where(Attempt.student_id == student_id)
            stmt = (
                select(Item)
                .where(
                    Item.source == "ai2",
                    Item.active.is_(True),
                    Item.id.not_in(answered),
                )
                # Least-answered first: retirement is `active`'s job, and spreading the
                # learner's session across the freshest questions keeps its items
                # independent of each other.
                .order_by(Item.n_attempts, Item.created_at)
                .limit(want)
            )
            if topic_id:
                stmt = stmt.where(Item.topic_id == topic_id)
            rows = list(s.execute(stmt).scalars().all())
            return [
                self._generated_pick(
                    GEN.row_dict(row),
                    reason="a question written for this topic earlier, still unseen by you",
                    meta={"stored": True, "generated_by": row.generated_by, "n_attempts": row.n_attempts},
                )
                for row in rows
            ]

    def _persist_generated(
        self, rows: Sequence[GEN.Validated], *, model: str | None
    ) -> list[dict[str, Any]]:
        """Store validated items under their content-derived id.

        Persisting before display is what makes the answer recordable at all: grading
        resolves an item with ``s.get(Item, item_id)``, so an id with no row is answered
        and then refused with "unknown or retired item".
        """
        stored: list[dict[str, Any]] = []
        with self.sf() as s:
            for validated in rows:
                payload = dict(validated.payload)
                item_id = str(payload["id"])
                existing = s.get(Item, item_id)
                if existing is None:
                    s.add(Item(**payload, source="ai2", generated_by=model, active=True))
                else:
                    # Same content, same id: the row keeps its measured difficulty and its
                    # attempt counters rather than being reset by a re-generation.
                    payload = GEN.row_dict(existing)
                s.flush()
                stored.append(
                    {
                        **payload,
                        "checks": validated.checks,
                        "generated_by": model,
                    }
                )
            s.commit()
        return stored

    async def review_teachback(self, student_id: str, item: dict[str, Any], text: str) -> dict[str, Any]:
        """Score a teach-back against a fixed three-point checklist.

        The rule grader decides. A model may read the same answer as a **second
        opinion**, running under master prompt 3
        (:func:`adapt.ai.prompts.TEACHBACK_GRADER_SYSTEM`) plus the checklist task
        prompt; its output is only used when it is structurally valid *and its quotes
        are verbatim* (:func:`adapt.assessment.teachback.accept_judgement`); otherwise
        the rejection is recorded and the rule result stands. Two properties matter
        more than the score itself:

        * the learner's text is *data* — ``as_data`` strips instruction-shaped lines and
          tags what is left, and the judge prompt says so;
        * a point can only be met by a sentence the learner actually wrote, so listing
          the rubric's words no longer passes and a good paraphrase no longer fails.

        The reply always carries master prompt 3's learner-facing contract —
        ``score`` (0..1), ``feedback`` (<=220 chars, second person, kind and
        specific) and ``misconceptions`` — on the model path and off it. An accepted
        judgement may replace the feedback and name misconceptions, and its own
        ``score`` is recorded as ``model_score`` for the trace; the recorded score
        stays the one the quoted points add up to.
        """
        from ..assessment import teachback as TB

        criteria = TB.checklist_from_rubric(item.get("rubric"))
        scored = TB.grade_checklist(text, criteria)
        out = dict(scored.as_dict())
        out["method"] = "checklist_3_point"
        out["matched"] = [p.statement for p in scored.points if p.met]
        out["missing"] = [p.statement for p in scored.points if not p.met]
        out["coverage"] = round(scored.score, 3)
        out["feedback_source"] = "rules"
        out["misconceptions_source"] = "rules"
        if self.llm is not None and getattr(self.llm, "configured", False):
            system = TB.judge_checklist(criteria)
            user = TB.as_data(text)
            safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=7000)
            res = await self.llm.complete(
                system=system,
                user=safe["q"],
                purpose="ai2_teachback",
                role="ai2",
                student_id=student_id,
                cache_prefix=SC.block("grader"),
                temperature=0.0,
                max_output_tokens=420,
                response_spec=TB.MODEL_SPEC,
            )
            await self._log_llm(
                "ai2_teachback", "ai2", res, student_id, len(user), dropped, kinds=redacted_kinds
            )
            judgement, reason = (None, "no_model_answer")
            if res.ok and res.data:
                judgement, reason = TB.accept_judgement(res.data, text=text, criteria=criteria)
            out["llm_status"] = res.status
            out["llm_checklist"] = reason
            if judgement is not None:
                merged = TB.merge_model_result(scored, judgement.points)
                out.update(merged.as_dict())
                out["method"] = "checklist_3_point+model"
                out["matched"] = [p.statement for p in merged.points if p.met]
                out["missing"] = [p.statement for p in merged.points if not p.met]
                out["coverage"] = round(merged.score, 3)
                if judgement.feedback:
                    out["feedback"] = judgement.feedback
                    out["feedback_source"] = "model"
                if judgement.misconceptions:
                    out["misconceptions"] = list(judgement.misconceptions)
                    out["misconceptions_source"] = "model"
                if judgement.score is not None:
                    out["model_score"] = round(judgement.score, 3)
        return out

    def _teaching_material(self, s) -> tuple[dict[str, str], dict[str, str]]:
        """Topic id -> (material text, title), for the method payloads.

        The teaching a method puts *before* the question comes from the learner's own
        course material, never from the item's answer key: showing the solution to the
        question you are about to be measured on is not teaching, it is marking. The
        item's own steps stay behind the measured "open the worked example" endpoint.
        """
        from ..assessment import bank as BANK

        text: dict[str, str] = {}
        titles: dict[str, str] = {}
        codes: dict[str, str] = {}
        for topic in s.execute(select(Topic)).scalars().all():
            titles[topic.id] = topic.title
            text[topic.id] = topic.description or ""
            codes[topic.id] = str(topic.code or "")
        for material in s.execute(select(LearningMaterial)).scalars().all():
            if material.topic_id and material.body:
                existing = text.get(material.topic_id) or ""
                if not existing or len(material.body) > len(existing):
                    text[material.topic_id] = material.body
        for tid, code in codes.items():
            if not text.get(tid) and code:
                notes = BANK.materials_for(code)
                if notes:
                    text[tid] = notes[0][1]
        return text, titles

    def diagnostic_items(self, student_id: str, *, count: int = 8, session_id: str | None = None) -> list[dict[str, Any]]:
        """The initial assessment: broad coverage with delivery modes rotated."""
        with self.sf() as s:
            topics = s.execute(select(Topic).order_by(Topic.difficulty)).scalars().all()
            items_by_topic: dict[str, list[dict[str, Any]]] = {}
            for t in topics:
                rows = s.execute(
                    select(Item).where(Item.topic_id == t.id, Item.active.is_(True)).order_by(Item.difficulty)
                ).scalars().all()
                items_by_topic[t.id] = [{**self._item_dict(it), "topic_title": t.title} for it in rows]
            teaching_text, titles = self._teaching_material(s)
            answered: list[dict[str, Any]] = []
            if session_id:
                # Placement adapts to what this learner has already answered *in this
                # session*. A client that re-plans after each answer therefore gets a
                # genuinely adaptive sequence; one that asks once gets the sequence the
                # evidence supports at that moment.
                from ..db.models import Attempt

                answered = [
                    {"item_id": a.item_id, "topic_id": a.topic_id, "correct": a.correct}
                    for a in s.execute(
                        select(Attempt).where(
                            Attempt.student_id == student_id,
                            Attempt.session_id == session_id,
                            Attempt.correct.is_not(None),
                        )
                    ).scalars().all()
                ]
        picks = SEL.placement_plan(
            topics=[{"id": t.id, "code": t.code, "title": t.title, "difficulty": t.difficulty} for t in topics],
            items_by_topic=items_by_topic,
            count=count,
            seed=student_id,
            answered=answered,
            teaching_text=teaching_text,
            teaching_titles=titles,
        )
        from ..assessment.bank import render_item_for

        out = []
        for i, pick in enumerate(picks):
            # Same seed as every other surface for this learner+item, so a reveal or a
            # follow-up hint can never disagree with the stem that was placed.
            rendered = render_item_for(pick.item, seed=bank_seed(student_id, pick.item.get("id")))
            public = public_item(rendered)
            # Placement promises "no hints". ``prompt_context`` is a hint in all but name
            # ("More digits does not mean a bigger decimal…"), and it was shown above the very
            # questions that promise is about. Withheld here *and* in the component, because a
            # promise the client keeps and the server does not is not a promise.
            withheld = public.pop("prompt_context", None)
            entry = {**pick.as_dict(), "item": public, "position": i + 1, "of": len(picks)}
            if withheld:
                entry["prompt_context_withheld"] = (
                    "placement questions are answered unaided; this text is kept for practice"
                )
            out.append(entry)
        return out

    def method_probe_items(self, student_id: str, *, count: int = 8) -> list[dict[str, Any]]:
        """The second half of what the 12-item diagnostic used to do: compare methods.

        Six to eight concepts, one method each, the answer format held fixed per
        concept — so the difference between two of these questions is what was taught,
        not how the answer had to be given.
        """
        with self.sf() as s:
            topics = s.execute(select(Topic).order_by(Topic.difficulty)).scalars().all()
            items_by_topic: dict[str, list[dict[str, Any]]] = {}
            for t in topics:
                rows = s.execute(
                    select(Item).where(Item.topic_id == t.id, Item.active.is_(True)).order_by(Item.difficulty)
                ).scalars().all()
                if rows:
                    items_by_topic[t.id] = [{**self._item_dict(it), "topic_title": t.title} for it in rows]
            teaching_text, titles = self._teaching_material(s)
        picks = SEL.method_probe_plan(
            topics=[{"id": t.id, "code": t.code, "title": t.title, "difficulty": t.difficulty} for t in topics],
            items_by_topic=items_by_topic,
            learner_id=student_id,
            count=max(6, min(8, count)),
            teaching_text=teaching_text,
            teaching_titles=titles,
        )
        from ..assessment.bank import render_item_for

        out = []
        for i, pick in enumerate(picks):
            rendered = render_item_for(pick.item, seed=bank_seed(student_id, pick.item.get("id")))
            out.append({**pick.as_dict(), "item": public_item(rendered), "position": i + 1, "of": len(picks)})
        return out

    # ------------------------------------------------------------------ #
    # phase 4: the content engine
    # ------------------------------------------------------------------ #

    def content_pack(
        self,
        *,
        concept: str,
        kind: str = "variant_set",
        topic_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate one concept's teaching pack: three variants, a matched pair, hints, a checklist.

        This is the hook the orchestrator's turn cycle looks for
        (``state/orchestrator.py`` step 3 calls ``ai2.content_pack``), and it is the only place in the
        app that authors content. The deterministic engine in :mod:`adapt.content` does the work; this
        method exists so the call arrives with AI 2's identity and the write matrix keeps its promise
        that authoring belongs to the AI and validating belongs to code.

        ``topic_id`` is the topic key (``calculus``, ``kiswahili``), not a database row id: the engine
        reads the authored chain by key, which is what makes it usable with no database at all — the
        same reason the quality gate can run it in a sandbox with no server.

        Returns the payload the registry stores, with ``concept``/``kind``/``payload`` keys the
        orchestrator reads. A topic with no authored chain returns a payload that says so, because
        "we have nothing for this concept" is a result and an invented concept is not.
        """
        from ..content.pipeline import generate

        result = generate(str(topic_id or concept), concept_id=str(concept), p_know=context.get("p_know") if context else None)
        if not result.available or not result.packs:
            return {
                "payload": {
                    "concept": str(concept),
                    "available": False,
                    "reason": result.reason,
                    "spec": result.spec,
                    "packs": [],
                },
                "notes": [result.reason, *result.notes],
            }
        pack = result.packs[0]
        payload = pack.as_dict()
        payload["spec"] = result.spec
        payload["available"] = True
        payload["reason"] = result.reason
        return {"payload": payload, "notes": list(result.notes) + list(result.rejections and [f"rejected: {r['code']}" for r in result.rejections] or [])}

    def validate_pack(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The deterministic §7.3 check on a pack payload, in the orchestrator's verdict shape.

        Exposed on AI 2 because the orchestrator asks the *generator* to validate what it made, and the
        honest implementation of that is to hand the payload to the code that has no stake in it. The
        write matrix still holds where it matters: the store's ``set_validation`` accepts only
        ``module="code"``, so an AI can never bless its own content in the registry — this verdict
        travels with the pack as a claim, and the store records it as one.
        """
        from ..content.validation import validate_pack

        return validate_pack(payload)

    def breakdown(self, topic_key: str, *, level: str | None = None) -> dict[str, Any]:
        """A topic's concepts, prerequisites, misconceptions and objectives (§7.1)."""
        from ..content.breakdown import breakdown_for

        return breakdown_for(topic_key, level=level).as_dict()

    @staticmethod
    def _item_dict(it: Any) -> dict[str, Any]:
        """One stored item as the plain dict the selector expects."""
        return {
            "id": it.id,
            "topic_id": it.topic_id,
            # Filled in by the callers that have the Topic rows in hand (see
            # ``diagnostic_items`` and ``_picks``); absent means "show no title" rather than
            # "show the id".
            "topic_title": getattr(it, "topic_title", None),
            "kind": it.kind,
            "stem": it.stem,
            "stem_template": getattr(it, "stem_template", None),
            "experience_mode": it.experience_mode,
            "choices": it.choices,
            "answer_spec": it.answer_spec,
            "solution_steps": it.solution_steps,
            "hints": it.hints,
            "rubric": it.rubric,
            "difficulty": it.difficulty,
            "est_seconds": it.est_seconds,
            "prompt_context": it.prompt_context,
            "visual_spec": it.visual_spec,
            "numbas_ref": it.numbas_ref,
            "tags": it.tags,
            "active": True,
        }

    def next_items(self, student_id: str, *, topic_id: str | None = None, count: int = 5, used: Sequence[str] = ()) -> list[dict[str, Any]]:
        return [
            {**p.as_dict(), "item": public_item(p.item)}
            for p in self._picks(student_id, topic_id=topic_id, count=count, used=used)
        ]

    def _picks(self, student_id: str, *, topic_id: str | None, count: int, used: Sequence[str]):
        with self.sf() as s:
            prof = self.ai1.profile(s, student_id)
            topics = {t.id: t for t in s.execute(select(Topic)).scalars().all()}
            items_by_topic: dict[str, list[dict[str, Any]]] = {}
            wanted_ids = [topic_id] if topic_id else list(topics)
            for tid in wanted_ids:
                rows = s.execute(select(Item).where(Item.topic_id == tid, Item.active.is_(True))).scalars().all()
                if rows:
                    items_by_topic[tid] = [self._item_dict(it) for it in rows]
            teaching_text, titles = self._teaching_material(s)
        mastery = {t.topic_id: t.p_know for t in prof.traces}
        counts: dict[str, int] = {}
        for u in used:
            tid = u.split(":", 1)[0]
            counts[tid] = counts.get(tid, 0) + 1
        from ..assessment.bank import render_item_for

        picks = SEL.next_items(
            items_by_topic=items_by_topic,
            mastery=mastery,
            teaching_text=teaching_text,
            teaching_titles=titles,
            counts=counts,
            due_topics=[d["topic_id"] for d in (prof.retention or {}).get("due_now", [])],
            gap_topics=[g.get("topic_id") for g in prof.gaps if g.get("topic_id")],
            gap_severity={g.get("topic_id"): float(g.get("severity", 0.5)) for g in prof.gaps},
            # The *prerequisite* behind each gap, so the queue can put real prerequisite
            # items first instead of only saying that it did (see
            # ``selector.enforce_target_band``).
            gap_prerequisites={
                g.get("topic_id"): [g["blocking_prerequisite"]]
                for g in prof.gaps
                if g.get("topic_id") and g.get("blocking_prerequisite")
            },
            directives=[d.as_dict() for d in prof.directives],
            used_item_ids=used,
            count=count,
            seed=student_id,
            params=None,
        )
        out = []
        for p in picks:
            p.item = render_item_for(p.item, seed=bank_seed(student_id, p.item.get("id")))
            out.append(p)
        return out

    async def remediation(self, student_id: str, *, topic_id: str | None = None) -> dict[str, Any]:
        """The 'go back to the prerequisite' package: mini-lesson + 3 items."""
        graph, mastery, plan = self.ai1.plan(student_id)
        steps = plan.get("remediation") or []
        if topic_id:
            steps = [x for x in steps if x.get("topic_id") == topic_id] or steps
        if not steps:
            return {"needed": False, "note": "no prerequisite below the gap threshold right now"}
        first = steps[0]
        tid = first["topic_id"]
        with self.sf() as s:
            topic = s.get(Topic, tid)
            rows = s.execute(select(Item).where(Item.topic_id == tid, Item.active.is_(True)).order_by(Item.difficulty)).scalars().all()
            items = [
                {
                    "id": it.id,
                    "topic_id": it.topic_id,
                    "kind": it.kind,
                    "stem": it.stem,
                    "stem_template": it.stem_template if hasattr(it, "stem_template") else None,
                    "experience_mode": it.experience_mode,
                    "choices": it.choices,
                    "answer_spec": it.answer_spec,
                    "solution_steps": it.solution_steps,
                    "hints": it.hints,
                    "difficulty": it.difficulty,
                    "active": True,
                }
                for it in rows
            ]
        from ..assessment.bank import render_item_for

        picks = SEL.remediation_items(topic_id=tid, items=items, count=int(first.get("items", 3)))
        lesson = ""
        if topic is not None:
            idx = LT.build_index(materials=[(tid, tid, topic.description or "", {"title": topic.title})])
            lesson = LT.explain(topic.title, index=idx, topic_title=topic.title, p_know=mastery.get(tid, 0.2), max_words=120).text
        return {
            "needed": True,
            "topic_id": tid,
            "topic_title": first.get("topic_title"),
            "mastery": first.get("mastery"),
            "unblocks": first.get("unblocks") or [],
            "why": first.get("why"),
            "lesson": lesson,
            # public_item() here is not decoration: the remediation payload is shown
            # before the learner has answered, so a raw bank item would hand them the
            # correct_index of the very questions we are about to measure.
            "items": [_remediation_row(p, student_id, render_item_for=render_item_for, public_item=public_item) for p in picks],
            "all_steps": steps,
        }

    async def feedback_for(self, student_id: str, item: dict[str, Any], g: dict[str, Any]) -> str:
        """LLM-optional enrichment of the rule-based feedback (never replaces it)."""
        base = g.get("feedback") or ""

        def safe_feedback(value: Any) -> str:
            text = _clean_model_text(value, maximum=1200)
            if not text:
                return ""
            # FEEDBACK_TASK asks for two or three sentences. If the model does not
            # meet that small contract, keep the deterministic grader wording rather
            # than showing an unbounded or half-finished answer.
            if len(LT.sentences(text)) not in (2, 3):
                return ""
            return text
        if self.llm is None or not getattr(self.llm, "configured", False):
            return base
        ctx = self.context(student_id, topic_id=item.get("topic_id"))
        user = json.dumps(
            {
                "question": (item.get("stem") or "")[:600],
                "learner_answer": str(g.get("detail") or "")[:400],
                "grading": {k: g.get(k) for k in ("correct", "score", "method", "partial")},
                "p_know": ctx.get("p_know"),
                "adaptations": [d["strategy"] for d in ctx["directives"]],
            },
            separators=(",", ":"),
        )
        safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=4000)
        res = await self.llm.complete(
            system="",
            user=safe["q"],
            purpose="ai2_feedback",
            role="ai2",
            student_id=student_id,
            cache_prefix=SC.block("feedback"),
            temperature=0.3,
            max_output_tokens=320,
        )
        await self._log_llm(
            "ai2_feedback", "ai2", res, student_id, len(user), dropped, kinds=redacted_kinds
        )
        if res.ok:
            accepted = safe_feedback(res.text)
            if accepted:
                return accepted
        return base

    # ------------------------------------------------------------------ #
    # interactive scenes (the engine's authoring path)
    # ------------------------------------------------------------------ #

    async def author_scene(
        self,
        *,
        request: str,
        kind: str | None = None,
        student_id: str | None = None,
        corrections: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Draft a scene *specification* for a topic -- data, never code.

        The contract with the rest of the engine is that this method does not decide whether the
        scene is usable: :func:`adapt.learning.scenespec.validate` does, by refusing anything
        executable, checking the parameters against what the simulation needs (a binary-search
        list that is not sorted is refused *because binary search would be wrong*), and dry-running
        the animation. So a draft can come back invalid and be honest about it, and one retry is
        spent feeding the reasons straight back to the model.

        With no model configured, the answer is the closest built-in scene for the request, said
        to be exactly that. A lesson that plays is worth more than a 503, and an invented spec
        that cannot be checked would be worth less than either.
        """
        from ..learning import scenespec, sims

        text = " ".join(str(request or "").split())[:400]
        catalogue = sims.catalogue()
        wanted = str(kind or "").strip()
        if wanted and wanted not in catalogue:
            return {
                "ok": False,
                "errors": [f"the engine cannot draw '{wanted}'. It can draw: {', '.join(sorted(catalogue))}"],
                "source": "none",
            }
        if self.llm is None:
            return self._builtin_scene(text, wanted or None, why="a written-for-you scene is not available on this instance")
        hints = list(corrections or [])
        spec_kind = {
            "kind": "str",
            "title": "str",
            "intent": "str",
            "intro": "str",
            "outro": "str",
            "concepts": ("array", {"type": "STRING"}),
            "params_json": "str",
        }
        # The task, the catalogue of kinds and the parameter rules used to be built here and
        # sent with every authoring request -- and again with the retry. They are the static
        # head now (`static_context.block("scene_author")`), so what is left in the request is
        # only what differs: the request, the preferred kind, and the validator's reasons from
        # a previous attempt.
        user = json.dumps({"request": text[:600], "prefer_kind": wanted or None, "previous_rejections": hints[:6]}, separators=(",", ":"))
        safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=4000)
        res = await self.llm.complete(
            system="",
            user=safe["q"],
            purpose="ai2_scene_spec",
            cache_prefix=SC.block("scene_author"),
            role="ai2",
            student_id=student_id or "anon",
            temperature=0.2,
            max_output_tokens=700,
            response_spec=spec_kind,
        )
        await self._log_llm("ai2_scene_spec", "ai2", res, student_id or "anon", len(user), dropped, kinds=redacted_kinds)
        data = res.data if (res.ok and isinstance(res.data, dict)) else None
        if not data:
            fallback = self._builtin_scene(text, wanted or None, why="a written-for-you scene could not be built just now")
            fallback["llm_status"] = res.status
            return fallback
        params: Any = {}
        raw_params = data.get("params_json")
        if isinstance(raw_params, str) and raw_params.strip():
            try:
                params = json.loads(raw_params)
            except json.JSONDecodeError as exc:
                params = {}
                data["_params_error"] = f"params_json was not JSON: {exc}"
        draft: dict[str, Any] = {
            "kind": str(data.get("kind") or wanted or "").strip(),
            "title": str(data.get("title") or "")[:160],
            "intent": str(data.get("intent") or "")[:400],
            "framing": {"intro": str(data.get("intro") or "")[:600], "outro": str(data.get("outro") or "")[:600]},
            "concepts": [str(c) for c in (data.get("concepts") or []) if str(c).strip()][:6],
            "citations": [{"ref": "the engine's own simulation", "claim": "the frames shown are computed by the same code that answers about them."}],
            "params": params if isinstance(params, dict) else {},
        }
        if data.get("_params_error"):
            draft["intent"] = (draft["intent"] + f" ({data['_params_error']})")[:400]
        result = scenespec.validate(draft)
        if result.ok:
            return {"ok": True, "draft": result.spec, "source": "llm", "warnings": result.warnings, "errors": []}
        # one retry, with the validator's reasons in the prompt -- the loop the brief asks for
        retry_user = json.dumps(
            {"request": text[:600], "your_previous_attempt": draft, "rejected_because": result.errors[:6]}, separators=(",", ":")
        )
        safe2, dropped2, redacted_kinds2 = redact_payload({"q": retry_user}, max_chars=5000)
        res2 = await self.llm.complete(
            system="",
            user=safe2["q"],
            purpose="ai2_scene_fix",
            cache_prefix=SC.block("scene_author"),
            role="ai2",
            student_id=student_id or "anon",
            temperature=0.0,
            max_output_tokens=700,
            response_spec=spec_kind,
        )
        await self._log_llm("ai2_scene_fix", "ai2", res2, student_id or "anon", len(retry_user), dropped2, kinds=redacted_kinds2)
        d2 = res2.data if (res2.ok and isinstance(res2.data, dict)) else None
        if d2:
            p2: Any = {}
            if isinstance(d2.get("params_json"), str) and d2["params_json"].strip():
                try:
                    p2 = json.loads(d2["params_json"])
                except json.JSONDecodeError:
                    p2 = {}
            draft2 = {
                "kind": str(d2.get("kind") or draft["kind"]),
                "title": str(d2.get("title") or draft["title"])[:160],
                "intent": str(d2.get("intent") or draft["intent"])[:400],
                "framing": {"intro": str(d2.get("intro") or "")[:600], "outro": str(d2.get("outro") or "")[:600]},
                "concepts": [str(c) for c in (d2.get("concepts") or []) if str(c).strip()][:6],
                "citations": list(draft["citations"]),
                "params": p2 if isinstance(p2, dict) else {},
            }
            result2 = scenespec.validate(draft2)
            if result2.ok:
                return {
                    "ok": True,
                    "draft": result2.spec,
                    "source": "llm",
                    "warnings": result2.warnings,
                    "errors": [],
                    "retried_after": result.errors[:6],
                }
            return {
                "ok": False,
                "draft": None,
                "source": "llm",
                "errors": result2.errors,
                "first_attempt_errors": result.errors,
                "note": "the model was given these reasons once and could not fix them; the scene was not stored",
            }
        return {"ok": False, "draft": None, "source": "llm", "errors": result.errors, "note": "the first draft was rejected and the retry did not answer"}

    def _builtin_scene(self, request: str, kind: str | None, *, why: str) -> dict[str, Any]:
        """The closest validated scene for a request, chosen by what the words mention.

        This is a real scene the engine can play, not a placeholder: it comes from the same
        ``builtin_specs`` the database is seeded with, and the reply says in plain words that
        nothing was written for the request — a built-in scene was chosen instead.
        """
        from ..learning import scenespec

        words = request.lower()
        clues = [
            ("binary_search", ("search", "sorted", "log", "halv", "middle", "index")),
            ("sorting", ("sort", "insertion", "order", "compare", "swap", "quadratic")),
            ("logic_gates", ("gate", "truth table", "boolean", "logic", "nand", "xor", "circuit")),
            ("cpu_cycle", ("fetch", "decode", "execute", "register", "program", "cpu", "memory", "pc", "opcode")),
        ]
        chosen = kind
        if chosen is None:
            scored = sorted(
                ((sum(1 for c in cs if c in words), k) for k, cs in clues),
                key=lambda t: (-t[0], t[1]),
            )
            chosen = scored[0][1] if scored and scored[0][0] > 0 else "binary_search"
        for spec in scenespec.builtin_specs():
            if spec["kind"] == chosen:
                return {
                    "ok": True,
                    "draft": spec,
                    "source": "builtin",
                    "warnings": [],
                    "errors": [],
                    "note": (
                        f"{why}, so this is the scene already in the app for '{chosen}' "
                        "rather than one written for your words"
                    ),
                }
        return {
            "ok": False,
            "draft": None,
            "source": "builtin",
            "errors": [f"no scene already in the app for '{chosen}'"],
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    async def _generate(
        self,
        *,
        question: str,
        ctx: dict[str, Any],
        local: LT.Grounded,
        student_id: str,
        history: Sequence[tuple[str, str]],
        purpose: str,
        want_spoken: bool,
        intent: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        payload = {
            "question": question[:1500],
            "learner": to_llm_payload(ctx["profile"]),
            "active_adaptations": [
                {"strategy": d["strategy"], "what": d.get("why", "")[:160], "params": d.get("payload")}
                for d in ctx["directives"]
            ],
            "grounded_source_material": [s.get("text", "")[:400] for s in local.sections if s.get("label") == "explanation"][:3],
            "presentation_rules": {
                k: v
                for k, v in ctx["presentation"].items()
                if k in ("max_words", "attach_worked_example", "require_teachback", "hints_allowed", "ask_confidence")
            },
            "spoken": want_spoken,
            "in_the_moment_ask": intent,
        }
        user = json.dumps(payload, separators=(",", ":"))[:9000]
        safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=9000)
        res = await self.llm.complete(
            system="",
            user=safe["q"],
            purpose=purpose,
            role="ai2",
            student_id=student_id,
            cache_prefix=SC.block("tutor"),
            temperature=0.45 if not want_spoken else 0.3,
            max_output_tokens=900 if not want_spoken else 420,
            response_spec=REPLY_SPEC,
            history=[(r, t) for r, t in history][-6:],
        )
        meta = {**res.as_dict(), "redacted_fields": (res.redacted_fields or 0) + dropped}
        await self._log_llm(purpose, "ai2", res, student_id, len(user), dropped, kinds=redacted_kinds)
        if not res.ok:
            meta["fell_back_to"] = "local tutor (extractive, grounded in course material)"
            return None, meta
        data = dict(res.data or {})
        answer = _clean_model_text(data.get("answer"), maximum=3500)
        if not answer:
            meta["fell_back_to"] = "local tutor (model returned no safe answer)"
            return None, meta
        data["answer"] = answer
        data["sections"] = _accepted_reply_sections(data.get("sections"), local.sections)
        data["next_action"] = _accepted_next_action(data.get("next_action"), None)
        data["adaptations_applied"] = _accepted_adaptations(
            data.get("adaptations_applied"), ctx["directives"], local.directives_applied
        )
        rules = ctx["presentation"]
        if rules.get("max_words"):
            words = data["answer"].split()
            if len(words) > int(rules["max_words"]) * 3:  # 3x the rule as a hard ceiling
                data["answer"] = " ".join(words[: int(rules["max_words"]) * 3]) + "."
        return data, meta

    async def _resource_hint(self, ctx: dict[str, Any]) -> str | None:
        try:
            resp = await self.resources.recommend_for_topic(
                title=ctx["topic"].title,
                note=(ctx["topic"].description or ""),
                level_hint=ctx.get("level", ""),
                limit=3,
            )
            if not resp.results:
                return None
            best = resp.results[0]
            return f"{best.get('title')} ({best.get('provider')}, {best.get('year') or 'n.d.'}) — {best.get('url')}"
        except Exception as exc:  # resource lookup must never break an answer
            log.info("resource hint skipped: %s", exc.__class__.__name__)
            return None

    async def curate_resources(
        self,
        *,
        query: str,
        results: Sequence[Mapping[str, Any]],
        level_hint: str = "",
        student_id: str | None = None,
        limit: int = 4,
    ) -> dict[str, Any]:
        """Master prompt 4: the search results, curated down to the picks for this learner.

        The deterministic path is the floor and always answers: the upstream rank
        order with a level-appropriate sentence each. The model is the upgrade, and
        its whole output is refused unless every pick's ``url`` is one it was actually
        given (:func:`adapt.resources.curation.accept_picks`) -- a curator that could
        add a plausible URL would be a curator that could recommend a source that does
        not exist. The reply says which path produced it.
        """
        from ..resources import curation as CUR

        out: dict[str, Any] = {
            "picks": CUR.deterministic_picks(results, level_hint=level_hint, limit=limit),
            "picks_source": "rules",
        }
        if not results:
            out["note"] = "nothing returned by the search, so there is nothing to curate"
            return out
        if self.llm is None or not getattr(self.llm, "configured", False):
            return out
        user = CUR.curation_task(query, level_hint, results)
        safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=7000)
        try:
            res = await self.llm.complete(
                system="",
                user=safe["q"],
                purpose="ai2_curate",
                role="ai2",
                student_id=student_id or "anon",
                cache_prefix=SC.block("curator"),
                temperature=0.2,
                max_output_tokens=520,
                response_spec=CUR.CURATION_SPEC,
            )
        except Exception as exc:  # curation decorates a search; it must not break one
            log.info("resource curation skipped: %s", exc.__class__.__name__)
            out["llm_status"] = "error"
            return out
        await self._log_llm("ai2_curate", "ai2", res, student_id or "anon", len(user), dropped, kinds=redacted_kinds)
        out["llm_status"] = res.status
        picks, reason = (None, "no_model_answer")
        if res.ok and res.data:
            picks, reason = CUR.accept_picks(res.data, results=results, limit=limit)
        out["llm_picks"] = reason
        if picks is not None:
            out["picks"] = picks
            out["picks_source"] = "model"
        return out

    async def _llm_practice(
        self, *, student_id: str, topics: dict[str, Any], want: int, topic_id: str | None, note: str
    ) -> list[dict[str, Any]]:
        """Write practice items with the model, then hold them to the item contract.

        The order is the point: the model proposes, every proposal is validated by
        ``assessment.generated``, the survivors are **stored** under a content-derived id,
        and only then is anything handed back to be shown. An item that fails a check is
        neither displayed nor stored, and the rejection is logged with its code -- a run
        where the model produced six questions and four were refused is a different
        operational fact from a model that produced nothing, and the log has to tell them
        apart.

        Rules 1-4 of the contract live here; rule 5 (retirement) lives in
        ``services.attempts``, where the responses actually arrive.
        """
        titles = [t.title for t in topics.values()][:12]
        user = json.dumps(
            {
                "topic_titles": titles,
                "topic_id": topic_id,
                "count": want,
                "note": note[:200],
            },
            separators=(",", ":"),
        )[:4000]
        safe, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=4000)
        res = await self.llm.complete(
            system="",
            user=safe["q"],
            purpose="ai2_generate_practice",
            role="ai2",
            student_id=student_id,
            cache_prefix=SC.block("practice_author"),
            temperature=0.7,
            max_output_tokens=1200,
            response_spec={"items": ("array", {"type": "OBJECT"})},
        )
        await self._log_llm(
            "ai2_generate_practice", "ai2", res, student_id, len(user), dropped, kinds=redacted_kinds
        )
        if not (res.ok and res.data and isinstance(res.data.get("items"), list)):
            return []

        allowed = set(topics)
        existing = self._authored_stems(topic_id if topic_id in allowed else None, allowed)
        accepted: list[GEN.Validated] = []
        rejected: list[GEN.Rejection] = []
        seen: set[str] = set()
        for raw in res.data["items"][: want * 3]:
            if not isinstance(raw, dict):
                continue
            tid = raw.get("topic_id") if raw.get("topic_id") in allowed else (topic_id or next(iter(topics), None))
            if tid not in allowed:
                rejected.append(GEN.Rejection("topic_unknown", "the model answered for an unknown topic"))
                continue
            validated, bad = GEN.validate_generated(
                raw, topic_id=str(tid), topic_ids=allowed, existing_stems=existing.get(str(tid), ())
            )
            if validated is None:
                rejected.extend(bad)
                continue
            if validated.payload["id"] in seen:
                continue
            seen.add(str(validated.payload["id"]))
            accepted.append(validated)
            if len(accepted) >= want:
                break
        if rejected:
            log.info(
                "ai2 practice: kept %d of %d candidate items from %s; refused: %s",
                len(accepted),
                len(res.data["items"]),
                res.model or "?",
                GEN.summarise(rejected),
            )
        rows = self._persist_generated(accepted, model=res.model or "gemini")
        return [
            self._generated_pick(
                row,
                reason="written for this topic because the bank had nothing left to ask",
                meta={"llm": True, "stored": True, "checks": list(row.get("checks") or [])},
            )
            for row in rows
        ]

    def _authored_stems(self, topic_id: str | None, allowed: set[str]) -> dict[str, list[str]]:
        """Authored stems per topic, so a generated question cannot duplicate one.

        Two rows with one question is one question counted twice: the learner answers it
        believing it is new material, and the second answer measures memory rather than
        the skill.
        """
        wanted = [topic_id] if topic_id else sorted(allowed)[:20]
        if not wanted:
            return {}
        with self.sf() as s:
            out: dict[str, list[str]] = {}
            for tid in wanted:
                rows = s.execute(
                    select(Item.stem).where(Item.topic_id == tid, Item.source != "ai2").limit(200)
                ).scalars().all()
                out[str(tid)] = [str(stem) for stem in rows if stem]
            return out

    async def _log_llm(
        self,
        purpose: str,
        role: str,
        res: Any,
        student_id: str,
        prompt_chars: int,
        dropped: int,
        kinds: Sequence[str] = (),
    ) -> None:
        """One row per LLM call. ``kinds`` is what was redacted, not just how much.

        ``LlmCall.redacted_fields`` is a count, and a count with no shape to it is what
        let this call path stay dead without anyone noticing: the row said "0 fields
        dropped" whether the payload was clean or the redactor never ran.
        """
        if kinds:
            log.debug("%s: redacted %s", purpose, ", ".join(sorted(set(kinds))[:8]))
        try:
            with self.sf() as s:
                s.add(
                    LlmCall(
                        student_id=student_id,
                        purpose=purpose,
                        role=role,
                        model=res.model or "?",
                        status=res.status if res.ok else (res.status or "error"),
                        tokens_in=res.tokens_in,
                        tokens_out=res.tokens_out,
                        latency_ms=res.latency_ms,
                        redacted_fields=dropped,
                        prompt_chars=prompt_chars,
                        tier=getattr(res, "tier", None),
                        cached_tokens=int(getattr(res, "cached_tokens", 0) or 0),
                        day=utcnow().date().isoformat(),
                    )
                )
                s.commit()
        except Exception:  # accounting must never break a reply
            log.debug("llm call accounting failed", exc_info=True)

    async def _record(self, *, student_id: str, session_id: str | None, question: str, reply: Reply, topic_id: str | None) -> None:
        with self.sf() as s:
            q = ChatMessage(
                student_id=student_id,
                session_id=session_id,
                role="student",
                content=question,
                topic_id=topic_id,
                provider="none",
            )
            a = ChatMessage(
                student_id=student_id,
                session_id=session_id,
                role="assistant",
                content=reply.text,
                topic_id=topic_id,
                provider=reply.provider,
                model=reply.llm.get("model"),
                adapted_from=reply.adaptations_applied,
                redacted_count=int(reply.llm.get("redacted_fields") or 0),
            )
            s.add_all([q, a])
            s.flush()
            reply.message_id = a.id
            if session_id:
                sess = s.get(LearningSession, session_id)
                if sess is not None and sess.mode == "voice":
                    s.add(VoiceTurn(student_id=student_id, ws_session=session_id, direction="in", text=question, stt_backend="text"))
                    s.add(VoiceTurn(student_id=student_id, ws_session=session_id, direction="out", text=reply.text, tts_backend=reply.provider))
            s.commit()


def _level(p_know: float | None) -> str:
    if p_know is None:
        return "new"
    if p_know < 0.35:
        return "new"
    if p_know < 0.65:
        return "developing"
    if p_know < 0.85:
        return "proficient"
    return "mastered"


def spoken_form(reply: Reply) -> str:
    """Collapse a written answer into something that works when heard once."""
    body = " ".join(s["text"] for s in reply.sections if s.get("label") in ("explanation", "framing", "worked example")) or reply.text
    body = " ".join(body.split())
    sentences = [x.strip() for x in LT.sentences(body) if x.strip()]
    out = " ".join(sentences[:4])
    if reply.next_action:
        out = (out + " " + reply.next_action).strip()
    return (out or body)[:900]


def public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Strip answers/hints from an item before it reaches the browser.

    ``topic_title`` travels with the item because the question screen used to print the
    topic *id* (``top_a6062406``) at the learner. An id is an internal key and reads as
    machine noise; the title is the thing the learner chose when they picked the topic.
    """
    return {
        "id": item.get("id"),
        "topic_id": item.get("topic_id"),
        "topic_title": item.get("topic_title"),
        "kind": item.get("kind"),
        "stem": item.get("stem"),
        "prompt_context": item.get("prompt_context"),
        "visual_spec": item.get("visual_spec"),
        "choices": [{"index": i, "text": c.get("text")} for i, c in enumerate(item.get("choices") or [])] or None,
        "experience_mode": item.get("experience_mode"),
        "difficulty": item.get("difficulty"),
        "est_seconds": item.get("est_seconds"),
        "hint_count": len(item.get("hints") or []),
        "has_worked_example": bool(item.get("solution_steps")),
        "numbas_ref": item.get("numbas_ref"),
        "teachback": item.get("kind") == "teachback",
    }
