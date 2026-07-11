# Milestone 5 Proposal — Learn from the room (DRAFT)

M4 closed the loop mechanically: sense → recommend → play → observe → log.
M5 makes the loop learn — at the sophistication the corpus supports. Through
2026-07-10 that corpus is 113 annotations (104 good / 9 wrong) and 60
override records (4 skip, 10 wrong_vibe, 16 manual, 30 played_through); at
n≈60, counting and rates are the right tools. Nothing here trains a model,
nothing adjusts itself mid-session, and every output remains an inspectable
proposal a human applies by hand (`.env` edit) — the M3/M4 contract,
unchanged.

Source of direction: `M5_PROMPT.md` (founder, 2026-07-11). Evidence base:
both 2026-07-10 entries in `docs/FIELD-NOTES.md` and a fresh analysis of all
30 played_through records in the corpus (tables below).

## Deliverable 1 — `played_through` presence gate (prerequisite, first)

The trio evening banked played_through "weak positives" for tracks that
completed in empty rooms (Thriller at 20:44:53, the silent-living-room
pair, the empty-room-AC pair, Atrebor at the goodbye). An empty room can't
veto; learn from the corpus as-is and it rewards whatever plays to nobody.

### The criterion, derived from the corpus

Every played_through record already carries the completion-frame
`headcount_staleness_s` (seconds since the last speech-certified evidence)
and the track's `duration_s`. Plotting all 30 records on those two axes
splits them into three regimes:

| regime | signature | reading |
|---|---|---|
| **fresh** | staleness ≤ ~60 s at completion | someone was audible near the track's end |
| **middle zone** | 60 s < staleness < duration | speech existed mid-window, then the room went silent long before the end — the departure signature |
| **warm handoff** | duration ≤ staleness ≤ duration + ~30 s | zero speech during the entire track, but the room was certified occupied within ~30 s of its start and never audibly emptied — the quiet-listener signature |

Beyond `duration + handoff` the room was already stale when the track
began: nobody was there to hand off. The proposed rule:

> **occupied** iff `staleness ≤ FRESH_S` (default 60), **or**
> `duration ≤ staleness ≤ duration + HANDOFF_S` (default 30), **or** a
> human tap (annotation or override) landed inside the track's play
> window. Env vars: `RTR_PLAYBACK_PRESENCE_FRESH_S`,
> `RTR_PLAYBACK_PRESENCE_HANDOFF_S`.

Against the real corpus this satisfies the prompt's acceptance set exactly
(verified offline on the PC, 2026-07-11; no taps fall inside any flagged
window):

- **Flagged (7):** all six named empty-room lines — Thriller (stale 485 vs
  duration 357.8), Just the Way You Are (211.7, middle zone), Just In Time
  (323.7 vs 109.5), Mumbles (115.6, middle), All The Things You Are (289.6
  vs 176.9), Atrebor (121.7, middle) — plus one conservative extra, Gas
  Drawls (20:38:54, stale 125 vs duration 223.5: a genuine mid-session
  quiet spell during the indoor trio segment lands in the middle zone).
- **Survive (23):** the four known-real part (c) completions — Game Over
  (stale 3.2), Earth Song (5.2), Rhymes Like Dimes (20.0), and critically
  **Minor Blues** (stale 244.0 on a 219.5 s track = warm handoff: the
  founder listened in silence, last certified 24.5 s before the track
  started) — plus every occupied-evening completion and the solo close-out
  (4 Lieder, stale 54, fresh).

Two honest edges, both quantified and both acceptable:

- **The handoff clause saves one empty-room line:** It's Only A Paper Moon
  (21:47:07, stale 221.6 on 205.0 s) — a warm handoff into a track that
  played to an emptying porch. On every recorded signal it is *identical*
  to Minor Blues; no function of the sensing record separates a quiet
  listener from a just-departed room. Saving quiet listeners costs one
  known false negative in 30. (Dead Bent, the full-volume blind-window
  completion, also survives via handoff — correctly: people were present;
  Deliverable 2's envelope filter excludes it from learning anyway.)
- **The middle zone flags one occupied line** (Gas Drawls). Flag-never-
  delete semantics make this safe: the line stays in the log, marked.

### Implementation

- **Live gate** in the dashboard's `played_through_sink` (the layer that
  already stamps the completion frame; the controller stays
  sensing-blind). Computable from the completion frame + track duration —
  the exact arithmetic above — plus a last-tap timestamp the app already
  observes for free on every POST. Records gain a stamped evidence block
  and `schema_version: 2`:

  ```json
  "presence": {"occupied": true, "basis": "fresh|handoff|tap|absent|unknown",
               "staleness_s": 3.2, "track_duration_s": 67.7,
               "fresh_s": 60.0, "handoff_s": 30.0, "last_tap_age_s": null}
  ```

  `unknown` covers a missing staleness (headcount layer disabled, never
  certified): tagged, excluded from learning, counted separately. Gated
  lines are still *written* (`occupied: false`) — the gate labels, it
  never drops a record.
- **Retroactive filter** in `tuning_report.py`: v1 (unstamped) records get
  the same criterion applied on the fly, cross-referencing same-day tap
  timestamps; a new report section lists suspect lines with their numbers.
  Flags, never deletes, never rewrites the files.

## Deliverable 2 — learned tuning v1: proposals, not autopilot

All of it lives in `scripts/tuning_report.py`, extending the existing
sections; output stays stdout-only, applying anything stays a human `.env`
edit.

- **Corpus gating (input side).** Sections 4–6 and everything below run on
  the presence-gated corpus: played_through lines that are `occupied:
  false` (stamped or retro-derived) are excluded from rates, with an
  audit line stating how many were excluded and why. Without the gate,
  6 of 30 weak positives in today's corpus are empty-room noise.
- **Operating-envelope filter.** The full-volume window (07-10,
  21:48–22:27) banked wrong-call and wrong_vibe taps against a system
  that could not hear the room (music −22 dBFS over voices at −25…−30,
  speech_ratio pinned to 0, staleness 200+ s). Those are envelope
  artifacts, not preference signal. Filter: exclude vetoes whose tap-time
  frame shows `playback_active` with `speech_ratio ≈ 0` **and** emotion
  staleness > 60 s — the "judged while blind" signature, readable from
  every frame in the corpus. (Once `noise_floor_dbfs` lands in state —
  Deliverable 4 — the filter can key on loudness-over-floor directly.)
- **Boundary proposals fed by strong labels.** Section 3's
  shift-suggestion machinery currently reads only annotations; extend it
  to weigh vetoes (skip/wrong_vibe) as wrong-equivalents and gated
  played_throughs as good-equivalents, reported separately so the two
  evidence grades never silently blend.
- **Tier-cutoff proposals.** Section 6 already counts manual-pick tier
  disagreement (today: higher 3 / lower 5 / same 8). Grow it into the
  section-3 idiom: candidate `RTR_PLAYBACK_TIER_*` shifts with how many
  past manual picks each would have agreed with.
- **Per-cell pool weighting — forward-looking only.** Vetoes judge a
  selection but today's records don't say which *genre* the vetoed track
  came from (only manual picks carry a chosen genre). Stamp the
  selection's `(genre, tier)` into the controller's attribution (and thus
  every future override line); propose pool reorderings only once that
  data exists. Retro genre recovery via playlist lookups would need the
  network — out.

## Deliverable 3 — the music-detection decision: **defer the ML gate, build the advisory**

The build-or-defer memo the prompt asked for, grounded in part (d):

- **Defer, because the measured baseline removes the main motivation.** At
  moderate volumes the strict `vad_playback_threshold` already rejects
  sung vocals outright (phase 5: speech_ratio ≤ 0.003 across five minutes
  of Pop in a silent room; phase 3 same for instrumental). There is no
  phantom-speaker problem for an ML gate to solve at in-envelope volumes.
- **What remains is (a) valence bleed during real speech, (b) the
  beyond-envelope volume regime, (c) hard rooms.** For (b), ML is the
  wrong tool anyway: at 93% output the gate *over*-suppressed — the
  system's problem was blindness, not phantoms, and a better music
  detector cannot hear voices the mic can't separate. For (a) the
  evidence is one informal datapoint (07-06: deliberately mellow human
  read excited under hip-hop). For (c) we have no playback data at all.
- **The cheap non-ML piece worth building now: a dashboard envelope
  advisory.** When `playback_active` and the blind signature holds for N
  consecutive hops (speech_ratio ≈ 0 while loudness sits ≥ X dB over the
  rolling noise floor — thresholds env-tunable, seeded from the 07-10
  numbers: music −22 dBFS vs floor ≈ −44), the dashboard shows "music is
  out-reading the room — lower the volume if you want me listening."
  Server computes it per frame (a few comparisons), page renders a
  banner; degrade-to-shadow behavior untouched. This directly addresses
  the limit cycle with zero model risk, and its trigger doubles as the
  logged marker for envelope filtering (Deliverable 2).
- **What would flip the decision to build:** (i) the flat-affect
  measurement (below) showing valence during *certified speech* pulled
  ≥ 0.2 toward the song's quadrant at in-envelope volumes, or (ii) a
  hard-room session (pool-style) showing phantom certification the
  threshold gate can't hold, or (iii) the advisory proving insufficient —
  users ignoring it and the corpus filling with blind-window labels.
- **The missing measurement, specified for the Mac** (goes in
  M5-TEST-PLAN as a part-(d)-style phase): known solo occupant, vocal
  playlist at the phase-3 volume, speaker reads a neutral script in a
  deliberately flat affect for 3 minutes over the music, then the same
  script in silence. Compare certified valence/arousal between the runs;
  the delta is the contamination number the 07-06 anecdote lacks.

## Deliverable 4 — observability (small, queued from the gate)

- `RoomState.noise_floor_dbfs: float | None` — the engine's seeded
  rolling-floor EMA, currently invisible (part (d) phase 1 noted the gap;
  phases 3–6 and the advisory both lean on floor-relative terms).
  Additive, default `None`; stamped into dashboard frames and thus every
  annotation/override snapshot.
- If cheap while in there: the last few raw headcount log2 estimates on
  the reading (the pool session's `pair → 16` attribution gap). Additive
  fields only; no behavioral change to any sensing path.

## Explicitly out of scope (per founder direction)

- **The headcount estimator.** The trio-undercount / animated-pair-
  overcount "no stable middle" problem is real, documented, and its own
  milestone. M5 code does not touch clustering, thresholds, or the crowd
  path.
- Auto-volume, echo cancellation, crossfade, multi-zone (M4 deferrals
  stand). No online learning, no model training, no self-adjustment.

## Gate (sketch — full checklist in `docs/M5-TEST-PLAN.md`)

On the 2019 Intel MacBook Pro, as always (`RTR_TORCH_THREADS=2`):

1. Bench regression: `bench_headcount.py --fallback` within the M2 gate
   (headcount p95 < 1.37 s, emotion overall p95 < 1.2 s) — M5 adds a few
   comparisons per hop at most; any drift is a bug.
2. `pytest` green; presence-gate acceptance encoded as fixtures carrying
   the real corpus numbers (no real data or network in the suite).
3. Retroactive filter on the real corpus: the six named empty-room lines
   flagged, the four known-real part (c) completions survive, and the
   report states the gated counts.
4. Live round-trip: one occupied completion logs `presence.basis: fresh`;
   one deliberate empty-room completion logs `occupied: false`; both
   lines schema v2.
5. Envelope advisory fires under a brief re-creation of the 93%-output
   condition and clears on volume drop.
6. The flat-affect-over-vocals measurement executed and recorded in
   FIELD-NOTES.md — the number feeds the next music-detection review.
