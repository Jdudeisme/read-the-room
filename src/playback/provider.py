"""PlaybackProvider: the M4 seam between mapping output and a music service.

The seam is deliberately narrow — six methods, control traffic only. Audio
decode/output never happens in our process (a provider issues a few HTTP
calls per track, or shells out to a local player), so the playback layer
adds ~zero compute to the engine path and the M2/M3 performance gate
carries over unchanged.

Design constraints, per the approved M4 proposal:

- **Playlist-mapped selection, not algorithmic.** `tracks_for(genre, tier)`
  resolves through human-curated playlists (data/playlists.json binds
  rulebook genre pools x energy tiers to playlist IDs). Nothing at the seam
  assumes recommendation/audio-features endpoints exist — Spotify deprecated
  them for new apps (Nov 2024), and the 2020 GenrePicker lineage keeps
  human curation in the loop anyway.
- **Failure isolation.** Everything a provider can't do raises
  ProviderError (or returns an honest empty/None). Callers catch it at the
  seam, surface it on the dashboard, and degrade to shadow mode; the
  sensing/mapping side never blocks on playback I/O.
- **Tests mock at this seam.** No network, no Spotify in tests — a fake
  in-memory provider satisfies the protocol (see tests/test_playback.py).

M4 ships one real implementation (Spotify via OAuth PKCE, controlling a
Spotify Connect device); a LocalLibraryProvider (folder of files +
afplay/ffplay) is the documented fallback if the platform shifts underneath
us.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

# Energy tiers for playlist mapping, derived from Recommendation
# target_arousal (cutoffs live in playback config as RTR_PLAYBACK_TIER_*).
ENERGY_TIERS = ("low", "mid", "high")


class ProviderError(RuntimeError):
    """A provider-side failure: token expiry, device gone, rate limit, dead
    player. Recoverable by design — callers degrade to shadow mode and keep
    consuming Mapper emissions; they never crash the dashboard process."""


@dataclass(frozen=True)
class Device:
    """A playback target (e.g. a Spotify Connect device)."""

    id: str
    name: str
    active: bool  # currently the provider's selected output


@dataclass(frozen=True)
class Track:
    id: str  # provider-scoped id/URI; opaque outside the provider
    title: str
    artist: str
    duration_s: float | None
    playlist_id: str | None  # the mapped playlist it was drawn from, if any

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(**d)


@dataclass(frozen=True)
class NowPlaying:
    """What the provider reports is audible right now. Serialized into
    override records (M4 deliverable 2), so it round-trips through dicts
    like Recommendation does."""

    track: Track
    progress_s: float
    is_playing: bool  # False while paused (still "loaded" on the device)
    device_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NowPlaying":
        d = dict(d)
        d["track"] = Track.from_dict(d["track"])
        return cls(**d)


@runtime_checkable
class PlaybackProvider(Protocol):
    """The six-method seam. Implementations own auth, transport, and player
    quirks; callers own policy (gentle-DJ queueing, tier derivation,
    recently-played suppression) so policy is testable without a service."""

    def devices(self) -> list[Device]:
        """Available playback targets. Empty list is honest (no device online)."""
        ...

    def play(self, track: Track) -> None:
        """Start `track` now, interrupting whatever is playing. Reserved for
        human overrides — the gentle-DJ policy never calls this."""
        ...

    def queue(self, track: Track) -> None:
        """Append `track` to the device's play queue. APPEND-ONLY: the real
        Spotify API offers no replace or remove, so queued tracks play FIFO
        and callers must keep at most one selection outstanding (the
        controller defers its push to the boundary window for this reason)."""
        ...

    def pause(self) -> None:
        """Pause playback on the current device."""
        ...

    def now_playing(self) -> NowPlaying | None:
        """Current audible state, or None when nothing is loaded/playing."""
        ...

    def tracks_for(self, genre: str, tier: str) -> list[Track]:
        """Candidate tracks for a rulebook genre + energy tier, resolved via
        the curated playlist mapping. Empty list when no playlist is mapped —
        the caller decides whether to fall back to another cell or hold."""
        ...
