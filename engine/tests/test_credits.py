"""Credits: the ledger, the drawdown order, and the two races that matter.

Three properties are load-bearing, and each has a test that fails loudly if it
goes away:

* **a balance cannot go negative** — 100 concurrent reservations against 10
  credits yield exactly ten successes, and the invariant
  ``granted = available + held + consumed`` holds afterwards;
* **a replay is not a second charge** — the same `Idempotency-Key` five times
  writes one ledger row and returns one response body;
* **an overdraw is refused, not clamped** — settling more than was held is a
  409, because a caller that believes it spent 12 credits must not be told 10.

The drawdown order is asserted grant by grant, not described in a comment.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from tests.pg import bootstrap_key, grant_credits, provision_tenant, test_database_engine  # noqa: F401


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"credits-{uuid.uuid4().hex[:10]}", name="Credits")


def _balance(engine: Engine, tenant_id: uuid.UUID) -> credits.Balance:
    with tenant_transaction(engine, tenant_id) as conn:
        return credits.balance(conn, tenant_id)


def _reserve(engine: Engine, tenant_id: uuid.UUID, amount: int, ref: str, key: str | None = None):
    key = key or f"reserve-{uuid.uuid4()}"
    return credits.run_idempotent(
        engine,
        tenant_id,
        lambda conn: credits.reserve(
            conn, tenant_id=tenant_id, amount=amount, operation_ref=ref, idempotency_key=key
        ),
    )


def _settle(engine: Engine, tenant_id: uuid.UUID, reservation_id, amount: int, key: str | None = None):
    key = key or f"settle-{uuid.uuid4()}"
    return credits.run_idempotent(
        engine,
        tenant_id,
        lambda conn: credits.settle(
            conn,
            tenant_id=tenant_id,
            reservation_id=reservation_id,
            amount=amount,
            idempotency_key=key,
        ),
    )


def _release(engine: Engine, tenant_id: uuid.UUID, reservation_id, key: str | None = None):
    key = key or f"release-{uuid.uuid4()}"
    return credits.run_idempotent(
        engine,
        tenant_id,
        lambda conn: credits.release(
            conn, tenant_id=tenant_id, reservation_id=reservation_id, idempotency_key=key
        ),
    )


def test_balance_is_a_query_over_grants(app_engine: Engine, database, tenant: uuid.UUID) -> None:
    grant_credits(database, tenant, kind="included", amount=10, expires_at=_days(30))
    grant_credits(database, tenant, kind="purchased", amount=5, expires_at=None)

    current = _balance(app_engine, tenant)
    assert (current.available, current.held, current.consumed, current.granted) == (15, 0, 0, 15)
    assert current.invariant_holds


def _days(n: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=n)


def test_reserve_settle_release_keep_the_invariant(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    held = _reserve(app_engine, tenant, 4, "op-1")
    assert held.state == "held"
    after_hold = _balance(app_engine, tenant)
    assert (after_hold.available, after_hold.held, after_hold.consumed) == (6, 4, 0)
    assert after_hold.invariant_holds

    settled = _settle(app_engine, tenant, held.id, 3)
    assert settled.state == "settled"
    after_settle = _balance(app_engine, tenant)
    # 4 held, 3 consumed, 1 returned to the grant.
    assert (after_settle.available, after_settle.held, after_settle.consumed) == (7, 0, 3)
    assert after_settle.invariant_holds

    another = _reserve(app_engine, tenant, 7, "op-2")
    released = _release(app_engine, tenant, another.id)
    assert released.state == "released"
    after_release = _balance(app_engine, tenant)
    assert (after_release.available, after_release.held, after_release.consumed) == (7, 0, 3)
    assert after_release.invariant_holds


def test_drawdown_is_expiring_first_then_purchased_oldest_first(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    """The order that protects the customer's money, asserted per grant."""
    soon = grant_credits(database, tenant, kind="included", amount=5, expires_at=_days(5))
    later = grant_credits(database, tenant, kind="included", amount=5, expires_at=_days(30))
    old_pack = grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)
    new_pack = grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    remaining = {grant.id: grant.remaining for grant in _balance(app_engine, tenant).grants}
    assert [remaining[soon], remaining[later], remaining[old_pack], remaining[new_pack]] == [
        5,
        5,
        10,
        10,
    ]

    _reserve(app_engine, tenant, 8, "op-draw-1")
    remaining = {grant.id: grant.remaining for grant in _balance(app_engine, tenant).grants}
    # All 5 of the about-to-expire allowance, then 3 of the later one.
    assert [remaining[soon], remaining[later], remaining[old_pack], remaining[new_pack]] == [
        0,
        2,
        10,
        10,
    ]

    _reserve(app_engine, tenant, 5, "op-draw-2")
    remaining = {grant.id: grant.remaining for grant in _balance(app_engine, tenant).grants}
    # The 2 remaining included credits, then the oldest purchased pack.
    assert [remaining[soon], remaining[later], remaining[old_pack], remaining[new_pack]] == [
        0,
        0,
        7,
        10,
    ]


def test_a_reservation_spans_grants_and_a_release_returns_each_part(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    first = grant_credits(database, tenant, kind="included", amount=2, expires_at=_days(5))
    second = grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    held = _reserve(app_engine, tenant, 5, "op-span")
    _release(app_engine, tenant, held.id)

    remaining = {grant.id: grant.remaining for grant in _balance(app_engine, tenant).grants}
    assert remaining[first] == 2, "the allowance was not replenished"
    assert remaining[second] == 10, "the pack was not replenished"


def test_one_hundred_concurrent_reservations_against_ten_credits(
    database, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Exactly ten succeed, the balance lands at 0, and nothing goes negative."""
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    engine = test_database_engine(database, pool_size=24, max_overflow=8)

    def attempt(index: int) -> str:
        try:
            _reserve(engine, tenant, 1, f"op-{index}", key=f"concurrent-{index}")
            return "ok"
        except Exception as exc:  # AdaptError: quota_exhausted
            code = getattr(exc, "code", None)
            assert code == "quota_exhausted", f"unexpected failure: {exc!r}"
            return "refused"

    try:
        with ThreadPoolExecutor(max_workers=100) as pool:
            outcomes = list(pool.map(attempt, range(100)))
    finally:
        engine.dispose()

    assert outcomes.count("ok") == 10, outcomes.count("ok")
    assert outcomes.count("refused") == 90

    final = _balance(app_engine, tenant)
    assert final.available == 0
    assert final.held == 10
    assert final.invariant_holds

    with tenant_transaction(app_engine, tenant) as conn:
        minimum = conn.execute(text("SELECT min(remaining) FROM credit_grants")).scalar_one()
    assert minimum >= 0, "a grant went negative"


def test_replaying_a_reserve_five_times_writes_one_row_and_one_body(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    first = _reserve(app_engine, tenant, 3, "op-replay", key="fixed-key-1")
    for _ in range(4):
        again = _reserve(app_engine, tenant, 3, "op-replay", key="fixed-key-1")
        assert (again.id, again.amount, again.state, again.operation_ref) == (
            first.id,
            first.amount,
            first.state,
            first.operation_ref,
        )

    with tenant_transaction(app_engine, tenant) as conn:
        rows = conn.execute(
            text("SELECT reason, delta FROM credit_ledger WHERE idempotency_key = 'fixed-key-1'")
        ).all()
    assert len(rows) == 1
    assert rows[0].reason == "reserve"

    final = _balance(app_engine, tenant)
    assert (final.available, final.held) == (7, 3)


def test_one_key_cannot_be_reused_for_a_different_operation(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)
    held = _reserve(app_engine, tenant, 2, "op-a", key="shared-key")

    with pytest.raises(Exception) as refused:
        _settle(app_engine, tenant, held.id, 2, key="shared-key")
    assert getattr(refused.value, "code", None) == "conflict"


def test_settling_more_than_was_held_is_refused_not_clamped(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=100, expires_at=None)
    held = _reserve(app_engine, tenant, 5, "op-overdraw")

    with pytest.raises(Exception) as refused:
        _settle(app_engine, tenant, held.id, 6)
    assert getattr(refused.value, "code", None) == "conflict"

    after = _balance(app_engine, tenant)
    assert after.consumed == 0, "a refused settlement was charged anyway"
    assert after.held == 5
    assert after.invariant_holds


def test_settlement_after_a_grant_expires_debits_the_rest_and_forfeits_the_remainder(
    app_engine: Engine, owner_engine: Engine, database, tenant: uuid.UUID
) -> None:
    """§4's mid-flight case: the settled amount is debited, the rest does not come back."""
    grant_id = grant_credits(
        database, tenant, kind="included", amount=5, expires_at=_days(1)
    )
    held = _reserve(app_engine, tenant, 5, "op-expiring")
    assert _balance(app_engine, tenant).held == 5

    # The allowance expires while the operation is in flight. The credit
    # tables are FORCEd, so even the owner writes inside tenant context.
    with tenant_transaction(owner_engine, tenant) as conn:
        conn.execute(
            text("UPDATE credit_grants SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": grant_id},
        )

    settled = _settle(app_engine, tenant, held.id, 2)
    assert settled.amount == 2

    after = _balance(app_engine, tenant)
    assert after.consumed == 2, "the settled amount was not debited"
    assert after.held == 0
    assert after.available == 0, "expired credits were resurrected by the partial settle"
    assert after.granted == 5


def test_release_is_idempotent_and_never_debits(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)
    held = _reserve(app_engine, tenant, 4, "op-release")

    first = _release(app_engine, tenant, held.id, key="release-key-1")
    for _ in range(4):
        again = _release(app_engine, tenant, held.id, key="release-key-1")
        assert (again.id, again.state, again.amount) == (first.id, "released", 0)

    after = _balance(app_engine, tenant)
    assert (after.available, after.held, after.consumed) == (10, 0, 0)

    with tenant_transaction(app_engine, tenant) as conn:
        rows = conn.execute(
            text("SELECT delta, reason FROM credit_ledger WHERE idempotency_key = 'release-key-1'")
        ).all()
    assert len(rows) == 1
    assert rows[0].delta == 0, "a release debited"


def test_reserving_more_than_the_balance_is_insufficient_credits(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=3, expires_at=None)
    with pytest.raises(Exception) as refused:
        _reserve(app_engine, tenant, 4, "op-too-big")
    assert getattr(refused.value, "code", None) == "quota_exhausted"

    with tenant_transaction(app_engine, tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one() == 0


def test_the_ledger_is_append_only_for_every_role(
    owner_engine: Engine, app_engine: Engine, database, tenant
) -> None:
    """Even the owner cannot rewrite history: the trigger refuses."""
    grant_credits(database, tenant, kind="purchased", amount=5, expires_at=None)
    _reserve(app_engine, tenant, 2, "op-append-only")
    with tenant_transaction(app_engine, tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one() >= 1

    # Inside tenant context, so the row is really there and the trigger fires.
    with pytest.raises(DBAPIError):
        with tenant_transaction(owner_engine, tenant) as conn:
            conn.execute(
                text("UPDATE credit_ledger SET delta = 0 WHERE tenant_id = :t"), {"t": tenant}
            )
    with pytest.raises(DBAPIError):
        with tenant_transaction(owner_engine, tenant) as conn:
            conn.execute(text("DELETE FROM credit_ledger WHERE tenant_id = :t"), {"t": tenant})


def test_a_reservation_is_invisible_to_another_tenant(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    other = provision_tenant(database, slug=f"credits-b-{uuid.uuid4().hex[:8]}", name="Other")
    grant_credits(database, tenant, kind="purchased", amount=5, expires_at=None)
    held = _reserve(app_engine, tenant, 2, "op-mine")

    with pytest.raises(Exception) as refused:
        _settle(app_engine, other, held.id, 1)
    assert getattr(refused.value, "code", None) == "route_unknown"
