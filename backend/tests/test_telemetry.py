"""The event allowlist and the metrics derived from it.

Two properties matter here and both are easy to lose in a refactor: a client cannot
push arbitrary JSON into the database, and the numbers AI 1 reasons about come from
the event *sequence*, not from whatever the client claimed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def ev(type_, *, seconds=0.0, base=None, **payload):  # noqa: A002
    base = base or datetime(2026, 1, 5, 9, 0, 0)
    return {"type": type_, "ts": base + timedelta(seconds=seconds), "payload": payload}


def test_only_listed_event_types_exist():
    from adapt.learning.telemetry import ALLOWED_EVENTS

    assert {"question_shown", "first_interaction", "response_change", "hint_opened", "pause", "resume", "focus_change"} <= set(ALLOWED_EVENTS)
    # Nothing that looks like a place to dump content or identifiers.
    assert not any(" keystroke" in k or "keystroke" in k for k in ALLOWED_EVENTS)
    for type_, schema in ALLOWED_EVENTS.items():  # noqa: A002
        assert isinstance(schema, dict), type_
        for field, (kind, bounds) in schema.items():
            assert kind in {"int", "num", "str", "bool"}, (type_, field, kind)
            if bounds is not None:
                assert bounds[0] <= bounds[1], (type_, field, bounds)


def test_unknown_type_is_rejected_not_stored():
    from adapt.learning.telemetry import sanitize_event

    out = sanitize_event("keystroke_log", {"keys": "the whole answer typed out"})
    assert not out.ok
    assert out.payload == {}
    assert out.rejected and "unknown event type" in out.rejected[0]


def test_payload_keys_are_filtered_and_coerced():
    from adapt.learning.telemetry import sanitize_event

    out = sanitize_event(
        "hint_opened",
        {"hint_no": "2", "seconds_read": 30, "device_id": "abc", "html": "<b>x</b>" * 40},
    )
    assert out.payload == {"hint_no": 2}, "only the schema's own fields survive"
    assert set(out.dropped) == {"seconds_read", "html"}
    assert any("forbidden" in r for r in out.rejected)


def test_trusted_meta_keys_pass_through_without_being_metrics():
    """item_id/topic_id/attempt_id locate the event; they are not measurements.

    They have to survive the filter or the server cannot tell which question a hint
    belonged to -- but they must not be folded into metrics as if they were numbers.
    """
    from adapt.learning.telemetry import metrics_for_events, sanitize_event

    out = sanitize_event("hint_opened", {"hint_no": 1, "item_id": "itm_a", "attempt_id": "att_1"})
    assert out.payload["item_id"] == "itm_a"
    m = metrics_for_events([{"type": "hint_opened", "payload": out.payload, "item_id": "itm_a"}])
    assert m.item_id == "itm_a" and m.hints_used == 1


@pytest.mark.parametrize(
    "key",
    [
        "ip",
        "ip_address",
        "user_agent",
        "User-Agent",
        "device_id",
        "screen_recording",
        "clipboard",
        "active_window",
        "geolocation",
        "attention_score",
        "sentiment",
        "keystrokes",
        "keydownCount",
    ],
)
def test_identifying_and_context_keys_are_forbidden_by_name(key):
    from adapt.learning.telemetry import is_forbidden_key

    assert is_forbidden_key(key), f"{key} must never be accepted as telemetry"


def test_out_of_range_values_are_clipped_not_invented():
    from adapt.learning.telemetry import sanitize_event

    # A client that reports a negative reading (or a clock skew) must not create a
    # negative duration, because those feed averages.
    out = sanitize_event("hint_opened", {"hint_no": -5})
    assert out.payload.get("hint_no") in (None, 0)
    out = sanitize_event("explanation_opened", {"seconds_read": 10_000_000})
    assert out.payload.get("seconds_read", 0) <= 3600


def test_metrics_fold_the_expected_sequence():
    from adapt.learning.telemetry import metrics_for_events

    events = [
        ev("question_shown", seconds=0),
        ev("first_interaction", seconds=3.5, ms_since_shown=3500),
        ev("response_change", seconds=20, ms_since_shown=20000, **{"from": "1/2", "to": "3/4"}),
        ev("hint_opened", seconds=25, hint_no=1),
        ev("example_opened", seconds=27, step=1),
        ev("explanation_opened", seconds=30, seconds_read=12),
        ev("answer_submitted", seconds=40, attempt_no=1, correct=True, score=1.0),
    ]
    m = metrics_for_events(events)
    assert m.first_interaction_ms == 3500
    assert m.time_on_item_ms == 40000
    assert m.change_count == 1 and m.hints_used == 1 and m.examples_opened == 1
    assert m.explanation_opened and m.correct and m.score == 1.0
    assert m.answer_changes[0]["from"] == "1/2"


def test_second_question_resets_per_question_counters():
    """Metrics are per question: a new card must not inherit the last one's hints."""
    from adapt.learning.telemetry import metrics_for_events

    base = datetime(2026, 1, 5, 9, 0, 0)
    events = [
        ev("question_shown", base=base),
        ev("hint_opened", base=base, seconds=1, hint_no=1),
        ev("answer_submitted", base=base, seconds=2, attempt_no=1, correct=False),
        ev("question_shown", base=base, seconds=5),
        ev("answer_submitted", base=base, seconds=8, attempt_no=1, correct=True),
    ]
    m = metrics_for_events(events)
    assert m.hints_used == 0, "the second item had no hint"
    assert m.correct and m.time_on_item_ms == 3000


def test_missing_first_interaction_stays_missing():
    from adapt.learning.telemetry import metrics_for_events

    m = metrics_for_events([ev("question_shown"), ev("answer_submitted", seconds=9)])
    assert m.first_interaction_ms is None
    assert m.time_on_item_ms == 9000


def test_away_time_is_counted_once():
    from adapt.learning.telemetry import active_ms, metrics_for_events

    base = datetime(2026, 1, 5, 9, 0, 0)
    events = [
        ev("question_shown", base=base),
        # focus_change reports seconds, resume reports milliseconds: both spellings
        # are in the schema, and a learner who hides the tab should not be double
        # charged for it.
        ev("focus_change", base=base, seconds=5, away_s=4),
        ev("resume", base=base, seconds=20, away_ms=11000),
        ev("answer_submitted", base=base, seconds=30),
    ]
    m = metrics_for_events(events)
    assert m.away_ms == 15000
    assert active_ms(30_000, m.away_ms) == 15000
    assert active_ms(1000, 5000) >= 0, "away time can exceed wall time on a resumed tab"


def test_latency_z_needs_a_history():
    from adapt.learning.telemetry import latency_z

    assert latency_z(1000, []) is None
    assert latency_z(1000, [1000, 1000, 1000]) in (0.0, None) or latency_z(1000, [1000, 1000, 1000]) == 0.0
    slow = latency_z(9000, [1000, 1200, 900, 1100])
    assert slow is not None and slow > 1.5


def test_ingest_assigns_monotonic_seq_and_dedupes_client_ids(client, services, learner):
    from adapt.services import telemetry as TEL

    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    batch = [
        {"type": "question_shown", "client_id": "c1", "payload": {}},
        {"type": "hint_opened", "client_id": "c2", "payload": {"hint_no": 1}},
        {"type": "hint_opened", "client_id": "c2", "payload": {"hint_no": 1}},  # retry of the same event
        {"type": "answer_changed", "payload": {"x": 1}},  # not in the allowlist
    ]
    with services.scope() as s:
        result = TEL.ingest(s, session_id=sid, student_id=learner["student_id"], events=batch)
    assert result.accepted == 2, result.reasons
    assert result.duplicate == 1
    assert result.rejected == 1

    with services.scope() as s:
        again = TEL.ingest(s, session_id=sid, student_id=learner["student_id"], events=[{"type": "pause", "payload": {}}])
        rows = TEL.event_stream(s, sid)
    assert again.accepted == 1
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    types = [r["type"] for r in rows]
    assert types[:4] == ["session_start", "question_shown", "hint_opened", "pause"], (
        "the batch landed in order after the session opener, and the rejected event left no row"
    )
    assert "answer_changed" not in types


def test_ingest_refuses_another_learners_session(client, services, learner):
    from adapt.services import telemetry as TEL

    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    with services.scope() as s:
        result = TEL.ingest(s, session_id=sid, student_id="stu_nobody", events=[{"type": "pause", "payload": {}}])
    assert result.accepted == 0 and result.rejected == 1
    assert "unknown session" in result.reasons[0]


def test_session_rollups_count_the_things_ai1_uses(client, services, learner):
    from adapt.services import telemetry as TEL

    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    events = [
        {"type": "question_shown", "payload": {}},
        {"type": "pause", "payload": {"reason": "the doorbell"}},
        {"type": "focus_change", "payload": {"away_s": 1.5}},
        {"type": "resume", "payload": {"away_ms": 2500}},
    ]
    with services.scope() as s:
        TEL.ingest(s, session_id=sid, student_id=learner["student_id"], events=events)
        stats = TEL.refresh_session_stats(s, session_id=sid)
    assert stats["pause_count"] == 1
    assert stats["focus_change_count"] == 1
    assert stats["interruptions"] == 2  # a pause and a resume are both interruptions
    assert stats["away_seconds"] == 4, "1.5 s + 2.5 s, rounded once instead of truncated twice"
