"""The voice websocket: audio in, VAD endpointing, answer + speech out.

Protocol (one connection per conversation; the endpoint is ``/ws/voice``, advertised by
``GET /api/v1/voice/capabilities`` so the client never hard-codes it):

client -> server
  * binary frame      : one microphone chunk (s16le mono, default 16 kHz; override
                        with the ``sr`` query parameter, e.g. ``sr=48000``)
  * {"t":"text", "text":...}  : typed or browser-dictated input -- same turn path
  * {"t":"listen"}            : push-to-talk: reopen the gate after a reply
  * {"t":"config", ...}       : voice on/off (``voice`` or ``voice_on``), gender,
                        barge-in on/off -> answered by {"t":"config_ack", ...}
  * {"t":"stop"}              : stop speaking right now
  * {"t":"status"} | {"t":"ping"}
server -> client
  * {"t":"hello", "sample_rate_in", "sample_rate_out", "status":{...}}
  * {"t":"state", "state": idle|listening|transcribing|thinking|speaking, "reason"?}
  * {"t":"transcript", "text", "backend", "ms", "note"}   what we heard
  * {"t":"reply", "text", "ms", "meta":{provider,intent,...}}
  * binary frame      : s16le mono speech at ``sample_rate_out`` (Kokoro: 24 kHz)
  * {"t":"tts_chunk", "index", "text"} : which words this audio was
  * {"t":"speak_unavailable", "reason", "text"} : no local TTS -> use browser voice
  * {"t":"error", "where", "detail", "recoverable"}

Three properties of the real world shaped this file:

1. **Audio never touches disk.** Frames are decoded in memory, transcribed, then
   dropped; only transcripts and turn metrics are stored, and only while the learner
   is using voice.
2. **Interruption has to be designed in, not bolted on.** The read loop keeps
   feeding the VAD while the assistant is talking (the pipeline answers in its own
   task), so sustained speech for ``barge_in_speech_ms`` flips the state and cancels
   the next TTS chunk. Chunked speech is what makes the latency of "stop" one chunk
   instead of one answer.
3. **A browser cannot send an Authorization header on a websocket.** The session
   cookie is the credential, and the same-origin/trusted-host middleware is what
   stops another site from opening the socket on the learner's behalf.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..config import get_settings
from ..db.models import LearningSession
from ..security import auth
from ..services import current

log = logging.getLogger("adapt.ws")

router = APIRouter()

MAX_FRAME_BYTES = 512 * 1024


def _authenticate(token: str | None, session_id: str | None) -> tuple[str | None, str | None]:
    """Resolve the socket's identity from the session cookie.

    Returns ``(student_id, session_id)``. The learning session is optional: a
    learner can talk without one, in which case turns are attributed to the learner
    and a session row is created lazily by AI 2's recorder.
    """
    if not token:
        return None, None
    svcs = current()
    with svcs.db.session() as s:
        row = auth.lookup_session(s, token)
        if row is None:
            return None, None
        if session_id:
            sess = s.get(LearningSession, session_id)
            if sess is None or sess.student_id != row.student_id:
                return None, None
            return row.student_id, session_id
        # no explicit session: attach to the newest active one, else none
        sess = s.query(LearningSession).filter(LearningSession.student_id == row.student_id, LearningSession.status == "active").order_by(LearningSession.started_at.desc()).first()
        return row.student_id, (sess.id if sess else None)


@router.websocket("/ws/voice")
async def voice(
    websocket: WebSocket,
    sr: int = Query(default=0, ge=0, le=192000),
    encoding: str = Query(default="s16le", pattern="^(s16le|f32le|wav)$"),
    session_id: str | None = Query(default=None),
    gender: str | None = Query(default=None),
    voice_on: bool = Query(default=True),
) -> None:
    st = get_settings()
    student_id, sid = _authenticate(websocket.cookies.get(st.cookie_name), session_id)
    if student_id is None:
        # Refused, not silently accepted -- and refused in a way a browser can read. Closing
        # *before* `accept()` answers the handshake with HTTP 403, which surfaces to a page as
        # code 1006 with no reason: `voice/page.tsx` switches on 4401 to say "sign in again",
        # and that branch was unreachable. Accepting for the length of one frame hands over no
        # data and creates no session; it only makes the refusal legible, then closes with 4401.
        await websocket.accept()
        await websocket.send_text(
            json.dumps(
                {
                    "t": "error",
                    "where": "auth",
                    "detail": "authentication required - sign in again; the voice socket is refused, not ignored",
                    "recoverable": False,
                },
                separators=(",", ":"),
            )
        )
        await websocket.close(code=4401, reason="authentication required")
        return
    await websocket.accept()
    sample_rate = sr or int(getattr(st, "sample_rate", 16000))

    async def send(msg: dict[str, Any]) -> None:
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.send_text(json.dumps(msg, separators=(",", ":")))

    async def send_audio(pcm: bytes) -> bool:
        if websocket.client_state is not WebSocketState.CONNECTED:
            return False
        await websocket.send_bytes(pcm)
        return True

    try:
        conv = current().make_conversation(
            student_id,
            send=send,
            send_audio=send_audio,
            gender=gender or "female",
            session_id=sid,
        )
    except Exception as exc:  # engine construction must be explainable, not a 1011
        log.exception("voice pipeline failed to start")
        await send({"t": "error", "where": "pipeline", "detail": f"{type(exc).__name__}: {exc}"[:200], "recoverable": False})
        await websocket.close(code=1011)
        return

    conv.sample_rate = sample_rate
    conv.set_voice_enabled(voice_on)
    await send(
        {
            "t": "hello",
            "sample_rate_in": sample_rate,
            "sample_rate_out": int(getattr(st, "tts_sample_rate", 24000)),
            "encoding": encoding,
            "session_id": sid,
            "status": conv.status(),
        }
    )

    frames = 0
    try:
        while True:
            try:
                msg = await websocket.receive()
            except (WebSocketDisconnect, RuntimeError):
                break
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                frames += 1
                ok, retry = current().limiter.allow("voice_frame", f"voice:{student_id}")
                if not ok:
                    # A runaway tab must not be able to saturate the CPU; we tell it
                    # to back off instead of dropping frames silently.
                    await send({"t": "throttle", "retry_after_s": round(retry, 2), "recoverable": True})
                    await asyncio.sleep(min(0.5, retry))
                    continue
                if len(data) > MAX_FRAME_BYTES:
                    await send({"t": "error", "where": "audio", "detail": f"frame too large (max {MAX_FRAME_BYTES} bytes)", "recoverable": True})
                    continue
                try:
                    await conv.push_audio(data, sample_rate=sample_rate, encoding=encoding)
                except Exception as exc:
                    log.exception("audio frame failed")
                    await send({"t": "error", "where": "audio", "detail": str(exc)[:200], "recoverable": True})
                continue
            raw = msg.get("text")
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await send({"t": "error", "where": "control", "detail": "expected one JSON object", "recoverable": True})
                continue
            await _control(payload, conv, send)
    finally:
        await conv.stop()
        _record_session(sid, len(conv.turns))
        log.info("voice socket closed: learner=%s turns=%d frames=%d bytes=%d", student_id[-8:], len(conv.turns), frames, conv.bytes_in)


async def _control(payload: dict[str, Any], conv: Any, send: Any) -> None:
    kind = str(payload.get("t") or payload.get("type") or "")
    if kind in ("text", "say", "typed"):
        text_in = str(payload.get("text") or "")[:2000]
        if text_in.strip():
            # Started, not awaited: this loop also reads the microphone, and a turn must
            # not block the learner's ability to interrupt it.
            conv.start_text(text_in)
        return
    if kind == "listen":
        conv.start_listening()
        await send({"t": "state", "state": "listening", "reason": "push_to_talk"})
        return
    if kind == "config":
        # ``voice_on`` is the name of the query parameter on this endpoint, so the
        # frame accepts it too: two spellings for one setting is how a client ends up
        # silently not speaking.
        for key in ("voice", "voice_on"):
            if key in payload:
                conv.set_voice_enabled(bool(payload[key]))
                break
        if payload.get("gender") in ("male", "female"):
            conv.set_gender(str(payload["gender"]))
        if payload.get("barge_in") is not None:
            conv.barge_in = bool(payload["barge_in"]) and bool(getattr(conv.settings, "barge_in_enabled", True))
        await send({"t": "config_ack", "voice_enabled": conv.voice_enabled, "gender": conv.gender, "barge_in": conv.barge_in})
        return
    if kind in ("stop", "interrupt"):
        await conv.stop()
        await send({"t": "state", "state": "listening", "reason": "stopped_by_learner"})
        return
    if kind == "status":
        await send({"t": "status", **conv.status()})
        return
    if kind == "ping":
        await send({"t": "pong"})
        return
    await send({"t": "error", "where": "control", "detail": f"unknown message '{kind}'", "recoverable": True, "known": ["text", "listen", "config", "stop", "status", "ping"]})


def _record_session(session_id: str | None, turns: int) -> None:
    if not session_id or not turns:
        return
    try:
        with current().db.session() as s:
            sess = s.get(LearningSession, session_id)
            if sess is not None:
                sess.voice_turns = (sess.voice_turns or 0) + turns
    except Exception:  # teardown must not raise over a closed socket
        log.debug("voice turn accounting skipped", exc_info=True)
