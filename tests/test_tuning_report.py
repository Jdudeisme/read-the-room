"""Tuning report: synthetic annotation logs with known boundary clustering
must produce the expected counts and suggestions — and write nothing."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPORT_PATH = Path(__file__).parent.parent / "scripts" / "tuning_report.py"
spec = importlib.util.spec_from_file_location("tuning_report", REPORT_PATH)
tuning_report = importlib.util.module_from_spec(spec)
sys.modules["tuning_report"] = tuning_report
spec.loader.exec_module(tuning_report)

SNAPSHOT = {
    "valence_low": -0.25,
    "valence_high": 0.25,
    "arousal_low": -0.25,
    "arousal_high": 0.25,
    "min_dwell_s": 30.0,
    "min_speech_ratio": 0.1,
    "min_headcount_confidence": 0.35,
}


def record(verdict, valence, arousal, cell=("4", "high", "high"), ts=1000.0):
    return {
        "schema_version": 1,
        "ts": ts,
        "verdict": verdict,
        "state": {"valence": valence, "arousal": arousal, "speech_ratio": 0.6},
        "recommendation": {
            "matched_cell": list(cell),
            "boundaries_snapshot": dict(SNAPSHOT),
            "genre_pool": ["Pop"],
        },
    }


def guard_record(verdict="wrong"):
    return {
        "schema_version": 1,
        "ts": 1000.0,
        "verdict": verdict,
        "state": {"valence": None, "arousal": None, "speech_ratio": 0.0},
        "recommendation": {
            "matched_cell": ["guard", "no-speech"],
            "boundaries_snapshot": dict(SNAPSHOT),
            "genre_pool": [],
        },
    }


@pytest.fixture
def synthetic_records():
    # Three wrong calls hugging valence_high from above (0.28 vs cutoff
    # 0.25), two good calls comfortably inside the high band, one guard.
    return (
        [record("wrong", 0.28, 0.5) for _ in range(3)]
        + [record("good", 0.60, 0.5) for _ in range(2)]
        + [guard_record()]
    )


@pytest.fixture
def log_dir(tmp_path, synthetic_records):
    d = tmp_path / "annotations"
    d.mkdir()
    path = d / "2026-07-05.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in synthetic_records), encoding="utf-8"
    )
    return d


class TestAnalysis:
    def test_cell_counts_include_guard_cells(self, synthetic_records):
        counts = tuning_report.cell_counts(synthetic_records)
        assert counts[("4", "high", "high")] == {"wrong": 3, "good": 2}
        assert counts[("guard", "no-speech")] == {"wrong": 1}

    def test_wrong_calls_cluster_at_valence_high(self, synthetic_records):
        near = tuning_report.boundary_proximity(synthetic_records)
        assert near["valence_high"] == 3  # |0.28 - 0.25| <= 0.1
        assert near.get("arousal_high", 0) == 0  # 0.5 is 0.25 away

    def test_suggests_smallest_shift_that_flips_the_wrong_cluster(
        self, synthetic_records
    ):
        suggestions = tuning_report.suggest_adjustments(synthetic_records)
        s = suggestions["valence_high"]
        # +0.05 moves the cutoff to 0.30: the 0.28 wrong calls flip to the
        # mid band, the 0.60 good calls stay put. +0.10 flips the same 3, so
        # the tie resolves to the gentler move.
        assert s["shift"] == pytest.approx(0.05)
        assert s["wrong_flipped"] == 3
        assert s["good_flipped"] == 0
        # boundaries the wrong calls don't hug get no suggestion
        assert suggestions["arousal_low"]["shift"] is None

    def test_good_flips_count_against_a_shift(self):
        # Wrong calls at 0.28 but good calls at 0.25 (mid band, at the cut):
        # +0.05 flips 3 wrong / 0 good; -0.05 flips 0 wrong / 1 good.
        records = [record("wrong", 0.28, 0.5) for _ in range(3)] + [
            record("good", 0.24, 0.5, cell=("4", "mid", "high"))
        ]
        s = tuning_report.suggest_adjustments(records)["valence_high"]
        assert s["shift"] == pytest.approx(0.05)
        assert s["good_flipped"] == 0


def override_record(
    action,
    valence=0.6,
    arousal=0.5,
    cell=("4", "high", "high"),
    target_arousal=0.5,
    energy_action="hold",
    chosen=None,
):
    r = {
        "schema_version": 1,
        "ts": 1000.0,
        "action": action,
        "state": {
            "valence": valence,
            "arousal": arousal,
            "speech_ratio": 0.6,
            "playback_active": True,
        },
        "recommendation": {
            "matched_cell": list(cell),
            "boundaries_snapshot": dict(SNAPSHOT),
            "genre_pool": ["Pop"],
            "target_arousal": target_arousal,
            "energy_action": energy_action,
        },
        "now_playing": {
            "track": {"id": "spotify:track:x", "title": "T", "artist": "A",
                      "duration_s": 180.0, "playlist_id": "pl"},
            "progress_s": 30.0,
            "is_playing": True,
            "device_id": "dev",
        },
    }
    if chosen is not None:
        r["chosen"] = chosen
    return r


class TestOverridesAnalysis:
    def test_cell_counts_and_override_rate(self):
        records = (
            [override_record("skip") for _ in range(2)]
            + [override_record("played_through") for _ in range(6)]
            + [override_record("wrong_vibe", cell=("8", "mid", "mid"))]
        )
        counts = tuning_report.override_cell_counts(records)
        assert counts[("4", "high", "high")] == {"skip": 2, "played_through": 6}
        rate = tuning_report.override_rate(counts[("4", "high", "high")])
        assert rate == pytest.approx(0.25)  # 2 vetoes / 8 outcomes
        # a cell with only vetoes has rate 1.0, never a divide-by-zero
        assert tuning_report.override_rate(counts[("8", "mid", "mid")]) == 1.0

    def test_vetoes_cluster_near_boundaries_manual_excluded(self):
        records = (
            [override_record("skip", valence=0.28) for _ in range(2)]
            + [override_record("manual", valence=0.28,
                               chosen={"genre": "Jazz", "tier": "mid"})]
            + [override_record("played_through", valence=0.28)]
        )
        near = tuning_report.veto_boundary_proximity(records)
        assert near["valence_high"] == 2  # skips only; manual/thru excluded

    def test_tier_disagreement_counts_direction(self, monkeypatch):
        monkeypatch.delenv("RTR_PLAYBACK_TIER_LOW_MAX", raising=False)
        monkeypatch.delenv("RTR_PLAYBACK_TIER_HIGH_MIN", raising=False)
        records = [
            # derived: target_arousal 0.0 hold -> mid; human went high
            override_record("manual", target_arousal=0.0,
                            chosen={"genre": "Pop", "tier": "high"}),
            # derived: 0.0 raise -> high; human agrees
            override_record("manual", target_arousal=0.0, energy_action="raise",
                            chosen={"genre": "Pop", "tier": "high"}),
            # derived: 0.5 hold -> high; human went low
            override_record("manual", target_arousal=0.5,
                            chosen={"genre": "Pop", "tier": "low"}),
            # non-manual records contribute nothing
            override_record("skip"),
        ]
        tiers = tuning_report.tier_disagreement(records)
        assert tiers == {"higher": 1, "same": 1, "lower": 1}

    def test_manual_played_through_attributes_to_the_choice(self):
        record = override_record("played_through")
        record["recommendation"] = {"source": "manual", "genre": "Jazz", "tier": "mid"}
        counts = tuning_report.override_cell_counts([record])
        assert counts[("manual",)] == {"played_through": 1}


def played_through(staleness=4.0, duration=180.0, ts=1000.0, presence=None):
    r = override_record("played_through")
    r["ts"] = ts
    r["state"]["headcount_staleness_s"] = staleness
    r["now_playing"]["track"]["duration_s"] = duration
    if presence is not None:
        r["presence"] = presence
    return r


class TestPresenceGateRetro:
    """M5 section 4a: v2 stamps are honored, v1 records get the mirrored
    criterion, taps rescue, and gated lines leave every rate/proposal."""

    def test_stamped_record_is_honored_not_recomputed(self):
        # the stamp says absent even though the numbers read fresh — the
        # live gate saw something the retro pass can't; trust the stamp
        r = played_through(
            staleness=3.0, presence={"occupied": False, "basis": "absent"}
        )
        p = tuning_report.assess_presence_retro(r, [])
        assert p["occupied"] is False
        assert p["stamped"] is True

    def test_retro_fresh_handoff_middle_absent(self, monkeypatch):
        monkeypatch.delenv("RTR_PLAYBACK_PRESENCE_FRESH_S", raising=False)
        monkeypatch.delenv("RTR_PLAYBACK_PRESENCE_HANDOFF_S", raising=False)
        cases = [
            (3.2, 67.7, True, "fresh"),
            (244.0, 219.5, True, "handoff"),   # Minor Blues
            (211.7, 220.7, False, "absent"),   # Just the Way You Are
            (485.0, 357.8, False, "absent"),   # Thriller
        ]
        for staleness, duration, occupied, basis in cases:
            p = tuning_report.assess_presence_retro(
                played_through(staleness, duration), []
            )
            assert (p["occupied"], p["basis"], p["stamped"]) == (
                occupied, basis, False,
            ), (staleness, duration)

    def test_same_day_tap_inside_the_window_rescues(self):
        r = played_through(staleness=150.0, duration=180.0, ts=1000.0)
        assert tuning_report.assess_presence_retro(r, [900.0])["occupied"] is True
        assert tuning_report.assess_presence_retro(r, [700.0])["occupied"] is False

    def test_missing_staleness_without_tap_is_unknown(self):
        r = played_through(duration=180.0)
        del r["state"]["headcount_staleness_s"]
        p = tuning_report.assess_presence_retro(r, [])
        assert (p["occupied"], p["basis"]) == (False, "unknown")

    def test_gate_overrides_partitions_and_never_deletes(self):
        records = [
            played_through(staleness=3.0),                    # occupied
            played_through(staleness=400.0, duration=180.0),  # empty room
            override_record("skip"),                          # normal veto
        ]
        blind = override_record("wrong_vibe")
        blind["state"].update(
            {"speech_ratio": 0.0, "emotion_staleness_s": 218.9,
             "playback_active": True}
        )
        records.append(blind)
        usable, suspects, blind_out = tuning_report.gate_overrides(records, [])
        assert len(usable) == 2
        assert len(suspects) == 1 and suspects[0][1]["basis"] == "absent"
        assert blind_out == [blind]
        assert len(usable) + len(suspects) + len(blind_out) == len(records)

    def test_blind_signature_requires_all_three_conditions(self):
        base = override_record("skip")
        base["state"].update(
            {"speech_ratio": 0.0, "emotion_staleness_s": 200.0,
             "playback_active": True}
        )
        assert tuning_report.is_blind_veto(base) is True
        for patch in (
            {"playback_active": False},
            {"speech_ratio": 0.4},
            {"emotion_staleness_s": 5.0},
        ):
            r = override_record("skip")
            r["state"].update(
                {"speech_ratio": 0.0, "emotion_staleness_s": 200.0,
                 "playback_active": True, **patch}
            )
            assert tuning_report.is_blind_veto(r) is False, patch
        # played_through is judged by the presence gate, never this filter
        thru = played_through(staleness=3.0)
        thru["state"].update({"speech_ratio": 0.0, "emotion_staleness_s": 200.0})
        assert tuning_report.is_blind_veto(thru) is False


class TestStrongLabelProposals:
    def test_vetoes_read_as_wrong_thrus_as_good(self):
        # three skips hugging valence_high from above, two occupied
        # played_throughs comfortably inside the band — same shape as the
        # annotation fixture, so the same +0.05 suggestion must appear
        records = [override_record("skip", valence=0.28) for _ in range(3)] + [
            played_through(staleness=3.0),
            played_through(staleness=3.0),
        ]
        for r in records[3:]:
            r["state"]["valence"] = 0.60
        pseudo = tuning_report._as_pseudo_annotations(records)
        assert [p["verdict"] for p in pseudo] == ["wrong"] * 3 + ["good"] * 2
        s = tuning_report.suggest_adjustments(pseudo)["valence_high"]
        assert s["shift"] == pytest.approx(0.05)
        assert (s["wrong_flipped"], s["good_flipped"]) == (3, 0)

    def test_manual_picks_never_feed_boundary_proposals(self):
        records = [
            override_record("manual", valence=0.28,
                            chosen={"genre": "Jazz", "tier": "mid"})
        ]
        assert tuning_report._as_pseudo_annotations(records) == []

    def test_tier_cutoff_shift_scored_by_manual_agreement(self, monkeypatch):
        monkeypatch.delenv("RTR_PLAYBACK_TIER_LOW_MAX", raising=False)
        monkeypatch.delenv("RTR_PLAYBACK_TIER_HIGH_MIN", raising=False)
        # three picks at target_arousal 0.28 (derived: high) where the human
        # chose mid — lowering high_min can't help; RAISING it to 0.30 makes
        # the derived tier mid for all three
        records = [
            override_record("manual", target_arousal=0.28,
                            chosen={"genre": "Pop", "tier": "mid"})
            for _ in range(3)
        ]
        s = tuning_report.suggest_tier_cutoffs(records)["tier_high_min"]
        assert s["shift"] == pytest.approx(0.05)
        assert (s["agree_gained"], s["agree_lost"]) == (3, 0)

    def test_no_tier_shift_when_picks_already_agree(self, monkeypatch):
        monkeypatch.delenv("RTR_PLAYBACK_TIER_LOW_MAX", raising=False)
        monkeypatch.delenv("RTR_PLAYBACK_TIER_HIGH_MIN", raising=False)
        records = [
            override_record("manual", target_arousal=0.5,
                            chosen={"genre": "Pop", "tier": "high"})
        ]
        for s in tuning_report.suggest_tier_cutoffs(records).values():
            assert s["shift"] is None

    def test_pool_weighting_needs_the_selection_stamp(self):
        unstamped = override_record("skip")
        stamped = override_record("skip")
        stamped["now_playing"]["track"]["genre"] = "Jazz"
        thru = played_through(staleness=3.0)
        thru["now_playing"]["track"]["genre"] = "Jazz"
        manual = override_record("manual", chosen={"genre": "Pop", "tier": "mid"})
        manual["now_playing"]["track"]["genre"] = "Jazz"
        counts = tuning_report.pool_weighting_counts(
            [unstamped, stamped, thru, manual]
        )
        assert counts == {
            (("4", "high", "high"), "Jazz"): {"veto": 1, "thru": 1}
        }


class TestScript:
    def test_report_output_and_no_writes(self, log_dir, capsys, monkeypatch):
        def tree_snapshot():
            return {
                p: (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sorted(log_dir.rglob("*"))
            }

        before = tree_snapshot()
        monkeypatch.chdir(log_dir.parent)  # a write to a relative path would land here
        cwd_before = sorted(Path.cwd().rglob("*"))

        exit_code = tuning_report.main([str(log_dir / "*.jsonl")])

        assert exit_code == 0
        assert tree_snapshot() == before  # nothing modified
        assert sorted(Path.cwd().rglob("*")) == cwd_before  # nothing created
        out = capsys.readouterr().out
        assert "TUNING REPORT" in out
        assert "4 / high / high" in out
        assert "guard / no-speech" in out
        assert "valence_high" in out
        assert "shift +0.05" in out
        assert "would flip 3 wrong / 0 good" in out

    def test_no_files_is_a_clean_failure(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert tuning_report.main([str(tmp_path / "*.jsonl")]) == 1
        assert "nothing to report" in capsys.readouterr().out

    def test_overrides_section_prints_when_override_logs_exist(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "overrides"
        d.mkdir()
        records = (
            [override_record("skip", valence=0.28) for _ in range(2)]
            + [override_record("played_through") for _ in range(2)]
            + [override_record("manual", target_arousal=0.0,
                               chosen={"genre": "Jazz", "tier": "high"})]
        )
        (d / "2026-07-06.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
        exit_code = tuning_report.main(
            [str(tmp_path / "nope*.jsonl"), "--overrides", str(d / "*.jsonl")]
        )
        assert exit_code == 0  # overrides alone are reportable
        out = capsys.readouterr().out
        assert "OVERRIDES" in out
        assert "skipping sections 1-3" in out
        assert "overrides per rulebook cell" in out
        assert "4 / high / high" in out
        assert "valence_high" in out  # the 0.28 skips hug the boundary
        assert "tier disagreement" in out
        assert "higher 1" in out
        # M5 sections render: the gate audit line plus proposals 7-9. The
        # played_throughs here carry no staleness but same-file taps land
        # inside their windows, so nothing is excluded.
        assert "gated: 0 played_through excluded" in out
        assert "presence gate - flagged, never deleted" in out
        assert "boundary adjustments from the strong labels" in out
        assert "tier-cutoff proposals" in out
        assert "per-cell pool weighting" in out
        assert "collecting" in out  # no selection genre in M4-era records

    def test_empty_room_thrus_leave_the_rates(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("RTR_PLAYBACK_PRESENCE_FRESH_S", raising=False)
        monkeypatch.delenv("RTR_PLAYBACK_PRESENCE_HANDOFF_S", raising=False)
        d = tmp_path / "overrides"
        d.mkdir()
        records = [
            played_through(staleness=3.0, ts=1000.0),
            played_through(staleness=400.0, duration=180.0, ts=5000.0),
        ]
        (d / "2026-07-10.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
        exit_code = tuning_report.main(
            [str(tmp_path / "nope*.jsonl"), "--overrides", str(d / "*.jsonl")]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "gated: 1 played_through excluded (1 empty-room" in out
        assert "absent" in out and "(retro)" in out
        # the surviving completion still counts; the flagged one is listed,
        # not deleted — and the source file is untouched
        assert (d / "2026-07-10.jsonl").read_text(encoding="utf-8").count(
            "played_through"
        ) == 2
