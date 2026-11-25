"""Ingest: one transaction per batch, and nothing written twice.

The order inside the transaction is the design:

1. **Idempotency first.** A replay must not spend quota, must not re-insert and
   must not re-flag. It returns the stored response — byte-identical, because it
   is the bytes we stored.
2. **Validate the whole batch.** Partial acceptance is silent data loss: a host
   that gets a 202 believes all 500 events are in. One bad event fails the batch
   and names the index and the field.
3. **Deduplicate events** on `(learner_id, session_id, seq)` when `seq` is
   present. Duplicates inside the batch and duplicates already stored are
   counted, not inserted.
4. **Count against the quota** — one statement that increments only if the cap
   allows it, so two concurrent batches cannot both take the last slot.
5. **Insert**, then write the batch row (with the response) last, so a failure
   anywhere rolls the whole thing back and the idempotency key stays unused.

Quota is counted here rather than in a nightly job because the host needs to
know *now* that it is out: a 402 the same second is a fallback the host can
take; a 402 tomorrow is a support ticket.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Uuid,
    column,
    insert,
    table,
    text,
)
from sqlalchemy.engine import Connection

from adapt_engine.observe import flags as flag_rules
from adapt_engine.observe.schema import Event

#: The batch's response, reused verbatim on a replay.
BatchResponse = dict[str, Any]


class QuotaExhausted(Exception):
    """The connection has no events left this month."""

    def __init__(self, *, resets_at: datetime, quota: int, used: int) -> None:
        super().__init__("event quota exhausted")
        self.resets_at = resets_at
        self.quota = quota
        self.used = used


class IdempotencyConflict(Exception):
    """The same key with a different body: a client bug, not a retry."""


@dataclass(frozen=True)
class StoredBatch:
    """Result of an ingest call, whether fresh or replayed."""

    response: BatchResponse
    batch_id: uuid.UUID
    replayed: bool


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month(now: datetime) -> datetime:
    start = month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def resets_at(now: datetime) -> datetime:
    """When the quota resets: the first instant of the next UTC month."""
    return next_month(now)


def request_hash(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def find_batch(conn: Connection, idempotency_key: str) -> tuple[BatchResponse, uuid.UUID] | None:
    row = conn.execute(
        text("SELECT id, response FROM observe_batches WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    ).one_or_none()
    if row is None:
        return None
    return row.response, row.id


def stored_hash(conn: Connection, idempotency_key: str) -> bytes | None:
    return conn.execute(
        text("SELECT request_hash FROM observe_batches WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    ).scalar_one_or_none()


def _bump_usage(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    period: datetime,
    count: int,
) -> int | None:
    """Increment the month's counter if it fits. Returns the new total or None.

    One statement, because the check and the increment have to be the same
    operation: `SELECT` then `UPDATE` is a race, and the race lets a tenant
    exceed its quota by exactly the number of concurrent requests.
    """
    row = conn.execute(
        text(
            """
            WITH cap AS (
                SELECT event_quota_monthly AS quota FROM connections WHERE id = :connection_id
            ), bump AS (
                INSERT INTO connection_event_usage AS u (tenant_id, connection_id, period, events)
                SELECT :tenant_id, :connection_id, :period, :count FROM cap
                 WHERE cap.quota >= :count
                ON CONFLICT (connection_id, period) DO UPDATE
                   SET events = u.events + :count
                 WHERE u.events + :count <= (SELECT quota FROM cap)
                RETURNING events
            )
            SELECT (SELECT events FROM bump) AS events
            """
        ),
        {
            "tenant_id": tenant_id,
            "connection_id": connection_id,
            "period": period.date(),
            "count": count,
        },
    ).one()
    return int(row.events) if row.events is not None else None


def usage_for(conn: Connection, *, connection_id: uuid.UUID, period: datetime) -> int:
    return int(
        conn.execute(
            text(
                "SELECT events FROM connection_event_usage"
                " WHERE connection_id = :id AND period = :period"
            ),
            {"id": connection_id, "period": period.date()},
        ).scalar_one_or_none()
        or 0
    )


def existing_event_keys(
    conn: Connection, keys: list[tuple[str, str, int]]
) -> set[tuple[str, str, int]]:
    """Which `(learner, session, seq)` triples are already stored.

    A partitioned table cannot carry a global unique index that excludes the
    partition key, so this is a check rather than a constraint — the one place
    in M1 where an invariant is enforced in code and backed by a test rather
    than by the schema (KNOWN_GAPS §2c).
    """
    if not keys:
        return set()
    # Join on the batch's own `(learner, session, seq)` triples, not on the
    # learner alone: a learner with a year of history made the first version of
    # this query grow with tenure — 500 events cost a 250,000-row read at 400k
    # stored events, and the load test said so (918 ms in one batch). Three
    # arrays and a three-column join is 500 index probes, whatever the history.
    learners = [learner for learner, _, _ in keys]
    sessions = [session for _, session, _ in keys]
    seqs = [seq for _, _, seq in keys]
    rows = conn.execute(
        text(
            """
            SELECT e.learner_id, e.session_id, e.seq
              FROM unnest(
                     CAST(:learners AS text[]),
                     CAST(:sessions AS text[]),
                     CAST(:seqs AS bigint[])
                   ) AS b(learner_id, session_id, seq)
              JOIN learning_events e
                ON e.learner_id = b.learner_id
               AND e.session_id = b.session_id
               AND e.seq = b.seq
            """
        ),
        {"learners": learners, "sessions": sessions, "seqs": seqs},
    ).all()
    stored = {(row.learner_id, row.session_id, row.seq) for row in rows}
    return stored & set(keys)


#: A Core construct for `learning_events`, so one batch is one INSERT with
#: 500 VALUES rather than 500 statements. The table is declared here, not
#: mapped: nothing in this module needs an ORM identity, and a mapped class
#: would invite loading events back through it (they are append-only rows read
#: by `SELECT`, in increment 4).
LEARNING_EVENTS = table(
    "learning_events",
    column("tenant_id", Uuid),
    column("connection_id", Uuid),
    column("batch_id", Uuid),
    column("learner_id", String),
    column("skill", String),
    column("item_id", String),
    column("correct", Boolean),
    column("ts", DateTime(timezone=True)),
    column("seq", Integer),
    column("session_id", String),
    column("device", String),
    column("input_mode", String),
    column("latency_ms", Integer),
    column("hint_used", Boolean),
    column("assisted", Boolean),
    column("difficulty_band", String),
    column("decision_id", String),
    column("response_digest", LargeBinary),
    column("response_length", Integer),
    column("validity_flags", ARRAY(String)),
)


def _insert_events(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    batch_id: uuid.UUID,
    events: list[Event],
    flags: list[tuple[str, ...]],
    now: datetime,
) -> None:
    """One statement for the batch, one round trip, one WAL record per commit."""
    del now  # the row's created_at is the database's `now()`
    if not events:
        return
    rows = [
        {
            "tenant_id": tenant_id,
            "connection_id": connection_id,
            "batch_id": batch_id,
            "learner_id": event.learner_id,
            "skill": event.skill,
            "item_id": event.item_id,
            "correct": event.correct,
            "ts": event.ts,
            "seq": event.seq,
            "session_id": event.session_id,
            "device": event.device,
            "input_mode": event.input_mode,
            "latency_ms": event.latency_ms,
            "hint_used": event.hint_used,
            "assisted": event.assisted,
            "difficulty_band": event.difficulty_band,
            "decision_id": event.decision_id,
            "response_digest": event.response_digest,
            "response_length": event.response_length,
            "validity_flags": list(event_flags),
        }
        for event, event_flags in zip(events, flags, strict=True)
    ]
    conn.execute(insert(LEARNING_EVENTS), rows)


def ingest(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    idempotency_key: str,
    body_hash: bytes,
    events: list[Event],
    now: datetime,
) -> StoredBatch:
    """Store one batch, or return the one already stored for this key."""
    stored = find_batch(conn, idempotency_key)
    if stored is not None:
        stored_response, batch_id = stored
        previous_hash = stored_hash(conn, idempotency_key)
        if previous_hash is not None and previous_hash != body_hash:
            raise IdempotencyConflict(idempotency_key)
        return StoredBatch(response=stored_response, batch_id=batch_id, replayed=True)

    keys = [key for key in (event.dedupe_key for event in events) if key is not None]
    already = existing_event_keys(conn, keys) if keys else set()

    fresh: list[Event] = []
    seen_keys: set[tuple[str, str, int]] = set()
    duplicates = 0
    for event in events:
        key = event.dedupe_key
        if key is None:
            fresh.append(event)
            continue
        if key in already or key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(key)
        fresh.append(event)

    period = month_start(now)
    counted = len(fresh)
    total = _bump_usage(
        conn, tenant_id=tenant_id, connection_id=connection_id, period=period, count=counted
    )
    if total is None:
        raise QuotaExhausted(
            resets_at=resets_at(now),
            quota=_quota(conn, connection_id),
            used=_used(conn, connection_id, period),
        )

    history = flag_rules.load_history(
        conn,
        sorted({event.learner_id for event in fresh}),
        sorted({event.item_id for event in fresh}),
        now=now,
    )
    event_flags = flag_rules.apply_flags(fresh, history)

    batch_id = uuid.uuid4()
    quota = _quota(conn, connection_id)
    with_decision = sum(1 for event in fresh if event.decision_id is not None)
    response: BatchResponse = {
        "batch_id": str(batch_id),
        "accepted": len(fresh),
        "duplicates": duplicates,
        "flags": _flag_counts(event_flags),
        "flagged_events": sum(len(flags) for flags in event_flags),
        "decision_attribution": {
            "events": len(fresh),
            "with_decision_id": with_decision,
            "share": round(with_decision / len(fresh), 4) if fresh else 0.0,
        },
        "usage": {
            "period": period.date().isoformat(),
            "used": total,
            "quota": quota,
            "remaining": max(quota - total, 0),
            "resets_at": resets_at(now).isoformat(),
        },
    }

    # The batch row goes in *before* its events, because the events carry a
    # foreign key to it: an orphan event would be a row nobody can replay, and
    # the FK is the cheapest way to make that impossible. The response is
    # already known at this point, so it is written once, not patched later.
    conn.execute(
        text(
            """
            INSERT INTO observe_batches
                (id, tenant_id, connection_id, idempotency_key, request_hash,
                 event_count, accepted_at, response)
            VALUES (:id, :tenant_id, :connection_id, :key, :hash, :count, :now,
                    CAST(:response AS jsonb))
            """
        ),
        {
            "id": batch_id,
            "tenant_id": tenant_id,
            "connection_id": connection_id,
            "key": idempotency_key,
            "hash": body_hash,
            "count": counted,
            "now": now,
            "response": json.dumps(response, sort_keys=True),
        },
    )
    _insert_events(
        conn,
        tenant_id=tenant_id,
        connection_id=connection_id,
        batch_id=batch_id,
        events=fresh,
        flags=event_flags,
        now=now,
    )
    return StoredBatch(response=response, batch_id=batch_id, replayed=False)


def _flag_counts(event_flags: list[tuple[str, ...]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for flags in event_flags:
        for flag in flags:
            counts[flag] = counts.get(flag, 0) + 1
    return dict(sorted(counts.items()))


def _quota(conn: Connection, connection_id: uuid.UUID) -> int:
    return int(
        conn.execute(
            text("SELECT event_quota_monthly FROM connections WHERE id = :id"),
            {"id": connection_id},
        ).scalar_one()
    )


def _used(conn: Connection, connection_id: uuid.UUID, period: datetime) -> int:
    return usage_for(conn, connection_id=connection_id, period=period)
