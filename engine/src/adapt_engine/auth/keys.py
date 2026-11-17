"""API keys: issue, revoke, resolve.

Format ``ak_<live|test>_<32 base32>``. Stored as the first 12 characters
(``ak_live_abcd``, the prefix) plus ``HMAC-SHA256(pepper, full_key)``. Not
bcrypt: authentication needs an O(1) lookup by prefix, and the pepper keeps a
database dump useless without the secret.

A key is shown once, at issue, and is never retrievable afterwards — the hash
is not reversible and the full key is not stored anywhere.

Resolution is the hard part: it happens *before* the tenant is known, so it
cannot run inside ``tenant_transaction``. It goes through
``adapt_resolve_api_key``, a SECURITY DEFINER function owned by
``adapt_owner`` with EXECUTE granted only to ``adapt_app``. That function is
the only place in the engine that reads across tenants (DECISIONS.md §1).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from adapt_engine.db.tenant import tenant_transaction

KEY_ENV_LIVE: Final = "live"
KEY_ENV_TEST: Final = "test"
KEY_ENVS: Final = (KEY_ENV_LIVE, KEY_ENV_TEST)

SCOPES: Final = ("observe", "decide", "read_model", "admin")

#: ``ak_live_`` is 8 characters; 4 more makes a 12-character prefix. Twelve is
#: enough to look up, short enough that the remaining 28 characters are the
#: secret.
PREFIX_LENGTH: Final = 12
SECRET_LENGTH: Final = 32

#: Compared against when no key matched, so an unknown prefix costs the same
#: HMAC comparison as a known one.
DUMMY_HASH: Final = hashlib.sha256(b"adapt-dummy-hash-for-timing").digest()

#: ``last_used_at`` is written at most this often per key.
LAST_USED_DEBOUNCE_SECONDS: Final = 60


class KeyFormatError(ValueError):
    """The presented string is not shaped like an API key."""


@dataclass(frozen=True)
class IssuedKey:
    """The one moment the full key exists outside a client."""

    key_id: uuid.UUID
    tenant_id: uuid.UUID
    prefix: str
    full_key: str
    scopes: tuple[str, ...]
    env: str


@dataclass(frozen=True)
class ResolvedKey:
    """What the resolver function returns. Never contains the full key."""

    key_id: uuid.UUID
    tenant_id: uuid.UUID
    stored_hash: bytes
    scopes: tuple[str, ...]
    revoked: bool
    last_used_at: object | None


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


def hashes_match(stored: bytes, computed: bytes) -> bool:
    """Constant-time comparison; never short-circuits on length mismatch."""
    return hmac.compare_digest(bytes(stored), bytes(computed))


def validate_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValueError(f"unknown scope(s): {', '.join(unknown)}; known: {', '.join(SCOPES)}")
    if not scopes:
        raise ValueError("a key needs at least one scope")
    return tuple(sorted(set(scopes)))


def insert_api_key(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    scopes: list[str] | tuple[str, ...],
    env: str,
    pepper: str,
) -> IssuedKey:
    """Insert a key on a connection that is already inside tenant context."""
    validated = validate_scopes(scopes)
    full_key, prefix = generate_key(env)
    key_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO api_keys (id, tenant_id, prefix, hash, scopes) "
            "VALUES (:id, :tenant_id, :prefix, :hash, :scopes)"
        ),
        {
            "id": key_id,
            "tenant_id": tenant_id,
            "prefix": prefix,
            "hash": hash_key(pepper, full_key),
            "scopes": list(validated),
        },
    )
    return IssuedKey(
        key_id=key_id,
        tenant_id=tenant_id,
        prefix=prefix,
        full_key=full_key,
        scopes=validated,
        env=env,
    )


def issue_api_key(
    engine: Engine,
    *,
    tenant_id: uuid.UUID,
    scopes: list[str] | tuple[str, ...],
    env: str,
    pepper: str,
) -> IssuedKey:
    """Create a key for a tenant and return the full key exactly once.

    Runs under tenant context, so RLS scopes the insert to its own tenant even
    though the caller supplies the tenant id.
    """
    with tenant_transaction(engine, tenant_id) as conn:
        return insert_api_key(
            conn, tenant_id=tenant_id, scopes=scopes, env=env, pepper=pepper
        )


def revoke_api_key(engine: Engine, *, tenant_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    """Revoke immediately: the next resolution sees ``revoked_at``."""
    with tenant_transaction(engine, tenant_id) as conn:
        result = conn.execute(
            text(
                "UPDATE api_keys SET revoked_at = now() "
                "WHERE id = :id AND revoked_at IS NULL"
            ),
            {"id": key_id},
        )
        return bool(result.rowcount)


def resolve_api_key(engine: Engine, prefix: str) -> ResolvedKey | None:
    """Look a key up by exact prefix, through the SECURITY DEFINER function.

    No tenant context is set — that is the point. The function is the only
    cross-tenant read in the system, and it is read-only.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT key_id, tenant_id, key_hash, scopes, revoked_at, last_used_at "
                "FROM adapt_resolve_api_key(:prefix)"
            ),
            {"prefix": prefix},
        ).one_or_none()
    if row is None:
        return None
    return ResolvedKey(
        key_id=row.key_id,
        tenant_id=row.tenant_id,
        stored_hash=bytes(row.key_hash),
        scopes=tuple(row.scopes),
        revoked=row.revoked_at is not None,
        last_used_at=row.last_used_at,
    )


def touch_last_used(engine: Engine, *, tenant_id: uuid.UUID, key_id: uuid.UUID) -> int:
    """Record use, at most once per ``LAST_USED_DEBOUNCE_SECONDS`` per key.

    The debounce is in the UPDATE's WHERE clause, so it is one statement and
    cannot write more often than the interval even under concurrency. Returns
    the number of rows written (0 when the write was debounced away).
    """
    with tenant_transaction(engine, tenant_id) as conn:
        result = conn.execute(
            text(
                "UPDATE api_keys SET last_used_at = now() "
                "WHERE id = :id "
                "  AND (last_used_at IS NULL "
                "       OR last_used_at < now() - make_interval(secs => :debounce))"
            ),
            {"id": key_id, "debounce": LAST_USED_DEBOUNCE_SECONDS},
        )
        return int(result.rowcount)
