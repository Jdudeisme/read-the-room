"""Shared test fixtures: synthetic RoomState factory (no mic, no models)."""

from __future__ import annotations

from sensing.state import HeadcountBucket, RoomState, mood_quadrant


def make_state(
    timestamp: float = 0.0,
    valence: float | None = 0.5,
    arousal: float | None = 0.5,
    speech_ratio: float = 0.6,
    headcount_bucket: HeadcountBucket | None = HeadcountBucket.FOUR,
    headcount_confidence: float | None = 0.8,
    trend: str = "stable",
    mood: str | None = "auto",
    **overrides,
) -> RoomState:
    """A plausible speech-active RoomState; override any field per test."""
    if mood == "auto":
        mood = (
            mood_quadrant(valence, arousal)
            if valence is not None and arousal is not None
            else None
        )
    fields = dict(
        timestamp=timestamp,
        loudness_dbfs=-35.0,
        activity_density=1.2,
        spectral_balance={"low": 0.3, "mid": 0.5, "high": 0.2},
        speech_ratio=speech_ratio,
        valence=valence,
        arousal=arousal,
        emotion_confidence=None if valence is None else 0.9,
        emotion_staleness_s=None if valence is None else 2.0,
        headcount_bucket=headcount_bucket,
        headcount_confidence=headcount_confidence,
        headcount_staleness_s=None if headcount_bucket is None else 3.0,
        energy=0.5,
        mood=mood,
        trend=trend,
    )
    fields.update(overrides)
    return RoomState(**fields)
