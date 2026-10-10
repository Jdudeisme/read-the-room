"""M12-02 canceller on synthetic echo (offline, no models, no audio files).

The synthetic room is fully linear (a known decaying impulse response), so
these tests check the machinery: alignment, convergence, re-lock after a
step, double-talk survival, and exact pass-through in silence. They do
NOT measure what JPad's real path allows: that ceiling is ~7–11 dB
(docs/M12-02-PLAN.md), and scripts/m12_aec_eval.py measures it on the
2026-10-09 captures.
"""

from __future__ import annotations

import numpy as np
import pytest

from sensing import aec

SR = aec.SR


def _music(seconds, seed=0):
    """Broadband 'music': noise shaped by a slow envelope, so the reference
    has the dynamics a real track has."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    env = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 0.7 * np.arange(n) / SR))
    return (0.1 * env * rng.standard_normal(n)).astype(np.float32)


def _room(seed=1, length_s=0.08):
    """A decaying random impulse response, 80 ms, gain ~0.3."""
    rng = np.random.default_rng(seed)
    n = int(length_s * SR)
    h = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.015 * SR))
    return (0.3 * h / np.sqrt(np.sum(h**2))).astype(np.float32)


def _echo(ref, h, lag):
    """mic[n] = (h * ref)[n - lag], i.e. the speaker plays `lag` samples
    after the loopback captured it."""
    y = np.convolve(ref, h)[: ref.size]
    out = np.zeros_like(ref)
    if lag >= 0:
        out[lag:] = y[: y.size - lag]
    else:
        out[: y.size + lag] = y[-lag:]
    return out


def _erle_db(mic, clean, a, b):
    return 10 * np.log10(np.sum(mic[a:b] ** 2) / (np.sum(clean[a:b] ** 2) + 1e-20))


def test_silence_passes_through_bit_identical():
    rng = np.random.default_rng(3)
    mic = (0.05 * rng.standard_normal(8 * SR)).astype(np.float32)
    ref = np.zeros(8 * SR, dtype=np.float32)  # loopback delivers exact zeros
    res = aec.cancel(mic, ref)
    assert np.array_equal(res.clean, mic)
    assert res.relocks == []


def test_filter_passes_through_when_its_reference_history_is_zero():
    f = aec.MdfFilter()
    d = np.random.default_rng(4).standard_normal(aec.BLOCK).astype(np.float32)
    out = f.process(d, np.zeros(aec.BLOCK, np.float32))
    assert np.array_equal(out, d)


@pytest.mark.parametrize("lag", [800, -2800])  # +50 ms; and file-relative negative
def test_tracker_finds_the_true_lag(lag):
    ref = _music(10)
    mic = _echo(ref, _room(), lag) + 0.002 * np.random.default_rng(5).standard_normal(ref.size).astype(np.float32)
    t = aec.DelayTracker()
    assert t.update(mic, ref) is False  # the first lock is confirmed, not taken
    assert t.update(mic, ref) is True
    # within 10 ms of the room response: well inside the filter's 32 ms pre-delay
    assert abs(t.lag - lag) <= int(0.010 * SR)
    assert t.coherence > 0.8


def test_converges_to_high_erle_on_linear_echo():
    # Conservative P-normalized step: ~12 dB at 6-8 s, >50 dB by 12 s on
    # purely linear echo (measured while building; the bound is loose).
    ref = _music(16)
    mic = _echo(ref, _room(), 800)
    res = aec.cancel(mic, ref)
    assert _erle_db(mic, res.clean, 12 * SR, 16 * SR) > 20.0


def test_relocks_after_an_alignment_step_and_recovers():
    # Re-lock takes ~6 s after a step (the new lag must win the newest 5 s,
    # then be confirmed twice at 2 s intervals), then ~6 s to re-converge.
    ref = _music(32, seed=7)
    h = _room()
    mic = np.concatenate([_echo(ref, h, 800)[: 12 * SR], _echo(ref, h, 800 + 400)[12 * SR :]])  # +25 ms step at 12 s
    res = aec.cancel(mic, ref)
    assert any(12.0 <= t <= 22.0 for t in res.relocks)
    assert abs(res.delays[-1][1] - 1200) <= int(0.010 * SR)
    assert _erle_db(mic, res.clean, 28 * SR, 32 * SR) > 15.0
    # The learned path survives the slip (robustness pass): the re-lock keeps
    # or shifts the filter and cancellation doesn't dip. Before that pass,
    # ERLE fell back to ~0 dB at every re-lock.
    assert res.relock_choices[-1] in ("keep", "shift")
    r = int(res.relocks[-1] * SR)
    assert _erle_db(mic, res.clean, r, r + 2 * SR) > 15.0


def test_double_talk_keeps_the_speech():
    # The first lock is confirmed at ~8 s (MIN_FIRST_LOCK_S + one 2 s
    # update), so the speech starts after the filter has settled.
    ref = _music(20, seed=8)
    echo = _echo(ref, _room(), 800)
    rng = np.random.default_rng(9)
    speech = np.zeros_like(ref)
    a, b = 12 * SR, 16 * SR
    # 'speech': louder than the echo, uncorrelated with the reference
    speech[a:b] = (0.08 * rng.standard_normal(b - a) * np.abs(np.sin(2 * np.pi * 4 * np.arange(b - a) / SR))).astype(np.float32)
    res = aec.cancel(echo + speech, ref)
    kept = np.corrcoef(res.clean[a:b], speech[a:b])[0, 1]
    assert kept > 0.95
    assert res.divergence_resets == 0
    assert _erle_db(echo, res.clean - speech, 17 * SR, 20 * SR) > 15.0  # still cancelling after


def test_divergence_guard_resets():
    f = aec.MdfFilter()
    f.W[:] = 50.0  # a filter gone wrong
    ref = _music(1)
    d = np.zeros(aec.BLOCK, np.float32)
    for i in range(aec.DIVERGE_BLOCKS + 1):
        f.process(d + 1e-3, ref[i * aec.BLOCK : (i + 1) * aec.BLOCK])
    assert f.divergence_resets >= 1


def test_first_lock_waits_for_enough_audio_and_has_no_bad_transient():
    ref = _music(14)
    mic = _echo(ref, _room(), 800)
    res = aec.cancel(mic, ref)
    first = res.relocks[0]
    assert first >= aec.MIN_FIRST_LOCK_S
    # Seeding the per-bin power from the history: no worse-than-input start.
    a = int(first * SR)
    assert _erle_db(mic, res.clean, a, a + 2 * SR) > -1.0



# -- CleanSource: the live plumbing equals the measured offline path ----------------

from sensing.audio import CleanSource, RingBuffer  # noqa: E402


class _RingOnly:
    def __init__(self, seconds):
        self.sample_rate = SR
        self.device_name = "fake"
        self.ring = RingBuffer(int(seconds * SR))


def test_clean_source_matches_offline_cancel_bit_for_bit():
    ref = _music(20, seed=11)
    mic = _echo(ref, _room(), 800) + 0.002 * np.random.default_rng(12).standard_normal(ref.size).astype(np.float32)
    m, r = _RingOnly(25), _RingOnly(25)
    cs = CleanSource(m, r, 25.0)
    chunk = 480  # 30 ms callbacks, both streams in lockstep
    m.ring.write(mic[:chunk])
    r.ring.write(ref[:chunk])
    assert cs.align()
    for i in range(chunk, mic.size - chunk + 1, chunk):
        m.ring.write(mic[i : i + chunk])
        r.ring.write(ref[i : i + chunk])
        cs.pump()
    got = cs.ring.read_range(0, cs.ring.total_written)
    want = aec.cancel(mic[chunk:], ref[chunk:]).clean[: got.size]
    assert got.size > 15 * SR
    assert np.array_equal(got, want)


def test_clean_source_passes_silence_through_and_resyncs_when_behind():
    m, r = _RingOnly(10), _RingOnly(10)
    cs = CleanSource(m, r, 10.0, max_behind_s=0.5)
    speech = (0.05 * np.random.default_rng(13).standard_normal(4 * SR)).astype(np.float32)
    m.ring.write(speech[:480])
    r.ring.write(np.zeros(480, np.float32))
    assert cs.align()
    m.ring.write(speech[480 : 2 * SR])  # 1.97 s arrive at once: > 0.5 s behind
    r.ring.write(np.zeros(2 * SR - 480, np.float32))
    assert cs.pump() == 0 and cs.resyncs == 1  # jumped to the newest audio
    m.ring.write(speech[2 * SR :])
    r.ring.write(np.zeros(2 * SR, np.float32))
    cs.pump()
    n = cs.ring.total_written
    assert np.array_equal(cs.ring.read_range(0, n), speech[2 * SR : 2 * SR + n])  # bit-identical


def test_clean_source_echo_ring_completes_the_mic():
    ref = _music(18, seed=14)
    mic = _echo(ref, _room(), 800)
    m, r = _RingOnly(20), _RingOnly(20)
    cs = CleanSource(m, r, 20.0)
    m.ring.write(mic[:480])
    r.ring.write(ref[:480])
    assert cs.align()
    for i in range(480, mic.size - 480 + 1, 480):
        m.ring.write(mic[i : i + 480])
        r.ring.write(ref[i : i + 480])
        cs.pump()
    n = cs.ring.total_written
    clean_w, echo_w = cs.windows(n)
    assert np.allclose(clean_w + echo_w, mic[480 : 480 + n], atol=1e-6)
    late = slice(n - 4 * SR, n)  # first lock ~8 s in; converged by 14 s: most of the mic is echo
    assert aec.reference_dominance((clean_w + echo_w)[late], echo_w[late]) > 0.9


def test_reference_dominance_is_zero_without_playback():
    x = np.random.default_rng(15).standard_normal(SR).astype(np.float32)
    assert aec.reference_dominance(x, np.zeros_like(x)) == 0.0
    assert aec.reference_dominance(np.zeros(SR, np.float32), np.zeros(SR, np.float32)) == 0.0
