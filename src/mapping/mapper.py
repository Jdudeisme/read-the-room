"""Mapper: RoomState -> shadow Recommendation.

Consumes the sensing seam (RoomState) and nothing deeper. Stateful across
updates: short-horizon valence/arousal trends and the hysteresis dwell timer.
All time arithmetic uses `state.timestamp` (wall seconds) so behavior is
fully reproducible from a recorded state stream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sensing.state import RoomState, Trend, TrendTracker

from .config import MappingConfig
from .rulebook import RULEBOOK

EnergyAction = str  # "hold" | "raise" | "lower"

SCHEMA_VERSION = 1

# Sentinel first element of matched_cell when the low-confidence guard fired
# instead of a rulebook cell. The second element names the reason.
GUARD_CELL = "guard"


@dataclass(frozen=True)
class Recommendation:
    energy_action: EnergyAction
    target_valence: float
    target_arousal: float
    genre_pool: list[str]
    confidence: float
    summary: str
    matched_cell: tuple  # rulebook key that fired, or ("guard", reason)
    boundaries_snapshot: dict  # boundary/threshold values in effect
    timestamp: float  # state.timestamp of the state that fired it
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["matched_cell"] = list(self.matched_cell)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Recommendation":
        d = dict(d)
        d["matched_cell"] = tuple(d["matched_cell"])
        return cls(**d)


def band(value: float, low_cut: float, high_cut: float) -> str:
    """Three-way band split, boundary semantics identical to the 2020 grid:
    strictly above high_cut is "high", strictly above low_cut is "mid"."""
    if value > high_cut:
        return "high"
    if value > low_cut:
        return "mid"
    return "low"


class Mapper:
    def __init__(self, config: MappingConfig | None = None):
        self.config = config or MappingConfig.from_env()
        self._valence_trend = TrendTracker(
            self.config.trend_horizon_s, self.config.trend_slope_threshold
        )
        self._arousal_trend = TrendTracker(
            self.config.trend_horizon_s, self.config.trend_slope_threshold
        )
        self._last_emitted: Recommendation | None = None
        self._last_signature: tuple | None = None  # (mood, bucket, action)

    def update(self, state: RoomState) -> Recommendation | None:
        now = state.timestamp

        valence_trend: Trend = "stable"
        arousal_trend: Trend = "stable"
        if state.valence is not None and state.arousal is not None:
            valence_trend = self._valence_trend.update(state.valence, now)
            arousal_trend = self._arousal_trend.update(state.arousal, now)

        guard_reason = self._guard_reason(state)
        if guard_reason is not None:
            candidate = self._guard_recommendation(state, guard_reason)
        else:
            candidate = self._rulebook_recommendation(
                state, valence_trend, arousal_trend
            )

        # Hysteresis: emit only if the dwell elapsed AND the target moved
        # materially (mood quadrant, headcount bucket, or energy action; a
        # guard <-> rulebook transition is always material).
        bucket = state.headcount_bucket.value if state.headcount_bucket else None
        signature = (
            state.mood,
            bucket,
            candidate.energy_action,
            candidate.matched_cell[0] == GUARD_CELL,
        )
        if self._last_emitted is not None:
            if now - self._last_emitted.timestamp < self.config.min_dwell_s:
                return None
            if signature == self._last_signature:
                return None
        self._last_emitted = candidate
        self._last_signature = signature
        return candidate

    # -- candidate construction ---------------------------------------------

    def _guard_reason(self, state: RoomState) -> str | None:
        """Why the mapper should hold instead of guessing, or None."""
        if state.speech_ratio < self.config.min_speech_ratio:
            return "no-speech"
        if state.mood is None or state.valence is None or state.arousal is None:
            return "no-emotion"  # absent or stale reading; mood already gates staleness
        if state.headcount_bucket is None:
            return "no-headcount"
        if (
            state.headcount_confidence is not None
            and state.headcount_confidence < self.config.min_headcount_confidence
        ):
            return "uncertain-regime"
        return None

    def _guard_recommendation(self, state: RoomState, reason: str) -> Recommendation:
        valence = state.valence if state.valence is not None else 0.0
        arousal = state.arousal if state.arousal is not None else 0.0
        return Recommendation(
            energy_action="hold",
            target_valence=round(valence, 3),
            target_arousal=round(arousal, 3),
            genre_pool=[],
            confidence=self.config.guard_confidence,
            summary=f"insufficient signal ({reason}) — hold current energy",
            matched_cell=(GUARD_CELL, reason),
            boundaries_snapshot=self.config.boundaries(),
            timestamp=state.timestamp,
        )

    def _rulebook_recommendation(
        self, state: RoomState, valence_trend: Trend, arousal_trend: Trend
    ) -> Recommendation:
        cfg = self.config
        vband = band(state.valence, cfg.valence_low, cfg.valence_high)
        aband = band(state.arousal, cfg.arousal_low, cfg.arousal_high)
        cell = (state.headcount_bucket.value, vband, aband)
        pool = RULEBOOK[cell]

        # Match the room's direction. Arousal is the energy axis; when it is
        # flat, defer to the engine's composite energy trend.
        direction = arousal_trend if arousal_trend != "stable" else state.trend
        action = {"rising": "raise", "falling": "lower", "stable": "hold"}[direction]

        lead = {"raise": cfg.target_lead, "lower": -cfg.target_lead, "hold": 0.0}
        target_arousal = _clamp(state.arousal + lead[action])
        target_valence = _clamp(
            state.valence
            + {"rising": cfg.target_lead, "falling": -cfg.target_lead, "stable": 0.0}[
                valence_trend
            ]
        )

        confidence = _clamp01(
            0.5 * (state.emotion_confidence or 0.0)
            + 0.5 * (state.headcount_confidence or 0.0)
        )
        summary = (
            f"{state.mood} room, ~{state.headcount_bucket.value} people "
            f"→ {' / '.join(pool)}; {action} energy"
        )
        return Recommendation(
            energy_action=action,
            target_valence=round(target_valence, 3),
            target_arousal=round(target_arousal, 3),
            genre_pool=list(pool),
            confidence=round(confidence, 2),
            summary=summary,
            matched_cell=cell,
            boundaries_snapshot=cfg.boundaries(),
            timestamp=state.timestamp,
        )


def _clamp(x: float) -> float:
    return min(1.0, max(-1.0, x))


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))
