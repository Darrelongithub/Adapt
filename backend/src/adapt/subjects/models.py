"""The subject round's own tables.

Defined here rather than in ``db/models.py`` because that file is frozen for this round (six agents
adding columns to one module is six merge conflicts and one lost migration). The migration in
``backend/migrations/versions/P2_subject_round.py`` creates these four tables, existence-aware, on
top of revision ``0008``.

**One consequence the integrator has to know:** ``Base.metadata.create_all`` only creates tables from
modules that have been imported. ``db/init_db.py`` must import ``adapt.subjects.models`` (see
``docs/AUDIT.md``), or a database built by ``adapt init`` will not have them and the
first subject round will fail on a missing table. The API route module imports this module too, so
once the router is registered the import happens on app start as well — but the CLI path goes through
``init_db`` alone.

What each table is for:

* ``subject_rounds`` — one sitting of the subject round for one topic: the resolved topic, the
  ratings (with their taxonomy version), the seed everything else derives from, and how far it got.
  Resumable, like the baseline.
* ``subject_trials`` — one answer, with the task as served, the response and the score. Unique on
  ``(run_id, task_id, index)``, which is the idempotency rule the evidence log uses: a flaky network
  must not turn one tap into two measurements.
* ``method_test_runs`` — the pop-up lesson's assignment and results, kept in its own row so the
  next-day recheck can be found without replaying a round.
* ``subject_snapshots`` — the subject map: what was measured, what was not, and the ladder positions.
  Immutable once written, because a snapshot that can change is not a record of a sitting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto.fields import EncryptedJSON
from ..db.models import Base, new_id, utcnow


class SubjectRound(Base):
    """One subject round: resolve, plan, test skills, climb the ladder, take the pop-up lesson."""

    __tablename__ = "subject_rounds"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("subr"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress|complete|abandoned
    topic_key: Mapped[str] = mapped_column(String(96), nullable=False)
    topic_title: Mapped[str] = mapped_column(String(160), default="")
    family: Mapped[str] = mapped_column(String(24), default="generic")
    #: The seed every task in this round derives from. Stored rather than inferred, so a learner who
    #: resumes on another device is served the same items in the same order.
    seed: Mapped[int] = mapped_column(Integer, default=0)
    #: Where the round is: skills | ladder | method | snapshot.
    stage: Mapped[str | None] = mapped_column(String(24), default="skills")
    #: What the learner typed and what it resolved to (subject, family, level hint, source).
    resolved: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    #: The ratings used, with the taxonomy version they were rated against and where they came from.
    leanings: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    #: Which skills were tested and which were not, with the reasons (the plan, frozen at start).
    plan: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    completion: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_subject_rounds_student", "student_id", "started_at"),)


class SubjectTrial(Base):
    """One answer in a round: what was asked, what was given, how long it took, how sure they were."""

    __tablename__ = "subject_trials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("subt"))
    run_id: Mapped[str] = mapped_column(ForeignKey("subject_rounds.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    #: skills | ladder | method_test | method_recheck
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Null for the ladder and the method test, which are not measuring a taxonomy skill.
    skill: Mapped[str | None] = mapped_column(String(48))
    node_id: Mapped[str | None] = mapped_column(String(64))
    sub_area: Mapped[str | None] = mapped_column(String(64))
    rung: Mapped[int | None] = mapped_column(Integer)
    #: The method that taught this concept, for the pop-up lesson's trials.
    method: Mapped[str | None] = mapped_column(String(24))
    concept: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(96), nullable=False)
    index: Mapped[int] = mapped_column(Integer, default=0)
    #: ``measure`` is scored. ``show`` is a lesson or a study card and is never a measurement.
    phase: Mapped[str] = mapped_column(String(12), default="measure")
    correct: Mapped[bool | None] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    wager: Mapped[int | None] = mapped_column(Integer)
    task: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    response: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    score: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "task_id", "index", name="uq_subject_trial_task"),
        Index("ix_subject_trials_run", "run_id", "index"),
        Index("ix_subject_trials_student_stage", "student_id", "stage"),
    )


class MethodTestRun(Base):
    """One pop-up lesson: the seeded assignment, both legs, and the next-day recheck."""

    __tablename__ = "method_test_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mtst"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("subject_rounds.id", ondelete="SET NULL"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(96), nullable=False)
    pair: Mapped[str] = mapped_column(String(64), default="")
    #: Which of the twelve counterbalance cells this learner was dealt.
    cell: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|complete|rechecked
    #: The assignment: methods, order, and the sentence that says it was code and not the learner.
    assignment: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    #: The two legs as served, with their lessons and their questions.
    legs: Mapped[Any] = mapped_column(EncryptedJSON, default=list)
    #: The scored result: per-leg accuracy, expectation and residual, and the comparison.
    results: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    recheck: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    recheck_at: Mapped[datetime | None] = mapped_column(DateTime)
    recheck_done_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_method_tests_student", "student_id", "created_at"),)


class SubjectSnapshot(Base):
    """The subject map: what was measured, what was not, and where the ladder settled."""

    __tablename__ = "subject_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("subj"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("subject_rounds.id", ondelete="SET NULL"))
    topic_key: Mapped[str] = mapped_column(String(96), nullable=False)
    topic_title: Mapped[str] = mapped_column(String(160), default="")
    family: Mapped[str] = mapped_column(String(24), default="generic")
    #: The taxonomy the ratings and the skills were read against. Without it a rating of 3 is a
    #: number with no scale.
    taxonomy_version: Mapped[str] = mapped_column(String(48), default="")
    #: One row per skill in the family: measured results and ``not_measured`` rows side by side.
    skills: Mapped[Any] = mapped_column(EncryptedJSON, default=list)
    leanings: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    ladder: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    method_test: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    #: What the round did and did not reach, in the learner's own words.
    completion: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_subject_snapshots_student", "student_id", "topic_key", "taken_at"),
        UniqueConstraint("run_id", "topic_key", name="uq_subject_snapshot_run"),
    )


class LearnerSubject(Base):
    """A subject a learner chose for the product-level learning flow.

    The older ``SubjectRound`` tables remain the evidence ledger for a single sitting.  This
    small row is the durable course spine around them: one learner may keep several subjects,
    each with its own checked test plan and progress.  Keeping the plan on the row also makes a
    resume deterministic when the model is unavailable on the next device.
    """

    __tablename__ = "learner_subjects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("subject"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(String(240), nullable=False)
    normalised_text: Mapped[str] = mapped_column(String(240), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[str] = mapped_column(String(32), default="generic")
    source: Mapped[str] = mapped_column(String(16), default="simple")  # model|simple
    stage: Mapped[str] = mapped_column(String(16), default="tests")  # tests|learning|done
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plan: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    progress: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_learner_subjects_student", "student_id", "updated_at"),
        UniqueConstraint("student_id", "normalised_text", name="uq_learner_subject_text"),
    )


class SubjectTestEvidence(Base):
    """One scored interaction from a real subject test run (Round 6, Track B).

    A test run produces these rows: item, answer, correct, ms, hints. Never a
    self-report — every row came from an interaction code scored. The dashboard
    and AI 1 read these; ``facts`` carries the item parameters as served, so a
    result can be re-read without replaying the run.
    """

    __tablename__ = "subject_test_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("tevi"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str | None] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[str] = mapped_column(String)
    item_type: Mapped[str] = mapped_column(String(48))
    skill: Mapped[str] = mapped_column(String(64))
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    answer: Mapped[str | None] = mapped_column(String(240))
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    facts: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="subject_test_evidence.facts"), default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        Index("ix_subject_test_evidence_run", "student_id", "run_id", "observed_at"),
        UniqueConstraint("run_id", "item_id", name="uq_subject_test_evidence_item"),
    )


__all__ = ["LearnerSubject", "MethodTestRun", "SubjectRound", "SubjectSnapshot", "SubjectTestEvidence", "SubjectTrial"]
