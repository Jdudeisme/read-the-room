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

import numpy as np
import pytest

from sensing.audio import Resampler

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
