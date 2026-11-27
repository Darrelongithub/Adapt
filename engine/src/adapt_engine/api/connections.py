"""Connections — control-plane, `admin` scope, excluded from host conformance.

INTEGRATION.md §6A: everything under `/v1/admin/*` is the tenant's console
surface. A host integrates against `/v1/observe` and `/v1/decide`; it does not
create its own connections, because a connection is the thing onboarding
provisions *for* it. The scope is `admin` for that reason, and these routes are
not part of the host-facing conformance list.

What is shown once, and only once:

* the **key** — `ak_test_…` at creation, never readable again (M0's rule);
* the **webhook signing secret** — `whsec_…` at creation. A receiver needs it to
  verify; a response that could re-read it would make the delivery log a place
  to steal it from.

Status codes are the contract's, not new ones: a missing connection is the
documented 404, a duplicate
(host, environment) or a change to a revoked connection is `409 conflict`, and
a paused connection's *ingest* is `403 connection_paused` — raised by
`ingest_connection`, not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from adapt_engine.api.deps import get_clock, get_settings
from adapt_engine.auth import keys as key_service
from adapt_engine.auth.deps import (
    AuthContext,
    require_scope,
    security_requirement,
    tenant_connection,
)
from adapt_engine.auth.keys import MAX_EXPIRY_DAYS
from adapt_engine.clock import Clock
from adapt_engine.config import Settings
from adapt_engine.connections import service as connections
from adapt_engine.crypto.store import DataKeyStore, crypto_store
from adapt_engine.db.tenant import commit_before_promise
from adapt_engine.errors import ERROR_RESPONSES, AdaptError
from adapt_engine.webhooks import dispatch

router = APIRouter(prefix="/v1", tags=["connections"], responses=ERROR_RESPONSES)


class ConnectionCreate(BaseModel):
    host_name: str = Field(min_length=3, max_length=64)
    environment: Literal["live", "test"]
    event_quota_monthly: int = Field(gt=0, le=1_000_000_000)
    scopes: list[str] = Field(default_factory=lambda: ["observe"])
    webhook_url: str | None = Field(default=None, max_length=2048)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)


class ConnectionView(BaseModel):
    id: uuid.UUID
    host_name: str
    environment: str
    key_id: uuid.UUID
    webhook_url: str | None
    status: str
    event_quota_monthly: int
    created_at: datetime
    paused_at: datetime | None
    revoked_at: datetime | None


class IssuedKeyOnce(BaseModel):
    """The full key, in the only response that will ever contain it."""

    key_id: uuid.UUID
    prefix: str
    full_key: str
    scopes: list[str]
    env: str
    expires_at: datetime


class ConnectionCreated(BaseModel):
    connection: ConnectionView
    key: IssuedKeyOnce
    webhook_secret: str | None = None


class ConnectionStatusUpdate(BaseModel):
    status: Literal["active", "paused"]


class DeliveryAttempt(BaseModel):
    attempt: int
    status_code: int | None
    error_class: str | None
    duration_ms: int
    attempted_at: datetime


class DeliveryView(BaseModel):
    """One row of the delivery log. No payload, by construction."""

    delivery_id: uuid.UUID
    event_type: str
    webhook_id: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    attempt_log: list[DeliveryAttempt]


def _to_connection(row: connections.ConnectionRow) -> ConnectionView:
    return ConnectionView(
        id=row.id,
        host_name=row.host_name,
        environment=row.environment,
        key_id=row.key_id,
        webhook_url=row.webhook_url,
        status=row.status,
        event_quota_monthly=row.event_quota_monthly,
        created_at=row.created_at,
        paused_at=row.paused_at,
        revoked_at=row.revoked_at,
    )


def _attempt_view(attempt: dispatch.AttemptLogRow) -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt=attempt.attempt,
        status_code=attempt.status_code,
        error_class=attempt.error_class,
        duration_ms=attempt.duration_ms,
        attempted_at=attempt.attempted_at,
    )


def _unknown() -> AdaptError:
    """A connection id this tenant does not have.

    §6 gained `connection_unknown` (404) after increment 1, which is a better
    answer than the `route_unknown` it replaced: the route exists, the
    connection does not, and the integrator can tell that apart from a typo in
    the path.
    """
    return AdaptError(
        code="connection_unknown",
        message="no such connection for this tenant",
        status_code=404,
        retryable=False,
    )


@router.post(
    "/admin/connections",
    response_model=ConnectionCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def create_connection(
    body: ConnectionCreate,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[DataKeyStore, Depends(crypto_store)],
) -> ConnectionCreated:
    """Create a connection and the key it authenticates with, in one transaction.

    The key is issued through the caller's key, so the scope-subset rule applies
    without being re-derived here; the secret is encrypted before the row exists
    and returned exactly once.
    """
    now = clock.now()
    connection_id = uuid.uuid4()
    secret = connections.new_webhook_secret() if body.webhook_url else None
    encrypted_secret = (
        store.encrypt(conn, auth.tenant_id, connection_id, "webhook_secret", secret)
        if secret is not None
        else None
    )

    try:
        issued = key_service.insert_api_key(
            conn,
            tenant_id=auth.tenant_id,
            scopes=body.scopes,
            env=body.environment,
            pepper=settings.key_pepper.get_secret_value(),
            issuer_key_id=auth.key_id,
            expires_in_days=body.expires_in_days,
        )
    except key_service.KeyIssueRefused as exc:
        raise _issue_refused(exc.reason) from exc
    except ValueError as exc:
        raise AdaptError(
            code="payload_invalid", message=str(exc), status_code=422, retryable=False
        ) from exc

    try:
        row = connections.insert_connection(
            conn,
            connection_id=connection_id,
            tenant_id=auth.tenant_id,
            host_name=body.host_name,
            environment=body.environment,
            key_id=issued.key_id,
            webhook_url=body.webhook_url,
            webhook_secret=encrypted_secret,
            event_quota_monthly=body.event_quota_monthly,
            created_by=auth.key_id,
            now=now,
        )
    except IntegrityError as exc:
        # A unique violation is a resource that already exists (409); anything
        # else is a CHECK the body could not satisfy (422). Telling them apart
        # matters: one means "use the connection you have", the other means
        # "fix the request", and a caller cannot guess which from a 409.
        if _is_unique_violation(exc):
            raise AdaptError(
                code="conflict",
                message="this tenant already has a connection for that host and environment",
                status_code=409,
                retryable=False,
            ) from exc
        raise AdaptError(
            code="payload_invalid",
            message=_check_message(exc),
            status_code=422,
            retryable=False,
        ) from exc

    connections.audit(
        conn,
        tenant_id=auth.tenant_id,
        actor_key_id=auth.key_id,
        action="connection_created",
        target=row.id,
        meta={
            "host_name": row.host_name,
            "environment": row.environment,
            "key_id": str(row.key_id),
            "webhook": row.webhook_url is not None,
        },
    )
    # Built from the row *before* the commit below expires it (see
    # `commit_before_promise`): this response carries `key.full_key`, so it must
    # not be sendable before the key is committed.
    created = ConnectionCreated(
        connection=_to_connection(row),
        key=IssuedKeyOnce(
            key_id=issued.key_id,
            prefix=issued.prefix,
            full_key=issued.full_key,
            scopes=list(issued.scopes),
            env=issued.env,
            expires_at=issued.expires_at,
        ),
        webhook_secret=secret,
    )
    commit_before_promise(conn)
    return created


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    sqlstate = getattr(orig, "sqlstate", None)
    return sqlstate == "23505" or constraint.endswith("_unique")


#: The CHECK constraints a caller can trip, and the sentence that helps.
_CHECK_MESSAGES = {
    "connections_webhook_url_shape": "webhook_url must be an absolute https URL",
    "connections_webhook_url_and_secret_agree": "a webhook_url needs a signing secret",
    "connections_host_name_shape": "host_name is 3-64 url-safe characters",
    "connections_environment_known": "environment must be live or test",
    "connections_quota_positive": "event_quota_monthly must be positive",
}


def _check_message(exc: IntegrityError) -> str:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    return _CHECK_MESSAGES.get(constraint, "the request could not be stored as sent")


def _issue_refused(reason: str) -> AdaptError:
    """Scope escalation and admin-minting refusals are `not_entitled`."""
    if reason in {"admin_not_mintable", "scope_escalation", "issuer_not_admin"}:
        return AdaptError(
            code="not_entitled",
            message=f"tier_required: {reason.replace('_', ' ')}",
            status_code=403,
            retryable=False,
        )
    return AdaptError(
        code="payload_invalid", message=reason.replace("_", " "), status_code=422, retryable=False
    )


@router.get(
    "/admin/connections",
    response_model=list[ConnectionView],
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def list_connections(
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> list[ConnectionView]:
    return [_to_connection(row) for row in connections.list_connections(conn)]


@router.get(
    "/admin/connections/{connection_id}",
    response_model=ConnectionView,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def get_connection(
    connection_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> ConnectionView:
    row = connections.get_connection(conn, connection_id)
    if row is None:
        raise _unknown()
    return _to_connection(row)


@router.patch(
    "/admin/connections/{connection_id}",
    response_model=ConnectionView,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionStatusUpdate,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ConnectionView:
    """Pause or resume. Revoking is `DELETE`, because it is not reversible."""
    try:
        row = connections.set_status(
            conn, connection_id=connection_id, status=body.status, now=clock.now()
        )
    except connections.ConnectionStateError as exc:
        if exc.reason == "unknown":
            raise _unknown() from exc
        raise AdaptError(
            code="conflict",
            message="a revoked connection is terminal; create a new connection instead",
            status_code=409,
            retryable=False,
        ) from exc

    connections.audit(
        conn,
        tenant_id=auth.tenant_id,
        actor_key_id=auth.key_id,
        action=f"connection_{'paused' if body.status == 'paused' else 'resumed'}",
        target=row.id,
        meta={"status": row.status},
    )
    view = _to_connection(row)
    # "Paused" is a promise about the *next* request: `ingest_connection` refuses
    # it at the same moment the row says so. Commit before announcing it, or a
    # paused connection ingests for one commit's width (DECISIONS §24.6).
    commit_before_promise(conn)
    return view


@router.delete(
    "/admin/connections/{connection_id}",
    response_model=ConnectionView,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def revoke_connection(
    connection_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ConnectionView:
    """Revoke: the connection stops accepting, and so does its key.

    Both in one transaction, so there is no window where the connection says
    revoked and the key still authenticates. That is why a revoked connection
    answers `401 unauthorized` and not a fourth status code.
    """
    try:
        row = connections.set_status(
            conn, connection_id=connection_id, status="revoked", now=clock.now()
        )
    except connections.ConnectionStateError as exc:
        raise _unknown() from exc

    key_service.revoke_api_key(
        conn, tenant_id=auth.tenant_id, key_id=row.key_id, actor_key_id=auth.key_id
    )
    connections.audit(
        conn,
        tenant_id=auth.tenant_id,
        actor_key_id=auth.key_id,
        action="connection_revoked",
        target=row.id,
        meta={"key_id": str(row.key_id)},
    )
    view = _to_connection(row)
    # The docstring above says there is no window where the connection says
    # revoked and the key still authenticates. That is true of the database; the
    # commit is what makes it true of the response (DECISIONS §24.6).
    commit_before_promise(conn)
    return view


@router.get(
    "/admin/connections/{connection_id}/deliveries",
    response_model=list[DeliveryView],
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def delivery_log(
    connection_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> list[DeliveryView]:
    """The console's delivery log: attempts, status codes, outcomes.

    No request body and no response body — the row does not have a column for
    one. An operator debugging a webhook needs to know *that* it was delivered
    and what the receiver said, not what we told them about a learner.
    """
    if connections.get_connection(conn, connection_id) is None:
        raise _unknown()
    return [
        DeliveryView(
            delivery_id=row.delivery_id,
            event_type=row.event_type,
            webhook_id=row.webhook_id,
            status=row.status,
            attempts=row.attempts,
            last_status_code=row.last_status_code,
            last_error=row.last_error,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
            attempt_log=[_attempt_view(attempt) for attempt in row.attempt_log],
        )
        for row in dispatch.delivery_log(conn, connection_id)
    ]
