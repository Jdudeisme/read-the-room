# M6 Prompt — Hear the room, not the record (founder direction, 2026-07-11)

Written on the Mac the afternoon M5 gated (merge `9723456`), for the
Claude Code session on the PC to turn into `docs/M6-PROPOSAL.md`, a
branch, and an implementation — the M5 pattern, which worked end to end:
prompt → proposal → build on the PC → gate live on the Mac (where part
(e) caught a real design bug; expect the same treatment). Before
proposing, read: the 2026-07-11 entry in `docs/FIELD-NOTES.md` (the part
(f) measurement IS this milestone's mandate), the M5-PROPOSAL's
music-detection decision section it overturns, and the M4-PROPOSAL
"Deferred" list.

## Mission

M5's part (f) settled a question we'd been deferring on vibes: **vocal
music drags certified-speech emotion by ΔV +0.26 / ΔA +0.39 — a mood-
quadrant flip on every tap — at normal listening volume, in a quiet
room, with high confidence.** The exact failure that matters most for a
DJ: the system reads the room happier and more energized *because of the
song it chose*, then recommends on that reading. A feedback loop that
flatters itself. M6 makes the emotion layer hear the room through the
music.

The number to beat is the number we measured: re-run the part (f)
protocol and the pull must come in **under 0.2 on both axes** — while
emotion still tracks *genuine* mood changes during playback (see the
gate's positive control; going deaf under music is not a fix, it blinds
the loop precisely when the DJ is doing its job).

## Design space (evaluate, pick, defend in the proposal)

In rough order of cost — the right answer may be a layered combination:

1. **Discount, don't clean:** while `playback_active`, widen emotion
   uncertainty / lower confidence / slow the V/A EMAs so the song's pull
   integrates weakly. Cheapest; caps how much a song can move the
   reading but doesn't remove the bias direction. Probably a component
   of any answer, not the whole answer.
2. **Vocal-music detector as an emotion gate:** a lightweight
   music/voice discriminator (spectral heuristics first — the M3
   spectral_balance fields already ride every frame — then a small ML
   model only if heuristics measurably fail) that scores each emotion
   window for "music-dominated vs speech-dominated" and gates/weights
   submissions accordingly. This is the reopened "ML music-detection
   gate," scoped to the emotion path where the damage is measured.
3. **Reference-signal knowledge:** we KNOW what's playing (track id,
   provider) and when. Short of echo cancellation, explore cheap
   track-aware corrections — e.g., estimate the playing track's own V/A
   signature (from audio features or a first-seconds calibration when
   nobody speaks) and subtract its expected pull. Novel, riskier;
   prototype only if 1+2 measure short.
4. **True echo cancellation / source separation:** still deferred unless
   everything above fails the gate — separation-class models likely
   blow the 2019-Intel bench budget, and AEC needs a local provider that
   owns the output PCM (M4 deferral stands).

Hard constraints: the M2 bench gate is untouchable (`RTR_TORCH_THREADS=2`,
headcount p95 < 1.37 s, emotion overall p95 < 1.2 s absolute, on the
2019 Intel MacBook Pro — any new model runs inside that envelope or
doesn't ship); logic-level tests with no network/models in the suite;
every knob env-tunable with the default defended by a measurement.

## What to build the evaluation on

- The part (f) protocol is the benchmark: flat monotone reading, no
  music vs vocal pop, ~33% output (music mic-side −31…−33 dBFS),
  3-minute phases, taps bank the frames. It's cheap, repeatable, and
  already has a baseline number attached.
- Add the **positive control** the first run lacked: a third phase of
  genuinely animated talking over the same music — post-fix, emotion
  must still move for the real mood change. Suppression that flattens
  both phases fails the gate even if the pull hits zero.
- The corpus (private repo `Jdudeisme/read-the-room-data`, sync per its
  README) now carries labeled frames from every session including the
  07-10 contamination phases and the 07-11 part (f) pair — use them to
  calibrate any detector offline before the live gate.
- Development on synthetic/recorded audio as always; the Mac runs the
  live gate.

## Also in scope (small, earned this week)

- **Advisory anchor persistence:** a session that starts mid-playback
  has no quiet anchor until the first playback-free stretch (fallback is
  step-detection only). Persist the last known anchor to `data/` and
  reload on start with a staleness bound — parties start with music
  already on; 2026-07-11's session did.

## Explicitly out of scope — don't let these creep in

- **The headcount "no stable middle" problem** (trio merge at 0.70 vs
  animated-pair crowd-path overcount, sep_collapse=1 misfire). Still
  real, still documented, still the presumptive M7 — a milestone that
  changes two estimators at once can't attribute its own gate failures.
- Learned tuning v2 — M5's proposals section keeps counting; the corpus
  isn't big enough to justify more sophistication yet.
- Auto-volume, crossfade, multi-zone, local audio provider (deferrals
  stand).

## Ground rules carried forward

- FakeProvider lesson: fakes model real semantics, not wished-for ones.
- Measure-first: no in-session tuning during the gate; knobs get changed
  by the numbers the gate produces.
- The Mac remains the validation machine. Write `docs/M6-TEST-PLAN.md`
  in the session-script style (Claude walks the founder through it,
  [HUMAN] steps marked). Plan for the DJ-bootstrap gotcha: any
  no-music or fixed-music phase runs with the inert playlist mapping
  (`RTR_PLAYBACK_PLAYLISTS_PATH` at an empty file) — it bit part (f)'s
  first attempt and M4 part (d) before it.
- Gate sketch: (a) bench unchanged; (b) pytest green; (c) part (f)
  re-run — pull < 0.2 both axes; (d) positive control — emotion tracks
  a genuine mood change under the same music; (e) 30-minute live DJ
  session — recommendations driven by decontaminated readings, no
  regressions in presence stamps or the advisory.

## Deliverables, in order

1. `docs/M6-PROPOSAL.md` (approach chosen from the design space, with
   the offline calibration evidence)
2. Branch `milestone-6-music-aware`
3. Implementation + tests
4. `docs/M6-TEST-PLAN.md`
