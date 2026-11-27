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

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, NoReturn

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Connection

from adapt_engine.auth import keys
from adapt_engine.auth.audit import AuthFailureAuditor, ip_hash
from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.auth.touch import LastUsedRecorder
from adapt_engine.config import Settings
from adapt_engine.connections import service as connections
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


#: The lowest tier whose contract includes each scope (ADR-002, Decision 2).
#: Every scope that exists today is Core; `probe` becomes "plus" and the deep
#: capabilities "deep" when their routes land — the map is the single place
#: that changes, and `details.tier_required` follows from it.
TIER_FOR_SCOPE: dict[str, str] = {
    "observe": "core",
    "decide": "core",
    "read_model": "core",
    "admin": "core",
}


def tier_required_for(*scopes: str) -> str:
    """The lowest tier that unlocks at least one of ``scopes``."""
    order = {"core": 0, "plus": 1, "deep": 2}
    tiers = [TIER_FOR_SCOPE.get(scope, "core") for scope in scopes]
    return min(tiers, key=lambda tier: order[tier]) if tiers else "core"


@dataclass(frozen=True)
class AuthContext:
    """Who is calling. Never carries the key or its hash."""

    tenant_id: uuid.UUID
    key_id: uuid.UUID
    scopes: tuple[str, ...]
    key_env: str
    expires_at: datetime

    def require(self, scope: str) -> None:
        """Scope failures are 403 not_entitled, per INTEGRATION.md §6."""
        if scope not in self.scopes:
            raise AdaptError(
                code="not_entitled",
                message=(f"this key needs the {scope} scope (tier: {tier_required_for(scope)})"),
                status_code=403,
                retryable=False,
                details={"tier_required": tier_required_for(scope)},
            )


def expected_key_env(settings: Settings) -> str:
    """Production accepts live keys; every other environment accepts test keys."""
    return keys.KEY_ENV_LIVE if settings.env == "production" else keys.KEY_ENV_TEST


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _unauthorized() -> AdaptError:
    return AdaptError(
        code="unauthorized",
        message=UNAUTHORIZED_MESSAGE,
        status_code=401,
        retryable=False,
    )


def _rate_limited(limiter: FailureLimiter, source: str) -> AdaptError:
    return AdaptError(
        code="rate_limited",
        message="too many failed authentication attempts",
        status_code=429,
        retryable=True,
        headers={"Retry-After": str(limiter.retry_after_seconds(source))},
    )


def resolve_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> AuthContext:
    """Resolve the caller from the bearer token, or fail as unauthorized.

    The order is the point. Validation happens first, failures are counted
    afterwards, and the rate-limit answer is only reachable from a failure —
    so a valid key is never refused because other tenants' bad guesses share
    its egress address.
    """
    settings: Settings = request.app.state.settings
    engine = request.app.state.engine
    limiter: FailureLimiter = request.app.state.auth_limiter
    auditor: AuthFailureAuditor = request.app.state.auth_auditor
    recorder: LastUsedRecorder = request.app.state.last_used
    pepper = settings.key_pepper.get_secret_value()
    source = ip_hash(pepper, _client_ip(request))

    def failed() -> NoReturn:
        """Count one failure, audit it, and answer 401 — or 429 once blocked.

        The budget is checked *before* this failure is counted, so
        `max_failures` is a real allowance: N failures answer 401 and the
        next one answers 429. Both branches count and audit, because the
        window has to keep moving for the block to expire.
        """
        blocked = limiter.is_blocked(source)
        limiter.record_failure(source)
        auditor.record(source)
        if blocked:
            raise _rate_limited(limiter, source)
        raise _unauthorized()

    if credentials is None or not credentials.credentials:
        failed()

    presented = credentials.credentials.strip()
    if not keys.looks_like_key(presented):
        failed()

    # Always computed, so a malformed-but-shaped key costs the same as a real
    # one that fails to match.
    computed = keys.hash_key(pepper, presented)

    presented_env = keys.key_env(presented)
    if presented_env != expected_key_env(settings):
        failed()

    # The digest goes to the database, which answers a row only on an exact
    # match. Nothing that could be replayed — and no stored hash — comes back.
    resolved = keys.resolve_api_key(engine, presented[: keys.PREFIX_LENGTH], computed)

    if resolved is None or resolved.revoked or _expired(resolved):
        failed()

    # Narrowed by the guard above: failed() never returns.
    assert resolved is not None and presented_env is not None

    # In memory only. The flush loop writes `last_used_at` off the request
    # path, debounced in SQL; see auth/touch.py and DECISIONS.md §5.
    recorder.note(resolved.key_id)

    return AuthContext(
        tenant_id=resolved.tenant_id,
        key_id=resolved.key_id,
        scopes=resolved.scopes,
        key_env=presented_env,
        expires_at=resolved.expires_at,
    )


def _expired(resolved: keys.ResolvedKey) -> bool:
    """An expired key is a wrong key: same status, same body, same timing."""
    expires_at = resolved.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


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


def ingest_connection(
    auth: Annotated[AuthContext, Depends(resolve_auth)],
    conn: Annotated[Connection, Depends(tenant_connection)],
) -> Connection:
    """A tenant connection whose *connection* is allowed to serve data.

    A paused connection keeps its key — pausing stops ingest and keeps data —
    so the key still authenticates and this is where the refusal happens. It is
    an explicit ``403 connection_paused``: the integrator has to be able to tell
    "someone paused this on purpose" from ``not_entitled`` ("buy something") and
    from ``unauthorized`` ("your key is wrong"), because the fix is different in
    each case.

    Admin routes deliberately do *not* use this. The console has to be able to
    resume a paused connection, and a key that is paused for ingest but blocked
    from the console is a key that cannot fix its own mistake.

    A key with no connection (the onboarding/console key) is never paused: it is
    not a host app, so there is nothing to pause.
    """
    status = connections.status_for_key(conn, auth.key_id)
    if status == "paused":
        raise AdaptError(
            code="connection_paused",
            message="this connection is paused; resume it from the console to continue",
            status_code=403,
            retryable=True,
        )
    return conn


def require_any_scope(*scopes: str) -> Any:
    """A dependency satisfied by at least one of ``scopes``."""

    def _dependency(auth: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
        if not set(auth.scopes) & set(scopes):
            tier = tier_required_for(*scopes)
            raise AdaptError(
                code="not_entitled",
                message=(f"this key needs one of these scopes: {', '.join(scopes)} (tier: {tier})"),
                status_code=403,
                retryable=False,
                details={"tier_required": tier},
            )
        return auth

    return _dependency


def require_scope(scope: str) -> Any:
    """A dependency that enforces one scope on the resolved caller."""

    def _dependency(auth: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
        auth.require(scope)
        return auth

    return _dependency
