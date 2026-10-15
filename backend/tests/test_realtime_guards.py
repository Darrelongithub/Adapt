"""Concurrent-request guards: what they refuse, and what they must never refuse.

The brief's fourth item is about spikes: a tab that reconnects in a loop, a page that sends a
frame per keystroke, a load generator pointed at the demo. Two kinds of limit answer that, and
they answer different questions:

* a **token bucket** (`Limiter`) bounds how *often* work may start;
* an **in-flight guard** (`InFlight`) bounds how much is running *at once*. Twenty requests at
  three a minute each pass every bucket in the table and still put twenty model calls on a free
  tier, so without the second kind the first kind is not enough.

This file is the floor under both, plus the websocket behaviour that uses them:

* the guard refuses the (n+1)th concurrent request, releases on the way out *including when the
  handler raised*, and counts what it refused (``test_*in_flight*``, ``test_*leak*``);
* a burst is refused before a concurrency question is asked, because a loop is not a queue
  (``test_a_burst_is_a_loop_not_a_queue``);
* the duplicate window drops the same bytes twice inside the window and never the same learner
  asking twice on purpose (``test_the_duplicate_window_drops_a_repeat_*``);
* over HTTP, a saturated real-time route answers 429 with a readable reason and a `Retry-After`,
  while a *read* is never counted at all (``test_a_saturated_realtime_route_*``);
* over the socket, a flood of control frames is answered with `throttle`, a repeated frame with
  `deduped`, a second turn with `busy`, and too many sockets for one learner with a 1013 close
  (``test_the_socket_*``).

Run: ``pytest -q tests/test_realtime_guards.py``.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from adapt.security.ratelimit import (
    DEFAULT_LIMITS,
    DuplicateWindow,
    InFlight,
    build_limiter,
    build_realtime_guard,
    realtime_guard,
    reset_realtime_guard,
)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


# --------------------------------------------------------------------------- #
# the in-flight guard itself
# --------------------------------------------------------------------------- #


def test_the_guard_admits_up_to_its_limit_and_refuses_the_next_one():
    guard = InFlight(max_inflight=2, window_ms=1000, burst_max=50)
    assert guard.begin("learner")[0] is True
    assert guard.begin("learner")[0] is True
    allowed, reason, wait = guard.begin("learner")
    assert allowed is False and reason == "in_flight" and wait > 0
    # A different key is a different learner: one learner's flood is not another's problem.
    assert guard.begin("other")[0] is True

    guard.end("learner")
    assert guard.begin("learner")[0] is True, "a released slot has to be reusable"
    assert guard.snapshot()["peak_in_flight"] >= 2
    assert guard.snapshot()["active_keys"] == 2
    assert guard.snapshot()["refused_in_flight"] == 1


def test_a_burst_is_a_loop_not_a_queue():
    """Too many *starts* in a window is a different failure from too many at once."""
    guard = InFlight(max_inflight=10, window_ms=1000, burst_max=3)
    for _ in range(3):
        allowed, _, _ = guard.begin("tab")
        assert allowed is True
        guard.end("tab")  # released immediately: nothing is concurrent, only fast
    allowed, reason, wait = guard.begin("tab")
    assert allowed is False and reason == "burst" and wait > 0
    # The burst counter is reported, so an operator can tell "someone is spamming" from
    # "someone has four tabs open".
    assert guard.snapshot()["refused_burst"] == 1
    assert guard.snapshot()["refused_in_flight"] == 0


def test_a_released_slot_is_released_even_when_the_work_raised():
    """A leaked slot would permanently lower the cap for that key -- a bug that only shows
    up under load, which is the worst kind to find in a demo."""
    guard = InFlight(max_inflight=1, window_ms=1000, burst_max=10)

    def work() -> None:
        assert guard.begin("k")[0] is True
        try:
            raise RuntimeError("the handler blew up")
        finally:
            guard.end("k")

    with pytest.raises(RuntimeError):
        work()
    assert guard.busy("k") == 0
    assert guard.begin("k")[0] is True, "the slot was never given back"


def test_ending_a_request_that_never_started_is_harmless():
    """Defensive: a caller that ends twice must not corrupt another caller's count."""
    guard = InFlight(max_inflight=1, window_ms=1000, burst_max=10)
    guard.begin("k")
    guard.end("k")
    guard.end("k")
    guard.end("nobody")
    assert guard.snapshot()["active_keys"] == 0
    assert guard.begin("k")[0] is True


def test_the_guard_is_built_from_settings_and_scaled_by_the_escape_hatch():
    """``ADAPT_RATE_LIMIT_MULTIPLIER`` scales the guard too: two stories about limits is one
    story too many."""
    from types import SimpleNamespace

    plain = SimpleNamespace(realtime_max_inflight=4, realtime_burst_window_ms=1000, realtime_burst_max=20, rate_limit_multiplier=1.0)
    scaled = SimpleNamespace(realtime_max_inflight=4, realtime_burst_window_ms=1000, realtime_burst_max=20, rate_limit_multiplier=60.0)
    assert build_realtime_guard(plain).snapshot()["max_in_flight"] == 4
    assert build_realtime_guard(scaled).snapshot()["max_in_flight"] == 240
    assert build_realtime_guard(scaled).snapshot()["burst_max"] == 1200

    # The process-wide guard is one object, so a report from anywhere describes the same
    # counters that are doing the refusing -- including after a reset, which is how a test
    # that wants to *see* a refusal gets a guard it can tighten.
    reset_realtime_guard()
    try:
        assert realtime_guard(plain) is realtime_guard(plain)
    finally:
        reset_realtime_guard()


def test_the_duplicate_window_drops_a_repeat_but_not_a_deliberate_second_ask():
    window = DuplicateWindow(900)
    assert window.seen("socket", '{"t":"text","text":"why flip it?"}') is False
    assert window.seen("socket", '{"t":"text","text":"why flip it?"}') is True
    # A different frame, or a different socket, is not a repeat.
    assert window.seen("socket", '{"t":"text","text":"what about dividing by zero?"}') is False
    assert window.seen("other", '{"t":"text","text":"why flip it?"}') is False
    assert window.snapshot()["suppressed"] == 1

    # And the window closes: the same words later are a new question, not a double-click.
    short = DuplicateWindow(30)
    assert short.seen("k", "same") is False
    time.sleep(0.05)
    assert short.seen("k", "same") is False
    short.forget("k")
    assert short.snapshot()["keys"] == 0


# --------------------------------------------------------------------------- #
# over HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture()
def saturated(client, services):
    """Saturate the process-wide in-flight guard for this test's client, then give it back.

    The middleware holds the same object ``realtime_guard()`` returns, so filling it here is
    exactly what a page firing a dozen parallel requests would do — without a thread pool, and
    deterministically. The fixture releases every slot it took, because a leaked slot in a
    session-scoped app would quietly lower the cap for every later test.
    """
    guard = realtime_guard()
    key = "rt:testclient"
    taken = 0
    while guard.begin(key)[0]:
        taken += 1
        if taken > 10_000:  # pragma: no cover - the loop is bounded by the cap
            pytest.fail("the guard never refused: max_inflight is unbounded")
    assert taken >= 1, "the guard refused before admitting anything"
    try:
        yield guard, taken
    finally:
        for _ in range(taken):
            guard.end(key)


def test_a_saturated_realtime_route_answers_429_in_the_house_shape(client, saturated):
    guard, taken = saturated
    response = client.post("/api/v1/subjects/plan", json={"topic": "adding fractions"})
    assert response.status_code == 429, response.text
    assert response.headers.get("retry-after"), "a client has to be told when to come back"
    body = response.json()
    detail = body["detail"]
    assert detail["error"] == "too_many_concurrent"
    assert detail["scope"] == "realtime"
    assert detail["retry_after_s"] >= 0
    assert "at once" in detail["message"] or "wait" in detail["message"]
    assert taken >= 1
    assert guard.snapshot()["refused_in_flight"] >= 1


def test_a_read_is_never_counted_against_the_concurrency_guard(client, saturated):
    """A page loading eight assets is not a spike, and a guard that says it is gets turned off."""
    assert client.get("/api/v1/voice/capabilities").status_code in (200, 401, 403)
    assert client.get("/api/v1/health").status_code < 500
    # And the route list itself is not a real-time path, so the guard never sees it.
    from adapt.security.middleware import REALTIME_PREFIXES

    assert not "/api/v1/health".startswith(REALTIME_PREFIXES)
    for path in ("/api/v1/subjects/plan", "/api/v1/tutor/ask", "/api/v1/lessons/run/x/progress"):
        assert path.startswith(REALTIME_PREFIXES), path


def test_every_declared_scope_is_still_used_somewhere(client):
    """The old promise, restated here because a new scope was added next to these tests."""
    source = "\n".join(p.read_text() for p in (Path(__file__).resolve().parents[1] / "src" / "adapt").rglob("*.py"))
    for scope in ("ws_control", "ws_turn", "voice_frame", "llm", "ip"):
        assert f'"{scope}"' in source, f"scope {scope!r} is configured but never used"
        assert scope in DEFAULT_LIMITS


# --------------------------------------------------------------------------- #
# over the socket
# --------------------------------------------------------------------------- #


def _drain(ws, wanted: str, tries: int = 24) -> dict:
    """Read frames until the one we are waiting for arrives.

    Every read is driven by a frame the server is guaranteed to send, because a websocket read
    with no timeout is how a suite hangs instead of failing.
    """
    for _ in range(tries):
        frame = json.loads(ws.receive_text())
        if frame.get("t") == wanted:
            return frame
    raise AssertionError(f"no {wanted!r} frame arrived")


def test_the_socket_refuses_a_flood_of_control_frames_with_a_reason(client, learner, services):
    """Control frames were the one unmetered input on this server: only audio was throttled,
    so a page sending a text frame per keystroke could start a turn per keystroke."""
    strict = build_limiter(multiplier=1.0)  # burst 12 on ws_control, instead of the test multiplier
    previous, services.limiter = services.limiter, strict
    try:
        with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["t"] == "hello"
            throttled = None
            for n in range(40):
                ws.send_text(json.dumps({"t": "ping", "n": n}))
                frame = json.loads(ws.receive_text())
                if frame.get("t") == "throttle":
                    throttled = frame
                    break
            assert throttled is not None, "a flood of control frames was never refused"
            assert throttled["scope"] == "control"
            assert throttled["retry_after_s"] >= 0
            assert throttled["recoverable"] is True
    finally:
        services.limiter = previous


def test_the_socket_drops_the_same_frame_twice_and_says_so(client, learner):
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        ws.send_text(json.dumps({"t": "status"}))
        first = _drain(ws, "status")
        assert first["turns"] == 0
        # The same frame again, immediately: the socket answers with the reason instead of
        # doing the work twice.
        ws.send_text(json.dumps({"t": "status"}))
        answer = json.loads(ws.receive_text())
        assert answer["t"] == "deduped", answer
        assert answer["recoverable"] is True
        # A different frame still works, so the duplicate window is not a socket-wide mute.
        ws.send_text(json.dumps({"t": "ping"}))
        assert _drain(ws, "pong")["t"] == "pong"


def test_the_socket_allows_one_turn_at_a_time(client, learner, services):
    """A page that fires a message per keystroke gets one answer, not a queue of model calls."""
    slow = _SlowConversation()
    original = services.make_conversation
    services.make_conversation = lambda *a, **kw: slow  # type: ignore[assignment]
    try:
        with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
            assert json.loads(ws.receive_text())["t"] == "hello"
            # Two turns' worth of frames, back to back, then a ping: the ping is what makes
            # this test terminate, because every read is driven by a frame the server is
            # guaranteed to send (the same rule `test_voice_pipeline` follows).
            ws.send_text(json.dumps({"t": "text", "text": "first"}))
            ws.send_text(json.dumps({"t": "text", "text": "second"}))
            ws.send_text(json.dumps({"t": "ping"}))
            frames = []
            for _ in range(12):
                frame = json.loads(ws.receive_text())
                frames.append(frame)
                if frame.get("t") == "pong":
                    break
            kinds = [f.get("t") for f in frames]
            assert "pong" in kinds, f"the control loop stopped answering: {kinds}"
            busy = next((f for f in frames if f.get("t") == "busy"), None)
            assert busy is not None, f"the second turn was not refused: {kinds}"
            # The frame names the state when the conversation can say (`thinking`), and says
            # `working` when it cannot -- an honest fallback rather than a guess.
            assert busy["state"] in {"working", "thinking", "transcribing", "speaking"}
            assert "wait" in busy["detail"].lower() or "already" in busy["detail"].lower()
            assert slow.started == 1, f"a second turn was started anyway: {slow.started}"
    finally:
        services.make_conversation = original  # type: ignore[assignment]


def test_too_many_sockets_for_one_learner_is_refused_legibly(client, learner):
    """A reconnect loop is a socket flood; the refusal has to be a frame a client can read."""
    guard = realtime_guard()
    key = _learner_key(learner)
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        # The socket that just opened is counted under this learner's key -- which is the
        # property that makes the admission cap a cap on *sockets*, not on luck.
        assert guard.busy(key) >= 1, guard.snapshot()
        from adapt.api import ws as WS

        held = 0
        while guard.begin(key)[0]:
            held += 1
        try:
            with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as second:
                frame = json.loads(second.receive_text())
                assert frame["t"] == "error" and frame["where"] == "admission", frame
                assert frame["recoverable"] is True
                assert frame["retry_after_s"] >= 0
                # ...and the socket is then closed with a code that says "try again later",
                # rather than left open and silent.
                with pytest.raises(WebSocketDisconnect) as closed:
                    second.receive_text()
                assert closed.value.code == 1013, closed.value.code
        finally:
            for _ in range(held):
                guard.end(key)
    assert WS.MAX_FRAME_BYTES > 0  # the module is imported: this is the socket path, not a copy


def _learner_key(learner) -> str:
    """The guard key the socket uses for this learner: ``ws:<student_id>``."""
    return f"ws:{learner['student_id']}"


class _SlowConversation:
    """A conversation that never finishes its first turn, so the second one is refused."""

    def __init__(self) -> None:
        self.started = 0
        self.sample_rate = 16000
        self.turns: list[object] = []
        self.bytes_in = 0
        self.voice_enabled = False
        self.gender = "female"
        self.barge_in = False
        self.settings = None
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start_text(self, text: str) -> None:
        self.started += 1
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._never())

    async def _never(self) -> None:  # pragma: no cover - the point is that it never returns
        await asyncio.sleep(3600)

    async def push_audio(self, *a, **kw) -> None:  # pragma: no cover - unused in this test
        return None

    def start_listening(self) -> None:
        return None

    def set_voice_enabled(self, value: bool) -> None:
        self.voice_enabled = value

    def set_gender(self, value: str) -> None:
        self.gender = value

    def status(self) -> dict[str, object]:
        return {"state": "thinking", "turns": 0, "session_id": None}

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()


# --------------------------------------------------------------------------- #
# the browser half
# --------------------------------------------------------------------------- #


def test_the_browser_half_of_the_guard_exists_and_is_used():
    """A server-side refusal costs a round trip that never needed to happen.

    The client guards are the cheap ones: a double-click on *send* and a held Enter on *submit*
    are refused before a frame leaves the page. They live in one tested module
    (``frontend/src/lib/debounce.test.mjs`` runs with ``npm run test:lib``) and this test is the
    backend's evidence that the pages use them -- the same habit ``test_api_learner_loop`` has
    for the websocket path.
    """
    module = FRONTEND / "src" / "lib" / "debounce.ts"
    assert module.exists(), "the client-side guards are missing"
    text = module.read_text()
    for name in ("createDeduper", "createInflight", "debounce", "throttle"):
        assert f"export function {name}" in text, f"{name} is not exported any more"
    assert (FRONTEND / "src" / "lib" / "debounce.test.mjs").exists(), "the guards are untested"

    voice = (FRONTEND / "src" / "app" / "voice" / "page.tsx").read_text()
    assert "createDeduper" in voice and "debounce" in voice, "the voice screen is unguarded"
    assert '"t": "throttle"' not in voice, "the browser does not send server-only frames"
    for frame in ('"throttle"', '"deduped"', '"busy"'):
        assert frame in voice, f"the screen ignores the server's {frame} frame"

    flow = (FRONTEND / "src" / "lib" / "flow.ts").read_text()
    assert "createInflight" in flow and "createDeduper" in flow, "the submit path is unguarded"
