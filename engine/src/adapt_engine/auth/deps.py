"""Authentication dependencies.

One failure shape: every way authentication can fail raises the same
``unauthorized`` error with the same message and the same amount of work, so a
response cannot be used to test whether a prefix exists, whether a key is
revoked, or which environment it was issued for.

Order of operations matters for that:

1. the header is parsed (no database);
2. the HMAC of the presented key is computed (no database) — always, including
   when the prefix will not be found;
3. the prefix is looked up through the SECURITY DEFINER function;
4. a constant-time comparison runs against the stored hash, or against a dummy
   hash when nothing was found.

Every path therefore performs one HMAC, one lookup and one comparison.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Connection

from adapt_engine.auth import keys
from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.config import Settings
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.errors import AdaptError

#: Documented in OpenAPI as the bearer scheme every /v1 route uses.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="APIKeyAuth",
    description="Adapt API key: `Authorization: Bearer ak_live_...` or `ak_test_...`",
)


def security_requirement_any(scopes: tuple[str, ...]) -> dict[str, list[dict[str, list[str]]]]:
    """An OpenAPI ``security`` block satisfied by *any* of ``scopes``.

    Separate requirement objects mean OR in OpenAPI, so this documents "one of
    these scopes is enough" honestly: identifying yourself does not require a
    particular capability.
    """
    return {"security": [{bearer_scheme.scheme_name: [scope]} for scope in scopes]}


def security_requirement(scope: str) -> dict[str, list[dict[str, list[str]]]]:
    """The OpenAPI ``security`` block for a route needing ``scope``.

    Passed as ``openapi_extra`` at the route definition so the contract names
    the scope each route needs, rather than only that auth is required.
    """
    return {"security": [{"APIKeyAuth": [scope]}]}

UNAUTHORIZED_MESSAGE = "Invalid or missing API key"


@dataclass(frozen=True)
class AuthContext:
    """Who is calling. Never carries the key or its hash."""

    tenant_id: uuid.UUID
    key_id: uuid.UUID
    scopes: tuple[str, ...]
    key_env: str

    def require(self, scope: str) -> None:
        """Scope failures are 403 not_entitled, per INTEGRATION.md §6."""
        if scope not in self.scopes:
            raise AdaptError(
                code="not_entitled",
                message=f"tier_required: a key with the {scope} scope",
                status_code=403,
                retryable=False,
            )


def _unauthorized() -> AdaptError:
    return AdaptError(
        code="unauthorized",
        message=UNAUTHORIZED_MESSAGE,
        status_code=401,
        retryable=False,
    )


def expected_key_env(settings: Settings) -> str:
    """Production accepts live keys; every other environment accepts test keys."""
    return keys.KEY_ENV_LIVE if settings.env == "production" else keys.KEY_ENV_TEST


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _fail(request: Request, limiter: FailureLimiter) -> NoReturn:
    limiter.record_failure(_client_ip(request))
    raise _unauthorized()


def resolve_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> AuthContext:
    """Resolve the caller from the bearer token, or fail as unauthorized."""
    settings: Settings = request.app.state.settings
    engine = request.app.state.engine
    limiter: FailureLimiter = request.app.state.auth_limiter
    client = _client_ip(request)

    if limiter.is_blocked(client):
        raise AdaptError(
            code="rate_limited",
            message="too many failed authentication attempts",
            status_code=429,
            retryable=True,
            headers={"Retry-After": str(limiter.retry_after_seconds(client))},
        )

    if credentials is None or not credentials.credentials:
        _fail(request, limiter)

    presented = credentials.credentials.strip()
    if not keys.looks_like_key(presented):
        _fail(request, limiter)

    # Always computed, so a malformed-but-shaped key costs the same as a real
    # one that fails to match.
    pepper = settings.key_pepper.get_secret_value()
    computed = keys.hash_key(pepper, presented)

    presented_env = keys.key_env(presented)
    if presented_env != expected_key_env(settings):
        _fail(request, limiter)

    resolved = keys.resolve_api_key(engine, presented[: keys.PREFIX_LENGTH])
    stored = resolved.stored_hash if resolved is not None else keys.DUMMY_HASH
    matched = keys.hashes_match(stored, computed)

    if resolved is None or not matched or resolved.revoked:
        _fail(request, limiter)

    # Narrowed by the guard above: _fail never returns.
    assert resolved is not None and presented_env is not None

    # Bounded and debounced in SQL (at most one row write per key per
    # minute), in its own short transaction. A failure here must not fail a
    # request that already authenticated, nor change what the caller sees.
    with contextlib.suppress(Exception):
        keys.touch_last_used(engine, tenant_id=resolved.tenant_id, key_id=resolved.key_id)

    return AuthContext(
        tenant_id=resolved.tenant_id,
        key_id=resolved.key_id,
        scopes=resolved.scopes,
        key_env=presented_env,
    )


def tenant_connection(
    request: Request,
    auth: Annotated[AuthContext, Depends(resolve_auth)],
) -> Iterator[Connection]:
    """A connection inside a transaction scoped to the authenticated tenant.

    This is the first place a request crosses into tenant-scoped data, and it
    is the only place a request sets ``adapt.tenant_id``.
    """
    engine = request.app.state.engine
    with tenant_transaction(engine, auth.tenant_id) as conn:
        yield conn


def require_any_scope(*scopes: str) -> Any:
    """A dependency satisfied by at least one of ``scopes``."""

    def _dependency(auth: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
        if not set(auth.scopes) & set(scopes):
            raise AdaptError(
                code="not_entitled",
                message=f"tier_required: a key with one of {', '.join(scopes)}",
                status_code=403,
                retryable=False,
            )
        return auth

    return _dependency


def require_scope(scope: str) -> Any:
    """A dependency that enforces one scope on the resolved caller."""

    def _dependency(auth: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
        auth.require(scope)
        return auth

    return _dependency
