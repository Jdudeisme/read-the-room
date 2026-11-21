"""M8-04: Resampler tests — the capture-rate fallback is demo-critical.

JPad's built-in array opens at 16 kHz and never resamples. The XVF3800 on
WASAPI opens at 48 kHz (FIELD-NOTES 2026-09-23), and M11's venue mics may
too, so this path runs live whenever a mic refuses 16 kHz.

The fractional-position edge (AUDIT finding 8c): before M8-04, `_frac`
went down to -1 at about a third of block boundaries and `np.interp`
clamped those reads. The output rate was unaffected, but samples near
boundaries were wrong by up to 0.06 on a 0.5-amplitude sine at 44.1 kHz
(and at 48 kHz with random block sizes). The fix keeps the invariant
0 <= _frac < ratio; `test_frac_stays_in_range` asserts it and
`test_random_blocks_match_one_call` is the regression test. The tests
also found an empty block emitting a spurious sample (np.convolve "valid"
swaps arguments when the signal is shorter than the kernel); fixed too.

Comparisons skip the first 64 output samples: the FIR's group delay is
(TAPS - 1) / 2 = 31 source samples, and the filter starts from zeros.
"""

from __future__ import annotations

import threading
import time
from unittest import mock

import numpy as np
import pytest

from sensing.audio import MicSource, Resampler, SynthSource

TARGET = 16_000
STEADY = 64  # output samples past the FIR warm-up
TOLERANCE = 1e-5  # float32 interpolation noise is ~3e-8; the old bug was 0.06


def _sine(rate: int, seconds: float = 10.0, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _in_blocks(rate: int, x: np.ndarray, sizes) -> tuple[np.ndarray, list[float]]:
    r = Resampler(rate, TARGET)
    outs, fracs, i = [], [], 0
    for n in sizes:
        if i >= x.size:
            break
        outs.append(r.process(x[i : i + int(n)]))
        fracs.append(r._frac)
        i += int(n)
    assert i >= x.size, "not enough block sizes to cover the signal"
    return np.concatenate(outs), fracs


def _steady_diff(a: np.ndarray, b: np.ndarray) -> float:
    n = min(a.size, b.size)
    return float(np.abs(a[STEADY:n] - b[STEADY:n]).max())


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_93_sample_blocks_match_one_call(rate):
    x = _sine(rate)
    one = Resampler(rate, TARGET).process(x)
    blocks, _ = _in_blocks(rate, x, [93] * (x.size // 93 + 1))
    assert abs(blocks.size - one.size) <= 2
    assert _steady_diff(one, blocks) < TOLERANCE


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_random_blocks_match_one_call(rate):
    x = _sine(rate)
    one = Resampler(rate, TARGET).process(x)
    sizes = np.random.default_rng(20261009).integers(1, 700, x.size)
    blocks, _ = _in_blocks(rate, x, sizes)
    assert abs(blocks.size - one.size) <= 2
    assert _steady_diff(one, blocks) < TOLERANCE


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_output_rate_within_a_tenth_of_a_percent(rate):
    seconds = 10.0
    out = Resampler(rate, TARGET).process(_sine(rate, seconds))
    assert abs(out.size - seconds * TARGET) <= 0.001 * seconds * TARGET


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_frac_stays_in_range(rate):
    """0 <= _frac < ratio across 1000 random-sized blocks, including blocks
    too small to produce any output."""
    rng = np.random.default_rng(8)
    sizes = rng.integers(1, 400, 1000)
    sizes[::7] = rng.integers(1, 3, sizes[::7].size)  # 1-2 sample blocks
    x = _sine(rate, seconds=float(sizes.sum()) / rate + 0.01)[: int(sizes.sum())]
    r = Resampler(rate, TARGET)
    _, fracs = _in_blocks(rate, x, sizes)
    assert len(fracs) == 1000
    assert min(fracs) >= 0.0
    assert max(fracs) < r.ratio


def test_passband_tone_keeps_its_amplitude():
    out = Resampler(48_000, TARGET).process(_sine(48_000))
    rms = float(np.sqrt(np.mean(out[STEADY:] ** 2)))
    assert rms == pytest.approx(0.5 / np.sqrt(2), rel=0.02)


def test_empty_block_is_a_no_op():
    r = Resampler(48_000, TARGET)
    r.process(_sine(48_000, seconds=0.01))
    before = r._frac
    assert r.process(np.empty(0, dtype=np.float32)).size == 0
    assert r._frac == before


# -- M8-07: idempotent source shutdown ---------------------------------------------


def test_mic_double_stop_releases_the_stream_once():
    mic = MicSource(16_000, 1.0)  # no device opened; the stream is a Mock
    stream = mic._stream = mock.Mock()
    mic.stop()
    mic.stop()
    assert stream.stop.call_count == 1
    assert stream.close.call_count == 1


def test_mic_concurrent_stop_releases_the_stream_at_most_once():
    for _ in range(20):
        mic = MicSource(16_000, 1.0)
        stream = mic._stream = mock.Mock()
        # PortAudio's stop() takes real time; hold the check-then-act
        # window open so the pre-M8-07 race actually shows.
        stream.stop.side_effect = lambda: time.sleep(0.01)
        barrier = threading.Barrier(2)

        def stop():
            barrier.wait()
            mic.stop()

        threads = [threading.Thread(target=stop) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert stream.stop.call_count == 1
        assert stream.close.call_count == 1


def test_synth_double_stop_is_safe():
    synth = SynthSource(16_000, 1.0)
    synth.start()
    synth.stop()
    synth.stop()
    assert synth._thread is None


# -- M12-01c: ReferenceSource (fake recorder; no WASAPI) -----------------------

from sensing.audio import ReferenceSource  # noqa: E402


class _FakeLoopback:
    """Context manager standing in for soundcard's recorder: a 1 kHz sine,
    stereo, at 48 kHz, delivered in real time."""

    def __init__(self, fail_on_enter=False):
        self.fail_on_enter = fail_on_enter
        self.t = 0

    def __enter__(self):
        if self.fail_on_enter:
            raise OSError("device busy")
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        n = np.arange(self.t, self.t + numframes)
        self.t += numframes
        time.sleep(numframes / 48_000)
        x = (0.25 * np.sin(2 * np.pi * 1000 * n / 48_000)).astype(np.float32)
        return np.stack([x, x], axis=1)


def _factory(fail=False):
    return lambda rate, channels: ("Speakers (fake)", _FakeLoopback(fail_on_enter=fail))


def test_reference_fills_its_ring_at_the_analysis_rate():
    ref = ReferenceSource(16_000, 2.0, recorder_factory=_factory())
    ref.start()
    time.sleep(0.3)
    ref.stop()
    written = ref.ring.total_written
    assert ref.device_name == "Speakers (fake)" and ref.blocks > 0
    # ~16 kHz worth of samples for the blocks delivered (48k -> 16k)
    assert abs(written - ref.blocks * 960 / 3) <= 2
    tail = ref.ring.read_last(1600)
    assert np.sqrt(np.mean(tail**2)) == pytest.approx(0.25 / np.sqrt(2), rel=0.05)
    assert ref.status == "stopped"


def test_reference_open_failure_raises_and_reports():
    ref = ReferenceSource(16_000, 2.0, recorder_factory=_factory(fail=True))
    with pytest.raises(RuntimeError, match="device busy"):
        ref.start()
    assert ref.status == "failed"
    ref.stop()  # still safe


def test_reference_concurrent_stop_is_safe():
    ref = ReferenceSource(16_000, 2.0, recorder_factory=_factory())
    ref.start()
    barrier = threading.Barrier(2)

    def stop():
        barrier.wait()
        ref.stop()

    threads = [threading.Thread(target=stop) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert ref._thread is None and ref.status == "stopped"


# -- RingBuffer.read_range (M12-02) --------------------------------------------

from sensing.audio import RingBuffer  # noqa: E402


def test_read_range_by_absolute_position_with_wrap_and_zero_fill():
    rb = RingBuffer(10)
    rb.write(np.arange(25, dtype=np.float32))  # holds 15..24 after wrapping
    assert rb.read_range(18, 4).tolist() == [18, 19, 20, 21]
    assert rb.read_range(13, 4).tolist() == [0, 0, 15, 16]  # 13, 14 overwritten
    assert rb.read_range(23, 4).tolist() == [23, 24, 0, 0]  # 25, 26 not yet written
    assert rb.read_range(-2, 3).tolist() == [0, 0, 0]
