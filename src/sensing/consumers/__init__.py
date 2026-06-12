"""RoomState consumers. Each implements `on_state(state: RoomState) -> None`.

Consumers are deliberately thin: the engine owns all analysis, a consumer only
presents. Milestone 2's dashboard replaces the console renderer without any
engine changes.
"""

from .console import ConsoleRenderer
from .jsonl import JsonlWriter

__all__ = ["ConsoleRenderer", "JsonlWriter"]
