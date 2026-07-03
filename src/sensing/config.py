"""Engine configuration, sourced from environment variables (with .env support)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


@dataclass(frozen=True)
class Config:
    # Audio capture. 16 kHz mono is native for both Silero VAD and wav2vec2;
    # the capture layer resamples if the device can't open at 16 kHz.
    sample_rate: int = 16_000
    input_device: str | None = None

    # Rolling analysis window.
    window_s: float = 5.0
    hop_s: float = 2.0

    # EMA smoothing time constants (seconds). Larger = smoother/slower.
    smooth_tau_dsp_s: float = 6.0
    smooth_tau_emotion_s: float = 10.0

    # VAD gate.
    vad_threshold: float = 0.5  # per-chunk speech probability cutoff

    # Emotion layer.
    emotion_enabled: bool = True
    emotion_model: str = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"
    emotion_min_interval_s: float = 2.0
    emotion_min_speech_ratio: float = 0.2
    # Beyond this age an emotion reading no longer drives the mood quadrant.
    emotion_max_staleness_s: float = 20.0

    # Headcount layer (Milestone 2).
    headcount_enabled: bool = True
    headcount_model: str = "speechbrain/spkrec-ecapa-voxceleb"
    # Rate limit, independent of the hop. The PRE-APPROVED fallback if the
    # concurrent benchmark (scripts/bench_headcount.py --concurrent) misses
    # the budget: set RTR_HEADCOUNT_MIN_INTERVAL_S=4.0 — headcount updates
    # every other hop, staleness reports it honestly, nothing else changes.
    headcount_min_interval_s: float = 2.0
    headcount_min_speech_ratio: float = 0.2
    headcount_buffer_s: float = 90.0  # rolling embedding evidence horizon
    headcount_buffer_cap: int = 200  # max buffered embeddings (memory bound,
    # limits evidence quality — NOT a representable-count ceiling)
    headcount_cluster_threshold: float = 0.40  # cosine distance cut
    headcount_smooth_tau_s: float = 20.0  # EMA time constant, log2 space
    headcount_hysteresis_k: int = 3  # consecutive updates to change bucket

    # Trend detection over the history buffer.
    trend_horizon_s: float = 60.0
    # Energy-score slope (per minute) beyond which trend reads rising/falling.
    trend_slope_threshold: float = 0.10

    torch_threads: int = 0  # 0 = torch default
    os_truststore: bool = True

    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()  # no-op if no .env file
        device = _env_str("RTR_INPUT_DEVICE", "") or None
        return cls(
            input_device=device,
            window_s=_env_float("RTR_WINDOW_S", cls.window_s),
            hop_s=_env_float("RTR_HOP_S", cls.hop_s),
            emotion_enabled=_env_bool("RTR_EMOTION_ENABLED", cls.emotion_enabled),
            emotion_model=_env_str("RTR_EMOTION_MODEL", cls.emotion_model),
            emotion_min_interval_s=_env_float(
                "RTR_EMOTION_MIN_INTERVAL_S", cls.emotion_min_interval_s
            ),
            emotion_min_speech_ratio=_env_float(
                "RTR_EMOTION_MIN_SPEECH_RATIO", cls.emotion_min_speech_ratio
            ),
            headcount_enabled=_env_bool("RTR_HEADCOUNT_ENABLED", cls.headcount_enabled),
            headcount_model=_env_str("RTR_HEADCOUNT_MODEL", cls.headcount_model),
            headcount_min_interval_s=_env_float(
                "RTR_HEADCOUNT_MIN_INTERVAL_S", cls.headcount_min_interval_s
            ),
            headcount_min_speech_ratio=_env_float(
                "RTR_HEADCOUNT_MIN_SPEECH_RATIO", cls.headcount_min_speech_ratio
            ),
            headcount_buffer_s=_env_float(
                "RTR_HEADCOUNT_BUFFER_S", cls.headcount_buffer_s
            ),
            headcount_cluster_threshold=_env_float(
                "RTR_HEADCOUNT_CLUSTER_THRESHOLD", cls.headcount_cluster_threshold
            ),
            headcount_smooth_tau_s=_env_float(
                "RTR_HEADCOUNT_SMOOTH_TAU_S", cls.headcount_smooth_tau_s
            ),
            headcount_hysteresis_k=_env_int(
                "RTR_HEADCOUNT_HYSTERESIS_K", cls.headcount_hysteresis_k
            ),
            torch_threads=_env_int("RTR_TORCH_THREADS", cls.torch_threads),
            os_truststore=_env_bool("RTR_OS_TRUSTSTORE", cls.os_truststore),
        )
