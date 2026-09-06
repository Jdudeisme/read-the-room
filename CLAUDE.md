# CLAUDE.md — working rules for Read the Room

Ambient room-sensing engine (mic → RoomState → music recommendation → Spotify
playback), built through evidence-gated milestones. Read `README.md` for the
system, `AUDIT.md` for known issues, `ROADMAP.md` for planned work,
`docs/FIELD-NOTES.md` for why things are the way they are. This file is the
discipline: the mistakes to not make.

## The prime directive: evidence over cleverness

Nearly every constant in this codebase was **measured**, not chosen — cluster
threshold 0.70, dominance knots 0.05/0.30, super-additivity scales 2.2/1.5,
presence windows 60/30 s. The provenance is written at the constant's site.

- **Never adjust a calibration constant, threshold, ramp, or default** because
  it "looks wrong" or a test would be easier to write. Changing one is a
  calibration event: it needs a measurement protocol, a FIELD-NOTES entry, and
  usually a live re-gate on the reference machine. If a value seems wrong,
  file it as a finding with your evidence; do not patch it.
- When you add a tunable, follow the house pattern: measured default, provenance
  comment at the definition, `RTR_*` env var wired in `from_env`, documented in
  `.env.example`. All three or none.
- Acceptance criteria for tuning work are **measurement protocols, not target
  numbers** (repo convention; see ROADMAP.md).

## Architectural invariants — violating these is a rejected PR

1. **One output contract.** Consumers see `RoomState`
   (`src/sensing/state.py`) and implement `on_state(state)`. Nothing downstream
   reaches into the engine's internals; new downstream features consume the
   frame/state, not the workers.
2. **VAD certification is centralized in the engine**
   (`src/sensing/engine.py`, `src/sensing/vad.py`). Emotion and headcount never
   run their own VAD, ever. New gates (e.g. music detection) slot into the
   engine's certification point so every layer inherits them at once.
3. **The DSP heartbeat never blocks.** The engine tick does no model inference
   and no network I/O. Models live on worker threads; playback state is a
   cached read (`PlaybackStateSource`), never a provider call.
4. **Latest-wins slots, never queues.** Workers (`EmotionWorker`,
   `HeadcountWorker`, `PlaybackController`) hold at most one pending job; new
   work replaces stale work. Do not introduce an unbounded queue anywhere.
5. **Honest uncertainty.** Every ML-derived value ships as a
   value/confidence/staleness triple. Silence **holds** the last reading and
   grows staleness — absence of evidence is never evidence of an empty room.
   `None` means "honestly absent"; never fabricate a value to keep a UI lively.
6. **No `max_speakers` constant, anywhere.** The bucket ladder is computed
   (`bucket_from_log2`), never enumerated in branching code.
7. **Shadow mode is the first-class default.** Anything missing, broken, or
   degraded in playback falls back to shadow; the sensing side never notices.
   Provider failures raise `ProviderError` and degrade — they never crash the
   dashboard or lose a label.
8. **Label capture before action.** Override records are written to disk
   *before* the playback action is attempted (`src/dashboard/app.py`). Never
   reorder this. Corollary: label capture must never depend on the provider
   being alive.
9. **Corpus lines are gated, never deleted.** Bad evidence gets marked
   (`occupied: false`, blind-veto flags) and excluded from rates; it is not
   removed. Record schemas carry `schema_version`; additive changes only —
   removals/renames need a version bump and a `scripts/tuning_report.py` audit.

## Threading rules

- Provider/network I/O happens **outside locks** — see
  `src/playback/controller.py` for the pattern (state mutation under
  `self._lock`, `httpx` calls outside it). Match it.
- Which thread owns what: engine tick owns EMAs, `TrackSignatureStore`,
  `CleanBaseline` (engine-thread-only by contract — no locking, so don't call
  them from elsewhere); workers own their models and `_latest`; the HTTP
  threadpool calls controller override methods; the asyncio loop owns
  websockets. The bridge crosses engine→asyncio via
  `loop.call_soon_threadsafe` + per-client queues that **drop frames when
  full** — never block the engine on a slow client.
- Workers are daemon threads; `stop()` sets events and does not `join()`. Keep
  it that way — don't add joins to "fix" shutdown.
- Benign documented races exist (e.g. `_last_infer_at` unlocked reads,
  PresenceGate tap timestamp). Don't add locks around them without reading the
  docstring that declares the race harmless.

## Deliberate behaviors — do not "fix" these

A context-free reviewer flags all of these; each is intentional. If you think
one is wrong, check AUDIT.md/ROADMAP.md first — several are already tracked.

- **The rescue flag is OFF** (`RTR_HEADCOUNT_RESCUE_ENABLED=0`). The
  distinct-voice rescue was disproven on the validated mic (FIELD-NOTES
  2026-07-15) and shelved. Never re-enable it, delete it, or "finish" it.
- **Undercounting beats phantom crowds.** Very similar voices merging is the
  accepted trade. Don't lower the cluster threshold to "catch" them.
- **Stale emotion readings re-feed the V/A EMAs every tick.** That's the
  smoothing design, not a bug. (The *correction context* aspect is tracked as
  ROADMAP M8-03 — don't fix it ad hoc.)
- **Spotify's queue is append-only**; the controller deliberately holds the
  next-up selection locally and pushes only inside the boundary window. Don't
  push eagerly, don't try to "replace" a queued track.
- **A lost recommendation on bootstrap `play()` failure** degrades and waits
  for the next emission. Deliberate — a retry could replay a stale rec.
  (Ledgered in ROADMAP as deferred.)
- **Guard recommendations and the 30 s dwell** suppress emissions on purpose;
  "make it more responsive" is a product decision, not a cleanup.
- **The mapper does all time arithmetic on `state.timestamp` (wall clock)** so
  behavior replays exactly from a recorded stream. Don't switch it to
  monotonic. Staleness internals use monotonic. Keep the two clocks straight.
- **`pause()` swallowing 403** is a known-imperfect tracked item (M8-08);
  either implement that item as specced or leave it.

## Sequencing constraints (check before touching code)

- **`src/sensing/engine.py` is under a soft freeze**: its M6 orchestration
  (`_tick`/`_bank_evidence`/`_correct`) has no direct tests. Until ROADMAP
  M8-01/M8-02 (collaborator extraction + `tests/test_engine.py`) are merged, do
  not modify engine.py beyond what those items themselves specify.
- **Do not push to an in-flight milestone branch** (e.g.
  `milestone-7-stable-middle` while its gate is open). New work branches from
  `main` after the milestone merges.
- Refactors touching the engine path must show the **benchmark regression row**
  (`python scripts/bench_headcount.py --fallback` on the **reference machine**,
  within run-to-run variance of the last reference-machine README row — not the
  historical Mac rows). Compare like with like; the budgets differ.
- Changes to headcount behavior must reproduce the committed replay evidence:
  `scripts/m7_replay_session.py` on the 2026-07-15 gate WAV must yield the
  recorded histogram (solo 126 / pair 110 / bucket-3 45) unless the change
  *intends* to move it, with FIELD-NOTES evidence.

## Tests

- The suite is **offline and fast** (~4 s, no models, no network). Keep it
  that way: no test may download a model, open a mic, or hit Spotify. Mock at
  the seams — for Spotify, at the **transport** (`httpx.MockTransport`), not
  the provider class.
- `python -m pytest` from the repo root. All tests green before any commit;
  never weaken an assertion to make a change pass — a failing headcount or
  presence test usually means your change broke measured behavior.
- Behavior changes to tested semantics update tests **deliberately and
  visibly** (the repo pins current behavior with characterization tests that
  reference the roadmap item that will change them — follow that pattern).

## Data, secrets, environment

- **Never commit**: `.env`, anything under `data/` except `data/.gitkeep` and
  `data/playlists.json` (the founder's baseline — don't overwrite it with
  local curation). Token cache and corpus files are local by design.
- **Never edit corpus files** (`data/annotations/`, `data/overrides/`)
  — append-only day files written by the running system. Analysis reads them;
  nothing rewrites them.
- **The dependency pins are load-bearing**: Python `>=3.12,<3.13`,
  `torch 2.2.x`, `numpy<2`, `speechbrain<1.1`, `transformers<4.50`. The demo
  target is a 2019 Intel MacBook Pro on the last Intel-macOS torch wheels.
  Do not bump pins; the reasons are commented in `pyproject.toml`.
- **Reference machine (founder direction, 2026-09-06)**: the Windows laptop
  (`JPad`) is the primary development **and** gate machine. Performance claims,
  benchmarks, and live calibrations count from it. The 2019 Intel MacBook Pro
  is a secondary compatibility target: its README gate rows (M2–M7) stay as
  historical record and are **not** comparable to new rows — the budget
  arithmetic differs (Mac 1.37 s from a 0.63 s emotion floor; reference machine
  ~1.66 s from 0.34 s). See `docs/MACHINE-DOCTRINE-REVISION.md`. When driving a
  browser at a localhost dashboard, confirm which machine is serving first.
- **Per-machine calibration is not doctrine until measured twice.** This
  machine's `.env` carries recalibrated dominance knots
  (`RTR_MUSIC_DOMINANCE_LO=0.022`, `_HI=0.050`) that make the M6 pull estimator
  work here at all — but they are **PROVISIONAL**: fitted to one track, with
  three speech-only controls disagreeing at the tail (FIELD-NOTES 2026-09-06).
  Do not promote them to `config.py` defaults until the ladder is re-run across
  ≥2 tracks and ≥3 speech-only controls. The Mac-measured defaults in
  `config.py` stay untouched meanwhile.
- Scripts that replay engine behavior must source constants consistently with
  the session being replayed — note that the Mac's `.env` sets
  `RTR_HEADCOUNT_MIN_INTERVAL_S=4.0` while the `Config()` default is 2.0
  (ROADMAP M10-05). State explicitly which config a replay mirrors.

## Things an agent must not do autonomously (REQUIRES-REVIEW)

Get explicit human sign-off on plan **and** diff for:

- Changes to **published sensor semantics** — anything that alters the
  valence/arousal/confidence/bucket values stamped into frames and corpus
  records (e.g. correction logic, estimator behavior, `separation_score`).
- Anything touching **audio capture** (`src/sensing/audio.py` device/stream
  handling) or adding a **new capture/log of room-derived data** (privacy:
  default new captures to off).
- **Live-session protocols** (gates, soak runs, characterization sessions):
  these are human-run on the reference machine with people in the room. You
  write the protocol; a human executes it. Never start a live mic session
  yourself.
- Deleting or rewriting anything in `docs/` — proposals, test plans, and
  FIELD-NOTES are the project's evidentiary record; they are append/extend,
  not clean-up targets.

## Conventions

- **Comments carry provenance and constraints** at the site of the decision
  (what was measured, when, and what it rules out). Match this density in
  sensing/mapping/playback code; a bare magic number is a defect here.
- **Docstrings explain design intent** at module top (read
  `src/sensing/headcount.py` for the house style). New modules follow it.
- **Commit messages**: milestone-prefixed, outcome-stating, plain —
  `M8 engine: extract music-aware correction (no behavior change)`. Look at
  `git log --oneline` and match.
- **Milestone flow**: proposal doc → implementation → test plan → evidence-
  gated pass recorded in FIELD-NOTES + README gate table. New milestone work
  follows ROADMAP.md's charters, including its acceptance criteria verbatim.
- Config precedence: dataclass default → `.env` → env var. Frozen dataclasses;
  runtime overrides go through `dataclasses.replace`, not mutation.
- Keep `.env.example` and `from_env` in exact agreement whenever either
  changes (ROADMAP M10-02 adds a test for this; don't pre-break it).
