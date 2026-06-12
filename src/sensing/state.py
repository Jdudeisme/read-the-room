"""RoomState: the engine's single output contract, plus the smoothing/derivation
that turns raw per-window measurements into the published state.

Consumers (console now, dashboard in M2) depend only on this module's types.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

Mood = Literal["excited", "tense", "chill", "flat"]
Trend = Literal["rising", "stable", "falling"]


class HeadcountBucket(str, Enum):
    """Powers-of-2 occupancy buckets. Milestone 2 fills this in; the field
    exists now so the consumer-facing schema never changes."""

    SOLO = "solo"
    PAIR = "pair"
    FOUR = "4"
    EIGHT = "8"
    SIXTEEN = "16"
    CROWD = "crowd"


@dataclass(frozen=True)
class RoomState:
    timestamp: float  # unix seconds, end of the analysis window

    # Continuous DSP layer (always present).
    loudness_dbfs: float  # EMA-smoothed RMS level
    activity_density: float  # onsets per second, EMA-smoothed
    spectral_balance: dict[str, float]  # {"low","mid","high"} energy fractions

    # VAD layer (always present).
    speech_ratio: float  # 0..1, fraction of window judged speech

    # Emotion layer (None until first speech-gated inference lands).
    valence: float | None  # -1..1
    arousal: float | None  # -1..1
    emotion_confidence: float | None  # 0..1 heuristic (speech ratio at inference)
    emotion_staleness_s: float | None  # age of the reading; grows between updates

    # Milestone 2 — always None in M1.
    headcount_bucket: HeadcountBucket | None

    # Derived.
    energy: float  # 0..1 composite
    mood: Mood | None  # quadrant of (valence, arousal); None when emotion is absent/stale
    trend: Trend  # energy direction over the trend horizon

    def to_dict(self) -> dict:
        d = asdict(self)
        d["headcount_bucket"] = self.headcount_bucket.value if self.headcount_bucket else None
        return d


def mood_quadrant(valence: float, arousal: float) -> Mood:
    if arousal >= 0:
        return "excited" if valence >= 0 else "tense"
    return "chill" if valence >= 0 else "flat"


def energy_score(
    loudness_dbfs: float,
    activity_density: float,
    speech_ratio: float,
    arousal: float | None,
) -> float:
    """Composite 0..1 room energy.

    Loudness maps -60..-10 dBFS onto 0..1; activity saturates at 4 onsets/s.
    When no emotion reading is available the arousal term drops out and the
    remaining weights are renormalised, so energy stays comparable.
    """
    loud = _clamp01((loudness_dbfs + 60.0) / 50.0)
    act = _clamp01(activity_density / 4.0)
    parts = [(0.30, loud), (0.20, act), (0.25, _clamp01(speech_ratio))]
    if arousal is not None:
        parts.append((0.25, _clamp01((arousal + 1.0) / 2.0)))
    total_w = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total_w


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


class Ema:
    """Exponential moving average with a time constant, robust to irregular ticks."""

    def __init__(self, tau_s: float):
        self.tau_s = tau_s
        self.value: float | None = None
        self._last_t: float | None = None

    def update(self, x: float, t: float) -> float:
        if self.value is None or self._last_t is None:
            self.value = x
        else:
            dt = max(1e-6, t - self._last_t)
            alpha = 1.0 - math.exp(-dt / self.tau_s)
            self.value += alpha * (x - self.value)
        self._last_t = t
        return self.value


class TrendTracker:
    """Least-squares slope of the energy score over a rolling horizon."""

    def __init__(self, horizon_s: float, slope_threshold_per_min: float):
        self.horizon_s = horizon_s
        self.slope_threshold = slope_threshold_per_min
        self._points: list[tuple[float, float]] = []  # (t, energy)

    def update(self, energy: float, t: float) -> Trend:
        self._points.append((t, energy))
        cutoff = t - self.horizon_s
        self._points = [p for p in self._points if p[0] >= cutoff]
        if len(self._points) < 5 or self._points[-1][0] - self._points[0][0] < 15.0:
            return "stable"
        ts = [p[0] for p in self._points]
        es = [p[1] for p in self._points]
        t_mean = sum(ts) / len(ts)
        e_mean = sum(es) / len(es)
        denom = sum((x - t_mean) ** 2 for x in ts)
        if denom == 0:
            return "stable"
        slope_per_s = sum((x - t_mean) * (y - e_mean) for x, y in zip(ts, es)) / denom
        slope_per_min = slope_per_s * 60.0
        if slope_per_min > self.slope_threshold:
            return "rising"
        if slope_per_min < -self.slope_threshold:
            return "falling"
        return "stable"
