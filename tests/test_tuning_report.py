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
