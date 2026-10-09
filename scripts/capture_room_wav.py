"""Capture a room WAV through the engine's real audio path, for offline
headcount analysis.

Why this exists (FIELD-NOTES 2026-09-06): this machine reads `pair` for a
solo speaker (`raw_clusters` 2, `dispersion` 0.551 morning / 0.588 evening),
and every candidate explanation — laptop position relative to a wall and
corner, the SoundWire mic array's own beamforming/AGC, or plain laptop-mic
embedding scatter, which headcount.py's min-mass comment already measures at
~0.6 mean pairwise cosine distance — was only answerable by running another
live session and reading a dashboard. A recorded window makes the question
answerable offline and repeatably: capture once per condition, then run
`scripts/analyze_headcount_wav.py` over the files as often as the analysis
changes.

PRIVACY: this is an opt-in capture of room audio. Nothing in the running
system records; audio is written only when a human runs this script. Output
lands in `data/captures/`, which `.gitignore` already excludes via `data/*`
— these files are local by design and are never committed. Delete them once
the question is settled.

Capture goes through `MicSource`, not a fresh sounddevice stream, so the WAV
carries whatever the real path does to the signal: device resolution, the
48k->16k fallback resampler, and any driver-side processing the array
applies. A cleaner capture would characterise a pipeline RTR does not have.

    python scripts/capture_room_wav.py --seconds 90 --note "center of room"
    python scripts/capture_room_wav.py --list-devices

M12-01 (`--reference`, opt-in): also record the laptop's own playback
through a WASAPI loopback (scripts/loopback_reference.py) in the same
process, so the mic stays a single stream (two streams on this array changed
embedding spread on 2026-09-06). Writes `<name>.ref.wav` (16 kHz mono,
resampled with RTR's own Resampler) and a `reference` block in the sidecar
with both streams' clock logs. The loopback is everything the laptop
plays, including calls and notifications, so it is written only under
this flag. Without the flag, this script and its sidecar are unchanged.

Each run writes `<name>.wav` plus `<name>.json` carrying the provenance the
WAV cannot: host, OS, device, capture rate, whether the resampler ran, the
config constants in force, and your `--note`. A WAV without that sidecar is
uninterpretable three months later, which is precisely the failure this
script exists to prevent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
import time
import wave
from pathlib import Path

import numpy as np

from sensing import dsp
from sensing.audio import MicSource, Resampler, list_input_devices
from sensing.config import Config

# Below this the capture is almost certainly not hearing the room. Chosen
# against measured session levels, not taste: FIELD-NOTES 2026-09-06 records
# real speech on this path at -31.5 dBFS and the *deaf* stream that started
# that session at -41.5 dBFS with speech_ratio 0.000 for seven minutes. -45
# sits below any real speech level recorded on either machine and above a
# silent stream, so it separates the two cases without flagging a quiet room.
DEAF_CAPTURE_DBFS = -45.0

# PortAudio delivers in bursts; poll well under the block period so the ring
# never has to absorb more than a few blocks between reads.
POLL_S = 0.1

# Non-tty progress cadence: frequent enough to show the take is alive,
# sparse enough that a 90 s capture logs nine lines instead of nine hundred.
PROGRESS_REPORT_S = 10.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record a room WAV through RTR's own capture path."
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=90.0,
        help="capture duration (default 90 s: one full headcount buffer_s "
        "at the 90 s default, so the analysis sees a saturated buffer)",
    )
    parser.add_argument(
        "--note",
        default="",
        help='free text describing the condition, e.g. "center of room, '
        'enhancements off". Recorded in the sidecar; this is the variable '
        "you are testing, so do not leave it blank.",
    )
    parser.add_argument(
        "--name",
        default="",
        help="basename for the output pair (default: timestamped)",
    )
    parser.add_argument(
        "--out-dir",
        default="data/captures",
        help="output directory (default data/captures, gitignored)",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="input device name substring or index; default = configured "
        "RTR_INPUT_DEVICE, so the capture matches the engine's",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="list input devices and exit"
    )
    parser.add_argument(
        "--reference",
        action="store_true",
        help="M12-01: also record the laptop's playback (WASAPI loopback) to "
        "<name>.ref.wav. It holds everything the laptop plays, including "
        "notifications: use Do Not Disturb, and only with consent",
    )
    args = parser.parse_args()

    if args.list_devices:
        print(list_input_devices())
        return 0

    config = Config.from_env()
    device = args.device if args.device is not None else config.input_device

    # Ring sized to the whole capture plus slack: this script drains it
    # continuously, but an oversized ring costs nothing and makes a stalled
    # reader lose nothing rather than silently wrap.
    source = MicSource(
        sample_rate=config.sample_rate,
        buffer_seconds=args.seconds + 5.0,
        device=device,
    )
    reference = None
    if args.reference:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from loopback_reference import LoopbackRecorder

        reference = LoopbackRecorder()
        print(f"reference: loopback of '{reference.start()}'")
    source.start()

    started_wall = dt.datetime.now().astimezone()
    print(
        f"capturing {args.seconds:.0f} s from '{source.device_name}' "
        f"@ {source.capture_rate} Hz"
        + (
            f" -> resampled to {config.sample_rate} Hz"
            if source.capture_rate != config.sample_rate
            else ""
        )
    )
    if args.note:
        print(f"condition: {args.note}")
    print("speak normally; ctrl-c aborts without writing.\n")

    chunks: list[np.ndarray] = []
    position = 0
    t0 = time.monotonic()
    mic_clock: list[tuple[float, int]] = []  # (monotonic, samples so far), --reference only
    # A \r-updated counter is right at a terminal and wrong everywhere else:
    # piped or captured, every poll becomes its own line (~900 of them for a
    # 90 s take). Redraw only on a tty; otherwise report at intervals.
    live = sys.stdout.isatty()
    next_report = PROGRESS_REPORT_S
    try:
        while True:
            elapsed = time.monotonic() - t0
            if elapsed >= args.seconds:
                break
            time.sleep(POLL_S)
            new, position = source.ring.read_since(position)
            if new.size:
                chunks.append(new)
                if reference is not None:
                    mic_clock.append((time.monotonic(), position))
            captured_s = sum(c.size for c in chunks) / config.sample_rate
            if live:
                print(
                    f"\r  {elapsed:5.1f} s elapsed / {captured_s:5.1f} s captured", end=""
                )
            elif elapsed >= next_report:
                print(f"  {elapsed:5.1f} s elapsed / {captured_s:5.1f} s captured")
                next_report += PROGRESS_REPORT_S
    except KeyboardInterrupt:
        source.stop()
        if reference is not None:
            reference.stop()
        print("\naborted; nothing written.")
        return 130
    finally:
        source.stop()
    ref_result = reference.stop() if reference is not None else None

    print()
    if not chunks:
        print("ERROR: the stream delivered no samples at all. Nothing written.")
        return 1

    audio = np.concatenate(chunks)
    elapsed = time.monotonic() - t0
    continuity = audio.size / (elapsed * config.sample_rate)
    measured = dsp.analyze(audio, config.sample_rate)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started_wall.strftime("%Y%m%d-%H%M%S")
    name = args.name or f"capture-{stamp}"
    wav_path = out_dir / f"{name}.wav"
    json_path = out_dir / f"{name}.json"

    _write_wav(wav_path, audio, config.sample_rate)

    sidecar = {
        "schema_version": 1,
        "note": args.note,
        "started_at": started_wall.isoformat(),
        "duration_s": round(audio.size / config.sample_rate, 2),
        "wav": wav_path.name,
        # Provenance mirrors the M6 signature source stamp (f07a2df): a
        # recording is only comparable to another if you can prove the
        # capture path matched.
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "source": "MicSource",
        "device_name": source.device_name,
        "device_requested": device,
        "capture_rate": source.capture_rate,
        "sample_rate": config.sample_rate,
        "resampled": source.capture_rate != config.sample_rate,
        "continuity": round(continuity, 4),
        "rms_dbfs": round(measured.rms_dbfs, 2),
        # The clustering constants in force at capture time. The analysis
        # script re-reads these from the environment it runs in; recording
        # them here is what lets you notice the two disagreeing.
        "config_at_capture": {
            "window_s": config.window_s,
            "hop_s": config.hop_s,
            "vad_threshold": config.vad_threshold,
            "headcount_cluster_threshold": config.headcount_cluster_threshold,
            "headcount_buffer_s": config.headcount_buffer_s,
            "headcount_min_interval_s": config.headcount_min_interval_s,
            "headcount_min_speech_ratio": config.headcount_min_speech_ratio,
            "headcount_min_cluster_frac": config.headcount_min_cluster_frac,
        },
    }
    if ref_result is not None:
        sidecar["reference"] = _write_reference(
            out_dir / f"{name}.ref.wav", ref_result, t0, mic_clock, config.sample_rate
        )
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {wav_path}  ({sidecar['duration_s']:.1f} s, {measured.rms_dbfs:.1f} dBFS)")
    print(f"wrote {json_path}")

    # Both checks exist because this session's predecessor lost twenty
    # minutes to a stream that opened cleanly and delivered near-silence
    # (FIELD-NOTES 2026-09-06, finding 2: "a deaf engine is pixel-identical
    # to a quiet room"). Fail loudly here instead of in the analysis.
    ok = True
    if measured.rms_dbfs < DEAF_CAPTURE_DBFS:
        print(
            f"\nWARNING: {measured.rms_dbfs:.1f} dBFS is below the {DEAF_CAPTURE_DBFS} dBFS "
            "deaf-stream floor.\n  The stream probably never woke up. Restart the "
            "capture and confirm the level rises before trusting this file."
        )
        ok = False
    if continuity < 0.95:
        print(
            f"\nWARNING: captured only {continuity:.1%} of real time - samples were "
            "dropped.\n  Analysis on this file will under-represent the session."
        )
        ok = False
    if ok:
        print("\ncapture looks healthy. Next:")
        print(f"  {sys.executable} scripts/analyze_headcount_wav.py {wav_path}")
    return 0


def _thin(log: list[tuple[float, int]], t0: float, every_s: float = 1.0) -> list[list[float]]:
    """Keep about one (seconds since t0, count) point per `every_s`."""
    out: list[list[float]] = []
    for t, n in log:
        if not out or t - t0 - out[-1][0] >= every_s:
            out.append([round(t - t0, 4), int(n)])
    return out


def _write_reference(path: Path, ref, t0: float, mic_clock, sample_rate: int) -> dict:
    """Resample the loopback to the mic's rate, write it, and describe it.
    The clock logs let scripts/analyze_reference.py measure drift."""
    mono16 = Resampler(ref.native_rate, sample_rate).process(ref.mono) if ref.mono.size else ref.mono
    _write_wav(path, mono16, sample_rate)
    level = dsp.analyze(mono16, sample_rate).rms_dbfs if mono16.size else None
    print(f"wrote {path}  ({mono16.size / sample_rate:.1f} s, "
          f"{'no audio' if level is None else f'{level:.1f} dBFS'})")
    if ref.error:
        print(f"WARNING: reference capture error: {ref.error}")
    return {
        "wav": path.name,
        "device_name": ref.device,
        "native_rate": ref.native_rate,
        "channels_downmixed": ref.channels,
        "duration_s": round(mono16.size / sample_rate, 2),
        "rms_dbfs": None if level is None else round(level, 2),
        # Positive: the loopback's first block arrived after the mic poll
        # loop started.
        "first_block_after_mic_start_s": (
            None if ref.started_at is None else round(ref.started_at - t0, 4)
        ),
        "max_block_gap_s": round(ref.max_gap_s, 3),
        "error": ref.error,
        # (seconds since mic start, cumulative frames). The mic counts
        # 16 kHz samples; the reference counts native-rate frames.
        "mic_clock": _thin(mic_clock, t0),
        "ref_clock": _thin(ref.blocks, t0),
    }


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """16-bit mono PCM. ECAPA and Silero both consume 16 kHz int16-derived
    audio in normal operation, so the quantisation is inside the models'
    ordinary operating range and costs the analysis nothing."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())


if __name__ == "__main__":
    raise SystemExit(main())
