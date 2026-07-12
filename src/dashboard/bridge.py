"""DashboardBridge: engine consumer -> websocket frames.

Runs `on_state` on the engine thread; websocket clients live on the asyncio
loop. Frames cross over via per-client asyncio.Queues and
`loop.call_soon_threadsafe` — the engine never blocks on a slow client
(full queues drop frames for that client only).

Server-side state is deliberately minimal: the rolling frame history needed
to fill the timeline on page load, plus the current recommendation.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

from mapping import Mapper
from sensing.state import RoomState

log = logging.getLogger(__name__)


class EnvelopeAdvisory:
    """Blind-signature detector (M5 deliverable 3, the non-ML piece): while
    the system's own output is audible, sustained loudness well over the
    room's quiet floor with zero certified speech means the music is
    out-reading the room — the 2026-07-10 full-volume limit cycle. The
    dashboard renders the verdict as a "turn it down" banner; nothing else
    acts on it. Hop-streak hysteresis so a single quiet-in-the-groove
    window can't flap the banner.

    The reference is a quiet-anchored floor, not the live rolling floor:
    the engine's noise floor deliberately absorbs "whatever the room
    sounds like when nobody is talking" — including our own playback — so
    comparing against it self-erases within one EMA tau of sustained
    loudness (observed live 2026-07-11: floor chased a 90%-volume ramp and
    kept the gap under threshold; the same chase would have blanked the
    banner ~60 s into the 07-10 blind window this class exists to catch).
    We remember the floor from playback-inactive frames and judge loud
    playback against that. A session that starts mid-playback has no
    anchor yet and falls back to the live floor (step-detection only)
    until its first quiet, playback-free stretch.

    M6: the anchor persists to disk (parties start with music already
    on — 2026-07-11's session did). A saved anchor younger than
    `anchor_max_age_s` seeds the session; older ones are ignored, since
    room floors drift with AC and weather. Persistence is best-effort:
    a failed read/write logs and never touches the verdict."""

    def __init__(
        self,
        db_over_floor: float = 10.0,
        speech_eps: float = 0.05,
        hops: int = 10,
        anchor_path: Path | str | None = None,
        anchor_max_age_s: float = 43200.0,
    ):
        self.db_over_floor = db_over_floor
        self.speech_eps = speech_eps
        self.hops = max(1, hops)
        self._streak = 0
        self._anchor: float | None = None
        self._anchor_path = None if anchor_path is None else Path(anchor_path)
        self._anchor_max_age_s = anchor_max_age_s
        self._saved_anchor: float | None = None
        self._load_anchor()

    def update(self, frame: dict) -> bool:
        floor = frame.get("noise_floor_dbfs")
        if frame.get("playback_active") is not True:
            if floor is not None:
                self._anchor = floor
                self._persist_anchor()
            self._streak = 0
            return False
        reference = self._anchor if self._anchor is not None else floor
        blind = (
            reference is not None
            and frame.get("loudness_dbfs", reference) - reference
            >= self.db_over_floor
            and frame.get("speech_ratio", 1.0) <= self.speech_eps
        )
        self._streak = self._streak + 1 if blind else 0
        return self._streak >= self.hops

    def _load_anchor(self) -> None:
        if self._anchor_path is None or not self._anchor_path.exists():
            return
        try:
            data = json.loads(self._anchor_path.read_text(encoding="utf-8"))
            age = time.time() - float(data["ts"])
            if 0 <= age <= self._anchor_max_age_s:
                self._anchor = float(data["anchor_dbfs"])
                self._saved_anchor = self._anchor
                log.info(
                    "advisory anchor restored: %.1f dBFS (%.0f s old)",
                    self._anchor, age,
                )
            else:
                log.info("saved advisory anchor is stale (%.0f s); ignoring", age)
        except Exception:
            log.exception("advisory anchor cache unreadable; starting unanchored")

    def _persist_anchor(self) -> None:
        # Throttle on meaningful change — quiet stretches update the anchor
        # every hop, and a 0.5 dB wobble isn't worth a disk write.
        if self._anchor_path is None or self._anchor is None:
            return
        if (
            self._saved_anchor is not None
            and abs(self._anchor - self._saved_anchor) < 0.5
        ):
            return
        try:
            self._anchor_path.parent.mkdir(parents=True, exist_ok=True)
            self._anchor_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "anchor_dbfs": round(self._anchor, 1),
                        "ts": time.time(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._saved_anchor = self._anchor
        except Exception:
            log.exception("failed to persist advisory anchor; continuing")


class DashboardBridge:
    def __init__(
        self,
        mapper: Mapper,
        history_maxlen: int = 300,
        engine=None,
        playback=None,
        advisory: EnvelopeAdvisory | None = None,
    ):
        self._mapper = mapper
        self.engine = engine  # optional; read for regime extras + statuses
        # Optional PlaybackController (M4): receives every Mapper emission
        # (non-blocking slot handoff) and contributes now-playing extras.
        self.playback = playback
        # Envelope advisory (M5): evaluated per frame on the engine thread;
        # None (shadow mode) pins the frame field to False.
        self.advisory = advisory
        self._history: deque[dict] = deque(maxlen=history_maxlen)
        self._current_rec: dict | None = None
        self._clients: dict[int, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._ids = itertools.count()
        self._lock = threading.Lock()

    # -- engine side (engine thread) ----------------------------------------

    def on_state(self, state: RoomState) -> None:
        frame = {
            "type": "state",
            **state.to_dict(),
            **self._engine_extras(),
            **self._playback_extras(),
        }
        frame["envelope_advisory"] = (
            self.advisory.update(frame) if self.advisory is not None else False
        )
        rec = self._mapper.update(state)
        if rec is not None and self.playback is not None:
            try:
                self.playback.on_recommendation(rec)  # slot handoff, no I/O
            except Exception:
                log.exception("playback controller rejected a recommendation")
        rec_frame = {"type": "recommendation", **rec.to_dict()} if rec else None
        with self._lock:
            self._history.append(frame)
            if rec_frame is not None:
                self._current_rec = rec_frame
            clients = list(self._clients.values())
        for queue, loop in clients:
            self._offer(queue, loop, frame)
            if rec_frame is not None:
                self._offer(queue, loop, rec_frame)

    def _engine_extras(self) -> dict:
        """Dashboard-added frame fields (NOT RoomState schema): regime info
        from the hosted engine's HeadcountReading, plus worker statuses.
        dispersion/fragmentation/smoothed_log2 are the M4 observability
        fields (see M4-PROPOSAL deliverable 3); riding the frame means every
        annotation/override snapshot carries them for free."""
        extras = {
            "headcount_crowd_weight": None,
            "headcount_raw_clusters": None,
            "headcount_dispersion": None,
            "headcount_fragmentation": None,
            "headcount_smoothed_log2": None,
            "headcount_recent_raw_log2": None,
            "emotion_status": None,
            "headcount_status": None,
        }
        engine = self.engine
        if engine is None:
            return extras
        extras["emotion_status"] = engine.emotion_status
        extras["headcount_status"] = engine.headcount_status
        if engine.headcount is not None:
            reading, _ = engine.headcount.latest(time.monotonic())
            if reading is not None:
                extras["headcount_crowd_weight"] = round(reading.crowd_weight, 3)
                extras["headcount_raw_clusters"] = reading.raw_clusters
                extras["headcount_dispersion"] = round(reading.dispersion, 3)
                extras["headcount_fragmentation"] = round(reading.fragmentation, 3)
                extras["headcount_smoothed_log2"] = round(reading.smoothed_log2, 3)
                extras["headcount_recent_raw_log2"] = [
                    round(x, 3) for x in reading.recent_raw_log2
                ]
        return extras

    def _playback_extras(self) -> dict:
        """Now-playing frame fields (M4 deliverable 4). Shadow mode is the
        first-class default — 'shadow' is what no controller looks like."""
        if self.playback is None:
            return {
                "playback_status": "shadow",
                "playback_error": None,
                "now_playing": None,
                "queued_track": None,
            }
        return self.playback.snapshot()

    @staticmethod
    def _offer(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, frame: dict):
        def put():
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass  # slow client: drop the frame, never block the engine

        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass  # loop already closed; the ws handler will unregister

    # -- websocket side (asyncio loop) ---------------------------------------

    def register(
        self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop
    ) -> int:
        with self._lock:
            token = next(self._ids)
            self._clients[token] = (queue, loop)
        return token

    def unregister(self, token: int) -> None:
        with self._lock:
            self._clients.pop(token, None)

    def snapshot(self) -> tuple[list[dict], dict | None]:
        """(history frames oldest-first, current recommendation frame)."""
        with self._lock:
            return list(self._history), self._current_rec
