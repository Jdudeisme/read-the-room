# Milestone 3 Proposal — Dashboard + shadow-mode mapping + annotation log

M3 adds the first *consumers* of the sensing seam: a mapping layer that turns
`RoomState` into a shadow music recommendation, a live dashboard that shows
both, and a human annotation loop that makes every recommendation attributable
and auditable. Nothing under `src/sensing/` changes; `RoomState` remains the
wire format.

Shadow mode is deliberate: no playback exists yet, so the mic never hears the
system's own output and the sensing layers stay uncontaminated. The
recommendation bar on the dashboard is explicitly labeled "not playing".

## Deliverable 1 — mapping layer (`src/mapping/`)

- `Recommendation` (frozen dataclass, `schema_version = 1`): `energy_action`
  ("hold" | "raise" | "lower"), `target_valence`, `target_arousal`,
  `genre_pool: list[str]`, `confidence`, human-readable `summary`,
  `matched_cell: tuple` (the rulebook key that fired),
  `boundaries_snapshot: dict` (every boundary/threshold in effect when it
  fired), `timestamp`. The attribution pair (`matched_cell` +
  `boundaries_snapshot`) exists so every future annotation is attributable to
  a specific rule under specific boundary values — the substrate for learned
  tuning in later milestones.
- `Mapper.update(state: RoomState) -> Recommendation | None`, stateful across
  calls (trend windows, dwell timer). All time arithmetic uses
  `state.timestamp` so tests drive time synthetically.
- **Rulebook granularity (amended from the original M3 spec):** keyed per
  power-of-2 headcount bucket, not per 3-tier group —
  `(bucket, valence_band, arousal_band)` over 12 buckets x 3 x 3 = 108 cells.
  Seeded from the 2020 thesis GenrePicker grid: buckets `solo`/`pair`/`4`
  take the <=3-person column, `8` takes the <=6 column, `16` through `crowd`
  take the >6 column (nearest bucket edges to the original 3/6 cutoffs).
  The seed is expressed as three 3x3 grids + a bucket->grid map, and tuned
  placements are single-line `RULEBOOK[cell] = [...]` overrides — no nested
  conditionals anywhere. Valence/arousal band cutoffs seed at +/-0.25,
  matching 2020. The seed is a starting point for tuning, not a source of
  truth.
- Trend: the mapper tracks short-horizon (~60 s) valence and arousal trends
  by reusing `sensing.state.TrendTracker` (import only — sensing unchanged).
  `energy_action` matches the room's direction: arousal rising -> "raise",
  falling -> "lower", flat -> fall back to the engine's published energy
  `trend`, else "hold". Targets lead the current reading slightly in the
  trend direction (`RTR_MAPPING_TARGET_LEAD`).
- Hysteresis: a new Recommendation is emitted only if BOTH (a)
  `RTR_MAPPING_MIN_DWELL_S` (default 30 s) elapsed since the last emission
  AND (b) the target moved materially — mood-quadrant change, headcount
  bucket change, or energy_action change. Otherwise `update` returns `None`
  and the dashboard keeps showing the current recommendation.
- Low-confidence guard: if the VAD says no speech, emotion is absent/stale
  (`mood is None`), the headcount bucket is unknown, or
  `headcount_confidence` is below `RTR_MAPPING_MIN_HEADCOUNT_CONFIDENCE`
  (the collapsed/uncertain crowd regime publishes <= ~0.3), the mapper emits
  a low-confidence "hold" instead of guessing. Guard recommendations carry
  `matched_cell = ("guard", <reason>)` so they stay attributable without
  pretending a rulebook cell fired.
- All thresholds/dwell times via `RTR_MAPPING_*` env vars following the
  existing `Config.from_env` pattern; documented in `.env.example`.

## Deliverable 2 — dashboard (`src/dashboard/`)

- FastAPI + uvicorn, launched via `python -m dashboard` or the
  `read-the-room-dashboard` entry point (mirrors the existing
  `read-the-room` script convention). New deps: `fastapi`, `uvicorn`
  (+ `httpx` in dev extras for the TestClient).
- The dashboard process hosts the sensing engine and the Mapper. A
  `DashboardBridge` is registered as an ordinary engine consumer (exactly
  like `console.py` — the engine is reused, never forked). Every hop it
  pushes `{type: "state", ...RoomState.to_dict()...}` over a websocket;
  when the Mapper emits, it pushes `{type: "recommendation",
  ...Recommendation.to_dict()...}`.
- Single static page (one HTML file, vanilla JS, inline CSS/JS, no build
  step): valence/arousal quadrant hero (SVG dot + ~2 min fading trail,
  quadrant labels excited/tense/low/content), metric cards (headcount bucket
  +/- uncertainty, regime, VAD + speech ratio), shadow recommendation bar
  ("not playing" label, Good call / Wrong call buttons), 10-minute
  valence/arousal sparkline strip (browser memory only), live staleness
  header, reconnecting websocket with an explicit disconnected state.
- Server keeps only the rolling frame history needed to fill the timeline on
  page load (~10 min) plus the current recommendation. Nothing else is
  persisted server-side.
- **Deviation — regime card:** the M3 spec asks for "dispersion and
  fragmentation values", but those are internal locals of
  `HeadcountEstimator.estimate()` and are not published on `RoomState` or
  `HeadcountReading`; exposing them would require modifying `src/sensing/`,
  which M3 forbids. Agreed resolution: the regime card derives its label
  (counting / blend / crowd) from `crowd_weight` and shows `crowd_weight` +
  `raw_clusters`, which the hosted engine's `HeadcountReading` already
  exposes. The bridge attaches these to the state frame as dashboard-added
  extras (`headcount_crowd_weight`, `headcount_raw_clusters`) — the
  RoomState schema itself is untouched.

## Deliverable 3 — annotation log

- `POST /annotations` on the dashboard app appends one JSON line to
  `data/annotations/YYYY-MM-DD.jsonl`:
  `{schema_version: 1, ts, verdict: "good"|"wrong", state: <RoomState
  snapshot as displayed>, recommendation: <Recommendation as displayed,
  incl. matched_cell + boundaries_snapshot>}`.
- The page sends the state/recommendation it is *displaying* at tap time —
  the annotation labels what the human saw, not whatever is latest
  server-side.
- `data/` is gitignored except `.gitkeep`.

## Deliverable 4 — tuning report (`scripts/tuning_report.py`)

Offline, run manually — the human-in-the-loop precursor to learned tuning.
Reads `data/annotations/*.jsonl` and prints (stdout only, benchmark-script
style):

- verdict counts per rulebook cell;
- wrong-call clustering per boundary (wrong calls whose signal sits within
  0.1 of a band cutoff, judged against each record's own
  `boundaries_snapshot`);
- per boundary, a suggested shift with the count of past verdicts it would
  have flipped to a different cell (wrong-calls flipped vs good-calls
  flipped).

The script proposes; it never modifies the rulebook, `.env`, or any file.

## Deferred (explicit non-goals)

- **No online/automatic threshold adjustment.** Learned tuning waits for
  post-playback milestones, when override-event data (room state, rejected
  track, chosen track) exists. Annotations against never-played shadow
  recommendations are weak labels; the attribution fields and the tuning
  report exist to make that future learning inevitable, not to do it now.
- **No playback, audio output, track/catalog integration, or Spotify
  anything.**
- **No music self-contamination handling** — moot until playback exists
  (and the engine's centralized VAD certification point is where that gate
  will land, per the M2 plan).
- **Dispersion/fragmentation surfacing** stays deferred until a milestone
  that is allowed to touch sensing (one additive field pair on
  `Estimate`/`HeadcountReading` would suffice).

## Gate

See `docs/M3-TEST-PLAN.md`. The performance gate still runs on the 2019
Intel MacBook Pro; this Windows box is dev-only and its numbers are
informational.
