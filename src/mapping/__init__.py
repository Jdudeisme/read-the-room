"""Mapping layer (M3): RoomState -> shadow music Recommendation.

Sibling consumer of the sensing seam. Depends on `sensing.state` types only —
never on the engine, audio, or model layers.
"""

from .config import MappingConfig
from .mapper import GUARD_CELL, Mapper, Recommendation, band
from .rulebook import BUCKETS, RULEBOOK

__all__ = [
    "BUCKETS",
    "GUARD_CELL",
    "Mapper",
    "MappingConfig",
    "Recommendation",
    "RULEBOOK",
    "band",
]
