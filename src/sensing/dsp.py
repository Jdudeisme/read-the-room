"""Continuous DSP layer: cheap per-window measurements computed with numpy only.

This layer must stay essentially free — it is the heartbeat that keeps the
console live while the ML layers warm up or fall behind.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-10

# STFT framing: 64 ms frames, 32 ms hop at 16 kHz.
_FRAME = 1024
_HOP = 512

# Spectral balance band edges (Hz): low = rumble/hum, mid = voice fundamentals
# and most speech energy, high = sibilance/clatter/brightness.
_LOW_HZ = 300.0
_HIGH_HZ = 2000.0


@dataclass(frozen=True)
class DspResult:
    rms_dbfs: float
    onset_density: float  # onsets per second
    spectral_balance: dict[str, float]  # {"low","mid","high"} fractions summing to 1


def analyze(window: np.ndarray, sample_rate: int) -> DspResult:
    """Analyze a mono float32 window in [-1, 1]."""
    if window.size == 0:
        return DspResult(-120.0, 0.0, {"low": 0.0, "mid": 0.0, "high": 0.0})

    rms = float(np.sqrt(np.mean(np.square(window, dtype=np.float64))))
    rms_dbfs = float(20.0 * np.log10(max(rms, _EPS)))

    mags = _stft_mags(window)
    duration_s = window.size / sample_rate
    onset_density = _count_onsets(mags) / duration_s
    balance = _spectral_balance(mags, sample_rate)

    return DspResult(max(rms_dbfs, -120.0), onset_density, balance)


def _stft_mags(window: np.ndarray) -> np.ndarray:
    """Magnitude spectrogram, shape (frames, bins). Returns empty for short input."""
    if window.size < _FRAME:
        return np.empty((0, _FRAME // 2 + 1))
    n_frames = 1 + (window.size - _FRAME) // _HOP
    strides = (window.strides[0] * _HOP, window.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        window, shape=(n_frames, _FRAME), strides=strides, writeable=False
    )
    return np.abs(np.fft.rfft(frames * np.hanning(_FRAME), axis=1))


def _count_onsets(mags: np.ndarray) -> int:
    """Spectral-flux onsets: positive flux peaks above an adaptive threshold."""
    if mags.shape[0] < 3:
        return 0
    flux = np.sum(np.maximum(0.0, np.diff(mags, axis=0)), axis=1)
    threshold = flux.mean() + 1.5 * flux.std()
    if threshold <= _EPS:
        return 0
    above = flux > threshold
    # Local maxima only, so one event isn't counted across adjacent frames.
    peaks = above[1:-1] & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] >= flux[2:])
    return int(np.count_nonzero(peaks))


def _spectral_balance(mags: np.ndarray, sample_rate: int) -> dict[str, float]:
    if mags.shape[0] == 0:
        return {"low": 0.0, "mid": 0.0, "high": 0.0}
    power = np.sum(np.square(mags), axis=0)
    freqs = np.fft.rfftfreq(_FRAME, d=1.0 / sample_rate)
    total = float(power.sum())
    if total <= _EPS:
        return {"low": 0.0, "mid": 0.0, "high": 0.0}
    low = float(power[freqs < _LOW_HZ].sum()) / total
    mid = float(power[(freqs >= _LOW_HZ) & (freqs < _HIGH_HZ)].sum()) / total
    high = float(power[freqs >= _HIGH_HZ].sum()) / total
    return {"low": round(low, 4), "mid": round(mid, 4), "high": round(high, 4)}
