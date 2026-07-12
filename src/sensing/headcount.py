"""Headcount layer: ECAPA speaker embeddings + clustering, VAD-gated.

Milestone 2. Estimates room occupancy as power-of-2 buckets (see
state.HeadcountBucket). Design decisions, per the approved M2 proposal:

- **VAD-gated, structurally.** Embeddings are only ever computed over speech
  runs certified by the engine's single VadGate (the worker receives the
  window *plus* the per-chunk speech mask). This is the direct fix for the
  2020 prototype's phantom-speaker bug, where silence was clustered as if it
  were voices.
- **Rolling evidence buffer, not per-window counting.** A 5 s window yields
  3-6 sub-segments and can never distinguish more speakers than segments, so
  clustering runs over ~90 s of accumulated embeddings. Silence freezes the
  output (clustering runs only when new embeddings arrive) and staleness
  reports the age honestly — silence is absence of evidence, not evidence of
  an empty room.
- **Threshold agglomerative clustering, no fixed k.** There is deliberately
  no `max_speakers` constant anywhere in this module; the bucket ladder is
  computed (state.bucket_from_log2), not enumerated. This is the design-time
  guarantee that 256+ buckets never force a restructure.
- **Two regimes.** Up to ~8 separable speakers the estimate is a cluster
  count (with a minimum-mass criterion so a fragmenting solo speaker still
  reads as one). Beyond that, overlapping babble destroys per-speaker
  structure — no single-mic system counts a crowd — so the estimate blends
  toward a babble-density heuristic and confidence drops to a fixed low
  ceiling. Above ~16 the bucket is an ordinal "how crowded" signal, not a
  census.
- **The stable middle (M7, docs/M7-PROPOSAL.md).** Three calibration-level
  changes from the 2026-07-10 trio-evening evidence: undefined silhouette
  no longer reads as maximal collapse (the animated-solo/pair overcount);
  the dispersion smear ramp starts at the clustering threshold, because
  mic-measured same-voice scatter (~0.6) lived inside the old ramp; and a
  distinct-voice rescue counts low-airtime speakers whose (speaker-pure)
  clusters the proportional evidence floor would silently starve.
- **Latest-wins worker thread**, same pattern as emotion.EmotionWorker: a
  slow inference can never stall the DSP/VAD heartbeat, and jobs replace
  rather than queue.

Future media-contamination note (out of scope for M2, per spec): vocal music
will present as a stable phantom speaker — a recurring consistent embedding
cluster with plenty of mass. The eventual fix is a music-detection gate at
the engine's *centralized* certification point, upstream of both emotion and
headcount; nothing in this module does its own gating, so both layers will
inherit that fix for free.

Confirmed live (2026-07-03): the same hazard applies to TV/media *spoken*
dialogue, not just music — a solo viewer with the TV on had each new
on-screen voice (commercial cuts, multiple actors) correctly clustered and
counted as additional room occupants, which is correct behavior for the
algorithm but wrong for the "solo person wants ambient sensing of their own
room" use case. Revisit together with the music-detection gate above: once
the engine can recognize known/playing audio (e.g. to answer "what song is
this"), the same source-identification signal should suppress its speech
from headcount (and ideally emotion) certification, not just music vocals.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .state import HeadcountBucket, bucket_from_log2

# Raw log2 estimates kept on the reading for attribution (M5 observability).
RECENT_RAW_KEEP = 5

log = logging.getLogger(__name__)

VAD_CHUNK = 512  # samples per Silero chunk at 16 kHz; mask granularity


# --------------------------------------------------------------------------
# Segmentation: window + speech mask -> embedding-ready sub-segments
# --------------------------------------------------------------------------


def speech_segments(
    window: np.ndarray,
    speech_mask: np.ndarray,
    sample_rate: int,
    segment_s: float = 1.25,
    overlap: float = 0.5,
    min_run_s: float = 0.75,
    max_segments: int = 6,
) -> list[np.ndarray]:
    """Cut VAD-certified speech into overlapping sub-segments for embedding.

    `speech_mask` is a boolean array with one entry per VAD_CHUNK samples,
    aligned to the *end* of `window` (the VAD gate's rolling chunk history may
    be shorter than the window early in a session). Contiguous speech runs
    shorter than `min_run_s` are skipped; runs shorter than `segment_s` but at
    least `min_run_s` yield one segment. At most `max_segments` are returned
    (evenly thinned), bounding per-hop embedding cost.
    """
    if window.size == 0 or speech_mask.size == 0:
        return []

    covered = min(window.size, speech_mask.size * VAD_CHUNK)
    tail = window[window.size - covered :]
    mask = speech_mask[speech_mask.size - covered // VAD_CHUNK :]

    seg_len = int(segment_s * sample_rate)
    step = max(1, int(seg_len * (1.0 - overlap)))
    min_run = int(min_run_s * sample_rate)

    segments: list[np.ndarray] = []
    for run_start, run_end in _mask_runs(mask):
        start = run_start * VAD_CHUNK
        end = min(run_end * VAD_CHUNK, tail.size)
        run = tail[start:end]
        if run.size < min_run:
            continue
        if run.size <= seg_len:
            segments.append(run)
            continue
        pos = 0
        while pos + seg_len <= run.size:
            segments.append(run[pos : pos + seg_len])
            pos += step

    if len(segments) > max_segments:
        idx = np.linspace(0, len(segments) - 1, max_segments).round().astype(int)
        segments = [segments[i] for i in idx]
    return segments


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs in a boolean array, as (start, end) chunk indices."""
    if mask.size == 0:
        return []
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, edges.size, 2)]


# --------------------------------------------------------------------------
# ECAPA embedding (torch/speechbrain deferred so logic tests need numpy only)
# --------------------------------------------------------------------------


def load_ecapa(model_name: str, torch_threads: int = 0, os_truststore: bool = True):
    """Load the ECAPA-TDNN speaker-embedding model.

    Returns embed(segments: list of float32 mono @16 kHz) -> (n, D) float32
    array of L2-normalised embeddings, computed in ONE batched forward pass.
    Factored out so bench_headcount.py reuses it, mirroring emotion.load_model.
    """
    if os_truststore:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # pragma: no cover - best-effort
            log.warning("truststore injection failed; falling back to certifi")

    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)

    classifier = EncoderClassifier.from_hparams(
        source=model_name, run_opts={"device": "cpu"}
    )
    classifier.eval()

    def embed(segments: list[np.ndarray]) -> np.ndarray:
        if not segments:
            return np.empty((0, 0), dtype=np.float32)
        longest = max(s.size for s in segments)
        batch = torch.zeros(len(segments), longest)
        lengths = torch.ones(len(segments))
        for i, seg in enumerate(segments):
            batch[i, : seg.size] = torch.from_numpy(
                np.ascontiguousarray(seg, dtype=np.float32)
            )
            lengths[i] = seg.size / longest
        with torch.inference_mode():
            emb = classifier.encode_batch(batch, lengths).squeeze(1).cpu().numpy()
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        return (emb / np.maximum(norms, 1e-10)).astype(np.float32)

    return embed


# --------------------------------------------------------------------------
# Clustering: average-linkage agglomerative, cosine distance, threshold cut
# --------------------------------------------------------------------------


def agglomerative_cluster(
    embeddings: np.ndarray, distance_threshold: float
) -> np.ndarray:
    """Cluster L2-normalised embeddings; returns integer labels.

    Average-linkage agglomerative clustering over cosine distance with a
    threshold cut — the number of clusters is an *output*, never an input.
    O(n^3) worst case is irrelevant at the buffer cap (~200 points).
    """
    n = embeddings.shape[0]
    if n == 0:
        return np.empty(0, dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)

    # Cosine distance on unit vectors.
    dist = 1.0 - np.clip(embeddings @ embeddings.T, -1.0, 1.0)
    np.fill_diagonal(dist, np.inf)

    active = list(range(n))
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    d = dist.copy()

    while len(active) > 1:
        sub = np.ix_(active, active)
        block = d[sub]
        flat = int(np.argmin(block))
        i_pos, j_pos = divmod(flat, len(active))
        if block[i_pos, j_pos] >= distance_threshold:
            break
        a, b = active[i_pos], active[j_pos]
        na, nb = len(members[a]), len(members[b])
        # Lance-Williams update for average linkage.
        for k in active:
            if k in (a, b):
                continue
            merged = (na * d[a, k] + nb * d[b, k]) / (na + nb)
            d[a, k] = d[k, a] = merged
        members[a].extend(members.pop(b))
        active.remove(b)
        d[b, :] = d[:, b] = np.inf

    labels = np.empty(n, dtype=int)
    for label, root in enumerate(active):
        labels[members[root]] = label
    return labels


def separation_score(embeddings: np.ndarray, labels: np.ndarray) -> float | None:
    """Mean silhouette coefficient in [-1, 1]; None when undefined.

    Measures how cleanly the clustering separates: near 1 means distinct
    voices, near 0 means the embedding space has collapsed into babble.
    Undefined (single cluster / n < 3) is None, NOT 0.0 — M7: a lone tight
    cluster is a confident solo, and reading "undefined" as "maximally
    collapsed" was the sep_collapse misfire behind the animated-solo/pair
    overcounts (M4 part (d) phase 2b, the pool pair->16; reproduced offline
    as an entire solo session publishing bucket 4, docs/M7-PROPOSAL.md).
    """
    n = embeddings.shape[0]
    if n < 3 or len(np.unique(labels)) < 2:
        return None
    dist = 1.0 - np.clip(embeddings @ embeddings.T, -1.0, 1.0)
    scores = []
    for i in range(n):
        same = labels == labels[i]
        same[i] = False
        if not same.any():
            continue  # singleton: silhouette undefined
        a = float(dist[i, same].mean())
        b = min(
            float(dist[i, labels == other].mean())
            for other in np.unique(labels)
            if other != labels[i]
        )
        denom = max(a, b)
        if denom > 0:
            scores.append((b - a) / denom)
    return float(np.mean(scores)) if scores else 0.0


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 below `lo`, 1 above `hi`, linear between — a deadzoned clamp."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _mean_intra_cluster_distance(embeddings: np.ndarray, labels: np.ndarray) -> float:
    """Segment-weighted mean within-cluster cosine distance.

    Low (~0.1-0.25) for a single real voice; high for crowd babble whose
    embeddings smear diffusely even when they merge into one cluster.
    """
    dist = 1.0 - np.clip(embeddings @ embeddings.T, -1.0, 1.0)
    total = 0.0
    weight = 0
    for label in np.unique(labels):
        idx = np.flatnonzero(labels == label)
        if idx.size < 2:
            continue
        block = dist[np.ix_(idx, idx)]
        total += float(block.sum()) / (idx.size - 1)  # mean over off-diagonal, x size
        weight += idx.size
    return total / weight if weight else 0.0


# --------------------------------------------------------------------------
# Estimator: buffer -> (log2 occupancy estimate, confidence)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    log2_count: float  # continuous occupancy estimate, log2 people
    confidence: float  # 0..1
    raw_clusters: int  # mass-passing + rescued cluster count (diagnostic)
    crowd_weight: float  # 0 = pure count regime, 1 = pure babble regime
    # M4 observability (FIELD-NOTES 2026-07-06 debuggability gap): the raw
    # smear signals behind crowd_weight, pre-ramp, so a smoothed-bucket
    # anomaly is attributable from the log alone. The ramps are pure
    # functions of these plus config, so raw values lose nothing.
    dispersion: float  # segment-weighted mean within-cluster cosine distance
    fragmentation: float  # fraction of segments in mass-failing stray clusters
    # M7 observability: what the collapse/rescue logic actually saw, so a
    # trio night's undercount frames attribute themselves from the log.
    separation: float | None = None  # silhouette; None = undefined (1 cluster)
    rescued_clusters: int = 0  # how many of raw_clusters came through rescue


class HeadcountEstimator:
    """Rolling embedding buffer + two-regime occupancy estimation.

    Counting regime: min-mass cluster count. Crowd regime: babble-density
    heuristic. The blend weight shifts continuously with cluster separation
    and count so the estimate never jumps discontinuously at the boundary.
    """

    def __init__(
        self,
        buffer_s: float = 90.0,
        buffer_cap: int = 200,
        cluster_threshold: float = 0.70,
        min_cluster_segments: int = 2,
        min_cluster_speech_s: float = 2.5,
        min_cluster_evidence_frac: float = 0.10,
        count_reliable_max: int = 4,
        count_regime_max: int = 8,
        rescue_margin: float = 0.80,
    ):
        self.buffer_s = buffer_s
        self.buffer_cap = buffer_cap
        self.cluster_threshold = cluster_threshold
        self.min_cluster_segments = min_cluster_segments
        self.min_cluster_speech_s = min_cluster_speech_s
        self.min_cluster_evidence_frac = min_cluster_evidence_frac
        self.count_reliable_max = count_reliable_max
        self.count_regime_max = count_regime_max
        # M7 distinct-voice rescue: a cluster failing only the proportional
        # evidence floor still counts when its centroid sits at least this
        # cosine distance from every mass-passing (and already-rescued)
        # cluster centroid. Calibrated offline (docs/M7-PROPOSAL.md): a
        # dominant speaker's scatter debris hugs its parent centroid
        # (median 0.73), distinct low-airtime voices sit ~0.82; solos
        # produce no rescue-eligible clusters at all.
        self.rescue_margin = rescue_margin
        self._embeddings: list[np.ndarray] = []
        self._times: list[float] = []
        self._durations: list[float] = []

    def add(self, embeddings: np.ndarray, durations: list[float], now: float) -> None:
        for row, dur in zip(embeddings, durations):
            self._embeddings.append(row)
            self._times.append(now)
            self._durations.append(dur)
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self.buffer_s
        while self._times and (
            self._times[0] < cutoff or len(self._times) > self.buffer_cap
        ):
            self._embeddings.pop(0)
            self._times.pop(0)
            self._durations.pop(0)

    @property
    def evidence_s(self) -> float:
        """Total buffered speech seconds — how much the estimate rests on."""
        return float(sum(self._durations))

    def estimate(
        self,
        speech_ratio: float,
        loudness_dbfs: float,
        playback_active: bool = False,
        noise_floor_dbfs: float | None = None,
    ) -> Estimate | None:
        """Estimate occupancy from the current buffer. None if no evidence.

        `speech_ratio` and `loudness_dbfs` come from the engine's existing
        VAD/DSP layers and drive the crowd-regime babble heuristic — no new
        signal processing is duplicated here.

        Contamination gate v1 (M4): while `playback_active` and a noise
        floor exists, the saturation loudness term keys on loudness RELATIVE
        to that rolling floor instead of absolute dBFS. Continuous music
        pins absolute loudness inside the [-45, -20] ramp permanently, so
        the absolute form would false-fire for the whole session (the pool
        fan produced exactly this, FIELD-NOTES 2026-07-06); a real crowd
        still rides well above the floor the music itself set.
        """
        if not self._embeddings:
            return None

        emb = np.vstack(self._embeddings)
        labels = agglomerative_cluster(emb, self.cluster_threshold)
        durations = np.asarray(self._durations)

        # Minimum-mass criterion, two tiers. Absolute floor: a cluster needs
        # >= min_cluster_segments segments or >= min_cluster_speech_s of
        # attributed speech. Proportional floor: it must also hold >=
        # min_cluster_evidence_frac of ALL buffered speech. The absolute
        # floor alone fails at buffer scale — with ~100+ segments accumulated
        # over buffer_s, same-speaker embedding scatter (measured ~0.35 mean
        # pairwise cosine distance on clean audio, ~0.6 on a laptop mic)
        # reliably forms far-tail fragments of 2+ segments, and counting each
        # as a person ratchets a solo speaker up the bucket ladder as the
        # buffer fills. Requiring a fraction of total evidence scales with
        # the buffer: a solo speaker's debris stays debris, while real
        # additional speakers (>= ~10% of the talk time) still count.
        passing: list[int] = []
        failing: list[int] = []
        stray_segments = 0
        min_frac_s = self.min_cluster_evidence_frac * float(durations.sum())
        for label in np.unique(labels):
            in_cluster = labels == label
            cluster_s = float(durations[in_cluster].sum())
            if (
                int(in_cluster.sum()) >= self.min_cluster_segments
                or cluster_s >= self.min_cluster_speech_s
            ) and cluster_s >= min_frac_s:
                passing.append(int(label))
            else:
                failing.append(int(label))
                stray_segments += int(in_cluster.sum())

        # M7 distinct-voice rescue (docs/M7-PROPOSAL.md). The proportional
        # floor has a blind spot the 2026-07-10 trio evening exposed: with
        # one speaker holding the floor, a real third voice at < 10% of the
        # buffered airtime CANNOT exist — its (speaker-pure, measured)
        # cluster fails the frac floor forever. A cluster failing only the
        # proportional floor still counts when it passes a strengthened
        # absolute floor (segments AND speech seconds, not OR) and its
        # centroid sits >= rescue_margin from every counted cluster —
        # distinct voices measure ~0.9 apart, a dominant speaker's debris
        # hugs its parent. Guards: never rescue when nothing passed at all
        # (an all-stray buffer is a solo/babble signature, not hidden
        # people), and rescued clusters must also clear each other (two
        # fragments of the same starved voice count once), greedily in mass
        # order.
        rescued = 0
        if passing and failing:
            def _centroid(label: int) -> np.ndarray:
                c = emb[labels == label].mean(axis=0)
                return c / max(float(np.linalg.norm(c)), 1e-10)

            counted = [_centroid(lb) for lb in passing]
            eligible = []
            for label in failing:
                in_cluster = labels == label
                cluster_s = float(durations[in_cluster].sum())
                if (
                    int(in_cluster.sum()) >= self.min_cluster_segments
                    and cluster_s >= self.min_cluster_speech_s
                ):
                    eligible.append((cluster_s, label, int(in_cluster.sum())))
            for cluster_s, label, n_segs in sorted(eligible, reverse=True):
                c = _centroid(label)
                if all(
                    1.0 - float(np.dot(c, other)) >= self.rescue_margin
                    for other in counted
                ):
                    counted.append(c)
                    rescued += 1
                    stray_segments -= n_segs

        raw = max(len(passing) + rescued, 1)  # evidence exists: someone is here
        # Fraction of evidence stuck in mass-failing stray clusters. A solo
        # speaker sheds a few strays; crowd babble under heavy overlap can
        # fragment into ALL strays — a regime signal min-mass would otherwise
        # silently swallow.
        fragmentation = stray_segments / len(labels)

        separation = separation_score(emb, labels)

        # Crowd weight: rises when EITHER regime-collapse signature appears —
        #   (a) cluster count climbing past the reliable range toward the
        #       credible ceiling, or
        #   (b) saturated babble whose embeddings SMEAR: speech ratio pinned
        #       high AND loud AND (high within-cluster dispersion OR heavy
        #       fragmentation into mass-failing strays).
        # Signal (b) matters because heavy overlap collapses a crowd into
        # either one indistinct cluster (dispersion) or all-stray confetti
        # (fragmentation) — without it, a packed room could masquerade as a
        # confident solo.
        #
        # Every signal has a DEADZONE covering its normal counting-regime
        # range, so ordinary operation contributes exactly zero crowd weight:
        # a legitimate 4-person room, or a solo podcaster talking loudly
        # nonstop (tight embeddings, a few shed strays), blends 0% babble —
        # important because the babble target is large (up to 2^10) and even
        # small leaked weights would visibly inflate confident counts. Past
        # the deadzones everything is continuous, so the transition zone
        # (~8-16) blends rather than jumps.
        count_pressure = _ramp(raw, self.count_reliable_max, self.count_regime_max)
        if playback_active and noise_floor_dbfs is not None:
            # Floor-relative ramp: quiet conversation over music sits a few
            # dB above the floor the music set; a packed talking crowd sits
            # 10+ dB above it. Absent a seeded floor (session just started)
            # we fall back to the absolute ramp rather than un-gating.
            loud_term = _ramp(loudness_dbfs - noise_floor_dbfs, 3.0, 12.0)
        else:
            loud_term = _ramp(loudness_dbfs, -45.0, -20.0)
        saturation = _ramp(speech_ratio, 0.6, 0.95) * loud_term
        # M7 recalibration (docs/M7-PROPOSAL.md): mic-measured same-voice
        # scatter is ~0.6 mean — INSIDE the old [0.5*threshold, threshold]
        # ramp, so every ordinary session on real hardware carried a
        # standing dispersion signal (the animated-pair bucket-8 and an
        # offline solo session publishing 4 all night). Within-cluster
        # dispersion is only babble evidence once it exceeds the distance
        # at which the clusterer would have SPLIT the cluster — i.e. the
        # cluster plausibly holds more than one voice (cross-voice pairs
        # measure ~0.9 inside a merged blob).
        dispersion = _mean_intra_cluster_distance(emb, labels)
        dispersion_signal = _ramp(
            dispersion, self.cluster_threshold, 1.3 * self.cluster_threshold
        )
        # A solo speaker sheds up to ~20-30% strays; only heavier
        # fragmentation reads as babble confetti.
        smear = max(dispersion_signal, _ramp(fragmentation, 0.3, 0.8))
        # M7 sep_collapse fix: undefined separation (single cluster) is NOT
        # maximal collapse — it defers to the dispersion evidence. A tight
        # lone cluster reads collapse ~0 (confident solo); one indistinct
        # merged blob still reads collapsed through its internal dispersion.
        if separation is None:
            sep_collapse = dispersion_signal
        else:
            sep_collapse = 1.0 - max(0.0, min(1.0, separation / 0.25))
        crowd_weight = max(
            0.0, min(1.0, sep_collapse * max(count_pressure, saturation * smear))
        )

        # Babble-density heuristic: speech saturation x loudness, mapped
        # monotonically onto log2 occupancy 3..10 (8..1024 people). Ordinal
        # by construction — "denser room, higher bucket" — never a census.
        loud01 = max(0.0, min(1.0, (loudness_dbfs + 45.0) / 25.0))
        babble_pressure = max(0.0, min(1.0, speech_ratio)) * loud01
        log2_babble = 3.0 + 7.0 * babble_pressure

        log2_count = math.log2(raw)
        log2_est = (1.0 - crowd_weight) * log2_count + crowd_weight * log2_babble

        # Confidence: counting regime earns it from separation quality and
        # evidence volume; the crowd regime is capped low — the number itself
        # says "estimate, not count". Undefined separation contributes the
        # neutral 0.0 it always did (a lone cluster earns confidence from
        # evidence volume, not silhouette).
        evidence = min(1.0, self.evidence_s / 15.0)
        count_conf = evidence * max(0.2, min(1.0, 0.4 + (separation or 0.0)))
        conf = (1.0 - crowd_weight) * count_conf + crowd_weight * 0.25

        return Estimate(
            log2_count=log2_est,
            confidence=max(0.0, min(1.0, conf)),
            raw_clusters=raw,
            crowd_weight=crowd_weight,
            dispersion=dispersion,
            fragmentation=fragmentation,
            separation=separation,
            rescued_clusters=rescued,
        )


# --------------------------------------------------------------------------
# Smoothing: EMA in log2 space + hysteresis bucketizer
# --------------------------------------------------------------------------


class BucketSmoother:
    """EMA over log2 estimates, bucketized with hysteresis.

    EMA in log2 space makes 4->8 the same smoothing step as 64->128 — the
    natural companion to power-of-2 buckets. The published bucket changes only
    when the smoothed estimate rounds to a different rung for `hold_k`
    CONSECUTIVE updates, so one loud laugh never flaps the bucket.
    """

    def __init__(self, tau_s: float = 20.0, hold_k: int = 3):
        self.tau_s = tau_s
        self.hold_k = hold_k
        self._value: float | None = None
        self._last_t: float | None = None
        self._bucket: HeadcountBucket | None = None
        self._pending: HeadcountBucket | None = None
        self._pending_count = 0

    def update(self, log2_estimate: float, t: float) -> HeadcountBucket:
        if self._value is None or self._last_t is None:
            self._value = log2_estimate
        else:
            dt = max(1e-6, t - self._last_t)
            alpha = 1.0 - math.exp(-dt / self.tau_s)
            self._value += alpha * (log2_estimate - self._value)
        self._last_t = t

        candidate = bucket_from_log2(self._value)
        if self._bucket is None:
            self._bucket = candidate
        elif candidate != self._bucket:
            if candidate == self._pending:
                self._pending_count += 1
            else:
                self._pending = candidate
                self._pending_count = 1
            if self._pending_count >= self.hold_k:
                self._bucket = candidate
                self._pending = None
                self._pending_count = 0
        else:
            self._pending = None
            self._pending_count = 0
        return self._bucket

    @property
    def smoothed_log2(self) -> float | None:
        return self._value


# --------------------------------------------------------------------------
# Worker thread (EmotionWorker pattern: latest-wins slot, never a queue)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadcountReading:
    bucket: HeadcountBucket
    confidence: float  # 0..1
    raw_clusters: int
    crowd_weight: float
    dispersion: float  # raw estimator signals, see Estimate (M4 observability)
    fragmentation: float
    separation: float | None  # M7 observability: silhouette the collapse saw
    rescued_clusters: int  # M7: distinct-voice rescues inside raw_clusters
    smoothed_log2: float  # the BucketSmoother EMA value that produced `bucket`
    at: float  # time.monotonic() when the estimate finished
    # M5 observability (the pool session's pair -> 16 attribution gap): the
    # last few raw log2 estimates, newest last, so a smoothed-bucket anomaly
    # is attributable from a single frame even after the raw inputs subside.
    recent_raw_log2: tuple[float, ...] = ()


class HeadcountWorker:
    """Background thread owning ECAPA load + embed + cluster + smooth.

    Thread-safe interface: submit() from the engine tick, latest()/status
    from anywhere. During silence nothing is submitted, so the last bucket
    holds and its staleness grows — the estimator never manufactures a
    reading from the absence of speech.
    """

    def __init__(
        self,
        model_name: str,
        min_interval_s: float,
        estimator: HeadcountEstimator,
        smoother: BucketSmoother,
        sample_rate: int = 16_000,
        torch_threads: int = 0,
        os_truststore: bool = True,
    ):
        self._model_name = model_name
        self._min_interval_s = min_interval_s
        self._estimator = estimator
        self._smoother = smoother
        self._sample_rate = sample_rate
        self._torch_threads = torch_threads
        self._os_truststore = os_truststore
        # job = (window, speech_mask, speech_ratio, loudness_dbfs, now,
        #        playback_active, noise_floor_dbfs)
        self._job: (
            tuple[np.ndarray, np.ndarray, float, float, float, bool, float | None]
            | None
        ) = None
        self._job_event = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: HeadcountReading | None = None
        self._recent_raw: deque[float] = deque(maxlen=RECENT_RAW_KEEP)
        self._last_infer_at = -1e9
        self.status = "loading"  # loading | ready | failed | stopped
        self.error: str | None = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="headcount-worker"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._job_event.set()

    def submit(
        self,
        window: np.ndarray,
        speech_mask: np.ndarray,
        speech_ratio: float,
        loudness_dbfs: float,
        now: float,
        playback_active: bool = False,
        noise_floor_dbfs: float | None = None,
    ) -> None:
        """Offer a speech-certified window plus its VAD mask. Dropped if
        rate-limited or busy (latest-wins replacement, never a queue)."""
        if self.status != "ready":
            return
        if now - self._last_infer_at < self._min_interval_s:
            return
        with self._lock:
            self._job = (
                window.copy(),
                speech_mask.copy(),
                speech_ratio,
                loudness_dbfs,
                now,
                playback_active,
                noise_floor_dbfs,
            )
        self._job_event.set()

    def latest(self, now: float) -> tuple[HeadcountReading | None, float | None]:
        """(reading, staleness_seconds) — both None before the first result."""
        with self._lock:
            reading = self._latest
        if reading is None:
            return None, None
        return reading, max(0.0, now - reading.at)

    def _run(self) -> None:
        try:
            embed = load_ecapa(
                self._model_name, self._torch_threads, self._os_truststore
            )
        except Exception as exc:
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("ECAPA model failed to load")
            return
        self.status = "ready"
        while not self._stop.is_set():
            self._job_event.wait()
            self._job_event.clear()
            if self._stop.is_set():
                break
            with self._lock:
                job, self._job = self._job, None
            if job is None:
                continue
            window, mask, speech_ratio, loudness, submitted_at, pb_active, floor = job
            started = time.monotonic()
            try:
                segments = speech_segments(window, mask, self._sample_rate)
                if segments:
                    embeddings = embed(segments)
                    durations = [s.size / self._sample_rate for s in segments]
                    self._estimator.add(embeddings, durations, submitted_at)
                estimate = self._estimator.estimate(
                    speech_ratio, loudness, pb_active, floor
                )
            except Exception:
                log.exception("headcount estimation failed; window skipped")
                continue
            self._last_infer_at = time.monotonic()
            if estimate is None:
                continue
            bucket = self._smoother.update(estimate.log2_count, submitted_at)
            # update() always seeds the EMA, so smoothed_log2 is non-None here.
            smoothed_log2 = self._smoother.smoothed_log2
            self._recent_raw.append(estimate.log2_count)
            with self._lock:
                self._latest = HeadcountReading(
                    bucket=bucket,
                    confidence=estimate.confidence,
                    raw_clusters=estimate.raw_clusters,
                    crowd_weight=estimate.crowd_weight,
                    dispersion=estimate.dispersion,
                    fragmentation=estimate.fragmentation,
                    separation=estimate.separation,
                    rescued_clusters=estimate.rescued_clusters,
                    smoothed_log2=(
                        smoothed_log2 if smoothed_log2 is not None else estimate.log2_count
                    ),
                    at=self._last_infer_at,
                    recent_raw_log2=tuple(self._recent_raw),
                )
            log.debug(
                "headcount took %.2fs (clusters=%d, crowd_w=%.2f, bucket=%s)",
                self._last_infer_at - started,
                estimate.raw_clusters,
                estimate.crowd_weight,
                bucket.value,
            )
        self.status = "stopped"
