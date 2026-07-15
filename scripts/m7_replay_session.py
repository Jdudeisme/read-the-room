"""Faithful session replay: drive a recorded gate WAV through the REAL
engine path — Silero VadGate (NOT the tts_harness energy mask) for the
speech mask/ratio, then speech_segments -> ECAPA -> HeadcountEstimator ->
BucketSmoother, mirroring engine.py's loop (5 s window, 2 s hop, headcount
every 4 s, cert threshold 0.5, playback off).

Written for the 2026-07-15 M7 gate recording (FIELD-NOTES entry): the
harness energy mask pins speech_ratio ~1.0 on conversational audio and
falsely engages the crowd path, so bucket-level claims from
`tts_harness.py replay-wav` do NOT transfer to the live engine — this
script's do. On that recording it measures: rescue ON -> buckets 4/6/8 on
137/281 hops (the live pair-overcount reproduced); rescue OFF (shipped
default) -> solo 126 / pair 110 / bucket-3 45, never above 3.

Usage:
    python scripts/m7_replay_session.py session.wav [--start-wallclock HH:MM:SS]

The WAV must be 16 kHz mono (afconvert -f WAVE -d LEI16@16000 -c 1 ...).
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sensing.headcount import (  # noqa: E402
    BucketSmoother,
    HeadcountEstimator,
    load_ecapa,
    speech_segments,
)
from sensing.vad import VadGate  # noqa: E402

SR = 16000
WINDOW_S, HOP_S, HC_INTERVAL_S = 5.0, 2.0, 4.0
VAD_THR = 0.5


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    return 20 * np.log10(max(rms, 1e-10))


def run(audio: np.ndarray, embed, rescue_enabled: bool, start_s: float):
    def wall(t: float) -> str:
        tot = start_s + t
        return f"{int(tot // 3600):02d}:{int(tot % 3600 // 60):02d}:{int(tot % 60):02d}"

    vad = VadGate(SR, WINDOW_S, VAD_THR)
    vad.load()
    est = HeadcountEstimator(rescue_enabled=rescue_enabled)
    sm = BucketSmoother()
    win, hop = int(WINDOW_S * SR), int(HOP_S * SR)
    fed = 0
    last_hc = -1e9
    buckets, aboves = [], []
    for start in range(0, len(audio) - win + 1, hop):
        end = start + win
        vad.feed(audio[fed:end])
        fed = end
        ratio = vad.speech_ratio(VAD_THR)
        now = end / SR
        if ratio < 0.2:  # engine's headcount_min_speech_ratio gate
            continue
        window = audio[start:end]
        segs = speech_segments(window, vad.speech_mask(VAD_THR), SR)
        if segs:
            est.add(embed(segs), [s.size / SR for s in segs], now)
        if now - last_hc < HC_INTERVAL_S:
            continue
        last_hc = now
        e = est.estimate(ratio, dbfs(window))
        if e is None:
            continue
        b = sm.update(e.log2_count, now)
        buckets.append(b.value)
        if b.value not in ("solo", "pair"):
            aboves.append((wall(now), round(dbfs(window), 1), round(ratio, 2),
                           e.raw_clusters, e.rescued_clusters,
                           round(e.crowd_weight, 2), b.value))
    return buckets, aboves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav", help="16 kHz mono WAV of the session")
    parser.add_argument("--start-wallclock", default="00:00:00",
                        help="wall-clock of the recording's first sample, HH:MM:SS")
    args = parser.parse_args()

    h, m, s = (int(p) for p in args.start_wallclock.split(":"))
    start_s = h * 3600 + m * 60 + s

    rate, data = wavfile.read(args.wav)
    if rate != SR:
        raise SystemExit(f"need 16 kHz input, got {rate}")
    audio = data.astype(np.float32) / 32768.0

    embed = load_ecapa("speechbrain/spkrec-ecapa-voxceleb", 2)
    for flag in (False, True):
        buckets, aboves = run(audio, embed, rescue_enabled=flag, start_s=start_s)
        print(f"\n===== rescue_enabled={flag} =====")
        print("bucket distribution:", dict(Counter(buckets)))
        print(f"hops above pair: {len(aboves)} / {len(buckets)}")
        print("above-pair hops (wall, dbfs, sr, raw, rescued, crowd, bucket):")
        for a in aboves:
            print("  ", a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
