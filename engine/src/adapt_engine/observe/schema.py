"""Event validation: an allowlist, not a filter.

AGENT_M1 §2 says "any key outside the allowlist is rejected, not ignored, and
the error names the key". That is a stronger rule than it looks: silently
dropping an unknown key is how a host ships `learner_email` for six months
before anyone notices it was never stored, and it is also how a typo in
`latency_ms` turns into a validity flag that never fires.

So this module is deliberately paranoid and deliberately small:

* the field names are a closed set, and the rejection names what it did not
  recognise (up to a few, never the value);
* every value is checked for *shape*, not just type — a `learner_id` that looks
  like an email is refused by the same rule the learner route uses;
* nothing here touches the database. It is pure, so the same checks can run in
  a test, in the route, and later in an SDK's local validation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from adapt_engine.db.schema import LearnerIdInvalid, validate_learner_id

#: Required on every event (AGENT_M1 §2).
REQUIRED_FIELDS: frozenset[str] = frozenset({"learner_id", "skill", "item_id", "correct", "ts"})

#: Optional on every event. `decision_id` is the one that matters most: without
#: it the engine can never score its own recommendations.
OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        "response",
        "latency_ms",
        "session_id",
        "device",
        "input_mode",
        "hint_used",
        "assisted",
        "difficulty_band",
        "decision_id",
        "seq",
    }
)

ALLOWED_FIELDS: frozenset[str] = REQUIRED_FIELDS | OPTIONAL_FIELDS

#: A batch is at most this many events (INTEGRATION §6: 413 payload_too_large).
MAX_EVENTS = 500

#: And at most this many bytes of body, before parsing.
MAX_BODY_BYTES = 1_000_000

#: How far in the future a client clock may be. Beyond this the event cannot be
#: placed in a partition window with any confidence.
FUTURE_TOLERANCE = timedelta(minutes=5)

#: And how far back. Older than this, retention has probably already run.
EARLIEST = timedelta(days=400)

#: free text the learner typed is hashed, never stored (see observe/service.py).
MAX_RESPONSE_LENGTH = 4096


class EventInvalid(ValueError):
    """A batch the API refuses, with the index and field that caused it."""

    def __init__(self, index: int, field_name: str, detail: str) -> None:
        super().__init__(f"event {index}: {field_name}: {detail}")
        self.index = index
        self.field_name = field_name
        self.detail = detail


@dataclass(frozen=True)
class Event:
    """One validated learning event, ready to store."""

    learner_id: str
    skill: str
    item_id: str
    correct: bool
    ts: datetime
    seq: int | None = None
    session_id: str | None = None
    device: str | None = None
    input_mode: str | None = None
    latency_ms: int | None = None
    hint_used: bool = False
    assisted: bool = False
    difficulty_band: str | None = None
    decision_id: str | None = None
    response_digest: bytes | None = None
    response_length: int | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dedupe_key(self) -> tuple[str, str, int] | None:
        """Event-level identity, when the host gave us one."""
        if self.seq is None or self.session_id is None:
            return None
        return (self.learner_id, self.session_id, self.seq)


def _require_str(raw: dict[str, Any], name: str, index: int, *, lo: int, hi: int) -> str:
    value = raw.get(name)
    if not isinstance(value, str):
        raise EventInvalid(index, name, "must be a string")
    if not lo <= len(value) <= hi:
        raise EventInvalid(index, name, f"must be {lo}-{hi} characters")
    return value


def _optional_str(raw: dict[str, Any], name: str, index: int, *, hi: int) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventInvalid(index, name, "must be a string")
    if len(value) > hi:
        raise EventInvalid(index, name, f"must be at most {hi} characters")
    return value


def parse_event(raw: object, *, index: int, now: datetime) -> Event:
    """Validate one event dict, or raise :class:`EventInvalid`."""
    if not isinstance(raw, dict):
        raise EventInvalid(index, "event", "must be an object")

    unknown = sorted(set(raw) - ALLOWED_FIELDS)
    if unknown:
        raise EventInvalid(index, "keys", "unknown field(s): " + ", ".join(unknown))

    missing = sorted(REQUIRED_FIELDS - set(raw))
    if missing:
        raise EventInvalid(index, "keys", "missing field(s): " + ", ".join(missing))

    learner_id = _require_str(raw, "learner_id", index, lo=8, hi=64)
    try:
        validate_learner_id(learner_id)
    except LearnerIdInvalid as exc:
        raise EventInvalid(index, "learner_id", str(exc)) from exc

    skill = _require_str(raw, "skill", index, lo=1, hi=64)
    if not (skill[0].isalpha() and all(char.isalnum() or char in "_.-" for char in skill)):
        raise EventInvalid(index, "skill", "must be letters, digits, dot, dash or underscore")

    item_id = _require_str(raw, "item_id", index, lo=1, hi=128)

    correct = raw.get("correct")
    if not isinstance(correct, bool):
        raise EventInvalid(index, "correct", "must be true or false")

    ts = raw.get("ts")
    if not isinstance(ts, str):
        raise EventInvalid(index, "ts", "must be an ISO-8601 timestamp string")
    parsed = _parse_ts(ts, index)
    if parsed > now + FUTURE_TOLERANCE:
        raise EventInvalid(index, "ts", "is in the future")
    if parsed < now - EARLIEST:
        raise EventInvalid(index, "ts", "is older than the retention window")

    latency_ms = raw.get("latency_ms")
    if latency_ms is not None:
        if not isinstance(latency_ms, int) or isinstance(latency_ms, bool):
            raise EventInvalid(index, "latency_ms", "must be an integer")
        if not 0 <= latency_ms <= 86_400_000:
            raise EventInvalid(index, "latency_ms", "must be 0-86400000")

    seq = raw.get("seq")
    if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool) or seq < 0):
        raise EventInvalid(index, "seq", "must be a non-negative integer")

    for name in ("hint_used", "assisted"):
        value = raw.get(name)
        if value is not None and not isinstance(value, bool):
            raise EventInvalid(index, name, "must be true or false")

    difficulty_band = _optional_str(raw, "difficulty_band", index, hi=32)
    if difficulty_band is not None and not _is_band(difficulty_band):
        raise EventInvalid(index, "difficulty_band", "must be a band like 0.55-0.75")

    decision_id = _optional_str(raw, "decision_id", index, hi=64)
    if decision_id is not None and not decision_id.strip():
        raise EventInvalid(index, "decision_id", "must not be blank")

    response = _optional_str(raw, "response", index, hi=MAX_RESPONSE_LENGTH)
    digest = hashlib.sha256(response.encode("utf-8")).digest() if response is not None else None

    return Event(
        learner_id=learner_id,
        skill=skill,
        item_id=item_id,
        correct=correct,
        ts=parsed,
        seq=seq,
        session_id=_optional_str(raw, "session_id", index, hi=64),
        device=_optional_str(raw, "device", index, hi=64),
        input_mode=_optional_str(raw, "input_mode", index, hi=32),
        latency_ms=latency_ms,
        hint_used=bool(raw.get("hint_used", False)),
        assisted=bool(raw.get("assisted", False)),
        difficulty_band=difficulty_band,
        decision_id=decision_id,
        response_digest=digest,
        response_length=len(response) if response is not None else None,
    )


def _is_band(value: str) -> bool:
    return all(char.isdigit() or char in "-." for char in value) and value[:1].isdigit()


def _parse_ts(value: str, index: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventInvalid(index, "ts", "is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        # A naive timestamp is ambiguous across a DST boundary and the whole
        # contract is UTC. Refusing is kinder than guessing.
        raise EventInvalid(index, "ts", "must carry a timezone offset (the contract is UTC)")
    return parsed.astimezone(UTC)
