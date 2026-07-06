"""VAD gate: streaming Silero VAD producing a per-window speech ratio.

Runs continuously on new audio (not per-window re-runs), keeping Silero's
internal recurrent state intact, and maintains a rolling history of per-chunk
speech probabilities. The speech ratio for any window is then just the
fraction of recent chunks above threshold.

This gate is what structurally prevents the phantom-speaker failure: the
emotion layer never sees a window the VAD didn't certify as containing speech.
"""

from __future__ import annotations

from collections import deque

import numpy as np

_CHUNK = 512  # Silero's required chunk size at 16 kHz


class VadGate:
    def __init__(self, sample_rate: int, window_s: float, threshold: float):
        if sample_rate != 16_000:
            raise ValueError("Silero VAD requires 16 kHz input")
        self.sample_rate = sample_rate
        self.threshold = threshold
        self._max_chunks = max(1, int(window_s * sample_rate / _CHUNK))
        self._probs: deque[float] = deque(maxlen=self._max_chunks)
        self._pending = np.empty(0, dtype=np.float32)
        self._model = None

    def load(self) -> None:
        """Load the Silero model (a few MB; quick). Call before first feed()."""
        import torch
        from silero_vad import load_silero_vad

        self._model = load_silero_vad()
        self._torch = torch

    @property
    def ready(self) -> bool:
        return self._model is not None

    def feed(self, samples: np.ndarray) -> None:
        """Consume newly captured samples; updates the rolling chunk history."""
        if self._model is None or samples.size == 0:
            return
        data = np.concatenate((self._pending, samples))
        n_chunks = data.size // _CHUNK
        for i in range(n_chunks):
            chunk = data[i * _CHUNK : (i + 1) * _CHUNK]
            tensor = self._torch.from_numpy(np.ascontiguousarray(chunk))
            with self._torch.inference_mode():
                prob = float(self._model(tensor, self.sample_rate).item())
            self._probs.append(prob)
        self._pending = data[n_chunks * _CHUNK :]

    def speech_ratio(self, threshold: float | None = None) -> float:
        """Fraction of the rolling window's chunks judged to be speech.

        `threshold` overrides the configured cutoff for this read; raw
        per-chunk probabilities are stored, so certification strictness is a
        read-time decision. The M4 contamination gate uses this: while
        playback is active the engine certifies with a stricter threshold.
        """
        if not self._probs:
            return 0.0
        thr = self.threshold if threshold is None else threshold
        hits = sum(1 for p in self._probs if p >= thr)
        return hits / len(self._probs)

    def speech_mask(self, threshold: float | None = None) -> np.ndarray:
        """Per-chunk speech decisions for the rolling window, oldest first.

        One boolean per 512-sample chunk, aligned to the end of the current
        analysis window. This is the single certification point both the
        emotion and headcount layers gate on — downstream layers must never
        run their own VAD (M2 spec; the M4 playback-aware threshold lands
        here so every layer inherits it at once, and a future music-detection
        gate slots in the same way).
        """
        thr = self.threshold if threshold is None else threshold
        return np.fromiter(
            (p >= thr for p in self._probs),
            dtype=bool,
            count=len(self._probs),
        )
