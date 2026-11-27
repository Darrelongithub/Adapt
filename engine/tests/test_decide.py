"""Increment 4: `decide` v1 — the four actions, six abstentions, one promise.

Three kinds of test live here, and they are deliberately separated:

* **Pure** — `evaluate(Inputs, now, decision_id)` is a function, so the rules,
  the citations, the abstention shapes and the byte-level determinism are tested
  without a database or a clock.
* **Stored** — the decisions table, the idempotency key, the replay endpoint, the
  credit reservation, and the tenant boundary.
* **Gates** — G1 (goldens and 100 replays), G3 (a recommendation with no
  resolvable claim cannot ship; a claim below its evidence floor cannot be
  written), and the red-then-green for the evidence floor at the end.

The claim that matters most is the absence in an abstention: no `value` field at
all, and a `needs` object that says what would change the answer.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.decide.constructs import (
    ABSTENTION_REASONS,
    ACTIONS,
    SERVED_REASONS,
)
from adapt_engine.decide.service import Inputs, evaluate
from adapt_engine.main import create_app
from adapt_engine.mastery.bkt import DEFAULT_PARAMS
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
DECISION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
GOLDEN = Path(__file__).parent / "golden" / "decisions"

CLAIMS = {
    "worked_examples": "clm_0001",
    "retrieval_practice": "clm_0002",
    "spacing": "clm_0003",
    "desirable_difficulty": "clm_0004",
}


def _inputs(**overrides: object) -> Inputs:
    base: dict[str, object] = {
        "learner_id": "learn-decide-0001",
        "skill": "skill.fractions",
        "tier": "core",
        "trials": 20,
        "correct": 12,
        "model_p": 0.5,
        "prior_version": 1,
        "prior_source": "documented_default",
        "params": DEFAULT_PARAMS,
        "recent": (True, False, True),
        "flagged_share": 0.0,
        "claims": dict(CLAIMS),
    }
    base.update(overrides)
    return Inputs(**base)  # type: ignore[arg-type]


def _outcome(inputs: Inputs, *, now: datetime = NOW):
    return evaluate(inputs, now=now, decision_id=DECISION_ID)


# --- the pure core --------------------------------------------------------


def test_evaluate_is_a_function() -> None:
    """Same inputs + same clock + same id ⇒ the same body, twice."""
    first = _outcome(_inputs()).body
    second = _outcome(_inputs()).body
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_body_survives_a_round_trip_through_its_stored_inputs() -> None:
    """What replay does: serialise the snapshot, reload it, recompute, compare."""
    inputs = _inputs(trials=24, correct=19, recent=(False, False, False))
    stored = json.loads(json.dumps(inputs.as_dict(), sort_keys=True))
    recomputed = evaluate(Inputs.from_dict(stored), now=NOW, decision_id=DECISION_ID)
    assert recomputed.body == _outcome(inputs).body


def test_a_served_decision_carries_cites_an_interval_and_reason_codes() -> None:
    """AGENT_M1 §4: no recommendation ships without a resolvable citation."""
    body = _outcome(_inputs()).body
    assert body["abstain"] is False
    assert body["claim_ids"] and all(cid for cid in body["claim_ids"])
    assert set(body["reason_codes"]) <= set(SERVED_REASONS)
    recommendation = body["recommendations"][0]
    assert recommendation["cites"] == body["claim_ids"]
    confidence = recommendation["confidence"]
    assert confidence["interval"]["low"] <= confidence["point"] <= confidence["interval"]["high"]
    assert confidence["interval"]["mass"] == 0.95
    assert recommendation["why"]
    assert recommendation["expires_at"] > body["issued_at"]
    assert body["level"]["served"] == "L1"
    assert body["level"]["limited_by"] is None


def test_an_abstention_has_no_value_field_and_names_its_need() -> None:
    """§6B's rule, applied to decide: a number we declined to give is absent."""
    body = _outcome(_inputs(trials=3, correct=2)).body
    assert body["abstain"] is True
    assert body["action"] == "abstain"
    assert "value" not in body
    assert body["reason"] in ABSTENTION_REASONS
    assert body["needs"], "an abstention without a resolution path is a dead end"
    assert "recommendations" not in body


def test_the_five_reachable_reasons_each_have_a_fixture() -> None:
    """METRICS §1.3 names six codes; five are reachable in `decide` v1."""
    fixtures = {
        # below the floor
        "insufficient_evidence": _inputs(trials=5, correct=4),
        # floor met, interval wider than the action threshold
        "low_confidence": _inputs(trials=8, correct=5),
        # confident estimate, last three answers disagree
        "conflicting_signals": _inputs(trials=20, correct=14, recent=(False, False, False)),
        # the host's instrumentation is the problem
        "data_quality": _inputs(flagged_share=0.6),
        # nothing to cite
        "unsupported_request": _inputs(claims={}),
    }
    for reason, inputs in fixtures.items():
        body = _outcome(inputs).body
        assert body["abstain"] is True, reason
        assert body["reason"] == reason, (reason, body["reason"])
        assert body["reason_codes"][0] == reason
    # The sixth is documented as unreachable and must not be quietly faked.
    assert "consent_withheld" in ABSTENTION_REASONS
    assert "consent_withheld" not in fixtures


def test_the_actions_follow_the_band() -> None:
    mid = _outcome(_inputs(trials=20, correct=12)).body
    assert mid["action"] == "next_item_difficulty"
    assert mid["recommendations"][0]["target"]["target_success"] == 0.78

    high = _outcome(_inputs(trials=60, correct=58)).body
    assert high["action"] == "spaced_review"
    assert high["recommendations"][0]["review_after_days"] == 7

    struggling = _outcome(_inputs(trials=20, correct=10, recent=(False, False, False))).body
    assert struggling["action"] == "worked_example"


def test_the_expertise_reversal_boundary_is_honoured() -> None:
    """The worked-example claim says not to use it with experts — so we don't.

    A learner at 0.62 who has just missed three in a row is the exact case where
    the claim's `does_not_apply_when` bites. We abstain as `conflicting_signals`
    rather than serving the action whose own evidence excludes this learner.
    """
    body = _outcome(_inputs(trials=20, correct=14, recent=(False, False, False))).body
    assert body["abstain"] is True
    assert body["reason"] == "conflicting_signals"
    assert body["needs"] == {"trials": 4}


def test_a_served_estimate_matches_the_mastery_endpoint() -> None:
    """Two endpoints, one number: `decide` reads mastery's own function."""
    from adapt_engine.mastery.bkt import State
    from adapt_engine.mastery.service import Prior, served_body

    inputs = _inputs(trials=30, correct=24)
    served = served_body(
        learner_id=inputs.learner_id,
        skill=inputs.skill,
        state=State(trials=inputs.trials, correct=inputs.correct, p=inputs.model_p),
        prior=Prior(
            skill=inputs.skill,
            version=1,
            params=inputs.params,
            source=inputs.prior_source,
        ),
        tier=inputs.tier,
    )
    body = _outcome(inputs).body
    assert body["estimate"]["value"] == served["value"]
    assert body["estimate"]["interval"] == served["interval"]


# --- goldens (G1) ---------------------------------------------------------

GOLDEN_CASES: list[dict[str, object]] = [
    {"name": "01_next_item_difficulty_mid", "inputs": {"trials": 20, "correct": 12}},
    {
        "name": "02_next_item_difficulty_low",
        "inputs": {"trials": 20, "correct": 7, "recent": (True, False, True)},
    },
    {
        "name": "03_spaced_review_high",
        "inputs": {"trials": 60, "correct": 58, "recent": (True, True, True)},
    },
    {
        "name": "04_worked_example_three_errors",
        "inputs": {"trials": 20, "correct": 10, "recent": (False, False, False)},
    },
    {"name": "05_abstain_insufficient_evidence", "inputs": {"trials": 5, "correct": 4}},
    {"name": "06_abstain_low_confidence", "inputs": {"trials": 8, "correct": 5}},
    {
        "name": "07_abstain_conflicting_signals",
        "inputs": {"trials": 20, "correct": 14, "recent": (False, False, False)},
    },
    {"name": "08_abstain_data_quality", "inputs": {"flagged_share": 0.6}},
]


def _golden_outcome(case: dict[str, object]) -> dict[str, object]:
    return _outcome(_inputs(**case["inputs"])).body  # type: ignore[arg-type]


def test_golden_decisions_match() -> None:
    """G1 for `decide`: eight bodies, byte-for-byte, from frozen inputs."""
    for case in GOLDEN_CASES:
        path = GOLDEN / f"{case['name']}.json"
        assert path.exists(), f"missing golden {path}"
        golden = json.loads(path.read_text())
        assert _golden_outcome(case) == golden["expected"], case["name"]


def test_the_golden_set_covers_every_action_and_reachable_reason() -> None:
    bodies = {case["name"]: _golden_outcome(case) for case in GOLDEN_CASES}
    actions = {b["action"] for b in bodies.values() if not b["abstain"]}
    assert actions == {"next_item_difficulty", "spaced_review", "worked_example"}
    reasons = {b["reason"] for b in bodies.values() if b["abstain"]}
    assert reasons == {
        "insufficient_evidence",
        "low_confidence",
        "conflicting_signals",
        "data_quality",
    }
    assert all(b["action"] in ACTIONS for b in bodies.values())


# --- stored decisions over HTTP -------------------------------------------


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"dec-{uuid.uuid4().hex[:8]}", name="Decide Test")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))


@pytest.fixture
def client(app_engine: Engine) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def connection(client: TestClient, admin_key):
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 250_000,
            "scopes": ["observe", "decide", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(connection, key: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key or connection['key']['full_key']}"}


def _seed_credits(app_engine: Engine, tenant: uuid.UUID, amount: int = 1000) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=amount,
            # A purchased grant never expires (`credit_grants_purchased_never_expires`);
            # an expiring allowance would be `kind="monthly"`.
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )


def _answers(total: int, correct: int, *, tail: tuple[bool, ...] = ()) -> list[bool]:
    """An answer list with a given rate, spread so no run reaches straight-line.

    `straight_line` fires on 8+ identical outcomes in a row, so a test fixture of
    twenty correct answers in a row is *flagged* data rather than a master — which
    is the ingest layer working. Spreading the minority answers keeps the fixture
    honest, and `tail` forces the last few outcomes for the streak rules.
    """
    body_total = total - len(tail)
    body_correct = correct - sum(1 for value in tail if value)
    out: list[bool] = []
    seen = 0
    for index in range(body_total):
        target = round((index + 1) * body_correct / body_total) if body_total else 0
        out.append(target > seen)
        seen = max(seen, target)
    return out + list(tail)


def _observe(client: TestClient, connection, learner: str, answers: list[bool], **extra: object):
    events = [
        {
            "learner_id": learner,
            "skill": extra.get("skill", "skill.fractions"),
            "item_id": f"item-{index:03d}",
            "correct": correct,
            "ts": (NOW - timedelta(minutes=60 - index)).isoformat(),
            "seq": index,
            "session_id": f"s-{learner[-4:]}-{extra.get('skill', 'skill.fractions')[:6]}",
        }
        for index, correct in enumerate(answers)
    ]
    resp = client.post(
        "/v1/observe",
        json={"events": events},
        headers={**_auth(connection), "Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text
    return resp


def _decide(
    client: TestClient, connection, learner: str, *, key: str | None = None, **extra: object
):
    return client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": extra.get("skill", "skill.fractions")},
        headers={**_auth(connection), "Idempotency-Key": key or f"decide-{uuid.uuid4().hex}"},
    )


def test_a_served_decision_is_stored_and_settled_once(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """A serve reserves, then settles inside the same transaction.

    Increment 5 settles every served decision rather than leaving the hold for
    the nightly job: one decision, one credit, visible in `credit_consumption`
    the moment the response is written.
    """
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0010"
    _observe(client, connection, learner, _answers(30, 26))

    resp = _decide(client, connection, learner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["abstain"] is False
    assert body["action"] in ACTIONS and body["action"] != "abstain"
    assert resp.headers["X-Adapt-Replayed"] == "false"

    with tenant_transaction(app_engine, tenant) as conn:
        row = conn.execute(
            text("SELECT action, abstain, reservation_id FROM decisions WHERE id = :id"),
            {"id": uuid.UUID(body["decision_id"])},
        ).one()
        held = conn.execute(
            text("SELECT coalesce(sum(amount), 0) FROM credit_reservations WHERE state = 'held'")
        ).scalar_one()
        settled = conn.execute(
            text("SELECT count(*) FROM credit_reservations WHERE state = 'settled'")
        ).scalar_one()
        consumed = conn.execute(
            text("SELECT coalesce(sum(consumed), 0) FROM credit_consumption")
        ).scalar_one()
    assert (row.action, row.abstain) == (body["action"], False)
    assert row.reservation_id is not None
    assert (held, settled, consumed) == (0, 1, 1)


def test_the_same_key_returns_the_stored_decision_and_charges_nothing_more(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0011"
    _observe(client, connection, learner, _answers(24, 18))
    key = f"decide-{uuid.uuid4().hex}"

    first = _decide(client, connection, learner, key=key)
    second = _decide(client, connection, learner, key=key)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert second.headers["X-Adapt-Replayed"] == "true"

    with tenant_transaction(app_engine, tenant) as conn:
        count = conn.execute(text("SELECT count(*) FROM decisions")).scalar_one()
        settled = conn.execute(
            text("SELECT count(*) FROM credit_reservations WHERE state = 'settled'")
        ).scalar_one()
        ledger = conn.execute(
            text("SELECT count(*) FROM credit_ledger WHERE entry_type <> 'grant'")
        ).scalar_one()
    assert (count, settled) == (1, 1)
    assert ledger == 2, "one reserve and one settle; the replay wrote nothing"


def test_a_retry_with_a_different_body_is_a_conflict(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0012"
    _observe(client, connection, learner, _answers(24, 18))
    key = f"decide-{uuid.uuid4().hex}"
    assert _decide(client, connection, learner, key=key).status_code == 200

    changed = client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": "skill.other"},
        headers={**_auth(connection), "Idempotency-Key": key},
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "conflict"


def test_replay_recomputes_from_the_stored_inputs(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0013"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    replay = client.post(f"/v1/decisions/{served['decision_id']}/replay", headers=_auth(connection))
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-Adapt-Replayed"] == "identical"
    assert replay.json()["decision_id"] == served["decision_id"]
    assert replay.json()["action"] == served["action"]


def test_replay_fails_loudly_when_a_stored_body_is_tampered(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID, owner_engine: Engine
) -> None:
    """The replay's teeth: a tampered row is a 500, not a quiet echo.

    The tampering is done as the **owner**, because the application role no longer
    holds `UPDATE` on `decisions` (migration 0017): a decision row is immutable to
    the thing that serves it, and only an operator with a database connection can
    change one. That is the attack the replay check exists to catch.
    """
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0014"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('adapt.tenant_id', :t, true)"), {"t": str(tenant)})
        conn.execute(
            text(
                "UPDATE decisions SET body = jsonb_set(body, '{action}', '\"spaced_review\"') "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(served["decision_id"])},
        )
        conn.commit()
    replay = client.post(f"/v1/decisions/{served['decision_id']}/replay", headers=_auth(connection))
    assert replay.status_code == 500
    assert replay.json()["error"]["code"] == "internal"


def test_a_hundred_stored_decisions_replay_byte_identically(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """AGENT_M1 §4 acceptance: 100 stored decisions, replayed, byte-identical."""
    _seed_credits(app_engine, tenant, amount=500)
    learners = [f"learn-bulk-{index:03d}" for index in range(10)]
    answers = _answers(24, 18)
    for learner in learners:
        _observe(client, connection, learner, answers)

    ids: list[str] = []
    for learner in learners:
        for _ in range(10):
            resp = _decide(client, connection, learner)
            assert resp.status_code == 200, resp.text
            ids.append(resp.json()["decision_id"])
    assert len(ids) == 100

    for decision_id in ids:
        replay = client.post(f"/v1/decisions/{decision_id}/replay", headers=_auth(connection))
        assert replay.status_code == 200, (decision_id, replay.text)
    stored = {}
    with tenant_transaction(app_engine, tenant) as conn:
        for decision_id in ids:
            stored[decision_id] = conn.execute(
                text("SELECT body FROM decisions WHERE id = :id"),
                {"id": uuid.UUID(decision_id)},
            ).scalar_one()
    assert len(stored) == 100


def test_an_abstention_is_free_and_a_serve_is_not(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Abstention is free, always — otherwise the honest answer costs money."""
    learner = "learn-decide-0015"
    _observe(client, connection, learner, [True, False, True])

    abstained = _decide(client, connection, learner)
    assert abstained.status_code == 200, abstained.text
    assert abstained.json()["abstain"] is True

    rich = "learn-decide-0016"
    _observe(client, connection, rich, _answers(24, 18))
    served = _decide(client, connection, rich)
    assert served.status_code == 402
    assert served.json()["error"]["code"] == "quota_exhausted"

    with tenant_transaction(app_engine, tenant) as conn:
        ledger = conn.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one()
        decisions = conn.execute(text("SELECT count(*) FROM decisions")).scalar_one()
    assert ledger == 0, "an abstention must not touch the ledger"
    assert decisions == 1, "the abstention is stored; the unaffordable serve is not"


def test_the_decide_scope_is_enforced(client: TestClient, admin_key, connection) -> None:
    narrow = client.post(
        "/v1/admin/keys",
        json={"scopes": ["observe"], "env": "test"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert narrow.status_code == 200, narrow.text
    resp = client.post(
        "/v1/decide",
        json={"learner_id": "learn-decide-0017", "skill": "skill.fractions"},
        headers={
            "Authorization": f"Bearer {narrow.json()['full_key']}",
            "Idempotency-Key": f"decide-{uuid.uuid4().hex}",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
    assert resp.json()["error"]["details"] == {"tier_required": "core"}


def test_a_paused_connection_cannot_decide(
    client: TestClient, connection, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0018"
    _observe(client, connection, learner, _answers(24, 18))
    paused = client.patch(
        f"/v1/admin/connections/{connection['connection']['id']}",
        json={"status": "paused"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert paused.status_code == 200, paused.text

    resp = _decide(client, connection, learner)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "connection_paused"


def test_an_unknown_learner_is_a_404(client: TestClient, connection) -> None:
    resp = _decide(client, connection, "learn-never-seen")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "learner_unknown"


def test_a_decision_is_not_visible_to_another_tenant(
    client: TestClient, connection, admin_key, app_engine: Engine, tenant: uuid.UUID, database
) -> None:
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0019"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    other = provision_tenant(database, slug=f"dec-{uuid.uuid4().hex[:8]}", name="Other")
    other_key = bootstrap_key(database, other, scopes=("observe", "decide"))
    resp = client.post(
        f"/v1/decisions/{served['decision_id']}/replay",
        headers={"Authorization": f"Bearer {other_key.full_key}"},
    )
    assert resp.status_code == 404


def test_follow_on_events_can_cite_the_decision(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """`decision_id` is recorded and resolvable — the ≥95% attribution metric."""
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0020"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    events = [
        {
            "learner_id": learner,
            "skill": "skill.fractions",
            "item_id": f"follow-{index}",
            "correct": True,
            "ts": NOW.isoformat(),
            "seq": index,
            "session_id": "follow-on-1",
            "decision_id": served["decision_id"] if index < 9 else str(uuid.uuid4()),
        }
        for index in range(10)
    ]
    resp = client.post(
        "/v1/observe",
        json={"events": events},
        headers={**_auth(connection), "Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text
    attribution = resp.json()["decision_attribution"]
    assert attribution["with_decision_id"] == 10
    assert attribution["resolvable"] == 9
    assert attribution["resolvable_share"] == 0.9


# --- G3: the claim set is gated at write time ----------------------------


def test_the_seed_set_is_five_citable_claims(app_engine: Engine, tenant: uuid.UUID) -> None:
    from adapt_engine.decide import claims as claim_service

    with tenant_transaction(app_engine, tenant) as conn:
        written = claim_service.ensure_seeded(conn, tenant_id=tenant)
        assert written == 5
        assert claim_service.ensure_seeded(conn, tenant_id=tenant) == 0, "seeding is idempotent"
        assert conn.execute(text("SELECT count(*) FROM claims")).scalar_one() == 5
        assert claim_service.cite(conn, tenant_id=tenant, construct="worked_examples") == "clm_0001"


def test_a_claim_below_its_evidence_floor_cannot_be_written(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """G3: test the refusal, not the filter."""
    with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(IntegrityError) as caught:
            conn.execute(
                text(
                    """
                    INSERT INTO claims (tenant_id, id, construct, statement, certainty,
                        applies_when, does_not_apply_when, evidence_n, evidence_floor,
                        sources, status)
                    VALUES (:tenant_id, 'clm_9001', 'spacing', 'thin', 'low',
                        ARRAY['x'], ARRAY['y'], 10, 500, '[]'::jsonb, 'active')
                    """
                ),
                {"tenant_id": tenant},
            )
    assert "claims_evidence_clears_floor" in str(caught.value)


def test_a_claim_for_an_ungated_construct_cannot_be_written(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(IntegrityError) as caught:
            conn.execute(
                text(
                    """
                    INSERT INTO claims (tenant_id, id, construct, statement, certainty,
                        applies_when, does_not_apply_when, evidence_n, evidence_floor,
                        sources, status)
                    VALUES (:tenant_id, 'clm_9002', 'horoscopes', 'unfalsifiable', 'low',
                        ARRAY['x'], ARRAY['y'], 1000, 500, '[]'::jsonb, 'active')
                    """
                ),
                {"tenant_id": tenant},
            )
    assert "claims_construct_gated" in str(caught.value)


def test_a_claim_without_a_boundary_cannot_be_written(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """`does_not_apply_when` is mandatory; an empty array is not a boundary."""
    with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(IntegrityError) as caught:
            conn.execute(
                text(
                    """
                    INSERT INTO claims (tenant_id, id, construct, statement, certainty,
                        applies_when, does_not_apply_when, evidence_n, evidence_floor,
                        sources, status)
                    VALUES (:tenant_id, 'clm_9003', 'spacing', 'always true', 'high',
                        ARRAY['x'], ARRAY[]::text[], 1000, 500, '[]'::jsonb, 'active')
                    """
                ),
                {"tenant_id": tenant},
            )
    assert "claims_boundary_stated" in str(caught.value)


# --- the measurement the report quotes ------------------------------------


def test_the_served_split_interval_width_and_latency_are_measured(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID, capsys
) -> None:
    """What the product looks like from outside: how often it speaks, how wide.

    Synthetic learners with mastery drawn from 0.1-0.9 and between 4 and 40
    observations, seeded so the numbers are reproducible. The test asserts only
    that the corpus is informative (it contains both outcomes) and that the SLO
    holds; the numbers are printed and quoted in the increment report, because a
    served share and a median interval width are product facts, not test facts.
    """
    import random
    import statistics
    import time

    rng = random.Random(20261118)
    _seed_credits(app_engine, tenant, amount=500)
    learners = [f"learn-measure-{index:03d}" for index in range(200)]
    for learner in learners:
        mastery = rng.uniform(0.1, 0.9)
        trials = rng.randint(4, 40)
        answers = [rng.random() < mastery for _ in range(trials)]
        _observe(client, connection, learner, answers)

    durations: list[float] = []
    served: list[float] = []
    widths: list[float] = []
    reasons: dict[str, int] = {}
    for learner in learners:
        started = time.perf_counter()
        resp = _decide(client, connection, learner)
        durations.append((time.perf_counter() - started) * 1000)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["abstain"]:
            reasons[body["reason"]] = reasons.get(body["reason"], 0) + 1
        else:
            served.append(body["recommendations"][0]["confidence"]["point"])
            interval = body["recommendations"][0]["confidence"]["interval"]
            widths.append(interval["high"] - interval["low"])

    total = len(learners)
    served_share = len(served) / total
    durations.sort()
    summary = {
        "decisions": total,
        "served": len(served),
        "abstained": total - len(served),
        "served_share": round(served_share, 3),
        "abstained_by_reason": dict(sorted(reasons.items())),
        "median_interval_width": round(statistics.median(widths), 3) if widths else None,
        "p50_ms": round(durations[len(durations) // 2], 2),
        "p95_ms": round(durations[int(len(durations) * 0.95)], 2),
    }
    print("\nDECIDE MEASUREMENT " + json.dumps(summary, sort_keys=True))

    assert 0.2 < served_share < 0.9, "the corpus must contain both outcomes"
    assert summary["median_interval_width"] is not None
    assert summary["median_interval_width"] <= 0.45, "a served advice carries a usable interval"
    assert summary["p95_ms"] < 250, f"p95 {summary['p95_ms']}ms exceeds the interactive budget"


def test_a_decision_row_is_immutable_and_replays_are_appended(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """A replay logs a row and never touches the decision it re-served.

    `AGENT_M1` §6: the stored body is the evidence G1 rests on. A counter on the
    decision row made that evidence mutable, so the counter became an append-only
    `decision_replays` table, and `UPDATE` on `decisions` was revoked from the
    application role.
    """
    _seed_credits(app_engine, tenant)
    learner = "learn-decide-0031"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    for _ in range(3):
        assert (
            client.post(
                f"/v1/decisions/{served['decision_id']}/replay", headers=_auth(connection)
            ).status_code
            == 200
        )

    with tenant_transaction(app_engine, tenant) as conn:
        replays = conn.execute(
            text("SELECT count(*) FROM decision_replays WHERE decision_id = :id"),
            {"id": uuid.UUID(served["decision_id"])},
        ).scalar_one()
        decision_rows = conn.execute(text("SELECT count(*) FROM decisions")).scalar_one()
        with pytest.raises(Exception) as caught:
            conn.execute(text("UPDATE decisions SET body = body"))
        conn.rollback()
    assert replays == 3
    assert decision_rows == 1
    assert "permission denied" in str(caught.value).lower()
