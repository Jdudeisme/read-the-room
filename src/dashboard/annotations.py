"""Annotation log: one JSON line per Good/Wrong verdict.

The record stores the state and recommendation the page was DISPLAYING at
tap time (sent by the client), not whatever is latest server-side — the
annotation labels what the human saw. `recommendation.matched_cell` and
`recommendation.boundaries_snapshot` make every verdict attributable to a
specific rulebook cell under specific boundary values.

Schema v2 (M12-04, founder D2 2026-10-11): the stamped state can carry the
playback-clean heartbeat (`clean_loudness_dbfs`, `clean_activity_density`,
`clean_spectral_balance`, `clean_energy`, `clean_trend`). They are None when
cancellation was off. Additive: a v1 record is a v2 record without them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SCHEMA_VERSION = 2

VERDICTS = ("good", "wrong")


def build_record(
    verdict: str, state: dict, recommendation: dict, ts: float | None = None
) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if not recommendation:
        raise ValueError("annotation requires the displayed recommendation")
    if not state:
        raise ValueError("annotation requires the displayed state")
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": time.time() if ts is None else ts,
        "verdict": verdict,
        "state": state,
        "recommendation": recommendation,
    }


def append_annotation(annotations_dir: Path, record: dict) -> Path:
    """Append one record to the day file (YYYY-MM-DD.jsonl, local date)."""
    annotations_dir.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.localtime(record["ts"]))
    path = annotations_dir / f"{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return path
