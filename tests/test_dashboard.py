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


@pytest.fixture
def bridge():
    return DashboardBridge(Mapper(MappingConfig(min_dwell_s=0.0)))


@pytest.fixture
def client(bridge, tmp_path):
    app = create_app(bridge, annotations_dir=tmp_path / "annotations")
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
            assert "headcount_crowd_weight" in frame

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
