"""scripts/record_frames.py: frame filtering and the M8-03 binding check.
Offline; no dashboard, no websocket."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "record_frames", Path(__file__).resolve().parents[1] / "scripts" / "record_frames.py"
)
rf = importlib.util.module_from_spec(_spec)
sys.modules["record_frames"] = rf
_spec.loader.exec_module(rf)

A, B = "spotify:track:A", "spotify:track:B"


def _frame(t, staleness, playing=None, correction_track=None):
    return {
        "type": "state",
        "timestamp": t,
        "emotion_staleness_s": staleness,
        "playback_active": playing is not None,
        "playback_track_id": playing,
        "emotion_correction": (
            None if correction_track is None
            else {"track_id": correction_track, "basis": "pull", "refs": 5}
        ),
    }


def test_only_state_frames_are_written():
    fh = io.StringIO()
    assert rf.write_state_frame(json.dumps({"type": "recommendation"}), fh) == 0
    assert rf.write_state_frame("not json", fh) == 0
    assert rf.write_state_frame(json.dumps(_frame(1.0, 0.4)), fh) == 1
    assert [json.loads(line)["type"] for line in fh.getvalue().splitlines()] == ["state"]


def test_bound_corrections_pass_across_a_boundary_and_a_stop():
    frames = [
        _frame(100.0, 0.4, A, A),   # reading 1 lands under A
        _frame(102.0, 2.4, B, A),   # B is playing; still A's reading -> A
        _frame(104.0, 0.4, B, B),   # reading 2 lands under B
        _frame(106.0, 2.4, None, B),  # playback stopped; B's reading stays bound
        _frame(108.0, 0.4, None, None),  # reading 3, playback off, uncorrected
    ]
    r = rf.check_correction_binding(frames)
    assert r.passed
    assert r.readings == 3
    assert (r.across_boundary, r.after_stop) == (1, 1)


def test_pre_m8_03_behavior_is_flagged():
    frames = [
        _frame(100.0, 0.4, A, A),
        _frame(102.0, 2.4, B, B),  # old behavior: A's reading corrected with B
        _frame(104.0, 4.4, None, None),
    ]
    r = rf.check_correction_binding(frames)
    assert not r.passed
    assert r.violations[0]["correction_track"] == B
    assert r.violations[0]["landing_track"] == A


def test_correction_on_a_reading_taken_without_music_is_a_violation():
    r = rf.check_correction_binding([_frame(100.0, 0.4, None, A)])
    assert not r.passed


def test_rounding_jitter_does_not_split_one_reading():
    frames = [_frame(100.0, 0.4, A, A), _frame(102.1, 2.4, B, A), _frame(103.9, 4.4, B, A)]
    r = rf.check_correction_binding(frames)
    assert r.readings == 1 and r.passed
