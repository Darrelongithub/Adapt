"""Event-level telemetry: schema, validation and aggregation.

Blueprint section 6 requires the *full sequence* of events, and section 12
forbids unrelated surveillance and keystroke logging. Both are implemented here
as machine-enforced rules rather than intentions:

* ``ALLOWED_EVENTS`` is a positive allowlist of event types *and* payload keys.
  Anything else is dropped at the API boundary (and reported in the response so
  the client developer notices).
* ``FORBIDDEN_PAYLOAD_KEYS`` is a denylist that catches the surveillance-shaped
  fields people reach for by accident (keystrokes, cursor paths, clipboard,
  camera/mic presence, focus text, selection).
* Numbers are clamped and strings truncated, so a tampering client cannot smuggle
  an arbitrary payload into the learner's encrypted store.

Aggregation is deliberately pure so it can be unit-tested and re-run after the
fact: metrics like "time on item" are derived from timestamps, never trusted from
the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

MAX_PAYLOAD_KEYS = 12
MAX_STRING_LEN = 200

# Anything a client may *not* send, whatever the event type. Checked on
# normalized keys so `keydownCount`, `key_down_count` and `Keystrokes` all fail.
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "keystroke",
        "keystrokes",
        "keydown",
        "keyup",
        "keypress",
        "typing",
        "keys",
        "paste",
        "clipboard",
        "copy",
        "selection",
        "selectedtext",
        "mouse",
        "mousemove",
        "cursor",
        "pointer",
        "screenshot",
        "screen",
        "camera",
        "mic",
        "microphone",
        "webcam",
        "face",
        "gaze",
        "eyetracking",
        "focus",
        "focustext",
        "activewindow",
        "activesite",
        "browserhistory",
        "devtools",
        "battery",
        "deviceid",
        "hardware",
        "geolocation",
        "location",
        "ip",
        "timezoneoffset",
        "fingerprint",
        "ua",
        "useragent",
        "emulator",
        "rooted",
        "emotion",
        "sentiment",
        "attention",
        "engagement",
        "motivation",
    }
)

# Positive allowlist: event type -> allowed payload keys with (kind, bounds).
ALLOWED_EVENTS: dict[str, dict[str, tuple[str, tuple[float, float] | None]]] = {
    "session_start": {"mode": ("str", None), "device_hint": ("str", None), "locale": ("str", None)},
    "session_end": {"reason": ("str", None), "active_ms": ("int", (0, 6 * 3600 * 1000))},
    "pause": {"reason": ("str", None)},
    "resume": {"away_ms": ("int", (0, 6 * 3600 * 1000))},
    # Visibility change only: how long the tab was hidden. Never what they looked at.
    "focus_change": {"away_s": ("num", (0, 3600))},
    "question_shown": {"stem_words": ("int", (0, 5000)), "variant": ("str", None)},
    "first_interaction": {"ms_since_shown": ("int", (0, 6 * 3600 * 1000)), "target": ("str", None)},
    "response_change": {
        "ms_since_shown": ("int", (0, 6 * 3600 * 1000)),
        "from": ("str", None),
        "to": ("str", None),
        "index": ("int", (0, 200)),
    },
    "hint_opened": {"hint_no": ("int", (0, 20)), "ms_since_shown": ("int", (0, 6 * 3600 * 1000))},
    "explanation_opened": {
        "ms_since_shown": ("int", (0, 6 * 3600 * 1000)),
        "chars": ("int", (0, 100000)),
        "seconds_read": ("num", (0, 3600)),
    },
    "example_opened": {"ms_since_shown": ("int", (0, 6 * 3600 * 1000)), "step": ("int", (0, 50))},
    "visual_opened": {"ms_since_shown": ("int", (0, 6 * 3600 * 1000))},
    "resource_opened": {"provider": ("str", None), "resource_id": ("str", None)},
    "answer_submitted": {
        "attempt_no": ("int", (1, 50)),
        "correct": ("bool", None),
        "score": ("num", (0, 1)),
    },
    "feedback_shown": {"kind": ("str", None), "chars": ("int", (0, 100000))},
    "teachback_started": {"topic_id": ("str", None), "mode": ("str", None)},
    "teachback_ended": {"seconds": ("num", (0, 3600)), "words": ("int", (0, 100000)), "mode": ("str", None)},
    "voice_turn": {"interrupted": ("bool", None), "words": ("int", (0, 100000)), "backend": ("str", None)},
    "proposal_shown": {"proposal_id": ("str", None), "strategy": ("str", None)},
    "proposal_decided": {"proposal_id": ("str", None), "decision": ("str", None)},
    "adaptation_effect": {"strategy": ("str", None), "metric": ("str", None)},
}

TRUSTED_META_KEYS = frozenset(
    {
        "item_id",
        "topic_id",
        "attempt_id",
        "ms_since_question",
        "experience_mode",
        "variant",
    }
)


@dataclass
class Sanitized:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.type in ALLOWED_EVENTS


def _norm_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def is_forbidden_key(key: str) -> bool:
    norm = _norm_key(key)
    return any(norm == forb or (len(norm) > 5 and norm.startswith(forb)) for forb in FORBIDDEN_PAYLOAD_KEYS)


def _coerce(kind: str, value: Any, bounds: tuple[float, float] | None) -> Any:
    if kind == "bool":
        return bool(value)
    if kind == "int":
        try:
            v = int(float(value))
        except (TypeError, ValueError):
            return None
        return _bound(v, bounds)
    if kind == "num":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return _bound(v, bounds)
    # str: truncate, strip control chars, never allow nested objects
    if value is None:
        return None
    s = str(value)
    s = "".join(ch for ch in s if ch >= " " or ch in "\t")[:MAX_STRING_LEN]
    return s


def _bound(v: float, bounds: tuple[float, float] | None) -> float:
    if bounds is None:
        return v
    lo, hi = bounds
    return max(lo, min(hi, v))


def sanitize_event(type: str, payload: dict[str, Any] | None) -> Sanitized:  # noqa: A002
    """Validate one client event against the allowlist.

    Unknown event types are rejected outright (``rejected``); known types keep
    only their allowed scalar keys.
    """
    out = Sanitized(type=str(type))
    schema = ALLOWED_EVENTS.get(out.type)
    if schema is None:
        out.rejected.append(f"unknown event type '{out.type}'")
        return out
    src = payload or {}
    for key, value in src.items():
        if is_forbidden_key(key):
            out.rejected.append(f"forbidden telemetry field '{key}'")
            continue
        norm = _norm_key(key)
        match = next((k for k in schema if _norm_key(k) == norm), None)
        if match is None:
            if norm in {_norm_key(k) for k in TRUSTED_META_KEYS}:
                out.payload[key] = _coerce("str", value, None)
            else:
                out.dropped.append(key)
            continue
        if len(out.payload) >= MAX_PAYLOAD_KEYS:
            out.dropped.append(key)
            continue
        kind, bounds = schema[match]
        coerced = _coerce(kind, value, bounds)
        if coerced is not None:
            out.payload[match] = coerced
    return out


def allowed_event_types() -> list[str]:
    return sorted(ALLOWED_EVENTS)


# --------------------------------------------------------------------------- #
# aggregation: derive metrics from the event sequence
# --------------------------------------------------------------------------- #


@dataclass
class AttemptMetrics:
    """Derived, replayable metrics for one question (blueprint §6 example set)."""

    item_id: str | None = None
    topic_id: str | None = None
    shown_at: datetime | None = None
    first_interaction_ms: int | None = None
    time_on_item_ms: int | None = None
    change_count: int = 0
    first_answer: Any = None
    final_answer: Any = None
    answer_changes: list[dict[str, Any]] = field(default_factory=list)
    submission_ms: int | None = None
    attempts: int = 0
    hints_used: int = 0
    examples_opened: int = 0
    explanation_opened: bool = False
    visual_opened: bool = False
    away_ms: int = 0
    correct: bool | None = None
    score: float | None = None
    teachback_seconds: float | None = None
    teachback_words: int | None = None
    interrupted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], {}, False)}


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def metrics_for_events(events: Iterable[dict[str, Any]]) -> AttemptMetrics:
    """Fold an ordered event list (already-sanitized dicts with ``ts``) into metrics.

    ``events`` items look like ``{"type", "ts", "payload", "item_id", ...}`` --
    either ORM rows converted by ``event_dict()`` or raw ingest records.
    """
    m = AttemptMetrics()
    shown: datetime | None = None
    prev_ts: datetime | None = None
    last_sub_ts: datetime | None = None
    rows = sorted(events, key=lambda e: (e.get("ts") or datetime.min))
    for e in rows:
        etype = e.get("type")
        payload = e.get("payload") or {}
        ts = e.get("ts")
        m.item_id = e.get("item_id") or m.item_id
        m.topic_id = e.get("topic_id") or m.topic_id
        if etype == "question_shown":
            shown = ts
            m.shown_at = ts
            # Everything below is *per question*. A fold that only resets the answer
            # counters carries the previous card's hints, examples and away time onto
            # the next one, and AI 1 then reads "needed 2 hints" for a question that
            # was answered cold. Anything that is counted, not merely stamped, has to
            # be cleared here.
            m.first_interaction_ms = None
            m.answer_changes = []
            m.change_count = 0
            m.hints_used = 0
            m.examples_opened = 0
            m.explanation_opened = False
            m.visual_opened = False
            m.away_ms = 0
            m.teachback_seconds = None
            m.teachback_words = None
            m.interrupted = False
        if etype == "first_interaction" and shown is not None and ts is not None:
            ms = payload.get("ms_since_shown")
            m.first_interaction_ms = int(ms) if ms is not None else max(0, _ms((ts - shown).total_seconds()))
        if etype == "response_change":
            m.change_count += 1
            entry = {
                "at_ms": int(payload.get("ms_since_shown") or 0)
                if shown is None or ts is None
                else max(0, _ms((ts - shown).total_seconds())),
                "ts": ts,
                "from": payload.get("from"),
                "to": payload.get("to"),
            }
            if m.first_answer is None and entry["from"] is not None:
                m.first_answer = entry["from"]
            m.final_answer = entry["to"]
            m.answer_changes.append(entry)
        elif etype == "hint_opened":
            m.hints_used += 1
        elif etype == "example_opened":
            m.examples_opened += 1
        elif etype == "explanation_opened":
            m.explanation_opened = True
        elif etype == "visual_opened":
            m.visual_opened = True
        elif etype == "answer_submitted":
            m.attempts = int(payload.get("attempt_no") or (m.attempts + 1))
            if "correct" in payload:
                m.correct = bool(payload["correct"])
            if "score" in payload:
                m.score = float(payload["score"])
            m.submission_ms = max(0, _ms((ts - shown).total_seconds())) if (ts and shown) else None
            m.final_answer = payload.get("answer", m.final_answer)
            last_sub_ts = ts
        elif etype == "teachback_started":
            m.teachback_seconds = 0.0
        elif etype == "teachback_ended":
            m.teachback_seconds = float(payload.get("seconds") or 0.0)
            m.teachback_words = int(payload.get("words") or 0)
        elif etype in ("focus_change", "resume"):
            away = payload.get("away_ms") if "away_ms" in payload else None
            if away is None and payload.get("away_s") is not None:
                away = float(payload["away_s"]) * 1000
            if away is None and prev_ts is not None and ts is not None:
                away = max(0.0, (ts - prev_ts).total_seconds() * 1000)
            if away is not None:
                m.away_ms += int(away)
        elif etype == "voice_turn" and payload.get("interrupted"):
            m.interrupted = True
        prev_ts = ts
    if shown is not None and last_sub_ts is not None:
        m.time_on_item_ms = max(0, _ms((last_sub_ts - shown).total_seconds()))
    if m.first_interaction_ms is None and m.time_on_item_ms is not None:
        # No explicit first-interaction event (older client): leave it None rather
        # than guessing -- a missing measurement beats a fake one.
        m.first_interaction_ms = None
    return m


def session_aggregates(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Roll session-level behaviour counters (used to interpret attempts)."""
    out = {"pause_count": 0, "focus_change_count": 0, "away_seconds": 0, "interruptions": 0, "voice_turns": 0}
    away_ms = 0  # accumulated in milliseconds, rounded once at the end
    for e in events:
        etype = e.get("type")
        payload = e.get("payload") or {}
        if etype == "pause":
            out["pause_count"] += 1
            out["interruptions"] += 1
        elif etype == "focus_change":
            out["focus_change_count"] += 1
            away_ms += int(float(payload.get("away_s") or 0) * 1000)
        elif etype == "resume":
            away_ms += int(payload.get("away_ms") or 0)
            out["interruptions"] += 1
        elif etype == "voice_turn":
            out["voice_turns"] += 1
            if payload.get("interrupted"):
                out["interruptions"] += 1
    # Rounding once, after the sum, instead of truncating each contribution: a
    # learner who stepped away for 1.5 s and came back 2.5 s later was away for 4 s,
    # not the 3 s that two separate int() casts would have reported.
    out["away_seconds"] = int(round(away_ms / 1000))
    return out


def active_ms(total_ms: int, away_ms: int, *, floor_ms: int = 0) -> int:
    """Time actually spent, discounting away gaps (documented, not stealthy)."""
    return max(floor_ms, int(total_ms) - int(away_ms))


def latency_z(current_ms: int, history_ms: list[int]) -> float | None:
    """Robust z-score against the learner's own history (median/MAD).

    Used only as *evidence to investigate*, never as a confidence judgement.
    """
    if len(history_ms) < 4 or current_ms is None:
        return None
    xs = sorted(float(x) for x in history_ms if x is not None)
    if not xs:
        return None
    med = xs[len(xs) // 2]
    mad = sorted(abs(x - med) for x in xs)[len(xs) // 2]
    scale = 1.4826 * mad if mad > 0 else (max(1.0, med * 0.25))
    return round((float(current_ms) - med) / scale, 3)
