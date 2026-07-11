"""Unit tests for the pure-logic core: derivations, smoothing, trend, buffer."""

import numpy as np
import pytest

from sensing.audio import RingBuffer
from sensing.dsp import analyze
from sensing.state import Ema, TrendTracker, energy_score, mood_quadrant


class TestMoodQuadrant:
    @pytest.mark.parametrize(
        "valence, arousal, expected",
        [
            (0.5, 0.5, "excited"),
            (-0.5, 0.5, "tense"),
            (0.5, -0.5, "chill"),
            (-0.5, -0.5, "flat"),
        ],
    )
    def test_quadrants(self, valence, arousal, expected):
        assert mood_quadrant(valence, arousal) == expected


class TestEnergyScore:
    def test_bounds(self):
        assert 0.0 <= energy_score(-120.0, 0.0, 0.0, None) <= 1.0
        assert 0.0 <= energy_score(0.0, 10.0, 1.0, 1.0) <= 1.0

    def test_silence_is_low_loud_room_is_high(self):
        quiet = energy_score(-70.0, 0.0, 0.0, None)
        loud = energy_score(-15.0, 3.0, 0.9, 0.8)
        assert quiet < 0.1
        assert loud > 0.8

    def test_missing_arousal_renormalizes(self):
        # Same inputs with/without arousal at its neutral midpoint (0.0 -> 0.5
        # normalised) should be close, not depressed by a zero-filled term.
        with_neutral = energy_score(-30.0, 2.0, 0.5, 0.0)
        without = energy_score(-30.0, 2.0, 0.5, None)
        assert abs(with_neutral - without) < 0.1


class TestEma:
    def test_first_value_passthrough(self):
        ema = Ema(tau_s=5.0)
        assert ema.update(10.0, t=0.0) == 10.0

    def test_converges_toward_input(self):
        ema = Ema(tau_s=2.0)
        ema.update(0.0, t=0.0)
        for i in range(1, 30):
            value = ema.update(1.0, t=i * 2.0)
        assert value == pytest.approx(1.0, abs=0.01)

    def test_smooths_spikes(self):
        ema = Ema(tau_s=10.0)
        ema.update(0.0, t=0.0)
        spiked = ema.update(100.0, t=2.0)
        assert spiked < 25.0


class TestTrendTracker:
    def test_stable_with_little_data(self):
        tracker = TrendTracker(horizon_s=60.0, slope_threshold_per_min=0.1)
        assert tracker.update(0.5, t=0.0) == "stable"
        assert tracker.update(0.9, t=2.0) == "stable"

    def test_rising(self):
        tracker = TrendTracker(horizon_s=60.0, slope_threshold_per_min=0.1)
        result = None
        for i in range(20):
            result = tracker.update(0.2 + i * 0.02, t=i * 2.0)  # +0.6/min
        assert result == "rising"

    def test_falling(self):
        tracker = TrendTracker(horizon_s=60.0, slope_threshold_per_min=0.1)
        result = None
        for i in range(20):
            result = tracker.update(0.8 - i * 0.02, t=i * 2.0)
        assert result == "falling"

    def test_flat_is_stable(self):
        tracker = TrendTracker(horizon_s=60.0, slope_threshold_per_min=0.1)
        result = None
        for i in range(20):
            result = tracker.update(0.5, t=i * 2.0)
        assert result == "stable"


class TestRingBuffer:
    def test_read_last_returns_newest(self):
        ring = RingBuffer(10)
        ring.write(np.arange(8, dtype=np.float32))
        assert ring.read_last(3).tolist() == [5.0, 6.0, 7.0]

    def test_wraparound(self):
        ring = RingBuffer(10)
        ring.write(np.arange(15, dtype=np.float32))
        assert ring.read_last(4).tolist() == [11.0, 12.0, 13.0, 14.0]

    def test_read_since_tracks_position(self):
        ring = RingBuffer(100)
        ring.write(np.arange(10, dtype=np.float32))
        data, pos = ring.read_since(0)
        assert data.size == 10 and pos == 10
        ring.write(np.arange(10, 15, dtype=np.float32))
        data, pos = ring.read_since(pos)
        assert data.tolist() == [10.0, 11.0, 12.0, 13.0, 14.0]
        assert pos == 15

    def test_read_since_when_lapped(self):
        ring = RingBuffer(10)
        ring.write(np.arange(25, dtype=np.float32))
        data, pos = ring.read_since(0)  # reader fell 25 behind; only 10 remain
        assert data.tolist() == list(range(15, 25))
        assert pos == 25


class TestDsp:
    def test_silence(self):
        result = analyze(np.zeros(16_000, dtype=np.float32), 16_000)
        assert result.rms_dbfs == -120.0
        assert result.onset_density == 0.0

    def test_full_scale_sine_is_near_minus_3dbfs(self):
        t = np.arange(16_000) / 16_000
        sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        result = analyze(sine, 16_000)
        assert result.rms_dbfs == pytest.approx(-3.0, abs=0.1)

    def test_spectral_balance_localizes_low_tone(self):
        t = np.arange(16_000 * 2) / 16_000
        low_tone = np.sin(2 * np.pi * 100 * t).astype(np.float32)
        result = analyze(low_tone, 16_000)
        assert result.spectral_balance["low"] > 0.9

    def test_onsets_detected_in_click_train(self):
        window = np.zeros(16_000 * 4, dtype=np.float32)
        window[:: 16_000] = 1.0  # one click per second
        result = analyze(window, 16_000)
        assert result.onset_density > 0.5

    def test_empty_window(self):
        result = analyze(np.empty(0, dtype=np.float32), 16_000)
        assert result.rms_dbfs == -120.0


class TestPlaybackAwareness:
    def test_defaults_off_and_serialized(self):
        """M4 deliverable 3: RoomState carries playback awareness; absent a
        playback source the stamps default to inactive, and both fields ride
        to_dict so every downstream artifact is tagged."""
        from conftest import make_state

        state = make_state()
        assert state.playback_active is False
        assert state.playback_track_id is None
        d = state.to_dict()
        assert d["playback_active"] is False
        assert d["playback_track_id"] is None

        tagged = make_state(playback_active=True, playback_track_id="track-9")
        assert tagged.to_dict()["playback_track_id"] == "track-9"

    def test_noise_floor_defaults_none_and_serializes(self):
        """M5 observability: the rolling quiescent-window floor rides
        RoomState (None until the EMA seeds), so floor-relative terms are
        auditable from any frame."""
        from conftest import make_state

        assert make_state().noise_floor_dbfs is None
        seeded = make_state(noise_floor_dbfs=-44.3)
        assert seeded.to_dict()["noise_floor_dbfs"] == -44.3

    def test_music_aware_fields_default_off_and_serialize(self):
        """M6: dominance and the applied correction ride RoomState so raw
        emotion is reconstructable from any frame."""
        from conftest import make_state

        state = make_state()
        assert state.emotion_music_dominance is None
        assert state.emotion_correction is None

        corrected = make_state(
            emotion_music_dominance=0.83,
            emotion_correction={
                "valence": 0.4, "arousal": 0.5, "track_id": "t1", "refs": 4,
            },
        )
        d = corrected.to_dict()
        assert d["emotion_music_dominance"] == 0.83
        assert d["emotion_correction"]["track_id"] == "t1"


class TestVadCertificationThreshold:
    def test_read_time_threshold_override(self):
        """Contamination gate v1 (M4): the gate stores raw per-chunk
        probabilities, so stricter certification during playback is a
        read-time decision — same audio, same model state, higher bar."""
        from sensing.vad import VadGate

        gate = VadGate(16_000, window_s=5.0, threshold=0.5)
        gate._probs.extend([0.6, 0.8, 0.4, 0.9])  # injected: no model in tests
        assert gate.speech_ratio() == pytest.approx(0.75)
        assert gate.speech_ratio(0.75) == pytest.approx(0.5)
        assert int(gate.speech_mask().sum()) == 3
        assert int(gate.speech_mask(0.75).sum()) == 2
