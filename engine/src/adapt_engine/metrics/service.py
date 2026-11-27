"""Abstention, implemented as `METRICS.md` §1 states it — including §1.8.

Three rules from the document shape everything here, and each of them exists
because the obvious implementation of the other two lies:

1. **Both grains, always.** A request-level rate collapses "the engine said
   nothing" and "the engine answered about mastery but not about calibration"
   into one number. The response therefore carries `rate`/`numerator`/
   `denominator` at the requested grain *and* a `by_construct` breakdown; at M1 a
   request evaluates one construct, so the two grains agree numerically — which
   is a fact about M1, not a licence to report only one.
2. **The denominator is a definition, not a count of rows.** `decide_requests_valid`
   is 2xx decisions minus replays, minus paused/revoked connections, minus test
   traffic. Every one of those subtractions is reported in `exclusions` with its
   own number, so a reader can add them back and see the rows the store holds.
3. **Served rate travels with abstention (§1.8), from the same denominator.** A
   tenant with no validated constructs can show 0 % abstention while receiving
   nothing at all. Showing abstention alone is a dashboard that can be gamed by
   accident, so `served` is computed from the same denominator and never omitted.

What this module deliberately does **not** invent: `entitlement_limits`,
`not_validated`, `not_capable`, `refusals` and `errors`. They are real series
(`METRICS.md` §1.2) and none of them is stored anywhere at M1 — a refusal is a 4xx
that writes no row, a 5xx rolls back, and the three axes live in the contract and
the validation harness rather than in `decisions`. They are returned as `null`
and listed in `unmeasured`, because `0` would be a claim and `null` is the truth.
`KNOWN_GAPS.md` §28 records the store that would fix it.
"""

from __future__ import annotations

import math
import statistics
import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

#: A change to any denominator or exclusion in this module ships as a new value.
METRIC_VERSION: Final = "abstention@1"

#: 95 % two-sided Wilson score interval.
WILSON_Z: Final = 1.959963984540054

#: `METRICS.md` §1.5: one connection above 5x the normal repeat ratio is polling.
POLLING_MULTIPLE: Final = 5.0

#: A ratio needs volume before it means anything: ten repeats on one
#: `(learner, construct)` is a pattern, one retry is a retry.
POLLING_MIN_REPEATS: Final = 10

#: A connection joins the baseline only once it has enough pairs to have a ratio.
POLLING_BASELINE_PAIRS: Final = 10

#: Series that exist in the document and have no store in the engine yet.
UNMEASURED: Final = (
    "entitlement_limits",
    "not_validated",
    "not_capable",
    "refusals",
    "errors",
)

_VALID = "NOT test_traffic AND (connection_status IS NULL OR connection_status = 'active')"

_WINDOW = """
    SELECT d.id, d.abstain, d.reason, d.test_traffic, d.skill AS construct,
           d.connection_id, d.learner_id,
           c.status AS connection_status,
           t.tier AS tier
      FROM decisions d
      LEFT JOIN connections c ON c.id = d.connection_id
      JOIN tenants t ON t.id = d.tenant_id
     WHERE d.tenant_id = :tenant_id
       AND d.issued_at >= :start
       AND d.issued_at < :end
       AND (CAST(:connection_id AS uuid) IS NULL OR d.connection_id = CAST(:connection_id AS uuid))
       AND (CAST(:construct AS text) IS NULL OR d.skill = CAST(:construct AS text))
       AND (CAST(:tier AS text) IS NULL OR t.tier = CAST(:tier AS text))
"""


def wilson(successes: int, total: int, *, z: float = WILSON_Z) -> dict[str, Any]:
    """The Wilson score interval for a proportion, or nulls for an empty sample.

    Wilson rather than Wald: at the edges a Wald interval leaves [0, 1] and a
    95 % band around a small sample is not a band at all. `METRICS.md` §1 asks
    for Wilson by name.
    """
    if total <= 0:
        return {"low": None, "high": None, "confidence": 0.95}
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {
        "low": round(max(0.0, centre - half), 4),
        "high": round(min(1.0, centre + half), 4),
        "confidence": 0.95,
    }


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
        "interval": wilson(numerator, denominator),
    }


def report(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    connection_id: uuid.UUID | None = None,
    construct: str | None = None,
    tier: str | None = None,
    grain: str = "request",
) -> dict[str, Any]:
    """The abstention report for one tenant, one window and the given filters."""
    params = {
        "tenant_id": tenant_id,
        "start": start,
        "end": end,
        "connection_id": str(connection_id) if connection_id else None,
        "construct": construct,
        "tier": tier,
    }

    totals = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT count(*)                                              AS window_rows,
                   count(*) FILTER (WHERE test_traffic)                  AS test_traffic,
                   count(*) FILTER (WHERE NOT test_traffic
                                      AND connection_status IS NOT NULL
                                      AND connection_status <> 'active') AS paused_or_revoked,
                   count(*) FILTER (WHERE {_VALID})                      AS denominator,
                   count(*) FILTER (WHERE {_VALID} AND abstain)          AS numerator
              FROM rows
            """
        ),
        params,
    ).one()

    abstention = _rate(int(totals.numerator), int(totals.denominator))
    served = _rate(int(totals.denominator) - int(totals.numerator), int(totals.denominator))

    body: dict[str, Any] = {
        "metric_version": METRIC_VERSION,
        "grain": grain,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "filters": {
            "connection_id": str(connection_id) if connection_id else None,
            "construct": construct,
            "tier": tier,
        },
        "rate": abstention["rate"],
        "numerator": abstention["numerator"],
        "denominator": abstention["denominator"],
        "interval": abstention["interval"],
        "served": served,
        "reasons": _reasons(conn, params),
        "by_construct": _by_construct(conn, params),
        "by_connection": _by_connection(conn, params),
        "exclusions": {
            "test_traffic": int(totals.test_traffic),
            "paused_or_revoked": int(totals.paused_or_revoked),
            "replays": _replays(conn, params),
        },
        "abstention_polling": _polling(conn, params),
    }
    for name in UNMEASURED:
        body[name] = None
    body["unmeasured"] = list(UNMEASURED)
    return body


def _reasons(conn: Connection, params: dict[str, Any]) -> dict[str, int]:
    rows = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT reason, count(*) AS n
              FROM rows
             WHERE {_VALID} AND abstain
             GROUP BY reason
             ORDER BY reason
            """
        ),
        params,
    ).all()
    return {str(row.reason): int(row.n) for row in rows}


def _by_construct(conn: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    """The construct grain: one entry per `(evaluation)` construct in the window."""
    totals = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT construct,
                   count(*)                                     AS denominator,
                   count(*) FILTER (WHERE abstain)              AS numerator
              FROM rows
             WHERE {_VALID}
             GROUP BY construct
             ORDER BY construct
            """
        ),
        params,
    ).all()
    reasons = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT construct, reason, count(*) AS n
              FROM rows
             WHERE {_VALID} AND abstain
             GROUP BY construct, reason
             ORDER BY construct, reason
            """
        ),
        params,
    ).all()

    by_construct: dict[str, dict[str, int]] = {}
    for row in reasons:
        by_construct.setdefault(str(row.construct), {})[str(row.reason)] = int(row.n)

    out: list[dict[str, Any]] = []
    for row in totals:
        abstention = _rate(int(row.numerator), int(row.denominator))
        out.append(
            {
                "construct": str(row.construct),
                "rate": abstention["rate"],
                "numerator": abstention["numerator"],
                "denominator": abstention["denominator"],
                "interval": abstention["interval"],
                "served": _rate(int(row.denominator) - int(row.numerator), int(row.denominator)),
                "reasons": by_construct.get(str(row.construct), {}),
            }
        )
    return out


def _by_connection(conn: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    """The connection dimension, so a single broken host cannot hide in a total."""
    totals = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT connection_id,
                   count(*)                        AS denominator,
                   count(*) FILTER (WHERE abstain) AS numerator
              FROM rows
             WHERE {_VALID}
             GROUP BY connection_id
             ORDER BY connection_id
            """
        ),
        params,
    ).all()
    out: list[dict[str, Any]] = []
    for row in totals:
        abstention = _rate(int(row.numerator), int(row.denominator))
        out.append(
            {
                "connection_id": None if row.connection_id is None else str(row.connection_id),
                "rate": abstention["rate"],
                "numerator": abstention["numerator"],
                "denominator": abstention["denominator"],
                "interval": abstention["interval"],
                "served": _rate(int(row.denominator) - int(row.numerator), int(row.denominator)),
            }
        )
    return out


def _replays(conn: Connection, params: dict[str, Any]) -> int:
    """Replays of decisions in the window, from the append-only log.

    A replay writes no decision and no money; it writes one row here, so
    "excluded" is a count rather than a belief.
    """
    row = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT count(*) AS replays
              FROM decision_replays r
             WHERE r.decision_id IN (SELECT id FROM rows)
            """
        ),
        params,
    ).one()
    return int(row.replays)


def _polling(conn: Connection, params: dict[str, Any]) -> dict[str, Any]:
    """`METRICS.md` §1.5: one connection at 5x the normal repeat ratio is polling.

    A repeat is a second `decide` for the same `(learner, construct)`: the
    documented behaviour of a host retrying until it gets a non-abstention. The
    baseline is the median ratio of connections with enough pairs to have one, and
    the alert needs volume — a single retry is not a pattern, and a tenant with no
    baseline cannot be five times it.
    """
    rows = conn.execute(
        text(
            f"""
            WITH rows AS ({_WINDOW})
            SELECT connection_id, learner_id, construct, count(*) AS requests
              FROM rows
             WHERE {_VALID} AND connection_id IS NOT NULL
             GROUP BY connection_id, learner_id, construct
            """
        ),
        params,
    ).all()

    per_connection: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = per_connection.setdefault(str(row.connection_id), {"pairs": 0, "repeats": 0})
        bucket["pairs"] += 1
        bucket["repeats"] += max(0, int(row.requests) - 1)

    ratios = {
        key: (value["repeats"] / value["pairs"] if value["pairs"] else 0.0)
        for key, value in per_connection.items()
    }
    baseline_pool = [ratios[key] for key, value in per_connection.items() if value["pairs"] >= 10]
    if baseline_pool:
        baseline = round(statistics.median(baseline_pool), 4)
        baseline_source = f"median of {len(baseline_pool)} connection(s) with >= 10 pairs"
    elif ratios:
        baseline = round(statistics.median(list(ratios.values())), 4)
        baseline_source = "median of every connection in the window"
    else:
        baseline = 0.0
        baseline_source = "no connection in the window"

    connections: list[dict[str, Any]] = []
    alerting: list[dict[str, Any]] = []
    for key in sorted(per_connection):
        value = per_connection[key]
        ratio = round(ratios[key], 4)
        # A zero baseline is not a multiplier: nothing else repeats, so any
        # connection that repeats a lot is the outlier by definition.
        multiple = round(ratio / baseline, 4) if baseline > 0 else None
        fires = value["repeats"] >= POLLING_MIN_REPEATS and (
            (multiple is not None and multiple > POLLING_MULTIPLE)
            or (multiple is None and ratio > 0)
        )
        entry = {
            "connection_id": key,
            "repeat_ratio": ratio,
            "multiple_of_baseline": multiple,
            "pairs": value["pairs"],
            "repeats": value["repeats"],
            "alerting": fires,
        }
        connections.append(entry)
        if fires:
            alerting.append(entry)

    return {
        "multiple": POLLING_MULTIPLE,
        "min_repeats": POLLING_MIN_REPEATS,
        "baseline_repeat_ratio": baseline,
        "baseline_source": baseline_source,
        "alerting": alerting,
        "by_connection": connections,
    }
