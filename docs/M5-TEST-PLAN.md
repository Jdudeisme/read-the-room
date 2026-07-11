# Milestone 5 Test Plan — Learn from the room

Logic-level tests live in `tests/test_presence.py` plus the M5 additions to
`tests/test_tuning_report.py`, `tests/test_dashboard.py`,
`tests/test_playback.py`, and `tests/test_state.py` (no mic, no models, no
network; the presence criterion's acceptance numbers are frozen as fixtures
carrying the real corpus values). This plan covers the milestone gate,
which runs on the 2019 Intel MacBook Pro against the real corpus and a
live playback session.

> **For Claude Code on the Mac:** this document is your session script.
> When the user asks to gate M5, walk them through it **one part at a
> time, in order**: run the commands you can run yourself, tell the user
> exactly what to do for the steps only they can do (talking, leaving the
> room, turning volume up), and verify each checkpoint before moving on.
> Steps only the human can perform are marked **[HUMAN]**. Record results
> where each section says — bench numbers in the README results table,
> the part (f) measurement in a new dated `docs/FIELD-NOTES.md` entry,
> gate outcomes summarized to the user at the end. If a checkpoint fails,
> stop and diagnose before continuing.

## Part 0 — setup (prerequisite, not the gate)

```bash
git pull && git checkout milestone-5-learning
source .venv/bin/activate
python -m pytest -q              # expect: 243+ passed, no network
```

The corpus must be current: pull `read-the-room-data` and sync
`annotations/` and `overrides/` into `data/` (both directions — if the Mac
has sessions the private repo lacks, push them first; the corpus repo is
the source of truth).

Checkpoint: pytest green, and `python scripts/tuning_report.py` header
reads at least `113` annotation records and `60` override records.

## Part (a) — bench regression

M5 adds a few comparisons per hop (advisory) and additive fields; any
drift is a bug, not a cost.

```bash
RTR_TORCH_THREADS=2 python scripts/bench_headcount.py --fallback
```

Checkpoint: headcount contended p95 < 1.37 s, emotion overall p95 < 1.2 s
— same gate as M2/M3/M4. Record the row in README's results section.

## Part (b) — test suite

```bash
python -m pytest -q
```

Checkpoint: all green on the Mac (the acceptance fixtures in
`tests/test_presence.py` encode the corpus contract; if any of those fail
here but passed on the PC, the environments disagree about defaults —
check `RTR_PLAYBACK_PRESENCE_*` in `.env`).

## Part (c) — retroactive presence filter on the real corpus

```bash
python scripts/tuning_report.py
```

Checkpoints, all from section 4a and the `gated:` header line:

1. Exactly these six lines are flagged `absent (retro)` — Thriller
   (2026-07-10 20:44:53), Just the Way You Are (21:20:04), Just In Time
   (21:21:54), Mumbles (22:27:47), All The Things You Are (22:30:41),
   Atrebor (23:09:28).
2. The four known-real part (c) completions from 2026-07-06 are **not**
   flagged (Game Over, Earth Song, Rhymes Like Dimes, Minor Blues — the
   last one survives via the warm-handoff clause; if it's flagged, the
   handoff window moved).
3. The gated line reports the excluded counts and the corpus files on
   disk are byte-identical before and after (the report never writes).

## Part (d) — live presence round-trip (playback session)

One dashboard run with playback enabled and the real playlist mapping.
Short tracks make this fast — a mapped playlist with 1–2 minute tracks is
ideal (checkpoint arithmetic works for any length).

1. **Occupied completion.** **[HUMAN]** Talk near the laptop (normal
   conversation is fine) through the end of a track and let it complete
   without touching anything.
   Checkpoint: today's `data/overrides/` file gains a `played_through`
   line with `schema_version: 2`, `presence.occupied: true`,
   `presence.basis: "fresh"`.
2. **Empty-room completion.** **[HUMAN]** Start a track, then leave the
   room (silence, no taps) for its entire remaining length plus ~a
   minute, staying out past the completion.
   Checkpoint: the completion logs `presence.occupied: false` with basis
   `absent` — and the line is still *written* (the gate labels, never
   drops).
3. **Tap rescue.** **[HUMAN]** Start a track, stay silent the whole
   track, but tap Good call once mid-track.
   Checkpoint: `presence.basis: "tap"`, `occupied: true`,
   `last_tap_age_s` less than the track duration.
4. Re-run `python scripts/tuning_report.py`: the new stamped lines appear
   with `(stamped)` in section 4a if gated, and the stamped verdicts are
   honored (no retro recomputation).

## Part (e) — envelope advisory

Same session or a fresh one, playback active.

1. **[HUMAN]** Set output volume to the known beyond-envelope level
   (~90%+ on the MacBook — the 2026-07-10 session read music at
   −22 dBFS against a −44 floor) and stay silent.
   Checkpoint: within ~20–30 s (10 hops) the dashboard shows the
   "music is out-reading the room" banner.
2. **[HUMAN]** Drop volume to the working envelope (~60%) and speak.
   Checkpoint: the banner clears within a few hops once speech
   certifies.
3. Confirm from the JSONL/frames that `envelope_advisory` was `true`
   during the loud window — the marker the corpus filter keys on going
   forward.

## Part (f) — the flat-affect measurement (feeds the music-detection review)

The missing number behind the deferred ML gate: how far does vocal music
drag valence/arousal during *certified* speech at in-envelope volume?
Protocol (part (d)-of-M4 style, one known solo occupant, quiet room,
moderate volume — music mic-side around −35…−45 dBFS):

1. **[HUMAN]** 3 minutes: read a neutral script aloud in a deliberately
   flat affect, **no music**. (Claude: note the wall-clock window.)
2. **[HUMAN]** 3 minutes: the same script, same delivery, over **vocal
   pop** (the RTR · Pop · high playlist).
3. Bank a handful of Good-call taps in each phase so the frames are in
   the annotation log with exact values.

Analysis (Claude, offline): compare certified valence/arousal between
the two windows — report the mean shift and whether mood quadrant
flipped. Record the protocol, numbers, and a one-paragraph verdict in a
dated `docs/FIELD-NOTES.md` entry. Per the M5 proposal, a pull of
≥ 0.2 toward the song's quadrant during certified speech is the
threshold that reopens the build decision; below that, the deferral
stands with data behind it.

## Gate summary

| part | what | pass condition |
|---|---|---|
| (a) | bench regression | p95s within the M2 gate |
| (b) | pytest | green on the Mac |
| (c) | retro filter, real corpus | exactly the six named lines flagged; the four known-real survive; nothing written |
| (d) | live presence round-trip | fresh / absent / tap all observed, schema v2 |
| (e) | envelope advisory | raises loud-blind, clears on recovery |
| (f) | flat-affect measurement | executed and recorded in FIELD-NOTES (a number, not a pass/fail) |
