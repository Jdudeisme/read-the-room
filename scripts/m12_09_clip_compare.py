"""M12-09: compare the live canceller with offline `aec.cancel()` on the same
audio (docs/M12-09-PLAN.md, step 5).

The dashboard writes the diagnostic clip when RTR_DEBUG_CANCEL_CLIP_S > 0:
`<base>.mic.wav`, `<base>.clean.wav` (what the live CleanSource produced),
`<base>.ref.wav` (the reference at the mic's aligned position, as
capture_room_wav.py records it) and `<base>.json` (the live 10 s reports
covering the clip).

    python scripts/m12_09_clip_compare.py data/captures/live-clip-YYYYmmdd-HHMMSS

Per 5 s window it prints the live reduction (mic vs live clean), the
offline reduction (mic vs a fresh offline cancel of the same mic +
reference), and the reference level. Then come the offline canceller's lag
timeline and re-locks, and the live reports. Reading it:
- **offline cancels, live didn't:** the live path is at fault: the
  canceller's state carried into the clip, or the alignment;
- **neither cancels, with lag jumps in the offline timeline:** the
  reference itself slips (dropouts), the M12-09 hypothesis;
- **neither cancels, with a steady lag:** the audio (double talk, level,
  nonlinearity).

A fresh offline canceller starts cold, so its first ~8-30 s (first lock
plus convergence) is excluded from the comparison: windows before
first lock + 10 s print as "settling".
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sensing import aec  # noqa: E402

WIN_S = 5.0


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as fh:
        if fh.getframerate() != aec.SR or fh.getnchannels() != 1:
            raise SystemExit(f"{path}: expected {aec.SR} Hz mono")
        return np.frombuffer(fh.readframes(fh.getnframes()), "<i2").astype(np.float32) / 32768.0


FLOOR = float("inf")  # clean cancelled to digital zero (16-bit quantization floor)


def _db(num: float, den: float) -> float | None:
    if num > 0 and den == 0:
        return FLOOR
    return 10 * np.log10(num / den) if num > 0 and den > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="clip path without the .mic.wav/.clean.wav/.ref.wav suffix")
    a = ap.parse_args()
    base = a.base
    mic = _read(Path(f"{base}.mic.wav"))
    live = _read(Path(f"{base}.clean.wav"))
    ref = _read(Path(f"{base}.ref.wav"))
    n = min(mic.size, live.size, ref.size)
    mic, live, ref = mic[:n], live[:n], ref[:n]
    off = aec.cancel(mic, ref)
    offline = off.clean[:n]
    settle_until = (off.relocks[0] + 10.0) if off.relocks else float("inf")

    print(f"clip {base}: {n / aec.SR:.1f} s")
    print(f"{'window':>11}  {'live dB':>8}  {'offline dB':>10}  {'ref dBFS':>8}")
    W = int(WIN_S * aec.SR)
    rows = []
    for s in range(0, n - W + 1, W):
        sl = slice(s, s + W)
        em = float(np.dot(mic[sl], mic[sl]))
        lv = _db(em, float(np.dot(live[sl], live[sl])))
        of = _db(em, float(np.dot(offline[sl], offline[sl])))
        er = float(np.dot(ref[sl], ref[sl])) / W
        rdb = 10 * np.log10(er) if er > 0 else None
        t0 = s / aec.SR
        settling = t0 < settle_until
        rows.append((lv, None if settling else of))
        fmt = lambda v: "   n/a" if v is None else (" floor" if v == FLOOR else f"{v:6.1f}")
        print(f"{t0:5.0f}-{t0 + WIN_S:<4.0f}s  {fmt(lv):>8}  "
              f"{'settling' if settling else fmt(of):>10}  {fmt(rdb):>8}")
    pairs = [(lv, of) for lv, of in rows
             if lv is not None and of is not None and FLOOR not in (lv, of)]
    if pairs:
        lv_med = float(np.median([p[0] for p in pairs]))
        of_med = float(np.median([p[1] for p in pairs]))
        print(f"median over {len(pairs)} settled windows: live {lv_med:.1f} dB, offline {of_med:.1f} dB")

    print("\noffline canceller: first lock "
          f"{off.relocks[0]:.1f} s" if off.relocks else "\noffline canceller: never locked")
    print(f"  re-locks at {[round(t, 1) for t in off.relocks]} choices {off.relock_choices}")
    print(f"  divergence resets {off.divergence_resets}, rollbacks {off.rollbacks}")
    lags = [(round(t, 1), lag) for t, lag, _ in off.delays if lag is not None]
    jumps = [(t, b - a_) for (t, b), (_, a_) in zip(lags[1:], lags[:-1]) if b != a_]
    print(f"  lag changes (t s, samples): {jumps[:40]}{' …' if len(jumps) > 40 else ''}")

    side_path = Path(f"{base}.json")
    if side_path.exists():
        side = json.loads(side_path.read_text(encoding="utf-8"))
        zf = side.get("ref_zero_fraction")
        if zf is None:
            print("\nWARNING: this clip predates the delayed reference read (FIELD-NOTES "
                  "2026-11-23): its ref track may be mostly zero-fill")
        elif zf > 0.5:
            print(f"\nWARNING: the ref track is {zf:.0%} exact zeros: the reference wasn't there "
                  "to read, so the offline column is not a fair comparison")
        print(f"\nlive reports during the clip (resyncs during: {side.get('resyncs_during')}):")
        for r in side.get("reports", []):
            print("  ", {k: r.get(k) for k in ("locked", "lag", "coherence", "relocks_new",
                                                "reduction_db", "ref_dbfs", "ref_lost_ms_new")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
