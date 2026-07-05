"""Mapping-layer configuration (RTR_MAPPING_* env vars, .env supported).

Every value here is a tunable boundary/threshold; the full set is embedded
into each Recommendation as `boundaries_snapshot` so annotations stay
attributable to the exact values that were in effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from sensing.config import _env_float


@dataclass(frozen=True)
class MappingConfig:
    # Valence/arousal band cutoffs (band is "high" above *_high, "low" at or
    # below *_low, "mid" between). Seeded at +/-0.25 to match the 2020 grid.
    valence_low: float = -0.25
    valence_high: float = 0.25
    arousal_low: float = -0.25
    arousal_high: float = 0.25

    # Hysteresis: minimum seconds between emitted Recommendations. Material
    # change (quadrant/bucket/energy_action) is ALSO required — dwell alone
    # never re-emits.
    min_dwell_s: float = 30.0

    # Low-confidence guard.
    min_speech_ratio: float = 0.1  # below this, "VAD says no speech"
    # The collapsed/crowd regime caps headcount confidence at ~0.25-0.3;
    # below this cutoff the mapper holds rather than guesses.
    min_headcount_confidence: float = 0.35
    guard_confidence: float = 0.15  # confidence reported on guard holds

    # Short-horizon valence/arousal trend (drives energy_action + targets).
    # Same semantics as the engine's energy trend: least-squares slope over
    # the horizon, rising/falling past the per-minute threshold.
    trend_horizon_s: float = 60.0
    trend_slope_threshold: float = 0.10

    # How far the target valence/arousal leads the current reading in the
    # trend direction ("match the room's direction, don't always escalate").
    target_lead: float = 0.15

    @classmethod
    def from_env(cls) -> "MappingConfig":
        load_dotenv()  # no-op if no .env file
        return cls(
            valence_low=_env_float("RTR_MAPPING_VALENCE_LOW", cls.valence_low),
            valence_high=_env_float("RTR_MAPPING_VALENCE_HIGH", cls.valence_high),
            arousal_low=_env_float("RTR_MAPPING_AROUSAL_LOW", cls.arousal_low),
            arousal_high=_env_float("RTR_MAPPING_AROUSAL_HIGH", cls.arousal_high),
            min_dwell_s=_env_float("RTR_MAPPING_MIN_DWELL_S", cls.min_dwell_s),
            min_speech_ratio=_env_float(
                "RTR_MAPPING_MIN_SPEECH_RATIO", cls.min_speech_ratio
            ),
            min_headcount_confidence=_env_float(
                "RTR_MAPPING_MIN_HEADCOUNT_CONFIDENCE", cls.min_headcount_confidence
            ),
            guard_confidence=_env_float(
                "RTR_MAPPING_GUARD_CONFIDENCE", cls.guard_confidence
            ),
            trend_horizon_s=_env_float(
                "RTR_MAPPING_TREND_HORIZON_S", cls.trend_horizon_s
            ),
            trend_slope_threshold=_env_float(
                "RTR_MAPPING_TREND_SLOPE_THRESHOLD", cls.trend_slope_threshold
            ),
            target_lead=_env_float("RTR_MAPPING_TARGET_LEAD", cls.target_lead),
        )

    def boundaries(self) -> dict:
        """Every boundary/threshold in effect — the Recommendation snapshot."""
        return {
            "valence_low": self.valence_low,
            "valence_high": self.valence_high,
            "arousal_low": self.arousal_low,
            "arousal_high": self.arousal_high,
            "min_dwell_s": self.min_dwell_s,
            "min_speech_ratio": self.min_speech_ratio,
            "min_headcount_confidence": self.min_headcount_confidence,
            "guard_confidence": self.guard_confidence,
            "trend_horizon_s": self.trend_horizon_s,
            "trend_slope_threshold": self.trend_slope_threshold,
            "target_lead": self.target_lead,
        }
