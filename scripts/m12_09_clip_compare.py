"""M12-09: compare the live canceller with offline `aec.cancel()` on the same
audio (docs/M12-09-PLAN.md, step 5).

The dashboard writes the diagnostic clip when RTR_DEBUG_CANCEL_CLIP_S > 0:
`<base>.mic.wav`, `<base>.clean.wav` (what the live CleanSource produced),
`<base>.ref.wav` (the reference at the mic's aligned position, as
capture_room_wav.py records it) and `<base>.json` (the live 10 s reports
covering the clip).

    python scripts/m12_09_clip_compare.py data/captures/live-clip-YYYYmmdd-HHMMSS
    python scripts/m12_09_clip_compare.py --capture data/captures/m12s-20261120-G-MX-H32
    python scripts/m12_09_clip_compare.py CLIP --phase G1=14:02:10 --phase G2=14:07:05 --phase G3=14:10:00

`--capture` reads a capture_room_wav.py take (`<base>.wav` + `<base>.ref.wav`)
with no live clean: offline columns only (used to validate the measures).
`--phase NAME=HH:MM:SS` (repeatable; local wall-clock start times on the clip's
date) adds per-phase medians, placing each window by the sidecar's
`started_at`. Windows within TRANSITION_S of a phase start are left out.

Per 5 s window it prints two measures, each for live and offline:
- **energy reduction**, E(mic) / E(clean) in dB. This is what the 10 s log
  lines report. **Talk caps it:** the canceller removes only the music,
  so the more people talk, the lower it reads even when cancellation is
  perfect;
- **music reduction** ("coh"): the energy linearly predictable from the
  reference, in the mic vs in the clean, in dB. Speech is incoherent with
  the reference and drops out, so this one doesn't fall just because
  people talk (2026-11-23 decision; the group session's readings R2, R2b
  and R4 use it). It is bias-corrected for the talk a finite average
  leaks in (`music_energy`). It still reads low for large reductions under
  heavy talk (true 20 dB reads ~12-18 dB with talk at 1-2x the music), so
  a large value is "at least".

Then: the share of windows where clean is LOUDER than the mic (only the
canceller can add energy; speech can't cause it; 31 % of frames on
2026-11-21), the offline canceller's lag timeline and re-locks, and the
live reports. Reading it:
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
from scipy.signal import csd, welch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sensing import aec  # noqa: E402

WIN_S = 5.0
# 256 ms segments: longer than the alcove's echo tail (the canceller's filter
# spans 208 ms, PARTITIONS x BLOCK), so one segment holds both an echo and its
# source. 5 s windows then average ~38 half-overlapped segments (bias ~0.03).
COH_NPERSEG = 4096
COH_BAND = aec._COH_BAND  # the delay tracker's band: 200-4000 Hz
# Talk doesn't stop or start on the second the founder notes: windows starting
# within this long after a phase start belong to neither phase.
TRANSITION_S = 10.0


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


def estimate_lag(mic: np.ndarray, ref: np.ndarray) -> int:
    """Whole-clip GCC-PHAT peak within ±MAX_LAG_S: samples the mic trails the
    reference (aec's sign: mic[n] ≈ (h * ref)[n - lag])."""
    n = 1 << int(np.ceil(np.log2(2 * mic.size)))
    x = np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ref, n))
    cc = np.fft.irfft(x / (np.abs(x) + 1e-12), n)
    L = int(aec.MAX_LAG_S * aec.SR)
    lags = np.arange(-L, L + 1)
    return int(lags[np.argmax(np.concatenate([cc[-L:], cc[: L + 1]]))])


def canceller_lag(off) -> int | None:
    """The offline canceller's most-held lag (its tracker rescores GCC peaks by
    coherence, which rejects the false peaks rhythmic music produces; aec.py)."""
    lags = [lg for _, lg, _ in off.delays if lg is not None]
    if not lags:
        return None
    vals, counts = np.unique(lags, return_counts=True)
    return int(vals[np.argmax(counts)])


def phase_of(wall: float, starts: list[tuple[str, float]]) -> str | None:
    """The phase a window starting at `wall` belongs to (starts sorted by time),
    or None before the first start or inside a transition."""
    name = None
    for nm, t in starts:
        if wall >= t:
            name, since = nm, wall - t
    if name is None or since < TRANSITION_S:
        return None
    return name


def align_ref(ref: np.ndarray, lag: int) -> np.ndarray:
    """The reference shifted to line up with the mic, keeping the canceller's
    PRE_DELAY of margin: ref_aligned[n] = ref[n - (lag - PRE_DELAY)], zero
    outside the clip."""
    d = lag - aec.PRE_DELAY
    out = np.zeros_like(ref)
    if d >= 0:
        out[d:] = ref[: ref.size - d]
    else:
        out[: ref.size + d] = ref[-d:]
    return out


def music_energy(x: np.ndarray, ref_aligned: np.ndarray) -> float:
    """Energy of x that is linearly predictable from the (aligned) reference,
    over COH_BAND: Σ |S_xr|² / S_rr, i.e. Σ coherence × S_xx, bias-corrected.

    Talk is incoherent with the reference, but a finite average still leaks
    ≈ 1/K_eff of it into |S_xr|²/S_rr. The correction subtracts that expected
    leak, (S_xx − P_coh) / (K_eff − 1), clipped at 0. K_eff is the Welch
    segment count over 1.056, the variance factor for Hann windows at 50 %
    overlap. On synthetic echo + talk at up to twice the music's level, for
    true reductions ≤ 10 dB it reads within 1.5 dB of the truth (uncorrected:
    up to 3.5 dB low; tests/test_m12_scripts.py)."""
    nper = min(COH_NPERSEG, x.size)
    f, sxr = csd(x, ref_aligned, fs=aec.SR, nperseg=nper)
    _, srr = welch(ref_aligned, fs=aec.SR, nperseg=nper)
    _, sxx = welch(x, fs=aec.SR, nperseg=nper)
    band = (f >= COH_BAND[0]) & (f < COH_BAND[1]) & (srr > 0)
    p_coh = np.abs(sxr[band]) ** 2 / srr[band]
    k_eff = (1 + (x.size - nper) // (nper // 2)) / 1.056
    if k_eff > 1:
        p_coh = np.maximum(p_coh - (sxx[band] - p_coh) / (k_eff - 1), 0.0)
    return float(np.sum(p_coh))


def music_reduction_db(mic: np.ndarray, clean: np.ndarray, ref_aligned: np.ndarray) -> float | None:
    """10·log10 of the reference-predictable energy in mic over that in clean."""
    return _db(music_energy(mic, ref_aligned), music_energy(clean, ref_aligned))


def _fmt(v):
    return "   n/a" if v is None else (" floor" if v == FLOOR else f"{v:6.1f}")


def _median(vals):
    vals = [v for v in vals if v is not None and v != FLOOR]
    return float(np.median(vals)) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="clip path without the .mic.wav/.clean.wav/.ref.wav suffix")
    ap.add_argument("--capture", action="store_true",
                    help="base is a capture_room_wav.py take (<base>.wav + <base>.ref.wav); offline only")
    ap.add_argument("--phase", action="append", default=[], metavar="NAME=HH:MM:SS",
                    help="phase start, local wall clock on the clip's date (repeatable)")
    a = ap.parse_args()
    base = a.base
    if a.capture:
        mic, ref, live = _read(Path(f"{base}.wav")), _read(Path(f"{base}.ref.wav")), None
        n = min(mic.size, ref.size)
    else:
        mic = _read(Path(f"{base}.mic.wav"))
        live = _read(Path(f"{base}.clean.wav"))
        ref = _read(Path(f"{base}.ref.wav"))
        n = min(mic.size, live.size, ref.size)
        live = live[:n]
    mic, ref = mic[:n], ref[:n]
    off = aec.cancel(mic, ref)
    offline = off.clean[:n]
    settle_until = (off.relocks[0] + 10.0) if off.relocks else float("inf")
    lag, lag_src = canceller_lag(off), "offline canceller's tracker"
    if lag is None:
        lag, lag_src = estimate_lag(mic, ref), "whole-clip GCC-PHAT"
    ref_al = align_ref(ref, lag)
    side_path = Path(f"{base}.json")
    side = (json.loads(side_path.read_text(encoding="utf-8"))
            if not a.capture and side_path.exists() else None)
    starts: list[tuple[str, float]] = []
    if a.phase:
        if side is None:
            raise SystemExit("--phase needs the clip's sidecar (started_at)")
        import datetime as _dt
        day = _dt.datetime.fromtimestamp(side["started_at"]).date()
        for spec in a.phase:
            nm, hms = spec.split("=", 1)
            t = _dt.datetime.combine(day, _dt.time.fromisoformat(hms)).timestamp()
            starts.append((nm, t))
        starts.sort(key=lambda x: x[1])

    print(f"{'capture' if a.capture else 'clip'} {base}: {n / aec.SR:.1f} s, "
          f"music-measure lag {lag} samples ({lag / aec.SR * 1000:.0f} ms, {lag_src})")
    print(f"{'window':>11}  {'live dB':>8}  {'offline dB':>10}  "
          f"{'live coh':>8}  {'off coh':>8}  {'ref dBFS':>8}")
    W = int(WIN_S * aec.SR)
    rows = []
    louder = 0
    by_phase: dict[str, list] = {}
    for s in range(0, n - W + 1, W):
        sl = slice(s, s + W)
        em = float(np.dot(mic[sl], mic[sl]))
        lv = lc = None
        if live is not None:
            el = float(np.dot(live[sl], live[sl]))
            lv = _db(em, el)
            lc = music_reduction_db(mic[sl], live[sl], ref_al[sl])
            louder += el > em
        t0 = s / aec.SR
        ph = phase_of(side["started_at"] + t0, starts) if starts else None
        of = _db(em, float(np.dot(offline[sl], offline[sl])))
        oc = music_reduction_db(mic[sl], offline[sl], ref_al[sl])
        er = float(np.dot(ref[sl], ref[sl])) / W
        rdb = 10 * np.log10(er) if er > 0 else None
        settling = t0 < settle_until
        row = (lv, lc, None if settling else of, None if settling else oc,
               live is not None and el > em)
        rows.append(row)
        if ph is not None and not settling:
            by_phase.setdefault(ph, []).append(row)
        print(f"{t0:5.0f}-{t0 + WIN_S:<4.0f}s  {_fmt(lv):>8}  "
              f"{'settling' if settling else _fmt(of):>10}  {_fmt(lc):>8}  "
              f"{'settling' if settling else _fmt(oc):>8}  {_fmt(rdb):>8}"
              f"{'  ' + ph if ph else ''}")
    settled = [r for r in rows if r[2] is not None]
    print(f"\nsettled windows: {len(settled)}")
    for name, i in (("live energy", 0), ("live music (coh)", 1),
                    ("offline energy", 2), ("offline music (coh)", 3)):
        m = _median([r[i] for r in settled])
        print(f"  median {name:<20} {'n/a' if m is None else f'{m:.1f} dB'}")
    if live is not None and rows:
        print(f"  live clean louder than the mic: {louder} of {len(rows)} windows "
              f"({louder / len(rows):.0%})")
    if starts:
        print("\nper phase (settled windows, transitions excluded):")
        print(f"  {'phase':<6} {'n':>3}  {'live dB':>8} {'off dB':>8}  {'live coh':>8} {'off coh':>8}  louder")
        for nm, _ in starts:
            rs = by_phase.get(nm, [])
            meds = [_median([r[i] for r in rs]) for i in (0, 2, 1, 3)]
            lf = f"{sum(r[4] for r in rs) / len(rs):.0%}" if rs and live is not None else "n/a"
            print(f"  {nm:<6} {len(rs):>3}  " + "  ".join(
                f"{'n/a' if m is None else f'{m:.1f}':>8}" for m in meds[:2]) + "  " + "  ".join(
                f"{'n/a' if m is None else f'{m:.1f}':>8}" for m in meds[2:]) + f"  {lf}")

    print("\noffline canceller: first lock "
          f"{off.relocks[0]:.1f} s" if off.relocks else "\noffline canceller: never locked")
    print(f"  re-locks at {[round(t, 1) for t in off.relocks]} choices {off.relock_choices}")
    print(f"  divergence resets {off.divergence_resets}, rollbacks {off.rollbacks}")
    lags = [(round(t, 1), lg) for t, lg, _ in off.delays if lg is not None]
    jumps = [(t, b - a_) for (t, b), (_, a_) in zip(lags[1:], lags[:-1]) if b != a_]
    print(f"  lag changes (t s, samples): {jumps[:40]}{' …' if len(jumps) > 40 else ''}")

    if side is not None:
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
