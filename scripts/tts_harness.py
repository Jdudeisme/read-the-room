"""M7 controlled-N ground-truth harness (rebuild of the M3 root-cause rig).

The M3 harness ran on macOS `say`; this one uses Windows OneCore TTS via
scripts/tts_synth.ps1 (David / Mark / Zira, plus a pitch-shifted David as a
fourth pseudo-voice). Three stages:

    python scripts/tts_harness.py synth       # generate utterance WAVs
    python scripts/tts_harness.py distances   # same/cross-voice segment stats
    python scripts/tts_harness.py run         # scenarios -> replay -> report

`run` assembles conversations with known ground truth N (turn-taking and
overlap variants, clean and mic-degraded), replays them through the real
pipeline (speech_segments -> ECAPA -> HeadcountEstimator -> BucketSmoother),
prints per-hop diagnostics, and saves each scenario's embedding stream to
data/tts_harness/replays/*.npz so candidate estimators can be iterated
offline without re-embedding (see m7_candidates.py).

Degradation model ("mic"): additive broadband noise at a target SNR plus a
short exponential-decay reverb tail — tuned so same-voice segment distances
land near the measured laptop-mic distribution (~0.60 mean / 0.75 p90)
rather than the clean ~0.35 (FakeProvider lesson: model measured reality).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sensing.headcount import (  # noqa: E402
    BucketSmoother,
    HeadcountEstimator,
    agglomerative_cluster,
    load_ecapa,
    speech_segments,
)

SR = 16_000
VAD_CHUNK = 512
HARNESS_DIR = REPO / "data" / "tts_harness"
UTT_DIR = HARNESS_DIR / "utts"
REPLAY_DIR = HARNESS_DIR / "replays"

# voice name -> (winrt voice match, pitch, rate)
VOICES = {
    "david": ("David", 1.0, 1.0),
    "mark": ("Mark", 1.0, 1.0),
    "zira": ("Zira", 1.0, 1.0),
    # Pitch-shifted David: measured NOT distinct to ECAPA (0.40 vs David's
    # 0.35 within) — kept as the deliberate same-speaker-variant probe.
    "david_lo": ("David", 0.62, 0.9),
}

# Derived voices: formant-shift via resampling (pitch+formants move together),
# which ECAPA DOES read as a distinct speaker (measured 0.78 vs base zira,
# ~ the david-vs-mark cross distance). base -> (up, down) resample factors.
DERIVED = {
    "zira_lo": ("zira", 100, 82),
}

SENTENCES = [
    "I still think the porch is the best spot this time of year.",
    "Did anyone actually try the new place on fourth street yet?",
    "Honestly the second half of that movie made no sense at all.",
    "We should plan the lake trip before everyone gets busy again.",
    "That reminds me of the time the grill caught fire at Dave's.",
    "I read that the market for those has completely collapsed.",
    "You have to hear the story about the airport shuttle driver.",
    "No way, she said it was cancelled two weeks ago.",
    "Pass me one of those before they're gone.",
    "The playlist tonight is actually pretty good for once.",
    "I can't believe how fast this summer is going by.",
    "Someone explain to me why the wifi only dies during playoffs.",
]


# --------------------------------------------------------------------------
# synth
# --------------------------------------------------------------------------


def cmd_synth(_args: argparse.Namespace) -> int:
    UTT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = HARNESS_DIR / "synth_manifest.jsonl"
    jobs = []
    for vname, (match, pitch, rate) in VOICES.items():
        vdir = UTT_DIR / vname
        vdir.mkdir(exist_ok=True)
        for i, text in enumerate(SENTENCES):
            out = vdir / f"utt{i:02d}.wav"
            jobs.append(
                {"voice": match, "text": text, "out": str(out),
                 "pitch": pitch, "rate": rate}
            )
    manifest.write_text("\n".join(json.dumps(j) for j in jobs))
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(REPO / "scripts" / "tts_synth.ps1"),
         "-Manifest", str(manifest)],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print(f"synthesized {len(jobs)} utterances into {UTT_DIR}")
    return 0


# --------------------------------------------------------------------------
# audio utilities
# --------------------------------------------------------------------------


def load_utterance(path: Path) -> np.ndarray:
    """WAV -> float32 mono 16 kHz, silence-trimmed, peak-normalized to 0.25."""
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    x = data.astype(np.float32)
    if data.dtype == np.int16:
        x /= 32768.0
    if rate != SR:
        from math import gcd

        g = gcd(rate, SR)
        x = resample_poly(x, SR // g, rate // g).astype(np.float32)
    # Trim leading/trailing silence (TTS pads generously).
    frame = 256
    n = len(x) // frame
    rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    active = np.flatnonzero(rms > 0.01 * rms.max())
    if active.size:
        x = x[active[0] * frame : (active[-1] + 1) * frame]
    peak = np.abs(x).max()
    return x * (0.25 / peak) if peak > 0 else x


def load_voice_utts(vname: str) -> list[np.ndarray]:
    if vname in DERIVED:
        from scipy.signal import resample_poly

        base, up, down = DERIVED[vname]
        return [
            resample_poly(u, up, down).astype(np.float32)
            for u in load_voice_utts(base)
        ]
    utts = [load_utterance(p) for p in sorted((UTT_DIR / vname).glob("*.wav"))]
    if not utts:
        raise SystemExit(f"no utterances for {vname}; run `synth` first")
    return utts


def degrade(x: np.ndarray, rng: np.random.Generator, snr_db: float = 12.0,
            reverb_s: float = 0.25) -> np.ndarray:
    """Mic/room degradation: exponential-decay reverb + broadband noise."""
    ir_len = int(reverb_s * SR)
    t = np.arange(ir_len) / SR
    ir = rng.standard_normal(ir_len).astype(np.float32) * np.exp(
        -t / (reverb_s / 4)
    ).astype(np.float32)
    ir[0] = 1.0
    ir /= np.sqrt((ir**2).sum())
    wet = np.convolve(x, ir)[: len(x)].astype(np.float32)
    sig_p = float((wet**2).mean())
    noise = rng.standard_normal(len(wet)).astype(np.float32)
    noise *= np.sqrt(sig_p / (10 ** (snr_db / 10)) / float((noise**2).mean()))
    return wet + noise


# --------------------------------------------------------------------------
# distances
# --------------------------------------------------------------------------


def _segment_embeddings(embed, utts: list[np.ndarray]) -> np.ndarray:
    """Cut a voice's utterances into pipeline-identical 1.25 s segments."""
    segs: list[np.ndarray] = []
    for utt in utts:
        mask = np.ones(len(utt) // VAD_CHUNK, dtype=bool)
        segs.extend(speech_segments(utt, mask, SR, max_segments=100))
    return embed(segs)


def _dist_stats(a: np.ndarray, b: np.ndarray | None = None) -> tuple[float, float, float]:
    """(mean, p10, p90) cosine distance; within-set when b is None."""
    if b is None:
        d = 1.0 - np.clip(a @ a.T, -1, 1)
        vals = d[np.triu_indices(len(a), k=1)]
    else:
        vals = (1.0 - np.clip(a @ b.T, -1, 1)).ravel()
    return float(vals.mean()), float(np.percentile(vals, 10)), float(np.percentile(vals, 90))


def cmd_distances(args: argparse.Namespace) -> int:
    embed = load_ecapa("speechbrain/spkrec-ecapa-voxceleb", args.threads)
    rng = np.random.default_rng(7)
    for label, deg in [("clean", False), ("mic-degraded", True)]:
        embs = {}
        for vname in VOICES:
            utts = load_voice_utts(vname)
            if deg:
                utts = [degrade(u, rng) for u in utts]
            embs[vname] = _segment_embeddings(embed, utts)
        print(f"\n== {label} ==")
        print(f"{'pair':<22} {'mean':>6} {'p10':>6} {'p90':>6}")
        for vname, e in embs.items():
            m, p10, p90 = _dist_stats(e)
            print(f"{vname + ' (within)':<22} {m:>6.3f} {p10:>6.3f} {p90:>6.3f}")
        names = list(embs)
        for i, va in enumerate(names):
            for vb in names[i + 1 :]:
                m, p10, p90 = _dist_stats(embs[va], embs[vb])
                print(f"{va + ' vs ' + vb:<22} {m:>6.3f} {p10:>6.3f} {p90:>6.3f}")
    return 0


# --------------------------------------------------------------------------
# scenario assembly
# --------------------------------------------------------------------------


def assemble(
    voices: list[str],
    rng: np.random.Generator,
    total_s: float = 150.0,
    overlap: bool = False,
    uneven: bool = False,
    gap_range: tuple[float, float] = (0.25, 0.9),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a conversation. Returns (audio, activity_mask, speaker_track).

    Turn-taking: speakers alternate (never the same speaker twice in a row),
    one at a time, with short gaps — the regime that starves clusters.
    Overlap: each new turn starts while the previous is still finishing
    (0.5–1.5 s overlap) — the goodbye-chatter regime.
    Uneven: the first voice holds the floor with full utterances; the others
    only interject short 1.2–2.5 s slices between turns — the real-party
    airtime distribution that starves non-dominant speakers of evidence.
    speaker_track holds, per VAD chunk, a bitmask of active speakers
    (ground truth for candidate evaluation, not used by the pipeline).
    """
    utts = {v: load_voice_utts(v) for v in voices}
    total = int(total_s * SR)
    audio = np.zeros(total, dtype=np.float32)
    speakers = np.zeros(total // VAD_CHUNK, dtype=np.int32)

    def place(v: str, clip: np.ndarray, at: int) -> int:
        end = min(at + len(clip), total)
        audio[at:end] += clip[: end - at]
        speakers[at // VAD_CHUNK : end // VAD_CHUNK] |= 1 << voices.index(v)
        return end

    pos = 0
    prev_v = None
    others = voices[1:]
    while pos < total:
        if uneven:
            # Dominant speaker takes a full turn...
            utt = utts[voices[0]][rng.integers(len(utts[voices[0]]))]
            pos = place(voices[0], utt, pos) + int(rng.uniform(*gap_range) * SR)
            # ...then usually one short interjection from someone else.
            if others and rng.random() < 0.75:
                v = others[rng.integers(len(others))]
                src = utts[v][rng.integers(len(utts[v]))]
                length = int(rng.uniform(1.2, 2.5) * SR)
                start = rng.integers(max(1, len(src) - length))
                pos = place(v, src[start : start + length], pos)
                pos += int(rng.uniform(*gap_range) * SR)
            continue
        candidates = [v for v in voices if v != prev_v] or voices
        v = candidates[rng.integers(len(candidates))]
        utt = utts[v][rng.integers(len(utts[v]))]
        if overlap and prev_v is not None:
            pos = max(0, pos - int(rng.uniform(0.5, 1.5) * SR))
        pos = place(v, utt, pos) + int(rng.uniform(*gap_range) * SR)
        prev_v = v

    activity = speakers != 0
    return audio, activity, speakers


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    return 20 * np.log10(max(rms, 1e-10))


def replay(
    name: str,
    audio: np.ndarray,
    activity: np.ndarray,
    speakers: np.ndarray,
    embed,
    truth: int,
    window_s: float = 5.0,
    hop_s: float = 4.0,
) -> dict:
    """Feed the assembled session through the real pipeline.

    hop_s=4.0 models the production RTR_HEADCOUNT_MIN_INTERVAL_S=4.0
    schedule. Saves the embedding stream + per-hop metadata for offline
    candidate iteration.
    """
    estimator = HeadcountEstimator()
    smoother = BucketSmoother()

    win = int(window_s * SR)
    hop = int(hop_s * SR)

    all_emb: list[np.ndarray] = []
    all_dur: list[float] = []
    all_t: list[float] = []
    all_spk: list[int] = []  # ground-truth dominant speaker bitmask per segment
    hops = []

    print(f"\n== scenario: {name} (true N={truth}) ==")
    print("t      raw crowd  disp  frag  bucket  smoothed")
    for start in range(0, len(audio) - win + 1, hop):
        now = start / SR
        window = audio[start : start + win]
        mask = activity[start // VAD_CHUNK : (start + win) // VAD_CHUNK]
        spk_track = speakers[start // VAD_CHUNK : (start + win) // VAD_CHUNK]
        speech_ratio = float(mask.mean())
        if speech_ratio == 0.0:
            continue
        segments = speech_segments(window, mask, SR)
        if segments:
            embeddings = embed(segments)
            durations = [s.size / SR for s in segments]
            estimator.add(embeddings, durations, now)
            # Ground-truth labels: majority active-speaker bitmask over the
            # window's speech chunks, per segment (approximate — segments
            # aren't offset-tracked, so use the window's per-chunk modes).
            active = spk_track[mask]
            seg_masks = _approx_segment_speakers(active, len(segments))
            all_emb.append(embeddings)
            all_dur.extend(durations)
            all_t.extend([now] * len(segments))
            all_spk.extend(seg_masks)
        est = estimator.estimate(speech_ratio, dbfs(window))
        if est is None:
            continue
        bucket = smoother.update(est.log2_count, now)
        hops.append(
            dict(t=now, raw=est.raw_clusters, crowd=est.crowd_weight,
                 disp=est.dispersion, frag=est.fragmentation,
                 sr=speech_ratio, dbfs=dbfs(window),
                 bucket=bucket.value, smoothed=smoother.smoothed_log2)
        )
        print(f"{now:5.0f}  {est.raw_clusters:>3} {est.crowd_weight:>5.2f} "
              f"{est.dispersion:>5.3f} {est.fragmentation:>5.3f}  "
              f"{bucket.value:<6}  {smoother.smoothed_log2:.3f}")

    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        REPLAY_DIR / f"{name}.npz",
        embeddings=np.vstack(all_emb) if all_emb else np.empty((0, 192)),
        durations=np.array(all_dur),
        times=np.array(all_t),
        speaker_masks=np.array(all_spk),
        truth=truth,
        hops=json.dumps(hops),
    )

    final = hops[-1]["bucket"] if hops else None
    raws = [h["raw"] for h in hops]
    print(f"summary: final bucket {final}, raw clusters mode "
          f"{max(set(raws), key=raws.count) if raws else '-'}, "
          f"mean {np.mean(raws):.2f}" if raws else "no hops")
    return dict(name=name, truth=truth, hops=hops)


def _approx_segment_speakers(active_chunks: np.ndarray, n_segments: int) -> list[int]:
    """Split the window's active chunks into n_segments spans; per-span
    dominant bitmask. Approximate ground truth for candidate scoring."""
    if active_chunks.size == 0 or n_segments == 0:
        return [0] * n_segments
    spans = np.array_split(active_chunks, n_segments)
    out = []
    for span in spans:
        if span.size == 0:
            out.append(0)
            continue
        vals, counts = np.unique(span, return_counts=True)
        out.append(int(vals[np.argmax(counts)]))
    return out


def cmd_run(args: argparse.Namespace) -> int:
    embed = load_ecapa("speechbrain/spkrec-ecapa-voxceleb", args.threads)
    rng = np.random.default_rng(42)

    scenarios = [
        # (name, voices, overlap, uneven)
        ("solo_clean", ["david"], False, False),
        ("pair_clean", ["david", "zira"], False, False),
        ("trio_clean", ["david", "mark", "zira"], False, False),
        ("quad_clean", ["david", "mark", "zira", "zira_lo"], False, False),
        ("trio_overlap_clean", ["david", "mark", "zira"], True, False),
        ("solo_mic", ["david"], False, False),
        ("pair_similar_mic", ["david", "mark"], False, False),
        ("trio_mic", ["david", "mark", "zira"], False, False),
        ("trio_overlap_mic", ["david", "mark", "zira"], True, False),
        ("quad_mic", ["david", "mark", "zira", "zira_lo"], False, False),
        # The real-party airtime distribution (FIELD-NOTES 2026-07-10:
        # turn-taking starves each speaker's cluster of contiguous evidence).
        ("trio_uneven_clean", ["david", "mark", "zira"], False, True),
        ("trio_uneven_mic", ["david", "mark", "zira"], False, True),
        # Pool-regime proxy: 4 voices in dense overlap on a degraded channel —
        # the crowd blend must still engage (regression: the M7 sepfix must
        # not lobotomize the babble path).
        ("quad_overlap_mic", ["david", "mark", "zira", "zira_lo"], True, False),
    ]
    if args.only:
        scenarios = [s for s in scenarios if args.only in s[0]]

    results = []
    for name, voices, overlap, uneven in scenarios:
        audio, activity, speakers = assemble(voices, rng, overlap=overlap, uneven=uneven)
        if name.endswith("_mic"):
            audio = degrade(audio, rng, snr_db=args.snr_db)
        results.append(replay(name, audio, activity, speakers, embed, len(voices)))

    print("\n== overall ==")
    for r in results:
        raws = [h["raw"] for h in r["hops"]]
        final = r["hops"][-1]["bucket"] if r["hops"] else "-"
        under = sum(1 for x in raws if x < r["truth"]) / len(raws) if raws else 0
        print(f"{r['name']:<22} truth {r['truth']}  final bucket {final:<5} "
              f"mean raw {np.mean(raws):4.2f}  hops undercounting {under:4.0%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threads", type=int, default=0)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("synth")
    sub.add_parser("distances")
    run_p = sub.add_parser("run")
    run_p.add_argument("--only", help="substring filter on scenario names")
    run_p.add_argument("--snr-db", type=float, default=12.0)
    args = parser.parse_args()
    return {"synth": cmd_synth, "distances": cmd_distances, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
