"""PlaybackController: gentle-DJ policy between Mapper emissions and a provider.

Runs in the dashboard process as a consumer of Mapper emissions — same
process as DashboardBridge, new subscriber, engine untouched. All provider
I/O happens on the controller's own worker thread (latest-wins slot, the
EmotionWorker pattern): a slow or dead provider can never stall the
engine/bridge heartbeat, and the sensing side reads playback state from a
cache, never over the network.

Gentle-DJ policy (M4 proposal deliverable 1):
- a new Recommendation NEVER interrupts the playing track; it replaces the
  queued next track, and transitions happen on track boundaries;
- the one exception is bootstrap: when nothing is audible at all, the
  selected track starts immediately (there is nothing to interrupt);
- guard recommendations (insufficient signal) hold — no selection, no queue;
- volume is never touched; energy moves only through tier selection.

Failure isolation: any ProviderError logs, flips status to "degraded" (the
dashboard surfaces it and presents shadow mode), and the next event retries.
The cached now-playing state is RETAINED through provider errors: if we
can't reach the provider, the device is usually still playing (token expiry,
rate limit), and for contamination tagging a stale "active" is safer than a
false "clean" — untagged contaminated evidence poisons the corpus, while
over-tagged clean evidence is merely discounted.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from mapping.mapper import GUARD_CELL

from .config import PlaybackConfig
from .provider import NowPlaying, PlaybackProvider, ProviderError, Track
from .selector import TrackSelector, derive_tier

if TYPE_CHECKING:
    from mapping.mapper import Recommendation

log = logging.getLogger(__name__)


class PlaybackController:
    """status: starting -> active | degraded -> stopped."""

    def __init__(
        self,
        provider: PlaybackProvider,
        selector: TrackSelector,
        config: PlaybackConfig,
    ):
        self._provider = provider
        self._selector = selector
        self._config = config
        self._rec: Recommendation | None = None  # latest-wins slot
        self._event = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._now: NowPlaying | None = None
        self._queued: Track | None = None
        self.status = "starting"  # starting | active | degraded | stopped
        self.error: str | None = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="playback-controller"
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._event.set()

    # -- engine/bridge side (their threads; never blocks on provider I/O) ----

    def on_recommendation(self, rec: "Recommendation | None") -> None:
        """Offer a Mapper emission. Guard recommendations hold; a fresh rec
        replaces any not-yet-handled one (latest-wins, never a queue)."""
        if rec is None:
            return
        if not rec.genre_pool or rec.matched_cell[0] == GUARD_CELL:
            return  # insufficient signal: hold whatever is playing
        with self._lock:
            self._rec = rec
        self._event.set()

    def playback_state(self) -> tuple[bool, str | None]:
        """(playback_active, track_id) from the cache — the engine's
        PlaybackStateSource for stamping RoomState. Non-blocking."""
        with self._lock:
            now = self._now
        if now is None or not now.is_playing:
            return False, None
        return True, now.track.id

    def snapshot(self) -> dict:
        """Dashboard frame extras: status + what is playing/queued."""
        with self._lock:
            now, queued = self._now, self._queued
        return {
            "playback_status": self.status,
            "playback_error": self.error,
            "now_playing": now.to_dict() if now is not None else None,
            "queued_track": queued.to_dict() if queued is not None else None,
        }

    # -- worker side ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                rec, self._rec = self._rec, None
            try:
                if rec is not None:
                    self._handle(rec)
                now = self._provider.now_playing()  # I/O outside the lock
                with self._lock:
                    self._now = now
                if self.status != "active":
                    log.info("playback provider reachable; status -> active")
                self.status = "active"
                self.error = None
            except ProviderError as exc:
                self.status = "degraded"
                self.error = str(exc)
                log.warning("playback degraded (shadow mode): %s", exc)
            except Exception:
                self.status = "degraded"
                self.error = "internal error (see log)"
                log.exception("playback controller failed; degrading to shadow")
            self._event.wait(timeout=self._config.poll_interval_s)
            self._event.clear()
        self.status = "stopped"

    def _handle(self, rec: "Recommendation") -> None:
        tier = derive_tier(rec.target_arousal, rec.energy_action, self._config)
        track = self._selector.select(rec.genre_pool, tier)
        if track is None:
            return  # no mapped playlist: hold (selector already logged)
        now = self._provider.now_playing()
        if now is None or not now.is_playing:
            self._provider.play(track)  # bootstrap: nothing audible to interrupt
        else:
            self._provider.queue(track)  # replaces next-up; boundary transition
        with self._lock:
            self._queued = track
        log.info(
            "selected %r (%s / %s) for cell %s -> %s",
            track.title,
            track.artist,
            tier,
            rec.matched_cell,
            "play" if now is None or not now.is_playing else "queue",
        )
