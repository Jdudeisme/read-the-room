"""Dominance ladder analyzer: the pure scoring that the run sheet's decision
rule reads. No audio, no VAD model — synthetic high-band shares only."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "analyze_dominance_wav.py"
spec = importlib.util.spec_from_file_location("analyze_dominance_wav", SCRIPT)
adw = importlib.util.module_from_spec(spec)
sys.modules["analyze_dominance_wav"] = adw
spec.loader.exec_module(adw)


def make_take(kind, label, highs, eligible=None):
    t = adw.Take(kind, label, Path(f"{label}.wav"))
    t.high = list(highs)
    t.eligible = list(eligible) if eligible is not None else [True] * len(highs)
    return t


def test_parse_take_kind_label_path():
    assert adw.parse_take("mix:T1-76=data/x.wav") == ("mix", "T1-76", Path("data/x.wav"))
    kind, label, path = adw.parse_take("speech=data/C1.wav")
    assert (kind, label) == ("speech", "C1")


@pytest.mark.parametrize("bad", ["noequals.wav", "voice:x=a.wav"])
def test_parse_take_rejects_malformed(bad):
    with pytest.raises(ValueError):
        adw.parse_take(bad)


def test_knot_metrics_uses_engine_ramp():
    # lo 0.02, hi 0.06: 0.02 -> m 0 (clean), 0.03 -> 0.25 (bankable, not
    # clean), 0.06 -> 1.0, 0.022 -> 0.05 (clean).
    km = adw.knot_metrics(np.array([0.02, 0.022, 0.03, 0.06]), 0.02, 0.06, 0.1, 0.25)
    assert km["n"] == 4
    assert km["clean"] == 0.5
    assert km["bankable"] == 0.5
    assert km["m_mean"] == pytest.approx((0 + 0.05 + 0.25 + 1.0) / 4, abs=1e-3)


def test_knot_metrics_empty_population():
    assert adw.knot_metrics(np.array([]), 0.02, 0.05, 0.1, 0.25) == {"n": 0}


def test_ineligible_windows_are_excluded():
    t = make_take("mix", "m", [0.01, 0.09], eligible=[True, False])
    assert t.eligible_high().tolist() == [0.01]


def test_rule_inputs_worst_control_and_hi_mix():
    takes = [
        make_take("speech", "C1", np.linspace(0.010, 0.020, 21)),
        make_take("speech", "C2", np.linspace(0.010, 0.030, 21)),  # worst tail
        make_take("mix", "T1-MX76", [0.05] * 10),
        make_take("mix", "T2-MX76", [0.07] * 10),
        make_take("mix", "T1-MX32", [0.001] * 10),  # not an HI take
    ]
    rule = adw.rule_inputs(takes, "MX76")
    assert rule["lo_candidate"] == adw.dist(np.linspace(0.010, 0.030, 21))["p95"]
    assert rule["hi_candidate"] == 0.06  # pooled p50 of the two 76 takes
    assert rule["hi_mix_takes"] == 2
    assert rule["separable"] is True


def test_rule_inputs_flags_overlap_as_not_separable():
    takes = [
        make_take("speech", "C1", [0.06] * 10),
        make_take("mix", "T1-MX76", [0.04] * 10),
    ]
    assert adw.rule_inputs(takes, "MX76")["separable"] is False


def test_rule_inputs_without_hi_takes_does_not_judge():
    takes = [make_take("speech", "C1", [0.02] * 10)]
    assert adw.rule_inputs(takes, "MX76")["separable"] is None
