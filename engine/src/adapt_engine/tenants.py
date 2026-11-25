"""Tenant-level facts a response needs: the tier, and nothing else.

One function, because the tier is read in three places (mastery, decide, the
level object) and three copies of `SELECT tier FROM tenants` is three places for
a default to drift. RLS makes the row the caller's own: a request cannot read
another tenant's contract by asking.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

#: Used when a row is somehow absent. `core` is the safe default: it serves the
#: least, so a bug cannot accidentally hand a tenant a level they did not buy.
DEFAULT_TIER = "core"


def tier_of(conn: Connection, tenant_id: uuid.UUID) -> str:
    """The tenant's contracted tier (`core` / `plus` / `deep`)."""
    row = conn.execute(
        text("SELECT tier FROM tenants WHERE id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).one_or_none()
    if row is None:
        return DEFAULT_TIER
    return str(row.tier)
