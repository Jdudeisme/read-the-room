# Milestone 7 Proposal — The stable middle (DRAFT)

Headcount has validated ends and a broken middle. The 2026-07-10 trio
evening put three real voices in front of the estimator for a full evening
and it read solo/pair essentially throughout (wrong-call taps mark the
ground truth, e.g. 21:10:28 `solo` during three-way porch chat); the same
night's part (d) phase 2b sent two animated people to bucket **8**. M7
makes 2–4 real people in ordinary conversation read as 2–4 — **both
failure directions, without wrecking the validated ends** (solo stability,
silence holds, the crowd blend, and M6's emotion numbers are regression
bars).

Source of direction: `M7_PROMPT.md` (founder, 2026-07-11). Evidence base:
the 07-10 field notes, the corpus through 2026-07-11, and a rebuilt
controlled-N TTS harness (below) that reproduces both failures offline on
the PC.

## Offline evidence 1 — the corpus names the mechanisms

Frame diagnostics from the 07-10 sessions (`scripts/m7_corpus_analysis.py`
over the private corpus):

| segment (truth) | frames | raw_clusters | crowd_wt | dispersion | fragmentation | bucket |
|---|---|---|---|---|---|---|
| 2b animated pair (2) | 2 | 3 | 0.26–0.27 | 0.605–0.608 | 0.23–0.32 | pair→**8** |
| indoor trio DJ (3) | 8 | 1 (6×), 2–4 | 0.00 | 0.56–0.60 | **0.29–1.00** | solo/pair |
| porch 1 (3) | 17 | mostly 1–2 | ≈0 | 0.54–0.62 | 0.52–1.00 | solo/pair |
| porch 2 (3) | 7 | 1–3 | 0.00 | 0.57–0.65 | 0.67–1.00 | solo/pair/4 |

Two readings, both load-bearing:

1. **The failure directions share one signature.** Dispersion 0.54–0.66 —
   exactly the measured same-voice scatter on this hardware (~0.60 mean,
   p90 0.75) — plus heavy evidence mass stuck in min-mass-failing stray
   clusters. In the undercount that evidence is *discarded* (several trio
   frames show fragmentation 0.8–1.0: nearly everything stray, `raw`
   forced to 1); in the overcount the same smear feeds `crowd_weight`,
   which leaks `log2_babble ≈ 7` into a log2-count of 1.58 and lifts the
   smoothed estimate to bucket 8 (phase 2b: `0.74·1.58 + 0.26·7 ≈ 3.0`).
2. **The dispersion ramp knots are calibrated to the wrong world.** The
   crowd-regime smear signal ramps over `[0.5·threshold, threshold]` =
   [0.35, 0.70] — knots set from *clean* same-voice scatter (~0.35). The
   mic-measured scatter (~0.60) lives inside that ramp, so every ordinary
   session on this hardware carries a standing dispersion signal ≈ 0.65
   that only needs `saturation` (animated + loud) to become crowd weight.
   Phase 2b is exactly that. The pool `pair→16` was its big brother.

## Offline evidence 2 — the rebuilt TTS harness reproduces both failures

The M3 harness scripts were gone; rebuilt on Windows OneCore TTS
(`scripts/tts_harness.py` + `tts_synth.ps1`): David/Mark/Zira plus a
formant-shifted Zira as a measured-distinct fourth voice (0.78 vs base —
a pitch-shift-only variant measured **0.40**, i.e. ECAPA reads it as the
same speaker; it is kept as the same-speaker probe). Distance validation
against the 2026-07-05 root-cause numbers, per-segment (1.25 s), cosine:

| condition | within-voice mean (p90) | cross-voice mean (p10) | measured human reference |
|---|---|---|---|
| clean | 0.35–0.41 (0.43–0.52) | 0.79–0.90 (0.70–0.83) | 0.35 (p90 0.47) / 0.94 (p10 0.87) |
| mic-degraded* | 0.58–0.69 (0.71–0.82) | 0.90–0.97 (0.77–0.89) | 0.60 (p90 0.75) / — |

\* +reverb (0.25 s tail) + noise at 12 dB SNR — tuned until same-voice
scatter matched the Mac-mic measurement (FakeProvider lesson: fixtures
model measured reality). David-vs-Mark is a genuinely similar pair
(p10 0.70 clean — at the clustering threshold).

Replaying assembled conversations through the real pipeline
(`speech_segments → ECAPA → HeadcountEstimator → BucketSmoother`,
4 s hop = the production min-interval schedule):

| scenario (truth) | raw mean | final bucket | verdict |
|---|---|---|---|
| solo_clean (1) | 1.00 | solo | ok |
| **solo_mic (1)** | 1.00 | **4, entire session** | **overcount reproduced** |
| pair_clean / pair_similar_mic (2) | 1.97 / 2.00 | pair | ok |
| trio_clean (3) | 2.92 | 4 | ok |
| trio_mic, equal airtime (3) | 3.51 | 4 | ok |
| **trio_uneven_mic (3)** | 2.32 | **pair (65% hops undercount)** | **undercount reproduced** |
| **trio_overlap_mic (3)** | 2.62 | **8** | **overshoot reproduced** |
| quad_clean / quad_mic (4) | 3.81 / 3.30 | 4 | ok |

Two details matter. `solo_mic` reads bucket 4 **all session on
raw_clusters = 1**: the single cluster makes `separation_score` return
0.0, `sep_collapse` pins at 1.0, and `crowd_weight = saturation × smear`
— the prompt's named misfire, live. And equal-airtime trios count fine;
the undercount only appears with the **real-party airtime distribution**
(one speaker holding the floor, others interjecting 1.2–2.5 s).

## Offline evidence 3 — the diagnosis (this is not a threshold problem)

With ground-truth speaker labels attached to every buffered segment
(`scripts/m7_candidates.py diagnose trio_uneven_mic`):

> t=48 s: buffered 60 s. Speaker 0: 45.2 s. Speaker 1: 5.7 s (5 segs).
> Speaker 2: 8.8 s. Clusters: {0: 45.2 s PASS, pure}, {2: 8.8 s PASS,
> pure}, **{1: 5.7 s fail, pure}** — floor is 10% of 60 s = 6.0 s.

**The clustering is speaker-pure. The proportional min-mass floor is the
starvation mechanism.** A speaker holding < 10% of buffered airtime
cannot exist, and the dominant speaker keeps raising the floor. This also
explains the goodbye clue without new machinery: overlapping speech gives
every voice airtime *simultaneously*, so everyone clears the floor — the
temporal-structure hypothesis resolves into an evidence-accounting fact.
(The field's frag-1.0 frames add a second, VAD-side starvation: real
interjections arrive clipped and scattered; the harness's 65% undercount
is therefore a *lower bound* on the field effect.)

The prompt's design-space options were measured against the saved
embedding streams (`m7_candidates.py evaluate`, seconds per sweep, no
re-embedding):

| scenario (truth) | baseline | opt 1 sep-fix | opt 2 tracks | opt 3 split | **M7 = fix + rescue** |
|---|---|---|---|---|---|
| solo_mic (1) | 1.00 / pair | 1.00 / **solo** | 1.05 / solo | 1.00 / solo | 1.00 / **solo** |
| pair_similar_mic (2) | 2.00 / pair | 2.00 / pair | **2.65** / pair | 2.00 / pair | 2.00 / pair |
| trio_uneven_mic (3) | 2.32 / pair | 2.32 / pair | **1.97** / pair | 2.32 / pair | **2.68** / pair |
| trio_overlap_mic (3) | 2.62 / 8 | 2.62 / **4** | 2.59 / 4 | 2.62 / 4 | 2.78 / **4** |
| quad_mic (4) | 3.30 / 4 | 3.30 / 4 | 3.24 / 4 | 3.30 / 4 | **3.65** / 4 |
| quad_overlap_mic (4, pool proxy) | 2.84 / 8 | 2.84 / 4 | 3.16 / 4 | 2.84 / 4 | 3.43 / 8 |
| solo/pair/trio/quad clean | all correct | all correct | all correct | all correct | all correct |

- **Option 1 (sep_collapse fix) is confirmed** — it alone repairs every
  overcount with zero regressions. Ships regardless, per the prompt.
- **Option 2 (turn-taking/alternation tracks) is rejected on numbers**:
  temporal leader-clustering overcounts the similar pair (2.00→2.65) and
  *worsens* the uneven trio (2.32→1.97). Temporal chaining doesn't touch
  the floor, and adds scatter-noise tracks.
- **Option 3 (multimodality split) is a measured no-op**: the clusters
  are already speaker-pure — there is nothing bimodal to split. (It would
  address a spatial-merge failure; the diagnosis shows starvation, not
  merging, dominates at these distances.)
- **Options 4/5 (overlap detection, quality weighting) stay deferred**:
  overlap already yields count evidence through simultaneous airtime once
  the floor is fixed, and quality weighting attacks a mechanism
  (scatter out-voting) the diagnosis didn't implicate.

## Design: fix the misfire, recalibrate the smear, rescue distinct voices

Three contained changes inside `headcount.py`'s estimator — no new model,
no new signal processing, O(k²) centroid arithmetic on top of the existing
O(n³) clustering, so the bench envelope is untouched by construction.

### 1. sep_collapse misfire (the known bug, fixed first)

`separation_score` returns 0.0 when silhouette is undefined (single
cluster / n < 3), which reads as *maximal* collapse. Change: undefined
separation defers to the dispersion evidence instead of pinning 1.0 —
a tight single cluster is a confident solo (collapse ≈ 0), a smeared one
can still read collapsed. A packed room that collapses into one indistinct
cluster keeps its escape hatch through (2).

### 2. Dispersion ramp recalibrated to measured reality

The smear ramp starts at the clustering threshold itself, not half of it:
`ramp(dispersion, threshold, 1.3·threshold)` (env-tunable knots). The
principle: within-cluster dispersion is only *babble evidence* once it
exceeds the distance at which the clusterer would have split the cluster —
i.e. the cluster plausibly holds more than one voice. Mic-scatter solos
(0.58) fall silent; merged multi-voice babble (≥ 0.70 by construction,
cross-voice pairs ~0.9 inside) still fires. Measured effect: solo_mic
pair→solo, trio_overlap 8→4, and the dense-overlap pool proxy still
escalates (crowd_weight 0.2–0.35 via fragmentation smear + defined-
silhouette collapse — the babble path is not lobotomized; the true pool
replay is a gate regression).

### 3. Distinct-voice rescue (the undercount fix, derived from the diagnosis)

The proportional floor stays — it is what keeps solo debris from
ratcheting (M2's bug) and 0.20 is known to break 4-voice separation, so
the floor cannot simply move. Instead, a cluster that fails **only** the
proportional floor still counts when:

- it passes a strengthened absolute floor: ≥ `min_cluster_segments`
  segments **and** ≥ `min_cluster_speech_s` attributed speech (both
  existing knobs, 2 / 2.5 s), and
- its centroid sits ≥ `rescue_margin` (default **0.80**, env-tunable)
  cosine distance from **every** mass-passing cluster's centroid.

That is the measured discriminator between "quiet third person" and
"dominant speaker's scatter debris":

| population | eligible-cluster → passing-centroid distance |
|---|---|
| solo (clean or mic) | **no eligible clusters exist at all** (debris never reaches 2 segs + 2.5 s) |
| similar-pair debris (mic) | min 0.38, median 0.73 — hugs its parent, rejected |
| starved trio voices (mic) | p10 0.74, median 0.82 — distinct, rescued |

The distributions overlap at the tails, which is why the rule requires
distance from *every* passing cluster and why `rescue_margin` is a gate
calibration knob (0.75 rescues more: uneven 2.70, overlap 3.54; 0.85
rescues none on degraded audio). Measured at 0.80: uneven trio 2.32→2.68,
quad_mic 3.30→3.65, **zero** false rescues on solo/pair across clean and
degraded runs.

### Observability (additive, the part-(d)/(e) lesson)

`Estimate`/`HeadcountReading`/RoomState gain `separation` (the silhouette
the collapse logic actually saw) and `rescued_clusters` (how many of
`raw_clusters` came through the rescue). A trio night's frames then
attribute themselves: undercount frames show what the rescue saw and
declined. Dashboard: the headcount card notes "+N quiet voice(s)" when
`rescued_clusters > 0`.

### Honest residual

Offline, the uneven trio improves from "pair, 65% of hops undercounting"
to raw mode 3 / mean 2.68 — but the *published* bucket often stays `pair`,
because rescues are intermittent and the log2-EMA needs sustained raw 3 to
cross the 1.5 rung boundary. The live gate must therefore score **raw
estimates and undercount-hop fraction, not just the bucket**, and real
friends' voices are more similar than TTS voices. Plan for the gate to
catch something — it has every milestone. Two pre-identified escalations
if the trio ladder still undercounts, in order: rescue-aware smoothing
(a rescued voice relaxes the EMA/hysteresis toward higher rungs — bounded,
logic-level) and `rescue_margin` recalibration from the session's own
recorded audio (see below).

## Evaluation assets

- `scripts/tts_harness.py` / `tts_synth.ps1` — the rebuilt harness
  (synth / distances / run), scenario audio + embedding streams under
  `data/tts_harness/` (gitignored, regenerable in ~3 min).
- `scripts/m7_candidates.py` — diagnose (cluster↔speaker composition) and
  evaluate (variant × scenario sweep in seconds, no re-embedding).
- `scripts/m7_corpus_analysis.py` — the corpus failure-signature tables.
- Pool recordings (outside repo, `UofA Pool RTR Test M3 copy.m4a`) — the
  hostile-room replay regression.
- **Founder ask, explicit:** record raw mic audio during the M7
  multi-person gate session (the trio evening wasn't recorded; offline
  replay is how M3's and this milestone's root-causes got done). The
  external-mic direction from the 07-11 TV-night notes should land
  *before* gate night — the gate needs VAD reach.

## Explicitly out of scope (per founder direction)

- **Anything on the emotion/music path** — M6 just gated; one estimator
  per milestone keeps gate failures attributable. The M6 cold-start seam
  and the operating-envelope volume ladder remain presumptive M8 scope.
- The 0.70 threshold itself: measured distributions overlap on this
  hardware; there is no magic threshold to find, and none of the fixes
  above move it.
- Segment length, normalization, denoising, resampling — measured
  non-fixes (2026-07-05).
- Club/dancefloor crowd estimation — the pool regime is a regression
  check, not a target.
- Learned tuning v2, auto-volume, echo cancellation, multi-zone
  (standing deferrals).

## Gate (sketch — full session script in `docs/M7-TEST-PLAN.md`)

On the 2019 Intel MacBook Pro (`RTR_TORCH_THREADS=2`), inert playlist
mapping for controlled phases. **The centerpiece is a scheduled
multi-person session — the founder needs friends over** for
solo→pair→trio(→4 if available) ladders with Good/Wrong taps, **with raw
mic audio recorded** (see founder ask above):

1. Bench regression: `bench_headcount.py --fallback` within the M2 gate
   (the changes are arithmetic on existing intermediates; this is a
   re-confirmation, not a budget question).
2. `pytest` green — logic-level fixtures drawn from the measured distance
   distributions (clean and mic rows of the table above), covering: solo
   debris produces no rescue, starved-speaker rescue fires, single-cluster
   collapse no longer pins, dense-overlap crowd blend still escalates,
   4-voice separation at frac 0.10 unbroken.
3. **Ladder phases (the milestone):** for each rung, ≥ 5 min ordinary
   conversation (not round-robin — natural, uneven airtime) + taps. Pass:
   trio phases read pair-or-4 with **zero solo frames** after buffer
   warm-up, undercount-hop fraction materially below the 07-10 baseline
   (solo/pair ≈ all evening), and `rescued_clusters` visible in frames.
   Animated-pair phase: **no bucket above 4** (the 2b fix), crowd_weight
   ≈ 0 in a quiet room.
4. Regression bars, all unchanged: solo phase stays solo (incl. loud
   animated solo — the sep-fix's own regression); silence-holds semantics
   (bucket freezes, staleness grows); an M4 part (d)-style music phase
   (playback on, no phantom growth, speech certified over music); pool
   recording replay — crowd blend still escalates ordinally; M6 part
   (c)/(d) numbers untouched (headcount-only diff, re-run to attribute).
5. No in-session tuning; `rescue_margin`/ramp knots move only on gate
   numbers, ideally recalibrated offline from the session's recorded
   audio before any re-run.
