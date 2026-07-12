# M7 Prompt — The stable middle (founder direction, 2026-07-11)

Written on the Mac the night M6 merged (`9cfc033`), for the Claude Code
session on the PC to turn into `docs/M7-PROPOSAL.md`, a branch, and an
implementation — the pattern that has now shipped three milestones:
prompt → proposal → build on the PC → gate live on the Mac, where the
gate has caught a real design flaw every single time (M5's advisory
floor, M6's standalone signature). Plan for that.

Before proposing, read in this order: the 2026-07-10 trio-evening entry
in `docs/FIELD-NOTES.md` (finding 1 — the mandate), the 2026-07-10
part (d) entry (phase 2b — the other half of the mandate), the
headcount root-cause paragraphs in the README results section, and
`src/sensing/headcount.py` end to end.

## Mission

Headcount has validated ends and a broken middle. Solo is stable
(post-M3 threshold recalibration), crowd-regime is a designed blend,
silence holds honestly — but the 2–4 person range, **where an apartment
party actually lives and where the rulebook's cells matter most**,
fails in both directions:

- **Trio undercount (the merge).** Three real voices read solo/pair for
  essentially an entire evening at cluster threshold 0.70 — only
  overlapping goodbye chatter ever separated them into bucket 4 (which
  is the *correct* bucket for a true 3). Similar voices merge, and
  turn-taking conversation starves each speaker's cluster of
  contiguous evidence. Wrong-call taps from 2026-07-10 mark the ground
  truth (e.g. 21:10:28, `solo` during three-way porch chat).
- **Animated-pair overcount (the crowd path).** Two excited people in a
  quiet room hit bucket 8 (raw_clusters 3, crowd_weight 0.27) — the
  small-magnitude cousin of the pool session's pair→16. Root cause has
  been known and fenced since M3: `separation_score` returns 0 for a
  single cluster, so `sep_collapse` pins at 1.0 and
  `crowd_weight = saturation × smear` — a loud, dispersed solo/pair
  reads as babble. M4's floor-relative saturation shaved it; the
  sep_collapse misfire itself is still in the code (`headcount.py`,
  crowd-regime block).

M7 makes 2–4 real people in ordinary conversation read as 2–4: **fix
both failure directions without wrecking the validated ends.**

## What we know (hard-won; don't re-derive it)

From the 2026-07-05 root-cause work and since:

- Same-speaker ECAPA cosine distances on the pipeline's 1.25 s segments
  center ~0.35 (p90 0.47) on clean audio, ~0.60 (p90 0.75) on the Mac's
  mic; different-voice pairs ~0.94 (p10 0.87). The 0.70 threshold was
  set from these measurements — there is no magic threshold left to
  find between distributions that overlap on this hardware.
- Quiet/monotone speech scatters embeddings far worse than animated
  speech (within-distance 0.669 vs 0.584) — fragments accrete.
- Proportional min-mass (`min_cluster_evidence_frac=0.10`) keeps debris
  out; 0.20 breaks 4-voice TTS separation. Fragmentation runs 0.6–1.0
  under music+speech and the floor held (M4 part (d)).
- Segment length (2.5 s/5 s), peak normalization, denoising, and
  resampling were all measured as non-fixes.
- **The goodbye clue:** overlapping/simultaneous speech separated three
  voices that turn-taking never did. Hypothesis worth building on:
  *temporal* structure — who alternates with whom, how often the active
  embedding jumps — carries count evidence the spatial clustering
  throws away.

## Design space (evaluate, pick, defend in the proposal)

1. **Kill the sep_collapse misfire first** — it's a known bug with a
   contained fix (single-cluster separation must not read as maximal
   collapse). This alone may fix the animated-pair overcount. Cheap,
   testable, do it regardless of what else ships.
2. **Turn-taking / alternation evidence.** Count speaker *transitions*,
   not just clusters: rapid alternation between embedding regions that
   merge spatially is still evidence of ≥2 voices. A
   transition-rate signal blended into the estimate (or used to split
   a suspiciously heavy cluster) attacks the merge without touching
   the threshold.
3. **Within-cluster multimodality tests.** A merged cluster of two
   voices should look bimodal at 0.70; a silhouette / gap-statistic
   check on heavy clusters ("would splitting this improve
   separation?") is a bounded-cost refinement pass.
4. **Overlap detection.** The goodbye separated because people spoke
   simultaneously. Overlapped-speech frames (VAD-certified,
   multi-source) are direct ≥2 evidence — even a crude spectral
   flatness/energy heuristic on certified speech may pay.
5. **Evidence-quality weighting.** Animated speech clusters tight,
   monotone scatters — weight evidence by within-segment coherence so
   a subdued speaker's debris doesn't out-vote a clear one.

Hard constraints: the bench gate is untouchable (`RTR_TORCH_THREADS=2`,
headcount contended p95 < 1.37 s on the 2019 Intel Mac — the clustering
refinements above run inside the existing worker budget); logic-level
tests, no models/network in the suite; **headcount only — do not touch
the emotion/music path** (M6 just gated; one estimator per milestone is
the rule that kept every gate attributable).

## Evaluation assets

- The corpus (private repo `Jdudeisme/read-the-room-data`) carries the
  2026-07-10 trio evening with deliberate wrong-call taps on undercount
  frames, and the part (d) phases with the 2b overcount.
- The macOS-TTS multi-voice harness from the M3 root-cause work
  (2/4-voice validation) — rebuild it if the scripts are gone; it's the
  controlled-N ground truth for offline iteration.
- Pool recordings exist outside the repo (`UofA Pool RTR Test M3
  copy.m4a`, 3:22) for the hostile-room replay.
- **Ask the founder to record mic audio during the next multi-person
  session** — the trio evening wasn't recorded, and offline replay is
  how M3's root-cause got done.

## Explicitly out of scope

- The M6 cold-start seam (baseline harvesting in crossfade gaps,
  genre-level pull priors) — field-noted 2026-07-11 late evening, real,
  and **not this milestone**: it's emotion-path work and M7 is a
  headcount milestone. Presumptive M8 candidate alongside whatever
  part (d)-style measurement it needs.
- Learned tuning v2, auto-volume, echo cancellation, multi-zone (all
  standing deferrals).
- Club/dancefloor-scale crowd estimation — the pool regime gets its
  replay as a regression check, not a target.

## Ground rules carried forward

- Measure-first: offline replay against labeled audio before any live
  gate; no in-session tuning during gates; knobs move on gate numbers.
- FakeProvider lesson: fixtures model measured reality (use the real
  distance distributions above, not idealized ones).
- The Mac is the validation machine. Write `docs/M7-TEST-PLAN.md` in
  the session-script style ([HUMAN] steps marked) and design the live
  gate around **a scheduled multi-person session — the founder needs
  friends over for solo→pair→trio(→4 if available) ladders with
  Good/Wrong taps**; say so explicitly in the plan so it gets
  scheduled, and include the mic-recording step. Regression bars: solo
  stability, silence-holds semantics, M4 part (d) contamination
  behavior, the crowd blend under pool-style replay, and the M6 gate's
  emotion numbers untouched.

## Deliverables, in order

1. `docs/M7-PROPOSAL.md` (approach chosen from the design space, with
   offline replay evidence on the TTS harness + corpus)
2. Branch `milestone-7-stable-middle`
3. Implementation + tests
4. `docs/M7-TEST-PLAN.md`
