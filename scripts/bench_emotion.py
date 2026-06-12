"""Benchmark the emotion model's per-window inference cost on this machine.

Run this on the demo MacBook on day one:

    python scripts/bench_emotion.py

The engine submits one 5 s window every 2 s hop at most, and inference runs on
a dedicated worker thread, so the budget is: mean inference time < hop (2 s).
If this machine misses the budget, the documented fallbacks are, in order:

  1. Raise RTR_EMOTION_MIN_INTERVAL_S in .env (e.g. 6.0) — emotion updates
     less often, staleness reports it honestly, everything else unaffected.
  2. Swap RTR_EMOTION_MODEL to a smaller checkpoint (e.g. a wav2vec2-base
     valence/arousal fine-tune) and re-run this benchmark.
"""

from __future__ import annotations

import argparse
import statistics
import time

import numpy as np

from sensing.config import Config
from sensing.emotion import load_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10, help="timed inferences (default 10)")
    parser.add_argument("--window", type=float, default=5.0, help="window seconds (default 5)")
    parser.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = auto)")
    args = parser.parse_args()

    config = Config.from_env()
    print(f"model:   {config.emotion_model}")
    print(f"window:  {args.window:.1f}s @ 16 kHz   runs: {args.runs}")

    t0 = time.perf_counter()
    _, _, infer = load_model(config.emotion_model, args.threads, config.os_truststore)
    print(f"load:    {time.perf_counter() - t0:.1f}s (includes download on first run)")

    rng = np.random.default_rng(0)
    n = int(args.window * 16_000)
    # Speech-like spectrum is irrelevant for timing; amplitude-realistic noise is fine.
    windows = [rng.standard_normal(n).astype(np.float32) * 0.1 for _ in range(args.runs)]

    for w in windows[:2]:  # warmup: first passes pay one-time allocation costs
        infer(w)

    times = []
    for w in windows:
        t = time.perf_counter()
        infer(w)
        times.append(time.perf_counter() - t)

    mean = statistics.fmean(times)
    p95 = sorted(times)[max(0, int(len(times) * 0.95) - 1)]
    print(f"per-window inference: mean {mean:.2f}s   min {min(times):.2f}s   "
          f"max {max(times):.2f}s   ~p95 {p95:.2f}s")

    budget = config.hop_s
    if mean < budget * 0.75:
        print(f"VERDICT: OK - comfortably under the {budget:.0f}s hop budget.")
    elif mean < budget:
        print(f"VERDICT: TIGHT - under the {budget:.0f}s hop but with little margin. "
              f"Consider RTR_EMOTION_MIN_INTERVAL_S=4.0.")
    else:
        interval = max(4.0, round(mean * 2))
        print(f"VERDICT: OVER BUDGET - exceeds the {budget:.0f}s hop. "
              f"Set RTR_EMOTION_MIN_INTERVAL_S={interval:.1f} in .env, or switch "
              f"RTR_EMOTION_MODEL to a smaller checkpoint (see module docstring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
