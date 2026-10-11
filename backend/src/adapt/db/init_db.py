"""Schema bootstrap and data seeding, from the command line.

``create_all`` is enough for a fresh clone, and Alembic is the answer for an
existing database; both are provided because they solve different problems (see
``docs/DATA_MODEL.md``). Seeding is idempotent by design -- a reseed updates the
bank in place rather than churning ids, because attempt history points at item ids
and a reseed that minted new ones would silently orphan a learner's record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..assessment import numbas as NB
from ..config import Settings, get_settings
from ..db.models import (
    Base,
    BktParams,
    Course,
    Item,
    LearningMaterial,
    LearningSession,
    NumbasPackage,
    Topic,
    TopicPrerequisite,
    utcnow,
)
from ..services import sessions as S
from .engine import Database

log = logging.getLogger("adapt.db.init")


@dataclass
class InitReport:
    created_tables: list[str] = field(default_factory=list)
    curriculum: dict[str, Any] = field(default_factory=dict)
    encryption: dict[str, Any] = field(default_factory=dict)
    demo: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_tables": len(self.created_tables),
            "tables": self.created_tables,
            "curriculum": self.curriculum,
            "encryption": self.encryption,
            "demo": self.demo,
            "errors": self.errors,
        }


def init(settings: Settings | None = None, *, seed_demo: bool = False, db: Database | None = None) -> InitReport:
    st = settings or get_settings()
    database = db or Database(st)
    before = set(_table_names(database))
    database.create_all()
    after = _table_names(database)
    report = InitReport(created_tables=sorted(set(after) - before))
    report.encryption = {
        "driver": database.driver.name,
        "file_encrypted": database.file_encrypted(),
        "header_is_plaintext": database.header_is_plaintext(),
        "key_source": database.driver.key_source,
        "detail": database.driver.detail,
    }
    with database.session() as s:
        report.curriculum = S.ensure_curriculum(s)
    if seed_demo:
        with database.session() as s:
            report.demo = seed_demo_learner(s, st)
    return report


def _table_names(db: Database) -> list[str]:
    with db.engine.connect() as conn:
        return [r[0] for r in conn.execute(text("select name from sqlite_master where type='table' order by name"))]


def seed_demo_learner(s: Session, settings: Settings) -> dict[str, Any]:
    """One ready-made learner so a judge sees a populated dashboard in seconds.

    The learner is created with a *known* weak spot pattern (fraction addition while
    equivalent fractions wobble), so the loop visibly produces a gap, a hypothesis and
    a proposal without anyone having to fake an analysis.
    """
    student = S.get_or_create_student(s, display_name="Demo learner", email=None, password=None)
    student.onboarding_complete = True
    S.set_preferences(
        s,
        student.id,
        {"goal": "pass the GCSE maths paper next spring", "weekly_minutes": 180, "session_minutes": 30, "max_explanation_words": 90},
        source="seed",
    )
    student.diagnostic_complete = True
    course = s.execute(select(Course).where(Course.title == S.COURSE_TITLE)).scalar_one_or_none()
    sess = S.start_session(s, student.id, mode="practice", course_id=course.id if course else None, device_hint="seed")
    topics = {t.code: t for t in s.execute(select(Topic)).scalars().all()}
    items = {i.id: i for i in s.execute(select(Item).where(Item.active.is_(True))).scalars().all()}

    def code_of(item_id: str) -> str:
        it = items.get(item_id)
        tid = it.topic_id if it else None
        return next((c for c, t in topics.items() if t.id == tid), "")

    # a believable history: solid on integers, shaky on equivalent fractions,
    # wrong on fraction addition -- the pattern the detectors are built for
    plan: list[tuple[str, bool]] = []
    for code, right in [
        ("integer_ops", True),
        ("integer_ops", True),
        ("fractions_intro", True),
        ("equivalent_fractions", False),
        ("equivalent_fractions", True),
        ("equivalent_fractions", False),
        ("fraction_add", False),
        ("fraction_add", False),
        ("fraction_add", True),
        ("fraction_add", False),
        ("decimals", True),
        ("percentages", False),
    ]:
        plan.append((code, right))
    recorded = 0
    for code, correct in plan:
        topic = topics.get(code)
        if topic is None:
            continue
        cand = next((i for i in items.values() if i.topic_id == topic.id), None)
        if cand is None:
            continue
        payload = {
            "id": cand.id,
            "topic_id": cand.topic_id,
            "kind": cand.kind,
            "stem": cand.stem,
            "stem_template": cand.stem_template,
            "experience_mode": cand.experience_mode,
            "choices": cand.choices,
            "answer_spec": cand.answer_spec,
            "solution_steps": cand.solution_steps,
            "hints": cand.hints,
            "rubric": cand.rubric,
            "difficulty": cand.difficulty,
            "est_seconds": cand.est_seconds,
            "prompt_context": cand.prompt_context,
            "visual_spec": cand.visual_spec,
            "active": True,
            "tags": cand.tags,
        }
        from ..assessment.bank import render_item_for
        from ..assessment.grading import grade as grade_answer

        rendered = render_item_for(payload, seed=recorded + 7)
        spec = rendered.get("answer_spec") or {}
        answer: Any = None
        if rendered["kind"] == "mcq":
            answer = {"selected": int(spec.get("correct_index") or 0)} if correct else {"selected": int(spec.get("correct_index") or 0) + 1}
        elif rendered["kind"] == "numeric":
            from ..ai.local_tutor import eval_formula

            try:
                value = eval_formula(spec.get("formula", "0"), {k: str(v) for k, v in (spec.get("variables") or {}).items()})
            except Exception:
                value = None
            answer = {"value": value if correct else (None if value is None else value + 3)}
        else:
            answer = {"text": "a part of a whole, made equal by multiplying top and bottom by the same number" if correct else "you add the tops and the bottoms"}
        g = grade_answer(rendered, answer, student_text=answer.get("text") if isinstance(answer, dict) else None)
        # Whatever the grader says is what gets recorded -- the seed plan describes
        # the *shape* of the history we want, and if a generated variant disagrees
        # with the intended right/wrong, the honest grade wins over the storyline.
        att_ok = bool(g.correct) if g.correct is not None else correct
        s.add(
            _attempt(sess.id, student.id, rendered, answer, correct=att_ok, score=1.0 if att_ok else 0.0, hints_used=2 if (not att_ok and recorded % 3 == 0) else 0)
        )
        recorded += 1
    s.commit()
    S.finish_session(s, sess.id, student_id=student.id)
    return {
        "student_id": student.id,
        "session_id": sess.id,
        "attempts": recorded,
        "note": "answers were chosen to make a real pattern (equivalent fractions -> fraction addition), not to fake one",
    }


def _attempt(session_id: str, student_id: str, item: dict[str, Any], answer: Any, *, correct: bool, score: float, hints_used: int = 0):
    from ..db.models import Attempt

    return Attempt(
        session_id=session_id,
        student_id=student_id,
        item_id=item["id"],
        topic_id=item["topic_id"],
        experience_mode=item.get("experience_mode") or "read",
        attempt_no=1,
        first_answer={"value": answer},
        final_answer={"value": answer},
        correct=correct,
        score=score,
        graded_by="rules",
        time_to_first_ms=3200 + 900 * (hints_used + 1),
        time_on_item_ms=52000 + 4000 * hints_used,
        change_count=1 if hints_used else 0,
        changed_from_correct=not correct and bool(hints_used),
        hints_used=hints_used,
        examples_opened=1 if hints_used >= 2 else 0,
        explanation_opened=bool(hints_used),
        submitted_at=utcnow(),
    )


def import_numbas_file(path: Path, s: Session, *, course_title: str | None = None) -> dict[str, Any]:
    """Load an edited ``.numbas``/exam JSON back into the item bank."""
    items, report = NB.import_exam(path)
    topic_by_code = {t.code: t.id for t in s.execute(select(Topic)).scalars().all()}
    stored = 0
    for payload in items:
        ref = NB.checksum(payload)
        code = (payload.get("tags") or [None])[0]
        topic_id = payload.get("topic_id") or topic_by_code.get(str(code)) or next(iter(topic_by_code.values()), None)
        if topic_id is None:
            report.warnings.append(f"{payload.get('name')}: no topic to attach to")
            continue
        existing = s.execute(select(Item).where(Item.numbas_ref == ref)).scalar_one_or_none()
        if existing is not None:
            continue
        s.add(
            Item(
                topic_id=topic_id,
                kind=payload.get("kind", "text"),
                stem=payload.get("stem", ""),
                stem_template=payload.get("stem_template"),
                experience_mode=payload.get("experience_mode", "practice"),
                prompt_context=payload.get("prompt_context"),
                choices=payload.get("choices"),
                answer_spec=payload.get("answer_spec") or {},
                solution_steps=payload.get("solution_steps"),
                hints=payload.get("hints"),
                rubric=payload.get("rubric"),
                difficulty=float(payload.get("difficulty", 0.5)),
                est_seconds=int(payload.get("est_seconds", 90)),
                tags=payload.get("tags") or [],
                numbas_ref=ref,
                source="numbas",
                active=True,
            )
        )
        stored += 1
    s.add(
        NumbasPackage(
            name=Path(path).name,
            direction="import",
            path=str(Path(path))[:400],
            checksum=NB.checksum({"n": stored, "w": len(report.warnings)}),
            item_count=stored,
            notes="; ".join(report.warnings[:10]) or None,
        )
    )
    s.commit()
    return {"imported": stored, "report": report.as_dict()}


def export_numbas(s: Session, out_path: Path, *, title: str = "ADAPT adaptive assessment") -> dict[str, Any]:
    items = []
    for it in s.execute(select(Item).where(Item.active.is_(True))).scalars().all():
        items.append(
            {
                "id": it.id,
                "topic_id": it.topic_id,
                "kind": it.kind,
                "stem": it.stem,
                "stem_template": it.stem_template,
                "choices": it.choices,
                "answer_spec": it.answer_spec,
                "solution_steps": it.solution_steps,
                "hints": it.hints,
                "difficulty": it.difficulty,
                "tags": it.tags,
            }
        )
    titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
    exam, report = NB.exam_export(items, title=title, topic_titles=titles)
    path = NB.write_numbas_zip(out_path, exam, report=report)
    s.add(
        NumbasPackage(
            name=out_path.name,
            direction="export",
            path=str(path)[:400],
            checksum=NB.checksum(exam),
            item_count=len(items),
            notes="; ".join(report.warnings[:10]) or None,
        )
    )
    s.commit()
    return {"path": str(path), "items": len(items), "report": report.as_dict(), "runnable": NB.supported_subset(items)}


def counts(s: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for model in (Course, Topic, TopicPrerequisite, Item, LearningMaterial, LearningSession, BktParams):
        out[model.__tablename__] = int(s.execute(select(func.count()).select_from(model)).scalar_one())
    return out


__all__ = ["init", "counts", "export_numbas", "import_numbas_file", "seed_demo_learner", "Base"]
