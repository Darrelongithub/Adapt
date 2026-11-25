"""Create the monthly partitions `learning_events` will need, and say when the
default partition has taken a row.

``learning_events`` is range-partitioned by month. The API cannot create a
partition: the runtime role has no DDL privilege, and a handler that could
``CREATE TABLE`` is a handler an attacker can aim. So partitions are made ahead
of time by this script, which runs as the owner role.

The **default partition** is the safety net: an event whose month has no
partition of its own still inserts, so ingest never fails because a cron job
was late. That safety net is also a silent correctness hazard — rows in the
default partition are not pruned with their month, and every future
`CREATE TABLE ... PARTITION OF` scan has to include them. So this script's
other job is to report:

    python -m scripts.ensure_event_partitions --check

which exits non-zero when the default partition holds a row, or when fewer than
``--min-ahead`` months of partitions exist. That is the alert. Run it from cron
nightly, and put the ``--check`` in CI where a database is available.

Exit codes: 0 clean, 1 the check failed, 2 it could not run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

MONTHS_AHEAD = 3


def _month_start(now: datetime, offset: int = 0) -> datetime:
    year, month = now.year, now.month + offset
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return now.replace(
        year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)


def _literal(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00")


def existing_partitions(conn: Connection) -> set[str]:
    names = conn.execute(
        text(
            """
            SELECT c.relname
              FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'learning_events'
            """
        )
    ).scalars()
    return set(names)


def ensure_partitions(
    engine: Engine, *, now: datetime | None = None, months: int = MONTHS_AHEAD
) -> list[str]:
    """Create missing month partitions. Returns the names it created."""
    now = now or datetime.now(UTC)
    created: list[str] = []
    with engine.begin() as conn:
        present = existing_partitions(conn)
        for offset in range(months):
            start = _month_start(now, offset)
            following = _month_start(now, offset + 1)
            name = f"learning_events_{start.year:04d}_{start.month:02d}"
            if name in present:
                continue
            # Partition bounds are DDL, not values: PostgreSQL will not accept a
            # bind parameter there. Both literals come from `_month_start`, never
            # from input.
            conn.execute(
                text(
                    f"CREATE TABLE {name} PARTITION OF learning_events"
                    f" FOR VALUES FROM ('{_literal(start)}') TO ('{_literal(following)}')"
                )
            )
            conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
            predicate = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"
            conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {name}"
                    f" USING ({predicate}) WITH CHECK ({predicate})"
                )
            )
            created.append(name)
    return created


def default_partition_rows(engine: Engine) -> int:
    """How many events landed outside every real partition.

    Should always be zero. A non-zero answer means the window ran out, and it
    is the only warning the design gives.
    """
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM learning_events_default")).scalar_one())


def months_ahead_available(engine: Engine, *, now: datetime | None = None) -> int:
    """How many consecutive months from this one already have a partition."""
    now = now or datetime.now(UTC)
    with engine.connect() as conn:
        present = existing_partitions(conn)
    count = 0
    while True:
        start = _month_start(now, count)
        if f"learning_events_{start.year:04d}_{start.month:02d}" not in present:
            return count
        count += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Owner-role URL. Defaults to ADAPT_DATABASE_URL.",
    )
    parser.add_argument("--months", type=int, default=MONTHS_AHEAD)
    parser.add_argument(
        "--min-ahead",
        type=int,
        default=2,
        help="Fail --check when fewer than this many months are provisioned.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report instead of creating: exit 1 when the net has been used.",
    )
    args = parser.parse_args(argv)

    import os

    url = args.database_url or os.environ.get("ADAPT_DATABASE_URL")
    if not url:
        print("ensure_event_partitions: no database URL (--database-url or ADAPT_DATABASE_URL)")
        return 2

    engine = create_engine(url, future=True)
    try:
        if args.check:
            rows = default_partition_rows(engine)
            ahead = months_ahead_available(engine)
            print(f"default partition rows: {rows}")
            print(f"months provisioned ahead: {ahead}")
            if rows:
                print(
                    "FAIL: events are landing in the default partition;"
                    " run without --check to extend the window",
                    file=sys.stderr,
                )
                return 1
            if ahead < args.min_ahead:
                print(
                    f"FAIL: only {ahead} month(s) provisioned, want {args.min_ahead}",
                    file=sys.stderr,
                )
                return 1
            print("ok")
            return 0

        created = ensure_partitions(engine, months=args.months)
        print(f"created {len(created)} partition(s): {', '.join(created) or 'none needed'}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
