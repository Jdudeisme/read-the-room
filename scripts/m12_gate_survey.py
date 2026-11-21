"""M12-03 offline survey: what would a residual-vs-echo certification gate
decide on the 2026-10-09 captures?

    python scripts/m12_gate_survey.py data/captures/ladder-20261009 [--json OUT]

M12-03's charter starts from the cheapest evidence: "post-cancellation
residual level against the reference level". Here the reference's
contribution is measured through the canceller's own echo estimate
(mic − clean), so the comparison is in mic units and needs no gain.

Per Silero chunk (512 samples): r = 10·log10(E_clean / E_echo), both energy
sums smoothed over 3 chunks (96 ms).
- r ≫ 0: the chunk holds much more than the playback the canceller
  removed. The room is talking.
- r ≲ 0: what is left is no louder than the echo removed. The chunk is
  playback residual.
- No playback (echo estimate exactly zero): r = +∞, never refused.

A chunk certifies (as in the engine, during playback) if Silero p ≥ 0.75.
The gate refuses a certified chunk when r < threshold. Reported per take:
the certified-chunk fraction raw, clean, and clean + gate at several
thresholds, and the 5 s windows that would be eligible (ratio ≥ 0.2).
Music-only takes measure false certification (ideally 0). Speech-only
controls and the founder-reading mix takes measure what real speech
keeps (ideally near C1's level).

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
from m12_aec_eval import eligible_windows, vad_probs  # noqa: E402
from sensing import aec  # noqa: E402

CHUNK = 512
THR = 0.75
GATE_DB = (-6.0, -3.0, 0.0, 3.0, 6.0)
TAKES = [
    "C1", "C3", "C4",
    "T1-MO32", "T2-MO32", "T3-MO32", "P1",
    "T1-MX32", "T1-MX66", "T1-MX76", "T2-MX32", "T2-MX66", "T2-MX76",
    "T3-MX32", "T3-MX66", "T3-MX76",
]


def chunk_ratio_db(clean: np.ndarray, echo: np.ndarray, n_chunks: int, smooth: int = 3) -> np.ndarray:
    n = n_chunks * CHUNK
    ec = (clean[:n].astype(np.float64) ** 2).reshape(n_chunks, CHUNK).sum(1)
    ee = (echo[:n].astype(np.float64) ** 2).reshape(n_chunks, CHUNK).sum(1)
    k = np.ones(smooth)
    ec, ee = np.convolve(ec, k, mode="same"), np.convolve(ee, k, mode="same")
    with np.errstate(divide="ignore"):
        r = 10 * np.log10(ec / ee)
    r[ee == 0] = np.inf
    return r


def survey(prefix: str, takes: list[str]) -> list[dict]:
    from sensing.vad import VadGate

    rows = []
    for take in takes:
        mic = read_wav(Path(f"{prefix}-{take}.wav"))
        ref = read_wav(Path(f"{prefix}-{take}.ref.wav"))
        clean = aec.cancel(mic, ref).clean
        echo = mic - clean
        p_raw, p_clean = vad_probs(mic, VadGate), vad_probs(clean, VadGate)
        n = min(p_raw.size, p_clean.size)
        r = chunk_ratio_db(clean, echo, n)
        cert_raw, cert_clean = p_raw[:n] >= THR, p_clean[:n] >= THR
        row = {
            "take": take,
            "cert_raw": round(float(cert_raw.mean()), 3),
            "cert_clean": round(float(cert_clean.mean()), 3),
            "elig_raw": eligible_windows(p_raw[:n]),
            "elig_clean": eligible_windows(p_clean[:n]),
        }
        for g in GATE_DB:
            kept = cert_clean & (r >= g)
            # eligible windows if refused chunks count as uncertified
            gated = np.where(kept, 1.0, 0.0)
            row[f"cert_gate{g:+.0f}"] = round(float(kept.mean()), 3)
            row[f"elig_gate{g:+.0f}"] = eligible_windows(gated)
        finite = r[cert_clean & np.isfinite(r)]
        row["r_certified_p10_p50_p90"] = (
            [round(float(x), 1) for x in np.percentile(finite, [10, 50, 90])] if finite.size else None
        )
        print(json.dumps(row), flush=True)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--takes", nargs="*", default=TAKES)
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = survey(a.prefix, a.takes)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
