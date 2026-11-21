"""Audio sources: microphone capture (sounddevice/PortAudio) and a synthetic
source for plumbing tests. Both feed a thread-safe ring buffer at 16 kHz mono.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Rates to try if the device won't open at the analysis rate directly.
_FALLBACK_RATES = (48_000, 44_100)

# Playback reference (M12-01c). JPad's WASAPI speaker runs shared-mode at
# 48 kHz (device listing, 2026-10-09), so the loopback is read at that rate
# and resampled here. 20 ms blocks keep the reference ring as fresh as the
# mic's: the 2026-10-09 self-tests returned every requested block in about
# its own duration, plus at most ~25 ms.
_REFERENCE_NATIVE_RATE = 48_000
_REFERENCE_CHANNELS = 2
_REFERENCE_BLOCK_S = 0.02

# M12-09 diagnostics (docs/M12-09-PLAN.md): how often CleanSource logs the
# live canceller's state and the reference's estimated lost audio. A
# logging cadence, not a calibration: 10 s spans ~5 hops and is shorter
# than one re-lock plus re-convergence (~12 s), so a stuck canceller shows
# within two lines.
CANCEL_REPORT_S = 10.0
# The opt-in diagnostic clip starts this long after the canceller's first
# lock, so it records the steady state, not the 8-30 s first convergence
# (FIELD-NOTES 2026-10-09).
CLIP_AFTER_LOCK_S = 30.0


class RingBuffer:
    """Single-writer single-reader circular float32 buffer.

    Tracks a monotonically increasing total-samples-written counter so readers
    can ask for "everything since position X" (used by the streaming VAD).
    """

    def __init__(self, capacity_samples: int):
        self._buf = np.zeros(capacity_samples, dtype=np.float32)
        self._capacity = capacity_samples
        self._written = 0  # total samples ever written
        self._lock = threading.Lock()

    @property
    def total_written(self) -> int:
        with self._lock:
            return self._written

    def write(self, samples: np.ndarray) -> None:
        samples = samples.astype(np.float32, copy=False).reshape(-1)
        total = samples.size
        if total >= self._capacity:
            samples = samples[-self._capacity :]
        with self._lock:
            skipped = total - samples.size  # oversized writes overwrite everything
            pos = (self._written + skipped) % self._capacity
            first = min(samples.size, self._capacity - pos)
            self._buf[pos : pos + first] = samples[:first]
            if first < samples.size:
                self._buf[: samples.size - first] = samples[first:]
            self._written += total

    def read_last(self, n: int) -> np.ndarray:
        """Most recent min(n, available) samples, oldest first."""
        with self._lock:
            n = min(n, self._written, self._capacity)
            if n == 0:
                return np.empty(0, dtype=np.float32)
            end = self._written % self._capacity
            start = (end - n) % self._capacity
            if start < end:
                return self._buf[start:end].copy()
            return np.concatenate((self._buf[start:], self._buf[:end]))

    def read_range(self, position: int, n: int) -> np.ndarray:
        """Samples [position, position + n) by absolute position (as counted
        by `total_written`), zero-filled where they are not, or no longer,
        in the buffer. Read-only. Used by the playback canceller (M12-02) to
        read the reference at the mic's aligned position."""
        out = np.zeros(n, dtype=np.float32)
        with self._lock:
            lo = max(position, self._written - self._capacity, 0)
            hi = min(position + n, self._written)
            if hi > lo:
                out[lo - position : hi - position] = self._buf[
                    np.arange(lo, hi) % self._capacity
                ]
        return out

    def read_since(self, position: int) -> tuple[np.ndarray, int]:
        """Samples written after `position` (a previous total_written value).

        Returns (samples, new_position). If the reader fell more than one
        buffer behind, returns only what is still present.
        """
        with self._lock:
            n = min(self._written - position, self._capacity)
            if n <= 0:
                return np.empty(0, dtype=np.float32), position
            end = self._written % self._capacity
            start = (end - n) % self._capacity
            if start < end:
                data = self._buf[start:end].copy()
            else:
                data = np.concatenate((self._buf[start:], self._buf[:end]))
            return data, self._written


class Resampler:
    """Anti-aliased linear resampler for capture-rate fallback (e.g. 48k -> 16k).

    Windowed-sinc FIR lowpass at 0.45 * target Nyquist, then linear
    interpolation. Quality is ample for VAD/emotion features; avoids a scipy
    dependency. Stateful across blocks (carries filter tail and fractional
    read position).
    """

    _TAPS = 63

    def __init__(self, source_rate: int, target_rate: int):
        self.ratio = source_rate / target_rate
        cutoff = 0.45 * (target_rate / source_rate)  # fraction of source Nyquist... see below
        # np.sinc operates on the normalised frequency axis: cutoff here is
        # expressed as a fraction of the source sample rate.
        n = np.arange(self._TAPS) - (self._TAPS - 1) / 2
        kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(self._TAPS)
        self._kernel = (kernel / kernel.sum()).astype(np.float32)
        self._carry = np.zeros(self._TAPS - 1, dtype=np.float32)
        # The previous block's last filtered sample. Interpolation runs over
        # [prev_last, *filtered], so a read position that falls between two
        # blocks interpolates across the boundary instead of clamping.
        self._prev_last = np.zeros(1, dtype=np.float32)
        # Read offset into the next block's [prev_last, *filtered] grid.
        # Invariant: 0 <= _frac (M8-04). Before M8-04 the grid ended at the
        # block's last sample, _frac went down to -1 at a third of block
        # boundaries, and np.interp clamped those reads to the next block's
        # first sample. That was up to 0.06 error on a 0.5-amplitude sine
        # at 44.1 kHz (the output rate was unaffected).
        self._frac = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        if block.size == 0:
            # np.convolve "valid" swaps its arguments when the signal is
            # shorter than the kernel, so the bare carry would yield two
            # spurious samples (found by M8-04's tests).
            return np.empty(0, dtype=np.float32)
        signal = np.concatenate((self._carry, block.astype(np.float32, copy=False)))
        filtered = np.convolve(signal, self._kernel, mode="valid")
        self._carry = signal[-(self._TAPS - 1) :]
        if filtered.size == 0:
            return np.empty(0, dtype=np.float32)
        grid = np.concatenate((self._prev_last, filtered))
        self._prev_last = filtered[-1:]
        # Every position below filtered.size has both neighbours in `grid`.
        positions = np.arange(self._frac, filtered.size, self.ratio)
        if positions.size == 0:
            self._frac -= filtered.size  # consumed without producing output
            return np.empty(0, dtype=np.float32)
        out = np.interp(positions, np.arange(grid.size), grid)
        self._frac = positions[-1] + self.ratio - filtered.size
        return out.astype(np.float32)


class MicSource:
    """Live microphone capture into a ring buffer at the analysis sample rate."""

    def __init__(self, sample_rate: int, buffer_seconds: float, device: str | None = None):
        self.sample_rate = sample_rate
        self.ring = RingBuffer(int(buffer_seconds * sample_rate))
        self._device = _resolve_device(device)
        self._stream = None
        self._stop_lock = threading.Lock()  # M8-07: stop() may race itself
        self._resampler: Resampler | None = None
        self.device_name = ""
        self.capture_rate = sample_rate

    def start(self) -> None:
        import sounddevice as sd

        last_error: Exception | None = None
        for rate in (self.sample_rate, *_FALLBACK_RATES):
            try:
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    device=self._device,
                    callback=self._callback,
                )
                stream.start()
            except sd.PortAudioError as exc:
                last_error = exc
                continue
            self._stream = stream
            self.capture_rate = rate
            if rate != self.sample_rate:
                self._resampler = Resampler(rate, self.sample_rate)
            info = sd.query_devices(stream.device, "input")
            self.device_name = info["name"]
            return
        raise RuntimeError(
            f"Could not open an input stream at {self.sample_rate} Hz or any "
            f"fallback rate. Last error: {last_error}"
        )

    def stop(self) -> None:
        # Take the stream under the lock, release it outside: a second
        # caller finds None and returns, so stop()/close() run at most once.
        with self._stop_lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0]
        if self._resampler is not None:
            mono = self._resampler.process(mono)
        if mono.size:
            self.ring.write(mono)


class SynthSource:
    """Deterministic synthetic source for end-to-end plumbing tests without a
    microphone: alternates 'speech-ish' modulated tone bursts with near-silence.
    """

    def __init__(self, sample_rate: int, buffer_seconds: float):
        self.sample_rate = sample_rate
        self.ring = RingBuffer(int(buffer_seconds * sample_rate))
        self.device_name = "synthetic"
        self.capture_rate = sample_rate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop_lock = threading.Lock()  # M8-07: stop() may race itself

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="synth-source")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._stop_lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        block_s = 0.1
        n = int(self.sample_rate * block_s)
        t0 = 0.0
        while not self._stop.is_set():
            t = t0 + np.arange(n) / self.sample_rate
            # 8-second cycle: 4s of buzzy modulated tone, 4s of room-tone noise.
            in_burst = (t0 % 8.0) < 4.0
            if in_burst:
                carrier = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t)
                envelope = 0.3 * (0.55 + 0.45 * np.sin(2 * np.pi * 3.0 * t))
                block = (carrier * envelope).astype(np.float32)
            else:
                block = (0.005 * np.random.default_rng(int(t0 * 10)).standard_normal(n)).astype(
                    np.float32
                )
            self.ring.write(block)
            t0 += block_s
            time.sleep(block_s)


class ReferenceSource:
    """M12-01c: the laptop's own playback, captured as a reference signal.

    A WASAPI loopback of the default speaker, via `soundcard` (pinned,
    Windows-only; sounddevice's bundled PortAudio exposes no loopback,
    docs/M12-PROPOSAL.md decision D1). It is resampled to the analysis rate
    into its own `RingBuffer`, beside the mic's, for the playback canceller
    (M12-02), the certification gate (M12-03) and reference-based dominance
    (M12-06). Nothing reads it yet.

    Measured on JPad, 2026-10-09 (FIELD-NOTES, evening): the loopback is
    tapped **after** Dolby Atmos and **before** the Windows volume slider.
    Silence arrives as exact zeros, not a stall. Within a take, alignment
    to the mic is steady to < 0.4 ms but can step by 15–30 ms, so
    consumers must track alignment and never assume a fixed delay.

    **Privacy.** The loopback is everything the laptop plays, calls and
    notifications included. It lives only in this in-memory ring and is
    never written to disk here. Off unless `RTR_PLAYBACK_REFERENCE_ENABLED=1`.

    **Never blocks the heartbeat** (invariant 3): capture runs on its own
    daemon thread (soundcard's COM calls stay on that thread); the engine
    only reads the ring. `stop()` is idempotent (the M8-07 swap-under-lock
    pattern) and joins with a bound, so a stalled loopback is abandoned,
    never waited on. `recorder_factory` is the test seam: it returns
    `(device_name, context_manager)`, where the manager yields an object
    with `record(numframes) -> ndarray (frames, channels)`.

    **Lost-audio estimate (M12-09).** soundcard fills true silence with
    wall-clock zeros, so the frames received should track the clock. A
    WASAPI "data discontinuity" means the capture buffer overflowed (this
    thread didn't read in time) and that audio is gone. `deficit_frames` is
    the clock-implied frames since the first block minus the frames
    received. It wobbles by a block or two with buffering, and grows by
    whatever is lost, so its growth is the dropout total. `clock` is the
    test seam.
    """

    def __init__(
        self,
        sample_rate: int,
        buffer_seconds: float,
        recorder_factory=None,
        clock=time.monotonic,
    ):
        self.sample_rate = sample_rate
        self.ring = RingBuffer(int(buffer_seconds * sample_rate))
        self.device_name = ""
        self.capture_rate = _REFERENCE_NATIVE_RATE
        self.status = "stopped"  # stopped | running | failed
        self.error: str | None = None
        self.blocks = 0
        self.last_block_at: float | None = None
        self.frames_received = 0
        self.deficit_frames = 0.0
        self._t0: float | None = None
        self._clock = clock
        self._factory = recorder_factory or _soundcard_loopback
        self._block_frames = int(round(_REFERENCE_BLOCK_S * _REFERENCE_NATIVE_RATE))
        self._resampler = Resampler(_REFERENCE_NATIVE_RATE, sample_rate)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._stop_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, timeout_s: float = 5.0) -> None:
        """Open the loopback. Raises RuntimeError if it can't open within
        `timeout_s`. Callers degrade without a reference; it is never
        required for sensing."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="reference-source"
        )
        self._thread.start()
        if not self._ready.wait(timeout_s):
            self.status, self.error = "failed", "loopback did not open in time"
            self._stop.set()
        if self.status == "failed":
            raise RuntimeError(f"playback reference unavailable: {self.error}")

    def stop(self) -> None:
        self._stop.set()
        with self._stop_lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        if self.status == "running":
            self.status = "stopped"

    def _run(self) -> None:
        try:
            name, manager = self._factory(_REFERENCE_NATIVE_RATE, _REFERENCE_CHANNELS)
            self.device_name = name
            with manager as rec:
                self.status = "running"
                self._ready.set()
                while not self._stop.is_set():
                    block = rec.record(numframes=self._block_frames)
                    mono = np.asarray(block, dtype=np.float32).mean(axis=1)
                    out = self._resampler.process(mono)
                    if out.size:
                        self.ring.write(out)
                    self.blocks += 1
                    now = self._clock()
                    self.last_block_at = now
                    frames = mono.size
                    if self._t0 is None:
                        self._t0 = now - frames / _REFERENCE_NATIVE_RATE
                    self.frames_received += frames
                    self.deficit_frames = (
                        (now - self._t0) * _REFERENCE_NATIVE_RATE - self.frames_received
                    )
        except Exception as exc:
            self.status, self.error = "failed", f"{type(exc).__name__}: {exc}"
            self._ready.set()


class CleanSource:
    """M12-02 step 4: the mic with RTR's own playback cancelled, as an audio
    source the engine can analyse instead of the raw mic.

    On its own daemon thread, it reads the mic ring and the reference ring
    (`ReferenceSource`) in 16 ms blocks, runs `aec.StreamingCanceller` (the
    same code path the offline evaluation measured), and writes a **clean
    ring**. The raw mic ring is never modified, so raw stays available and
    every frame can say which stream it analysed.

    **Alignment by time, not position.** The two streams open a few hundred
    ms apart (the reference took 109–484 ms to open in the 2026-10-09
    self-tests), so their ring positions don't line up. At start, both
    rings' `total_written` are read back to back, which fixes the
    reference position matching the mic's "now". The delay tracker then
    finds only the real playout + acoustic + buffering delay. The devices
    share one clock (measured), so this offset doesn't drift. The tracker
    re-locks through the reference slips.

    **Never a queue** (invariant 4): if processing falls more than
    `max_behind_s` behind the mic, it jumps to the newest audio, re-aligns
    and starts a fresh canceller (counted in `resyncs`). **Degrade, never
    fail** (invariant 7): on any error the status becomes "failed", and the
    engine falls back to raw. With nothing playing, the canceller passes
    the mic through bit-identical, so the clean ring equals the raw one.
    """

    def __init__(self, mic, reference, buffer_seconds: float,
                 canceller_factory=None, max_behind_s: float = 1.0, poll_s: float = 0.005,
                 clip_s: float = 0.0, clip_dir: str = "data/captures",
                 clip_after_lock_s: float = CLIP_AFTER_LOCK_S,
                 report_s: float = CANCEL_REPORT_S, clock=time.monotonic):
        from .aec import BLOCK, StreamingCanceller

        self.mic, self.reference = mic, reference
        self.sample_rate = mic.sample_rate
        self.ring = RingBuffer(int(buffer_seconds * self.sample_rate))
        # M12-06: the canceller's echo estimate (mic - clean), sample-aligned
        # with `ring`, so playback dominance is E(echo) / E(clean + echo).
        self.echo_ring = RingBuffer(int(buffer_seconds * self.sample_rate))
        self.device_name = ""
        self.capture_rate = getattr(mic, "capture_rate", mic.sample_rate)
        self.status = "stopped"  # stopped | running | failed
        self.error: str | None = None
        self.resyncs = 0
        self._factory = canceller_factory or StreamingCanceller
        self._block = BLOCK
        self._max_behind = int(max_behind_s * self.sample_rate)
        self._poll_s = poll_s
        self._canceller = None
        self._pm0 = 0  # mic position of the canceller's sample 0
        self._pm = 0  # next mic position to process
        self._offset = 0  # reference position minus mic position, same instant
        self._stop = threading.Event()
        self._stop_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # M12-09 diagnostics: interval energy sums for the report, the last
        # report, and the opt-in clip. Clean-source thread only.
        self._clock = clock
        self._report_s = report_s
        self._next_report: float | None = None
        self._acc = {"mic": 0.0, "clean": 0.0, "ref": 0.0, "n": 0}
        self._seen_relocks = 0
        self._report_canceller = None
        self._last_deficit = 0.0
        self.last_report: dict | None = None
        self._clip_s = clip_s
        self._clip_dir = clip_dir
        self._clip_after_lock_s = clip_after_lock_s
        self._clip: dict | None = None
        self.clip_path: str | None = None  # set once the clip is written

    @property
    def canceller(self):
        return self._canceller

    def start(self) -> None:
        self.device_name = f"{getattr(self.mic, 'device_name', '')} (playback cancelled)"
        self._thread = threading.Thread(target=self._run, daemon=True, name="clean-source")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._stop_lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        if self.status == "running":
            self.status = "stopped"

    def align(self) -> bool:
        """Fix the mic/reference offset from both rings' current positions and
        start a fresh canceller. False until both rings have audio."""
        mic_total = self.mic.ring.total_written
        ref_total = self.reference.ring.total_written
        if mic_total == 0 or ref_total == 0:
            return False
        self._offset = ref_total - mic_total
        self._pm0 = self._pm = mic_total
        self._canceller = self._factory()
        return True

    def windows(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        """The latest `n` samples of (clean, echo) at the same positions.
        The echo ring is written just after the clean one, so its total is the
        safe common end."""
        end = min(self.ring.total_written, self.echo_ring.total_written)
        return self.ring.read_range(end - n, n), self.echo_ring.read_range(end - n, n)

    def _ref_get(self, i: int, n: int) -> np.ndarray:
        return self.reference.ring.read_range(self._pm0 + i + self._offset, n)

    def pump(self) -> int:
        """Process every complete mic block available; returns how many. The
        thread calls this in a loop; tests call it directly."""
        avail = self.mic.ring.total_written
        if avail - self._pm > self._max_behind:
            self.resyncs += 1
            self.align()
            return 0
        done = 0
        B = self._block
        while self._pm + B <= avail and not self._stop.is_set():
            block = self.mic.ring.read_range(self._pm, B)
            clean = self._canceller.process(block, self._ref_get)
            self.ring.write(clean)
            self.echo_ring.write(block - clean)  # exactly zero on pass-through
            self._observe_block(block, clean)
            self._pm += B
            done += 1
        self._maybe_report()
        return done

    # -- M12-09 diagnostics (clean-source thread; never raise) -----------------

    def _observe_block(self, block: np.ndarray, clean: np.ndarray) -> None:
        """Accumulate the report's energy sums and feed the opt-in clip. The
        reference is read at the mic's aligned position (no lag), as
        capture_room_wav.py records it, so an offline cancel() on the clip
        sees what a capture would."""
        ref = self._ref_get(self._pm - self._pm0, len(block))
        acc = self._acc
        acc["mic"] += float(np.dot(block, block))
        acc["clean"] += float(np.dot(clean, clean))
        acc["ref"] += float(np.dot(ref, ref))
        acc["n"] += len(block)
        if self._clip_s > 0 and self.clip_path is None:
            try:
                self._clip_step(block, clean, ref)
            except Exception:
                log.exception("diagnostic clip failed; no clip this session")
                self._clip_s = 0.0

    def _maybe_report(self) -> None:
        now = self._clock()
        if self._next_report is None:
            self._next_report = now + self._report_s
            return
        if now < self._next_report:
            return
        self._next_report = now + self._report_s
        try:
            self.last_report = self._report()
            log.info("%s", _format_cancel_report(self.last_report))
            if self._clip is not None:
                self._clip["reports"].append(self.last_report)
        except Exception:
            log.exception("canceller report failed")

    def _report(self) -> dict:
        can = self._canceller
        if can is not self._report_canceller:  # fresh canceller after a resync
            self._report_canceller, self._seen_relocks = can, 0
        res = getattr(can, "result", None)
        tracker = getattr(can, "tracker", None)
        relocks = list(getattr(res, "relocks", []))
        choices = list(getattr(res, "relock_choices", []))
        new_choices = choices[self._seen_relocks :]
        self._seen_relocks = len(choices)
        acc, self._acc = self._acc, {"mic": 0.0, "clean": 0.0, "ref": 0.0, "n": 0}
        n = max(acc["n"], 1)
        reduction = (
            10 * math.log10(acc["mic"] / acc["clean"])
            if acc["mic"] > 0 and acc["clean"] > 0 else None
        )
        ref_dbfs = 10 * math.log10(acc["ref"] / n) if acc["ref"] > 0 else None
        deficit = float(getattr(self.reference, "deficit_frames", 0.0))
        lost_delta = deficit - self._last_deficit
        self._last_deficit = deficit
        return {
            "t": round(time.time(), 3),
            "locked": getattr(tracker, "lag", None) is not None,
            "lag": getattr(tracker, "lag", None),
            "coherence": round(float(getattr(tracker, "coherence", 0.0) or 0.0), 3),
            "relocks_total": len(relocks),
            "relocks_new": new_choices,
            "divergence_resets": int(getattr(res, "divergence_resets", 0)),
            "rollbacks": int(getattr(res, "rollbacks", 0)),
            "resyncs": self.resyncs,
            "reduction_db": None if reduction is None else round(reduction, 2),
            "ref_dbfs": None if ref_dbfs is None else round(ref_dbfs, 1),
            "ref_deficit_ms": round(deficit / (_REFERENCE_NATIVE_RATE / 1000), 1),
            "ref_lost_ms_new": round(lost_delta / (_REFERENCE_NATIVE_RATE / 1000), 1),
        }

    def _clip_step(self, block: np.ndarray, clean: np.ndarray, ref: np.ndarray) -> None:
        """Opt-in (RTR_DEBUG_CANCEL_CLIP_S > 0): from first lock +
        clip_after_lock_s, keep clip_s seconds of raw mic, clean and
        reference on the same positions, then write them once."""
        if self._clip is None:
            relocks = getattr(getattr(self._canceller, "result", None), "relocks", [])
            pos_s = (self._pm - self._pm0) / self.sample_rate
            if not relocks or pos_s < relocks[0] + self._clip_after_lock_s:
                return
            self._clip = {"mic": [], "clean": [], "ref": [], "n": 0,
                          "started_at": time.time(), "resyncs_at_start": self.resyncs,
                          "canceller_pos_s": round(pos_s, 3), "reports": []}
            log.info("diagnostic clip: recording %.0f s", self._clip_s)
        c = self._clip
        c["mic"].append(block.copy())
        c["clean"].append(clean.copy())
        c["ref"].append(ref.copy())
        c["n"] += len(block)
        if c["n"] >= int(self._clip_s * self.sample_rate):
            self._write_clip()

    def _write_clip(self) -> None:
        c, self._clip = self._clip, None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(c["started_at"]))
        base = Path(self._clip_dir) / f"live-clip-{stamp}"
        base.parent.mkdir(parents=True, exist_ok=True)
        for kind in ("mic", "clean", "ref"):
            _write_wav16(Path(f"{base}.{kind}.wav"), np.concatenate(c[kind]), self.sample_rate)
        sidecar = {
            "what": "M12-09 diagnostic clip (docs/M12-09-PLAN.md): raw mic, clean, "
                    "reference on the same sample positions. Founder-alone sessions only; "
                    "delete once the FIELD-NOTES entry is written.",
            "started_at": c["started_at"],
            "seconds": round(c["n"] / self.sample_rate, 3),
            "sample_rate": self.sample_rate,
            "canceller_pos_s": c["canceller_pos_s"],
            "resyncs_during": self.resyncs - c["resyncs_at_start"],
            "mic_device": getattr(self.mic, "device_name", ""),
            "reference_device": getattr(self.reference, "device_name", ""),
            "reports": c["reports"],
        }
        Path(f"{base}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        self.clip_path = str(base)
        log.info("diagnostic clip written: %s.{mic,clean,ref}.wav", base)

    def _run(self) -> None:
        try:
            while not self._stop.is_set() and not self.align():
                time.sleep(self._poll_s)
            self.status = "running"
            while not self._stop.is_set():
                if self.pump() == 0:
                    time.sleep(self._poll_s)
        except Exception as exc:
            self.status, self.error = "failed", f"{type(exc).__name__}: {exc}"


def _soundcard_loopback(rate: int, channels: int):
    """The default speaker's WASAPI loopback (soundcard, Windows)."""
    import soundcard as sc

    speaker = sc.default_speaker()
    loop = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    return loop.name, loop.recorder(samplerate=rate, channels=channels)


def _resolve_device(device: str | None):
    if device is None:
        return None
    try:
        return int(device)
    except ValueError:
        return device  # sounddevice matches name substrings


def list_input_devices() -> str:
    import sounddevice as sd

    lines = []
    default_in = sd.default.device[0]
    for idx, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            marker = "*" if idx == default_in else " "
            lines.append(
                f"{marker} [{idx:3d}] {info['name']}  "
                f"({info['max_input_channels']} ch, {info['default_samplerate']:.0f} Hz)"
            )
    return "\n".join(lines)


def _format_cancel_report(r: dict) -> str:
    """One log line per CANCEL_REPORT_S (M12-09)."""
    state = f"locked lag={r['lag']} coh={r['coherence']}" if r["locked"] else "not locked"
    relocks = f"+{len(r['relocks_new'])}"
    if r["relocks_new"]:
        relocks += f" ({','.join(r['relocks_new'])})"
    red = "n/a" if r["reduction_db"] is None else f"{r['reduction_db']:.1f} dB"
    ref = "silent" if r["ref_dbfs"] is None else f"{r['ref_dbfs']:.1f} dBFS"
    return (
        f"canceller: {state} relocks={relocks} resets={r['divergence_resets']} "
        f"resyncs={r['resyncs']} reduction={red} | reference: {ref} "
        f"lost={r['ref_lost_ms_new']:+.0f} ms (deficit {r['ref_deficit_ms']:.0f} ms)"
    )


def _write_wav16(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """16-bit PCM mono WAV (the M12-09 diagnostic clip)."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
