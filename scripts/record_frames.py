"""Record the dashboard's state frames to disk, and check them for M8-03.

    python scripts/record_frames.py                      # record until Ctrl+C
    python scripts/record_frames.py --out data/sessions/m8-gate.frames.jsonl
    python scripts/record_frames.py --check data/sessions/m8-gate.frames.jsonl

Why this exists: the dashboard publishes frames only to the browser, and
the M8 gate's part (c) check 6 (ROADMAP M8-03) needs them on disk. Every
frame must show `emotion_correction.track_id` equal to the track that was
playing when that reading landed (docs/M8-TEST-PLAN.md).

Privacy (CLAUDE.md, REQUIRES-REVIEW; founder-approved 2026-10-09). This is
a capture of room-derived data, so it is **off unless a person starts it
by hand**. Claude never starts it during a live session. It is a read-only
websocket client: it never writes to the dashboard. It stores only the
`type: "state"` frames the page already receives: the same RoomState
fields the corpus records carry, plus the dashboard's playback and
observability extras. No audio. Output goes under `data/` (gitignored),
one JSON object per line, appended, flushed per frame.

`--check` is offline: it reads a recorded file and needs no dashboard.
Frames carry no reading id, so a reading's landing time is reconstructed
as `timestamp - emotion_staleness_s`. Both are rounded (0.1 s staleness),
so readings are told apart by a jump of more than `LANDING_JITTER_S`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Readings land at most one per emotion interval (2.0 s on JPad), and the
# reconstructed landing time is good to ~0.1 s (staleness rounding plus
# tick jitter). Half a second separates readings without merging them.
LANDING_JITTER_S = 0.5

DEFAULT_URL = "ws://127.0.0.1:8000/ws"


# -- recording ----------------------------------------------------------------


def record(url: str, out: Path) -> int:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    print(f"recording state frames from {url} -> {out} (Ctrl+C to stop)",
          file=sys.stderr)
    try:
        with connect(url) as ws, out.open("a", encoding="utf-8") as fh:
            while True:
                count += write_state_frame(ws.recv(), fh)
    except KeyboardInterrupt:
        pass
    except ConnectionClosed:
        print("dashboard closed the connection", file=sys.stderr)
    except OSError as exc:
        print(f"could not connect to {url}: {exc}", file=sys.stderr)
        return 1
    print(f"{count} state frames written to {out}", file=sys.stderr)
    return 0


def write_state_frame(message: str | bytes, fh) -> int:
    """Append one message if it is a state frame. Returns 1 if written."""
    try:
        frame = json.loads(message)
    except (TypeError, ValueError):
        return 0
    if not isinstance(frame, dict) or frame.get("type") != "state":
        return 0
    fh.write(json.dumps(frame, separators=(",", ":")) + "\n")
    fh.flush()
    return 1


# -- the M8-03 check ------------------------------------------------------------


@dataclass
class BindingReport:
    frames: int = 0
    readings: int = 0
    corrected_frames: int = 0
    # Corrected frames whose reading outlived its track: the playing track
    # differs from the reading's landing track (the M8-03 boundary case).
    across_boundary: int = 0
    # Frames still corrected after playback stopped, from a music reading.
    after_stop: int = 0
    violations: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations


def check_correction_binding(frames) -> BindingReport:
    """Every frame's `emotion_correction.track_id` must name the track that
    was playing when its reading landed. A reading that landed with
    playback off must carry no correction."""
    report = BindingReport()
    landed_at: float | None = None
    landing_track: str | None = None
    for frame in frames:
        if frame.get("type", "state") != "state":
            continue
        report.frames += 1
        staleness = frame.get("emotion_staleness_s")
        if staleness is None:
            continue  # no reading yet
        at = float(frame["timestamp"]) - float(staleness)
        if landed_at is None or abs(at - landed_at) > LANDING_JITTER_S:
            landed_at = at
            landing_track = (
                frame.get("playback_track_id") if frame.get("playback_active") else None
            )
            report.readings += 1
        correction = frame.get("emotion_correction")
        if correction is None:
            continue
        report.corrected_frames += 1
        current = frame.get("playback_track_id") if frame.get("playback_active") else None
        if current != landing_track:
            if current is None:
                report.after_stop += 1
            else:
                report.across_boundary += 1
        if correction.get("track_id") != landing_track:
            report.violations.append({
                "timestamp": frame["timestamp"],
                "correction_track": correction.get("track_id"),
                "landing_track": landing_track,
                "playing_now": current,
            })
    return report


def _read_frames(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="default: data/sessions/frames-YYYYMMDD-HHMMSS.jsonl",
    )
    parser.add_argument("--check", type=Path, metavar="FRAMES",
                        help="check a recorded file for M8-03 and exit")
    args = parser.parse_args(argv)

    if args.check is not None:
        r = check_correction_binding(_read_frames(args.check))
        print(f"frames {r.frames}, readings {r.readings}, "
              f"corrected frames {r.corrected_frames}")
        print(f"corrected across a track boundary: {r.across_boundary}; "
              f"after playback stopped: {r.after_stop}")
        if r.across_boundary == 0 and r.after_stop == 0:
            print("note: no boundary or stop was exercised; the check proves little")
        for v in r.violations[:20]:
            print("VIOLATION", v)
        print("M8-03 binding:", "PASS" if r.passed else f"FAIL ({len(r.violations)})")
        return 0 if r.passed else 1

    out = args.out or Path("data/sessions") / time.strftime(
        "frames-%Y%m%d-%H%M%S.jsonl"
    )
    return record(args.url, out)


if __name__ == "__main__":
    raise SystemExit(main())
