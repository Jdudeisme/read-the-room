"""Track selection: Recommendation -> a concrete track, via curated playlists.

Selection is playlist-mapped, not algorithmic (M4 proposal deliverable 1):
the provider resolves (genre, tier) through the human-curated mapping, and
this module only picks WHICH mapped track — not-recently-played, uniformly
at random. Human curation stays in the loop, the direct descendant of the
2020 GenrePicker lineage, and nothing here touches deprecated
recommendation/audio-features endpoints.
"""

from __future__ import annotations

import logging
import random
from collections import deque

from .config import PlaybackConfig
from .provider import ENERGY_TIERS, PlaybackProvider, Track

log = logging.getLogger(__name__)


def derive_tier(
    target_arousal: float, energy_action: str, config: PlaybackConfig
) -> str:
    """Recommendation (target_arousal, energy_action) -> energy tier.

    Base tier bands target_arousal with mapping.band() semantics; then
    energy_action shifts one tier within the ladder ("raise" -> up,
    "lower" -> down, clamped at the ends). Volume is never touched —
    energy moves only through selection (gentle-DJ policy).
    """
    if target_arousal > config.tier_high_min:
        base = "high"
    elif target_arousal > config.tier_low_max:
        base = "mid"
    else:
        base = "low"
    shift = {"raise": 1, "lower": -1}.get(energy_action, 0)
    idx = ENERGY_TIERS.index(base) + shift
    return ENERGY_TIERS[max(0, min(len(ENERGY_TIERS) - 1, idx))]


class TrackSelector:
    """Pick a not-recently-played track for (genre_pool, tier).

    Genres are tried in pool order (the rulebook lists them by priority);
    the first genre with any mapped tracks wins. Within a genre the pick is
    uniform over tracks outside the recently-played window — and if the
    window has swallowed the whole playlist, over everything (repeating
    beats silence). Returns None only when NO genre in the pool has a
    mapped playlist for the tier; the caller decides whether to hold.

    ProviderError propagates: selection is always caller-initiated provider
    I/O, and the caller (PlaybackController) owns degradation.
    """

    def __init__(
        self,
        provider: PlaybackProvider,
        recently_played_window: int = 10,
        seed: int | None = None,
    ):
        self._provider = provider
        self._recent: deque[str] = deque(maxlen=max(1, recently_played_window))
        self._rng = random.Random(seed)

    def select(self, genre_pool: list[str], tier: str) -> Track | None:
        for genre in genre_pool:
            candidates = self._provider.tracks_for(genre, tier)
            if not candidates:
                continue
            fresh = [t for t in candidates if t.id not in self._recent]
            choice = self._rng.choice(fresh if fresh else candidates)
            self._recent.append(choice.id)
            return choice
        log.info("no mapped playlist for pool=%s tier=%s", genre_pool, tier)
        return None
