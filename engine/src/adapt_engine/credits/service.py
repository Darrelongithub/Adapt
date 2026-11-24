"""Credits: reserve, settle, release — and a balance that is a query.

The model, in one paragraph. A **grant** is a pile of credits with an identity
(an included allowance expires; a purchased pack never does). ``remaining`` is
what is *free*: a reservation takes credits out of ``remaining`` into a hold, a
settlement turns held credits into consumption, and a release puts them back.
So

    granted = available + held + consumed + forfeited, for every tenant, always

where ``available = sum(remaining)`` over **non-expired** grants, ``held`` is
the sum of open reservations, ``consumed`` is the settle-only figure the
``credit_consumption`` view exposes, and ``forfeited`` is ``sum(remaining)``
over grants whose ``expires_at`` has passed.

The fourth term is not bookkeeping taste. Without it the identity breaks the
moment any allowance expires: the credits are missing from the books, which is
both a reconciliation failure and a number the customer is entitled to see. It
is derivable, so nothing sweeps it. The invariant is asserted after an expiry,
not only on the happy path.

The ledger records **consumption**, not motion: reserve and release write a
``delta = 0`` row because no credit was consumed, settle writes ``-amount``.
Those zero rows are not noise — they are how an idempotency key is bound to an
operation before the operation can be retried into existence. The key is
claimed by inserting its ledger row *first*, so a concurrent duplicate blocks
on the unique index and then replays instead of racing the drawdown.

Concurrency is handled by ``SELECT ... FOR UPDATE`` on the tenant's open
grants, taken in drawdown order, so concurrent reservations serialise and the
100-at-balance-10 test yields exactly ten successes. The CHECK constraints are
the last line, not the mechanism.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.errors import AdaptError

GrantKind = Literal["included", "purchased", "promo"]

#: Expiring-first, then oldest granted first, then id so the order is total.
#: This is METERING.md §4's lock query, in the one place that uses it.
DRAWDOWN_ORDER = "ORDER BY expires_at NULLS LAST, granted_at, id"

_LIVE_GRANT = "(expires_at IS NULL OR expires_at > now())"

T = TypeVar("T")

@dataclass(frozen=True)
class Grant:
    id: uuid.UUID
    kind: str
    amount: int
    remaining: int
    expires_at: datetime | None


@dataclass(frozen=True)
class Balance:
    available: int
    held: int
    consumed: int
    forfeited: int
    granted: int
    grants: tuple[Grant, ...]

    @property
    def invariant_holds(self) -> bool:
        """Four terms, not three: expired credits are forfeited, not missing."""
        return (
            self.granted
            == self.available + self.held + self.consumed + self.forfeited
        )


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    amount: int
    state: str
    operation_ref: str


def balance(conn: Connection, tenant_id: uuid.UUID) -> Balance:
    """Available, held and consumed, derived from rows on every call."""
    live = tuple(
        Grant(
            id=row.id,
            kind=row.kind,
            amount=row.amount,
            remaining=row.remaining,
            expires_at=row.expires_at,
        )
        for row in conn.execute(
            text(
                "SELECT id, kind, amount, remaining, expires_at FROM credit_grants "
                f"WHERE tenant_id = :tenant_id AND {_LIVE_GRANT} {DRAWDOWN_ORDER}"
            ),
            {"tenant_id": tenant_id},
        ).all()
    )
    held = conn.execute(
        text(
            "SELECT coalesce(sum(amount), 0) FROM credit_reservations "
            "WHERE tenant_id = :tenant_id AND state = 'held'"
        ),
        {"tenant_id": tenant_id},
    ).scalar_one()
    # Consumption comes from the view, which filters entry_type = 'settle'.
    # `sum(delta)` over the ledger is not a consumption figure (METERING.md §4).
    consumed = conn.execute(
        text(
            "SELECT coalesce(sum(consumed), 0) FROM credit_consumption "
            "WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": tenant_id},
    ).scalar_one()
    forfeited = conn.execute(
        text(
            "SELECT coalesce(sum(remaining), 0) FROM credit_grants "
            "WHERE tenant_id = :tenant_id AND expires_at IS NOT NULL AND expires_at <= now()"
        ),
        {"tenant_id": tenant_id},
    ).scalar_one()
    granted = conn.execute(
        text("SELECT coalesce(sum(amount), 0) FROM credit_grants WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).scalar_one()
    return Balance(
        available=sum(grant.remaining for grant in live),
        held=int(held),
        consumed=int(consumed),
        forfeited=int(forfeited),
        granted=int(granted),
        grants=live,
    )


def grant_credits(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    kind: GrantKind,
    amount: int,
    expires_at: datetime | None,
    source_ref: str | None = None,
    idempotency_key: str | None = None,
    period_start: date | None = None,
) -> uuid.UUID:
    """Create a grant.

    ``delta = 0`` in the ledger: adding credits is not consumption, and the
    grant row is the record of the credits themselves.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    if idempotency_key is not None:
        replay = _replay(conn, tenant_id=tenant_id, idempotency_key=idempotency_key, expect="grant")
        if replay is not None:
            return uuid.UUID(str(replay.ref))

    grant_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO credit_grants "
            "(id, tenant_id, kind, amount, remaining, expires_at, source_ref) "
            "VALUES (:id, :tenant_id, :kind, :amount, :amount, :expires_at, :source_ref)"
        ),
        {
            "id": grant_id,
            "tenant_id": tenant_id,
            "kind": kind,
            "amount": amount,
            "expires_at": expires_at,
            "source_ref": source_ref,
        },
    )
    if idempotency_key is not None:
        _write_ledger(
            conn,
            tenant_id=tenant_id,
            grant_id=grant_id,
            delta=0,
            entry_type="grant",
            ref=str(grant_id),
            idempotency_key=idempotency_key,
            period_start=period_start,
        )
    return grant_id


def reserve(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    amount: int,
    operation_ref: str,
    idempotency_key: str,
) -> Reservation:
    """Hold ``amount`` credits for an operation, drawing in published order."""
    if amount <= 0:
        raise ValueError("amount must be positive")

    replay = _replay(conn, tenant_id=tenant_id, idempotency_key=idempotency_key, expect="reserve")
    if replay is not None:
        return _reservation_by_id(conn, tenant_id, _ref_id(replay), amount=None)

    reservation_id = uuid.uuid4()
    _write_ledger(
        conn,
        tenant_id=tenant_id,
        grant_id=None,
        delta=0,
        entry_type="reserve",
        ref=str(reservation_id),
        idempotency_key=idempotency_key,
        period_start=None,
    )

    locked = conn.execute(
        text(
            "SELECT id, remaining FROM credit_grants "
            f"WHERE tenant_id = :tenant_id AND {_LIVE_GRANT} AND remaining > 0 "
            f"{DRAWDOWN_ORDER} FOR UPDATE"
        ),
        {"tenant_id": tenant_id},
    ).all()

    if sum(row.remaining for row in locked) < amount:
        raise _quota_exhausted(conn, tenant_id)

    to_draw = amount
    draws: list[tuple[uuid.UUID, int]] = []
    for row in locked:
        if to_draw == 0:
            break
        taken = min(row.remaining, to_draw)
        conn.execute(
            text("UPDATE credit_grants SET remaining = remaining - :taken WHERE id = :id"),
            {"taken": taken, "id": row.id},
        )
        draws.append((row.id, taken))
        to_draw -= taken

    conn.execute(
        text(
            "INSERT INTO credit_reservations (id, tenant_id, amount, state, operation_ref) "
            "VALUES (:id, :tenant_id, :amount, 'held', :operation_ref)"
        ),
        {
            "id": reservation_id,
            "tenant_id": tenant_id,
            "amount": amount,
            "operation_ref": operation_ref,
        },
    )
    for grant_id, taken in draws:
        conn.execute(
            text(
                "INSERT INTO credit_reservation_draws "
                "(tenant_id, reservation_id, grant_id, amount) "
                "VALUES (:tenant_id, :reservation_id, :grant_id, :amount)"
            ),
            {
                "tenant_id": tenant_id,
                "reservation_id": reservation_id,
                "grant_id": grant_id,
                "amount": taken,
            },
        )

    return Reservation(id=reservation_id, amount=amount, state="held", operation_ref=operation_ref)


def settle(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    amount: int,
    idempotency_key: str,
) -> Reservation:
    """Consume up to the held amount; the unused remainder goes back.

    Settling **more** than was held is refused, not clamped: a caller that
    believes it spent 12 credits and is silently charged 10 has been lied to,
    and the lie points at "your balance is fine".
    """
    replay = _replay(conn, tenant_id=tenant_id, idempotency_key=idempotency_key, expect="settle")
    if replay is not None:
        return _reservation_by_id(conn, tenant_id, _ref_id(replay), amount=-int(replay.delta))

    _write_ledger(
        conn,
        tenant_id=tenant_id,
        grant_id=None,
        delta=-amount,
        entry_type="settle",
        ref=str(reservation_id),
        idempotency_key=idempotency_key,
        period_start=None,
    )

    reservation = _lock_reservation(conn, tenant_id, reservation_id)
    if reservation.state != "held":
        raise AdaptError(
            code="conflict",
            message=f"reservation is already {reservation.state}",
            status_code=409,
            retryable=False,
        )
    if amount < 1 or amount > reservation.amount:
        raise AdaptError(
            code="conflict",
            message=(
                f"settlement of {amount} exceeds the {reservation.amount} credits held; "
                "refused rather than clamped"
            ),
            status_code=409,
            retryable=False,
        )

    draws = conn.execute(
        text(
            "SELECT grant_id, amount FROM credit_reservation_draws "
            "WHERE reservation_id = :reservation_id AND tenant_id = :tenant_id ORDER BY id"
        ),
        {"reservation_id": reservation_id, "tenant_id": tenant_id},
    ).all()

    to_consume = amount
    for draw in draws:
        consumed = min(draw.amount, to_consume)
        to_consume -= consumed
        returned = draw.amount - consumed
        if returned:
            # Back to the grant it came from, if that grant is still alive. A
            # grant that expired mid-flight forfeits its unspent credits: they
            # are not resurrected and not moved to another grant.
            conn.execute(
                text(
                    "UPDATE credit_grants SET remaining = remaining + :returned "
                    f"WHERE id = :grant_id AND {_LIVE_GRANT}"
                ),
                {"returned": returned, "grant_id": draw.grant_id},
            )

    conn.execute(
        text("UPDATE credit_reservations SET state = 'settled', closed_at = now() WHERE id = :id"),
        {"id": reservation_id},
    )
    return Reservation(
        id=reservation_id,
        amount=amount,
        state="settled",
        operation_ref=reservation.operation_ref,
    )


def release(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    idempotency_key: str,
) -> Reservation:
    """Return every held credit. A release never debits."""
    replay = _replay(conn, tenant_id=tenant_id, idempotency_key=idempotency_key, expect="release")
    if replay is not None:
        return _reservation_by_id(conn, tenant_id, _ref_id(replay), amount=0)

    _write_ledger(
        conn,
        tenant_id=tenant_id,
        grant_id=None,
        delta=0,
        entry_type="release",
        ref=str(reservation_id),
        idempotency_key=idempotency_key,
        period_start=None,
    )

    reservation = _lock_reservation(conn, tenant_id, reservation_id)
    if reservation.state != "held":
        raise AdaptError(
            code="conflict",
            message=f"reservation is already {reservation.state}",
            status_code=409,
            retryable=False,
        )

    draws = conn.execute(
        text(
            "SELECT grant_id, amount FROM credit_reservation_draws "
            "WHERE reservation_id = :reservation_id AND tenant_id = :tenant_id"
        ),
        {"reservation_id": reservation_id, "tenant_id": tenant_id},
    ).all()
    for draw in draws:
        conn.execute(
            text(
                "UPDATE credit_grants SET remaining = remaining + :amount "
                f"WHERE id = :grant_id AND {_LIVE_GRANT}"
            ),
            {"amount": draw.amount, "grant_id": draw.grant_id},
        )

    conn.execute(
        text("UPDATE credit_reservations SET state = 'released', closed_at = now() WHERE id = :id"),
        {"id": reservation_id},
    )
    return Reservation(
        id=reservation_id, amount=0, state="released", operation_ref=reservation.operation_ref
    )


# PEP 695 generics (`def f[T]`) would be the py312-native spelling, but this
# tree has to stay importable on 3.11 — the only interpreter that exists in
# this environment (KNOWN_GAPS §2). The annotation does the same job.
def run_idempotent(  # noqa: UP047
    engine: Engine,
    tenant_id: uuid.UUID,
    operation: Callable[[Connection], T],
    *,
    attempts: int = 3,
) -> T:
    """Run one credit operation in its own tenant transaction.

    A concurrent duplicate of the same ``Idempotency-Key`` loses the unique
    index race; the loser retries, finds the winner's ledger row, and replays
    its result. The constraint decides, not a pre-flight check.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with tenant_transaction(engine, tenant_id) as conn:
                return operation(conn)
        except IntegrityError as exc:
            if not _is_idempotency_conflict(exc):
                raise
            last = exc
    assert last is not None
    raise last


def _quota_exhausted(conn: Connection, tenant_id: uuid.UUID) -> AdaptError:
    """402 `quota_exhausted`, with `resets_at` when an allowance is what refills it.

    INTEGRATION.md §6 is the vocabulary: 402 is `quota_exhausted`, and the
    message carries `resets_at`. A tenant whose only credits are purchased
    packs has no reset date — somebody has to buy more.
    """
    resets_at = conn.execute(
        text(
            "SELECT min(expires_at) FROM credit_grants "
            f"WHERE tenant_id = :tenant_id AND {_LIVE_GRANT} AND remaining = 0 "
            "AND expires_at IS NOT NULL"
        ),
        {"tenant_id": tenant_id},
    ).scalar_one_or_none()
    suffix = f"; resets_at={resets_at.isoformat()}" if resets_at else ""
    return AdaptError(
        code="quota_exhausted",
        message=f"no credits available for this operation{suffix}",
        status_code=402,
        retryable=False,
    )


def _is_idempotency_conflict(exc: DBAPIError) -> bool:
    """Whether this unique violation is the ledger's idempotency index."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505":
        return False
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    return "idempotency" in constraint


def _replay(
    conn: Connection, *, tenant_id: uuid.UUID, idempotency_key: str, expect: str
) -> Any:
    """The original ledger row for this key, if this is a replay."""
    row = conn.execute(
        text(
            "SELECT id, entry_type, ref, delta FROM credit_ledger "
            "WHERE tenant_id = :tenant_id AND idempotency_key = :key"
        ),
        {"tenant_id": tenant_id, "key": idempotency_key},
    ).one_or_none()
    if row is None:
        return None
    if row.entry_type != expect:
        raise AdaptError(
            code="conflict",
            message=(
                f"that Idempotency-Key was already used for a {row.entry_type}; "
                "a key is bound to one operation"
            ),
            status_code=409,
            retryable=False,
        )
    return row


def _ref_id(row: Any) -> uuid.UUID:
    return uuid.UUID(str(row.ref))


def _lock_reservation(
    conn: Connection, tenant_id: uuid.UUID, reservation_id: uuid.UUID
) -> Reservation:
    row = conn.execute(
        text(
            "SELECT id, amount, state, operation_ref FROM credit_reservations "
            "WHERE id = :id AND tenant_id = :tenant_id FOR UPDATE"
        ),
        {"id": reservation_id, "tenant_id": tenant_id},
    ).one_or_none()
    if row is None:
        raise AdaptError(
            code="route_unknown",
            message="no such reservation for this tenant",
            status_code=404,
            retryable=False,
        )
    return Reservation(
        id=row.id, amount=row.amount, state=row.state, operation_ref=row.operation_ref
    )


def _reservation_by_id(
    conn: Connection,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    *,
    amount: int | None,
) -> Reservation:
    """Load a reservation for a replay, reporting the first call's amount."""
    row = conn.execute(
        text(
            "SELECT id, amount, state, operation_ref FROM credit_reservations "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": reservation_id, "tenant_id": tenant_id},
    ).one()
    return Reservation(
        id=row.id,
        amount=row.amount if amount is None else amount,
        state=row.state,
        operation_ref=row.operation_ref,
    )


def _write_ledger(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID | None,
    delta: int,
    entry_type: str,
    ref: str | None,
    idempotency_key: str,
    period_start: date | None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO credit_ledger "
            "(tenant_id, grant_id, delta, entry_type, ref, idempotency_key, period_start) "
            "VALUES (:tenant_id, :grant_id, :delta, :entry_type, :ref, :key, :period_start)"
        ),
        {
            "tenant_id": tenant_id,
            "grant_id": grant_id,
            "delta": delta,
            "entry_type": entry_type,
            "ref": ref,
            "key": idempotency_key,
            "period_start": period_start,
        },
    )
