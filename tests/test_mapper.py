"""Mapping layer: rulebook seed, bands, trend->action, hysteresis, guard,
attribution, serialization round-trips. Pure logic — no mic, models, network."""

import json

import pytest

from conftest import make_state
from mapping import GUARD_CELL, Mapper, MappingConfig, Recommendation, band
from mapping.rulebook import AROUSAL_BANDS, BUCKETS, RULEBOOK, VALENCE_BANDS
from sensing.state import HeadcountBucket


def quick_config(**overrides) -> MappingConfig:
    """Zero-dwell config so single-shot tests emit immediately."""
    defaults = dict(min_dwell_s=0.0)
    defaults.update(overrides)
    return MappingConfig(**defaults)


class TestRulebookSeed:
    def test_full_grid_present(self):
        assert len(RULEBOOK) == len(BUCKETS) * 9
        for bucket in BUCKETS:
            for v in VALENCE_BANDS:
                for a in AROUSAL_BANDS:
                    pool = RULEBOOK[(bucket, v, a)]
                    assert isinstance(pool, list) and pool

    @pytest.mark.parametrize(
        "cell, expected",
        [
            # 2020 GenrePicker transcription spot-checks, one per seed grid
            # plus corners. solo/pair/4 <- "<=3", 8 <- "<=6", 16+ <- ">6".
            (("solo", "high", "high"), ["Pop"]),
            (("4", "high", "high"), ["Pop"]),
            (("4", "mid", "mid"), ["Jazz"]),
            (("4", "low", "high"), ["Soft Rock"]),
            (("solo", "low", "low"), ["Lofi Beats"]),
            (("8", "high", "high"), ["Dance"]),
            (("8", "high", "low"), ["Soul"]),
            (("8", "low", "mid"), ["Country"]),
            (("8", "low", "low"), ["Blues"]),
            (("16", "high", "high"), ["Electronic Dance Music"]),
            (("64", "mid", "mid"), ["R&B"]),
            (("crowd", "low", "high"), ["Hard Rock"]),
            (("crowd", "low", "low"), ["Jazz"]),
        ],
    )
    def test_2020_seed_cells(self, cell, expected):
        assert RULEBOOK[cell] == expected


class TestBands:
    @pytest.mark.parametrize(
        "value, expected",
        [(0.3, "high"), (0.25, "mid"), (0.0, "mid"), (-0.25, "low"), (-0.6, "low")],
    )
    def test_band_split_matches_2020_boundary_semantics(self, value, expected):
        # 2020 used strict `> 0.25` / `> -0.25` comparisons.
        assert band(value, -0.25, 0.25) == expected


class TestMapperBasics:
    def test_first_update_emits_with_attribution(self):
        mapper = Mapper(quick_config())
        rec = mapper.update(make_state())
        assert rec is not None
        assert rec.matched_cell == ("4", "high", "high")
        assert rec.genre_pool == ["Pop"]
        assert rec.schema_version == 1
        assert rec.boundaries_snapshot == quick_config().boundaries()
        assert rec.timestamp == 0.0
        assert "Pop" in rec.summary

    def test_bucket_granularity(self):
        mapper = Mapper(quick_config())
        rec = mapper.update(
            make_state(headcount_bucket=HeadcountBucket.THIRTY_TWO)
        )
        assert rec.matched_cell == ("32", "high", "high")
        assert rec.genre_pool == ["Electronic Dance Music"]

    def test_confidence_blends_emotion_and_headcount(self):
        mapper = Mapper(quick_config())
        rec = mapper.update(
            make_state(emotion_confidence=0.8, headcount_confidence=0.4)
        )
        assert rec.confidence == pytest.approx(0.6, abs=0.01)


class TestTrendToEnergyAction:
    def _run_ramp(self, slope_per_tick: float, start: float = 0.0):
        mapper = Mapper(quick_config())
        recs = []
        for i in range(25):
            arousal = max(-0.9, min(0.9, start + i * slope_per_tick))
            rec = mapper.update(
                make_state(timestamp=i * 2.0, valence=0.5, arousal=arousal)
            )
            if rec:
                recs.append(rec)
        return recs

    def test_rising_arousal_raises(self):
        recs = self._run_ramp(+0.02)  # +0.6/min, well past the 0.1 threshold
        assert recs[-1].energy_action == "raise"
        assert recs[-1].target_arousal > 0.0

    def test_falling_arousal_lowers(self):
        recs = self._run_ramp(-0.02, start=0.9)
        assert recs[-1].energy_action == "lower"

    def test_flat_arousal_holds(self):
        recs = self._run_ramp(0.0, start=0.5)
        assert all(r.energy_action == "hold" for r in recs)

    def test_flat_arousal_defers_to_engine_energy_trend(self):
        mapper = Mapper(quick_config())
        rec = None
        for i in range(25):
            rec = rec or mapper.update(
                make_state(timestamp=i * 2.0, arousal=0.5, trend="rising")
            )
        assert rec.energy_action == "raise"

    def test_target_lead_matches_direction(self):
        cfg = quick_config(target_lead=0.2)
        mapper = Mapper(cfg)
        arousal_at = {}
        last = None
        for i in range(25):
            arousal = 0.0 + i * 0.02
            arousal_at[i * 2.0] = arousal
            r = mapper.update(
                make_state(timestamp=i * 2.0, valence=0.5, arousal=arousal)
            )
            last = r or last
        assert last.energy_action == "raise"
        # Target leads the arousal reading at emission time by target_lead.
        assert last.target_arousal == pytest.approx(
            arousal_at[last.timestamp] + 0.2, abs=1e-6
        )


class TestHysteresis:
    def test_no_reemission_without_material_change(self):
        mapper = Mapper(MappingConfig(min_dwell_s=30.0))
        emitted = [
            r
            for i in range(60)  # 120 s of identical rooms
            if (r := mapper.update(make_state(timestamp=i * 2.0)))
        ]
        assert len(emitted) == 1  # dwell alone never re-emits

    def test_oscillating_mood_respects_dwell(self):
        # Mood flips excited<->chill every tick; without dwell this would
        # emit every tick. Emissions must be spaced >= min_dwell_s.
        mapper = Mapper(MappingConfig(min_dwell_s=30.0))
        times = []
        for i in range(60):
            arousal = 0.5 if i % 2 == 0 else -0.5
            rec = mapper.update(
                make_state(timestamp=i * 2.0, valence=0.5, arousal=arousal)
            )
            if rec:
                times.append(rec.timestamp)
        assert len(times) >= 2  # material changes do eventually re-emit
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert all(gap >= 30.0 for gap in gaps)

    def test_bucket_change_is_material(self):
        mapper = Mapper(MappingConfig(min_dwell_s=10.0))
        assert mapper.update(make_state(timestamp=0.0)) is not None
        assert mapper.update(make_state(timestamp=4.0)) is None  # within dwell
        rec = mapper.update(
            make_state(timestamp=20.0, headcount_bucket=HeadcountBucket.EIGHT)
        )
        assert rec is not None
        assert rec.matched_cell[0] == "8"


class TestLowConfidenceGuard:
    @pytest.mark.parametrize(
        "state_kwargs, reason",
        [
            (dict(speech_ratio=0.0), "no-speech"),
            (
                dict(valence=None, arousal=None, mood=None),
                "no-emotion",
            ),
            (dict(mood=None), "no-emotion"),  # stale reading: values, no mood
            (dict(headcount_bucket=None, headcount_confidence=None), "no-headcount"),
            (dict(headcount_confidence=0.2), "uncertain-regime"),
        ],
    )
    def test_guard_holds_instead_of_guessing(self, state_kwargs, reason):
        mapper = Mapper(quick_config())
        rec = mapper.update(make_state(**state_kwargs))
        assert rec is not None
        assert rec.energy_action == "hold"
        assert rec.confidence == quick_config().guard_confidence
        assert rec.matched_cell == (GUARD_CELL, reason)
        assert rec.genre_pool == []
        assert rec.boundaries_snapshot["min_headcount_confidence"] == 0.35

    def test_guard_to_rulebook_transition_is_material(self):
        mapper = Mapper(quick_config())
        first = mapper.update(make_state(timestamp=0.0, speech_ratio=0.0))
        assert first.matched_cell[0] == GUARD_CELL
        second = mapper.update(make_state(timestamp=2.0))
        assert second is not None
        assert second.matched_cell == ("4", "high", "high")


class TestSerialization:
    def test_recommendation_round_trip(self):
        mapper = Mapper(quick_config())
        rec = mapper.update(make_state())
        wire = json.dumps(rec.to_dict())
        parsed = Recommendation.from_dict(json.loads(wire))
        assert parsed == rec
        assert isinstance(parsed.matched_cell, tuple)
        assert parsed.schema_version == 1
        assert parsed.boundaries_snapshot == rec.boundaries_snapshot
