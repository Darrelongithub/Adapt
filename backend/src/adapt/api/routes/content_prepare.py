"""Asynchronous topic-Pack preparation over HTTP (Phase B).

The job surface exposes only state and the pack's source label. Pack payloads, question keys and raw
topic text remain out of the status response and the job table.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...content.skeleton.cache import (
    ContentPrepareJob,
    cache_identity,
    get_cached,
    running_job_for,
    store_pack,
)
from ...content.skeleton.service import canonical_topic_key, get_pack
from ...db.models import Student, utcnow
from ...services import current as current_services
from ..deps import db_session, rate, student_from_request

log = logging.getLogger("adapt.api.content_prepare")
router = APIRouter(prefix="/api/v1/content/prepare", tags=["content-prepare"])
_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="adapt-content-prepare")
_MODEL_CALL_TIMEOUT_SECONDS = 20.0
# The service allows at most 32 provider attempts at 20 seconds each per Pack.
_JOB_STALE_SECONDS = 900


class PrepareBody(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    level: int = Field(default=0, ge=0, le=20)
    language: str = Field(default="en", min_length=2, max_length=32)
    #: Round 9 wizard scope; recorded on the built pack (see below).
    curriculum: str | None = Field(default=None, max_length=32)
    grade: str | None = Field(default=None, max_length=64)


class _GeminiBridge:
    """Adapt the existing async LLM client to the frozen synchronous model-call interface."""

    def __init__(
        self, llm: Any, loop: asyncio.AbstractEventLoop, student_id: str | None = None
    ) -> None:
        self.llm = llm
        self.loop = loop
        self.student_id = student_id

    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        async def invoke() -> dict[str, Any]:
            result = await asyncio.wait_for(
                self.llm.complete(
                    system=system,
                    user=user,
                    purpose=purpose,
                    role="ai2",
                    student_id=self.student_id,
                    max_output_tokens=max_tokens,
                    response_spec=schema,
                ),
                timeout=_MODEL_CALL_TIMEOUT_SECONDS,
            )
            if not result.ok or not isinstance(result.data, dict):
                raise RuntimeError("the checked content call did not return JSON")
            return result.data

        future = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        try:
            return future.result(timeout=_MODEL_CALL_TIMEOUT_SECONDS + 1.0)
        except Exception:
            future.cancel()
            raise


def _state_for_source(source: str) -> str:
    return "fallback" if source == "simple" else "ready"


def _update_job(*, job_id: str, state: str, source: str | None) -> None:
    services = current_services()
    with services.scope() as session:
        row = session.get(ContentPrepareJob, job_id)
        if row is None:
            return
        row.state = state
        row.pack_source = source
        row.updated_at = utcnow()


async def _prepare_and_store(
    job_id: str, topic: str, level: int, language: str, student_id: str | None = None,
    curriculum: str | None = None, grade: str | None = None,
) -> None:
    services = current_services()
    topic_key = canonical_topic_key(topic)
    identity = cache_identity(topic_key, level=level, language=language)
    with services.scope() as session:
        cached = get_cached(session, identity)
    if cached is not None:
        pack, _digest = cached
        _update_job(job_id=job_id, state=_state_for_source(pack.source), source=pack.source)
        return

    shared_llm = getattr(getattr(services, "ai2", None), "llm", None)
    model = None
    if shared_llm is not None and getattr(shared_llm, "configured", False):
        model = _GeminiBridge(shared_llm, asyncio.get_running_loop(), student_id)
    pack = await asyncio.to_thread(
        get_pack,
        topic,
        level=level,
        language=language,
        model=model,
        curriculum=curriculum,
        grade=grade,
    )
    with services.scope() as session:
        # Another worker may have filled the cache while this pack was being validated. Upsert the
        # same content key so no duplicate cache rows can be created. The
        # shared pack cache stays learner-neutral: the Round 9 scope is
        # stripped before storing (approved objectives are per learner and
        # must never ride in a cache entry shared with other learners).
        from dataclasses import replace as _replace

        shared = _replace(pack, curriculum=None, grade=None, objectives=()) if (curriculum or grade) else pack
        store_pack(session, identity, shared)
        row = session.get(ContentPrepareJob, job_id)
        if row is not None:
            row.state = _state_for_source(pack.source)
            row.pack_source = pack.source
            row.updated_at = utcnow()


def _run_prepare(
    job_id: str, topic: str, level: int, language: str, student_id: str | None = None,
    curriculum: str | None = None, grade: str | None = None,
) -> None:
    try:
        asyncio.run(_prepare_and_store(job_id, topic, level, language, student_id, curriculum, grade))
    except Exception:
        # Do not stringify exception messages: they can include provider or topic text.
        log.error("content_prepare_failed job_id=%s", job_id)
        try:
            _update_job(job_id=job_id, state="fallback", source="simple")
        except Exception:
            log.error("content_prepare_status_write_failed job_id=%s", job_id)


@router.post("")
def prepare_content(
    body: PrepareBody,
    session: Session = Depends(db_session),
    _student: Student = Depends(rate("content")),
) -> dict[str, str]:
    """Queue a validated pack build or immediately return a status job for a cache hit."""
    from ...curriculum.scanner import normalise_curriculum

    curriculum = normalise_curriculum(body.curriculum)
    if body.curriculum and curriculum is None:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_curriculum", "message": "choose Kenya CBC, KCSE, IGCSE, University or Self-study"},
        )
    key = canonical_topic_key(body.topic)
    identity = cache_identity(key, level=body.level, language=body.language)
    cached = get_cached(session, identity)
    if cached is not None:
        pack, _digest = cached
        job = ContentPrepareJob(
            cache_key=identity.cache_key,
            state=_state_for_source(pack.source),
            pack_source=pack.source,
        )
        session.add(job)
        session.commit()
        return {"job_id": job.id}

    running = running_job_for(session, identity.cache_key)
    if running is not None:
        age = utcnow() - (running.updated_at or running.created_at)
        if age <= timedelta(seconds=_JOB_STALE_SECONDS):
            return {"job_id": running.id}
        running.state = "fallback"
        running.pack_source = "simple"
        running.updated_at = utcnow()

    job = ContentPrepareJob(cache_key=identity.cache_key, state="running", pack_source=None)
    session.add(job)
    session.flush()
    job_id = job.id
    # Commit before submitting so the worker can safely see the job row on another DB connection.
    session.commit()
    if curriculum or body.grade:
        _WORKERS.submit(
            _run_prepare,
            job_id,
            body.topic,
            body.level,
            body.language,
            getattr(_student, "id", None),
            curriculum,
            body.grade,
        )
    else:
        # The unscoped call keeps its pre-Round-9 shape exactly.
        _WORKERS.submit(
            _run_prepare,
            job_id,
            body.topic,
            body.level,
            body.language,
            getattr(_student, "id", None),
        )
    return {"job_id": job_id}


@router.get("/{job_id}")
def prepare_status(
    job_id: str,
    session: Session = Depends(db_session),
    _student: Student = Depends(student_from_request),
) -> dict[str, str | None]:
    row = session.get(ContentPrepareJob, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content job not found")
    age = utcnow() - (row.updated_at or row.created_at)
    if row.state == "running" and age > timedelta(seconds=_JOB_STALE_SECONDS):
        row.state = "fallback"
        row.pack_source = "simple"
        row.updated_at = utcnow()
        session.commit()
    return {"state": row.state, "pack_source": row.pack_source}


__all__ = ["PrepareBody", "prepare_content", "prepare_status", "router"]
