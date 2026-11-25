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

#: Above this, an item is expected to be answered right most of the time, so a
#: fast answer is fluency rather than a guess (MEASUREMENT §4: "non-trivial item").
TRIVIAL_BAND_HIGH = 0.85

#: Eight identical *responses* in a row is a pattern, not a coincidence.
#:
#: `response` — the option the learner chose — not `correct`. A run of identical
#: correctness is what a learner on a roll produces, and flagging it made the
#: honesty machinery abstain on the learners who were progressing fastest
#: (MEASUREMENT §4, METRICS §1.3). Correctness is never a flag.
STRAIGHT_LINE_RUN = 8

#: How severe each flag is, published so a host can see what it is being asked to
#: fix (METRICS §1.3). Only `uninformative` flags gate serving: a `device_change`
#: is a context note, `assisted` is recorded as data (ADR-001), and
#: `undeterminable` is an admission, not an accusation.
SEVERITY: dict[str, str] = {
    "rapid_guess": "uninformative",
    "straight_line": "uninformative",
    # MEASUREMENT §4: "session marked reduced-validity" — a trial the learner
    # may not have been looking at is uninformative, like a rapid guess. Nothing
    # emits it today (KNOWN_GAPS §2b), so this classification changes no decision;
    # it is here so that the day a visibility channel lands, the gate is already
    # on the right side of the table.
    "tab_hidden": "uninformative",
    "device_change": "context",
    "undeterminable": "context",
    "assisted": "scoring",
    "repeated_item": "scoring",
}

#: The flags that mean "this answer may not be the learner's".
UNINFORMATIVE_FLAGS: tuple[str, ...] = tuple(
    name for name, severity in SEVERITY.items() if severity == "uninformative"
)

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
    #: Trailing run of identical *response digests*, most recent last.
    run_length: int = 0
    run_digest: bytes | None = None
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
    #
    # The tie-break is total: `ts` alone ties whenever a client sends a batch
    # with one timestamp (our own fixtures do), and `id` is a random uuid, so
    # ordering by it alone made the tail — and therefore the run length —
    # different from tenant to tenant for identical input. `created_at` (server
    # ingest time) then `seq` (client order within a batch) then `id` resolves
    # ties reproducibly; the sort is still bounded by one learner's rows because
    # the lateral has already filtered to that learner.
    tail = conn.execute(
        text(
            """
            SELECT e.learner_id, e.skill, e.response_digest, e.device, e.ts, e.id
              FROM unnest(CAST(:learners AS text[])) AS l(learner_id)
              CROSS JOIN LATERAL (
                    SELECT learner_id, skill, response_digest, device, ts, id,
                           created_at, seq
                      FROM learning_events
                     WHERE learner_id = l.learner_id
                     ORDER BY ts DESC, created_at DESC, seq DESC NULLS LAST, id DESC
                     LIMIT :limit
              ) e
             ORDER BY e.learner_id, e.ts, e.created_at, e.seq, e.id
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
        run_digest = last.response_digest
        run_length = 0
        for row in reversed(rows):
            # A missing response breaks the run: "no answer recorded" is not the
            # same value as another missing answer, and treating it as one would
            # flag every host that does not send responses.
            if run_digest is None or row.response_digest != run_digest:
                break
            if row.skill != last.skill:
                break
            run_length += 1
        history[learner_id] = History(
            last_skill=last.skill,
            last_device=last.device,
            run_length=run_length,
            run_digest=run_digest,
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
            last_skill=base.last_skill,
            last_device=base.last_device,
            run_length=base.run_length,
            run_digest=base.run_digest,
            seen_items=frozenset(found),
        )
    return history


def _is_trivial(difficulty_band: str | None) -> bool:
    """ "0.55-0.75" style band: true when even its low end is nearly certain."""
    if difficulty_band is None:
        return False
    high = difficulty_band.split("-")[-1]
    try:
        return float(high) >= TRIVIAL_BAND_HIGH
    except ValueError:
        return False


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

        # A fast *correct* answer is fluency, and a fast answer on an item
        # everyone gets right is the item's fault. Correctness is used here only
        # to exonerate — never to accuse — so a strong, quick learner is not
        # turned into a data-quality abstention.
        if (
            event.latency_ms is not None
            and event.latency_ms < RAPID_GUESS_MS
            and not event.correct
            and not _is_trivial(event.difficulty_band)
        ):
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

        # Straight-lining is a property of the *response*, and when there is no
        # response it is undeterminable — never inferred from correctness.
        digest = event.response_digest
        run_length = 0
        if digest is None:
            flags.append("undeterminable")
        else:
            run_length = 1
            if (
                previous.run_digest is not None
                and previous.last_skill == event.skill
                and previous.run_digest == digest
            ):
                run_length = previous.run_length + 1
            if run_length >= STRAIGHT_LINE_RUN:
                flags.append("straight_line")

        out.append(tuple(flags))
        state[event.learner_id] = History(
            last_skill=event.skill,
            last_device=event.device if event.device is not None else previous.last_device,
            run_length=run_length,
            run_digest=digest,
            seen_items=previous.seen_items | frozenset({event.item_id}),
        )
    return out
