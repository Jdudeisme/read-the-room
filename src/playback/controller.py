"""PlaybackController: gentle-DJ policy between Mapper emissions and a provider.

Runs in the dashboard process as a consumer of Mapper emissions — same
process as DashboardBridge, new subscriber, engine untouched. All provider
I/O happens on the controller's own worker thread (latest-wins slot, the
EmotionWorker pattern) or on the HTTP threadpool for human overrides; the
sensing side reads playback state from a cache, never over the network.

Gentle-DJ policy (M4 proposal deliverable 1):
- a new Recommendation NEVER interrupts the playing track; it replaces the
  queued next track, and transitions happen on track boundaries;
- the one exception is bootstrap: when nothing is audible at all, the
  selected track starts immediately (there is nothing to interrupt);
- guard recommendations (insufficient signal) hold — no selection, no queue;
- volume is never touched; energy moves only through tier selection;
- only a HUMAN override (skip / wrong_vibe / manual_pick) cuts mid-track.

Override capture (deliverable 2): the controller keeps track-id ->
recommendation attribution for everything it selects, marks overridden
tracks, and emits an implicit weak-positive via `on_played_through` when a
selected track crosses a boundary with no override — the caller (dashboard)
owns writing the record.

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
from typing import TYPE_CHECKING, Callable

from mapping.mapper import GUARD_CELL
from mapping.rulebook import AROUSAL_BANDS, RULEBOOK, VALENCE_BANDS

from .config import PlaybackConfig
from .provider import NowPlaying, PlaybackProvider, ProviderError, Track
from .selector import TrackSelector, derive_tier

if TYPE_CHECKING:
    from mapping.mapper import Recommendation

log = logging.getLogger(__name__)

_ATTRIBUTION_KEEP = 8  # selected tracks to keep rec attribution for
_OVERRIDDEN_KEEP = 16  # overridden track ids to remember


def cell_adjacent_pool(matched_cell: tuple, exclude: list[str]) -> list[str]:
    """Genres from rulebook cells one band-step away (same bucket, valence
    or arousal moved by one), minus the vetoed pool — the wrong_vibe
    resample space. Empty for guard cells or when every neighbor repeats
    the vetoed genres."""
    if len(matched_cell) != 3 or matched_cell[0] == GUARD_CELL:
        return []
    bucket, vband, aband = matched_cell
    if vband not in VALENCE_BANDS or aband not in AROUSAL_BANDS:
        return []
    vi, ai = VALENCE_BANDS.index(vband), AROUSAL_BANDS.index(aband)
    genres: list[str] = []
    for dv, da in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        v, a = vi + dv, ai + da
        if not (0 <= v < len(VALENCE_BANDS) and 0 <= a < len(AROUSAL_BANDS)):
            continue
        for genre in RULEBOOK[(bucket, VALENCE_BANDS[v], AROUSAL_BANDS[a])]:
            if genre not in exclude and genre not in genres:
                genres.append(genre)
    return genres


class PlaybackController:
    """status: starting -> active | degraded -> stopped."""

    def __init__(
        self,
        provider: PlaybackProvider,
        selector: TrackSelector,
        config: PlaybackConfig,
        on_played_through: Callable[[dict, dict], None] | None = None,
    ):
        self._provider = provider
        self._selector = selector
        self._config = config
        # Called with (now_playing_dict, recommendation_dict) when a selected
        # track crosses a boundary with no override — the implicit weak
        # positive. The dashboard wires this to the override log.
        self.on_played_through = on_played_through
        self._rec: Recommendation | None = None  # latest-wins slot
        self._event = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._now: NowPlaying | None = None
        self._queued: Track | None = None  # selected but not yet playing
        self._last_rec: Recommendation | None = None  # for override resamples
        self._attribution: dict[str, dict] = {}  # track id -> rec/choice dict
        self._overridden: dict[str, None] = {}  # ordered set of track ids
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

    # -- human overrides (HTTP threadpool; provider I/O is acceptable here,
    #    ProviderError propagates — the endpoint already banked the label) --

    def skip(self) -> Track | None:
        """Veto the playing track NOW. Prefers the already-queued next
        selection; otherwise resamples from the last handled recommendation.
        With nothing to go to, pauses — a veto means silence beats it."""
        with self._lock:
            now, queued, last_rec = self._now, self._queued, self._last_rec
        if now is None:
            return None
        self._mark_overridden(now.track.id)
        replacement = queued
        if replacement is None and last_rec is not None:
            tier = derive_tier(
                last_rec.target_arousal, last_rec.energy_action, self._config
            )
            replacement = self._selector.select(last_rec.genre_pool, tier)
            if replacement is not None:
                self._attribute(replacement.id, last_rec.to_dict())
        if replacement is None:
            self._provider.pause()
            with self._lock:
                if self._now is not None:
                    self._now = NowPlaying(
                        track=self._now.track,
                        progress_s=self._now.progress_s,
                        is_playing=False,
                        device_id=self._now.device_id,
                    )
            return None
        self._play_now(replacement)
        return replacement

    def wrong_vibe(self) -> Track | None:
        """Veto the SELECTION, not just the track: resample from a
        cell-adjacent pool (same bucket, one band step away), excluding the
        vetoed genres. None when there is no rec to pivot from or no
        adjacent pool is mapped."""
        with self._lock:
            now, last_rec = self._now, self._last_rec
        if last_rec is None:
            return None
        pool = cell_adjacent_pool(last_rec.matched_cell, exclude=last_rec.genre_pool)
        if not pool:
            return None
        tier = derive_tier(
            last_rec.target_arousal, last_rec.energy_action, self._config
        )
        track = self._selector.select(pool, tier)
        if track is None:
            return None
        if now is not None:
            self._mark_overridden(now.track.id)
        self._attribute(track.id, last_rec.to_dict())
        self._play_now(track)
        return track

    def manual_pick(self, genre: str, tier: str) -> Track | None:
        """Human picked a mapped (genre, tier) outright. Plays immediately;
        attribution records the human choice, so a play-through of this
        track is a weak positive for the CHOICE, not for any rulebook cell."""
        track = self._selector.select([genre], tier)
        if track is None:
            return None
        with self._lock:
            now = self._now
        if now is not None:
            self._mark_overridden(now.track.id)
        self._attribute(track.id, {"source": "manual", "genre": genre, "tier": tier})
        self._play_now(track)
        return track

    # -- worker side ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                rec, self._rec = self._rec, None
            try:
                if rec is not None:
                    self._handle(rec)
                self._observe(self._provider.now_playing())  # I/O outside lock
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
        bootstrap = now is None or not now.is_playing
        if bootstrap:
            self._provider.play(track)  # nothing audible to interrupt
        else:
            self._provider.queue(track)  # replaces next-up; boundary transition
        self._attribute(track.id, rec.to_dict())
        with self._lock:
            self._last_rec = rec
            self._queued = None if bootstrap else track
        log.info(
            "selected %r (%s / %s) for cell %s -> %s",
            track.title,
            track.artist,
            tier,
            rec.matched_cell,
            "play" if bootstrap else "queue",
        )

    def _observe(self, new: NowPlaying | None) -> None:
        """Cache fresh provider state and do boundary bookkeeping: clear the
        queued slot once its track starts, and emit the played_through weak
        positive when a selected, non-overridden track ends."""
        with self._lock:
            prev = self._now
            self._now = new
            if (
                new is not None
                and self._queued is not None
                and new.track.id == self._queued.id
            ):
                self._queued = None  # next-up crossed the boundary
            ended = (
                prev is not None
                and (new is None or new.track.id != prev.track.id)
                and prev.track.id not in self._overridden
            )
            attribution = self._attribution.get(prev.track.id) if ended else None
        if ended and attribution is not None and self.on_played_through is not None:
            try:
                self.on_played_through(prev.to_dict(), attribution)
            except Exception:
                log.exception("played_through sink failed; label lost")

    # -- shared bookkeeping ----------------------------------------------------

    def _play_now(self, track: Track) -> None:
        self._provider.play(track)
        with self._lock:
            device = self._now.device_id if self._now is not None else None
            self._now = NowPlaying(
                track=track, progress_s=0.0, is_playing=True, device_id=device
            )
            if self._queued is not None and self._queued.id == track.id:
                self._queued = None

    def _attribute(self, track_id: str, chose_it: dict) -> None:
        with self._lock:
            self._attribution[track_id] = chose_it
            while len(self._attribution) > _ATTRIBUTION_KEEP:
                self._attribution.pop(next(iter(self._attribution)))

    def _mark_overridden(self, track_id: str) -> None:
        with self._lock:
            self._overridden[track_id] = None
            while len(self._overridden) > _OVERRIDDEN_KEEP:
                self._overridden.pop(next(iter(self._overridden)))
