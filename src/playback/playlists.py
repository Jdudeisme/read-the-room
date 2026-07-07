"""The curated playlist mapping: data/playlists.json. The committed copy is
the founder's baseline; a user running their own instance overrides it by
editing the file locally without committing.

Format (schema_version 1):

    {
      "schema_version": 1,
      "playlists": {
        "Pop":  {"high": "spotify:playlist:...", "mid": "spotify:playlist:..."},
        "Jazz": {"low": "spotify:playlist:..."}
      }
    }

Genres are rulebook genre-pool names, verbatim; tiers are ENERGY_TIERS.
Partial coverage is expected while curation catches up — an unmapped
(genre, tier) simply yields no tracks and the selector moves on — but a
malformed file fails LOUDLY: silent curation typos would look identical to
"nothing mapped" and waste a live session.
"""

from __future__ import annotations

import json
from pathlib import Path

from .provider import ENERGY_TIERS

SCHEMA_VERSION = 1


def load_playlists(path: Path) -> dict[tuple[str, str], str]:
    """(genre, tier) -> playlist id. Missing file = nothing mapped yet."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version must be {SCHEMA_VERSION}, "
            f"got {raw.get('schema_version')!r}"
        )
    playlists = raw.get("playlists")
    if not isinstance(playlists, dict):
        raise ValueError(f"{path}: 'playlists' must be an object")
    mapping: dict[tuple[str, str], str] = {}
    for genre, tiers in playlists.items():
        if not isinstance(tiers, dict):
            raise ValueError(f"{path}: playlists[{genre!r}] must be an object")
        for tier, playlist_id in tiers.items():
            if tier not in ENERGY_TIERS:
                raise ValueError(
                    f"{path}: unknown tier {tier!r} for {genre!r} "
                    f"(expected one of {ENERGY_TIERS})"
                )
            if not isinstance(playlist_id, str) or not playlist_id:
                raise ValueError(
                    f"{path}: playlist id for ({genre!r}, {tier!r}) "
                    "must be a non-empty string"
                )
            mapping[(genre, tier)] = playlist_id
    return mapping
