"""Playback seam tests: the provider protocol and its wire types.

No network, no Spotify — per the M4 gate, providers are mocked at the seam.
FakeProvider is the reference in-memory implementation the selector/policy
tests will build on.
"""

import json

import pytest

from playback import (
    ENERGY_TIERS,
    Device,
    NowPlaying,
    PlaybackProvider,
    ProviderError,
    Track,
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
