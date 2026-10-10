"""M8-02: engine orchestration tests — the tick contract, offline.

`Engine._tick` composes DSP, VAD certification, emotion with the M6
music-aware correction, and headcount. Until M8 that composition was
exercised only by live sessions. These tests drive the real `_tick` with:

- a fake `AudioSource` over a **real** `RingBuffer`, so the VAD's
  `read_since` position arithmetic is exercised;
- a scripted VAD (speech ratio set per tick; records the certification
  threshold it was asked for);
- scripted emotion and headcount workers (no threads, no models);
- a scripted `PlaybackStateSource`.

Time is injected through `_tick(now=..., wall=...)` (M8-02 design point
(i), founder 2026-10-09); `time.monotonic` is never patched.

Dominance is not faked. The fake source writes a two-tone signal (1 kHz in
the mid band, 5 kHz in the high band) whose period divides every write, so
each tick's 5 s window is sample-identical. `_window_m` then recomputes the
engine's own dominance from that window, exactly. Every test that depends
on a dominance regime asserts the regime first.

Config is `Config()` defaults (the measured values; `.env` is not read)
with only the signature path, and where a test says so one knob, replaced.
Each acceptance bullet of ROADMAP M8-02 is one test below; two of them pin
behavior M8-03 will change, and say so.
"""

from __future__ import annotations

import dataclasses
import threading

import numpy as np
import pytest

from sensing import dsp
from sensing.audio import RingBuffer
from sensing.config import Config
from sensing.emotion import EmotionReading
from sensing.engine import Engine
from sensing.headcount import HeadcountReading
from sensing.music import dominance
from sensing.state import HeadcountBucket

SR = 16_000
TRACK_A = "spotify:track:A"
TRACK_B = "spotify:track:B"


# -- fakes --------------------------------------------------------------------


class FakeSource:
    sample_rate = SR
    device_name = "fake"

    def __init__(self, seconds: float = 30.0):
        self.ring = RingBuffer(int(seconds * SR))
        self.stops = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stops += 1


class FakeVad:
    """Scripted certification. Records what the engine fed it and which
    threshold it certified at."""

    def __init__(self):
        self.ratio = 0.0
        self.fed: list[int] = []
        self.thresholds: list[float | None] = []

    def load(self) -> None:
        pass

    def feed(self, samples: np.ndarray) -> None:
        self.fed.append(int(samples.size))

    def speech_ratio(self, threshold: float | None = None) -> float:
        self.thresholds.append(threshold)
        return self.ratio

    def speech_mask(self, threshold: float | None = None) -> np.ndarray:
        return np.ones(4, dtype=bool)


class FakeEmotion:
    """The worker surface the tick uses, with the real latest() arithmetic."""

    status = "ready"

    def __init__(self):
        self.reading: EmotionReading | None = None
        self.reference: tuple[str, float, float] | None = None
        self.submits: list[float] = []
        self.windows: list = []
        self.reference_submits: list[str] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def submit(self, window, speech_ratio, now) -> None:
        self.submits.append(speech_ratio)
        self.windows.append(window)

    def submit_reference(self, window, track_id, now) -> None:
        self.reference_submits.append(track_id)

    def pop_reference(self):
        ref, self.reference = self.reference, None
        return ref

    def latest(self, now):
        if self.reading is None:
            return None, None
        return self.reading, max(0.0, now - self.reading.at)


class FakeHeadcount:
    status = "ready"

    def __init__(self):
        self.reading: HeadcountReading | None = None
        self.submits: list[tuple[float, bool]] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def submit(self, window, mask, ratio, rms, now, playback_active, floor):
        self.submits.append((ratio, playback_active))

    def latest(self, now):
        if self.reading is None:
            return None, None
        return self.reading, max(0.0, now - self.reading.at)


class FakePlayback:
    def __init__(self):
        self.active = False
        self.track: str | None = None

    def playback_state(self):
        return self.active, self.track


# -- rig ----------------------------------------------------------------------


def _signal(high_share: float, seconds: float, amp: float = 0.1) -> np.ndarray:
    """1 kHz + 5 kHz, power split by `high_share`. Period 16 samples, so any
    whole-second write keeps the window sample-identical tick to tick."""
    t = np.arange(int(round(seconds * SR))) / SR
    lo, hi = np.sqrt(1.0 - high_share), np.sqrt(high_share)
    x = lo * np.sin(2 * np.pi * 1000 * t) + hi * np.sin(2 * np.pi * 5000 * t)
    return (amp * x).astype(np.float32)


class Rig:
    def __init__(self, consumers=(), clean=None, **overrides):
        overrides.setdefault("music_signatures_path", None)
        self.config = dataclasses.replace(Config(), **overrides)
        self.source = FakeSource()
        self.playback = FakePlayback()
        self.engine = Engine(
            self.source, self.config, list(consumers), self.playback, clean_source=clean
        )
        self.vad = self.engine.vad = FakeVad()
        self.emotion = self.engine.emotion = FakeEmotion()
        self.headcount = self.engine.headcount = FakeHeadcount()
        self.now = 1000.0
        self.high = 0.0
        self.amp = 0.1
        # Fill one window so the first tick sees a full, homogeneous one.
        self.source.ring.write(_signal(self.high, self.config.window_s, self.amp))

    def set_signal(self, high: float, amp: float | None = None) -> None:
        self.high = high
        self.amp = self.amp if amp is None else amp
        self.source.ring.write(_signal(high, self.config.window_s, self.amp))

    def tick(self, dt: float = 2.0, ratio: float | None = None):
        self.now += dt
        if ratio is not None:
            self.vad.ratio = ratio
        self.source.ring.write(_signal(self.high, dt, self.amp))
        return self.engine._tick(now=self.now, wall=1.79e9 + self.now)

    def play(self, track: str | None) -> None:
        self.playback.active, self.playback.track = True, track

    def stop_playback(self) -> None:
        self.playback.active, self.playback.track = False, None

    def reading(self, valence, arousal, confidence=0.9, age=0.5) -> EmotionReading:
        """Land a new inference `age` seconds before the next tick."""
        r = EmotionReading(valence, arousal, confidence, at=self.now + 2.0 - age)
        self.emotion.reading = r
        return r

    def window_m(self) -> float:
        """The dominance the engine computed on the last tick, recomputed
        from the identical window."""
        window = self.source.ring.read_last(int(self.config.window_s * SR))
        high = dsp.analyze(window, SR).spectral_balance.get("high", 0.0)
        return dominance(
            high, self.config.music_dominance_lo, self.config.music_dominance_hi
        )

    @property
    def corrector(self):
        return self.engine._music_aware

    @property
    def baseline(self):
        return self.corrector._clean_baseline

    @property
    def signatures(self):
        return self.engine._signatures

    def count_baseline_updates(self) -> list[float]:
        calls: list[float] = []
        real = self.baseline.update

        def spy(v, a, now):
            calls.append(now)
            real(v, a, now)

        self.baseline.update = spy
        return calls

    def establish_baseline(self, valence=0.1, arousal=0.0) -> None:
        """One clean, fresh reading with playback off."""
        self.stop_playback()
        self.reading(valence, arousal)
        self.tick(ratio=0.6)


# High shares for each dominance regime under the default knots
# (lo 0.05, hi 0.30). Each test re-measures and asserts its regime.
MIXED = 0.25  # m ~ 0.8, above music_pull_m_floor
DEAD_BAND = 0.09  # m ~ 0.16, between music_baseline_m_max and the floor
CLEAN = 0.0  # m == 0


# -- dedup and freshness --------------------------------------------------------


def test_dedup_one_reading_banks_once_across_three_ticks():
    rig = Rig()
    updates = rig.count_baseline_updates()
    rig.reading(0.3, 0.2, age=0.1)
    for _ in range(3):
        rig.tick(dt=1.0, ratio=0.6)  # staleness 0.1 → 2.1 s, fresh throughout
    assert len(updates) == 1


def test_dedup_one_mixed_reading_banks_one_pull_sample():
    rig = Rig()
    rig.establish_baseline()
    rig.play(TRACK_A)
    rig.set_signal(MIXED)
    rig.reading(0.6, 0.5, age=0.1)
    for _ in range(3):
        rig.tick(dt=1.0, ratio=0.6)
    assert rig.window_m() >= rig.config.music_pull_m_floor
    assert rig.signatures.lookup(TRACK_A).pull_refs == 1


def test_freshness_reading_past_the_bound_banks_nothing():
    rig = Rig()
    updates = rig.count_baseline_updates()
    bound = rig.config.emotion_min_interval_s + rig.config.hop_s
    rig.reading(0.3, 0.2, age=bound + 0.5)
    rig.tick(ratio=0.6)
    assert updates == []


def test_freshness_bound_is_inclusive():
    rig = Rig()
    updates = rig.count_baseline_updates()
    bound = rig.config.emotion_min_interval_s + rig.config.hop_s
    rig.reading(0.3, 0.2, age=bound)
    rig.tick(ratio=0.6)
    assert len(updates) == 1


# -- clean vs mixed ---------------------------------------------------------------


def test_playback_off_updates_the_clean_baseline():
    rig = Rig()
    rig.reading(0.3, -0.2)
    rig.tick(ratio=0.6)
    assert rig.baseline.get(rig.now, rig.config.music_baseline_max_age_s) == (0.3, -0.2)


def test_mixed_reading_banks_exactly_one_pull_sample_of_the_measured_value():
    rig = Rig()
    rig.establish_baseline(valence=0.1, arousal=-0.1)
    base = rig.baseline.get(rig.now, rig.config.music_baseline_max_age_s)
    rig.play(TRACK_A)
    rig.set_signal(MIXED)
    rig.reading(0.7, 0.4)
    rig.tick(ratio=0.6)
    m = rig.window_m()
    assert m >= rig.config.music_pull_m_floor
    sig = rig.signatures.lookup(TRACK_A)
    assert sig.pull_refs == 1
    # First sample of the adaptive mean is the sample itself.
    assert sig.pull_valence == (0.7 - base[0]) / m
    assert sig.pull_arousal == (0.4 - base[1]) / m
    # Mixed readings never feed the baseline.
    assert rig.baseline.get(rig.now, rig.config.music_baseline_max_age_s) == base


def test_dead_band_dominance_banks_neither():
    rig = Rig()
    rig.establish_baseline()
    updates = rig.count_baseline_updates()
    rig.play(TRACK_A)
    rig.set_signal(DEAD_BAND)
    rig.reading(0.7, 0.4)
    rig.tick(ratio=0.6)
    m = rig.window_m()
    assert rig.config.music_baseline_m_max < m < rig.config.music_pull_m_floor
    assert updates == []
    assert rig.signatures.lookup(TRACK_A) is None


def test_stale_baseline_banks_no_pull_sample():
    rig = Rig()
    rig.establish_baseline()
    rig.now += rig.config.music_baseline_max_age_s + 10.0
    rig.play(TRACK_A)
    rig.set_signal(MIXED)
    rig.reading(0.7, 0.4)
    rig.tick(ratio=0.6)
    assert rig.window_m() >= rig.config.music_pull_m_floor
    assert rig.signatures.lookup(TRACK_A) is None


# -- basis order and the discount floor -----------------------------------------


def _mixed_tick(rig: Rig, track: str | None, valence=0.5, arousal=0.3, conf=0.9):
    rig.play(track)
    rig.set_signal(MIXED)
    rig.reading(valence, arousal, confidence=conf)
    return rig.tick(ratio=0.6)


def test_basis_pull_beats_standalone():
    rig = Rig()
    for _ in range(rig.config.music_min_refs):
        rig.signatures.add_reference(TRACK_A, 0.2, 0.1)
        rig.signatures.add_pull_reference(TRACK_A, 0.3, 0.2)
    state = _mixed_tick(rig, TRACK_A)
    assert state.emotion_correction["basis"] == "pull"
    assert state.emotion_correction["refs"] == rig.config.music_min_refs
    assert state.emotion_correction["track_id"] == TRACK_A


def test_basis_standalone_beats_discount_floor():
    rig = Rig()
    for _ in range(rig.config.music_min_refs):
        rig.signatures.add_reference(TRACK_A, 0.2, 0.1)
    state = _mixed_tick(rig, TRACK_A)
    assert state.emotion_correction["basis"] == "standalone"
    assert state.emotion_correction["refs"] == rig.config.music_min_refs
    assert state.emotion_confidence == 0.9  # no discount when corrected


def test_basis_below_min_refs_falls_to_the_discount_floor():
    rig = Rig()
    for _ in range(rig.config.music_min_refs - 1):
        rig.signatures.add_reference(TRACK_A, 0.2, 0.1)
        rig.signatures.add_pull_reference(TRACK_A, 0.3, 0.2)
    state = _mixed_tick(rig, TRACK_A)
    assert state.emotion_correction is None
    assert state.emotion_confidence < 0.9


def test_discount_floor_confidence():
    rig = Rig()
    state = _mixed_tick(rig, TRACK_A, conf=0.9)
    m = rig.window_m()
    assert m > 0.0
    expected = 0.9 * max(0.0, 1.0 - rig.config.music_discount_gamma * m)
    assert state.emotion_correction is None
    assert state.emotion_confidence == round(expected, 2)
    assert state.emotion_music_dominance == round(m, 3)


def test_discount_floor_clamps_at_zero():
    # gamma replaced only to reach the clamp; not a calibration.
    rig = Rig(music_discount_gamma=5.0)
    state = _mixed_tick(rig, TRACK_A, conf=0.9)
    assert rig.config.music_discount_gamma * rig.window_m() > 1.0
    assert state.emotion_confidence == 0.0


def test_reference_tap_lands_in_the_signature_store():
    rig = Rig()
    rig.play(TRACK_A)
    rig.emotion.reference = (TRACK_A, 0.25, -0.1)
    rig.tick(ratio=0.0)  # music-only window: also submits the next tap
    sig = rig.signatures.lookup(TRACK_A)
    assert (sig.refs, sig.valence, sig.arousal) == (1, 0.25, -0.1)
    assert rig.emotion.reference_submits == [TRACK_A]


# -- M8-03: corrections bind to the reading's context -----------------------------
# These two were pinned characterization tests of the pre-M8-03 behavior
# (wrong-track correction; uncorrected after a stop). M8-03 flipped them
# deliberately (founder-approved, 2026-10-09).


def test_track_boundary_keeps_the_readings_own_track():
    """ROADMAP M8-03: a reading taken under track A, still fresh when B
    becomes current, stays corrected with A's signature."""
    rig = Rig()
    for _ in range(rig.config.music_min_refs):
        rig.signatures.add_pull_reference(TRACK_A, 0.3, 0.2)
        rig.signatures.add_pull_reference(TRACK_B, -0.4, -0.3)
    rig.play(TRACK_A)
    rig.set_signal(MIXED)
    rig.reading(0.5, 0.3, age=0.1)
    under_a = rig.tick(dt=1.0, ratio=0.6)
    assert under_a.emotion_correction["track_id"] == TRACK_A
    rig.play(TRACK_B)  # same reading, still fresh
    under_b = rig.tick(dt=1.0, ratio=0.0)
    assert under_b.emotion_correction == under_a.emotion_correction
    assert under_b.emotion_correction["track_id"] == TRACK_A
    rig.reading(0.5, 0.3, age=0.1)  # a new reading lands under B
    fresh_b = rig.tick(dt=1.0, ratio=0.6)
    assert fresh_b.emotion_correction["track_id"] == TRACK_B


def test_playback_stop_keeps_a_music_reading_corrected():
    """ROADMAP M8-03: a reading taken during playback stays corrected with
    its captured context after playback stops; a reading taken after the
    stop is uncorrected. The published dominance is the live measurement
    (founder choice (a)), so it reads None once playback is off."""
    rig = Rig()
    for _ in range(rig.config.music_min_refs):
        rig.signatures.add_pull_reference(TRACK_A, 0.3, 0.2)
    rig.play(TRACK_A)
    rig.set_signal(MIXED)
    rig.reading(0.5, 0.3, age=0.1)
    corrected = rig.tick(dt=1.0, ratio=0.6)
    assert corrected.emotion_correction is not None
    rig.stop_playback()
    after = rig.tick(dt=1.0, ratio=0.0)
    assert after.emotion_correction == corrected.emotion_correction
    assert after.emotion_music_dominance is None
    rig.reading(0.5, 0.3, age=0.1)  # taken with playback off
    clean = rig.tick(dt=1.0, ratio=0.6)
    assert clean.emotion_correction is None


# -- certification, consumers, publish ----------------------------------------------


def test_certification_threshold_switches_with_playback():
    rig = Rig()
    rig.tick(ratio=0.6)
    rig.play(TRACK_A)
    rig.tick(ratio=0.6)
    assert rig.vad.thresholds[-2] == rig.config.vad_threshold
    assert rig.vad.thresholds[-1] == rig.config.vad_playback_threshold
    assert [p for _, p in rig.headcount.submits] == [False, True]


def test_vad_is_fed_exactly_the_samples_written_since_the_last_tick():
    rig = Rig()
    rig.tick(dt=2.0)
    rig.tick(dt=1.0)
    rig.tick(dt=3.0)
    # The first tick also sees the window-filling write from the rig.
    first = int(rig.config.window_s * SR) + 2 * SR
    assert rig.vad.fed == [first, 1 * SR, 3 * SR]


def test_silence_submits_nothing_and_headcount_holds():
    rig = Rig()
    rig.headcount.reading = HeadcountReading(
        HeadcountBucket.PAIR, 0.7, 2, 0.0, 0.5, 0.2, 0.3, 0, 1.0, at=rig.now
    )
    state = rig.tick(ratio=0.05)
    assert rig.headcount.submits == [] and rig.emotion.submits == []
    assert state.headcount_bucket == HeadcountBucket.PAIR
    assert state.headcount_staleness_s == 2.0


class _Boom:
    def on_state(self, state):
        raise RuntimeError("consumer failure")


class _Collect:
    def __init__(self):
        self.states = []

    def on_state(self, state):
        self.states.append(state)


def test_a_failing_consumer_does_not_starve_the_next():
    collect = _Collect()
    # hop_s shortened only so run() paces quickly; run() reads real clocks.
    rig = Rig(consumers=[_Boom(), collect], hop_s=0.01)
    rig.engine.run(max_ticks=3)
    assert len(collect.states) == 3


def test_noise_floor_updates_only_on_quiescent_windows():
    rig = Rig()
    quiet = rig.tick(ratio=0.05)
    assert quiet.noise_floor_dbfs is not None
    rig.set_signal(CLEAN, amp=0.5)  # much louder room
    talking = rig.tick(ratio=0.5)
    assert talking.noise_floor_dbfs == quiet.noise_floor_dbfs
    rig.tick(ratio=0.05)
    assert rig.engine._noise_floor.value > (quiet.noise_floor_dbfs + 0.5)


def test_mood_drops_once_emotion_is_stale_but_the_reading_holds():
    rig = Rig()
    rig.reading(0.5, 0.5, age=0.5)
    fresh = rig.tick(ratio=0.6)
    assert fresh.mood is not None
    rig.now += rig.config.emotion_max_staleness_s  # no new inference
    stale = rig.tick(ratio=0.0)
    assert stale.emotion_staleness_s > rig.config.emotion_max_staleness_s
    assert stale.mood is None
    assert stale.valence is not None  # silence holds; never fabricated away


@pytest.mark.parametrize("enabled", [True, False])
def test_music_aware_off_means_no_dominance_and_no_correction(enabled):
    rig = Rig(music_aware_enabled=enabled)
    for _ in range(3):
        if rig.signatures is not None:
            rig.signatures.add_pull_reference(TRACK_A, 0.3, 0.2)
    state = _mixed_tick(rig, TRACK_A)
    if enabled:
        assert state.emotion_music_dominance is not None
        assert state.emotion_correction is not None
    else:
        assert state.emotion_music_dominance is None
        assert state.emotion_correction is None
        assert state.emotion_confidence == 0.9


# -- M8-07: idempotent shutdown ---------------------------------------------------


def _count_flushes(rig: Rig) -> list[int]:
    calls: list[int] = []
    real = rig.signatures.flush

    def spy():
        calls.append(1)
        real()

    rig.signatures.flush = spy
    return calls


def test_concurrent_stop_stops_the_source_and_flushes_exactly_once():
    for _ in range(100):
        rig = Rig()
        flushes = _count_flushes(rig)
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def stop():
            barrier.wait()
            try:
                rig.engine.stop()
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=stop) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert errors == []
        assert rig.source.stops == 1
        assert len(flushes) == 1


def test_stop_after_runs_own_finally_is_a_no_op():
    rig = Rig(hop_s=0.01)
    flushes = _count_flushes(rig)
    rig.engine.run(max_ticks=1)  # run()'s finally is the first stop
    rig.engine.stop()  # the dashboard main thread's, second
    assert rig.source.stops == 1
    assert len(flushes) == 1


def test_final_flush_waits_for_an_in_flight_tick():
    rig = Rig()
    flushes = _count_flushes(rig)
    rig.engine._tick_lock.acquire()  # a tick is mid-way on the engine thread
    stopper = threading.Thread(target=rig.engine.stop)
    stopper.start()
    stopper.join(0.1)
    assert stopper.is_alive() and flushes == []  # waiting, not flushing
    rig.engine._tick_lock.release()
    stopper.join(5)
    assert not stopper.is_alive()
    assert len(flushes) == 1


# -- M12-01c: the playback reference's lifecycle ---------------------------------


class _FakeReference:
    def __init__(self, fail=False):
        self.fail = fail
        self.device_name = "Speakers (fake)"
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        if self.fail:
            raise RuntimeError("playback reference unavailable: device busy")

    def stop(self):
        self.stops += 1


def test_reference_starts_and_stops_with_the_engine():
    rig = Rig(hop_s=0.01)
    ref = rig.engine.reference = _FakeReference()
    rig.engine.run(max_ticks=2)
    rig.engine.stop()  # a second stop (M8-07): still one reference stop
    assert (ref.starts, ref.stops) == (1, 1)


def test_reference_failure_never_stops_sensing():
    collect = _Collect()
    rig = Rig(consumers=[collect], hop_s=0.01)
    rig.engine.reference = _FakeReference(fail=True)
    rig.engine.run(max_ticks=3)
    assert len(collect.states) == 3  # ticks ran, published as without it
    assert rig.engine.reference is None


def test_reference_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RTR_PLAYBACK_REFERENCE_ENABLED", raising=False)
    assert Config().playback_reference_enabled is False
    monkeypatch.setenv("RTR_PLAYBACK_REFERENCE_ENABLED", "1")
    assert Config.from_env().playback_reference_enabled is True


# -- M12-02 step 4: analysing the clean stream -------------------------------------


class _FakeClean:
    """A clean stream: its own ring, at half the raw amplitude."""

    def __init__(self, status="running"):
        self.ring = RingBuffer(int(30 * SR))
        self.status = status
        self.error = None
        self.device_name = "fake (playback cancelled)"
        self.stops = 0

    def start(self):
        pass

    def stop(self):
        self.stops += 1


def _feed_both(rig, clean, dt=2.0):
    """Advance one tick with the clean ring carrying half the raw signal."""
    rig.now += dt
    x = _signal(rig.high, dt, rig.amp)
    rig.source.ring.write(x)
    clean.ring.write(0.5 * x)
    return rig.engine._tick(now=rig.now, wall=1.79e9 + rig.now)


def test_clean_stream_feeds_certification_emotion_and_headcount_not_dsp():
    clean = _FakeClean()
    rig = Rig(clean=clean)
    clean.ring.write(0.5 * _signal(0.0, rig.config.window_s, rig.amp))
    rig.vad.ratio = 0.6
    raw_only = Rig()
    raw_only.vad.ratio = 0.6
    state = _feed_both(rig, clean)
    ref_state = raw_only.tick(ratio=0.6)
    assert state.analysis_stream == "clean" and ref_state.analysis_stream == "raw"
    # VAD read the clean ring (same sample counts, different ring)...
    assert rig.vad.fed[-1] == raw_only.vad.fed[-1]
    # ...emotion got the clean window (half amplitude)...
    w_clean, w_raw = rig.emotion.windows[-1], raw_only.emotion.windows[-1]
    assert np.allclose(w_clean, 0.5 * w_raw)
    # ...but loudness is the raw signal's (the DSP heartbeat stays raw).
    assert state.loudness_dbfs == ref_state.loudness_dbfs


def test_clean_path_learns_its_own_signatures_file(tmp_path):
    cfg_path = str(tmp_path / "sigs.json")
    clean = _FakeClean()
    eng = Engine(FakeSource(), dataclasses.replace(Config(), music_signatures_path=cfg_path), [], clean_source=clean)
    raw = Engine(FakeSource(), dataclasses.replace(Config(), music_signatures_path=cfg_path), [])
    assert str(eng._signatures.path).endswith("sigs.clean.json")
    assert str(raw._signatures.path).endswith("sigs.json")


def test_failed_clean_stream_falls_back_to_raw_with_raw_signatures(tmp_path):
    clean = _FakeClean()
    rig = Rig(clean=clean, music_signatures_path=str(tmp_path / "sigs.json"))
    clean.ring.write(0.5 * _signal(0.0, rig.config.window_s, rig.amp))
    assert _feed_both(rig, clean).analysis_stream == "clean"
    clean.status, clean.error = "failed", "boom"
    state = _feed_both(rig, clean)
    assert state.analysis_stream == "raw"
    assert rig.engine.clean is None and clean.stops == 1
    assert str(rig.engine._signatures.path).endswith("sigs.json")
    assert rig.engine._music_aware._signatures is rig.engine._signatures


def test_no_reference_means_no_cancellation():
    collect = _Collect()
    clean = _FakeClean()
    rig = Rig(consumers=[collect], clean=clean, hop_s=0.01)
    rig.engine.reference = _FakeReference(fail=True)
    rig.engine.run(max_ticks=2)
    assert rig.engine.clean is None
    assert {s.analysis_stream for s in collect.states} == {"raw"}
