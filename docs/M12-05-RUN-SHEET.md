# M12-05 — paired live session on JPad — run sheet

**Status.** DRAFT, written 2026-10-11. On 2026-10-11 the founder chose
**Rule 2's harm-required variant**, added **Rule 1c** (phantom count,
FIELD-NOTES 2026-10-11, later), and picked track I. The rule is
otherwise PROPOSED, not signed. The
founder signs it, together with the track I pick, **before** the session. After the data is in,
nothing in the rule moves. Human-run on JPad by the founder, with ≥ 3
people for Block G. It is never started by an agent (CLAUDE.md). It changes no
constant, threshold or default. Its output is a FIELD-NOTES entry, the
README gate-table row for M12 gate (b), and the rule's outcomes.

**What it answers** (ROADMAP M12-05, with founder decision D3 of
2026-10-11):
1. **M12-03, the deferred gate.** With cancellation on, do music-only
   phases still certify? Is headcount over vocal music worse than over
   instrumental? Either one reopens M12-03 at step 3, the classifier
   (deferral note, ROADMAP M12-03).
2. **M12-04, the mapper's trend.** Does the mapper's `energy_action`
   move from `trend` to `clean_trend`? That depends on the raw-versus-clean
   energy difference between matched music-on and music-off phases with
   the same talk, and on the volume step D3 added.
3. **M12 gate (b):** the protocol run once, with ≥ 3 people, the rule
   applied, and the result recorded.

**Why one capture-only session.** The charter's captures are mic +
reference, so every phase replays offline with cancellation on **and**
off. That's both arms from one sitting, against identical audio. So the
dashboard does **not** run during takes: two streams on this array at
once changed embedding spread on 2026-09-06 (addendum 1, item 5).
Spotify is driven by hand.

## A. Decide before the session (founder)

1. **Approve this sheet and sign the rule (section G).** It is
   pre-registered: tolerances can change now, not afterwards.
2. **Track I**: decided 2026-10-11, *Rhymes Like Dimes (Instrumental)*.
   Confirm it plays on Spotify before Block S, and record the URI.
3. **Consent.** This session **records the guests' voices to disk**: a
   new capture of room-derived data. Tell every guest before Block G
   starts, and get their agreement (the script is in section D). Without
   consent from everyone present, Block G doesn't run.
4. **Who and when.** Block S is the founder alone, before guests arrive
   (~75 min, and the music-only takes need nobody in the room). Block G
   is ≥ 3 people, the same people throughout (~55 min).

## B. Preflight — Claude runs this before the session

Offline, about 15 minutes. **Stop and diagnose** on any failure.

1. Branch `milestone-12-hear-the-room`, at or after `6979023`, with a
   clean tree. `python -m pytest -q` is green (426+).
2. `.env`: `RTR_HEADCOUNT_RESCUE_ENABLED` unset or 0; no `RTR_HEADCOUNT_*`
   drift from the defaults (as in the M7 part (f) preflight). The
   cancellation and reference flags don't matter here, because capture
   runs through `capture_room_wav.py`, not the engine.
3. **The analysis tooling exists and is tested before the session.**
   `scripts/m12_session_replay.py --ladder` is run once on the
   2026-10-09 ladder takes. It must reproduce
   `m12_energy_survey.py`'s numbers for those takes, so the rule's
   analysis is fixed in code before the data exists.
4. `.venv\Scripts\python.exe scripts\capture_room_wav.py --list-devices`
   names `Microphone Array on SoundWire D`. A 10 s `--reference` test
   take writes a `.ref.wav`. Delete the test take afterwards.
5. Nothing is serving the dashboard:
   `Get-CimInstance Win32_Process | Where-Object CommandLine -match 'dashboard' | Select-Object ProcessId, CommandLine`.

## C. Fixed conditions — record before the first take, then don't touch

Copy this into the FIELD-NOTES entry, filled in.

| setting | value |
|---|---|
| Mic input volume | 34 % |
| Mic "Audio enhancements" | (as found; record it) |
| Dolby Atmos for Speakers | **ON** (the condition RTR runs in; every M12 measurement so far) |
| Spotify volume / Normalize / EQ | 100 % / (record) / (record) |
| Repeat-one | **on** |
| Do Not Disturb | **ON, required**: the reference records every sound the laptop plays |
| Power | plugged in |
| Laptop position | centre of the group, lid at the usual angle. Far-field broke the middle on 2026-08-09 |
| Solo talk position | **N**, 0.6 m in front (tape mark, as in the ladder) |
| Group seating | everyone within ~1.5 m of the laptop; note the arrangement |
| Start wall-clock time | |

## D. What to tell the guests (before Block G)

> "My laptop is going to record about 45 minutes of us talking, with
> music playing at different volumes. It's to test whether the system can
> tell our voices apart from its own music. The recording stays on this
> laptop. It's analysed as numbers (loudness, how many voices), never
> transcribed, and I delete the audio once the results are written up.
> Is everyone OK with that?"

Everyone present must agree. If someone arrives mid-block, pause, ask
them, and note the time.

**How to talk in Block G:** natural conversation, the same kind
throughout. Keep one ongoing topic list, so the talk in music-on takes
resembles the talk in music-off takes. Nobody performs or reads. Don't talk
over the music on purpose, and don't hold back for it either.

## E. Tracks, volumes and takes

| id | track | why | Spotify URI |
|---|---|---|---|
| H | "Surround Sound", JID ft. 21 Savage & Baby Tate (ladder T3) | vocal hip-hop: the 09-30 mechanism, and the M12-03 question | `spotify:track:1udwFobQ1JoOdWPQrp2b6u` |
| I | **"Rhymes Like Dimes (Instrumental)", MF DOOM** (founder's pick, 2026-10-11) | instrumental hip-hop, no vocals: the control for H, and continuity with 09-30 (the instrumental the room recovered under). Its drums are boom-bap, not H's trap, so H vs I differs in more than vocals: say so when reading 1b | note the URI at the session (the instrumental isn't confirmed on Spotify; if it isn't there, stop and pick again before Block S) |
| J | "Georgia On My Mind", Oscar Peterson Trio (ladder T2) | jazz: the 09-30 case where headcount recovered | `spotify:track:2fOx7wWR2sOBIWyveecAGX` |

**Volumes:** Windows 32 / 66 / 76 % (founder D2, 2026-10-09, in place of the
charter's 32 / 75 %). Spotify stays at 100 %.

Every take is one command (`DATE` = `20261012` style). Names must be
unique, because the script overwrites:

```powershell
.venv\Scripts\python.exe scripts\capture_room_wav.py --seconds <S> --name m12s-DATE-<ID> --note "<note>" --reference
```

The note must give track, Windows %, Atmos, who is talking and how, e.g.
`"H, Win 66%, Atmos on, mix, 4 people conversing (A,B,C,D)"`. For music
takes, start the track and then the capture. **200 s** is ≥ 3 minutes
after the canceller's lock, which took 8–30 s on the ladder.

### Block S — the founder alone (before guests)

| # | ID | music | Win % | talk | s |
|---|---|---|---|---|---|
| 1 | S-C1 | — | — | N, reading | 200 |
| 2–10 | S-MO-{H,I,J}{32,66,76} | each track at each volume | 32 / 66 / 76 | **silent; leaving the room is fine** | 200 each |
| 11–19 | S-MX-{H,I,J}{32,66,76} | each track at each volume | 32 / 66 / 76 | N, reading | 200 each |
| 20 | S-VS-I | I | **32 → 66 at 180 s** | N, reading, without pausing at the step | 360 |
| 21 | S-C2 | — | — | N, reading | 200 |

### Block G — ≥ 3 people, the same people throughout

| # | ID | music | Win % | talk | s |
|---|---|---|---|---|---|
| 1 | G-C1 | — | — | conversation | 200 |
| 2–10 | G-MX-{H,I,J}{32,66,76} | each track at each volume | 32 / 66 / 76 | conversation | 200 each |
| 11 | G-VS-I | I | **32 → 66 at 180 s** | conversation, carrying on through the step | 360 |
| 12 | G-C2 | — | — | conversation | 200 |

**The volume step (D3).** Move the Windows slider in one motion from 32 to
66 % at 3:00 of the take, and note the second you did it. Talk continues
unchanged. This is the DJ turning the music up while the room stays the
same: the feedback case the ladder couldn't show (FIELD-NOTES
2026-10-11, finding 3).

**Ground truth, logged live.** For each take, write who is present and
the clock time of any arrival, departure, interruption or notification.
A take whose occupancy changed is **gated, not deleted**: kept, flagged,
and left out of the rule (invariant 9's spirit).

Controls bracket each block (C1/C2). Drift shows up as a C1/C2
difference, not as an effect.

## F. Analysis — offline, after the session

`scripts/m12_session_replay.py` (written 2026-10-11; tested on the
ladder, section B3). It is `m12_energy_survey.py`'s replay, extended with
headcount, on the engine's schedule. Per take, it runs **cancellation off
and cancellation on** (`aec.cancel`, causal, the live path bit for bit),
and reports from the canceller's first lock on. Takes that never lock are
reported as such: under the accepted B66 envelope, faint 32 % music under
talk may not lock, so there clean = raw.

**The config it mirrors:** `Config()` defaults as on JPad's `.env` (headcount
interval 2.0 s, min speech ratio 0.2, certification 0.75 during playback
and 0.5 otherwise, rescue OFF, ECAPA on 2 threads). That is stated in the
FIELD-NOTES entry (CLAUDE.md).

**Energy** is the three-term `energy_score(..., None)` from the 2026-10-11
survey. Arousal is the same input to raw and clean energy, so it can't
create a raw-vs-clean difference. Leaving it out avoids replaying emotion
for the energy question. This is stated, not hidden.

Per take, it reports:
- **Certification:** the share of locked windows that are eligible (certified
  ratio ≥ 0.2, the headcount/emotion submit bar), cancellation off and on.
- **Headcount:** the smoothed bucket distribution and the `solo` share
  (`HeadcountEstimator` + `BucketSmoother`, defaults), off and on.
- **Energy:** median `energy` (as the engine publishes it with cancellation
  on: raw loudness and activity, clean certification) and median
  `clean_energy`, each minus the bracketing controls' mean.
- **Volume-step takes:** the share of hops where `trend` and `clean_trend`
  read `rising` in the 60 s after the step, against the 60 s before; and the
  energy step of each.

## G. Decision rule — PROPOSED (sign before the session)

Every input below comes from the **cancellation-on** replay, which is
the configuration under decision. Only Block G takes with unchanged
occupancy count for the headcount tests. Block S counts for the rest.

### Rule 1 — does M12-03 reopen?

- **1a, music-only certification.** For each `S-MO` take, the eligible
  share of locked windows. **Reopen if any take is above 0.05.** That is
  the same tolerance the ladder's rule gave speech-only controls for
  banking (signed 2026-10-09). On the 10-09 ladder, cancellation took the
  rap from 0.345 of chunks to 0.001.
- **1b, vocal vs instrumental headcount (Block G).** At each volume,
  compare the `solo` share over H with the share over I. **Reopen if, at
  any volume, H's `solo` share exceeds I's by more than 0.15 _and_
  exceeds the mean of G-C1/G-C2 by more than 0.15.** That is the 09-30
  failure: a room of 3 read `solo` under vocal hip-hop and recovered
  under other music.
  The tolerance is the same at every volume. Whether H's take locked is
  reported beside each comparison: an unlocked take at 32 % is
  uncancelled (the accepted B66 envelope), and the entry says so.
- **Outcome:** if neither fires, M12-03 **stays deferred**, with this
  session as the evidence. If either fires, M12-03 reopens at step 3 (the
  classifier), as its deferral note says. The FIELD-NOTES entry records
  which test fired and on which takes.

### Rule 1c — does a lone voice over our own music phantom-count? (added 2026-10-11)

Block S is one person, so every non-`solo` headcount update there is a
phantom. On the ladder, with cancellation on, a lone reader at 66/76 %
read `solo` only 0.20–0.45, against 1.00 for the speech-only controls
(FIELD-NOTES 2026-10-11, later).

- For each Block S mix take **where the canceller locked** (any volume;
  unlocked, the take is uncancelled and 1c's question doesn't apply),
  compare the `solo` share with the mean of S-C1/S-C2.
- **Fires if any take's `solo` share is more than 0.15 below the
  controls'** (the same margin as 1b).
- **Outcome:** **FILE** a REQUIRES-REVIEW finding, "phantom count over
  RTR's own playback with cancellation on", with the takes and shares.
  The remedy isn't pre-decided: M12-03's classifier refuses music-only
  chunks, while here the music sits under real speech. If nothing fires:
  **CLEAR**.

### Rule 2 — does the mapper's trend move to `clean_trend`?

- **2a, the feedback loop is observed.** In S-VS-I or G-VS-I, `trend`
  reads `rising` in at least **0.20** more of the post-step hops than
  `clean_trend` does.
- **2b, clean is no worse.** In every 66/76 % mix take (S and G), the
  distance of `clean_energy` from the bracketing controls is no greater
  than the distance of `energy`, within **0.01**. **Not at 32 %.** The
  pre-session ladder test (B3) showed why. At 32 % the music's loudness
  bias is about zero, and the gap is set by take-to-take talk variation
  (the two ladder reading controls differ by 0.011). The one locked 32 %
  ladder take, T3-MX32, failed by 0.006 on talk alone (published energy
  0.000 from the controls, clean 0.016). A looser 32 % tolerance would
  only be guessing at that variation, so 32 % is out of 2b.
- **Outcome:**
  - **SWITCH:** both 2a and 2b hold. The mapper reads `clean_trend` when
    it is present and `trend` otherwise. That is a mapper-only,
    REQUIRES-REVIEW diff with this entry as its evidence.
  - **HOLD:** 2b holds but 2a doesn't. No switch. The live room didn't
    show the loop, matching the offline finding that loudness alone stays
    under the trend's threshold (FIELD-NOTES 2026-10-11). `clean_*` stays
    recorded, so a later session can revisit this.
  - **FILE:** 2b fails. No switch, and a REQUIRES-REVIEW finding records
    where clean energy read farther from the room than raw.

**Why the rule asks for harm before switching (2a).** Switching has
a cost: it is a published behavior change in the DJ. The 2026-10-11
survey found no case where the loudness path alone moved the trend. Evidence
over cleverness says to switch on an observed loop, not on a
plausible one. **Chosen by the founder, 2026-10-11** over the
alternative of switching on 2b alone.

**Drift check (both rules).** If a block's C1 and C2 differ by more than
the spread among that block's mix takes at 32 %, on certified share or
energy, record the block as drifting. Each outcome then stands only if it
holds with C1 excluded and with C2 excluded. The script does this itself. An
outcome that changes when either control is dropped prints as
`NOT ROBUST TO DRIFT`, and the session then decides nothing on that rule.

**Implemented in code** before the session (run sheet B3):
`scripts/m12_session_replay.py`, constants `R1A_MAX_ELIGIBLE`,
`R1B_SOLO_MARGIN`, `R1C_SOLO_MARGIN`, `R2A_RISING_MARGIN`, `R2B_TOLERANCE`. The script prints
each test's inputs and the outcomes. Changing a constant after the
session voids the rule.

Signed: __________ (founder). Date: __________. Variant for 2: **harm-required** (chosen 2026-10-11).

## H. Afterwards

- **FIELD-NOTES entry:** the conditions table, the occupancy log, per-take
  numbers, the rule worked line by line, and each outcome.
- **README gate table:** M12 gate (b) row.
- **The WAVs** (mic and reference) stay until every decision is recorded
  and any diff the rule triggers has merged. Then **delete them**, as told
  to the guests. `data/captures/` is local and never committed.
- No `.env`, `config.py` or signature-file change comes out of this
  session. Each outcome that changes code is its own reviewed diff.

## I. Hard don'ts

- **Never start a capture or a session as an agent.** The founder runs
  every take.
- **No mid-session tuning.** If something looks wrong, note it and carry
  on. The rule decides afterwards.
- **No dashboard during takes** (a single stream on the array).
- **Never enable the headcount rescue.**
- **No Block G without everyone's consent.**
