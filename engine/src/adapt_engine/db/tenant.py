"""Tenant context: set once per transaction, read back, never per query.

PostgreSQL's row-level security reads ``adapt.tenant_id``. Three properties
matter and each has a failure mode that passes tests when it is wrong:

* **Explicit transaction.** ``SET LOCAL`` outside a transaction is a silent
  no-op, leaving the session with no context (zero rows, which looks like
  "empty tenant" rather than "misconfigured") — or, worse, with the previous
  transaction's context if a session-level ``SET`` was ever used.
* **``SET LOCAL`` semantics.** A session-level ``SET`` survives COMMIT and
  hands the next request on a pooled connection the previous tenant's context.
* **Read-back.** A ``SET LOCAL`` that silently did nothing must fail loudly
  before a query runs, not return the wrong rows.

``set_config(name, value, is_local => true)`` is the parameterisable
equivalent of ``SET LOCAL``; both roll back at COMMIT/ROLLBACK.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

#: The GUC the RLS policies read.
TENANT_SETTING = "adapt.tenant_id"


class TenantContextError(RuntimeError):
    """The tenant context is missing, unset, or does not match the request."""


def set_tenant_local(conn: Connection, tenant_id: uuid.UUID | str) -> None:
    """Set the tenant GUC for the current transaction and verify it."""
    if not conn.in_transaction():
        raise TenantContextError(
            "tenant context must be set inside an explicit transaction; "
            "SET LOCAL outside one is a silent no-op"
        )
    value = str(tenant_id)
    conn.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_SETTING, "value": value},
    )
    applied = conn.execute(
        text("SELECT current_setting(:name, true)"), {"name": TENANT_SETTING}
    ).scalar_one_or_none()
    if applied != value:
        raise TenantContextError(
            f"{TENANT_SETTING} did not take effect (got {applied!r}, expected {value!r})"
        )


@contextmanager
def tenant_transaction(engine: Engine, tenant_id: uuid.UUID | str) -> Iterator[Connection]:
    """Open a transaction scoped to one tenant.

    This is the single place tenant context is set. Data access takes the
    connection it yields and does not touch the GUC itself.
    """
    with engine.begin() as conn:
        set_tenant_local(conn, tenant_id)
        yield conn
