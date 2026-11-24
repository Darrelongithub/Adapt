#!/usr/bin/env python3
"""Owner-only, read-only queries for a security review.

A failed-authentication row has no tenant, so it is visible to ``adapt_owner``
and to nobody else (migration 0004, DECISIONS §7). Before this script the only
way to look at one was ``psql`` — which means, in practice, that nobody looked.

Three reports:

    python scripts/audit_query.py --auth-failures [--minutes 60]
    python scripts/audit_query.py --key-events [--tenant <uuid>]
    python scripts/audit_query.py --system-count

It connects with ``ADAPT_MIGRATION_DATABASE_URL`` (the owner role), the same
credential migrations use, and it only ever issues SELECTs. Every statement
runs inside a ``READ ONLY`` transaction, so a bug in this file cannot write.

What it prints is already non-sensitive: an ``ip_hash``, key ids, counts and
timestamps. No key, no prefix, no address.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


def _engine():  # type: ignore[no-untyped-def]
    url = os.environ.get("ADAPT_MIGRATION_DATABASE_URL") or os.environ.get("ADAPT_DATABASE_URL")
    if not url:
        print(
            "error: ADAPT_MIGRATION_DATABASE_URL is not set (owner role required)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return create_engine(url, future=True)


def _read_only(conn: Connection) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))


def auth_failures(conn: Connection, *, minutes: int, limit: int = 50) -> list[Any]:
    """Recent failures by source hash, newest first, with the aggregate flag."""
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    return conn.execute(
        text(
            """
            SELECT occurred_at,
                   metadata->>'ip_hash'  AS ip_hash,
                   (metadata->>'count')::int AS count_in_row,
                   (metadata->>'aggregated')::boolean AS aggregated,
                   metadata->>'window_start' AS window_start
              FROM audit_log
             WHERE tenant_id IS NULL
               AND action = 'auth_failed'
               AND occurred_at >= :since
             ORDER BY occurred_at DESC
             LIMIT :limit
            """
        ),
        {"since": since, "limit": limit},
    ).all()


def key_events(conn: Connection, *, tenant_id: str | None, limit: int = 50) -> list[Any]:
    """Key issuance, revocation and expiry changes, newest first."""
    query = """
        SELECT occurred_at, tenant_id, action, actor, target
          FROM audit_log
         WHERE action IN ('api_key_issued', 'api_key_revoked', 'api_key_expiry_set')
    """
    params: dict[str, object] = {"limit": limit}
    if tenant_id is not None:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id
    query += " ORDER BY occurred_at DESC LIMIT :limit"
    return conn.execute(text(query), params).all()


def system_count(conn: Connection) -> int:
    """How many tenant-less audit rows exist, by action."""
    return int(
        conn.execute(
            text("SELECT count(*) FROM audit_log WHERE tenant_id IS NULL")
        ).scalar_one()
    )


def _print_rows(rows: list[Any]) -> None:
    if not rows:
        print("(no rows)")
        return
    print(" | ".join(rows[0]._fields))  # type: ignore[attr-defined]
    print("-" * 72)
    for row in rows:
        print(" | ".join(str(value) for value in row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-only, read-only audit queries")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--auth-failures", action="store_true", help="recent failed authentications")
    group.add_argument("--key-events", action="store_true", help="key issue/revoke/expiry rows")
    group.add_argument("--system-count", action="store_true", help="tenant-less audit row count")
    parser.add_argument("--minutes", type=int, default=60, help="window for --auth-failures")
    parser.add_argument("--tenant", default=None, help="tenant uuid for --key-events")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    if args.minutes < 1:
        print("error: --minutes must be positive", file=sys.stderr)
        return 2

    engine = _engine()
    try:
        with engine.connect() as conn:
            _read_only(conn)
            if args.auth_failures:
                _print_rows(auth_failures(conn, minutes=args.minutes, limit=args.limit))
            elif args.key_events:
                _print_rows(key_events(conn, tenant_id=args.tenant, limit=args.limit))
            else:
                print(f"tenant-less audit rows: {system_count(conn)}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
