# Playback cancellation on by default — plan (REQUIRES-REVIEW)

**Status:** plan and D1–D3 approved by the founder, 2026-11-21. The diff
awaits the founder's review, then the live check (D3) before the merge.

**What this is.** The founder's direction (2026-11-20): RTR should run with
its own playback cancelled by default. This was decision D2 of
`docs/M12-04-MAPPER-SWITCH-PLAN.md`, deferred until M12-08 merged; it
merged as `af0386a`. Changing a default is a calibration event (CLAUDE.md),
so this plan covers four things: the change, its evidence, what it
alters, and the live check before it merges.

## The change

Cancellation needs the playback reference, so **two defaults flip, not
one**:

1. **`src/sensing/config.py`:**
   - `playback_reference_enabled`: `False` → `True`;
   - `playback_cancel_enabled`: `False` → `True`.

   The provenance comments are rewritten. The reference was off "for
   privacy, not from a measurement" (D1 below). Cancellation was off
   because "it changes published values"; the comment now cites the
   evidence below.
2. **`.env.example`:** both lines go to `=1`, with comments matching
   `from_env` (the M10-02 rule: kept in exact agreement). The reference
   comment's "Nothing consumes it yet" is out of date since M12-02 and is
   corrected.
3. **`tests/test_engine.py`:** the characterization test pinning
   `Config().playback_reference_enabled is False` is updated deliberately
   and visibly to `True`, with a matching pin for `playback_cancel_enabled`.
   Both cite this plan.
4. **If D2 is approved:** a mid-session reference failure falls back to raw
   (below), with a test.

`RTR_PLAYBACK_REFERENCE_ENABLED=0` or `RTR_PLAYBACK_CANCEL_ENABLED=0` still
turns either off, through `.env` or the environment. The synthetic source
never gets a reference (unchanged), and a reference that can't open at
start-up falls back to raw (unchanged, `Engine.run`).

## Evidence

- **Certification:** with cancellation, music-only certification is 0.000
  on all 9 music-only takes at 32/66/76 % on 2026-11-20, and ≤ 0.011 on
  2026-10-26. Without it, vocal hip-hop certified 0.84–0.96 (FIELD-NOTES
  2026-11-20, 2026-10-26).
- **Headcount, one person reading over music (2026-11-20 Block S):** the
  `solo` share is higher with cancellation on than off on **all 9** mix
  takes, e.g. H66 0.09 → 0.28 and J76 0.20 → 0.85. M12-07's phantom count
  is real, but it's worse with cancellation *off*, not better.
- **Energy:** clean energy sits closer to the no-music controls on all 12
  66/76 % takes (Rule 2b). The mapper already reads `clean_trend` when it's
  present (`f5fd2ae`).
- **Budget:** gate (c) passed with the canceller running (headcount p95
  ≤ 0.35 s against 1.66 s; heartbeat p95 2.016 s, never blocking).
- **Robustness:** M12-08 is fixed. A big reference dropout now costs a
  re-lock, not the session.
- **Silence:** with nothing playing, the reference is exact zeros, and the
  canceller passes the mic through bit for bit (`tests/test_aec.py`). So
  with playback idle, the published values are unchanged apart from the
  labels (`analysis_stream`, `clean_*`).

## What turning it on changes (so the review sees all of it)

- **Published frames, while cancellation runs:**
  - certification, emotion and headcount read the clean stream;
  - `analysis_stream` reads `"clean"`;
  - the `clean_*` fields are populated;
  - `emotion_dominance_source` becomes `"reference"` (M12-06: the
    correction engages from 66 % up, and faint 32 % music goes uncorrected);
  - the mapper's energy fallback follows `clean_trend`.
- **Music-aware signatures:** the clean path learns its own file,
  `<signatures>.clean.json`. **None exists on JPad yet**
  (`data/track_signatures-lenovo.clean.json`), so the M6 correction on the
  clean path starts with no banked tracks and builds them up as tracks are
  tapped. The raw file is untouched and is still used whenever the engine
  falls back to raw.
- **Unchanged:** loudness, activity, spectral balance and the noise floor
  stay raw. No schema changes. Scripts that replay WAVs have no reference
  and are unaffected (verified below).

## Decisions for the founder

- **D1. The reference on by default (privacy).** The reference is a WASAPI
  loopback of **everything the laptop plays**: RTR's music, but also calls,
  videos and notifications. It's held in memory only and never written to
  disk by the engine or dashboard. Only `capture_room_wav.py --reference`
  writes it, and that stays opt-in per take. It isn't room audio, so CLAUDE.md's
  "default new captures of room-derived data to off" doesn't strictly
  apply, but this is the reason it was off. **Recommended: on**, as
  directed, with the config and `.env.example` comments saying plainly what
  it holds and that it never leaves memory.
- **D2. A mid-session reference failure.** Today, if the loopback dies
  mid-session (`ReferenceSource.status` becomes `"failed"`), its ring stops,
  the canceller reads zeros, and the mic passes through. **But the frames
  keep saying `analysis_stream: "clean"`, and the `clean_*` fields carry
  raw-equivalent values.** That's a mislabel, against invariant 5 (honest
  uncertainty). With the default off, this was rare. With it on, it isn't.
  **Recommended: fix it here.** `Engine._analysis_source` also falls back
  to raw when `self.reference.status == "failed"`, through the existing
  `_fall_back_to_raw` (the same path as a clean-source failure). It's one
  condition plus an offline test with a fake reference. It is an engine
  change, so it gets a benchmark regression row (below).
- **D3. The live check before the merge.** **Recommended: yes**, per the
  calibration-event rule. A founder-run smoke session on JPad (protocol
  below, about 30 minutes, founder alone). Claude never starts it.

## Live check protocol (founder-run, if D3 is approved)

Run the dashboard in its own PowerShell window (not `!`, which stops it at
30 min):
`cd C:\dev\read-the-room; .venv\Scripts\read-the-room-dashboard.exe`. Use
the branch, with `.env` unchanged, so the new defaults apply. Keep Spotify
playing through RTR at 32 % for ~25 min, with talking in some stretches.
Then:
1. **Start-up log:** "playback reference from …" and "analysing … (playback
   cancelled)", and **no** "playback cancellation off" line for the whole
   session.
2. **Dashboard / annotations:** frames show `analysis_stream` `clean`, and
   the `clean_*` fields are non-null while music plays.
3. **`data/track_signatures-lenovo.clean.json` exists** after a few track
   taps.
4. **No exceptions** in the log. The dashboard stays live (frames keep
   updating every hop), and the music never drops out.
5. **Optional, for D2:** disconnect or switch the default output device
   mid-session, and confirm one "playback cancellation off (… reference …)"
   line and frames returning to `raw`.

Claude then records the outcome in FIELD-NOTES. Acceptance is the protocol
run and recorded, with no target numbers.

## Verification (Claude)

- `python -m pytest` green. The suite stays offline and fast.
- **Headcount replay evidence:** `scripts/m7_replay_session.py` on the
  2026-07-15 gate WAV must still give solo 126 / pair 110 / bucket-3 45. It
  states its own config ("playback off"), and this confirms the new
  defaults don't leak into it.
- **Gate (a) harness** (`m12_partf_replay.py --gate none`): 0 differing
  fields.
- **If D2:** a `bench_headcount.py --fallback --cancel` row, within
  run-to-run variance of the gate (c) rows (README, M12 gate).
- **Commits:**
  - `M12 config: playback reference and cancellation on by default (founder direction 2026-11-20)`;
  - if D2: `M12 engine: fall back to raw when the playback reference fails mid-session`.
