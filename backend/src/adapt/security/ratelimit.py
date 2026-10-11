"""In-process token buckets: no Redis, no paid service, still not trivially spammable.

Two knobs matter for the demo: a per-IP ceiling that protects the app, and a
per-provider ceiling that protects the *free tiers* (Gemini, OpenAlex, arXiv,
Open Library, YouTube). Hitting a provider limit is the difference between a
degraded answer and a locked-out API key for the rest of the judging window, so
the provider buckets are deliberately conservative and the callers must ask
permission rather than fire-and-forget.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    rate_per_min: float
    capacity: float
    tokens: float = field(init=False)
    updated: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def take(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * (self.rate_per_min / 60.0))
        self.updated = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def retry_after_s(self) -> float:
        if self.tokens >= 1:
            return 0.0
        return max(0.05, (1.0 - self.tokens) * 60.0 / max(0.001, self.rate_per_min))


class Limiter:
    """Fixed window of buckets keyed by ``(scope, key)``. Thread-safe, LRU-capped."""

    def __init__(self, limits: dict[str, tuple[float, float]], max_keys: int = 4096) -> None:
        self._limits = limits
        self._buckets: dict[tuple[str, str], Bucket] = {}
        self._order: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def allow(self, scope: str, key: str, *, n: float = 1.0) -> tuple[bool, float]:
        spec = self._limits.get(scope)
        if spec is None:
            return True, 0.0
        bucket_key = (scope, key or "-")
        with self._lock:
            b = self._buckets.get(bucket_key)
            if b is None:
                b = Bucket(rate_per_min=spec[0], capacity=spec[1])
                self._buckets[bucket_key] = b
                self._order.append(bucket_key)
                if len(self._order) > self._max_keys:
                    self._buckets.pop(self._order.pop(0), None)
            ok = b.take(n)
            return ok, (0.0 if ok else b.retry_after_s())

    def blocked(self, scope: str, key: str) -> tuple[bool, float]:
        """Is this key already out of tokens? Refills the bucket, spends nothing.

        This exists so a *failure* budget can act as a lockout. Checking with
        :meth:`allow` would spend a token on every attempt, and then a learner signing
        in a few times from a shared connection would lock themselves out; checking
        here costs nothing, and the caller spends only when something failed.
        """
        spec = self._limits.get(scope)
        if spec is None:
            return False, 0.0
        bucket_key = (scope, key or "-")
        with self._lock:
            b = self._buckets.get(bucket_key)
            if b is None:
                return False, 0.0  # do not create buckets for keys that never failed
            b.take(0.0)  # refill, without spending
            if b.tokens >= 1.0:
                return False, 0.0
            return True, b.retry_after_s()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "scopes": {
                    scope: {"rate_per_min": spec[0], "burst": spec[1]} for scope, spec in self._limits.items()
                },
                "active_buckets": len(self._buckets),
            }


# (rate per minute, burst capacity). Provider numbers are the documented free-tier
# ceilings divided by ~2 for headroom; see docs/API-QUOTAS.md for the verification.
DEFAULT_LIMITS: dict[str, tuple[float, float]] = {
    "ip": (120.0, 240.0),
    "auth_fail": (5.0, 5.0),
    "guest_create": (6.0, 6.0),
    "mutation": (90.0, 180.0),
    "llm": (8.0, 12.0),
    "gemini": (4.0, 8.0),
    "openalex": (50.0, 100.0),  # polite pool: 10/s documented, we stay far below
    "arxiv": (20.0, 2.0),  # 1 request / 3 s is the documented courtesy limit -> 20/min
    "openlibrary": (8.0, 20.0),
    "youtube": (6.0, 10.0),  # 10k units/day quota; search costs 100 units
    "voice_frame": (6000.0, 12000.0),  # WS audio frames: sanity ceiling, not UX limit
    "numbas_export": (6.0, 10.0),
}


def build_limiter(limits: dict[str, tuple[float, float]] | None = None, *, multiplier: float = 1.0) -> Limiter:
    """Scale every bucket by ``ADAPT_RATE_LIMIT_MULTIPLIER``.

    The multiplier exists because the right ceiling depends on who is holding the
    machine: a tunnel open to the internet needs the defaults, while a load test or a
    demo rehearsed on a shared NAT (where ten teammates share one IP) trips buckets
    that are not protecting anyone. Above 1 it is a *documented* escape hatch, not a
    silent edit to the table; ``/api/v1/quotas`` reports the effective numbers.
    """
    src = dict(limits or DEFAULT_LIMITS)
    factor = max(0.05, float(multiplier or 1.0))
    if factor != 1.0:
        src = {scope: (rate * factor, burst * factor) for scope, (rate, burst) in src.items()}
    return Limiter(src)


def effective_limits(multiplier: float = 1.0) -> dict[str, tuple[float, float]]:
    factor = max(0.05, float(multiplier or 1.0))
    return {scope: (rate * factor, burst * factor) for scope, (rate, burst) in DEFAULT_LIMITS.items()}
