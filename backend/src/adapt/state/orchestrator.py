"""The Orchestrator: one deterministic turn cycle, and a re-plan that never restarts a lesson.

Round brief §2A, steps 1–5:

1. **Ingest** events into the evidence log (idempotent).
2. **Model**: AI 1 (deterministic maths plus constrained model suggestions) produces a new
   ``model_version``.
3. **Content**: AI 2 ensures the next concept's pack exists — a cache hit by id, generating and
   validating only on a miss.
4. **Plan**: AI 3 builds the lesson plan from model version *N* and the pack ids.
5. **Deliver**: render, and record the delivery with its provenance.

Two properties matter more than the code here:

* **Determinism.** Every step is a call to a deterministic function of the store's contents plus the
  events handed in. Nothing in the cycle reads a clock, a random number or the network except where
  that is the point (a genuinely new content pack), and the model step is a pure fold over the log.
* **The plan is not the lesson.** Re-planning produces a *new plan* and supersedes the old one; the
  session state (which block the learner is on, what they have already seen) is untouched, so a
  re-plan mid-lesson changes what comes next and not what is on screen.

The Orchestrator imports the AIs. The AIs import the store. No AI imports another AI — that is the
direction the brief asks for, and ``tests/test_state_foundation.py`` asserts it by parsing the
modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .store import EvidenceEvent, IngestResult, LearnerState

log = logging.getLogger("adapt.orchestrator")


@dataclass
class Turn:
    """What one turn did, in order. Every field is something a console or a test can read."""

    learner_id: str
    session_id: str | None = None
    ingest: IngestResult | None = None
    model_version: int = 0
    model_advanced: bool = False
    plan_id: str | None = None
    re_planned: bool = False
    content_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "session_id": self.session_id,
            "ingest": self.ingest.as_dict() if self.ingest else None,
            "model_version": self.model_version,
            "model_advanced": self.model_advanced,
            "plan_id": self.plan_id,
            "re_planned": self.re_planned,
            "content_ids": list(self.content_ids),
            "notes": list(self.notes),
        }


class Orchestrator:
    """Runs the cycle. Holds the three intelligences as *collaborators*, never one inside another."""

    def __init__(
        self,
        *,
        state: LearnerState,
        ai1: Any = None,
        ai2: Any = None,
        ai3: Any = None,
        settings: Any = None,
    ) -> None:
        self.state = state
        self.ai1 = ai1
        self.ai2 = ai2
        self.ai3 = ai3
        self.settings = settings

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #

    def ingest(self, learner_id: str, events: Sequence[EvidenceEvent], *, session_id: str | None = None) -> IngestResult:
        """Step 1. The code layer writes the log; nothing else may."""
        out = self.state.ingest_many(learner_id, events)
        if out.accepted:
            log.debug("orchestrator: ingested %d event(s) for %s", out.accepted, learner_id)
        return out

    def advance_model(self, learner_id: str, *, reason: str = "ingest", expect_version: int | None = None) -> tuple[int, bool]:
        """Step 2. AI 1's turn: derive a new model version from the log.

        Returns ``(version, advanced)``. The maths lives in ``state.model.build_model`` — a pure
        fold — and this is where a constrained model suggestion may be layered on top of it by AI 1
        (``ai1`` when present); the fold is what guarantees a rebuild equals the incremental model.
        """
        before = self.state.latest_version(learner_id)
        version = self.state.advance_model(
            learner_id,
            module="ai1",
            reason=reason,
            titles=self.state.title_map(),
            expect_version=expect_version,
        )
        return version, version != before

    def ensure_content(
        self,
        concept: str,
        *,
        kind: str = "explanation",
        topic_id: str | None = None,
        generator: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """Step 3. AI 2's turn: the pack exists, or it is made and validated now.

        A cache hit is the normal path: the id is the hash of the content, so "have we already
        generated this?" is a primary-key lookup rather than a similarity judgement.
        """
        for row in self.state.content_for(concept, kind=kind):
            if row["validation"] != "rejected":
                return str(row["id"])
        if generator is None and self.ai2 is not None:
            generator = getattr(self.ai2, "content_pack", None)
        if generator is None:
            # No AI 2 and no generator: the authored bank is the content. Say so rather than
            # inventing a pack id that resolves to nothing.
            raise LookupError(f"no content pack for {concept!r} and no generator available")
        made = generator(concept=concept, kind=kind, topic_id=topic_id, context=dict(context or {}))
        payload = dict(made.get("payload") if isinstance(made, Mapping) else {})
        notes = list(made.get("notes") or []) if isinstance(made, Mapping) else []
        validator = getattr(self.ai2, "validate_pack", None)
        if validator is not None:
            verdict = validator(payload) or {}
            ok = bool(verdict.get("ok", True))
            notes.extend(list(verdict.get("notes") or []))
        else:
            ok = True
        cid = self.state.put_content(
            concept=concept,
            kind=kind,
            payload=payload,
            topic_id=topic_id,
            validation="valid" if ok else "rejected",
            validation_notes=notes,
            module="ai2",
        )
        return cid

    def plan(
        self,
        learner_id: str,
        *,
        session_id: str | None = None,
        blocks: Sequence[Mapping[str, Any]] = (),
        supersedes: str | None = None,
        model_version: int | None = None,
        content_ids: Sequence[str] = (),
    ) -> tuple[str, bool]:
        """Step 4. AI 3's turn: a plan built from model version *N* and these pack ids.

        ``re_planned`` is true when this supersedes an earlier plan in the same session — the
        mid-lesson case — and that is the whole difference between a plan and a re-plan: the learner
        keeps their place.
        """
        if model_version is None:
            model_version = self.state.latest_version(learner_id)
        plan = {
            "blocks": [dict(b) for b in blocks],
            "model_version": model_version,
            "content_ids": list(content_ids),
            "voice": (self.state.get_snapshot(learner_id, session_id=session_id).session.get("voice") or "one"),
        }
        pid = self.state.put_plan(
            learner_id,
            plan=plan,
            model_version=model_version,
            content_ids=list(content_ids),
            session_id=session_id,
            supersedes=supersedes,
            module="ai3",
        )
        if session_id:
            # The current-plan pointer moves; the block the learner is on does not. Written for
            # every plan, not only a replacement — the pointer is how the next turn knows whether
            # the plan still matches the model version, and a plan nothing points at is a plan the
            # app cannot re-plan from.
            self.state.advance_session(learner_id, session_id, plan_id=pid, module="code")
        return pid, bool(supersedes)

    def deliver(
        self,
        learner_id: str,
        *,
        block: str,
        plan_id: str | None = None,
        content_id: str | None = None,
        seen: Mapping[str, Any] | None = None,
        why: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        advance_to: str | None = None,
    ) -> str:
        """Step 5. Record what was rendered, with the references that explain it."""
        plan_version = 0
        if plan_id:
            row = self.state.get_plan(plan_id) or {}
            plan_version = int(row.get("model_version") or 0)
        why = dict(why or {})
        why.setdefault("model_version", plan_version)
        if content_id:
            why.setdefault("content_id", content_id)
        did = self.state.record_delivery(
            learner_id,
            block=block,
            seen=seen,
            why=why,
            plan_id=plan_id,
            content_id=content_id,
            session_id=session_id,
        )
        if advance_to and session_id:
            self.state.advance_session(learner_id, session_id, block=advance_to, plan_id=plan_id, module="code")
        return did

    # ------------------------------------------------------------------ #
    # the whole cycle
    # ------------------------------------------------------------------ #

    def cycle(
        self,
        learner_id: str,
        *,
        session_id: str | None = None,
        events: Sequence[EvidenceEvent] = (),
        blocks: Sequence[Mapping[str, Any]] = (),
        content_ids: Sequence[str] = (),
        force_replan: bool = False,
    ) -> Turn:
        """Steps 1–4 in order. Returns what happened; rendering and delivery are separate calls."""
        turn = Turn(learner_id=learner_id, session_id=session_id)
        if events:
            turn.ingest = self.ingest(learner_id, events, session_id=session_id)
        turn.model_version, turn.model_advanced = self.advance_model(learner_id)

        state = self.state.session_state(learner_id, session_id) if session_id else {}
        current = state.get("plan_id")
        plan_row = self.state.get_plan(str(current)) if current else None
        stale = bool(
            force_replan
            or (plan_row and int(plan_row.get("model_version") or 0) != turn.model_version)
        )
        if plan_row and not stale:
            turn.plan_id = str(current)
            turn.content_ids = list(plan_row.get("content_ids") or [])
            turn.notes.append("plan still matches the model version; nothing to re-plan")
            return turn

        turn.plan_id, turn.re_planned = self.plan(
            learner_id,
            session_id=session_id,
            blocks=blocks,
            supersedes=str(current) if current else None,
            model_version=turn.model_version,
            content_ids=content_ids,
        )
        turn.content_ids = list(content_ids)
        if turn.re_planned:
            turn.notes.append(
                "re-planned from the current model version without restarting the lesson: "
                "the learner keeps the block they are on and only the rest changes"
            )
        return turn

    def replan(
        self,
        learner_id: str,
        session_id: str,
        *,
        reason: str,
        events: Sequence[EvidenceEvent] = (),
        blocks: Sequence[Mapping[str, Any]] = (),
        content_ids: Sequence[str] = (),
    ) -> Turn:
        """The failure loop's entry point: new evidence lands and the rest of the lesson changes.

        The lesson is *not* restarted. The old plan stays in the store with ``superseded_by`` set,
        the learner's block and teaching memory are untouched, and the new plan carries the model
        version that justified it — so the "why did this lesson change?" panel has an exact answer.
        """
        turn = self.cycle(
            learner_id,
            session_id=session_id,
            events=events,
            blocks=blocks,
            content_ids=content_ids,
            force_replan=True,
        )
        turn.notes.append(f"re-plan reason: {reason}")
        return turn
