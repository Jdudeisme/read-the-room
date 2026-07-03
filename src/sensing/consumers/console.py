"""Live console renderer: redraws a compact status block once per RoomState."""

from __future__ import annotations

import sys
import time

from ..state import RoomState

_BAR_WIDTH = 16

_MOOD_LABEL = {
    "excited": "EXCITED",
    "tense": "TENSE",
    "chill": "CHILL",
    "flat": "FLAT",
    None: "--",
}

_GLYPHS_UNICODE = {
    "fill": "█", "empty": "░", "sep": "·",
    "rising": "↗ rising", "stable": "→ stable", "falling": "↘ falling",
}
_GLYPHS_ASCII = {
    "fill": "#", "empty": ".", "sep": "|",
    "rising": "^ rising", "stable": "= stable", "falling": "v falling",
}


def _bar(fraction: float, glyphs: dict, width: int = _BAR_WIDTH) -> str:
    fraction = min(1.0, max(0.0, fraction))
    filled = round(fraction * width)
    return glyphs["fill"] * filled + glyphs["empty"] * (width - filled)


class ConsoleRenderer:
    def __init__(self, engine=None, stream=None):
        self._engine = engine  # optional, only to show emotion layer status
        self._stream = stream or sys.stdout
        self._lines_drawn = 0
        self._ansi = self._stream.isatty()
        # Legacy Windows consoles are often cp1252 and reject the bar glyphs.
        encoding = getattr(self._stream, "encoding", None) or "ascii"
        try:
            "█░·↗→↘".encode(encoding)
            self._glyphs = _GLYPHS_UNICODE
        except (UnicodeEncodeError, LookupError):
            self._glyphs = _GLYPHS_ASCII

    def on_state(self, state: RoomState) -> None:
        g = self._glyphs
        emotion_note = self._emotion_note(state)
        clock = time.strftime("%H:%M:%S", time.localtime(state.timestamp))
        sb = state.spectral_balance
        sep = g["sep"]
        loud_frac = (state.loudness_dbfs + 60.0) / 50.0
        lines = [
            f"Read the Room {sep} M2  {clock}",
            f"  loudness  {state.loudness_dbfs:7.1f} dBFS  {_bar(loud_frac, g)}",
            f"  activity  {state.activity_density:7.2f} onset/s",
            f"  spectrum   low {sb['low']:.0%} {sep} mid {sb['mid']:.0%} {sep} high {sb['high']:.0%}",
            f"  speech    {state.speech_ratio:7.2f}       {_bar(state.speech_ratio, g)}",
            f"  emotion   {emotion_note}",
            f"  mood      {_MOOD_LABEL[state.mood]:8s} energy {state.energy:.2f}  "
            f"{g[state.trend]}",
            f"  headcount {self._headcount_note(state)}",
        ]
        self._draw(lines)

    def _headcount_note(self, state: RoomState) -> str:
        if state.headcount_bucket is not None:
            return (
                f"{state.headcount_bucket.value:8s} "
                f"(conf {state.headcount_confidence:.2f}, "
                f"{state.headcount_staleness_s:.0f}s old)"
            )
        status = self._engine.headcount_status if self._engine else "unknown"
        notes = {
            "loading": "warming up... (model loading)",
            "ready": "no speech detected yet",
            "disabled": "disabled",
            "failed": "FAILED — see log",
        }
        return notes.get(status, status)

    def _emotion_note(self, state: RoomState) -> str:
        if state.valence is not None:
            return (
                f"valence {state.valence:+.2f}  arousal {state.arousal:+.2f}  "
                f"(conf {state.emotion_confidence:.2f}, {state.emotion_staleness_s:.0f}s old)"
            )
        status = self._engine.emotion_status if self._engine else "unknown"
        notes = {
            "loading": "warming up... (model loading)",
            "ready": "no speech detected yet",
            "disabled": "disabled",
            "failed": "FAILED — see log",
        }
        return notes.get(status, status)

    def _draw(self, lines: list[str]) -> None:
        out = self._stream
        if self._ansi and self._lines_drawn:
            out.write(f"\x1b[{self._lines_drawn}F\x1b[J")  # cursor up + clear below
        out.write("\n".join(lines) + "\n")
        out.flush()
        self._lines_drawn = len(lines)
