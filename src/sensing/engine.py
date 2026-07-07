"""The engine: ticks every hop, layers DSP -> VAD -> emotion into a RoomState,
and pushes it to consumers.

Layering contract (M1 spec, extended by M2):
  1. DSP runs every tick, unconditionally — the heartbeat.
  2. VAD runs continuously on new audio; its speech ratio gates layers 3-4.
     VAD certification is CENTRALIZED here: emotion and headcount both
     consume the same gate's output and never run their own VAD. (This is
     also where a future music-detection gate inserts, so every downstream
     layer inherits it at once.)
  3. Emotion runs only on speech-certified windows, asynchronously, published
     with confidence + staleness so consumers can judge freshness.
  4. Headcount (M2) runs only on speech-certified windows, asynchronously, on
     its own worker thread, published as a power-of-2 bucket with the same
     confidence + staleness pattern.

Consumers receive a finished RoomState and nothing else; the console renderer
today and the M2 dashboard tomorrow plug in identically.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from . import dsp
from .config import Config
from .emotion import EmotionWorker
from .headcount import BucketSmoother, HeadcountEstimator, HeadcountWorker
from .state import Ema, RoomState, TrendTracker, energy_score, mood_quadrant
from .vad import VadGate

log = logging.getLogger(__name__)


class Consumer(Protocol):
    def on_state(self, state: RoomState) -> None: ...


class PlaybackStateSource(Protocol):
    """Where playback awareness comes from (M4): the hosted playback
    controller satisfies this with a cached, non-blocking read. The engine
    only ever stamps the answer onto RoomState — it must NEVER wait on
    playback I/O, so implementations return cached state."""

    def playback_state(self) -> tuple[bool, str | None]: ...


class AudioSource(Protocol):
    sample_rate: int
    device_name: str
    ring: object

    def start(self) -> None: ...
    def stop(self) -> None: ...


class Engine:
    def __init__(
        self,
        source,
        config: Config,
        consumers: list[Consumer],
        playback_source: PlaybackStateSource | None = None,
    ):
        self.source = source
        self.config = config
        self.consumers = list(consumers)
        self.playback_source = playback_source
        self.vad = VadGate(config.sample_rate, config.window_s, config.vad_threshold)
        self.emotion: EmotionWorker | None = (
            EmotionWorker(
                config.emotion_model,
                config.emotion_min_interval_s,
                config.torch_threads,
                config.os_truststore,
            )
            if config.emotion_enabled
            else None
        )
        self.headcount: HeadcountWorker | None = (
            HeadcountWorker(
                config.headcount_model,
                config.headcount_min_interval_s,
                HeadcountEstimator(
                    buffer_s=config.headcount_buffer_s,
                    buffer_cap=config.headcount_buffer_cap,
                    cluster_threshold=config.headcount_cluster_threshold,
                    min_cluster_evidence_frac=config.headcount_min_cluster_frac,
                ),
                BucketSmoother(
                    tau_s=config.headcount_smooth_tau_s,
                    hold_k=config.headcount_hysteresis_k,
                ),
                sample_rate=config.sample_rate,
                torch_threads=config.torch_threads,
                os_truststore=config.os_truststore,
            )
            if config.headcount_enabled
            else None
        )
        self._ema_loudness = Ema(config.smooth_tau_dsp_s)
        self._ema_activity = Ema(config.smooth_tau_dsp_s)
        self._ema_speech = Ema(config.smooth_tau_dsp_s)
        # Rolling noise floor: EMA over QUIESCENT windows only (raw speech
        # ratio < 0.1), so it tracks fans/HVAC/music, not conversation.
        self._noise_floor = Ema(config.noise_floor_tau_s)
        self._ema_valence = Ema(config.smooth_tau_emotion_s)
        self._ema_arousal = Ema(config.smooth_tau_emotion_s)
        self._trend = TrendTracker(config.trend_horizon_s, config.trend_slope_threshold)
        self._vad_position = 0
        self._running = False

    @property
    def emotion_status(self) -> str:
        if self.emotion is None:
            return "disabled"
        return self.emotion.status

    @property
    def headcount_status(self) -> str:
        if self.headcount is None:
            return "disabled"
        return self.headcount.status

    def run(self, max_ticks: int | None = None) -> None:
        """Blocking loop: capture -> tick every hop -> publish. Ctrl+C to stop."""
        self.vad.load()
        if self.emotion is not None:
            self.emotion.start()  # loads the model off-thread; ticks don't wait
        if self.headcount is not None:
            self.headcount.start()
        self.source.start()
        log.info("capturing from %r", self.source.device_name)
        self._running = True
        ticks = 0
        next_tick = time.monotonic() + self.config.hop_s
        try:
            while self._running:
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_tick += self.config.hop_s
                state = self._tick()
                for consumer in self.consumers:
                    try:
                        consumer.on_state(state)
                    except Exception:
                        log.exception("consumer %r failed", consumer)
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.source.stop()
        if self.emotion is not None:
            self.emotion.stop()
        if self.headcount is not None:
            self.headcount.stop()

    def _tick(self) -> RoomState:
        now = time.monotonic()
        wall = time.time()
        window = self.source.ring.read_last(
            int(self.config.window_s * self.config.sample_rate)
        )

        # Layer 1: DSP heartbeat.
        measured = dsp.analyze(window, self.config.sample_rate)
        loudness = self._ema_loudness.update(measured.rms_dbfs, now)
        activity = self._ema_activity.update(measured.onset_density, now)

        # Playback awareness (M4): a cached read, never provider I/O. A
        # broken source must not take down the sensing heartbeat. Read
        # BEFORE certification — it selects the VAD threshold.
        playback_active, playback_track_id = False, None
        if self.playback_source is not None:
            try:
                playback_active, playback_track_id = (
                    self.playback_source.playback_state()
                )
            except Exception:
                log.exception("playback state source failed; stamping inactive")

        # Layer 2: VAD on audio captured since the last tick. Contamination
        # gate v1: while the system's own output is audible, certification
        # demands a stricter per-chunk threshold — this is the centralized
        # certification point, so emotion and headcount inherit it at once.
        new_samples, self._vad_position = self.source.ring.read_since(self._vad_position)
        self.vad.feed(new_samples)
        cert_threshold = (
            self.config.vad_playback_threshold
            if playback_active
            else self.config.vad_threshold
        )
        raw_ratio = self.vad.speech_ratio(cert_threshold)
        speech_ratio = self._ema_speech.update(raw_ratio, now)
        # Quiescent windows feed the rolling noise floor (fan/HVAC/music —
        # whatever the room sounds like when nobody is talking).
        if raw_ratio < 0.1:
            self._noise_floor.update(measured.rms_dbfs, now)

        # Layer 3: emotion, gated on the *instantaneous* window's speech.
        valence = arousal = confidence = staleness = None
        if self.emotion is not None:
            if (
                raw_ratio >= self.config.emotion_min_speech_ratio
                and window.size >= self.config.sample_rate  # at least 1s of audio
            ):
                self.emotion.submit(window, raw_ratio, now)
            reading, staleness = self.emotion.latest(now)
            if reading is not None:
                valence = self._ema_valence.update(reading.valence, now)
                arousal = self._ema_arousal.update(reading.arousal, now)
                confidence = reading.confidence

        # Layer 4: headcount, gated on the same instantaneous VAD certification.
        # During silence nothing is submitted: the bucket holds and staleness
        # grows — silence is absence of evidence, not evidence of an empty room.
        hc_bucket = hc_confidence = hc_staleness = None
        if self.headcount is not None:
            if (
                raw_ratio >= self.config.headcount_min_speech_ratio
                and window.size >= self.config.sample_rate
            ):
                self.headcount.submit(
                    window,
                    self.vad.speech_mask(cert_threshold),
                    raw_ratio,
                    measured.rms_dbfs,
                    now,
                    playback_active,
                    self._noise_floor.value,
                )
            hc_reading, hc_staleness = self.headcount.latest(now)
            if hc_reading is not None:
                hc_bucket = hc_reading.bucket
                hc_confidence = hc_reading.confidence

        energy = energy_score(loudness, activity, speech_ratio, arousal)
        mood = None
        if (
            valence is not None
            and arousal is not None
            and staleness is not None
            and staleness <= self.config.emotion_max_staleness_s
        ):
            mood = mood_quadrant(valence, arousal)

        return RoomState(
            timestamp=wall,
            loudness_dbfs=round(loudness, 1),
            activity_density=round(activity, 2),
            spectral_balance=measured.spectral_balance,
            speech_ratio=round(speech_ratio, 3),
            valence=None if valence is None else round(valence, 3),
            arousal=None if arousal is None else round(arousal, 3),
            emotion_confidence=None if confidence is None else round(confidence, 2),
            emotion_staleness_s=None if staleness is None else round(staleness, 1),
            headcount_bucket=hc_bucket,
            headcount_confidence=None if hc_confidence is None else round(hc_confidence, 2),
            headcount_staleness_s=None if hc_staleness is None else round(hc_staleness, 1),
            energy=round(energy, 3),
            mood=mood,
            trend=self._trend.update(energy, now),
            playback_active=playback_active,
            playback_track_id=playback_track_id,
        )
