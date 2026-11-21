# Live session — M12-09 diagnostics + M9-07 next-up check — run sheet

**Branch:** `live-session-m12-09-m9-07`, which is `main` (`77646fa`) plus
`m12-09-diagnostics` (`97fc9fb`) plus `m9-07-queue-slot` (`de02fc4`); 447
tests pass. **Founder-run, founder alone.** The diagnostic clip records
room audio, so nobody else is in the room (`docs/M12-09-PLAN.md` D2).
Claude never starts a capture. About 25 minutes, plus Claude's preflight
and analysis.

**What it answers.**
1. **M12-09:** why the live canceller removed ~0 dB on 2026-11-21. The new
   10 s log lines show lock, lag, re-locks, live reduction and the
   reference's lost audio. The 60 s clip lets Claude run offline `cancel()`
   on the exact audio the live canceller saw.
2. **M9-07:** whether the next-up shown as `(queued in Spotify)` is the
   track that actually plays.

## A. Preflight (Claude, at the start of the session)

1. The main checkout is on `live-session-m12-09-m9-07`, with a clean tree.
   `python -m pytest -q` passes (447+).
2. No dashboard process is running and nothing listens on :8000
   (FIELD-NOTES 2026-09-06: a stale process held the port).
3. `.env` has no `RTR_PLAYBACK_REFERENCE_*`, `RTR_PLAYBACK_CANCEL_*` or
   `RTR_DEBUG_*` lines, so the window's environment variables decide
   (precedence: default, then `.env`, then environment).
4. Claude fills in `<DATE>` in the commands below.

## B. Fixed conditions (founder; same as gate (b) and 2026-11-21)

| setting | value |
|---|---|
| Room / spot | living-room alcove, laptop on the TV stand, the same spot; TV off |
| People | **founder alone** |
| Windows volume | 32 % (66 % in phase C) |
| Mic input | 34 % (check: it was found at 68 % on 2026-11-20) |
| Dolby Atmos | on |
| Spotify | 100 %, Normalize on, EQ flat |
| Do Not Disturb | on |
| Other apps | **closed** (browser, Discord, Teams, OneDrive). Load caused the 2026-11-20 G-MX-I76 dropouts. Note anything left open |
| Power | plugged in |

## C. Start (founder, in a new PowerShell window, not Claude's `!`)

```powershell
cd C:\dev\read-the-room
$env:RTR_PLAYBACK_REFERENCE_ENABLED = "1"
$env:RTR_PLAYBACK_CANCEL_ENABLED = "1"
$env:RTR_DEBUG_CANCEL_CLIP_S = "60"
cmd /c ".venv\Scripts\read-the-room-dashboard.exe > data\sessions\live-<DATE>.log 2>&1"
```

The window stays blank: the log goes to the file. To watch it live, open a
**second** PowerShell window:

```powershell
cd C:\dev\read-the-room
Get-Content data\sessions\live-<DATE>.log -Wait -Tail 5
```

A `canceller: …` line should appear every 10 s once the dashboard runs.
Open the dashboard at http://127.0.0.1:8000.

## D. Phases (founder; note the clock time at each start)

| phase | music | you | minutes | note |
|---|---|---|---|---|
| A | genre pick to start, Windows **32 %** | **silent** (leave or sit still) | 5 | The clip records 60 s here (30 s after the first lock): music only, the cleanest comparison |
| B | 32 % | **reading aloud** at your seat | 5 | Speech lets RTR make selections, so the M9-07 check can start |
| C | Windows **66 %** | reading aloud | 5 | |
| D | **paused** in Spotify | reading aloud | 2 | Control: the canceller should pass through, and lost audio should keep being counted |

**M9-07, during B and C (do three):** when the dashboard's line shows
`next: <title> (queued in Spotify)`, write the title down and watch the
song change. Did that title play? Also note any next-up that only ever
shows `(held)` through a song change.

**Write down:** each phase's start time; the three next-up titles and
yes/no for each; anything odd (notifications, someone walking in, apps
opening).

## E. Stop (founder)

`Ctrl+C` in the dashboard window. If `cmd` asks "Terminate batch job?",
answer `Y`. Then tell Claude it's done, with the notes from D. **Don't
paste the log**: it's in `data\sessions\live-<DATE>.log`.

## F. Analysis (Claude)

1. From the log: the `canceller:` lines per phase (lock, lag, re-locks,
   reduction, lost audio); the "diagnostic clip written" path; M9-07 lines
   ("crossed the boundary … matched by …", any "never observed" warning,
   "pushed next-up").
2. `python scripts/m12_09_clip_compare.py data/captures/live-clip-<stamp>`:
   live vs offline reduction on the same 60 s, the offline lag timeline,
   and the live reports.
3. FIELD-NOTES entry: the conditions, per-phase numbers, which M12-09
   hypothesis the evidence supports (reference dropouts, live
   misalignment, double talk or level, or something else), and the M9-07
   results.
4. **Delete the clip** (`data/captures/live-clip-*`) once the entry is
   written, as `docs/M12-09-PLAN.md` promises.
5. Then the founder's decisions:
   - **M9-07:** merge `m9-07-queue-slot` to `main` if the three next-ups
     played;
   - **M12-09:** the fix proposal;
   - **`cancel-on-by-default`:** stays parked until the fix is re-checked.

The test branch itself is never merged. Each feature branch merges on its
own.
