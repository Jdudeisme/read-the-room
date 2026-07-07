# Read the Room

An ambient room-sensing engine. It listens to a live microphone and publishes a
rolling **RoomState** — loudness, activity, speech presence, and emotional tone
— a few times per second-scale window, entirely on-CPU and on-device.

**Milestone 1:** loudness / speech / emotion engine + console renderer.
**Milestone 2:** headcount estimation — ECAPA speaker embeddings +
clustering, published as power-of-2 occupancy buckets with confidence and
staleness.
**Milestone 3:** shadow-mode mapping layer (RoomState → music
recommendation, never played), live web dashboard, and a Good/Wrong
annotation loop feeding an offline tuning report.
**Milestone 4 (this branch):** playback — recommendations select tracks
from human-curated Spotify playlists (gentle-DJ: transitions on track
boundaries only), human override capture (the strong labels), and
contamination handling v1 now that the mic can hear the system's own
output.

## Architecture

Layered RoomState producers over a 5 s analysis window with a 2 s hop,
EMA-smoothed:

1. **Continuous DSP layer** (numpy, every window, ~free): RMS loudness (dBFS),
   onset/activity density, spectral balance. This is the heartbeat — the
   console always has live numbers, even while models warm up.
2. **VAD gate** (Silero VAD, always on, streaming): per-window speech ratio.
   The emotion layer only ever sees windows the VAD certified as containing
   speech — silence and non-speech noise can never produce a phantom reading.
3. **Emotion layer** ([audeering wav2vec2 valence/arousal](https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim),
   VAD-gated, own worker thread): continuous valence/arousal in [-1, 1],
   `null` when there is no speech, always published with a confidence and a
   staleness age.
4. **Headcount layer** ([SpeechBrain ECAPA-TDNN](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb),
   VAD-gated, own worker thread, M2): speaker embeddings over certified
   speech runs, accumulated in a rolling ~90 s evidence buffer and clustered
   (average-linkage, cosine threshold — the cluster count is an output,
   never an input). Two regimes: a real count up to ~8 separable speakers,
   blending into an ordinal crowd-density estimate beyond (`crowd_weight`
   in the debug logs shows the blend). Published as a power-of-2 bucket,
   EMA-smoothed in log2 space with hysteresis so one loud laugh never flaps
   the bucket. During silence the bucket holds and `headcount_staleness_s`
   grows — silence is absence of evidence, not evidence of an empty room.

VAD certification is centralized in the engine: emotion and headcount consume
the same gate and never run their own VAD. (This is also where the future
music-detection gate slots in — a deliberate seam, since vocal music would
otherwise read as a stable phantom speaker once RTR starts playing music.)

One ML framework: PyTorch (CPU). No TensorFlow, no GPU required.

### RoomState (the consumer contract)

Per window: `timestamp`, `loudness_dbfs`, `activity_density`,
`spectral_balance` {low, mid, high}, `speech_ratio`, `valence`, `arousal`,
`emotion_confidence`, `emotion_staleness_s`, `headcount_bucket`
(solo / pair / 4 / 8 / … / 1024 / crowd — powers of 2, geometric-midpoint
boundaries), `headcount_confidence`, `headcount_staleness_s`, plus derived
`energy` (0–1), `mood` (excited / tense / chill / flat) and `trend`
(rising / stable / falling over the last ~60 s).

Consumers implement one method, `on_state(state)`. The console renderer is
deliberately thin; M2's dashboard swaps in without touching the engine.
`--jsonl` writes the exact wire format to a file if you want to see it.

## Setup

Requires **Python 3.12** — not newer. The demo target (2019 Intel MacBook Pro)
is capped at torch 2.2.2, the last PyTorch release with Intel-macOS wheels,
and its wheels stop at Python 3.12. Both platforms pin the same torch version
so performance numbers transfer.

### Windows

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### macOS (Intel)

```bash
python3.12 -m venv .venv          # e.g. brew install python@3.12
source .venv/bin/activate
pip install -e '.[dev]'
```

**Microphone permission:** macOS prompts for mic access the first time the
process opens an input stream, and the permission attaches to the *launching
app* (Terminal / iTerm2 / VS Code). If you ever denied it, the engine will see
pure silence rather than fail loudly — fix it under
**System Settings → Privacy & Security → Microphone** and enable your
terminal. After changing the setting, restart the terminal app.

### First run

The first emotion-enabled run downloads the model (~1.2 GB) into the
HuggingFace cache (`HF_HOME` to relocate it). On networks with
TLS-intercepting proxies/AV the download uses the OS certificate store
(`RTR_OS_TRUSTSTORE=1`, the default).

## Usage

```bash
read-the-room                     # live mic, full engine (or: python -m sensing)
read-the-room --list-devices      # pick a mic, then set RTR_INPUT_DEVICE or --device
read-the-room --source synth      # synthetic signal, no mic needed
read-the-room --no-emotion        # run without the emotion layer
read-the-room --no-headcount      # run without the headcount layer
read-the-room --jsonl out.jsonl   # also log every RoomState as JSON lines
read-the-room --ticks 10          # exit after 10 windows (smoke tests)
```

Configuration is via environment variables / a `.env` file — see
[.env.example](.env.example) for every knob and its default.

## Dashboard (M3, shadow mode)

```bash
read-the-room-dashboard                  # live mic (or: python -m dashboard)
read-the-room-dashboard --source synth   # no mic needed
read-the-room-dashboard --port 8000      # default binds 127.0.0.1:8000
```

Open http://127.0.0.1:8000. The dashboard process hosts the sensing engine
plus the M3 **mapping layer**: a rulebook keyed by (headcount bucket,
valence band, arousal band) — seeded from the 2020 thesis GenrePicker grid —
turns each RoomState into a `Recommendation` (genre pool, energy action,
targets, confidence, and full attribution: the rulebook cell that fired and
every boundary value in effect). Band cutoffs and dwell times are
`RTR_MAPPING_*` env vars ([.env.example](.env.example)).

**Shadow mode** is the default: unless playback is explicitly enabled
(M4, below), nothing is ever played and the mic never hears the system's
own output. Shadow remains a first-class state — the dashboard reverts to
it whenever playback is off, unconfigured, or degraded.

The page shows the valence/arousal quadrant (with a ~2 min trail),
headcount / regime / voice-activity cards, live `emotion age` /
`headcount age` staleness (the honest end-to-end health numbers), a
10-minute valence/arousal timeline (browser memory only), and the shadow
recommendation with **Good call / Wrong call** buttons.

### Annotation log

Each button tap appends one line to `data/annotations/YYYY-MM-DD.jsonl`
(gitignored; override the directory with `RTR_DASHBOARD_ANNOTATIONS_DIR`):

```json
{"schema_version": 1, "ts": ..., "verdict": "good" | "wrong",
 "state": { ...RoomState as displayed... },
 "recommendation": { ...incl. matched_cell + boundaries_snapshot... }}
```

The page sends the state/recommendation it was *displaying* at tap time, so
the label always describes what the human actually saw.

### Tuning report

```bash
python scripts/tuning_report.py          # data/annotations + data/overrides
```

Prints verdict counts per rulebook cell, wrong-call clustering near band
boundaries, and suggested boundary shifts with the number of past verdicts
each would have flipped. With override logs present (M4) it adds override
rate per cell (`vetoes / (vetoes + played_through)`), veto clustering near
boundaries, and manual-pick tier disagreement. Output only — it never
modifies anything; apply suggestions by editing `RTR_MAPPING_*` /
`RTR_PLAYBACK_TIER_*` in `.env` and re-observing. Learned (automatic)
tuning is deliberately deferred until the override corpus exists — see
[docs/M4-PROPOSAL.md](docs/M4-PROPOSAL.md).

## Playback (M4)

Recommendations become actual music through **Spotify Connect** (control
traffic only — playback decode/output never happens in this process, so the
engine's performance budget is untouched). Requires Spotify **Premium**.

First time on the Mac? [docs/M4-TEST-PLAN.md](docs/M4-TEST-PLAN.md) is a
step-by-step walkthrough (setup + the milestone gate) written to be run
with Claude Code — ask it to walk you through the M4 test plan.

One-time setup:

1. Register an app at [developer.spotify.com](https://developer.spotify.com/dashboard)
   with redirect URI `http://127.0.0.1:8912/callback` (the port is
   `RTR_PLAYBACK_REDIRECT_PORT`). No client secret is needed (PKCE).
2. In `.env`: `RTR_PLAYBACK_ENABLED=1`, `RTR_PLAYBACK_CLIENT_ID=<id>`, and
   optionally `RTR_PLAYBACK_DEVICE_NAME=<name substring>` to pin a device
   (default: whatever device is active).
3. Authorize once: `read-the-room-spotify-auth` — the token cache in
   `data/` refreshes itself afterwards.
4. Curate playlists and map them in `data/playlists.json` (gitignored),
   keyed by rulebook genre and energy tier:

   ```json
   {"schema_version": 1,
    "playlists": {
      "Pop":  {"high": "spotify:playlist:...", "mid": "spotify:playlist:..."},
      "Jazz": {"low": "https://open.spotify.com/playlist/..."}}}
   ```

   Partial coverage is fine — unmapped cells simply hold. Track selection is
   **playlist-mapped, not algorithmic**: human curation stays in the loop,
   and nothing is built on Spotify's deprecated recommendation endpoints.

Then run `read-the-room-dashboard` as usual. The shadow bar becomes a
**now-playing bar**. Behavior is *gentle-DJ*: a new recommendation never
interrupts the playing track (it replaces what plays next, on the track
boundary); tiers move with `target_arousal` + `energy_action`
(`RTR_PLAYBACK_TIER_*` cutoffs); volume stays human-owned. Only the human
override controls cut mid-track:

- **Skip** — veto the playing track (plays the queued next, or resamples);
- **Wrong vibe** — veto the *selection*: resample from a cell-adjacent
  rulebook pool;
- **manual picker** — play any mapped (genre, tier) outright.

Every override appends one line to `data/overrides/YYYY-MM-DD.jsonl` with
the tap-time state/recommendation snapshot; a track that plays to
completion logs a `played_through` weak positive. These are the strong
labels the learned-tuning work (M5+) has been waiting for. Provider
failures (token expiry, device gone, rate limit) surface on the dashboard
and degrade to shadow mode; the label capture never depends on the
provider being alive.

**Contamination handling v1:** while playback is active, RoomState carries
`playback_active`/`playback_track_id` (so all downstream evidence is
taggable), evidence certification uses a stricter VAD threshold
(`RTR_VAD_PLAYBACK_THRESHOLD`), and the crowd/babble heuristic keys on
loudness relative to a rolling noise floor rather than absolute dBFS — see
[docs/M4-PROPOSAL.md](docs/M4-PROPOSAL.md) and the pool-session analysis in
[docs/FIELD-NOTES.md](docs/FIELD-NOTES.md).

## Performance budget (run this on the MacBook first)

Budget arithmetic: the 2 s hop, minus emotion's measured 0.63 s floor (M1,
2019 Intel MacBook Pro), leaves **1.37 s (p95) for headcount**. Both models
run on their own worker threads, so the real risk is CPU contention:

```bash
python scripts/bench_headcount.py               # standalone timing
python scripts/bench_headcount.py --concurrent  # strict every-hop gate
python scripts/bench_headcount.py --fallback    # every-other-hop gate — THE M2 GATE
```

`--concurrent` fails on this machine on a relative-drift technicality even
though no hop misses its deadline; the milestone gate is `--fallback`, which
validates the pre-approved fallback config directly:
`RTR_HEADCOUNT_MIN_INTERVAL_S=4.0` (headcount every other hop) plus
`RTR_TORCH_THREADS=2` (limit core oversubscription). Full rationale and
pass criteria for each mode are in
[docs/M2-TEST-PLAN.md](docs/M2-TEST-PLAN.md).

The emotion model is the more expensive of the two. It runs on its own worker
thread (it can never stall the DSP/VAD heartbeat), is rate-limited by
`RTR_EMOTION_MIN_INTERVAL_S`, and must average under the 2 s hop to update
every window. Measure it on day one:

```bash
python scripts/bench_emotion.py
```

The script prints a per-window cost and a verdict. If the machine misses the
budget, the fallbacks — in order — are:

1. **Raise `RTR_EMOTION_MIN_INTERVAL_S`** (e.g. `6.0`): emotion updates every
   third window instead of every window; `emotion_staleness_s` reports the
   age honestly and nothing else is affected.
2. **Swap `RTR_EMOTION_MODEL`** to a smaller checkpoint (a wav2vec2-*base*
   valence/arousal fine-tune ≈ 4× cheaper) and re-run the benchmark.

### Results (2019 Intel MacBook Pro, `RTR_TORCH_THREADS=2`)

| Benchmark | Scenario | mean | p95 | Budget | Verdict |
|---|---|---|---|---|---|
| `bench_emotion.py` | solo | 0.66 s | 0.66 s | < 2.0 s hop | OK |
| `bench_headcount.py --fallback` | headcount, contended hops | 1.03 s | 1.09 s | < 1.37 s | PASS |
| `bench_headcount.py --fallback` | emotion, overall | 0.90 s | 1.09 s | < 1.2 s absolute | PASS |

`--fallback` is the milestone gate; see
[docs/M2-TEST-PLAN.md](docs/M2-TEST-PLAN.md) for why `--concurrent` fails
here without any hop actually missing its deadline, and for the full
contended-vs-uncontended emotion breakdown.

### Milestone 3 gate (2019 Intel MacBook Pro, `RTR_TORCH_THREADS=2`)

All four parts of the M3 gate pass; see
[docs/M3-TEST-PLAN.md](docs/M3-TEST-PLAN.md) for the full checklist.

| Benchmark | Scenario | mean | p95 | Budget | Verdict |
|---|---|---|---|---|---|
| `bench_headcount.py --fallback` | headcount, contended hops | 0.99 s | 1.05 s | < 1.37 s | PASS |
| `bench_headcount.py --fallback` | emotion, overall | 0.87 s | 1.06 s | < 1.2 s absolute | PASS |

M3 adds no compute to the engine path (the mapper is a few comparisons per
hop, the dashboard is I/O), so this is the same M2 gate re-run as a
regression check, not a new cost. The other three gate parts — 10-minute
live dashboard run, `pytest` (102 passed), and a real button-tap annotation
round-trip verified against `tuning_report.py` — were also confirmed live.

### Milestone 4 gate (2019 Intel MacBook Pro, `RTR_TORCH_THREADS=2`)

Gate progress — see [docs/M4-TEST-PLAN.md](docs/M4-TEST-PLAN.md) for the
full checklist. Parts (a) benchmark regression and (b) `pytest`
(185 passed) are green; parts (c) live playback session and (d) the
contamination measurement protocol are pending.

| Benchmark | Scenario | mean | p95 | Budget | Verdict |
|---|---|---|---|---|---|
| `bench_headcount.py --fallback` | headcount, contended hops | 1.08 s | 1.11 s | < 1.37 s | PASS |
| `bench_headcount.py --fallback` | emotion, overall | 0.95 s | 1.16 s | < 1.2 s absolute | PASS |

Playback is control-plane I/O on its own thread (no audio decode
in-process), so this too is the M2 gate re-run as a regression check; the
numbers sit within run-to-run variance of the M2/M3 rows. Setup on this
machine surfaced one real API-shape bug — Spotify hard-403s
`GET /playlists/{id}/tracks` for apps registered after its Nov 2024 API
changes; fixed by moving the provider to `GET /playlists/{id}/items`
(entries keyed `item`), verified live against all eight mapped playlist
cells.

The live runs on this machine surfaced a real M2 calibration bug — a solo
speaker ratcheted solo → pair → 4 → 8 as the evidence buffer filled — which
was then root-caused offline: measured same-voice ECAPA distances on 1.25s
segments run ~0.35 mean (p90 0.47) even on clean synthetic speech, so the
original `0.40` clustering threshold sat *inside* the same-speaker
distribution, and the absolute 2-segment min-mass rule let far-tail
fragments count as people once ~100+ segments accumulated. Fixed by
recalibrating the default threshold to `0.70` (different-voice pairs
measure ~0.9) and making min-mass proportional to buffered evidence
(`RTR_HEADCOUNT_MIN_CLUSTER_FRAC`). Two residual cautions: keep the mic
input level modest (a hot input level inflates the crowd-regime heuristic —
a loud solo speaker with dispersed embeddings can read as babble), and very
similar voices may now merge (undercounting beats phantom crowds for this
use case).

## Tests

```bash
python -m pytest
```
