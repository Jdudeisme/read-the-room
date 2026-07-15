# M7 charter revision — after the 2026-07-15 two-person gate

**For the PC session.** The M7 gate caught a real design flaw, as every
gate has. This note records what it found, what shipped, and the
proposed re-wording of M7's mission. Nothing here is committed as
doctrine yet — it needs the founder's sign-off, because it narrows what
the milestone claims.

## What the gate found

`M7_PROMPT.md`'s mission set two mandates: fix the **trio undercount**
(the merge) and the **animated-pair overcount** (the crowd path). The
2026-07-15 gate (founder + 1 friend, phases 1/2/3/6; trio phases 4/5/7
still need a third person) resolved them unequally:

- **Overcount half — FIXED and validated live.** The sep_collapse fix
  and dispersion-ramp recalibration hold: `crowd_weight` stayed ≈ 0
  through loud, quiet, and super-animated speech. The bucket-8 driver
  that made M7 necessary did not reappear once.
- **Undercount half — the rescue is UNFIXABLE on this hardware.** The
  distinct-voice rescue counts a low-airtime cluster as a quiet extra
  person when its centroid sits ≥ `rescue_margin` from every counted
  cluster. That premise requires same-speaker and cross-speaker centroid
  distances to be separable. They are not, on a consumer mic with real
  conversational speech. Instrumented replay of the recorded gate audio
  (`m7-gate-2026-07-15.wav`, offline, real rescue path) measured a
  **single** speaker's scatter throwing off sub-cluster centroids across
  **0.80–1.00** — the same range distinct voices occupy. So no
  `rescue_margin` separates "one person's fragment" from "a third
  person," and live, a mere pair inflated to bucket 6 (rescued clusters
  tracked raw over-segmentation almost 1:1; a two-person buffer reached
  raw 8 / rescued 7). Neither raising the margin nor gating on loudness
  fixes it — the over-rescue occurred at −18 dBFS (loud) as well as
  −40 dBFS (quiet).

The deeper point: exact speaker *counting* from short-segment embedding
clustering on a single consumer mic is not reliable past ~2 people,
because ECAPA's same-speaker scatter overlaps its cross-speaker
distance. This is a property of the hardware+method, not a bug to tune
out. The system already encodes this in `count_reliable_max = 4` and its
deliberate handoff to the crowd/density path; the rescue was an
over-reach past that self-declared ceiling.

## What shipped (branch `milestone-7-stable-middle`)

- sep_collapse fix, dispersion ramp, bucket-ladder rungs 3 and 6 — kept.
- Distinct-voice rescue — kept in code, **default off**
  (`RTR_HEADCOUNT_RESCUE_ENABLED=0`). Its mechanics remain under test
  (rescue-enabled fixtures) for a future lower-scatter mic; the default
  decline is pinned by a regression test. With the rescue off, the
  recorded gate audio replays at solo/pair across the whole session.

## Proposed mission re-wording

Old (M7_PROMPT.md): *"M7 makes 2–4 real people in ordinary conversation
read as 2–4"* — fix both the undercount and the overcount.

Proposed: **"M7 makes the middle stable, not exact: 2–4 people in
ordinary conversation read as pair-to-small-group and never inflate into
a crowd. Exact resolution of a quiet third person is out of reach on a
consumer mic and is not attempted; the crowd/density path carries the
middle."**

Rationale: it is the strongest claim that is *true* on the hardware the
founder wants to use (laptop or phone mic, no external dependency). It
keeps the validated win (no crowd-path blowups) and drops the promise
the mic physically cannot keep. A trio reading "pair / small group" is
correct enough for the rulebook — the DJ needs the room's density, not a
census.

## Open decisions for the founder / PC

1. **Accept the re-wording?** If yes, it edits M7_PROMPT.md's Mission and
   the milestone's gate criteria (drop "trio publishes 3"; keep "never
   above 4 / never a crowd for ≤4 people").
2. **Re-word or replace the gate's part (c) pass condition** accordingly,
   then finish it with a real 3-person night — verifying the trio reads
   pair/small-group and does *not* inflate, rather than that it counts
   exactly 3.
3. **Where does exact small-N counting go, if anywhere?** Candidates are
   research-grade (proper diarization with temporal modeling, overlap
   detection, longer-context speaker models) or hardware (a lower-scatter
   mic re-enables the existing rescue). Either is a future milestone, not
   a tuning pass — and single-channel far-field counting stays hard even
   then. The phone-as-mic idea helps only via *placement* (mic in the
   middle of the group), which tightens scatter slightly but does not
   change the regime.
