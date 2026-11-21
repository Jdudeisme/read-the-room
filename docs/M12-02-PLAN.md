# M12-02 plan — reference-based playback cancellation on the built-in array (DRAFT)

The charter is ROADMAP M12-02, adopted verbatim. This plan says how,
grounded in what the 2026-10-09 session measured. It changes nothing
until each step below is approved. The item is REQUIRES-REVIEW: plan,
then each diff.

## What the measurements already decide

From the dominance-ladder + probe session (FIELD-NOTES 2026-10-09,
evening) and the offline checks run while writing this plan:

| Fact | Consequence for the canceller |
|---|---|
| Reference is tapped **after Dolby Atmos** | the filter models speaker + room only, not Atmos's processing |
| Reference is **before the volume slider** (−11.9 dBFS for T1 at 32/66/76 %) | gain is unknown from the reference, so the adaptive filter must learn it, and re-learn on every volume change |
| Mic and speaker share **one clock** (within-take delay steady to < 0.4 ms in 12 of 16 music takes) | no resampling or drift compensation is needed |
| **Alignment steps** of 15–30 ms (4 of 16 takes) | bulk delay must be tracked continuously and re-locked; a fixed delay fails |
| A one-window delay estimate can lock onto the **wrong peak** on rhythmic music (T1: −175 ms from its first 10 s; the true delay was −110 ms, where coherence is 0.66 against 0.00 at the false peak) | the tracker scores a delay by coherence over a longer window, not by a single GCC peak |
| **Coherence 0.66–0.81** between reference and mic at the right delay, on all three tracks (200 Hz–4 kHz, music-only takes at 32 %) | most of the music at the mic is linearly predictable from the reference, so a linear canceller is worth building |
| JPad's output **tops out between 66 and 76 %** | a limiter is suspected near the top, and non-linear residue is likely at loud levels. Measure it; don't assume |
| Speech-only controls: reference is **exact zeros** | with no playback, a correct canceller must return the mic **bit-identical**. That is a hard test |

**Linear-cancellation ceiling** (2026-10-09, offline). For each 40 s
segment: GCC-PHAT alignment on its first 20 s, a per-frequency Wiener
filter (4096-point) fit on those 20 s, applied to the next 20 s, and
ERLE measured over 100 Hz–6 kHz. This is roughly the upper bound for a
time-invariant linear canceller:

| music-only, 32 % | @20 s | @100 s | @160 s |
|---|---|---|---|
| T1 pop | 2.6 dB (quiet intro) | 7.0 dB | 7.2 dB |
| T2 piano | 10.4 dB | 8.5 dB | 10.7 dB |
| T3 rap (alignment step tracked: −193 → −217 ms) | 7.0 dB | 7.2 dB | 7.1 dB |

**About 7–11 dB.** That is consistent with the coherence: −10·log10(1 − C)
for C ≈ 0.7–0.83 gives 5–8 dB. So on JPad's built-in path, roughly 80–90 %
of the music energy is linearly predictable from the reference, and the
rest is not. Small-speaker non-linearity and the array's own processing
are the likely reasons; this run didn't separate them. Two consequences:

- Don't expect the 20–30 dB textbook ERLE. Success here is "near the
  ceiling", measured against this table, not against a target.
- **The canceller narrows the problem; it doesn't solve it.** A rap
  residual 7 dB down may still certify as speech, so M12-03's gate
  (residual vs reference) carries the rest, and its value goes up. The
  evaluation measures exactly this: VAD-eligible windows on T3-MO32, raw
  vs clean.

(An earlier version of this check aligned the whole take from its first
10 s and reported near-zero ceilings for T1 and T3. That was the false-
peak and alignment-step problem in the table above, not the music.)

## Design

Three parts, each its own module function, testable offline with no
models:

1. **Bulk-delay tracker.** Every ~2 s, over the last ~10 s, it scores
   candidate delays in ±600 ms by mic–reference coherence (200 Hz–4 kHz).
   That is coarse via GCC-PHAT, then refined. It keeps the current delay
   until a candidate wins clearly for two consecutive updates (hysteresis),
   then re-locks and tells the filter to re-converge. With no reference
   (all zeros), it holds and reports "no playback". This answers the
   steps and the false GCC peaks above.
2. **Adaptive filter: partitioned-block frequency-domain NLMS** (the
   Speex "MDF" family), in numpy. Block 256 samples (16 ms), filter span
   set by the measured echo tail (first measurement, see Evaluation),
   expected 100–250 ms. Double-talk (people talking over music) is where
   cancellers diverge, so step size is coherence-controlled: it adapts
   when mic and reference are coherent, and freezes when speech
   dominates. A divergence guard resets the filter if the output gets
   louder than the input.
3. **Optional residual suppressor**, a spectral post-filter. It is
   non-linear and *will* touch speech, so it is evaluated separately. A
   residual is probably better left for M12-03's gate to refuse than
   scrubbed from the audio emotion and headcount see.

**Where it runs.** `src/sensing/aec.py` holds pure functions and a
stateful canceller object. A `CleanSource` thread reads the mic ring
and the `ReferenceSource` ring, writes a **clean ring**, and never touches
the raw ring. The engine keeps reading raw until a separate, reviewed
switch (`RTR_PLAYBACK_CANCEL_ENABLED`, default 0) points analysis at the
clean ring. Frames then carry which stream was analysed, so raw is
always reconstructable (house rule). The canceller runs on its own
thread, never in the tick (invariant 3). If it falls behind, it re-syncs
from the newest audio instead of queueing (invariant 4).

**Algorithm choice is a measurement.** numpy MDF is built first, with no
new dependency. A pinned library (e.g. a SpeexDSP or WebRTC binding) is
considered only if numpy measures short on ERLE or on CPU, and then as
its own evidence event (Windows wheels under the project's pins are not
assumed).

## Evaluation (offline, on the 2026-10-09 captures; no new session to start)

`scripts/m12_aec_eval.py` runs the canceller over each take's
`.wav`/`.ref.wav` pair and reports per take:

- **Echo tail**: the length of the fitted impulse response holding 99 %
  of its energy. This sets the filter span.
- **ERLE** on music-only windows: the `MO32` takes, P1, and Atmos-off,
  plus speech pauses inside the mix takes (VAD p < 0.2), which is the
  only music-only material at 66 / 76 %. This is reported against the
  linear ceiling above.
- **Convergence**: time to within 3 dB of steady-state ERLE after P1's
  play, pause, play and skip, after each take's start, and after the
  alignment steps in T2-MX76, T3-MO32 and T3-MX66.
- **Speech preservation**, as the charter specifies: certified
  `speech_ratio` and ECAPA embedding similarity, clean vs raw. On the
  controls it must be bit-identical (reference all zeros). On the mix
  takes, ECAPA similarity of clean speech to the founder's C1 speech, vs
  raw.
- **The M12 payoff, measured here**: VAD-eligible windows on T3-MO32
  (the rap alone certified 61 / 118 raw), and on T2-MX76 (the founder's
  reading, blinded to 19 / 116 raw), raw vs clean.
- **CPU**: canceller seconds per audio second on JPad. It must fit beside
  emotion and headcount; gate (c) benchmarks it.

No target numbers (repo convention). Everything is recorded in FIELD-NOTES.

**A gap the captures can't fill:** music-only at 66 % and 76 %. The
charter asks for ERLE "at both M12-01 volumes on music-only windows", and
loud-level non-linearity is the open risk. Speech pauses inside the mix
takes give a partial answer. If they're not enough, a **12-minute
founder top-up** would settle it: T1/T3 music-only at 66 % and 76 %,
plus 1 min of P1-style transport at 66 %. That is only proposed after the
offline numbers show whether it's needed.

## Steps and what each needs

| # | Step | Needs from founder |
|---|---|---|
| 1 | `src/sensing/aec.py` (tracker + MDF), synthetic-echo unit tests (known impulse response, steps, double-talk, zeros-in → bit-identical out), `scripts/m12_aec_eval.py`. **No engine change** | approve this plan |
| 2 | Run the evaluation on the 2026-10-09 captures; FIELD-NOTES entry | — (offline) |
| 3 | Decide: numpy MDF enough, or a library evidence event; residual suppressor yes or no; is the 12-min top-up needed | decisions |
| 4 | `CleanSource` + the `RTR_PLAYBACK_CANCEL_ENABLED` switch (default 0), engine reads clean when on, frames record which stream | plan + diff |
| 5 | Live: the M12-05 paired session runs with cancellation on and off | (M12-05 sheet) |

## Risks

- **Double-talk divergence** is the classic canceller failure, so it is
  measured on every mix take, not assumed handled.
- **Non-linear speaker at loud levels.** The ceiling at 66–76 % is only
  partly measurable from current captures; see the gap above.
- **Over-cancellation of speech** would hurt emotion and headcount
  directly. That is why ECAPA similarity is a first-class metric, and why
  the suppressor is optional.
- **Bit-exactness on silence** guards the "shadow is first-class" rule:
  with no playback, the clean stream must be the raw stream.
