"""SQLAlchemy models: the single persistent store shared by AI 1 and AI 2.

Everything in blueprint section 6 is represented here:

* learner profile + history -> ``Student``, ``ProfileSnapshot``, ``LearningSession``
* topic mastery             -> ``BktState``, ``BktParams``
* questions/answers/attempts-> ``Item``, ``Attempt``
* event telemetry           -> ``LearnerEvent`` (full sequence, not just scores)
* evidence/hypotheses       -> ``EvidenceRecord``, ``Hypothesis``
* proposals + approval      -> ``AdaptationProposal``, ``ActiveAdaptation``
* retention                 -> ``RetentionSchedule``, ``RetentionCheck``
* resources                 -> ``ResourceReference``, ``HttpCacheEntry``
* session behaviour         -> ``LearningSession`` counters + ``LearnerEvent``
* stated preferences        -> ``StudentPreference`` (vs measured: see ai1.profile)
* interactive lessons       -> ``LessonScene``, ``LessonRun``, ``NarrationCache``
* privacy plumbing          -> ``AuditLog``, ``LlmCall``, ``AuthSession``

Timestamps are naive UTC (SQLite has no tz type); convert at the API edge.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ..crypto.fields import BlindIndexedEmail, BlindIndexedUsername, EncryptedJSON, EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Base(DeclarativeBase):
    """Declarative base with id/timestamp conventions."""


# --------------------------------------------------------------------------- #
# enums (persisted as short strings; kept here so API + DB cannot drift)
# --------------------------------------------------------------------------- #


class HypothesisStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    RETIRED = "retired"


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"
    EVALUATED = "evaluated"
    WITHDRAWN = "withdrawn"


class ItemKind(str, enum.Enum):
    TEXT = "text"  # free/text-based answer
    MCQ = "mcq"
    NUMERIC = "numeric"
    WORKED_EXAMPLE = "worked_example"
    INTERACTIVE = "interactive"
    TEACHBACK = "teachback"  # student explains in their own words


class ExperienceMode(str, enum.Enum):
    """Delivery variants used by the diagnostic to *measure* preference by
    performance -- never to assign a fixed learning-style label."""

    READ = "read"
    VISUAL = "visual"
    EXAMPLE = "example"
    PRACTICE = "practice"
    TEACHBACK = "teachback"


class EventType(str, enum.Enum):
    """Allowlist for client telemetry.

    Anything not listed is rejected by ``telemetry.ingest_event``. This is how
    the "no unrelated surveillance / no keystroke logging" rule is enforced
    mechanically rather than by comment.
    """

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PAUSE = "pause"
    RESUME = "resume"
    FOCUS_CHANGE = "focus_change"  # tab/app visibility only (seconds away)
    QUESTION_SHOWN = "question_shown"
    FIRST_INTERACTION = "first_interaction"
    RESPONSE_CHANGE = "response_change"
    HINT_OPENED = "hint_opened"
    EXPLANATION_OPENED = "explanation_opened"
    EXAMPLE_OPENED = "example_opened"
    VISUAL_OPENED = "visual_opened"
    RESOURCE_OPENED = "resource_opened"
    ANSWER_SUBMITTED = "answer_submitted"
    FEEDBACK_SHOWN = "feedback_shown"
    TEACHBACK_STARTED = "teachback_started"
    TEACHBACK_ENDED = "teachback_ended"
    VOICE_TURN = "voice_turn"
    PROPOSAL_SHOWN = "proposal_shown"
    PROPOSAL_DECIDED = "proposal_decided"
    ADAPTATION_EFFECT = "adaptation_effect"


class VoiceGender(str, enum.Enum):
    FEMALE = "female"
    MALE = "male"


# --------------------------------------------------------------------------- #
# accounts, sessions, audit
# --------------------------------------------------------------------------- #


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("stu"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # PII is encrypted at the column level; the blind index allows login lookups
    # without ever storing plaintext.
    display_name: Mapped[str | None] = mapped_column(EncryptedText(context="student.name"))
    email: Mapped[str | None] = mapped_column(BlindIndexedEmail())
    email_index: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    # The identifier a learner signs in with on this build. Separate column, separate digest
    # label (see `crypto/fields.py`), so "which identifier does this row carry" is answerable
    # from the row rather than from a convention nobody can see. Email is kept for existing
    # accounts and the tunnel demo, and either one signs in.
    username: Mapped[str | None] = mapped_column(BlindIndexedUsername())
    username_index: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="student")

    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    diagnostic_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")
    age_band: Mapped[str | None] = mapped_column(String(16))  # "16-18" etc, no DOB

    # voice preferences (device-local; nothing recorded unless voice used)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_gender: Mapped[str] = mapped_column(String(8), default=VoiceGender.FEMALE.value)

    # consent + privacy flags
    consent_analytics: Mapped[bool] = mapped_column(Boolean, default=False)
    #: DB and write-API name. Learner-facing *reads* call this ``consent_share_with_ai``
    #: (``services/sessions.py``); the split is deliberate privacy copy, not a rename.
    #: New endpoints should read through that mapping rather than exposing this name.
    consent_share_with_llm: Mapped[bool] = mapped_column(Boolean, default=True)
    retain_days: Mapped[int] = mapped_column(Integer, default=365)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    sessions: Mapped[list[LearningSession]] = relationship(back_populates="student")


class StudentPreference(Base):
    """What the learner told us they want, kept separate from what we measured.

    This distinction matters in two directions. A stated preference is an
    instruction we should follow immediately (no approval gate -- the student is
    the one asking). A *measured* pattern is only a hypothesis, and changing
    teaching because of it needs their agreement. Neither is ever treated as a
    fixed trait: rows are editable, and the profile says "as of" when it was taken.
    """

    __tablename__ = "student_preferences"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    # where it came from, so the UI can show provenance next to every rule it applies
    source: Mapped[str] = mapped_column(String(24), default="onboarding")  # onboarding|settings|in_session
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_pref_student", "student_id"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ses"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip_hash: Mapped[str | None] = mapped_column(String(32))
    user_agent_hash: Mapped[str | None] = mapped_column(String(32))
    csrf_token: Mapped[str] = mapped_column(String(64), default=lambda: uuid.uuid4().hex)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    student: Mapped[Student] = relationship(backref="auth_sessions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str | None] = mapped_column(String(32))
    object_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), default="ok")
    detail: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="audit.detail"))

    __table_args__ = (Index("ix_audit_actor_action", "actor_id", "action"),)


# --------------------------------------------------------------------------- #
# curriculum
# --------------------------------------------------------------------------- #


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("crs"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)

    topics: Mapped[list[Topic]] = relationship(back_populates="course")


class Topic(Base):
    """Atomic skill used by BKT. Kept small deliberately: BKT assumes one skill."""

    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("top"))
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1
    est_seconds: Mapped[int] = mapped_column(Integer, default=120)
    keywords: Mapped[list[str] | None] = mapped_column(JSON)

    course: Mapped[Course] = relationship(back_populates="topics")
    items: Mapped[list[Item]] = relationship(back_populates="topic")

    __table_args__ = (UniqueConstraint("course_id", "code", name="uq_topic_course_code"),)


class TopicPrerequisite(Base):
    """Directed prerequisite edges: Topic -> Topic (edges = missing
    prerequisites that AI 1 must detect and AI 2 must remediate)."""

    __tablename__ = "topic_prerequisites"

    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    prerequisite_id: Mapped[str] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    rationale: Mapped[str | None] = mapped_column(Text)


class LearningMaterial(Base):
    """Syllabus sections / notes AI 2 is allowed to teach from."""

    __tablename__ = "learning_materials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mat"))
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24), default="syllabus")  # syllabus|notes|transcript
    body: Mapped[str] = mapped_column(EncryptedText(context="material.body"))
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- #
# items + attempts
# --------------------------------------------------------------------------- #


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("itm"))
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default=ItemKind.TEXT.value, index=True)
    stem: Mapped[str] = mapped_column(Text)
    # Parametric items keep their template plus the variable ranges in
    # ``answer_spec``; the rendered ``stem`` is one draw. Re-drawing a variant is
    # how ADAPT gets a second genuine retrieval attempt on the same skill instead
    # of a memory-of-numbers attempt.
    stem_template: Mapped[str | None] = mapped_column(Text)
    # Delivery variant used for measurement-only comparison (see ExperienceMode).
    experience_mode: Mapped[str] = mapped_column(String(16), default=ExperienceMode.READ.value)
    prompt_context: Mapped[str | None] = mapped_column(Text)  # long explanation / visual alt text
    visual_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # svg/tikz-ish description
    choices: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    answer_spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    solution_steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)  # worked example
    hints: Mapped[list[str] | None] = mapped_column(JSON)
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # teachback rubric
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    est_seconds: Mapped[int] = mapped_column(Integer, default=90)
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    numbas_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(24), default="bank")  # bank|ai2|numbas
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # --- provenance and calibration for model-written items (see assessment/generated.py) --
    # Which model wrote this one. Without it, "was the batch that broke the fractions
    # items the 1.5 or the 2.0 key?" has no answer, and that is the question an incident
    # review actually starts from.
    generated_by: Mapped[str | None] = mapped_column(String(64))
    # What the writer claimed, kept because ``difficulty`` stops being that value as soon
    # as the first learner answers: an observed estimate has to shrink toward something.
    prior_difficulty: Mapped[float | None] = mapped_column(Float)
    n_attempts: Mapped[int] = mapped_column(Integer, default=0)
    n_correct: Mapped[int] = mapped_column(Integer, default=0)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: How this item's answer key was checked: ``authored`` (a person wrote it),
    #: ``arithmetic`` (an expression was evaluated), ``quotable`` (the key's text is in
    #: the source material) or ``unverified`` (neither — the item says so out loud).
    verification: Mapped[str] = mapped_column(String(16), default="authored")

    topic: Mapped[Topic] = relationship(back_populates="items")

    __table_args__ = (Index("ix_items_topic_mode", "topic_id", "experience_mode"),)


class NumbasPackage(Base):
    """Numbas export/import bookkeeping so assessments stay authorable in Numbas."""

    __tablename__ = "numbas_packages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("nbp"))
    name: Mapped[str] = mapped_column(String(200))
    direction: Mapped[str] = mapped_column(String(8), default="export")  # export|import
    path: Mapped[str | None] = mapped_column(String(400))
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.3")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningSession(Base):
    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("lrs"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(24), default="quiz")  # diagnostic|quiz|practice|chat|voice
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|finished|abandoned
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Session behaviour that changes how attempts should be read (blueprint §6).
    pause_count: Mapped[int] = mapped_column(Integer, default=0)
    focus_change_count: Mapped[int] = mapped_column(Integer, default=0)
    away_seconds: Mapped[int] = mapped_column(Integer, default=0)
    interruptions: Mapped[int] = mapped_column(Integer, default=0)
    voice_turns: Mapped[int] = mapped_column(Integer, default=0)
    device_hint: Mapped[str | None] = mapped_column(String(32))  # "desktop"/"mobile" only
    active_ms: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime)  # AI 1 run marker
    summary: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="session.summary"))

    student: Mapped[Student] = relationship(back_populates="sessions")
    events: Mapped[list[LearnerEvent]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    attempts: Mapped[list[Attempt]] = relationship(back_populates="session")


class LearnerEvent(Base):
    """Append-only event log: the full sequence, not just the final score."""

    __tablename__ = "learner_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # per-session order
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String, index=True)
    ms_since_question: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON(context="event.payload"), default=None
    )
    client_id: Mapped[str | None] = mapped_column(String(64))  # dedupe key

    session: Mapped[LearningSession] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_event_session_seq"),
        UniqueConstraint("session_id", "client_id", name="uq_event_client_id"),
        Index("ix_event_student_type_ts", "student_id", "type", "ts"),
    )


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("att"))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(String, index=True)
    experience_mode: Mapped[str] = mapped_column(String(16), default=ExperienceMode.READ.value)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)

    first_answer: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="attempt.answer"))
    final_answer: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="attempt.answer"))
    correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)  # 0..1 partial credit
    graded_by: Mapped[str] = mapped_column(String(16), default="rules")  # rules|ai2|teacher

    time_to_first_ms: Mapped[int | None] = mapped_column(Integer)
    time_on_item_ms: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    change_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_from_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    examples_opened: Mapped[int] = mapped_column(Integer, default=0)
    explanation_opened: Mapped[bool] = mapped_column(Boolean, default=False)
    away_ms: Mapped[int] = mapped_column(Integer, default=0)
    latency_z: Mapped[float | None] = mapped_column(Float)  # vs student's own baseline
    intervention_ids: Mapped[list[str] | None] = mapped_column(JSON)  # active adaptations applied
    ai_feedback: Mapped[str | None] = mapped_column(EncryptedText(context="attempt.feedback"))
    teachback_score: Mapped[float | None] = mapped_column(Float)

    # --- the method that was *assigned* for this attempt (learning/methods.py) ---- #
    # These are decisions, recorded per attempt so the comparison can be read back
    # across sessions: the method, the format of the question it was measured with
    # (kept separate on purpose — a method comparison confounded with the answer
    # format measures the format), the model's own prediction for the question
    # (which is what makes "actual vs expected" possible), the arm when the
    # worked-example question was randomly assigned, and whether the row is pilot data.
    method: Mapped[str | None] = mapped_column(String(32), index=True)
    question_kind: Mapped[str | None] = mapped_column(String(24))
    expected_success: Mapped[float | None] = mapped_column(Float)
    mapping_design: Mapped[str | None] = mapped_column(String(24))
    method_arm: Mapped[str | None] = mapped_column(String(24))
    #: The attempt this one is the reteach of, when the fail→reteach loop chose it.
    reteach_of: Mapped[str | None] = mapped_column(String, index=True)
    pilot: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[LearningSession] = relationship(back_populates="attempts")
    item: Mapped[Item] = relationship()

    __table_args__ = (Index("ix_attempt_student_topic", "student_id", "topic_id", "submitted_at"),)


# --------------------------------------------------------------------------- #
# AI 1 state: mastery, evidence, hypotheses, adaptations
# --------------------------------------------------------------------------- #


class BktState(Base):
    """Per (student, topic) knowledge trace."""

    __tablename__ = "bkt_states"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    p_know: Mapped[float] = mapped_column(Float, default=0.2)
    p_slip: Mapped[float] = mapped_column(Float, default=0.1)
    p_guess: Mapped[float] = mapped_column(Float, default=0.2)
    p_learn: Mapped[float] = mapped_column(Float, default=0.2)
    p_forget: Mapped[float] = mapped_column(Float, default=0.0)
    half_life_hours: Mapped[float | None] = mapped_column(Float)  # PGLR-learned
    n_obs: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    mastery_band: Mapped[str] = mapped_column(String(16), default="new")
    decay_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_practice_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    student: Mapped[Student] = relationship(backref="bkt_states")

    __table_args__ = (Index("ix_bkt_topic", "topic_id", "p_know"),)


class BktParams(Base):
    """Cohort-level parameters learned by EM, shared as a prior for new students."""

    __tablename__ = "bkt_params"

    scope: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")  # global|course:<id>
    topic_id: Mapped[str] = mapped_column(String, primary_key=True, default="*")
    p_learn: Mapped[float] = mapped_column(Float, default=0.2)
    p_guess: Mapped[float] = mapped_column(Float, default=0.2)
    p_slip: Mapped[float] = mapped_column(Float, default=0.1)
    p_forget: Mapped[float] = mapped_column(Float, default=0.0)
    p_init: Mapped[float] = mapped_column(Float, default=0.2)
    n_sequences: Mapped[int] = mapped_column(Integer, default=0)
    log_likelihood: Mapped[float | None] = mapped_column(Float)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)


class EvidenceRecord(Base):
    """A quantified observation. Facts only -- interpretation lives in Hypothesis."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("evi"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    detector: Mapped[str] = mapped_column(String(48), index=True)
    topic_ids: Mapped[list[str] | None] = mapped_column(JSON)
    attempt_ids: Mapped[list[str] | None] = mapped_column(JSON)
    metric: Mapped[str] = mapped_column(String(48))
    value: Mapped[float | None] = mapped_column(Float)
    baseline: Mapped[float | None] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer, default=0)
    facts: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="evidence.facts"))
    strength: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 sample-size/consistency
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_evidence_student_detector", "student_id", "detector", "observed_at"),)


class Hypothesis(Base):
    """An explicitly *fallible* explanation for a pattern."""

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("hyp"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    detector: Mapped[str] = mapped_column(String(48), index=True)
    statement: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[str] | None] = mapped_column(JSON)  # learning-science references
    topic_ids: Mapped[list[str] | None] = mapped_column(JSON)
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=HypothesisStatus.CANDIDATE.value, index=True)
    prior: Mapped[float] = mapped_column(Float, default=0.3)
    posterior: Mapped[float] = mapped_column(Float, default=0.3)
    supports: Mapped[int] = mapped_column(Integer, default=0)
    refutes: Mapped[int] = mapped_column(Integer, default=0)
    falsification: Mapped[str | None] = mapped_column(Text)  # what would disprove it
    proposed_strategy: Mapped[str | None] = mapped_column(String(48))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("student_id", "detector", name="uq_hypo_student_detector"),)


class StrategyStat(Base):
    """Per-student outcome model for teaching approaches (Beta posterior).

    AI 1 keeps this so "which approach works for *this* student" is a running
    estimate fed by measured outcomes, not an assumed style label.
    """

    __tablename__ = "strategy_stats"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(48), primary_key=True)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    n: Mapped[int] = mapped_column(Integer, default=0)
    mean_lift: Mapped[float] = mapped_column(Float, default=0.0)  # mean delta accuracy
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdaptationProposal(Base):
    """AI 1's request to change something persistent. Requires a student decision."""

    __tablename__ = "adaptation_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("adp"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    hypothesis_id: Mapped[str | None] = mapped_column(ForeignKey("hypotheses.id", ondelete="SET NULL"))
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSON)
    strategy: Mapped[str] = mapped_column(String(48), index=True)

    facts_text: Mapped[str] = mapped_column(Text)  # what was observed
    pattern_text: Mapped[str] = mapped_column(Text)  # what repeats
    hypothesis_text: Mapped[str] = mapped_column(Text)  # possible explanation (not a diagnosis)
    change_text: Mapped[str] = mapped_column(Text)  # the concrete change
    ask_text: Mapped[str] = mapped_column(Text)  # the approval question
    expected_effect: Mapped[str | None] = mapped_column(Text)
    caveat_text: Mapped[str | None] = mapped_column(Text)  # alternative explanations
    alternatives: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    action: Mapped[str] = mapped_column(String(48))
    action_payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="proposal.payload"))
    review_metric: Mapped[str | None] = mapped_column(String(48))
    review_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String(16), default=ProposalStatus.PENDING.value, index=True)
    student_note: Mapped[str | None] = mapped_column(EncryptedText(context="proposal.note"))
    modifications: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="proposal.mods"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Outcome tracking (blueprint §3: "track whether approved adaptations help")
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evaluation_status: Mapped[str | None] = mapped_column(String(16))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)

    hypothesis: Mapped[Hypothesis | None] = relationship()


class ActiveAdaptation(Base):
    """The only table AI 2 is allowed to read for *instruction overrides*.

    Rows are written exclusively by :meth:`adapt.services.adaptations.approve`,
    which requires a recorded student decision -- enforced at runtime in
    ``services/adaptations.py`` (see ``assert_student_approved``).
    """

    __tablename__ = "approved_adaptations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("act"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("adaptation_proposals.id", ondelete="CASCADE"))
    strategy: Mapped[str] = mapped_column(String(48), index=True)
    action: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="active.payload"))
    source: Mapped[str] = mapped_column(String(16), default="approved")  # approved|modified
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    end_reason: Mapped[str | None] = mapped_column(String(32))
    effect_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_active_student", "student_id", "active"),)


class ProfileSnapshot(Base):
    """Immutable history of the learner profile -> powers the journey view."""

    __tablename__ = "profile_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pro"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reason: Mapped[str] = mapped_column(String(32), default="session_end")
    profile: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="profile.snapshot"))
    topic_count: Mapped[int] = mapped_column(Integer, default=0)
    hypothesis_count: Mapped[int] = mapped_column(Integer, default=0)
    adaptation_count: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- #
# retention + spaced practice
# --------------------------------------------------------------------------- #


class RetentionSchedule(Base):
    """Half-life regression (PGLR-style) schedule per student x topic."""

    __tablename__ = "retention_schedules"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    half_life_hours: Mapped[float] = mapped_column(Float, default=24.0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    last_recall_at: Mapped[datetime | None] = mapped_column(DateTime)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    expected_recall_at_due: Mapped[float] = mapped_column(Float, default=0.8)
    strength: Mapped[float] = mapped_column(Float, default=0.0)  # 1/half_life normalised
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    feature_weights: Mapped[list[float] | None] = mapped_column(JSON)
    last_lag_hours: Mapped[float | None] = mapped_column(Float)
    last_result: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (Index("ix_schedule_due", "due_at", "student_id"),)


class RetentionCheck(Base):
    """Delayed retest result: did the learning stick, and by how much?"""

    __tablename__ = "retention_checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rtc"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[str | None] = mapped_column(String)
    proposal_id: Mapped[str | None] = mapped_column(String, index=True)
    lag_hours: Mapped[float] = mapped_column(Float, default=0.0)
    expected_recall: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|probe|post_adaptation
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------------- #
# interactive learning engine (scenes, runs, narration)
# --------------------------------------------------------------------------- #


class LessonScene(Base):
    """A validated scene specification the player can run.

    ``spec`` is exactly what :func:`adapt.learning.scenespec.validate` returned: a plain data
    document with no executable content in it, which is what makes it safe to keep a learner- or
    AI-authored scene beside a built-in one. The derived numbers (the generated list, the frame
    narration, the frame count) are stored rather than recomputed so that a lesson resumed after a
    restart replays the *same* trace the learner left, and AI 3 explains frames that still exist.

    Anything a learner typed *about* the scene goes in ``learner_note``: like every other learner
    utterance in this database, that is encrypted, while the engine's own content stays readable
    so a list of lessons costs no crypto at all.
    """

    __tablename__ = "lesson_scenes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("scene"))
    spec_id: Mapped[str] = mapped_column(String(32), index=True)  # content-addressed scn_...
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(160))
    origin: Mapped[str] = mapped_column(String(16), default="builtin")  # builtin|ai2|learner
    author_student_id: Mapped[str | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    derived_from: Mapped[str | None] = mapped_column(String(32))  # spec_id this edit started from
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    prediction_count: Mapped[int] = mapped_column(Integer, default=0)
    learner_note: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="lesson.scene_note"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_scene_kind_origin", "kind", "origin", "created_at"),
        # Content-addressed, but not globally unique, and the reason is a learner's privacy and
        # their memory at once: two people who both narrow a search to `[1, 4, 7, 9, 12]` produce
        # the same *content*, so a unique index on spec_id silently attaches the second learner's
        # "my copy" to the first learner's row -- their copy then never appears in their list, and
        # their own note lands on someone else's record. So: one canonical shared row per content
        # id (the built-in and AI-authored ones, which have no author), and one row per learner.
        Index(
            "uq_scene_spec_shared",
            "spec_id",
            unique=True,
            sqlite_where=text("author_student_id IS NULL"),
            postgresql_where=text("author_student_id IS NULL"),
        ),
        Index("uq_scene_spec_author", "spec_id", "author_student_id", unique=True),
    )


class LessonRun(Base):
    """One learner's pass through one scene -- including where they stopped.

    The position lives here rather than being reconstructed from the event log because a restart
    has to put the player back on the same frame in one read. What they *predicted* is mirrored
    into ``LearnerEvent`` (that is the sequence AI 1 reasons over) and summarised per frame in
    ``payload``, which is what the review screen shows them afterwards. The counters are the
    dashboard's cheap answer; ``params`` is the learner's own variables, kept so "re-run it my
    way" survives a reload instead of being a client-side fluke.
    """

    __tablename__ = "lesson_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("lesson"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("lesson_scenes.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|finished|stopped
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    furthest_frame: Mapped[int] = mapped_column(Integer, default=0)
    frames_viewed: Mapped[int] = mapped_column(Integer, default=0)
    replays: Mapped[int] = mapped_column(Integer, default=0)
    pauses: Mapped[int] = mapped_column(Integer, default=0)
    seeks: Mapped[int] = mapped_column(Integer, default=0)
    predictions_total: Mapped[int] = mapped_column(Integer, default=0)
    predictions_correct: Mapped[int] = mapped_column(Integer, default=0)
    param_changes: Mapped[int] = mapped_column(Integer, default=0)
    seconds_watched: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="lesson.run"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_lesson_run_student_status", "student_id", "status", "last_seen_at"),
        Index("ix_lesson_run_scene_student", "scene_id", "student_id"),
    )


class NarrationCache(Base):
    """AI 3's answer about one frame, kept once so no frame ever costs a model call per play.

    Keyed by the scene fingerprint, the frame and a hash of the question -- so a spec that
    changes (new params, new frame count) simply cannot hit an old answer, and the learner's words
    are not stored here at all: only their digest.
    """

    __tablename__ = "narration_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    spec_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    question_hash: Mapped[str] = mapped_column(String(16), default="")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="deterministic")  # deterministic|llm|template
    model: Mapped[str | None] = mapped_column(String(48))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    __table_args__ = (
        UniqueConstraint("spec_id", "frame_index", "question_hash", name="uq_narration_frame_question"),
    )


# --------------------------------------------------------------------------- #
# AI 2 interactions + resources + speech
# --------------------------------------------------------------------------- #


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("msg"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String(16))  # student|assistant|system
    content: Mapped[str] = mapped_column(EncryptedText(context="chat.content"))
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    adapted_from: Mapped[list[str] | None] = mapped_column(JSON)  # strategies actually used
    provider: Mapped[str] = mapped_column(String(16), default="local")  # gemini|local
    model: Mapped[str | None] = mapped_column(String(48))
    redacted_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class VoiceTurn(Base):
    __tablename__ = "voice_turns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("voi"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    ws_session: Mapped[str | None] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in (STT) | out (TTS)
    text: Mapped[str | None] = mapped_column(EncryptedText(context="voice.text"))
    stt_backend: Mapped[str | None] = mapped_column(String(24))
    tts_backend: Mapped[str | None] = mapped_column(String(24))
    voice_gender: Mapped[str | None] = mapped_column(String(8))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ResourceReference(Base):
    __tablename__ = "resource_references"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("res"))
    student_id: Mapped[str | None] = mapped_column(String, index=True)
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)  # openalex|arxiv|openlibrary|youtube
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # paper|preprint|book|video
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str] | None] = mapped_column(JSON)
    year: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(128))
    snippet: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    why: Mapped[str | None] = mapped_column(Text)  # AI 2's stated reason
    license: Mapped[str | None] = mapped_column(String(48))
    flags: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # paywalled, level, verified
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_resource_provider_ext"),
    )


class HttpCacheEntry(Base):
    """Cache for external API results: fewer quota calls, works when offline."""

    __tablename__ = "http_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    request_url: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|stale


class LlmCall(Base):
    """Audit + quota guard for Gemini usage. Records *that* data was sent and
    how much was redacted -- never the content (which stays in the DB)."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("llm"))
    student_id: Mapped[str | None] = mapped_column(String, index=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)  # ai1_analyze|ai2_explain|...
    role: Mapped[str] = mapped_column(String(8))  # ai1|ai2
    model: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24))  # ok|quota|error|fallback|disabled
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    redacted_fields: Mapped[int] = mapped_column(Integer, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    #: Which class of work this call was (``ai/routing.py``): light | standard | deep.
    #: Recorded per call rather than derived on read, because the routing table changes and
    #: "what did we actually spend the cheap model on last Tuesday" has to survive that.
    tier: Mapped[str | None] = mapped_column(String(12))
    #: Input tokens the provider served from a registered static prefix (a *saving*, not a
    #: cost): the number that says whether the context cache earned its place.
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- #
# the Learner State Store (round brief 2A)
# --------------------------------------------------------------------------- #
#
# One logical store, four domains, and a *write-permission matrix* enforced in code (see
# ``adapt.state.permissions``). The tables here are deliberately new rather than extra columns on
# the older ones: this repo has no migration tool in the seed path (``create_all`` adds tables and
# never alters one), and a store that only exists on a fresh clone is not a store.
#
# Nothing here is a second copy of the curriculum: ``ContentPack`` is the *registry* for
# generated content (id, hash, validation status), while the authored bank stays in ``Item``.


class StateEvidence(Base):
    """One append-only entry in the evidence log.

    Distinct from ``LearnerEvent`` (the raw client telemetry stream, unfiltered) and from
    ``EvidenceRecord`` (a *detector's* quantified finding about a pattern). This is the layer
    between them: every scored observation, game result and method assignment, with the weight
    its source deserves and an idempotency key, so re-ingesting the same event cannot double-count
    it.

    ``source_type`` is the load-bearing field:

    * ``controlled`` (weight 1.0) — produced by an instrument the app ran: a graded attempt, a
      baseline-game result, a scored teach-back. It is evidence about ability.
    * ``observational`` (weight ~0.4) — noticed rather than measured: a stall, a tab switch, a
      re-asked question. It is evidence about the *session*, and it is not allowed to move the
      learner model as far as a controlled observation does.
    """

    __tablename__ = "state_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("evd"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    #: The event's own id when it has one (``Attempt.id``, a game result id, a client ``client_id``).
    #: Unique per learner: that is what makes ingestion idempotent.
    event_id: Mapped[str] = mapped_column(String(96), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)  # controlled | observational
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    item_id: Mapped[str | None] = mapped_column(String, index=True)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="state.evidence"), default=None)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("student_id", "event_id", name="uq_state_evidence_event"),
        Index("ix_state_evidence_student_time", "student_id", "occurred_at"),
    )


class ModelVersion(Base):
    """An immutable version of the derived learner model.

    Every write produces a new row; nothing is ever updated in place. That is what makes the
    "rebuild equals incremental" test meaningful (both routes write versions from the same
    evidence and the same code) and what lets a lesson say *exactly* which model it was built
    from.

    ``evidence_upto`` is the id of the last evidence row folded in, and ``evidence_count`` the
    number folded in in total: the first makes the version's provenance precise, the second lets a
    rebuild prove it consumed the same evidence.
    """

    __tablename__ = "model_versions"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_upto: Mapped[str | None] = mapped_column(String)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="state.model"))
    #: Why this version exists: ``ingest`` (new evidence), ``rebuild`` (from the log), ``manual``.
    reason: Mapped[str] = mapped_column(String(24), default="ingest")
    #: The version this one was derived from, when it was derived rather than rebuilt.
    parent_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_model_versions_student_created", "student_id", "created_at"),)


class ContentPack(Base):
    """The content registry: a generated (or authored) pack, addressed by its own content hash.

    "Content generated once is reused by id" is only true if the id is a function of the content.
    ``id`` is ``cpk_<sha256 of the canonical payload>``, so generating the same pack twice produces
    the same row rather than a second one, and a *changed* pack is a new id — which is what the
    lesson plan's provenance needs to stay exact.
    """

    __tablename__ = "content_packs"

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    topic_id: Mapped[str | None] = mapped_column(String, index=True)
    concept: Mapped[str] = mapped_column(String(96), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # explanation | variant_set | rubric | scene
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="state.content"))
    #: ``unvalidated`` | ``valid`` | ``rejected`` — set by the deterministic checker, never by the
    #: module that wrote the content.
    validation: Mapped[str] = mapped_column(String(16), default="unvalidated", index=True)
    validation_notes: Mapped[list[str] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16), default="ai2")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_content_packs_concept_kind", "concept", "kind"),)


class TeachingMemoryEntry(Base):
    """What this learner has already been told, shown or asked — so nothing repeats by accident.

    One row per (session, kind, ref): adding the same fact twice is a no-op, which is what makes
    "the app never asks for anything already stored" checkable rather than aspirational.
    """

    __tablename__ = "teaching_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    ref: Mapped[str] = mapped_column(String(96))
    text: Mapped[str | None] = mapped_column(EncryptedText(context="memory.text"))
    #: Part C: the method and analogy shown, and which lesson moment produced this memory.
    method: Mapped[str | None] = mapped_column(String(48))
    analogy_id: Mapped[str | None] = mapped_column(String(96))
    moment: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("student_id", "session_id", "kind", "ref", name="uq_teaching_memory_entry"),
    )


class LessonPlanRecord(Base):
    """The plan an adaptor produced, with the model version and content ids it was built from.

    ``superseded_by`` is how the mid-lesson re-plan is recorded: the old plan stays in the log (the
    learner saw it) and the new one points back at it.
    """

    __tablename__ = "lesson_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pln"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    model_version: Mapped[int] = mapped_column(Integer, default=0)
    content_ids: Mapped[list[str] | None] = mapped_column(JSON)
    plan: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="state.plan"))
    superseded_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_lesson_plans_student_created", "student_id", "created_at"),)


class DeliveryRecord(Base):
    """What the learner actually saw, and why — the provenance side of every screen.

    ``why`` stores the references (evidence ids, model version, pack ids) rather than a sentence,
    so the "why this lesson looks like this" panel is exact rather than narrated.
    """

    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("dlv"))
    plan_id: Mapped[str | None] = mapped_column(String, index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    block: Mapped[str] = mapped_column(String(48), index=True)
    content_id: Mapped[str | None] = mapped_column(String)
    seen: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="state.delivery"), default=None)
    why: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON(context="state.why"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SessionState(Base):
    """Where the learner is right now: topic, block, pace, fatigue and the plan in force.

    Server-side on purpose: a learner who switches device mid-lesson resumes at the same block of
    the same plan, because nothing about the session lives in the browser.
    """

    __tablename__ = "session_states"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str | None] = mapped_column(String)
    block: Mapped[str] = mapped_column(String(48), default="")
    plan_id: Mapped[str | None] = mapped_column(String)
    pace: Mapped[float] = mapped_column(Float, default=1.0)
    fatigue: Mapped[float] = mapped_column(Float, default=0.0)
    #: Optimistic concurrency: a writer states the version it read, and a mismatch is refused
    #: rather than silently overwriting (two simultaneous events must both land).
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- #
# the baseline (round brief section 5): games, trials, and the mind snapshot
# --------------------------------------------------------------------------- #
# Three tables rather than one. A run is *when* a learner sat the baseline; a trial is one scored
# attempt inside it (and is the only place a measurement lives — one row per tap, one row per
# machine probe); a snapshot is the derived mind map, stored with the run that produced it so
# "which baseline is this reading from?" is answerable years later.
#
# Trials are the same shape as the shared evidence log (brief 2A) on purpose: the baseline is
# evidence, so it is ingested into ``state_evidence`` as well, with its own event ids, which is what
# makes a partial baseline usable by the rest of the app instead of a special case.


class BaselineRun(Base):
    """One sitting of the mind baseline: skippable at any point, resumable at any point."""

    __tablename__ = "baseline_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("base"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress|complete|abandoned
    #: The seed everything in this run derives from. Stored, not inferred: the same learner must get
    #: the same codewords and the same machine rules on a second device mid-run.
    seed: Mapped[int] = mapped_column(Integer, default=0)
    format_pick: Mapped[str | None] = mapped_column(String(24))
    stage: Mapped[str | None] = mapped_column(String(32), default="format")
    completion: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_baseline_runs_student", "student_id", "started_at"),)


class BaselineTrial(Base):
    """One task attempt: what was asked, what was answered, how long it took, how sure they were."""

    __tablename__ = "baseline_trials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("btrial"))
    run_id: Mapped[str] = mapped_column(ForeignKey("baseline_runs.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    index: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(12), default="measure")  # practice|measure|show
    correct: Mapped[bool | None] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    #: Confidence, 0-3 coins. Nullable because most trials are not wagered on.
    wager: Mapped[int | None] = mapped_column(Integer)
    task: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    response: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    score: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        # The idempotency rule, the same one the evidence log uses: a re-sent trial is one row.
        UniqueConstraint("run_id", "task_id", "index", name="uq_baseline_trial_task"),
        Index("ix_baseline_trials_run", "run_id", "index"),
        Index("ix_baseline_trials_student_game", "student_id", "game"),
    )


class MindSnapshot(Base):
    """The eight factors, their ranges and n, and the levers derived from them."""

    __tablename__ = "mind_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mind"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("baseline_runs.id", ondelete="SET NULL"))
    factors: Mapped[Any] = mapped_column(EncryptedJSON, default=list)
    levers: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    completion: Mapped[Any] = mapped_column(EncryptedJSON, default=dict)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_mind_snapshots_student", "student_id", "taken_at"),)


__all__ = [
    "Base",
    "utcnow",
    "new_id",
    "Student",
    "AuthSession",
    "AuditLog",
    "StudentPreference",
    "BaselineRun",
    "BaselineTrial",
    "MindSnapshot",
    "Course",
    "Topic",
    "TopicPrerequisite",
    "LearningMaterial",
    "Item",
    "NumbasPackage",
    "LearningSession",
    "LearnerEvent",
    "Attempt",
    "BktState",
    "BktParams",
    "EvidenceRecord",
    "Hypothesis",
    "StrategyStat",
    "AdaptationProposal",
    "ActiveAdaptation",
    "ProfileSnapshot",
    "RetentionSchedule",
    "RetentionCheck",
    "ChatMessage",
    "VoiceTurn",
    "ResourceReference",
    "HttpCacheEntry",
    "LlmCall",
    "LessonScene",
    "LessonRun",
    "NarrationCache",
    "EventType",
    "HypothesisStatus",
    "ProposalStatus",
    "ItemKind",
    "ExperienceMode",
    "VoiceGender",
    "StateEvidence",
    "ModelVersion",
    "ContentPack",
    "TeachingMemoryEntry",
    "LessonPlanRecord",
    "DeliveryRecord",
    "SessionState",
]


# -- the round's own tables --------------------------------------------------- #
# ``adapt/subjects/models.py`` declares the subject round's four tables (phase 3) away from this
# file, which was frozen while six workstreams ran. Importing it here is what makes ``Base.metadata``
# *the whole schema* no matter which module a process imported first: ``create_all`` builds every
# table on app boot and on ``adapt init``, and the migration test compares the migrated database
# against all of it. Without this line, whether a database has the subject tables depended on import
# order -- a create-all database could silently lack them and the first subject round would fail on
# a missing table.
from ..subjects import models as _subject_models  # noqa: E402,F401

# The journey's one table (round 2, Part D), for the same reason and by the same rule:
# ``create_all`` builds whatever is on ``Base.metadata`` when it runs, and ``alembic check``
# compares the database against exactly that. Importing the module is what guarantees both
# see ``journey_stages`` no matter which module a process imported first.
from ..journey import models as _journey_models  # noqa: E402,F401

# The generated-content cache (round 2, Part B) lives in ``adapt/content/skeleton/cache.py``
# and follows the same rule. Without this import, a ``create_all`` run whose process only
# imported the engine would silently lack ``content_pack_cache``, ``content_prepare_jobs``
# and ``content_pack_item_calibration`` — exactly the drift ``tests/test_migrations.py``
# exists to catch. Phase G added this while integrating Part B (see handoff-b.md §2).
from ..content.skeleton import cache as _skeleton_cache  # noqa: E402,F401
