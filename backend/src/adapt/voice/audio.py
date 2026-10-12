"""PCM/WAV helpers for the realtime voice path.

The browser captures 48 kHz float32 (MediaRecorder/AudioWorklet default) and we
need 16 kHz int16 mono for Whisper-class models. Rather than pull in a DSP
library, this module does the two operations that matter -- decimation with a
simple anti-alias low-pass, and frame slicing -- in numpy. Quality is adequate
for STT (verified against faster-whisper's own preprocessing expectations: 16 kHz
mono float in [-1, 1]).
"""

from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass

import numpy as np

TARGET_RATE = 16_000


def bytes_to_int16(buf: bytes) -> np.ndarray:
    return np.frombuffer(buf, dtype="<i2").astype(np.int16)


def int16_to_float(x: np.ndarray) -> np.ndarray:
    return x.astype(np.float32) / np.float32(32768.0)


def float_to_int16(x: np.ndarray) -> np.ndarray:
    clipped = np.clip(x, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2")


def resample(x: np.ndarray, *, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear resample with a pre-sinc for downsampling.

    Decimating without filtering folds high frequencies into the speech band and
    measurably hurts recognition, so when ``dst < src`` we low-pass first at
    0.45 * dst Nyquist.
    """
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size == 0 or src_rate == dst_rate:
        return x
    if dst_rate < src_rate:
        x = lowpass(x, fir_lowpass(0.45 * dst_rate, sample_rate=src_rate))
    n_out = max(1, int(round(x.size * dst_rate / src_rate)))
    xi = np.linspace(0.0, x.size - 1, n_out)
    return np.interp(xi, np.arange(x.size), x).astype(np.float32)


def fir_lowpass(cutoff_hz: float, *, sample_rate: int, taps: int = 65) -> np.ndarray:
    """Windowed-sinc low-pass kernel (Hamming).

    A Butterworth/IIR would need scipy. A windowed FIR is a few lines, has an exact
    -6 dB point at ``cutoff_hz``, and is more than enough ahead of an 8:1 decimate
    for speech -- without adding a DSP dependency to the demo laptop.
    """
    taps = taps | 1  # odd for a symmetric kernel and integer group delay
    cutoff = float(np.clip(cutoff_hz / (sample_rate / 2.0), 1e-4, 0.999))
    n = np.arange(taps, dtype=np.float64)
    mid = (taps - 1) / 2.0
    sinc = np.sinc(cutoff * (n - mid)) * cutoff
    kernel = sinc * np.hamming(taps)
    kernel /= float(np.sum(kernel))
    return kernel.astype(np.float32)


def lowpass(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x.astype(np.float32)
    if x.size < kernel.size:
        kernel = kernel / float(np.sum(kernel))
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def dbfs(value: float, *, floor: float = -80.0) -> float:
    return floor if value <= 1e-9 else max(floor, 20.0 * math.log10(value))


def spectral_tilt(x: np.ndarray, *, sample_rate: int) -> float:
    """Fraction of signal energy in the speech band (120 Hz-3.2 kHz).

    Speech puts most energy there; an impulse click, a keyboard tap or hiss spreads
    it thin or pushes it high. Used only as a *tie-breaker* in the VAD, never as a
    gate, because a wrong "that was noise" decision would silently swallow a
    learner's question.
    """
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size < 64:
        return 0.0
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    energy = spec * spec
    freqs = np.fft.rfftfreq(x.size, 1.0 / sample_rate)
    mid = float(energy[(freqs >= 120.0) & (freqs <= 3200.0)].sum())
    total = float(energy.sum())
    if total <= 1e-12:
        return 0.0
    return mid / total


def frames(x: np.ndarray, *, frame_ms: int, sample_rate: int, hop_ms: int | None = None) -> list[np.ndarray]:
    n = int(sample_rate * frame_ms / 1000)
    hop = int(sample_rate * (hop_ms or frame_ms) / 1000)
    if n <= 0:
        return []
    out = []
    for start in range(0, max(1, x.size - n + 1), hop):
        chunk = x[start : start + n]
        if chunk.size == n:
            out.append(chunk)
    return out


@dataclass
class Pcm:
    samples: np.ndarray  # float32 mono
    sample_rate: int

    @property
    def duration_s(self) -> float:
        return float(self.samples.size) / max(1, self.sample_rate)

    def int16(self) -> np.ndarray:
        return float_to_int16(self.samples)

    def wav_bytes(self) -> bytes:
        return encode_wav(self.samples, sample_rate=self.sample_rate)


def encode_wav(x: np.ndarray, *, sample_rate: int, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(int(sample_rate))
        data = float_to_int16(x).tobytes() if width == 2 else (np.clip(x, -1, 1) * 127).astype(np.int8).tobytes()
        wf.writeframes(data)
    return buf.getvalue()


def decode_wav(raw: bytes) -> Pcm:
    with wave.open(io.BytesIO(raw), "rb") as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        n = wf.getnframes()
        frames = wf.readframes(n)
    if width == 2:
        arr = int16_to_float(bytes_to_int16(frames))
    elif width == 4:
        arr = np.frombuffer(frames, dtype="<f4").astype(np.float32)
    elif width == 1:
        arr = (np.frombuffer(frames, dtype=np.int8).astype(np.float32)) / 128.0
    else:  # pragma: no cover
        raise ValueError(f"unsupported sample width {width}")
    if wf.getnchannels() > 1:  # downmix
        arr = arr.reshape(-1, wf.getnchannels()).mean(axis=1).astype(np.float32)
    return Pcm(samples=arr, sample_rate=int(rate))


def pcm16_from_bytes(buf: bytes, *, sample_rate: int) -> Pcm:
    return Pcm(samples=int16_to_float(bytes_to_int16(buf)), sample_rate=sample_rate)


def tone(freq: float = 440.0, *, seconds: float = 0.2, sample_rate: int = TARGET_RATE, amp: float = 0.25) -> np.ndarray:
    """Used by the tests and the voice self-check (not by learners)."""
    t = np.arange(int(seconds * sample_rate), dtype=np.float32) / sample_rate
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def silence(seconds: float, *, sample_rate: int = TARGET_RATE, noise: float = 0.0) -> np.ndarray:
    n = int(seconds * sample_rate)
    if noise <= 0:
        return np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)
    return (rng.standard_normal(n) * noise).astype(np.float32)


def s16le_frame_count(byte_len: int) -> int:
    return byte_len // 2


__all__ = [
    "TARGET_RATE",
    "Pcm",
    "bytes_to_int16",
    "int16_to_float",
    "float_to_int16",
    "resample",
    "lowpass",
    "rms",
    "dbfs",
    "spectral_tilt",
    "frames",
    "encode_wav",
    "decode_wav",
    "pcm16_from_bytes",
    "tone",
    "silence",
    "s16le_frame_count",
]
