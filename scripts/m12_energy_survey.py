"""M12-04 offline: how much of the published energy is RTR's own music?

    python scripts/m12_energy_survey.py data/captures/ladder-20261009 [--json OUT]

ROADMAP M12-04: `energy_score` blends loudness (0.30), onset activity
(0.20), certified `speech_ratio` (0.25) and arousal (0.25). Today the DSP
heartbeat measures loudness and activity on the **raw** mic even when
cancellation runs (engine.py: "the DSP heartbeat reads raw"), so the
music's level and beat reach energy, and the energy trend steers the
mapper's `energy_action` whenever the arousal trend is flat.

This replays every 2026-10-09 ladder take through the engine's energy
path, on the engine's schedule (5 s windows every 2 s, EMA tau
`smooth_tau_dsp_s`, `TrendTracker` with the config horizon and slope),
three ways:

- **raw**: cancellation off (today's default). DSP and certification on
  the raw mic.
- **hybrid**: cancellation on, today's code. DSP on raw, certification on
  the clean stream (M12-02 step 4).
- **clean**: the M12-04 proposal. DSP and certification on the clean
  stream (`aec.cancel`, causal, bit-identical to the live `CleanSource`).

Arousal is left out: it comes from the emotion worker, which already reads
clean audio and is M6-corrected, and is not M12-04's to change. Energy is
therefore the three-term renormalised form `energy_score(..., None)`, the
same formula the engine uses when no emotion reading exists. The
certification threshold follows `playback_active` as the engine does:
0.75 on music takes, 0.5 on the speech-only controls.

Music takes are reported from the canceller's first lock on (before it,
clean = raw by construction; a live session locks once, not once per
track, as in `m12_dominance_reference.py`). Takes that never lock are
reported as such: there the clean stream *is* the raw one.

**Volume step (`--step`).** The ladder never changes volume inside a
take, and that is the feedback case M12-04 exists for: the DJ turns the
music up, raw loudness rises, and the energy trend reads "rising". So a
synthetic step is also run: a speech-only control (constant talk) plus a
music-only take's mic signal (the echo alone, nobody talking), with that
echo's gain stepped by `STEP_DB` halfway through, and the music-only
take's own reference. Sound adds linearly in air, and the reference is
pre-volume, so a gain on the echo is what a volume move looks like to the
canceller, apart from speaker non-linearity at high volume (the real
66/76 % takes carry that; this doesn't). Reported: the energy step and
how often the trend reads "rising" in the 60 s after the step, against
the 60 s before.

Offline; numbers only. No target numbers: the acceptance measurement is
the M12-05 live session.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_reference import read_wav  # noqa: E402
from sensing import aec, dsp  # noqa: E402
from sensing.config import Config  # noqa: E402
from sensing.state import Ema, TrendTracker, energy_score  # noqa: E402
from sensing.vad import VadGate  # noqa: E402

# kind -> takes. "reading" controls are the matched talk for the "mix"
# takes (same reader, same position N, reading aloud); C2 (far) and C3
# (conversation) are reported but not used as the match.
TAKES = {
    "speech": ["C1", "C2", "C3", "C4"],
    "music_only": ["T1-MO32", "T2-MO32", "T3-MO32", "X-T1-MO32-atmosoff"],
    "mix": ["T1-MX32", "T1-MX66", "T1-MX76", "T2-MX32", "T2-MX66", "T2-MX76",
            "T3-MX32", "T3-MX66", "T3-MX76"],
}
READING_MATCH = ["C1", "C4"]
MODES = ("raw", "hybrid", "clean")
# Synthetic volume step: talk control + echo of each music-only take.
STEP_TALK = ["C1", "C4"]
STEP_ECHO = ["T1-MO32", "T2-MO32", "T3-MO32"]
# 32 % -> ~66 %: at 66 % the locked echo fraction m runs 0.56-0.96
# (FIELD-NOTES 2026-10-09, night), i.e. echo ~ talk level, while the 32 %
# music-only echo sits ~6 dB under the reading controls (this survey).
# +8 dB is that move, rounded; the survey also runs +4 dB.
STEP_DBS = (4.0, 8.0)
STEP_AT_S = 90.0
STEP_SPAN_S = 60.0


def replay(mic: np.ndarray, clean: np.ndarray, config: Config, playback: bool) -> dict:
    """Per tick, per mode: (end_s, loudness, activity, speech, energy, trend)."""
    sr = config.sample_rate
    win, hop = int(config.window_s * sr), int(config.hop_s * sr)
    threshold = config.vad_playback_threshold if playback else config.vad_threshold
    vads = {}
    for stream in ("raw", "clean"):
        vads[stream] = VadGate(sr, config.window_s, config.vad_threshold)
        vads[stream].load()
    state = {
        m: {
            "loud": Ema(config.smooth_tau_dsp_s),
            "act": Ema(config.smooth_tau_dsp_s),
            "speech": Ema(config.smooth_tau_dsp_s),
            "trend": TrendTracker(config.trend_horizon_s, config.trend_slope_threshold),
            "rows": [],
        }
        for m in MODES
    }
    fed = 0
    for start in range(0, mic.size - win + 1, hop):
        end = start + win
        t = end / sr
        vads["raw"].feed(mic[fed:end])
        vads["clean"].feed(clean[fed:end])
        fed = end
        d_raw = dsp.analyze(mic[start:end], sr)
        d_clean = dsp.analyze(clean[start:end], sr)
        ratio = {s: vads[s].speech_ratio(threshold) for s in vads}
        inputs = {
            "raw": (d_raw, ratio["raw"]),
            "hybrid": (d_raw, ratio["clean"]),
            "clean": (d_clean, ratio["clean"]),
        }
        for m, (d, r) in inputs.items():
            s = state[m]
            loud = s["loud"].update(d.rms_dbfs, t)
            act = s["act"].update(d.onset_density, t)
            sp = s["speech"].update(r, t)
            e = energy_score(loud, act, sp, None)
            s["rows"].append((t, loud, act, sp, e, s["trend"].update(e, t)))
    return {m: state[m]["rows"] for m in MODES}


def summarize(rows: list[tuple], from_s: float | None) -> dict:
    if from_s is not None:
        rows = [r for r in rows if r[0] - 5.0 >= from_s]
    if not rows:
        return {"n": 0}
    a = np.array([r[1:5] for r in rows], dtype=np.float64)
    trends = [r[5] for r in rows]
    med = np.median(a, axis=0)
    p10, p90 = np.percentile(a[:, 3], [10, 90])
    return {
        "n": len(rows),
        "loudness_dbfs": round(float(med[0]), 1),
        "activity": round(float(med[1]), 2),
        "speech_ratio": round(float(med[2]), 3),
        "energy": round(float(med[3]), 3),
        "energy_p10_p90": [round(float(p10), 3), round(float(p90), 3)],
        # How often the energy trend alone would move the mapper's action.
        "trend_moving": round(sum(t != "stable" for t in trends) / len(trends), 3),
    }


def step_trial(prefix: str, talk: str, echo: str, step_db: float, config: Config) -> dict:
    """Talk + an echo whose gain steps up by step_db at STEP_AT_S."""
    sr = config.sample_rate
    speech = read_wav(Path(f"{prefix}-{talk}.wav"))
    music = read_wav(Path(f"{prefix}-{echo}.wav"))
    ref = read_wav(Path(f"{prefix}-{echo}.ref.wav"))
    n = min(speech.size, music.size)
    gain = np.ones(n, dtype=np.float32)
    gain[int(STEP_AT_S * sr):] = 10.0 ** (step_db / 20.0)
    mic = (speech[:n] + gain * music[:n]).astype(np.float32)
    res = aec.cancel(mic, ref)
    per_mode = replay(mic, res.clean, config, playback=True)
    out = {"talk": talk, "echo": echo, "step_db": step_db,
           "first_lock_s": res.relocks[0] if res.relocks else None,
           "relocks": len(res.relocks)}
    for m in MODES:
        rows = per_mode[m]
        before = [r for r in rows if STEP_AT_S - STEP_SPAN_S <= r[0] < STEP_AT_S]
        after = [r for r in rows if STEP_AT_S <= r[0] < STEP_AT_S + STEP_SPAN_S]
        e_b = float(np.median([r[4] for r in before]))
        e_a = float(np.median([r[4] for r in after]))
        out[m] = {
            "energy_step": round(e_a - e_b, 3),
            "loudness_step_db": round(float(np.median([r[1] for r in after])
                                            - np.median([r[1] for r in before])), 1),
            "rising_before": round(sum(r[5] == "rising" for r in before) / len(before), 3),
            "rising_after": round(sum(r[5] == "rising" for r in after) / len(after), 3),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--json")
    ap.add_argument("--step", action="store_true", help="run only the synthetic volume step")
    a = ap.parse_args()
    config = Config()
    if a.step:
        trials = []
        for talk in STEP_TALK:
            for echo in STEP_ECHO:
                for db in STEP_DBS:
                    t = step_trial(a.prefix, talk, echo, db, config)
                    print(json.dumps(t), flush=True)
                    trials.append(t)
        if a.json:
            Path(a.json).write_text(json.dumps(trials, indent=2) + "\n", encoding="utf-8")
        return 0
    rows = []
    for kind, takes in TAKES.items():
        playback = kind != "speech"
        for take in takes:
            mic = read_wav(Path(f"{a.prefix}-{take}.wav"))
            ref = read_wav(Path(f"{a.prefix}-{take}.ref.wav"))
            res = aec.cancel(mic, ref)
            lock = res.relocks[0] if res.relocks else None
            per_mode = replay(mic, res.clean, config, playback)
            # Speech controls play nothing: clean == raw bit for bit, all ticks count.
            from_s = lock if playback else None
            row = {
                "kind": kind, "take": take, "first_lock_s": lock,
                "locked": (lock is not None) or not playback,
                **{m: summarize(per_mode[m], from_s if lock is not None else None)
                   for m in MODES},
            }
            print(json.dumps(row), flush=True)
            rows.append(row)

    ref_e = {m: float(np.mean([r[m]["energy"] for r in rows if r["take"] in READING_MATCH]))
             for m in MODES}
    print("\nmix takes vs the reading controls (" + "+".join(READING_MATCH) + "), "
          "energy median minus control:")
    print(f"{'take':10s} {'lock':>6s} " + " ".join(f"{m:>8s}" for m in MODES))
    for r in rows:
        if r["kind"] != "mix":
            continue
        lk = "-" if r["first_lock_s"] is None else f"{r['first_lock_s']:.0f}s"
        print(f"{r['take']:10s} {lk:>6s} "
              + " ".join(f"{r[m]['energy'] - ref_e[m]:+8.3f}" for m in MODES))
    if a.json:
        out = {"reading_control_energy": ref_e, "takes": rows}
        Path(a.json).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
