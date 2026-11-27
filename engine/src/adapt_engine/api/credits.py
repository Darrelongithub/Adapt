"""Credits over HTTP: a balance to read, and a grant that only provisioning may write.

Correction from review: **credit mutations are provisioning, not runtime.** A
host key moves no credits over the API. Reservations, settlement and release
happen inside the engine, next to the work they pay for, and have no HTTP
surface at all — a route that holds credits would be a route a compromised host
key could use to block its own account, and a route a bug could use to hold
credits that never settle.

So this module has exactly two routes:

* ``GET /v1/credits/balance`` — host-facing, `observe`.
* ``POST /v1/admin/credits/grants`` — control-plane, `admin`, excluded from the
  host-facing conformance list, classified with the rest of ``/v1/admin/*``.

The balance reports four terms, because three is wrong the moment an allowance
expires: ``granted = available + held + consumed + forfeited``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection, Engine

from adapt_engine.api.deps import get_engine
from adapt_engine.auth.deps import (
    AuthContext,
    ingest_connection,
    require_scope,
    security_requirement,
)
from adapt_engine.credits import service as credits
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1", tags=["credits"], responses=ERROR_RESPONSES)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        description="Stable per logical operation, never per attempt (INTEGRATION.md §7)",
    ),
]


class GrantOut(BaseModel):
    id: uuid.UUID
    kind: str
    amount: int
    remaining: int
    expires_at: datetime | None


class BalanceOut(BaseModel):
    available: int
    held: int
    consumed: int
    forfeited: int
    granted: int
    grants: list[GrantOut]


class GrantRequest(BaseModel):
    kind: Literal["included", "purchased", "promo"]
    amount: int = Field(ge=1, le=1_000_000)
    expires_at: datetime | None = None
    source_ref: str | None = Field(default=None, max_length=200)
    period_start: date | None = None


class GrantedOut(BaseModel):
    grant_id: uuid.UUID
    kind: str
    amount: int
    expires_at: datetime | None


def _require_key(key: str) -> str:
    if not key.strip():
        raise AdaptError(
            code="payload_invalid",
            message="Idempotency-Key is required on mutating calls",
            status_code=422,
            retryable=False,
        )
    return key


@router.get(
    "/credits/balance",
    response_model=BalanceOut,
    dependencies=[Depends(require_scope("observe"))],
    openapi_extra=security_requirement("observe"),
)
def get_balance(
    auth: Annotated[AuthContext, Depends(require_scope("observe"))],
    conn: Annotated[Connection, Depends(ingest_connection)],
) -> BalanceOut:
    """The balance is a query over grants, never a mutable column."""
    current = credits.balance(conn, auth.tenant_id)
    return BalanceOut(
        available=current.available,
        held=current.held,
        consumed=current.consumed,
        forfeited=current.forfeited,
        granted=current.granted,
        grants=[
            GrantOut(
                id=grant.id,
                kind=grant.kind,
                amount=grant.amount,
                remaining=grant.remaining,
                expires_at=grant.expires_at,
            )
            for grant in current.grants
        ],
    )


@router.post(
    "/admin/credits/grants",
    response_model=GrantedOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def create_grant(
    body: GrantRequest,
    idempotency_key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    engine: Annotated[Engine, Depends(get_engine)],
) -> GrantedOut:
    """Provision credits. Control-plane: a host key never calls this in anger."""
    key = _require_key(idempotency_key)
    _validate_terms(body)

    grant_id = credits.run_idempotent(
        engine,
        auth.tenant_id,
        lambda conn: credits.grant_credits(
            conn,
            tenant_id=auth.tenant_id,
            kind=body.kind,
            amount=body.amount,
            expires_at=body.expires_at,
            source_ref=body.source_ref,
            idempotency_key=key,
            period_start=body.period_start,
        ),
    )
    return GrantedOut(
        grant_id=grant_id,
        kind=body.kind,
        amount=body.amount,
        expires_at=body.expires_at,
    )


def _validate_terms(body: GrantRequest) -> None:
    """The two commercial invariants, checked before the schema has to.

    The columns enforce them too (`included` must expire, `purchased` must
    not); saying so here turns a constraint violation into an answer the caller
    can act on.
    """
    if body.kind == "included" and body.expires_at is None:
        raise AdaptError(
            code="payload_invalid",
            message="an included allowance must have an expires_at",
            status_code=422,
            retryable=False,
        )
    if body.kind == "purchased" and body.expires_at is not None:
        raise AdaptError(
            code="payload_invalid",
            message="paid credits never expire; expires_at must be omitted",
            status_code=422,
            retryable=False,
        )
    if body.expires_at is not None and body.expires_at <= datetime.now(UTC):
        raise AdaptError(
            code="payload_invalid",
            message="expires_at must be in the future",
            status_code=422,
            retryable=False,
        )
