"""Presence gate (M5 deliverable 1) + envelope advisory (deliverable 3).

The acceptance fixtures below carry the REAL numbers from the 2026-07
corpus (docs/M5-PROPOSAL.md): the six named empty-room completions must
read absent, the four known-real part (c) completions must read occupied —
including Minor Blues, the quiet-listener warm handoff. No real data files
are read; the numbers are frozen here as the criterion's contract.
"""

import json

import pytest

from dashboard import EnvelopeAdvisory, PresenceGate, assess_presence
from dashboard.overrides import make_played_through_sink


class TestAssessPresence:
    FRESH, HANDOFF = 60.0, 30.0

    def basis(self, staleness, duration, tap=False):
        return assess_presence(staleness, duration, self.FRESH, self.HANDOFF, tap)

    def test_fresh_speech_near_completion(self):
        assert self.basis(3.2, 67.7) == "fresh"

    def test_warm_handoff_silent_listener(self):
        # zero speech during the track, room certified just before it began
        assert self.basis(244.0, 219.5) == "handoff"

    def test_middle_zone_is_the_departure_signature(self):
        # speech existed mid-window, then silence long before the end
        assert self.basis(211.7, 220.7) == "absent"

    def test_stale_before_the_track_even_began(self):
        assert self.basis(485.0, 357.8) == "absent"

    def test_tap_rescues_the_middle_zone(self):
        assert self.basis(211.7, 220.7, tap=True) == "tap"

    def test_fresh_wins_over_tap_as_the_basis_label(self):
        assert self.basis(3.0, 180.0, tap=True) == "fresh"

    def test_no_staleness_signal_at_all(self):
        assert self.basis(None, 180.0) == "unknown"
        assert self.basis(None, 180.0, tap=True) == "tap"

    def test_unknown_duration_never_reaches_handoff(self):
        # the controller never emits completions for unknown durations, but
        # the criterion must not crash or guess if handed one
        assert self.basis(100.0, None) == "absent"
        assert self.basis(100.0, 0) == "absent"

    def test_boundaries_are_inclusive(self):
        assert self.basis(60.0, 300.0) == "fresh"
        assert self.basis(300.0, 300.0) == "handoff"
        assert self.basis(330.0, 300.0) == "handoff"
        assert self.basis(330.1, 300.0) == "absent"


# (staleness_s at completion, track duration_s) from the real corpus.
CORPUS_ACCEPTANCE = {
    # the six named empty-room lines (FIELD-NOTES 2026-07-10) — must flag
    "Thriller": (485.0, 357.753, False),
    "Just the Way You Are": (211.7, 220.734, False),
    "Just In Time": (323.7, 109.493, False),
    "Mumbles": (115.6, 121.266, False),
    "All The Things You Are": (289.6, 176.866, False),
    "Atrebor": (121.7, 136.68, False),
    # the four known-real part (c) completions (2026-07-06) — must survive
    "Game Over": (3.2, 67.709, True),
    "Earth Song": (5.2, 302.187, True),
    "Rhymes Like Dimes": (20.0, 258.613, True),
    "Minor Blues (quiet listener)": (244.0, 219.493, True),
    # the solo close-out, founder present (2026-07-10) — must survive
    "4 Lieder": (54.0, 265.52, True),
}


class TestCorpusAcceptance:
    @pytest.mark.parametrize(
        "title,staleness,duration,occupied",
        [(t, s, d, o) for t, (s, d, o) in CORPUS_ACCEPTANCE.items()],
    )
    def test_criterion_matches_the_founder_labels(
        self, title, staleness, duration, occupied
    ):
        # no taps landed inside any of these windows (verified offline)
        basis = assess_presence(staleness, duration, 60.0, 30.0, False)
        assert (basis in ("fresh", "handoff", "tap")) is occupied, title

    def test_paper_moon_is_the_known_false_negative(self):
        """It's Only A Paper Moon (empty porch) is indistinguishable from
        Minor Blues (quiet listener) on every recorded signal — the handoff
        clause knowingly saves both. If this ever starts failing, the
        criterion changed and docs/M5-PROPOSAL.md needs re-deriving."""
        assert assess_presence(221.6, 205.0, 60.0, 30.0, False) == "handoff"


class TestPresenceGate:
    def test_stamp_builds_the_evidence_block(self):
        gate = PresenceGate(fresh_s=60.0, handoff_s=30.0)
        block = gate.stamp(
            {"headcount_staleness_s": 3.2},
            {"track": {"duration_s": 67.7}},
            ts=1000.0,
        )
        assert block == {
            "occupied": True,
            "basis": "fresh",
            "staleness_s": 3.2,
            "track_duration_s": 67.7,
            "fresh_s": 60.0,
            "handoff_s": 30.0,
            "last_tap_age_s": None,
        }

    def test_emotion_staleness_backstops_headcount(self):
        gate = PresenceGate()
        block = gate.stamp(
            {"headcount_staleness_s": None, "emotion_staleness_s": 10.0},
            {"track": {"duration_s": 200.0}},
        )
        assert block["basis"] == "fresh"
        assert block["staleness_s"] == 10.0

    def test_tap_inside_the_play_window_is_presence(self):
        gate = PresenceGate()
        gate.note_tap(ts=950.0)  # 50s before completion of a 180s track
        block = gate.stamp(
            {"headcount_staleness_s": 150.0},  # middle zone on its own
            {"track": {"duration_s": 180.0}},
            ts=1000.0,
        )
        assert block["basis"] == "tap"
        assert block["occupied"] is True
        assert block["last_tap_age_s"] == 50.0

    def test_tap_before_the_track_began_does_not_count(self):
        gate = PresenceGate()
        gate.note_tap(ts=100.0)  # 900s ago, track is 180s long
        block = gate.stamp(
            {"headcount_staleness_s": 150.0},
            {"track": {"duration_s": 180.0}},
            ts=1000.0,
        )
        assert block["basis"] == "absent"
        assert block["occupied"] is False

    def test_no_signals_at_all_is_unknown(self):
        block = PresenceGate().stamp({}, {"track": {}}, ts=1000.0)
        assert block["basis"] == "unknown"
        assert block["occupied"] is False


class FakeBridge:
    def __init__(self, frame):
        self._frame = frame

    def snapshot(self):
        return ([self._frame] if self._frame else []), None


class TestPlayedThroughSink:
    NOW_PLAYING = {
        "track": {"id": "t1", "title": "Song", "artist": "A",
                  "duration_s": 180.0, "playlist_id": "pl",
                  "genre": "Jazz", "tier": "mid"},
        "progress_s": 178.0,
        "is_playing": True,
        "device_id": "dev",
    }

    def _run_sink(self, tmp_path, frame, gate=None):
        sink = make_played_through_sink(FakeBridge(frame), tmp_path, gate)
        sink(self.NOW_PLAYING, {"matched_cell": ["4", "high", "high"]})
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        return json.loads(files[0].read_text(encoding="utf-8"))

    def test_occupied_completion_stamped_schema_v2(self, tmp_path):
        frame = {"type": "state", "headcount_staleness_s": 4.0, "valence": 0.5}
        record = self._run_sink(tmp_path, frame, PresenceGate())
        assert record["schema_version"] == 2
        assert record["action"] == "played_through"
        assert record["presence"]["occupied"] is True
        assert record["presence"]["basis"] == "fresh"
        assert "type" not in record["state"]  # frame-type tag stripped
        assert record["now_playing"]["track"]["genre"] == "Jazz"

    def test_empty_room_completion_still_written_marked_absent(self, tmp_path):
        frame = {"type": "state", "headcount_staleness_s": 400.0}
        record = self._run_sink(tmp_path, frame, PresenceGate())
        assert record["presence"]["occupied"] is False
        assert record["presence"]["basis"] == "absent"

    def test_no_gate_writes_unstamped_record(self, tmp_path):
        record = self._run_sink(tmp_path, {"type": "state"}, gate=None)
        assert "presence" not in record

    def test_no_frames_yet_records_unavailable_state(self, tmp_path):
        record = self._run_sink(tmp_path, None, PresenceGate())
        assert record["state"] == {"unavailable": True}
        assert record["presence"]["basis"] == "unknown"


class TestEnvelopeAdvisory:
    BLIND = {
        "playback_active": True,
        "noise_floor_dbfs": -44.0,
        "loudness_dbfs": -22.0,  # 22 dB over the floor
        "speech_ratio": 0.0,
    }

    def test_sustained_blind_signature_raises_the_advisory(self):
        adv = EnvelopeAdvisory(db_over_floor=10.0, speech_eps=0.05, hops=3)
        assert [adv.update(dict(self.BLIND)) for _ in range(4)] == [
            False, False, True, True,
        ]

    def test_certified_speech_clears_the_streak(self):
        adv = EnvelopeAdvisory(hops=2)
        adv.update(dict(self.BLIND))
        assert adv.update({**self.BLIND, "speech_ratio": 0.4}) is False
        assert adv.update(dict(self.BLIND)) is False  # streak restarted
        assert adv.update(dict(self.BLIND)) is True

    def test_quiet_playback_never_triggers(self):
        # music at moderate volume sits near the floor it created
        adv = EnvelopeAdvisory(hops=1)
        frame = {**self.BLIND, "loudness_dbfs": -40.0}
        assert adv.update(frame) is False

    def test_shadow_mode_never_triggers(self):
        adv = EnvelopeAdvisory(hops=1)
        assert adv.update({**self.BLIND, "playback_active": False}) is False

    def test_unseeded_noise_floor_never_triggers(self):
        adv = EnvelopeAdvisory(hops=1)
        assert adv.update({**self.BLIND, "noise_floor_dbfs": None}) is False
