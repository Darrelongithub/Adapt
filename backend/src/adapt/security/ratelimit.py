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
from collections import deque
from dataclasses import dataclass, field
from typing import Any


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


class InFlight:
    """How many requests one key is *currently* waiting on, plus a burst detector.

    A token bucket answers "how often"; this answers "how many at once", and the two are not
    the same failure. A page that fires twenty parallel requests at 3 req/s each passes every
    bucket in this file and still lands twenty concurrent model calls on the free tier. The
    bucket is about fairness over a minute; this is about a runaway loop in the next second.

    Two refusals, both cheap to explain to a client:

    * **too many in flight** -- the key already has ``max_inflight`` requests being served.
      The right client behaviour is to wait for the ones it already sent, which is what the
      ``Retry-After`` header says;
    * **a burst** -- more than ``burst_max`` requests started inside ``window_ms``, which is
      not a person clicking. It is a bug, a loop, or a load generator, and all three are
      better off being slowed down than absorbed.

    Its state is a counter and a short deque per key, pruned on every call, bounded by
    ``max_keys``: a guard against a flood must not itself be the thing that grows without
    limit.
    """

    def __init__(
        self,
        *,
        max_inflight: int = 4,
        window_ms: int = 1000,
        burst_max: int = 20,
        max_keys: int = 4096,
    ) -> None:
        self.max_inflight = max(1, int(max_inflight))
        self.window_s = max(0.05, int(window_ms) / 1000.0)
        self.burst_max = max(1, int(burst_max))
        self._max_keys = max(16, int(max_keys))
        self._inflight: dict[str, int] = {}
        self._recent: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self.refused_inflight = 0
        self.refused_burst = 0
        self.peak_inflight = 0

    def begin(self, key: str) -> tuple[bool, str, float]:
        """Register a request. Returns ``(allowed, reason, retry_after_s)``.

        The caller *must* call :meth:`end` in a ``finally`` when this returns ``True``: a
        leak here would permanently reduce the key's allowed concurrency.
        """
        k = key or "-"
        now = time.monotonic()
        with self._lock:
            recent = self._recent.setdefault(k, deque())
            while recent and now - recent[0] > self.window_s:
                recent.popleft()
            if len(recent) >= self.burst_max:
                self.refused_burst += 1
                wait = max(0.2, self.window_s - (now - recent[0]))
                return False, "burst", wait
            busy = self._inflight.get(k, 0)
            if busy >= self.max_inflight:
                self.refused_inflight += 1
                return False, "in_flight", 0.5
            recent.append(now)
            self._inflight[k] = busy + 1
            self.peak_inflight = max(self.peak_inflight, busy + 1)
            self._prune_locked(now)
            return True, "", 0.0

    def end(self, key: str) -> None:
        k = key or "-"
        with self._lock:
            busy = self._inflight.get(k, 0)
            if busy <= 1:
                self._inflight.pop(k, None)
            else:
                self._inflight[k] = busy - 1

    def busy(self, key: str) -> int:
        with self._lock:
            return self._inflight.get(key or "-", 0)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "max_in_flight": self.max_inflight,
                "window_ms": int(self.window_s * 1000),
                "burst_max": self.burst_max,
                "active_keys": len(self._inflight),
                "peak_in_flight": self.peak_inflight,
                "refused_in_flight": self.refused_inflight,
                "refused_burst": self.refused_burst,
            }

    def _prune_locked(self, now: float) -> None:
        """Keep the key tables bounded: an idle key costs nothing to forget."""
        if len(self._recent) <= self._max_keys and len(self._inflight) <= self._max_keys:
            return
        for k in list(self._recent):
            if len(self._recent) <= self._max_keys:
                break
            if not self._recent[k] or now - self._recent[k][-1] > self.window_s * 4:
                self._recent.pop(k, None)
                if self._inflight.get(k, 0) == 0:
                    self._inflight.pop(k, None)


# (rate per minute, burst capacity). Provider numbers are the documented free-tier
# ceilings divided by ~2 for headroom; see docs/EFFICIENCY.md for the measured
# cost/latency figures and the guard tables these numbers bound.
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
    # -- interactive engine -----------------------------------------------------------
    # Position writes are the chatty ones (a seek, a pause), so the bucket is generous but not
    # open: 60/min is more than a person can click, and it is what stops a bug in the player
    # from appending telemetry forever.
    "lesson_play": (60.0, 120.0),
    "lesson_predict": (40.0, 80.0),
    # each of these can reach the model once per (scene, frame, question); the cache in front of
    # it means a replay is free, so the bucket is about first passes, not about steady state
    "lesson_explain": (12.0, 20.0),
    "lesson_author": (4.0, 6.0),
    # -- the voice socket -------------------------------------------------------------
    # Control frames were the one unbounded input on this server: only binary microphone
    # frames were metered, so a page that sent a text frame per keystroke could start a model
    # call per keystroke. ``ws_control`` covers every control frame, and ``ws_turn`` is the
    # narrower bucket on the frames that actually cost a turn (a typed message), because a
    # config change is free and a turn is not.
    "ws_control": (60.0, 12.0),
    "ws_turn": (20.0, 6.0),
}


class DuplicateWindow:
    """Identical messages inside one window are one message.

    A double-click, a key repeat, a client that retries on a flaky connection, or a
    reconnect that replays the last frame -- all produce the *same* bytes twice in under a
    second, and all of them would otherwise be a second model call. Deduplicating at the
    edge is the cheap place to do it: no prompt is built, no quota is spent, and the client
    gets told what happened rather than being answered twice.

    Deliberately per key and deliberately tiny: it remembers one message per key, not a
    history, because the case it exists for is *the same frame arriving twice*, not "this
    learner likes to repeat themselves" (that is legitimate, and a person who asks the same
    question twice deserves two answers).
    """

    def __init__(self, window_ms: int = 900, *, max_keys: int = 4096) -> None:
        self.window_s = max(0.0, int(window_ms) / 1000.0)
        self._last: dict[str, tuple[str, float]] = {}
        self._max_keys = max(16, int(max_keys))
        self._lock = threading.Lock()
        self.suppressed = 0

    def seen(self, key: str, payload: str) -> bool:
        """Record ``payload`` for ``key``; True when it is a repeat of the last one."""
        now = time.monotonic()
        k = key or "-"
        with self._lock:
            last = self._last.get(k)
            if last is not None and last[0] == payload and (now - last[1]) <= self.window_s:
                self.suppressed += 1
                self._last[k] = (payload, now)
                return True
            self._last[k] = (payload, now)
            if len(self._last) > self._max_keys:
                cutoff = now - max(self.window_s * 4, 1.0)
                for stale in [key_ for key_, (_, at) in self._last.items() if at < cutoff]:
                    self._last.pop(stale, None)
            return False

    def forget(self, key: str) -> None:
        with self._lock:
            self._last.pop(key or "-", None)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"window_ms": int(self.window_s * 1000), "keys": len(self._last), "suppressed": self.suppressed}


def build_realtime_guard(settings: Any = None) -> InFlight:
    """The in-flight/burst guard, sized from settings so a load test can raise it.

    A test harness driving twenty parallel requests is *supposed* to look like a flood; the
    point of ``ADAPT_REALTIME_MAX_INFLIGHT`` is that an operator knows which of the two they
    are running, rather than having to rewrite a table to find out.

    ``ADAPT_RATE_LIMIT_MULTIPLIER`` scales this guard exactly as it scales every bucket in
    :data:`DEFAULT_LIMITS`. That is not a coincidence to be tidied away later: a deployment
    that raised the ceilings for a shared NAT or a load test would otherwise find that the
    *one* limit the multiplier did not touch is the one refusing it, and the operator would
    have two stories to read instead of one.
    """
    if settings is None:
        return InFlight()
    factor = max(1.0, float(getattr(settings, "rate_limit_multiplier", 1.0) or 1.0))
    return InFlight(
        max_inflight=max(1, int(round(int(getattr(settings, "realtime_max_inflight", 4) or 4) * factor))),
        window_ms=int(getattr(settings, "realtime_burst_window_ms", 1000) or 1000),
        burst_max=max(1, int(round(int(getattr(settings, "realtime_burst_max", 20) or 20) * factor))),
    )


_GUARD: InFlight | None = None
_GUARD_LOCK = threading.Lock()


def realtime_guard(settings: Any = None) -> InFlight:
    """The process-wide in-flight guard: one place that knows how many are being served.

    Process-wide rather than per-request, because "how many at once" is only answerable if
    every request counts into the same counter. Built on first use from the settings, and
    replaceable through :func:`reset_realtime_guard` -- a test that wants to *see* a refusal
    needs a guard it can tighten, and production never wants a fresh counter.
    """
    global _GUARD
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    with _GUARD_LOCK:
        if _GUARD is None:
            _GUARD = build_realtime_guard(settings)
        return _GUARD


def reset_realtime_guard() -> None:
    """Forget the process-wide guard (tests, or a deployment that changed its settings)."""
    global _GUARD
    with _GUARD_LOCK:
        _GUARD = None


def realtime_snapshot(settings: Any = None) -> dict[str, object]:
    """The guard's counters, for ``/api/v1/quotas`` and ``adapt doctor``."""
    return realtime_guard(settings).snapshot()


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
