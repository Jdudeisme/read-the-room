"""Spotify playback provider: raw httpx over the six endpoints we use.

Why raw httpx instead of spotipy (the decision the M4 proposal deferred):
the seam needs exactly six API calls, Authorization Code + PKCE needs no
client secret, and spotipy's extra surface wraps endpoints (recommendations,
audio features) that are deprecated for new apps and that playlist-mapped
selection deliberately does not build on. Owning ~200 lines of HTTP keeps
the dependency footprint one well-understood library.

Control traffic only: nothing here decodes or outputs audio. Every failure
a caller can hit surfaces as ProviderError (with `.status` carrying the
HTTP code when there was one) so the controller can degrade to shadow mode.

Endpoints:
  GET  /v1/me/player/devices          devices()
  PUT  /v1/me/player/play             play()      (interrupts; overrides only)
  POST /v1/me/player/queue            queue()
  PUT  /v1/me/player/pause            pause()
  GET  /v1/me/player                  now_playing()
  GET  /v1/playlists/{id}/tracks      tracks_for()

Known deviation from the seam docstring: Spotify's queue is append-only, so
`queue()` cannot REPLACE an already-queued next track. In practice the
gentle-DJ controller queues at most one track per recommendation dwell
(>= 30 s apart), so drift is a rare extra queued track, not a pile-up —
record the observed behavior in FIELD-NOTES during the M4 gate session.

First-time auth is interactive: run `read-the-room-spotify-auth` once (see
spotify_auth.py); after that the token cache refreshes itself.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from .config import PlaybackConfig
from .playlists import load_playlists
from .provider import Device, NowPlaying, ProviderError, Track

log = logging.getLogger(__name__)

ACCOUNTS_BASE = "https://accounts.spotify.com"
TOKEN_URL = f"{ACCOUNTS_BASE}/api/token"
API_BASE = "https://api.spotify.com/v1"

_EXPIRY_MARGIN_S = 30.0  # refresh this many seconds before nominal expiry


# -- token cache (shared with spotify_auth) ----------------------------------


def load_token_cache(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ProviderError(f"unreadable token cache {path}: {exc}") from exc


def save_token_cache(path: Path, cache: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")


def token_cache_from_response(payload: dict, fallback_refresh: str | None = None) -> dict:
    """Normalize a token endpoint response into our cache format."""
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or fallback_refresh,
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
    }


def bare_playlist_id(configured: str) -> str:
    """Accept 'spotify:playlist:ID', an open.spotify.com URL, or a bare ID."""
    value = configured.strip()
    if value.startswith("spotify:playlist:"):
        return value.split(":")[-1]
    if "open.spotify.com/playlist/" in value:
        tail = value.split("open.spotify.com/playlist/", 1)[1]
        return tail.split("?")[0].strip("/")
    return value


def _error(message: str, status: int | None = None) -> ProviderError:
    err = ProviderError(message)
    err.status = status
    return err


class SpotifyProvider:
    """PlaybackProvider over the Spotify Web API (Spotify Connect control).

    `client` is injectable so tests drive the full provider through
    httpx.MockTransport — the M4 gate's "provider mocked at the seam" is
    mocked at the TRANSPORT here, which also exercises auth/retry logic.
    """

    def __init__(
        self,
        config: PlaybackConfig,
        playlists: dict[tuple[str, str], str] | None = None,
        client: httpx.Client | None = None,
    ):
        if not config.client_id:
            raise ValueError("SpotifyProvider requires RTR_PLAYBACK_CLIENT_ID")
        self._config = config
        self._playlists = (
            playlists
            if playlists is not None
            else load_playlists(Path(config.playlists_path))
        )
        self._client = client or httpx.Client(timeout=10.0)
        self._token: dict | None = None
        self._device: str | None = None  # resolved device id cache

    # -- auth -----------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token is None:
            self._token = load_token_cache(Path(self._config.token_cache_path))
        if self._token is None:
            raise _error(
                "not authenticated: run read-the-room-spotify-auth once "
                f"(no token cache at {self._config.token_cache_path})"
            )
        if time.time() >= float(self._token.get("expires_at", 0)) - _EXPIRY_MARGIN_S:
            self._refresh()
        return self._token["access_token"]

    def _refresh(self) -> None:
        refresh_token = (self._token or {}).get("refresh_token")
        if not refresh_token:
            raise _error("token cache has no refresh_token; re-run spotify auth")
        try:
            res = self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._config.client_id,
                },
            )
        except httpx.HTTPError as exc:
            raise _error(f"token refresh failed: {exc}") from exc
        if res.status_code != 200:
            raise _error(
                f"token refresh rejected ({res.status_code}): {res.text[:200]}",
                res.status_code,
            )
        self._token = token_cache_from_response(res.json(), refresh_token)
        save_token_cache(Path(self._config.token_cache_path), self._token)
        log.info("spotify access token refreshed")

    # -- transport ------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, _retry_auth: bool = True, **kwargs
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            res = self._client.request(
                method, API_BASE + path, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise _error(f"spotify unreachable: {exc}") from exc
        if res.status_code == 401 and _retry_auth:
            self._refresh()  # token revoked/expired early; retry once
            return self._request(method, path, _retry_auth=False, **kwargs)
        if res.status_code == 429:
            raise _error("spotify rate limited (429)", 429)
        if res.status_code >= 400:
            raise _error(
                f"spotify {method} {path} -> {res.status_code}: {res.text[:200]}",
                res.status_code,
            )
        return res

    def _player_params(self) -> dict:
        """Target the configured device by name substring; empty params let
        Spotify use whatever device is active."""
        if self._config.device_name is None:
            return {}
        if self._device is None:
            wanted = self._config.device_name.lower()
            matches = [d for d in self.devices() if wanted in d.name.lower()]
            if not matches:
                raise _error(
                    f"no Spotify Connect device matching "
                    f"{self._config.device_name!r} is online"
                )
            self._device = matches[0].id
        return {"device_id": self._device}

    # -- the six seam methods ---------------------------------------------------

    def devices(self) -> list[Device]:
        data = self._request("GET", "/me/player/devices").json()
        return [
            Device(id=d["id"], name=d["name"], active=bool(d.get("is_active")))
            for d in data.get("devices", [])
            if d.get("id")
        ]

    def play(self, track: Track) -> None:
        try:
            self._request(
                "PUT",
                "/me/player/play",
                params=self._player_params(),
                json={"uris": [track.id]},
            )
        except ProviderError:
            self._device = None  # device may be gone; re-resolve on recovery
            raise

    def queue(self, track: Track) -> None:
        try:
            self._request(
                "POST",
                "/me/player/queue",
                params={"uri": track.id, **self._player_params()},
            )
        except ProviderError:
            self._device = None
            raise

    def pause(self) -> None:
        try:
            self._request("PUT", "/me/player/pause", params=self._player_params())
        except ProviderError as exc:
            if getattr(exc, "status", None) == 403:
                return  # already paused / restriction — the goal state holds
            self._device = None
            raise

    def now_playing(self) -> NowPlaying | None:
        res = self._request("GET", "/me/player")
        if res.status_code == 204 or not res.content:
            return None  # no active session anywhere
        data = res.json()
        item = data.get("item")
        if item is None or not item.get("uri"):
            return None
        context = data.get("context") or {}
        track = Track(
            id=item["uri"],
            title=item.get("name", ""),
            artist=", ".join(a.get("name", "") for a in item.get("artists", [])),
            duration_s=(item.get("duration_ms") or 0) / 1000.0,
            playlist_id=context.get("uri")
            if context.get("type") == "playlist"
            else None,
        )
        return NowPlaying(
            track=track,
            progress_s=(data.get("progress_ms") or 0) / 1000.0,
            is_playing=bool(data.get("is_playing")),
            device_id=(data.get("device") or {}).get("id"),
        )

    def tracks_for(self, genre: str, tier: str) -> list[Track]:
        configured = self._playlists.get((genre, tier))
        if configured is None:
            return []
        # /playlists/{id}/items with entries keyed "item" — apps registered
        # after Spotify's Nov 2024 API changes get a hard 403 from the older
        # /tracks endpoint ("track" entries), even on the user's own playlists.
        res = self._request(
            "GET",
            f"/playlists/{bare_playlist_id(configured)}/items",
            params={
                # First 100 tracks: curated playlists are human-scale, and
                # the selector only needs a pool, not completeness.
                "limit": 100,
                "fields": "items(item(uri,name,artists(name),duration_ms))",
            },
        )
        tracks = []
        for entry in res.json().get("items", []):
            t = entry.get("item") or {}
            if not t.get("uri"):
                continue  # removed or local-file entries
            tracks.append(
                Track(
                    id=t["uri"],
                    title=t.get("name", ""),
                    artist=", ".join(a.get("name", "") for a in t.get("artists", [])),
                    duration_s=(t.get("duration_ms") or 0) / 1000.0,
                    playlist_id=configured,
                )
            )
        return tracks
