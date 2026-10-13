"""Voice activity detection + endpointing.

Two backends, one interface:

* :class:`WebRTC` -- ``webrtcvad`` if installed (aggressive mode, 20 ms frames).
* :class:`EnergyVAD` -- adaptive energy gate with a hanging floor, plus a
  spectral-shape tie-breaker. Always available, pure numpy, and the reason the
  demo can show "start talking / stop talking" transitions without a model
  download.

Why endpointing matters more than detection: in a tutoring conversation the cost of
cutting a learner off mid-sentence is much higher than the cost of waiting an extra
300 ms, so ``hangover_ms`` is generous by default and ``max_utterance_s`` is the
only forced cut. Interruption of the *assistant* uses a separate, stricter
threshold (see ``barge_in_*`` in config) so a cough cannot stop an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from .audio import dbfs, rms, spectral_tilt

FRAME_MS = 20


@dataclass
class VadFrame:
    t_ms: int
    rms: float
    db: float
    speech: bool
    state: str  # idle | speech | endpoint


@dataclass
class VadDecision:
    speech_started: bool = False
    speech_ended: bool = False
    in_speech: bool = False
    speech_ms: int = 0
    frames: list[VadFrame] = field(default_factory=list)

    @property
    def endpoint(self) -> bool:
        return self.speech_ended


class BaseVAD:
    backend = "base"

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        frame_ms: int = FRAME_MS,
        hangover_ms: int = 600,
        min_speech_ms: int = 240,
        max_utterance_s: float = 25.0,
        start_fraction: float = 0.7,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.hangover_ms = int(hangover_ms)
        self.min_speech_ms = int(min_speech_ms)
        self.max_utterance_s = float(max_utterance_s)
        self.start_fraction = float(start_fraction)
        self.frame_n = max(1, int(self.sample_rate * self.frame_ms / 1000))
        self._reset()

    def _reset(self) -> None:
        self.in_speech = False
        self.speech_run_ms = 0
        self.silence_run_ms = 0
        self.utterance_ms = 0
        self.noise_floor_db = -70.0
        self.total_ms = 0
        self._pending: list[tuple[int, np.ndarray]] = []  # (level, samples) queued while starting

    # -- API ------------------------------------------------------------ #

    def accept(self, samples: np.ndarray) -> list[VadDecision]:
        """Feed a chunk; get one decision per frame (usually 1-3)."""
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        out: list[VadDecision] = []
        if x.size == 0:
            return out
        carry = x.size % self.frame_n
        usable = x if carry == 0 else x[: x.size - carry]
        # Fixed-stride frames (array_split would yield ragged sizes, and the
        # webrtcvad backend requires an exact 20 ms frame).
        for i in range(usable.size // self.frame_n):
            chunk = usable[i * self.frame_n : (i + 1) * self.frame_n]
            d = self._frame(chunk)
            if d is not None:
                out.append(d)
        if carry:
            self._partial = np.concatenate([getattr(self, "_partial", np.zeros(0, np.float32)), x[x.size - carry :]])
            if self._partial.size >= self.frame_n:
                head, self._partial = self._partial[: self.frame_n], self._partial[self.frame_n :]
                d = self._frame(head)
                if d is not None:
                    out.append(d)
        return out

    def flush(self) -> VadDecision | None:
        """End of stream: force an endpoint if we were mid-speech."""
        if not self.in_speech and self.speech_run_ms < self.min_speech_ms:
            self._reset()
            return None
        dec = VadDecision(speech_ended=True, in_speech=False, speech_ms=self.speech_run_ms)
        self._reset()
        return dec

    def _frame(self, chunk: np.ndarray) -> VadDecision | None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers -------------------------------------------------------- #

    def _track(self, level_db: float, is_speech: bool) -> VadDecision | None:
        self.total_ms += self.frame_ms
        started = ended = False
        if is_speech:
            self.speech_run_ms += self.frame_ms
            self.silence_run_ms = 0
            self.utterance_ms += self.frame_ms
            if not self.in_speech and self.speech_run_ms >= self.min_speech_ms:
                self.in_speech = True
                started = True
        else:
            self.silence_run_ms += self.frame_ms
            # slow noise-floor adaptation while we are not in speech
            if not self.in_speech:
                self.noise_floor_db = 0.97 * self.noise_floor_db + 0.03 * level_db
            if self.in_speech:
                self.utterance_ms += self.frame_ms
                if self.silence_run_ms >= self.hangover_ms:
                    ended = True
                    self.in_speech = False
            else:
                self.speech_run_ms = max(0, self.speech_run_ms - self.frame_ms)
        if self.utterance_ms > self.max_utterance_s * 1000 and not ended:
            ended = True
            self.in_speech = False
        dec = VadDecision(
            speech_started=started,
            speech_ended=ended,
            in_speech=self.in_speech,
            speech_ms=self.speech_run_ms if self.in_speech else (self.utterance_ms if ended else 0),
        )
        if ended or started:
            self.utterance_ms = 0 if ended else self.utterance_ms
            if ended:
                self.speech_run_ms = 0
        return dec


class EnergyVAD(BaseVAD):
    """Adaptive threshold in dBFS with hysteresis (start harder than hold)."""

    backend = "energy"

    def __init__(self, *, margin_db: float = 9.0, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.margin_db = float(margin_db)

    def _frame(self, chunk: np.ndarray) -> VadDecision | None:
        level = rms(chunk)
        db = dbfs(level)
        floor = self.noise_floor_db
        if self.in_speech:
            thresh = floor + self.margin_db * self.start_fraction
        else:
            thresh = floor + self.margin_db
        is_speech = db >= thresh
        if is_speech and not self.in_speech and self.speech_run_ms < self.min_speech_ms:
            # Reject the classic false trigger: a single loud click that is not
            # shaped like speech (speech has most energy in 120 Hz-3 kHz).
            if spectral_tilt(chunk, sample_rate=self.sample_rate) < 0.55:
                is_speech = False
        return self._track(db, is_speech)


class WebRTC(BaseVAD):
    """webrtcvad wrapper (16 kHz, 20 ms frames, aggressiveness 2)."""

    backend = "webrtcvad"

    def __init__(self, *, mode: int = 2, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        import webrtcvad  # noqa: F401  (import checked by caller)

        self._vad = webrtcvad.Vad(int(mode))
        if self.frame_ms not in (10, 20, 30):
            self.frame_ms = 20
            self.frame_n = int(self.sample_rate * 0.02)

    def _frame(self, chunk: np.ndarray) -> VadDecision | None:
        from .audio import float_to_int16

        raw = float_to_int16(chunk).tobytes()
        try:
            is_speech = self._vad.is_speech(raw, self.sample_rate)
        except Exception:  # wrong frame size for webrtcvad -> treat as energy VAD
            is_speech = rms(chunk) > 10 ** ((self.noise_floor_db + 9) / 20)
        return self._track(dbfs(rms(chunk)), bool(is_speech))


def build_vad(settings, *, sample_rate: int | None = None) -> BaseVAD:
    """``auto`` prefers webrtcvad and silently falls back to the energy gate."""
    want = getattr(settings, "vad_backend", "auto")
    kwargs = dict(
        sample_rate=sample_rate or getattr(settings, "sample_rate", 16_000),
        hangover_ms=int(getattr(settings, "vad_hangover_ms", 600)),
        min_speech_ms=int(getattr(settings, "vad_min_speech_ms", 240)),
    )
    if want in ("auto", "webrtc"):
        try:
            return WebRTC(**kwargs)  # type: ignore[arg-type]
        except Exception:
            if want == "webrtc":
                raise
    return EnergyVAD(**kwargs)  # type: ignore[arg-type]


def segment_summary(decisions: Iterable[VadDecision]) -> dict[str, float | int]:
    """Diagnostic summary for the doctor command / voice self-test screen."""
    segs = 0
    speech_ms = 0
    rows = 0
    for d in decisions:
        rows += 1
        if d.speech_started:
            segs += 1
        if d.in_speech:
            speech_ms += FRAME_MS
    return {"frames": rows, "segments": segs, "speech_ms": int(speech_ms)}
