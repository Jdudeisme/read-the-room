"""Presence gate for played_through weak positives (M5 deliverable 1).

An empty room can't veto: the 2026-07-10 trio evening banked
played_through "weak positives" for tracks that completed with nobody
home. Learn from that corpus as-is and it rewards whatever plays to
nobody, so every completion is now assessed for plausible occupancy and
the verdict — with the evidence behind it — is stamped into the record.
The gate LABELS, it never drops: `occupied: false` lines are still
written, and the tuning report excludes them from learning.

The criterion was derived from all 30 played_through records in the
corpus (docs/M5-PROPOSAL.md, deliverable 1). On the two axes every
record already carries — staleness of the last speech-certified
evidence at completion, and the track's duration — completions split
into three regimes:

- **fresh** (staleness <= fresh_s): someone was audible near the
  track's end;
- **warm handoff** (duration <= staleness <= duration + handoff_s):
  zero speech during the entire track, but the room was certified
  occupied within handoff_s of its start and never audibly emptied —
  the quiet-listener signature (Minor Blues, 07-06);
- the **middle zone** between them: speech existed mid-window, then
  the room went silent long before the end — the departure signature
  (Just the Way You Are, 07-10).

Beyond duration + handoff_s the room was already stale when the track
began. A human tap (annotation or override) inside the track's play
window is presence regardless — taps require fingers.

The same arithmetic is mirrored by scripts/tuning_report.py to filter
v1 (unstamped) records retroactively; keep the two in sync.
"""

from __future__ import annotations

import time

OCCUPIED_BASES = ("fresh", "handoff", "tap")


def assess_presence(
    staleness_s: float | None,
    duration_s: float | None,
    fresh_s: float,
    handoff_s: float,
    tap_in_window: bool,
) -> str:
    """The pure criterion: one of fresh | handoff | tap | absent | unknown.

    `unknown` means no staleness signal existed at all (headcount and
    emotion layers both disabled or never certified) and no tap landed —
    tagged so the report can count it separately rather than guess.
    """
    if staleness_s is None:
        return "tap" if tap_in_window else "unknown"
    if staleness_s <= fresh_s:
        return "fresh"
    if (
        duration_s is not None
        and duration_s > 0
        and duration_s <= staleness_s <= duration_s + handoff_s
    ):
        return "handoff"
    if tap_in_window:
        return "tap"
    return "absent"


class PresenceGate:
    """Tap bookkeeping + evidence-block construction for the live sink.

    The app notes every annotation/override POST (`note_tap`, its thread);
    the played_through sink calls `stamp` with the completion-frame state
    and the NowPlaying dict (controller worker thread). A float write/read
    race is harmless — any tap is recent enough to matter or it isn't.
    """

    def __init__(self, fresh_s: float = 60.0, handoff_s: float = 30.0):
        self.fresh_s = fresh_s
        self.handoff_s = handoff_s
        self._last_tap_ts: float | None = None

    def note_tap(self, ts: float | None = None) -> None:
        self._last_tap_ts = time.time() if ts is None else ts

    def stamp(
        self, state: dict, now_playing: dict, ts: float | None = None
    ) -> dict:
        """The `presence` block for an override record (schema v2)."""
        now = time.time() if ts is None else ts
        # Headcount staleness is the primary signal (it tracks certified
        # speech directly); emotion staleness is the same clock when the
        # headcount layer is off. Both None -> unknown.
        staleness = state.get("headcount_staleness_s")
        if staleness is None:
            staleness = state.get("emotion_staleness_s")
        track = now_playing.get("track") or {}
        duration = track.get("duration_s")
        tap_age = None if self._last_tap_ts is None else max(0.0, now - self._last_tap_ts)
        tap_in_window = (
            tap_age is not None and duration is not None and tap_age <= duration
        )
        basis = assess_presence(
            staleness, duration, self.fresh_s, self.handoff_s, tap_in_window
        )
        return {
            "occupied": basis in OCCUPIED_BASES,
            "basis": basis,
            "staleness_s": staleness,
            "track_duration_s": duration,
            "fresh_s": self.fresh_s,
            "handoff_s": self.handoff_s,
            "last_tap_age_s": None if tap_age is None else round(tap_age, 1),
        }
