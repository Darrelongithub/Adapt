"""Per-connection ingest throughput limit.

METERING §6 gives the number: **50 requests per second per key, burst 100**.
Authentication failures were already limited; event volume was not, which meant
one tenant's runaway client could saturate the database and degrade every other
tenant's requests — the failure mode is not "this tenant is slow", it is "the
fleet is slow and nobody knows why".

Three properties, and each was a decision:

* **Per connection, not per tenant or globally.** A tenant with several hosts is
  several clients, and a shared bucket would let one host starve its siblings.
* **A token bucket, not a window.** A fixed window lets a client send the burst
  twice at the boundary; a bucket smooths it, and the refill is the documented
  rate.
* **In-process.** Like the auth limiter, this is a brake, not a quota: with N
  replicas a client gets N times the rate. That is in KNOWN_GAPS, not implied.

The limiter is free and counted: a refused request spends no quota, writes
nothing, and is logged with `observe_rate_limited` so a spike is visible.
"""

from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass

from adapt_engine.clock import Clock

#: METERING §6: "Rate per key | 50 rps, burst 100".
DEFAULT_RATE_PER_SECOND = 50.0
DEFAULT_BURST = 100.0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class IngestLimiter:
    """Token buckets keyed by connection id, refilled from a monotonic clock."""

    def __init__(
        self,
        *,
        clock: Clock,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        burst: float = DEFAULT_BURST,
        max_connections: int = 10_000,
    ) -> None:
        if rate_per_second <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._clock = clock
        self._rate = rate_per_second
        self._burst = burst
        self._max = max_connections
        self._buckets: dict[uuid.UUID, _Bucket] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return self._clock.now().timestamp()

    def _prune(self, now: float) -> None:
        """Forget buckets that have been full for long enough to be uninteresting.

        Without this, one bucket per connection id ever seen is a slow leak. A
        full bucket and a missing bucket behave identically, so dropping one is
        free.
        """
        if len(self._buckets) < self._max:
            return
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at > self._burst / self._rate * 4
        ]
        for key in stale:
            del self._buckets[key]

    def take(self, connection_id: uuid.UUID, *, cost: float = 1.0) -> float | None:
        """Spend one request's tokens.

        Returns ``None`` when the request is allowed, or the number of seconds
        to wait before it would be — a whole number of seconds, never zero, so
        ``Retry-After`` is always actionable.
        """
        now = self._now()
        with self._lock:
            self._prune(now)
            bucket = self._buckets.get(connection_id)
            if bucket is None:
                bucket = _Bucket(tokens=self._burst, updated_at=now)
                self._buckets[connection_id] = bucket
            elapsed = max(now - bucket.updated_at, 0.0)
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.updated_at = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return None
            shortfall = cost - bucket.tokens
            # Ceiling, not floor+1: one token short at one token per second is
            # a one-second wait, and "retry after 2" would be a lie the client
            # pays for.
            return max(math.ceil(shortfall / self._rate), 1)

    def reset(self) -> None:
        """Forget every bucket. Tests only."""
        with self._lock:
            self._buckets.clear()
