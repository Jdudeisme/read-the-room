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
- **Pull signature** (the 2026-07-11 gate iteration): additivity failed
  its test — the model's read of speech-over-music is super-additive
  (measured valence pull ~4x the record's standalone signature, arousal
  ~1.5x; FIELD-NOTES 2026-07-11 afternoon), so no scalar on the
  music-only signature cancels both axes. The estimator now measures
  the interaction directly: while a **clean-speech baseline** is fresh
  (readings taken with playback off or dominance ~ 0), every
  speech-over-music reading contributes a pull sample
  `(reading − baseline) / m` to the track's pull signature. The
  correction subtracts the measured pull; the standalone signature
  remains only as a capped cold-start prior (scaled per axis by the
  gate-measured super-additivity ratios) until pull samples exist.
- **Correction**: `corrected = clamp(raw − β_axis · m · pull)` on the
  per-window reading, upstream of the V/A EMAs, magnitude-capped. A
  shift, never a mute: genuine mood changes move raw and corrected
  identically, so the positive-control requirement holds (part (d)
  2026-07-11 set the live bar: +0.68 arousal separation).

The discount floor (no usable signature yet) lives in the engine:
confidence scales by `1 − γ·m` until the playing track has evidence.

Known trade-off, accepted deliberately: a genuine mood change occurring
while the clean baseline is still fresh banks mood-shifted pull samples
(the estimator can't tell "the song reads happy" from "the room got
happy in the last five minutes"). Three guards bound the damage — the
baseline age limit stops banking once the no-music read is stale, the
dominance floor sheds most animated-speech windows (measured m
0.17–0.33 when speech wins), and the signature's EMA horizon washes
transients out. The part (d) positive-control bar is the regression
check that these guards suffice.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .state import Ema

log = logging.getLogger(__name__)

# v2 (2026-07-11 gate iteration): signatures carry pull_* fields — the
# measured speech-over-music interaction — alongside the standalone
# response. v1 files load with pull fields empty.
SCHEMA_VERSION = 2

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
    pull_v: float,
    pull_a: float,
    m: float,
    beta_v: float,
    beta_a: float,
    cap: float,
) -> tuple[float, float, float, float]:
    """(corrected_v, corrected_a, subtracted_v, subtracted_a). Per-axis
    betas because the two axes measured different super-additivity; the
    magnitude cap bounds the damage of a bad estimate. The subtracted
    amounts are published for observability — raw is always
    reconstructable from any frame."""

    def _capped(x: float) -> float:
        return max(-cap, min(cap, x))

    dv = _capped(beta_v * m * pull_v)
    da = _capped(beta_a * m * pull_a)
    return (
        max(-1.0, min(1.0, valence - dv)),
        max(-1.0, min(1.0, arousal - da)),
        dv,
        da,
    )


class CleanBaseline:
    """The room's emotion read absent music: an EMA over readings taken
    while playback is off or dominance ~ 0. Pull samples are only banked
    while this is fresh — a stale baseline would launder mood drift into
    a track's pull signature. Engine-thread only."""

    def __init__(self, tau_s: float = 20.0):
        self._valence = Ema(tau_s)
        self._arousal = Ema(tau_s)
        self._last_update: float | None = None

    def update(self, valence: float, arousal: float, now: float) -> None:
        self._valence.update(valence, now)
        self._arousal.update(arousal, now)
        self._last_update = now

    def get(self, now: float, max_age_s: float) -> tuple[float, float] | None:
        if self._last_update is None or now - self._last_update > max_age_s:
            return None
        return self._valence.value, self._arousal.value


@dataclass
class TrackSignature:
    # Standalone response: the model on music-only windows (reference
    # taps). Cold-start prior only — additivity measurably fails.
    valence: float
    arousal: float
    refs: int  # reference taps accumulated
    # Measured pull: (mixed reading − clean baseline) / dominance,
    # accumulated from speech-over-music windows. The real estimator.
    pull_valence: float = 0.0
    pull_arousal: float = 0.0
    pull_refs: int = 0

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "refs": self.refs,
            "pull_valence": round(self.pull_valence, 4),
            "pull_arousal": round(self.pull_arousal, 4),
            "pull_refs": self.pull_refs,
        }


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
        sig = self._signatures.setdefault(track_id, TrackSignature(0.0, 0.0, 0))
        sig.refs += 1
        alpha = 1.0 / min(sig.refs, _SIGNATURE_HORIZON)
        sig.valence += alpha * (valence - sig.valence)
        sig.arousal += alpha * (arousal - sig.arousal)
        self._dirty = True
        self._maybe_save()

    def add_pull_reference(
        self, track_id: str, pull_valence: float, pull_arousal: float
    ) -> None:
        """One measured pull sample: (mixed reading − fresh clean
        baseline) / dominance. Same adaptive-mean update as the
        standalone response."""
        sig = self._signatures.setdefault(track_id, TrackSignature(0.0, 0.0, 0))
        sig.pull_refs += 1
        alpha = 1.0 / min(sig.pull_refs, _SIGNATURE_HORIZON)
        sig.pull_valence += alpha * (pull_valence - sig.pull_valence)
        sig.pull_arousal += alpha * (pull_arousal - sig.pull_arousal)
        self._dirty = True
        self._maybe_save()

    def lookup(self, track_id: str | None) -> TrackSignature | None:
        """The raw signature regardless of evidence counts; the caller
        picks a basis from refs/pull_refs against min_refs."""
        return None if track_id is None else self._signatures.get(track_id)

    def get(self, track_id: str | None) -> TrackSignature | None:
        """The signature for a track, only once its standalone response is
        trustworthy (>= min_refs reference taps); None otherwise."""
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
                    float(s["valence"]),
                    float(s["arousal"]),
                    int(s["refs"]),
                    # v1 files predate the pull estimator; empty is honest.
                    float(s.get("pull_valence", 0.0)),
                    float(s.get("pull_arousal", 0.0)),
                    int(s.get("pull_refs", 0)),
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
