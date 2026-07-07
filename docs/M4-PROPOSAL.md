# Milestone 4 Proposal — Playback: closing the loop (DRAFT)

M4 gives the mapping layer a speaker. A playback layer turns each
`Recommendation` into actual music via a streaming provider, the dashboard
grows override controls, and every human override is captured with full
room-state attribution — the strong labels that M3's deferred learned-tuning
work has been waiting for. This is also the first milestone since M2 that is
*allowed to touch sensing*, because playback creates the self-contamination
problem M3 explicitly deferred: once RTR plays music, the mic hears the
system's own output.

Two field findings shape this proposal (see `FIELD-NOTES.md`, 2026-07-06):
the crowd/babble heuristic keys on **absolute dBFS**, and playback will
raise the room's loudness floor permanently — exactly the condition that
produced the pool session's phantom `16`. Contamination handling is
therefore not an optional hardening pass; it is a gating deliverable.

## Deliverable 1 — playback layer (`src/playback/`)

- `PlaybackProvider` protocol (the seam): `devices()`, `play(track)`,
  `queue(track)`, `pause()`, `now_playing() -> NowPlaying | None`,
  `tracks_for(genre: str, tier: str) -> list[Track]`. One implementation in
  M4: **Spotify** via OAuth (Authorization Code + PKCE), controlling a
  Spotify Connect device — the Mac's Spotify app or an external speaker.
  Playback decode/output never happens in our process: the layer is control
  traffic only (a few HTTP calls per track), so it adds ~zero compute to
  the engine path and the M2/M3 performance gate carries over unchanged.
- **Track selection is playlist-mapped, not algorithmic.** Spotify's
  recommendations and audio-features endpoints are deprecated/restricted
  for new apps (Nov 2024), so we do not build on them. Instead the user
  curates playlists keyed by `(genre, energy tier)` — e.g.
  `RTR · Pop · high` — and a mapping file
  (`data/playlists.json`, committed as the founder's baseline; users
  override by editing locally without committing) binds rulebook
  genre pools to playlist IDs. The selector takes the Recommendation's
  `genre_pool` + a tier derived from `target_arousal`, picks a
  not-recently-played track from the mapped playlist, and queues it. Human
  curation stays in the loop — the direct descendant of the 2020
  GenrePicker lineage, and robust to API churn.
- **Gentle-DJ policy:** a new Recommendation never interrupts the current
  track. It replaces the *queued next* track; transitions happen on track
  boundaries. Only a human override skips mid-track. `energy_action`
  moves the tier of the next selection ("raise" → one tier up within the
  pool); it does not touch device volume — volume stays human-owned in M4.
- The playback controller runs in the dashboard process as a consumer of
  Mapper emissions (exactly as `DashboardBridge` consumes engine state —
  same process, new subscriber, engine untouched). Failure isolation: a
  provider error (token expiry, device gone, rate limit) logs, surfaces on
  the dashboard, and degrades to shadow mode — the sensing/mapping side
  never blocks on playback I/O.
- Config via `RTR_PLAYBACK_*` env vars (`Config.from_env` pattern,
  documented in `.env.example`): client ID, redirect port, device name,
  recently-played window, tier cutoffs. Token cache in `data/` (gitignored).
- New dependency: `spotipy` (or raw `httpx` if we would rather own the
  three endpoints we use — decide at implementation time).

## Deliverable 2 — override capture (the strong labels)

The M3 proposal deferred learned tuning "until override-event data (room
state, rejected track, chosen track) exists." This deliverable is that data.

- Dashboard override controls: **Skip** (veto the playing track),
  **Wrong vibe** (veto the *selection*, not just the track — resamples from
  a different cell-adjacent pool), and a manual picker (choose any mapped
  playlist). Good call / Wrong call annotation buttons remain.
- Every override appends one line to `data/overrides/YYYY-MM-DD.jsonl`
  (`schema_version: 1`): `ts`, `action`, the `NowPlaying` track, the
  `Recommendation` that chose it (incl. `matched_cell` +
  `boundaries_snapshot`), the RoomState *as displayed at tap time* (the M3
  annotation convention), and — for manual picks — what the human chose
  instead. A track that plays to completion with no override is logged once
  as an implicit weak-positive (`action: "played_through"`).
- `scripts/tuning_report.py` grows an overrides section: override rate per
  rulebook cell, skip clustering near band boundaries, tier disagreement
  (human consistently picking higher/lower energy than `energy_action`).
  Same contract as M3: the report proposes, never modifies.

## Deliverable 3 — sensing touch: contamination handling v1 + observability

The one milestone-sanctioned change to `src/sensing/`, in three additive
parts. No behavioral change to existing paths when playback is off.

- **Playback awareness.** The engine accepts an optional playback-state
  source; RoomState gains `playback_active: bool` and `playback_track_id`.
  Every downstream artifact (annotations, overrides, JSONL logs) is thereby
  tagged, so contaminated evidence is separable offline forever.
- **Certification gate v1** at the centralized VAD certification point (the
  seam the M2 plan reserved for exactly this): while `playback_active`,
  emotion/headcount evidence certification requires a stricter Silero
  speech threshold (`RTR_VAD_PLAYBACK_THRESHOLD`), and the babble
  `saturation` ramp switches from absolute dBFS to loudness *relative to a
  rolling noise floor* (EMA of quiescent-window loudness). The pool test
  showed absolute-dBFS saturation false-firing under a fan; under
  continuous music it would false-fire constantly. This is measure-first
  engineering, not a solved problem — v1's job is to stop the known
  failure mode and tag everything else.
- **Observability fields** (closes the M3 deferral and the FIELD-NOTES
  debuggability gap): `Estimate`/`HeadcountReading` gain `dispersion` and
  `fragmentation`; the reading also carries `smoothed_log2` from the
  BucketSmoother. Bridge attaches them to dashboard frames the same way it
  attaches `crowd_weight` today. The pool session's `pair → 16` jump was
  undiagnosable from the log alone; after this, it wouldn't be.

## Deliverable 4 — dashboard: now-playing

The shadow recommendation bar becomes a **now-playing bar**: track, source
playlist, the Recommendation that chose it, and the override controls from
Deliverable 2. When playback is degraded/off it reverts to the M3 "not
playing" shadow presentation — shadow mode remains a first-class state, not
a legacy one. A small `playback_active` indicator joins the staleness
header so contamination status is always visible.

## Risks / open decisions

- **Spotify platform risk:** premium account required for playback control;
  dev-app registration and rate limits; API surface has been shrinking.
  Mitigation: the provider seam is narrow (six methods), selection logic is
  playlist-based (no deprecated endpoints), and a `LocalLibraryProvider`
  (folder of files + `afplay`/`ffplay`) is the documented fallback — it
  would also hand us the reference signal for future echo-aware work.
- **Contamination severity is unmeasured.** v1 gating may prove too weak
  (vocal music still reads as a phantom speaker) or too strict (real
  speech starved during playback). The M4 test plan must include a
  controlled contamination measurement: known room, instrumental vs vocal
  tracks at fixed volumes, headcount/emotion drift recorded. Findings feed
  M5, where a real music-detection gate can land with data behind it.
- **Tier derivation from `target_arousal`** is a crude scalar → 3-tier map;
  boundaries are `RTR_PLAYBACK_TIER_*` env vars so the tuning loop can move
  them like any other boundary.

## Deferred (explicit non-goals)

- **No learned/automatic tuning yet** — M4 *collects* the strong labels;
  learning from them is M5+, once the override corpus exists.
- **No echo cancellation / reference-signal subtraction.** Requires owning
  the output PCM; revisit if/when a local provider lands.
- **No ML music-detection gate** — v1 is threshold + noise-floor logic
  only; a trained gate waits for the contamination measurements.
- **No auto-volume, no crossfade logic** (the provider's player handles
  transitions), **no multi-zone/multi-device orchestration**.
- **No new UI framework** — the dashboard stays one static page.

## Gate (sketch — full checklist in a future M4-TEST-PLAN.md)

On the 2019 Intel MacBook Pro, as always:

1. M2/M3 regression: `bench_headcount.py --fallback` unchanged (playback is
   control-plane I/O; any drift is a bug).
2. A 30-minute live session with real playback: recommendations select
   tracks, transitions happen on boundaries only, at least one of each
   override type round-trips into `data/overrides/` with correct
   attribution, provider failure (kill Spotify mid-run) degrades to shadow
   mode and recovers.
3. Contamination measurement protocol executed in a controlled room, with
   numbers recorded in FIELD-NOTES.md — pass/fail thresholds to be set in
   the test plan once we have a first baseline.
4. `pytest` green: playback selector, override log schema, tier mapping,
   and the sensing additive fields all unit-tested with synthetic state
   (no network, no Spotify in tests — the provider is mocked at the seam).
