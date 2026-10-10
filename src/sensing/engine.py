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
import platform
import socket
import threading
import time
from pathlib import Path
from typing import Protocol

from . import dsp
from .config import Config
from .emotion import EmotionWorker
from .headcount import BucketSmoother, HeadcountEstimator, HeadcountWorker
from .music import TrackSignatureStore, dominance
from .music_aware import MusicAwareCorrector
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


def capture_source_info(source, config: Config) -> dict:
    """Describe the capture path a measurement was made through.

    An M6 signature is the pull a track exerts on emotion readings *through
    one microphone on one machine* — reusing it behind a different capture
    path subtracts a correction that was never measured there. Stamped into
    the signature file so it is self-describing; resolved at save time
    because device_name is only populated once the stream opens.
    """
    return {
        "host": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()}",
        "capture": type(source).__name__,
        "input_device": (
            getattr(source, "device_name", "") or config.input_device or "default"
        ),
        "capture_rate": int(getattr(source, "capture_rate", config.sample_rate)),
        "stamped_at": time.time(),
    }


class Engine:
    def __init__(
        self,
        source,
        config: Config,
        consumers: list[Consumer],
        playback_source: PlaybackStateSource | None = None,
        reference_source=None,
        clean_source=None,
    ):
        self.source = source
        # M12-02 step 4: the mic with RTR's own playback cancelled
        # (sensing/audio.py CleanSource). When present, certification,
        # emotion and headcount read it; DSP measures stay on raw. If it
        # fails, the engine falls back to raw for good (invariant 7).
        self.clean = clean_source
        # M12-01c: the laptop's own playback (sensing/audio.py
        # ReferenceSource), started and stopped with the mic. Optional:
        # if it can't open, sensing runs exactly as without it (invariant
        # 7). The tick does not read it yet.
        self.reference = reference_source
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
                    rescue_enabled=config.headcount_rescue_enabled,
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
        self._signatures = self._signature_store(clean=self.clean is not None)
        # Banking, correction and the discount floor (M8-01: extracted from
        # the tick, behavior unchanged). Exists exactly when the store does,
        # which is also the only case where dominance is ever computed.
        self._music_aware = (
            MusicAwareCorrector(config, self._signatures)
            if self._signatures is not None
            else None
        )
        self._trend = TrendTracker(config.trend_horizon_s, config.trend_slope_threshold)
        self._vad_position = 0
        self._running = False
        # Idempotent shutdown (M8-07, AUDIT finding 5). run()'s finally and
        # the dashboard's main thread both call stop(). The first call does
        # the work; any other waits until it finishes, so the process never
        # exits mid-flush. Workers are not joined (daemon threads, by design).
        self._stop_lock = threading.Lock()
        self._stopped = False
        # Held around each tick in run(). stop() takes it for the final flush,
        # so the flush never overlaps a tick's own signature save. Contended
        # only at shutdown, where stop() waits at most one tick.
        self._tick_lock = threading.Lock()

    def _signature_store(self, clean: bool):
        """Music-aware signatures for the stream being analysed. A signature
        measures a track's pull through ONE capture path, so the clean path
        (playback cancelled) learns its own file, `<path>.clean<ext>`, and
        never borrows the raw path's. Raw-learned pulls would over-correct
        readings the canceller has already partly cleaned."""
        config = self.config
        if not (config.music_aware_enabled and config.emotion_enabled):
            return None
        path = config.music_signatures_path
        if clean and path:
            p = Path(path)
            path = str(p.with_name(f"{p.stem}.clean{p.suffix}"))
        return TrackSignatureStore(
            path,
            min_refs=config.music_min_refs,
            source_fn=lambda: capture_source_info(self.clean if clean else self.source, config),
        )

    def _fall_back_to_raw(self, why: str) -> None:
        """Stop analysing the clean stream: raw from here on, with the raw
        path's signatures."""
        log.error("playback cancellation off (%s); analysing the raw mic", why)
        clean, self.clean = self.clean, None
        if clean is not None:
            clean.stop()
        self._vad_position = self.source.ring.total_written
        if self._signatures is not None:
            self._signatures.flush()
        self._signatures = self._signature_store(clean=False)
        self._music_aware = (
            MusicAwareCorrector(self.config, self._signatures)
            if self._signatures is not None
            else None
        )

    def _analysis_source(self):
        if self.clean is not None and self.clean.status == "failed":
            self._fall_back_to_raw(f"clean source failed: {self.clean.error}")
        return self.clean if self.clean is not None else self.source

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
        if self.reference is not None:
            try:
                self.reference.start()
                log.info("playback reference from %r", self.reference.device_name)
            except Exception:
                log.exception("playback reference unavailable; continuing without it")
                self.reference = None
        if self.clean is not None:
            if self.reference is None:
                self._fall_back_to_raw("no playback reference")
            else:
                self.clean.start()
                log.info("analysing %r", self.clean.device_name)
        self._running = True
        ticks = 0
        next_tick = time.monotonic() + self.config.hop_s
        try:
            while self._running:
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_tick += self.config.hop_s
                with self._tick_lock:
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
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            self._running = False
            if self.clean is not None:
                self.clean.stop()
            self.source.stop()
            if self.reference is not None:
                self.reference.stop()
            if self.emotion is not None:
                self.emotion.stop()
            if self.headcount is not None:
                self.headcount.stop()
            if self._signatures is not None:
                with self._tick_lock:
                    self._signatures.flush()

    def _tick(self, now: float | None = None, wall: float | None = None) -> RoomState:
        # Optional clocks (M8-02, founder choice 2026-10-09): tests drive time
        # through the tick instead of patching time.monotonic, which the
        # workers also read on their own threads. Production passes nothing.
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        n_window = int(self.config.window_s * self.config.sample_rate)
        analysis = self._analysis_source()
        raw_window = self.source.ring.read_last(n_window)
        # M12-02: certification, emotion and headcount read `window` (clean
        # when cancellation runs); the DSP heartbeat reads raw (M12-04/06).
        window = raw_window if analysis is self.source else analysis.ring.read_last(n_window)

        # Layer 1: DSP heartbeat.
        measured = dsp.analyze(raw_window, self.config.sample_rate)
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
        new_samples, self._vad_position = analysis.ring.read_since(self._vad_position)
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
                if self._music_aware is not None:
                    out = self._music_aware.process(
                        reading, staleness, playback_active,
                        playback_track_id, music_dominance, now,
                    )
                    v_inst, a_inst = out.valence, out.arousal
                    confidence, emotion_correction = out.confidence, out.correction
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
            analysis_stream="raw" if analysis is self.source else "clean",
        )

    def _publish(
        self, wall, loudness, activity, measured, speech_ratio, valence,
        arousal, confidence, staleness, hc_bucket, hc_confidence,
        hc_staleness, energy, now, playback_active, playback_track_id,
        music_dominance, emotion_correction, analysis_stream="raw",
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
            analysis_stream=analysis_stream,
        )
