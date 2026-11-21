"""M12-01 probe analysis: mic vs playback reference, offline.

    python scripts/analyze_reference.py data/captures/ladder-DATE-T1-MO32 [more takes...]
    python scripts/analyze_reference.py TAKE ... --compare TAKE_A TAKE_B
    python scripts/analyze_reference.py TAKE --transport

Each TAKE is a basename written by `capture_room_wav.py --reference`:
`TAKE.wav` (mic), `TAKE.ref.wav` (loopback, 16 kHz mono) and `TAKE.json`
(sidecar with both clock logs). The questions it answers
(ROADMAP M12-01; docs/M12-PROPOSAL.md):

- **delay**: the lag from reference to mic, per 10 s window, by GCC-PHAT
  over ±`MAX_LAG_S`. Reported per take as the median and spread, plus the
  within-take slope.
- **drift**: the slope of the delay across a session's takes (by sidecar
  start time), and the clock-rate ratio from the two clock logs.
- **levels**: mic and reference dBFS. Across takes at different Windows
  volumes this answers Q2: is the loopback before or after the volume
  slider?
- **`--compare A B`**: reference band shares and per-octave level
  difference between two takes of the same track. Atmos on vs off answers
  Q1: is the loopback tapped before or after Dolby Atmos?
- **`--transport`**: when the reference is on and off (100 ms resolution),
  for comparing against the founder's noted play / pause / skip times.

Privacy: reads consent-gated captures under `data/`; prints numbers only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import wave
from pathlib import Path

import numpy as np

SR = 16_000
WIN_S = 10.0  # delay window
MAX_LAG_S = 0.5  # search range; acoustic + buffering delays are far below this
ACTIVE_DBFS = -60.0  # reference "on" threshold for transport and delay windows
OCTAVES = (63, 125, 250, 500, 1000, 2000, 4000)  # band centres, Hz (16 kHz audio)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as fh:
        assert fh.getframerate() == SR and fh.getnchannels() == 1, path
        pcm = np.frombuffer(fh.readframes(fh.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def dbfs(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-20))


def gcc_phat(mic: np.ndarray, ref: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Lag in samples by which `mic` trails `ref` (positive: mic later), and
    the PHAT peak height (0..1, higher = more confident)."""
    n = 1 << int(np.ceil(np.log2(mic.size + ref.size)))
    cross = np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ref, n))
    cross /= np.abs(cross) + 1e-12
    cc = np.fft.irfft(cross, n)
    cc = np.concatenate((cc[-max_lag:], cc[: max_lag + 1]))
    k = int(np.argmax(np.abs(cc)))
    return k - max_lag, float(np.abs(cc[k]))


def delays(mic: np.ndarray, ref: np.ndarray, win_s: float = WIN_S) -> list[dict]:
    """Per-window delay over windows where the reference is on."""
    w, max_lag = int(win_s * SR), int(MAX_LAG_S * SR)
    out = []
    for start in range(0, min(mic.size, ref.size) - w + 1, w):
        r = ref[start : start + w]
        if dbfs(r) < ACTIVE_DBFS:
            continue
        lag, peak = gcc_phat(mic[start : start + w], r, max_lag)
        out.append({"t": start / SR, "delay_ms": 1000.0 * lag / SR, "peak": peak})
    return out


def clock_ratio(mic_clock, ref_clock, ref_rate: int) -> float | None:
    """Reference clock rate relative to the mic clock, in parts per million,
    from the sidecar logs ([seconds since mic start, cumulative count]).
    0 = the two devices tick at the same rate."""
    if len(mic_clock) < 3 or len(ref_clock) < 3:
        return None
    mt, mn = np.asarray(mic_clock, float).T
    rt, rn = np.asarray(ref_clock, float).T
    mic_rate = np.polyfit(mt, mn, 1)[0]  # samples per second of monotonic time
    ref_rate_meas = np.polyfit(rt, rn, 1)[0]
    return float(1e6 * ((ref_rate_meas / ref_rate) / (mic_rate / SR) - 1.0))


def transport(ref: np.ndarray, hop_s: float = 0.1) -> list[tuple[float, float]]:
    """(start_s, end_s) spans where the reference is on."""
    h = int(hop_s * SR)
    on = [dbfs(ref[i : i + h]) >= ACTIVE_DBFS for i in range(0, ref.size - h + 1, h)]
    spans, start = [], None
    for k, v in enumerate(on + [False]):
        if v and start is None:
            start = k
        elif not v and start is not None:
            spans.append((round(start * hop_s, 1), round(k * hop_s, 1)))
            start = None
    return spans


def octave_levels(x: np.ndarray) -> dict[int, float]:
    spec = np.abs(np.fft.rfft(x.astype(np.float64))) ** 2
    f = np.fft.rfftfreq(x.size, 1.0 / SR)
    out = {}
    for c in OCTAVES:
        band = (f >= c / np.sqrt(2)) & (f < c * np.sqrt(2))
        out[c] = float(10 * np.log10(spec[band].sum() / x.size + 1e-20))
    return out


def load_take(base: str):
    b = Path(base)
    side = json.loads(b.with_suffix(".json").read_text(encoding="utf-8"))
    mic = read_wav(b.with_suffix(".wav"))
    ref = read_wav(b.parent / side["reference"]["wav"])
    return side, mic, ref


def summarize(base: str) -> dict:
    side, mic, ref = load_take(base)
    rs = side["reference"]
    d = delays(mic, ref)
    good = [x for x in d if x["peak"] >= 0.05]
    out = {
        "take": Path(base).name,
        "note": side.get("note", ""),
        "started_at": side["started_at"],
        "mic_dbfs": round(dbfs(mic), 1),
        "ref_dbfs": round(dbfs(ref), 1) if ref.size else None,
        "ref_on_fraction": round(
            sum(e - s for s, e in transport(ref)) / max(ref.size / SR, 1e-9), 3
        ),
        "windows": len(d),
        "confident_windows": len(good),
        "clock_ppm": clock_ratio(rs["mic_clock"], rs["ref_clock"], rs["native_rate"]),
        "max_block_gap_s": rs["max_block_gap_s"],
    }
    if good:
        dm = np.array([x["delay_ms"] for x in good])
        out.update(
            delay_ms_median=round(float(np.median(dm)), 2),
            delay_ms_iqr=round(float(np.subtract(*np.percentile(dm, [75, 25]))), 2),
            delay_slope_ms_per_min=(
                round(float(np.polyfit([x["t"] for x in good], dm, 1)[0] * 60), 3)
                if len(good) >= 3 else None
            ),
        )
    return out


def session_drift(summaries: list[dict]) -> float | None:
    """Slope of the per-take median delay against take start time, ms/min."""
    pts = [(dt.datetime.fromisoformat(s["started_at"]).timestamp(), s["delay_ms_median"])
           for s in summaries if "delay_ms_median" in s]
    if len(pts) < 2:
        return None
    t, d = np.asarray(pts).T
    return float(np.polyfit((t - t[0]) / 60.0, d, 1)[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("takes", nargs="+")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--transport", action="store_true")
    ap.add_argument("--json", help="also write the summaries here")
    a = ap.parse_args()
    summaries = [summarize(t) for t in a.takes]
    for s in summaries:
        print(json.dumps(s))
    drift = session_drift(summaries)
    print(f"session drift: {'n/a' if drift is None else f'{drift:.3f} ms/min'}")
    if a.transport:
        for t in a.takes:
            _, _, ref = load_take(t)
            print(Path(t).name, "reference on:", transport(ref))
    if a.compare:
        la, lb = (octave_levels(load_take(t)[2]) for t in a.compare)
        print(f"reference octave levels, {Path(a.compare[1]).name} minus {Path(a.compare[0]).name} (dB):")
        print("  " + "  ".join(f"{c} Hz {lb[c] - la[c]:+.1f}" for c in OCTAVES))
    if a.json:
        Path(a.json).write_text(json.dumps({"takes": summaries, "session_drift_ms_per_min": drift}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
