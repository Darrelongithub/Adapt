"""API-key administration and identity.

Issuing returns the full key exactly once. There is no route that reads a key
back, and none that lists hashes: a tenant can see its own key prefixes,
scopes and timestamps, and nothing that could be replayed.

Revocation is a write to the row, so the next resolution — which goes through
the SECURITY DEFINER function every request, with no cache in front of it —
sees it immediately.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from adapt_engine.api.deps import get_settings
from adapt_engine.auth import keys as key_service
from adapt_engine.auth.deps import (
    AuthContext,
    require_any_scope,
    require_scope,
    security_requirement,
    security_requirement_any,
    tenant_connection,
)
from adapt_engine.auth.keys import SCOPES
from adapt_engine.config import Settings
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1", tags=["keys"], responses=ERROR_RESPONSES)


class WhoAmI(BaseModel):
    tenant_id: uuid.UUID
    key_id: uuid.UUID
    scopes: list[str]


class KeyIssueRequest(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["observe"])
    env: str = Field(default="test")


class KeyIssued(BaseModel):
    """The only response that ever contains a full key."""

    key_id: uuid.UUID
    prefix: str
    full_key: str
    scopes: list[str]
    env: str


class KeyRevoked(BaseModel):
    key_id: uuid.UUID
    revoked: bool


@router.get(
    "/whoami",
    response_model=WhoAmI,
    dependencies=[Depends(require_any_scope(*SCOPES))],
    openapi_extra=security_requirement_any(SCOPES),
)
def whoami(auth: Annotated[AuthContext, Depends(require_any_scope(*SCOPES))]) -> WhoAmI:
    """Identity of the calling key. No key material in the response.

    Any valid key may ask who it is: identifying yourself is not a capability,
    and requiring `observe` here would mean an admin-only key could not ask.
    """
    return WhoAmI(
        tenant_id=auth.tenant_id,
        key_id=auth.key_id,
        scopes=list(auth.scopes),
    )


@router.post(
    "/admin/keys",
    response_model=KeyIssued,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def issue_key(
    body: KeyIssueRequest,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KeyIssued:
    """Issue another key for the caller's tenant. Shown once."""
    try:
        issued = key_service.insert_api_key(
            conn,
            tenant_id=auth.tenant_id,
            scopes=body.scopes,
            env=body.env,
            pepper=settings.key_pepper.get_secret_value(),
        )
    except ValueError as exc:
        raise AdaptError(
            code="payload_invalid",
            message=str(exc),
            status_code=422,
            retryable=False,
        ) from exc
    return KeyIssued(
        key_id=issued.key_id,
        prefix=issued.prefix,
        full_key=issued.full_key,
        scopes=list(issued.scopes),
        env=issued.env,
    )


@router.post(
    "/admin/keys/{key_id}/revoke",
    response_model=KeyRevoked,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def revoke_key(
    key_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> KeyRevoked:
    result = conn.execute(
        text("UPDATE api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"),
        {"id": key_id},
    )
    if result.rowcount == 0:
        # Already revoked or not this tenant's key. Both are "not found" from
        # the caller's point of view; RLS means it cannot tell which.
        exists = conn.execute(
            text("SELECT 1 FROM api_keys WHERE id = :id"), {"id": key_id}
        ).scalar_one_or_none()
        if exists is None:
            raise AdaptError(
                code="route_unknown",
                message="no such key for this tenant",
                status_code=404,
                retryable=False,
            )
    return KeyRevoked(key_id=key_id, revoked=True)
