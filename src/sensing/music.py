"""Music-aware emotion (M6): hear the room through the record.

M5 part (f) measured vocal music dragging certified-speech emotion by
ΔV +0.26 / ΔA +0.39 at normal listening volume — a mood-quadrant flip on
every tap, delivered with high confidence. The correction here removes a
*measured* per-track pull rather than guessing:

- **Reference taps** (engine-driven): while playback is active and a
  window has no certified speech, the emotion model runs on it anyway —
  its V/A response to music-only audio IS the bias term, as heard by our
  model through this mic. Responses accumulate into a per-track
  signature, persisted so repeat plays sharpen it across sessions.
- **Dominance ramp**: `spectral_balance.high` separates contaminated
  speech windows from clean ones by ~10x with zero overlap at the volume
  where harm was measured (07-11 corpus: clean monotone 0.014–0.031,
  same voice over pop 0.257–0.484), but overlaps at quieter volumes —
  so it scales the correction instead of gating it.
- **Correction**: `corrected = clamp(raw − β · m · signature)` on the
  per-window reading, upstream of the V/A EMAs. A shift, never a mute:
  genuine mood changes move raw and corrected identically, so the
  positive-control requirement holds unless additivity itself fails —
  which the part (f) re-run would then show. β is the additivity
  assumption made explicit and env-tunable; the gate's numbers move it.

The discount floor (no signature yet) lives in the engine: confidence
scales by `1 − γ·m` until `min_refs` reference taps exist for the
playing track.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Running mean for the first samples, then an EMA with this effective
# horizon — signatures keep adapting (mic position, volume changes)
# instead of freezing on their first impression.
_SIGNATURE_HORIZON = 20

# Persist at most this often (plus a final flush on stop); reference taps
# can land every emotion interval during silent playback.
_SAVE_INTERVAL_S = 10.0


def dominance(high_share: float, lo: float, hi: float) -> float:
    """Music-dominance weight from the window's high-band spectral share:
    0 at or below `lo` (quiet-room speech), 1 at or above `hi` (music-
    flooded), linear between. Pure and clamped."""
    if hi <= lo:
        return 1.0 if high_share >= hi else 0.0
    return min(1.0, max(0.0, (high_share - lo) / (hi - lo)))


def apply_correction(
    valence: float,
    arousal: float,
    signature: "TrackSignature",
    m: float,
    beta: float,
) -> tuple[float, float, float, float]:
    """(corrected_v, corrected_a, subtracted_v, subtracted_a). The
    subtracted amounts are published for observability — raw is always
    reconstructable from any frame."""
    dv = beta * m * signature.valence
    da = beta * m * signature.arousal
    return (
        max(-1.0, min(1.0, valence - dv)),
        max(-1.0, min(1.0, arousal - da)),
        dv,
        da,
    )


@dataclass
class TrackSignature:
    valence: float
    arousal: float
    refs: int  # reference taps accumulated

    def to_dict(self) -> dict:
        return {"valence": round(self.valence, 4),
                "arousal": round(self.arousal, 4), "refs": self.refs}


class TrackSignatureStore:
    """Per-track model-response signatures, JSON-persisted.

    Engine-thread only (add/get from the tick, flush from stop) — no
    locking needed. A missing or corrupt file is an empty store, never a
    crash: signatures are an optimization, not a dependency."""

    def __init__(self, path: Path | str | None, min_refs: int = 3):
        self.path = None if path is None else Path(path)
        self.min_refs = max(1, min_refs)
        self._signatures: dict[str, TrackSignature] = {}
        self._dirty = False
        self._last_save = 0.0
        self._load()

    def add_reference(self, track_id: str, valence: float, arousal: float) -> None:
        sig = self._signatures.get(track_id)
        if sig is None:
            self._signatures[track_id] = TrackSignature(valence, arousal, 1)
        else:
            sig.refs += 1
            alpha = 1.0 / min(sig.refs, _SIGNATURE_HORIZON)
            sig.valence += alpha * (valence - sig.valence)
            sig.arousal += alpha * (arousal - sig.arousal)
        self._dirty = True
        self._maybe_save()

    def get(self, track_id: str | None) -> TrackSignature | None:
        """The signature for a track, only once it is trustworthy
        (>= min_refs reference taps); None otherwise."""
        if track_id is None:
            return None
        sig = self._signatures.get(track_id)
        return sig if sig is not None and sig.refs >= self.min_refs else None

    def flush(self) -> None:
        if self._dirty:
            self._save()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for track_id, s in data.get("signatures", {}).items():
                self._signatures[track_id] = TrackSignature(
                    float(s["valence"]), float(s["arousal"]), int(s["refs"])
                )
            log.info("loaded %d track signature(s)", len(self._signatures))
        except Exception:
            log.exception("track signature cache unreadable; starting empty")
            self._signatures = {}

    def _maybe_save(self) -> None:
        if time.monotonic() - self._last_save >= _SAVE_INTERVAL_S:
            self._save()

    def _save(self) -> None:
        if self.path is None:
            self._dirty = False
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "signatures": {
                            t: s.to_dict() for t, s in self._signatures.items()
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self._dirty = False
            self._last_save = time.monotonic()
        except Exception:
            log.exception("failed to persist track signatures; keeping in memory")
