# XVF3800 vs built-in array under playback — run sheet

**Status.** Written 2026-09-23, not yet run. Human-run on the reference
machine (`JPad`), founder solo. Changes no constant, threshold, ramp or
default; its output is a FIELD-NOTES entry and one founder decision.

**The question.** Which microphone should be the everyday default for Read
the Room: the built-in array (`Microphone Array on SoundWire D`) or the
reSpeaker XVF3800 in its operating state (AGC frozen at gain 2.0,
WDM-KS)?

**Why this session and not the last one.** FIELD-NOTES 2026-09-23 settled
the XVF3800's *configuration* but deliberately not the default mic: on
that session (solo, near-field, **playback off**) the built-in array
out-scored the frozen XVF3800 on affect range (52 vs 22 cutoff crossings)
and solo headcount (ends `solo` vs `4`). The array's plausible advantages
were never exercised:

- **Hearing through music.** RTR's normal operating condition is its own
  music playing. How much of that music each mic certifies as speech,
  feeds to the emotion model, or turns into phantom occupants was not
  measured.
- **Far-field pickup.** Every 09-23 leg was talked from the same close
  position.
- **Echo cancellation.** The XVF3800 enumerates an *output* endpoint
  (`Echo Cancelling Speakerphone`, WASAPI 13 / WDM-KS 20 on 2026-09-23).
  AEC cancels against a reference signal, and in the array's USB
  configuration that reference is expected to be the audio played
  *through that USB output*. **Not yet verified against the XMOS user
  guide; check before running Block 2.** Music from the laptop's own
  speakers gives it no reference, so Block 1 tests the array **without**
  AEC doing anything to the music; Block 2 (optional) tests it with.

Two further things change under playback that make this comparison fairer
than 09-23's. While `playback_active`, the crowd path's `loud_term` keys on
loudness **relative to the noise floor** rather than absolute dBFS (M4), so
the mic-gain inflation found on 09-23 (finding 2) should largely drop out.
The babble *target* (`log2 = 3 + 7·speech·loud01`) is still absolute, so
some level dependence remains; this session measures how much.

---

## A. Decide before the session (founder)

1. **The track.** One track for every leg, on **repeat-one** (FIELD-NOTES
   2026-09-06: Spotify advances otherwise, and per-song mastering moves the
   mic-side level by several dB). Pick one **with vocals**: sung vocals are
   the hardest case for VAD certification, and that is RTR's real condition.
   Record its Spotify URI.
2. **Speakers.** Block 1 uses the **laptop's own speakers**, the everyday
   co-located case and the loudest contamination. Block 2 needs a **powered
   speaker with a 3.5 mm input**. If you don't have one, skip Block 2; the
   default-mic decision can be made from Block 1 alone, with AEC left as an
   open item.
3. **Placement, fixed for the whole session.** XVF3800 sitting directly
   beside the laptop, same edge, so both mics share a position. Mark two
   talking spots on the floor on the same bearing, facing the mics:
   **NEAR ≈ 0.6 m** and **FAR ≈ 3 m**. Nothing moves between legs except
   you.
4. **The decision rule (§G).** Read it now and amend it **before** the
   session if you disagree. Not after.

---

## B. Preflight — Claude runs this (~10 min)

```bash
cd /c/dev/read-the-room
git status --short                       # main; nothing staged that matters
.venv/Scripts/python.exe -m pytest -q    # green, offline
```

1. **XVF3800 operating state.** `python scripts/xvf3800_dashboard.py
   --check`, expecting "operating state verified: AGC frozen at gain 2.0"
   and the WDM-KS index.
2. **Built-in index.** List input devices. The built-in array's MME entry
   was `1` on 2026-09-23. Indices move; use whatever it is today.
3. **Spotify alive.** Token refresh and device list, as in
   `M7-PART-F-RUN-SHEET.md` §B (needs `inject_os_truststore()` first).
   `JPAD` must be visible. If the token is stale, the founder runs
   `read-the-room-spotify-auth`.
4. **Isolation directory.** `data/playback-compare/` must be **empty or
   absent**. Every leg writes its signatures, anchor, annotations and
   overrides there (see §D), never to the real files. This is not optional:
   - `TrackSignatureStore` loads a signature file whatever mic it was
     stamped for, keeps adapting it, and **re-stamps it with the current
     mic on save** (`src/sensing/music.py` `_load`/`_save`). Pointing the
     XVF3800 at `data/track_signatures-lenovo.json` would apply built-in
     signatures behind the array, blend array readings into them, and
     relabel the file. (Filed as a finding in §I, not fixed here.)
   - The advisory anchor (`data/advisory_anchor.json`) is unstamped: a
     noise floor from one mic, restored into the other mic's session.
   - Fresh files for every leg also make the M6 correction start from zero
     refs on *both* mics. That is fair. The built-in's existing `pull_refs`
     would otherwise be an advantage the array has no way to have.
5. **Follower present.** `~/tools/xvf3800/leg_snapshot.py --follow` (the
   phase clock, §D). It lives beside `xvf_host.exe`, outside the repo.
6. **Output device and volume (Block 1).** Windows default output =
   `Speakers (Cirrus Logic XU …)`, **Windows 75 %, Spotify 100 %**. That is
   the 2026-09-06 setting that put music at −31.1 dBFS on the built-in mic,
   inside the M6 target band. Leave both untouched for every Block 1 leg.
7. **Nothing else holding the XVF3800.** WDM-KS opens exclusively. Close
   Teams, Zoom, Discord and the like.

**Stop and diagnose if** pytest is red, `--check` fails, `JPAD` is not
visible, or `data/playback-compare/` has files in it.

---

## C. Room [HUMAN]

Quiet room, door closed, no fan or TV. Phone on silent. Note the start
wall-clock time. Talk in the **same style as 2026-09-23** (continuous,
animated, one voice), so the no-music baseline stays comparable.

---

## D. Running one leg

Each leg is one fresh dashboard plus one follower, **12.5 minutes of
phases**. The follower is the clock: it prints each instruction as its
phase starts, counts down, marks every frame with its phase, and stops by
itself.

| Phase | Length | You do | It measures |
|---|---|---|---|
| **P0** | 1:30 | Silent, **no music** | Seeds the noise floor and advisory anchor. Without this, playback-mode crowd gating has no floor to reference |
| **P1** | 3:00 | **Start the track** (repeat-one), stay **silent** | Contamination: what each mic does with music alone |
| **P2** | 4:00 | **Talk** at the NEAR mark, music playing | Reading a talker through music |
| **P3** | 4:00 | **Move to FAR**, keep talking, music playing | Far-field retention |

The first 20 s of every phase is excluded from its statistics, covering
the 5 s playback poll plus the EMAs. At **DONE**, pause the track, then
Ctrl+C the dashboard. Pausing before the next leg matters: its P0 must be
music-free.

**Terminal 1 — session-only environment (every leg; change the leg letter):**

```powershell
$L = "A"                                   # A, X, A2 ... per §E
$env:RTR_PLAYBACK_ENABLED = "1"            # needed for playback_active stamping
$env:RTR_PLAYBACK_PLAYLISTS_PATH = "data/playlists-inert.json"   # DJ inert
$env:RTR_MUSIC_SIGNATURES_PATH = "data/playback-compare/signatures-$L.json"
$env:RTR_PLAYBACK_ADVISORY_ANCHOR_PATH = "data/playback-compare/anchor-$L.json"
$env:RTR_DASHBOARD_ANNOTATIONS_DIR = "data/playback-compare/annotations-$L"
$env:RTR_DASHBOARD_OVERRIDES_DIR = "data/playback-compare/overrides-$L"
```

With the DJ inert, the controller has nothing to choose, so it never
touches playback, and `played_through` only fires for tracks the DJ chose.
The corpus redirects are belt-and-braces against an accidental tap. **Don't
tap label buttons during this session.**

**Terminal 1 — the dashboard:**

```powershell
# built-in legs (index from preflight step 2):
.venv\Scripts\read-the-room-dashboard.exe --device 1
# XVF3800 legs (applies + verifies AGC frozen at 2.0, resolves WDM-KS):
.venv\Scripts\python.exe scripts\xvf3800_dashboard.py
```

**Terminal 2 — start the follower as soon as the dashboard is serving:**

```powershell
.venv\Scripts\python.exe $env:USERPROFILE\tools\xvf3800\leg_snapshot.py --follow `
  --leg $L --device-state "<see §E>" --out data\playback-compare\leg$L.jsonl
```

Or just say **"start leg A"** and Claude verifies the launch and runs the
follower.

---

## E. The legs

### Block 1 — the default-mic decision (required, ~45 min)

| Leg | Mic | Device state to record | Music out |
|---|---|---|---|
| **A** | built-in | `built-in MME <idx>, stock` | laptop speakers |
| **X** | XVF3800 | `XVF3800 WDM-KS <idx>, AGC frozen at gain 2.0` | laptop speakers |
| **A2** | built-in | `built-in MME <idx>, stock — repeat of A` | laptop speakers |

**Why A2.** 09-23's legs ran in a fixed order, so drift (warm-up, fatigue,
room) was confounded with the mic. Repeating A at the end measures that
drift directly: **a difference between A and X only counts if it is larger
than the difference between A and A2** (§G). If time allows, a fourth leg
**X2** after A2 makes it a full ABBA and gives the array its own drift
estimate.

### Block 2 — does echo cancellation change the answer? (optional, ~30 min)

Needs the powered speaker. Only the **cable** moves between these two legs.

| Leg | Mic | Speaker plugged into | Windows default output |
|---|---|---|---|
| **XE** | XVF3800 frozen | laptop headphone jack | laptop output |
| **XR** | XVF3800 frozen | **XVF3800's 3.5 mm jack** | `Echo Cancelling Speakerphone (reSpeaker…)` |

- **Level matching.** Windows volume percentages are not comparable across
  two different DACs, and the XVF3800 can't meter its own output, since
  cancelling it is the point. Match by ear-independent means: a phone SPL
  meter app at the NEAR mark, the track playing, speaker knob untouched,
  Windows volume adjusted on each endpoint to within **1 dB** of each
  other. Record both readings.
- **Preflight for XR.** Confirm sound actually comes out of the array's
  jack before starting the leg.
- **What XE vs XR answers.** Everything else is equal, so any drop in P1
  contamination and rise in P2/P3 affect range is attributable to AEC
  having a reference.

---

## F. What gets measured

The follower prints this per phase at the end of each leg; `--analyze
legX.jsonl` re-prints it. After each leg Claude also reads that leg's
signature file (`refs` / `pull_refs` for the track).

| Phase | Measure | Better is | Why it matters |
|---|---|---|---|
| P0 | noise floor; loudness | — (context) | Level provenance for everything after |
| P1 | **fresh inferences landed** (emotion, headcount) | fewer | Each one is music being read *as the room*. Silence should **hold** |
| P1 | speech ratio; frames ≤ 0.1 | lower / more | Music certified as speech. ≤ 0.1 is also the gate for banking a standalone signature |
| P1 | loudness over floor; `envelope_advisory` frames | — / fewer | §5 Q3 of the protocol doc: the advisory margin on each mic |
| P1 | music dominance p5 / p50 / p95 | — (record) | §5 Q4: do the PROVISIONAL knots (0.022 / 0.050) mean anything on the array? **Record, don't refit** |
| P2 | valence / arousal spread; ±0.25 crossings | wider | Affect range through music |
| P2 | `emotion_correction` applied fraction, magnitude | — (record) | Whether M6 correction runs on each mic |
| P2 | buckets; final; `crowd_weight`; dispersion | closer to `solo` | Solo headcount through music, with floor-relative gating |
| P2 | speech ratio | higher | Certification under the stricter playback VAD (0.75) |
| P3 | same as P2 | — | Far field: **P3 relative to P2** on each mic is the retention figure |
| end | signature `refs`, `pull_refs` | — (record) | Whether each mic can bank the M6 evidence at all |

---

## G. Decision rule (pre-registered)

The founder may amend this **before** the session, never after. There is no
target number (repo convention); every comparison is **X vs A**, and a
difference counts only if it exceeds the **A vs A2** difference on the same
measure. A difference inside that band is a tie.

Three questions, each won by whichever mic is better on the majority of
its measures that aren't ties:

1. **Contamination (P1):** fresh inferences landed (emotion + headcount),
   frames ≤ 0.1.
2. **Near talk through music (P2):** arousal stdev, total ±0.25 crossings,
   solo headcount (frames in `solo`, then final bucket).
3. **Far talk (P3):** the same three measures as P2.

**The XVF3800 becomes the everyday default if it wins at least two of the
three and does not lose question 2's solo-headcount measure.** Otherwise the
built-in array stays the default (today's status quo: `RTR_INPUT_DEVICE`
is empty and the Windows default input is the built-in). Block 2 informs,
but doesn't enter, the rule. If XR clearly beats XE, the recorded decision
notes that the array's case improves with the speaker on its jack.

---

## H. Recording the outcome

A dated `docs/FIELD-NOTES.md` entry in house style:
- **Setup with full provenance:** branch and commit, `.env` over defaults,
  the session-only env block, track URI, speaker, volumes and SPL readings,
  device indices, device state per leg.
- **A per-leg, per-phase table** of §F.
- **The §G verdict** worked through measure by measure, **with the A vs A2
  drift shown**.
- **Signature file contents** per leg.
- **Dominance distributions** as evidence toward protocol-doc §5 Q4.
- **The decision.**

**Mechanics of the decision.**
- **If the XVF3800 wins:** the launcher becomes the documented everyday
  start in README, and the entry says so.
- **If the built-in wins:** the launcher stays the XVF3800-only path.
- **Either way, `.env` is not edited in-session.** A default-device change
  is a separate commit, after the entry.

---

## I. Hard don'ts, and one finding this sheet works around

- **No in-session tuning.** Not the dominance knots (PROVISIONAL, and
  single-mic even then), not the advisory margin, not a VAD threshold.
  Record, then decide in a separate calibration protocol.
- **Never point an XVF3800 leg at `data/track_signatures-lenovo.json` or
  the real advisory anchor.** Use the §D env block every leg. Closing
  Terminal 1 afterwards drops it all.
- **Never run `SAVE_CONFIGURATION`** on the array (protocol doc §3).
- **Don't tap labels.** This is a measurement session, not a DJ session.
- **Don't stop a leg early to save time.** A short P2 or P3 never gets the
  90 s headcount buffer to turn over, which is where the 09-23 behaviour
  lived.

**Finding (for FIELD-NOTES; not fixed here).** The v3 signature
`source` stamp is **recorded but not enforced**. `_load` logs the stamp
and uses the signatures regardless; `_save` overwrites the stamp with the
current capture source. MACHINE-DOCTRINE-REVISION says the stamp "exists
for exactly this reason", but nothing refuses a mismatched file. Any
mic change, including this new array, silently blends two capture paths'
signatures under one label. A fix would change which signatures get
applied, and so the published valence/arousal (REQUIRES-REVIEW). It
belongs in ROADMAP, not in this session.

---

## Addendum, 2026-09-24: session run; AEC reference verified

**Run status.** Block 1 ran on 2026-09-24 as legs A, X, B, X2, B2. The
verdict legs were B/X2/B2, after A (16 % volume) and X (late P1, array
~0.6 m off) were set aside. See the FIELD-NOTES 2026-09-24 entry. The §G
outcome: the built-in array stays the everyday default. Block 2 was not
run.

**The open item in "The question" (echo cancellation) is now checked
against the XMOS documentation** (XVF3800 v3.2.1, read 2026-09-24):

- **The reference is the audio the host plays to the array.** "A far-end
  AEC reference signal must be provided on the left (0) channel of the
  I2S or USB input signal. Data on the right channel is ignored."
  (Datasheet, *Voice Processing Pipeline*.) In the USB configuration
  this is the stream sent to the array's USB sound card, which also plays
  out of its line out / 3.5 mm jack. That confirms this sheet's working
  assumption. Block 1 (laptop speakers) gave the AEC no reference, and
  Block 2's XR leg (speaker on the array's jack, Windows default output =
  the array) is the correct way to give it one.
- **RTR already reads the AEC-processed channel.** On the reSpeaker's
  default mux (`AUDIO_MGR_OP_L`/`_R`), the left output channel is "the
  processed output from the XVF3800's AEC, beamforming and post process
  stage", and the right is the ASR beam (reSpeaker `host_control`
  README). `MicSource` opens one channel and takes `indata[:, 0]`
  (`src/sensing/audio.py`), so no capture change is needed for Block 2.
- **Limits that bear on any XR-style leg:**
  - AEC tail length is 192 ms.
  - Reference delay is 0–500 ms, fixed, set by `AUDIO_MGR_SYS_DELAY`
    (reSpeaker default 12). Alignment is measured with XMOS's
    `mic_ref_correlate` procedure.
  - Convergence takes a few seconds (< 30 s) of reference audio and is
    readable as `AEC_AECCONVERGED`. Moving the speaker forces
    reconvergence.
  - The speaker and amplifier must stay in their linear region, and the
    mic should peak ~6 dB below the reference (*Tuning the Application*).
- **Procedural consequence for XR.** Before the leg starts, confirm
  `AEC_AECCONVERGED` = 1 with the track playing. Read it only; like the
  AGC writes, no `SAVE_CONFIGURATION`. The P1 settle window (20 s) may
  be shorter than convergence.

**Scope note (founder direction, 2026-09-24).** Development continues
on JPad's built-in mic and speakers. The intended venue deployment is a
microphone in the centre of the space with the dashboard on a
background computer. That's a remote-mic case this sheet doesn't test,
and for AEC to help there, the music would have to be routed through the
array's line out to the venue's system. Block 2 is therefore deferred.
A venue-shaped protocol (array at the centre, music through its line out
to a separate speaker at a distance, talking marks measured from the
array) belongs in its own run sheet when venue work starts.

Sources: XMOS XVF3800 v3.2.1 documentation, *Voice Processing
Pipeline*, *Setting Up the Hardware* and *Tuning the Application*
(xmos.com/documentation/XM-014888-PC); reSpeaker
`reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/README.md` (GitHub).
