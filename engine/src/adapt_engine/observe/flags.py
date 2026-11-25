"""Validity flags, evaluated at ingest and stored on the event.

MEASUREMENT §4 lists six flags, and two rules govern all of them: **never
imputed, never dropped**. A flagged event is still real data — it happened —
and the honest move is to record that we do not trust it for a particular
purpose, not to delete it or to invent a "clean" version.

| Flag | Rule | Reachable at M1? |
|---|---|---|
| `rapid_guess` | `latency_ms < 300` | yes |
| `straight_line` | 8+ identical outcomes in a row on one skill | yes |
| `device_change` | device differs from that learner's previous event | yes |
| `assisted` | the host says help was present | yes, as a passthrough |
| `repeated_item` | the same learner has seen this item already | yes |
| `tab_hidden` | document visibility was lost mid-trial | **no** — no field for it |

`tab_hidden` is the honest gap: the flag exists in the vocabulary and in the
column's CHECK, but no host input can set it, because the event allowlist has
no visibility field. Adding one is a contract change, so it is reported rather
than invented (KNOWN_GAPS §2b).

The lookback is bounded on purpose: the last 16 stored events per learner, not
their whole history. A run of eight identical outcomes is a local pattern, and
scanning a learner's lifetime at ingest would make ingest cost grow with tenure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from adapt_engine.observe.schema import Event

#: Below this a response cannot be a genuine attempt on a non-trivial item.
RAPID_GUESS_MS = 300

#: Eight identical outcomes in a row is a pattern, not a coincidence.
STRAIGHT_LINE_RUN = 8

#: How much history the flag rules may read, per learner.
LOOKBACK_EVENTS = 16

#: How far back `repeated_item` looks. A month is long enough to catch a
#: repeated item inside a course unit and short enough to stay cheap.
REPEAT_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class History:
    """What the flags need to know about one learner's recent past."""

    last_correct: bool | None = None
    last_skill: str | None = None
    last_device: str | None = None
    #: Trailing run of identical outcomes, most recent first (1 = last event).
    run_length: int = 0
    run_value: bool | None = None
    seen_items: frozenset[str] = frozenset()


def load_history(
    conn: Connection,
    learners: Sequence[str],
    items: Sequence[str],
    *,
    now: datetime,
) -> dict[str, History]:
    """The last events and recent items for the learners and items in a batch.

    Two queries, not two per event: a bounded tail per learner, and one item
    lookup restricted to the items this batch mentions. A batch of 500 events
    costs the same as a batch of 1, and neither query's cost grows with the
    learner's history — both are index probes.
    """
    if not learners:
        return {}

    # LATERAL, not a window function over the whole history: `row_number()`
    # partitioned by learner still *scans* every event that learner has, which
    # at 400k events per connection turned a 500-event batch into a full table
    # sort (the load test found it: 752 µs/event). A lateral `ORDER BY ts DESC
    # LIMIT 16` is an index scan of sixteen rows per learner, which is the cost
    # the design always claimed.
    tail = conn.execute(
        text(
            """
            SELECT e.learner_id, e.skill, e.correct, e.device, e.ts, e.id
              FROM unnest(CAST(:learners AS text[])) AS l(learner_id)
              CROSS JOIN LATERAL (
                    SELECT learner_id, skill, correct, device, ts, id
                      FROM learning_events
                     WHERE learner_id = l.learner_id
                     ORDER BY ts DESC, id DESC
                     LIMIT :limit
              ) e
             ORDER BY e.learner_id, e.ts, e.id
            """
        ),
        {"learners": list(learners), "limit": LOOKBACK_EVENTS},
    ).all()

    per_learner: dict[str, list[Any]] = {}
    for row in tail:
        per_learner.setdefault(row.learner_id, []).append(row)

    history: dict[str, History] = {}
    for learner_id, rows in per_learner.items():
        last = rows[-1]
        run_value = last.correct
        run_length = 0
        for row in reversed(rows):
            if row.correct != run_value or row.skill != last.skill:
                break
            run_length += 1
        history[learner_id] = History(
            last_correct=last.correct,
            last_skill=last.skill,
            last_device=last.device,
            run_length=run_length,
            run_value=run_value,
        )

    wanted = sorted(set(items))
    seen_rows = conn.execute(
        text(
            """
            SELECT DISTINCT learner_id, item_id
              FROM learning_events
             WHERE learner_id = ANY(:learners)
               AND item_id = ANY(:items)
               AND ts >= :since
            """
        ),
        {"learners": list(learners), "items": wanted, "since": now - REPEAT_WINDOW},
    ).all()
    seen: dict[str, set[str]] = {}
    for row in seen_rows:
        seen.setdefault(row.learner_id, set()).add(row.item_id)
    for learner_id, found in seen.items():
        base = history.get(learner_id, History())
        history[learner_id] = History(
            last_correct=base.last_correct,
            last_skill=base.last_skill,
            last_device=base.last_device,
            run_length=base.run_length,
            run_value=base.run_value,
            seen_items=frozenset(found),
        )
    return history


def apply_flags(events: Sequence[Event], history: dict[str, History]) -> list[tuple[str, ...]]:
    """One flag tuple per event, in batch order.

    Within a batch the running state advances, so eight identical outcomes
    split across two batches are still caught by the second one — the batch is
    a transport unit, not a unit of meaning.
    """
    state: dict[str, History] = dict(history)
    seen_in_batch: dict[str, set[str]] = {}
    out: list[tuple[str, ...]] = []

    for event in events:
        previous = state.get(event.learner_id, History())
        flags: list[str] = []

        if event.latency_ms is not None and event.latency_ms < RAPID_GUESS_MS:
            flags.append("rapid_guess")

        if event.assisted:
            flags.append("assisted")

        if (
            previous.last_device is not None
            and event.device is not None
            and event.device != previous.last_device
        ):
            flags.append("device_change")

        seen = seen_in_batch.setdefault(event.learner_id, set())
        if event.item_id in previous.seen_items or event.item_id in seen:
            flags.append("repeated_item")
        seen.add(event.item_id)

        run_length = 1
        if (
            previous.run_value is not None
            and previous.last_skill == event.skill
            and previous.run_value == event.correct
        ):
            run_length = previous.run_length + 1
        if run_length >= STRAIGHT_LINE_RUN:
            flags.append("straight_line")

        out.append(tuple(flags))
        state[event.learner_id] = History(
            last_correct=event.correct,
            last_skill=event.skill,
            last_device=event.device if event.device is not None else previous.last_device,
            run_length=run_length,
            run_value=event.correct,
            seen_items=previous.seen_items | frozenset({event.item_id}),
        )
    return out
