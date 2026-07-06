"""Dashboard smoke tests: websocket frames + annotation round-trip.

Synthetic RoomState only — no mic, no models, no network. The bridge is fed
directly (as the engine would); the TestClient exercises the real app.
"""

import json

import pytest
from fastapi.testclient import TestClient

from conftest import make_state
from dashboard import DashboardBridge, build_record, create_app
from mapping import Mapper, MappingConfig
from sensing.headcount import HeadcountReading
from sensing.state import HeadcountBucket


@pytest.fixture
def bridge():
    return DashboardBridge(Mapper(MappingConfig(min_dwell_s=0.0)))


@pytest.fixture
def client(bridge, tmp_path):
    app = create_app(
        bridge,
        annotations_dir=tmp_path / "annotations",
        overrides_dir=tmp_path / "overrides",
    )
    with TestClient(app) as c:
        yield c


class TestWebsocket:
    def test_state_and_recommendation_frames(self, bridge, client):
        state = make_state(timestamp=1000.0)
        bridge.on_state(state)  # engine-thread entry point

        with client.websocket_connect("/ws") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "state"
            assert frame["speech_ratio"] == state.speech_ratio
            assert frame["headcount_bucket"] == "4"
            # dashboard-added extras exist even without a hosted engine
            for extra in (
                "headcount_crowd_weight",
                "headcount_dispersion",
                "headcount_fragmentation",
                "headcount_smoothed_log2",
            ):
                assert extra in frame

            rec = ws.receive_json()
            assert rec["type"] == "recommendation"
            assert rec["matched_cell"] == ["4", "high", "high"]
            assert rec["genre_pool"] == ["Pop"]
            assert rec["schema_version"] == 1
            assert rec["boundaries_snapshot"]["valence_high"] == 0.25

    def test_history_replay_fills_timeline(self, bridge, client):
        for i in range(5):
            bridge.on_state(make_state(timestamp=1000.0 + 2 * i))
        with client.websocket_connect("/ws") as ws:
            frames = [ws.receive_json() for _ in range(6)]
        states = [f for f in frames if f["type"] == "state"]
        assert len(states) == 5
        assert states[0]["timestamp"] == 1000.0  # oldest first
        assert frames[-1]["type"] == "recommendation"  # current rec last


class TestEngineExtras:
    def test_hosted_engine_reading_attaches_observability_fields(self):
        """M4 deliverable 3: dispersion/fragmentation/smoothed_log2 ride the
        frame exactly like crowd_weight, rounded for the wire."""

        class FakeWorker:
            def latest(self, now):
                reading = HeadcountReading(
                    bucket=HeadcountBucket.PAIR,
                    confidence=0.8,
                    raw_clusters=2,
                    crowd_weight=0.12345,
                    dispersion=0.45678,
                    fragmentation=0.25,
                    smoothed_log2=1.23456,
                    at=0.0,
                )
                return reading, 0.0

        class FakeEngine:
            emotion_status = "ready"
            headcount_status = "ready"
            headcount = FakeWorker()

        bridge = DashboardBridge(
            Mapper(MappingConfig(min_dwell_s=0.0)), engine=FakeEngine()
        )
        bridge.on_state(make_state(timestamp=1000.0))
        frame = bridge.snapshot()[0][-1]
        assert frame["headcount_crowd_weight"] == 0.123
        assert frame["headcount_dispersion"] == 0.457
        assert frame["headcount_fragmentation"] == 0.25
        assert frame["headcount_smoothed_log2"] == 1.235
        assert frame["headcount_status"] == "ready"


class TestAnnotations:
    def test_post_writes_displayed_snapshot(self, bridge, client, tmp_path):
        bridge.on_state(make_state(timestamp=1000.0))
        with client.websocket_connect("/ws") as ws:
            displayed_state = ws.receive_json()
            displayed_rec = ws.receive_json()

        # The page strips the frame-type tag and sends what it displayed.
        displayed_state.pop("type")
        displayed_rec.pop("type")
        res = client.post(
            "/annotations",
            json={
                "verdict": "good",
                "state": displayed_state,
                "recommendation": displayed_rec,
            },
        )
        assert res.status_code == 201

        files = list((tmp_path / "annotations").glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["schema_version"] == 1
        assert record["verdict"] == "good"
        # the log holds EXACTLY what was displayed
        assert record["state"] == displayed_state
        assert record["recommendation"] == displayed_rec
        # attribution fields present in the written record
        assert record["recommendation"]["matched_cell"] == ["4", "high", "high"]
        assert "valence_high" in record["recommendation"]["boundaries_snapshot"]

    def test_wrong_verdict_appends_second_line(self, bridge, client, tmp_path):
        bridge.on_state(make_state(timestamp=1000.0))
        with client.websocket_connect("/ws") as ws:
            state = ws.receive_json()
            rec = ws.receive_json()
        for verdict in ("good", "wrong"):
            res = client.post(
                "/annotations",
                json={"verdict": verdict, "state": state, "recommendation": rec},
            )
            assert res.status_code == 201
        lines = (
            list((tmp_path / "annotations").glob("*.jsonl"))[0]
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()
        )
        assert [json.loads(l)["verdict"] for l in lines] == ["good", "wrong"]

    def test_invalid_verdict_rejected(self, client):
        res = client.post(
            "/annotations",
            json={"verdict": "meh", "state": {"x": 1}, "recommendation": {"y": 2}},
        )
        assert res.status_code == 422

    def test_missing_recommendation_rejected(self, client):
        res = client.post(
            "/annotations",
            json={"verdict": "good", "state": {"x": 1}, "recommendation": {}},
        )
        assert res.status_code == 400

    def test_build_record_round_trip(self):
        record = build_record("wrong", {"valence": 0.1}, {"matched_cell": ["4"]}, ts=5.0)
        parsed = json.loads(json.dumps(record))
        assert parsed == record
        assert parsed["schema_version"] == 1
        assert parsed["ts"] == 5.0


class TestOverridesEndpoint:
    """M4 deliverable 2: the label is banked BEFORE the playback action is
    attempted — a dead provider degrades music, never loses a record."""

    _NOW_PLAYING = {
        "track": {
            "id": "t1",
            "title": "Song",
            "artist": "A",
            "duration_s": 180.0,
            "playlist_id": "pl-pop-high",
        },
        "progress_s": 10.0,
        "is_playing": True,
        "device_id": "dev-1",
    }

    def _payload(self, bridge, client, action="skip", **extra):
        bridge.on_state(make_state(timestamp=1000.0))
        with client.websocket_connect("/ws") as ws:
            state = ws.receive_json()
            rec = ws.receive_json()
        state.pop("type")
        rec.pop("type")
        return {
            "action": action,
            "state": state,
            "recommendation": rec,
            "now_playing": self._NOW_PLAYING,
            **extra,
        }

    def test_shadow_mode_records_without_acting(self, bridge, client, tmp_path):
        res = client.post("/overrides", json=self._payload(bridge, client))
        assert res.status_code == 201
        body = res.json()
        assert body["ok"] is True
        assert body["action_ok"] is False  # no playback controller wired

        files = list((tmp_path / "overrides").glob("*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["schema_version"] == 1
        assert record["action"] == "skip"
        assert record["now_playing"]["track"]["id"] == "t1"
        assert record["recommendation"]["matched_cell"] == ["4", "high", "high"]
        assert "playback_active" in record["state"]  # contamination-taggable

    def test_manual_requires_chosen(self, bridge, client):
        res = client.post(
            "/overrides", json=self._payload(bridge, client, action="manual")
        )
        assert res.status_code == 400

    def test_manual_with_chosen_records_it(self, bridge, client, tmp_path):
        payload = self._payload(
            bridge, client, action="manual", chosen={"genre": "Jazz", "tier": "mid"}
        )
        assert client.post("/overrides", json=payload).status_code == 201
        record = json.loads(
            list((tmp_path / "overrides").glob("*.jsonl"))[0].read_text(
                encoding="utf-8"
            )
        )
        assert record["chosen"] == {"genre": "Jazz", "tier": "mid"}

    def test_unknown_action_rejected(self, bridge, client):
        res = client.post(
            "/overrides", json=self._payload(bridge, client, action="louder")
        )
        assert res.status_code == 422  # pydantic Literal

    def test_action_dispatches_to_playback_controller(self, bridge, tmp_path):
        from playback import Track

        class StubPlayback:
            def skip(self):
                return Track(
                    id="t2", title="Next", artist="B",
                    duration_s=200.0, playlist_id="pl",
                )

        app = create_app(
            bridge,
            annotations_dir=tmp_path / "annotations",
            overrides_dir=tmp_path / "overrides",
            playback=StubPlayback(),
        )
        with TestClient(app) as client:
            res = client.post(
                "/overrides", json=self._payload(bridge, client)
            )
        assert res.status_code == 201
        body = res.json()
        assert body["action_ok"] is True
        assert body["acted_track"]["id"] == "t2"


class TestIndexPage:
    def test_served_with_all_components(self, client):
        res = client.get("/")
        assert res.status_code == 200
        html = res.text
        for marker in (
            "SHADOW MODE",
            "Good call",
            "Wrong call",
            "quadrant",
            "timeline",
            "DISCONNECTED",
            "emostale",
            "hcstale",
        ):
            assert marker in html
