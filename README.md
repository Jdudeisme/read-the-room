# Read the Room

An ambient room-sensing engine. It listens to a live microphone and publishes a
rolling **RoomState** — loudness, activity, speech presence, and emotional tone
— a few times per second-scale window, entirely on-CPU and on-device.

**Milestone 1 (this commit):** loudness / speech / emotion engine + console renderer.
**Milestone 2 (next):** headcount estimation + dashboard consumer. The RoomState
schema already carries a stubbed `headcount_bucket` field so the wire format
will not change.

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

One ML framework: PyTorch (CPU). No TensorFlow, no GPU required.

### RoomState (the consumer contract)

Per window: `timestamp`, `loudness_dbfs`, `activity_density`,
`spectral_balance` {low, mid, high}, `speech_ratio`, `valence`, `arousal`,
`emotion_confidence`, `emotion_staleness_s`, `headcount_bucket`
(solo / pair / 4 / 8 / 16 / crowd — always `null` in M1), plus derived
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
read-the-room --no-emotion        # DSP + VAD only
read-the-room --jsonl out.jsonl   # also log every RoomState as JSON lines
read-the-room --ticks 10          # exit after 10 windows (smoke tests)
```

Configuration is via environment variables / a `.env` file — see
[.env.example](.env.example) for every knob and its default.

## Performance budget (run this on the MacBook first)

The emotion model is the only expensive component. It runs on its own worker
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
