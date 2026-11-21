"""M12 gate part (a) harness: replay the 2026-09-30 part (f) recording with a
pluggable playback gate at the certification point.

    python scripts/m12_partf_replay.py data/captures/m7-partf-2026-09-30-16k.wav OUT.jsonl
    python scripts/m12_partf_replay.py WAV OUT.jsonl --gate NAME

ROADMAP M12 gate (a): "the 2026-09-30 recording replayed through the M12-03
gate, with the per-segment table compared against the 'before' table in
FIELD-NOTES 2026-09-30 (night), from `data/partf-replay/` or a regenerated
equivalent". That "before" was produced by a local, uncommitted script
(`data/partf-replay/replay_partf.py`). This is the committed equivalent, so
gate (a) is reproducible from the repo.

**Mirrors the 09-30 session's JPad config, not `Config()` defaults**
(CLAUDE.md: state which config a replay mirrors): headcount interval 2.0 s,
min speech ratio 0.2, certification threshold 0.75 while playback is
active and 0.5 otherwise, the rolling noise floor (`Ema` tau 60 s over
windows with raw ratio < 0.1) passed to the estimator, rescue OFF,
estimator and smoother defaults, ECAPA on 2 torch threads. `playback`
comes from the session timeline below. With `--gate none` (the default),
every output line must equal `data/partf-replay/faithful.jsonl`.

**The gate hook.** A gate sees one 5 s window, the VAD's per-chunk
probabilities and the certified mask (one entry per 512-sample chunk,
oldest first, aligned to the window's end), and whether playback is
active. It returns the chunks it refuses. A refused chunk is uncertified
for everything downstream, which is exactly what an M12-03 gate at the
engine's certification point would do (invariant 2). Gates are offline
candidates here, never engine code.

The recording has no reference channel, so only reference-free gates can
be evaluated on it (the charter says so).

Privacy: reads a consent-gated recording under `data/` (gitignored);
writes per-hop numbers only, no audio.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sensing.headcount import (  # noqa: E402
    BucketSmoother,
    HeadcountEstimator,
    load_ecapa,
    speech_segments,
)
from sensing.state import Ema  # noqa: E402
from sensing.vad import VadGate  # noqa: E402

SR, WINDOW_S, HOP_S = 16_000, 5.0, 2.0
CHUNK = 512  # Silero's chunk at 16 kHz (vad._CHUNK)
HC_INTERVAL_S, MIN_SPEECH_RATIO = 2.0, 0.2  # JPad .env, 2026-09-30
THR_IDLE, THR_PLAYBACK = 0.5, 0.75  # RTR_VAD_PLAYBACK_THRESHOLD on JPad
FLOOR_TAU_S = 60.0  # RTR_NOISE_FLOOR_TAU_S
# The recording starts at ~15:00:30: the 45 s level gap at offset ~540 s is
# the 15:09:29.9 accidental Skip (FIELD-NOTES 2026-09-30, night). The 09-30
# replay used 15:00:30.
START_WALL = "15:00:30"

# Ground truth and playback timeline, wall clock (session log + founder;
# copied from data/partf-replay/replay_partf.py so the replays agree).
TRUTH = [("14:55:00", 2), ("15:00:32", 3), ("15:26:00", 2), ("15:27:00", 1)]
MUSIC = [
    ("15:00:00", "MF DOOM Tick Tock (vocal hip-hop)", True),
    ("15:05:34", "MF DOOM Rhymes Like Dimes (instrumental)", True),
    ("15:07:46", "Swanee River (big band)", True),
    ("15:09:30", "NO MUSIC (skip gap)", False),
    ("15:10:15", "manual Hip-Hop/mid playlist", True),
    ("15:14:13", "jazz (Sophisticated Lady onward)", True),
]


def _parse(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def _lookup(table, wall):
    cur = None
    for row in table:
        if wall >= _parse(row[0]):
            cur = row
    return cur


def _dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt((x.astype(np.float64) ** 2).mean())), 1e-10))


def chunk_audio(window: np.ndarray, n_chunks: int, window_end: int, fed_end: int) -> np.ndarray:
    """The audio of each mask chunk, shape (n_chunks, CHUNK). Chunks are
    contiguous from sample 0, so the last complete chunk ends at
    floor(fed_end / CHUNK) * CHUNK, which may fall before the window's end."""
    last_end = (fed_end // CHUNK) * CHUNK
    start_in_window = (last_end - n_chunks * CHUNK) - (window_end - window.size)
    out = np.zeros((n_chunks, CHUNK), dtype=np.float32)
    for k in range(n_chunks):
        a = start_in_window + k * CHUNK
        if a >= 0 and a + CHUNK <= window.size:
            out[k] = window[a : a + CHUNK]
    return out


# -- candidate gates: (window, chunks, probs, mask, playback) -> refused -----


def gate_none(window, chunks, probs, mask, playback):
    return np.zeros_like(mask)


GATES = {"none": gate_none}


def replay(wav: str, out: str, gate_name: str = "none") -> None:
    gate = GATES[gate_name]
    rate, data = wavfile.read(wav)
    assert rate == SR, rate
    audio = data.astype(np.float32) / 32768.0
    start_s = _parse(START_WALL)
    embed = load_ecapa("speechbrain/spkrec-ecapa-voxceleb", 2)
    vad = VadGate(SR, WINDOW_S, THR_IDLE)
    vad.load()
    est, sm = HeadcountEstimator(rescue_enabled=False), BucketSmoother()
    floor = Ema(FLOOR_TAU_S)
    win, hop = int(WINDOW_S * SR), int(HOP_S * SR)
    fed, last_hc = 0, -1e9
    with open(out, "w", encoding="utf-8") as fh:
        for start in range(0, len(audio) - win + 1, hop):
            end = start + win
            vad.feed(audio[fed:end])
            fed = end
            now = end / SR
            wall = start_s + now
            music = _lookup(MUSIC, wall)
            pb = music[2]
            thr = THR_PLAYBACK if pb else THR_IDLE
            window = audio[start:end]
            probs = np.asarray(vad._probs)
            mask = vad.speech_mask(thr)
            refused = gate(window, chunk_audio(window, mask.size, end, fed), probs, mask, pb)
            certified = mask & ~refused
            ratio = float(certified.mean()) if certified.size else 0.0
            level = _dbfs(window)
            if ratio < 0.1:
                floor.update(level, now)
            row = dict(
                t=round(now, 1), wall=round(wall, 1), truth=_lookup(TRUTH, wall)[1],
                music=music[1], playback=pb, ratio=round(ratio, 3),
                ratio05=round(vad.speech_ratio(THR_IDLE), 3),
                mid_band=round(float(((probs >= 0.5) & (probs < 0.75)).mean()), 3),
                level=round(level, 1),
                floor=None if floor.value is None else round(floor.value, 1),
            )
            if gate_name != "none":
                row["refused"] = int((mask & refused).sum())
            if ratio >= MIN_SPEECH_RATIO and now - last_hc >= HC_INTERVAL_S:
                segs = speech_segments(window, certified, SR)
                row["n_segs"] = len(segs)
                if segs:
                    est.add(embed(segs), [s.size / SR for s in segs], now)
                e = est.estimate(ratio, level, pb, floor.value)
                last_hc = now
                if e is not None:
                    b = sm.update(e.log2_count, now)
                    row.update(
                        bucket=b.value, smoothed_log2=round(sm.smoothed_log2, 3),
                        log2=round(e.log2_count, 3), raw=e.raw_clusters,
                        conf=round(e.confidence, 3), crowd=round(e.crowd_weight, 3),
                        dispersion=round(e.dispersion, 3),
                        fragmentation=round(e.fragmentation, 3),
                        separation=None if e.separation is None else round(e.separation, 3),
                    )
            fh.write(json.dumps(row) + "\n")


def segment_table(path: str) -> list[tuple[str, dict]]:
    """Per music segment: bucket counts and mean certified ratio, the shape
    of the FIELD-NOTES 2026-09-30 (night) table."""
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    order: list[str] = []
    stats: dict[str, dict] = {}
    for r in rows:
        if r["music"] not in stats:
            order.append(r["music"])
            stats[r["music"]] = {"buckets": {}, "ratio": [], "frag": []}
        s = stats[r["music"]]
        s["ratio"].append(r["ratio"])
        if "bucket" in r:
            s["buckets"][r["bucket"]] = s["buckets"].get(r["bucket"], 0) + 1
            s["frag"].append(r["fragmentation"])
    out = []
    for m in order:
        s = stats[m]
        out.append((m, {
            "buckets": s["buckets"],
            "ratio": round(float(np.mean(s["ratio"])), 2),
            "frag": round(float(np.mean(s["frag"])), 2) if s["frag"] else None,
        }))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("wav")
    p.add_argument("out")
    p.add_argument("--gate", default="none", choices=sorted(GATES))
    p.add_argument("--table-only", action="store_true",
                   help="print the per-segment table of an existing OUT and exit")
    a = p.parse_args()
    if not a.table_only:
        replay(a.wav, a.out, a.gate)
    for music, s in segment_table(a.out):
        print(f"{music:42s} ratio {s['ratio']:.2f} frag {s['frag']} {s['buckets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
