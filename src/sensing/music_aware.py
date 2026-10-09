"""Music-aware correction (M6), as an engine-thread collaborator (M8-01).

The engine used to do this inline in its tick. It is the M6 pull estimator's
wiring: given the newest emotion reading and the tick's playback context,
bank the reading as evidence, then correct it or discount it. Pulled out of
the tick so it can be tested offline. It was extracted with no change in
behavior. `tests/test_music_aware_characterization.py` proves that against a
frozen copy of the pre-extraction engine, byte for byte.

What lives here: freshness, dedup per inference, clean/mixed
classification, the clean baseline, pull-sample banking, basis selection
(pull, else standalone, else none), the correction, and the discount floor.
What stays in the engine: worker submission, reference taps
(`submit_reference` / `pop_reference` → `TrackSignatureStore.add_reference`),
the dominance computation from the DSP frame, the V/A EMAs and publishing.
The engine also keeps ownership of the signature store and flushes it on
stop; this class only borrows it.

Context binding (M8-03, AUDIT finding 2). The engine feeds the latest
reading to the EMAs on every tick until a new inference lands; that re-feed
is the smoothing design. Before M8-03 each tick corrected the reading with
*that tick's* track and dominance. So across a track boundary, track A's
reading was corrected with B's signature, and when playback stopped, a
reading taken over music fed the EMAs uncorrected. Now the playback context
`(playback_active, track_id, dominance)` is captured on the tick where a
reading first appears, keyed by `reading.at`, and reused for that reading's
whole life: banking, correction and the discount floor. The published
`emotion_music_dominance` stays the live per-tick measurement (founder
choice (a), 2026-10-09); `emotion_correction.track_id` always names the
track the correction was computed against. The worker never learns about
playback; capture happens here, at the tick.

Engine-thread only. `TrackSignatureStore` and `CleanBaseline` are
engine-thread-only by contract, unlocked, so `process()` is called from the
tick and never from a worker.

Primitives (`dominance`, `apply_correction`, the store, the baseline) and
the measurements behind them are documented in `music.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .emotion import EmotionReading
from .music import CleanBaseline, TrackSignatureStore, apply_correction


@dataclass(frozen=True)
class CorrectedReading:
    """One tick's emotion values, ready for the EMAs. `correction` is the
    published `emotion_correction` dict, or None when nothing was
    subtracted."""

    valence: float
    arousal: float
    confidence: float
    correction: dict | None


class MusicAwareCorrector:
    def __init__(self, config: Config, signatures: TrackSignatureStore):
        self._config = config
        self._signatures = signatures
        # The room's emotion read absent music, for pull sampling.
        self._clean_baseline = CleanBaseline(config.music_baseline_tau_s)
        self._last_banked_at: float | None = None  # dedup per inference
        # M8-03: the playback context of the reading currently being fed,
        # captured when it first appeared. One slot suffices: readings are
        # latest-wins, so a superseded reading never comes back.
        self._context_at: float | None = None
        self._context: tuple[bool, str | None, float | None] = (False, None, None)

    def process(
        self,
        reading: EmotionReading,
        staleness: float | None,
        playback_active: bool,
        playback_track_id: str | None,
        music_dominance: float | None,
        now: float,
    ) -> CorrectedReading:
        """Bank the raw reading, then correct it by its track's signature,
        or, with no usable signature, discount its confidence. The context
        arguments are this tick's; they are captured only on the tick where
        `reading` first appears and reused after that (M8-03).
        `music_dominance` is None whenever playback is off. Banking comes
        first, so a pull sample banked this tick already counts toward the
        basis this tick."""
        if reading.at != self._context_at:
            self._context_at = reading.at
            self._context = (playback_active, playback_track_id, music_dominance)
        playback_active, playback_track_id, music_dominance = self._context
        self._bank(
            reading, staleness, playback_active, playback_track_id,
            music_dominance, now,
        )
        v_inst, a_inst = reading.valence, reading.arousal
        confidence = reading.confidence
        correction = None
        if music_dominance is not None and music_dominance > 0.0:
            corrected = self._correct(
                v_inst, a_inst, playback_track_id, music_dominance
            )
            if corrected is not None:
                v_inst, a_inst, correction = corrected
            else:
                # Discount floor: no usable signature yet — the reading is
                # blended room+song and we can't unblend it, so it arrives
                # with less conviction.
                confidence *= max(
                    0.0,
                    1.0 - self._config.music_discount_gamma * music_dominance,
                )
        return CorrectedReading(v_inst, a_inst, confidence, correction)

    def _bank(
        self,
        reading: EmotionReading,
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
        # Dedup keys on the inference time, never the tick time.
        if reading.at == self._last_banked_at:
            return
        # Read from config on every call, not stored at construction (M8-01
        # charter, trap 2).
        fresh = staleness is not None and staleness <= (
            self._config.emotion_min_interval_s + self._config.hop_s
        )
        if not fresh:
            return
        self._last_banked_at = reading.at
        clean = not playback_active or (
            music_dominance is not None
            and music_dominance <= self._config.music_baseline_m_max
        )
        if clean:
            self._clean_baseline.update(reading.valence, reading.arousal, now)
            return
        if (
            playback_track_id is not None
            and music_dominance is not None
            and music_dominance >= self._config.music_pull_m_floor
        ):
            base = self._clean_baseline.get(
                now, self._config.music_baseline_max_age_s
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
            scale_v, scale_a = self._config.music_beta_v, self._config.music_beta_a
        elif sig.refs >= self._signatures.min_refs:
            basis, pv, pa, refs = "standalone", sig.valence, sig.arousal, sig.refs
            scale_v = self._config.music_standalone_scale_v
            scale_a = self._config.music_standalone_scale_a
        else:
            return None
        v, a, dv, da = apply_correction(
            v_inst, a_inst, pv, pa, m, scale_v, scale_a,
            self._config.music_max_correction,
        )
        return v, a, {
            "valence": round(dv, 3),
            "arousal": round(da, 3),
            "track_id": playback_track_id,
            "basis": basis,
            "refs": refs,
        }
