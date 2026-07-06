"""Playback layer (Milestone 4): turns Mapper Recommendations into music.

Everything downstream of the mapping seam and outside the engine path —
provider protocol, playlist-mapped selection, gentle-DJ queueing policy.
"""

from .provider import (
    ENERGY_TIERS,
    Device,
    NowPlaying,
    PlaybackProvider,
    ProviderError,
    Track,
)

__all__ = [
    "ENERGY_TIERS",
    "Device",
    "NowPlaying",
    "PlaybackProvider",
    "ProviderError",
    "Track",
]
