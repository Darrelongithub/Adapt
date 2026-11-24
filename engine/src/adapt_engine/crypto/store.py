"""Per-tenant data keys, and the two operations the rest of the engine needs.

A tenant gets one data key, generated lazily on first write and stored wrapped
by the current master key::

    tenant_data_keys(tenant_id PK, kid, wrapped, created_at, rotated_at)

The wrapper is itself a ``v1`` envelope whose AAD is the tenant id, so a
wrapped key lifted out of tenant A's row cannot be unwrapped in tenant B's.

Encrypting a value:

1. read (or create) the tenant's data key — one row, one lookup;
2. seal the plaintext with the data key, AAD = tenant id, header kid = the
   master key id that currently wraps the data key.

Rotating the master key re-wraps step 1 only. The data key does not change, so
rows written yesterday still open after the rotation: that is the whole reason
the header exists.

What is *not* here: encrypting identifiers. ``tenant_id`` and ``learner_id``
stay in clear because they are lookups — a unique constraint, an index and an
RLS predicate all read them — and because a pseudonymous id is still personal
data, so encrypting it would not make it anonymous. See DECISIONS §9.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from adapt_engine.crypto.envelope import (
    DecryptionFailed,
    Envelope,
    MalformedEnvelope,
    aad_for,
    seal,
    unseal,
)
from adapt_engine.crypto.keyring import Keyring

#: A tenant data key is a full AES-256 key, generated from the OS.
DATA_KEY_BYTES = 32


@dataclass
class DataKeyStore:
    """Encrypt and decrypt one tenant's personal columns."""

    keyring: Keyring
    #: kid -> data key, per tenant, for the life of the process. A rotation
    #: changes the *wrapper*, not this; the refusal-on-unknown-kid at startup
    #: is what keeps a cache from outliving its key.
    _cache: dict[uuid.UUID, tuple[str, bytes]] = field(default_factory=dict, repr=False)

    # -- data keys -------------------------------------------------------
    def _record_kid(self, conn: Connection, kid: str) -> None:
        """Catalogue a kid once. Append-only: this is the startup check's data."""
        conn.execute(
            text("INSERT INTO data_key_ids (kid) VALUES (:kid) ON CONFLICT DO NOTHING"),
            {"kid": kid},
        )

    def _load(self, conn: Connection, tenant_id: uuid.UUID) -> tuple[str, bytes]:
        cached = self._cache.get(tenant_id)
        if cached is not None:
            return cached
        row = conn.execute(
            text("SELECT kid, wrapped FROM tenant_data_keys WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).one_or_none()
        if row is None:
            kid, wrapped = self.keyring.current, seal(
                self.keyring.key(self.keyring.current),
                os.urandom(DATA_KEY_BYTES),
                aad=aad_for(tenant_id),
                kid=self.keyring.current,
            )
            conn.execute(
                text(
                    "INSERT INTO tenant_data_keys (tenant_id, kid, wrapped) "
                    "VALUES (:tenant_id, :kid, :wrapped) "
                    "ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"tenant_id": tenant_id, "kid": kid, "wrapped": wrapped},
            )
            self._record_kid(conn, kid)
            row = conn.execute(
                text("SELECT kid, wrapped FROM tenant_data_keys WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            ).one()
        kid, wrapped = row.kid, row.wrapped
        data_key = self._unwrap(tenant_id, kid, wrapped)
        self._cache[tenant_id] = (kid, data_key)
        return kid, data_key

    def _unwrap(self, tenant_id: uuid.UUID, kid: str, wrapped: str) -> bytes:
        envelope = Envelope.parse(wrapped)
        master = self.keyring.key(envelope.kid)
        try:
            return unseal(master, envelope, aad=aad_for(tenant_id))
        except DecryptionFailed as exc:
            raise DecryptionFailed(
                "the wrapped data key does not belong to this tenant"
            ) from exc

    # -- values ----------------------------------------------------------
    def encrypt(self, conn: Connection, tenant_id: uuid.UUID, plaintext: str | None) -> str | None:
        """Encrypt one value. ``None`` stays ``None``: absent is not empty."""
        if plaintext is None:
            return None
        kid, data_key = self._load(conn, tenant_id)
        return seal(data_key, plaintext.encode("utf-8"), aad=aad_for(tenant_id), kid=kid)

    def decrypt(self, conn: Connection, tenant_id: uuid.UUID, envelope: str | None) -> str | None:
        """Decrypt one value, refusing anything that does not authenticate."""
        if envelope is None:
            return None
        parsed = Envelope.parse(envelope)
        if parsed.kid not in self.keyring:
            raise MalformedEnvelope(
                f"ciphertext names master key id {parsed.kid!r}, which is not configured"
            )
        _, data_key = self._load(conn, tenant_id)
        return unseal(data_key, parsed, aad=aad_for(tenant_id)).decode("utf-8")

    # -- rotation --------------------------------------------------------
    def rewrap(self, conn: Connection, tenant_id: uuid.UUID) -> str:
        """Re-wrap this tenant's data key under the current master key.

        Rows are untouched. Returns the kid now in use.
        """
        _, data_key = self._load(conn, tenant_id)
        new_kid = self.keyring.current
        wrapped = seal(
            self.keyring.key(new_kid), data_key, aad=aad_for(tenant_id), kid=new_kid
        )
        conn.execute(
            text(
                "UPDATE tenant_data_keys "
                "SET kid = :kid, wrapped = :wrapped, rotated_at = now() "
                "WHERE tenant_id = :tenant_id"
            ),
            {"kid": new_kid, "wrapped": wrapped, "tenant_id": tenant_id},
        )
        self._record_kid(conn, new_kid)
        self._cache[tenant_id] = (new_kid, data_key)
        return new_kid

    def kids_in_use(self, engine: Engine) -> set[str]:
        """Every master key id that has wrapped a data key, ever.

        `data_key_ids` is append-only, so this includes ids that only appear in
        the headers of old rows — which is exactly the set the keyring must
        cover.
        """
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT kid FROM data_key_ids")).all()
        return {row.kid for row in rows}

    def verify_covers_usage(self, engine: Engine) -> None:
        """Refuse to run if a kid in use has no key: startup's last gate."""
        self.keyring.check_covers(self.kids_in_use(engine))


def crypto_store(request: Request) -> DataKeyStore:
    """The process-wide store, built once in ``create_app``."""
    store: DataKeyStore = request.app.state.crypto
    return store


def rewrap_all(engine: Engine, keyring: Keyring, tenant_ids: Iterable[uuid.UUID]) -> int:
    """Re-wrap every listed tenant's data key; returns how many changed."""
    store = DataKeyStore(keyring=keyring)
    changed = 0
    for tenant_id in tenant_ids:
        from adapt_engine.db.tenant import tenant_transaction

        with tenant_transaction(engine, tenant_id) as conn:
            store.rewrap(conn, tenant_id)
        changed += 1
    return changed


__all__ = ["DATA_KEY_BYTES", "DataKeyStore", "crypto_store", "rewrap_all"]
