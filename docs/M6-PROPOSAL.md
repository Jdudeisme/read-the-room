# Milestone 6 Proposal — Hear the room, not the record (DRAFT)

M5's part (f) measured the failure M4 could only describe: vocal music at
normal listening volume drags certified-speech emotion by **ΔV +0.26 /
ΔA +0.39**, flipping the mood quadrant on every tap, with confidence
0.6–0.97. The DJ reads the room happier because of the song it chose,
then recommends on that reading. M6 makes the emotion layer hear the room
through the music: the part (f) re-run must come in **under 0.2 pull on
both axes** while emotion still tracks genuine mood changes during
playback — going deaf under music is not a fix.

Source of direction: `M6_PROMPT.md` (founder, 2026-07-11). Evidence base:
the 2026-07-11 FIELD-NOTES entry (the mandate), the 07-10 part (d)
phases, and fresh offline analysis of the corpus through 2026-07-11
(tables below).

## Offline calibration evidence (PC, 2026-07-11 corpus)

**1. The contamination is spectrally loud where it hurts.** Part (f)'s
frames (one voice, same monotone text, same room) on the
`spectral_balance.high` share that already rides every frame:

| frames | sb_high | emotion read |
|---|---|---|
| flat reading, no music (6 taps) | **0.014–0.031** | flat, V −0.13 / A −0.54 |
| same reading over vocal pop (5 taps) | **0.257–0.484** | chill, V +0.13 / A −0.15 |
| music-only windows (07-10/07-11, sr ≈ 0) | 0.20–0.77 | (VAD-gated, none) |

Zero overlap, ~10× separation: quiet-room speech has almost no high-band
share; pop production floods it. A music-dominance score from the
spectral profile is essentially free and needs no new model **at the
volume where harm is measured** (music mic-side −31…−33 dBFS).

**2. But spectral dominance is not sufficient alone.** At the 07-10
part (d) volume (−35…−45 dBFS) the same comparison overlaps: speech-only
frames reached sb_high 0.142 (animated pair) while vocal+speech frames
sat at 0.053–0.158. Two saving graces: that quieter regime showed ≈ no
measured drift in part (d), and `playback_active` already tells us *when*
to be suspicious — the discriminator only has to say *how much*, not
*whether*. Conclusion: use spectral dominance as a weighting input, and
put the bias removal somewhere principled.

**3. The bias term is directly measurable, per track, for free.** The
emotion model's response to music-only windows *is* the contamination we
want to remove — not the song's "true" V/A, but what *our model* reads
into this recording through this mic. Today those windows are discarded
(VAD-gated). During playback the DJ regularly plays to silence
(conversation lulls; entire 07-10 segments), so reference windows are
plentiful exactly when music is on.

## Design: subtract a measured per-track pull, weighted by dominance, with a discount floor

From the prompt's design space: **option 3 as the core, option 2's
heuristic (no ML) as the scaling term, option 1 as the fallback floor.**
Option 4 (separation/AEC) stays deferred — nothing below needs a new
model, so the bench envelope is untouched by construction.

### Layer 1 — reference taps: estimate the playing track's pull

While `playback_active` and the window is **not** speech-certified (the
same instantaneous gate the emotion layer already uses, inverted), the
engine submits the window to the existing emotion worker as a **reference
tap** — rate-limited by the existing `min_interval_s`, and tagged so a
real speech submission always supersedes a pending reference (latest-wins
slot, speech wins ties; reference inference can never delay a speech
reading). The resulting V/A accumulates into a per-`playback_track_id`
**signature** (EMA + sample count).

Signatures persist to `data/track_signatures.json` (schema-versioned,
env-relocatable): playlists repeat, so RTR hears through a song better
every time it plays it. Contention math: reference taps fire only in
windows where speech_ratio is low — precisely the windows where emotion
and headcount are otherwise idle — so the contended-hop profile the M2
bench gates is unchanged.

### Layer 2 — correction: subtract the signature, scaled by dominance

For speech-certified windows while that track plays, once its signature
has at least `min_refs` samples:

```
m         = ramp(sb_high share)          # 0 below LO, 1 above HI
corrected = clamp(raw − β · m · signature)
```

- `m` knots seeded from the tables above (`LO=0.05`, `HI=0.30`,
  env-tunable): a monotone voice with no music reads m ≈ 0 and the
  correction vanishes; part (f) phase-2 frames read m ≈ 0.8–1.0.
- `β` (default 1.0, env-tunable) is the additivity leap made explicit:
  we assume the model's response to speech+music ≈ response to speech +
  β·(response to music) at these SNRs. Part (f)'s re-run measures the
  truth; the gate's numbers move `β`, not vibes.
- The correction is a **shift, never a mute**: a genuine mood change
  moves the raw reading and the corrected one identically. The positive
  control passes by construction unless additivity itself fails — which
  the gate would then show honestly.
- Smoothing order: correct per-window readings *before* the V/A EMAs, so
  the published trail and targets are decontaminated everywhere at once.

### Layer 3 — discount floor: no signature yet

Fresh track and people talking non-stop (no reference windows yet): scale
`emotion_confidence` down by `1 − γ·m` (γ default 0.5, env-tunable) until
the signature exists. Confidence discount only in this state — a
permanent discount during playback would push the mapper toward its
guard and starve the DJ exactly when it's working. Optional
`RTR_EMOTION_GENERIC_SIGNATURE` fallback (the mean of cached signatures)
stays **off** by default: measured per-track beats assumed generic.

### Observability (the part-(d)/(e) lesson: make the gate diagnosable)

RoomState gains additive fields: `emotion_music_dominance` (m),
`emotion_correction` (`{valence, arousal, track_id, refs} | None` — the
subtracted amounts, present only when a correction applied). Raw vs
corrected is reconstructable from any frame; annotation taps inherit both
for free. The dashboard emotion card notes "hearing through music" when
a correction is live.

## Also in scope (small, from the prompt)

- **Advisory anchor persistence:** the quiet anchor (308f9e9) persists to
  `data/advisory_anchor.json` on update and reloads at start when younger
  than `RTR_PLAYBACK_ADVISORY_ANCHOR_MAX_AGE_S` (default 12 h — floors
  drift with AC/weather; a day-old anchor is a guess). Sessions that
  start mid-playback — every party — get a real anchor instead of
  step-detection fallback.

## Explicitly out of scope (per founder direction)

- The headcount "no stable middle" problem (presumptive M7; two
  estimators changing in one milestone can't attribute gate failures).
- Learned tuning v2; auto-volume, crossfade, multi-zone, local provider.
- An ML music classifier: the heuristic + subtraction path must be
  measured first; if the part (f) re-run lands ≥ 0.2 on either axis, the
  classifier is the *next* escalation (scoped, in the proposal's terms,
  as a replacement for `m` — the rest of the pipeline stands).

## Iteration after the 2026-07-11 gate (part (c) failed at ΔV +0.325 / ΔA +0.274)

The additivity assumption failed its measurement: the model's read of
speech-over-music is **super-additive** (valence pull ~4× the record's
standalone signature, arousal ~1.5× — FIELD-NOTES 2026-07-11 afternoon),
so no scalar β on Layer 1's signature cancels both axes. The estimator
changed; the architecture did not:

- **Pull signature (new primary).** The engine keeps a **clean-speech
  baseline** (EMA of readings with playback off or dominance ≈ 0, age-
  bounded). While it is fresh, each speech-over-music reading banks
  `(reading − baseline) / m` as a pull sample for the playing track —
  measuring the interaction itself, super-additivity included. The
  correction becomes `clamp(raw − β_axis · m · pull)`, per-axis βs
  defaulting to 1.0 (subtract what was measured), magnitude-capped.
- **Layer 1's standalone signature is demoted to cold-start prior**,
  scaled per axis by the gate-measured ratios (defaults 2.2 / 1.5) and
  capped, used only until pull samples exist; the discount floor
  remains beneath both.
- **Known trade-off, accepted:** a genuine mood change inside the
  baseline's freshness window banks mood-shifted pull samples. Guards:
  the age bound stops banking on stale baselines (part (d)'s exact
  state), the dominance floor sheds animated-speech windows (measured
  m 0.17–0.33 when speech wins), and the signature EMA washes
  transients. Part (d)'s +0.68 separation bar is the regression check.

Replaying the gate's calibration record through the new estimator
offline cancels both axes back to the baseline; the part (c) re-run on
the Mac is the live confirmation.

## Gate (sketch — full checklist in `docs/M6-TEST-PLAN.md`)

On the 2019 Intel MacBook Pro (`RTR_TORCH_THREADS=2`), inert playlist
mapping for every fixed-music phase (the DJ-bootstrap gotcha):

1. Bench regression: `bench_headcount.py --fallback` within the M2 gate.
2. `pytest` green (signature store, correction math, dominance ramp,
   reference-tap precedence, anchor persistence — all synthetic).
3. **Part (f) re-run:** flat monotone, no music vs vocal pop at the same
   ~33% output, after letting the track build a signature. Pass: pull
   **< 0.2 on both axes**; record β/m calibration numbers in
   FIELD-NOTES.
4. **Positive control:** third phase, genuinely animated talking over the
   same music — corrected emotion must leave the flat quadrant and track
   the change. Suppression that flattens this phase fails the gate.
5. 30-minute live DJ session: recommendations driven by corrected
   readings; presence stamps and the advisory unregressed; signatures
   visibly accumulating in `data/track_signatures.json`.
