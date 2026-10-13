"""``LearnerState`` — the typed service layer over the one store.

Everything the three intelligences share goes through this object. It owns:

* the **append-only evidence log** (idempotent by event id, weighted by source);
* the **versioned derived model** (immutable rows, optimistic advance, rebuildable from the log);
* the **content registry** (addressed by content hash, validated by code, reused by id);
* **teaching memory** (what was already said, shown or asked);
* **lesson plans and deliveries** (with the model version and content ids they were built from);
* **session state** (topic, block, pace, fatigue, the plan in force).

Every write states which module is writing and is checked against the permission matrix. Every read
that a module builds from returns an immutable :class:`~adapt.state.snapshot.LearnerSnapshot`, so a
lesson is planned from one consistent view rather than from a moving set of rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..db.models import (
    ContentPack,
    DeliveryRecord,
    Item,
    LearningSession,
    LessonPlanRecord,
    ModelVersion,
    SessionState,
    StateEvidence,
    StudentPreference,
    TeachingMemoryEntry,
    Topic,
    utcnow,
)
from . import permissions as PERM
from .model import build_model
from .snapshot import LearnerSnapshot, snapshot_from_row, thaw

log = logging.getLogger("adapt.state")

#: The weights evidence carries, by source. ``controlled`` is an instrument the app ran (a graded
#: attempt, a baseline-game result); ``observational`` is noticed rather than measured (a pause, a
#: re-ask, a tab switch). The brief's numbers: 1.0 and ≈0.4.
WEIGHT_CONTROLLED = 1.0
WEIGHT_OBSERVATIONAL = 0.4


class ModelConflict(RuntimeError):
    """An optimistic write lost a race: someone else advanced the model first.

    Raised rather than swallowed. The caller re-reads and re-applies, because the alternative —
    writing version N+1 on top of a version the caller never saw — is how a learner's estimate
    silently loses an observation.
    """


@dataclass(frozen=True)
class EvidenceEvent:
    """One thing that happened, as it is offered to the log."""

    event_id: str
    kind: str
    source_type: str = "controlled"
    topic_id: str | None = None
    item_id: str | None = None
    correct: bool | None = None
    score: float | None = None
    session_id: str | None = None
    occurred_at: datetime | None = None
    payload: Mapping[str, Any] | None = None

    @property
    def weight(self) -> float:
        return WEIGHT_CONTROLLED if self.source_type == "controlled" else WEIGHT_OBSERVATIONAL


@dataclass(frozen=True)
class IngestResult:
    """What ingestion did: how many were new, and which were already there."""

    accepted: int
    duplicates: int
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "duplicates": self.duplicates, "evidence_ids": list(self.evidence_ids)}


class LearnerState:
    """The store, as a service.

    ``sf`` is a *plain callable* returning a Session (``Database.sessionmaker``), not the
    ``with``-style helper: the store opens and closes its own sessions so a caller cannot leave one
    open by forgetting to exit a block, and so a store method is safe to call from anywhere.
    """

    def __init__(self, sf: Callable[[], Any], *, settings: Any = None) -> None:
        if not callable(sf):  # pragma: no cover - a wrong factory fails loudly at construction
            raise TypeError("LearnerState needs a callable returning a Session (e.g. Database.sessionmaker)")
        self.sf = sf
        self.settings = settings

    # ------------------------------------------------------------------ #
    # evidence log
    # ------------------------------------------------------------------ #

    def ingest(
        self,
        learner_id: str,
        event: EvidenceEvent | Sequence[EvidenceEvent],
        *,
        session: Any = None,
        module: str = "code",
    ) -> IngestResult:
        """Append one event, or a batch. Idempotent: the same ``event_id`` is a no-op, not a second row.

        Accepts either shape because both are natural at call sites ("this attempt happened" /
        "here are the four events since the last turn") and guessing wrong at a call site is a
        runtime error for no reason.
        """
        events = [event] if isinstance(event, EvidenceEvent) else list(event)
        return self.ingest_many(learner_id, events, session=session, module=module)

    def ingest_many(
        self,
        learner_id: str,
        events: Sequence[EvidenceEvent],
        *,
        session: Any = None,
        module: str = "code",
    ) -> IngestResult:
        PERM.check_write(module, "evidence")
        own = session is None
        s = session or self.sf()
        try:
            known = {
                eid
                for (eid,) in s.execute(
                    select(StateEvidence.event_id).where(StateEvidence.student_id == learner_id)
                ).all()
            }
            accepted: list[str] = []
            duplicates = 0
            for ev in events:
                if ev.event_id in known:
                    duplicates += 1
                    continue
                s.add(
                    StateEvidence(
                        student_id=learner_id,
                        session_id=ev.session_id,
                        event_id=ev.event_id,
                        kind=ev.kind,
                        source_type=ev.source_type,
                        weight=ev.weight,
                        topic_id=ev.topic_id,
                        item_id=ev.item_id,
                        correct=ev.correct,
                        score=ev.score,
                        payload=dict(ev.payload or {}),
                        occurred_at=ev.occurred_at or utcnow(),
                    )
                )
                known.add(ev.event_id)
                accepted.append(ev.event_id)
            if own:
                s.commit()
            else:
                s.flush()
            return IngestResult(accepted=len(accepted), duplicates=duplicates, evidence_ids=tuple(accepted))
        except IntegrityError:
            # Two writers raced on the same event id. The unique constraint did its job: the event
            # is in the log once, which is the guarantee, and the loser reports it as a duplicate.
            if own:
                s.rollback()
            return IngestResult(accepted=0, duplicates=len(events))
        finally:
            if own:
                s.close()

    def evidence_rows(self, learner_id: str, *, session: Any = None, limit: int | None = None) -> list[dict[str, Any]]:
        """The log as plain dicts, in fold order (oldest first)."""
        own = session is None
        s = session or self.sf()
        try:
            stmt = select(StateEvidence).where(StateEvidence.student_id == learner_id).order_by(StateEvidence.occurred_at, StateEvidence.id)
            rows = list(s.execute(stmt).scalars().all())
            if limit:
                rows = rows[-limit:]
            return [
                {
                    "id": r.id,
                    "event_id": r.event_id,
                    "kind": r.kind,
                    "source_type": r.source_type,
                    "weight": float(r.weight),
                    "topic_id": r.topic_id,
                    "item_id": r.item_id,
                    "correct": r.correct,
                    "score": r.score,
                    "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                    "payload": dict(r.payload or {}),
                }
                for r in rows
            ]
        finally:
            if own:
                s.close()

    def evidence_count(self, learner_id: str, *, session: Any = None) -> int:
        own = session is None
        s = session or self.sf()
        try:
            return int(
                s.execute(
                    select(func.count()).select_from(StateEvidence).where(StateEvidence.student_id == learner_id)
                ).scalar_one()
            )
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # the derived model
    # ------------------------------------------------------------------ #

    def latest_version_row(self, learner_id: str, *, session: Any = None) -> ModelVersion | None:
        own = session is None
        s = session or self.sf()
        try:
            return (
                s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.student_id == learner_id)
                    .order_by(ModelVersion.version.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        finally:
            if own:
                s.close()

    def latest_version(self, learner_id: str, *, session: Any = None) -> int:
        row = self.latest_version_row(learner_id, session=session)
        return int(row.version) if row else 0

    def advance_model(
        self,
        learner_id: str,
        *,
        module: str = "ai1",
        reason: str = "ingest",
        titles: Mapping[str, str] | None = None,
        expect_version: int | None = None,
        session: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> int:
        """Fold the log into a new model version. Returns the new version number.

        The model is a pure function of the evidence log, so this is safe to call whenever new
        evidence lands and safe to call again when it has not: an unchanged log produces an
        identical payload, and an identical payload does not get a new version (see below).
        """
        PERM.check_write(module, "learner_model")
        own = session is None
        s = session or self.sf()
        try:
            current = self.latest_version_row(learner_id, session=s)
            if expect_version is not None and int(getattr(current, "version", 0) or 0) != int(expect_version):
                raise ModelConflict(
                    f"model moved: expected version {expect_version}, found {int(getattr(current, 'version', 0) or 0)}"
                )
            rows = self.evidence_rows(learner_id, session=s)
            if not rows and current is None:
                # Nothing measured yet: version 0 *is* the cold start, and minting an empty
                # version 1 would make "version 1" mean nothing in particular — the first real
                # version should be the first one with evidence behind it.
                return 0
            previous = dict(getattr(current, "model", None) or {})
            model = build_model(rows, titles=titles, previous=previous)
            if extra:
                model.update(dict(extra))
            if current is not None and thaw(current.model) == model:
                # Same evidence, same model: nothing to version. "Every change produces a new
                # model_version" is a promise about *changes*, and minting versions for no-ops
                # would make the version number useless for spotting a real one.
                return int(current.version)
            version = int(getattr(current, "version", 0) or 0) + 1
            s.add(
                ModelVersion(
                    student_id=learner_id,
                    version=version,
                    evidence_upto=(rows[-1]["id"] if rows else None),
                    evidence_count=len(rows),
                    model=model,
                    reason=reason,
                    parent_version=int(current.version) if current is not None else None,
                    created_at=utcnow(),
                )
            )
            if own:
                s.commit()
            else:
                s.flush()
            return version
        except IntegrityError:
            if own:
                s.rollback()
            raise ModelConflict("another writer advanced the model at the same time") from None
        finally:
            if own:
                s.close()

    def rebuild_model(self, learner_id: str, *, session: Any = None, titles: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Recompute the model from the whole evidence log and prove it equals the incremental one.

        Returns the new version, the version it was checked against, and whether the payloads are
        identical. ``adapt rebuild-model`` prints this; the test suite asserts it.
        """
        own = session is None
        s = session or self.sf()
        try:
            before = self.latest_version_row(learner_id, session=s)
            before_payload = thaw(before.model) if before is not None else None
            rows = self.evidence_rows(learner_id, session=s)
            if before is None and not rows:
                return {
                    "learner_id": learner_id,
                    "version": 0,
                    "previous_version": 0,
                    "equal": True,
                    "evidence_count": 0,
                    "note": "no evidence yet: version 0 is the cold start, and a rebuild of nothing is nothing",
                }
            # ``previous`` carries a topic's title forward when the bank no longer lists it, so a
            # curriculum edit cannot make a rebuild disagree with the incremental model about a
            # *title* — the one field in the model that does not come from the learner's evidence.
            built = build_model(rows, titles=titles, previous=before_payload)
            if before_payload is not None:
                built = {**built, **{k: v for k, v in before_payload.items() if k not in built}}
            if before_payload == built:
                return {
                    "learner_id": learner_id,
                    "version": int(getattr(before, "version", 0) or 0),
                    "previous_version": int(getattr(before, "version", 0) or 0),
                    "equal": True,
                    "evidence_count": len(rows),
                    "note": "the incremental model already equals a full rebuild of the log",
                }
            version = int(getattr(before, "version", 0) or 0) + 1
            s.add(
                ModelVersion(
                    student_id=learner_id,
                    version=version,
                    evidence_upto=(rows[-1]["id"] if rows else None),
                    evidence_count=len(rows),
                    model=built,
                    reason="rebuild",
                    parent_version=int(getattr(before, "version", 0) or 0) or None,
                    created_at=utcnow(),
                )
            )
            if own:
                s.commit()
            else:
                s.flush()
            return {
                "learner_id": learner_id,
                "version": version,
                "previous_version": int(getattr(before, "version", 0) or 0),
                "equal": False,
                "evidence_count": len(rows),
                "note": "the rebuild differed from the incremental model; the rebuilt version is now current",
            }
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # content registry
    # ------------------------------------------------------------------ #

    @staticmethod
    @staticmethod
    def content_id(payload: Any) -> str:
        """The id of a content pack *is* the hash of its content."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return "cpk_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]

    def put_content(
        self,
        *,
        concept: str,
        kind: str,
        payload: Mapping[str, Any],
        topic_id: str | None = None,
        validation: str = "unvalidated",
        validation_notes: Sequence[str] = (),
        version: int = 1,
        session: Any = None,
        module: str = "ai2",
    ) -> str:
        """Register a pack. Same content → same id → no second row.

        ``module`` is AI 2's by default and *cannot* be ``code``: authoring content is the AI's job,
        and letting the deterministic layer seed the registry with content would make "generated and
        validated" and "hard-coded" indistinguishable in the same table.
        """
        if module == "code":
            raise PERM.WriteNotPermitted(module, "content (authoring)")
        PERM.check_write(module, "content")
        body = dict(payload)
        cid = self.content_id({"concept": concept, "kind": kind, "topic_id": topic_id, "payload": body})
        own = session is None
        s = session or self.sf()
        try:
            if s.get(ContentPack, cid) is None:
                s.add(
                    ContentPack(
                        id=cid,
                        topic_id=topic_id,
                        concept=concept,
                        kind=kind,
                        version=version,
                        payload=body,
                        validation=validation,
                        validation_notes=list(validation_notes),
                        source=module,
                        created_at=utcnow(),
                    )
                )
                if own:
                    s.commit()
                else:
                    s.flush()
            return cid
        except IntegrityError:
            if own:
                s.rollback()
            return cid
        finally:
            if own:
                s.close()

    def get_content(self, content_id: str, *, session: Any = None) -> dict[str, Any] | None:
        own = session is None
        s = session or self.sf()
        try:
            row = s.get(ContentPack, content_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "topic_id": row.topic_id,
                "concept": row.concept,
                "kind": row.kind,
                "version": row.version,
                "payload": dict(row.payload or {}),
                "validation": row.validation,
                "validation_notes": list(row.validation_notes or []),
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        finally:
            if own:
                s.close()

    def set_validation(self, content_id: str, status: str, *, notes: Sequence[str] = (), session: Any = None, module: str = "code") -> bool:
        """The checker's verdict on a pack.

        Only the code layer may set it: a content author certifying its own output is precisely the
        failure mode the matrix exists to stop. ``content`` is in code's row solely for this call.
        """
        if module != "code":
            raise PERM.WriteNotPermitted(module, "content (validation)")
        PERM.check_write(module, "content")
        own = session is None
        s = session or self.sf()
        try:
            row = s.get(ContentPack, content_id)
            if row is None:
                return False
            row.validation = status
            row.validation_notes = list(notes)
            if own:
                s.commit()
            else:
                s.flush()
            return True
        finally:
            if own:
                s.close()

    def content_for(
        self,
        concept: str,
        *,
        kind: str | None = None,
        include_rejected: bool = False,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        """Packs for a concept, newest first. Rejected packs are hidden unless asked for.

        Hidden by default because this is the *delivery* lookup: a pack the validator threw out is
        not content, and a caller that has to remember to filter it will eventually forget. Keeping
        the row addressable (rather than deleting it) is what makes "we tried this and rejected it"
        answerable later, which is why ``include_rejected`` exists at all.
        """
        own = session is None
        s = session or self.sf()
        try:
            stmt = select(ContentPack).where(ContentPack.concept == concept)
            if kind:
                stmt = stmt.where(ContentPack.kind == kind)
            if not include_rejected:
                stmt = stmt.where(ContentPack.validation != "rejected")
            return [
                {
                    "id": r.id,
                    "concept": r.concept,
                    "kind": r.kind,
                    "validation": r.validation,
                    "topic_id": r.topic_id,
                }
                for r in s.execute(stmt.order_by(ContentPack.created_at.desc())).scalars().all()
            ]
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # teaching memory
    # ------------------------------------------------------------------ #

    def remember(
        self,
        learner_id: str,
        *,
        kind: str,
        ref: str,
        text: str | None = None,
        session_id: str | None = None,
        session: Any = None,
        module: str = "ai3",
    ) -> bool:
        """Record that something was explained, shown, asked or misread. Idempotent."""
        PERM.check_write(module, "teaching_memory")
        own = session is None
        s = session or self.sf()
        try:
            existing = s.execute(
                select(TeachingMemoryEntry).where(
                    TeachingMemoryEntry.student_id == learner_id,
                    TeachingMemoryEntry.session_id == session_id,
                    TeachingMemoryEntry.kind == kind,
                    TeachingMemoryEntry.ref == ref,
                )
            ).scalars().first()
            if existing is not None:
                return False
            s.add(
                TeachingMemoryEntry(
                    student_id=learner_id,
                    session_id=session_id,
                    kind=kind,
                    ref=ref,
                    text=text,
                    created_at=utcnow(),
                )
            )
            if own:
                s.commit()
            else:
                s.flush()
            return True
        except IntegrityError:
            if own:
                s.rollback()
            return False
        finally:
            if own:
                s.close()

    def memory(self, learner_id: str, *, session_id: str | None = None, session: Any = None) -> dict[str, list[str]]:
        """Everything remembered for this learner (or this session), by kind."""
        own = session is None
        s = session or self.sf()
        try:
            stmt = select(TeachingMemoryEntry).where(TeachingMemoryEntry.student_id == learner_id)
            if session_id:
                stmt = stmt.where(TeachingMemoryEntry.session_id == session_id)
            out: dict[str, list[str]] = {}
            for row in s.execute(stmt.order_by(TeachingMemoryEntry.created_at)).scalars().all():
                out.setdefault(row.kind, []).append(row.ref)
            return out
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # plans, deliveries, session state
    # ------------------------------------------------------------------ #

    def put_plan(
        self,
        learner_id: str,
        *,
        plan: Mapping[str, Any],
        model_version: int,
        content_ids: Sequence[str] = (),
        session_id: str | None = None,
        supersedes: str | None = None,
        session: Any = None,
        module: str = "ai3",
    ) -> str:
        """Store a lesson plan with the model version and content ids it was built from."""
        PERM.check_write(module, "plans")
        from ..db.models import new_id

        own = session is None
        s = session or self.sf()
        try:
            pid = new_id("pln")
            s.add(
                LessonPlanRecord(
                    id=pid,
                    student_id=learner_id,
                    session_id=session_id,
                    model_version=int(model_version),
                    content_ids=list(content_ids),
                    plan=dict(plan),
                    created_at=utcnow(),
                )
            )
            if supersedes:
                old = s.get(LessonPlanRecord, supersedes)
                if old is not None:
                    old.superseded_by = pid
            if own:
                s.commit()
            else:
                s.flush()
            return pid
        finally:
            if own:
                s.close()

    def get_plan(self, plan_id: str, *, session: Any = None) -> dict[str, Any] | None:
        own = session is None
        s = session or self.sf()
        try:
            row = s.get(LessonPlanRecord, plan_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "student_id": row.student_id,
                "session_id": row.session_id,
                "model_version": row.model_version,
                "content_ids": list(row.content_ids or []),
                "plan": dict(row.plan or {}),
                "superseded_by": row.superseded_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        finally:
            if own:
                s.close()

    def record_delivery(
        self,
        learner_id: str,
        *,
        block: str,
        seen: Mapping[str, Any] | None = None,
        why: Mapping[str, Any] | None = None,
        plan_id: str | None = None,
        content_id: str | None = None,
        session_id: str | None = None,
        session: Any = None,
        module: str = "ai3",
    ) -> str:
        """Record what the learner was actually shown, and the references behind it."""
        PERM.check_write(module, "deliveries")
        from ..db.models import new_id

        own = session is None
        s = session or self.sf()
        try:
            did = new_id("dlv")
            s.add(
                DeliveryRecord(
                    id=did,
                    plan_id=plan_id,
                    student_id=learner_id,
                    session_id=session_id,
                    block=block,
                    content_id=content_id,
                    seen=dict(seen or {}),
                    why=dict(why or {}),
                    created_at=utcnow(),
                )
            )
            if own:
                s.commit()
            else:
                s.flush()
            return did
        finally:
            if own:
                s.close()

    def session_state(self, learner_id: str, session_id: str, *, session: Any = None) -> dict[str, Any]:
        """Where this learner is in this session. Created empty when it does not exist yet."""
        own = session is None
        s = session or self.sf()
        try:
            row = s.get(SessionState, session_id)
            if row is None:
                row = SessionState(session_id=session_id, student_id=learner_id, version=1)
                s.add(row)
                if own:
                    s.commit()
                else:
                    s.flush()
            return {
                "session_id": row.session_id,
                "student_id": row.student_id,
                "topic_id": row.topic_id,
                "block": row.block,
                "plan_id": row.plan_id,
                "pace": float(row.pace or 1.0),
                "fatigue": float(row.fatigue or 0.0),
                "version": int(row.version or 1),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        finally:
            if own:
                s.close()

    def advance_session(
        self,
        learner_id: str,
        session_id: str,
        *,
        block: str | None = None,
        topic_id: str | None = None,
        plan_id: str | None = None,
        pace: float | None = None,
        fatigue: float | None = None,
        expect_version: int | None = None,
        session: Any = None,
        module: str = "code",
    ) -> dict[str, Any]:
        """Move the session on. Optimistic: a stale ``expect_version`` is refused, not overwritten."""
        PERM.check_write(module, "session_state")
        own = session is None
        s = session or self.sf()
        try:
            state = self.session_state(learner_id, session_id, session=s)
            if expect_version is not None and int(state["version"]) != int(expect_version):
                raise ModelConflict(
                    f"session state moved: expected version {expect_version}, found {state['version']}"
                )
            row = s.get(SessionState, session_id)
            if topic_id is not None:
                row.topic_id = topic_id
            if block is not None:
                row.block = block
            if plan_id is not None:
                row.plan_id = plan_id
            if pace is not None:
                row.pace = float(pace)
            if fatigue is not None:
                row.fatigue = float(fatigue)
            row.version = int(row.version or 1) + 1
            row.updated_at = utcnow()
            if own:
                s.commit()
            else:
                s.flush()
            return self.session_state(learner_id, session_id, session=s)
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # stated preferences (the learner's own words, not a measurement)
    # ------------------------------------------------------------------ #
    def set_preference(
        self,
        learner_id: str,
        key: str,
        value: Any,
        *,
        source: str = "baseline",
        note: str | None = None,
        session: Any = None,
        module: str = "code",
    ) -> dict[str, Any]:
        """Store one *stated* preference.

        Only the code layer may write this row, and it writes what the learner said rather than what
        a module inferred — ``source`` records which screen asked, so the settings page can show
        where a value came from. The value is stored as JSON exactly as given; interpreting it is
        the reader's job, which keeps one opinion about what "timers help" means instead of two.
        """
        PERM.check_write(module, "preferences")
        own = session is None
        s = session or self.sf()
        try:
            row = s.get(StudentPreference, (learner_id, key))
            if row is None:
                row = StudentPreference(student_id=learner_id, key=key, value=value, source=source, note=note)
                s.add(row)
            else:
                row.value = value
                row.source = source
                row.note = note
                row.updated_at = utcnow()
            if own:
                s.commit()
            else:
                s.flush()
            return {"key": key, "value": value, "source": source}
        finally:
            if own:
                s.close()

    def preferences(self, learner_id: str, *, session: Any = None) -> dict[str, Any]:
        """Everything the learner has told us, as ``{key: value}``."""
        own = session is None
        s = session or self.sf()
        try:
            rows = s.execute(
                select(StudentPreference).where(StudentPreference.student_id == learner_id)
            ).scalars().all()
            return {r.key: r.value for r in rows}
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #

    def get_snapshot(self, learner_id: str, *, session_id: str | None = None, session: Any = None) -> LearnerSnapshot:
        """The learner's state at one model version, immutable.

        With no model version yet (a brand-new learner), the snapshot is version 0 with an empty
        model: nothing measured, no claim made. That is the cold start, and it is not an error.
        """
        own = session is None
        s = session or self.sf()
        try:
            version_row = self.latest_version_row(learner_id, session=s)
            prefs = {
                row.key: row.value
                for row in s.execute(select(StudentPreference).where(StudentPreference.student_id == learner_id)).scalars().all()
            }
            # Two reads on purpose: the learner's whole memory (what must not be repeated, ever)
            # and this session's slice (what has happened since the lesson started).
            memory = self.memory(learner_id, session=s)
            session_memory = self.memory(learner_id, session_id=session_id, session=s) if session_id else {}
            session_state = (
                self.session_state(learner_id, session_id, session=s) if session_id else {}
            )
            return snapshot_from_row(
                learner_id=learner_id,
                version_row=version_row,
                preferences=prefs,
                memory=memory,
                session=session_state,
                session_memory=session_memory,
            )
        finally:
            if own:
                s.close()

    def snapshot_with_model(
        self,
        learner_id: str,
        *,
        session_id: str | None = None,
        session: Any = None,
    ) -> tuple[LearnerSnapshot, dict[str, Any]]:
        """The snapshot, and its model payload as a plain dict (for modules that need both)."""
        snap = self.get_snapshot(learner_id, session_id=session_id, session=session)
        return snap, thaw(snap.model)

    # ------------------------------------------------------------------ #
    # helpers used by the serving paths
    # ------------------------------------------------------------------ #

    def title_map(self, *, session: Any = None) -> dict[str, str]:
        own = session is None
        s = session or self.sf()
        try:
            return {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
        finally:
            if own:
                s.close()

    def open_session_ids(self, learner_id: str, *, session: Any = None, limit: int = 5) -> list[str]:
        own = session is None
        s = session or self.sf()
        try:
            rows = s.execute(
                select(LearningSession.id)
                .where(LearningSession.student_id == learner_id)
                .order_by(LearningSession.started_at.desc())
                .limit(limit)
            ).all()
            return [r[0] for r in rows]
        finally:
            if own:
                s.close()

    def item_kind_map(self, item_ids: Iterable[str], *, session: Any = None) -> dict[str, dict[str, Any]]:
        """Format facts for evidence rows: the guess rate depends on the item, not the learner."""
        ids = [i for i in item_ids if i]
        if not ids:
            return {}
        own = session is None
        s = session or self.sf()
        try:
            out: dict[str, dict[str, Any]] = {}
            for row in s.execute(select(Item).where(Item.id.in_(ids))).scalars().all():
                choices = row.choices or []
                out[row.id] = {"kind": row.kind, "choices": len(choices) if choices else None}
            return out
        finally:
            if own:
                s.close()
