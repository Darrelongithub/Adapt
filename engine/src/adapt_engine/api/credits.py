"""Credit routes: the balance, and the three moves.

`Idempotency-Key` is a header on every mutating call (INTEGRATION.md §7), not a
body field: the SDK derives it from the logical operation, and the same key
replayed must return the original result rather than charge twice.

These are host-facing billing routes. Reading the balance needs `observe`;
moving credits needs `admin`, because a reservation is a commitment to spend.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection, Engine

from adapt_engine.api.deps import get_engine
from adapt_engine.auth.deps import (
    AuthContext,
    require_scope,
    security_requirement,
    tenant_connection,
)
from adapt_engine.credits import service as credits
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1/credits", tags=["credits"], responses=ERROR_RESPONSES)

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
    granted: int
    grants: list[GrantOut]


class ReserveRequest(BaseModel):
    amount: int = Field(ge=1, le=10_000)
    operation_ref: str = Field(min_length=1, max_length=200)


class ReservationOut(BaseModel):
    """Deliberately excludes the live balance: a replay must be byte-identical."""

    reservation_id: uuid.UUID
    amount: int
    state: str
    operation_ref: str


class SettleRequest(BaseModel):
    amount: int = Field(ge=1, le=10_000)


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
    "/balance",
    response_model=BalanceOut,
    dependencies=[Depends(require_scope("observe"))],
    openapi_extra=security_requirement("observe"),
)
def get_balance(
    auth: Annotated[AuthContext, Depends(require_scope("observe"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> BalanceOut:
    """The balance is a query over grants, never a mutable column."""
    current = credits.balance(conn, auth.tenant_id)
    return BalanceOut(
        available=current.available,
        held=current.held,
        consumed=current.consumed,
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
    "/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def create_reservation(
    body: ReserveRequest,
    idempotency_key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    engine: Annotated[Engine, Depends(get_engine)],
) -> ReservationOut:
    """Hold credits for an operation. 402 when the balance cannot cover it."""
    key = _require_key(idempotency_key)
    reservation = credits.run_idempotent(
        engine,
        auth.tenant_id,
        lambda conn: credits.reserve(
            conn,
            tenant_id=auth.tenant_id,
            amount=body.amount,
            operation_ref=body.operation_ref,
            idempotency_key=key,
        ),
    )
    return ReservationOut(
        reservation_id=reservation.id,
        amount=reservation.amount,
        state=reservation.state,
        operation_ref=reservation.operation_ref,
    )


@router.post(
    "/reservations/{reservation_id}/settle",
    response_model=ReservationOut,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def settle_reservation(
    reservation_id: uuid.UUID,
    body: SettleRequest,
    idempotency_key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    engine: Annotated[Engine, Depends(get_engine)],
) -> ReservationOut:
    """Consume up to the held amount. Over-settlement is refused, not clamped."""
    key = _require_key(idempotency_key)
    reservation = credits.run_idempotent(
        engine,
        auth.tenant_id,
        lambda conn: credits.settle(
            conn,
            tenant_id=auth.tenant_id,
            reservation_id=reservation_id,
            amount=body.amount,
            idempotency_key=key,
        ),
    )
    return ReservationOut(
        reservation_id=reservation.id,
        amount=reservation.amount,
        state=reservation.state,
        operation_ref=reservation.operation_ref,
    )


@router.post(
    "/reservations/{reservation_id}/release",
    response_model=ReservationOut,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def release_reservation(
    reservation_id: uuid.UUID,
    idempotency_key: IdempotencyKey,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    engine: Annotated[Engine, Depends(get_engine)],
) -> ReservationOut:
    """Return the held credits. Idempotent, and never debits."""
    key = _require_key(idempotency_key)
    reservation = credits.run_idempotent(
        engine,
        auth.tenant_id,
        lambda conn: credits.release(
            conn,
            tenant_id=auth.tenant_id,
            reservation_id=reservation_id,
            idempotency_key=key,
        ),
    )
    return ReservationOut(
        reservation_id=reservation.id,
        amount=reservation.amount,
        state=reservation.state,
        operation_ref=reservation.operation_ref,
    )
