# Machine doctrine revision — the Windows laptop becomes the reference machine

**Founder direction, 2026-09-06.** Supersedes the "two machines" rule in
`CLAUDE.md` and the Mac-anchored framing of README's performance budget.

## What changes

The Windows laptop (`JPad`, Lenovo) is now the **primary development and
gate machine**. Performance claims, benchmark regression rows, live
calibrations, and milestone gates count from it. The 2019 Intel MacBook
Pro becomes a **secondary compatibility target**.

Prior doctrine (`CLAUDE.md`): *"Windows dev box builds; the Intel Mac
validates and runs gates. Performance claims only count from the Mac."*

## Why

Two sessions on 2026-09-06 (see `docs/FIELD-NOTES.md`) established this
machine's baselines and found it is not merely adequate but a materially
better target:

- Emotion 0.34 s mean / 0.36 s p95, against the Mac's 0.66 / 0.66.
- Headcount `--fallback` 0.23 / 0.25 s, against the Mac's 1.00 / 1.02.
- It **passes `--concurrent`**, the strict every-hop gate, which the Mac
  has never passed. Headcount can run every hop here rather than every
  other hop.

The founder's development has moved to this machine; keeping gate
authority on a machine that is no longer used daily would mean every
milestone waits on hardware access, and the two machines have already
been shown to differ in ways that matter beyond speed (below).

## Consequences that are not just "faster"

1. **The budget arithmetic changes.** The headcount budget is the 2 s hop
   minus the emotion floor: Mac 2.0 − 0.63 = **1.37 s**; reference machine
   2.0 − 0.34 = **~1.66 s**. Every README gate row through M7 was judged
   against 1.37 s. Those rows are retained as historical record and
   explicitly marked not comparable to new rows. Do not mix them in a
   regression comparison.

2. **Calibration does not transfer between the machines, and this is now
   proven, not assumed.** The M6 dominance ramp (`RTR_MUSIC_DOMINANCE_LO/HI`
   = 0.05/0.30, measured on the Mac) leaves `m` pinned near zero on this
   machine's mic-and-speaker path, which silently disabled the M6 pull
   estimator *and* fed speech-over-music readings into the clean-speech
   baseline. Recalibrated knots (0.022/0.050) in this machine's `.env` fixed
   it — verified live, `pull_refs` 0 → 6, correction basis `pull`. The
   signature source stamp (schema v3, `f07a2df`) exists for exactly this
   reason and did its job on the first machine that needed it.

3. **Those knots are PROVISIONAL and must not be promoted yet.** They are
   fitted to a single track; three speech-only control takes disagreed at
   the upper tail (max 0.0198 / 0.0298 / 0.0346) with the chosen `LO` of
   0.022 sitting inside that disagreement; and a morning-vs-evening
   spectral discrepancy on the same track is unexplained. Promotion to
   `config.py` defaults requires the ladder re-run across ≥2 tracks and
   ≥3 speech-only controls. Until then they live in `.env` only and the
   Mac-measured defaults stay in `config.py`.

4. **A known bias now sits in the reference machine.** A solo speaker reads
   `pair` on this laptop's built-in mic — `raw_clusters` 2, `dispersion`
   0.588, `crowd_weight` 0.0, `rescued_clusters` 0, on the merged M7 branch.
   This is charter-compliant (M7 promises "never inflate into a crowd", and
   exact counting was deferred), and M7's crowd-path fixes work correctly.
   But as the gate machine it means **solo sessions feed `pair` into the
   rulebook**, and every solo corpus line captured here inherits it. This
   deserves a decision of its own before corpus accumulates; it is not a
   reason to reverse the machine change, but it is a standing caveat on
   any evidence gathered solo.

5. **The torch pin's rationale weakens but the pin stays.** `torch 2.2.2`
   is pinned because it is the last release with Intel-macOS wheels. With
   the Mac demoted to compatibility target that constraint is no longer
   load-bearing for development — but the pin is still doctrine
   (`CLAUDE.md`, `pyproject.toml`) and is not changed here. Revisit only as
   its own deliberate decision, with the Mac's continued role settled.

## Open

- Finish the dominance ladder (≥2 tracks, ≥3 controls), then promote knots.
- Decide whether the solo→`pair` reading on this mic gets its own
  investigation.
- M7 still needs only part (f) before merge; this revision does not change
  M7's gate criteria, which were set under the Mac budget and should be
  judged there.
- Existing test plans (`docs/M*-TEST-PLAN.md`) are written "for Claude Code
  on the Mac". They are evidentiary record and are **not** rewritten; new
  milestone test plans target the reference machine.
