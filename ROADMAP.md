# ROADMAP — Read the Room, post-M7

Two goals, in priority order: **(1) live-demo readiness** — a 60+ minute live event
where a musician performs while RTR visibly responds, ending with export of a
shareable "energy curve" artifact of the session; **(2) commercialization
readiness** — a codebase a small team can onboard onto and ship.

Ground rules for the executing agent:

- **M7 is in flight and must not be blocked.** The trio night, parts (e-live)/(f),
  and the merge of `milestone-7-stable-middle` happen first. Every item below
  branches from `main` **after** that merge. Do not push anything to the M7 branch.
- **Milestones are evidence-gated** (repo convention): a milestone closes only when
  its stated gate is observed and recorded (docs/FIELD-NOTES.md entry + README gate
  table row), never when its items are merely merged.
- **Sequencing is load-bearing:** M8 makes the engine safe to change; nothing that
  modifies `src/sensing/engine.py` may land before M8-01/M8-02 are complete.
- Items tagged **REQUIRES-REVIEW** must not be executed autonomously: get explicit
  human sign-off on the plan *and* the diff. They touch the audio path, published
  sensor semantics, or session/corpus capture.
- Findings referenced as "AUDIT finding N" are from `AUDIT.md` (2026-07-19).

---

## M8 — Trust the engine offline

**Charter.** M7 proved the headcount middle; M8 proves the *engine orchestration*
— the code that composes VAD, emotion, the M6 music-aware correction, and
headcount every tick — without a live session. Today `Engine._tick`,
`_bank_evidence`, and `_correct` (engine.py:185–390) have zero direct tests, so a
regression in the M6 estimator wiring can only be caught at a live gate; every
correctness fix in this milestone is blocked behind making that code testable.
**Gate:** (a) `pytest` green with a new `tests/test_engine.py` (or equivalent)
suite covering the enumerated scenarios below, all runnable offline in seconds
with no models; (b) `bench_headcount.py --fallback` re-run on the Mac lands within
run-to-run variance of the M7 row (README convention — refactors add no compute);
(c) a 10-minute live dashboard smoke on the Mac shows corrections, banking, and
headcount behaving as before (statuses ready, "hearing through music" chip fires
during a playback test, no exceptions in the log).

### M8-01 — Extract the music-aware correction into an engine-thread collaborator

- **Problem:** The M6 pull-estimator wiring is inlined in `Engine._tick` /
  `_bank_evidence` / `_correct` (src/sensing/engine.py:185–390). It touches only
  `_signatures`, `_clean_baseline`, and `_last_banked_at`, but cannot be unit
  tested because it is welded to the tick loop, the workers, and the audio source
  (AUDIT finding 1).
- **Why it matters:** This is the single highest-leverage change for both goals.
  Demo: the next estimator regression gets caught by pytest instead of on stage.
  Commercialization: the engine is the file a new team member fears most; a
  seam here is the difference between "readable" and "don't touch".
- **Scope:** `src/sensing/engine.py`, new module `src/sensing/music_aware.py` (or
  extend `src/sensing/music.py`). The collaborator owns: freshness check, dedup by
  `reading.at`, clean/mixed classification, baseline updates, pull-sample banking,
  basis selection (pull vs standalone vs none), correction application, and the
  discount-floor confidence fallback. The engine keeps: worker submission,
  reference-tap plumbing (`submit_reference`/`pop_reference`), EMA smoothing, and
  publishing. **Out of scope:** any behavior change whatsoever; `music.py`
  primitives (`dominance`, `apply_correction`, `TrackSignatureStore`,
  `CleanBaseline`); the workers; headcount; config values.
- **Acceptance criteria:**
  - A pure refactor: for any sequence of (reading, playback_active, track_id,
    dominance, now) inputs, the new collaborator produces byte-identical published
    `valence`/`arousal`/`emotion_confidence`/`emotion_correction` values to the
    pre-refactor code. Demonstrate with a characterization test written **before**
    the refactor: drive the old inline logic and the new collaborator with the
    same scripted 50+ step sequence (covering clean → mixed → track-change →
    playback-stop) and assert identical outputs.
  - All 288 existing tests pass unmodified.
  - The engine no longer references `_last_banked_at`, `_clean_baseline`, or
    signature basis selection directly.
- **Risk notes:** A naive extraction changes semantics silently. The traps:
  (1) `_last_banked_at` dedup keys on `reading.at`, not tick time — preserve
  exactly; (2) the freshness bound is `emotion_min_interval_s + hop_s`, computed
  from config at call time — do not bake it into the collaborator's constructor;
  (3) `CleanBaseline.update` must happen **only** for fresh, deduped, clean
  readings, and `add_pull_reference` only when dominance ≥ `music_pull_m_floor`
  AND the baseline is younger than `music_baseline_max_age_s` — keep the early
  `return` structure; (4) the discount floor multiplies confidence only when
  `_correct` returns None *and* dominance > 0; (5) `TrackSignatureStore` is
  engine-thread-only by contract (music.py docstring) — the collaborator must be
  called from the tick only, never from a worker.
- **Effort:** M

### M8-02 — Engine orchestration test suite

- **Problem:** No `tests/test_engine.py` exists; the composition logic (AUDIT
  finding 1) and the engine→bridge→mapper live path (AUDIT test-suite note) are
  exercised only via live sessions or `FakeEngine` stubs.
- **Why it matters:** Demo: gate-critical wiring becomes regression-checked
  offline. Commercialization: the suite is executable documentation of the tick
  contract.
- **Scope:** New `tests/test_engine.py` (+ fixtures in `tests/conftest.py` if
  needed). Build a fake audio source (satisfying the `AudioSource` protocol,
  engine.py:51) and fake emotion/headcount workers or a scripted collaborator.
  **Out of scope:** changes to `src/` beyond what M8-01 already landed; loading
  real models in tests (suite must stay offline and fast).
- **Acceptance criteria — each of these is a distinct test that fails if the
  behavior regresses:**
  - Dedup: the same `EmotionReading` (same `.at`) surfaced across 3 ticks banks
    exactly one baseline update / pull sample.
  - Freshness: a reading older than `emotion_min_interval_s + hop_s` banks
    nothing.
  - Clean vs mixed: playback off → baseline updates; playback on with dominance
    ≥ `music_pull_m_floor` and a fresh baseline → exactly one pull sample of
    value `(reading − baseline) / m`; dominance between `music_baseline_m_max`
    and `music_pull_m_floor` → neither.
  - Stale baseline: with the baseline older than `music_baseline_max_age_s`, a
    mixed reading banks no pull sample.
  - Basis order: pull signature (≥ min_refs) beats standalone beats discount
    floor; the published `emotion_correction` dict carries the right `basis` and
    `refs`.
  - Discount floor: with no usable signature and dominance m, published
    confidence equals `raw_conf * (1 − γ·m)` clamped at 0.
  - Track boundary: a fresh reading corrected under track A, then track B
    becomes current — assert the *current* (possibly wrong-track) behavior as a
    pinned characterization test with a comment referencing M8-03, so the fix
    changes a test deliberately rather than silently.
  - Playback stop: playback_active flips false while a reading is fresh — pin
    current behavior (uncorrected EMA feed) the same way.
  - Consumer isolation: a consumer whose `on_state` raises does not prevent the
    next consumer from receiving the state (engine.py:162–166).
  - Publish path: `noise_floor_dbfs` only updates on raw_ratio < 0.1 windows;
    mood is None once staleness exceeds `emotion_max_staleness_s`.
- **Risk notes:** Do not mock `time.monotonic` globally (workers use it on other
  threads); inject timestamps through the tick path instead — the engine already
  threads `now` explicitly through `_bank_evidence`/`latest`. Keep the fake
  source's ring buffer real (use `RingBuffer`) so `read_since` position
  arithmetic is exercised.
- **Effort:** M

### M8-03 — Fix stale-reading correction: bind corrections to the reading's context — REQUIRES-REVIEW

- **Problem:** AUDIT finding 2. The engine re-feeds the latest (possibly stale)
  reading into the V/A EMAs every tick, but corrects it with the *current* tick's
  track and dominance (engine.py:255–279). Across a track boundary a reading from
  track A is corrected with track B's signature for up to
  `emotion_min_interval_s + hop_s`; when playback stops, a still-fresh
  contaminated reading feeds the EMA fully uncorrected until it ages out.
- **Why it matters:** Corpus integrity: published valence/arousal is what gets
  stamped into annotations and overrides; a few seconds of wrong-track
  correction per boundary is exactly the drift the M6 estimator exists to remove.
- **Scope:** The M8-01 collaborator (and `src/sensing/engine.py` only where the
  collaborator's inputs are assembled): capture `(playback_track_id, dominance)`
  *at the tick where a reading first appears* and reuse that pair for the
  reading's remaining lifetime. Update the two pinned characterization tests
  from M8-02. **Out of scope:** the EMA re-feed design itself (feeding the latest
  reading every tick is intentional smoothing); signature storage; worker code.
- **Acceptance criteria:**
  - The M8-02 track-boundary test now asserts the correction uses track A's
    signature for A's reading even after B starts.
  - The playback-stop test now asserts a reading taken during playback remains
    corrected (with its captured context) after playback stops, and a reading
    taken after the stop is uncorrected.
  - `emotion_correction.track_id` published on a frame always names the track
    the correction was computed against.
  - Live smoke (part of the M8 gate): during a playback session with a track
    change, the log/frames never show `emotion_correction.track_id` ≠ the track
    that was playing when the reading landed.
- **Risk notes:** The captured context must be per-*reading* (keyed by
  `reading.at`), not a second engine-level "last track" variable — otherwise
  rapid submit/complete interleavings re-introduce the bug. Do not capture
  context at *submit* time inside the worker (the worker must stay ignorant of
  playback; the reference-tap path already handles the music-only case).
  REQUIRES-REVIEW because it changes published sensor values that feed the
  corpus.
- **Effort:** M

### M8-04 — Resampler tests + fractional-position edge

- **Problem:** AUDIT finding 8c. `Resampler` (src/sensing/audio.py:80–115) is the
  only untested class in `sensing`; `_frac` can go slightly negative across
  blocks (audio.py:114), silently clamped by `np.interp`.
- **Why it matters:** Demo: the Mac's mic path may open at 48 kHz and resample
  live on stage; this code must not be a black box. (The 2026-07-15 gate ran on
  this hardware — treat the resampler as demo-critical.)
- **Scope:** New tests in `tests/test_state.py` or a new `tests/test_audio.py`;
  a minimal fix in `audio.py` for the fractional edge **only if** the tests
  demonstrate output-rate drift. **Out of scope:** replacing the resampler with
  scipy (the no-scipy constraint is documented in the class docstring); MicSource
  / PortAudio code.
- **Acceptance criteria:**
  - Block-boundary invariance: resampling a 10 s 48 kHz sine in one call vs. in
    93-sample odd-sized blocks yields sample counts within ±2 and max absolute
    sample difference below a stated tolerance in the steady-state region.
  - Rate correctness: total output samples over 10 s of 48 kHz and 44.1 kHz
    input are each within 0.1% of `duration * 16000`.
  - A test asserts `_frac` stays ≥ 0 across 1000 random-sized blocks, or the
    edge is fixed and the test asserts the fix.
- **Risk notes:** Do not "fix" the negative `_frac` by clamping to 0 (that is
  what already effectively happens and it drops phase); the correct invariant is
  frac ∈ [0, ratio). Tolerances must account for the FIR group delay
  ((TAPS−1)/2 samples) — compare steady-state, not the first 63 samples.
- **Effort:** S

### M8-05 — `separation_score`: all-singleton case returns None, not 0.0 — REQUIRES-REVIEW

- **Problem:** AUDIT finding 3. When every cluster is a singleton (n ≥ 3),
  `separation_score` returns 0.0 (src/sensing/headcount.py:275), which
  `sep_collapse` reads as maximal collapse (headcount.py:549) — the same
  "undefined read as collapsed" class the M7 sep_collapse fix corrected for the
  single-cluster case. Currently masked because count_pressure and smear are ~0
  in that scenario.
- **Why it matters:** Corpus integrity / demo: a latent inversion in the crowd
  blend is exactly what M7 spent a milestone rooting out; the next calibration
  pass could unmask it.
- **Scope:** `src/sensing/headcount.py` (`separation_score` return; verify
  `sep_collapse` and the confidence term `(separation or 0.0)`,
  headcount.py:570, still behave sensibly with the new None). Tests in
  `tests/test_headcount.py`. **Out of scope:** any ramp/threshold value; the
  rescue path; BucketSmoother.
- **Acceptance criteria:**
  - New test: 3 mutually distant singleton embeddings → `separation_score`
    returns None; the resulting `Estimate.crowd_weight` equals what the
    dispersion evidence alone implies (mirroring the M7 single-cluster
    semantics), asserted against a hand-computed value.
  - Existing 40 headcount tests pass unmodified (if any fail, the change is not
    the minimal one — stop and re-read).
  - Offline replay evidence, repo convention: re-run
    `scripts/m7_replay_session.py` on the 2026-07-15 gate WAV before and after;
    the per-bucket hop histogram must be identical (this scenario should not
    occur in that recording — identical output is the no-regression proof).
- **Risk notes:** `count_conf = evidence * max(0.2, min(1.0, 0.4 + (separation
  or 0.0)))` already treats None as neutral — do not "improve" it in passing.
  This must land **after the M7 merge** (it touches the file the M7 gate is
  validating). REQUIRES-REVIEW: published headcount semantics.
- **Effort:** S

### M8-06 — Serialize Spotify token refresh

- **Problem:** AUDIT finding 4. `_access_token`/`_refresh`
  (src/playback/spotify.py:127–161) run unlocked from both the controller worker
  and the HTTP threadpool; Spotify rotates refresh tokens on PKCE apps, so a
  concurrent double-refresh can persist the losing token and force manual
  re-auth ("degraded" mid-event).
- **Why it matters:** Demo: a mid-set forced re-auth is a show-stopper.
- **Scope:** `src/playback/spotify.py` only: a `threading.Lock` around the
  check-expiry-then-refresh critical section. Test in `tests/test_spotify.py`.
  **Out of scope:** controller code; auth flow (`spotify_auth.py`); retry policy.
- **Acceptance criteria:**
  - New test: two threads call a token-requiring method simultaneously through
    `httpx.MockTransport` with an expired cache; the transport records exactly
    one POST to the token URL.
  - The lock covers only token state (read cache, decide, refresh, write
    cache); a test or assertion demonstrates API request I/O
    (`_client.request` to API_BASE) is never made while the lock is held.
- **Risk notes:** Match the repo's outside-the-lock discipline
  (controller.py:239–355): the token POST itself is I/O and *must* happen under
  the lock here (that's the point — one refresher), but the subsequent API call
  must not. Double-checked locking: re-read expiry after acquiring, so the
  second thread returns the fresh token instead of refreshing again. Don't
  forget the 401-retry path (spotify.py:176) reaches `_refresh` too.
- **Effort:** S

### M8-07 — Idempotent shutdown

- **Problem:** AUDIT finding 5. `engine.run()`'s finally and the dashboard main
  thread (src/dashboard/__main__.py:167) both call `Engine.stop()`;
  `MicSource.stop()` (src/sensing/audio.py:159–163) is an unlocked
  check-then-act, and `TrackSignatureStore.flush()` can run concurrently with
  the engine thread's own flush.
- **Why it matters:** Demo: shutdown races surface as scary tracebacks in front
  of an audience and can corrupt the signatures file's final write.
  Commercialization: embarrassing in any packaged form.
- **Scope:** `src/sensing/engine.py` (`stop()` guarded by a once-flag/lock),
  `src/sensing/audio.py` (`MicSource.stop`, `SynthSource.stop` idempotent under
  a lock). **Out of scope:** worker `stop()` methods (already event-based and
  idempotent); uvicorn lifecycle; signal handling.
- **Acceptance criteria:**
  - New test: `Engine.stop()` called from two threads concurrently 100× with a
    fake source — source `stop()` observed exactly once per run, no exceptions.
  - New test: `MicSource`-shaped double-stop (with `_stream` a Mock) calls
    `stream.stop()`/`close()` at most once.
  - Existing suite green.
- **Risk notes:** Don't make `stop()` block on `join()`ing daemon workers — the
  current non-joining design is deliberate (daemon threads, latest-wins). The
  flush must still run exactly once even if the *first* stop was the engine
  thread's own finally.
- **Effort:** S

### M8-08 — `pause()` 403 handling: swallow only "already paused"

- **Problem:** AUDIT finding 8d. `pause()` treats any 403 as "goal state holds"
  (src/playback/spotify.py:241); 403 also covers Premium-required and device
  restrictions, where it does not.
- **Why it matters:** Demo: a skip-to-silence that silently fails leaves music
  playing against the operator's intent, with "logged ✓" on screen.
- **Scope:** `src/playback/spotify.py` `pause()` only: parse the error body's
  `reason` field and swallow only the already-paused/no-op class; re-raise the
  rest. Tests in `tests/test_spotify.py` via MockTransport. **Out of scope:**
  other endpoints' error handling; controller retry logic.
- **Acceptance criteria:**
  - Test: 403 with a restriction/premium `reason` in the body → `ProviderError`
    raised with `.status == 403`.
  - Test: 403 with an already-paused-class body (and a body-less 403, the
    ambiguous case — document the chosen default in a comment) behaves as
    specified.
- **Risk notes:** Spotify's error body shape is
  `{"error": {"status", "message", "reason"?}}` and `reason` is not always
  present — the ambiguous no-reason case should stay permissive (current
  behavior) so a mid-demo pause against an already-paused player doesn't start
  raising. Cite the observed body shapes in the test fixtures.
- **Effort:** S

### M8-09 — `/annotations` handler: move file I/O off the event loop

- **Problem:** AUDIT finding 8e. The annotations endpoint is `async def` but
  appends to disk synchronously (src/dashboard/app.py:82–93); the overrides
  endpoint is already correctly sync (app.py:96).
- **Why it matters:** Commercialization polish; at demo write rates it's
  harmless, but it's a one-word fix and removes a class of "why did the
  websocket hiccup" mysteries.
- **Scope:** `src/dashboard/app.py`: change `async def annotate` to `def`
  (FastAPI threadpool), mirroring the overrides handler and its docstring.
  **Out of scope:** everything else in the file.
- **Acceptance criteria:** `tests/test_dashboard.py` suite passes unmodified;
  the handler is `def`, with the same one-line rationale comment style as the
  overrides handler.
- **Risk notes:** `note_tap()` and `append_annotation` are thread-safe already
  (PresenceGate docstring documents the tolerated float race) — do not add
  locking.
- **Effort:** S

### M8-10 — Resolve the `httpx2` dev dependency

- **Problem:** AUDIT packaging note. `pyproject.toml:53` lists `httpx2` in the
  dev extras — an unusual package name; Starlette's TestClient normally needs
  only `httpx`. If it's a typo'd or abandoned third-party package it is a supply
  chain risk installed on every dev machine.
- **Why it matters:** Commercialization: dev-environment supply chain hygiene.
- **Scope:** `pyproject.toml` dev extras; verification only otherwise.
  **Out of scope:** runtime dependencies; version pins (the torch/numpy pins are
  load-bearing and documented — do not touch).
- **Acceptance criteria:** Either (a) `httpx2` removed and the full suite passes
  in a **fresh** venv built from `pip install -e .[dev]`, or (b) a comment in
  `pyproject.toml` documenting exactly which import breaks without it,
  reproduced in the fresh venv. The fresh-venv run is the evidence either way.
- **Risk notes:** Test in a clean venv, not the current one — the current env
  may satisfy the import from an unrelated install. Note the existing comment
  claims newer Starlette "requires the httpx2 package specifically"; verify
  against the *pinned* dependency resolution, not the claim.
- **Effort:** S

---

## M9 — Stage-ready: the live-performance demo

**Charter.** M9 proves RTR can run a 60+ minute live-music event and hand the
musician a shareable artifact at the end. This requires facing an uncomfortable
fact head-on: the engine is *speech*-gated by design (README architecture §2), so
during instrumental/sung performance the emotion and headcount layers will be
mostly stale and the "visible response" must come from the always-on DSP
heartbeat (loudness, activity, spectral balance, energy, trend) — and live music
arrives at the mic with `playback_active=false`, so none of the M6 contamination
machinery applies. M9 therefore measures how the engine actually reads a live
performer before building the presentation on top. **Gate:** a full dress
rehearsal — 60+ continuous minutes of live/loud music on the Mac, dashboard
visibly tracking throughout, zero unhandled exceptions, memory/CPU flat per the
soak protocol, ending with a one-command export of the session's energy-curve
artifact; runbook followed end-to-end by a human who did not write it.

### M9-01 — Session recorder: full-session RoomState log from the dashboard — REQUIRES-REVIEW

- **Problem:** The dashboard keeps only ~10 minutes of frames
  (`history_maxlen`, src/dashboard/__main__.py:112) and, unlike the CLI
  (`--jsonl`, src/sensing/__main__.py:76), never persists frames. A 60-minute
  energy curve has no data source.
- **Why it matters:** Demo goal 1 directly: no session log → no exportable
  artifact. Also gives soak runs (M9-05) their evidence stream.
- **Scope:** `src/dashboard/__main__.py` (wire an additional consumer),
  reusing `src/sensing/consumers/jsonl.py` (JsonlWriter) unchanged if possible;
  a new env var `RTR_DASHBOARD_SESSION_LOG_DIR` (default `data/sessions/`,
  gitignored — confirm `data/*` ignore covers it) writing
  `data/sessions/YYYY-MM-DD-HHMMSS.jsonl` per process start; document in
  `.env.example`. Record the *frame* the bridge publishes if cheap, else raw
  `RoomState.to_dict()` — state which, in the file header line. **Out of
  scope:** the engine; the bridge's websocket path; any UI; retention/rotation
  policy (one file per session is fine).
- **Acceptance criteria:**
  - New test: dashboard-style wiring with a synthetic source writes ≥ N lines
    for N ticks; each line round-trips `json.loads` and contains `timestamp`,
    `loudness_dbfs`, `energy`, `trend`.
  - A 2-minute live synth-source run produces a file whose line count matches
    tick count ±1, appended (never truncated) across a restart into the same
    day.
  - Writer failure (disk full, permission) is logged and never takes down the
    engine tick — test with a consumer whose write raises (the engine's
    consumer isolation, engine.py:162–166, is the mechanism; assert it holds
    for this consumer).
- **Risk notes:** Do not buffer frames in memory for the session duration —
  append per tick with line buffering (JsonlWriter already does,
  `buffering=1`). One file handle for the whole session; do not reopen per
  frame. REQUIRES-REVIEW: this creates a new always-on capture of room-derived
  data (privacy-adjacent, same class as annotations/overrides) — a human must
  approve the default-on/off decision; recommend default **off**, enabled in
  the demo runbook.
- **Effort:** S

### M9-02 — Energy-curve export artifact

- **Problem:** No exporter exists. The demo must end with a shareable artifact
  of the session's energy over time.
- **Why it matters:** Demo goal 1 — it is the closing moment of the event.
- **Scope:** New `scripts/energy_curve.py`: reads a session JSONL (M9-01
  format), renders a **self-contained** SVG or single-file HTML — energy curve
  over wall-clock time, loudness as a secondary trace, trend
  rising/falling shading, session start/end and duration in the title; input
  path + output path as CLI args. New `tests/test_energy_curve.py` with a
  synthetic JSONL fixture. **Out of scope:** the dashboard UI (no export
  button this milestone — a terminal command is acceptable for the gate);
  external chart libraries beyond what's already vendored (self-contained
  output, no CDN references); PNG rasterization.
- **Acceptance criteria:**
  - `python scripts/energy_curve.py data/sessions/X.jsonl out.svg` on a
    60-minute synthetic fixture (18k lines) completes in < 10 s and produces a
    file that opens in a browser with no network access.
  - Test: known synthetic input (energy ramp 0→1 over 100 frames) produces a
    path/polyline whose parsed coordinates are monotonic in both axes.
  - Handles gaps (engine restarts mid-session) by breaking the line, not
    interpolating across the gap — test with a fixture containing a 5-minute
    timestamp jump.
  - Malformed lines are skipped with a count reported to stderr, never a crash
    — test with a fixture containing truncated JSON lines.
- **Risk notes:** Use `timestamp` (wall clock) for the x-axis, not line index —
  ticks are not perfectly uniform and restarts create jumps. Null-valence
  frames are normal (no speech): the energy series is always present, but do
  not plot valence/arousal traces unless nulls are handled. Keep the aesthetic
  self-contained and simple; this is a keepsake, not a dashboard.
- **Effort:** M

### M9-03 — Live-performance characterization session — REQUIRES-REVIEW

- **Problem:** Unknown, and unmeasurable offline: how the engine reads a *live
  musician*. Known hazards from the field notes: sung vocals may pass VAD and
  read as a stable phantom speaker (headcount.py module docstring, "Confirmed
  live 2026-07-03" — TV dialogue counted as occupants); a hot input can push
  the crowd/babble heuristic (README M4 caution); sustained loud music will
  drag the rolling noise floor (config.py:66). None of the M6 machinery applies
  (playback_active is false for live sound).
- **Why it matters:** Demo goal 1: the performance view (M9-04) and the runbook's
  mic-gain guidance must be built on measurement, not guesses — the repo's core
  discipline.
- **Scope:** A *protocol + evidence* item, not a code item. Write the protocol
  into `docs/M9-TEST-PLAN.md` (repo convention), execute it with a human
  present, record results in `docs/FIELD-NOTES.md`. Protocol must cover, with
  the session JSONL (M9-01) captured throughout: (a) instrumental-only passage;
  (b) sung-vocals passage; (c) performer + talking audience; (d) applause;
  (e) between-song banter; at ≥ 2 mic gain settings. **Out of scope:** ANY
  tuning changes during the session — this measures; changes are follow-up
  items with their own evidence.
- **Acceptance criteria:**
  - The FIELD-NOTES entry reports, per passage: speech_ratio distribution,
    headcount bucket trajectory + crowd_weight, noise-floor drift, energy/trend
    responsiveness (does the curve visibly track the set's dynamics?).
  - An explicit written decision per hazard: phantom-speaker on vocals
    (observed or not, magnitude), babble false-fire (observed or not), with
    either "acceptable for demo as-is" or a follow-up backlog item filed with
    the measured numbers.
  - The decision of what the performance view (M9-04) shows is written down and
    justified from the measurements.
- **Risk notes:** This is a measurement protocol — it must not smuggle in
  target numbers. Do not run with playback enabled (it would confound
  contamination tagging). An autonomous agent must not execute this: it
  requires a human, a musician, and judgment about recording people
  (REQUIRES-REVIEW; also record consent for any audio captured).
- **Effort:** M (mostly human time)

### M9-04 — Performance view for the dashboard

- **Problem:** The current page (src/dashboard/static/index.html) is an
  operator's debug view — small numbers, annotation buttons, regime
  diagnostics. Nothing on it reads from across a room, and during instrumental
  passages the quadrant/headcount cards will sit stale (correctly), which looks
  broken to an audience.
- **Why it matters:** Demo goal 1: "RTR visibly responds" is this item.
- **Scope:** `src/dashboard/static/` only — either a mode toggle in
  `index.html` or a second page `perform.html` served at `/perform` (one added
  route in `src/dashboard/app.py` following the existing `FileResponse`
  pattern, app.py:58–60, plus the package-data glob in `pyproject.toml:64`
  already covers `static/*.html`). Content driven by the M9-03 decision;
  expected: large energy curve (rolling window), loudness/activity/spectral
  live elements, trend indication; staleness-honest — stale layers hidden, not
  frozen. **Out of scope:** the websocket protocol and frame schema (consume
  what exists); server-side state; the operator view's behavior.
- **Acceptance criteria:**
  - Sequenced after M9-03; the view renders only signals M9-03 found
    responsive during performance, and the item's PR links the FIELD-NOTES
    justification.
  - Readable from 5 m on the demo machine's screen (subjective — human checks
    at rehearsal; the automated proxy: primary elements sized in viewport
    units, verified present in the HTML).
  - Reconnect behavior identical to the operator page (the existing
    backoff/overlay pattern, index.html:433–456).
  - `tests/test_dashboard.py` gains a smoke test asserting the route serves
    200 with `text/html`.
- **Risk notes:** Do not add per-frame DOM churn that grows unbounded over 60
  minutes — the operator page prunes `timeline`/`trail` arrays by time
  (index.html:247, 303); mirror that or the soak test will catch a browser-tab
  leak. Never invent values for stale layers to keep the screen lively; the
  repo's honesty-about-staleness principle applies to pixels too.
- **Effort:** M

### M9-05 — 60-minute soak protocol — REQUIRES-REVIEW

- **Problem:** The repo's gates measure per-hop latency, never endurance. The
  demo requires 60+ minutes; unbounded-growth candidates the audit noted as
  individually bounded (history deques, attribution caps, trend pruning,
  engine-thread disk writes — AUDIT findings 8a/8b context) have never been
  observed *together* over an hour under load.
- **Why it matters:** Demo goal 1: the failure mode "it slowed down 40 minutes
  in" is only findable this way. Also the standing monitor for AUDIT findings
  8a (engine-thread disk I/O) and 8b (post-stall VAD flood), which are
  accepted/deferred on the strength of this protocol existing.
- **Scope:** New `docs/M9-TEST-PLAN.md` section + a small observer script
  (`scripts/soak_report.py`) that post-processes a session JSONL for: tick-rate
  regularity (inter-frame timestamp deltas: count of gaps > 2× hop),
  staleness-age distributions, plus a psutil-free memory sampling method
  documented in the protocol (e.g. periodic `ps -o rss` capture to a file).
  **Out of scope:** fixing anything it finds (file follow-up items with the
  measurements); load-generation tooling beyond "play music at the mic".
- **Acceptance criteria:**
  - The protocol document specifies: duration ≥ 60 min, source = live/loud
    music at the validated mic, dashboard + one browser client connected
    throughout, measurements captured (RSS over time, tick-gap count,
    worker statuses at end), and a pass rule stated as *flat-line criteria
    measured against the session's own first 10 minutes* (evidence-first: no
    absolute MB targets).
  - `scripts/soak_report.py` run on a session JSONL prints the tick-gap count
    and staleness distributions; tested against a synthetic fixture with one
    injected 10 s gap (must report exactly one).
  - One full execution recorded in FIELD-NOTES (this doubles as the M9 gate
    rehearsal's instrumentation).
- **Risk notes:** Run on the demo Mac, not the dev box (the two-machine trap is
  documented project history). Browser-tab memory is part of the system under
  test — keep the performance view open the whole hour. REQUIRES-REVIEW: the
  run itself is human-supervised and involves recording a live room.
- **Effort:** M

### M9-06 — Demo-day runbook

- **Problem:** Setup knowledge lives across README sections, test plans, and
  field notes (mic permissions README:104–109, device pinning, Spotify auth,
  degraded modes). On demo day under pressure, nobody greps FIELD-NOTES.
- **Why it matters:** Demo goal 1: operational readiness is the difference
  between a demo and a debugging session with an audience.
- **Scope:** New `docs/DEMO-RUNBOOK.md`. Sections: pre-event checklist (env
  vars incl. session log on, mic permission + input-level check with the M4
  hot-mic caution, disk space, `--ticks 10` smoke); start-of-show procedure;
  in-show playbook for each failure the code can surface (`degraded` status →
  what it means/what to do, mic gone silent → the macOS permission trap,
  websocket disconnect → the auto-reconnect expectation, advisory banner →
  meaning); end-of-show export procedure (M9-02 command); rollback decisions
  (disable a layer via `--no-emotion`/`--no-headcount` rather than fight it
  live). **Out of scope:** code changes — if writing the runbook reveals a
  needed knob, file an item, don't patch inline.
- **Acceptance criteria:**
  - A human who did not write it executes it cold on the demo machine, from
    laptop-closed to dashboard-up, timed; every step either works as written
    or gets a correction commit. This execution is part of the M9 gate.
  - Every dashboard status string a user can see (`shadow`, `degraded`,
    `PLAYBACK DEGRADED`, advisory banner, stale chips) appears in the playbook
    with an action.
- **Risk notes:** Keep it to one page of checklists + one page of playbook;
  a runbook that reads like documentation won't be used mid-show. The
  degraded-Spotify path matters even though the musician performs live —
  pre/post-set ambient music is plausibly playing through RTR.
- **Effort:** S

---

## M10 — A codebase a team can hold

**Charter.** M10 proves a stranger can become productive without this
conversation, the founder, or the field notes as oral history: CI enforces what
the gates used to enforce by hand, the implicit contracts (frames, env knobs,
script constants) become explicit, and the security posture is stated rather
than assumed. **Gate:** cold-onboarding evidence — on a machine that has never
built the project, a person (or agent) with only the repo goes from clone to
green `pytest` and a running synth-source dashboard using only committed docs,
with every stumble captured as a doc fix; plus CI green on a PR that
deliberately breaks a test (proving it gates).

### M10-01 — CI: test + lint on every PR

- **Problem:** No CI exists; "288 tests, 3.9 s, fully offline" is enforced only
  by discipline. The suite's offline-ness (no model downloads — AUDIT test
  section) makes CI cheap, but nothing guards it staying that way.
- **Why it matters:** Commercialization: the first thing a team needs; also
  guards the M8 invariants permanently.
- **Scope:** `.github/workflows/ci.yml`: Python 3.12 (respect the
  `>=3.12,<3.13` pin, pyproject.toml:13), `pip install -e .[dev]`, `pytest`,
  plus `ruff check` with a minimal committed `ruff.toml` (start from zero
  autofixes: rule set chosen so the current tree passes **unmodified** —
  codify, don't churn). Cache pip. **Out of scope:** reformatting the codebase;
  macOS runners (the Mac gate is a human protocol, not CI); coverage gates.
- **Acceptance criteria:**
  - CI passes on an unmodified main; a PR with a deliberately broken assertion
    fails; both runs linked in the item's closing note.
  - Wall time under 10 minutes with a warm cache (torch CPU is the long pole;
    document the measured install time).
  - A network-isolation check: the test job sets `HF_HUB_OFFLINE=1` and
    `TRANSFORMERS_OFFLINE=1`, so any future test that tries to download a model
    fails loudly in CI.
- **Risk notes:** Do not "fix" lint findings in the same PR that introduces the
  linter — config the linter to the tree, then tighten in later PRs. The torch
  pin means `pip install` needs the default PyPI CPU wheels for Linux —
  verify no `--index-url` gymnastics are required before assuming.
- **Effort:** M

### M10-02 — Config truth: wire or un-document the phantom knobs

- **Problem:** AUDIT finding 6. `Config.from_env`
  (src/sensing/config.py:175–270) never reads `vad_threshold`,
  `emotion_max_staleness_s`, `headcount_buffer_cap`, `smooth_tau_*`, or
  `trend_*`; `.env.example:29` says "horizon (seconds) and entry cap" but no
  cap variable exists. The tuning loop's contract is "apply proposals by
  editing env vars" — knobs that silently ignore the env break that contract.
- **Why it matters:** Corpus integrity (a tuning-report proposal someone
  "applies" via a nonexistent env var is a silent no-op) and commercialization
  (config that lies is the classic onboarding trap).
- **Scope:** `src/sensing/config.py`, `.env.example`. For each unwired field,
  one explicit decision: wire it (add to `from_env` + `.env.example`, following
  the existing naming pattern) or mark it code-level-only (comment on the
  dataclass field stating so, and no `.env.example` mention). At minimum, wire
  `vad_threshold` (the base certification cutoff — the playback variant already
  is wired) and fix the buffer-cap comment. **Out of scope:** changing any
  default value; MappingConfig/PlaybackConfig (audit found them consistent).
- **Acceptance criteria:**
  - New test: for every field of `Config` the test asserts it is either
    settable via its documented `RTR_*` var (set env → `from_env` reflects it)
    or its name appears in an explicit `_CODE_ONLY` allowlist in the test —
    making future drift a test failure, not an audit finding.
  - `.env.example` and `from_env` agree exactly: a test parses `.env.example`
    for `RTR_*` names and asserts each is consumed somewhere in the three
    config modules (grep-style, or via monkeypatched env round-trip).
  - Defaults unchanged: `Config()` field-for-field equal before and after.
- **Risk notes:** Wiring `vad_threshold` changes no behavior at default but
  creates a new live-tunable with corpus implications — the boundaries
  snapshot in Recommendations does NOT include sensing-layer knobs; note in
  the field comment that changing it mid-corpus is a calibration event to be
  logged in FIELD-NOTES (evidence-first culture; do not silently invite
  drive-by tuning).
- **Effort:** S

### M10-03 — CSRF guard on the label/override endpoints

- **Problem:** AUDIT finding 7. `/annotations` and `/overrides` accept
  unauthenticated POSTs and Starlette parses JSON regardless of content type;
  a malicious page in the same browser can inject fake labels or skip tracks
  on localhost (src/dashboard/app.py:82–139).
- **Why it matters:** Corpus integrity — the corpus is the product's learning
  substrate and everything else in the repo protects it; and
  commercialization — "unauthenticated localhost API" needs to be a stated,
  bounded posture.
- **Scope:** `src/dashboard/app.py` (require a custom header, e.g.
  `X-RTR-Client: dashboard`, on the two POST endpoints — its mere presence
  forces a CORS preflight cross-origin, which fails absent CORS middleware),
  `src/dashboard/static/index.html` (+ perform view) fetch calls, tests.
  **Out of scope:** authentication/tokens/sessions (out of proportion for a
  localhost tool); CORS middleware (its absence is the mechanism); the GET
  endpoints and websocket.
- **Acceptance criteria:**
  - Test: POST without the header → 403, and **no** record file is written
    (assert the day file is absent/unchanged — the label must not be captured
    before rejection).
  - Test: POST with the header behaves exactly as today (existing tests
    updated mechanically to send it).
  - A short "security posture" paragraph added to README's dashboard section:
    localhost-only binding, header-gated writes, and the explicit statement
    that `--host 0.0.0.0` is unsupported-at-your-own-risk.
- **Risk notes:** The header check must run **before** `build_record`/
  `note_tap` — the capture-before-action ordering (app.py:96 docstring) is
  about provider failures, not unauthenticated writes; do not "bank the label
  first" here. Don't break `curl` workflows in the test plans — update any doc
  that shows a raw POST (grep `docs/` for `/annotations`).
- **Effort:** S

### M10-04 — Frame schema: make the second contract explicit

- **Problem:** AUDIT hygiene note. The websocket frame (`RoomState.to_dict()` +
  bridge extras, src/dashboard/bridge.py:163–245) is consumed by two HTML
  views, stamped into annotation/override records, and parsed by
  `scripts/tuning_report.py` — but unlike the records (which carry
  `schema_version`), the frame itself is an untyped dict with no version. The
  extras list doubled across M4–M7.
- **Why it matters:** Commercialization: this is the contract a team's
  frontend/data people will build against; corpus integrity: record `state`
  blocks are frames, so frame drift is silent corpus schema drift.
- **Scope:** New `src/dashboard/frames.py` (or section in `bridge.py`): a
  TypedDict (or dataclass) enumerating every frame key with types and
  provenance comments (RoomState field vs engine extra vs playback extra), a
  `FRAME_SCHEMA_VERSION` stamped into each state frame, and a test that
  constructs a frame via the real bridge path and asserts its key set equals
  the schema's. Bump record `SCHEMA_VERSION` **only if** adding the frame
  version key changes record contents (it will — records embed frames; handle
  per risk note). **Out of scope:** changing/removing any existing key;
  runtime validation (a test-time contract is enough at this scale);
  tuning_report parsing changes beyond tolerating the new key.
- **Acceptance criteria:**
  - Test: `DashboardBridge.on_state` output with a full-featured fake engine
    and playback yields exactly the schema's keys — a new key added anywhere
    without updating the schema fails CI.
  - `scripts/tuning_report.py` runs unchanged against both an old recorded
    day file (pre-key) and a newly generated one — proven with fixtures in
    `tests/test_tuning_report.py`.
  - The schema module's docstring states the compatibility rule (additive
    keys only; removals/renames require a version bump and a tuning_report
    audit).
- **Risk notes:** The trap is the corpus: annotation/override `state` blocks
  are tap-time frames sent from the browser, so the new key flows into
  records; the tuning report and presence retro-filter must be verified
  key-agnostic (they access by name, but prove it with the old-fixture test,
  don't assume). Do not let the TypedDict drift from `RoomState` — derive the
  RoomState portion programmatically from `dataclasses.fields(RoomState)` in
  the test rather than hand-copying names.
- **Effort:** M

### M10-05 — Single source of truth for replay constants and the presence criterion

- **Problem:** AUDIT hygiene note. `scripts/m7_replay_session.py` hardcodes
  `WINDOW_S, HOP_S, HC_INTERVAL_S, VAD_THR` that must mirror the engine, and
  `scripts/tuning_report.py` mirrors the presence criterion with a
  "keep the two in sync" docstring (src/dashboard/presence.py:31–33,
  tuning_report.py `assess_presence_retro`). Replay fidelity — the backbone of
  the repo's offline-evidence method — currently depends on manual sync.
- **Why it matters:** Corpus integrity: an out-of-sync replay produces
  confident wrong evidence, the worst failure mode this repo has; the M7 gate's
  own credibility rests on `m7_replay_session.py` being "engine-matched".
- **Scope:** Scripts import from `src/` (they already `sys.path.insert` —
  tts_harness.py:35): replay constants come from `sensing.config.Config()`
  defaults; `tuning_report` imports `assess_presence` from
  `dashboard.presence` for the shared arithmetic, keeping only the
  retro-specific record-unpacking local. Touch:
  `scripts/m7_replay_session.py`, `scripts/tuning_report.py`,
  `scripts/tts_harness.py` (constants only). **Out of scope:** changing any
  constant's value; restructuring the scripts; the harness's energy-mask
  design (documented as deliberately different from the engine —
  m7_replay_session.py docstring).
- **Acceptance criteria:**
  - `grep -n "WINDOW_S\s*=\|HOP_S\s*=\|VAD_THR\s*=" scripts/` shows only
    assignments *from* `Config` defaults, none literal.
  - `tests/test_tuning_report.py` presence tests pass unmodified, and a new
    test asserts `tuning_report`'s retro assessment delegates to
    `dashboard.presence.assess_presence` (e.g. monkeypatch it and observe the
    call).
  - `m7_replay_session.py` re-run on the 2026-07-15 gate WAV reproduces the
    committed histogram exactly (solo 126 / pair 110 / bucket-3 45) — the
    no-regression proof that the constants resolved identically.
- **Risk notes:** `Config()` (no env) vs `Config.from_env()` matters: replays
  must use pinned defaults, NOT the local `.env` (the Mac's `.env` sets
  `RTR_HEADCOUNT_MIN_INTERVAL_S=4.0` — which is what the replay wants for
  HC_INTERVAL_S=4.0 but NOT what `Config()` defaults to (2.0)!). Resolve
  explicitly: the replay documents *which* config it mirrors and takes an
  override flag; the acceptance replay must reproduce the histogram, which is
  the arbiter. This subtlety is exactly why the item exists.
- **Effort:** S

### M10-06 — Namespace the packages: `readtheroom.*`

- **Problem:** AUDIT naming note. Top-level importable packages are `sensing`,
  `mapping`, `playback`, `dashboard` — extremely generic names in
  site-packages; guaranteed collision risk the day this installs next to
  anything else.
- **Why it matters:** Commercialization only (zero demo value — hence last).
- **Scope:** Move `src/*` under `src/readtheroom/`; update every import,
  `pyproject.toml` entry points and package-find, the three `sys.path` script
  headers, and doc references. Mechanical but total. **Out of scope:**
  renaming modules themselves; any behavior change; doing this before M8/M9
  land (it conflicts with every open branch — schedule when the tree is
  quiet).
- **Acceptance criteria:**
  - Fresh-venv `pip install -e .[dev]` → full suite green; all three console
    scripts (`read-the-room`, `read-the-room-dashboard`,
    `read-the-room-spotify-auth`) run `--help` successfully.
  - `python -c "import sensing"` fails in the fresh venv (the old names are
    gone, not shadow-shimmed).
  - `grep -rn "^from sensing\|^from mapping\|^from playback\|^from dashboard\|import sensing" src/ scripts/ tests/`
    returns nothing.
  - A 10-minute live synth dashboard smoke (models load — the HF cache paths
    and truststore hooks must survive the move).
- **Risk notes:** Do it as ONE commit with no logic changes so review is
  pure-mechanical. `dashboard/static` package-data glob
  (pyproject.toml:63–64) must follow the move or the wheel ships without the
  UI. The egg-info in `src/` regenerates — delete the stale one. Check
  `.claude/` settings and docs for hardcoded module paths.
- **Effort:** L

### M10-07 — Onboarding docs: ARCHITECTURE.md + CONTRIBUTING.md

- **Problem:** The README is excellent but is a *milestone chronicle* — a new
  engineer must reverse-engineer the current-state architecture from seven
  milestones of history. There is no contributor guide (branch/gate
  conventions, evidence-first rules, the two-machine workflow, REQUIRES-REVIEW
  areas).
- **Why it matters:** Commercialization: this is the onboarding path; also
  where the M10 gate's cold-start run gets its instructions.
- **Scope:** New `docs/ARCHITECTURE.md`: current-state only — the layer
  diagram (DSP → VAD gate → emotion/headcount workers → RoomState → bridge →
  mapper → controller → provider), the three seams (Consumer,
  PlaybackStateSource, PlaybackProvider) with file pointers, the threading
  model (which thread owns what; the latest-wins pattern; the outside-the-lock
  I/O rule), and the data contracts (RoomState, frames per M10-04, record
  schemas). New `CONTRIBUTING.md`: setup (link README), test/CI expectations,
  the evidence-gated milestone convention, calibration-change rules (measured,
  FIELD-NOTES-logged), and the list of REQUIRES-REVIEW areas from this
  roadmap. **Out of scope:** rewriting the README (add a pointer only);
  API-reference generation.
- **Acceptance criteria:**
  - Every claim in ARCHITECTURE.md carries a `file:symbol` pointer, and a
    reviewer spot-checks 10 at random against the tree (recorded in the PR).
  - The M10 gate's cold-onboarding run uses only README + these two docs; each
    stumble becomes a doc commit — the run log is the evidence.
  - Under 400 lines combined; if it needs more, it is duplicating the code's
    own (excellent) docstrings — link instead.
- **Risk notes:** Write it AFTER M10-04/M10-06 land or it documents a moved
  tree. The failure mode is aspirational architecture docs — describe only
  what `main` does on the day of writing.
- **Effort:** M

---

## M11 — Venue-ready: external microphones and external speakers

**Charter.** Every constant RTR measures was fitted on one capture path at a
time, first the Mac and now JPad's built-in array, with music from the same
laptop's speakers. The intended venue deployment is different (founder
direction, 2026-09-24): **a microphone in the centre of the space, the
dashboard on a background computer, and music from the venue's own
speakers.** The 2026-09-23 and 2026-09-24 XVF3800 sessions (FIELD-NOTES)
showed what happens when the capture path changes and nothing is
re-measured:
- mic gain became a calibration input to the crowd path;
- the PROVISIONAL dominance knots saturated (p50 1.000);
- the arousal correction pinned at its 0.600 clamp, and the pull estimator
  banked nothing;
- sung vocals passed certification and clustered as voices.

M11 proves RTR can be installed in a space it was not calibrated in: the
capture path is known and enforced, the per-install calibration is a written
procedure, and the configurations are compared by pre-registered protocol
rather than by assumption.

**Not started yet, by founder direction.** Development and testing stay on
JPad's built-in mic and speakers until the founder opens this milestone. M11
branches from `main` after M8 (M11-03 touches the engine path), and should
follow M9: the live-performance evidence (M9-03) covers the same vocals and
noise-floor hazards from the live-music side.

**Gate:** a venue-shaped session, on the reference machine, in a space other
than the development room. Conditions:
- a microphone at the centre of the space, music from an external speaker at
  a distance, and at least three people talking;
- the M11-05 per-install calibration pass completed from its runbook by a
  person who did not write it;
- the M11-06 decision rule applied as pre-registered, with the result
  recorded in FIELD-NOTES and the README gate table.

No target numbers: the gate is the protocol run end to end and the decision
recorded with its measurements.

### M11-01 — Enforce the signature `source` stamp — REQUIRES-REVIEW

- **Problem:** v3 signature files carry a capture-path stamp
  (src/sensing/music.py:66), but it is recorded, not enforced. `_load` logs the
  stamp and applies the signatures whatever mic stamped them, and `_save`
  re-stamps the file with the current capture. Any mic change silently blends
  two capture paths' signatures under one label. This was found writing the
  2026-09-24 run sheet (§I), which had to work around it with per-leg files.
- **Why it matters:** With more than one capture path in play, the
  correction applied to published valence/arousal must come from the same
  path that is listening. The 2026-09-24 signatures differed sharply by mic:
  the arousal ref was ~+0.47 on the built-in and ~+0.76 on the array, for the
  same track.
- **Scope:** `TrackSignatureStore` load/save (src/sensing/music.py). On a
  stamp mismatch, do not apply or adapt the loaded signatures, and do not
  overwrite or re-stamp the file. Either keep a per-source file alongside it
  or start empty in memory, and state which in the design note. Unstamped
  v1/v2 files keep today's behaviour unless the reviewer decides otherwise,
  stated explicitly. **Out of scope:** migrating existing files; a signature
  merge across sources; the engine.
- **Acceptance criteria:**
  - Tests: a mismatched stamp means no correction is applied from the file
    and the file's bytes are unchanged after a save cycle; a matching stamp
    loads and applies as today; unstamped files follow the documented rule.
  - Schema changes are additive only; any rename or removal bumps
    `schema_version`, with a `scripts/tuning_report.py` audit (CLAUDE.md
    invariant 9).
- **Risk notes:** This changes which corrections reach published
  valence/arousal: REQUIRES-REVIEW, plan and diff. It must never delete or
  rewrite a signature file, since it is measured evidence. What counts as
  "the same source" (device name, host, capture rate) is part of the review.
- **Effort:** S

### M11-02 — Advisory anchor: never persist a music-inflated floor

- **Problem:** When a track is paused, `playback_active` flips false within
  one 5 s poll. The rolling floor (τ 60 s) still sits at the music's level at
  that moment. `AdvisoryDetector.update` (src/dashboard/bridge.py:74–81) takes
  that floor as the quiet anchor and `_persist_anchor` writes it. Measured
  2026-09-24:
  - leg A: persisted −48.1 dBFS against its quiet anchor of −59.5;
  - leg B: persisted −24.4 against −55.8.

  A dashboard stopped soon after pausing hands the next session (within
  `RTR_PLAYBACK_ADVISORY_ANCHOR_MAX_AGE_S`, 12 h) an anchor ~30 dB too high,
  and the "turn it down" banner cannot fire.
- **Why it matters:** This happens on JPad today, not only in venues. In a
  venue, where parties start with music already on (the reason the anchor
  persists at all, M6), a bad anchor blinds the only playback-loudness
  guardrail for the whole next session. Not venue-specific; it can be pulled
  forward on its own.
- **Scope:** `AdvisoryDetector` anchor seeding and persistence only. For
  example, suppress anchor updates for a settle window after a
  playback→inactive edge, sized from `noise_floor_tau_s` with the provenance
  written at the site. **Out of scope:** the advisory margin, the speech
  epsilon, the engine's floor.
- **Acceptance criteria:**
  - Test: a synthetic frame sequence of quiet (floor −60), then 3 min of
    loud playback (floor chasing up to −25), then a pause, then stop within
    10 s. The persisted anchor stays within 0.5 dB of −60.
  - Test: a genuine quiet change (the floor drops, then holds past the settle
    window with no playback) still updates and persists the anchor.
- **Risk notes:** `envelope_advisory` is a frame-only field set by the bridge
  (bridge.py:170) and is not written to corpus records, so this is not a
  published-semantics change. Re-check that before implementing. Any new
  settle constant follows the tunable pattern: measured default, provenance,
  `RTR_*`, `.env.example`.
- **Effort:** S

### M11-03 — Publish a peak / clip indicator per hop

- **Problem:** Frames publish `loudness_dbfs` (RMS) but no peak level. On
  2026-09-24 the XVF3800 beside the speakers read music at −8.4 dBFS RMS
  with the gain frozen at 2.0, which is plausibly clipping on peaks, and
  nothing on the socket can confirm it. External mics in venues, with unknown
  gain staging next to PA systems, make this the first question of every
  install.
- **Why it matters:** Clipping corrupts every downstream model silently, and
  echo cancellation needs the capture chain in its linear region (XMOS
  tuning guide). The per-install calibration (M11-05) needs this readout to
  set gain.
- **Scope:** `src/sensing/dsp.py` (a peak dBFS and a clipped-sample fraction
  per hop, alongside `rms_dbfs`), one additive `RoomState` field or pair, and
  a dashboard readout. **Out of scope:** any gain control or automatic
  adjustment.
- **Acceptance criteria:**
  - Tests: a synthetic full-scale square wave reports a peak of 0 dBFS and a
    non-zero clip fraction; a −20 dBFS sine reports a peak of about −20 and
    a clip fraction of 0.
  - The benchmark regression row on the reference machine
    (`bench_headcount.py --fallback`) lands within run-to-run variance.
- **Risk notes:** This touches the engine path, so it is blocked behind
  M8-01/M8-02, and the new field lands through M10-04's frame schema if that
  is merged first. It's additive, with no change to existing fields.
- **Effort:** S

### M11-04 — Echo-cancellation routing for a remote array

- **Problem:** The XVF3800's echo canceller only cancels what the host plays
  *through it*. Its reference is the left channel of the USB playback stream
  (XMOS XVF3800 v3.2.1 datasheet; verified 2026-09-24,
  docs/XVF3800-PLAYBACK-RUN-SHEET.md addendum). Venue music on the venue's
  own system gives it no reference. Other limits: a 192 ms tail; a
  reference delay of 0–500 ms, fixed via `AUDIO_MGR_SYS_DELAY`; convergence
  in under 30 s, readable as `AEC_AECCONVERGED`; and a linear-region
  requirement on the speaker chain.
- **Why it matters:** Echo cancellation is the only capability an array has
  that the laptop mic structurally lacks. 2026-09-24 measured the array
  without a reference and it lost every question. Whether it wins *with* a
  reference in a venue-shaped setup is unmeasured.
- **Scope:** A setup procedure plus read-only tooling:
  - the audio routing (Spotify output device = the array; its line out →
    mixer or external speaker);
  - the delay measurement (XMOS `mic_ref_correlate`) and where the result is
    recorded;
  - `scripts/xvf3800_dashboard.py --check` extended to *read*
    `AEC_AECCONVERGED` and report it.

  **Out of scope:** writing any AEC parameter persistently
  (`SAVE_CONFIGURATION` stays forbidden); non-XMOS arrays; changes to
  `audio.py`. RTR already captures the left, AEC-processed channel
  (src/sensing/audio.py:166).
- **Acceptance criteria:** A written procedure that a human follows once on
  the reference machine, with an external speaker at ≥ 2 m. The FIELD-NOTES
  entry records the measured reference delay, the time to
  `AEC_AECCONVERGED` = 1, and the peak level at the mic (M11-03).
- **Risk notes:** Routing the venue's music through a USB accessory makes it
  a single point of failure for the venue's audio. The procedure must say
  what happens if the array is unplugged mid-set. Large rooms' reverb tails
  exceed 192 ms: record residual echo, don't assume it away.
- **Effort:** M

### M11-05 — Per-install calibration pass: inventory, procedure, record

- **Problem:** No list exists of which constants depend on the capture path.
  The evidence so far points to at least these:
  - the dominance knots (`RTR_MUSIC_DOMINANCE_LO/HI`, PROVISIONAL even on
    JPad);
  - the absolute and floor-relative loudness ramps in the crowd path
    (headcount.py:446–453; FIELD-NOTES 2026-09-23 finding 2 and 2026-09-24
    finding 2);
  - mic gain;
  - the advisory margin and speech epsilon (2026-09-24 finding 6: the banner
    went silent on the array because its vocals certified as speech).

  Today these live in one machine's `.env` with provenance in FIELD-NOTES.
- **Why it matters:** Every venue is a new capture path. Without an
  inventory and a procedure, each install is either uncalibrated or a
  bespoke research session.
- **Scope:** A new doc listing every capture-path-dependent constant, its
  current provenance, and the measurement protocol that fits it. A
  per-install record template stating which values were measured where and
  when. A decision, with sign-off, on the mechanism: a plain per-install
  `.env` block within today's precedence (default → `.env` → env var), or a
  new profile layer. The recommendation is `.env` unless the inventory shows
  it can't hold. **Out of scope:** refitting any constant (each refit is its
  own calibration event); automatic self-calibration.
- **Acceptance criteria:**
  - The inventory cites a code site and a FIELD-NOTES provenance line for
    every entry.
  - The procedure is run once on JPad's built-in path and once on a second
    capture path, with both records filed.
  - A reviewer confirms that the two records can be compared field by
    field.
- **Risk notes:** This is where "per-machine calibration is not doctrine
  until measured twice" (CLAUDE.md) becomes per-install. The procedure must
  carry that rule rather than bless one-shot fits. Promoting any value to a
  `config.py` default is out of scope here.
- **Effort:** M

### M11-06 — Venue-configuration comparison protocol — REQUIRES-REVIEW

- **Problem:** The 2026-09-24 verdict covers only the co-located case (mic
  beside the speakers). The venue configurations are unmeasured.
- **Why it matters:** The venue default must come from a pre-registered
  comparison, like the 09-24 §G rule, not from the co-located result or from
  the array's spec sheet.
- **Scope:** A run sheet in the house pattern
  (docs/XVF3800-PLAYBACK-RUN-SHEET.md is the template). Configurations,
  each with its own per-leg isolation directory:
  - **(i)** the JPad built-in, co-located: the baseline, a repeat of 09-24
    leg B;
  - **(ii)** a remote mic at the centre, music from an external speaker at a
    distance, with no AEC reference;
  - **(iii)** the same as (ii), with the music routed through the array's
    line out (M11-04).

  Legs: talking marks measured from each mic, drift legs as in 09-24 (ABA at
  minimum, ABBA preferred), at ≥ 2 music levels (09-24 finding 5), and both
  solo and ≥ 3-person talking legs (09-24 caveat: neither mic has seen a
  group under playback). Measures: run sheet §F, plus the peak level (M11-03)
  and `AEC_AECCONVERGED`. **Out of scope:** any in-session tuning.
- **Acceptance criteria:**
  - The decision rule is written and signed off **before** the session.
  - The FIELD-NOTES entry reports per-leg, per-phase tables with the drift
    shown, and the rule worked measure by measure.
  - A written venue-default decision, with the configurations it does and
    doesn't cover.
- **Risk notes:** This is a live session with people in the room, so it is
  human-run on the reference machine (REQUIRES-REVIEW): Claude writes the
  protocol and a human executes it. Record consent from everyone present.
  Default any new capture to off.
- **Effort:** M (mostly human time)

---

## Deferred / rejected — the no-silent-drops ledger

Every AUDIT item not in the backlog above, with its disposition:

- **Finding 8a — engine-thread disk I/O** (signature saves music.py:238,
  advisory anchor bridge.py:110): **ACCEPTED AS-IS.** Both writes are
  throttled (10 s / 0.5 dB), the M2–M7 gate rows show the heartbeat inside
  budget with them present, and M9-05's soak protocol becomes the standing
  monitor (tick-gap count would expose a stall). Revisit only if a soak run
  shows tick gaps correlated with save timestamps.
- **Finding 8b — post-stall VAD flood** (vad.py:44 after a >12 s stall):
  **DEFERRED.** Self-correcting, occurs only after a stall that is itself the
  real bug, and capped by the 12 s ring. M9-05's tick-gap metric will show
  whether it happens in practice; file a bounded-drain item only with a
  measured occurrence.
- **Finding 8f — `_handle` loses a recommendation when bootstrap `play()`
  raises** (controller.py:263–289): **DEFERRED.** The degrade-to-shadow
  semantics are deliberate; a retry loop risks replaying a stale rec after
  recovery, which is worse than holding. Revisit only if the M9 dress
  rehearsal or a live session logs this path actually firing.
- **AUDIT note — `RoomState` shallow-frozen (mutable dicts inside a frozen
  dataclass)**: **REJECTED.** No consumer mutates frames today, all consumers
  are in-repo, and deep-freezing costs allocations on the heartbeat for a
  purely theoretical hazard. M10-04's schema test is the guard that matters.
- **AUDIT note — `wait_for_code` stray-request race in the OAuth callback
  server** (spotify_auth.py:62–106): **REJECTED.** One-shot interactive flow,
  human present, worst case is "run it again"; hardening it buys nothing for
  either goal.
- **AUDIT note — torch/numpy/speechbrain version pins**: **ACCEPTED AS-IS,
  explicitly out of scope everywhere above.** The pins are load-bearing for
  the demo Mac (Intel wheels) and documented at the pin site
  (pyproject.toml:10–28). Any change is its own evidence-gated event on the
  demo hardware, not roadmap hygiene.
