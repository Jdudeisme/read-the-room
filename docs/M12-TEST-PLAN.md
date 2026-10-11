# Milestone 12 Test Plan — Hear the room, not the playlist

This plan closes M12. It wraps the three gate parts from `ROADMAP.md` § M12,
verbatim:

> (a) **Offline:** the 2026-09-30 recording replayed through the M12-03
> gate, with the per-segment table compared against the "before" table in
> FIELD-NOTES 2026-09-30 (night), from `data/partf-replay/` or a
> regenerated equivalent. The recording has no reference channel, so this
> exercises only the reference-free part of M12-03.
> (b) **Live:** the M12-05 protocol run once on JPad by a human, with ≥ 3
> people, the pre-registered decision rule applied, and the result
> recorded in FIELD-NOTES and the README gate table.
> (c) **Budget:** a `bench_headcount.py --fallback` row on the reference
> machine with the M12 path enabled, inside the ~1.66 s headcount budget,
> with the DSP heartbeat still never blocking.
>
> No target numbers: the gate is the protocols run and the decisions
> recorded with their measurements.

Part (b)'s protocol is `docs/M12-05-RUN-SHEET.md` (approved, rule signed
2026-10-11). This plan adds the day's schedule, the take-by-take commands,
and who does what. Where they disagree, the run sheet wins.

> **Who does what.** **[FOUNDER]** runs everything with a mic open and
> everything with people in the room. Claude never starts a capture.
> **[CLAUDE]** does the offline work: preflight, analysis, write-up and
> benches. **No tuning in-session.** If something looks wrong, note it and
> carry on. The signed rule decides afterwards.

## What's left, and how long it takes

| step | who | when | time |
|---|---|---|---|
| 1. Gate (c) tooling: `bench_headcount.py --cancel` (the canceller running beside the workers) | CLAUDE | any time before step 7 | ~1 h, plus your diff review |
| 2. Preflight (run sheet B) | CLAUDE | the session day, before you start | ~20 min |
| 3. Fixed conditions + Block S, alone | FOUNDER | the session day | **~1 h 40 min** |
| 4. Break; guests arrive; consent | FOUNDER | | ~15 min |
| 5. Block G, **you + 3 guests (4 people)** | FOUNDER | | **~1 h**; guests needed ~1 h 10 min in total |
| 6. Analysis + FIELD-NOTES entry + rule outcomes | CLAUDE | after the session | ~1.5 h of compute, then the write-up |
| 7. Gate (c) bench rows | CLAUDE | after step 1 | ~20 min |
| 8. Gates (a), (b), (c) recorded in the README gate table | CLAUDE | after 6–7 | ~30 min |
| 9. Decisions on the rule's outcomes, then the merge to `main` | FOUNDER | after 8 | your call |

**Session day, in total:** about **3 h 15 min**, with Block S in the
afternoon and the guests for the last ~1 h 10 min. Block S can't be moved
to another day: both blocks must share one fixed-conditions table, and the
bracketing controls exist to catch drift within a day, not across days.

## Part (a) — offline replay of 2026-09-30 [CLAUDE]

**Status: the measurement is done. It needs only recording.** M12-03
is deferred (founder, 2026-10-09), and the reference-free features were
measured ineffective (D3 survey, FIELD-NOTES 2026-10-09). So there is no
reference-free gate to put in the replay. `scripts/m12_partf_replay.py
--gate none` reproduces the "before" table exactly: 830 rows, 0 differing
fields (FIELD-NOTES 2026-10-09). Part (a)'s "after" table therefore equals
its "before" table, by decision. Claude re-runs it once at step 8 to show
the harness still reproduces, and records that in the gate row.

## Part (b) — the M12-05 session [FOUNDER runs; CLAUDE analyses]

### Before the day [FOUNDER]
- [ ] Book **3 guests** (4 people with you) for the last ~1 h 10 min.
      Tell them up front that their voices will be recorded (the run sheet's
      section D has the exact words).
- [ ] Tape two floor marks: **N** at 0.6 m in front of the laptop (as in
      the ladder), and a seating arc within ~1.5 m for Block G.
- [ ] Choose something to read aloud for Block S (any book or article,
      enough for ~45 min), and a topic list for Block G's conversation.
- [ ] A phone stopwatch for the two volume-step takes.

### On the day, first [CLAUDE preflight; run sheet B]
- Branch `milestone-12-hear-the-room`, clean, tests green.
- `.env` rescue unset/off; no `RTR_HEADCOUNT_*` drift.
- The capture device is listed, and a 10 s `--reference` test take works
  (deleted after).
- No dashboard process running.
- Claude prints the take commands with today's date filled in.

### Fixed conditions [FOUNDER; run sheet C]

Fill in the run sheet's table and don't touch anything after. The ones
that bite:
- **Do Not Disturb ON.** The reference records every sound the laptop makes.
- **Dolby Atmos ON.** Spotify at 100 %, **repeat-one on**.
- Mic input 34 %. Plugged in.
- **Dashboard not running.**
- Open a PowerShell window in `C:\dev\read-the-room` and set the
  session prefix once:

```powershell
$P = "m12s-YYYYMMDD"   # today's date, e.g. m12s-20261012
function Take($Seconds, $Id, $Note) {
  .venv\Scripts\python.exe scripts\capture_room_wav.py --reference --seconds $Seconds --name "$P-$Id" --note $Note
}
```

Every take below is one line. For music takes: **set the Windows volume,
start the track from 0:00 in Spotify, then run the line.** Each take ends
on its own (200 s, or 360 s for the volume step). About a minute of
changeover between takes is built into the times above.

Tracks (repeat-one on, play these exact URIs):
- **H**: "Surround Sound", `spotify:track:1udwFobQ1JoOdWPQrp2b6u`
- **I**: "Rhymes Like Dimes (Instrumental)", `spotify:track:7y8Xkh3XqYDzBEyAofu6Qi`
- **J**: "Georgia On My Mind", `spotify:track:2fOx7wWR2sOBIWyveecAGX`

### Block S — you alone (~1 h 40 min)

Read aloud at mark N for every take that says "reading". For music-only
takes, stay silent. You can leave the room, but come back to change the
track and volume between takes.

| ✓ | # | music, volume | you | command |
|---|---|---|---|---|
| ☐ | 1 | none | reading | `Take 200 S-C1 "S control, reading at N, no music"` |
| ☐ | 2 | H 32 % | silent | `Take 200 S-MO-H32 "H, Win 32%, Atmos on, music only, silent"` |
| ☐ | 3 | H 66 % | silent | `Take 200 S-MO-H66 "H, Win 66%, Atmos on, music only, silent"` |
| ☐ | 4 | H 76 % | silent | `Take 200 S-MO-H76 "H, Win 76%, Atmos on, music only, silent"` |
| ☐ | 5 | I 32 % | silent | `Take 200 S-MO-I32 "I, Win 32%, Atmos on, music only, silent"` |
| ☐ | 6 | I 66 % | silent | `Take 200 S-MO-I66 "I, Win 66%, Atmos on, music only, silent"` |
| ☐ | 7 | I 76 % | silent | `Take 200 S-MO-I76 "I, Win 76%, Atmos on, music only, silent"` |
| ☐ | 8 | J 32 % | silent | `Take 200 S-MO-J32 "J, Win 32%, Atmos on, music only, silent"` |
| ☐ | 9 | J 66 % | silent | `Take 200 S-MO-J66 "J, Win 66%, Atmos on, music only, silent"` |
| ☐ | 10 | J 76 % | silent | `Take 200 S-MO-J76 "J, Win 76%, Atmos on, music only, silent"` |
| ☐ | 11 | H 32 % | reading | `Take 200 S-MX-H32 "H, Win 32%, Atmos on, mix, reading at N"` |
| ☐ | 12 | H 66 % | reading | `Take 200 S-MX-H66 "H, Win 66%, Atmos on, mix, reading at N"` |
| ☐ | 13 | H 76 % | reading | `Take 200 S-MX-H76 "H, Win 76%, Atmos on, mix, reading at N"` |
| ☐ | 14 | I 32 % | reading | `Take 200 S-MX-I32 "I, Win 32%, Atmos on, mix, reading at N"` |
| ☐ | 15 | I 66 % | reading | `Take 200 S-MX-I66 "I, Win 66%, Atmos on, mix, reading at N"` |
| ☐ | 16 | I 76 % | reading | `Take 200 S-MX-I76 "I, Win 76%, Atmos on, mix, reading at N"` |
| ☐ | 17 | J 32 % | reading | `Take 200 S-MX-J32 "J, Win 32%, Atmos on, mix, reading at N"` |
| ☐ | 18 | J 66 % | reading | `Take 200 S-MX-J66 "J, Win 66%, Atmos on, mix, reading at N"` |
| ☐ | 19 | J 76 % | reading | `Take 200 S-MX-J76 "J, Win 76%, Atmos on, mix, reading at N"` |
| ☐ | 20 | **I, 32 → 66 % at 3:00** | reading | `Take 360 S-VS-I "I, Win 32->66% at 3:00, Atmos on, volume step, reading at N"` |
| ☐ | 21 | none | reading | `Take 200 S-C2 "S control, reading at N, no music"` |

**Take 20, the volume step.** Set 32 %, start I, start the take **and** the
stopwatch together. At 3:00, move the Windows slider to 66 % in one
motion and **write down the stopwatch second**. Keep reading through it.

### Break, then guests (~15 min) [FOUNDER]
- [ ] Read the consent script (run sheet D) to all 3 guests and get each
      one's agreement. No agreement from everyone, no Block G.
- [ ] Seat everyone within ~1.5 m of the laptop. **Write down who is
      there** (initials).
- [ ] The talk: natural conversation from the topic list, the same kind
      all the way through. Don't talk over the music on purpose, and
      don't hold back for it either.

### Block G — 4 people (~1 h)

Write the initials in each note, e.g. `"... conversation, 4 people (J,A,B,C)"`.

| ✓ | # | music, volume | command |
|---|---|---|---|
| ☐ | 1 | none | `Take 200 G-C1 "G control, no music, conversation, 4 people (...)"` |
| ☐ | 2 | H 32 % | `Take 200 G-MX-H32 "H, Win 32%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 3 | H 66 % | `Take 200 G-MX-H66 "H, Win 66%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 4 | H 76 % | `Take 200 G-MX-H76 "H, Win 76%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 5 | I 32 % | `Take 200 G-MX-I32 "I, Win 32%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 6 | I 66 % | `Take 200 G-MX-I66 "I, Win 66%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 7 | I 76 % | `Take 200 G-MX-I76 "I, Win 76%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 8 | J 32 % | `Take 200 G-MX-J32 "J, Win 32%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 9 | J 66 % | `Take 200 G-MX-J66 "J, Win 66%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 10 | J 76 % | `Take 200 G-MX-J76 "J, Win 76%, Atmos on, mix, conversation, 4 people (...)"` |
| ☐ | 11 | **I, 32 → 66 % at 3:00** | `Take 360 G-VS-I "I, Win 32->66% at 3:00, Atmos on, volume step, conversation, 4 people (...)"` |
| ☐ | 12 | none | `Take 200 G-C2 "G control, no music, conversation, 4 people (...)"` |

**Occupancy log.** If anyone leaves, arrives, takes a call, or a
notification sounds during a take, write down the take number and the
second. That take is **gated, not deleted**: it's kept and left out of the
rule. Re-record it at the end of the block if there's time, under a new
name with a `b` on the end (e.g. `Take 200 G-MX-H66b "..."`). The
analysis gates the original and uses the re-take.

### After the takes [FOUNDER, 5 min]
Send Claude:
- the filled conditions table;
- the two stopwatch seconds (S-VS-I, G-VS-I);
- the occupancy log and any gated takes.

The WAVs stay on JPad in `data/captures/`. They are never committed.

### Analysis [CLAUDE]

```powershell
.venv\Scripts\python.exe scripts\m12_session_replay.py data/captures/$P `
  --step-at S-VS-I=<s> --step-at G-VS-I=<s> [--gate <ID> ...] `
  --json data/m12-replay/session-$P.json
```

The rule is applied by the signed code: Rule 1 (1a, 1b), Rule 1c, Rule 2
(2a, 2b) and the drift check. The FIELD-NOTES entry carries:
- the conditions table;
- the occupancy log;
- per-take numbers, off and on;
- each test worked line by line;
- the three outcomes: M12-03 *stays deferred / reopens*; 1c *clear / file*;
  mapper *switch / hold / file*.

## Part (c) — the budget with the M12 path on [CLAUDE]

`bench_headcount.py --fallback` times the workers directly. Today it never
runs the canceller, so it can't see M12's extra CPU. Step 1 adds a
`--cancel` option: the M12-02 canceller (`StreamingCanceller`, as
`CleanSource` runs it) processing a ladder take in real time on its own
thread, beside the timed workers. That's the contention the live path
adds. Its diff gets your review. Then:
- three `--fallback --cancel` runs alternated with three plain `--fallback`
  runs in one sitting (the M8 convention), against the 1.66 s headcount
  budget and the 1.2 s emotion bound;
- the heartbeat: `bench_cancel_heartbeat.py` as already run
  (2026-10-11: cancelling p95 2.032 s, worst tick 32 ms late), repeated
  once for the gate row.

## Completing M12

M12 passes when all three parts are **run and recorded**: FIELD-NOTES
entries, plus a "Milestone 12 gate (Windows laptop `JPad`)" section in
the README with the bench table. There are no target numbers. What
happens next depends on the rule's outcomes:
- **Mapper SWITCH:** a small mapper-only diff (reads `clean_trend` when
  present). It's in M12-04's charter, so it lands **before** the merge.
- **M12-03 REOPENS, or 1c FILES:** these are new work. **Your call:** finish
  it inside M12 before merging, or merge M12 and carry it as a ROADMAP
  item (as M8 carried its caveats to M12). Recommended: carry it, because
  the gate is "decisions recorded", and M9 waits on M12.
- **The merge to `main`** is a separate, deliberate step, never done
  in-session.
- **Delete the session WAVs** once every decision is recorded and any diff
  they triggered has merged, as promised to the guests.
