"""Measure the M6 dominance proxy on recorded ladder takes, offline.

Companion to `scripts/capture_room_wav.py` and
`docs/DOMINANCE-LADDER-RUN-SHEET.md`. The dominance knots
(`RTR_MUSIC_DOMINANCE_LO/HI`) on JPad are PROVISIONAL (FIELD-NOTES
2026-09-06): fitted on one track, with three speech-only controls
disagreeing at the tail, and read live off the dashboard socket, one
40 s take at a time. This script makes the ladder a set of files that
can be analysed as often as needed. Nothing here touches a microphone,
the network, or any calibration constant. It is measurement only, and
it never writes `.env`.

WHAT IT MIRRORS. The engine's dominance input is
`dsp.analyze(window).spectral_balance["high"]` on each `window_s` window
every `hop_s` (engine.py `_tick`). Dominance only exists while
`playback_active`, and certification then uses
`vad_playback_threshold` rather than `vad_threshold`. A reading reaches
the music-aware path only when the window's raw speech ratio clears
`emotion_min_speech_ratio`. Streaming Silero VAD runs with its recurrent
state intact, exactly as the engine feeds it. Constants come from
`Config.from_env()` (this machine's `.env` over `config.py`) and are
printed in the header.

TAKE KINDS. Each WAV is given as `KIND[:LABEL]=PATH`:

- `speech`: speech only, no music (a control). It is scored **as if
  playback were active** (0.75 certification), because that is when a
  speech window's own high-band share matters: quiet or paused music
  with someone talking. A control window above `LO` gets a spurious
  correction; one at `m >= pull_m_floor` banks a bogus pull sample.
- `mix`: speech over the playing track (the population the pull
  estimator consumes). A window at `m <= baseline_m_max` is absorbed
  into the clean-speech baseline, the failure 2026-09-06 found
  (contamination poisoning the reference). A window at
  `m >= pull_m_floor` is bankable.
- `music`: the track with nobody talking. Mostly context for `HI`. Only
  its phantom-certified windows (music the VAD passes as speech) ever
  reach the correction.

    python scripts/analyze_dominance_wav.py \\
        speech:C1=data/captures/ladder-C1.wav \\
        mix:T1-76=data/captures/ladder-T1-76-mix.wav \\
        --hi-from MX76 --knots 0.025,0.055

The decision rule lives in the run sheet, not here. This script prints
the rule's inputs and scores every candidate pair of knots against the
same windows.
"""

from __future__ import annotations

import argparse
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sensing import dsp
from sensing.config import Config
from sensing.music import dominance
from sensing.vad import VadGate

KINDS = ("speech", "mix", "music")


@dataclass
class Take:
    kind: str
    label: str
    path: Path
    # Per-window values, engine schedule. `high` for every full window;
    # `eligible` marks windows whose raw speech ratio clears
    # emotion_min_speech_ratio (the readings the correction acts on).
    high: list[float] = field(default_factory=list)
    ratio: list[float] = field(default_factory=list)
    eligible: list[bool] = field(default_factory=list)
    dbfs: list[float] = field(default_factory=list)
    sidecar: dict = field(default_factory=dict)

    def eligible_high(self) -> np.ndarray:
        return np.array([h for h, e in zip(self.high, self.eligible) if e])


def parse_take(spec: str) -> tuple[str, str, Path]:
    """`KIND[:LABEL]=PATH` -> (kind, label, path). The label defaults to
    the file stem."""
    if "=" not in spec:
        raise ValueError(f"take {spec!r}: expected KIND[:LABEL]=PATH")
    head, path = spec.split("=", 1)
    kind, _, label = head.partition(":")
    if kind not in KINDS:
        raise ValueError(f"take {spec!r}: kind must be one of {KINDS}")
    p = Path(path)
    return kind, label or p.stem, p


def dist(values: np.ndarray) -> dict:
    """The distribution summary the run sheet's rule reads."""
    if values.size == 0:
        return {"n": 0}
    q = np.percentile(values, [10, 50, 90, 95])
    return {
        "n": int(values.size),
        "mean": round(float(values.mean()), 4),
        "p10": round(float(q[0]), 4),
        "p50": round(float(q[1]), 4),
        "p90": round(float(q[2]), 4),
        "p95": round(float(q[3]), 4),
        "max": round(float(values.max()), 4),
    }


def knot_metrics(
    highs: np.ndarray, lo: float, hi: float, m_max: float, m_floor: float
) -> dict:
    """Score one knot pair on one population of eligible windows: the
    fraction absorbed as clean (m <= m_max), the fraction bankable as
    pull (m >= m_floor), and mean m. Uses the engine's own ramp."""
    if highs.size == 0:
        return {"n": 0}
    m = np.array([dominance(float(h), lo, hi) for h in highs])
    return {
        "n": int(m.size),
        "clean": round(float((m <= m_max).mean()), 3),
        "bankable": round(float((m >= m_floor).mean()), 3),
        "m_mean": round(float(m.mean()), 3),
    }


def rule_inputs(takes: list[Take], hi_tag: str | None) -> dict:
    """The two numbers the run sheet's draft rule is built from: the
    worst control's p95 (LO candidate) and the pooled p50 of mix windows
    whose label contains `hi_tag` (HI candidate: the loud takes where music
    unambiguously dominates). Reported, never applied."""
    controls = [t for t in takes if t.kind == "speech" and t.eligible_high().size]
    lo = max((dist(t.eligible_high())["p95"] for t in controls), default=None)
    mixes = [
        t for t in takes
        if t.kind == "mix" and (hi_tag is None or hi_tag in t.label)
    ]
    pooled = (
        np.concatenate([t.eligible_high() for t in mixes]) if mixes else np.array([])
    )
    hi = dist(pooled).get("p50") if pooled.size else None
    return {
        "lo_candidate": lo,
        "hi_candidate": hi,
        "controls": len(controls),
        "hi_mix_takes": len(mixes),
        # None = not enough takes to say (no controls or no HI mix takes).
        "separable": None if lo is None or hi is None else hi > lo,
    }


def analyse(take: Take, config: Config, vad: VadGate) -> None:
    audio, rate = _read_wav(take.path)
    if rate != config.sample_rate:
        raise SystemExit(f"{take.path}: {rate} Hz, need {config.sample_rate}")
    # Speech-only controls are scored as if playback were active; see the
    # module docstring. Every kind therefore certifies at the playback
    # threshold, which is what the engine does whenever dominance exists.
    cert = config.vad_playback_threshold
    win = int(config.window_s * rate)
    hop = int(config.hop_s * rate)
    fed = 0
    for start in range(0, audio.size - win + 1, hop):
        end = start + win
        vad.feed(audio[fed:end])
        fed = end
        window = audio[start:end]
        measured = dsp.analyze(window, rate)
        ratio = vad.speech_ratio(cert)
        take.high.append(float(measured.spectral_balance.get("high", 0.0)))
        take.ratio.append(float(ratio))
        take.eligible.append(ratio >= config.emotion_min_speech_ratio)
        take.dbfs.append(float(measured.rms_dbfs))
    take.sidecar = _read_sidecar(take.path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("takes", nargs="+", help="KIND[:LABEL]=PATH, KIND in speech/mix/music")
    parser.add_argument(
        "--knots", action="append", default=[],
        help="extra LO,HI pair to score (repeatable)",
    )
    parser.add_argument(
        "--hi-from", default=None,
        help="label substring selecting the mix takes that set the HI candidate, e.g. MX76",
    )
    parser.add_argument("--json", type=Path, help="also write the full result here")
    args = parser.parse_args(argv)

    config = Config.from_env()
    takes = [Take(*parse_take(s)) for s in args.takes]
    candidates = {
        "config.py default": (Config.music_dominance_lo, Config.music_dominance_hi),
        "in force (.env)": (config.music_dominance_lo, config.music_dominance_hi),
    }
    for k in args.knots:
        lo, hi = (float(x) for x in k.split(","))
        candidates[f"candidate {lo},{hi}"] = (lo, hi)

    print(
        f"config: window_s={config.window_s} hop_s={config.hop_s} "
        f"cert(playback)={config.vad_playback_threshold} "
        f"emotion_min_speech_ratio={config.emotion_min_speech_ratio} "
        f"m_max={config.music_baseline_m_max} pull_m_floor={config.music_pull_m_floor}"
    )
    for take in takes:
        vad = VadGate(config.sample_rate, config.window_s, config.vad_threshold)
        vad.load()
        analyse(take, config, vad)

    rule = rule_inputs(takes, args.hi_from)
    if rule["lo_candidate"] is not None and rule["hi_candidate"] is not None:
        candidates.setdefault(
            "draft rule", (rule["lo_candidate"], rule["hi_candidate"])
        )

    result = {"takes": [], "pooled": {}, "rule_inputs": rule, "knots": {}}
    print("\nper take (high-band share on eligible windows):")
    for t in takes:
        d = dist(t.eligible_high())
        row = {
            "kind": t.kind, "label": t.label, "path": str(t.path),
            "windows": len(t.high), "eligible": int(sum(t.eligible)),
            "dbfs_mean": round(float(np.mean(t.dbfs)), 1) if t.dbfs else None,
            "eligible_high": d, "all_high": dist(np.array(t.high)),
            "note": t.sidecar.get("note", ""),
            "device": t.sidecar.get("device_name", ""),
        }
        result["takes"].append(row)
        print(
            f"  {t.kind:6s} {t.label:18s} win {row['windows']:4d} elig {row['eligible']:4d} "
            f"dBFS {row['dbfs_mean']}  {_fmt(d)}"
        )
        if not t.sidecar:
            print(f"    (no sidecar for {t.path.name}: provenance unknown)")

    for kind in KINDS:
        pool = [t.eligible_high() for t in takes if t.kind == kind]
        pooled = np.concatenate(pool) if pool else np.array([])
        result["pooled"][kind] = dist(pooled)
    print("\npooled by kind:")
    for kind, d in result["pooled"].items():
        print(f"  {kind:6s} {_fmt(d)}")

    print(
        f"\nrule inputs: LO candidate (worst control p95) = {rule['lo_candidate']}  "
        f"HI candidate (--hi-from mix p50) = {rule['hi_candidate']}  "
        f"from {rule['controls']} controls, {rule['hi_mix_takes']} HI mix takes"
        + {
            True: "",
            False: "  ** NOT SEPARABLE: HI <= LO **",
            None: "  (need speech controls and --hi-from mix takes to judge)",
        }[rule["separable"]]
    )

    print("\nknots scored per take (clean = m<=m_max, bankable = m>=pull_m_floor):")
    for name, (lo, hi) in candidates.items():
        print(f"  {name}: LO={lo} HI={hi}")
        scored = {}
        for t in takes:
            km = knot_metrics(
                t.eligible_high(), lo, hi,
                config.music_baseline_m_max, config.music_pull_m_floor,
            )
            scored[t.label] = km
            if km["n"]:
                print(
                    f"    {t.kind:6s} {t.label:18s} clean {km['clean']:.3f}  "
                    f"bankable {km['bankable']:.3f}  m_mean {km['m_mean']:.3f}"
                )
        result["knots"][name] = {"lo": lo, "hi": hi, "per_take": scored}

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


def _fmt(d: dict) -> str:
    if not d.get("n"):
        return "n 0"
    return (
        f"n {d['n']}  mean {d['mean']:.4f}  p10 {d['p10']:.4f}  p50 {d['p50']:.4f}  "
        f"p95 {d['p95']:.4f}  max {d['max']:.4f}"
    )


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        if fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16-bit mono PCM")
        rate = fh.getframerate()
        pcm = np.frombuffer(fh.readframes(fh.getnframes()), dtype="<i2")
    return (pcm.astype(np.float32) / 32768.0), rate


def _read_sidecar(path: Path) -> dict:
    sidecar = path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
