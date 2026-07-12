# Field notes — live sessions

Informal, non-gating observations from running RTR in real environments.
The gates live in the milestone test plans; this file records what the
tool did in the wild, what the logs captured, and which hypotheses that
raises. Newest session first.

## 2026-07-11 (night) — founder requirement: the M6 operating envelope, and the external-speaker direction

**Requirement (founder-stated):** music-aware emotion must work at
listening volumes well above the gate's ~33% baseline — target at least
~66% on the current setup. Stated durably, since volume sliders don't
transfer across devices: **the correction must hold while music
approaches voice level at the mic** (voices measure −25…−30 dBFS on
this hardware; 33% music reads −31…−33 — voice wins comfortably; the
only measured hard failure is ~90% / −22 dBFS, where nothing certifies
— the M5 limit cycle). The 33–90% band is unmapped. Expected
degradation order, to be verified by a volume-ladder measurement
(monotone over a pull-measured track at 33/50/66/80%, taps per step):
(1) stored pull signatures under-correct (measured at one volume;
dominance scaling compensates only if the pull-vs-dominance
relationship extrapolates — measure it), (2) strict-VAD certification
thins, (3) blindness. Likely design successor: volume/dominance-aware
pull correction — regress pull against dominance from the samples the
estimator already banks, instead of a flat per-track mean. Pairs
naturally with the cold-start seam (entry below) as M8 material.

**External-speaker direction (same conversation):** the current
geometry is worst-case by construction — the MacBook's speakers sit
inches from its own mic, so music is heard near-field and voices
room-attenuated. Playing via a Spotify Connect device across the room
(the Echo Show is on the account's device list) inverts the geometry:
voice near-field, music room-shaped. Plausibly worth more than any
software fix, matches the real deployment (nobody parties off laptop
speakers), and is a pure config change (`RTR_PLAYBACK_DEVICE_NAME`) —
the M4 playback path is device-agnostic. Caveats when tried: pull
signatures are implicitly per-acoustic-setup (they re-converge as refs
accumulate, but first-session corrections will be approximate), and
the volume ladder should be re-run in the new geometry.

## 2026-07-11 (late evening) — post-merge demo: the cold-start seam, field-observed

Founder demo after the merge, continuous high-energy playlist, inert
mapping. What worked and what didn't, in one sequence (three banked
frames confirm it): monotone over a **pull-measured** track read flat at
dominance 1.0 (21:06:30, basis `pull`); deliberate animation read
excited (21:06:58); then the **next** track arrived with `basis: None`
(21:07:15) — no pull signature, not even the cold-start prior engaged
yet — and the monotone could no longer bring the reading back down
until a crossfade gap let the voice read true (dominance → 0) and the
EMA walked home to the flat quadrant.

Root cause, by design: pull signatures are per-track, and building one
needs a clean-speech baseline no older than
`RTR_MUSIC_BASELINE_MAX_AGE_S` (300 s). Under continuously crossfading
playback there is never a no-music speech window, so the baseline ages
out and every unmeasured track stays at cold start indefinitely — 4 of
52 known tracks have pull refs after the gate day. **M6's residual
limitation: correction quality is gated on per-track measurement
opportunities that continuous playback structurally denies.**

Mitigation candidates for a future milestone (not tuned in-session):
opportunistic baseline harvesting during low-dominance windows — the
crossfade gaps the founder watched read correctly are exactly the
moments to bank near-clean speech samples; a genre-level or global mean
pull as the cold-start prior instead of none; per-track signatures do
accumulate across sessions, so a stable playlist rotation self-heals
over time — new music will always cold-start.

**Context.** The PC's iteration (`0e83099`) replaced the failed
standalone-signature estimator with a **pull estimator**: measure the
speech-over-music interaction directly, as monotone-speech readings over
the track against a fresh no-music baseline, per track. Re-run per the
revised part (c) protocol (same room, same track `…5ynNMdW7`, same
volume, inert mapping): (b) 277 tests green, then phases 20:42–20:58.

**The pull warm-up measured what the record's own signature couldn't:**
pull V **0.51** / A **0.38** from 22 samples, vs the standalone
signature's V 0.08 / A 0.32 — the 6× valence interaction from the
afternoon's failure analysis, now captured by the estimator itself.

| run | ΔV | ΔA | phase-2 mood |
|---|---|---|---|
| 2026-07-11 baseline (no fix) | +0.26 | +0.39 | chill, every tap |
| standalone estimator (failed gate) | +0.33 | +0.27 | chill, every tap |
| **pull estimator (this run)** | **−0.06** | **+0.07** | **flat, every tap** |

**Part (c): PASS.** Both axes under 0.2 with margin (P1 mean
V −0.078 / A −0.423 over 7 taps; P2 mean V −0.140 / A −0.353 over 8,
basis `pull` on every frame, corrections tracking dominance 0.37–0.94).
The monotone finally reads as a monotone with the record playing.
Valence lands slightly negative — mild over-correction, well inside
target; noted for β fine-tuning if it ever drifts further.

**Part (d): PASS, redone properly.** First attempt banked one tap (the
founder was mid-story); redone 20:55–20:58 with 11: mean V +0.109 /
A **+0.533**, moods excited/tense, confidence 0.84–0.99, **arousal
separation from monotone-over-music +0.89** — all with pull corrections
actively applied (cV up to 0.47 at dominance 0.83). Shift-not-mute
holds with the strongest margin yet measured.

**Gate verdict: M6 PASSES** — (a)/(e)/(f) from the afternoon run stand
as regression bars (bench row in README; anchor persistence; 30-min
sweep), (b) and (c) re-ran green post-iteration, (d) re-verified the
trade-off under the new estimator. The emotion layer hears the room
through the record: the DJ's feedback loop no longer flatters itself.

## 2026-07-11 (afternoon) — M6 gate: five parts pass, part (c) fails honestly

**Gate context** (`docs/M6-TEST-PLAN.md`, Mac, quiet apartment): (a)
bench p95 1.04 s / 1.09 s — reference taps didn't move the contended
profile; (b) 270 tests green; (d) positive control PASS; (e) anchor
persistence PASS; (f) 30-min live sweep PASS. **(c) — the milestone's
reason to exist — FAILED: ΔV +0.325 / ΔA +0.274 against a < 0.2 target
(baseline +0.26 / +0.39).** Per protocol, no in-session tuning; the
numbers below are the calibration record the PC asked for.

**Part (c) detail.** Warm-up fingerprinted the phase track
(`…5ynNMdW7`) at V +0.15 / A +0.36 from 44 reference taps (redone once:
the AC ran during the first attempt's opening minute — signature wiped,
room corrected, clean re-run). Flat-reading phases 15:46:22–15:49:41
(no music, mean V −0.125 / A −0.606 — replicates yesterday's baseline
almost exactly) and 15:49:55–15:53:05 (same track, same delivery, mean
V +0.200 / A −0.332). The machinery all worked as designed: the
"hearing through music" chip showed, dominance tracked voice-vs-music
competition faithfully (0.50→1.0 as the monotone lost to the record;
0.17–0.33 once animated speech won), corrections scaled with it
(corrA up to 0.37 ≈ the full signature at dominance 1).

**Why it still failed — the estimator, not the knob.** The correction
subtracts the record's *standalone* signature scaled by dominance. But
the measured valence push on mixed speech (+0.33) is ~4× the record's
own valence signature (+0.09…0.15), while the arousal push (~0.5 raw)
is ~1.5× its signature. Cancelling arousal needs β≈1.5–2; cancelling
valence needs β≈4, which would over-correct arousal into the floor. **A
single scalar β on the standalone signature cannot satisfy both axes:
the model's valence read of speech-over-music is super-additive — it
hears flat speech + mildly-positive music as "chill" beyond what the
music alone carries.** Candidate directions for the PC: per-axis β
(cheap, calibratable from this session's numbers); or estimate the
pull from mixed windows rather than music-only windows (the reference
architecture already exists; a "contaminated-speech tap" during the
warm-up would measure the interaction directly).

**Part (d) — the fix doesn't mute the room.** Same track still playing,
genuinely animated talking: mean V +0.043 / A +0.352, moods
excited/tense, arousal separation from the monotone-over-music phase
**+0.68**, confidence 0.91–0.97. Shift-not-mute holds live; whatever
part (c)'s next iteration does, part (d)'s bar is set.

**Part (e) — anchor persistence, decisive evidence.** Quiet re-seed
saved `advisory_anchor.json` at −45.3 dBFS (a mid-seed notification
chime didn't distort it — 60 s EMA re-converged). Loud-first
mid-playback start: banner rose ~10 hops in while the live floor sat at
−19.9 against −21 loudness — a zero-to-negative live gap; only the
restored anchor (24 dB gap) could have judged it. Anchor deleted:
same session shape stayed dark for 45 s (fallback as documented).

**Part (f) — 30-min normal-evening sweep (16:05–16:35), all green:**
15 tracks fingerprinted (refs 1–77, signatures plausibly ranked from
A +0.62 bangers to A −0.52 ballads); corrected readings fed non-guard
recommendation cells; 6 played_throughs all occupied (5 fresh, 1
tap-rescued in a quiet stretch — sensible); zero advisory frames at
normal volume; 22 controller selections; report reads the full corpus
back cleanly (160 annotations / 81 overrides, presence gate math
coherent).

Protocol notes: the AC-contamination restart above is the third
time-of-day hazard this room has taught us (AC onset 07-06, AC
steady-state 07-10, AC-in-warm-up today); the test plan's checkpoint
text mentions a "reference tap" log line that doesn't exist — the
signatures file is the real evidence (doc nit for the PC).

**Gate verdict: NOT passed — (c) is the milestone.** Branch stays
unmerged; the calibration record above goes back to the PC for the β/
estimator iteration, then (c) re-runs on the Mac. Parts (d), (e), (f)
established regression bars the iteration must not break.

## 2026-07-11 — M5 gate day (apartment, afternoon): part (f) flat-affect measurement + the part (e) advisory bug

**Gate context.** M5 gated per `docs/M5-TEST-PLAN.md` on the Mac: parts
(a)–(d) passed as written (bench p95 1.02 s/1.07 s; 243 tests; the retro
filter flagged exactly the six named empty-room lines and spared the four
known-real completions, corpus byte-identical; live presence round-trip
produced fresh/absent/tap stamps in schema v2, honored un-recomputed by
the report). Two live findings worth the record:

**Part (e) found a real design bug in the new advisory.** As shipped, the
advisory compared playback loudness against the live rolling noise floor
— but that floor deliberately absorbs sustained sound including our own
playback (M3 semantics: "fan/HVAC/music"), so the gap self-erases within
one EMA tau. Observed live: lofi at 90% output read −28 dBFS against a
music-contaminated floor of −36 (8 dB < the 10 dB threshold, banner never
rose), with the floor visibly chasing the ramp in the frame log; replayed
against the 07-10 limit cycle, the banner would have blanked ~60 s into
the 4-minute incident the feature exists to catch. Fixed on the branch
(`308f9e9`): the advisory now anchors on the floor from playback-inactive
frames. Re-gated live: banner rose at ~13 dB over anchor while the live
floor sat 10 dB closer, held through the chase, cleared within a few hops
of certified speech at 60% volume. Part (e) passed post-fix; three new
tests encode the failure signature.

**Part (f): vocal music drags certified-speech emotion, decisively.**
Protocol: one known solo occupant, quiet room, same neutral text read in
a deliberately flat monotone for two 3-minute phases — phase 1 no music
(14:42:48–14:46:01), phase 2 over RTR · Pop · high at normal volume
(music mic-side ≈ −31…−33 dBFS; 14:46:20–14:49:16). Inert playlist
mapping so the DJ couldn't interfere (it bootstrapped a track off the
monotone reading on the first attempt — aborted, restarted defanged).
Speech stayed certified throughout both phases (speech_ratio 0.68–0.88).

| phase | taps | mean valence | mean arousal | mood on every tap |
|---|---|---|---|---|
| 1. flat reading, no music | 6 | **−0.135** | **−0.541** | flat |
| 2. same reading, vocal pop | 5 | **+0.129** | **−0.150** | chill |

Identical delivery, identical text: **ΔV +0.26, ΔA +0.39, and the mood
quadrant flipped on every single tap** (flat → chill, both axes pulled
toward the songs' excited quadrant). Emotion confidence stayed high
(0.6–0.97), so the contamination arrives with conviction, not hedged.

**Verdict:** the M5 proposal set ≥ 0.2 pull toward the song's quadrant
during certified speech as the threshold that reopens the ML
music-detection build decision. Both axes clear it — valence by 0.06,
arousal by 0.19 — at normal listening volume, in the easiest possible
room. The deferral does not survive its own test: music-aware emotion
(source separation, lyric/vocal discounting, or an ML music gate) should
be scoped as a first-class M6 candidate. Until then, mood readings while
vocal music plays should be treated as blended room+song signal — the
07-06 informal observation is now a measured effect.

## 2026-07-10 — Trio free-form DJ evening: indoor, porch, silent room (non-gating)

**Setup.** Followed the part (d) gate the same evening. One dashboard run
with the real playlist mapping (11 cells), founder + 2 friends, laptop
carried between rooms. Segments (wall-clock): **indoor trio DJ**
20:16:49–20:45:53; **porch 1 (trio, outdoor)** 20:55:54–21:17:16;
**silent living room (laptop alone, music playing)** 21:17:16–21:34:47;
**porch 2 (trio, outdoor)** 21:34:47–21:48:20; **full volume (output
93%) indoor** 21:48:20–22:08:12, **then outdoor** 22:08:12–22:27:23;
**empty room, AC on, charging** 22:27:23–23:02:07; **trio goodbye**
23:02:07–~23:10; **solo close-out (calming classical)** to shutdown
~23:14. First-ever data for: three real voices, outdoor acoustics, and
full-volume playback. 83 annotation frames and 41 override-log lines on
the day (both files also carry the part (d) gate phases).

**Observations.**

1. **Trio undercount is systematic — the first real multi-voice test of
   the 0.70 threshold.** Across indoor and both porch segments, the
   bucket read solo or pair for essentially the entire trio
   conversation (raw_clusters 1–4, crowd_weight ≈ 0); it touched 4 only
   twice, briefly, in porch 2. The founder deliberately tapped **Wrong
   call** on undercount frames (e.g. 21:10:28 `solo` during three-way
   porch chat) — first session where wrong-verdicts mark headcount
   ground truth. Consistent with the known similar-voices-merge
   trade-off of the 0.70 threshold plus intermittent per-speaker
   airtime; the opposite failure direction from the crowd-path
   overcounts (pool `16`, tonight's phase-2b `8`). The estimator
   currently has no stable middle: animated pairs can overcount, real
   trios undercount.
2. **Outdoor acoustics are friendly, not hostile.** Porch speech
   certified at speech_ratio up to 0.92 at −25…−30 dBFS — full VAD
   reach, no fan-masking, none of the pool session's reverb pathology.
   Open air (no reflections, low noise floor) looks like an easier
   regime than either the pool or a music-filled room. Mood tracked
   plausibly throughout: excited porch chatter early, flat/chill as the
   evening wound down, honest `None` in silence.
3. **`played_through` weak positives are banked by empty rooms.** Music
   kept playing while everyone sat outside (by design — silence is
   absence of evidence), and tracks that completed logged
   played_through "weak positive" lines with nobody present to veto:
   Thriller at 20:44:53, Just the Way You Are + Just In Time in the
   silent-living-room segment. **An empty room can't veto, so these are
   mislabeled approvals.** Before M5 learns from the override corpus,
   played_through lines need a presence gate (e.g. require speech
   evidence / low headcount staleness within the track's window) — or
   the corpus rewards whatever plays to an empty room.
4. **DJ mechanics round-tripped under real group use:** 6 manual picks
   honored (including genre pivots Hip-Hop→Jazz→Lofi as the group
   mellowed), 3 wrong_vibe vetoes each followed by a different-pool
   pick, boundary-only transitions, played_through only on real
   completions while occupied. The `guard/uncertain-regime` cell
   appeared at low headcount confidence — guards holding rather than
   guessing.

5. **Full volume revealed the gate's over-suppression limit — and a
   self-correcting cascade.** At 93% output the music read −22 dBFS at
   the mic, *louder than the participants' voices* (−25…−30 all night),
   and `speech_ratio` pinned to 0: the strict `vad_playback_threshold`
   that rejected sung vocals in part (d) phase 5 now rejected everyone.
   The cascade, all from the logs: blind system → guard recommendations
   → nothing selected/queued (last selection 22:06:07) → track completed
   into silence at 22:12:00 → **silence flipped `playback_active` off,
   the VAD reverted to its normal threshold, speech certified within
   ~30 s** (sr 0.33 by 22:12:28) → real recommendation fired →
   controller bootstrapped 'April Showers' at 22:13:13. No crash, no
   intervention — but at volumes where music out-shouts the room the
   system is a limit cycle (music blinds → starves → silence heals →
   repeats), not a DJ. Founder banked Wrong-call taps during the blind
   window (22:11:46, 22:12:01: stale 200+ s, sr 0, −22 dBFS) and Good
   taps on recovery — a clean before/after pair. **Operating envelope
   finding: voices must out-read music at the mic; ~60–70% output on
   this hardware.** After recovery, speech certified at 0.6–0.8 over
   playback for the rest of the segment.
6. **AC-on empty room: no phantom growth.** The deliberate AC phase the
   2026-07-06 notes asked for, opportunistically: 35 min of AC broadband
   + playback, nobody home. The one banked frame (22:59:06) shows the
   bucket holding stale (pair, 84 s), raw_clusters frozen, sr 0 — the
   contamination gate held against AC + music together. The 07-06 blip
   suspect (AC *onset* during active estimation) remains untested; this
   covers steady-state AC under playback.
7. **Participant-validated emotion accuracy.** All three participants —
   the first group to watch it live, and the first informal validation
   not done by the founder alone — reported being genuinely impressed by
   how accurately the valence/arousal readings tracked the room across
   the evening. The frame record agrees: excited through the animated
   porch conversation, chill/flat as the night wound down, a tense blip
   or two, and honest `None` whenever the room went quiet. Still
   informal (no per-frame verdicts on mood specifically, and the earlier
   phases-4/6 excitement confound stands), but the strongest
   multi-person endorsement of the emotion layer to date.
8. **The goodbye produced the night's most accurate group read.** Three
   people talking animatedly at 23:02 finally drove the bucket to 4 —
   the nearest bucket to a true 3 after an evening of solo/pair
   undercounts (suggesting simultaneous/overlapping speech, not just
   more speech, is what separates the clusters). When the friends left
   and calming classical took over, the gate certified nothing and the
   bucket froze at 4 with growing staleness — observed live by the
   founder as "still says 4 even though it's just me": correct hold
   semantics, informally the bookend of phase 1's held `pair`.

Session ended ~23:14 (dashboard stopped; Spotify kept playing the
close-out classical — the provider owning playback across shutdown, as
designed). `tuning_report.py` read the full corpus back cleanly: 113
annotations (104 good / 9 wrong) and 60 override records (4 skip,
10 wrong_vibe, 16 manual, 30 played_through) across all sessions, no
boundary adjustments suggested. Segment detail lives in
`data/annotations/2026-07-10.jsonl` and `data/overrides/2026-07-10.jsonl`
(timeline timestamps above segment the files cleanly).

## 2026-07-10 — M4 gate part (d) phases 2–6: contamination protocol (apartment, evening)

**Setup.** Two known participants (founder + 1 friend) for phases 2–3;
**correction, learned post-session: a third participant was present from
phase 4 onward** (arrival not caught on the phase clock; speech
participation in phases 4/6 unconfirmed — ground truth for those phases
is 2–3 speakers, not a clean pair). Same closed quiet apartment room as
phase 1, AC/fans off, mic input 34%. One continuous
dashboard run (started 19:40) with playback **enabled** but pointed at an
empty playlist mapping via `RTR_PLAYBACK_PLAYLISTS_PATH` — the controller
polls Spotify honestly (so `playback_active` tags evidence and the
stricter `vad_playback_threshold` engages) but every genre pool is
unmapped, so the DJ can never start or swap a track ("silence beats wrong
guess" doing measurement duty). Phase music was started by hand in
Spotify: instrumental = RTR · Lofi Beats · low, vocal = RTR · Pop · high,
volume set once at phase 3 and untouched through phase 6 (mic-side it
read −35…−45 dBFS — results are conditional on this moderate volume).
2-min quiet re-seed 19:41:57–19:43:57 preceded phase 2 (noise floor is a
60 s in-memory EMA; phase 1 ran in an earlier process — see entry below).
24 frames banked via taps.

Phase windows: 2 = 19:44:09–19:49:35 (solo half → both from 19:46:51);
3 = 19:49:35–19:55:00; 4 = 19:55:00–20:00:09; 5 = 20:00:09–20:05:33;
6 = 20:05:33–20:10:33.

Representative frames (full set in `data/annotations/2026-07-10.jsonl`):

| phase | frames | bucket range | raw | crowd_wt | frag | speech_ratio | dBFS | playback_active |
|---|---|---|---|---|---|---|---|---|
| 2a solo speech | 2 | solo→pair | 1 | 0.15–0.17 | 0–0.11 | 0.83–0.85 | −27 | false |
| 2b pair speech | 2 | pair→**8** | 3 | 0.26–0.27 | 0.23–0.32 | 0.95 | −25…−30 | false |
| 3 instr, silent | 6 | held 4 (stale 40→298 s) | frozen | 0 | frozen | **0.000–0.002** | −38…−45 | **true** |
| 4 instr + speech | 6 | solo/pair (one 4-blip) | 1–2 | 0–0.28 | 0.59–0.89 | 0.69–0.92 | −26…−35 | **true** |
| 5 vocal, silent | 4 | held pair (stale 30→298 s) | frozen | 0 | frozen | **0.000–0.003** | −36…−44 | **true** |
| 6 vocal + speech | 4 | solo/pair | 1–2 | 0–0.23 | 0.64–1.0 | 0.74–0.91 | −25…−31 | **true** |

**Findings.**

1. **The v1 contamination gate passed its hardest test.** Phase 5 — vocal
   music (Pop with sung vocals), silent room — certified essentially zero
   speech for 5 straight minutes (`speech_ratio` ≤ 0.003), so the
   headcount received no submissions (honest staleness growth to 298 s),
   the bucket held, and the mood went stale rather than absorbing the
   song. Zero phantom clusters, zero bucket creep. Per protocol, the
   `RTR_VAD_PLAYBACK_THRESHOLD=0.85` repeat was **not** run — no phantom
   growth to knock down. Same result in phase 3 (instrumental).
2. **Speech stayed certified under both music types.** Phases 4/6:
   speech_ratio 0.69–0.92 while music played, no phantom growth, and
   phase-6-vs-4 drift ≈ none (vocal penalty on headcount: not observed
   at this volume). `playback_active: true` on every music-phase frame.
   Caveat per the setup correction: true occupancy in 4/6 was 3, so if
   the third participant was talking, the solo/pair buckets are an
   **undercount** (consistent with the known similar-voices-merge
   trade-off at threshold 0.70) rather than a clean pair match — the
   no-phantom-growth conclusion stands either way, the accuracy claim
   doesn't.
3. **The night's real overcount came from the clean baseline.** Phase 2b —
   two animated people, NO music — hit bucket 8 (raw_clusters 3,
   crowd_weight 0.27, speech_ratio 0.95): the crowd/babble path engaging
   on excited pair conversation in a quiet room, a small-magnitude cousin
   of the pool session's pair→16. In a quiet apartment at moderate music
   volume, **the headcount's enemy is animated conversation, not
   playback**. The known crowd-regime misfire (separation_score=0 →
   sep_collapse=1) remains the suspect.
4. **Fragmentation runs hot under music + speech** (0.59–1.0 in phases
   4/6 vs ≤0.32 without music) — the proportional min-mass floor
   (`min_cluster_evidence_frac`) is what kept that debris from counting.
   Worth watching at higher music volumes.
5. **Mood contamination went unquantified tonight** — phases 4/6 read
   `excited` over genuinely excited conversation, so the song's emotion
   and the room's were confounded. The 2026-07-06 observation
   (deliberately mellow human read excited under hip-hop) remains the
   datapoint; a deliberately-flat-affect phase over vocal music is the
   missing measurement.
6. Solo speech (2a) blipped solo→pair (crowd_weight ~0.16), consistent
   with known solo behavior; mood tracked excited with one tense blip.

**M4 part (d): complete.** Measure-first baseline recorded; with (a)–(c)
green on 2026-07-06, the M4 gate is fully passed. Baseline numbers for
the M5 music-detection decision: at quiet-apartment volumes the VAD-side
gate already rejects vocals outright; M5's case must rest on louder
playback, valence contamination during speech, or harder rooms.

## 2026-07-10 — M4 gate part (d) phase 1: baseline quiet (apartment, evening)

**Setup.** Solo occupant (phases 2–6 with known participants run later
tonight), closed quiet apartment room, AC/fans off, mic input volume 34%,
dashboard fresh-started 19:25:23 with `RTR_PLAYBACK_ENABLED=0` for this
run — deliberate, so a bootstrap recommendation could not start music
during the no-playback phase (startup line confirmed shadow mode). Phase
window **19:27:23–19:32:23**; four frames banked via Good-call taps.

Protocol deviation, noted: phase 1 ran standalone ahead of phases 2–6
(separate engine run). The noise floor is an in-memory 60 s EMA
(`engine.py`, `Ema(noise_floor_tau_s)`) fed by quiescent windows, so it
does not persist across restarts — tonight's session will prepend ~2 min
of quiet before phase 2 to re-seed. Phase 1's own observations (hold /
staleness / no phantom growth) are restart-independent.

| time | bucket | stale (s) | raw_clusters | crowd_weight | speech_ratio | dBFS | playback_active | matched_cell |
|---|---|---|---|---|---|---|---|---|
| 19:28:56 | pair | 105.5 | 4 | 0 | 0 | −45.7 | false | guard/no-speech |
| 19:30:35 | pair | 205.5 | 4 | 0 | 0 | −44.3 | false | guard/no-speech |
| 19:31:36 | pair | 265.5 | 4 | 0 | 0 | −47.7 | false | guard/no-speech |
| 19:32:11 | pair | 299.5 | 4 | 0 | 0 | −54.4 | false | guard/no-speech |

**Result: expected phase 1 behavior on every axis.**

- **Bucket holds, staleness honest.** No headcount submissions in
  silence; staleness grew linearly 105→299 s while the bucket held.
  The last-window diagnostics (`raw_clusters` 4, `dispersion` 0.579,
  `fragmentation` 0.429, `smoothed_log2` 1.672) were frozen across all
  four frames — residue of the final pre-phase submission (~19:27:11,
  incidental pre-"go" audio), not live estimates. The held `pair` on a
  solo-occupant silent room is the designed "silence is absence of
  evidence" semantics, not an error.
- **VAD certified zero speech** (`speech_ratio` 0 on every frame) and the
  recommender stayed on the no-speech guard throughout — no
  recommendation fired in 5 min of silence.
- **Quiescent floor is low**: −44…−54 dBFS, far below the pool session's
  fan-inflated −10…−20 and below the prior quiet-room −28 reference.
  With `raw_ratio < 0.1` the entire phase, the 60 s-tau floor EMA was
  fully seeded well before phase end.
- **Debuggability gap (small, same family as the fixed `smoothed_log2`
  one):** the seeded `noise_floor_dbfs` isn't exposed in state or
  annotation frames, so "noise floor seeds" is inferred from quiescent
  loudness rather than read directly. Worth exposing before/during M5,
  since part (d)'s phases 3–6 lean on floor-relative terms.

## 2026-07-06 — M4 gate part (c): first real playback (apartment, evening)

Two live sessions on the Mac, ~30 min (21:07–21:37) + ~17 min re-check
(21:40–21:57), solo speaker, Spotify playing through the MacBook speakers.
All override types round-tripped, degrade/recover on quitting Spotify
worked (engine never gapped), and `tuning_report.py` read all 13 override
records back. Two real bugs found, fixed between the sessions
(commit `8438264`):

1. **Stale-queue pile-up.** The controller pushed every selection to
   Spotify's queue assuming replacement, but the queue is **append-only**
   (no replace/remove). With recommendations firing at the 30 s dwell
   floor, 12 selections queued in 8 minutes; boundaries played the OLDEST
   while the "next" label showed the newest (observed live: bar promised
   Classical, boundary delivered the jazz queued 13 minutes earlier). The
   FakeProvider in the test suite modeled the wished-for replace
   semantics, so 185 green tests had validated fiction — the fake now
   appends, and the controller holds selections locally (latest-wins) and
   pushes exactly one track inside the final `RTR_PLAYBACK_QUEUE_LEAD_S`
   (15 s) of the playing track. Re-check confirmed: 5–6 selections
   superseded each other per track, one push per boundary, label and
   boundary agreed.
2. **False played_through on provider death.** Quitting Spotify mid-track
   (the degrade test) logged a played_through weak positive for a track
   last seen at 90 s of 246 — "vanished" counted as "ended". Completion
   now requires the last observation inside the boundary window of the
   track's own end. Re-check: all four played_through lines show real
   completions (67/68, 298/302, 255/259, 219/219 s).

Sensing observations under real playback (expected, now seen live):

- **Vocal-music mood contamination is real and visible.** Hip-hop/pop
  playback pushed valence/arousal into the excited quadrant even while
  the human was deliberately mellow. This is the known v1 gap — the
  certification gate can reject non-speech, but sung vocals that pass VAD
  carry the song's emotion, not the room's. Part (d) phases 5/6 will
  quantify it; feeds the M5 music-detection decision.
- **Headcount blipped to 4** once, coinciding with the air conditioner
  starting plus laptop/body movement — consistent with the pool session's
  finding that broadband noise onset perturbs the estimator. One blip in
  ~50 min with music playing is much better than the pool baseline;
  worth a deliberate AC-on phase in a future controlled session.

Session hygiene notes: hard-refresh the dashboard after every milestone
(the browser cached the M3 page and hid the M4 UI — consider a no-cache
header); clear the Spotify queue before a session that follows a
pre-fix run.

## 2026-07-06 — UofA pool (large reverberant space, loud ventilation fan)

**Setup.** ~7 people around the laptop talking intermittently at varying
distances. Two dominant environmental factors, both new relative to all
prior (apartment) testing:

- a loud ventilation fan close enough that the mic picked it up strongly;
- a very large, hard-surfaced space — open-space acoustics, much closer to
  the large-club / dancefloor regime we eventually want to target than to
  a living room.

Audio recordings of the mic input exist outside the repo:
`UofA Pool RTR Test M3 copy.m4a` (3:22) and
`Me and Claire super excited RTR Test M3.m4a` (0:40).

### Test 1 — group of 7, intermittent chatter (`UofA Pool RTR Test M3 copy.m4a`)

- **Mood: good.** Valence/arousal sat in the *excited* quadrant, which
  matched the room.
- **Headcount: poor.** Bucket ranged `solo`..`4` against an actual ~7.
- **Speech pickup: poor at distance.** The dashboard voice-input bar
  stayed low unless the speaker was within ~3 ft of the laptop. In the
  apartment the same laptop hears speech well from ~15 ft, so the fan
  (masking/raised noise floor) and the room's reverberation are the prime
  suspects, not the mic. Undercounting follows directly: speakers the VAD
  can't hear contribute no embeddings to cluster.

### Test 2 — solo → Claire joins (`Me and Claire super excited RTR Test M3.m4a`)

Solo speech to RTR, then Claire sat down next to the laptop and we had an
animated back-and-forth about the tool.

- **Headcount tracked the transition correctly**: `solo` → `pair`. Both of
  us were within a few feet of the mic, which likely overcame the fan.
- **Mood read strongly excited — accurate** (mutual enthusiasm, genuinely
  a high point).
- **Anomaly:** at the very end the bucket jumped `pair` → `16` with no
  change in the room. See analysis below.

### What the annotation log shows (`data/annotations/2026-07-06.jsonl`)

| time | bucket | raw_clusters | crowd_weight | speech_ratio | dBFS | mood |
|---|---|---|---|---|---|---|
| 11:44:10 | solo | 1 | 0.099 | 0.60 | −9.9 | excited |
| 11:51:32 | 4 | 5 | 0.134 | 0.79 | −13.8 | excited |
| 11:52:18 | 4 | 2 | 0 | 0.04 | −20.2 | excited |
| 11:55:01 | pair | 1 | 0 | 0.21 | −18.9 | excited |
| 11:59:16 | pair | 1 | 0.215 | 0.85 | −12.6 | excited |
| 11:59:27 | pair | 1 | 0.074 | 0.85 | −9.7 | excited |
| 11:59:57 | **16** | 1 | 0 | 0.77 | −12.7 | excited |
| 13:53:45 | solo | 3 | 0.026 | 0.51 | −28.2 | flat |

Note the loudness floor: the pool session sits around −10..−20 dBFS even
during sparse speech (the fan), where the later quiet-room entry reads
−28 dBFS.

### Analysis of the `pair` → `16` jump

The `16` snapshot looks self-contradictory — `raw_clusters: 1`,
`crowd_weight: 0` — but it isn't a logging error. The published bucket is
`BucketSmoother` output (EMA in log2 space, `tau_s=20`, plus 3-update
hysteresis), while `raw_clusters`/`crowd_weight` in RoomState reflect only
the **latest** window. For the smoothed value to round to 16 and survive
hysteresis, several consecutive windows in the ~30 s before 11:59:57 must
have produced high-log2 estimates via the crowd/babble path
(`headcount.py`, `crowd_weight` blend), then subsided by the time the
snapshot was taken.

The babble path fires on exactly this session's signature: `saturation`
needs speech_ratio ≳ 0.6 (we were at 0.77–0.85) AND loudness ≳ −45..−20
dBFS (fan-inflated −9..−13), times `smear` — high within-cluster embedding
dispersion, which reverberant open-space acoustics plausibly produce even
for two speakers. When all three line up, `log2_babble` (8–1024 range)
leaks into the estimate and the EMA climbs. So the jump is most likely a
**babble-regime false positive driven by fan loudness + reverb smear**,
not a clustering bug. Two people talking excitedly near the mic in a loud
reverberant room matched the "packed room" signature.

### Takeaways / open questions

1. **Strong broadband background noise is a real hurdle** — it masks
   distant speech (kills VAD reach, starves the headcount of evidence)
   and inflates the loudness term of the babble heuristic (occasional
   massive overcounts). Both failure directions in one session.
2. **Open-space acoustics matter** and are on the roadmap (club/dancefloor
   analysis), so this isn't an out-of-scope environment — it's an early
   look at the target regime. Reverb likely smears ECAPA embeddings,
   raising `dispersion`; worth measuring directly on the pool recordings.
3. **Debuggability gap:** RoomState carries only the latest window's
   `raw_clusters`/`crowd_weight`, so a smoothed-bucket anomaly can't be
   attributed after the fact. Exposing `BucketSmoother.smoothed_log2`
   (and perhaps the last few raw estimates) in state/annotations would
   have made the `16` jump diagnosable from the log alone.
4. **Possible mitigations to explore** (not yet decided): noise-floor
   estimation so the `saturation`/babble loudness ramp keys on
   speech-band SNR rather than absolute dBFS; checking whether Silero VAD
   confidence degrades gracefully under fan noise or misclassifies it as
   speech.
5. **Next test should be the control:** a closed, quiet room with a known
   group size, to separate "M3 headcount limits" from "pool-specific
   noise/reverb effects".
