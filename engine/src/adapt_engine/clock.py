"""Injectable clock.

Business code never reads the wall clock directly; it asks the injected
Clock. Tests use FixedClock so durations and timestamps are deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware datetime."""
        ...


class SystemClock:
    """Production clock reading the system time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock returning a fixed instant."""

    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed
