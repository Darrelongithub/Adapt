"""The voice path, tested where the missing models are not in the way.

``faster-whisper`` and ``kokoro`` are optional extras and are not installed in this
environment, so no test here claims to transcribe or synthesise speech. What can be
tested for real is everything around the models, and that is where a voice feature
actually breaks:

* the VAD decides when the learner stopped talking -- pure numpy, fully exercised here
  on synthetic audio with the energy backend that ships as the fallback;
* the conversation state machine decides what happens when the learner talks *over*
  the assistant -- barge-in, and stopping audio that is now obsolete;
* a socket that cannot transcribe has to say so, not sit silent;
* the interview is one turn of state per frame, so a reconnect must land on a fresh
  conversation without leaking the previous one's transcript.

What is not tested here, and cannot be in this sandbox: a real microphone, real
browser permission prompts (the frontend asks ``getUserMedia``; the harness has no
device), a real transcript, or real synthesised audio.
"""

from __future__ import annotations

import asyncio
import json
import math
import time

import numpy as np

from adapt.voice.pipeline import State, VoiceConversation
from adapt.voice.tts import Chunk
from adapt.voice.vad import EnergyVAD, build_vad

SR = 16_000


def _tone(ms: int, *, freq: float = 440.0, amp: float = 0.25) -> np.ndarray:
    """Speech-shaped energy: a tone in the 120 Hz-3 kHz band the VAD expects."""
    n = int(SR * ms / 1000)
    t = np.arange(n, dtype=np.float32) / SR
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def _silence(ms: int) -> np.ndarray:
    return np.zeros(int(SR * ms / 1000), dtype=np.float32)


def _pcm(x: np.ndarray) -> bytes:
    """Microphone frames arrive as s16le bytes, which is what the socket sends."""
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class _Settings:
    sample_rate = SR
    barge_in_enabled = True
    barge_in_speech_ms = 220
    vad_backend = "energy"


class _Transcription:
    text = "what is a fraction"
    confidence = 0.9
    backend = "stub-stt"
    note = "synthetic"
    segments = 1
    model = "stub"


class _Stt:
    """Stands in for faster-whisper: the pipeline's contract, not the model."""

    name = "stub-stt"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def transcribe(self, audio, sample_rate=SR):  # noqa: ANN001 - duck-typed on purpose
        self.calls += 1
        if self.fail:
            raise RuntimeError("model file missing")
        return _Transcription()


class _Tts:
    """Stands in for kokoro: ``stream`` is pulled on a worker thread, chunk by chunk."""

    name = "stub-tts"

    def __init__(self, *, chunks: int = 6, delay: float = 0.25) -> None:
        self.chunks = chunks
        self.delay = delay
        self.spoken: list[str] = []
        self.yielded = 0

    def stream(self, text: str, *, gender: str | None = None):
        self.spoken.append(text)

        def gen():
            for i in range(self.chunks):
                time.sleep(self.delay)
                self.yielded += 1
                yield Chunk(index=i, text=text[:20], pcm=b"\x00\x00" * 240, first=(i == 0))

        return gen()


def _conversation(*, stt=None, tts=None, vad=None, sent: list | None = None) -> VoiceConversation:
    out = sent if sent is not None else []

    async def reply(text: str, meta: dict) -> tuple[str, dict]:
        return "A fraction is a part of a whole.", {"provider": "local", "asked": text[:20]}

    async def send(frame: dict) -> None:
        out.append(frame)

    async def send_audio(pcm: bytes) -> bool:
        out.append({"t": "audio", "bytes": len(pcm)})
        return True

    return VoiceConversation(
        settings=_Settings(),
        vad=vad or EnergyVAD(sample_rate=SR),
        stt=stt or _Stt(),
        tts=tts or _Tts(),
        reply=reply,
        send=send,
        send_audio=send_audio,
    )


# --------------------------------------------------------------------------- #
# the VAD, on synthetic audio
# --------------------------------------------------------------------------- #


def test_the_energy_vad_calls_silence_silence_and_a_tone_speech():
    vad = EnergyVAD(sample_rate=SR)
    silence = vad.accept(_silence(400))
    assert silence, "the VAD should still report per-frame decisions"
    assert not any(d.in_speech for d in silence), "silence was called speech"

    spoken = vad.accept(_tone(400))
    assert any(d.in_speech for d in spoken), "a tone in the speech band was not detected as speech"


def test_the_vad_falls_back_to_the_energy_backend_when_webrtcvad_is_absent():
    """``auto`` must not need the optional dependency to work at all."""
    settings = _Settings()
    settings.vad_backend = "auto"
    vad = build_vad(settings, sample_rate=SR)
    assert isinstance(vad, (EnergyVAD,)) or vad.backend in {"energy", "webrtcvad"}, vad.backend
    decisions = vad.accept(_tone(300))
    assert decisions, "the fallback VAD produced no decisions"


def test_an_utterance_ends_when_the_learner_stops_talking():
    """Endpointing, the one VAD decision the whole turn lifecycle hangs on."""
    vad = EnergyVAD(sample_rate=SR)
    vad.accept(_silence(300))  # establish a noise floor
    ended = None
    for _ in range(12):  # 1.2 s of speech in 100 ms frames
        for d in vad.accept(_tone(100)):
            if d.speech_ended:
                ended = d
    assert ended is None, "the utterance ended while the learner was still talking"
    for _ in range(15):  # then quiet: the hangover has to expire
        for d in vad.accept(_silence(100)):
            if d.speech_ended:
                ended = d
    assert ended is not None, "the utterance never ended after speech stopped"
    assert ended.speech_ms >= 500, f"a truncated utterance length ({ended.speech_ms}ms) would clip the turn"


# --------------------------------------------------------------------------- #
# barge-in and obsolete audio
# --------------------------------------------------------------------------- #


def test_talking_over_the_assistant_stops_the_audio_and_says_so():
    async def run() -> tuple[list[dict], _Tts, int]:
        tts = _Tts(chunks=8, delay=0.3)
        sent: list[dict] = []
        convo = _conversation(tts=tts, sent=sent)
        convo.start_listening()
        asyncio.ensure_future(convo.push_text("why do denominators have to match"))
        for _ in range(30):  # wait for playback to start (the first chunk takes 0.3s)
            await asyncio.sleep(0.02)
            if convo.state is State.SPEAKING:
                break
        assert convo.state is State.SPEAKING, f"never started speaking: {[f.get('t') for f in sent]}"
        before = tts.yielded
        # ...the learner talks over it: 300 ms of speech in 100 ms frames
        for _ in range(3):
            await convo.push_audio(_pcm(_tone(100)))
        await asyncio.sleep(0.1)
        interrupted = [f for f in sent if f.get("reason") == "interrupted"]
        assert interrupted, f"no interruption frame was sent: {sent[-4:]}"
        assert interrupted[0]["state"] == "listening"
        assert convo.state is State.LISTENING
        await asyncio.sleep(0.4)  # long enough for a chunk that was queued anyway
        return sent, tts, before

    sent, tts, before = asyncio.run(run())
    stopped = [f for f in sent if f.get("stopped") is True]
    assert stopped, "nothing reported that the obsolete speech was stopped"
    assert tts.yielded <= before + 1, f"audio kept being synthesised after the interruption: {tts.yielded} chunks"


def test_a_broken_transcription_model_is_an_error_frame_not_a_dead_socket():
    async def run() -> list[dict]:
        sent: list[dict] = []
        convo = _conversation(stt=_Stt(fail=True), sent=sent)
        convo.start_listening()
        for _ in range(6):  # speech: the VAD needs ~240 ms before it opens a turn
            await convo.push_audio(_pcm(_tone(100)))
        for _ in range(10):  # then quiet, so the hangover expires and the turn endpoints
            await convo.push_audio(_pcm(_silence(100)))
        for _ in range(60):
            await asyncio.sleep(0.02)
            if any(f.get("t") == "error" for f in sent):
                break
        assert convo.state is State.LISTENING, "the socket must go back to listening after a failure"
        return sent

    sent = asyncio.run(run())
    errors = [f for f in sent if f.get("t") == "error"]
    assert errors and errors[0]["where"] == "stt" and errors[0]["recoverable"] is True, sent[-3:]
    assert not [f for f in sent if f.get("t") == "transcript"], "a transcript appeared from a failed model"


def test_a_couple_of_coughs_does_not_cut_the_assistant_off():
    """Barge-in needs sustained speech, or every noise restarts the sentence."""

    async def run() -> list[dict]:
        sent: list[dict] = []
        convo = _conversation(tts=_Tts(chunks=8, delay=0.3), sent=sent)
        convo.start_listening()
        asyncio.ensure_future(convo.push_text("explain denominators"))
        for _ in range(30):
            await asyncio.sleep(0.02)
            if convo.state is State.SPEAKING:
                break
        assert convo.state is State.SPEAKING, f"never started speaking: {[f.get('t') for f in sent]}"
        for _ in range(3):
            await convo.push_audio(_pcm(_silence(20)))
        await convo.push_audio(_pcm(_tone(100, amp=0.0005)))  # a quiet click
        await asyncio.sleep(0.1)
        return sent

    sent = asyncio.run(run())
    assert not [f for f in sent if f.get("reason") == "interrupted"], "a quiet click interrupted the assistant"


def test_stop_cancels_speech_that_is_already_obsolete():
    """The learner pressed stop: no further audio may arrive for that sentence."""

    async def run() -> tuple[VoiceConversation, _Tts, list[dict]]:
        tts = _Tts(chunks=10, delay=0.3)
        sent: list[dict] = []
        convo = _conversation(tts=tts, sent=sent)
        convo.start_listening()
        asyncio.ensure_future(convo.push_text("explain equivalent fractions"))
        for _ in range(30):
            await asyncio.sleep(0.02)
            if convo.state is State.SPEAKING:
                break
        assert convo.state is State.SPEAKING, f"never started speaking: {[f.get('t') for f in sent]}"
        await convo.stop()
        await asyncio.sleep(0.5)
        return convo, tts, sent

    convo, tts, sent = asyncio.run(run())
    finished = [f for f in sent if f.get("stopped") is True]
    assert finished, f"stop was not reported: {sent[-3:]}"
    assert len([f for f in sent if f.get("t") == "tts_chunk"]) < tts.chunks, "audio kept arriving after stop"
    assert convo.state in (State.IDLE, State.LISTENING), convo.state


def test_voice_off_still_answers_with_text():
    async def run() -> list[dict]:
        sent: list[dict] = []
        tts = _Tts()
        convo = _conversation(tts=tts, sent=sent)
        convo.set_voice_enabled(False)
        convo.start_listening()
        turn = await convo.push_text("what is a numerator")
        await asyncio.sleep(0.1)
        assert turn.text
        assert tts.spoken == [], "voice off must not synthesise"
        assert convo.state is State.IDLE
        return sent

    sent = asyncio.run(run())
    assert any(f.get("t") == "reply" for f in sent), "the answer itself must still be delivered"


def test_voice_off_drops_audio_before_stt_or_recording():
    async def run() -> tuple[VoiceConversation, _Stt, list[dict]]:
        sent: list[dict] = []
        stt = _Stt()
        convo = _conversation(stt=stt, sent=sent)
        convo.set_voice_enabled(False)
        convo.start_listening()  # a stale push-to-talk command must not reopen input
        await convo.push_audio(_pcm(_tone(500)))
        await convo.push_audio(_pcm(_silence(700)))
        await asyncio.sleep(0.05)
        return convo, stt, sent

    convo, stt, sent = asyncio.run(run())
    assert stt.calls == 0, "voice-off audio must never reach speech-to-text"
    assert convo.bytes_in == 0 and not convo.turns, "voice-off audio must not be counted or recorded"
    assert not [frame for frame in sent if frame.get("t") == "transcript"]
    assert convo.state is State.IDLE


def test_turning_voice_off_cancels_pending_speech_and_reports_idle():
    async def run() -> tuple[VoiceConversation, _Tts, list[dict], int]:
        sent: list[dict] = []
        tts = _Tts(chunks=10, delay=0.3)
        convo = _conversation(tts=tts, sent=sent)
        convo.start_text("explain equivalent fractions")
        for _ in range(30):
            await asyncio.sleep(0.02)
            if convo.state is State.SPEAKING:
                break
        assert convo.state is State.SPEAKING
        before = tts.yielded
        convo.set_voice_enabled(False)
        await asyncio.sleep(0.5)
        return convo, tts, sent, before

    convo, tts, sent, before = asyncio.run(run())
    assert convo.state is State.IDLE
    assert tts.yielded <= before + 1, "voice-off allowed obsolete speech to keep streaming"
    stopped = [frame for frame in sent if frame.get("t") == "state" and frame.get("stopped") is True]
    assert stopped and stopped[-1]["state"] == State.IDLE.value


def test_a_typed_turn_does_not_hold_the_receive_loop():
    """``start_text`` is what lets a learner interrupt a *typed* question.

    The socket awaits almost everything it is sent, and it is also the loop that reads
    microphone frames. Awaiting a typed turn there meant the whole spoken answer had to
    finish before another frame could be read -- so barge-in was unreachable for typed
    questions, whatever the settings said. ``start_text`` returns at once and the turn
    runs on its own task; this asserts that ordering, which the socket depends on.
    """

    async def run() -> tuple[VoiceConversation, list[dict]]:
        sent: list[dict] = []
        convo = _conversation(tts=_Tts(chunks=6, delay=0.3), sent=sent)
        convo.start_listening()
        convo.start_text("explain denominators")
        # Returns immediately: no speech has been synthesised yet.
        assert convo.turns == [] or convo.state in (State.THINKING, State.SPEAKING, State.LISTENING)
        assert not [f for f in sent if f.get("t") == "audio"], "start_text blocked until the audio was ready"
        await asyncio.sleep(0.1)
        assert convo.turns, "the turn never started on its own task"
        return convo, sent

    convo, sent = asyncio.run(run())
    assert any(f.get("t") == "reply" for f in sent), "the background turn never produced an answer"


# --------------------------------------------------------------------------- #
# the socket, without a transcription engine
# --------------------------------------------------------------------------- #


def test_the_socket_answers_audio_frames_honestly_when_it_cannot_transcribe(client, learner):
    """No local STT is an answer, not a hang -- and never an invented transcript.

    Every read here is driven by a frame the server is guaranteed to send (``hello``,
    the answer to a control frame), because a websocket read with no timeout is how a
    test suite hangs instead of failing.
    """
    caps = client.get("/api/v1/voice/capabilities", headers=learner["headers"]).json()
    assert caps["stt"]["local_stt"] in (True, False)

    # Voice is enabled here because this test exercises the audio/STT fallback. The
    # privacy behavior for voice_on=false is covered immediately below.
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=true") as ws:
        first = json.loads(ws.receive_text())
        assert first["t"] == "hello"
        assert first["status"]["stt"], "the socket has to name the engine it is using"

        # ~600 ms of speech-shaped tone, then quiet: the VAD endpoints the turn.
        for _ in range(6):
            ws.send_bytes(_pcm(_tone(100)))
        for _ in range(10):
            ws.send_bytes(_pcm(_silence(100)))
        frames: list[dict] = []
        ws.send_text(json.dumps({"t": "status"}))
        for _ in range(40):
            frame = json.loads(ws.receive_text())
            frames.append(frame)
            if frame["t"] == "status":
                break
        kinds = [f["t"] for f in frames]
        assert "status" in kinds, f"the control frame was never answered: {kinds}"

        blob = json.dumps(frames)
        assert "Traceback" not in blob, "an internal error reached the client"
        transcripts = [f for f in frames if f["t"] == "transcript"]
        if not caps["stt"]["local_stt"]:
            assert transcripts, f"the audio frames produced no transcript frame: {kinds}"
            assert transcripts[0]["text"] == "", (
                "there is no local model, so the only honest transcript is empty: "
                f"{transcripts[0]['text']!r}"
            )
            assert transcripts[0]["backend"] == "browser", transcripts[0]
            assert "browser" in (transcripts[0].get("note") or "").lower(), (
                "the client has to be told the text is meant to come from the browser"
            )
        assert not [f for f in frames if f.get("t") == "error" and f.get("where") == "audio"], kinds


def test_the_socket_does_not_process_audio_when_voice_is_off(client, learner):
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        assert hello["status"]["voice_enabled"] is False
        for _ in range(6):
            ws.send_bytes(_pcm(_tone(100)))
        for _ in range(6):
            ws.send_bytes(_pcm(_silence(100)))
        ws.send_text(json.dumps({"t": "status"}))
        frames: list[dict] = []
        for _ in range(20):
            frame = json.loads(ws.receive_text())
            frames.append(frame)
            if frame["t"] == "status":
                break
        report = next(frame for frame in frames if frame["t"] == "status")
        assert report["voice_enabled"] is False
        assert report["turns"] == 0
        assert not [frame for frame in frames if frame["t"] == "transcript"]


def test_rest_voice_off_does_not_decode_or_synthesise(client, learner, monkeypatch):
    from adapt.api.routes import system as system_routes

    calls = {"stt": 0, "tts": 0}

    def should_not_stt(*_args, **_kwargs):
        calls["stt"] += 1
        raise AssertionError("voice-off upload reached STT")

    def should_not_tts(*_args, **_kwargs):
        calls["tts"] += 1
        raise AssertionError("voice-off request reached TTS")

    monkeypatch.setattr(system_routes.STT, "get_stt", should_not_stt)
    import adapt.voice.tts as tts_mod

    monkeypatch.setattr(tts_mod, "synthesize_or_none", should_not_tts)
    transcribe = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("clip.wav", b"not decoded", "audio/wav")},
        data={"sample_rate": "16000"},
        headers=learner["headers"],
    )
    assert transcribe.status_code == 200
    assert transcribe.json()["backend"] == "disabled"
    speak = client.post(
        "/api/v1/voice/speak",
        json={"text": "do not say this", "gender": "female"},
        headers=learner["headers"],
    )
    assert speak.status_code == 200
    assert speak.json()["reason"] == "voice_off"
    assert calls == {"stt": 0, "tts": 0}


def test_a_reconnect_starts_a_clean_conversation(client, learner):
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as first:
        hello = json.loads(first.receive_text())
        assert hello["status"]["turns"] == 0
        first.send_text(json.dumps({"t": "text", "text": "first conversation"}))
        for _ in range(12):
            frame = json.loads(first.receive_text())
            if frame["t"] == "reply":
                break
        first.send_text(json.dumps({"t": "status"}))
        for _ in range(8):
            frame = json.loads(first.receive_text())
            if frame["t"] == "status":
                assert frame["turns"] == 1, f"the turn was not counted: {frame}"
                break

    # A second socket is a second conversation: nothing from the first may carry over.
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as second:
        hello = json.loads(second.receive_text())
        assert hello["t"] == "hello"
        assert hello["status"]["turns"] == 0, "the previous conversation's turns leaked into the new socket"
        second.send_text(json.dumps({"t": "status"}))
        for _ in range(8):
            frame = json.loads(second.receive_text())
            if frame["t"] == "status":
                assert frame["turns"] == 0 and not frame["last_turn"], frame
                break


def test_voice_speak_returns_wav_bytes_when_tts_synthesizes(client, learner, monkeypatch):
    enabled = client.post("/api/v1/me/voice", json={"enabled": True}, headers=learner["headers"])
    assert enabled.status_code == 200, enabled.text
    from adapt.voice import tts as tts_mod
    from adapt.voice.audio import encode_wav

    sample_wav = encode_wav(_tone(100), sample_rate=24_000)
    monkeypatch.setattr(tts_mod, "synthesize_or_none", lambda settings, text, gender="female": (sample_wav, 24_000))
    res = client.post("/api/v1/voice/speak", json={"text": "hello learner", "gender": "female"}, headers=learner["headers"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")
    assert res.headers["x-tts-sample-rate"] == "24000"
    assert res.content.startswith(b"RIFF") and len(res.content) == len(sample_wav)


def test_cli_voice_selftest_completes_and_reports_speech_segments():
    from typer.testing import CliRunner

    from adapt.cli import app

    res = CliRunner().invoke(app, ["voice", "selftest"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["frames_in"] == 132
    assert data["speech_ms"] > 0 and data["segments"] >= 1


