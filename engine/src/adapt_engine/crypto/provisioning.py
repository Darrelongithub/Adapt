"""Provisioning: create a tenant's data key, as the owner, once, on purpose.

The runtime role holds ``SELECT`` on ``tenant_data_keys`` and ``data_key_ids``
and no write on either. That is not decoration: the startup check trusts
``data_key_ids`` to answer "is every kid in use covered by my keyring?", so a
role that serves requests must not be able to add a row to it. Creating a data
key is therefore a provisioning step — the same class of operation as creating
the tenant — and it runs with the owner credential.

Everything here is idempotent: provisioning a tenant twice does not replace a
data key that already exists, because replacing it would make every row already
encrypted under the old key unreadable.
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from adapt_engine.crypto.envelope import CURRENT_DATA_KEY_VERSION, data_key_aad, seal
from adapt_engine.crypto.keyring import Keyring
from adapt_engine.crypto.store import DATA_KEY_BYTES


def provision_data_key(conn: Connection, tenant_id: uuid.UUID, keyring: Keyring) -> str:
    """Create the tenant's data key row if it has none. Returns the master kid.

    The caller supplies a connection running as the owner inside the tenant's
    context (the table is FORCEd RLS, so even the owner must declare which
    tenant it is writing to). Returns the kid *currently wrapping* the key,
    which for a fresh key is the keyring's current kid.
    """
    existing = conn.execute(
        text("SELECT kid FROM tenant_data_keys WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).scalar_one_or_none()
    if existing is not None:
        _register(conn, str(existing))
        return str(existing)

    kid = keyring.current
    wrapped = seal(
        keyring.key(kid),
        os.urandom(DATA_KEY_BYTES),
        aad=data_key_aad(tenant_id),
        dkv=CURRENT_DATA_KEY_VERSION,
    )
    conn.execute(
        text(
            "INSERT INTO tenant_data_keys (tenant_id, kid, dkv, wrapped) "
            "VALUES (:tenant_id, :kid, :dkv, :wrapped) "
            "ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {
            "tenant_id": tenant_id,
            "kid": kid,
            "dkv": CURRENT_DATA_KEY_VERSION,
            "wrapped": wrapped,
        },
    )
    _register(conn, kid)
    return kid


def _register(conn: Connection, kid: str) -> None:
    conn.execute(
        text("INSERT INTO data_key_ids (kid) VALUES (:kid) ON CONFLICT DO NOTHING"),
        {"kid": kid},
    )


def provision_data_key_for_tenant(engine: Engine, tenant_id: uuid.UUID, keyring: Keyring) -> str:
    """Same, opening the tenant context for you. ``engine`` must be the owner."""
    from adapt_engine.db.tenant import tenant_transaction

    with tenant_transaction(engine, tenant_id) as conn:
        return provision_data_key(conn, tenant_id, keyring)


__all__ = ["provision_data_key", "provision_data_key_for_tenant"]
