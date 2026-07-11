"""Music-aware emotion (M6): dominance ramp, correction math, signature
store, and the emotion worker's speech-over-reference precedence. No
models — the worker is exercised at the job-slot level only."""

import json

import numpy as np
import pytest

from sensing.emotion import EmotionWorker
from sensing.music import (
    TrackSignature,
    TrackSignatureStore,
    apply_correction,
    dominance,
)


class TestDominance:
    LO, HI = 0.05, 0.30

    def test_corpus_derived_knots_separate_the_measured_cases(self):
        # 07-11 part (f): clean monotone sb_high 0.014-0.031 -> m == 0;
        # same voice over pop 0.257-0.484 -> m high.
        for clean in (0.014, 0.031, 0.05):
            assert dominance(clean, self.LO, self.HI) == 0.0
        assert dominance(0.257, self.LO, self.HI) == pytest.approx(0.828)
        for flooded in (0.30, 0.484, 0.77):
            assert dominance(flooded, self.LO, self.HI) == 1.0

    def test_linear_between_the_knots(self):
        assert dominance(0.175, self.LO, self.HI) == pytest.approx(0.5)

    def test_degenerate_knots_are_a_step(self):
        assert dominance(0.1, 0.2, 0.2) == 0.0
        assert dominance(0.3, 0.2, 0.2) == 1.0


class TestApplyCorrection:
    SIG = TrackSignature(valence=0.5, arousal=0.6, refs=5)

    def test_full_dominance_subtracts_the_signature(self):
        v, a, dv, da = apply_correction(0.3, 0.1, self.SIG, m=1.0, beta=1.0)
        assert (v, a) == pytest.approx((-0.2, -0.5))
        assert (dv, da) == pytest.approx((0.5, 0.6))

    def test_dominance_and_beta_scale_the_shift(self):
        v, a, dv, da = apply_correction(0.3, 0.1, self.SIG, m=0.5, beta=0.8)
        assert dv == pytest.approx(0.2)
        assert da == pytest.approx(0.24)
        assert v == pytest.approx(0.1)

    def test_shift_never_mute_a_mood_change_survives(self):
        # the same correction applied to two different raw readings keeps
        # their difference intact (away from the [-1,1] clamp) — the
        # positive-control property
        lo = apply_correction(-0.1, -0.3, self.SIG, m=1.0, beta=1.0)
        hi = apply_correction(0.5, 0.3, self.SIG, m=1.0, beta=1.0)
        assert hi[0] - lo[0] == pytest.approx(0.6)
        assert hi[1] - lo[1] == pytest.approx(0.6)

    def test_output_clamped_to_the_contract(self):
        strong = TrackSignature(valence=1.0, arousal=1.0, refs=5)
        v, a, _, _ = apply_correction(-0.9, -0.9, strong, m=1.0, beta=2.0)
        assert (v, a) == (-1.0, -1.0)


class TestTrackSignatureStore:
    def test_signature_untrusted_until_min_refs(self, tmp_path):
        store = TrackSignatureStore(tmp_path / "sig.json", min_refs=3)
        store.add_reference("t1", 0.4, 0.5)
        store.add_reference("t1", 0.4, 0.5)
        assert store.get("t1") is None
        store.add_reference("t1", 0.4, 0.5)
        sig = store.get("t1")
        assert sig is not None and sig.refs == 3
        assert sig.valence == pytest.approx(0.4)

    def test_running_mean_early(self, tmp_path):
        store = TrackSignatureStore(tmp_path / "sig.json", min_refs=1)
        for v in (0.2, 0.4, 0.6):
            store.add_reference("t1", v, 0.0)
        assert store.get("t1").valence == pytest.approx(0.4)

    def test_persistence_round_trip(self, tmp_path):
        path = tmp_path / "sig.json"
        store = TrackSignatureStore(path, min_refs=1)
        store.add_reference("t1", 0.3, -0.2)
        store.flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1

        reloaded = TrackSignatureStore(path, min_refs=1)
        sig = reloaded.get("t1")
        assert sig.valence == pytest.approx(0.3)
        assert sig.arousal == pytest.approx(-0.2)
        assert sig.refs == 1

    def test_corrupt_cache_starts_empty_never_crashes(self, tmp_path):
        path = tmp_path / "sig.json"
        path.write_text("{not json", encoding="utf-8")
        store = TrackSignatureStore(path, min_refs=1)
        assert store.get("t1") is None
        store.add_reference("t1", 0.1, 0.1)  # and it can still save over it

    def test_none_path_is_memory_only(self):
        store = TrackSignatureStore(None, min_refs=1)
        store.add_reference("t1", 0.1, 0.2)
        store.flush()  # no-op, no crash
        assert store.get("t1") is not None

    def test_unknown_track_and_none_id(self, tmp_path):
        store = TrackSignatureStore(tmp_path / "sig.json")
        assert store.get("nope") is None
        assert store.get(None) is None


def _worker() -> EmotionWorker:
    """A worker with the model never loaded: status forced ready, thread
    never started — only the job-slot logic is under test."""
    w = EmotionWorker("unused", min_interval_s=2.0)
    w.status = "ready"
    return w


WINDOW = np.zeros(16_000, dtype=np.float32)


class TestReferencePrecedence:
    def test_reference_never_displaces_a_pending_speech_job(self):
        w = _worker()
        w.submit(WINDOW, 0.8, now=100.0)
        w.submit_reference(WINDOW, "track-1", now=100.0)
        assert w._job[2] is None  # still the speech job

    def test_speech_displaces_a_pending_reference(self):
        w = _worker()
        w.submit_reference(WINDOW, "track-1", now=100.0)
        assert w._job[2] == "track-1"
        w.submit(WINDOW, 0.8, now=100.0)
        assert w._job[2] is None

    def test_newer_reference_replaces_older_reference(self):
        w = _worker()
        w.submit_reference(WINDOW, "track-1", now=100.0)
        w.submit_reference(WINDOW, "track-2", now=100.0)
        assert w._job[2] == "track-2"

    def test_reference_respects_the_shared_rate_limit(self):
        w = _worker()
        w._last_infer_at = 100.0
        w.submit_reference(WINDOW, "track-1", now=101.0)  # 1s < 2s interval
        assert w._job is None

    def test_pop_reference_is_once_only(self):
        w = _worker()
        w._reference = ("track-1", 0.4, 0.5)
        assert w.pop_reference() == ("track-1", 0.4, 0.5)
        assert w.pop_reference() is None

    def test_not_ready_drops_both_kinds(self):
        w = EmotionWorker("unused", min_interval_s=2.0)  # status "loading"
        w.submit(WINDOW, 0.8, now=100.0)
        w.submit_reference(WINDOW, "track-1", now=100.0)
        assert w._job is None
