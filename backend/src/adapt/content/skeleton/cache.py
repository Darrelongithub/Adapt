"""Encrypted persistence for generated Packs and their asynchronous prepare jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...assessment import generated as G
from ...crypto.fields import EncryptedJSON
from ...db.models import Base, new_id, utcnow
from .service import PACK_SPEC_VERSION
from .types import ItemCalibration, Pack, pack_from_dict
from .validation import normalize_language


class PackCacheEntry(Base):
    """One immutable content pack per normalized topic, level, language and spec revision.

    ``topic_key_hash`` is a one-way digest, not free learner text. The encrypted payload carries the
    complete pack, including answer keys; status endpoints only read the non-sensitive source label.
    """

    __tablename__ = "content_pack_cache"

    topic_key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, primary_key=True)
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    spec_version: Mapped[str] = mapped_column(String(40), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        EncryptedJSON(context="content.pack_cache"), nullable=False
    )
    created_at: Mapped[Any] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_content_pack_cache_source", "source"),)


class ContentPrepareJob(Base):
    """Job status contains only a cache digest and counts, never topic or learner text."""

    __tablename__ = "content_prepare_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pjob"))
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)
    pack_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[Any] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_content_prepare_jobs_cache_state", "cache_key", "state"),)


class PackItemCalibration(Base):
    """Response-only calibration for one content-derived Pack item id.

    This table contains no prompt or answer text. IDs are generated content hashes, and the values
    are the same declared/observed-difficulty, exposure and retirement fields used by
    ``assessment.generated`` for persisted AI 2 items.
    """

    __tablename__ = "content_pack_item_calibration"

    item_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    declared_difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    observed_difficulty: Mapped[float] = mapped_column(Float, nullable=False)
    n_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retirement_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    updated_at: Mapped[Any] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_content_pack_item_cache_active", "cache_key", "active"),)


@dataclass(frozen=True)
class CacheIdentity:
    topic_key_hash: str
    level: int
    language: str
    spec_version: str

    @property
    def cache_key(self) -> str:
        raw = f"{self.topic_key_hash}|{self.level}|{self.language}|{self.spec_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def primary_key(self) -> tuple[str, int, str, str]:
        return (self.topic_key_hash, self.level, self.language, self.spec_version)


def cache_identity(
    topic_key: str,
    *,
    level: int,
    language: str,
    spec_version: str = PACK_SPEC_VERSION,
) -> CacheIdentity:
    normalized_topic = " ".join(str(topic_key or "").casefold().split())
    topic_digest = hashlib.sha256(normalized_topic.encode("utf-8")).hexdigest()
    return CacheIdentity(
        topic_key_hash=topic_digest,
        level=int(level),
        language=normalize_language(language),
        spec_version=str(spec_version),
    )


def content_hash(pack: Pack | dict[str, Any]) -> str:
    """Full SHA-256 of canonical pack content; identical content always has identical identity."""
    payload = pack.as_dict() if isinstance(pack, Pack) else pack
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _calibration_view(row: PackItemCalibration) -> ItemCalibration:
    return ItemCalibration(
        declared_difficulty=float(row.declared_difficulty),
        observed_difficulty=float(row.observed_difficulty),
        n_attempts=int(row.n_attempts),
        n_correct=int(row.n_correct),
        active=bool(row.active),
        retirement_reason=row.retirement_reason,
    )


def get_cached(session: Session, identity: CacheIdentity) -> tuple[Pack, str] | None:
    row = session.get(PackCacheEntry, identity.primary_key)
    if row is None:
        return None
    payload = row.payload
    if not isinstance(payload, dict):
        return None
    try:
        pack = pack_from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return None

    item_ids = [item.id for item in pack.items]
    states = (
        session.execute(
            select(PackItemCalibration).where(PackItemCalibration.item_id.in_(item_ids))
        )
        .scalars()
        .all()
        if item_ids
        else []
    )
    by_id = {state.item_id: state for state in states}
    if pack.source == "model" and any(
        item.scorable and item.id not in by_id for item in pack.items
    ):
        # A verified generated key without its calibration record cannot be safely scored.
        return None
    restored_items = tuple(
        replace(item, calibration=_calibration_view(by_id[item.id])) if item.id in by_id else item
        for item in pack.items
    )
    if by_id:
        pack = replace(pack, items=restored_items)
    return pack, row.content_hash


def _store_item_calibration(
    session: Session,
    identity: CacheIdentity,
    pack: Pack,
) -> None:
    for item in pack.items:
        initial = item.calibration
        if initial is None:
            continue
        row = session.get(PackItemCalibration, item.id)
        if row is None:
            row = PackItemCalibration(
                item_id=item.id,
                cache_key=identity.cache_key,
                declared_difficulty=initial.declared_difficulty,
                observed_difficulty=initial.observed_difficulty,
                n_attempts=initial.n_attempts,
                n_correct=initial.n_correct,
                active=initial.active,
                retirement_reason=initial.retirement_reason,
            )
            session.add(row)
        else:
            # A content-derived id is the same question: never reset its observed difficulty,
            # attempt counts or retirement status during a cache refresh.
            row.cache_key = identity.cache_key
            row.updated_at = utcnow()


def store_pack(session: Session, identity: CacheIdentity, pack: Pack) -> PackCacheEntry:
    """Insert or refresh the exact cache key. The payload is encrypted by its mapped column type."""
    payload = pack.as_dict()
    digest = content_hash(payload)
    row = session.get(PackCacheEntry, identity.primary_key)
    if row is None:
        row = PackCacheEntry(
            topic_key_hash=identity.topic_key_hash,
            level=identity.level,
            language=identity.language,
            spec_version=identity.spec_version,
            content_hash=digest,
            source=pack.source,
            payload=payload,
        )
        session.add(row)
    else:
        row.content_hash = digest
        row.source = pack.source
        row.payload = payload
        row.created_at = utcnow()
    session.flush()
    _store_item_calibration(session, identity, pack)
    session.flush()
    return row


def record_item_attempt(
    session: Session,
    item_id: str,
    *,
    correct: bool,
) -> ItemCalibration | None:
    """Apply the shared observed-difficulty and retirement rules to one scored Pack item.

    Return ``None`` for an item that has no verified generated calibration row. Callers must also
    reject inactive items before recording another attempt.
    """
    if not isinstance(correct, bool):
        raise TypeError("correct must be a bool")
    row = session.get(PackItemCalibration, item_id)
    if row is None:
        return None
    if row.active:
        row.n_attempts += 1
        row.n_correct += int(correct)
        row.observed_difficulty = G.observed_difficulty(
            None,
            declared=float(row.declared_difficulty),
            n_correct=int(row.n_correct),
            n_attempts=int(row.n_attempts),
        )
        retired, reason = G.should_retire(
            declared=float(row.declared_difficulty),
            observed=float(row.observed_difficulty),
            n_attempts=int(row.n_attempts),
            n_correct=int(row.n_correct),
        )
        if retired:
            row.active = False
            row.retirement_reason = reason
        row.updated_at = utcnow()
    return _calibration_view(row)


def running_job_for(session: Session, cache_key: str) -> ContentPrepareJob | None:
    return session.execute(
        select(ContentPrepareJob)
        .where(ContentPrepareJob.cache_key == cache_key, ContentPrepareJob.state == "running")
        .order_by(ContentPrepareJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


__all__ = [
    "CacheIdentity",
    "ContentPrepareJob",
    "PackCacheEntry",
    "PackItemCalibration",
    "cache_identity",
    "content_hash",
    "get_cached",
    "record_item_attempt",
    "running_job_for",
    "store_pack",
]
