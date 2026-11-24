"""API-key administration and identity — the control plane.

`/v1/admin/*` is the console's surface, not the host's: it requires the `admin`
scope, and it is excluded from the host-facing conformance list in
INTEGRATION.md §11. Hosts receive their keys at onboarding; they do not mint
them.

Issuing returns the full key exactly once. There is no route that reads a key
back, and none that lists hashes. Revocation writes the row, so the next
resolution — which goes through the SECURITY DEFINER function every request,
with no cache in front of it — sees it immediately.

An `admin` key may issue only keys whose scopes are a subset of its own, and
may never mint another `admin` key. That rule lives in `adapt_issue_api_key`,
so there is one implementation of it, and the handler translates the refusal
into `not_entitled` rather than re-deriving it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
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
from adapt_engine.auth.keys import MAX_EXPIRY_DAYS, SCOPES
from adapt_engine.config import Settings
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1", tags=["keys"], responses=ERROR_RESPONSES)

#: The refusal reasons the database raises, and how they are reported.
_ESCALATION_REASONS = {
    "admin_not_mintable": "admin keys are provisioned at onboarding, never minted over the API",
    "scope_escalation": "a key may only issue keys whose scopes are a subset of its own",
    "issuer_not_admin": "only an admin key may issue keys",
    "issuer_unknown": "the issuing key is not valid for this tenant",
    "issuer_required": "no issuer; provisioning requires the owner role",
}
_PAYLOAD_REASONS = {
    "scopes_required": "a key needs at least one scope",
    "expires_at_required": "expires_at must be in the future",
}


class WhoAmI(BaseModel):
    tenant_id: uuid.UUID
    key_id: uuid.UUID
    scopes: list[str]
    expires_at: datetime


class KeyIssueRequest(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["observe"])
    env: str = Field(default="test")
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)


class KeyIssued(BaseModel):
    """The only response that ever contains a full key."""

    key_id: uuid.UUID
    prefix: str
    full_key: str
    scopes: list[str]
    env: str
    expires_at: datetime


class KeyRevoked(BaseModel):
    key_id: uuid.UUID
    revoked: bool


class KeyExpiryRequest(BaseModel):
    expires_at: datetime


class KeyExpirySet(BaseModel):
    key_id: uuid.UUID
    expires_at: datetime


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
        expires_at=auth.expires_at,
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
    """Issue another key for the caller's tenant. Shown once, then gone."""
    try:
        issued = key_service.insert_api_key(
            conn,
            tenant_id=auth.tenant_id,
            scopes=body.scopes,
            env=body.env,
            pepper=settings.key_pepper.get_secret_value(),
            issuer_key_id=auth.key_id,
            expires_in_days=body.expires_in_days,
        )
    except key_service.KeyIssueRefused as exc:
        if exc.reason in _ESCALATION_REASONS:
            raise AdaptError(
                code="not_entitled",
                message=f"tier_required: {_ESCALATION_REASONS[exc.reason]}",
                status_code=403,
                retryable=False,
            ) from exc
        raise AdaptError(
            code="payload_invalid",
            message=_PAYLOAD_REASONS.get(exc.reason, exc.reason),
            status_code=422,
            retryable=False,
        ) from exc
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
        expires_at=issued.expires_at,
    )


@router.post(
    "/admin/keys/{key_id}/expiry",
    response_model=KeyExpirySet,
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
)
def set_key_expiry(
    key_id: uuid.UUID,
    body: KeyExpiryRequest,
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> KeyExpirySet:
    """Shorten (or extend) a key's life — as an audited operation.

    The app role holds no UPDATE on ``api_keys``, so this is the only path a
    request can take, and it writes an audit row naming the key id. A raw
    ``UPDATE`` from a request connection would fail on privileges, not on
    review.
    """
    changed = key_service.set_key_expiry(
        conn,
        tenant_id=auth.tenant_id,
        key_id=key_id,
        expires_at=body.expires_at,
        actor_key_id=auth.key_id,
    )
    if not changed:
        raise AdaptError(
            code="route_unknown",
            message="no such key for this tenant, or it is revoked",
            status_code=404,
            retryable=False,
        )
    return KeyExpirySet(key_id=key_id, expires_at=body.expires_at)


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
    """Revoke immediately. Idempotent: the key is unusable afterwards.

    A key that is already revoked, expired, or was never this tenant's is not
    distinguishable from here — the app role holds no privilege on ``api_keys``
    and the revoke function is tenant-addressed. That is deliberate: an
    "unknown vs already revoked" answer would be an oracle.
    """
    key_service.revoke_api_key(
        conn, tenant_id=auth.tenant_id, key_id=key_id, actor_key_id=auth.key_id
    )
    return KeyRevoked(key_id=key_id, revoked=True)
