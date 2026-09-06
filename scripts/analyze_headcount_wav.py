"""Replay the headcount layer over a recorded WAV, offline and repeatably.

Companion to `scripts/capture_room_wav.py`. Answers "why does a solo speaker
read `pair` on this machine?" without a live session: capture one file per
condition (position, mic-enhancement setting, machine), then run this over
each and compare. Nothing here touches a microphone, the network, or any
calibration constant — it is measurement only.

WHICH CONFIG THIS MIRRORS (repo rule: replay scripts state this explicitly).
Constants come from `Config.from_env()` — that is, this machine's `.env`
layered over `config.py` defaults — and every value used is printed in the
header so a pasted result is self-describing. The capture sidecar records
the constants in force *at capture time*; when the two disagree the header
says so, because a replay under different constants than the session it
reproduces is a different experiment.

The engine's schedule is reproduced faithfully: streaming Silero VAD with
its recurrent state intact, `window_s` windows every `hop_s`, submission
gated on `headcount_min_speech_ratio` and rate-limited by
`headcount_min_interval_s`, then `speech_segments` -> ECAPA -> the same
`HeadcountEstimator` and `BucketSmoother` the worker owns.

    python scripts/analyze_headcount_wav.py data/captures/foo.wav
    python scripts/analyze_headcount_wav.py a.wav b.wav          # compare
    python scripts/analyze_headcount_wav.py foo.wav --sweep      # diagnostic

THE NUMBER TO READ IS `scatter`, not the bucket. `dispersion` is mean
*within-cluster* distance, so it is measured after the split and understates
how spread a solo speaker's embeddings really are. `scatter` is the all-pairs
cosine distance over every buffered segment, which is directly comparable to
the two figures headcount.py's min-mass comment already records: ~0.35 mean
on clean audio, ~0.6 on a laptop mic. If solo scatter here sits near 0.6, a
0.70 average-linkage cut is operating at the edge of the documented laptop-mic
regime and the split is expected behaviour for this hardware class — not
something the room did. If it sits near 0.35 in one condition and near 0.6 in
another, the difference between those conditions is the answer.

`--sweep` reports the cluster count the buffer would yield at other
thresholds. It is a diagnostic view of the measured distribution, NOT a
tuning knob: `RTR_HEADCOUNT_CLUSTER_THRESHOLD` is a measured constant
(calibrated 2026-07-05, see config.py) and changing it is a calibration event
with its own protocol. Read the sweep as "how close is the current cut to a
cliff", never as "pick a better number".
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

from sensing import dsp
from sensing.config import Config
from sensing.headcount import (
    BucketSmoother,
    HeadcountEstimator,
    agglomerative_cluster,
    load_ecapa,
    speech_segments,
)
from sensing.vad import VadGate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay headcount over recorded WAV(s)."
    )
    parser.add_argument("wavs", nargs="+", help="WAV file(s) to analyse")
    parser.add_argument(
        "--frames", action="store_true", help="print every window, not just the summary"
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="also report cluster counts at other thresholds (diagnostic only)",
    )
    parser.add_argument(
        "--json", default="", help="write results to this path as JSON"
    )
    args = parser.parse_args()

    config = Config.from_env()
    print("=" * 74)
    print("config in force for this replay (Config.from_env: .env over defaults)")
    print(
        f"  window_s={config.window_s}  hop_s={config.hop_s}  "
        f"vad_threshold={config.vad_threshold}"
    )
    print(
        f"  cluster_threshold={config.headcount_cluster_threshold}  "
        f"buffer_s={config.headcount_buffer_s}  "
        f"min_cluster_frac={config.headcount_min_cluster_frac}"
    )
    print(
        f"  min_interval_s={config.headcount_min_interval_s}  "
        f"min_speech_ratio={config.headcount_min_speech_ratio}"
    )
    print("=" * 74)

    print("\nloading models (Silero VAD + ECAPA)...")
    vad_proto = VadGate(config.sample_rate, config.window_s, config.vad_threshold)
    vad_proto.load()
    embed = load_ecapa(config.headcount_model, config.torch_threads, config.os_truststore)

    results = []
    for path in args.wavs:
        result = analyse(Path(path), config, embed, args)
        results.append(result)
        report(result, args)

    if len(results) > 1:
        compare(results)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


def analyse(path: Path, config: Config, embed, args) -> dict:
    audio, rate = _read_wav(path)
    if rate != config.sample_rate:
        raise SystemExit(
            f"{path}: {rate} Hz, expected {config.sample_rate} Hz. "
            "capture_room_wav.py writes at the analysis rate; this file did not "
            "come from it, and resampling here would characterise a path RTR "
            "does not have."
        )
    sidecar = _read_sidecar(path)

    # A fresh gate per file: Silero's recurrent state must not leak between
    # recordings, or the second file inherits the first one's context.
    vad = VadGate(config.sample_rate, config.window_s, config.vad_threshold)
    vad.load()
    estimator = HeadcountEstimator(
        buffer_s=config.headcount_buffer_s,
        buffer_cap=config.headcount_buffer_cap,
        cluster_threshold=config.headcount_cluster_threshold,
        min_cluster_evidence_frac=config.headcount_min_cluster_frac,
    )
    smoother = BucketSmoother(
        tau_s=config.headcount_smooth_tau_s, hold_k=config.headcount_hysteresis_k
    )

    window_n = int(config.window_s * config.sample_rate)
    hop_n = int(config.hop_s * config.sample_rate)
    frames: list[dict] = []
    fed = 0
    last_infer_t = -1e9
    # Playback was inert for these captures, so certification uses the plain
    # threshold and the crowd path's floor-relative branch stays off — same
    # as a music-free live session.
    for end in range(window_n, audio.size + 1, hop_n):
        vad.feed(audio[fed:end])
        fed = end
        t = end / config.sample_rate  # replay clock: seconds into the file
        window = audio[end - window_n : end]
        raw_ratio = vad.speech_ratio()
        measured = dsp.analyze(window, config.sample_rate)

        if raw_ratio < config.headcount_min_speech_ratio:
            continue
        if t - last_infer_t < config.headcount_min_interval_s:
            continue
        last_infer_t = t

        segments = speech_segments(window, vad.speech_mask(), config.sample_rate)
        if segments:
            embeddings = embed(segments)
            estimator.add(
                embeddings, [s.size / config.sample_rate for s in segments], t
            )
        estimate = estimator.estimate(raw_ratio, measured.rms_dbfs)
        if estimate is None:
            continue
        bucket = smoother.update(estimate.log2_count, t)
        frames.append(
            {
                "t": round(t, 1),
                "bucket": bucket.value,
                "raw_clusters": estimate.raw_clusters,
                "dispersion": round(estimate.dispersion, 3),
                "fragmentation": round(estimate.fragmentation, 3),
                "crowd_weight": round(estimate.crowd_weight, 3),
                "confidence": round(estimate.confidence, 2),
                "speech_ratio": round(raw_ratio, 3),
                "dbfs": round(measured.rms_dbfs, 1),
            }
        )

    # Final buffer: the raw embedding evidence, independent of clustering.
    # Deliberately the estimator's own post-eviction buffer rather than a
    # parallel copy kept here — a copy would have to re-implement buffer_s /
    # buffer_cap eviction to stay honest, and any drift would silently make
    # `scatter` describe a different set of segments than the ones actually
    # clustered. Private access is the cheaper correctness guarantee.
    emb = np.vstack(estimator._embeddings) if estimator._embeddings else np.empty((0, 0))
    scatter = _scatter_stats(emb)
    sweep = _threshold_sweep(emb) if (args.sweep and emb.size) else {}

    return {
        "file": str(path),
        "note": sidecar.get("note", ""),
        "device_name": sidecar.get("device_name", ""),
        "host": sidecar.get("host", ""),
        "duration_s": round(audio.size / config.sample_rate, 1),
        "config_drift": _config_drift(sidecar, config),
        "frames": frames,
        "segments_buffered": int(emb.shape[0]) if emb.size else 0,
        "scatter": scatter,
        "sweep": sweep,
        "summary": _summarise(frames),
    }


def _summarise(frames: list[dict]) -> dict:
    if not frames:
        return {}
    buckets: dict[str, int] = {}
    raw_hist: dict[int, int] = {}
    for f in frames:
        buckets[f["bucket"]] = buckets.get(f["bucket"], 0) + 1
        raw_hist[f["raw_clusters"]] = raw_hist.get(f["raw_clusters"], 0) + 1
    return {
        "windows": len(frames),
        "buckets": buckets,
        "raw_clusters_hist": dict(sorted(raw_hist.items())),
        # Sorted candidates so a tie resolves to the lower count every run —
        # the single-number summary must not shift between identical replays.
        "raw_clusters_mode": max(sorted(raw_hist), key=lambda c: raw_hist[c]),
        "dispersion_mean": round(float(np.mean([f["dispersion"] for f in frames])), 3),
        "dispersion_max": round(float(max(f["dispersion"] for f in frames)), 3),
        "crowd_weight_max": round(float(max(f["crowd_weight"] for f in frames)), 3),
        "speech_ratio_mean": round(
            float(np.mean([f["speech_ratio"] for f in frames])), 3
        ),
        "dbfs_mean": round(float(np.mean([f["dbfs"] for f in frames])), 1),
    }


def _scatter_stats(emb: np.ndarray) -> dict:
    """All-pairs cosine distance over the final buffer.

    Clustering-independent, so it measures the embedding spread itself rather
    than what survived a 0.70 cut. Comparable to headcount.py's recorded
    ~0.35 (clean audio) / ~0.6 (laptop mic) same-speaker figures.
    """
    if emb.size == 0 or emb.shape[0] < 2:
        return {}
    dist = 1.0 - np.clip(emb @ emb.T, -1.0, 1.0)
    iu = np.triu_indices(dist.shape[0], k=1)
    pairs = dist[iu]
    return {
        "n_segments": int(emb.shape[0]),
        "mean": round(float(pairs.mean()), 3),
        "p10": round(float(np.percentile(pairs, 10)), 3),
        "median": round(float(np.median(pairs)), 3),
        "p90": round(float(np.percentile(pairs, 90)), 3),
        "max": round(float(pairs.max()), 3),
        # Share of pairs the current cut would refuse to link directly. High
        # here with a solo speaker is the split, quantified.
        "frac_over_0.70": round(float((pairs >= 0.70).mean()), 3),
    }


def _threshold_sweep(emb: np.ndarray) -> dict:
    """Cluster count vs threshold — a view of the distribution, not a knob."""
    return {
        f"{thr:.2f}": int(len(np.unique(agglomerative_cluster(emb, thr))))
        for thr in (0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90)
    }


def _config_drift(sidecar: dict, config: Config) -> dict:
    """Constants that differ between capture time and replay time."""
    at_capture = sidecar.get("config_at_capture") or {}
    now = {
        "window_s": config.window_s,
        "hop_s": config.hop_s,
        "vad_threshold": config.vad_threshold,
        "headcount_cluster_threshold": config.headcount_cluster_threshold,
        "headcount_buffer_s": config.headcount_buffer_s,
        "headcount_min_interval_s": config.headcount_min_interval_s,
        "headcount_min_speech_ratio": config.headcount_min_speech_ratio,
        "headcount_min_cluster_frac": config.headcount_min_cluster_frac,
    }
    return {
        key: {"at_capture": at_capture[key], "now": value}
        for key, value in now.items()
        if key in at_capture and at_capture[key] != value
    }


def report(result: dict, args) -> None:
    print(f"\n{'-' * 74}")
    print(f"{result['file']}")
    if result["note"]:
        print(f"  condition: {result['note']}")
    if result["device_name"]:
        print(f"  device:    {result['device_name']}  ({result['host']})")
    print(f"  duration:  {result['duration_s']} s")

    if result["config_drift"]:
        print("\n  WARNING: constants changed since capture - this replay is not")
        print("  reproducing the session that recorded the file:")
        for key, pair in result["config_drift"].items():
            print(f"    {key}: {pair['at_capture']} at capture -> {pair['now']} now")

    summary = result["summary"]
    if not summary:
        print("\n  no windows cleared the speech gate - nothing to analyse.")
        print("  (check the capture's level and speech_ratio before re-running)")
        return

    print(f"\n  windows analysed:  {summary['windows']}")
    print(f"  buckets:           {summary['buckets']}")
    print(
        f"  raw_clusters:      mode {summary['raw_clusters_mode']}  "
        f"distribution {summary['raw_clusters_hist']}"
    )
    print(
        f"  dispersion:        mean {summary['dispersion_mean']}  "
        f"max {summary['dispersion_max']}"
    )
    print(f"  crowd_weight max:  {summary['crowd_weight_max']}")
    print(
        f"  speech_ratio mean: {summary['speech_ratio_mean']}   "
        f"level: {summary['dbfs_mean']} dBFS"
    )

    scatter = result["scatter"]
    if scatter:
        print(
            f"\n  SCATTER (all-pairs cosine distance, {scatter['n_segments']} segments)"
        )
        print(
            f"    mean {scatter['mean']}   median {scatter['median']}   "
            f"p10 {scatter['p10']}   p90 {scatter['p90']}   max {scatter['max']}"
        )
        print(f"    pairs at/over the 0.70 cut: {scatter['frac_over_0.70']:.1%}")
        print("    reference: ~0.35 clean audio / ~0.6 laptop mic (headcount.py)")

    if result["sweep"]:
        print("\n  THRESHOLD SWEEP (diagnostic - do not treat as a tuning result)")
        print("    " + "  ".join(f"{k}:{v}" for k, v in result["sweep"].items()))

    if args.frames:
        print("\n  t      bucket  raw  disp   frag   crowd  conf  sr     dBFS")
        for f in result["frames"]:
            print(
                f"  {f['t']:5.1f}  {f['bucket']:>6}  {f['raw_clusters']:>3}  "
                f"{f['dispersion']:.3f}  {f['fragmentation']:.3f}  "
                f"{f['crowd_weight']:.3f}  {f['confidence']:.2f}  "
                f"{f['speech_ratio']:.3f}  {f['dbfs']:.1f}"
            )


def compare(results: list[dict]) -> None:
    print(f"\n{'=' * 74}")
    print("COMPARISON")
    print(f"{'=' * 74}")
    print(f"  {'condition':<34} {'raw':>4} {'disp':>6} {'scatter':>8} {'>=0.70':>7}")
    for r in results:
        summary, scatter = r["summary"], r["scatter"]
        if not summary:
            print(f"  {(r['note'] or Path(r['file']).name)[:34]:<34}   (no windows)")
            continue
        label = (r["note"] or Path(r["file"]).name)[:34]
        print(
            f"  {label:<34} {summary['raw_clusters_mode']:>4} "
            f"{summary['dispersion_mean']:>6.3f} "
            f"{scatter.get('mean', float('nan')):>8.3f} "
            f"{scatter.get('frac_over_0.70', float('nan')):>7.1%}"
        )
    print(
        "\n  A condition that moves `scatter` moved the acoustics or the capture\n"
        "  path. A condition that moves only `raw` without moving `scatter` moved\n"
        "  the buffer past the cut by chance - take more takes before believing it."
    )


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        if fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16-bit mono PCM")
        rate = fh.getframerate()
        pcm = np.frombuffer(fh.readframes(fh.getnframes()), dtype="<i2")
    return (pcm.astype(np.float32) / 32768.0), rate


def _read_sidecar(path: Path) -> dict:
    sidecar = path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
