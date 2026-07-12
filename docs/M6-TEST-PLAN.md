# Milestone 6 Test Plan — Hear the room, not the record

Logic-level tests live in `tests/test_music.py` plus the M6 additions to
`tests/test_dashboard.py` and `tests/test_state.py` (no mic, no models,
no network; the emotion worker is exercised at the job-slot level). This
plan covers the milestone gate, which runs on the 2019 Intel MacBook Pro
with live playback. The number to beat is the 2026-07-11 baseline:
**ΔV +0.26 / ΔA +0.39** — post-fix, the same protocol must land
**under 0.2 on both axes** while emotion still tracks a genuine mood
change under music.

> **For Claude Code on the Mac:** this document is your session script.
> Walk the founder through it **one part at a time, in order**; steps
> only the human can perform are marked **[HUMAN]**. Bench numbers go in
> the README results table; parts (c), (d), and the β/m calibration go
> in a dated `docs/FIELD-NOTES.md` entry. If a checkpoint fails, stop
> and diagnose. **No in-session tuning:** knobs move after the gate, by
> the gate's numbers, not during it.
>
> **The DJ-bootstrap gotcha (it has bitten twice):** every fixed-music
> or no-music phase runs with the inert playlist mapping —
> `RTR_PLAYBACK_PLAYLISTS_PATH` pointed at an empty mapping file — so
> the controller polls honestly (playback tagging works) but can never
> start or swap a track. Phase music is started by hand in Spotify.

## Part 0 — setup

```bash
git pull && git checkout milestone-6-music-aware
source .venv/bin/activate
python -m pytest -q              # expect: 270+ passed
```

Sync the corpus (`read-the-room-data`) both ways per its README. Confirm
`.env` carries no leftover `RTR_MUSIC_*` overrides — the gate measures
the defaults.

Checkpoint: pytest green; `python scripts/tuning_report.py` reads the
full corpus (113+/60+ through 07-11).

## Part (a) — bench regression

Reference taps reuse the existing worker at the existing rate limit and
fire only in windows where speech (and therefore headcount) is idle, so
the contended-hop profile must be unchanged.

```bash
RTR_TORCH_THREADS=2 python scripts/bench_headcount.py --fallback
```

Checkpoint: headcount contended p95 < 1.37 s, emotion overall p95
< 1.2 s. Record the row in the README.

## Part (b) — test suite on the Mac

```bash
python -m pytest -q
```

Checkpoint: green.

## Part (c) — part (f) re-run: the pull, post-fix

Same protocol as 2026-07-11, plus warm-ups for BOTH signatures (the
2026-07-11 iteration: the pull signature is the primary estimator; it
builds from speech-over-music readings against a fresh no-music
baseline, so the phase order matters). One known solo occupant, quiet
closed room, inert mapping, playback enabled.

1. **Warm the standalone signature. [HUMAN]** Start the vocal-pop phase
   track (the exact track matters — signatures are per-track; note its
   id) in Spotify at the baseline volume (~33% output; verify music
   mic-side reads −31…−33 dBFS on the dashboard) and stay silent
   ~2 minutes.
   Checkpoint (Claude): `data/track_signatures.json` holds that track
   id with `refs >= 3`. (The signatures file is the evidence — there is
   no per-tap log line.)
2. **Phase 1 — flat reading, no music. [HUMAN]** Pause Spotify. Read
   the neutral script in a deliberately flat monotone for 3 minutes;
   bank ~6 Good-call taps. This phase is also what seeds the
   clean-speech baseline the pull estimator needs — do not linger
   silent between phases; the baseline ages out at
   `RTR_MUSIC_BASELINE_MAX_AGE_S` (default 300 s).
3. **Pull warm-up — flat reading over the track, no taps. [HUMAN]**
   Restart the warmed track at the same volume and keep reading in the
   same monotone for ~90 seconds WITHOUT banking taps: each reading
   banks a pull sample against the fresh phase-1 baseline.
   Checkpoint (Claude): the track's entry in
   `data/track_signatures.json` now shows `pull_refs >= 3`, and the
   frame log shows `emotion_correction.basis` switching from
   `standalone` (or discount) to `pull`.
4. **Phase 2 — same reading, same track, taps. [HUMAN]** Continue the
   monotone for 3 more minutes; bank ~5 taps. The "hearing through
   music" chip must be visible with basis `pull` — if it isn't, stop
   and diagnose before banking taps.
5. Analysis (Claude, offline): mean certified V/A per phase from the
   banked frames. **Pass: |ΔV| < 0.2 AND |ΔA| < 0.2.** Record both
   deltas, the correction amounts and basis on the phase-2 frames
   (`emotion_correction`), the dominance range, the pull signature's
   value, and the baseline the pulls were measured against — these
   numbers are the β calibration record. If either axis fails, bank
   the numbers anyway; they say whether `RTR_MUSIC_BETA_V/_A` under-
   or over-correct, and the knobs move after the gate.

## Part (d) — positive control: emotion still hears the room

Immediately after part (c), same track still playing, same volume. (By
now the phase-1 baseline is past its age bound, so pull banking has
stopped — the correction is a fixed shift and must not eat the mood
change; 2026-07-11 set the bar at +0.68 arousal separation.)

1. **[HUMAN]** 3 minutes of genuinely animated, enthusiastic talking
   (phone a friend, tell a story — real energy, not acting the script);
   ~5 Good-call taps.
   Checkpoint: certified V/A on those frames leaves the flat quadrant
   and reads meaningfully higher than phase 2 (mood excited/chill with
   arousal clearly above the phase-2 mean). Suppression that flattens
   this phase **fails the gate** even if part (c) passed.
2. (Claude) Confirm the phase-2 vs part-(d) separation survives in the
   corrected values — the shift-not-mute property, live.

## Part (e) — anchor persistence

1. Run a dashboard session with playback off long enough to seed the
   anchor (~2 min quiet), quit. Checkpoint: `data/advisory_anchor.json`
   exists with a fresh `ts`.
2. **[HUMAN]** Start Spotify playing loud (~90%) FIRST, then start the
   dashboard mid-playback. Stay silent.
   Checkpoint: the advisory banner rises within ~10 hops — the restored
   anchor is doing the judging (pre-M6 this session shape had no anchor
   and the banner stayed dark against the chased floor).
3. Sanity: delete the anchor file, repeat — banner behavior reverts to
   the live-floor fallback (may stay dark). Restore normal volume.

## Part (f) — 30-minute live DJ session (regression sweep)

Real playlist mapping, normal evening use, taps as they come.

Checkpoints, from the logs afterward:
- recommendations fired on corrected readings (frames with
  `emotion_correction` present feeding non-guard cells);
- signatures accumulated for multiple tracks in
  `data/track_signatures.json`;
- presence stamps on the session's played_throughs are sane (occupied
  while people talked; basis values sensible);
- no advisory false positives at normal volume;
- `python scripts/tuning_report.py` reads the session back cleanly.

## Gate summary

| part | what | pass condition |
|---|---|---|
| (a) | bench regression | p95s within the M2 gate |
| (b) | pytest | green on the Mac |
| (c) | part (f) re-run, post-fix | pull < 0.2 both axes (baseline +0.26/+0.39) |
| (d) | positive control | animated phase clearly separates from monotone under the same music |
| (e) | anchor persistence | mid-playback start rises the banner via the restored anchor |
| (f) | 30-min live DJ | corrected readings drive the DJ; no presence/advisory regressions |
