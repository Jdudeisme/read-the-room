# M5 Prompt — Learn from the room (founder direction, 2026-07-11)

Written on the Mac the morning after the M4 gate closed (`ed84bed`), for
the Claude Code session on the PC to turn into `docs/M5-PROPOSAL.md`, a
branch, and an implementation — same pattern as M3/M4. Before proposing,
read: both 2026-07-10 entries in `docs/FIELD-NOTES.md` (the part (d)
gate and the trio evening — they ARE the M5 agenda), and the "Deferred"
sections of `docs/M3-PROPOSAL.md` and `docs/M4-PROPOSAL.md`.

Unlike M3_PROMPT.md (which only ever lived on the PC), this prompt is
committed — the founder decided milestone prompts belong in the repo.
The corpus it references does NOT travel with it; see "Data" below.

## Mission

M4 closed the loop mechanically: sense → recommend → play → observe →
log. M5 makes the loop *learn*. The feedback corpus now exists — 113
annotations (104 good / 9 wrong) and 60 override records (4 skip,
10 wrong_vibe, 16 manual, 30 played_through) through 2026-07-10 — and
both prior milestones explicitly deferred learning to this moment. Same
culture as part (d): measure first, count before you model, keep every
adjustment inspectable by a human.

## Scope, in order

### 1. `played_through` presence gate — prerequisite, do this first

Field notes 2026-07-10, finding 3: tracks that completed in empty rooms
logged played_through "weak positives" all evening (Thriller at
20:44:53; Just the Way You Are and Just In Time in the silent-living-room
segment; Mumbles, All The Things You Are during empty-room-AC; Atrebor
during/after the goodbye). An empty room can't veto. If M5 learns from
the corpus as-is, it rewards whatever plays to nobody.

- **Live gate:** a completion only logs played_through if the room was
  plausibly occupied during the track's play window. Derive the
  criterion from data, not intuition — the 07-10 corpus has known
  empty-room completions and the 07-06 part (c) session has four known
  real ones (67/68 s, 298/302, 255/259, 219/219). Candidate signals:
  certified speech evidence within the window, headcount staleness at
  completion. Stamp whatever evidence the gate uses into the override
  line (schema_version bump if the shape changes).
- **Retroactive filter:** `tuning_report.py` flags (never deletes)
  suspect played_through lines in the existing corpus using the same
  criterion.
- **Acceptance:** the known 07-10 empty-room lines get flagged; the four
  known-real part (c) completions survive.

### 2. Learned tuning v1 — proposals, not autopilot

- Input: the (gated) override corpus + annotations. Output: per-cell
  adjustments — selector weighting, boundary nudges — rendered as
  inspectable proposals extending `tuning_report.py`'s existing
  "suggested boundary adjustments" section. Applying a proposal remains
  a human act (`.env` / config edit), exactly as today.
- At n≈60, counting and rates are the right sophistication. No online
  learning, no model training, nothing that adjusts itself mid-session.
- Mind the confounds the field notes recorded: wrong_vibe taps during
  the full-volume window (21:48–22:27) are operating-envelope artifacts,
  not music-preference signal — frames carry loudness and playback
  state, so filter on them.

### 3. The music-detection decision — a memo with data, not reflex code

Part (d)'s baseline: at quiet-apartment volumes the VAD-side gate
rejects sung vocals outright (phase 5: speech_ratio ≤ 0.003 for five
minutes of Pop). So the ML music-detection gate M4 deferred needs a
case built on what's left:

- (a) **valence contamination during speech** — still unquantified; the
  07-06 observation (deliberately mellow human read excited under
  hip-hop) is the only datapoint. Specify the missing measurement (a
  flat-affect-over-vocals protocol the Mac can run live) rather than
  guessing.
- (b) **the beyond-envelope volume regime** — the 93%-output limit cycle
  (music at −22 dBFS out-shouting voices at −25…−30 → system blind →
  DJ starves → silence heals → repeat).
- (c) harder rooms (pool-style noise/reverb).

Write build-or-defer into the proposal, with what measurement would flip
the decision. Also cost the cheap alternative that addresses (b) without
any ML: a dashboard advisory when music out-reads voices at the mic
(loudness vs noise floor + speech evidence) — "turn it down if you want
me reading the room."

### 4. Observability (small, queued from the gate)

- Expose the seeded `noise_floor_dbfs` in RoomState and annotation
  frames (part (d) phase 1 debuggability note — phases 3–6 lean on
  floor-relative terms and the seed is currently invisible).
- If cheap while you're in there: the last few raw headcount estimates
  in state (the pool session's `pair`→`16` attribution gap).

## Explicitly out of scope — don't let these creep in

- **The headcount "no stable middle" problem.** Trio undercount at
  threshold 0.70 (three real voices read solo/pair all night; only
  overlapping goodbye chatter reached 4) vs animated-pair overcount via
  the crowd path (phase 2b's `8`; sep_collapse=1 for single clusters,
  still unfixed). Real, documented, and its own milestone — M5 does not
  touch the estimator.
- Auto-volume, echo cancellation, crossfade, multi-zone (M4 deferrals
  stand).

## Data

`data/*` is deliberately never committed to THIS repo (public repo,
local data — the gitignore comment is founder policy). The corpus lives
in the founder's **private** repo `Jdudeisme/read-the-room-data`
(annotations/ and overrides/ at its root, with a README covering layout
and provenance). Clone it and copy the files under `data/annotations/`
and `data/overrides/` on the PC, then verify parity by running
`python scripts/tuning_report.py` —
expect 113 annotation records (104 good / 9 wrong) and 60 override
records, matching the Mac's output. Development and the test suite stay
on synthetic logs as always; the real corpus is for validating whatever
the learning proposes.

## Ground rules carried forward

- Logic-level tests, no network/no models in the suite; remember the
  FakeProvider lesson — fakes model the real provider's semantics, not
  the wished-for ones.
- The M2/M3/M4 bench gate is untouchable: `RTR_TORCH_THREADS=2`,
  headcount p95 < 1.37 s, emotion overall p95 < 1.2 s; any drift from
  M5 code is a bug, not a cost.
- The Mac remains the validation machine. Write `docs/M5-TEST-PLAN.md`
  in the M4 session-script style (Claude walks the founder through it,
  [HUMAN] steps marked), with a gate the Mac can run live — including a
  part-(d)-style measured phase for anything sensing-adjacent.

## Deliverables, in order

1. `docs/M5-PROPOSAL.md` (scope, design, explicit deferrals)
2. Branch `milestone-5-learning`
3. Implementation + tests
4. `docs/M5-TEST-PLAN.md`
