"""Playback layer (Milestone 4): turns Mapper Recommendations into music.

Everything downstream of the mapping seam and outside the engine path —
provider protocol, playlist-mapped selection, gentle-DJ queueing policy.
"""

from .config import PlaybackConfig
from .controller import PlaybackController
from .playlists import load_playlists
from .provider import (
    ENERGY_TIERS,
    Device,
    NowPlaying,
    PlaybackProvider,
    ProviderError,
    Track,
)
from .selector import TrackSelector, derive_tier
from .spotify import SpotifyProvider

__all__ = [
    "ENERGY_TIERS",
    "Device",
    "NowPlaying",
    "PlaybackConfig",
    "PlaybackController",
    "PlaybackProvider",
    "ProviderError",
    "SpotifyProvider",
    "Track",
    "TrackSelector",
    "derive_tier",
    "load_playlists",
]
