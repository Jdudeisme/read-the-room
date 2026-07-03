"""Unit tests for the headcount layer's pure logic (no torch/model required).

The regression tier at the top encodes the 2020 prototype's failure modes at
the logic level; live-audio versions of the same cases are in the M2 test
plan (docs/M2-TEST-PLAN.md) and run on the demo machine.
"""

import numpy as np
import pytest

from sensing.headcount import (
    VAD_CHUNK,
    BucketSmoother,
    HeadcountEstimator,
    agglomerative_cluster,
    separation_score,
    speech_segments,
)
from sensing.state import BUCKET_LADDER, HeadcountBucket, bucket_from_log2


def _synthetic_speakers(n_speakers: int, per_speaker: int, seed: int = 0, spread: float = 0.05):
    """Well-separated unit-norm embedding clusters, one per 'speaker'."""
    rng = np.random.default_rng(seed)
    centers = rng.standard_normal((n_speakers, 192))
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    rows = []
    for c in centers:
        pts = c + spread * rng.standard_normal((per_speaker, 192))
        pts /= np.linalg.norm(pts, axis=1, keepdims=True)
        rows.append(pts)
    return np.vstack(rows).astype(np.float32)


# ---------------------------------------------------------------------------
# Regression tier: the 2020 failure modes, at the logic level
# ---------------------------------------------------------------------------


class Test2020Regressions:
    def test_solo_speaker_with_fragmenting_singletons_counts_as_one(self):
        """The phantom-speaker bug: a lone speaker whose embeddings fragment
        (one heavy cluster + stray singletons) must read as 1, not 8."""
        rng = np.random.default_rng(1)
        main = _synthetic_speakers(1, 12, seed=1)
        strays = rng.standard_normal((3, 192)).astype(np.float32)
        strays /= np.linalg.norm(strays, axis=1, keepdims=True)
        est = HeadcountEstimator()
        est.add(np.vstack([main, strays]), [1.25] * 15, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
        assert result is not None
        assert result.raw_clusters == 1

    def test_silence_produces_no_estimate_not_a_phantom_count(self):
        """Empty buffer (pure silence session) -> None, never a number."""
        est = HeadcountEstimator()
        assert est.estimate(speech_ratio=0.0, loudness_dbfs=-80.0) is None

    def test_silence_after_speech_freezes_evidence(self):
        """Clustering runs only when new embeddings arrive; during silence
        (no add() calls) repeated estimates are identical — the bucket holds
        upstream and staleness does the reporting."""
        est = HeadcountEstimator()
        est.add(_synthetic_speakers(2, 8), [1.25] * 16, now=0.0)
        first = est.estimate(speech_ratio=0.6, loudness_dbfs=-30.0)
        second = est.estimate(speech_ratio=0.6, loudness_dbfs=-30.0)
        assert first == second


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


class TestClustering:
    @pytest.mark.parametrize("n_speakers", [1, 2, 3, 4])
    def test_separable_speakers_counted_exactly(self, n_speakers):
        emb = _synthetic_speakers(n_speakers, 10)
        labels = agglomerative_cluster(emb, distance_threshold=0.40)
        assert len(np.unique(labels)) == n_speakers

    def test_no_fixed_max_cluster_count(self):
        """Design-time sanity check from the spec: nothing caps cluster count.
        20 separable clusters must come back as 20."""
        emb = _synthetic_speakers(20, 4, spread=0.02)
        labels = agglomerative_cluster(emb, distance_threshold=0.40)
        assert len(np.unique(labels)) == 20

    def test_empty_and_single(self):
        assert agglomerative_cluster(np.empty((0, 192), dtype=np.float32), 0.4).size == 0
        one = _synthetic_speakers(1, 1)
        assert agglomerative_cluster(one, 0.4).tolist() == [0]

    def test_separation_score_high_for_distinct_low_for_babble(self):
        distinct = _synthetic_speakers(3, 10, spread=0.02)
        labels_d = agglomerative_cluster(distinct, 0.40)
        rng = np.random.default_rng(2)
        babble = rng.standard_normal((30, 192)).astype(np.float32) * 0.01
        babble += rng.standard_normal(192).astype(np.float32)  # one smeared blob
        babble /= np.linalg.norm(babble, axis=1, keepdims=True)
        labels_b = agglomerative_cluster(babble, 0.40)
        assert separation_score(distinct, labels_d) > 0.5
        # A single smeared cluster has undefined/zero separation.
        assert separation_score(babble, labels_b) <= 0.2


# ---------------------------------------------------------------------------
# Segmentation from the VAD mask
# ---------------------------------------------------------------------------


class TestSpeechSegments:
    SR = 16_000

    def test_pure_silence_yields_nothing(self):
        window = np.random.default_rng(0).standard_normal(5 * self.SR).astype(np.float32)
        mask = np.zeros(5 * self.SR // VAD_CHUNK, dtype=bool)
        assert speech_segments(window, mask, self.SR) == []

    def test_short_blips_are_skipped(self):
        """Sub-min_run speech runs (a cough, a clink) produce no segments."""
        window = np.ones(5 * self.SR, dtype=np.float32)
        mask = np.zeros(5 * self.SR // VAD_CHUNK, dtype=bool)
        mask[10:20] = True  # ~0.32s run, under the 0.75s minimum
        assert speech_segments(window, mask, self.SR) == []

    def test_full_speech_window_yields_overlapping_segments(self):
        window = np.ones(5 * self.SR, dtype=np.float32)
        mask = np.ones(5 * self.SR // VAD_CHUNK, dtype=bool)
        segs = speech_segments(window, mask, self.SR)
        assert 3 <= len(segs) <= 6  # cost bound
        assert all(s.size == int(1.25 * self.SR) for s in segs)

    def test_mask_shorter_than_window_aligns_to_end(self):
        """Early in a session the VAD history is shorter than the window; the
        mask must align to the window's END, not its start."""
        window = np.zeros(5 * self.SR, dtype=np.float32)
        window[-self.SR :] = 1.0  # speech content only in the last second
        mask = np.ones(self.SR // VAD_CHUNK, dtype=bool)  # 1s of history
        segs = speech_segments(window, mask, self.SR)
        assert segs and all(np.all(s == 1.0) for s in segs)


# ---------------------------------------------------------------------------
# Bucket ladder + smoothing
# ---------------------------------------------------------------------------


class TestBucketLadder:
    def test_ladder_is_computed_not_enumerated(self):
        """Every rung reachable by round(log2), CROWD past the end — the
        design guarantee that 256+ needs no restructure."""
        import math

        for i, bucket in enumerate(BUCKET_LADDER):
            assert bucket_from_log2(float(i)) is bucket
        assert bucket_from_log2(math.log2(600)) is HeadcountBucket.FIVE_TWELVE
        assert bucket_from_log2(math.log2(2000)) is HeadcountBucket.CROWD
        assert bucket_from_log2(-1.0) is HeadcountBucket.SOLO

    def test_geometric_midpoint_boundaries(self):
        # 4 vs 8 splits at 2^2.5 ~ 5.66 people.
        assert bucket_from_log2(2.49) is HeadcountBucket.FOUR
        assert bucket_from_log2(2.51) is HeadcountBucket.EIGHT


class TestBucketSmoother:
    def test_oscillation_around_midpoint_never_flaps(self):
        """Estimates bouncing across the 4/8 boundary each update must not
        flap the bucket — hysteresis requires hold_k consecutive agreements."""
        sm = BucketSmoother(tau_s=0.01, hold_k=3)  # tiny tau isolates hysteresis
        buckets = set()
        for i in range(30):
            log2_est = 2.4 if i % 2 == 0 else 2.6  # straddles the midpoint
            buckets.add(sm.update(log2_est, t=float(i * 2)))
        assert len(buckets) == 1

    def test_sustained_shift_crosses_after_hold_k(self):
        sm = BucketSmoother(tau_s=0.01, hold_k=3)
        assert sm.update(2.0, t=0.0) is HeadcountBucket.FOUR
        results = [sm.update(3.0, t=float(2 + i * 2)) for i in range(5)]
        assert results[0] is HeadcountBucket.FOUR  # held during hysteresis
        assert results[-1] is HeadcountBucket.EIGHT  # crossed after hold_k

    def test_ema_smooths_in_log2_space(self):
        sm = BucketSmoother(tau_s=20.0, hold_k=1)
        sm.update(0.0, t=0.0)
        sm.update(10.0, t=2.0)  # solo -> "1024 people" spike
        assert sm.smoothed_log2 < 2.0  # heavily damped


# ---------------------------------------------------------------------------
# Two-regime estimator
# ---------------------------------------------------------------------------


class TestRegimes:
    def test_small_counts_are_counts(self):
        for n in (1, 2, 4):
            est = HeadcountEstimator()
            est.add(_synthetic_speakers(n, 10, seed=n), [1.25] * (n * 10), now=0.0)
            result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
            assert result.raw_clusters == n
            assert result.crowd_weight < 0.5
            assert abs(result.log2_count - np.log2(n)) < 1.0

    def test_babble_pressure_is_monotone_in_the_crowd_regime(self):
        """Denser room (higher speech saturation + loudness) -> equal-or-higher
        estimate. Ordinal correctness is all the crowd regime promises."""
        blob = _synthetic_speakers(1, 60, seed=3, spread=0.055)  # smeared babble

        estimates = []
        for ratio, loud in [(0.5, -40.0), (0.8, -30.0), (1.0, -18.0)]:
            est = HeadcountEstimator()
            est.add(blob, [1.25] * 60, now=0.0)
            estimates.append(est.estimate(ratio, loud).log2_count)
        assert estimates == sorted(estimates)
        assert estimates[-1] > estimates[0] + 1.0  # actually exercised the path

    def test_smeared_babble_enters_crowd_regime_with_low_confidence(self):
        """Heavy overlap can collapse a crowd into ONE diffuse cluster —
        saturated + loud + high-dispersion must still read as crowd regime,
        never as a confident solo."""
        # Spread 0.055 -> pairwise cosine distance ~0.35: just under the 0.40
        # threshold, so AHC chains everything into ONE cluster whose internal
        # dispersion is far beyond real same-speaker tightness (~0.1-0.2).
        blob = _synthetic_speakers(1, 80, seed=4, spread=0.055)
        est = HeadcountEstimator()
        est.add(blob, [1.25] * 80, now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert result.crowd_weight > 0.5
        assert result.confidence < 0.5

    def test_loud_continuous_solo_speaker_stays_in_counting_regime(self):
        """The counter-case: a podcaster talking nonstop at high volume has
        TIGHT embeddings — saturation alone must not flip the regime.

        Spread calibration: per-coordinate noise sigma yields cosine distance
        ~ sigma^2 * d in 192-dim space; 0.02 -> ~0.08, matching real ECAPA
        same-speaker segment distances (~0.1-0.2)."""
        tight = _synthetic_speakers(1, 20, seed=9, spread=0.02)
        est = HeadcountEstimator()
        est.add(tight, [1.25] * 20, now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert result.raw_clusters == 1
        assert result.crowd_weight < 0.5
        assert result.log2_count < 1.0  # still reads ~solo

    def test_buffer_evicts_by_time_and_cap(self):
        est = HeadcountEstimator(buffer_s=10.0, buffer_cap=5)
        est.add(_synthetic_speakers(1, 4, seed=5), [1.0] * 4, now=0.0)
        est.add(_synthetic_speakers(1, 4, seed=6), [1.0] * 4, now=20.0)  # evicts old
        assert est.evidence_s == pytest.approx(4.0)
        est.add(_synthetic_speakers(1, 4, seed=7), [1.0] * 4, now=21.0)  # cap: 5
        assert est.evidence_s == pytest.approx(5.0)
