"""Playback layer tests: provider seam, config/tiers, selector, controller.

No network, no Spotify — per the M4 gate, providers are mocked at the seam.
FakeProvider is the reference in-memory implementation the policy tests
build on.
"""

import json
import time

import pytest

from mapping.mapper import GUARD_CELL, Recommendation
from playback import (
    ENERGY_TIERS,
    Device,
    NowPlaying,
    PlaybackConfig,
    PlaybackController,
    PlaybackProvider,
    ProviderError,
    Track,
    TrackSelector,
    derive_tier,
)


def _track(n: int, playlist: str = "pl-pop-high") -> Track:
    return Track(
        id=f"track-{n}",
        title=f"Song {n}",
        artist="Artist",
        duration_s=180.0,
        playlist_id=playlist,
    )


class FakeProvider:
    """In-memory provider honoring the seam's semantics: play interrupts,
    queue replaces next-up, tracks_for resolves a (genre, tier) mapping."""

    def __init__(self, library: dict[tuple[str, str], list[Track]] | None = None):
        self.library = library or {}
        self._device = Device(id="dev-1", name="Living Room", active=True)
        self._now: NowPlaying | None = None
        self.next_up: Track | None = None
        self.failing = False

    def _check(self):
        if self.failing:
            raise ProviderError("device gone")

    def devices(self) -> list[Device]:
        self._check()
        return [self._device]

    def play(self, track: Track) -> None:
        self._check()
        self._now = NowPlaying(
            track=track, progress_s=0.0, is_playing=True, device_id=self._device.id
        )

    def queue(self, track: Track) -> None:
        self._check()
        self.next_up = track  # replaces, never appends: gentle-DJ semantics

    def pause(self) -> None:
        self._check()
        if self._now is not None:
            self._now = NowPlaying(
                track=self._now.track,
                progress_s=self._now.progress_s,
                is_playing=False,
                device_id=self._now.device_id,
            )

    def now_playing(self) -> NowPlaying | None:
        self._check()
        return self._now

    def tracks_for(self, genre: str, tier: str) -> list[Track]:
        self._check()
        return list(self.library.get((genre, tier), []))


class TestProtocol:
    def test_fake_provider_satisfies_the_seam(self):
        assert isinstance(FakeProvider(), PlaybackProvider)

    def test_incomplete_implementation_fails_the_seam(self):
        class MissingQueue:
            def devices(self): ...
            def play(self, track): ...
            def pause(self): ...
            def now_playing(self): ...
            def tracks_for(self, genre, tier): ...

        assert not isinstance(MissingQueue(), PlaybackProvider)

    def test_energy_tiers_are_the_three_band_labels(self):
        assert ENERGY_TIERS == ("low", "mid", "high")


class TestFakeProviderSemantics:
    def test_play_pause_now_playing_round_trip(self):
        p = FakeProvider()
        assert p.now_playing() is None
        p.play(_track(1))
        now = p.now_playing()
        assert now.is_playing and now.track.id == "track-1"
        p.pause()
        assert not p.now_playing().is_playing

    def test_queue_replaces_next_up(self):
        """Gentle-DJ: a new Recommendation replaces the QUEUED track, never
        the playing one."""
        p = FakeProvider()
        p.play(_track(1))
        p.queue(_track(2))
        p.queue(_track(3))  # new recommendation before the boundary
        assert p.next_up.id == "track-3"
        assert p.now_playing().track.id == "track-1"  # uninterrupted

    def test_tracks_for_unmapped_cell_is_empty_not_an_error(self):
        p = FakeProvider(library={("Pop", "high"): [_track(1)]})
        assert p.tracks_for("Pop", "high") == [_track(1)]
        assert p.tracks_for("Jazz", "low") == []

    def test_provider_failure_raises_provider_error(self):
        """The failure-isolation contract: everything a dead provider can't
        do surfaces as ProviderError for the caller to degrade on."""
        p = FakeProvider()
        p.failing = True
        for call in (
            p.devices,
            lambda: p.play(_track(1)),
            lambda: p.queue(_track(1)),
            p.pause,
            p.now_playing,
            lambda: p.tracks_for("Pop", "high"),
        ):
            with pytest.raises(ProviderError):
                call()


def _rec(
    genre_pool=("Pop",),
    target_arousal=0.5,
    energy_action="hold",
    matched_cell=("4", "high", "high"),
) -> Recommendation:
    return Recommendation(
        energy_action=energy_action,
        target_valence=0.5,
        target_arousal=target_arousal,
        genre_pool=list(genre_pool),
        confidence=0.8,
        summary="test",
        matched_cell=matched_cell,
        boundaries_snapshot={},
        timestamp=1000.0,
    )


def _wait_until(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestConfig:
    def test_defaults_are_shadow_mode(self):
        cfg = PlaybackConfig()
        assert not cfg.enabled
        assert cfg.client_id is None

    def test_from_env_reads_playback_vars(self, monkeypatch):
        monkeypatch.setenv("RTR_PLAYBACK_ENABLED", "1")
        monkeypatch.setenv("RTR_PLAYBACK_CLIENT_ID", "abc123")
        monkeypatch.setenv("RTR_PLAYBACK_DEVICE_NAME", "Living Room")
        monkeypatch.setenv("RTR_PLAYBACK_RECENT_WINDOW", "5")
        monkeypatch.setenv("RTR_PLAYBACK_TIER_HIGH_MIN", "0.4")
        cfg = PlaybackConfig.from_env()
        assert cfg.enabled
        assert cfg.client_id == "abc123"
        assert cfg.device_name == "Living Room"
        assert cfg.recently_played_window == 5
        assert cfg.tier_high_min == 0.4
        assert cfg.tier_low_max == -0.25  # untouched default


class TestTierDerivation:
    @pytest.mark.parametrize(
        "arousal, expected", [(0.5, "high"), (0.0, "mid"), (-0.5, "low")]
    )
    def test_base_bands_match_mapping_semantics(self, arousal, expected):
        assert derive_tier(arousal, "hold", PlaybackConfig()) == expected

    def test_energy_action_shifts_one_tier(self):
        cfg = PlaybackConfig()
        assert derive_tier(0.0, "raise", cfg) == "high"
        assert derive_tier(0.0, "lower", cfg) == "low"

    def test_shift_clamps_at_the_ladder_ends(self):
        cfg = PlaybackConfig()
        assert derive_tier(0.9, "raise", cfg) == "high"
        assert derive_tier(-0.9, "lower", cfg) == "low"

    def test_cutoffs_are_tunable_boundaries(self):
        cfg = PlaybackConfig(tier_low_max=0.0, tier_high_min=0.6)
        assert derive_tier(0.5, "hold", cfg) == "mid"
        assert derive_tier(-0.1, "hold", cfg) == "low"


class TestTrackSelector:
    def test_pool_order_is_priority_order(self):
        provider = FakeProvider(library={("Jazz", "mid"): [_track(1, "pl-jazz")]})
        selector = TrackSelector(provider, seed=0)
        chosen = selector.select(["Pop", "Jazz"], "mid")
        assert chosen.playlist_id == "pl-jazz"

    def test_recently_played_are_suppressed(self):
        tracks = [_track(n) for n in range(3)]
        provider = FakeProvider(library={("Pop", "high"): tracks})
        selector = TrackSelector(provider, recently_played_window=10, seed=0)
        picks = {selector.select(["Pop"], "high").id for _ in range(3)}
        assert len(picks) == 3  # never repeats while fresh tracks remain

    def test_exhausted_playlist_repeats_rather_than_silence(self):
        provider = FakeProvider(library={("Pop", "high"): [_track(1)]})
        selector = TrackSelector(provider, seed=0)
        assert selector.select(["Pop"], "high").id == "track-1"
        assert selector.select(["Pop"], "high").id == "track-1"

    def test_unmapped_pool_returns_none(self):
        selector = TrackSelector(FakeProvider(), seed=0)
        assert selector.select(["Jazz"], "low") is None

    def test_provider_error_propagates_to_caller(self):
        provider = FakeProvider()
        provider.failing = True
        selector = TrackSelector(provider, seed=0)
        with pytest.raises(ProviderError):
            selector.select(["Pop"], "high")


class TestController:
    @pytest.fixture
    def provider(self):
        return FakeProvider(
            library={
                ("Pop", "high"): [_track(1)],
                ("Jazz", "mid"): [_track(2, "pl-jazz-mid")],
            }
        )

    @pytest.fixture
    def controller(self, provider):
        c = PlaybackController(
            provider,
            TrackSelector(provider, seed=0),
            PlaybackConfig(poll_interval_s=0.05),
        )
        c.start()
        assert _wait_until(lambda: c.status == "active")
        yield c
        c.stop()

    def test_bootstrap_plays_when_nothing_is_audible(self, provider, controller):
        controller.on_recommendation(_rec())
        assert _wait_until(lambda: provider.now_playing() is not None)
        assert provider.now_playing().track.id == "track-1"
        assert _wait_until(lambda: controller.playback_state() == (True, "track-1"))

    def test_recommendation_queues_and_never_interrupts(self, provider, controller):
        provider.play(_track(99, playlist=None))  # something already playing
        controller.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        assert _wait_until(lambda: provider.next_up is not None)
        assert provider.next_up.id == "track-2"
        assert provider.now_playing().track.id == "track-99"  # uninterrupted

    def test_guard_recommendation_holds(self, provider, controller):
        controller.on_recommendation(
            _rec(genre_pool=(), matched_cell=(GUARD_CELL, "no-speech"))
        )
        time.sleep(0.2)
        assert provider.now_playing() is None
        assert provider.next_up is None

    def test_provider_error_degrades_then_recovers(self, provider, controller):
        controller.on_recommendation(_rec())
        assert _wait_until(lambda: controller.playback_state()[0])

        provider.failing = True
        assert _wait_until(lambda: controller.status == "degraded")
        # Cache retained through the outage: for contamination tagging a
        # stale "active" is safer than a false "clean".
        assert controller.playback_state() == (True, "track-1")

        provider.failing = False
        controller.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        assert _wait_until(lambda: controller.status == "active")
        assert _wait_until(lambda: provider.next_up is not None)

    def test_playback_state_false_when_idle(self, controller):
        assert controller.playback_state() == (False, None)

    def test_snapshot_carries_status_and_tracks(self, provider, controller):
        controller.on_recommendation(_rec())
        assert _wait_until(lambda: controller.snapshot()["now_playing"] is not None)
        snap = controller.snapshot()
        assert snap["playback_status"] == "active"
        assert snap["now_playing"]["track"]["id"] == "track-1"
        assert snap["queued_track"] is None  # bootstrap plays; nothing queued

        controller.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        assert _wait_until(
            lambda: controller.snapshot()["queued_track"] is not None
        )
        assert controller.snapshot()["queued_track"]["id"] == "track-2"


class TestOverrideActions:
    """M4 deliverable 2: skip / wrong_vibe / manual_pick semantics, plus the
    played_through weak positive at track boundaries."""

    @pytest.fixture
    def provider(self):
        return FakeProvider(
            library={
                ("Pop", "high"): [_track(1), _track(4)],
                ("Jazz", "mid"): [_track(2, "pl-jazz-mid")],
                ("Hip-Hop", "high"): [_track(5, "pl-hh-high")],
            }
        )

    def _controller(self, provider, sink=None):
        c = PlaybackController(
            provider,
            TrackSelector(provider, seed=0),
            PlaybackConfig(poll_interval_s=0.05),
            on_played_through=sink,
        )
        c.start()
        assert _wait_until(lambda: c.status == "active")
        return c

    def test_skip_prefers_the_queued_next_selection(self, provider):
        c = self._controller(provider)
        try:
            provider.play(_track(99, playlist=None))
            c.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
            assert _wait_until(lambda: provider.next_up is not None)
            skipped_to = c.skip()
            assert skipped_to.id == "track-2"
            assert provider.now_playing().track.id == "track-2"
        finally:
            c.stop()

    def test_skip_resamples_from_the_last_recommendation(self, provider):
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec())  # bootstrap: plays a Pop/high track
            assert _wait_until(lambda: c.playback_state()[0])
            first = provider.now_playing().track.id
            replacement = c.skip()
            assert replacement is not None
            assert replacement.id != first  # recently-played suppression
            assert provider.now_playing().track.id == replacement.id
        finally:
            c.stop()

    def test_skip_with_nothing_to_go_to_pauses(self, provider):
        c = self._controller(provider)
        try:
            provider.play(_track(99, playlist=None))  # not our selection
            assert _wait_until(lambda: c.playback_state()[0])
            assert c.skip() is None  # no queued track, no rec to resample
            assert provider.now_playing().is_playing is False
            assert c.playback_state() == (False, None)
        finally:
            c.stop()

    def test_wrong_vibe_resamples_a_cell_adjacent_pool(self, provider):
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec())  # cell ("4","high","high"), pool Pop
            assert _wait_until(lambda: c.playback_state()[0])
            track = c.wrong_vibe()
            # ("4","high","mid")/("4","mid","high") both map Hip-Hop
            assert track.id == "track-5"
            assert provider.now_playing().track.id == "track-5"
        finally:
            c.stop()

    def test_wrong_vibe_without_history_is_a_noop(self, provider):
        c = self._controller(provider)
        try:
            assert c.wrong_vibe() is None
        finally:
            c.stop()

    def test_manual_pick_plays_immediately(self, provider):
        c = self._controller(provider)
        try:
            provider.play(_track(99, playlist=None))
            assert _wait_until(lambda: c.playback_state()[0])
            track = c.manual_pick("Jazz", "mid")
            assert track.id == "track-2"
            assert provider.now_playing().track.id == "track-2"
        finally:
            c.stop()

    def test_played_through_emits_the_weak_positive(self, provider):
        events = []
        c = self._controller(provider, sink=lambda np, rec: events.append((np, rec)))
        try:
            c.on_recommendation(_rec())
            assert _wait_until(lambda: c.playback_state()[0])
            played = provider.now_playing().track
            # Natural boundary: the provider moves on to another track.
            provider._now = NowPlaying(
                track=_track(42, playlist=None),
                progress_s=0.0,
                is_playing=True,
                device_id="dev-1",
            )
            assert _wait_until(lambda: len(events) == 1)
            now_playing_dict, rec_dict = events[0]
            assert now_playing_dict["track"]["id"] == played.id
            assert rec_dict["matched_cell"] == ["4", "high", "high"]
        finally:
            c.stop()

    def test_skipped_track_never_logs_played_through(self, provider):
        events = []
        c = self._controller(provider, sink=lambda np, rec: events.append((np, rec)))
        try:
            c.on_recommendation(_rec())
            assert _wait_until(lambda: c.playback_state()[0])
            c.skip()  # human veto: the transition this causes is not a positive
            time.sleep(0.2)  # a few poll cycles
            assert events == []
        finally:
            c.stop()


class TestWireTypes:
    def test_track_dict_round_trip(self):
        t = _track(7)
        assert Track.from_dict(json.loads(json.dumps(t.to_dict()))) == t

    def test_now_playing_dict_round_trip(self):
        """Override records serialize NowPlaying (M4 deliverable 2); it must
        survive JSONL and come back equal."""
        now = NowPlaying(
            track=_track(7), progress_s=42.5, is_playing=True, device_id="dev-1"
        )
        assert NowPlaying.from_dict(json.loads(json.dumps(now.to_dict()))) == now
