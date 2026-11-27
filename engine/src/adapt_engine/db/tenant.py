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


def commit_before_promise(conn: Connection) -> None:
    """Commit a transaction whose response asserts something about this write.

    Two families of route need this. The obvious one hands out a **secret** — a key
    that must work on the next request, a webhook secret. The larger one states a
    **fact about a write** — "paused", "revoked", "expires at T", "this batch is
    durable", "this learner exists", "this decision can be replayed" — where the
    next request a client makes is entitled to see the fact.

    FastAPI runs a ``yield`` dependency's teardown *after* the response bytes are
    on the wire (measured, not assumed: see ``DECISIONS.md`` §24.5). ``tenant_
    transaction`` commits in that teardown, so for a route that returns a freshly
    minted API key the client can hold the key in its hand — and use it — while the
    row that makes it valid is still uncommitted. The result is a ``401
    invalid_api_key`` for a key that is, microseconds later, perfectly good. It is
    not hypothetical: it is what turned ``engine (postgres via docker)`` red on a
    commit whose sibling run was green, because ``adapt sandbox seed`` mints a
    connection key and observes an event with it on the next line.

    Two disciplines come with it, both because the commit ends the *transaction*
    and not just the write:

    * **Build the response object first, then commit, then return it.** Committing
      expires the ORM identity map, so reading a row afterwards re-selects it — in
      a transaction that no longer has ``adapt.tenant_id`` set, which is to say
      under row-level security, which is to say it is gone.
    * **The rule is about the promise, not the row.** DECISIONS §24.6 sweeps every
      write route and records which ones promise something the next request has to
      observe; a route that only returns numbers it computed (a grant, a body
      re-serialised from inputs) does not need this and does not call it.
    * **Nothing may query the connection after this call.** The tenant setting is
      transaction-local (``set_config(..., true)``); a follow-up statement would
      both lose the tenant and raise *"can't operate on closed transaction inside
      context manager"*.

    So: last statement touching ``conn``, response model already built.
    """
    conn.commit()
