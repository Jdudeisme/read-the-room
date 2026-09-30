"""The rulebook: (headcount bucket, valence band, arousal band) -> genre pool.

Plain data, no conditionals. The rulebook is tuned constantly from dashboard
observation, so every placement must be a one-line edit:

  - move a whole bucket to a different seed grid: edit one line in
    _BUCKET_SEED;
  - retune a single cell: add one override line at the bottom
    (`RULEBOOK[("16", "high", "high")] = ["Dance"]`);
  - move a band cutoff: edit one default in `mapping.config` (or set the
    RTR_MAPPING_* env var).

SEED PROVENANCE: transcribed cell-for-cell from the 2020 thesis
ReadtheRoom.py GenrePicker function. The original used people-count cutoffs
<=3 / <=6 / >6; M3 keys the rulebook at full bucket granularity, so each
bucket starts from the nearest 2020 column (solo/pair/4 <- "<=3",
8 <- "<=6", 16..crowd <- ">6") and diverges from there through tuning. The
seed is a starting point, not a source of truth. M7's mid-rungs land
exactly on the 2020 cutoffs: "3" <- "<=3" (small), "6" <- "<=6" (medium) —
founder curation adjusts from there like any cell.
"""

from __future__ import annotations

# Band labels. Cutoffs live in mapping.config (seeded at +/-0.25 to match
# 2020); the rulebook only ever sees the labels.
VALENCE_BANDS = ("low", "mid", "high")
AROUSAL_BANDS = ("low", "mid", "high")

# All publishable buckets, in ladder order (mirrors sensing BUCKET_LADDER +
# CROWD, as strings — the rulebook is deliberately decoupled from the enum).
BUCKETS = (
    "solo", "pair", "3", "4", "6", "8", "16", "32", "64", "128", "256",
    "512", "1024", "crowd",
)

# --- 2020 GenrePicker seed grids: (valence_band, arousal_band) -> pool -----

_SMALL_2020 = {  # GenrePicker `people <= 3`
    ("high", "high"): ["Pop"],
    ("high", "mid"): ["Hip-Hop"],
    ("high", "low"): ["Lofi Beats"],
    ("mid", "high"): ["Hip-Hop"],
    ("mid", "mid"): ["Jazz"],
    ("mid", "low"): ["Classical"],
    ("low", "high"): ["Soft Rock"],
    ("low", "mid"): ["Classical"],
    ("low", "low"): ["Lofi Beats"],
}

_MEDIUM_2020 = {  # GenrePicker `people <= 6`
    ("high", "high"): ["Dance"],
    ("high", "mid"): ["Hip-Hop"],
    ("high", "low"): ["Soul"],
    ("mid", "high"): ["Hip-Hop"],
    ("mid", "mid"): ["Jazz"],
    ("mid", "low"): ["Classical"],
    ("low", "high"): ["Rock"],
    ("low", "mid"): ["Country"],
    ("low", "low"): ["Blues"],
}

_LARGE_2020 = {  # GenrePicker `people > 6`
    ("high", "high"): ["Electronic Dance Music"],
    ("high", "mid"): ["Dance"],
    ("high", "low"): ["Hip-Hop"],
    ("mid", "high"): ["Hip-Hop"],
    ("mid", "mid"): ["R&B"],
    ("mid", "low"): ["Soul"],
    ("low", "high"): ["Hard Rock"],
    ("low", "mid"): ["Country"],
    ("low", "low"): ["Jazz"],
}

# Which seed grid each bucket starts from (nearest bucket edges to the 2020
# 3/6-person cutoffs: <=4 small, <=8 medium, >8 large). One line per bucket.
_BUCKET_SEED = {
    "solo": _SMALL_2020,
    "pair": _SMALL_2020,
    "3": _SMALL_2020,  # M7 rung; 2020's "<=3" column verbatim
    "4": _SMALL_2020,
    "6": _MEDIUM_2020,  # M7 rung; 2020's "<=6" column verbatim
    "8": _MEDIUM_2020,
    "16": _LARGE_2020,
    "32": _LARGE_2020,
    "64": _LARGE_2020,
    "128": _LARGE_2020,
    "256": _LARGE_2020,
    "512": _LARGE_2020,
    "1024": _LARGE_2020,
    "crowd": _LARGE_2020,
}

# The flat rulebook: (bucket, valence_band, arousal_band) -> genre pool.
RULEBOOK: dict[tuple[str, str, str], list[str]] = {
    (bucket, v, a): list(pool)
    for bucket, grid in _BUCKET_SEED.items()
    for (v, a), pool in grid.items()
}

# --- Per-cell overrides (tuning lands here, one line each) -----------------
# Example: RULEBOOK[("16", "high", "high")] = ["Dance", "House"]

# Tense quadrant (low valence, high arousal): Rock appended as pool fallback —
# the seed genres (Soft Rock / Hard Rock) keep priority if curated later, but
# Rock is the only one with a mapped playlist today. ("8" already seeds Rock.)
RULEBOOK[("solo", "low", "high")] = ["Soft Rock", "Rock"]
RULEBOOK[("pair", "low", "high")] = ["Soft Rock", "Rock"]
RULEBOOK[("3", "low", "high")] = ["Soft Rock", "Rock"]  # M7: rides with its seed family
RULEBOOK[("4", "low", "high")] = ["Soft Rock", "Rock"]
RULEBOOK[("16", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("32", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("64", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("128", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("256", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("512", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("1024", "low", "high")] = ["Hard Rock", "Rock"]
RULEBOOK[("crowd", "low", "high")] = ["Hard Rock", "Rock"]
