"""Local speech-to-text (faster-whisper) with a real fallback path.

Verified against ``faster-whisper`` 1.2.1 metadata: it needs ``ctranslate2``,
``tokenizers``, ``onnxruntime`` and ``av``, and downloads model weights from
Hugging Face on first use. That is fine on the presenter's laptop (one-time
download, then fully local and offline) and impossible inside a locked-down
sandbox -- so the availability probe is explicit and the app degrades to the
browser's own speech recognition rather than showing an error.

Model choice for the "free + local + real-time" constraint (measured, 2 CPU cores,
no GPU, see docs/VOICE.md): ``base`` int8 gives usable maths dictation at roughly
real-time x3 on a 6-second utterance. ``small`` is noticeably better on "one half"
vs "a half" and costs ~2.5x. ``ADAPT_WHISPER_MODEL_SIZE`` switches it.

Transcription is post-processed by :func:`normalize_spoken_math` because "three
quarters" must become ``3/4`` before the grader ever sees it -- recognition accuracy
and *understanding* accuracy are separate requirements in the blueprint.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger("adapt.voice")

_FILLERS = re.compile(r"\b(?:uh+|um+|erm+|ah+|like,|you know,)\b", re.IGNORECASE)
_WS = re.compile(r"\s+")

# Spoken-maths normalisation: what the learner says -> what the item means.
SPOKEN_MATH: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:x|multiply)\s*(?:by|times)\s*(-?\d+(?:\.\d+)?)\b", re.I), r"x \1"),
    (re.compile(r"\bminus\s*(-?\d+(?:\.\d+)?)\b", re.I), r"- \1"),
    (re.compile(r"\bplus\s*(-?\d+(?:\.\d+)?)\b", re.I), r"+ \1"),
    (re.compile(r"\b(\d+)\s*over\s*(\d+)\b", re.I), r"\1/\2"),
    (re.compile(r"\b(\d+)\s*squared\b", re.I), r"\1^2"),
    (re.compile(r"\b(\d+)\s*cubed\b", re.I), r"\1^3"),
    (re.compile(r"\b(\w+)\s*squared\b", re.I), r"\1^2"),
    (re.compile(r"\bhalf\b", re.I), "1/2"),
    (re.compile(r"\bquarter\b", re.I), "1/4"),
    (re.compile(r"\bthree quarters\b", re.I), "3/4"),
    (re.compile(r"\bthree eighths?\b", re.I), "3/8"),
    (re.compile(r"\btwo thirds?\b", re.I), "2/3"),
    (re.compile(r"\bpercent\b", re.I), "%"),
    (re.compile(r"\bdivided (?:by|into)\s*(-?\d+)\b", re.I), r"/ \1"),
)

WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "twenty": "20", "fifty": "50", "hundred": "100",
}


@dataclass
class Transcript:
    text: str
    backend: str = "none"
    language: str = "en"
    confidence: float | None = None
    duration_s: float = 0.0
    latency_ms: int = 0
    model: str | None = None
    segments: int = 0
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "backend": self.backend,
            "language": self.language,
            "confidence": round(self.confidence, 3) if self.confidence is not None else None,
            "duration_s": round(self.duration_s, 3),
            "latency_ms": self.latency_ms,
            "model": self.model,
            "segments": self.segments,
            "note": self.note,
        }


def normalize_spoken_math(text: str) -> str:
    """Map spoken arithmetic onto the notation the grader understands."""
    out = _FILLERS.sub(" ", text or "")
    out = _WS.sub(" ", out).strip()
    for pattern, repl in SPOKEN_MATH:
        out = pattern.sub(repl, out)
    for word, digit in WORD_NUMBERS.items():
        out = re.sub(rf"\b{word}\b(?=\s+(?:plus|minus|times|divided|over))", digit, out, flags=re.I)
    return out.strip()


class BaseSTT:
    name = "none"
    needs_download = False

    def transcribe(self, samples: np.ndarray, *, sample_rate: int) -> Transcript:  # pragma: no cover
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, "needs_model_download": self.needs_download}


class WhisperSTT(BaseSTT):
    """faster-whisper wrapper. Loaded once, lazily, guarded by a lock."""

    name = "faster-whisper"
    needs_download = True

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._model: Any = None
        self._lock = threading.Lock()
        self.load_error: str | None = None
        self.load_ms: int | None = None

    @staticmethod
    def probe(settings: Any) -> tuple[bool, str]:
        """``(available, learner-safe sentence)``.

        The sentence is what `GET /voice/capabilities` shows a learner, so it names no package,
        provider or model (brief §3). The install hint goes to the log and `docs/VOICE.md`, which
        is where an operator reads it -- the app never tells a learner to run pip.
        """
        try:
            import faster_whisper  # noqa: F401
        except Exception as exc:
            log.info(
                "voice: faster-whisper not importable (%s); install with: pip install 'adapt-backend[voice]'",
                exc.__class__.__name__,
            )
            return False, "local transcription is not installed in this environment"
        try:
            from pathlib import Path

            import os

            # HF caches weights under ~/.cache/huggingface; if absent the first
            # call downloads ~74 MB (base) -- fine locally, fatal offline.
            cache = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
            if not Path(cache).exists():
                return True, f"importable, but no model cache at {cache}: first use will download"
            return True, "importable and model cache present"
        except Exception as exc:  # pragma: no cover
            return True, f"importable (cache check failed: {exc})"

    def load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel  # imported lazily on purpose

            size = getattr(self.settings, "whisper_model_size", "base")
            compute = getattr(self.settings, "whisper_compute_type", "int8")
            device = getattr(self.settings, "whisper_device", "auto")
            if device == "auto":
                try:
                    import ctranslate2  # type: ignore

                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            started = time.monotonic()
            log.info("loading whisper model size=%s compute=%s device=%s", size, compute, device)
            self._model = WhisperModel(size, device=device, compute_type=compute)
            self.load_ms = int((time.monotonic() - started) * 1000)
            log.info("whisper ready in %dms", self.load_ms)
            return self._model

    def transcribe(self, samples: np.ndarray, *, sample_rate: int) -> Transcript:
        from .audio import resample

        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if sample_rate != 16_000:
            x = resample(x, src_rate=sample_rate, dst_rate=16_000)
        dur = x.size / 16_000.0
        if dur < 0.25:
            return Transcript("", backend=self.name, duration_s=dur, note="too short to transcribe")
        started = time.monotonic()
        try:
            model = self.load()
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"[:200]
            return Transcript(
                "",
                backend="unavailable",
                duration_s=dur,
                note=f"local model unavailable ({self.load_error}); type or use browser dictation instead",
            )
        # condition_on_previous_text=False: on short tutoring utterances the
        # history-conditioned decode loops ("and and and"), which is worse than the
        # small accuracy gain.
        segments, info = model.transcribe(
            x,
            language=getattr(self.settings, "voice_language", None) or "en",
            beam_size=1,
            vad_filter=False,  # we already endpoint with our own VAD
            condition_on_previous_text=False,
            word_timestamps=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            logprob_threshold=-0.6,
        )
        texts: list[str] = []
        probs: list[float] = []
        count = 0
        for seg in segments:
            texts.append((seg.text or "").strip())
            if getattr(seg, "no_speech_prob", None) is not None:
                probs.append(1.0 - float(seg.no_speech_prob))
            count += 1
        raw = " ".join(t for t in texts if t).strip()
        conf = float(np.mean(probs)) if probs else None
        return Transcript(
            text=normalize_spoken_math(raw),
            backend=self.name,
            language=getattr(info, "language", "en") or "en",
            confidence=conf,
            duration_s=dur,
            latency_ms=int((time.monotonic() - started) * 1000),
            model=f"faster-whisper/{getattr(self.settings, 'whisper_model_size', 'base')}",
            segments=count,
            note=None if raw else "no speech recognised in that audio",
        )

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "needs_model_download": True,
            "model_size": getattr(self.settings, "whisper_model_size", "base"),
            "compute_type": getattr(self.settings, "whisper_compute_type", "int8"),
            "loaded": self._model is not None,
            "load_ms": self.load_ms,
            "error": self.load_error,
        }


class BrowserTranscriptSTT(BaseSTT):
    """Pass-through for text the browser already transcribed (Web Speech API).

    Not a competitor to the local model: it exists so the demo can run in a
    browser on a machine where the 74 MB download never happened. The backend name
    is recorded on the turn, so nothing pretends to be local inference.
    """

    name = "browser"
    needs_download = False

    def transcribe(self, samples: np.ndarray, *, sample_rate: int) -> Transcript:  # pragma: no cover
        return Transcript("", backend=self.name, note="browser STT supplies text, not audio")

    def accept_text(self, text: str, *, duration_s: float = 0.0) -> Transcript:
        return Transcript(
            text=normalize_spoken_math(text or ""),
            backend=self.name,
            duration_s=duration_s,
            note="transcribed in the browser (Web Speech API); not verified locally",
        )


def get_stt(settings: Any) -> BaseSTT:
    ok, _why = WhisperSTT.probe(settings)
    return WhisperSTT(settings) if ok else BrowserTranscriptSTT()


def capabilities(settings: Any) -> dict[str, Any]:
    ok, why = WhisperSTT.probe(settings)
    return {
        "local_stt": ok,
        "detail": why,
        "fallback": "browser Web Speech API dictation (free, on your device in Chrome, Edge or Safari)",
        "model_size": getattr(settings, "whisper_model_size", "base"),
        "compute_type": getattr(settings, "whisper_compute_type", "int8"),
        "spoken_math_normalisation": True,
    }
