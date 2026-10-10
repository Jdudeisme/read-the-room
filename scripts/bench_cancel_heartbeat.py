"""M12-02 step 4 / gate (c) groundwork: does running the playback canceller
beside the engine delay the DSP heartbeat?

    python scripts/bench_cancel_heartbeat.py data/captures/ladder-20261009-T3-MX66 --seconds 90

Replays a recorded take's mic WAV and reference WAV into real rings in real
time (30 ms chunks, like the audio callbacks), and runs the real `Engine`
with its emotion and headcount workers (models loaded, cached) twice: once
on raw, once with `CleanSource` cancelling. A consumer timestamps every
published frame. A tick is "late" by how much its interval exceeds the 2 s
hop. Invariant 3 says the heartbeat never blocks. The risk measured here:
the canceller's numpy work (9–16 % of one core) holding the GIL against the
tick.

No mic, no network: the "sources" are files. Prints tick-interval
percentiles per mode, plus CleanSource resyncs and status.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_reference import read_wav  # noqa: E402
from sensing.audio import CleanSource, RingBuffer  # noqa: E402
from sensing.config import Config  # noqa: E402
from sensing.engine import Engine  # noqa: E402

SR = 16_000
CHUNK = 480  # 30 ms, like a capture callback


class FileSource:
    """Writes a WAV into its ring in real time on a thread."""

    def __init__(self, x: np.ndarray, name: str, buffer_seconds: float):
        self.x = x
        self.sample_rate = SR
        self.capture_rate = SR
        self.device_name = name
        self.ring = RingBuffer(int(buffer_seconds * SR))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        t0 = time.monotonic()
        for i in range(0, self.x.size - CHUNK + 1, CHUNK):
            if self._stop.is_set():
                return
            target = t0 + (i + CHUNK) / SR
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.ring.write(self.x[i : i + CHUNK])


class Stamp:
    def __init__(self):
        self.t: list[float] = []
        self.streams: set[str] = set()

    def on_state(self, state):
        self.t.append(time.monotonic())
        self.streams.add(state.analysis_stream)


def run(mic, ref, seconds, cancel: bool):
    config = dataclasses.replace(
        Config.from_env(), music_signatures_path=None, playback_cancel_enabled=cancel,
    )
    buffer_s = config.window_s * 2 + 2.0
    mic_src = FileSource(mic, "file mic", buffer_s)
    ref_src = FileSource(ref, "file reference", buffer_s)
    clean = CleanSource(mic_src, ref_src, buffer_s) if cancel else None
    stamp = Stamp()
    eng = Engine(mic_src, config, [stamp], reference_source=ref_src if cancel else None, clean_source=clean)
    ticks = int(seconds / config.hop_s)
    eng.run(max_ticks=ticks)
    iv = np.diff(stamp.t)[3:]  # skip start-up while models load
    late = np.maximum(0.0, iv - config.hop_s)
    return {
        "mode": "cancel" if cancel else "raw",
        "ticks": len(stamp.t),
        "interval_p50_s": round(float(np.percentile(iv, 50)), 4),
        "interval_p95_s": round(float(np.percentile(iv, 95)), 4),
        "interval_max_s": round(float(iv.max()), 4),
        "late_max_ms": round(1000 * float(late.max()), 1),
        "streams": sorted(stamp.streams),
        "clean_status": None if clean is None else clean.status,
        "clean_resyncs": None if clean is None else clean.resyncs,
        "clean_error": None if clean is None else clean.error,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("take", help="basename: TAKE.wav + TAKE.ref.wav")
    ap.add_argument("--seconds", type=float, default=90.0)
    a = ap.parse_args()
    mic = read_wav(Path(a.take + ".wav"))
    ref = read_wav(Path(a.take + ".ref.wav"))
    for cancel in (False, True):
        print(run(mic, ref, a.seconds, cancel), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
