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
    load_playlists,
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
    queue APPENDS (the real Spotify queue has no replace/remove — modeling
    it as replaceable is exactly how the M4 stale-queue bug shipped),
    tracks_for resolves a (genre, tier) mapping."""

    def __init__(self, library: dict[tuple[str, str], list[Track]] | None = None):
        self.library = library or {}
        self._device = Device(id="dev-1", name="Living Room", active=True)
        self._now: NowPlaying | None = None
        self.queued: list[Track] = []  # append-only, FIFO — like the real API
        self.failing = False

    @property
    def next_up(self) -> Track | None:
        """What would actually play at the boundary: the FIFO head."""
        return self.queued[0] if self.queued else None

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
        self.queued.append(track)  # append-only: Spotify offers no replace

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

    def test_queue_is_append_only_fifo(self):
        """The real Spotify queue offers no replace/remove: everything
        queued plays, oldest first. The gentle-DJ latest-wins behavior must
        live in the CONTROLLER (deferred push), never be assumed of the
        provider."""
        p = FakeProvider()
        p.play(_track(1))
        p.queue(_track(2))
        p.queue(_track(3))
        assert [t.id for t in p.queued] == ["track-2", "track-3"]
        assert p.next_up.id == "track-2"  # FIFO head, not the newest
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

    def test_selection_stamps_genre_and_tier(self):
        """M5: the pick carries which (genre, tier) it answered, so every
        downstream record has genre-level evidence for pool weighting."""
        provider = FakeProvider(library={("Jazz", "mid"): [_track(1, "pl-jazz")]})
        selector = TrackSelector(provider, seed=0)
        chosen = selector.select(["Pop", "Jazz"], "mid")
        assert chosen.genre == "Jazz"
        assert chosen.tier == "mid"
        assert chosen.to_dict()["genre"] == "Jazz"
        # tracks straight off the provider are unstamped
        assert provider.tracks_for("Jazz", "mid")[0].genre is None


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

    def test_recommendation_holds_next_up_without_pushing(self, provider, controller):
        provider.play(_track(99, playlist=None))  # something already playing
        controller.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        assert _wait_until(
            lambda: controller.snapshot()["queued_track"] is not None
        )
        assert controller.snapshot()["queued_track"]["id"] == "track-2"
        assert provider.queued == []  # far from the boundary: nothing pushed
        assert provider.now_playing().track.id == "track-99"  # uninterrupted

    def test_guard_recommendation_holds(self, provider, controller):
        controller.on_recommendation(
            _rec(genre_pool=(), matched_cell=(GUARD_CELL, "no-speech"))
        )
        time.sleep(0.2)
        assert provider.now_playing() is None
        assert provider.queued == []
        assert controller.snapshot()["queued_track"] is None

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
        assert _wait_until(
            lambda: controller.snapshot()["queued_track"] is not None
        )

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

    def test_skip_prefers_the_held_next_selection(self, provider):
        c = self._controller(provider)
        try:
            provider.play(_track(99, playlist=None))
            c.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
            assert _wait_until(lambda: c.snapshot()["queued_track"] is not None)
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
            # Ride the track to its final seconds (completion requires the
            # last observation inside the boundary window of the end)...
            provider._now = NowPlaying(
                track=played, progress_s=172.0, is_playing=True, device_id="dev-1"
            )
            assert _wait_until(
                lambda: (c.snapshot()["now_playing"] or {}).get("progress_s")
                == 172.0
            )
            # ...then a natural boundary: the provider moves on.
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


class TestBoundaryWindow:
    """Deferred queue push against the append-only provider queue: the
    controller holds the latest selection and pushes exactly one track,
    inside the final queue_lead_s of whatever is playing. Direct calls to
    _handle/_observe — no worker thread — so every observation step is
    deterministic."""

    @pytest.fixture
    def provider(self):
        return FakeProvider(
            library={
                ("Pop", "high"): [_track(1)],
                ("Jazz", "mid"): [_track(2, "pl-jazz-mid")],
            }
        )

    def _controller(self, provider, sink=None):
        return PlaybackController(
            provider,
            TrackSelector(provider, seed=0),
            PlaybackConfig(poll_interval_s=0.05, queue_lead_s=15.0),
            on_played_through=sink,
        )

    @staticmethod
    def _playing(track: Track, progress: float) -> NowPlaying:
        return NowPlaying(
            track=track, progress_s=progress, is_playing=True, device_id="dev-1"
        )

    def test_push_happens_only_inside_the_boundary_window(self, provider):
        c = self._controller(provider)
        current = _track(99, playlist=None)
        provider.play(current)
        c._handle(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        assert provider.queued == []  # held, not pushed
        c._observe(self._playing(current, 100.0))  # 80s left: hold
        assert provider.queued == []
        c._observe(self._playing(current, 170.0))  # 10s left: push
        assert [t.id for t in provider.queued] == ["track-2"]
        # The pushed track takes over at the boundary; next-up slot clears.
        c._observe(self._playing(_track(2, "pl-jazz-mid"), 0.0))
        assert c.snapshot()["queued_track"] is None

    def test_latest_selection_wins_with_a_single_push(self, provider):
        c = self._controller(provider)
        current = _track(99, playlist=None)
        provider.play(current)
        c._handle(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        c._handle(_rec())  # fresher rec while far from the boundary: Pop
        assert c.snapshot()["queued_track"]["id"] == "track-1"
        c._observe(self._playing(current, 170.0))
        # One push total, and it is the freshest selection — never a pile-up.
        assert [t.id for t in provider.queued] == ["track-1"]

    def test_boundary_into_silence_starts_the_held_selection(self, provider):
        c = self._controller(provider)
        current = _track(99, playlist=None)
        provider.play(current)
        c._observe(self._playing(current, 170.0))  # near end, nothing held yet
        c._handle(_rec(genre_pool=("Jazz",), target_arousal=0.0))  # last-second rec
        provider._now = None  # the track ran out; nothing queued, silence
        c._observe(None)
        # Bootstrap semantics: nothing audible to interrupt, so it starts.
        assert provider.now_playing().track.id == "track-2"

    def test_vanishing_mid_track_is_not_played_through(self, provider):
        events = []
        c = self._controller(provider, sink=lambda np, rec: events.append((np, rec)))
        c._handle(_rec())  # bootstrap: plays and attributes track-1
        c._observe(self._playing(_track(1), 90.0))  # mid-flight
        c._observe(None)  # provider quit / external skip: not a completion
        assert events == []

    def test_observed_completion_is_played_through(self, provider):
        events = []
        c = self._controller(provider, sink=lambda np, rec: events.append((np, rec)))
        c._handle(_rec())  # bootstrap: plays and attributes track-1
        c._observe(self._playing(_track(1), 172.0))  # last seen near its end
        c._observe(self._playing(_track(42, playlist=None), 0.0))
        assert len(events) == 1
        assert events[0][0]["track"]["id"] == "track-1"

    # -- M9-07 (docs/M9-07-PLAN.md): substituted ids and the next-up slot -----

    @staticmethod
    def _as(track: Track, new_id: str, **changes) -> Track:
        """`track` as the provider reports it under another id."""
        d = track.to_dict()
        d.update(id=new_id, **changes)
        return Track.from_dict(d)

    def _push_jazz(self, c, provider, current):
        provider.play(current)
        c._handle(_rec(genre_pool=("Jazz",), target_arousal=0.0))
        c._observe(self._playing(current, 170.0))  # inside the window: push
        assert [t.id for t in provider.queued] == ["track-2"]
        assert c.snapshot()["queued_state"] == "pushed"

    def test_substituted_pushed_track_clears_the_slot_and_pushing_resumes(self, provider):
        # 2026-11-21: requested ...3cwGy, played as ...0zhDL, same title and
        # duration; before M9-07 the slot never cleared and nothing else was pushed.
        c = self._controller(provider)
        current = _track(99, playlist=None)
        self._push_jazz(c, provider, current)
        played = self._as(_track(2, "pl-jazz-mid"), "track-2-relinked")
        c._observe(self._playing(played, 0.0))
        assert c.snapshot()["queued_track"] is None
        assert c.snapshot()["queued_state"] is None
        c._handle(_rec())  # a fresh selection: Pop -> track-1, held
        assert c.snapshot()["queued_state"] == "held"
        c._observe(self._playing(played, 170.0))  # the next boundary window
        assert [t.id for t in provider.queued] == ["track-2", "track-1"]

    def test_a_pick_after_the_push_never_hides_the_pushed_track(self, provider):
        # 2026-11-23 re-check: Gas Drawls was pushed and 0.6 s later a new
        # selection arrived; the line showed the new pick "(held)" for the
        # song's last 16 s, though Gas Drawls (append-only queue) played next.
        c = self._controller(provider)
        current = _track(99, playlist=None)
        self._push_jazz(c, provider, current)
        c._handle(_rec())  # a fresh selection: Pop -> track-1, held behind it
        snap = c.snapshot()
        assert snap["queued_track"]["id"] == "track-2"
        assert snap["queued_state"] == "pushed"
        assert snap["held_track"]["id"] == "track-1"
        c._observe(self._playing(_track(2, "pl-jazz-mid"), 0.0))  # it plays next
        snap = c.snapshot()
        assert (snap["queued_track"]["id"], snap["queued_state"]) == ("track-1", "held")
        assert snap["held_track"]["id"] == "track-1"

    def test_linked_from_matches_even_when_the_title_differs(self, provider):
        c = self._controller(provider)
        self._push_jazz(c, provider, _track(99, playlist=None))
        played = self._as(_track(2, "pl-jazz-mid"), "track-2-alt",
                          title="Song 2 (Remastered)", linked_from="track-2")
        c._observe(self._playing(played, 0.0))
        assert c.snapshot()["queued_track"] is None

    @pytest.mark.parametrize("changes", [
        {"title": "Another Song"},       # same duration, different title
        {"duration_s": 183.5},           # same title, 3.5 s apart
    ])
    def test_a_different_track_is_not_the_pushed_one(self, provider, changes):
        c = self._controller(provider)
        self._push_jazz(c, provider, _track(99, playlist=None))
        other = self._as(_track(2, "pl-jazz-mid"), "track-77", **changes)
        c._observe(self._playing(other, 0.0))
        assert c.snapshot()["queued_state"] == "pushed"  # one boundary: still held

    def test_backstop_clears_a_slot_never_observed_after_two_boundaries(self, provider, caplog):
        c = self._controller(provider)
        self._push_jazz(c, provider, _track(99, playlist=None))
        c._observe(self._playing(_track(50, playlist=None), 0.0))  # boundary 1: not it
        assert c.snapshot()["queued_state"] == "pushed"
        with caplog.at_level("WARNING"):
            c._observe(self._playing(_track(51, playlist=None), 0.0))  # boundary 2
        assert c.snapshot()["queued_track"] is None
        assert "never observed after 2 boundaries" in caplog.text
        c._handle(_rec())
        c._observe(self._playing(_track(51, playlist=None), 170.0))
        assert [t.id for t in provider.queued] == ["track-2", "track-1"]

    def test_substituted_pushed_track_earns_played_through(self, provider):
        events = []
        c = self._controller(provider, sink=lambda np, rec: events.append((np, rec)))
        self._push_jazz(c, provider, _track(99, playlist=None))
        played = self._as(_track(2, "pl-jazz-mid"), "track-2-relinked")
        c._observe(self._playing(played, 0.0))
        c._observe(self._playing(played, 172.0))  # last seen near its end
        c._observe(self._playing(_track(60, playlist=None), 0.0))
        assert len(events) == 1
        assert events[0][0]["track"]["id"] == "track-2-relinked"
        assert events[0][1]["genre_pool"] == ["Jazz"]

    def test_substituted_start_earns_played_through(self):
        # The FIELD-NOTES 2026-09-30 open item: a substituted START could never
        # earn played_through, because attribution was keyed by the requested id.
        provider = SubstitutingProvider(library={("Pop", "high"): [_track(1)]})
        events = []
        c = PlaybackController(
            provider, TrackSelector(provider, seed=0),
            PlaybackConfig(poll_interval_s=0.05, queue_lead_s=15.0, start_verify_s=0.0),
            on_played_through=lambda np, rec: events.append((np, rec)),
        )
        c._handle(_rec())  # bootstrap: plays track-1, reported as track-1-substituted
        sub = provider.now_playing().track
        c._observe(self._playing(sub, 1.0))  # start verified: same track by title+duration
        c._observe(self._playing(sub, 172.0))
        c._observe(self._playing(_track(60, playlist=None), 0.0))
        assert len(events) == 1
        assert events[0][0]["track"]["id"] == "track-1-substituted"


class DroppingProvider(FakeProvider):
    """Accepts play() and loads nothing — what the Windows desktop client
    did on 2026-09-30: a 204, then the device unloaded (item None) whether
    it had been playing, paused or idle (FIELD-NOTES that date)."""

    def play(self, track: Track) -> None:
        self._check()
        self._now = None


class SubstitutingProvider(FakeProvider):
    """Plays the request but reports a different track id for it, as the
    desktop client did for 'RUSH' on 2026-09-30."""

    def play(self, track: Track) -> None:
        super().play(
            Track(
                id=track.id + "-substituted",
                title=track.title,
                artist=track.artist,
                duration_s=track.duration_s,
                playlist_id=track.playlist_id,
            )
        )


class TestStartVerification:
    """A 204 is not a start: each play is judged by the first poll at least
    start_verify_s after it."""

    LIBRARY = {
        ("Pop", "high"): [_track(1)],
        ("Jazz", "mid"): [_track(2, "pl-jazz-mid")],
    }

    def _controller(self, provider, verify_s=0.1):
        c = PlaybackController(
            provider,
            TrackSelector(provider, seed=0),
            PlaybackConfig(poll_interval_s=0.05, start_verify_s=verify_s),
        )
        c.start()
        assert _wait_until(lambda: c.status == "active")
        return c

    def test_dropped_bootstrap_play_degrades(self):
        provider = DroppingProvider(library=self.LIBRARY)
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec())
            assert _wait_until(lambda: c.status == "degraded")
            assert "nothing started" in c.error
            assert "Song 1" in c.error
        finally:
            c.stop()

    def test_dropped_skip_play_degrades(self):
        """The 15:09 case: a track was playing, the skip's play unloaded it
        and loaded nothing."""
        provider = DroppingProvider(library=self.LIBRARY)
        FakeProvider.play(provider, _track(99, playlist=None))
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
            assert _wait_until(lambda: c.snapshot()["queued_track"] is not None)
            assert c.skip().id == "track-2"
            assert _wait_until(lambda: c.status == "degraded")
            assert "nothing started" in c.error
        finally:
            c.stop()

    def test_ignored_skip_play_degrades(self):
        """A play that leaves the interrupted track playing did not take,
        even though something is audible."""

        class IgnoringProvider(FakeProvider):
            def play(self, track: Track) -> None:
                self._check()  # accepted; the old track plays on

        provider = IgnoringProvider(library=self.LIBRARY)
        FakeProvider.play(provider, _track(99, playlist=None))
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec(genre_pool=("Jazz",), target_arousal=0.0))
            assert _wait_until(lambda: c.snapshot()["queued_track"] is not None)
            c.skip()
            assert _wait_until(lambda: c.status == "degraded")
        finally:
            c.stop()

    def test_verified_play_stays_active(self):
        provider = FakeProvider(library=self.LIBRARY)
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec())
            assert _wait_until(lambda: c.playback_state()[0])
            assert _wait_until(lambda: c._pending_start is None)  # judged
            assert c.status == "active" and c.error is None
        finally:
            c.stop()

    def test_substituted_id_is_not_a_failure(self, caplog):
        provider = SubstitutingProvider(library=self.LIBRARY)
        c = self._controller(provider)
        try:
            with caplog.at_level("INFO", logger="playback.controller"):
                c.on_recommendation(_rec())
                # the verdict clears the slot just before it logs
                assert _wait_until(lambda: "track-1-substituted" in caplog.text)
            assert c.status == "active"
        finally:
            c.stop()

    def test_no_verdict_before_the_grace_period(self):
        """0.25 s after a real play the old state can still read as playing
        or empty; a poll inside the grace period proves nothing either way."""
        provider = DroppingProvider(library=self.LIBRARY)
        c = self._controller(provider, verify_s=60.0)
        try:
            c.on_recommendation(_rec())
            time.sleep(0.3)  # several polls, all inside the grace period
            assert c.status == "active"
            assert c._pending_start is not None
        finally:
            c.stop()

    def test_failed_start_recovers_on_the_next_good_poll(self):
        """No retry and no sticky failure: the failed start degrades, and
        the next successful poll restores active (the next emission tries
        afresh)."""
        provider = DroppingProvider(library=self.LIBRARY)
        c = self._controller(provider)
        try:
            c.on_recommendation(_rec())
            assert _wait_until(lambda: c.status == "degraded")
            assert _wait_until(lambda: c.status == "active")
            assert provider.now_playing() is None  # nothing was retried
        finally:
            c.stop()


class TestPlaylistMapping:
    def test_missing_file_means_nothing_mapped_yet(self, tmp_path):
        assert load_playlists(tmp_path / "playlists.json") == {}

    def test_valid_mapping_round_trip(self, tmp_path):
        path = tmp_path / "playlists.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "playlists": {
                        "Pop": {"high": "spotify:playlist:aaa", "mid": "spotify:playlist:bbb"},
                        "Jazz": {"low": "spotify:playlist:ccc"},
                    },
                }
            ),
            encoding="utf-8",
        )
        mapping = load_playlists(path)
        assert mapping[("Pop", "high")] == "spotify:playlist:aaa"
        assert mapping[("Jazz", "low")] == "spotify:playlist:ccc"
        assert len(mapping) == 3

    def test_curation_typos_fail_loudly(self, tmp_path):
        """A silent typo would look identical to 'nothing mapped' and waste
        a live session — malformed files must raise."""
        path = tmp_path / "playlists.json"
        path.write_text(
            json.dumps(
                {"schema_version": 1, "playlists": {"Pop": {"hgih": "x"}}}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown tier"):
            load_playlists(path)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_playlists(path)
        path.write_text(json.dumps({"playlists": {}}), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            load_playlists(path)


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
