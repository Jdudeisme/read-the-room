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
import logging
import threading
import time
from collections import deque

from mapping import Mapper
from sensing.state import RoomState

log = logging.getLogger(__name__)


class DashboardBridge:
    def __init__(self, mapper: Mapper, history_maxlen: int = 300, engine=None):
        self._mapper = mapper
        self.engine = engine  # optional; read for regime extras + statuses
        self._history: deque[dict] = deque(maxlen=history_maxlen)
        self._current_rec: dict | None = None
        self._clients: dict[int, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._ids = itertools.count()
        self._lock = threading.Lock()

    # -- engine side (engine thread) ----------------------------------------

    def on_state(self, state: RoomState) -> None:
        frame = {"type": "state", **state.to_dict(), **self._engine_extras()}
        rec = self._mapper.update(state)
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
        return extras

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
