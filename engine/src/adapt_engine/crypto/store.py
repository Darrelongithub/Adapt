"""Per-tenant data keys: read for the runtime, provisioned and re-wrapped by the owner.

A tenant gets one data key, created when the tenant is provisioned and stored
wrapped by the master key named on its row::

    tenant_data_keys(tenant_id PK, kid, dkv, wrapped, created_at, rotated_at)

The wrapper is itself a ``v1`` envelope whose AAD is the tenant id, so a
wrapped key lifted out of tenant A's row cannot be unwrapped in tenant B's.

Encrypting a value:

1. read the tenant's data key row — one lookup, one unwrap (cached per process);
2. seal the plaintext with the data key, AAD = tenant + row id + column, header
   ``dkv`` = the data key version.

**The runtime cannot write any of this.** ``adapt_app`` holds ``SELECT`` on
``tenant_data_keys`` and ``data_key_ids`` and nothing else: a request-serving
role that could insert a data key row could register a master key id the
startup check then trusts. Creation and re-wrap are provisioning operations,
run as the owner, and they are the only writers of both tables.

Master-key rotation re-wraps this row and touches no learner data. Retirement
is the point: ``data_key_ids`` is pruned when its last referring row moves to a
new key, so a master key can actually be deleted — see ``rotate.py`` for the
preflight query that answers "is k1 still referenced?".

What is *not* here: encrypting identifiers. ``tenant_id`` and ``learner_id``
stay in clear because they are lookups — a unique constraint, an index and an
RLS predicate all read them — and because a pseudonymous id is still personal
data, so encrypting it would not make it anonymous. See DECISIONS §12.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from adapt_engine.crypto.envelope import (
    DecryptionFailed,
    Envelope,
    MalformedEnvelope,
    data_key_aad,
    seal,
    unseal,
    value_aad,
)
from adapt_engine.crypto.keyring import Keyring

#: A tenant data key is a full AES-256 key, generated from the OS.
DATA_KEY_BYTES = 32


class DataKeyMissing(RuntimeError):
    """The tenant has no data key row; provisioning did not run."""


@dataclass
class DataKeyStore:
    """Encrypt and decrypt one tenant's personal columns."""

    keyring: Keyring
    #: tenant -> (dkv, data key), for the life of the process. A rotation
    #: changes the *wrapper* and the kid, not the data key, so this stays
    #: valid; a data key version the tenant does not hold is refused, not
    #: guessed.
    _cache: dict[uuid.UUID, tuple[str, bytes]] = field(default_factory=dict, repr=False)

    # -- data keys -------------------------------------------------------
    def _load(self, conn: Connection, tenant_id: uuid.UUID) -> tuple[str, bytes]:
        cached = self._cache.get(tenant_id)
        if cached is not None:
            return cached
        row = conn.execute(
            text("SELECT kid, dkv, wrapped FROM tenant_data_keys WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).one_or_none()
        if row is None:
            raise DataKeyMissing(
                f"tenant {tenant_id} has no data key; provision it as the owner "
                "(scripts/provision_tenant.py) before serving writes"
            )
        data_key = self._unwrap(tenant_id, row.kid, row.wrapped)
        self._cache[tenant_id] = (row.dkv, data_key)
        return row.dkv, data_key

    def _unwrap(self, tenant_id: uuid.UUID, kid: str, wrapped: str) -> bytes:
        """Unwrap with the master key the row names — the header no longer does."""
        envelope = Envelope.parse(wrapped)
        master = self.keyring.key(kid)
        try:
            return unseal(master, envelope, aad=data_key_aad(tenant_id))
        except DecryptionFailed as exc:
            raise DecryptionFailed("the wrapped data key does not belong to this tenant") from exc

    # -- values ----------------------------------------------------------
    def encrypt(
        self,
        conn: Connection,
        tenant_id: uuid.UUID,
        row_id: uuid.UUID,
        column: str,
        plaintext: str | None,
    ) -> str | None:
        """Encrypt one cell. ``None`` stays ``None``: absent is not empty."""
        if plaintext is None:
            return None
        dkv, data_key = self._load(conn, tenant_id)
        return seal(
            data_key,
            plaintext.encode("utf-8"),
            aad=value_aad(tenant_id, row_id, column),
            dkv=dkv,
        )

    def decrypt(
        self,
        conn: Connection,
        tenant_id: uuid.UUID,
        row_id: uuid.UUID,
        column: str,
        envelope: str | None,
    ) -> str | None:
        """Decrypt one cell, refusing anything not authenticated for that cell."""
        if envelope is None:
            return None
        parsed = Envelope.parse(envelope)
        dkv, data_key = self._load(conn, tenant_id)
        if parsed.dkv != dkv:
            raise MalformedEnvelope(
                f"ciphertext is data key version {parsed.dkv!r} but this tenant holds "
                f"{dkv!r}; data-key rotation is not supported, so this row is unreadable"
            )
        return unseal(data_key, parsed, aad=value_aad(tenant_id, row_id, column)).decode("utf-8")

    # -- what the startup check reads ------------------------------------
    def kids_in_use(self, engine: Engine) -> set[str]:
        """Master key ids referenced by data key rows, across tenants.

        ``data_key_rows`` is maintained by provisioning and by re-wrap, both of
        which run as the owner; the runtime only reads it. A kid disappears
        from it when its last data key row is re-wrapped away, which is what
        makes retirement possible at all.
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


__all__ = ["DATA_KEY_BYTES", "DataKeyMissing", "DataKeyStore", "crypto_store"]
