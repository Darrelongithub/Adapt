"""Local text-to-speech with Kokoro-82M, plus a browser fallback.

Verified against the ``kokoro`` 0.9.4 package metadata and README (checked from
PyPI, not assumed):

* ``KPipeline(lang_code='a')`` -- 'a' American English, 'b' British, and other
  single-letter codes per language; ``lang_code`` must match the voice prefix.
* voice ids are ``<lang><gender>_<name>``, e.g. ``af_heart`` (American female) and
  ``am_fenrir`` (American male) -- so the blueprint's male/female selection maps to
  the second character, ``f``/``m``.
* output is 24 kHz float audio; ``pipeline(text, voice=...)`` yields
  ``(graphemes, phonemes, audio)`` chunks, one per sentence-sized split, which is
  exactly what we want for streaming so speech starts before the whole answer is
  synthesised.
* requires ``torch`` + ``transformers`` and downloads the 82M weights on first use
  (needs python >=3.10,<3.13 -- note the upper bound, it will break on 3.13).

If any of that is unavailable, :func:`synthesize_or_none` returns ``None`` and the
frontend switches to ``speechSynthesis`` (also free, also local to the device, with
its own male/female voice choice). The learner hears a voice either way; the turn
record says which engine produced it.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

log = logging.getLogger("adapt.voice")

OUTPUT_RATE = 24_000
VOICES: dict[str, dict[str, str]] = {
    "female": {"a": "af_heart", "b": "bf_violet"},
    "male": {"a": "am_fenrir", "b": "bm_ola"},
}
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
MAX_CHUNK_CHARS = 260


@dataclass
class Chunk:
    index: int
    text: str
    pcm: bytes  # s16le mono
    sample_rate: int = OUTPUT_RATE
    first: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "sample_rate": self.sample_rate,
            "bytes": len(self.pcm),
            "ms": int(1000 * (len(self.pcm) / 2) / self.sample_rate),
        }


def split_for_speech(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Sentence-first chunking; long sentences are cut on commas/word boundaries.

    Chunking matters for the interruption requirement: the learner can interrupt
    between chunks, so a 900-word answer must not be one unbreakable blob.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    parts = [p.strip() for p in SENT_SPLIT.split(raw) if p.strip()]
    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
            continue
        buf = part
        while len(buf) > max_chars:
            cut = buf.rfind(",", 0, max_chars)
            if cut < max_chars // 2:
                cut = buf.rfind(" ", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars
            head, buf = buf[:cut].strip(), buf[cut:].strip(" ,")
            if head:
                out.append(head)
        if buf:
            out.append(buf)
    return out


def to_wav_bytes(pcm_s16le: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm_s16le)
    return buf.getvalue()


def _to_s16(audio: Any) -> bytes:
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return b""
    peak = float(np.max(np.abs(arr)))
    if peak > 1.5:  # some backends hand back int16-shaped floats
        arr = arr / 32768.0
    elif peak > 1.0:
        arr = arr / peak
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


class KokoroTTS:
    name = "kokoro"

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._pipeline: Any = None
        self._lock = threading.Lock()
        self.load_error: str | None = None
        self.load_ms: int | None = None
        self.last_ms: int | None = None

    @staticmethod
    def probe(settings: Any) -> tuple[bool, str]:
        try:
            import kokoro  # noqa: F401
        except Exception as exc:
            return False, (
                "kokoro not importable "
                f"({exc.__class__.__name__}); install with: pip install 'adapt-backend[voice]' "
                "(pulls torch ~2 GB and downloads the 82M weights once)"
            )
        return True, "kokoro importable; weights fetched on first synthesis unless cached"

    @staticmethod
    def voice_for(gender: str | None, lang: str) -> str:
        g = (gender or "female").lower()
        if g not in VOICES:
            g = "female"
        return VOICES[g].get(lang, VOICES[g]["a"])

    def load(self) -> Any:
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            from kokoro import KPipeline  # lazy: importing pulls torch

            lang = getattr(self.settings, "kokoro_lang", "a") or "a"
            started = time.monotonic()
            log.info("loading kokoro pipeline lang=%s", lang)
            self._pipeline = KPipeline(lang_code=lang)
            self.load_ms = int((time.monotonic() - started) * 1000)
            return self._pipeline

    def stream(self, text: str, *, gender: str = "female") -> Iterator[Chunk]:
        lang = getattr(self.settings, "kokoro_lang", "a") or "a"
        voice = self.voice_for(gender, lang)
        pipeline = self.load()
        started = time.monotonic()
        rate = int(getattr(self.settings, "tts_sample_rate", OUTPUT_RATE) or OUTPUT_RATE)
        for i, part in enumerate(split_for_speech(text)):
            payload = None
            for _gs, _ps, audio in pipeline(part, voice=voice):
                payload = audio
                break
            if payload is None:
                continue
            yield Chunk(index=i, text=part, pcm=_to_s16(np.asarray(payload, dtype=np.float32)), sample_rate=rate, first=i == 0)
        self.last_ms = int((time.monotonic() - started) * 1000)

    def synthesize(self, text: str, *, gender: str = "female") -> tuple[bytes, int] | None:
        """Whole-answer WAV (used by the REST endpoint; WS uses stream())."""
        chunks = list(self.stream(text, gender=gender))
        if not chunks:
            return None
        pcm = b"".join(c.pcm for c in chunks)
        return to_wav_bytes(pcm, chunks[0].sample_rate), chunks[0].sample_rate

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "loaded": self._pipeline is not None,
            "load_ms": self.load_ms,
            "last_ms": self.last_ms,
            "voices": {k: v for k, v in VOICES.items()},
            "sample_rate": int(getattr(self.settings, "tts_sample_rate", OUTPUT_RATE)),
            "error": self.load_error,
        }


class NullTTS:
    name = "none"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def stream(self, text: str, *, gender: str = "female") -> Iterator[Chunk]:
        return iter(())

    def info(self) -> dict[str, Any]:
        return {"backend": "none", "detail": self.reason}


def get_tts(settings: Any) -> KokoroTTS | NullTTS:
    want = getattr(settings, "tts_engine", "auto")
    if want == "off":
        return NullTTS("TTS disabled by ADAPT_TTS_ENGINE=off")
    ok, why = KokoroTTS.probe(settings)
    if not ok:
        log.info("TTS falling back to browser speechSynthesis: %s", why)
        return NullTTS(why)
    return KokoroTTS(settings)


def synthesize_or_none(settings: Any, text: str, *, gender: str = "female") -> tuple[bytes, int] | None:
    """Best-effort audio for one reply. ``None`` means 'use the browser voice'."""
    engine = get_tts(settings)
    if isinstance(engine, NullTTS):
        return None
    try:
        return engine.synthesize(text, gender=gender)
    except Exception as exc:
        log.warning("TTS synthesis failed (%s); falling back to browser voice", exc.__class__.__name__)
        return None


def capabilities(settings: Any) -> dict[str, Any]:
    ok, why = KokoroTTS.probe(settings)
    return {
        "local_tts": ok,
        "detail": why,
        "fallback": "browser speechSynthesis with male/female voice selection (free, on-device)",
        "voices": {
            "female": KokoroTTS.voice_for("female", getattr(settings, "kokoro_lang", "a")),
            "male": KokoroTTS.voice_for("male", getattr(settings, "kokoro_lang", "a")),
        },
        "sample_rate": int(getattr(settings, "tts_sample_rate", OUTPUT_RATE)),
        "interruption": "chunked synthesis so speech can be stopped between sentences",
    }
