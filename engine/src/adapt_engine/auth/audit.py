"""Auditing failed authentication without letting an attacker choose the write rate.

Every failure is a row, up to ten per (ip_hash, minute). The eleventh failure in
that minute writes **one** aggregate row carrying the count, and nothing more is
written for that (ip_hash, minute) until the minute changes.

So the bound is: 11 rows per source per minute, whatever the request rate. An
unbounded audit trail on the unauthenticated path is a write-amplification
vector — the audit becomes the denial of service.

The source is a keyed hash of the client address, never the address itself:
`audit_log.meta` carries no PII, and an IPv4 address is personal data.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from adapt_engine.auth import keys as key_service
from adapt_engine.clock import Clock

#: Individual rows allowed per source per minute before aggregation starts.
PER_MINUTE_ROWS = 10


def ip_hash(pepper: str, client: str) -> str:
    """A stable, non-reversible identifier for a client address."""
    digest = hmac.new(pepper.encode("utf-8"), client.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:32]


def _minute_of(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


class AuthFailureAuditor:
    """Writes at most ``PER_MINUTE_ROWS + 1`` rows per source per minute."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._counts: dict[tuple[str, datetime], int] = {}
        self._lock = threading.Lock()
        self.rows_written = 0

    def record(self, source_hash: str) -> int:
        """Write the audit row for one failure; return the count for the minute."""
        minute = _minute_of(self._clock.now())
        key = (source_hash, minute)
        with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            self._prune(minute)

        if count <= PER_MINUTE_ROWS:
            self._write(source_hash, minute, count=1, aggregated=False)
        elif count == PER_MINUTE_ROWS + 1:
            # One row for everything past the tenth, then silence this minute.
            self._write(source_hash, minute, count=count, aggregated=True)
        return count

    def _write(self, source_hash: str, minute: datetime, *, count: int, aggregated: bool) -> None:
        try:
            key_service.note_auth_failure(
                self._engine,
                ip_hash=source_hash,
                window_start=minute,
                count=count,
                aggregated=aggregated,
            )
            self.rows_written += 1
        except Exception:
            # The audit must never turn a 401 into a 500.
            pass

    def _prune(self, current_minute: datetime) -> None:
        cutoff = current_minute - timedelta(minutes=5)
        for key in [k for k in self._counts if k[1] < cutoff]:
            del self._counts[key]
