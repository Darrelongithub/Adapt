"""Learner routes: the first end-to-end tenant-scoped data path.

Every query here runs on a connection opened by ``tenant_connection``, i.e.
inside an explicit transaction with ``SET LOCAL adapt.tenant_id``. Nothing in
this module filters by tenant itself: the database does, and a route that
"remembers" to add a WHERE clause would be the bug, not the safeguard.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from adapt_engine.auth.deps import (
    AuthContext,
    require_scope,
    security_requirement,
    tenant_connection,
)
from adapt_engine.crypto.store import DataKeyStore, crypto_store
from adapt_engine.db.schema import PROFILES, validate_learner_id
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1", tags=["learners"], responses=ERROR_RESPONSES)


class LearnerCreate(BaseModel):
    """Allowlisted keys only; no free-form metadata."""

    learner_id: str = Field(min_length=8, max_length=64)
    profile: str = Field(default="adult_general")
    date_of_birth: date | None = None


class Learner(BaseModel):
    id: uuid.UUID
    learner_id: str
    profile: str
    date_of_birth: date | None = None


def _row_to_learner(
    row: object, store: DataKeyStore, conn: Connection, tenant_id: uuid.UUID
) -> Learner:
    """Decrypt the personal columns; the rest of the row is already readable.

    ``profile`` and ``date_of_birth`` are ciphertext at rest, so this is the
    only place they become plaintext again — and the only place that could log
    them, which it does not.
    """
    profile = store.decrypt(conn, tenant_id, row.profile)  # type: ignore[attr-defined]
    dob = store.decrypt(conn, tenant_id, row.date_of_birth)  # type: ignore[attr-defined]
    return Learner(
        id=row.id,  # type: ignore[attr-defined]
        learner_id=row.learner_id,  # type: ignore[attr-defined]
        profile=profile or "",
        date_of_birth=date.fromisoformat(dob) if dob else None,
    )


@router.get(
    "/learners/{learner_id}",
    response_model=Learner,
    dependencies=[Depends(require_scope("observe"))],
    openapi_extra=security_requirement("observe"),
)
def get_learner(
    learner_id: str,
    auth: Annotated[AuthContext, Depends(require_scope("observe"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    store: Annotated[DataKeyStore, Depends(crypto_store)],
) -> Learner:
    """Read one learner, scoped to the caller's tenant by RLS.

    An id belonging to another tenant is simply not there: the policy filters
    it out, and the route answers ``learner_unknown`` — not a 403, which would
    confirm the id exists elsewhere.
    """
    row = conn.execute(
        text(
            "SELECT id, learner_id, profile, date_of_birth FROM learners "
            "WHERE learner_id = :learner_id AND deleted_at IS NULL"
        ),
        {"learner_id": learner_id},
    ).one_or_none()
    if row is None:
        raise AdaptError(
            code="learner_unknown",
            message="no such learner; register the learner first",
            status_code=404,
            retryable=False,
        )
    return _row_to_learner(row, store, conn, auth.tenant_id)


@router.get(
    "/learners",
    response_model=list[Learner],
    dependencies=[Depends(require_scope("observe"))],
    openapi_extra=security_requirement("observe"),
)
def list_learners(
    auth: Annotated[AuthContext, Depends(require_scope("observe"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    store: Annotated[DataKeyStore, Depends(crypto_store)],
) -> list[Learner]:
    rows = conn.execute(
        text(
            "SELECT id, learner_id, profile, date_of_birth FROM learners "
            "WHERE deleted_at IS NULL ORDER BY created_at LIMIT 200"
        )
    ).all()
    return [_row_to_learner(row, store, conn, auth.tenant_id) for row in rows]


@router.post(
    "/learners",
    response_model=Learner,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def create_learner(
    body: LearnerCreate,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    store: Annotated[DataKeyStore, Depends(crypto_store)],
) -> Learner:
    """Register a learner for the caller's tenant.

    The tenant id comes from the API key, never from the body: a caller cannot
    name a tenant, so it cannot choose one.
    """
    if body.profile not in PROFILES:
        raise AdaptError(
            code="payload_invalid",
            message=f"profile must be one of {', '.join(PROFILES)}",
            status_code=422,
            retryable=False,
        )
    try:
        learner_id = validate_learner_id(body.learner_id)
    except ValueError as exc:
        raise AdaptError(
            code="payload_invalid",
            message=str(exc),
            status_code=422,
            retryable=False,
        ) from exc

    try:
        row = conn.execute(
            text(
                "INSERT INTO learners (tenant_id, learner_id, profile, date_of_birth) "
                "VALUES (:tenant_id, :learner_id, :profile, :date_of_birth) "
                "RETURNING id, learner_id, profile, date_of_birth"
            ),
            {
                "tenant_id": auth.tenant_id,
                "learner_id": learner_id,
                "profile": store.encrypt(conn, auth.tenant_id, body.profile),
                "date_of_birth": store.encrypt(
                    conn,
                    auth.tenant_id,
                    body.date_of_birth.isoformat() if body.date_of_birth else None,
                ),
            },
        ).one()
    except IntegrityError as exc:
        raise AdaptError(
            code="conflict",
            message="that learner_id is already registered for this tenant",
            status_code=409,
            retryable=False,
        ) from exc
    return _row_to_learner(row, store, conn, auth.tenant_id)


__all__ = ["router"]
