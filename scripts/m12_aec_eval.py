"""M12-02 evaluation: run the playback canceller over recorded mic +
reference takes and measure it (docs/M12-02-PLAN.md, "Evaluation").

    python scripts/m12_aec_eval.py data/captures/ladder-20261009 [--takes T1-MO32 ...] [--json OUT]

Each take is `<prefix>-<take>.wav` (mic) + `.ref.wav` (loopback), as
written by `capture_room_wav.py --reference`. Per take:

- **cpu**: canceller seconds per audio second (one core).
- **relocks / divergence_resets / lag**: the delay tracker's re-locks
  (the first lock counts), filter resets by the divergence guard, and the
  final lag.
- **echo_tail_ms**: span of the learned impulse response holding 99 % of
  its energy (from the converged filter). This sets `PARTITIONS`.
- **erle_db** (music-only takes): median ERLE over 2 s windows in the take's
  second half, plus **converge_s**: the first time after lock that a 2 s
  window comes within 3 dB of that. Compare against the linear ceiling in
  the plan (~7–11 dB).
- **pause_erle_db** (mix takes): ERLE over 0.5 s windows where the raw VAD
  sees no speech (p < 0.2). That's the only music-only material at
  66 / 76 %.
- **eligible raw → clean**: windows certified as speech (5 s window, 2 s
  hop, speech ratio ≥ 0.2 at the 0.75 playback threshold, as in
  analyze_dominance_wav). The M12 payoff: T3-MO32 (rap alone) should
  fall; T2-MX76 (reading over loud piano) should rise.
- **ecapa_to_C1 raw → clean** (mix takes): cosine similarity of the take's
  mean speaker embedding to the founder's C1 (speech-only) embedding.
  Cancellation should move a reading toward the clean voice, not away.
- **bit_identical** (controls): the clean stream must equal the raw one.

Offline; reads consent-gated captures under data/ and prints numbers.
`--save-clean DIR` also writes each clean stream as a WAV for listening.
That is a derived recording of the same room audio, so it is off unless
asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_reference import read_wav  # noqa: E402
from sensing import aec  # noqa: E402

SR = aec.SR
TAKES = [
    "C1", "C4",
    "T1-MO32", "T2-MO32", "T3-MO32", "P1", "X-T1-MO32-atmosoff",
    "T1-MX32", "T1-MX66", "T1-MX76", "T2-MX32", "T2-MX66", "T2-MX76",
    "T3-MX32", "T3-MX66", "T3-MX76",
]
PLAYBACK_THR, MIN_RATIO = 0.75, 0.2  # engine certification during playback


def _db(num: float, den: float) -> float:
    return float(10 * np.log10((num + 1e-20) / (den + 1e-20)))


def erle_series(mic, clean, start_s, win_s=2.0):
    w = int(win_s * SR)
    out = []
    for a in range(int(start_s * SR), min(mic.size, clean.size) - w + 1, w):
        out.append((a / SR, _db(np.sum(mic[a : a + w] ** 2), np.sum(clean[a : a + w] ** 2))))
    return out


def echo_tail_ms(ir: np.ndarray | None) -> float | None:
    if ir is None or not np.any(ir):
        return None
    e = np.cumsum(ir**2)
    e /= e[-1]
    first = int(np.argmax(e > 0.005))
    last = int(np.argmax(e >= 0.995))
    return round(1000 * (last - first) / SR, 1)


def vad_probs(x: np.ndarray, vad_cls) -> np.ndarray:
    v = vad_cls(SR, 1e9, 0.5)  # rolling window large enough to keep every chunk
    v.load()
    v.feed(x)
    return np.asarray(v._probs)


def eligible_windows(probs: np.ndarray) -> int:
    per_win, per_hop = int(5.0 * SR / 512), int(2.0 * SR / 512)
    n = 0
    for a in range(0, probs.size - per_win + 1, per_hop):
        if np.mean(probs[a : a + per_win] >= PLAYBACK_THR) >= MIN_RATIO:
            n += 1
    return n


def mean_embedding(x, probs, embed, speech_segments):
    mask = probs >= PLAYBACK_THR
    segs = speech_segments(x[: mask.size * 512], mask, SR, max_segments=200)
    if not segs:
        return None
    e = embed(segs)
    m = e.mean(axis=0)
    return m / (np.linalg.norm(m) + 1e-12)


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())


def evaluate(prefix: str, takes: list[str], save_clean: Path | None) -> list[dict]:
    from sensing.headcount import load_ecapa, speech_segments
    from sensing.vad import VadGate

    embed = load_ecapa("speechbrain/spkrec-ecapa-voxceleb", 2)
    c1 = read_wav(Path(f"{prefix}-C1.wav"))
    c1_emb = mean_embedding(c1, vad_probs(c1, VadGate), embed, speech_segments)
    rows = []
    for take in takes:
        mic = read_wav(Path(f"{prefix}-{take}.wav"))
        ref = read_wav(Path(f"{prefix}-{take}.ref.wav"))
        t0 = time.perf_counter()
        res = aec.cancel(mic, ref)
        cpu = (time.perf_counter() - t0) / (mic.size / SR)
        clean = res.clean
        row = {
            "take": take,
            "cpu": round(cpu, 3),
            "relocks": res.relocks,
            "divergence_resets": res.divergence_resets,
            "rollbacks": res.rollbacks,
            "relock_choices": res.relock_choices,
            "lag_ms": None if res.delays[-1][1] is None else round(1000 * res.delays[-1][1] / SR, 1),
            "lock_coh": res.delays[-1][2],
            "echo_tail_ms": echo_tail_ms(res.impulse_response),
        }
        if not np.any(ref):
            row["bit_identical"] = bool(np.array_equal(clean, mic))
        else:
            first = res.relocks[0] if res.relocks else 0.0
            series = erle_series(mic, clean, first)
            if series:
                half = [e for t, e in series if t >= (first + mic.size / SR) / 2]
                steady = float(np.median(half)) if half else float(np.median([e for _, e in series]))
                row["erle_db"] = round(steady, 1)
                hit = next((t for t, e in series if e >= steady - 3.0), None)
                row["converge_s"] = None if hit is None else round(hit - first, 1)
        p_raw = vad_probs(mic, VadGate)
        p_clean = vad_probs(clean, VadGate)
        row["eligible_raw"] = eligible_windows(p_raw)
        row["eligible_clean"] = eligible_windows(p_clean)
        if "-MX" in take and np.any(ref):
            quiet = np.repeat(p_raw < 0.2, 512)[: mic.size]
            w = int(0.5 * SR)
            vals = [
                _db(np.sum(mic[a : a + w] ** 2), np.sum(clean[a : a + w] ** 2))
                for a in range(int((res.relocks[0] if res.relocks else 0) * SR), mic.size - w + 1, w)
                if quiet[a : a + w].mean() > 0.9
            ]
            row["pause_erle_db"] = round(float(np.median(vals)), 1) if vals else None
            row["pause_windows"] = len(vals)
            if c1_emb is not None:
                er = mean_embedding(mic, p_raw, embed, speech_segments)
                ec = mean_embedding(clean, p_clean, embed, speech_segments)
                row["ecapa_to_C1_raw"] = None if er is None else round(float(er @ c1_emb), 3)
                row["ecapa_to_C1_clean"] = None if ec is None else round(float(ec @ c1_emb), 3)
        if save_clean is not None:
            save_clean.mkdir(parents=True, exist_ok=True)
            write_wav(save_clean / f"{Path(prefix).name}-{take}.clean.wav", clean)
        print(json.dumps(row), flush=True)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prefix", help="e.g. data/captures/ladder-20261009")
    ap.add_argument("--takes", nargs="*", default=TAKES)
    ap.add_argument("--json")
    ap.add_argument("--save-clean", type=Path)
    a = ap.parse_args()
    rows = evaluate(a.prefix, a.takes, a.save_clean)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
