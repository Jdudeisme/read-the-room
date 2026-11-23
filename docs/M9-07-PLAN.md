# M9-07 — the next-up slot survives a substituted track ID — plan

**Status:** plan and D1–D4 approved by the founder, 2026-11-21. The diff
was approved the same day: 443 tests pass, and the 9 new tests fail on the
previous code. The live check (below) is still to run.

**What this is.** The fix for ROADMAP M9-07 (FIELD-NOTES 2026-11-21, night,
finding 2). On that night, RTR pushed one track to Spotify, and Spotify
played it under a different ID:
- requested `…3cwGyOXYErgvc4XMRYdM9m`, played as `…0zhDLBmgNKFejGUkyqMsaQ`;
- same title (*Clarinet Concerto … Rondo*), same duration (556.493 s).

`PlaybackController` only clears its pushed slot (`_queued`) when the
playing ID equals the pushed ID (`took_over` in `_observe`). So the slot
stayed full, and `_observe` never pushed again: **one push in the session,
against ~30 selections.** The dashboard's next-up (`_intent or _queued`)
kept changing, so the DJ looked alive while Spotify just ran on through
the last genre pick's playlist. Six out of six track changes from 01:47 to
02:03 ignored the shown next-up, and the founder confirmed one live.

The same substitution already has a logged consequence on the start path.
`_observe` logs "started …, but the provider reports id …" and gives up on
attribution: "this track cannot earn a played_through (FIELD-NOTES
2026-09-30 open item)". This plan fixes both with one notion of "the same
track".

## The change

1. **One identity test, `_same_track(requested, observed)`**
   (`src/playback/controller.py`). True when any of these holds:
   - **(a)** the IDs are equal;
   - **(b)** the observed track says it was **linked from** the requested
     ID (D1);
   - **(c)** the titles are equal and the durations are within 2 s. That's
     the fallback for substitutions the provider doesn't explain. The 2 s
     is a tolerance on "same recording", not a calibration: tonight's pair
     matched to the millisecond, and two different tracks sharing an exact
     title within 2 s inside one session is not a realistic collision.

   It is used in `took_over` (the pushed slot crosses the boundary) and in
   start verification (a play counts as substituted-but-started).
2. **`linked_from` from Spotify (D1)** (`src/playback/spotify.py`).
   `GET /me/player` gains `market=from_token`, so Spotify applies track
   relinking and reports the original URI in `item.linked_from.uri` when it
   substitutes. `Track` gains `linked_from: str | None = None`. This is an
   additive field: `to_dict` carries it, and override records gain a key
   without a schema bump (invariant 9: additive only). `None` everywhere a
   provider doesn't report one, so FakeProvider and every existing record
   are unchanged.
3. **Attribution follows the substitution (D3).** When `_same_track` matches
   a substituted ID, at start or at the slot crossing, the attribution and
   override marks keyed by the requested ID are copied to the observed ID.
   A substituted track can then earn its `played_through` weak positive,
   which closes the 2026-09-30 open item. The log line stays, as INFO.
4. **Backstop: a pushed slot can't outlive two boundaries (D2).** The slot
   remembers the boundary count at push time. If the pushed track hasn't
   been matched after **2** boundaries, the slot clears with a WARNING
   ("pushed track never observed; assuming it was consumed or dropped").
   Pushing resumes at the next near-end.

   Why 2: Spotify plays its queue at the first boundary after the push, so
   one boundary is the normal case. The second tolerates one human change
   in between (a genre pick, or a skip in the Spotify app) while Spotify's
   queue still holds the pushed track. Clearing at the first boundary would
   risk the stale pile-up the controller docstring warns about. It is a
   policy bound, not a measured constant, and is stated at its definition.
5. **Held vs pushed on the dashboard (D4).** `snapshot()` adds
   `queued_state: "held" | "pushed" | None` beside `queued_track`. The frame
   gains an additive field: the bridge passes the snapshot through, and its
   shadow-mode default (`DashboardBridge._playback_extras`) gains
   `queued_state: None` to match. The status line (`static/index.html`) shows
   `next: <title> (held)` or `next: <title> (queued in Spotify)`, so the
   operator can see whether RTR's pick is actually lined up.

## Tests (offline; CLAUDE.md: fakes at the policy seam, transport at Spotify)

- **The 2026-11-21 case**, in `TestBoundaryWindow`: push the next-up; at the
  boundary, the provider reports the pushed track under a substituted ID
  (same title and duration). The slot clears, and the next held selection
  is pushed at the following near-end. **Fails on today's code.**
- **The linked-from case:** the same test, with a different title but
  `linked_from` = the pushed ID. The slot clears.
- **No false match:** a different title at a similar duration, or the same
  title more than 2 s apart, does not match.
- **The backstop:** the pushed track never appears across two boundaries.
  The slot clears with a warning, and pushing resumes. After only one
  boundary it is still held (the human-override tolerance).
- **Attribution:** a substituted start (the existing `SubstitutingProvider`)
  that plays through now emits `played_through` with the requested
  track's recommendation. The existing test that pins "no played_through
  for a substituted track", if there is one, is updated deliberately and
  visibly, citing this plan. There's no such pin today:
  `test_substituted_id_is_not_a_failure` only checks the log line, which
  stays.
- **Spotify transport** (`tests/test_spotify.py`, `httpx.MockTransport`):
  `GET /me/player` is sent with `market=from_token`, and an item carrying
  `linked_from` parses into `Track.linked_from`. Without it, `None`.
- **Snapshot:** `queued_state` reads `held` before the push, `pushed` after,
  and `None` after the crossing.
- The existing controller, boundary, override and start-verification
  tests stay green unchanged.

## What this does NOT change

- **The gentle-DJ policy:** still one outstanding push, still only inside
  the boundary window, never an eager push, never a replace (Spotify's
  queue is append-only). This makes the existing rule work under
  substitution; it doesn't loosen it.
- **Sensing, the mapper, the engine:** untouched. No benchmark row (the
  change isn't on the engine path), and no headcount replay impact.
- **Shadow mode:** the backstop and matching never raise. Provider errors
  degrade exactly as today.

## Decisions for the founder

- **D1. Use Spotify's `linked_from`** (`market=from_token` on `GET
  /me/player`). **Recommended**, with (c) as the fallback. Spotify documents
  track relinking with `linked_from`, but whether the desktop client's
  substitution tonight was relinking is unverified. The live check (below)
  records which branch matched.
- **D2. The backstop at 2 boundaries.** **Recommended.**
- **D3. Let substituted tracks earn `played_through`.** **Recommended.** It's
  a label-semantics change: more weak positives land in
  `data/overrides/`, each for the track RTR actually selected. No schema
  bump; the record's `now_playing.track.id` is the observed ID, and its
  attribution carries the requested selection.
- **D4. Held vs pushed on the dashboard.** **Recommended.** It's the
  difference the founder couldn't see on 2026-11-21.

## Open observation, not fixed here

On 2026-11-21 the pushed Clarinet Concerto played **a second time**, after
*Dai Dai* ended (~01:29:41), although its first play had already started at
~01:25. With the slot jammed, RTR pushed nothing in between, so this replay
came from Spotify's side (its queue, or the client's context handling after
the 01:25:53 genre pick). This plan can't explain or prevent it. The live
check watches for a repeat.

## Live check (founder; can share the M12-09 session)

During normal use, pick **one named next-up** shown as `(queued in
Spotify)` and watch it across the boundary, three times. Each should
play. Also note any WARNING backstop line and which identity branch
matched (logged at INFO). Recorded in FIELD-NOTES. If M9-07 merges before
the M12-09 session, that session can carry this check too, after
`m12-09-diagnostics` is brought up to date with `main`.

## Verification (Claude)

- `python -m pytest` green, offline, fast.
- `git diff` touches only `src/playback/{controller,spotify,provider}.py`,
  `src/dashboard/{bridge.py,static/index.html}`, `tests/test_playback.py`,
  `tests/test_spotify.py`, and this plan's status line.
- Commit: `M9-07 playback: the next-up slot survives a substituted track ID (linked_from + title/duration match, 2-boundary backstop); substituted plays earn played_through`.
