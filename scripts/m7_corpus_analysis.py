"""M7 offline evidence: mine the corpus for the stable-middle failure signatures.

Reads the private-corpus annotation frames (sibling checkout of
read-the-room-data) and summarizes headcount diagnostics for:

- the 2026-07-10 trio evening segments (ground truth 3 talking people),
  split by verdict, with the deliberate Wrong-call undercount taps;
- the goodbye window (overlapping speech — the one time the bucket rose);
- M4 part (d) phase 2b (animated pair, no music — the bucket-8 overcount).

Segment boundaries come from the field-notes timeline (wall-clock local).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "read-the-room-data" / "annotations"

SEGMENTS = [
    # (name, date, start, end, true_talking_count)
    ("2b animated pair (overcount)", "2026-07-10", "19:46:51", "19:49:35", 2),
    ("indoor trio DJ", "2026-07-10", "20:16:49", "20:45:53", 3),
    ("porch 1 (trio)", "2026-07-10", "20:55:54", "21:17:16", 3),
    ("porch 2 (trio)", "2026-07-10", "21:34:47", "21:48:20", 3),
    ("full volume indoor (trio)", "2026-07-10", "21:48:20", "22:08:12", 3),
    ("full volume outdoor (trio)", "2026-07-10", "22:08:12", "22:27:23", 3),
    ("trio goodbye (overlap)", "2026-07-10", "23:02:07", "23:10:00", 3),
]


def local(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def to_epoch(date: str, hms: str) -> float:
    return datetime.strptime(f"{date} {hms}", "%Y-%m-%d %H:%M:%S").timestamp()


def main() -> None:
    frames = []
    for path in sorted(CORPUS.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                frames.append(json.loads(line))
    print(f"total corpus frames: {len(frames)}")

    for name, date, start, end, truth in SEGMENTS:
        lo, hi = to_epoch(date, start), to_epoch(date, end)
        seg = [f for f in frames if lo <= f["ts"] <= hi]
        if not seg:
            print(f"\n== {name}: NO FRAMES")
            continue
        print(f"\n== {name} (true talking N={truth}, {len(seg)} frames) ==")
        hdr = (
            "time     verd  bucket stale   raw  crowd  disp   frag   sr    "
            "dBFS   smoothed recent_raw_log2"
        )
        print(hdr)
        for f in seg:
            s = f["state"]
            recent = s.get("headcount_recent_raw_log2")
            recent_s = (
                "[" + " ".join(f"{v:.2f}" for v in recent) + "]" if recent else "-"
            )
            print(
                f"{local(f['ts'])} {f['verdict']:<5} "
                f"{str(s['headcount_bucket']):<6} "
                f"{s['headcount_staleness_s']:>5.0f}  "
                f"{s['headcount_raw_clusters']:>3}  "
                f"{s['headcount_crowd_weight']:>5.2f} "
                f"{s['headcount_dispersion']:>5.3f} "
                f"{s['headcount_fragmentation']:>5.3f} "
                f"{s['speech_ratio']:>5.2f} "
                f"{s['loudness_dbfs']:>6.1f} "
                f"{s['headcount_smoothed_log2']:>7.3f}  {recent_s}"
            )


if __name__ == "__main__":
    sys.exit(main())
