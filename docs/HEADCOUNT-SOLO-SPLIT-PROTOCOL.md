# Protocol: why does a solo speaker read `pair` on JPad?

**Status:** first run 2026-09-06 (night) — results and open items in
`docs/FIELD-NOTES.md`. Human-executed on the reference machine.
**Question owner:** founder. **Written:** 2026-09-06.

> **What the first run changed.** Three corrections, all from the same night:
>
> 1. **Capture length was wrong** (see the box under Conditions). 90 s barely
>    reaches buffer saturation, which is where the split lives.
> 2. **Condition F (movement) was added mid-session and dominates.** Scatter
>    moved ~3× the noise floor on posture versus ~1.6× on position. Holding
>    posture fixed is good hygiene for isolating position, but it suppresses
>    the variable that turned out to matter most; run both.
> 3. **The split reproduces, but only intermittently** — offline, in
>    natural-speech captures at scatter 0.62–0.64, in 2.5–21 % of windows at
>    unpredictable points in the take. It is an artifact of a scatter
>    distribution straddling the 0.70 cut, not a stable regime, and a
>    *sustained* two-cluster reading has never reproduced. So candidate 4
>    below, not 1, is the live hypothesis.
>
> Open items live in the FIELD-NOTES addendum. The live-vs-offline test
> attempted there was confounded (two processes, two separate mic streams)
> and is unresolved.

FIELD-NOTES 2026-09-06 records the fact twice: a solo founder on the Lenovo
reads `pair`, with `raw_clusters` 2 and `dispersion` 0.551 (afternoon, `main`)
/ 0.588 (evening, merged M7). M7's crowd path is silent at that dispersion
(`crowd_weight` 0.0), so this is plain over-segmentation — one voice forming
two clusters that both clear the min-mass floor — not the crowd inflation M7
targets. It is open item (c) in that entry, and it colours every solo
annotation captured on this machine.

This protocol separates three candidate causes. It changes no constants and
touches no calibration: it is measurement only, and its output is a
FIELD-NOTES entry.

## Candidates

1. **Laptop position.** The recorded sessions ran with the laptop ~2 ft from
   a wall and corner. A boundary at that distance puts a strong early
   reflection ~3.5 ms behind the direct sound, comb-filtering the vocal band
   with notches roughly every 286 Hz; a corner adds two more paths at
   different delays. The pattern shifts when the speaker moves or turns, so
   the same voice lands in different regions of embedding space. The pool
   session (FIELD-NOTES 2026-07-06) already attributed dispersion inflation
   to reverb smear and left "worth measuring directly" as an open item.

2. **The capture path's own processing.** The device is
   `Microphone Array on SoundWire D` — a multi-element array. Windows arrays
   commonly apply beamforming, AGC, and noise suppression in the driver. A
   beamformer re-steering mid-session perturbs the effective channel harder
   than room geometry does. This is also a live suspect for the unexplained
   morning/evening spectral discrepancy (2026-09-06, finding 5).

3. **Ordinary laptop-mic scatter.** `headcount.py`'s min-mass comment already
   records same-speaker embedding scatter at **~0.35 mean pairwise cosine
   distance on clean audio, ~0.6 on a laptop mic**. If this machine's solo
   scatter sits near 0.6, an average-linkage cut at 0.70 is operating at the
   edge of a documented hardware regime, and the split is expected for this
   class of mic rather than something the room or the driver did.

4. **Speaking style — posture and movement while talking.** Added after the
   first run, which measured it as the largest effect and the only one that
   reproduced the split. Turning toward the screen, shifting, gesturing
   changes the direct-to-reflected balance continuously, so the same voice
   samples a range of channels within one buffer. Measured ladder, all solo,
   one night, same mic: seated and still 0.508–0.558 scatter (never splits);
   deliberate movement 0.589 (clusters form but fail the mass floor); natural
   speech 0.637 (two mass-passing clusters). The 0.70 cut behaves as a
   threshold on scatter, and this is the candidate that crosses it.

Candidate 3 remains the null hypothesis. **Candidate 4 is the live one** —
candidate 1 measured at roughly half candidate 4's effect and is not
established.

## Instruments

Both scripts were added for this investigation and touch nothing in `src/`:

- `scripts/capture_room_wav.py` — records through `MicSource`, so the WAV
  carries the real path (device resolution, fallback resampler, driver
  processing). Writes a provenance sidecar next to each WAV.
- `scripts/analyze_headcount_wav.py` — replays the engine's VAD/window/
  submission schedule over a WAV and reports `raw_clusters`, `dispersion`,
  `fragmentation`, `crowd_weight`, and **`scatter`** (all-pairs cosine
  distance over the final buffer).

Captures land in `data/captures/`, already excluded by `.gitignore`'s
`data/*`. They are local by design; delete them when the question closes.

**Read `scatter`, not the bucket.** `dispersion` is measured *within* clusters,
so it is computed after the split and understates the true spread. `scatter`
is clustering-independent and directly comparable to the 0.35 / 0.6 figures
above. The bucket is the symptom; scatter is the measurement.

This protocol deliberately does not involve the dashboard, which sidesteps
both process errors recorded on 2026-09-06 (the un-killable stale process, and
the 300-frame `/ws` replay).

## Conditions

Captures of **4–5 minutes each**, **one variable at a time**.

> **Not 90 seconds.** The split is intermittent — it fires in a small,
> unpredictable fraction of windows (2.5–21 % across the first run's
> captures) — so a 90 s take gives you ~43 windows and far too small a sample
> to estimate that rate. A 240 s take gives ~118 and also covers the
> first ~20 s separately, which matters: the estimator over-splits early,
> while the buffer is too sparse for the 10 % evidence floor to reject debris.
> Report the split *rate* and where in the take it fell, never just whether
> it happened. Use `--seconds 240` or more.

| # | condition | `--note` |
|---|---|---|
| A | center of room, mic settings as they are now | `center, enhancements as-is` |
| B | recreate the wall/corner position (~2 ft from both) | `wall+corner, enhancements as-is` |
| C | center of room, Windows mic enhancements **off** | `center, enhancements off` |
| D | center of room, **wired headset mic** (bypasses the array) | `center, headset mic` |
| E | repeat of A, at the end of the session | `center, enhancements as-is (repeat)` |
| F | center, **natural speech and movement** — look at the screen, shift, turn your head, gesture; do not read the passage | `center, natural speech and movement` |

**E is not optional.** Take-to-take variance on this machine is known to be
large — three music-only takes at fixed volume spread 0.0389–0.0572 on the
dominance proxy (2026-09-06). Without a repeat control there is no way to
tell a real condition effect from run-to-run noise, and any difference
smaller than the A/E gap means nothing.

D bounds the problem: a headset bypasses the array entirely, so it establishes
what "clean" scatter looks like *on this machine, with this voice*. Skip it
only if no wired headset is available, and say so in the write-up.

### Holding everything else fixed

- Same seat, same posture, same distance to the laptop, normal speaking voice.
- **Read the same passage each time** — same text removes content as a
  variable. Any neutral paragraph will do; note which one.
- No music, DJ inert, no other people, doors and HVAC unchanged throughout.
- Do not change any `RTR_*` value during the session. If something looks
  wrong, record it and keep going; in-session tuning is what makes a session
  uninterpretable.

## Running it

Use the venv interpreter explicitly - the system `python` on this machine
has no numpy and fails at import.

```bash
# once per condition, changing --note and the physical setup between takes
.venv/Scripts/python.exe scripts/capture_room_wav.py --seconds 240 --note "center, enhancements as-is"
```

The script warns if the capture came back below the deaf-stream floor
(-45 dBFS) or dropped samples. **Re-take on either warning** rather than
analysing the file — a deaf stream is pixel-identical to a quiet room, which
cost twenty minutes on 2026-09-06.

Then, all five at once:

```bash
.venv/Scripts/python.exe scripts/analyze_headcount_wav.py data/captures/*.wav --sweep \
  --json data/captures/results.json
```

The comparison table at the end is the result.

## Reading the outcome

Compare each condition's `scatter.mean` against **the A/E gap**, which is this
session's noise floor. A difference smaller than that gap is not a finding.

| observation | conclusion |
|---|---|
| B differs from A by more than the A/E gap | position matters — a **placement doctrine** finding (where the laptop goes), not a calibration change |
| C differs from A by more than the A/E gap | driver processing matters — pursue the enhancement settings, and re-examine the morning/evening spectral discrepancy in that light |
| D far below all others | the array is the ceiling; the built-in mic is the constraint, and D is this machine's clean reference |
| all five within the A/E gap, near ~0.6 | candidate 3 holds: this is documented laptop-mic scatter. Nothing in the room is wrong |

The last row is the expected outcome, and it is a real result, not a failed
session — it converts an open mystery into a known hardware characteristic
and moves the question to "what should RTR do about a solo speaker on a
laptop array?", which is a product decision with a roadmap item, not a
constant to patch.

**In no case does this session change a constant.** `RTR_HEADCOUNT_CLUSTER_-
THRESHOLD` is measured (calibrated 2026-07-05) and the repo's rule against
lowering it to "catch" merging voices applies in the other direction too:
undercounting beats phantom crowds, and this protocol is not authority to
retune. `--sweep` shows how near the 0.70 cut sits to a cliff; it is a view
of the distribution, never a suggested value. If the evidence argues the
threshold is wrong for this hardware, that is a calibration event with its own
protocol, its own FIELD-NOTES entry, and a live re-gate.

## Output

A dated FIELD-NOTES entry with: the setup, the five-row comparison table, the
A/E gap, which row of the outcome table fired, and what stays open. Attach
the `results.json`. If the conclusion is candidate 1 or 2, the follow-up is a
second session confirming it — one session establishes a difference, two
establish that it repeats.
