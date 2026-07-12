"""M7 design-space evaluation over saved TTS-harness embedding streams.

Loads data/tts_harness/replays/*.npz (written by tts_harness.py run) and
replays the buffered-embedding accumulation through candidate estimator
variants WITHOUT re-running ECAPA, so a full sweep takes seconds:

    python scripts/m7_candidates.py diagnose trio_uneven_mic
    python scripts/m7_candidates.py evaluate

Variants:
  baseline   — HeadcountEstimator as shipped (sanity check vs the harness)
  sepfix     — design-space option 1: single-cluster separation no longer
               reads as maximal collapse, and the dispersion ramp is
               recalibrated to mic-measured same-voice scatter (a smeared
               cluster only signals babble once its internal dispersion
               exceeds the clustering threshold itself, i.e. it plausibly
               holds >1 voice)
  tracks     — design-space option 2: temporal-continuity (leader)
               clustering in time order; consecutive same-utterance
               segments chain onto a track even when session-wide scatter
               exceeds the spatial threshold
  split      — design-space option 3: post-pass 2-means split of heavy
               clusters, accepted only when the split separates cleanly
  combined   — sepfix + tracks-as-count-refinement (the M7 candidate)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sensing.headcount import (  # noqa: E402
    BucketSmoother,
    HeadcountEstimator,
    agglomerative_cluster,
    separation_score,
    _mean_intra_cluster_distance,
    _ramp,
)
from sensing.state import bucket_from_log2  # noqa: E402

REPLAY_DIR = REPO / "data" / "tts_harness" / "replays"
BUFFER_S = 90.0


def load_stream(name: str) -> dict:
    z = np.load(REPLAY_DIR / f"{name}.npz")
    return dict(
        embeddings=z["embeddings"],
        durations=z["durations"],
        times=z["times"],
        speaker_masks=z["speaker_masks"],
        truth=int(z["truth"]),
        hops=json.loads(str(z["hops"])),
    )


def buffer_at(stream: dict, now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(embeddings, durations, speaker_masks) inside the rolling buffer."""
    t = stream["times"]
    sel = (t <= now) & (t > now - BUFFER_S)
    return stream["embeddings"][sel], stream["durations"][sel], stream["speaker_masks"][sel]


# --------------------------------------------------------------------------
# diagnose: where does each true speaker's evidence go?
# --------------------------------------------------------------------------


def cmd_diagnose(args: argparse.Namespace) -> int:
    stream = load_stream(args.scenario)
    est = HeadcountEstimator()
    hops = stream["hops"]
    for h in hops[:: max(1, len(hops) // 8)]:
        emb, dur, spk = buffer_at(stream, h["t"])
        if emb.shape[0] < 2:
            continue
        labels = agglomerative_cluster(emb, est.cluster_threshold)
        total_s = dur.sum()
        min_frac_s = est.min_cluster_evidence_frac * total_s
        print(f"\n-- t={h['t']:.0f}s  buffered {total_s:.0f}s "
              f"({emb.shape[0]} segments, {len(np.unique(labels))} raw labels, "
              f"min-mass floor {min_frac_s:.1f}s)")
        # Per true speaker: buffered seconds.
        for bit in range(4):
            m = (spk >> bit) & 1
            if m.any():
                print(f"   speaker {bit}: {dur[m.astype(bool)].sum():5.1f}s buffered "
                      f"({m.sum()} segments)")
        # Per cluster: mass, pass/fail, speaker composition.
        for label in np.unique(labels):
            in_c = labels == label
            mass = dur[in_c].sum()
            ok = (
                int(in_c.sum()) >= est.min_cluster_segments
                or mass >= est.min_cluster_speech_s
            ) and mass >= min_frac_s
            comp = {}
            for bit in range(4):
                s = dur[in_c & (((spk >> bit) & 1).astype(bool))].sum()
                if s > 0:
                    comp[bit] = round(float(s), 1)
            print(f"   cluster {label}: {mass:5.1f}s {in_c.sum():>3} segs "
                  f"{'PASS' if ok else 'fail':<4} composition {comp}")
    return 0


# --------------------------------------------------------------------------
# candidate estimators (buffer -> raw count and/or crowd weight)
# --------------------------------------------------------------------------


def _mass_pass_count(labels: np.ndarray, dur: np.ndarray, est: HeadcountEstimator) -> tuple[int, float]:
    raw = 0
    stray = 0
    min_frac_s = est.min_cluster_evidence_frac * float(dur.sum())
    for label in np.unique(labels):
        in_c = labels == label
        mass = float(dur[in_c].sum())
        if (
            int(in_c.sum()) >= est.min_cluster_segments
            or mass >= est.min_cluster_speech_s
        ) and mass >= min_frac_s:
            raw += 1
        else:
            stray += int(in_c.sum())
    return max(raw, 1), stray / len(labels)


def _mass_pass_count_rescue(
    emb: np.ndarray,
    labels: np.ndarray,
    dur: np.ndarray,
    est: HeadcountEstimator,
    margin: float = 0.80,
) -> tuple[int, float, int]:
    """Min-mass count + distinct-voice rescue.

    A cluster that fails ONLY the proportional floor still counts when it
    (a) passes a strengthened absolute floor (>= min_cluster_segments
    segments AND >= min_cluster_speech_s attributed speech), and (b) its
    centroid sits at least `margin` cosine distance from every
    mass-passing cluster's centroid — i.e. it is a distinct voice with low
    airtime, not the dominant speaker's scatter debris (same-voice debris
    hugs the parent centroid; different voices measure ~0.9).
    Returns (count, fragmentation, n_rescued).
    """
    total_s = float(dur.sum())
    min_frac_s = est.min_cluster_evidence_frac * total_s
    passing, failing = [], []
    stray = 0
    for label in np.unique(labels):
        in_c = labels == label
        mass = float(dur[in_c].sum())
        if (
            int(in_c.sum()) >= est.min_cluster_segments
            or mass >= est.min_cluster_speech_s
        ) and mass >= min_frac_s:
            passing.append(label)
        else:
            failing.append(label)
            stray += int(in_c.sum())

    def centroid(label: int) -> np.ndarray:
        c = emb[labels == label].mean(axis=0)
        return c / max(np.linalg.norm(c), 1e-10)

    pass_centroids = [centroid(lb) for lb in passing]
    rescued = 0
    for label in failing:
        in_c = labels == label
        mass = float(dur[in_c].sum())
        if int(in_c.sum()) < est.min_cluster_segments or mass < est.min_cluster_speech_s:
            continue
        c = centroid(label)
        if all(1.0 - float(np.dot(c, p)) >= margin for p in pass_centroids):
            rescued += 1
            stray -= int(in_c.sum())
    raw = max(len(passing) + rescued, 1)
    return raw, stray / len(labels), rescued


def leader_tracks(
    emb: np.ndarray, dur: np.ndarray, assign_thr: float
) -> np.ndarray:
    """Temporal leader clustering: segments arrive in time order; each joins
    the nearest existing track (duration-weighted running centroid, cosine
    distance < assign_thr) or opens a new one. Returns labels."""
    labels = np.empty(len(emb), dtype=int)
    centroids: list[np.ndarray] = []
    masses: list[float] = []
    for i in range(len(emb)):
        v = emb[i]
        best, best_d = -1, np.inf
        for k, c in enumerate(centroids):
            d = 1.0 - float(np.dot(v, c) / max(np.linalg.norm(c), 1e-10))
            if d < best_d:
                best, best_d = k, d
        if best >= 0 and best_d < assign_thr:
            w = dur[i]
            centroids[best] = centroids[best] + w * v
            masses[best] += w
            labels[i] = best
        else:
            centroids.append(dur[i] * v.copy())
            masses.append(dur[i])
            labels[i] = len(centroids) - 1
    return labels


def split_heavy(
    emb: np.ndarray, dur: np.ndarray, labels: np.ndarray, est: HeadcountEstimator
) -> np.ndarray:
    """Post-pass: try a 2-means split on any mass-passing cluster; keep the
    split only if the two halves separate at the clustering threshold
    (centroid distance >= threshold) and both halves pass min-mass."""
    labels = labels.copy()
    next_label = labels.max() + 1
    min_frac_s = est.min_cluster_evidence_frac * float(dur.sum())
    for label in np.unique(labels):
        idx = np.flatnonzero(labels == label)
        if idx.size < 4:
            continue
        sub = emb[idx]
        # 2-means (cosine, via normalized euclidean on unit vectors), few iters.
        far = np.unravel_index(
            np.argmax(1.0 - sub @ sub.T), (idx.size, idx.size)
        )
        c = np.stack([sub[far[0]], sub[far[1]]])
        for _ in range(8):
            d = 1.0 - sub @ c.T
            assign = d.argmin(axis=1)
            for j in (0, 1):
                if (assign == j).any():
                    m = sub[assign == j].mean(axis=0)
                    c[j] = m / max(np.linalg.norm(m), 1e-10)
        inter = 1.0 - float(np.dot(c[0], c[1]))
        if inter < est.cluster_threshold:
            continue
        halves = [idx[assign == j] for j in (0, 1)]
        ok = all(
            (
                h.size >= est.min_cluster_segments
                or float(dur[h].sum()) >= est.min_cluster_speech_s
            )
            and float(dur[h].sum()) >= min_frac_s
            for h in halves
        )
        if ok:
            labels[halves[1]] = next_label
            next_label += 1
    return labels


def crowd_weight_sepfix(
    emb: np.ndarray,
    labels: np.ndarray,
    raw: int,
    fragmentation: float,
    sr: float,
    dbfs: float,
    est: HeadcountEstimator,
) -> float:
    """Option-1 fix: (a) when the silhouette is undefined (single cluster),
    collapse is judged from within-cluster dispersion against the clustering
    threshold itself — a cluster is only 'collapsed babble' if its internal
    spread says it plausibly holds >1 voice; (b) the dispersion ramp starts
    at the clustering threshold, not half of it, because mic-measured
    same-voice scatter (~0.6) lives inside the old [0.35, 0.70] ramp."""
    separation = separation_score(emb, labels)
    dispersion = _mean_intra_cluster_distance(emb, labels)
    thr = est.cluster_threshold
    dispersion_signal = _ramp(dispersion, thr, 1.3 * thr)
    n_labels = len(np.unique(labels))
    if emb.shape[0] < 3 or n_labels < 2:
        sep_collapse = dispersion_signal  # was: pinned 1.0
    else:
        sep_collapse = 1.0 - max(0.0, min(1.0, separation / 0.25))
    count_pressure = _ramp(raw, est.count_reliable_max, est.count_regime_max)
    loud_term = _ramp(dbfs, -45.0, -20.0)
    saturation = _ramp(sr, 0.6, 0.95) * loud_term
    smear = max(dispersion_signal, _ramp(fragmentation, 0.3, 0.8))
    return max(0.0, min(1.0, sep_collapse * max(count_pressure, saturation * smear)))


def evaluate_variant(
    stream: dict, variant: str, assign_thr: float = 0.60, args_margin: float = 0.80
) -> dict:
    est = HeadcountEstimator()
    smoother = BucketSmoother()
    raws, crowds, buckets = [], [], []
    for h in stream["hops"]:
        emb, dur, _ = buffer_at(stream, h["t"])
        if emb.shape[0] == 0:
            continue
        if variant == "tracks":
            labels = leader_tracks(emb, dur, assign_thr)
        else:
            labels = agglomerative_cluster(emb, est.cluster_threshold)
        if variant == "split":
            labels = split_heavy(emb, dur, labels, est)
        if variant == "m7":
            # The M7 candidate: sepfix crowd weight + distinct-voice rescue.
            raw, frag, _ = _mass_pass_count_rescue(emb, labels, dur, est,
                                                   margin=args_margin)
        else:
            raw, frag = _mass_pass_count(labels, dur, est)

        if variant == "baseline":
            # Pre-M7 semantics: undefined silhouette read as 0.0 (the
            # misfire). separation_score returns None post-M7, so map back.
            separation = separation_score(emb, labels) or 0.0
            sep_collapse = 1.0 - max(0.0, min(1.0, separation / 0.25))
            count_pressure = _ramp(raw, est.count_reliable_max, est.count_regime_max)
            loud_term = _ramp(h["dbfs"], -45.0, -20.0)
            saturation = _ramp(h["sr"], 0.6, 0.95) * loud_term
            dispersion = _mean_intra_cluster_distance(emb, labels)
            d_sig = _ramp(dispersion, 0.5 * est.cluster_threshold, est.cluster_threshold)
            smear = max(d_sig, _ramp(frag, 0.3, 0.8))
            crowd = max(0.0, min(1.0, sep_collapse * max(count_pressure, saturation * smear)))
        else:
            crowd = crowd_weight_sepfix(emb, labels, raw, frag, h["sr"], h["dbfs"], est)

        loud01 = max(0.0, min(1.0, (h["dbfs"] + 45.0) / 25.0))
        log2_babble = 3.0 + 7.0 * max(0.0, min(1.0, h["sr"])) * loud01
        log2_est = (1.0 - crowd) * np.log2(raw) + crowd * log2_babble
        bucket = smoother.update(float(log2_est), h["t"])
        raws.append(raw)
        crowds.append(crowd)
        buckets.append(bucket.value)
    return dict(
        mean_raw=float(np.mean(raws)) if raws else 0.0,
        mean_crowd=float(np.mean(crowds)) if crowds else 0.0,
        final_bucket=buckets[-1] if buckets else "-",
        buckets=buckets,
    )


def cmd_evaluate(args: argparse.Namespace) -> int:
    scenarios = sorted(p.stem for p in REPLAY_DIR.glob("*.npz"))
    if args.only:
        scenarios = [s for s in scenarios if args.only in s]
    variants = ["baseline", "sepfix", "tracks", "split", "m7"]
    print(f"{'scenario':<22} {'truth':>5} | " + " | ".join(f"{v:^16}" for v in variants))
    print(f"{'':22} {'':>5} | " + " | ".join(f"{'raw / bucket':^16}" for _ in variants))
    for name in scenarios:
        stream = load_stream(name)
        cells = []
        for v in variants:
            r = evaluate_variant(stream, v, assign_thr=args.assign_thr,
                                 args_margin=args.margin)
            cells.append(f"{r['mean_raw']:4.2f} / {r['final_bucket']:<6}")
        print(f"{name:<22} {stream['truth']:>5} | " + " | ".join(f"{c:^16}" for c in cells))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnose")
    d.add_argument("scenario")
    e = sub.add_parser("evaluate")
    e.add_argument("--only")
    e.add_argument("--assign-thr", type=float, default=0.60)
    e.add_argument("--margin", type=float, default=0.80)
    args = parser.parse_args()
    return {"diagnose": cmd_diagnose, "evaluate": cmd_evaluate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
