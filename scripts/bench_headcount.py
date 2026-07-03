"""Benchmark the M2 headcount layer's per-window cost on this machine.

Run this on the demo MacBook, both modes:

    python scripts/bench_headcount.py               # standalone timing
    python scripts/bench_headcount.py --concurrent  # THE MILESTONE GATE

Budget arithmetic: hop = 2.0 s, emotion's measured floor = 0.63 s, leaving
~1.37 s for headcount (embedding + clustering + smoothing). But the real
constraint is CPU contention, not per-layer latencies summing — two torch
workers on a 2019 Intel chip fight for cores. The milestone gate is therefore
the CONCURRENT run:

  PASS requires BOTH:
    1. headcount p95 < 1.37 s while emotion inference runs on another thread
    2. emotion's concurrent mean stays within tolerance of its M1 baseline
       (0.63 s mean / 0.66 s p95) — i.e. headcount didn't silently steal
       emotion's budget

Pre-approved fallback if the gate fails: RTR_HEADCOUNT_MIN_INTERVAL_S=4.0
(headcount updates every other hop; people don't arrive at 2 s resolution).
Tune RTR_TORCH_THREADS (try 2) to limit oversubscription — note torch's
intra-op pool is process-global, so this caps both workers together.
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time

import numpy as np

from sensing.config import Config
from sensing.headcount import (
    BucketSmoother,
    HeadcountEstimator,
    load_ecapa,
    speech_segments,
)

# M1 measured baseline on the 2019 Intel MacBook Pro (bench_emotion.py).
M1_EMOTION_MEAN_S = 0.63
M1_EMOTION_P95_S = 0.66
EMOTION_DRIFT_TOLERANCE = 1.25  # concurrent mean may grow at most 25%

HEADCOUNT_BUDGET_S = 1.37  # 2.0 s hop minus emotion's 0.63 s floor


def _speech_like_window(rng: np.ndarray, window_s: float, n: int) -> np.ndarray:
    """Amplitude-modulated noise; spectrum is irrelevant for timing."""
    samples = int(window_s * 16_000)
    t = np.arange(samples) / 16_000
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t + n)
    return (rng.standard_normal(samples) * 0.1 * envelope).astype(np.float32)


def _full_speech_mask(window_s: float) -> np.ndarray:
    return np.ones(int(window_s * 16_000) // 512, dtype=bool)


def _one_headcount_pass(embed, estimator, smoother, window, mask, now) -> None:
    segments = speech_segments(window, mask, 16_000)
    if segments:
        embeddings = embed(segments)
        estimator.add(embeddings, [s.size / 16_000 for s in segments], now)
    est = estimator.estimate(speech_ratio=0.9, loudness_dbfs=-25.0)
    if est is not None:
        smoother.update(est.log2_count, now)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=20, help="timed passes (default 20)")
    parser.add_argument("--window", type=float, default=5.0, help="window seconds")
    parser.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = auto)")
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="run emotion inference on a second thread simultaneously — "
        "this mode is the milestone gate",
    )
    args = parser.parse_args()

    config = Config.from_env()
    print(f"headcount model: {config.headcount_model}")
    print(f"window: {args.window:.1f}s @ 16 kHz   runs: {args.runs}   "
          f"budget: {HEADCOUNT_BUDGET_S:.2f}s (p95)")

    t0 = time.perf_counter()
    embed = load_ecapa(config.headcount_model, args.threads, config.os_truststore)
    print(f"ECAPA load: {time.perf_counter() - t0:.1f}s (includes download on first run)")

    rng = np.random.default_rng(0)
    windows = [_speech_like_window(rng, args.window, i) for i in range(args.runs)]
    mask = _full_speech_mask(args.window)

    estimator = HeadcountEstimator()
    smoother = BucketSmoother()

    # Warmup: first passes pay one-time allocation costs, and pre-fill the
    # buffer so clustering runs at realistic size (near the cap), not empty.
    for i, w in enumerate(windows[: min(8, len(windows))]):
        _one_headcount_pass(embed, estimator, smoother, w, mask, float(i * 2))
    print(f"buffer after warmup: {estimator.evidence_s:.0f}s of speech evidence")

    # Optional concurrent emotion load — the realistic contention scenario.
    emotion_times: list[float] = []
    stop_emotion = threading.Event()
    emotion_thread = None
    if args.concurrent:
        from sensing.emotion import load_model

        print(f"loading emotion model for concurrent run: {config.emotion_model}")
        _, _, infer = load_model(config.emotion_model, args.threads, config.os_truststore)
        emo_window = rng.standard_normal(int(args.window * 16_000)).astype(np.float32) * 0.1
        infer(emo_window)  # warmup

        def emotion_loop() -> None:
            while not stop_emotion.is_set():
                t = time.perf_counter()
                infer(emo_window)
                emotion_times.append(time.perf_counter() - t)
                # Real engine submits at most once per 2 s hop.
                stop_emotion.wait(max(0.0, 2.0 - emotion_times[-1]))

        emotion_thread = threading.Thread(target=emotion_loop, daemon=True)
        emotion_thread.start()
        time.sleep(1.0)  # let it settle

    times: list[float] = []
    for i, w in enumerate(windows):
        t = time.perf_counter()
        _one_headcount_pass(embed, estimator, smoother, w, mask, float(100 + i * 2))
        times.append(time.perf_counter() - t)

    if emotion_thread is not None:
        stop_emotion.set()
        emotion_thread.join(timeout=10.0)

    mean = statistics.fmean(times)
    p95 = sorted(times)[max(0, int(len(times) * 0.95) - 1)]
    label = "CONCURRENT" if args.concurrent else "standalone"
    print(f"[{label}] headcount per-window: mean {mean:.2f}s   "
          f"min {min(times):.2f}s   max {max(times):.2f}s   ~p95 {p95:.2f}s")

    headcount_ok = p95 < HEADCOUNT_BUDGET_S
    emotion_ok = True
    if args.concurrent and emotion_times:
        emo_mean = statistics.fmean(emotion_times)
        emo_p95 = sorted(emotion_times)[max(0, int(len(emotion_times) * 0.95) - 1)]
        print(f"[CONCURRENT] emotion per-window:   mean {emo_mean:.2f}s   "
              f"~p95 {emo_p95:.2f}s   (M1 baseline: mean {M1_EMOTION_MEAN_S:.2f}s, "
              f"p95 {M1_EMOTION_P95_S:.2f}s)")
        emotion_ok = emo_mean <= M1_EMOTION_MEAN_S * EMOTION_DRIFT_TOLERANCE
        if not emotion_ok:
            print(f"  emotion drifted >{(EMOTION_DRIFT_TOLERANCE - 1) * 100:.0f}% past "
                  f"its M1 baseline under concurrent load — headcount is stealing "
                  f"its budget.")

    if headcount_ok and emotion_ok:
        margin = "comfortably" if p95 < HEADCOUNT_BUDGET_S * 0.75 else "with little margin"
        print(f"VERDICT: PASS — headcount p95 under the {HEADCOUNT_BUDGET_S:.2f}s "
              f"budget {margin}"
              + (", emotion unchanged from M1." if args.concurrent else
                 ". Now run with --concurrent — that mode is the milestone gate."))
    else:
        print(f"VERDICT: FAIL — apply the pre-approved fallback: set "
              f"RTR_HEADCOUNT_MIN_INTERVAL_S=4.0 in .env (headcount every other "
              f"hop). Also try RTR_TORCH_THREADS=2 to limit core "
              f"oversubscription, then re-run.")
    return 0 if (headcount_ok and emotion_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
