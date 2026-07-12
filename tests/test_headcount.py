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


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _center_at(base: np.ndarray, cosine_dist: float, rng) -> np.ndarray:
    """A unit vector at an exact cosine distance from `base`. Fixtures place
    same-speaker debris at the MEASURED ~0.75 from its parent centroid and
    distinct voices at ~0.9 (docs/M7-PROPOSAL.md distance tables) — random
    independent centers are cross-voice geometry, not fragments."""
    u = rng.standard_normal(base.size)
    u -= (u @ base) * base
    u = _unit(u)
    cos = 1.0 - cosine_dist
    return _unit(cos * base + np.sqrt(max(0.0, 1.0 - cos**2)) * u)


def _cluster_at(center: np.ndarray, n: int, rng, spread: float = 0.02) -> np.ndarray:
    pts = center + spread * rng.standard_normal((n, center.size))
    return (pts / np.linalg.norm(pts, axis=1, keepdims=True)).astype(np.float32)


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

    def test_solo_speaker_with_heavy_fragments_counts_as_one(self):
        """The 2026-07-05 live finding: at buffer scale a solo speaker's
        far-tail fragments arrive as small CLUSTERS (2-3 segments), not
        singletons — the absolute min-mass floor alone counts each as a
        person. The proportional evidence floor must filter them, and the
        M7 rescue must decline them (fragments sit at the measured ~0.75
        from their parent centroid, inside the 0.80 rescue margin — random
        independent centers would be cross-voice geometry)."""
        rng = np.random.default_rng(2)
        dominant_c = _unit(rng.standard_normal(192))
        dominant = _cluster_at(dominant_c, 30, rng)
        frag_a = _cluster_at(_center_at(dominant_c, 0.74, rng), 3, rng)
        frag_b = _cluster_at(_center_at(dominant_c, 0.76, rng), 3, rng)
        est = HeadcountEstimator()
        est.add(np.vstack([dominant, frag_a, frag_b]), [1.25] * 36, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
        assert result is not None
        assert result.raw_clusters == 1  # 3/36 segments < 10% of evidence
        assert result.rescued_clusters == 0

    def test_two_balanced_speakers_both_pass_proportional_min_mass(self):
        """The proportional floor must not swallow real speakers: two voices
        splitting the talk time each hold ~50% of the evidence."""
        est = HeadcountEstimator()
        est.add(_synthetic_speakers(2, 10, seed=5, spread=0.02), [1.25] * 20, now=0.0)
        result = est.estimate(speech_ratio=0.6, loudness_dbfs=-30.0)
        assert result.raw_clusters == 2

    def test_default_threshold_tolerates_measured_same_voice_scatter(self):
        """Calibration regression (2026-07-05): same-voice ECAPA distances on
        1.25s segments measure ~0.35 mean / 0.47 p90 on CLEAN speech. One
        speaker at that scatter must cluster as one person at the default
        threshold. (The old 0.40 default sat inside this distribution and
        fragmented a solo speaker into phantom people.)"""
        emb = _synthetic_speakers(1, 40, seed=6, spread=0.055)  # ~0.35-0.45 dist
        est = HeadcountEstimator()
        est.add(emb, [1.25] * 40, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
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

    def test_separation_score_high_for_distinct_none_when_undefined(self):
        distinct = _synthetic_speakers(3, 10, spread=0.02)
        labels_d = agglomerative_cluster(distinct, 0.40)
        rng = np.random.default_rng(2)
        babble = rng.standard_normal((30, 192)).astype(np.float32) * 0.01
        babble += rng.standard_normal(192).astype(np.float32)  # one smeared blob
        babble /= np.linalg.norm(babble, axis=1, keepdims=True)
        labels_b = agglomerative_cluster(babble, 0.40)
        assert separation_score(distinct, labels_d) > 0.5
        # A single cluster has UNDEFINED separation — None, not 0.0. Reading
        # it as 0.0 (maximal collapse) was the M7-fixed sep_collapse misfire.
        assert separation_score(babble, labels_b) is None


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
# Contamination gate v1 (M4): floor-relative saturation during playback
# ---------------------------------------------------------------------------


class TestContaminationGateV1:
    def _smeared_estimator(self) -> HeadcountEstimator:
        """Babble confetti — heavy overlap fragmenting the buffer into
        mass-failing strays (fragmentation ~0.7, silhouette ~0), the crowd
        signature that still fires post-M7. (The old fixture's single blob
        at ~0.6 dispersion is mic-measured SOLO scatter, which M7's
        recalibrated ramp deliberately reads as counting regime — see
        TestM7StableMiddle.)"""
        est = HeadcountEstimator()
        est.add(_synthetic_speakers(1, 80, seed=4, spread=0.12), [1.25] * 80, now=0.0)
        return est

    def test_music_raised_floor_no_longer_false_fires(self):
        """The gating scenario: continuous music pins absolute loudness
        inside the [-45, -20] ramp for the whole session. With playback
        active and a seeded floor, loudness barely above that floor must
        contribute ~zero saturation — no phantom 16 from two people talking
        over the music. The same acoustics WITHOUT playback still fire the
        absolute ramp (the gate never changes clean-path behavior)."""
        est = self._smeared_estimator()
        contaminated = est.estimate(
            speech_ratio=1.0,
            loudness_dbfs=-15.0,
            playback_active=True,
            noise_floor_dbfs=-17.0,  # only +2 dB above the music's floor
        )
        assert contaminated.crowd_weight < 0.25
        clean = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert clean.crowd_weight > 0.5

    def test_real_crowd_rides_well_above_the_floor(self):
        """A packed talking room sits 10+ dB over whatever floor the music
        set — the crowd regime must still engage during playback."""
        est = self._smeared_estimator()
        result = est.estimate(
            speech_ratio=1.0,
            loudness_dbfs=-15.0,
            playback_active=True,
            noise_floor_dbfs=-32.0,  # +17 dB above floor
        )
        assert result.crowd_weight > 0.5

    def test_unseeded_floor_falls_back_to_absolute_ramp(self):
        """Playback starting before any quiescent window: no floor yet.
        Falling back to the absolute ramp keeps the babble path gated the
        old way rather than un-gating it entirely."""
        est = self._smeared_estimator()
        result = est.estimate(
            speech_ratio=1.0,
            loudness_dbfs=-15.0,
            playback_active=True,
            noise_floor_dbfs=None,
        )
        assert result.crowd_weight > 0.5


# ---------------------------------------------------------------------------
# Bucket ladder + smoothing
# ---------------------------------------------------------------------------


class TestBucketLadder:
    def test_ladder_is_computed_not_enumerated(self):
        """Every rung reachable from its nominal occupancy, CROWD past the
        end — the design guarantee that 256+ needs no restructure. M7
        ladder: 1,2,3,4,6,8 then powers of 2 (docs/M7-PROPOSAL.md)."""
        import math

        nominals = (1, 2, 3, 4, 6, 8, 16, 32, 64, 128, 256, 512, 1024)
        assert len(BUCKET_LADDER) == len(nominals)
        for n, bucket in zip(nominals, BUCKET_LADDER):
            assert bucket_from_log2(math.log2(n)) is bucket
        assert bucket_from_log2(math.log2(600)) is HeadcountBucket.FIVE_TWELVE
        assert bucket_from_log2(math.log2(2000)) is HeadcountBucket.CROWD
        assert bucket_from_log2(-1.0) is HeadcountBucket.SOLO

    def test_geometric_midpoint_boundaries(self):
        """Boundaries are geometric midpoints: pair/3 at ~2.45 people
        (log2 ~1.29), 3/4 at ~3.46 (~1.79), 4/6 at ~4.9 (~2.29), 6/8 at
        ~6.9 (~2.79). A true trio (log2 1.585) now sits mid-rung instead
        of ON the old pair/4 boundary — the M7 quantization fix."""
        assert bucket_from_log2(1.28) is HeadcountBucket.PAIR
        assert bucket_from_log2(1.30) is HeadcountBucket.THREE
        assert bucket_from_log2(1.585) is HeadcountBucket.THREE
        assert bucket_from_log2(1.78) is HeadcountBucket.THREE
        assert bucket_from_log2(1.80) is HeadcountBucket.FOUR
        assert bucket_from_log2(2.28) is HeadcountBucket.FOUR
        assert bucket_from_log2(2.30) is HeadcountBucket.SIX
        assert bucket_from_log2(2.78) is HeadcountBucket.SIX
        assert bucket_from_log2(2.80) is HeadcountBucket.EIGHT
        # The crowd cutover is unchanged from pre-M7 (log2(1024) + 0.5).
        assert bucket_from_log2(10.49) is HeadcountBucket.TEN_TWENTY_FOUR
        assert bucket_from_log2(10.51) is HeadcountBucket.CROWD


class TestBucketSmoother:
    def test_oscillation_around_midpoint_never_flaps(self):
        """Estimates bouncing across the 6/8 boundary each update must not
        flap the bucket — hysteresis requires hold_k consecutive agreements."""
        sm = BucketSmoother(tau_s=0.01, hold_k=3)  # tiny tau isolates hysteresis
        buckets = set()
        for i in range(30):
            log2_est = 2.7 if i % 2 == 0 else 2.9  # straddles the 6/8 midpoint
            buckets.add(sm.update(log2_est, t=float(i * 2)))
        assert len(buckets) == 1

    def test_intermittent_trio_settles_on_three(self):
        """The M7 uneven-trio profile: rescues are intermittent, so raw
        estimates mix log2(3) with log2(2) (mostly 3). The EMA must settle
        mid-rung on THREE — on the old ladder this exact stream sat on the
        pair/4 boundary and published pair all night (FIELD-NOTES
        2026-07-10; docs/M7-PROPOSAL.md honest residual)."""
        import math

        sm = BucketSmoother(tau_s=20.0, hold_k=3)
        stream = [3, 3, 3, 2] * 20  # 75% rescued-trio hops, 25% starved
        result = None
        for i, n in enumerate(stream):
            result = sm.update(math.log2(n), t=float(i * 4))
        assert result is HeadcountBucket.THREE
        assert 1.29 < sm.smoothed_log2 < 1.79  # mid-rung, not boundary-riding

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
        blob = _synthetic_speakers(1, 60, seed=3, spread=0.12)  # babble confetti

        estimates = []
        for ratio, loud in [(0.5, -40.0), (0.8, -30.0), (1.0, -18.0)]:
            est = HeadcountEstimator()
            est.add(blob, [1.25] * 60, now=0.0)
            estimates.append(est.estimate(ratio, loud).log2_count)
        assert estimates == sorted(estimates)
        assert estimates[-1] > estimates[0] + 1.0  # actually exercised the path

    def test_fragmenting_babble_enters_crowd_regime_with_low_confidence(self):
        """Heavy overlap destroys per-speaker structure — saturated + loud +
        fragmenting-into-strays must still read as crowd regime, never as a
        confident solo. (Spread 0.12 scatters past the 0.70 threshold: most
        evidence lands in mass-failing stray clusters, fragmentation ~0.7,
        silhouette ~0 — the confetti half of the smear signal. The
        single-blob ~0.6-dispersion variant is mic-measured solo scatter
        and now correctly stays counting regime: TestM7StableMiddle.)"""
        blob = _synthetic_speakers(1, 80, seed=4, spread=0.12)
        est = HeadcountEstimator()
        est.add(blob, [1.25] * 80, now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert result.crowd_weight > 0.5
        assert result.confidence < 0.5

    def test_loud_continuous_solo_speaker_stays_in_counting_regime(self):
        """The counter-case: a podcaster talking nonstop at high volume has
        TIGHT embeddings — saturation alone must not flip the regime.

        Spread calibration: per-coordinate noise sigma yields cosine distance
        ~ sigma^2 * d in 192-dim space; 0.02 -> ~0.08, an idealized tight
        speaker. (Measured clean same-voice scatter on 1.25s segments is
        ~0.35, which the [0.35, 0.70] dispersion ramp deliberately zeroes;
        noisier capture can push real solo speech up the ramp — a known
        limitation of the smear signal, mitigated by modest mic gain.)"""
        tight = _synthetic_speakers(1, 20, seed=9, spread=0.02)
        est = HeadcountEstimator()
        est.add(tight, [1.25] * 20, now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert result.raw_clusters == 1
        assert result.crowd_weight < 0.5
        assert result.log2_count < 1.0  # still reads ~solo

    def test_observability_fields_expose_raw_smear_signals(self):
        """M4 deliverable 3: dispersion/fragmentation ride the Estimate so a
        smoothed-bucket anomaly (the pool session's pair -> 16, FIELD-NOTES
        2026-07-06) is attributable from the log alone.

        Raw signals, pre-ramp: a tight solo speaker reads low dispersion and
        zero fragmentation; a mic-scatter blob reads its dispersion honestly
        (~0.6) even though the M7 ramp no longer treats that as smear."""
        tight = _synthetic_speakers(1, 20, seed=9, spread=0.02)
        est = HeadcountEstimator()
        est.add(tight, [1.25] * 20, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
        assert result.dispersion < 0.2
        assert result.fragmentation == 0.0

        blob = _synthetic_speakers(1, 80, seed=4, spread=0.09)
        est = HeadcountEstimator()
        est.add(blob, [1.25] * 80, now=0.0)
        smeared = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert smeared.dispersion > 0.5  # raw value published un-ramped

    def test_fragmentation_counts_mass_failing_stray_evidence(self):
        """Same inputs as the heavy-fragments regression above: two 3-segment
        far-tail fragments (at the measured ~0.75 parent distance, so the
        rescue declines them) fail min-mass — 6 of 36 segments are strays."""
        rng = np.random.default_rng(2)
        dominant_c = _unit(rng.standard_normal(192))
        dominant = _cluster_at(dominant_c, 30, rng)
        frag_a = _cluster_at(_center_at(dominant_c, 0.74, rng), 3, rng)
        frag_b = _cluster_at(_center_at(dominant_c, 0.76, rng), 3, rng)
        est = HeadcountEstimator()
        est.add(np.vstack([dominant, frag_a, frag_b]), [1.25] * 36, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
        assert result.raw_clusters == 1
        assert result.fragmentation == pytest.approx(6 / 36)

    def test_buffer_evicts_by_time_and_cap(self):
        est = HeadcountEstimator(buffer_s=10.0, buffer_cap=5)
        est.add(_synthetic_speakers(1, 4, seed=5), [1.0] * 4, now=0.0)
        est.add(_synthetic_speakers(1, 4, seed=6), [1.0] * 4, now=20.0)  # evicts old
        assert est.evidence_s == pytest.approx(4.0)
        est.add(_synthetic_speakers(1, 4, seed=7), [1.0] * 4, now=21.0)  # cap: 5
        assert est.evidence_s == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# M7: the stable middle (docs/M7-PROPOSAL.md) — fixtures model the MEASURED
# distance distributions (clean same-voice ~0.35, mic same-voice ~0.6,
# cross-voice ~0.9; FakeProvider lesson). Geometry helpers at top of file.
# ---------------------------------------------------------------------------


class TestM7StableMiddle:
    """The trio-evening failure pair: mic-scatter solos must not read as
    crowds (sep_collapse misfire + dispersion ramp), and low-airtime real
    voices must not be starved by the proportional evidence floor (rescue)."""

    def test_mic_scatter_solo_stays_counting_regime(self):
        """THE overcount fix. A solo speaker at mic-measured scatter (~0.6
        mean within-distance) talking loud and nonstop: pre-M7 the single
        cluster read separation 0.0 -> sep_collapse 1.0, and dispersion 0.6
        sat inside the [0.35, 0.70] smear ramp — an entire session published
        bucket 4 (offline repro; field: M4 part (d) phase 2b, pool pair->16).
        """
        blob = _synthetic_speakers(1, 80, seed=4, spread=0.09)  # disp ~0.60
        est = HeadcountEstimator()
        est.add(blob, [1.25] * 80, now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        assert result.raw_clusters == 1
        assert result.separation is None  # single cluster: undefined, not 0.0
        assert result.crowd_weight < 0.05
        assert result.log2_count < 0.5  # reads solo, not blended toward babble

    def test_merged_multivoice_blob_still_reads_collapsed(self):
        """The escape hatch the sep_collapse fix must not close: ONE
        indistinct cluster whose INTERNAL dispersion exceeds the clustering
        threshold (cross-voice pairs ~0.9 chained inside a merged blob)
        still reads as collapse through the recalibrated ramp."""
        rng = np.random.default_rng(11)
        base = _unit(rng.standard_normal(192))
        # A chain of overlapping voice-centers: neighbours ~0.5 apart merge
        # under average linkage, but end-to-end spread pushes internal
        # dispersion past the 0.70 threshold.
        centers, c = [base], base
        for _ in range(5):
            c = _center_at(c, 0.5, rng)
            centers.append(c)
        emb = np.vstack([_cluster_at(c, 12, rng, spread=0.05) for c in centers])
        est = HeadcountEstimator()
        est.add(emb, [1.25] * len(emb), now=0.0)
        result = est.estimate(speech_ratio=1.0, loudness_dbfs=-15.0)
        if result.separation is None:  # did merge into one cluster
            assert result.dispersion > est.cluster_threshold
            assert result.crowd_weight > 0.3

    def test_rescue_counts_distinct_low_airtime_voice(self):
        """THE undercount fix. Dominant speaker holds 91% of the buffered
        airtime; a real second voice (cross-voice distance ~0.9) holds 9% —
        under the 10% proportional floor, so pre-M7 it could not exist
        (FIELD-NOTES 2026-07-10: raw 1-2 all evening for a real trio; the
        starved cluster measured speaker-pure offline)."""
        rng = np.random.default_rng(12)
        dominant_c = _unit(rng.standard_normal(192))
        quiet_c = _center_at(dominant_c, 0.92, rng)
        est = HeadcountEstimator()
        est.add(_cluster_at(dominant_c, 40, rng), [1.25] * 40, now=0.0)
        est.add(_cluster_at(quiet_c, 4, rng), [1.25] * 4, now=1.0)  # 9% of 55s
        result = est.estimate(speech_ratio=0.7, loudness_dbfs=-30.0)
        assert result.raw_clusters == 2
        assert result.rescued_clusters == 1
        assert result.fragmentation == 0.0  # rescued evidence is not stray

    def test_rescue_declines_same_voice_debris(self):
        """The rescue must not resurrect the M2 ratchet bug: a far-tail
        fragment of the dominant speaker (past the 0.70 threshold, so it
        clusters separately, but hugging the parent centroid at ~0.75)
        stays debris. Margin 0.80 is calibrated between measured debris
        (median 0.73) and measured distinct voices (~0.82+)."""
        rng = np.random.default_rng(13)
        dominant_c = _unit(rng.standard_normal(192))
        debris_c = _center_at(dominant_c, 0.75, rng)
        est = HeadcountEstimator()
        est.add(_cluster_at(dominant_c, 40, rng), [1.25] * 40, now=0.0)
        est.add(_cluster_at(debris_c, 4, rng), [1.25] * 4, now=1.0)
        result = est.estimate(speech_ratio=0.7, loudness_dbfs=-30.0)
        assert result.raw_clusters == 1
        assert result.rescued_clusters == 0
        assert result.fragmentation > 0.0  # declined evidence stays stray

    def test_rescue_requires_a_passing_anchor(self):
        """No rescue when NOTHING passed the floors: an all-stray buffer is
        a solo/babble scatter signature, not a room full of hidden people —
        vacuous rescue would count same-voice confetti as a crowd."""
        blob = _synthetic_speakers(1, 80, seed=4, spread=0.12)  # all-stray
        est = HeadcountEstimator()
        est.add(blob, [1.25] * 80, now=0.0)
        result = est.estimate(speech_ratio=0.5, loudness_dbfs=-35.0)
        assert result.raw_clusters == 1
        assert result.rescued_clusters == 0

    def test_rescue_dedups_fragments_of_the_same_quiet_voice(self):
        """Two rescue-eligible clusters that are far from the dominant
        speaker but near EACH OTHER (~0.75 — one scattered quiet voice, not
        two people) count once: greedy acceptance requires clearing
        already-rescued centroids too."""
        rng = np.random.default_rng(14)
        dominant_c = _unit(rng.standard_normal(192))
        quiet_a = _center_at(dominant_c, 0.92, rng)
        # Near quiet_a (0.75: separate cluster, inside the rescue margin),
        # and constructed to also sit far from the dominant centroid.
        quiet_b = _center_at(quiet_a, 0.75, rng)
        if 1.0 - float(quiet_b @ dominant_c) < 0.85:  # keep the fixture honest
            quiet_b = _center_at(quiet_a, 0.75, np.random.default_rng(15))
        assert 1.0 - float(quiet_b @ dominant_c) >= 0.85
        est = HeadcountEstimator()
        est.add(_cluster_at(dominant_c, 60, rng), [1.25] * 60, now=0.0)
        est.add(_cluster_at(quiet_a, 4, rng), [1.25] * 4, now=1.0)
        est.add(_cluster_at(quiet_b, 3, rng), [1.25] * 3, now=2.0)
        result = est.estimate(speech_ratio=0.7, loudness_dbfs=-30.0)
        assert result.raw_clusters == 2  # dominant + ONE rescued voice
        assert result.rescued_clusters == 1

    def test_rescue_respects_the_absolute_floor(self):
        """A distinct-but-tiny cluster (1 segment / 1.25s) is not rescued:
        the strengthened absolute floor (segments AND seconds) holds. Solo
        sessions produce no rescue-eligible clusters at all (measured), so
        this floor is what keeps the rescue silent for solos."""
        rng = np.random.default_rng(16)
        dominant_c = _unit(rng.standard_normal(192))
        tiny_c = _center_at(dominant_c, 0.92, rng)
        est = HeadcountEstimator()
        est.add(_cluster_at(dominant_c, 40, rng), [1.25] * 40, now=0.0)
        est.add(_cluster_at(tiny_c, 1, rng), [1.25], now=1.0)
        result = est.estimate(speech_ratio=0.7, loudness_dbfs=-30.0)
        assert result.raw_clusters == 1
        assert result.rescued_clusters == 0

    def test_trio_with_two_quiet_voices_counts_three(self):
        """The gate-night target shape: one dominant voice, two distinct
        quiet voices (mutually ~0.9 apart) each under the proportional
        floor -> raw 3, and the log2 estimate lands in rung THREE's span."""
        rng = np.random.default_rng(17)
        d = _unit(rng.standard_normal(192))
        q1 = _center_at(d, 0.92, rng)
        q2 = _center_at(d, 0.90, rng)  # independent direction: ~0.9 from q1 too
        assert 1.0 - float(q1 @ q2) >= 0.80
        est = HeadcountEstimator()
        est.add(_cluster_at(d, 50, rng), [1.25] * 50, now=0.0)
        est.add(_cluster_at(q1, 4, rng), [1.25] * 4, now=1.0)
        est.add(_cluster_at(q2, 3, rng), [1.25] * 3, now=2.0)
        result = est.estimate(speech_ratio=0.7, loudness_dbfs=-30.0)
        assert result.raw_clusters == 3
        assert result.rescued_clusters == 2
        assert bucket_from_log2(result.log2_count) is HeadcountBucket.THREE

    def test_four_voice_separation_unbroken(self):
        """M3 regression bar: four balanced voices at the 0.10 proportional
        floor still count as four, no rescue involved."""
        est = HeadcountEstimator()
        est.add(_synthetic_speakers(4, 10, seed=18, spread=0.02), [1.25] * 40, now=0.0)
        result = est.estimate(speech_ratio=0.6, loudness_dbfs=-30.0)
        assert result.raw_clusters == 4
        assert result.rescued_clusters == 0
