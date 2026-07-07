"""Tuning report: offline analysis of the annotation + override logs.

    python scripts/tuning_report.py                       # data/annotations/*.jsonl
    python scripts/tuning_report.py path/to/*.jsonl ...   # explicit files
    python scripts/tuning_report.py --overrides data/overrides/*.jsonl

The human-in-the-loop precursor to learned tuning. Reads annotation JSONL
records ({schema_version, ts, verdict, state, recommendation}) and reports:

  1. verdict counts per rulebook cell (matched_cell);
  2. wrong-call clustering: wrong calls whose valence/arousal sits within
     PROXIMITY_TOL of a band boundary — judged against each record's OWN
     boundaries_snapshot, since boundaries may have moved between sessions;
  3. per boundary, a suggested shift, with how many past verdicts it would
     have flipped into a different cell (wrong flipped = evidence for the
     move; good flipped = evidence against).

M4 adds the override log ({..., action, now_playing, chosen?}) — the strong
labels — and three more sections:

  4. overrides per rulebook cell, with the override rate
     vetoes / (vetoes + played_through);
  5. veto clustering near band boundaries (skip / wrong_vibe, same proximity
     logic as section 2);
  6. tier disagreement: manual picks whose chosen tier sits above/below the
     tier the playback layer derived from the recommendation.

OUTPUT ONLY: this script proposes; it never modifies the rulebook, .env, or
any file. Guard records (matched_cell[0] == "guard") appear in the cell
table but are excluded from boundary analysis — no rulebook cell fired.

Annotations against never-played shadow recommendations are weak labels;
overrides are strong ones but still human-scale corpora. Apply suggestions
by hand, if at all, and re-observe.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
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


# -- overrides (M4): the strong labels ---------------------------------------

# Actions that veto the system's choice; played_through is the implicit
# weak positive the rate is measured against.
VETO_ACTIONS = ("skip", "wrong_vibe", "manual")

TIERS = ("low", "mid", "high")


def override_cell_counts(records: list[dict]) -> dict[tuple, Counter]:
    counts: dict[tuple, Counter] = {}
    for r in records:
        rec = r.get("recommendation", {})
        cell = rec.get("matched_cell")
        if not cell:
            # played_through of a manual pick attributes to the human choice,
            # not to any rulebook cell.
            cell = ["manual"] if rec.get("source") == "manual" else ["?"]
        counts.setdefault(tuple(cell), Counter())[r.get("action", "?")] += 1
    return counts


def override_rate(counts: Counter) -> float | None:
    """vetoes / (vetoes + played_through); None when nothing completed or
    was vetoed (rate undefined)."""
    vetoes = sum(counts.get(a, 0) for a in VETO_ACTIONS)
    total = vetoes + counts.get("played_through", 0)
    return vetoes / total if total else None


def veto_boundary_proximity(records: list[dict], tol: float = PROXIMITY_TOL) -> Counter:
    """Per boundary: vetoes (skip/wrong_vibe) whose signal sits within tol.
    Manual picks are excluded — they judge the replacement, not the cut."""
    near = Counter()
    for r in _signal_records(records):
        if r.get("action") not in ("skip", "wrong_vibe"):
            continue
        snap = r["recommendation"]["boundaries_snapshot"]
        for name, signal in BOUNDARIES:
            if abs(r["state"][signal] - snap[name]) <= tol:
                near[name] += 1
    return near


def _derived_tier(target_arousal: float, energy_action: str,
                  low_max: float, high_min: float) -> str:
    # Must mirror playback.selector.derive_tier (band + one-step shift).
    if target_arousal > high_min:
        base = "high"
    elif target_arousal > low_max:
        base = "mid"
    else:
        base = "low"
    shift = {"raise": 1, "lower": -1}.get(energy_action, 0)
    idx = max(0, min(len(TIERS) - 1, TIERS.index(base) + shift))
    return TIERS[idx]


def tier_disagreement(records: list[dict]) -> Counter:
    """Manual picks: chosen tier vs the tier the playback layer derived.
    Consistent 'higher'/'lower' means the RTR_PLAYBACK_TIER_* cutoffs (or
    the arousal targets feeding them) sit in the wrong place.

    Tier cutoffs are read from the environment (they are not in
    boundaries_snapshot); if they moved between sessions the comparison is
    approximate — noted in the output.
    """
    low_max = float(os.environ.get("RTR_PLAYBACK_TIER_LOW_MAX", -0.25))
    high_min = float(os.environ.get("RTR_PLAYBACK_TIER_HIGH_MIN", 0.25))
    out = Counter()
    for r in records:
        if r.get("action") != "manual":
            continue
        chosen_tier = (r.get("chosen") or {}).get("tier")
        rec = r.get("recommendation", {})
        target_arousal = rec.get("target_arousal")
        if chosen_tier not in TIERS or target_arousal is None:
            continue
        derived = _derived_tier(
            target_arousal, rec.get("energy_action", "hold"), low_max, high_min
        )
        diff = TIERS.index(chosen_tier) - TIERS.index(derived)
        out["higher" if diff > 0 else "lower" if diff < 0 else "same"] += 1
    return out


def print_overrides_report(records: list[dict]) -> None:
    total = Counter(r.get("action", "?") for r in records)
    print("=" * 64)
    print("OVERRIDES - playback strong labels")
    print("=" * 64)
    print(
        f"records: {len(records)}  ("
        f"skip {total.get('skip', 0)}, wrong_vibe {total.get('wrong_vibe', 0)}, "
        f"manual {total.get('manual', 0)}, "
        f"played_through {total.get('played_through', 0)})"
    )

    print("\n-- 4. overrides per rulebook cell ------------------------------")
    print(f"{'cell':<30} {'skip':>5} {'vibe':>5} {'man':>5} {'thru':>5} {'rate':>6}")
    for cell, counts in sorted(override_cell_counts(records).items()):
        label = " / ".join(str(c) for c in cell)
        rate = override_rate(counts)
        print(
            f"{label:<30} {counts.get('skip', 0):>5} "
            f"{counts.get('wrong_vibe', 0):>5} {counts.get('manual', 0):>5} "
            f"{counts.get('played_through', 0):>5} "
            f"{'—' if rate is None else f'{rate:.0%}':>6}"
        )

    print("\n-- 5. vetoes near a band boundary (within "
          f"{PROXIMITY_TOL:.2f}) ------------")
    near = veto_boundary_proximity(records)
    if not near:
        print("none - vetoes (if any) are not boundary-clustered")
    for name, _ in BOUNDARIES:
        if near.get(name):
            print(f"{name:<14} {near[name]} veto(es) within {PROXIMITY_TOL:.2f}")

    print("\n-- 6. tier disagreement (manual picks vs derived tier) ---------")
    tiers = tier_disagreement(records)
    if not tiers:
        print("no manual picks with a chosen tier")
    else:
        print(
            f"higher {tiers.get('higher', 0)} / "
            f"lower {tiers.get('lower', 0)} / same {tiers.get('same', 0)}"
        )
        print("(derived with the CURRENT RTR_PLAYBACK_TIER_* cutoffs; if the")
        print(" cutoffs moved between sessions this comparison is approximate)")
    print("=" * 64)


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
        description="Offline tuning report over annotation + override logs "
        "(stdout only)."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["data/annotations/*.jsonl"],
        help="annotation JSONL files or globs (default: data/annotations/*.jsonl)",
    )
    parser.add_argument(
        "--overrides",
        nargs="*",
        default=["data/overrides/*.jsonl"],
        help="override JSONL files or globs (default: data/overrides/*.jsonl)",
    )
    args = parser.parse_args(argv)

    files = sorted({Path(p) for pattern in args.paths for p in glob.glob(str(pattern))})
    override_files = sorted(
        {Path(p) for pattern in args.overrides for p in glob.glob(str(pattern))}
    )
    records = load_annotations(files) if files else []
    override_records = load_annotations(override_files) if override_files else []

    if not records and not override_records:
        print(
            f"no annotation files match {args.paths} and no override files "
            f"match {args.overrides} - nothing to report"
        )
        return 1

    if records:
        print(f"reading {len(files)} file(s): {', '.join(str(f) for f in files)}")
        print_report(records)
    else:
        print("no annotation records - skipping sections 1-3")
    if override_records:
        print(
            f"reading {len(override_files)} override file(s): "
            f"{', '.join(str(f) for f in override_files)}"
        )
        print_overrides_report(override_records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
