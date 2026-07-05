"""Tuning report: offline analysis of the M3 annotation log.

    python scripts/tuning_report.py                       # data/annotations/*.jsonl
    python scripts/tuning_report.py path/to/*.jsonl ...   # explicit files

The human-in-the-loop precursor to learned tuning. Reads annotation JSONL
records ({schema_version, ts, verdict, state, recommendation}) and reports:

  1. verdict counts per rulebook cell (matched_cell);
  2. wrong-call clustering: wrong calls whose valence/arousal sits within
     PROXIMITY_TOL of a band boundary — judged against each record's OWN
     boundaries_snapshot, since boundaries may have moved between sessions;
  3. per boundary, a suggested shift, with how many past verdicts it would
     have flipped into a different cell (wrong flipped = evidence for the
     move; good flipped = evidence against).

OUTPUT ONLY: this script proposes; it never modifies the rulebook, .env, or
any file. Guard records (matched_cell[0] == "guard") appear in the cell
table but are excluded from boundary analysis — no rulebook cell fired.

Annotations against never-played shadow recommendations are weak labels;
apply suggestions by hand, if at all, and re-observe.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

# A wrong call this close to a boundary (valence/arousal units) counts as
# "clustered at the boundary" — the classic symptom of a misplaced cutoff.
PROXIMITY_TOL = 0.1

# Candidate boundary shifts evaluated for section 3, smallest first so ties
# prefer the gentler move.
CANDIDATE_SHIFTS = (-0.05, 0.05, -0.10, 0.10)

# (boundary name in boundaries_snapshot, state signal it cuts)
BOUNDARIES = (
    ("valence_low", "valence"),
    ("valence_high", "valence"),
    ("arousal_low", "arousal"),
    ("arousal_high", "arousal"),
)


def load_annotations(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"  ! skipping bad line {path}:{line_no}", file=sys.stderr)
    return records


def is_guard(record: dict) -> bool:
    cell = record["recommendation"].get("matched_cell", [])
    return bool(cell) and cell[0] == "guard"


def cell_counts(records: list[dict]) -> dict[tuple, Counter]:
    counts: dict[tuple, Counter] = {}
    for r in records:
        cell = tuple(r["recommendation"].get("matched_cell", ["?"]))
        counts.setdefault(cell, Counter())[r["verdict"]] += 1
    return counts


def _band(value: float, low: float, high: float) -> str:
    # Must mirror mapping.mapper.band (strict > comparisons, 2020 semantics).
    if value > high:
        return "high"
    if value > low:
        return "mid"
    return "low"


def _signal_records(records: list[dict]) -> list[dict]:
    """Records usable for boundary analysis: a rulebook cell fired and the
    state carries the signal values plus a boundaries snapshot."""
    usable = []
    for r in records:
        if is_guard(r):
            continue
        state = r.get("state", {})
        snap = r["recommendation"].get("boundaries_snapshot", {})
        if state.get("valence") is None or state.get("arousal") is None:
            continue
        if not all(name in snap for name, _ in BOUNDARIES):
            continue
        usable.append(r)
    return usable


def boundary_proximity(records: list[dict], tol: float = PROXIMITY_TOL) -> Counter:
    """Per boundary: wrong calls whose signal sits within tol of it."""
    near = Counter()
    for r in _signal_records(records):
        if r["verdict"] != "wrong":
            continue
        snap = r["recommendation"]["boundaries_snapshot"]
        for name, signal in BOUNDARIES:
            if abs(r["state"][signal] - snap[name]) <= tol:
                near[name] += 1
    return near


def suggest_adjustments(records: list[dict]) -> dict[str, dict]:
    """For each boundary, the candidate shift that flips the most wrong
    calls into a different cell while flipping the fewest good calls.

    A record "flips" if its (valence, arousal) band assignment changes when
    the one boundary moves by the shift — evaluated against the record's own
    snapshot. Returns {} entries with shift None when no candidate helps.
    """
    usable = _signal_records(records)
    suggestions: dict[str, dict] = {}
    for name, signal in BOUNDARIES:
        best = {"shift": None, "wrong_flipped": 0, "good_flipped": 0}
        for shift in CANDIDATE_SHIFTS:
            wrong_flipped = good_flipped = 0
            for r in usable:
                snap = r["recommendation"]["boundaries_snapshot"]
                low, high = snap[f"{signal}_low"], snap[f"{signal}_high"]
                moved_low = low + (shift if name.endswith("_low") else 0.0)
                moved_high = high + (shift if name.endswith("_high") else 0.0)
                if moved_low >= moved_high:
                    continue  # degenerate band; skip this record
                value = r["state"][signal]
                if _band(value, low, high) != _band(value, moved_low, moved_high):
                    if r["verdict"] == "wrong":
                        wrong_flipped += 1
                    else:
                        good_flipped += 1
            score = wrong_flipped - good_flipped
            best_score = best["wrong_flipped"] - best["good_flipped"]
            if score > best_score and score > 0:
                best = {
                    "shift": shift,
                    "wrong_flipped": wrong_flipped,
                    "good_flipped": good_flipped,
                }
        suggestions[name] = best
    return suggestions


def print_report(records: list[dict]) -> None:
    total = Counter(r["verdict"] for r in records)
    print("=" * 64)
    print("TUNING REPORT - shadow-mode annotations")
    print("=" * 64)
    print(
        f"records: {len(records)}  "
        f"(good {total.get('good', 0)}, wrong {total.get('wrong', 0)})"
    )

    print("\n-- 1. verdicts per rulebook cell -------------------------------")
    print(f"{'cell':<38} {'good':>5} {'wrong':>6}")
    for cell, counts in sorted(cell_counts(records).items()):
        label = " / ".join(str(c) for c in cell)
        print(f"{label:<38} {counts.get('good', 0):>5} {counts.get('wrong', 0):>6}")

    print("\n-- 2. wrong calls near a boundary (within "
          f"{PROXIMITY_TOL:.2f}) ------------")
    near = boundary_proximity(records)
    if not near:
        print("none - wrong calls (if any) are not boundary-clustered")
    for name, _ in BOUNDARIES:
        if near.get(name):
            print(f"{name:<14} {near[name]} wrong call(s) within {PROXIMITY_TOL:.2f}")

    print("\n-- 3. suggested boundary adjustments ---------------------------")
    print("(proposals only - nothing is modified; apply by editing")
    print(" RTR_MAPPING_* in .env and re-observing)")
    any_suggestion = False
    for name, s in suggest_adjustments(records).items():
        if s["shift"] is None:
            continue
        any_suggestion = True
        print(
            f"{name:<14} shift {s['shift']:+.2f}  ->  would flip "
            f"{s['wrong_flipped']} wrong / {s['good_flipped']} good "
            f"call(s) to a different cell"
        )
    if not any_suggestion:
        print("no adjustment suggested by the current annotations")
    print("=" * 64)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline tuning report over M3 annotation logs (stdout only)."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["data/annotations/*.jsonl"],
        help="annotation JSONL files or globs (default: data/annotations/*.jsonl)",
    )
    args = parser.parse_args(argv)

    files = sorted({Path(p) for pattern in args.paths for p in glob.glob(str(pattern))})
    if not files:
        print(f"no annotation files match {args.paths} - nothing to report")
        return 1
    records = load_annotations(files)
    if not records:
        print("annotation files are empty - nothing to report")
        return 1
    print(f"reading {len(files)} file(s): {', '.join(str(f) for f in files)}")
    print_report(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
