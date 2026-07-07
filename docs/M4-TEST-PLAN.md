# Milestone 4 Test Plan — Playback: closing the loop

Logic-level tests live in `tests/test_playback.py`, `tests/test_spotify.py`,
plus the M4 additions to `tests/test_headcount.py`, `tests/test_state.py`,
`tests/test_dashboard.py`, and `tests/test_tuning_report.py` (no mic, no
models, no network — the Spotify provider is tested through an
`httpx.MockTransport`). This plan covers the milestone gate, which runs on
the 2019 Intel MacBook Pro with a Spotify **Premium** account.

> **For Claude Code on the Mac:** this document is your session script.
> When the user asks to set up or gate M4, walk them through it **one part
> at a time, in order**: run the commands you can run yourself, tell the
> user exactly what to do for the steps only they can do (browser auth,
> playlist curation, talking, unplugging things), and verify each
> checkpoint before moving on. Steps only the human can perform are marked
> **[HUMAN]**. Record results where each section says — bench numbers in
> the README results table, contamination numbers in a new dated
> `docs/FIELD-NOTES.md` entry, gate outcomes summarized to the user at the
> end. If a checkpoint fails, stop and diagnose before continuing; nothing
> later is trustworthy on top of a failed checkpoint.

## Part 0 — one-time setup (prerequisite, not the gate)

Skip any step whose checkpoint already passes (re-runs of this plan should
fly through Part 0).

### 0.1 Environment

```bash
git pull
source .venv/bin/activate
pip install -e '.[dev]'          # M4 moved httpx into main dependencies
python -m pytest -q              # expect: 185+ passed, no network
```

Checkpoint: pytest green. The suite is the gate's part (b); running it
first catches a broken install before any Spotify setup.

### 0.2 Spotify app registration **[HUMAN]**

At <https://developer.spotify.com/dashboard>, create an app (any name),
and add this exact redirect URI:

```
http://127.0.0.1:8912/callback
```

(8912 is `RTR_PLAYBACK_REDIRECT_PORT`; if it collides with something, pick
another port, set the env var, and register that URI instead.) No client
secret is used anywhere — the flow is Authorization Code + PKCE.

Then in `.env`:

```
RTR_PLAYBACK_ENABLED=1
RTR_PLAYBACK_CLIENT_ID=<the app's client id>
# optional: pin a Spotify Connect device by name substring; empty = the
# currently active device
RTR_PLAYBACK_DEVICE_NAME=
```

Checkpoint: `.env` contains both values (Claude: verify presence, never
print the client id back into the transcript unnecessarily).

### 0.3 Authorize once **[HUMAN completes in browser]**

```bash
read-the-room-spotify-auth
```

Opens the consent page; after approval the terminal prints
`authorized ✓ token cache written to data/spotify_token.json`.

Checkpoint (Claude can run this):

```bash
python - <<'EOF'
from playback import PlaybackConfig, SpotifyProvider
provider = SpotifyProvider(PlaybackConfig.from_env())
print([f"{d.name} (active={d.active})" for d in provider.devices()])
EOF
```

Must print at least one device with the **Spotify app open** on the Mac
(open it if the list is empty — a closed app is not a Connect device).
`ProviderError: not authenticated` means 0.3 didn't complete;
`no Spotify Connect device matching ...` means `RTR_PLAYBACK_DEVICE_NAME`
doesn't substring-match any online device.

### 0.4 Curate and map playlists **[HUMAN curates; Claude scaffolds]**

Create playlists in Spotify keyed by rulebook genre × energy tier (e.g.
`RTR · Pop · high`), each with **at least ~15 tracks** (fewer defeats
recently-played suppression and the selector will repeat). A useful
starting set — the cells a small live room actually fires (buckets
solo/pair/4, mood mostly excited/chill during a test session):

| genre (rulebook pool) | tiers worth mapping first |
|---|---|
| Pop | high, mid |
| Hip-Hop | high, mid |
| Jazz | mid, low |
| Lofi Beats | low |
| Classical | low |

Then write `data/playlists.json` (gitignored; Claude: offer to write this
file from the user's pasted playlist links):

```json
{"schema_version": 1,
 "playlists": {
   "Pop":        {"high": "https://open.spotify.com/playlist/...",
                  "mid":  "spotify:playlist:..."},
   "Hip-Hop":    {"high": "...", "mid": "..."},
   "Jazz":       {"mid": "...", "low": "..."},
   "Lofi Beats": {"low": "..."},
   "Classical":  {"low": "..."}}}
```

URLs, `spotify:playlist:` URIs, and bare IDs are all accepted. Partial
coverage is fine — unmapped cells hold — but a malformed file fails the
dashboard at startup **on purpose**.

Checkpoint:

```bash
python - <<'EOF'
from pathlib import Path
from playback import PlaybackConfig, SpotifyProvider, load_playlists
cfg = PlaybackConfig.from_env()
mapping = load_playlists(Path(cfg.playlists_path))
print(f"{len(mapping)} cells mapped")
provider = SpotifyProvider(cfg)
genre, tier = next(iter(mapping))
tracks = provider.tracks_for(genre, tier)
print(f"({genre}, {tier}) -> {len(tracks)} tracks, e.g. {tracks[0].title!r}")
EOF
```

Must print a nonzero cell count and real track titles.

## The gate (all four must pass, on the Mac)

### (a) M2/M3 regression: `--fallback` benchmark unchanged and passing

```bash
RTR_TORCH_THREADS=2 python scripts/bench_headcount.py --fallback
```

Same criteria as M2/M3: headcount p95 < 1.37 s on contended hops, emotion
overall p95 < 1.2 s absolute. Playback is control-plane I/O on its own
thread — a few HTTP calls per track, no audio decode in-process — so **any
drift here is a bug, not a cost**. Record the numbers as a new
"Milestone 4 gate" row set in the README results section.

### (b) pytest green

```bash
python -m pytest -q
```

Covers the playback selector, tier mapping, gentle-DJ/override controller
semantics, override log schema, the Spotify provider over a mock
transport, the sensing additive fields, and the contamination gate logic.
No network, no Spotify, no models.

### (c) 30-minute live session with real playback

```bash
read-the-room-dashboard
```

Startup line must say `playback enabled`. Browser on
<http://127.0.0.1:8000>. Have the Spotify app open. Talk naturally; let
recommendations fire. Checklist, in rough order:

1. **Bootstrap:** with nothing playing, the first non-guard recommendation
   starts a track from the mapped playlist for its cell/tier. The bar
   shows **PLAYBACK LIVE**, track + source playlist, and the header
   `playback` chip flips to **on** (RoomState is now tagging evidence as
   contaminated).
2. **Boundary transitions only:** while a track plays, later
   recommendations appear as `next: <track>` in the bar and take over
   **only when the current track ends**. Zero unprompted mid-track cuts
   in 30 minutes. (Observation to record in FIELD-NOTES, not a failure:
   Spotify's queue is append-only, so rapid successive recommendations
   may leave an extra queued track — note it if seen.)
3. **Overrides round-trip — at least one of each [HUMAN taps]:**
   - **Skip** → playing track cut immediately, replacement plays;
   - **Wrong vibe** → new track from a *different* (cell-adjacent) genre
     pool than the vetoed recommendation's;
   - **manual picker** → the chosen (genre, tier) plays.
   Then inspect `data/overrides/<today>.jsonl` (Claude: do this
   programmatically): one line per tap, `schema_version: 1`, correct
   `action`, `now_playing` matching what the bar showed at tap time,
   `recommendation.matched_cell` + `boundaries_snapshot` populated,
   `state.playback_active: true`, and manual lines carrying `chosen`.
4. **Implicit weak positives:** after a track plays to completion with no
   override, a `played_through` line appears without any tap.
5. **Provider failure degrades, sensing survives [HUMAN]:** quit the
   Spotify app mid-run. Within one poll interval the badge flips to
   **PLAYBACK DEGRADED — SHADOW MODE**, override controls hide, and — the
   actual point — the quadrant/headcount/VAD cards keep updating (the
   engine never blocks on playback). Reopen Spotify, play anything once
   so a device is active again: the controller returns to **active** on a
   later recommendation without a restart.
6. **Report reads it all:**

   ```bash
   python scripts/tuning_report.py
   ```

   Prints the overrides sections (rate per cell, veto boundary
   clustering, tier disagreement) over today's file, writes nothing.

### (d) Contamination measurement protocol (controlled room)

The v1 gate (stricter VAD threshold + noise-floor-relative babble) was
built against the pool session's failure signature; this measures what it
actually does. **Measure-first: v1 sets no pass/fail thresholds — the
numbers recorded here become the baseline that sets them (and feed the M5
music-detection decision).** Quiet, closed room; known participants (1–2);
fixed, moderate speaker volume throughout.

Claude: for each phase, note start/end wall-clock times, then afterwards
pull the numbers (bucket, `raw_clusters`, `crowd_weight`, `dispersion`,
`fragmentation`, `smoothed_log2`, `speech_ratio`, `loudness_dbfs`,
`playback_active`) from the frames captured in `data/annotations/` — so
**[HUMAN] tap Good/Wrong a few times per phase** to snapshot them (the
verdict itself doesn't matter here; the tap banks the frame).

| phase | duration | what happens | what to watch |
|---|---|---|---|
| 1. baseline quiet | 5 min | no playback, no speech | bucket holds/stale; noise floor seeds |
| 2. baseline speech | 5 min | solo, then two people talking | bucket solo→pair; the clean reference for 4/6 |
| 3. instrumental, silent room | 5 min | instrumental playlist plays, nobody talks | headcount does NOT grow; `playback_active` true on every frame |
| 4. instrumental + speech | 5 min | same music, two people talk | speech still certified (voice bar responds); bucket ≈ pair, no phantom growth |
| 5. vocal music, silent room | 5 min | vocal playlist, nobody talks | **the known v1 weakness**: watch for phantom clusters/bucket creep from sung vocals |
| 6. vocal + speech | 5 min | vocal music, two people talk | drift vs phase 4 quantifies the vocal penalty |

Record everything in a new dated `docs/FIELD-NOTES.md` entry (session
setup, per-phase table like the pool entry, takeaways). If phase 5/6 shows
phantom growth, note the magnitude and try one knob —
`RTR_VAD_PLAYBACK_THRESHOLD` at `0.85` — for a single repeat of phase 5;
record both runs. Do **not** tune further in-session; that's what the
baseline is for.

## Shakedown scenarios (best-effort, not gating)

| Scenario | Expectation |
|---|---|
| Recommendation for an unmapped cell | Nothing plays/queues, current track unaffected, log line notes the unmapped pool — silence-beats-wrong-guess |
| Skip with nothing queued and no prior rec | Playback pauses (a veto means silence beats it); no crash |
| `RTR_PLAYBACK_DEVICE_NAME` set to an offline device | ProviderError → degraded badge; recovers when the device appears |
| Laptop lid events / network blips | Controller degrades and recovers on its own; engine heartbeat never gaps |
| Dashboard restart mid-track | Music keeps playing (Spotify owns playback); new session re-attaches via `now_playing` on the first poll |

## Out of scope (deliberately — see M4-PROPOSAL "Deferred")

- Echo cancellation / reference-signal subtraction (needs a local
  provider that owns the output PCM).
- ML music-detection gate — waits for this plan's part (d) baseline.
- Learned/automatic tuning — M4 collects the override corpus; M5+ learns
  from it.
- Auto-volume, crossfade logic, multi-zone orchestration.
