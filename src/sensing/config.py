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
    # Contamination gate v1 (M4): while playback is active, evidence
    # certification (emotion/headcount gating, speech ratio) requires this
    # stricter per-chunk cutoff — vocal music sits lower on Silero's speech
    # probability than live room speech, so raising the bar sheds most of it.
    vad_playback_threshold: float = 0.75
    # Rolling noise-floor EMA (loudness of quiescent windows). During
    # playback the babble saturation heuristic keys on loudness RELATIVE to
    # this floor instead of absolute dBFS — continuous music raises the
    # room's absolute floor permanently (FIELD-NOTES 2026-07-06: the fan
    # produced exactly this signature and a phantom 16).
    noise_floor_tau_s: float = 60.0

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
    # Cosine-distance cut for speaker clustering. Calibrated 2026-07-05
    # against measured ECAPA distances on 1.25s segments: same-voice pairs
    # center ~0.35 (p90 0.47) even on clean synthetic speech and ~0.6 on a
    # laptop mic, while different-voice pairs sit ~0.9 (p10 0.87). The old
    # 0.40 default sat INSIDE the same-speaker distribution and fragmented a
    # solo speaker into phantom people.
    headcount_cluster_threshold: float = 0.70
    # A cluster only counts as a person while it holds at least this fraction
    # of all buffered speech evidence (see HeadcountEstimator min-mass).
    headcount_min_cluster_frac: float = 0.10
    headcount_smooth_tau_s: float = 20.0  # EMA time constant, log2 space
    headcount_hysteresis_k: int = 3  # consecutive updates to change bucket

    # Music-aware emotion (M6): remove the playing track's measured pull
    # from certified-speech readings. See sensing/music.py and
    # docs/M6-PROPOSAL.md for the derivation of every default.
    music_aware_enabled: bool = True
    # Per-axis scale on the measured PULL signature (the mixed-window
    # estimator). 1.0 = subtract exactly what was measured. Per-axis
    # because the 2026-07-11 gate measured different super-additivity on
    # each axis; the part (c) re-run moves these, not vibes.
    music_beta_v: float = 1.0
    music_beta_a: float = 1.0
    # Cold-start prior: while a track has no pull samples yet, subtract
    # its STANDALONE signature scaled by these — the super-additivity
    # ratios measured 2026-07-11 (valence pull +0.33 vs signature
    # +0.09..0.15 -> ~2.2 at the conservative end; arousal ~1.5).
    music_standalone_scale_v: float = 2.2
    music_standalone_scale_a: float = 1.5
    # Per-axis magnitude cap on any subtraction — bounds the damage of a
    # bad estimate (a correction should never be able to swing a reading
    # across most of the [-1, 1] range).
    music_max_correction: float = 0.6
    # Clean-speech baseline: EMA over readings taken with playback off or
    # dominance <= baseline_m_max; pull samples are banked only while the
    # baseline is younger than baseline_max_age_s (a stale baseline would
    # launder mood drift into the track's pull signature).
    music_baseline_tau_s: float = 20.0
    music_baseline_max_age_s: float = 300.0
    music_baseline_m_max: float = 0.1
    # A mixed window contributes a pull sample only at dominance >= this
    # (dividing by a tiny m amplifies noise).
    music_pull_m_floor: float = 0.25
    # Dominance ramp knots on spectral_balance.high: 0 at/below lo
    # (quiet-room speech measured 0.014-0.031), 1 at/above hi (speech over
    # pop measured 0.257-0.484).
    music_dominance_lo: float = 0.05
    music_dominance_hi: float = 0.30
    # Reference taps: a playback window counts as music-only (eligible to
    # measure the track's pull) when its raw speech ratio is at most this.
    music_ref_max_speech_ratio: float = 0.1
    # A track's signature is trusted after this many reference taps.
    music_min_refs: int = 3
    # Discount floor while no signature exists: confidence scales by
    # (1 - gamma * dominance).
    music_discount_gamma: float = 0.5
    music_signatures_path: str = "data/track_signatures.json"

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
            vad_playback_threshold=_env_float(
                "RTR_VAD_PLAYBACK_THRESHOLD", cls.vad_playback_threshold
            ),
            noise_floor_tau_s=_env_float(
                "RTR_NOISE_FLOOR_TAU_S", cls.noise_floor_tau_s
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
            headcount_min_cluster_frac=_env_float(
                "RTR_HEADCOUNT_MIN_CLUSTER_FRAC", cls.headcount_min_cluster_frac
            ),
            headcount_smooth_tau_s=_env_float(
                "RTR_HEADCOUNT_SMOOTH_TAU_S", cls.headcount_smooth_tau_s
            ),
            headcount_hysteresis_k=_env_int(
                "RTR_HEADCOUNT_HYSTERESIS_K", cls.headcount_hysteresis_k
            ),
            music_aware_enabled=_env_bool(
                "RTR_MUSIC_AWARE_ENABLED", cls.music_aware_enabled
            ),
            music_beta_v=_env_float("RTR_MUSIC_BETA_V", cls.music_beta_v),
            music_beta_a=_env_float("RTR_MUSIC_BETA_A", cls.music_beta_a),
            music_standalone_scale_v=_env_float(
                "RTR_MUSIC_STANDALONE_SCALE_V", cls.music_standalone_scale_v
            ),
            music_standalone_scale_a=_env_float(
                "RTR_MUSIC_STANDALONE_SCALE_A", cls.music_standalone_scale_a
            ),
            music_max_correction=_env_float(
                "RTR_MUSIC_MAX_CORRECTION", cls.music_max_correction
            ),
            music_baseline_tau_s=_env_float(
                "RTR_MUSIC_BASELINE_TAU_S", cls.music_baseline_tau_s
            ),
            music_baseline_max_age_s=_env_float(
                "RTR_MUSIC_BASELINE_MAX_AGE_S", cls.music_baseline_max_age_s
            ),
            music_baseline_m_max=_env_float(
                "RTR_MUSIC_BASELINE_M_MAX", cls.music_baseline_m_max
            ),
            music_pull_m_floor=_env_float(
                "RTR_MUSIC_PULL_M_FLOOR", cls.music_pull_m_floor
            ),
            music_dominance_lo=_env_float(
                "RTR_MUSIC_DOMINANCE_LO", cls.music_dominance_lo
            ),
            music_dominance_hi=_env_float(
                "RTR_MUSIC_DOMINANCE_HI", cls.music_dominance_hi
            ),
            music_ref_max_speech_ratio=_env_float(
                "RTR_MUSIC_REF_MAX_SPEECH_RATIO", cls.music_ref_max_speech_ratio
            ),
            music_min_refs=_env_int("RTR_MUSIC_MIN_REFS", cls.music_min_refs),
            music_discount_gamma=_env_float(
                "RTR_MUSIC_DISCOUNT_GAMMA", cls.music_discount_gamma
            ),
            music_signatures_path=_env_str(
                "RTR_MUSIC_SIGNATURES_PATH", cls.music_signatures_path
            ),
            torch_threads=_env_int("RTR_TORCH_THREADS", cls.torch_threads),
            os_truststore=_env_bool("RTR_OS_TRUSTSTORE", cls.os_truststore),
        )
