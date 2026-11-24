"""Master-key rotation: re-wrap data keys, retire a key only when nothing needs it.

Rotation moves no learner data. For every tenant's data key row it:

1. unwraps the data key with the kid that row currently names;
2. re-wraps it under the keyring's current key, and updates ``kid``;
3. registers the new kid in ``data_key_ids`` and **deletes the old one when no
   row references it any more**.

Step 3 is what makes retirement real. The earlier design put the master kid in
every *value* header, so rows written under k1 kept pointing at k1 forever and
k1 could never be dropped; the catalogue had to grow monotonically and startup
had to keep accepting a key nobody wanted. Now the value header carries a data
key version, the master kid lives only on the data key row, and a re-wrap
updates it.

``preflight`` answers the operator's actual question before anything is
deleted: *is this kid still referenced?* It is read-only, and it is the query
to run in a change review before removing a key from the environment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from adapt_engine.crypto.envelope import DecryptionFailed, Envelope, data_key_aad, seal, unseal
from adapt_engine.crypto.keyring import Keyring, UnknownKeyId
from adapt_engine.db.tenant import tenant_transaction


@dataclass(frozen=True)
class RewrapResult:
    """What one tenant's re-wrap did."""

    tenant_id: uuid.UUID
    old_kid: str
    new_kid: str
    changed: bool


def referenced_kids(conn: Connection) -> dict[str, int]:
    """How many data key rows reference each kid. Owner-only, read-only."""
    rows = conn.execute(
        text("SELECT kid, count(*) AS rows FROM tenant_data_keys GROUP BY kid ORDER BY kid")
    ).all()
    return {row.kid: int(row.rows) for row in rows}


def preflight(engine: Engine, kid: str) -> int:
    """How many data key rows still name ``kid``. Zero means it can be deleted.

    ``engine`` must be the owner: the count crosses tenants, and the owner is
    the only role that may read wrapped data keys beyond its own tenant.
    """
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM tenant_data_keys WHERE kid = :kid"), {"kid": kid}
            ).scalar_one()
        )


def rewrap_tenant(conn: Connection, tenant_id: uuid.UUID, keyring: Keyring) -> RewrapResult:
    """Re-wrap one tenant's data key under ``keyring.current``.

    The caller supplies the owner role inside the tenant's context.
    """
    row = conn.execute(
        text("SELECT kid, dkv, wrapped FROM tenant_data_keys WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).one_or_none()
    if row is None:
        raise LookupError(f"tenant {tenant_id} has no data key to re-wrap")

    old_kid = str(row.kid)
    new_kid = keyring.current
    if old_kid == new_kid:
        return RewrapResult(tenant_id=tenant_id, old_kid=old_kid, new_kid=new_kid, changed=False)

    try:
        data_key = unseal(
            keyring.key(old_kid),
            Envelope.parse(row.wrapped),
            aad=data_key_aad(tenant_id),
        )
    except DecryptionFailed as exc:
        raise DecryptionFailed(
            f"the data key for {tenant_id} does not unwrap under {old_kid!r}"
        ) from exc

    wrapped = seal(keyring.key(new_kid), data_key, aad=data_key_aad(tenant_id), dkv=row.dkv)
    conn.execute(
        text(
            "UPDATE tenant_data_keys "
            "SET kid = :kid, wrapped = :wrapped, rotated_at = now() "
            "WHERE tenant_id = :tenant_id"
        ),
        {"kid": new_kid, "wrapped": wrapped, "tenant_id": tenant_id},
    )
    conn.execute(
        text("INSERT INTO data_key_ids (kid) VALUES (:kid) ON CONFLICT DO NOTHING"),
        {"kid": new_kid},
    )
    _prune(conn, old_kid)
    return RewrapResult(tenant_id=tenant_id, old_kid=old_kid, new_kid=new_kid, changed=True)


def _prune(conn: Connection, kid: str) -> None:
    """Drop a kid from the catalogue once no data key row references it."""
    conn.execute(
        text(
            "DELETE FROM data_key_ids "
            " WHERE kid = :kid "
            "   AND NOT EXISTS (SELECT 1 FROM tenant_data_keys WHERE kid = :kid)"
        ),
        {"kid": kid},
    )


def rewrap_all(engine: Engine, keyring: Keyring) -> list[RewrapResult]:
    """Re-wrap every tenant's data key. ``engine`` must be the owner.

    Tenants are enumerated from ``tenant_data_keys``, not from ``tenants``: the
    owner has no business listing tenants, and a tenant that has no data key
    needs no rotation.
    """
    with engine.connect() as conn:
        tenant_ids = [
            row.tenant_id
            for row in conn.execute(
                text("SELECT tenant_id FROM tenant_data_keys ORDER BY tenant_id")
            ).all()
        ]
    results: list[RewrapResult] = []
    for tenant_id in tenant_ids:
        with tenant_transaction(engine, tenant_id) as conn:
            results.append(rewrap_tenant(conn, tenant_id, keyring))
    return results


def assert_retirable(engine: Engine, kid: str, keyring: Keyring) -> None:
    """Refuse a retirement that would make stored rows unreadable."""
    still_used = preflight(engine, kid)
    if still_used:
        raise UnknownKeyId(
            f"{still_used} data key row(s) still name {kid!r}; re-wrap them before "
            f"removing it from the keyring (it currently holds {sorted(keyring.keys)})"
        )


__all__ = [
    "RewrapResult",
    "assert_retirable",
    "preflight",
    "referenced_kids",
    "rewrap_all",
    "rewrap_tenant",
]
