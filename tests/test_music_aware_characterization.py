"""M8-01 characterization: the music-aware correction, before and after
extraction, is byte-identical.

`_Frozen` below is a verbatim transcription of the pre-refactor engine logic
at `main` @ ae9775f: `Engine._bank_evidence`, `Engine._correct`, and the
inline block of `Engine._tick` that banks, corrects or discounts a reading
(engine.py 281–302 at that commit). It is deliberately a separate copy: a
characterization test that shares code with what it tests proves nothing.
Do not edit it to make a test pass; if the comparison fails, the refactor
changed behavior.

The script drives `_Frozen` and the code under test side by side and
requires exact float equality on every step's (valence, arousal,
confidence, correction), and on the final signature store and clean
baseline. Offline, no models.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from sensing.config import Config
from sensing.emotion import EmotionReading
from sensing.engine import Engine
from sensing.music import CleanBaseline, TrackSignatureStore, apply_correction


class _Frozen:
    """Pre-refactor logic, transcribed from engine.py @ ae9775f."""

    def __init__(self, config: Config, signatures: TrackSignatureStore):
        self.config = config
        self._signatures = signatures
        self._clean_baseline = CleanBaseline(config.music_baseline_tau_s)
        self._last_banked_at: float | None = None

    def step(self, reading, staleness, playback_active, playback_track_id,
             music_dominance, now):
        # engine.py 282–302 (inside `if reading is not None:`)
        emotion_correction = None
        v_inst, a_inst = reading.valence, reading.arousal
        confidence = reading.confidence
        if self._signatures is not None:
            self._bank_evidence(
                reading, staleness, playback_active,
                playback_track_id, music_dominance, now,
            )
        if music_dominance is not None and music_dominance > 0.0:
            corrected = self._correct(
                v_inst, a_inst, playback_track_id, music_dominance
            )
            if corrected is not None:
                v_inst, a_inst, emotion_correction = corrected
            else:
                confidence *= max(
                    0.0,
                    1.0 - self.config.music_discount_gamma * music_dominance,
                )
        return v_inst, a_inst, confidence, emotion_correction

    def _bank_evidence(self, reading, staleness, playback_active,
                       playback_track_id, music_dominance, now):
        if reading.at == self._last_banked_at:
            return
        fresh = staleness is not None and staleness <= (
            self.config.emotion_min_interval_s + self.config.hop_s
        )
        if not fresh:
            return
        self._last_banked_at = reading.at
        clean = not playback_active or (
            music_dominance is not None
            and music_dominance <= self.config.music_baseline_m_max
        )
        if clean:
            self._clean_baseline.update(reading.valence, reading.arousal, now)
            return
        if (
            playback_track_id is not None
            and music_dominance is not None
            and music_dominance >= self.config.music_pull_m_floor
        ):
            base = self._clean_baseline.get(
                now, self.config.music_baseline_max_age_s
            )
            if base is not None:
                self._signatures.add_pull_reference(
                    playback_track_id,
                    (reading.valence - base[0]) / music_dominance,
                    (reading.arousal - base[1]) / music_dominance,
                )

    def _correct(self, v_inst, a_inst, playback_track_id, m):
        sig = self._signatures.lookup(playback_track_id)
        if sig is None:
            return None
        if sig.pull_refs >= self._signatures.min_refs:
            basis, pv, pa, refs = "pull", sig.pull_valence, sig.pull_arousal, sig.pull_refs
            scale_v, scale_a = self.config.music_beta_v, self.config.music_beta_a
        elif sig.refs >= self._signatures.min_refs:
            basis, pv, pa, refs = "standalone", sig.valence, sig.arousal, sig.refs
            scale_v = self.config.music_standalone_scale_v
            scale_a = self.config.music_standalone_scale_a
        else:
            return None
        v, a, dv, da = apply_correction(
            v_inst, a_inst, pv, pa, m, scale_v, scale_a,
            self.config.music_max_correction,
        )
        return v, a, {
            "valence": round(dv, 3),
            "arousal": round(da, 3),
            "track_id": playback_track_id,
            "basis": basis,
            "refs": refs,
        }


# -- the script ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Step:
    now: float
    reading: EmotionReading
    playback_active: bool
    track_id: str | None
    dominance: float | None  # None whenever playback is off, as in _tick
    ref_tap: tuple[str, float, float] | None = None  # popped before banking


def _script() -> list[Step]:
    """Deterministic 79-step session at a 2 s hop. Phases: clean room →
    playback with standalone taps → pull estimator takes over → track change
    with no signature (discount floor) → dead band and m == 0 → None track
    → baseline goes stale → playback stops while a reading is fresh, plus
    exact-boundary steps for every threshold the banking logic compares."""
    rng = random.Random(20261009)
    steps: list[Step] = []
    now = 1000.0
    reading = None

    def new_reading(at: float) -> EmotionReading:
        return EmotionReading(
            valence=round(rng.uniform(-0.6, 0.8), 4),
            arousal=round(rng.uniform(-0.5, 0.9), 4),
            confidence=round(rng.uniform(0.4, 1.0), 3),
            at=at,
        )

    def add(n, playback, track, m_fn, every=1, lag=0.4, taps=None,
            stale_at=None):
        nonlocal now, reading
        for i in range(n):
            now += 2.0
            if reading is None or i % every == 0:
                reading = new_reading(now - lag)  # a new inference landed
            if stale_at is not None and i == stale_at:
                reading = new_reading(now - 7.5)  # older than the 4 s bound
            m = m_fn(i) if playback else None
            tap = taps(i) if taps is not None else None
            steps.append(Step(now, reading, playback, track, m, tap))

    # A: clean room, playback off; new reading every other tick (dedup), one
    # reading that lands already stale.
    add(12, False, None, None, every=2, stale_at=7)
    # B: track A playing; standalone reference taps first, mixed dominance.
    add(
        14, True, "spotify:track:A",
        lambda i: [0.05, 0.6, 0.18, 0.4, 0.9, 0.3, 0.75][i % 7],
        every=1,
        taps=lambda i: ("spotify:track:A", 0.31, 0.22) if i < 4 else None,
    )
    # C: pull refs for A are past min_refs; repeated readings across ticks.
    add(10, True, "spotify:track:A", lambda i: 0.5 + 0.04 * i, every=3)
    # C2: exact boundaries. m == music_baseline_m_max (clean), m ==
    # music_pull_m_floor (banks a pull sample), staleness == the freshness
    # bound emotion_min_interval_s + hop_s (fresh). Integral `now` keeps
    # the arithmetic exact.
    add(4, True, "spotify:track:A", lambda i: [0.1, 0.25][i % 2], every=1)
    add(2, False, None, None, every=1, lag=4.0)
    # D: track change to B with no signature: discount floor; then taps.
    add(
        10, True, "spotify:track:B",
        lambda i: [0.8, 0.35, 0.0, 0.12, 0.66][i % 5],
        every=2,
        taps=lambda i: ("spotify:track:B", -0.2, 0.4) if i >= 5 else None,
    )
    # E: playback on but the controller has no track id.
    add(4, True, None, lambda i: 0.7, every=1)
    # F: long gap: the clean baseline is older than music_baseline_max_age_s.
    now += 400.0
    add(8, True, "spotify:track:A", lambda i: 0.45 + 0.05 * i, every=2)
    # G: playback stops while the last mixed reading is still fresh.
    add(12, False, None, None, every=2, lag=0.2)
    # H: baseline age exactly music_baseline_max_age_s (still usable),
    # then 2 s past it (stale).
    add(1, False, None, None, every=1)
    now += 298.0
    add(2, True, "spotify:track:A", lambda i: 0.6, every=1)
    return steps


def _config() -> Config:
    # Defaults are the measured config; nothing here may tune them.
    return dataclasses.replace(
        Config(), music_signatures_path=None, headcount_enabled=False
    )


def _store(config: Config) -> TrackSignatureStore:
    return TrackSignatureStore(None, min_refs=config.music_min_refs)


def _snapshot(signatures: TrackSignatureStore, baseline: CleanBaseline):
    return (
        dict(signatures._signatures),
        baseline._valence.value,
        baseline._arousal.value,
        baseline._last_update,
    )


class _LiveEngine:
    """The live engine's methods, driven through the same inline block."""

    def __init__(self, config: Config):
        self.engine = Engine(object(), config, [])
        self.engine._signatures = _store(config)

    def step(self, reading, staleness, playback_active, track_id, m, now):
        e = self.engine
        emotion_correction = None
        v_inst, a_inst = reading.valence, reading.arousal
        confidence = reading.confidence
        e._bank_evidence(reading, staleness, playback_active, track_id, m, now)
        if m is not None and m > 0.0:
            corrected = e._correct(v_inst, a_inst, track_id, m)
            if corrected is not None:
                v_inst, a_inst, emotion_correction = corrected
            else:
                confidence *= max(0.0, 1.0 - e.config.music_discount_gamma * m)
        return v_inst, a_inst, confidence, emotion_correction

    @property
    def signatures(self):
        return self.engine._signatures

    @property
    def baseline(self):
        return self.engine._clean_baseline


def _run(subject, signatures: TrackSignatureStore, script: list[Step]):
    out = []
    for s in script:
        if s.ref_tap is not None:
            signatures.add_reference(*s.ref_tap)
        staleness = max(0.0, s.now - s.reading.at)
        out.append(
            subject.step(s.reading, staleness, s.playback_active,
                         s.track_id, s.dominance, s.now)
        )
    return out


def test_script_covers_every_branch():
    """The script must exercise every path, or identity proves little."""
    config = _config()
    frozen = _Frozen(config, _store(config))
    outputs = _run(frozen, frozen._signatures, _script())
    bases = {o[3]["basis"] for o in outputs if o[3] is not None}
    discounted = sum(
        1 for s, o in zip(_script(), outputs)
        if o[3] is None and s.dominance and o[2] != s.reading.confidence
    )
    assert len(outputs) >= 50
    assert bases == {"pull", "standalone"}
    assert discounted >= 3
    sig_a = frozen._signatures.lookup("spotify:track:A")
    assert sig_a.pull_refs >= config.music_min_refs
    assert frozen._signatures.lookup("spotify:track:B").pull_refs >= 1


def test_live_engine_matches_frozen_copy():
    """Proves `_Frozen` is a faithful copy of the pre-refactor engine."""
    config = _config()
    script = _script()
    frozen = _Frozen(config, _store(config))
    live = _LiveEngine(config)
    assert _run(live, live.signatures, script) == _run(
        frozen, frozen._signatures, script
    )
    assert _snapshot(live.signatures, live.baseline) == _snapshot(
        frozen._signatures, frozen._clean_baseline
    )


# Each mutation flips one comparison the charter calls a trap. The script
# must notice every one, or "identical outputs" would not rule it out.
# (Dropping the early `return` after a clean baseline update is not listed:
# a clean reading can never satisfy the pull conditions, so that mutant is
# equivalent by construction.)
_MUTATIONS = {
    "freshness bound inclusive": ("staleness <= (", "staleness < ("),
    "pull floor inclusive": (
        "music_dominance >= self.config.music_pull_m_floor",
        "music_dominance > self.config.music_pull_m_floor",
    ),
    "clean ceiling inclusive": (
        "music_dominance <= self.config.music_baseline_m_max",
        "music_dominance < self.config.music_baseline_m_max",
    ),
    "correct only when m > 0": ("music_dominance > 0.0:", "music_dominance >= 0.0:"),
    "dedup by reading.at": (
        "if reading.at == self._last_banked_at:\n            return",
        "if False:\n            return",
    ),
    "pull basis at min_refs": (
        "sig.pull_refs >= self._signatures.min_refs",
        "sig.pull_refs > self._signatures.min_refs",
    ),
    "standalone basis at min_refs": (
        "sig.refs >= self._signatures.min_refs",
        "sig.refs > self._signatures.min_refs",
    ),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_script_detects_mutation(name):
    import inspect

    before, after = _MUTATIONS[name]
    source = inspect.getsource(_Frozen)
    assert source.count(before) == 1, "mutation anchor drifted"
    namespace: dict = {}
    exec(  # noqa: S102 - test-local mutant of a test-local class
        compile(
            "from __future__ import annotations\n"
            "from sensing.music import CleanBaseline, apply_correction\n"
            + source.replace(before, after),
            f"<mutant: {name}>",
            "exec",
        ),
        namespace,
    )
    config = _config()
    script = _script()
    original = _Frozen(config, _store(config))
    mutant = namespace["_Frozen"](config, _store(config))
    same_outputs = _run(mutant, mutant._signatures, script) == _run(
        original, original._signatures, script
    )
    same_state = _snapshot(mutant._signatures, mutant._clean_baseline) == _snapshot(
        original._signatures, original._clean_baseline
    )
    assert not (same_outputs and same_state)
