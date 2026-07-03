# Read the Room

An ambient room-sensing engine. It listens to a live microphone and publishes a
rolling **RoomState** — loudness, activity, speech presence, and emotional tone
— a few times per second-scale window, entirely on-CPU and on-device.

**Milestone 1:** loudness / speech / emotion engine + console renderer.
**Milestone 2 (this branch):** headcount estimation — ECAPA speaker embeddings
+ clustering, published as power-of-2 occupancy buckets with confidence and
staleness. Dashboard consumer still to come.

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

## Performance budget (run this on the MacBook first)

Budget arithmetic: the 2 s hop, minus emotion's measured 0.63 s floor (M1,
2019 Intel MacBook Pro), leaves **1.37 s (p95) for headcount**. Both models
run on their own worker threads, so the real risk is CPU contention — which
is why the M2 gate is the **concurrent** benchmark:

```bash
python scripts/bench_headcount.py               # standalone timing
python scripts/bench_headcount.py --concurrent  # THE M2 GATE
```

PASS requires headcount p95 < 1.37 s while emotion runs concurrently AND
emotion staying within 25% of its M1 baseline. On FAIL, the pre-approved
fallback: `RTR_HEADCOUNT_MIN_INTERVAL_S=4.0` (every other hop), plus
`RTR_TORCH_THREADS=2` to limit core oversubscription. Live-audio test tiers
are in [docs/M2-TEST-PLAN.md](docs/M2-TEST-PLAN.md).

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

## Tests

```bash
python -m pytest
```
