"""Playback cancellation (M12-02): subtract RTR's own music from the mic.

RTR plays music into the room it measures. `ReferenceSource` (M12-01c)
captures exactly what the speakers are fed, so the music's part of the mic
signal can be *estimated and subtracted* instead of guessed at. This module
is that estimator. It is pure numpy: no model, no I/O, no threads. It is
built and measured offline first (docs/M12-02-PLAN.md, approved
2026-10-09). Nothing in the engine uses it yet; that switch is a separate,
reviewed step.

**What the 2026-10-09 JPad measurements decided** (FIELD-NOTES, evening;
the plan's table):

- The reference is post-Atmos and pre-volume, so the filter learns
  speaker + room + the unknown volume gain, and re-learns when the volume
  moves.
- Mic and speaker share one clock, so there is no drift compensation. But
  the bulk alignment **steps** by 15–30 ms in some takes, and a single
  GCC-PHAT peak can lock onto a **false** lag on rhythmic music (T1: −175
  vs a true −110 ms). So `DelayTracker` scores candidate lags by
  coherence over seconds, and re-locks only on a clear, repeated win.
- The linear ceiling is **~7–11 dB** ERLE on this path (coherence
  0.66–0.83). The canceller narrows the problem; the residual is M12-03's.

**Algorithm.** A partitioned-block frequency-domain NLMS (the "MDF"
family, as in Speex): overlap-save, block `BLOCK` samples, `PARTITIONS`
blocks of filter span, gradient-constrained. Adaptation is **coherence
controlled** per frequency bin: a bin adapts at full rate while the mic
is coherent with the reference (the echo dominates), and barely at all
while it isn't (people talking over the music, "double talk", where
cancellers classically diverge). A divergence guard resets the filter if
its output grows louder than its input.

**Silence passes through bit-identical.** With an all-zero reference
history (nothing playing; the loopback delivers exact zeros), the echo
estimate is exactly zero and the output *is* the input. That keeps
"shadow is first-class" true at the sample level (tests pin it).

Constants below are first-build choices, each with its reason. The
M12-02 evaluation (`scripts/m12_aec_eval.py`) measures them. They are
not calibration values yet, and change only with that evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 16_000
# 16 ms blocks: one Silero chunk is 32 ms, so cancellation adds at most half
# a VAD chunk of latency.
BLOCK = 256
# Filter span 13 x 16 ms = 208 ms. A small room's reverberant tail is
# ~0.2-0.4 s; the evaluation's fitted-impulse-response "echo tail" is the
# measurement that confirms or moves this.
PARTITIONS = 13
# NLMS step (normalized, 0 < MU <= 1). 0.5 is a conventional fast-but-
# stable choice for partitioned FDAF; the evaluation measures convergence.
MU = 0.5
# Per-bin power smoothing per block (16 ms): tau = -16 ms / ln(0.9), about
# 150 ms.
POWER_SMOOTH = 0.9
# Regularization floor on the per-bin reference power, relative to its mean:
# keeps near-empty bins from taking huge steps.
REG_FRACTION = 1e-3
# Divergence guard: reset when output power exceeds input power by this
# ratio, sustained over DIVERGE_BLOCKS (~0.5 s).
DIVERGE_RATIO = 2.0
DIVERGE_BLOCKS = 32

# Delay tracking. Search +/-600 ms: the 2026-10-09 file-relative lags spanned
# -74..-217 ms. In live use the true acoustic lag is tens of ms.
MAX_LAG_S = 0.6
TRACK_WINDOW_S = 10.0  # GCC candidates come from the last 10 s
# Candidates are scored by coherence over only the last 5 s: half the FFT
# work, and a step is won by the new lag in about half the time.
SCORE_WINDOW_S = 5.0
TRACK_EVERY_S = 2.0  # one estimate per engine hop
GCC_CANDIDATES = 5  # top GCC-PHAT peaks re-scored by coherence
# Re-lock only when a different lag beats the current one by this much
# coherence on two consecutive estimates (hysteresis against false peaks).
RELOCK_MARGIN = 0.1
RELOCK_CONFIRMATIONS = 2
# Below this median coherence, the "lag" is noise: hold, don't lock.
MIN_LOCK_COHERENCE = 0.3
# The first lock waits for this much audio and is confirmed like a re-lock.
# On 2026-10-09, T1-MO32 locked at 2 s on its quiet intro onto a false
# -173 ms (true -109 ms) and needed ~36 s and three re-locks to recover.
MIN_FIRST_LOCK_S = 6.0
# Robustness pass (2026-10-09, same evening): the 15-30 ms alignment steps
# are the reference stream slipping. The room's echo path is unchanged, so
# on a re-lock the filter is re-aligned and KEPT if it was helping (recent
# ERLE above this), and reset only if it wasn't (e.g. correcting a bad
# lock). A per-window GCC check confirmed T3-MX66's six re-locks were real
# slips (-186 -> -119 ms), not flapping.
KEEP_FILTER_MIN_ERLE_DB = 1.0
# Recent-ERLE smoothing per block (~0.3 s), and the last-good snapshot the
# divergence guard rolls back to (taken at most every SNAPSHOT_BLOCKS, ~1 s,
# while recent ERLE is above KEEP_FILTER_MIN_ERLE_DB).
ERLE_SMOOTH = 0.95
SNAPSHOT_BLOCKS = 64
# At a re-lock, candidate filters are scored on this much recent audio
# (32 blocks = 0.5 s).
RELOCK_TEST_BLOCKS = 32
# The tracked lag lands within a few ms of the impulse response's main
# energy, not exactly on its first tap: PHAT doesn't pick the largest tap,
# and coherence barely changes over a few ms. Early reflections and the
# speaker's own response can also sit just before it. A causal filter can't
# model taps ahead of its window, so the window starts this far before the
# tracked lag: 2 blocks (32 ms) of the 208 ms span.
PRE_DELAY = 2 * BLOCK
_COH_NPERSEG = 1024
_COH_BAND = (200.0, 4000.0)


# -- delay estimation ------------------------------------------------------------


def coherence(x: np.ndarray, y: np.ndarray, nperseg: int = _COH_NPERSEG) -> float:
    """Median magnitude-squared coherence of x and y over `_COH_BAND`
    (Welch, Hann, 50 % overlap). 1 = y is a linear function of x."""
    n = min(x.size, y.size)
    if n < 2 * nperseg:
        return 0.0
    win = np.hanning(nperseg)
    step = nperseg // 2
    starts = np.arange(0, n - nperseg + 1, step)
    idx = starts[:, None] + np.arange(nperseg)[None, :]
    X = np.fft.rfft(win * x[idx], axis=1)  # one batched FFT per signal
    Y = np.fft.rfft(win * y[idx], axis=1)
    sxy = np.mean(X * np.conj(Y), axis=0)
    sxx = np.mean(np.abs(X) ** 2, axis=0)
    syy = np.mean(np.abs(Y) ** 2, axis=0)
    c = np.abs(sxy) ** 2 / (sxx * syy + 1e-30)
    f = np.fft.rfftfreq(nperseg, 1.0 / SR)
    band = (f >= _COH_BAND[0]) & (f < _COH_BAND[1])
    return float(np.median(c[band]))


def gcc_candidates(mic: np.ndarray, ref: np.ndarray, max_lag: int, k: int) -> list[int]:
    """The `k` strongest GCC-PHAT peaks, as lags L with mic[n] ~ ref[n - L].
    Peaks within 2 ms of a stronger one are skipped (same peak)."""
    n = 1 << int(np.ceil(np.log2(mic.size + ref.size)))
    cross = np.fft.rfft(mic, n) * np.conj(np.fft.rfft(ref, n))
    cross /= np.abs(cross) + 1e-12
    cc = np.fft.irfft(cross, n)
    cc = np.abs(np.concatenate((cc[-max_lag:], cc[: max_lag + 1])))
    lags = np.arange(-max_lag, max_lag + 1)
    out: list[int] = []
    sep = int(0.002 * SR)
    for i in np.argsort(-cc):
        if all(abs(lags[i] - j) > sep for j in out):
            out.append(int(lags[i]))
        if len(out) == k:
            break
    return out


def score_lag(mic: np.ndarray, ref: np.ndarray, lag: int) -> float:
    """Coherence of mic with ref shifted by `lag` (mic[n] ~ ref[n - lag]),
    over the overlap."""
    if lag >= 0:
        m, r = mic[lag:], ref[: ref.size - lag]
    else:
        m, r = mic[: mic.size + lag], ref[-lag:]
    n = min(m.size, r.size)
    return coherence(r[:n], m[:n])


@dataclass
class DelayTracker:
    """Bulk mic-vs-reference delay, re-estimated every TRACK_EVERY_S over the
    last TRACK_WINDOW_S. Candidates come from GCC-PHAT and are scored by
    coherence, so a beat-period false peak loses to the true lag. A new lag
    replaces the current one only after RELOCK_CONFIRMATIONS consecutive
    clear wins."""

    lag: int | None = None
    coherence: float = 0.0
    _pending: int | None = None
    _pending_count: int = 0

    def update(self, mic_window: np.ndarray, ref_window: np.ndarray) -> bool:
        """Returns True if the lag changed (the filter must re-converge)."""
        if not np.any(ref_window):
            return False  # nothing playing: hold
        max_lag = int(MAX_LAG_S * SR)
        tail = int(SCORE_WINDOW_S * SR)
        m_tail, r_tail = mic_window[-tail:], ref_window[-tail:]
        # Candidates from the whole window (stable) and from its newest part
        # (a fresh step shows there first).
        cands = list(dict.fromkeys(
            gcc_candidates(mic_window, ref_window, max_lag, GCC_CANDIDATES)
            + gcc_candidates(m_tail, r_tail, max_lag, GCC_CANDIDATES)
        ))
        scored = sorted(((score_lag(m_tail, r_tail, c), c) for c in cands), reverse=True)
        best_coh, best = scored[0]
        if self.lag is not None:
            self.coherence = score_lag(m_tail, r_tail, self.lag)
        if best_coh < MIN_LOCK_COHERENCE:
            return False
        if self.lag is None:
            if mic_window.size < int(MIN_FIRST_LOCK_S * SR):
                return False
            if self._pending is not None and abs(best - self._pending) <= int(0.002 * SR):
                self.lag, self.coherence = best, best_coh
                self._pending, self._pending_count = None, 0
                return True
            self._pending, self._pending_count = best, 1
            return False
        if abs(best - self.lag) <= int(0.002 * SR) or best_coh < self.coherence + RELOCK_MARGIN:
            self._pending, self._pending_count = None, 0
            return False
        if self._pending is not None and abs(best - self._pending) <= int(0.002 * SR):
            self._pending_count += 1
        else:
            self._pending, self._pending_count = best, 1
        if self._pending_count >= RELOCK_CONFIRMATIONS:
            self.lag, self.coherence = best, best_coh
            self._pending, self._pending_count = None, 0
            return True
        return False


# -- the adaptive filter ---------------------------------------------------------


class MdfFilter:
    """Partitioned-block frequency-domain NLMS with per-bin coherence-
    controlled step and a divergence guard. Feed aligned blocks: `ref_block`
    is the reference already shifted by the bulk delay."""

    def __init__(self, block: int = BLOCK, partitions: int = PARTITIONS, mu: float = MU):
        self.B, self.P, self.mu = block, partitions, mu
        self.bins = block + 1
        self.reset()

    def reset(self) -> None:
        """Forget the learned path (after a delay re-lock)."""
        self.W = np.zeros((self.P, self.bins), dtype=np.complex128)
        self.X = np.zeros((self.P, self.bins), dtype=np.complex128)
        self._prev_ref = np.zeros(self.B)
        self._pxx = np.zeros(self.bins)
        self._sdd = np.zeros(self.bins)
        self._syy = np.zeros(self.bins)
        self._sdy = np.zeros(self.bins, dtype=np.complex128)
        self._sdx = np.zeros(self.bins, dtype=np.complex128)
        self._diverging = 0
        self.divergence_resets = getattr(self, "divergence_resets", 0)
        self.rollbacks = getattr(self, "rollbacks", 0)
        self._pd = 0.0
        self._pe = 0.0
        self._good: np.ndarray | None = None
        self._good_peak: int | None = None  # echo-peak tap of the last good filter
        self._since_snapshot = 0

    @property
    def recent_erle_db(self) -> float:
        if self._pd <= 0 or self._pe <= 0:
            return 0.0
        return float(10 * np.log10(self._pd / self._pe))

    def _peak(self) -> int:
        """Tap index of the learned echo's main energy (32-tap smoothed)."""
        ir = self.impulse_response()
        env = np.convolve(ir**2, np.ones(32), mode="same")
        return int(np.argmax(env))

    def _shift(self, taps: int) -> None:
        """Move the learned impulse response by `taps` (zero-filled)."""
        ir = self.impulse_response()
        out = np.zeros_like(ir)
        if taps >= 0:
            out[taps:] = ir[: ir.size - taps]
        else:
            out[: ir.size + taps] = ir[-taps:]
        B = self.B
        self.W = np.array([
            np.fft.rfft(np.concatenate((out[p * B : (p + 1) * B], np.zeros(B))))
            for p in range(self.P)
        ])

    def realign(self, ref_blocks: list[np.ndarray], keep: bool) -> None:
        """After a re-lock: rebuild the reference history from blocks taken
        at the new alignment (oldest first), keeping the learned path if
        `keep`, else starting over.

        The caller decides what to keep (see `cancel`): a step smaller than
        the filter span is partly absorbed by adaptation before the tracker
        re-locks, so neither "keep" nor "shift" is right a priori."""
        if not keep:
            self.reset()
        X = np.zeros_like(self.X)
        prev = np.zeros(self.B)
        spectra = []
        for blk in ref_blocks:
            spectra.append(np.fft.rfft(np.concatenate((prev, blk))))
            prev = np.asarray(blk, dtype=np.float64)
        for i, spec in enumerate(reversed(spectra[-self.P :])):
            X[i] = spec
        self.X = X
        self._prev_ref = prev
        # Seed the per-bin power from the history: starting from zero makes the
        # first normalized steps huge (measured: -12 dB ERLE right after lock).
        self._pxx = np.mean(np.abs(X) ** 2, axis=0)

    def predict_erle_db(self, W: np.ndarray, ref_blocks: list[np.ndarray], mic_blocks: list[np.ndarray]) -> float:
        """ERLE that filter `W` would have achieved, without adapting, on
        `mic_blocks` given aligned `ref_blocks` (PARTITIONS earlier blocks,
        then one per mic block)."""
        B, P = self.B, self.P
        prev = np.zeros(B)
        spectra = []
        for blk in ref_blocks:
            spectra.append(np.fft.rfft(np.concatenate((prev, blk))))
            prev = np.asarray(blk, dtype=np.float64)
        num = den = 0.0
        for i, d in enumerate(mic_blocks):
            j = P + i  # spectra index of the block aligned with this mic block
            X = np.array(spectra[j - P + 1 : j + 1][::-1])
            y = np.fft.irfft(np.sum(W * X, axis=0))[B:]
            d = np.asarray(d, dtype=np.float64)
            num += float(np.sum(d**2))
            den += float(np.sum((d - y) ** 2))
        return float(10 * np.log10((num + 1e-20) / (den + 1e-20)))

    def impulse_response(self) -> np.ndarray:
        """The filter as one time-domain impulse response, P*B taps."""
        return np.concatenate([np.fft.irfft(w)[: self.B] for w in self.W])

    def process(self, mic_block: np.ndarray, ref_block: np.ndarray) -> np.ndarray:
        B = self.B
        x2 = np.concatenate((self._prev_ref, ref_block))
        self._prev_ref = ref_block.astype(np.float64, copy=True)
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = np.fft.rfft(x2)
        if not np.any(self.X):
            return mic_block.copy()  # no playback in the filter span: exact pass-through
        Y = np.sum(self.W * self.X, axis=0)
        y = np.fft.irfft(Y)[B:]
        d = mic_block.astype(np.float64)
        e = d - y
        # Per-bin statistics for the step size.
        D = np.fft.rfft(np.concatenate((np.zeros(B), d)))
        Yb = np.fft.rfft(np.concatenate((np.zeros(B), y)))
        a = POWER_SMOOTH
        self._pxx = a * self._pxx + (1 - a) * np.abs(self.X[0]) ** 2
        self._sdd = a * self._sdd + (1 - a) * np.abs(D) ** 2
        self._syy = a * self._syy + (1 - a) * np.abs(Yb) ** 2
        self._sdy = a * self._sdy + (1 - a) * D * np.conj(Yb)
        self._sdx = a * self._sdx + (1 - a) * D * np.conj(self.X[0])
        coh_dy = np.abs(self._sdy) ** 2 / (self._sdd * self._syy + 1e-30)
        coh_dx = np.abs(self._sdx) ** 2 / (self._sdd * self._pxx + 1e-30)
        # Before the filter has learned anything, mic-vs-reference coherence
        # is the only echo evidence; afterwards, mic-vs-echo-estimate is.
        rate = np.clip(np.maximum(coh_dy, coh_dx), 0.0, 1.0)
        E = np.fft.rfft(np.concatenate((np.zeros(B), e)))
        reg = REG_FRACTION * (np.mean(self._pxx) + 1e-12)
        step = self.mu * rate / (self._pxx * self.P + reg)
        G = np.conj(self.X) * (step * E)
        # Gradient constraint (linear, not circular, convolution).
        g = np.fft.irfft(G, axis=1)
        g[:, B:] = 0.0
        self.W += np.fft.rfft(g, axis=1)
        # Recent ERLE and the last-good snapshot.
        b = ERLE_SMOOTH
        self._pd = b * self._pd + (1 - b) * float(np.mean(d**2))
        self._pe = b * self._pe + (1 - b) * float(np.mean(e**2))
        self._since_snapshot += 1
        if self._since_snapshot >= SNAPSHOT_BLOCKS and self.recent_erle_db > KEEP_FILTER_MIN_ERLE_DB:
            self._good = self.W.copy()
            self._good_peak = self._peak()
            self._since_snapshot = 0
        # Divergence guard: roll back to the last good filter if there is one.
        if np.mean(e**2) > DIVERGE_RATIO * np.mean(d**2) + 1e-20:
            self._diverging += 1
            if self._diverging >= DIVERGE_BLOCKS:
                good = self._good
                self._diverging = 0
                if good is not None:
                    self.W = good.copy()
                    self.rollbacks += 1
                else:
                    self.reset()
                    self.divergence_resets += 1
                return mic_block.copy()
        else:
            self._diverging = 0
        return e.astype(mic_block.dtype, copy=False)


# -- offline whole-signal cancellation -------------------------------------------


@dataclass
class CancelResult:
    clean: np.ndarray
    delays: list[tuple[float, int | None, float]] = field(default_factory=list)  # (t_s, lag, coh)
    relocks: list[float] = field(default_factory=list)
    divergence_resets: int = 0
    rollbacks: int = 0  # divergences recovered from a last-good snapshot
    relock_choices: list[str] = field(default_factory=list)  # keep / shift / reset per re-lock
    # The converged filter as a time-domain impulse response (None if never
    # locked), for measuring the echo tail.
    impulse_response: np.ndarray | None = None


def _ref_block(ref_get, start: int, lag: int) -> np.ndarray:
    """The reference block aligned to mic[start:start+BLOCK]: zeros where the
    reference has no audio (before it starts, after it ends, or not yet
    arrived)."""
    return ref_get(start - (lag - PRE_DELAY), BLOCK)


def _choose_on_relock(filt, mic_get, ref_get, start, old_lag, new_lag) -> str:
    """At a re-lock, test what to do with the learned filter on the last
    RELOCK_TEST_BLOCKS of real audio, under the NEW alignment, and pick
    whichever would have cancelled best: keep it, shift it by the lag
    change, or start over. A step smaller than the span is partly absorbed
    before the re-lock, so neither keep nor shift is right a priori (the
    re-centring heuristic tried first failed on synthetic steps)."""
    if old_lag is None or not np.any(filt.W):
        return "reset"
    n = RELOCK_TEST_BLOCKS
    first = start - n * BLOCK
    if first - PARTITIONS * BLOCK < 0:
        return "reset"
    refs = [_ref_block(ref_get, first - k * BLOCK, new_lag) for k in range(PARTITIONS, 0, -1)]
    refs += [_ref_block(ref_get, first + i * BLOCK, new_lag) for i in range(n)]
    mics = [mic_get(first + i * BLOCK, first + (i + 1) * BLOCK) for i in range(n)]
    kept = filt.W
    probe = MdfFilter(filt.B, filt.P, filt.mu)
    probe.W = kept.copy()
    probe._shift(-(new_lag - old_lag))
    scores = {
        "keep": filt.predict_erle_db(kept, refs, mics),
        "shift": filt.predict_erle_db(probe.W, refs, mics),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > KEEP_FILTER_MIN_ERLE_DB else "reset"


class StreamingCanceller:
    """The canceller, one BLOCK at a time, as the live `CleanSource` runs it.

    `process(mic_block, ref_get)` takes the next BLOCK of mic samples and a
    reader `ref_get(position, n)` for the reference in the *mic's* sample
    index space (0 = this canceller's first mic sample), zero-filled where
    the reference has no audio. Every decision at a block uses only audio up
    to that block: it keeps the last TRACK_WINDOW_S of mic itself, and asks
    the reference reader for nothing later than the current block. The
    offline `cancel()` drives this same object from arrays, so the live path
    is the measured path."""

    def __init__(self):
        self.tracker = DelayTracker()
        self.filt = MdfFilter()
        self.prev_lag: int | None = None
        self.n = 0  # mic samples consumed so far
        self._win = int(TRACK_WINDOW_S * SR)
        self._every = int(TRACK_EVERY_S * SR)
        self._next_track = self._every
        self._hist = np.zeros(0, dtype=np.float32)  # mic [n - len(hist), n)
        self.result = CancelResult(clean=np.empty(0, dtype=np.float32))

    def _mic_get(self, a: int, b: int) -> np.ndarray:
        first = self.n + BLOCK - self._hist.size  # history includes the current block
        return self._hist[a - first : b - first]

    def process(self, mic_block: np.ndarray, ref_get) -> np.ndarray:
        mic_block = np.asarray(mic_block, dtype=np.float32)
        start, end = self.n, self.n + BLOCK
        self._hist = np.concatenate((self._hist, mic_block))[-self._win :]
        res = self.result
        if end >= self._next_track:
            a = max(0, end - self._win)
            changed = self.tracker.update(self._mic_get(a, end), ref_get(a, end - a))
            res.delays.append((end / SR, self.tracker.lag, round(self.tracker.coherence, 3)))
            if changed:
                lag = self.tracker.lag
                choice = _choose_on_relock(self.filt, self._mic_get, ref_get, start, self.prev_lag, lag)
                history = [_ref_block(ref_get, start - k * BLOCK, lag) for k in range(PARTITIONS, 0, -1)]
                self.filt.realign(history, keep=choice != "reset")
                if choice == "shift":
                    self.filt._shift(-(lag - self.prev_lag))
                res.relocks.append(end / SR)
                res.relock_choices.append(choice)
            self.prev_lag = self.tracker.lag
            self._next_track += self._every
        self.n = end
        res.divergence_resets = self.filt.divergence_resets
        res.rollbacks = self.filt.rollbacks
        if self.tracker.lag is None:
            return mic_block.copy()
        return self.filt.process(mic_block, _ref_block(ref_get, start, self.tracker.lag))


def array_reader(x: np.ndarray):
    """`ref_get` over a whole array: x[position:position+n], zero-filled
    outside it."""
    x = np.asarray(x, dtype=np.float32)

    def get(position: int, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        lo, hi = max(position, 0), min(position + n, x.size)
        if hi > lo:
            out[lo - position : hi - position] = x[lo:hi]
        return out

    return get


def cancel(mic: np.ndarray, ref: np.ndarray) -> CancelResult:
    """Cancel `ref` out of `mic` (both at SR), causally, via the same
    `StreamingCanceller` the live path runs. Before the first lock, output =
    input. `ref` indices are file-relative, so lags may be negative offline.
    The live `CleanSource` aligns ring positions instead."""
    mic = np.asarray(mic, dtype=np.float32)
    clean = mic.copy()
    sc = StreamingCanceller()
    ref_get = array_reader(ref)
    for start in range(0, mic.size - BLOCK + 1, BLOCK):
        clean[start : start + BLOCK] = sc.process(mic[start : start + BLOCK], ref_get)
    res = sc.result
    res.clean = clean
    if sc.tracker.lag is not None:
        res.impulse_response = sc.filt.impulse_response()
    return res
