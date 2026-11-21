"""M12-03 reference-free candidates: per-chunk feature survey of the
2026-09-30 part (f) recording.

    python scripts/m12_chunk_survey.py data/captures/m7-partf-2026-09-30-16k.wav OUT.npz

The question (docs/M12-PROPOSAL.md, decision D3): with no playback
reference, is there a cheap per-chunk signal that tells "certified because
the record's vocals passed the VAD" from "certified because someone in the
room talked"? The recording offers one contrast. Its no-music gaps hold
certified room speech only. Its vocal hip-hop segment holds room speech
plus rap the VAD certified (8.6 % of its chunks sit in the [0.5, 0.75)
band, against 34 % for the instrumental; FIELD-NOTES 2026-09-30, night).
Three people talked throughout, so no music segment is music-only. A good
feature separates hip-hop's certified chunks from the gaps' certified
chunks, *and* does not flag the instrumental/jazz segments' speech as
heavily. AUC here measures separation of two mixtures, not accuracy
against a per-chunk truth; this recording has no per-chunk truth.

Per Silero chunk (512 samples, 32 ms), streaming the VAD exactly as the
engine does. Features use no models beyond Silero:
- `p`: VAD speech probability.
- `hi`, `lo`: power share above 2 kHz / below 300 Hz (dsp.py band edges).
- `flat`: spectral flatness over 300–4000 Hz (geometric / arithmetic mean
  of power); music is often tonally denser than speech.
- `flux`: positive spectral flux against the previous chunk.
- `beat`: periodicity of the flux envelope over the past 5 s. Autocorrelation
  peak at lags 0.33–1.0 s (60–180 BPM), normalized by lag 0. Speech
  syllables are quasi-periodic around 4–5 Hz, outside this band.
- `p_sd`: standard deviation of `p` over the past 1 s. Room conversation
  dips between words; continuous rap may not.
- `lvl`: chunk level minus the 5 s rolling median level (dB).

Privacy: reads a consent-gated recording under `data/`; writes numbers only.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from m12_partf_replay import MUSIC, START_WALL, THR_IDLE, THR_PLAYBACK, _lookup, _parse  # noqa: E402
from sensing.vad import VadGate  # noqa: E402

SR, CHUNK = 16_000, 512
FEATURES = ("p", "hi", "lo", "flat", "flux", "beat", "p_sd", "lvl")
_FREQS = np.fft.rfftfreq(CHUNK, 1.0 / SR)
_WIN = np.hanning(CHUNK).astype(np.float32)
BEAT_CTX = int(5.0 * SR / CHUNK)  # chunks of flux history for `beat`
P_CTX = int(1.0 * SR / CHUNK)
LVL_CTX = int(5.0 * SR / CHUNK)
_LAG_LO, _LAG_HI = int(0.33 * SR / CHUNK), int(1.0 * SR / CHUNK)


def survey(wav: str):
    rate, data = wavfile.read(wav)
    assert rate == SR, rate
    audio = data.astype(np.float32) / 32768.0
    vad = VadGate(SR, 5.0, THR_IDLE)
    vad.load()
    n = audio.size // CHUNK
    feats = np.zeros((n, len(FEATURES)), dtype=np.float32)
    seg = np.empty(n, dtype=object)
    certified = np.zeros(n, dtype=bool)
    flux_hist: deque[float] = deque(maxlen=BEAT_CTX)
    p_hist: deque[float] = deque(maxlen=P_CTX)
    lvl_hist: deque[float] = deque(maxlen=LVL_CTX)
    prev_mag = None
    start_s = _parse(START_WALL)
    for k in range(n):
        x = audio[k * CHUNK : (k + 1) * CHUNK]
        vad.feed(x)
        p = vad._probs[-1]
        power = np.abs(np.fft.rfft(x * _WIN)) ** 2 + 1e-12
        total = power.sum()
        mag = np.sqrt(power)
        band = power[(_FREQS >= 300) & (_FREQS <= 4000)]
        flux = 0.0 if prev_mag is None else float(np.maximum(mag - prev_mag, 0).sum() / (mag.sum() + 1e-9))
        prev_mag = mag
        flux_hist.append(flux)
        p_hist.append(p)
        lvl = 10 * np.log10(float((x.astype(np.float64) ** 2).mean()) + 1e-12)
        lvl_hist.append(lvl)
        beat = 0.0
        if len(flux_hist) == BEAT_CTX:
            f = np.asarray(flux_hist) - np.mean(flux_hist)
            ac = np.correlate(f, f, mode="full")[f.size - 1 :]
            if ac[0] > 0:
                beat = float(ac[_LAG_LO : _LAG_HI + 1].max() / ac[0])
        feats[k] = (
            p,
            power[_FREQS >= 2000].sum() / total,
            power[_FREQS < 300].sum() / total,
            float(np.exp(np.mean(np.log(band))) / np.mean(band)),
            flux,
            beat,
            float(np.std(p_hist)),
            lvl - float(np.median(lvl_hist)),
        )
        wall = start_s + (k + 1) * CHUNK / SR
        music = _lookup(MUSIC, wall)
        seg[k] = music[1]
        certified[k] = p >= (THR_PLAYBACK if music[2] else THR_IDLE)
    return feats, seg, certified


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(pos > neg) by ranks, ties half. 0.5 = no separation."""
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort().astype(np.float64) + 1
    # average ranks for ties
    order = np.argsort(allv)
    sorted_v = allv[order]
    i = 0
    while i < sorted_v.size:
        j = i
        while j + 1 < sorted_v.size and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    r = ranks[: pos.size].sum()
    return float((r - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def report(feats, seg, certified) -> None:
    segments = list(dict.fromkeys(seg))
    gap = (seg == "NO MUSIC (skip gap)") & certified
    print(f"certified chunks: {int(certified.sum())} of {seg.size}")
    print(f"{'segment':42s} {'n_cert':>6s}  " + "  ".join(f"{f:>12s}" for f in FEATURES))
    for s in segments:
        m = (seg == s) & certified
        meds = [f"{np.median(feats[m, i]):6.3f}/{auc(feats[m, i], feats[gap, i]):.2f}" if m.any() else "     -      "
                for i in range(len(FEATURES))]
        print(f"{s[:42]:42s} {int(m.sum()):6d}  " + "  ".join(f"{v:>12s}" for v in meds))
    print("cells: median / AUC against the no-music gap's certified chunks "
          "(AUC 0.5 = indistinguishable; >0.5 = higher than room speech)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav")
    ap.add_argument("out", help=".npz with per-chunk features, segment, certified")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()
    if a.report_only:
        z = np.load(a.out, allow_pickle=True)
        feats, seg, certified = z["feats"], z["seg"], z["certified"]
    else:
        feats, seg, certified = survey(a.wav)
        np.savez_compressed(a.out, feats=feats, seg=seg, certified=certified,
                            features=np.array(FEATURES))
    report(feats, seg, certified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
