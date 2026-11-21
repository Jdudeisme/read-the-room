# M12-09 — live canceller diagnostics — plan (REQUIRES-REVIEW)

**Status:** plan approved by the founder, 2026-11-21, with D1–D3 as
recommended. The diff (branch `m12-09-diagnostics`, from `main`) comes back
for review before it's committed.

**What this is.** Step one of ROADMAP M12-09: find out why the live
canceller didn't remove the music on 2026-11-21 (raw − clean 0.1–0.5 dB
live, against 2.3–3.5 dB offline on the same room, spot and volume;
FIELD-NOTES 2026-11-21, night). **Diagnostics only.** No published value
changes, and no fix until the evidence says what to fix. It touches audio
capture (`src/sensing/audio.py`), so the plan and the diff both need the
founder's review.

**The hypothesis to confirm or rule out.** Under the dashboard's model
load, the WASAPI loopback drops audio. `ReferenceSource` writes only what
it receives, so each dropout permanently shifts the reference against the
mic. The canceller then spends its time re-locking, and a misaligned filter
adds energy instead of removing it. Alternatives the data must be able to
separate:
- the live alignment (`CleanSource.align` by ring totals) starts outside
  the tracker's ±0.6 s;
- constant five-way double talk keeps the filter from converging;
- mic-side dropouts.

## What gets measured (the diff)

1. **Reference dropouts and lost samples** (`ReferenceSource`, on its own
   thread). It keeps a running `received` frame count and compares it with
   the frames the loopback's clock implies since start: wall elapsed ×
   48 kHz, anchored at the first block. The **difference is the estimated
   lost audio**. It also counts the "data discontinuity" warnings, through a
   `warnings` filter scoped to the reference thread's own module, so no
   global warning state changes. Exposed as `ref.lost_frames_est` and
   `ref.discontinuities`.
2. **Canceller state** (`CleanSource`, on its own thread, never on the
   engine tick; invariant 3). From the live `StreamingCanceller.result`:
   - locked or not;
   - the current lag and its coherence (`delays[-1]`);
   - re-locks since the last report, with their choices;
   - divergence resets and rollbacks;
   - `resyncs`.

   It also computes the **live echo reduction** over the last 10 s, as
   E(mic) / E(clean) in dB, accumulated per block (cheap). This is the
   number that was ~0 dB on 2026-11-21.
3. **One INFO log line every 10 s** from the clean-source thread, e.g.:
   `canceller: locked lag=1184 coh=0.62 relocks=+2 (shift,reset) resets=0 resyncs=0 reduction=3.1 dB | reference: discontinuities=+4 lost≈38 ms (total 412 ms) drift=+0.4 s`.
   Logs only, with **no new frame fields** (D1).
4. **An opt-in diagnostic clip** (D2): `RTR_DEBUG_CANCEL_CLIP_S` (default
   **0 = off**). When > 0, once the canceller has first locked plus 30 s,
   `CleanSource` writes the next N seconds of raw mic, clean and reference,
   on the same sample positions, to
   `data/captures/live-clip-<timestamp>.{mic,clean,ref}.wav` plus a JSON
   sidecar with the log lines covering it. Disk I/O happens on the
   clean-source thread, never the engine. This follows the house pattern:
   default with provenance, `from_env`, and `.env.example` in exact
   agreement. It writes room audio, so CLAUDE.md's "new capture defaults to
   off" applies, and **it's only to be enabled with the founder alone in
   the room**.
5. **An offline comparison script,**
   `scripts/m12_09_clip_compare.py CLIP`. It runs offline `aec.cancel(mic,
   ref)` on the clip and reports, per 5 s window: offline reduction vs the
   live clean's reduction, and the offline lag vs the live log's lag. If
   offline cancels the same audio and live didn't, the live path (alignment,
   dropouts) is at fault. If neither cancels, it's the audio (double talk,
   level, nonlinearity).
6. **Tests** (offline, fake recorder and fake canceller):
   - the lost-frame estimate counts a simulated dropout exactly;
   - the 10 s report reads the canceller's result without disturbing it;
   - the clip writes three aligned WAVs of N s when enabled, and nothing at
     0;
   - `.env.example` ↔ `from_env` agree.

## Decisions for the founder

- **D1. Logs, not frames.** **Recommended.** Frame fields are a published
  schema (invariant 9); these are diagnostics for one investigation. If
  they prove useful long-term, promoting a couple to frames is a separate
  additive change.
- **D2. The opt-in clip.** **Recommended, default off, founder alone.** It's
  the only way to compare live and offline on identical audio. Without it,
  the comparison is between different nights.
- **D3. Where it runs.** **Recommended: on a new branch from `main`**
  (`m12-09-diagnostics`). That means the diagnostics run with cancellation
  **off by default**, and the live session sets
  `RTR_PLAYBACK_REFERENCE_ENABLED=1 RTR_PLAYBACK_CANCEL_ENABLED=1` in the
  PowerShell window. `cancel-on-by-default` stays parked until M12-09 is
  understood.

## The live session (founder-run, after the diff is approved)

Founder alone, same alcove and spot, dashboard in its own PowerShell
window, ~20 minutes, `RTR_DEBUG_CANCEL_CLIP_S=60`. Phases:
1. 5 min of music at 32 %, silent;
2. 5 min at 32 %, reading aloud;
3. 5 min at 66 %, reading;
4. 2 min with the music paused (control).

Paste the shell log to Claude. Claude runs the comparison script on the
clip and writes the FIELD-NOTES entry: the dropouts and lost audio per
phase, the canceller state, live vs offline reduction, and which
hypothesis the evidence supports. **Delete the clip** once the entry is
written.

## Verification (Claude)

- `python -m pytest` green, offline, fast.
- With the clip at 0, a diagnostic run changes no published value. The
  2026-07-15 headcount replay and gate (a) are unchanged (no sensing path is
  touched).
- A `bench_headcount.py --fallback --cancel` row with the report running,
  within run-to-run variance of gate (c). The report adds per-block energy
  sums on the clean-source thread.
