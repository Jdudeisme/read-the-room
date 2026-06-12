"""Audio sources: microphone capture (sounddevice/PortAudio) and a synthetic
source for plumbing tests. Both feed a thread-safe ring buffer at 16 kHz mono.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np

# Rates to try if the device won't open at the analysis rate directly.
_FALLBACK_RATES = (48_000, 44_100)


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
        self._frac = 0.0  # fractional source-sample offset into the next block

    def process(self, block: np.ndarray) -> np.ndarray:
        signal = np.concatenate((self._carry, block.astype(np.float32, copy=False)))
        filtered = np.convolve(signal, self._kernel, mode="valid")
        self._carry = signal[-(self._TAPS - 1) :]
        if filtered.size == 0:
            return np.empty(0, dtype=np.float32)
        positions = np.arange(self._frac, filtered.size - 1, self.ratio)
        if positions.size == 0:
            self._frac -= filtered.size  # consumed without producing output
            self._frac = max(self._frac, 0.0)
            return np.empty(0, dtype=np.float32)
        out = np.interp(positions, np.arange(filtered.size), filtered)
        self._frac = positions[-1] + self.ratio - filtered.size
        return out.astype(np.float32)


class MicSource:
    """Live microphone capture into a ring buffer at the analysis sample rate."""

    def __init__(self, sample_rate: int, buffer_seconds: float, device: str | None = None):
        self.sample_rate = sample_rate
        self.ring = RingBuffer(int(buffer_seconds * sample_rate))
        self._device = _resolve_device(device)
        self._stream = None
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
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

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

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="synth-source")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

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
