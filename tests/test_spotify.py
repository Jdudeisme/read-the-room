"""SpotifyProvider tests: the full provider driven through httpx.MockTransport.

No network — the M4 gate's "no Spotify in tests" is honored by faking the
TRANSPORT, which (unlike stubbing the provider) also exercises auth refresh,
retry, device resolution, and error mapping.
"""

import json
import time
import urllib.parse

import httpx
import pytest

from playback import PlaybackConfig, PlaybackProvider, ProviderError, Track
from playback.spotify import (
    SpotifyProvider,
    bare_playlist_id,
    load_token_cache,
    save_token_cache,
)
from playback.spotify_auth import build_authorize_url, pkce_pair

PLAYLISTS = {("Pop", "high"): "spotify:playlist:PL1"}


def _track(uri="spotify:track:t1"):
    return Track(id=uri, title="Song", artist="A", duration_s=180.0, playlist_id=None)


class FakeSpotify:
    """MockTransport handler simulating the six endpoints + token refresh."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.player_state: dict | None = None  # None -> 204
        self.devices = [
            {"id": "dev-mac", "name": "MacBook Pro", "is_active": False},
            {"id": "dev-spk", "name": "Living Room Speaker", "is_active": True},
        ]
        self.playlist_items = [
            {"track": {"uri": "spotify:track:a", "name": "Alpha",
                       "artists": [{"name": "X"}], "duration_ms": 200000}},
            {"track": {"uri": "spotify:track:b", "name": "Beta",
                       "artists": [{"name": "Y"}, {"name": "Z"}],
                       "duration_ms": 100000}},
            {"track": None},  # removed entry: must be skipped
        ]
        self.fail_all = False
        self.reject_token = False  # force 401 once per request until refreshed
        self.refreshes = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_all:
            raise httpx.ConnectError("boom", request=request)
        path = request.url.path
        if path == "/api/token":
            self.refreshes += 1
            self.reject_token = False
            return httpx.Response(
                200,
                json={"access_token": "fresh", "expires_in": 3600,
                      "refresh_token": "refresh-2"},
            )
        if self.reject_token:
            return httpx.Response(401, json={"error": "expired"})
        if path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        if path == "/v1/me/player/play":
            return httpx.Response(204)
        if path == "/v1/me/player/queue":
            return httpx.Response(204)
        if path == "/v1/me/player/pause":
            return httpx.Response(204)
        if path == "/v1/me/player":
            if self.player_state is None:
                return httpx.Response(204)
            return httpx.Response(200, json=self.player_state)
        if path.startswith("/v1/playlists/"):
            return httpx.Response(200, json={"items": self.playlist_items})
        return httpx.Response(404, json={"error": "unknown path"})


@pytest.fixture
def fake():
    return FakeSpotify()


@pytest.fixture
def config(tmp_path):
    cfg = PlaybackConfig(
        enabled=True,
        client_id="client-123",
        token_cache_path=str(tmp_path / "token.json"),
        playlists_path=str(tmp_path / "playlists.json"),
    )
    save_token_cache(
        tmp_path / "token.json",
        {"access_token": "ok", "refresh_token": "refresh-1",
         "expires_at": time.time() + 3600},
    )
    return cfg


def _provider(config, fake, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return SpotifyProvider(config, playlists=dict(PLAYLISTS), client=client, **kwargs)


class TestSeam:
    def test_satisfies_the_playback_protocol(self, config, fake):
        assert isinstance(_provider(config, fake), PlaybackProvider)

    def test_devices_parsed(self, config, fake):
        devices = _provider(config, fake).devices()
        assert [d.id for d in devices] == ["dev-mac", "dev-spk"]
        assert devices[1].active

    def test_tracks_for_maps_playlist_and_skips_dead_entries(self, config, fake):
        tracks = _provider(config, fake).tracks_for("Pop", "high")
        assert [t.id for t in tracks] == ["spotify:track:a", "spotify:track:b"]
        assert tracks[1].artist == "Y, Z"
        assert tracks[0].playlist_id == "spotify:playlist:PL1"
        # the request used the bare id, not the uri
        assert "/v1/playlists/PL1/tracks" in str(fake.requests[-1].url)

    def test_tracks_for_unmapped_is_empty_without_a_request(self, config, fake):
        assert _provider(config, fake).tracks_for("Jazz", "low") == []
        assert fake.requests == []

    def test_now_playing_none_when_no_session(self, config, fake):
        assert _provider(config, fake).now_playing() is None

    def test_now_playing_parsed(self, config, fake):
        fake.player_state = {
            "is_playing": True,
            "progress_ms": 42500,
            "device": {"id": "dev-spk"},
            "context": {"type": "playlist", "uri": "spotify:playlist:PL1"},
            "item": {"uri": "spotify:track:a", "name": "Alpha",
                     "artists": [{"name": "X"}], "duration_ms": 200000},
        }
        now = _provider(config, fake).now_playing()
        assert now.track.id == "spotify:track:a"
        assert now.track.playlist_id == "spotify:playlist:PL1"
        assert now.progress_s == pytest.approx(42.5)
        assert now.is_playing and now.device_id == "dev-spk"

    def test_play_and_queue_hit_the_control_endpoints(self, config, fake):
        p = _provider(config, fake)
        p.play(_track())
        assert json.loads(fake.requests[-1].content) == {"uris": ["spotify:track:t1"]}
        p.queue(_track("spotify:track:t2"))
        q = urllib.parse.parse_qs(fake.requests[-1].url.query.decode())
        assert q["uri"] == ["spotify:track:t2"]


class TestDeviceTargeting:
    def test_configured_name_substring_resolves_to_device_id(self, tmp_path, fake):
        cfg = PlaybackConfig(
            client_id="client-123",
            device_name="living room",  # case-insensitive substring
            token_cache_path=str(tmp_path / "token.json"),
        )
        save_token_cache(
            tmp_path / "token.json",
            {"access_token": "ok", "refresh_token": "r",
             "expires_at": time.time() + 3600},
        )
        p = SpotifyProvider(
            cfg, playlists={}, client=httpx.Client(transport=httpx.MockTransport(fake))
        )
        p.play(_track())
        q = urllib.parse.parse_qs(fake.requests[-1].url.query.decode())
        assert q["device_id"] == ["dev-spk"]

    def test_missing_device_is_a_provider_error(self, tmp_path, fake):
        cfg = PlaybackConfig(
            client_id="client-123",
            device_name="garage",
            token_cache_path=str(tmp_path / "token.json"),
        )
        save_token_cache(
            tmp_path / "token.json",
            {"access_token": "ok", "refresh_token": "r",
             "expires_at": time.time() + 3600},
        )
        p = SpotifyProvider(
            cfg, playlists={}, client=httpx.Client(transport=httpx.MockTransport(fake))
        )
        with pytest.raises(ProviderError, match="garage"):
            p.play(_track())


class TestAuth:
    def test_missing_cache_is_a_clear_provider_error(self, tmp_path, fake):
        cfg = PlaybackConfig(
            client_id="client-123",
            token_cache_path=str(tmp_path / "nope.json"),
        )
        p = SpotifyProvider(
            cfg, playlists={}, client=httpx.Client(transport=httpx.MockTransport(fake))
        )
        with pytest.raises(ProviderError, match="spotify-auth"):
            p.devices()

    def test_expired_token_refreshes_and_persists(self, tmp_path, fake, config):
        save_token_cache(
            tmp_path / "token.json",
            {"access_token": "stale", "refresh_token": "refresh-1",
             "expires_at": time.time() - 10},
        )
        p = _provider(config, fake)
        p.devices()
        assert fake.refreshes == 1
        cache = load_token_cache(tmp_path / "token.json")
        assert cache["access_token"] == "fresh"
        assert cache["refresh_token"] == "refresh-2"

    def test_401_mid_session_refreshes_once_and_retries(self, config, fake):
        p = _provider(config, fake)
        fake.reject_token = True  # cache says valid, API disagrees
        devices = p.devices()
        assert devices  # retry after refresh succeeded
        assert fake.refreshes == 1

    def test_network_failure_maps_to_provider_error(self, config, fake):
        p = _provider(config, fake)
        fake.fail_all = True
        with pytest.raises(ProviderError, match="unreachable"):
            p.now_playing()

    def test_requires_client_id(self, tmp_path):
        with pytest.raises(ValueError, match="CLIENT_ID"):
            SpotifyProvider(PlaybackConfig(), playlists={})


class TestHelpers:
    @pytest.mark.parametrize(
        "raw",
        [
            "PL1",
            "spotify:playlist:PL1",
            "https://open.spotify.com/playlist/PL1?si=abc123",
        ],
    )
    def test_playlist_id_normalization(self, raw):
        assert bare_playlist_id(raw) == "PL1"

    def test_pkce_pair_is_rfc7636_s256(self):
        import base64
        import hashlib

        verifier, challenge = pkce_pair()
        assert 43 <= len(verifier) <= 128
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

    def test_authorize_url_carries_pkce_and_state(self):
        url = build_authorize_url("cid", "http://127.0.0.1:8912/callback", "chal", "st8")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["client_id"] == ["cid"]
        assert q["code_challenge"] == ["chal"]
        assert q["code_challenge_method"] == ["S256"]
        assert q["state"] == ["st8"]
        assert "user-modify-playback-state" in q["scope"][0]
