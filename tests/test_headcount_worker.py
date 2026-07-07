"""HeadcountWorker integration: threading, staleness, and the silence-hold
contract — with load_ecapa patched to a fake embedder so no model or network
is required."""

import time

import numpy as np
import pytest

from sensing import headcount as hc
from sensing.state import HeadcountBucket

SR = 16_000


def _fake_load_ecapa(model_name, torch_threads=0, os_truststore=True):
    """Every segment maps to (nearly) the same unit vector: one 'speaker'."""
    rng = np.random.default_rng(42)
    base = rng.standard_normal(192).astype(np.float32)
    base /= np.linalg.norm(base)

    def embed(segments):
        out = np.tile(base, (len(segments), 1))
        out += 0.005 * rng.standard_normal(out.shape).astype(np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    return embed


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(hc, "load_ecapa", _fake_load_ecapa)
    w = hc.HeadcountWorker(
        model_name="fake",
        min_interval_s=0.0,
        estimator=hc.HeadcountEstimator(),
        smoother=hc.BucketSmoother(tau_s=0.01, hold_k=1),
        sample_rate=SR,
    )
    w.start()
    deadline = time.monotonic() + 5.0
    while w.status == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert w.status == "ready"
    yield w
    w.stop()


def _speech_window(seconds: float = 5.0):
    window = np.random.default_rng(0).standard_normal(int(seconds * SR)).astype(np.float32) * 0.1
    mask = np.ones(int(seconds * SR) // hc.VAD_CHUNK, dtype=bool)
    return window, mask


def _wait_for_reading(w, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reading, staleness = w.latest(time.monotonic())
        if reading is not None:
            return reading, staleness
        time.sleep(0.02)
    raise AssertionError("no headcount reading produced in time")


class TestWorker:
    def test_no_reading_before_first_speech(self, worker):
        assert worker.latest(time.monotonic()) == (None, None)

    def test_speech_produces_solo_reading(self, worker):
        window, mask = _speech_window()
        worker.submit(window, mask, speech_ratio=0.8, loudness_dbfs=-30.0,
                      now=time.monotonic())
        reading, staleness = _wait_for_reading(worker)
        assert reading.bucket is HeadcountBucket.SOLO
        assert reading.confidence > 0.0
        assert staleness < 2.0

    def test_silence_holds_bucket_and_grows_staleness(self, worker):
        """The temporal half of the 2020 silence fix: no submissions ->
        the bucket holds and staleness climbs; nothing is manufactured."""
        window, mask = _speech_window()
        worker.submit(window, mask, 0.8, -30.0, now=time.monotonic())
        first, s0 = _wait_for_reading(worker)
        time.sleep(0.3)  # silence: nothing submitted
        second, s1 = worker.latest(time.monotonic())
        assert second == first  # identical reading object — held, not recomputed
        assert s1 > s0

    def test_reading_carries_observability_fields(self, worker):
        """M4 deliverable 3: the reading exposes the estimator's raw smear
        signals plus the smoother's EMA value, for the bridge to attach to
        dashboard frames. The fake embedder is one tight 'speaker', so
        dispersion is tiny, nothing fragments, and the smoothed log2 sits
        near 0 (solo)."""
        window, mask = _speech_window()
        worker.submit(window, mask, 0.8, -30.0, now=time.monotonic())
        reading, _ = _wait_for_reading(worker)
        assert reading.dispersion < 0.1
        assert reading.fragmentation == 0.0
        assert reading.smoothed_log2 == pytest.approx(0.0, abs=0.5)

    def test_all_silence_mask_never_produces_a_reading(self, worker):
        """Even if a window is submitted, an all-False mask (no certified
        speech) must yield no segments, no embeddings, and no reading."""
        window, _ = _speech_window()
        silent_mask = np.zeros(window.size // hc.VAD_CHUNK, dtype=bool)
        worker.submit(window, silent_mask, 0.0, -70.0, now=time.monotonic())
        time.sleep(0.5)
        assert worker.latest(time.monotonic()) == (None, None)
