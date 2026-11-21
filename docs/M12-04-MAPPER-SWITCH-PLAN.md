# M12-04 mapper switch — plan (REQUIRES-REVIEW)

**Status:** plan and D1–D3 approved by the founder, 2026-11-20. The diff
was approved by the founder the same day. 429 tests pass; the two new
switch tests fail against the previous mapper.

**What this is.** The mapper-only diff that M12-05's Rule 2 calls for. The
outcome was **SWITCH** (FIELD-NOTES 2026-11-20; README, M12 gate part (b)).
The run sheet fixes the change in one sentence: "The mapper reads
`clean_trend` when it is present and `trend` otherwise. That is a
mapper-only, REQUIRES-REVIEW diff with this entry as its evidence." This
plan adds nothing to that sentence. It names the line, the tests, and what
stays out. Per the test plan, it lands **before** M12 merges.

**Evidence, including its weak point.**
- 2b held on all 12 66/76 % mix takes in both blocks: clean energy sat
  0.01–0.06 from the no-music controls, against 0.03–0.16 for published.
- 2a held on S-VS-Ib (`trend` rising 0.233 after the step, `clean_trend`
  0.000; threshold 0.20). It did not hold on G-VS-I (0.767 vs 0.700).
- **The margin is thin.** On 2026-10-26 the same Block S test read 0.167
  (HOLD), so the outcome sits within day-to-day variation. The rule was
  pre-registered and signed, and it says SWITCH. This plan follows it, and
  says so here so the review sees it.

## The change

1. **`src/mapping/mapper.py`, `_rulebook_recommendation`.** The fallback
   when the arousal trend is flat becomes the clean trend when present:

   ```python
   # Match the room's direction. Arousal is the energy axis; when it is
   # flat, defer to the engine's composite energy trend, measured on the
   # playback-clean stream while cancellation runs (M12-05 Rule 2: SWITCH,
   # FIELD-NOTES 2026-11-20). clean_trend is None whenever nothing was
   # cleaned (M12-04 D1), which leaves today's raw path untouched.
   energy_trend = state.clean_trend if state.clean_trend is not None else state.trend
   direction = arousal_trend if arousal_trend != "stable" else energy_trend
   ```

   Nothing else in the mapper changes: the arousal-first precedence, the
   dwell and hysteresis, the targets, the confidence and the summary text.
   The time arithmetic stays on `state.timestamp`, so replays are
   unaffected.
2. **`src/sensing/state.py`, comment only.** The `clean_*` block says "The
   mapper reads `trend`, not `clean_trend`, until M12-05's pre-registered
   rule says otherwise." It becomes "The mapper's energy fallback reads
   `clean_trend` when present (M12-05 Rule 2: SWITCH, 2026-11-20)." No field
   changes and no schema change.
3. **Tests, `tests/test_mapper.py`** (offline, no models):
   - **Unchanged and still green:** `test_flat_arousal_defers_to_engine_energy_trend`
     (no `clean_trend`, so it uses `trend`). It now also pins the
     `None` → raw fallback.
   - **New:** with flat arousal, `trend="stable"` and `clean_trend="rising"`
     → `raise`.
   - **New:** with flat arousal, `trend="rising"` and `clean_trend="stable"`
     → `hold`. This is the feedback-loop case Rule 2a measured: the raw
     trend chases the music, and the clean trend doesn't.
   - **New:** a rising arousal trend still wins over a `"falling"`
     `clean_trend` → `raise`. Precedence is unchanged.

## What this does NOT change

- **Default behavior.** Cancellation is off by default
  (`RTR_PLAYBACK_CANCEL_ENABLED=0`, and JPad's `.env` leaves it at the
  default). With it off, `clean_trend` is always `None`, so **the DJ
  behaves exactly as today** until cancellation is turned on. Enabling
  cancellation by default is a separate decision (D2).
- **Published sensor values.** `trend`, `energy` and every `clean_*` field
  are computed as before. Only the mapper's choice of input changes.
- **Corpus schemas.** No new fields. Which trend the mapper used can be
  derived from the stamped state: `clean_trend` present (with
  `analysis_stream == "clean"`) means the clean trend (D1).
- **Display.** The console consumer and the dashboard keep showing raw
  `trend` (D3).
- **Fallback mid-session.** If the clean source fails (e.g. M12-08), the
  engine falls back to raw, `clean_trend` becomes `None`, and the mapper
  returns to `trend` on the next frame with no code path of its own. When
  cancellation starts, after the canceller aligns, `clean_trend` begins
  with a fresh tracker and reads `stable` for its first ~15 s (the
  `TrendTracker` minimum: 5 points spanning 15 s).
  The raw path behaves the same at startup.
- **Engine, benchmarks, headcount replay.** Not touched. No benchmark row
  is needed (the change isn't on the engine path), and the 2026-07-15
  histogram isn't affected (headcount is untouched).

## Decisions for the founder

- **D1. Record which trend the mapper used?** **Recommended: no new field.**
  It can be derived from the stamped state (above). Adding it to
  `Recommendation` would put it in override records, which needs an
  overrides schema bump (invariant 9) for a value that can already be
  recovered.
- **D2. Cancellation default.** **Recommended: out of scope.** Leave
  `RTR_PLAYBACK_CANCEL_ENABLED=0` as the default. This plan makes the
  switch correct whenever cancellation runs. Whether RTR runs with
  cancellation on by default, or on JPad's `.env`, is its own decision. It
  touches the published sensor stream (certification, emotion and
  headcount read clean), and M12-08 should probably land first.
- **D3. Show the clean trend in the UI?** **Recommended: out of scope.**
  It's a display change with no evidence attached. It can follow if the
  founder wants the dashboard to show what the DJ is actually following.

## Verification

- `python -m pytest` green (426 + 3 new). The suite stays offline and fast.
- `git diff` touches only `src/mapping/mapper.py`, `src/sensing/state.py`
  (comment), `tests/test_mapper.py`, and this plan's status line.
- Commit: `M12-04 mapper: energy fallback reads clean_trend when present (M12-05 Rule 2 SWITCH)`.
