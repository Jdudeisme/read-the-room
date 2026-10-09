# Milestone 8 Test Plan — Trust the engine offline

Logic-level tests are the point of M8: `tests/test_engine.py` (the tick
contract), `tests/test_music_aware_characterization.py` (the M8-01 proof,
now carrying M8-03), `tests/test_audio.py` (resampler, source shutdown),
and the additions to `tests/test_spotify.py` and `tests/test_headcount.py`.
No mic, no models, no network. This plan covers the gate, verbatim from
`ROADMAP.md` § M8:

> (a) `pytest` green with a new `tests/test_engine.py` (or equivalent) suite
> covering the enumerated scenarios below, all runnable offline in seconds
> with no models; (b) `bench_headcount.py --fallback` re-run on the
> reference machine (JPad) lands within run-to-run variance of the last
> reference-machine README row, not the historical Mac rows (README
> convention — refactors add no compute); (c) a 10-minute live dashboard
> smoke on JPad shows corrections, banking, and headcount behaving as before
> (statuses ready, "hearing through music" chip fires during a playback
> test, no exceptions in the log).

M8-03's charter adds one live criterion to (c): during a playback session
with a track change, frames never show `emotion_correction.track_id` ≠ the
track that was playing when the reading landed.

> **For Claude Code on JPad:** parts (a) and (b) are yours. They are
> offline: no mic, nobody in the room. Part (c) is **[HUMAN]**: the founder
> runs it, and Claude never opens the mic. Claude analyzes its log and
> frames afterward. Results go in a dated `docs/FIELD-NOTES.md` entry and
> a README gate row. **No tuning in-session.** If a check fails, stop and
> diagnose.

## Part 0 — setup

```powershell
cd C:\dev\read-the-room
git fetch; git checkout milestone-8-trust-engine; git pull
.venv\Scripts\python.exe -m pytest -q      # expect: 378 passed
```

`.env` is used as it stands. Record it, don't change it. Values that
matter here: `RTR_MUSIC_AWARE_ENABLED=1`,
`RTR_MUSIC_DOMINANCE_LO/HI=0.022/0.050` (PROVISIONAL),
`RTR_VAD_PLAYBACK_THRESHOLD=0.75`, `RTR_HEADCOUNT_MIN_INTERVAL_S=2.0`,
rescue unset (off), `RTR_INPUT_DEVICE=Microphone Array on SoundWire D`.

Fixed conditions (record each one, per the founder's assessment variables):
room size (people, room), Windows output volume (**32 %**, the small-room
level; one person is enough for this gate), Spotify 100 %, Dolby Atmos
**on**, Windows mic input level (**34 %** in M7 part (f)).

## Part (a) — offline suite [Claude]

`pytest -q` on the branch: green, offline, in seconds, with no models
downloaded. Also confirm that `tests/test_engine.py` holds one test per
M8-02 acceptance bullet (dedup, freshness, clean vs mixed, stale baseline,
basis order, discount floor, track boundary, playback stop, consumer
isolation, publish path).

## Part (b) — benchmark regression row [Claude, offline]

Nothing else may be running: no dashboard, no replay, no browser playing
audio. Run the benchmark on the same session's `main` and on the branch,
so run-to-run variance is measured, not assumed:

```powershell
# main, in a scratch worktree (its own src is what the script imports)
git worktree add --detach <scratch>\m8-bench-main main
cd <scratch>\m8-bench-main
C:\dev\read-the-room\.venv\Scripts\python.exe scripts\bench_headcount.py --fallback
# branch
cd C:\dev\read-the-room
.venv\Scripts\python.exe scripts\bench_headcount.py --fallback
```

Run each twice, alternating main, branch, main, branch. **Pass:** the
branch rows sit within the spread of the main rows, and within run-to-run
variance of the README JPad row (2026-09-06, `--fallback`: headcount on
contended hops 0.23 s mean / 0.25 s p95; emotion overall 0.35 / 0.39). The
budget verdicts stay PASS (< 1.66 s headcount p95, < 1.2 s emotion). Add a
README results row dated with this run.

## Part (c) — 10-minute live smoke [HUMAN, founder on JPad]

One person (the founder) is enough. This checks wiring, not headcount.

**Before starting**, close any other dashboard (a stale process once held
the port; FIELD-NOTES 2026-09-06). Open Spotify on JPad. Then, in its own
PowerShell window (not Claude Code's `!`, which kills it at 30 min):

```powershell
cd C:\dev\read-the-room
cmd /c ".venv\Scripts\read-the-room-dashboard.exe > data\sessions\m8-gate-<DATE>.log 2>&1"
```

The terminal stays quiet; the log goes to the file. Open
http://127.0.0.1:8000 once the log shows uvicorn running (the banner
prints before uvicorn binds).

**Frame capture** (check 6, M8-03; founder-approved 2026-10-09). Once the
dashboard is up, start the recorder in a **second** PowerShell window:

```powershell
cd C:\dev\read-the-room
.venv\Scripts\python.exe scripts\record_frames.py --out data\sessions\m8-gate-<DATE>.frames.jsonl
```

It prints one line and stays quiet. Stop it with Ctrl+C after the
dashboard is stopped, or let it exit when the dashboard closes.

| Minute | Do | Watch for |
|---|---|---|
| 0–1 | Wait; say something | emotion and headcount statuses reach `ready`; loudness readout moves when you talk |
| 1–3 | Talk normally, **no music** | emotion values update; headcount reads `solo`; no correction shown |
| 3–6 | Press play in Spotify on JPad if the DJ hasn't started a track; keep talking | the **"hearing through music"** chip appears; note the time |
| 6–7 | **Skip** to the next track while talking (dashboard button) | note the time of the skip |
| 7–9 | Keep talking over the new track | chip stays; corrections continue |
| 9–10 | **Pause** in the Spotify app (the dashboard has no pause button) while talking; talk 30 s more | note the time; the chip clears within one 5 s playback poll |
| 10 | Stop the dashboard with **Ctrl+C** in its window | it exits without a traceback |

Write down the times for minutes 3, 6 and 9, and any Good/Wrong taps.

### Claude's checks afterward (from the log and frames)

1. **No exceptions** in `data\sessions\m8-gate-<DATE>.log`: no `Traceback`,
   no `ERROR` except the known Windows `ConnectionResetError`
   (WinError 10054) from a closed browser tab, which is logged by asyncio
   and harmless (FIELD-NOTES 2026-10-08).
2. **Clean shutdown** (M8-07): the log ends without a traceback after
   Ctrl+C, and the signature file's mtime is from the shutdown.
3. **Banking**: the track-signature file gains pull references for the
   tracks played (compare the `pull_refs` counts before and after).
4. **Corrections**: during playback, override records (if tapped) show
   `emotion_correction` with a `basis`; with frames, `basis` and `refs`
   are visible per frame.
5. **Headcount** reads `solo` for one person, with no exception paths.
6. **M8-03** (frames only): at each frame where `emotion_staleness_s` drops
   (a new reading landed), note `playback_track_id`. On every later frame
   until the next drop, `emotion_correction.track_id` must equal it, across
   the skip and after the pause.

**Pass:** (a) and (b) pass, and part (c) checks 1–5 hold. Check 6 is
passed only if measured.

## Frame capture — `scripts/record_frames.py` (approved 2026-10-09)

A read-only websocket client for `ws://127.0.0.1:8000/ws`. It appends
`type: "state"` frames to a JSONL file under `data/sessions/` (gitignored),
with no audio, and runs only when a person starts it. Claude never starts
it during a live session. `--check FILE` runs check 6 offline:

```powershell
.venv\Scripts\python.exe scripts\record_frames.py --check data\sessions\m8-gate-<DATE>.frames.jsonl
```

It reports PASS/FAIL and how many corrected frames crossed a track
boundary or outlived a playback stop. If both are 0, the run didn't
exercise M8-03, and check 6 counts as **not measured**.
