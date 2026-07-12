# Milestone 7 Test Plan — The stable middle

Logic-level tests live in `tests/test_headcount.py` (the M7 tier:
`TestM7StableMiddle`, plus recalibrated M4 contamination fixtures and the
new-ladder tests; no mic, no models, no network). This plan covers the
milestone gate, which runs on the 2019 Intel MacBook Pro with real
people. The numbers to beat are the 2026-07-10 baselines: a real trio
read **solo/pair essentially all evening** (wrong-call taps at e.g.
21:10:28), and an animated pair in a quiet room hit **bucket 8**.

> **For Claude Code on the Mac:** this document is your session script.
> Walk the founder through it **one part at a time, in order**; steps
> only the human can perform are marked **[HUMAN]**. Bench numbers go in
> the README results table; part (c)/(d) numbers go in a dated
> `docs/FIELD-NOTES.md` entry. If a checkpoint fails, stop and diagnose.
> **No in-session tuning:** `RTR_HEADCOUNT_RESCUE_MARGIN` and friends
> move after the gate, by the gate's numbers — ideally recalibrated
> offline from part (c)'s recorded audio before any re-run.
>
> **This gate needs scheduling.** Part (c) requires the founder plus
> **two friends (three if a 4-person rung is wanted) for about an hour**.
> Book the evening before anything else; every other part fits around
> it. If the external-mic direction (FIELD-NOTES 2026-07-11, TV night)
> has landed, decide ONE mic setup before starting and keep it for the
> whole gate — rescue-margin calibration is per-acoustic-setup.

## Part 0 — setup

```bash
git pull && git checkout milestone-7-stable-middle
source .venv/bin/activate
python -m pytest -q              # expect: 287+ passed
```

Sync the corpus (`read-the-room-data`) both ways per its README. Confirm
`.env` carries no leftover `RTR_HEADCOUNT_*` overrides — the gate
measures the defaults (rescue margin 0.80).

**Rulebook note for the founder:** buckets **3** and **6** are new; 3
seeds from the 2020 "small" grid, 6 from "medium". Skim the pools
(`src/mapping/rulebook.py`) before the session — a bad seed cell will
show up as odd DJ picks during part (f), not as a gate failure.

Checkpoint: pytest green; `python scripts/tuning_report.py` reads the
full corpus back cleanly.

## Part (a) — bench regression

M7 adds centroid arithmetic (O(k²) on a handful of clusters) inside the
existing clustering pass — the contended profile must be unchanged.

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

## Part (c) — the ladder night (the milestone)

One continuous dashboard run, **inert playlist mapping** for phases 1–6
(playback stays OFF until phase 5 — the first phases baseline the quiet
room), normal mic input level (~34%), quiet closed room or porch.

**[HUMAN] Record raw mic audio for the entire session** (QuickTime →
File → New Audio Recording, or `sox -d m7-gate.wav`). The trio evening
wasn't recorded and its root-cause had to be reconstructed from frame
diagnostics; offline replay of tonight's audio is how any calibration
iteration happens without a second friends-night. Park the file next to
the corpus, note its start wall-clock time so frames align.

Phases — natural, uneven conversation throughout ("not round-robin":
let whoever talks most talk most; the estimator must handle the real
airtime distribution). Bank Good/Wrong taps per the verdicts below;
note phase boundaries by wall-clock.

1. **Solo, ordinary speech (5 min). [HUMAN]** Founder alone, normal
   talking with natural pauses.
   Checkpoint: bucket `solo` after warm-up; `rescued_clusters` 0 on
   every frame; crowd_weight ≈ 0.
2. **Solo, loud + animated (3 min). [HUMAN]** The sep-fix's own
   regression: excited, loud, continuous solo talking.
   Checkpoint: bucket holds `solo`/`pair`, **never higher**;
   crowd_weight ≈ 0 (pre-M7 this signature drove the crowd path).
3. **Pair, animated (5 min). [HUMAN]** Founder + friend 1, genuinely
   excited conversation — the part (d) phase-2b re-run.
   Checkpoint: bucket `pair` (a `3` blip is tolerable; **any bucket
   above 4 fails** — that was the bucket-8 overcount).
4. **Trio, ordinary (10 min). [HUMAN]** All three, natural conversation
   with drinks-on-the-porch dynamics. This is the phase the milestone
   exists for; tap generously (target ≥ 8 verdicts).
   Pass: bucket publishes **3** for sustained stretches (transient
   `pair` while rescues come and go is expected — the honest residual);
   **zero `solo` frames** after buffer warm-up; undercount-hop fraction
   (frames reading solo/pair) materially below the 07-10 baseline
   (≈ 100%); `rescued_clusters ≥ 1` visible on frames while a quiet
   participant's evidence is fresh.
5. **Trio over music (5 min). [HUMAN]** Start a track by hand in
   Spotify at ~33% output; keep talking. The M4 part (d) regression bar
   plus M7 under playback.
   Checkpoint: speech stays certified (speech_ratio well above 0);
   **no phantom bucket growth** vs phase 4; emotion corrections (M6)
   still apply with sane dominance/basis — M7 touched no emotion-path
   code, this frame check is the attribution evidence.
6. **Silence hold (3 min). [HUMAN]** Music off, everyone quiet.
   Checkpoint: bucket freezes at its last value, staleness grows
   linearly, raw diagnostics frozen — the silence-is-absence-of-evidence
   semantics, unchanged.
7. **Goodbye chatter / 4-person if available (5 min). [HUMAN]**
   Overlapping, simultaneous talk (the 07-10 goodbye regime); with a
   fourth person, this is the 4-rung phase.
   Checkpoint: trio overlap reads **3–4, never 8** (pre-M7: smoothed to
   8 offline); a true 4 reads 3–6, never pair.

Afterwards (Claude, offline): per-phase bucket/raw/rescued tables from
the banked frames into FIELD-NOTES; the undercount-hop fraction against
the 07-10 baseline; and the phase-4 frames' `headcount_separation` /
`rescued_clusters` — if the trio still undercounts, those plus the
recorded audio are the recalibration record (the two pre-scoped
escalations, in order: rescue-aware smoothing, then `rescue_margin`
recalibrated from tonight's audio).

## Part (d) — pool-regime replay (crowd blend regression)

The recordings live outside the repo (`UofA Pool RTR Test M3 copy.m4a`,
3:22, ~7 people + loud fan).

```bash
afconvert -f WAVE -d LEI16@16000 -c 1 "UofA Pool RTR Test M3 copy.m4a" pool.wav
python scripts/tts_harness.py replay-wav pool.wav --truth 7
```

Checkpoint: the crowd blend still escalates ordinally (crowd_weight
rises, bucket climbs past the raw cluster count) — the sep-fix must not
have lobotomized the babble path. The energy-based mask over-includes
fan noise, so judge the blend's *engagement*, not counting accuracy.
(Offline reference: the 4-voice dense-overlap TTS proxy reads bucket 6
against a true 4 with crowd_weight 0.1–0.35.)

## Part (e) — M6 emotion numbers, attribution check

M7's diff touches no emotion/music-path code (verify:
`git diff main..milestone-7-stable-middle -- src/sensing/emotion.py
src/sensing/music.py` is empty; engine changes are headcount-wiring
only). Combined with part (c) phase 5's live check (corrections
applying, dominance sane), the M6 gate numbers stand as regression
bars without a full part (c) re-run. **If** phase 5 shows any emotion
drift, stop: re-run the M6 part (c) protocol before merging, because
attribution is then broken.

## Part (f) — 30-minute live DJ session (regression sweep)

Real playlist mapping, normal evening use (solo or whoever's still
around), taps as they come.

Checkpoints, from the logs afterward:
- recommendations fire from the new buckets when occupancy is 3 or 6
  (matched_cell shows ("3", …) / ("6", …) — the rulebook rows work);
- presence stamps, played_through gating, and the envelope advisory
  unregressed;
- no crowd-regime excursions during ordinary conversation
  (crowd_weight ≈ 0 outside deliberate babble);
- `python scripts/tuning_report.py` reads the session back cleanly,
  including frames carrying the new bucket labels.

## Gate summary

| part | what | pass condition |
|---|---|---|
| (a) | bench regression | p95s within the M2 gate |
| (b) | pytest | green on the Mac |
| (c) | ladder night | trio publishes 3 (zero solo frames post-warm-up); animated pair ≤ 4; solo stays solo; silence holds; mic audio recorded |
| (d) | pool replay | crowd blend still escalates ordinally |
| (e) | M6 attribution | emotion-path diff empty + phase-5 corrections sane |
| (f) | 30-min live DJ | new buckets drive cells; no regressions; corpus reads back |
