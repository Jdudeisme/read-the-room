"""Emotion layer: audeering wav2vec2 valence/arousal, VAD-gated.

The model (wav2vec2-large, ~1.2 GB, CPU-only here) is the expensive layer, so:

- it loads and runs on its own worker thread — a slow inference can never
  stall the DSP/VAD heartbeat;
- the engine only submits windows whose speech ratio passed the VAD gate;
- submissions use a latest-wins slot: if inference is still running when the
  next window arrives, the stale job is replaced, never queued;
- `emotion_min_interval_s` rate-limits inference independently of the hop —
  the documented fallback knob if the demo machine can't run it every 2 s.

Model outputs (arousal, dominance, valence) in [0, 1]; we publish valence and
arousal rescaled to [-1, 1] per the RoomState contract.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmotionReading:
    valence: float  # -1..1
    arousal: float  # -1..1
    confidence: float  # 0..1 (speech ratio of the analyzed window)
    at: float  # time.monotonic() when inference finished


def load_model(model_name: str, torch_threads: int = 0, os_truststore: bool = True):
    """Load processor + model. Factored out so the benchmark reuses it.

    Returns (processor, model, infer) where infer(samples: float32 @16k) ->
    (valence, arousal) in [-1, 1].
    """
    if os_truststore:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # pragma: no cover - best-effort
            log.warning("truststore injection failed; falling back to certifi")

    import torch
    import torch.nn as nn
    from transformers import Wav2Vec2Processor
    from transformers.models.wav2vec2.modeling_wav2vec2 import (
        Wav2Vec2Model,
        Wav2Vec2PreTrainedModel,
    )

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)

    # Custom head from the audeering model card: the checkpoint is a plain
    # Wav2Vec2 encoder plus a 3-output regression head (arousal, dominance,
    # valence), not a stock transformers task class.
    class RegressionHead(nn.Module):
        def __init__(self, config):
            super().__init__()
            self.dense = nn.Linear(config.hidden_size, config.hidden_size)
            self.dropout = nn.Dropout(config.final_dropout)
            self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

        def forward(self, features):
            x = self.dropout(features)
            x = torch.tanh(self.dense(x))
            x = self.dropout(x)
            return self.out_proj(x)

    class EmotionModel(Wav2Vec2PreTrainedModel):
        def __init__(self, config):
            super().__init__(config)
            self.wav2vec2 = Wav2Vec2Model(config)
            self.classifier = RegressionHead(config)
            self.init_weights()

        def forward(self, input_values):
            hidden = self.wav2vec2(input_values)[0]
            pooled = torch.mean(hidden, dim=1)
            return self.classifier(pooled)

    processor = Wav2Vec2Processor.from_pretrained(model_name)
    model = EmotionModel.from_pretrained(model_name)
    model.eval()

    def infer(samples: np.ndarray) -> tuple[float, float]:
        inputs = processor(samples, sampling_rate=16_000, return_tensors="pt")
        with torch.inference_mode():
            out = model(inputs.input_values)[0]
        arousal, _dominance, valence = (float(x) for x in out)
        # Model space is [0, 1]; RoomState contract is [-1, 1].
        return _rescale(valence), _rescale(arousal)

    return processor, model, infer


def _rescale(x: float) -> float:
    return max(-1.0, min(1.0, 2.0 * x - 1.0))


class EmotionWorker:
    """Background thread owning model load + inference. Thread-safe interface:
    submit() from the engine tick, latest()/status from anywhere."""

    def __init__(self, model_name: str, min_interval_s: float, torch_threads: int = 0,
                 os_truststore: bool = True):
        self._model_name = model_name
        self._min_interval_s = min_interval_s
        self._torch_threads = torch_threads
        self._os_truststore = os_truststore
        # job = (window, speech_ratio, reference_track_id | None). A None
        # track id is a speech job; otherwise a reference tap (M6).
        self._job: tuple[np.ndarray, float, str | None] | None = None
        self._job_event = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: EmotionReading | None = None
        # Reference-tap result slot, popped by the engine: (track_id, v, a).
        # Reference results NEVER touch _latest — they measure the record,
        # not the room.
        self._reference: tuple[str, float, float] | None = None
        self._last_infer_at = -1e9
        self.status = "loading"  # loading | ready | failed | stopped
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="emotion-worker")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._job_event.set()

    def submit(self, window: np.ndarray, speech_ratio: float, now: float) -> None:
        """Offer a speech-certified window. Dropped if rate-limited or busy
        (latest-wins replacement, never a queue). Speech always wins the
        slot — a pending reference tap is displaced without ceremony."""
        if self.status != "ready":
            return
        if now - self._last_infer_at < self._min_interval_s:
            return
        with self._lock:
            self._job = (window.copy(), speech_ratio, None)
        self._job_event.set()

    def submit_reference(
        self, window: np.ndarray, track_id: str, now: float
    ) -> None:
        """Offer a music-only playback window as a reference tap (M6): the
        model's response to it measures the playing track's pull. Same rate
        limit as speech (one model, one budget), but a reference NEVER
        displaces a pending speech job — the room outranks the record."""
        if self.status != "ready":
            return
        if now - self._last_infer_at < self._min_interval_s:
            return
        with self._lock:
            if self._job is not None and self._job[2] is None:
                return  # speech job pending; keep it
            self._job = (window.copy(), 0.0, track_id)
        self._job_event.set()

    def pop_reference(self) -> tuple[str, float, float] | None:
        """(track_id, valence, arousal) of the newest reference result,
        once; None until the next one lands."""
        with self._lock:
            ref, self._reference = self._reference, None
        return ref

    def latest(self, now: float) -> tuple[EmotionReading | None, float | None]:
        """(reading, staleness_seconds) — both None before the first result."""
        with self._lock:
            reading = self._latest
        if reading is None:
            return None, None
        return reading, max(0.0, now - reading.at)

    def _run(self) -> None:
        try:
            _, _, infer = load_model(
                self._model_name, self._torch_threads, self._os_truststore
            )
        except Exception as exc:
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("emotion model failed to load")
            return
        self.status = "ready"
        while not self._stop.is_set():
            self._job_event.wait()
            self._job_event.clear()
            if self._stop.is_set():
                break
            with self._lock:
                job, self._job = self._job, None
            if job is None:
                continue
            window, speech_ratio, reference_track_id = job
            started = time.monotonic()
            try:
                valence, arousal = infer(window)
            except Exception:
                log.exception("emotion inference failed; window skipped")
                continue
            self._last_infer_at = time.monotonic()
            with self._lock:
                if reference_track_id is not None:
                    self._reference = (reference_track_id, valence, arousal)
                else:
                    self._latest = EmotionReading(
                        valence=valence,
                        arousal=arousal,
                        confidence=min(1.0, speech_ratio),
                        at=self._last_infer_at,
                    )
            log.debug(
                "emotion inference took %.2fs%s",
                self._last_infer_at - started,
                " (reference tap)" if reference_track_id is not None else "",
            )
        self.status = "stopped"
