# Milestone 8 Proposal — Trust the engine offline (DRAFT)

M7 proved the headcount middle. M8 proves the **engine orchestration**: the
code in `src/sensing/engine.py` that composes VAD, emotion, the M6
music-aware correction and headcount on every tick. Today
`Engine._tick`, `_bank_evidence` and `_correct` have no direct tests, so a
regression in the M6 estimator wiring can only be caught at a live gate.
Every engine correctness fix (M8-03, then all of M12) is blocked behind
making that code testable. M8 is that unblocking: a seam, a test suite, and
the small hardening items that ride along.

Source of direction: `ROADMAP.md` § M8 (charters, gate and acceptance
criteria, adopted verbatim; this proposal adds execution order and design,
not new criteria). Founder go-ahead: 2026-10-09.

**What M8 does not do.** The 2026-10-08 eight-person session read eight
people as `solo` under vocal playback (FIELD-NOTES 2026-10-08). That is
playback contaminating certification, which belongs to M12
(M12-03, playback-aware certification gate) and the open music-detection
item in FIELD-NOTES 2026-09-30. M8 changes no sensor behavior except where
M8-03 and M8-05 say so, and both are REQUIRES-REVIEW.

## Drift since the charter was written

- **Test count.** The charter says "all 288 existing tests pass
  unmodified". `main` @ `ae9775f` has **317**. The criterion is read as
  "every test on `main` at branch time passes unmodified": 317.
- **Line numbers.** The charter cites engine.py:185–390. At `ae9775f` the
  same code is `_tick` 210–335, `_bank_evidence` 337–380, `_correct`
  382–415. Consumer isolation (cited 162–166) is now `run()` 187–191.

## Gate (verbatim from ROADMAP.md)

(a) `pytest` green with a new `tests/test_engine.py` (or equivalent) suite
covering the enumerated scenarios below, all runnable offline in seconds
with no models; (b) `bench_headcount.py --fallback` re-run on the reference
machine (JPad) lands within run-to-run variance of the last
reference-machine README row, not the historical Mac rows; (c) a 10-minute
live dashboard smoke on JPad shows corrections, banking, and headcount
behaving as before (statuses ready, "hearing through music" chip fires
during a playback test, no exceptions in the log).

(b) and (c) are founder-run on JPad. The agent writes the run sheet
(`docs/M8-TEST-PLAN.md`) and runs the analysis; it never opens the mic.

## Execution order

| Order | Item | Review | Why here |
|---|---|---|---|
| 1 | **M8-01** extract the music-aware correction | agent | the seam everything else needs |
| 2 | **M8-02** engine orchestration tests | agent | needs the seam; pins M8-03's two behaviors first |
| 3 | M8-04 resampler tests | agent | independent; demo-critical for 48 kHz mics |
| 4 | M8-06 token-refresh lock, M8-07 idempotent shutdown, M8-09 annotations off the loop, M8-10 `httpx2` | agent | small, independent; M8-07 touches engine.py, so after M8-02 |
| 5 | M8-08 `pause()` 403 | agent | implement as specced or leave (CLAUDE.md) |
| 6 | **M8-03** bind corrections to the reading's context | **founder: plan + diff** | changes published V/A; flips two pinned M8-02 tests deliberately |
| 7 | **M8-05** `separation_score` all-singleton → None | **founder: plan + diff** | changes a published headcount field |
| 8 | test plan, then gate (b)/(c) on JPad | founder runs | the gate |

The engine soft freeze holds throughout: nothing touches engine.py except
M8-01, M8-03 and M8-07, each as its charter specifies.

## M8-01 design

New module `src/sensing/music_aware.py`, class `MusicAwareCorrector`,
engine-thread only (it holds the engine's `TrackSignatureStore`, which is
engine-thread-only by contract).

```python
corrector = MusicAwareCorrector(config, signatures)
out = corrector.process(reading, staleness, playback_active,
                        playback_track_id, music_dominance, now)
# out.valence, out.arousal, out.confidence, out.correction
```

`process()` is the old inline block, unchanged in order: bank the raw
reading (`_bank`, formerly `Engine._bank_evidence`), then, if dominance is
present and > 0, correct it (`_correct`) or apply the discount floor.

**The collaborator owns** freshness, dedup by `reading.at`, clean/mixed
classification, baseline updates, pull-sample banking, basis selection,
correction and the discount floor. **The engine keeps** worker
submission, reference taps (`submit_reference`/`pop_reference` →
`signatures.add_reference`), the dominance computation from the DSP
frame, the EMAs and publishing. It still owns the `TrackSignatureStore`
(construction, reference taps, `flush()` on stop) and passes it in.

How each charter trap is kept:

1. Dedup stays keyed on `reading.at`, never tick time.
2. The freshness bound `emotion_min_interval_s + hop_s` is computed from
   config on every call, not stored at construction.
3. The early-`return` structure of `_bank_evidence` is moved verbatim:
   baseline update only for fresh, deduped, clean readings; a pull sample
   only at dominance ≥ `music_pull_m_floor` with a baseline younger than
   `music_baseline_max_age_s`.
4. The discount floor applies only when correction returns None *and*
   dominance > 0.
5. The engine constructs the corrector only when it has a signature store
   (music-aware and emotion both enabled), exactly the old condition under
   which dominance was ever non-None. The corrector is called from
   `_tick` only.

**Proof of "pure refactor"** (two commits):

- *Commit 1, before any `src/` change:*
  `tests/test_music_aware_characterization.py` holds a verbatim frozen copy
  of the pre-refactor logic (`_bank_evidence`, `_correct` and the inline
  tick block, from `ae9775f`). A 60+ step script (clean → mixed → track
  change → baseline goes stale → playback stop, plus dedup, stale readings,
  the dominance dead band, `m == 0`, a `None` track and standalone-basis
  reference taps) drives the frozen copy and the **live** `Engine` methods
  side by side. Every step's (valence, arousal, confidence, correction)
  must be identical with exact float equality. So must the final signature
  store and clean baseline. This proves the frozen copy is faithful.
- *Commit 2, the refactor:* the same script now drives the frozen copy
  and `MusicAwareCorrector`. Faithful copy = old engine and corrector =
  faithful copy, so corrector = old engine, byte for byte.

## M8-02 plan (sketch; detailed when M8-01 lands)

`tests/test_engine.py` drives the real `Engine._tick` with fakes: an
`AudioSource` over a real `RingBuffer` (so `read_since` is exercised), a
scripted VAD, scripted emotion and headcount workers, and a scripted
`PlaybackStateSource`. Each acceptance bullet in the charter is one test.
The track-boundary and playback-stop tests pin today's behavior with a
comment naming M8-03.

Open design point for founder review: `_tick` reads `time.monotonic()`
and `time.time()` itself. The charter says not to mock `time.monotonic`
globally and to inject time through the tick path. Two options:

- **(i)** `_tick(now=None, wall=None)`. Two optional parameters, no
  behavior change, production passes nothing. This is one more line of
  engine change, made inside M8-02.
- **(ii)** Drive time by patching only `sensing.engine.time`, the
  module-local binding. The fake workers have no threads, so the charter's
  concern doesn't arise, but it is closer to what the charter warns
  against.

Recommendation: (i). M8-02's scope says "no `src/` changes beyond M8-01",
so (i) would be folded into M8-01's commit 2 as part of the seam.

## Risks

- **A characterization test that shares code with what it tests proves
  nothing.** The frozen copy is a separate transcription. Commit 1 checks
  it against the live engine before the engine changes.
- **Exact float equality is the bar, not tolerance.** If any step differs,
  the refactor changed an operation order. Fix the refactor; don't loosen
  the test.
- **Benchmark (b).** The refactor adds one method call per tick. That's
  expected to be within noise, and the gate measures it.
