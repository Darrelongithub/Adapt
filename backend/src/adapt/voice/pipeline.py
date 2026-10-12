"""Conversation turn-taking: listen -> endpoint -> transcribe -> answer -> speak.

The state machine lives server-side so the browser only has to push microphone
frames and play audio, and so the *interruption* behaviour is one implementation
rather than a race between three client timers.

States::

    idle -> listening -> (endpoint) -> transcribing -> thinking -> speaking
                                                        ^            |
                                                        +-- barge-in+--> listening

Barge-in rules, and why they are asymmetric:
* while *listening*, normal VAD hangover ends the turn (generous: cutting a
  learner off mid-thought is the worst failure here);
* while *speaking*, only sustained speech (``barge_in_speech_ms``) counts as an
  interrupt, and it stops playback immediately -- a cough or an echo from the
  speakers should not kill an explanation.

The transport is injected (``send``), so the same pipeline runs over a WebSocket,
over the REST upload endpoint, and inside a test with a list in place of a socket.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import numpy as np

from .audio import pcm16_from_bytes, resample
from .tts import Chunk
from .vad import BaseVAD

log = logging.getLogger("adapt.voice")

def _next_or_none(stream: Any) -> Any:
    """``next(stream, None)`` as a plain function, so a thread can run it."""
    return next(stream, None)


class State(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class Turn:
    index: int
    text: str = ""
    reply: str = ""
    state: State = State.LISTENING
    audio_s: float = 0.0
    stt_ms: int = 0
    think_ms: int = 0
    speak_ms: int = 0
    interrupted: bool = False
    backend: str = "none"
    confidence: float | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "reply": self.reply,
            "state": self.state.value,
            "audio_s": round(self.audio_s, 3),
            "stt_ms": self.stt_ms,
            "think_ms": self.think_ms,
            "speak_ms": self.speak_ms,
            "latency_ms": self.stt_ms + self.think_ms,
            "interrupted": self.interrupted,
            "backend": self.backend,
            "confidence": round(self.confidence, 3) if self.confidence is not None else None,
            "error": self.error,
            "meta": self.meta,
        }


SendFn = Callable[[dict[str, Any]], Awaitable[None]]
SendAudioFn = Callable[[bytes], Awaitable[bool]]
ReplyFn = Callable[[str, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]


class VoiceConversation:
    def __init__(
        self,
        *,
        settings: Any,
        vad: BaseVAD,
        stt: Any,
        tts: Any,
        reply: ReplyFn,
        send: SendFn,
        send_audio: SendAudioFn | None = None,
        session_id: str = "local",
        gender: str = "female",
    ) -> None:
        self.settings = settings
        self.vad = vad
        self.stt = stt
        self.tts = tts
        self.reply_fn = reply
        self.send = send
        self.send_audio = send_audio
        self.session_id = session_id
        self.gender = gender
        self.voice_enabled = True

        self.state = State.IDLE
        self.turn_index = 0
        self.buffer: list[np.ndarray] = np.array([], dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._speak_cancel = asyncio.Event()
        self._lock = asyncio.Lock()
        self.turns: list[Turn] = []
        # One in-flight turn. Transcription and answering run *outside* the audio
        # lock: if they did not, a frame arriving while the assistant is speaking
        # could never be seen, and "interrupt it mid-sentence" would be impossible
        # no matter what the settings said.
        self._turn_task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.frames_in = 0
        self.bytes_in = 0
        self.sample_rate = int(getattr(settings, "sample_rate", 16_000))
        self.barge_in = bool(getattr(settings, "barge_in_enabled", True))
        self.barge_in_ms = int(getattr(settings, "barge_in_speech_ms", 220))

    # -- public --------------------------------------------------------- #

    def set_gender(self, gender: str | None) -> None:
        if gender in ("male", "female"):
            self.gender = gender

    def set_voice_enabled(self, enabled: bool) -> None:
        """Voice on/off toggle: off still transcribes and answers, just silently."""
        self.voice_enabled = bool(enabled)

    def start_listening(self) -> None:
        """Open the gate. IDLE means 'the microphone is not ours yet'."""
        self.state = State.LISTENING
        self._buf = np.zeros(0, dtype=np.float32)
        self.vad._reset()

    async def push_audio(self, raw: bytes, *, sample_rate: int | None = None, encoding: str = "s16le") -> None:
        """One microphone frame from the client."""
        sr = int(sample_rate or self.sample_rate)
        if encoding == "wav":
            from .audio import decode_wav

            pcm = decode_wav(raw)
            x = pcm.samples
            sr = pcm.sample_rate
        elif encoding == "f32le":
            x = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        else:
            x = pcm16_from_bytes(raw, sample_rate=sr).samples
        if x.size == 0:
            return
        if self.state is State.IDLE:
            # Hands-free by default: the first frame means the learner has opened
            # their mouth or pressed talk, and the VAD decides the rest. A client
            # that wants push-to-talk sends {"t":"listen"} around each turn instead.
            self.start_listening()
        self.frames_in += 1
        self.bytes_in += len(raw)
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if sr != self.sample_rate:
            x = resample(x, src_rate=sr, dst_rate=self.sample_rate)

        utterance: np.ndarray | None = None
        speech_ms = 0
        async with self._lock:
            decisions = self.vad.accept(x)
            listening = self.state in (State.LISTENING, State.SPEAKING)
            if listening:
                self._buf = np.concatenate([self._buf, x])
            if self.state is State.SPEAKING and self.barge_in:
                await self._maybe_barge_in(decisions)
            elif self.state is State.LISTENING and self._idle():
                for d in decisions:
                    if d.speech_ended and d.speech_ms:
                        speech_ms = int(d.speech_ms)
                        utterance = self._buf
                        self._buf = np.zeros(0, dtype=np.float32)
                        break
        if utterance is not None and utterance.size > 0:
            self._spawn(self._transcribe_and_answer(utterance, speech_ms))

    async def push_text(self, text: str) -> Turn:
        """Browser-dictated or typed input: same turn path, no audio decode."""
        accept = getattr(self.stt, "accept_text", None)
        transcript = accept(text) if callable(accept) else None
        final = (transcript.text if transcript is not None else text) or ""
        turn = Turn(index=self.turn_index, text=final, backend=getattr(self.stt, "name", "text"), audio_s=0.0)
        self.turn_index += 1
        self.turns.append(turn)
        await self._answer(turn)
        return turn

    def start_text(self, text: str) -> None:
        """Typed input as a turn in the background.

        ``push_text`` is awaited by the socket's control loop, and that same loop is the
        one reading microphone frames -- so awaiting it here froze the socket for the
        whole spoken answer, and a learner talking over a *typed* question could not be
        heard until the assistant had finished the sentence. Audio turns have always
        been started on their own task; this gives typed turns the same treatment.
        """
        self._spawn(self.push_text(text))

    async def stop(self) -> None:
        """Stop speaking, and abandon an answer that has not been spoken yet."""
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
        await self._finish_speaking(cancelled=True)

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "session_id": self.session_id,
            "turns": len(self.turns),
            "buffered_s": round(self._buf.size / max(1, self.sample_rate), 2),
            "voice_enabled": self.voice_enabled,
            "barge_in": self.barge_in,
            "gender": self.gender,
            "frames_in": self.frames_in,
            "bytes_in": self.bytes_in,
            "stt": getattr(self.stt, "name", "none"),
            "tts": getattr(self.tts, "name", "none"),
            "vad": getattr(self.vad, "backend", "?"),
            "last_error": self.last_error,
            "last_turn": self.turns[-1].as_dict() if self.turns else None,
        }

    # -- internals ------------------------------------------------------ #

    async def _maybe_barge_in(self, decisions: list[Any]) -> None:
        speech = sum(d.speech_ms for d in decisions if d.in_speech or d.speech_started)
        held = 0
        for d in decisions:
            held = d.speech_ms if d.in_speech else 0
            if held >= self.barge_in_ms:
                break
        if speech <= 0 and held <= 0:
            return
        if max(speech, held) < self.barge_in_ms:
            return
        log.info("barge-in after %dms of speech", max(speech, held))
        if self.tts is not None:
            self._speak_cancel.set()
        await self.send({"t": "state", "state": State.LISTENING.value, "reason": "interrupted"})
        self.state = State.LISTENING
        for turn in reversed(self.turns):
            if turn.state in (State.SPEAKING, State.THINKING):
                turn.interrupted = True
                turn.state = State.LISTENING

    def _idle(self) -> bool:
        return self._turn_task is None or self._turn_task.done()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._turn_task = task
        task.add_done_callback(self._turn_finished)

    def _turn_finished(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("voice turn failed")
        if self._turn_task is task:
            self._turn_task = None

    async def _endpoint(self, speech_ms: int) -> None:
        """Force an endpoint from whatever is buffered (used by tests and by 'stop')."""
        audio = self._buf
        self._buf = np.zeros(0, dtype=np.float32)
        if audio.size == 0:
            return
        await self._transcribe_and_answer(audio, speech_ms)

    async def _transcribe_and_answer(self, audio: np.ndarray, speech_ms: int) -> None:
        """Cut the utterance to just the speech, transcribe, answer, speak."""
        # Trim VAD hangover silence off the tail so Whisper is not fed 600 ms of room tone.
        trimmed = _trim_silence(audio, sample_rate=self.sample_rate)
        dur = trimmed.size / max(1, self.sample_rate)
        turn = Turn(index=self.turn_index, audio_s=dur, state=State.TRANSCRIBING)
        self.turn_index += 1
        self.turns.append(turn)
        await self.send({"t": "state", "state": State.TRANSCRIBING.value, "audio_s": round(dur, 2)})
        self.state = State.TRANSCRIBING
        started = time.monotonic()
        try:
            res = await asyncio.to_thread(self.stt.transcribe, trimmed, sample_rate=self.sample_rate)
        except Exception as exc:  # a broken model must not kill the socket
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            turn.error = self.last_error
            turn.state = State.ERROR
            log.exception("STT failed")
            await self.send({"t": "error", "where": "stt", "detail": self.last_error, "recoverable": True})
            self.state = State.LISTENING
            return
        turn.stt_ms = int((time.monotonic() - started) * 1000)
        turn.text = res.text
        turn.backend = res.backend
        turn.confidence = res.confidence
        turn.meta = {"stt_note": res.note, "segments": res.segments, "model": res.model}
        await self.send({"t": "transcript", "text": res.text, "backend": res.backend, "ms": turn.stt_ms, "note": res.note})
        if not res.text.strip():
            self.state = State.LISTENING
            await self.send({"t": "state", "state": State.LISTENING.value, "reason": "empty_transcript"})
            return
        await self._answer(turn)

    async def _answer(self, turn: Turn) -> None:
        turn.state = State.THINKING
        self.state = State.THINKING
        await self.send({"t": "state", "state": State.THINKING.value})
        started = time.monotonic()
        try:
            reply_text, meta = await self.reply_fn(turn.text, {"turn_index": turn.index, "audio_s": turn.audio_s})
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("reply generation failed")
            turn.error = self.last_error
            await self.send({"t": "error", "where": "ai", "detail": self.last_error, "recoverable": True})
            self.state = State.LISTENING
            return
        turn.think_ms = int((time.monotonic() - started) * 1000)
        turn.reply = reply_text
        turn.meta.update(meta or {})
        await self.send({"t": "reply", "text": reply_text, "ms": turn.think_ms, "meta": meta or {}})
        await self._speak(turn, reply_text)

    async def _speak(self, turn: Turn, text: str) -> None:
        if not self.voice_enabled or self.send_audio is None or getattr(self.tts, "name", "none") == "none":
            self.state = State.LISTENING
            await self.send(
                {
                    "t": "speak_unavailable",
                    "reason": "browser_speech_synthesis" if self.voice_enabled else "voice_off",
                    "text": text,
                }
            )
            return
        self._speak_cancel = asyncio.Event()
        turn.state = State.SPEAKING
        self.state = State.SPEAKING
        self.vad._reset()
        await self.send({"t": "state", "state": State.SPEAKING.value, "gender": self.gender})
        started = time.monotonic()
        chunks = 0
        try:
            # Each chunk of speech is real synthesis work, so it is pulled on a worker
            # thread. Iterating the generator inline (which this used to do) froze the
            # event loop between chunks -- and the barge-in listener lives on that loop,
            # so an interruption could not be heard until the engine happened to yield.
            stream = self.tts.stream(text, gender=self.gender)
            while True:
                chunk = await asyncio.to_thread(_next_or_none, stream)
                if chunk is None or self._speak_cancel.is_set():
                    break
                wav = _wav_for(chunk)
                if not await self.send_audio(wav):
                    break
                chunks += 1
                await self.send({"t": "tts_chunk", **chunk.as_dict()})
        except Exception as exc:
            self.last_error = f"TTS failed: {type(exc).__name__}: {str(exc)[:150]}"
            log.warning(self.last_error + "; client will use browser voice")
            await self.send({"t": "speak_unavailable", "reason": "tts_error", "detail": self.last_error, "text": text})
        turn.speak_ms = int((time.monotonic() - started) * 1000)
        await self._finish_speaking(cancelled=self._speak_cancel.is_set(), chunks=chunks)

    async def _finish_speaking(self, *, cancelled: bool, chunks: int = 0) -> None:
        self.state = State.LISTENING  # back to the gate: the learner may reply at once
        self._buf = np.zeros(0, dtype=np.float32)
        self.vad._reset()
        await self.send({"t": "state", "state": State.LISTENING.value, "stopped": bool(cancelled), "chunks": chunks})


def _trim_silence(x: np.ndarray, *, sample_rate: int, keep_ms: int = 180) -> np.ndarray:
    """Trim leading/trailing low-energy, keeping a short pad either side."""
    if x.size < sample_rate // 4:
        return x
    frame = max(1, int(sample_rate * 0.02))
    n = x.size // frame
    if n < 2:
        return x
    env = np.sqrt((x[: n * frame].reshape(n, frame).astype(np.float64) ** 2).mean(axis=1))
    floor = float(np.percentile(env, 15))
    thresh = max(floor * 2.5, 10 ** (-55 / 20) * 1.0)
    voiced = np.where(env > thresh)[0]
    if voiced.size == 0:
        return x
    pad = max(1, int(keep_ms / 20))
    lo = max(0, int(voiced[0]) - pad)
    hi = min(n, int(voiced[-1]) + pad + 1)
    return x[lo * frame : hi * frame]


def _wav_for(chunk: Chunk) -> bytes:
    from .tts import to_wav_bytes

    return to_wav_bytes(chunk.pcm, chunk.sample_rate)
