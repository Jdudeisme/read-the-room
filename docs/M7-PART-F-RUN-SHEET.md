# M7 part (f) — run sheet (30-min live DJ regression sweep)

**Status.** Part (f) is the **only** part left before
`milestone-7-stable-middle` merges. Parts 0/(a)/(b)/(d)/(e-diff) passed
2026-07-12; part (c) closed 2026-08-09 as a *defensible pass under the
revised charter*; part (e-live) passed the same night. The authoritative
plan is `docs/M7-TEST-PLAN.md` **on the M7 branch** (not on `main`) —
this sheet is the execution wrapper for it, written for a 4–6 person
gathering.

**Why a gathering.** Part (f) reads "solo or whoever's still around", but
its headline checkpoint is that `matched_cell` shows `("3", …)` /
`("6", …)`. Those rungs only exist above 2 occupants, so a solo evening
cannot demonstrate the thing part (f) exists to prove. **4–6 people is
the right occupancy; 6 is the rarer rung.**

---

## A. Decide before the night (founder)

1. **Which machine.** `docs/MACHINE-DOCTRINE-REVISION.md` says M7's
   criteria "should be judged" under the Mac budget, and the test plan is
   written "for Claude Code on the Mac". But part (f)'s four checkpoints
   are **behavioral, not performance** — the budget-sensitive parts
   ((a) bench, (b) pytest) already passed on the Mac on 2026-07-12.
   **Recommendation: run on JPad.** Two concrete reasons: the full
   playback stack is proven working there (2026-09-06 evening, three
   hours, zero provider errors), and the Mac's `.env` sets
   `RTR_HEADCOUNT_MIN_INTERVAL_S=4.0` against a default of 2.0, which
   **fails Part 0's own "no leftover `RTR_HEADCOUNT_*` overrides" check**
   and would have to be reconciled first. Founder's call — but make it
   before, not after.
2. **Recording: optional, and consent-gated.** Part (f) does **not**
   require recorded audio (part (c) did). If you want a WAV for offline
   replay, **tell your guests and get their agreement first** — this is a
   new capture of room-derived data and the repo's default for those is
   off. `scripts/capture_room_wav.py` lives on `main`, **not** on the M7
   branch; do not add it to the branch. Use an external recorder
   (QuickTime, or `sox -d m7-partf.wav`), 16 kHz mono if you want
   `scripts/m7_replay_session.py` to read it, and note the start
   wall-clock time.
3. **Skim the new rulebook rows** (`src/mapping/rulebook.py` on the M7
   branch): rung `3` seeds from the 2020 "small" column, `6` from
   "medium". Part 0 warns that a bad seed cell shows up as **odd DJ picks
   during part (f), not as a gate failure** — so if a pick feels wrong,
   check the cell before blaming the milestone.

---

## B. Preflight — Claude runs this, before guests arrive

Allow ~15 minutes. Every step is offline except the Spotify check.

```bash
# 1. Land main's working tree first — the M7 branch has its own
#    FIELD-NOTES.md and an uncommitted entry will collide on checkout.
cd /c/dev/read-the-room
git status --short                # expect: clean, or commit/stash first

# 2. Switch to the branch under test. data/, .env and the token cache are
#    gitignored, so corpus, playlists and Spotify auth all survive.
git checkout milestone-7-stable-middle

# 3. Suite must be green on the branch, not on main.
.venv/Scripts/python.exe -m pytest -q      # expect: 287+ passed

# 4. Part 0's precondition: headcount config must equal the DEFAULTS.
grep "^RTR_HEADCOUNT" .env
```

**Verified on JPad 2026-09-06:** all nine `RTR_HEADCOUNT_*` values in
`.env` are byte-identical to the M7 branch defaults, and
`RTR_HEADCOUNT_RESCUE_ENABLED` / `_RESCUE_MARGIN` are unset, so the
shipped defaults apply (`enabled=False`, `margin=0.80`). **Re-check
anyway** — if `.env` has drifted, fix `.env`, never `config.py`.

```bash
# 5. Rescue MUST be off. Non-negotiable: it was disproven 2026-07-15.
grep -i "RESCUE" .env || echo "unset -> default off, correct"

# 6. Real playlist mapping (NOT data/playlists-inert.json, which part (c)
#    used). Confirm every mapped cell still resolves, and that Spotify
#    auth is alive — the token cache expires silently.
grep RTR_PLAYBACK_PLAYLISTS_PATH .env
grep RTR_PLAYBACK_DEVICE_NAME .env
```

Then run the Spotify preflight (token refresh + device list + per-cell
track counts). It needs `inject_os_truststore()` first or it dies on
`CERTIFICATE_VERIFY_FAILED` behind TLS interception.

```bash
# 7. Corpus reads back cleanly BEFORE the session, so a post-session
#    failure is attributable to the session.
.venv/Scripts/python.exe scripts/tuning_report.py | tail -20
```

**Stop and diagnose if:** pytest is not green, any `RTR_HEADCOUNT_*`
differs from default, rescue is enabled, or the tuning report errors.

---

## C. Room setup [HUMAN]

- **Laptop in the middle of the group, close range.** This is the
  sharpest lesson from 2026-08-09: far-field placement *broke the
  middle* — the reading "collapses toward solo when far-field or during
  one-at-a-time talk, resolves pair-to-small-group when close +
  multi-party". Last night's `solo`-at-54%-with-4-people is that same
  collapse. Do not put it on a side table.
- Mic input level ~34% (the level every prior session used).
- Music through whichever device you want — but if it's the laptop's own
  speakers, expect the "hearing through music" chip and possibly the
  advisory banner. Both are normal.
- Note the **start wall-clock time**.

---

## D. What to tell your guests

Short and honest, because it is all true:

> "There's a thing on my laptop listening to the room and picking the
> music. It's not recording anything and it doesn't do speech
> recognition — it never turns anything into words. It measures how loud
> and how animated the room is, roughly how many people are talking, and
> the emotional tone of voices, and picks from playlists I made. If a
> song is wrong, tell me and I'll hit skip — that's data."

If you chose to record audio in step A2, say so explicitly and get
agreement. If you didn't: no audio is written to disk, the 90-second
speaker-embedding buffer stays in memory, and the only things saved are
the numbers and which track was playing.

**During the session:** use the room normally — natural, uneven
conversation, "let whoever talks most talk most". Tap **Good call /
Wrong call** as reactions happen, and **Skip / Wrong vibe** on any track
that genuinely doesn't fit. Aim for 30 minutes minimum; longer is fine
and gives the rungs more chances to appear.

---

## E. The four checkpoints — Claude verifies from the logs afterward

Run the dashboard so its output is captured to a file. Then:

**1. New buckets drive cells** *(the headline — needs 3+ occupants)*

```bash
grep -o "for cell ('[^']*'" <logfile> | sort | uniq -c | sort -rn
```
PASS: at least one `('3', …)` and, if occupancy reached 5–6, `('6', …)`.
Report the full bucket distribution, not just the presence of a 3.

**2. Presence, gating and advisory unregressed**

```bash
# occupied / reason split across played_through records
# expect: mostly reason="fresh", empty-room completions marked absent
```
Compare against last night's baseline: 39 occupied (38 `fresh`, 1
`handoff`), 4 `absent` correctly excluded.

**3. No crowd-regime excursions**

```bash
# headcount_crowd_weight across every corpus record
```
PASS: `crowd_weight ≈ 0` outside deliberate babble. Baseline: exactly 0
in all 59 records last night, on `main`.

**4. Tuning report reads the session back**

```bash
.venv/Scripts/python.exe scripts/tuning_report.py
```
PASS: no errors, and frames carrying the **new bucket labels** (`3`, `6`)
appear in the per-cell breakdown.

---

## F. Recording the outcome

If all four pass: a dated `docs/FIELD-NOTES.md` entry (house style —
setup with full config provenance, numbered findings with the
measurement behind each, open items) **plus** the README gate table row.
A milestone closes only when its gate is *observed and recorded*, never
when the code merely works. The git merge is a separate, deliberate step
— 2026-08-09 explicitly declined to take it in-session.

If a checkpoint fails: **stop and diagnose. No in-session tuning** — the
knobs move after the gate, by the gate's numbers, ideally recalibrated
offline from recorded audio.

---

## G. Hard don'ts

- **Never push to `milestone-7-stable-middle`** while its gate is open.
  Run from it; commit nothing to it.
- **Never enable the rescue.** Disproven 2026-07-15 on this hardware.
- **Never adjust a threshold mid-session** to make a checkpoint pass.
- **Don't read last night's session as part (f) evidence.** It ran on
  `main`, where rungs 3 and 6 don't exist — checkpoint 1 is structurally
  unobservable there, and the other three don't constitute a pass alone.
- Return to `main` afterwards (`git checkout main`) so later work doesn't
  accidentally land on the branch.
