"""Override log: one JSON line per human playback override (M4 deliverable 2).

These are the strong labels the M3 tuning loop deferred learned tuning for:
every record carries the room state and recommendation *as displayed at tap
time* (the annotation convention), the NowPlaying track under judgment, and
— for manual picks — what the human chose instead. A track that plays to
completion with no override is logged once as an implicit weak positive
(`played_through`, emitted by the playback controller, stamped with the
latest frame rather than a tap-time snapshot).

Capture ordering matters: the record is appended BEFORE the playback action
is attempted, so a provider failure can degrade the music but never lose
the label.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SCHEMA_VERSION = 1

# skip: veto the playing track; wrong_vibe: veto the SELECTION (resample a
# cell-adjacent pool); manual: human picked a mapped (genre, tier) instead;
# played_through: implicit weak positive, no human action.
ACTIONS = ("skip", "wrong_vibe", "manual", "played_through")


def build_override_record(
    action: str,
    state: dict,
    recommendation: dict,
    now_playing: dict,
    chosen: dict | None = None,
    ts: float | None = None,
) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
    if not state:
        raise ValueError("override requires the displayed state")
    if not recommendation:
        raise ValueError("override requires the recommendation that chose the track")
    if not now_playing:
        raise ValueError("override requires the now-playing snapshot")
    if action == "manual" and not chosen:
        raise ValueError("manual override requires what the human chose instead")
    record = {
        "schema_version": SCHEMA_VERSION,
        "ts": time.time() if ts is None else ts,
        "action": action,
        "state": state,
        "recommendation": recommendation,
        "now_playing": now_playing,
    }
    if chosen is not None:
        record["chosen"] = chosen
    return record


def append_override(overrides_dir: Path, record: dict) -> Path:
    """Append one record to the day file (YYYY-MM-DD.jsonl, local date)."""
    overrides_dir.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.localtime(record["ts"]))
    path = overrides_dir / f"{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return path
