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
from .music import CleanBaseline, TrackSignatureStore, apply_correction, dominance
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
                    rescue_margin=config.headcount_rescue_margin,
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
        # Music-aware emotion (M6): per-track signatures — the measured
        # speech-over-music pull (primary) and the standalone response
        # (cold-start prior) — subtracted from speech readings.
        self._signatures = (
            TrackSignatureStore(
                config.music_signatures_path, min_refs=config.music_min_refs
            )
            if config.music_aware_enabled and config.emotion_enabled
            else None
        )
        # The room's emotion read absent music, for pull sampling.
        self._clean_baseline = CleanBaseline(config.music_baseline_tau_s)
        self._last_banked_at: float | None = None  # dedup per inference
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
        if self._signatures is not None:
            self._signatures.flush()

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
        # Music-aware (M6): speech windows get corrected by the playing
        # track's measured signature before smoothing; music-only playback
        # windows become reference taps that measure that signature.
        valence = arousal = confidence = staleness = None
        music_dominance = emotion_correction = None
        if self.emotion is not None:
            has_audio = window.size >= self.config.sample_rate  # >= 1s
            if raw_ratio >= self.config.emotion_min_speech_ratio and has_audio:
                self.emotion.submit(window, raw_ratio, now)
            elif (
                self._signatures is not None
                and playback_active
                and playback_track_id is not None
                and raw_ratio <= self.config.music_ref_max_speech_ratio
                and has_audio
            ):
                self.emotion.submit_reference(window, playback_track_id, now)
            if self._signatures is not None:
                ref = self.emotion.pop_reference()
                if ref is not None:
                    self._signatures.add_reference(*ref)
                if playback_active:
                    music_dominance = dominance(
                        measured.spectral_balance.get("high", 0.0),
                        self.config.music_dominance_lo,
                        self.config.music_dominance_hi,
                    )
            reading, staleness = self.emotion.latest(now)
            if reading is not None:
                v_inst, a_inst = reading.valence, reading.arousal
                confidence = reading.confidence
                if self._signatures is not None:
                    self._bank_evidence(
                        reading, staleness, playback_active,
                        playback_track_id, music_dominance, now,
                    )
                if music_dominance is not None and music_dominance > 0.0:
                    corrected = self._correct(
                        v_inst, a_inst, playback_track_id, music_dominance
                    )
                    if corrected is not None:
                        v_inst, a_inst, emotion_correction = corrected
                    else:
                        # Discount floor: no usable signature yet — the
                        # reading is blended room+song and we can't unblend
                        # it, so it arrives with less conviction.
                        confidence *= max(
                            0.0,
                            1.0 - self.config.music_discount_gamma * music_dominance,
                        )
                valence = self._ema_valence.update(v_inst, now)
                arousal = self._ema_arousal.update(a_inst, now)

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
        return self._publish(
            wall, loudness, activity, measured, speech_ratio, valence, arousal,
            confidence, staleness, hc_bucket, hc_confidence, hc_staleness,
            energy, now, playback_active, playback_track_id,
            music_dominance, emotion_correction,
        )

    def _bank_evidence(
        self,
        reading,
        staleness: float | None,
        playback_active: bool,
        playback_track_id: str | None,
        music_dominance: float | None,
        now: float,
    ) -> None:
        """Feed the clean baseline and the pull estimator from a RAW
        reading, once per inference (readings persist across ticks). The
        baseline learns the room absent music; while it is fresh, a
        speech-over-music reading measures the playing track's pull
        directly — the interaction, not the standalone response
        (additivity failed its 2026-07-11 test)."""
        if reading.at == self._last_banked_at:
            return
        fresh = staleness is not None and staleness <= (
            self.config.emotion_min_interval_s + self.config.hop_s
        )
        if not fresh:
            return
        self._last_banked_at = reading.at
        clean = not playback_active or (
            music_dominance is not None
            and music_dominance <= self.config.music_baseline_m_max
        )
        if clean:
            self._clean_baseline.update(reading.valence, reading.arousal, now)
            return
        if (
            playback_track_id is not None
            and music_dominance is not None
            and music_dominance >= self.config.music_pull_m_floor
        ):
            base = self._clean_baseline.get(
                now, self.config.music_baseline_max_age_s
            )
            if base is not None:
                self._signatures.add_pull_reference(
                    playback_track_id,
                    (reading.valence - base[0]) / music_dominance,
                    (reading.arousal - base[1]) / music_dominance,
                )

    def _correct(
        self,
        v_inst: float,
        a_inst: float,
        playback_track_id: str | None,
        m: float,
    ) -> tuple[float, float, dict] | None:
        """Subtract the playing track's pull. Basis order: the measured
        pull signature, else the standalone response scaled by the
        gate-measured super-additivity ratios (cold start), else None —
        the caller falls back to the confidence discount."""
        sig = self._signatures.lookup(playback_track_id)
        if sig is None:
            return None
        if sig.pull_refs >= self._signatures.min_refs:
            basis, pv, pa, refs = "pull", sig.pull_valence, sig.pull_arousal, sig.pull_refs
            scale_v, scale_a = self.config.music_beta_v, self.config.music_beta_a
        elif sig.refs >= self._signatures.min_refs:
            basis, pv, pa, refs = "standalone", sig.valence, sig.arousal, sig.refs
            scale_v = self.config.music_standalone_scale_v
            scale_a = self.config.music_standalone_scale_a
        else:
            return None
        v, a, dv, da = apply_correction(
            v_inst, a_inst, pv, pa, m, scale_v, scale_a,
            self.config.music_max_correction,
        )
        return v, a, {
            "valence": round(dv, 3),
            "arousal": round(da, 3),
            "track_id": playback_track_id,
            "basis": basis,
            "refs": refs,
        }

    def _publish(
        self, wall, loudness, activity, measured, speech_ratio, valence,
        arousal, confidence, staleness, hc_bucket, hc_confidence,
        hc_staleness, energy, now, playback_active, playback_track_id,
        music_dominance, emotion_correction,
    ) -> RoomState:
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
            noise_floor_dbfs=(
                None
                if self._noise_floor.value is None
                else round(self._noise_floor.value, 1)
            ),
            emotion_music_dominance=(
                None if music_dominance is None else round(music_dominance, 3)
            ),
            emotion_correction=emotion_correction,
        )
