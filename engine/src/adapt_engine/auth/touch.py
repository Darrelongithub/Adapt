"""`last_used_at`, off the request path.

A request records a key id in a set in memory and returns. A background loop
drains the set and calls ``adapt_touch_api_key`` for each id; the debounce
(length of time since the last write) lives in that function's ``WHERE``
clause, so it holds even with several flushers or several processes.

Two things this buys:

* no second connection acquisition on the hot path — a request that
  authenticates does one lookup and nothing else;
* `last_used_at` is accurate to within the flush interval (15 s by default),
  which is more than enough for "was this key in use", and is bounded at one
  write per key per minute regardless of request rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import uuid

from sqlalchemy.engine import Engine

from adapt_engine.auth import keys as key_service

DEFAULT_FLUSH_INTERVAL_SECONDS = 15.0


class LastUsedRecorder:
    """Collects key ids used since the last flush and writes them in a batch."""

    def __init__(self, engine: Engine, *, flush_interval: float = DEFAULT_FLUSH_INTERVAL_SECONDS):
        self._engine = engine
        self._interval = flush_interval
        self._pending: set[uuid.UUID] = set()
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self.writes = 0

    def note(self, key_id: uuid.UUID) -> None:
        """Record that a key was used. No I/O: this runs on the request path."""
        with self._lock:
            self._pending.add(key_id)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def flush(self) -> int:
        """Write the debounced touch for every key noted since the last flush.

        Returns the number of rows actually written (0 when every key was
        touched inside the debounce window).
        """
        with self._lock:
            batch, self._pending = self._pending, set()
        written = 0
        for key_id in batch:
            if key_service.touch_api_key(self._engine, key_id):
                written += 1
        self.writes += written
        return written

    async def run(self) -> None:
        """Flush on an interval until cancelled."""
        while True:
            await asyncio.sleep(self._interval)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.flush)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.flush)
