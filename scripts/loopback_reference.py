"""Playback reference for the M12-01 probe: record what the laptop plays.

A WASAPI loopback of the default speaker, via `soundcard` (pinned in
pyproject.toml; sounddevice's bundled PortAudio exposes no loopback,
docs/M12-PROPOSAL.md decision D1). Used by `capture_room_wav.py
--reference`. It is the probe's tool, not engine code: the engine's
`ReferenceSource` (M12-01c) is a separate, reviewed change.

**Privacy.** The loopback is *everything* the laptop plays: Spotify, but
also calls, videos and notifications. This module never writes to disk;
the caller decides, and `capture_room_wav.py` writes it only under
`--reference`, which a person passes for a session they've consented to.
Turn on Do Not Disturb first.

**Threads.** soundcard talks to WASAPI over COM, so the recorder is opened
and read inside its own daemon thread. `stop()` sets an event and joins
briefly, and never waits indefinitely: if the loopback stalls (WASAPI can
deliver no packets while nothing plays), the thread is abandoned, as
workers are elsewhere in RTR.

**Clock log.** Every block's arrival time (`time.monotonic`) and the
running frame count are kept, so drift between the speaker clock and the
mic clock can be measured offline against the mic's own log.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

# The device's shared-mode rate on JPad: sounddevice lists the WASAPI speaker
# at 48000.0 Hz (2026-10-09), so asking for it avoids WASAPI resampling.
NATIVE_RATE = 48_000
CHANNELS = 2
# 100 ms blocks: the 2026-10-09 self-test returned every 100 ms request in
# at most 110 ms while music played.
BLOCK_S = 0.1


@dataclass
class LoopbackResult:
    mono: np.ndarray  # float32 at NATIVE_RATE, channels averaged
    device: str
    native_rate: int
    channels: int
    started_at: float | None  # monotonic time the first block arrived
    blocks: list[tuple[float, int]] = field(default_factory=list)  # (monotonic, frames so far)
    error: str | None = None

    @property
    def duration_s(self) -> float:
        return self.mono.size / self.native_rate

    @property
    def max_gap_s(self) -> float:
        """Longest wait between consecutive blocks: a stall indicator."""
        if len(self.blocks) < 2:
            return 0.0
        t = np.array([b[0] for b in self.blocks])
        return float(np.diff(t).max())


class LoopbackRecorder:
    def __init__(self, block_s: float = BLOCK_S):
        self._frames = int(round(block_s * NATIVE_RATE))
        self._stop = threading.Event()
        self._chunks: list[np.ndarray] = []
        self._blocks: list[tuple[float, int]] = []
        self._total = 0
        self._started_at: float | None = None
        self._device = ""
        self._error: str | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="loopback-ref")

    def start(self, timeout_s: float = 5.0) -> str:
        """Open the loopback; returns its device name. Raises RuntimeError if
        it can't be opened within `timeout_s`."""
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise RuntimeError("loopback did not open in time")
        if self._error:
            raise RuntimeError(f"loopback failed to open: {self._error}")
        return self._device

    def stop(self, join_s: float = 2.0) -> LoopbackResult:
        self._stop.set()
        self._thread.join(join_s)  # bounded: a stalled loopback is abandoned
        mono = np.concatenate(self._chunks) if self._chunks else np.empty(0, np.float32)
        return LoopbackResult(
            mono=mono, device=self._device, native_rate=NATIVE_RATE,
            channels=CHANNELS, started_at=self._started_at,
            blocks=list(self._blocks), error=self._error,
        )

    def _run(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            loop = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self._device = loop.name
            with loop.recorder(samplerate=NATIVE_RATE, channels=CHANNELS,
                               blocksize=self._frames // 10) as rec:
                self._ready.set()
                while not self._stop.is_set():
                    x = rec.record(numframes=self._frames)
                    now = time.monotonic()
                    if self._started_at is None:
                        self._started_at = now
                    self._chunks.append(x.mean(axis=1).astype(np.float32))
                    self._total += x.shape[0]
                    self._blocks.append((now, self._total))
        except Exception as exc:  # surfaced through start() / the result
            self._error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
