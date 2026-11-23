# Live session — M12-09 group session: does group talk defeat the live canceller? — run sheet

**Why.** On 2026-11-21 (5 people at ~1.5 m, talking most of the time,
32 %) the live canceller removed almost nothing (frame raw − clean
0.1–0.5 dB). On 2026-11-23 the founder was alone at ~1.5 m, and it removed
6.5 dB (32 %) and 7.6 dB (66 %) while they read. Reference dropouts were
ruled out in that condition. The diagnostic clip's reference was broken,
so the live-versus-offline comparison couldn't run. That bug is fixed
(`14b52d6`, merged in `d3044d1`). This session puts the 11-21 condition
back, a group talking, with a working clip. M12-09's acceptance criterion
can then be met: attribute the ineffectiveness, or rule the hypotheses out
(ROADMAP M12-09; FIELD-NOTES 2026-11-21 and 2026-11-23).

**Branch:** `main` at or after `c0191ea`. It contains the diagnostics, the
clip fix and M9-07. **Founder-run:** the founder plus **at least 3
guests, the same people throughout**. Claude never starts a capture, the
dashboard or the frame recorder. About 30 minutes with the guests, plus
Claude's preflight and analysis.

**What it answers.**
1. During group talk, does live cancellation fall short of offline
   `cancel()` on **the same audio**? If so, the live path is at fault. If
   both fall short, the audio itself is the cause: double talk or level.
2. Does the same session's music-only stretch cancel well? It is the
   within-session control.
3. Once the filter has converged in silence, does it hold up when talk
   resumes? This separates "can't converge under talk" from "can't cancel
   under talk".
4. Are there reference slips (re-locks, lag jumps, lost audio) under the
   heavier load of several voices?

## A. Decide before the session (founder)

1. **Approve this sheet and the reading in section G.** It is
   pre-registered: tolerances can change now, not afterwards.
2. **Approve recording the group in the clip (amends
   `docs/M12-09-PLAN.md` D2).** D2 approved the clip "only with the
   founder alone in the room", and `.env.example` says the same. This
   session needs the clip **during group talk**: that's the comparison
   the founder-alone session couldn't make. Use the M12-05 precedent: 3
   minutes of room audio, **only with every guest's agreement** (script
   in section D), deleted once the FIELD-NOTES entry is written.
   **Without everyone's consent, the clip stays off** (section E, the
   no-consent variant). Questions 1 and 4 then go unanswered, and only
   the log-line evidence remains.
3. **Approve the frame recorder** (`scripts/record_frames.py`;
   founder-approved for the M8 gate, 2026-10-09). It saves the dashboard's
   state frames: numbers, no audio. That lets this session be measured
   the way 11-21 was (frame `loudness_dbfs` − `clean_loudness_dbfs`), so
   today's numbers line up with 11-21's directly. The founder starts it by
   hand.
4. **Clip length: 180 s** (`RTR_DEBUG_CANCEL_CLIP_S=180`). The offline
   canceller starts cold and settles over its first lock + 10 s (~20–40
   s). At 60 s that leaves only 4–8 comparable 5 s windows; 180 s
   leaves about 28–32. This is a run-time setting, not a calibration.

## B. Preflight — Claude, before the guests arrive

1. The main checkout is on `main` (at or after `c0191ea`), with a clean
   tree. `python -m pytest -q` passes (450+). `src/sensing/audio.py` has
   `DIAG_REF_DELAY_S`, the clip fix.
2. No dashboard process is running, and nothing listens on :8000
   (FIELD-NOTES 2026-09-06).
3. `.env` has no `RTR_PLAYBACK_REFERENCE_*`, `RTR_PLAYBACK_CANCEL_*` or
   `RTR_DEBUG_*` lines, so the window's variables decide.
   `RTR_HEADCOUNT_RESCUE_ENABLED` is unset or 0.
4. `data/captures/` has no `live-clip-*` files. `scripts/record_frames.py
   --help` runs.
5. Claude fills in `<DATE>` below.

## C. Fixed conditions (founder; as 2026-11-20 Block G and 2026-11-21)

Copy into the FIELD-NOTES entry, filled in.

| setting | value |
|---|---|
| Room / spot | the living-room alcove, laptop on the TV stand in the usual spot, TV off |
| People | founder + ≥ 3 guests, **the same people throughout**; names or initials and the count |
| Seating | an arc, everyone at **~1.5 m** from the laptop (estimate; note the arrangement) |
| Windows volume | 32 % (66 % in G4) |
| Mic input | 34 % (check it) |
| Dolby Atmos | on |
| Spotify | 100 %, Normalize on, EQ flat |
| Do Not Disturb | on: the reference records every sound the laptop plays |
| Other apps | **closed** (browser except the dashboard tab, Discord, Teams, OneDrive). Note anything left open: load is a suspect |
| Power | plugged in |

## D. What to tell the guests (before starting)

> "My laptop's going to play music and listen to the room for about 25
> minutes. It's testing whether the system can take its own music out of
> what the microphone hears. For 3 minutes of that, it saves the audio,
> our voices included. That stays on this laptop. It's analysed as numbers
> (how much music was removed), never transcribed, and I delete it once
> the results are written up. Is everyone OK with that?"

Everyone present must agree. If someone new arrives, pause and ask them,
and note the time. If anyone says no, run the no-consent variant
(section E).

**How to talk:** natural conversation, the same kind throughout, as in
Block G. Nobody reads aloud or performs. Don't raise your voices over the
music on purpose, and don't hold back for it either.

## E. Start (founder; separate PowerShell windows, not Claude's `!`)

**Window 1, the dashboard:**

```powershell
cd C:\dev\read-the-room
$env:RTR_PLAYBACK_REFERENCE_ENABLED = "1"
$env:RTR_PLAYBACK_CANCEL_ENABLED = "1"
$env:RTR_DEBUG_CANCEL_CLIP_S = "180"
cmd /c ".venv\Scripts\read-the-room-dashboard.exe > data\sessions\live-<DATE>-m12-09-group.log 2>&1"
```

**No-consent variant:** use the same commands but set
`$env:RTR_DEBUG_CANCEL_CLIP_S = "0"`.

Open http://127.0.0.1:8000. **Tell Claude it's up** so Claude can confirm
the serving process before anything else.

**Window 2, the frame recorder** (once Claude has confirmed the process):

```powershell
cd C:\dev\read-the-room
.venv\Scripts\python.exe scripts\record_frames.py --out data\sessions\live-<DATE>-m12-09-group.frames.jsonl
```

**Optional window 3**, to watch the log:
`Get-Content data\sessions\live-<DATE>-m12-09-group.log -Wait -Tail 5`.

## F. Phases (founder; note the clock time at each start)

Everyone is seated before the music starts.

| phase | music | room | minutes | what it's for |
|---|---|---|---|---|
| G1 | **genre pick Hip-Hop / mid**, Windows 32 % | **conversation** | 8 | The 11-21 condition. The canceller locks about 10–30 s after the music starts, and **the clip records 3 minutes from about 30 s after that**, all inside G1 |
| G2 | 32 % (let it run) | **everyone silent** | 3 | The same session's music-only control: does it cancel when nobody talks? |
| G3 | 32 % | conversation | 5 | After converging in silence, does it hold up under talk? |
| G4 | Windows **66 %** | conversation | 4 | Level. **Optional:** skip it if the guests need to go |

- **Starting the music:** start the conversation as soon as the dashboard
  is up, then make the Hip-Hop / mid pick ("play it") **once**, as soon
  as you can. If RTR starts a song by itself first (bootstrap, once it
  hears talk), make the pick anyway. **G1 starts at your pick:** note
  that time.
- **After the pick, don't touch Spotify:** no pause, skip or pick. RTR
  picks the songs. Change only the Windows volume, at G4.
- **No "music paused" phase:** pausing doesn't hold while people talk
  (ROADMAP M9-08). The pass-through control was already shown on
  2026-11-23, phase D.
- **Write down:** each phase's start time; who's present (and any change);
  anything odd: notifications, someone walking in, apps opening, a guest
  leaving.

## G. Pre-registered reading (written before the session)

**Measures:**
- **(a)** Live reduction per phase: the median of the 10 s `canceller:`
  log lines (E(mic) / E(clean), dB).
- **(b)** The 11-21 metric per phase: the median of frame
  `loudness_dbfs` − `clean_loudness_dbfs` over frames with music
  playing.
- **(c)** In the clip window: the live and offline medians over the
  settled 5 s windows (`scripts/m12_09_clip_compare.py`). Also the
  offline lag timeline, plus re-locks and lost audio in the live
  reports.

**Tolerance: 1.2 dB.** This is the spread of the offline raw − clean
medians across the three 2026-11-20 Block G takes at 32 %, the closest
run-to-run variance measured in this room and condition (2.3 / 3.4 /
3.5 dB; FIELD-NOTES 2026-11-21). It comes from a different metric than
(a) and (c). It is used because it is the nearest measured spread, not
because it fits exactly.

**Readings.** More than one can hold; each that holds is recorded.
- **R1, the live path is at fault:** in (c), offline exceeds live by more
  than 1.2 dB. Next: the live canceller's state and alignment (for
  example, 11-23 finding 2's filter carried through "shift" re-locks).
- **R2, the audio itself (double talk or level):** in (c), live and offline
  are within 1.2 dB, **and** G1's (a) is more than 1.2 dB below G2's (a).
  Then the canceller, live or offline, can't remove the music under
  group talk. Next: a fix proposal (double-talk control, step-size,
  post-filter).
- **R2b, it can't converge under talk:** G3's (a) is more than 1.2 dB
  above G1's. The filter that converged in G2's silence cancels under
  talk that the cold filter in G1 couldn't.
- **R3, reference slips:** inside the clip window there are re-locks or
  lost audio (`lost=+N ms`, N > 0, in any report), or lag changes in the
  offline timeline. That points at 11-21's leading hypothesis under
  load. Record the count of "data discontinuity" warnings too.
- **R4, not reproduced:** G1's (b) is within 1.2 dB of G2's (b), and G1's
  (a) is within 1.2 dB of G2's (a): group talk didn't hurt today. Next:
  what else differed from 11-21 (5 vs this many people, load, music).
- **Inconclusive:** no clip written (crash, or a resync that discarded it
  and never recorded again); fewer than 6 settled windows in (c); or the
  no-consent variant, where R1 can't be read. Recorded as inconclusive,
  never as a reading.

## H. Stop and afterwards

**Stop.** `Ctrl+C` in window 2 (the frame recorder), then in window 1
(the dashboard). Answer `Y` to "Terminate batch job?". Tell Claude it's
done, with the notes from F. **Don't paste the log.**

**Analysis (Claude):**
1. From the log, per phase: the canceller lines (lock, lag, re-locks,
   reduction, reference level, lost audio), the "diagnostic clip written"
   path (or "discarded"), and the data-discontinuity count.
2. Per-phase (b) from the frames file. Also the headcount buckets with the
   known N: M12-07 evidence under group talk with cancellation on.
3. `python scripts/m12_09_clip_compare.py data/captures/live-clip-<stamp>`.
   Check that the sidecar's `ref_zero_fraction` is small; if the script
   warns, the comparison is void (inconclusive).
4. The FIELD-NOTES entry: the conditions, the occupancy log, the measures
   per phase, section G worked line by line, and the readings that hold.
   Also the ROADMAP M12-09 note.
5. **Delete the clip** (`data/captures/live-clip-*`) once the entry is
   written, as told to the guests. The log and the frames file hold
   numbers only and stay local in `data/sessions/` (never committed).
6. Then the founder's decision: the M12-09 fix proposal on whichever
   reading holds. `cancel-on-by-default` stays parked until a fix is
   re-checked live.

## I. Hard don'ts

- **Claude never starts** the dashboard, the frame recorder or a capture.
- **No clip without every guest's consent.**
- **No mid-session tuning.** If something looks wrong, note it and carry
  on. Section G decides afterwards.
- **Never enable the headcount rescue.**
- **Don't touch Spotify after the G1 pick** (M9-08).
