"""M12-06 offline: reference-based playback dominance, scored with the
dominance ladder's signed decision rule.

    python scripts/m12_dominance_reference.py data/captures/ladder-20261009 [--json OUT]

The 2026-10-09 ladder came out outcome C: the high-band *share* can't tell
speech from speech over music on JPad (FIELD-NOTES 2026-10-09, evening).
ROADMAP M12-06 replaces it with what the reference makes possible: the
fraction of the window's mic energy that RTR's own playback explains,

    m = E(echo estimate) / E(mic),    echo estimate = mic − clean

from the M12-02 canceller (`aec.cancel`, causal). m is already in [0, 1],
so there are no knots to fit. With no playback the echo estimate is
exactly zero, so speech-only windows read m = 0 by construction.

The schedule and eligibility mirror `analyze_dominance_wav.py`: 5 s
windows every 2 s, Silero at the playback threshold (0.75), eligible =
raw speech ratio ≥ 0.2. Each take is scored as clean (m ≤ 0.1, folded into
the baseline) or bankable (m ≥ 0.25, a pull sample), as the rule reads
them. Windows before the canceller's first lock read m = 0. That is a
start-up artefact of replaying each take from cold: a live session locks
once, not once per track. So each take is also reported from its first
lock on ("locked").

Offline; numbers only.
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
from sensing import aec  # noqa: E402
from sensing.config import Config  # noqa: E402
from sensing.vad import VadGate  # noqa: E402

M_MAX, M_FLOOR = 0.1, 0.25  # config music_baseline_m_max, music_pull_m_floor
TAKES = {
    "speech": ["C1", "C2", "C3", "C4"],
    "mix": ["T1-MX32", "T1-MX66", "T1-MX76", "T2-MX32", "T2-MX66", "T2-MX76",
            "T3-MX32", "T3-MX66", "T3-MX76"],
}


def windows(mic, clean, config, vad):
    """Per engine window: (end_s, m, eligible)."""
    sr = config.sample_rate
    win, hop = int(config.window_s * sr), int(config.hop_s * sr)
    echo = (mic - clean).astype(np.float64)
    out, fed = [], 0
    for start in range(0, mic.size - win + 1, hop):
        end = start + win
        vad.feed(mic[fed:end])
        fed = end
        e_mic = float(np.sum(mic[start:end].astype(np.float64) ** 2))
        e_echo = float(np.sum(echo[start:end] ** 2))
        m = 0.0 if e_mic <= 0 else min(1.0, e_echo / e_mic)
        ratio = vad.speech_ratio(config.vad_playback_threshold)
        out.append((end / sr, m, ratio >= config.emotion_min_speech_ratio))
    return out


def score(ms: np.ndarray) -> dict:
    if ms.size == 0:
        return {"n": 0}
    return {
        "n": int(ms.size),
        "clean": round(float((ms <= M_MAX).mean()), 3),
        "bankable": round(float((ms >= M_FLOOR).mean()), 3),
        "m_p10_p50_p90": [round(float(x), 3) for x in np.percentile(ms, [10, 50, 90])],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--json")
    a = ap.parse_args()
    config = Config()
    rows = []
    for kind, takes in TAKES.items():
        for take in takes:
            mic = read_wav(Path(f"{a.prefix}-{take}.wav"))
            ref = read_wav(Path(f"{a.prefix}-{take}.ref.wav"))
            res = aec.cancel(mic, ref)
            vad = VadGate(config.sample_rate, config.window_s, config.vad_threshold)
            vad.load()
            w = windows(mic, res.clean, config, vad)
            lock = res.relocks[0] if res.relocks else None
            elig = np.array([m for _, m, e in w if e])
            locked = np.array([m for t, m, e in w if e and lock is not None and t - 5.0 >= lock])
            row = {"kind": kind, "take": take, "first_lock_s": lock,
                   "all": score(elig), "locked": score(locked)}
            print(json.dumps(row), flush=True)
            rows.append(row)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
