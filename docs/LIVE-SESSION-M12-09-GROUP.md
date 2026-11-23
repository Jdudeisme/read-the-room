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

**Branch:** `main`, with this sheet's measure merged (section B checks it).
**Founder-run:** the founder plus **3–4 guests (4–5 people in all), the
same people throughout**, matching 2026-11-20 Block G (4) and 2026-11-21
(5). Claude never starts a capture, the dashboard or the frame recorder.
About 30 minutes with the guests, plus Claude's preflight and analysis.

**What it answers.**
1. During group talk, does live cancellation fall short of offline
   `cancel()` on **the same audio**? If so, the live path is at fault.
2. If not, does this group's talk keep **any** canceller from removing the
   music, more than 11-20's group did?
3. Does the live canceller **add** energy (clean louder than the mic)?
   Talk can't cause that.
4. Are there reference slips (re-locks, lag jumps, lost audio) under the
   heavier load of several voices?

## Two measures (why both)

- **Energy reduction**, E(mic) / E(clean). This is what the 10 s log
  lines report, and close to 11-21's frame raw − clean. **Talk caps it.**
  The canceller removes music, not voices, so any talk keeps it low even
  when cancellation is perfect. On the 2026-11-20 takes, offline
  `cancel()` read **2.2–3.6 dB** whenever anyone talked: the founder alone
  reading (S-MX-H32, 2.2), and the group (G-MX-H32/I32b/J32, 3.6/3.5/2.7).
- **Music reduction**: the energy predictable from the reference, in the
  mic vs in the clean. This is the talk-proof measure
  (`scripts/m12_09_clip_compare.py`, added for this session; tested on
  synthetic echo plus talk). On those same takes it read **17.7 dB**
  (founder reading), and **19.1 / 15.2 / 13.6 dB** for the group. It
  ignores the room's noise floor as well as talk, so it's higher than the
  energy measure even with no talk (S-MO-H32, music only: 25.5 vs
  11.9 dB). **Compare it only with itself.** It varies ~5.5 dB between
  tracks, so decisions compare **live with offline on the same windows**,
  never one phase's song against another's.

## A. Decisions (founder, 2026-11-23)

1. **This sheet and the reading in section G: approved,** including the
   talk-proof measure. They're pre-registered: tolerances can change now,
   not afterwards.
2. **Recording: allowed and kept** (CLAUDE.md, recording policy, MVP build
   phase; this amends `docs/M12-09-PLAN.md` D2). Everyone present is told
   before the dashboard starts (section D). If anyone objects, the clip
   stays off (the no-recording variant, section E). Questions 1–3 then
   can't be answered.
3. **The frame recorder: approved** (`scripts/record_frames.py`).
4. **Clip length: 660 s** (`RTR_DEBUG_CANCEL_CLIP_S=660`). It starts about
   30 s after the canceller first locks, and so covers most of G1, all of
   G2 and most of G3. The talk-proof measure then reaches every phase. It
   holds ~127 MB in memory while recording and writes ~63 MB of WAV once.
   This is a run-time setting, not a calibration.

## B. Preflight — Claude, before the guests arrive

1. The main checkout is on `main`, with a clean tree, and includes this
   sheet's commits. `scripts/m12_09_clip_compare.py --help` lists
   `--phase`. `python -m pytest -q` passes. `src/sensing/audio.py` has
   `DIAG_REF_DELAY_S`.
2. No dashboard process is running, and nothing listens on :8000
   (FIELD-NOTES 2026-09-06).
3. `.env` has no `RTR_PLAYBACK_REFERENCE_*`, `RTR_PLAYBACK_CANCEL_*` or
   `RTR_DEBUG_*` lines. `RTR_HEADCOUNT_RESCUE_ENABLED` is unset or 0.
4. `scripts/record_frames.py --help` runs. Free disk space ≥ 1 GB.
5. Claude fills in `<DATE>` below.

## C. Fixed conditions (founder; as 2026-11-20 Block G and 2026-11-21)

Copy into the FIELD-NOTES entry, filled in.

| setting | value |
|---|---|
| Room / spot | the living-room alcove, laptop on the TV stand in the usual spot, TV off |
| People | **4–5 in all**, the same people throughout; names or initials and the count. If more turn up, run anyway at 32 % and note the count (less comparable) |
| Seating | an arc, everyone at **~1.5 m** from the laptop (estimate; note the arrangement) |
| Windows volume | 32 % (66 % in G4) |
| Mic input | 34 % (check it) |
| Dolby Atmos | on |
| Spotify | 100 %, Normalize on, EQ flat |
| Do Not Disturb | on: the reference records every sound the laptop plays |
| Other apps | **closed** (browser except the dashboard tab, Discord, Teams, OneDrive). Note anything left open: load is a suspect |
| Power | plugged in |

## D. What to tell the guests (before starting)

> "I'm building a system that listens to a room and picks music for it.
> For the next half hour or so my laptop will play music and record the
> room, our voices included, so I can analyse it and improve the system.
> The recording stays on this laptop. It's analysed as numbers (how much
> music the system removed, how many voices), never transcribed or shared,
> and I keep it while I'm building. Is everyone OK with that?"

Everyone present must agree. If someone new arrives, tell them, and note
the time. If anyone objects, run the no-recording variant (section E).

**How to talk:** natural conversation, the same kind throughout, as in
Block G. Nobody reads aloud or performs. Don't raise your voices over the
music on purpose, and don't hold back for it either.

## E. Start (founder; separate PowerShell windows, not Claude's `!`)

**Window 1, the dashboard:**

```powershell
cd C:\dev\read-the-room
$env:RTR_PLAYBACK_REFERENCE_ENABLED = "1"
$env:RTR_PLAYBACK_CANCEL_ENABLED = "1"
$env:RTR_DEBUG_CANCEL_CLIP_S = "660"
cmd /c ".venv\Scripts\read-the-room-dashboard.exe > data\sessions\live-<DATE>-m12-09-group.log 2>&1"
```

**No-recording variant:** use the same commands but set
`$env:RTR_DEBUG_CANCEL_CLIP_S = "0"`, and skip window 2.

Open http://127.0.0.1:8000. **Tell Claude it's up** so Claude can confirm
the serving process before anything else.

**Window 2, the frame recorder** (once Claude has confirmed the process):

```powershell
cd C:\dev\read-the-room
.venv\Scripts\python.exe scripts\record_frames.py --out data\sessions\live-<DATE>-m12-09-group.frames.jsonl
```

**Optional window 3**, to watch the log:
`Get-Content data\sessions\live-<DATE>-m12-09-group.log -Wait -Tail 5`.
A `diagnostic clip: recording 660 s` line appears about 40–60 s after the
music starts.

## F. Phases (founder; note each start to the second if you can)

Everyone is seated and talking before the music starts.

| phase | music | room | minutes (from your pick) | what it's for |
|---|---|---|---|---|
| G1 | **genre pick Hip-Hop / mid**, Windows 32 % | **conversation** | 0–5 | The 11-21 condition. The canceller locks about 10–30 s in, and the clip starts about 30 s later |
| G2 | 32 % (let it run) | **everyone silent** | 5–8 | Music only: is the clean ever louder than the mic, and what's the music measure with no talk? |
| G3 | 32 % | conversation | 8–12 | Talk again, after a stretch of silence. The clip ends around here |
| G4 | Windows **66 %** | conversation | 12–16 | Level; log lines and frames only. **Optional:** skip it if the guests need to go |

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

**Data.** The clip, run through `scripts/m12_09_clip_compare.py CLIP
--phase G1=… --phase G2=… --phase G3=…`. That gives, per phase: settled
5 s windows, live and offline energy reduction, live and offline music
reduction, and the share of windows where the live clean is louder than
the mic. Also the 10 s log lines and the frames, per phase.

**Tolerance: 1.2 dB,** the spread of offline energy reduction across the
three 11-20 group takes at 32 % (FIELD-NOTES 2026-11-21). It is the
nearest measured run-to-run variance for this room and condition. It is
applied only to same-window comparisons (live vs offline on identical
audio).

**Talk windows** are G1's and G3's settled windows in the clip. At least
10 are required; otherwise the session is inconclusive.

**Readings.** R1 and R2/R4 exclude each other; R3 and R5 can hold
alongside any of them.
- **R1, the live path is at fault:** over the talk windows, the offline
  music reduction exceeds the live one by more than 1.2 dB (medians).
  Next: the live canceller's state and alignment (for example, 11-23
  finding 2's filter carried through "shift" re-locks).
- **R2, this group's talk defeats any canceller:** live is within 1.2 dB
  of offline (or above it), **and** the offline music reduction over the
  talk windows is below **12.4 dB**. That is 1.2 dB under the lowest
  11-20 group take (13.6 dB, G-MX-J32, measured 2026-11-23 with the same
  script). Next: a fix proposal for talk (double-talk control, step size,
  post-filter).
- **R4, not reproduced:** live is within 1.2 dB of offline (or above it),
  **and** the offline music reduction over the talk windows is ≥ 12.4 dB.
  The live canceller removes music under group talk about as well as
  offline does on 11-20's group. So 11-21's near-zero frame numbers
  reflected the talk-capped measure (2.2–3.6 dB is what offline reads
  under talk) plus whatever differed that night. Next: check 11-21's
  differences (5 people, load, the "clean louder" frames) against this
  session's frames.
- **R3, reference slips:** inside the clip there are re-locks or lost
  audio (`lost=+N ms`, N > 0) in the live reports, or lag changes in the
  offline timeline. Record the data-discontinuity count too.
- **R5, the canceller adds energy:** the live clean is louder than the mic
  in more than 10 % of the talk windows. Talk can't cause that, so it
  points at the live filter. Offline `cancel()` on the 11-20 group takes
  showed it in 0–7 % of windows (FIELD-NOTES 2026-11-21), and 11-21's
  frames 31 %.
  G2's share is reported too.
- **Inconclusive:** no clip written (crash, or a resync that discarded it
  and never re-recorded); the script warns about zero-fill; fewer than 10
  talk windows; or the no-recording variant. Recorded as inconclusive,
  never as a reading.

**Reported, not decided on:**
- energy reduction per phase (log lines and frames), for comparison with
  11-21;
- G1 vs G3 (different songs, so ±5.5 dB from the music alone);
- G4;
- headcount buckets against the known N (M12-07 evidence under group talk
  with cancellation on).

## H. Stop and afterwards

**Stop.** `Ctrl+C` in window 2 (the frame recorder), then in window 1
(the dashboard). Answer `Y` to "Terminate batch job?". Tell Claude it's
done, with the notes from F. **Don't paste the log.**

**Analysis (Claude):**
1. From the log: the canceller lines per phase; "diagnostic clip
   written" (or "discarded"); the data-discontinuity count.
2. `python scripts/m12_09_clip_compare.py data/captures/live-clip-<stamp>
   --phase G1=… --phase G2=… --phase G3=…`. If the script warns about
   zero-fill, the comparison is void (inconclusive).
3. Per-phase frame numbers: raw − clean and clean-louder share (the 11-21
   measures), and the headcount buckets.
4. The FIELD-NOTES entry: the conditions, the occupancy log, the measures
   per phase, section G worked line by line, and the readings that hold.
   Also the ROADMAP M12-09 note.
5. **The clip, the log and the frames are kept** under `data/` for
   re-analysis (CLAUDE.md, recording policy). They are never committed.
6. Then the founder's decision: the M12-09 fix proposal on whichever
   reading holds. `cancel-on-by-default` stays parked until a fix is
   re-checked live.

## I. Hard don'ts

- **Claude never starts** the dashboard, the frame recorder or a capture.
- **No recording unless everyone present has been told** and nobody
  objects.
- **No mid-session tuning.** If something looks wrong, note it and carry
  on. Section G decides afterwards.
- **Never enable the headcount rescue.**
- **Don't touch Spotify after the G1 pick** (M9-08).
