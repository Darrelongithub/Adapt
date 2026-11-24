"""API keys: issue, revoke, resolve, and the debounced `last_used_at` touch.

Format ``ak_<live|test>_<32 base32>``. Stored as the first 12 characters
(``ak_live_abcd``, the prefix) plus ``HMAC-SHA256(pepper, full_key)``. Not
bcrypt: authentication needs an O(1) lookup by prefix, and the pepper keeps a
database dump useless without the secret.

A key is shown once, at issue, and is never retrievable afterwards — the hash
is not reversible and the full key is not stored anywhere.

**`adapt_app` holds no privilege on `api_keys`.** Not SELECT, not INSERT, not
UPDATE. Every access is one of four SECURITY DEFINER functions owned by
`adapt_owner` (migration 0004):

    adapt_resolve_api_key(text)                   read one row by prefix
    adapt_touch_api_key(uuid)                     debounced last_used_at
    adapt_issue_api_key(...)                      insert, with the mint rules
    adapt_revoke_api_key(uuid, uuid)              set revoked_at, tenant-addressed

RLS keeps tenants apart; grants keep key material from everyone. This module is
the only caller of those functions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.errors import AdaptError

KEY_ENV_LIVE: Final = "live"
KEY_ENV_TEST: Final = "test"
KEY_ENVS: Final = (KEY_ENV_LIVE, KEY_ENV_TEST)

SCOPES: Final = ("observe", "decide", "read_model", "admin")

#: ``ak_live_`` is 8 characters; 4 more makes a 12-character prefix. Twelve is
#: enough to look up, short enough that the remaining 28 characters are the
#: secret.
PREFIX_LENGTH: Final = 12
SECRET_LENGTH: Final = 32

#: Every key expires. 90 days for everything, unless the issuer says otherwise;
#: `admin` keys are held to the same default and may not exceed the cap.
DEFAULT_EXPIRY_DAYS: Final = 90
MAX_EXPIRY_DAYS: Final = 365

#: ``last_used_at`` is written at most this often per key.
LAST_USED_DEBOUNCE_SECONDS: Final = 60

#: SQLSTATEs the functions raise, mapped to reasons the caller can act on.
_SQLSTATE_INSUFFICIENT_PRIVILEGE: Final = "42501"
_SQLSTATE_INVALID_PARAMETER: Final = "22023"


class KeyFormatError(ValueError):
    """The presented string is not shaped like an API key."""


class KeyIssueRefused(Exception):
    """The database refused to create the key.

    The rules live in ``adapt_issue_api_key`` so there is one implementation of
    "an admin key may only mint a subset of itself, and never admin".
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class IssuedKey:
    """The one moment the full key exists outside a client."""

    key_id: uuid.UUID
    tenant_id: uuid.UUID
    prefix: str
    full_key: str
    scopes: tuple[str, ...]
    env: str
    expires_at: datetime


@dataclass(frozen=True)
class ResolvedKey:
    """What the resolver function returns.

    No hash: the function is given the client-side digest and answers a row
    only on an exact match, so the runtime never holds a stored hash next to
    the pepper that makes it useful.
    """

    key_id: uuid.UUID
    tenant_id: uuid.UUID
    scopes: tuple[str, ...]
    revoked: bool
    expires_at: datetime


def generate_key(env: str = KEY_ENV_TEST) -> tuple[str, str]:
    """Return ``(full_key, prefix)``. The full key is never stored."""
    if env not in KEY_ENVS:
        raise KeyFormatError(f"env must be one of {KEY_ENVS}")
    secret = _base32_secret(SECRET_LENGTH)
    full_key = f"ak_{env}_{secret}"
    return full_key, full_key[:PREFIX_LENGTH]


def _base32_secret(length: int) -> str:
    """Lowercase RFC 4648 base32: a-z and 2-7, no padding."""
    raw = secrets.token_bytes(length * 5 // 8 + 1)
    encoded = base64.b32encode(raw).decode("ascii").lower().rstrip("=")
    return encoded[:length]


def key_env(full_key_or_prefix: str) -> str | None:
    """Read the environment out of a key or prefix, or None if malformed."""
    parts = full_key_or_prefix.split("_")
    if len(parts) < 3 or parts[0] != "ak":
        return None
    if parts[1] not in KEY_ENVS:
        return None
    return parts[1]


def looks_like_key(presented: str) -> bool:
    """Whether a presented string has the documented shape.

    Shape only: this says nothing about whether the key exists. A malformed
    string and an unknown key fail identically to the caller.
    """
    env = key_env(presented)
    if env is None:
        return False
    _, _, secret = presented.partition(f"ak_{env}_")
    if len(secret) != SECRET_LENGTH:
        return False
    return all(char in "abcdefghijklmnopqrstuvwxyz234567" for char in secret)


def hash_key(pepper: str, full_key: str) -> bytes:
    """HMAC-SHA256(pepper, full_key). The pepper never leaves configuration."""
    return hmac.new(pepper.encode("utf-8"), full_key.encode("utf-8"), hashlib.sha256).digest()


def validate_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValueError(f"unknown scope(s): {', '.join(unknown)}; known: {', '.join(SCOPES)}")
    if not scopes:
        raise ValueError("a key needs at least one scope")
    return tuple(sorted(set(scopes)))


def expiry_from_days(days: int | None) -> datetime:
    """``expires_at`` for a new key. Every key expires (migration 0004)."""
    horizon = DEFAULT_EXPIRY_DAYS if days is None else days
    if horizon < 1 or horizon > MAX_EXPIRY_DAYS:
        raise ValueError(f"expiry must be between 1 and {MAX_EXPIRY_DAYS} days")
    return datetime.now(UTC) + timedelta(days=horizon)


def _reason_from_error(exc: DBAPIError) -> str | None:
    """The function's RAISE message, or None if this is a different error."""
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate not in (_SQLSTATE_INSUFFICIENT_PRIVILEGE, _SQLSTATE_INVALID_PARAMETER):
        return None
    diag = getattr(orig, "diag", None)
    message = getattr(diag, "message_primary", None)
    return message or "refused"


def insert_api_key(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    scopes: list[str] | tuple[str, ...],
    env: str,
    pepper: str,
    issuer_key_id: uuid.UUID | None = None,
    expires_in_days: int | None = None,
) -> IssuedKey:
    """Insert a key through ``adapt_issue_api_key``.

    ``issuer_key_id=None`` is the provisioning path: only a session connected
    as ``adapt_owner`` may use it, which the function checks against
    ``session_user`` rather than trusting the caller.
    """
    validated = validate_scopes(scopes)
    expires_at = expiry_from_days(expires_in_days)
    full_key, prefix = generate_key(env)
    try:
        key_id = conn.execute(
            text(
                "SELECT adapt_issue_api_key("
                ":tenant_id, :prefix, :hash, :scopes, :expires_at, :issuer_key_id)"
            ),
            {
                "tenant_id": tenant_id,
                "prefix": prefix,
                "hash": hash_key(pepper, full_key),
                "scopes": list(validated),
                "expires_at": expires_at,
                "issuer_key_id": issuer_key_id,
            },
        ).scalar_one()
    except DBAPIError as exc:
        reason = _reason_from_error(exc)
        if reason is None:
            raise
        raise KeyIssueRefused(reason) from exc

    _write_audit(
        conn,
        tenant_id=tenant_id,
        actor=_actor(issuer_key_id),
        action="api_key_issued",
        subject=str(key_id),
        meta={"scopes": list(validated), "expires_at": expires_at.isoformat()},
    )
    return IssuedKey(
        key_id=key_id,
        tenant_id=tenant_id,
        prefix=prefix,
        full_key=full_key,
        scopes=validated,
        env=env,
        expires_at=expires_at,
    )


def issue_api_key(
    engine: Engine,
    *,
    tenant_id: uuid.UUID,
    scopes: list[str] | tuple[str, ...],
    env: str,
    pepper: str,
    issuer_key_id: uuid.UUID | None = None,
    expires_in_days: int | None = None,
) -> IssuedKey:
    """Create a key for a tenant and return the full key exactly once."""
    with tenant_transaction(engine, tenant_id) as conn:
        return insert_api_key(
            conn,
            tenant_id=tenant_id,
            scopes=scopes,
            env=env,
            pepper=pepper,
            issuer_key_id=issuer_key_id,
            expires_in_days=expires_in_days,
        )


def revoke_api_key(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_key_id: uuid.UUID | None = None,
) -> bool:
    """Revoke immediately: the next resolution sees ``revoked_at``."""
    revoked = conn.execute(
        text("SELECT adapt_revoke_api_key(:tenant_id, :key_id)"),
        {"tenant_id": tenant_id, "key_id": key_id},
    ).scalar_one()
    if revoked:
        _write_audit(
            conn,
            tenant_id=tenant_id,
            actor=_actor(actor_key_id),
            action="api_key_revoked",
            subject=str(key_id),
            meta=None,
        )
    return bool(revoked)


def set_key_expiry(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    expires_at: datetime,
    actor_key_id: uuid.UUID | None = None,
) -> bool:
    """Shorten (or extend) a key's life — as an audited operation.

    ``adapt_app`` holds no UPDATE on ``api_keys``, so there is no raw path from
    a request connection; this function plus the audit row is the only way the
    runtime can change an expiry, which is why it is not a quietly-audited
    UPDATE.
    """
    try:
        changed = conn.execute(
            text("SELECT adapt_set_key_expiry(:tenant_id, :key_id, :expires_at)"),
            {"tenant_id": tenant_id, "key_id": key_id, "expires_at": expires_at},
        ).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _SQLSTATE_INVALID_PARAMETER:
            raise AdaptError(
                code="payload_invalid",
                message="expires_at must be in the future",
                status_code=422,
                retryable=False,
            ) from exc
        raise
    if changed:
        _write_audit(
            conn,
            tenant_id=tenant_id,
            actor=_actor(actor_key_id),
            action="api_key_expiry_set",
            subject=str(key_id),
            meta={"expires_at": expires_at.isoformat()},
        )
    return bool(changed)


def resolve_api_key(engine: Engine, prefix: str, presented_hash: bytes) -> ResolvedKey | None:
    """Look a key up by exact ``(prefix, digest)`` through the definer function.

    The digest is computed here and compared *inside the database*; the
    function returns a row only on an exact match, and returns no hash. No
    tenant context is set — that is the point: the key is what says which
    tenant is calling.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT key_id, tenant_id, scopes, revoked_at, expires_at "
                "FROM adapt_resolve_api_key(:prefix, :hash)"
            ),
            {"prefix": prefix, "hash": presented_hash},
        ).one_or_none()
    if row is None:
        return None
    return ResolvedKey(
        key_id=row.key_id,
        tenant_id=row.tenant_id,
        scopes=tuple(row.scopes),
        revoked=row.revoked_at is not None,
        expires_at=row.expires_at,
    )


def touch_api_key(engine: Engine, key_id: uuid.UUID) -> bool:
    """Record use of one key, debounced in SQL to once per minute.

    Called from the flush loop, never from the request path: the request only
    notes the key id in memory (see ``auth/touch.py``). The ``WHERE`` clause is
    the concurrency guard, so two flushers cannot both write inside a minute.
    """
    with engine.begin() as conn:
        return bool(
            conn.execute(
                text("SELECT adapt_touch_api_key(:key_id, :interval)"),
                {"key_id": key_id, "interval": LAST_USED_DEBOUNCE_SECONDS},
            ).scalar_one()
        )


def note_auth_failure(
    engine: Engine,
    *,
    ip_hash: str,
    window_start: datetime,
    count: int,
    aggregated: bool,
) -> None:
    """Audit one failed authentication. No key, no prefix, no address."""
    with engine.begin() as conn:
        conn.execute(
            text("SELECT adapt_note_auth_failure(:ip_hash, :window_start, :count, :aggregated)"),
            {
                "ip_hash": ip_hash,
                "window_start": window_start,
                "count": count,
                "aggregated": aggregated,
            },
        )


def _actor(issuer_key_id: uuid.UUID | None) -> str:
    """The audit actor: the issuing key id, or 'provisioning'. Never a key."""
    return f"key:{issuer_key_id}" if issuer_key_id is not None else "provisioning"


def _write_audit(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    actor: str,
    action: str,
    subject: str | None,
    meta: dict[str, object] | None,
) -> None:
    """Tenant-scoped audit row, written as ``adapt_app`` under RLS.

    The caller must already be inside tenant context: the policy's WITH CHECK
    is what proves the row belongs to the tenant the request authenticated as.
    """
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor, action, target, metadata) "
            "VALUES (:tenant_id, :actor, :action, :target, CAST(:metadata AS jsonb))"
        ),
        {
            "tenant_id": tenant_id,
            "actor": actor,
            "action": action,
            "target": subject,
            "metadata": "{}" if meta is None else _json(meta),
        },
    )


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True)
