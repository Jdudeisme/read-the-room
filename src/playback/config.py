"""Playback-layer configuration (RTR_PLAYBACK_* env vars, .env supported).

Same pattern as sensing/mapping config: frozen dataclass, from_env, every
tunable an env var documented in .env.example. The tier cutoffs are
boundaries the tuning loop can move like any other (M4 proposal, risks
section): target_arousal -> tier is a crude scalar -> 3-tier map by design.
"""

from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from sensing.config import _env_bool, _env_float, _env_int, _env_str


@dataclass(frozen=True)
class PlaybackConfig:
    # Playback is opt-in; without it the dashboard stays in M3 shadow mode.
    enabled: bool = False

    # Spotify app credentials (Authorization Code + PKCE — no client secret).
    client_id: str | None = None
    redirect_port: int = 8912  # localhost OAuth callback port

    # Spotify Connect device to control: name substring, case-insensitive.
    # None targets whatever device is currently active.
    device_name: str | None = None

    # Curated playlist mapping, (genre, tier) -> playlist id. Gitignored
    # local data, like annotations (data/* is ignored).
    playlists_path: str = "data/playlists.json"
    token_cache_path: str = "data/spotify_token.json"

    # Track selection: suppress the last N selected tracks when picking.
    recently_played_window: int = 10

    # target_arousal -> energy tier cutoffs. Band semantics match
    # mapping.band(): strictly above high_min is "high", strictly above
    # low_max is "mid", else "low".
    tier_low_max: float = -0.25
    tier_high_min: float = 0.25

    # Controller worker: how often the cached now-playing state refreshes
    # between recommendations (track boundaries are observed at this rate).
    poll_interval_s: float = 5.0

    # Boundary window (seconds). The held next-up selection is pushed to the
    # provider's APPEND-ONLY queue when the playing track's remaining time
    # drops under this, and a track only counts as played-through when its
    # last observed progress is inside this window of its end. Keep
    # comfortably above poll_interval_s or boundaries get missed.
    queue_lead_s: float = 15.0

    @classmethod
    def from_env(cls) -> "PlaybackConfig":
        load_dotenv()  # no-op if no .env file
        client_id = _env_str("RTR_PLAYBACK_CLIENT_ID", "") or None
        device_name = _env_str("RTR_PLAYBACK_DEVICE_NAME", "") or None
        return cls(
            enabled=_env_bool("RTR_PLAYBACK_ENABLED", cls.enabled),
            client_id=client_id,
            redirect_port=_env_int("RTR_PLAYBACK_REDIRECT_PORT", cls.redirect_port),
            device_name=device_name,
            playlists_path=_env_str("RTR_PLAYBACK_PLAYLISTS_PATH", cls.playlists_path),
            token_cache_path=_env_str(
                "RTR_PLAYBACK_TOKEN_CACHE_PATH", cls.token_cache_path
            ),
            recently_played_window=_env_int(
                "RTR_PLAYBACK_RECENT_WINDOW", cls.recently_played_window
            ),
            tier_low_max=_env_float("RTR_PLAYBACK_TIER_LOW_MAX", cls.tier_low_max),
            tier_high_min=_env_float("RTR_PLAYBACK_TIER_HIGH_MIN", cls.tier_high_min),
            poll_interval_s=_env_float(
                "RTR_PLAYBACK_POLL_INTERVAL_S", cls.poll_interval_s
            ),
            queue_lead_s=_env_float("RTR_PLAYBACK_QUEUE_LEAD_S", cls.queue_lead_s),
        )
