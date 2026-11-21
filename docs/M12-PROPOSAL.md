# Milestone 12 Proposal — Hear the room, not the playlist (DRAFT)

RTR plays music into the room it measures, and that music reaches
headcount, emotion and energy as if it were the room. M12 removes RTR's
own playback, which RTR knows sample for sample, from every layer. This
proposal adds execution order, one hardware finding and the decisions
needed. The charter, gate and acceptance criteria are `ROADMAP.md` § M12,
adopted verbatim. Founder go-ahead: 2026-10-09, right after M8 merged.

## Why now: three sessions, one mechanism

| Session | What the mic heard | What RTR read |
|---|---|---|
| 2026-09-30 part (f), 3 people, 32 % | vocal hip-hop certified as speech (only 8.6 % of chunks in the band the 0.75 gate removes) | `solo` on 117 of 143 hops; `pair`/`3` within 30 s of jazz starting |
| 2026-10-08, 8 people, 66 % | mostly vocal tracks | `solo` on 34 of 41 DJ picks; the only `3` came under an instrumental |
| 2026-10-09 M8 gate, **1 person**, 32 % | jazz | `pair` 79 / `3` 6 frames against `solo` 50, until the music changed |

The same certification point fails in both directions. Music the VAD fully
believes becomes one loud "voice" and collapses the room. Music it half
believes scatters into phantom speakers. Neither failure can be fixed by
moving the 0.5/0.75 thresholds (out of scope by charter).

## Drift since the charter was written

- **Volume levels.** The charter says 32 % and 75 %. The founder's
  operating levels are now **32 %** (1–5 people), **66 %** (small room of
  8, 2026-10-08) and **76 %** (10–15+). Dolby Atmos is always on
  (`DOMINANCE-LADDER-RUN-SHEET.md`; FIELD-NOTES 2026-10-08). Proposed:
  M12 protocols use 32 / 66 / 76 in place of 32 / 75, with Atmos on and
  recorded. **Decision D2.**
- **M8 has merged** (2026-10-09). The engine soft freeze is lifted, and
  `tests/test_engine.py` now covers the certification point M12-03
  changes, so M12-03 lands with tests that run offline.
- **Mac retired.** The charter already leaves macOS out of scope.

## Finding: the current audio stack cannot capture the playback reference

Checked 2026-10-09 by listing devices only, with no stream opened and
nothing recorded:

- `sounddevice` 0.5.6 bundles PortAudio "V19.7.0-devel". It exposes **no**
  WASAPI loopback device, and its `WasapiSettings` has no loopback option.
  No "Stereo Mix"-style input exists on JPad's Cirrus Logic stack (the
  WDM-KS inputs are unnamed endpoints).
- **`soundcard` 0.4.6** (pure Python over WASAPI via `cffi`; requires
  only `cffi` and `numpy`) was installed in a **throwaway venv** with the
  project's `numpy<2` pin (resolved 1.26.4). It lists
  `'Speakers (Cirrus Logic XU (with APO Extensions))'` as a 2-channel
  **loopback** input, beside the mic array.

So M12-01 needs a new dependency. Under the charter that is its own
evidence event: a fresh-venv suite run and a pin, decided before any
`src/` code uses it.

**Decision D1, the capture path.** Recommended: `soundcard`, pinned, used
only by the reference capture, off by default. Alternatives:

- `PyAudioWPatch`: a second PortAudio binding beside `sounddevice`, with
  two copies of the same C library in one process;
- a hand-written `ctypes` WASAPI loopback client: no dependency, but
  COM vtables by hand, the most code to own;
- routing Spotify through a virtual cable: this changes the playback path
  the mic hears, including Atmos, and invalidates every banked signature.
  Rejected.

## Questions the M12-01 probe must answer first

The charter's probe measures the mic-versus-reference delay, its drift
over 30 minutes, and reference level. Three more questions decide whether
cancellation (M12-02) is even well posed on this laptop:

1. **Is the loopback before or after Dolby Atmos?** WASAPI loopback taps
   the render stream, but where JPad's "APO Extensions" sit relative to
   that tap is not documented for this driver. If Atmos is applied after
   the tap, the canceller must learn the Atmos transfer function as well
   as the room.
2. **Is the reference level before or after the Windows volume slider?**
   This decides whether reference level tells us playback volume.
3. **Is there a clock offset?** The mic and the speakers are the same
   Cirrus Logic device, so drift may be negligible. Measure it; don't
   assume it.

## Execution order (every item is REQUIRES-REVIEW)

| # | Step | Who | Needs from founder |
|---|---|---|---|
| 1 | **M12-01a, dependency event:** `soundcard` in a fresh venv from `pip install -e .[dev]`, full suite, pin in `pyproject.toml` with a provenance comment | Claude | D1 + diff |
| 2 | **M12-01b, probe:** `scripts/probe_reference.py` records mic + loopback for the session and writes **numbers only** by default (delay, drift, levels, play/pause/skip tracking). An opt-in `--save-wav` writes both channels for offline work. Founder-run at 32 / 66 / 76 % | Claude writes, **founder runs** | plan + diff; consent for `--save-wav` |
| 3 | **M12-03 reference-free, offline:** measure candidate per-chunk "playback-dominated" signals on the 09-30 recording (`data/captures/m7-partf-2026-09-30-16k.wav`, `data/partf-replay/`). This is gate part (a). It changes no `src/` until a signal is chosen | Claude | D3 (start in parallel with 1–2) |
| 4 | M12-01c, `ReferenceSource` beside `MicSource`, off by default (`RTR_PLAYBACK_REFERENCE_ENABLED=0`, house tunable pattern) | Claude | plan + diff |
| 5 | M12-02, canceller (algorithm a measured choice; raw stream kept) | Claude | plan + diff |
| 6 | M12-03, reference-based gate at the certification point; additive per-window frame field | Claude | plan + diff |
| 7 | M12-04, clean energy as new RoomState fields; corpus `schema_version` bump | Claude | plan + diff |
| 8 | M12-05, pre-registered run sheet, then the paired live session (≥ 3 people) | Claude writes, **founder runs** | sheet approval |
| 9 | Gate (c), `bench_headcount.py --fallback` with the M12 path on | Claude | — |

Steps 1–3 are independent of each other. Step 3 is offline, so nothing
waits on a session for it.

**Decision D4: combine sessions.** The dominance ladder
(`docs/DOMINANCE-LADDER-RUN-SHEET.md`) is still unrun and needs the
founder's T2/T3 picks and a signed decision rule. Its takes are
music-only and speech-over-music at fixed volumes, which is exactly what
the M12-01 probe also needs. One sitting could run both: the ladder's
takes, with the probe recording the reference beside them. That would be
one founder session instead of two, and the ladder's dominance numbers
would come with a ground-truth playback level.

## Invariants M12 must keep

- **Heartbeat never blocks** (3): the reference capture runs on its own
  stream callback like `MicSource`; the canceller runs in capture/DSP time
  with no model; any classifier runs on a worker with a latest-wins result.
- **Certification stays centralized** (2): the playback gate slots into
  the engine's certification point, so emotion and headcount inherit it at
  once.
- **Raw is reconstructable**: the clean stream is added beside the raw
  one; frames carry the gate decision; new RoomState fields are additive.
- **Shadow is first-class** (7): with no reference (feature off, device
  missing, capture failed), everything behaves exactly as today.
- **Privacy**: the reference is everything the laptop plays, including
  calls and notifications. It is never written to disk unless a person
  opts in for a session.
- **Over-gating is a failure too**: M5 measured "blindness, not
  phantoms" at 93 %. Silence holds and staleness grows, never "empty".

## Decisions requested

- **D1.** Capture path: `soundcard` (recommended), pinned, behind a
  default-off flag.
- **D2.** Protocol volumes: 32 / 66 / 76 % (Atmos on) in place of the
  charter's 32 / 75 %.
- **D3.** Start the reference-free offline analysis (step 3) now, in
  parallel.
- **D4.** Combine the dominance ladder with the M12-01 probe in one
  founder session.
