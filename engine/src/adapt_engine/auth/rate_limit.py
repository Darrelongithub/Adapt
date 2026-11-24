"""Failed-auth rate limiting, per client IP.

In-memory and per-process on purpose for M0: it is a brake on credential
guessing, not a shared quota, and a shared limiter belongs with the credit
ledger in a later milestone. What matters here is that a failure is counted
the same way whichever way it failed — malformed header, unknown prefix, bad
hash, revoked key and wrong environment all cost one token.
"""

from __future__ import annotations

import threading
from collections import deque

from adapt_engine.clock import Clock

#: Failures allowed per IP inside the window before 429s start.
DEFAULT_MAX_FAILURES = 20
DEFAULT_WINDOW_SECONDS = 60.0


class FailureLimiter:
    """Sliding window of failure timestamps per key (the client IP)."""

    def __init__(
        self,
        *,
        clock: Clock,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        self._clock = clock
        self._max_failures = max_failures
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return self._clock.now().timestamp()

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.setdefault(key, deque())
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            bucket = self._prune(key, self._now())
            return len(bucket) >= self._max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._now()
            self._prune(key, now).append(now)

    def retry_after_seconds(self, key: str) -> int:
        """Seconds until the oldest failure leaves the window."""
        with self._lock:
            now = self._now()
            bucket = self._prune(key, now)
            if not bucket:
                return 0
            return max(1, int(self._window - (now - bucket[0])) + 1)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)
