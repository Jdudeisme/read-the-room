# Live session — M9-07 focused re-check: does the next-up shown play next? — run sheet

**Why.** On 2026-11-23 the founder confirmed two of three next-ups
(*TWIST & TURN* under a substituted ID, *Healing*). The third, *It's Up*, was
only ever held, never pushed. The founder was focused on volume changes and
reading at 1.5 m and is unsure what the line showed (FIELD-NOTES
2026-11-23). This session has **one job: watch the next-up line.** It
satisfies the live-check half of ROADMAP M9-07's acceptance criteria ("watching
one named next-up title across a boundary, records the match in
FIELD-NOTES"), this time several boundaries in a row, with attention on the line.

**Branch:** `m9-07-queue-slot` (`de02fc4`), the feature branch itself. No
test branch is needed: cancellation and the M12-09 diagnostics aren't part
of this check, and `.env` leaves them off. **Founder-run, founder alone**
(nothing room-derived is recorded beyond the usual corpus taps). About 25
minutes.

## How the next-up line works (what you're watching)

The dashboard shows `next: <title> (held)` or `next: <title> (queued in Spotify)`.
- **`(held)`:** RTR's current pick, kept locally. It can change every
  ~30 s as the room changes. That's normal, and nothing is in Spotify yet.
- **`(queued in Spotify)`:** RTR pushed it into Spotify's queue. That
  happens only in the **last ~15 s** of the playing song (`queue_lead_s`
  = 15 s, polled every 5 s). From then on it should not change, and it
  should be the next song.

So the moment to look is **the last ~20 s of each song**. Use Spotify's
progress bar to see when a song is ending. Optional independent check:
open Spotify's **Queue** panel. The pushed track should appear under "Next
in queue" at the same moment.

## A. Preflight (Claude, at the start of the session)

1. The main checkout is on `m9-07-queue-slot`, with a clean tree.
   `python -m pytest -q` passes.
2. No dashboard process is running, and nothing listens on :8000
   (FIELD-NOTES 2026-09-06).
3. `.env` has no `RTR_PLAYBACK_REFERENCE_*`, `RTR_PLAYBACK_CANCEL_*` or
   `RTR_DEBUG_*` lines, and none are set in the window's environment.
   `RTR_PLAYBACK_QUEUE_LEAD_S` is unset (default 15 s).
4. Claude fills in `<DATE>` below.

## B. Conditions (founder)

| setting | value |
|---|---|
| People | **founder alone** |
| Position | wherever you can **comfortably read the dashboard**. Distance doesn't matter for this check; note it roughly |
| Talking material | printed text or a phone, **not** the laptop screen. The laptop shows the dashboard |
| Windows volume | 32 %, unchanged all session |
| Spotify | 100 %, Normalize on. **Don't pause, skip or pick songs in Spotify after the start** (a pause doesn't hold: ROADMAP M9-08; a skip makes a boundary RTR didn't cause) |
| Do Not Disturb | on; other apps closed |

## C. Start (founder, in a new PowerShell window, not Claude's `!`)

```powershell
cd C:\dev\read-the-room
cmd /c ".venv\Scripts\read-the-room-dashboard.exe > data\sessions\live-<DATE>-m9-07.log 2>&1"
```

Open http://127.0.0.1:8000 and tell Claude it's up, so Claude can confirm
the serving process. Start talking or reading aloud right away. RTR needs
speech to make selections.
- **Music:** once the models are ready and you're talking, RTR starts a
  song by itself (bootstrap). If nothing has started after ~2 minutes of
  talking, use the dashboard's genre pick → "play it" **once**, and note
  the time.

## D. The check (founder; keep talking throughout)

Keep talking or reading the whole time, so RTR keeps selecting. For **each
song change** (aim for **5**, and stop after ~25 min even if fewer):

1. In the song's last ~20 s, write down the next-up line **exactly as
   shown**, title and suffix: `(held)` or `(queued in Spotify)`.
2. When the next song starts, write down its title.
3. Note the clock time to the minute.

A notes template:

| # | time | line in the last ~20 s (title + suffix) | song that played next | same? |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |

Also note anything odd: the suffix flipping back to `(held)`, a song
starting that the line never showed, notifications, someone walking in.

## E. Stop (founder)

`Ctrl+C` in the dashboard window (answer `Y` to "Terminate batch job?").
Tell Claude it's done, with the table. **Don't paste the log.**

## F. Analysis (Claude)

Claude checks each row against the log:
- `selected … -> next-up`;
- `pushed next-up … inside the boundary window`;
- `next-up … crossed the boundary as … (matched by …)` (logged only for a
  substituted ID; a same-ID crossing is silent);
- any `pushed track … never observed after 2 boundaries` warning;
- `played_through` records in `data/overrides/<DATE>.jsonl`.

**Pre-registered reading** (written before the session):
- **Pass:** at every song change the founder observed, the title shown
  `(queued in Spotify)` in the last ~20 s is the song that played next,
  matching the log's push. There is a push at every song change while
  the founder was talking: the DJ never stalls, which is the original
  M9-07 failure (one push, then none). There's no "never observed"
  warning, and at least 3 changes are observed.
- **Fail:** a `(queued in Spotify)` title that didn't play next; a song
  change during talking with no push and no logged reason; or a "never
  observed" warning. A fail is diagnosed before any merge.
- **Inconclusive:** fewer than 3 observed changes, or a change where the
  line wasn't seen. Inconclusive is recorded as such, not as a pass.

Claude writes the FIELD-NOTES entry (the table, the log lines per row,
the reading). Then the founder decides on merging `m9-07-queue-slot` to `main`.
