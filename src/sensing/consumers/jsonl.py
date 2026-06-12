"""JSONL consumer: one RoomState per line. Useful for debugging, capture
sessions, and as the reference for the M2 dashboard's wire format."""

from __future__ import annotations

import json
from pathlib import Path

from ..state import RoomState


class JsonlWriter:
    def __init__(self, path: str | Path):
        self._file = open(path, "a", encoding="utf-8", buffering=1)

    def on_state(self, state: RoomState) -> None:
        self._file.write(json.dumps(state.to_dict()) + "\n")

    def close(self) -> None:
        self._file.close()
