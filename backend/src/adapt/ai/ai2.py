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
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select

from ..assessment import selector as SEL
from ..assessment.grading import grade as grade_item
from ..assessment.numbas import checksum
from ..config import Settings, get_settings
from ..db.models import (
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
from .prompts import AI2_SYSTEM

log = logging.getLogger("adapt.ai2")

REPLY_SPEC = {
    "answer": "str",
    "sections": ("array", {"type": "OBJECT"}),
    "next_action": "str",
    "adaptations_applied": ("array", {"type": "STRING"}),
    "ask_teachback": "str",
    "resource_hint": "str",
}


def bank_seed(student_id: str | None, item_id: str | None) -> int:
    """Indirection so every AI 2 render asks bank.variant_seed for the same number."""
    from ..assessment.bank import variant_seed

    return variant_seed(student_id, item_id)


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
    ) -> None:
        self.sf = session_factory
        self.ai1 = ai1
        self.settings = settings or get_settings()
        self.llm = llm
        self.resources = resources

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
            items = []
            if topic:
                for it in s.execute(select(Item).where(Item.topic_id == topic.id).limit(30)).scalars().all():
                    items.append(
                        {
                            "id": it.id,
                            "topic_id": it.topic_id,
                            "kind": it.kind,
                            "stem": it.stem,
                            "experience_mode": it.experience_mode,
                            "prompt_context": it.prompt_context,
                            "visual_spec": it.visual_spec,
                            "choices": it.choices,
                            "answer_spec": it.answer_spec,
                            "solution_steps": it.solution_steps,
                            "hints": it.hints,
                            "rubric": it.rubric,
                            "difficulty": it.difficulty,
                            "explanation": it.prompt_context or "",
                        }
                    )
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
            return {
                "profile": profile,
                "profile_dict": profile.as_dict(),
                "topic": topic,
                "p_know": p_know,
                "directives": directives,
                "materials": [(m.id, m.topic_id or "", m.body, {"title": m.title}) for m in materials],
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
    ) -> Reply:
        """Answer an educational question, adapted to the learner."""
        ctx = self.context(student_id, topic_id=topic_id)
        idx = self.index_for(ctx)
        intent = LT.unexpected_request(question)
        local = LT.explain(
            question,
            index=idx,
            topic_title=ctx["topic"].title if ctx.get("topic") else None,
            p_know=ctx.get("p_know"),
            directives=ctx["directives"],
            item=(ctx["items"] or [None])[0],
            prereq_note=ctx.get("prereq_note"),
            max_words=600 if intent in ("simpler", "shorter") else 320,
        )
        reply = Reply(
            text=local.text,
            sections=local.sections,
            sources=local.sources,
            adaptations_applied=local.directives_applied,
            level=local.level,
            intent=intent,
            meta={"grounded": local.confident, "provider_detail": "local BM25 over course materials"},
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
        meta = {
            "provider": reply.provider,
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
        picks = SEL.next_items(
            items_by_topic=pool,
            mastery=mastery,
            due_topics=due,
            gap_topics=[g for g in gaps if g],
            gap_severity=severity,
            directives=[d.as_dict() for d in prof.directives],
            count=count,
            seed=student_id,
        )
        from ..assessment.bank import render_item_for

        out = []
        for pick in picks:
            rendered = render_item_for(pick.item, seed=abs(hash((student_id, pick.item.get("id")))) & 0xFFFF)
            out.append({**pick.as_dict(), "item": public_item(rendered)})
        if len(out) < count and self.llm is not None and getattr(self.llm, "configured", False):
            extra = await self._llm_practice(student_id=student_id, topics=topics, want=count - len(out), topic_id=topic_id, note=note)
            out.extend(extra)
        return out

    async def review_teachback(self, student_id: str, item: dict[str, Any], text: str) -> dict[str, Any]:
        """Score a teach-back: rubric coverage locally, plus an LLM comment if available."""
        scored = LT.teachback_score(text, item.get("rubric"))
        out = dict(scored)
        out["method"] = scored.get("method")
        if self.llm is not None and getattr(self.llm, "configured", False):
            system = AI2_SYSTEM + "\nScoring a teach-back. Judge coverage of the required ideas only."
            user = json.dumps(
                {
                    "required": [c.get("concept") for c in ((item.get("rubric") or {}).get("required_concepts") or [])],
                    "local_rubric_result": {k: scored[k] for k in ("score", "matched", "missing") if k in scored},
                    "learner_explanation": text[:1800],
                    "task": "Say what is right, name the single missing idea, ask one question that would expose it. "
                    "Do not comment on grammar or length. Reply JSON with keys: verdict, right_first, missing, question.",
                },
                separators=(",", ":"),
            )
            safe, dropped = redact_payload({"q": user}, max_chars=7000)
            res = await self.llm.complete(
                system=system,
                user=safe["q"],
                purpose="ai2_teachback",
                role="ai2",
                student_id=student_id,
                temperature=0.2,
                max_output_tokens=420,
                response_spec={"verdict": "str", "right_first": "str", "missing": "str", "question": "str"},
            )
            await self._log_llm("ai2_teachback", "ai2", res, student_id, len(user), dropped)
            if res.ok and res.data:
                out["llm_comment"] = res.data
                out["llm_status"] = res.status
            else:
                out["llm_status"] = res.status
        return out

    def diagnostic_items(self, student_id: str, *, count: int = 12) -> list[dict[str, Any]]:
        """The initial assessment: broad coverage with delivery modes rotated."""
        with self.sf() as s:
            topics = s.execute(select(Topic).order_by(Topic.difficulty)).scalars().all()
            items_by_topic: dict[str, list[dict[str, Any]]] = {}
            for t in topics:
                rows = s.execute(
                    select(Item).where(Item.topic_id == t.id, Item.active.is_(True)).order_by(Item.difficulty)
                ).scalars().all()
                items_by_topic[t.id] = [
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
                        "est_seconds": it.est_seconds,
                        "numbas_ref": it.numbas_ref,
                        "active": True,
                    }
                    for it in rows
                ]
        picks = SEL.diagnostic_plan(
            topics=[{"id": t.id, "code": t.code, "title": t.title, "difficulty": t.difficulty} for t in topics],
            items_by_topic=items_by_topic,
            count=count,
            seed=student_id,
        )
        from ..assessment.bank import render_item_for

        out = []
        for i, pick in enumerate(picks):
            # Same seed as every other surface for this learner+item, so a reveal or a
            # follow-up hint can never disagree with the stem that was placed.
            rendered = render_item_for(pick.item, seed=bank_seed(student_id, pick.item.get("id")))
            out.append({**pick.as_dict(), "item": public_item(rendered), "position": i + 1, "of": len(picks)})
        return out

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
                            "est_seconds": it.est_seconds,
                            "numbas_ref": it.numbas_ref,
                            "active": True,
                            "tags": it.tags,
                        }
                        for it in rows
                    ]
        mastery = {t.topic_id: t.p_know for t in prof.traces}
        counts: dict[str, int] = {}
        for u in used:
            tid = u.split(":", 1)[0]
            counts[tid] = counts.get(tid, 0) + 1
        from ..assessment.bank import render_item_for

        picks = SEL.next_items(
            items_by_topic=items_by_topic,
            mastery=mastery,
            counts=counts,
            due_topics=[d["topic_id"] for d in (prof.retention or {}).get("due_now", [])],
            gap_topics=[g.get("topic_id") for g in prof.gaps if g.get("topic_id")],
            gap_severity={g.get("topic_id"): float(g.get("severity", 0.5)) for g in prof.gaps},
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
                "task": "Write 2-3 sentences of feedback: name the misconception, give the exact next step. "
                "No praise padding. Plain text.",
            },
            separators=(",", ":"),
        )
        safe, dropped = redact_payload({"q": user}, max_chars=4000)
        res = await self.llm.complete(
            system=AI2_SYSTEM,
            user=safe["q"],
            purpose="ai2_feedback",
            role="ai2",
            student_id=student_id,
            temperature=0.3,
            max_output_tokens=320,
        )
        await self._log_llm("ai2_feedback", "ai2", res, student_id, len(user), dropped)
        if res.ok and res.text.strip():
            return res.text.strip()[:1200]
        return base

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
            "task": (
                "Teach this. Use the source material as the ground truth: correct or extend it, do not "
                "contradict it or invent facts. Respect the presentation rules. Return JSON: answer, "
                "sections[{label,text}], next_action, adaptations_applied[], ask_teachback, resource_hint."
            ),
        }
        user = json.dumps(payload, separators=(",", ":"))[:9000]
        safe, dropped = redact_payload({"q": user}, max_chars=9000)
        res = await self.llm.complete(
            system=AI2_SYSTEM,
            user=safe["q"],
            purpose=purpose,
            role="ai2",
            student_id=student_id,
            temperature=0.45 if not want_spoken else 0.3,
            max_output_tokens=900 if not want_spoken else 420,
            response_spec=REPLY_SPEC,
            history=[(r, t) for r, t in history][-6:],
        )
        meta = {**res.as_dict(), "redacted_fields": (res.redacted_fields or 0) + dropped}
        await self._log_llm(purpose, "ai2", res, student_id, len(user), dropped)
        if not res.ok:
            meta["fell_back_to"] = "local tutor (extractive, grounded in course material)"
            return None, meta
        data = dict(res.data or {})
        if not data.get("answer") and res.text:
            data["answer"] = res.text
        if not data.get("answer"):
            meta["fell_back_to"] = "local tutor (model returned no text)"
            return None, meta
        data["answer"] = str(data["answer"])[:3500]
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
            return f"{best.get('title')} ({best.get('provider')}, {best.get('year') or 'n.d.'}) -- {best.get('url')}"
        except Exception as exc:  # resource lookup must never break an answer
            log.info("resource hint skipped: %s", exc.__class__.__name__)
            return None

    async def _llm_practice(self, *, student_id: str, topics: dict[str, Any], want: int, topic_id: str | None, note: str) -> list[dict[str, Any]]:
        titles = [t.title for t in topics.values()][:12]
        user = json.dumps(
            {
                "topic_titles": titles,
                "topic_id": topic_id,
                "count": want,
                "note": note[:200],
                "task": "Write new practice questions in the style of this bank. Numeric answers must be "
                "exactly computable from the stem. JSON with key items: [{topic_id, kind(numeric|mcq|text), "
                "stem, answer_value, choices?, correct_index?, solution_steps:[str], hints:[str]}].",
            },
            separators=(",", ":"),
        )[:4000]
        safe, dropped = redact_payload({"q": user}, max_chars=4000)
        res = await self.llm.complete(
            system=AI2_SYSTEM,
            user=safe["q"],
            purpose="ai2_generate_practice",
            role="ai2",
            student_id=student_id,
            temperature=0.7,
            max_output_tokens=1200,
            response_spec={"items": ("array", {"type": "OBJECT"})},
        )
        await self._log_llm("ai2_generate_practice", "ai2", res, student_id, len(user), dropped)
        out: list[dict[str, Any]] = []
        if not (res.ok and res.data and isinstance(res.data.get("items"), list)):
            return out
        import uuid

        for raw in res.data["items"][:want]:
            if not isinstance(raw, dict) or not raw.get("stem"):
                continue
            tid = raw.get("topic_id") if raw.get("topic_id") in topics else (topic_id or next(iter(topics), None))
            if tid is None:
                continue
            kind = str(raw.get("kind", "numeric"))
            item = {
                "id": f"gen_{uuid.uuid4().hex[:12]}",
                "topic_id": tid,
                "kind": kind if kind in ("numeric", "mcq", "text") else "text",
                "stem": str(raw["stem"])[:900],
                "experience_mode": "practice",
                "difficulty": 0.5,
                "source": "ai2",
                "active": True,
                "solution_steps": [{"text": str(x)[:400]} for x in (raw.get("solution_steps") or [])[:5]],
                "hints": [str(x)[:300] for x in (raw.get("hints") or [])[:3]],
                "answer_spec": {},
                "generated_by": res.model or "gemini",
                "checksum": checksum(raw),
            }
            if item["kind"] == "mcq" and isinstance(raw.get("choices"), list):
                item["choices"] = [{"text": str(c)[:200], "correct": i == int(raw.get("correct_index", 0))} for i, c in enumerate(raw["choices"][:6])]
                item["answer_spec"] = {"correct_index": int(raw.get("correct_index", 0))}
            else:
                item["answer_spec"] = {"value": raw.get("answer_value")}
            out.append({"item": item, "reason": "generated by AI 2 to fill the practice quota", "utility": 0.4, "presentation": {}, "decision_trace": {"llm": True, "store": True}})
        return out

    async def _log_llm(self, purpose: str, role: str, res: Any, student_id: str, prompt_chars: int, dropped: int) -> None:
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


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Strip answers/hints from an item before it reaches the browser."""
    return {
        "id": item.get("id"),
        "topic_id": item.get("topic_id"),
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
