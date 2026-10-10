"""Pure helpers of the M12 offline scripts (no models, no audio files)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


replay = _load("m12_partf_replay")
survey = _load("m12_chunk_survey")


def test_chunk_audio_aligns_to_the_last_complete_chunk():
    sr, chunk = replay.SR, replay.CHUNK
    audio = np.arange(10 * sr, dtype=np.float32)  # sample value = its index
    end = 5 * sr + 300  # window end not on a chunk boundary
    window = audio[end - 5 * sr : end]
    chunks = replay.chunk_audio(window, 4, end, end)
    last_end = (end // chunk) * chunk
    assert chunks[-1][0] == last_end - chunk
    assert chunks[0][0] == last_end - 4 * chunk
    assert (np.diff(chunks[:, 0]) == chunk).all()


def test_gate_none_refuses_nothing():
    mask = np.array([True, False, True])
    assert not replay.gate_none(None, None, None, mask, True).any()


def test_segment_table_counts_buckets_per_segment(tmp_path):
    rows = [
        {"music": "A", "ratio": 0.5, "bucket": "solo", "fragmentation": 0.2},
        {"music": "A", "ratio": 0.7},
        {"music": "B", "ratio": 0.1, "bucket": "pair", "fragmentation": 0.4},
    ]
    p = tmp_path / "x.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    table = dict(replay.segment_table(str(p)))
    assert table["A"] == {"buckets": {"solo": 1}, "ratio": 0.6, "frag": 0.2}
    assert table["B"]["buckets"] == {"pair": 1}


@pytest.mark.parametrize(
    "pos,neg,expected",
    [([3, 4], [1, 2], 1.0), ([1, 2], [3, 4], 0.0), ([1, 2], [1, 2], 0.5)],
)
def test_auc(pos, neg, expected):
    assert survey.auc(np.array(pos, float), np.array(neg, float)) == expected


ar = _load("analyze_reference")


def _noise(seconds, seed=0):
    return np.random.default_rng(seed).standard_normal(int(seconds * ar.SR)).astype(np.float32) * 0.1


def test_gcc_phat_recovers_a_known_delay():
    ref = _noise(10)
    lag = 437  # samples: 27.3 ms
    mic = np.concatenate([np.zeros(lag, np.float32), ref[:-lag]]) * 0.3 + _noise(10, 1) * 0.05
    got, peak = ar.gcc_phat(mic, ref, int(0.5 * ar.SR))
    assert got == lag and peak > 0.1


def test_delays_skip_windows_where_the_reference_is_off():
    ref = np.concatenate([_noise(10), np.zeros(10 * ar.SR, np.float32)])
    mic = ref.copy()
    d = ar.delays(mic, ref)
    assert [x["t"] for x in d] == [0.0]
    assert d[0]["delay_ms"] == 0.0


def test_clock_ratio_reads_a_fast_reference_clock():
    t = np.arange(0, 60, 1.0)
    mic = [[x, x * ar.SR] for x in t]
    ref = [[x, x * 48_000 * (1 + 50e-6)] for x in t]  # +50 ppm
    assert ar.clock_ratio(mic, ref, 48_000) == pytest.approx(50.0, abs=0.01)


def test_transport_spans():
    on = _noise(1)
    ref = np.concatenate([on, np.zeros(ar.SR, np.float32), on])
    assert ar.transport(ref) == [(0.0, 1.0), (2.0, 3.0)]


ev = _load("m12_aec_eval")


def test_echo_tail_spans_the_energetic_part_of_an_impulse_response():
    ir = np.zeros(1600)
    ir[160:480] = 1.0  # energy only between 10 and 30 ms
    assert ev.echo_tail_ms(ir) == pytest.approx(20.0, abs=0.2)
    assert ev.echo_tail_ms(np.zeros(10)) is None


def test_eligible_windows_counts_certified_5s_windows():
    per_win, per_hop = int(5 * ev.SR / 512), int(2 * ev.SR / 512)
    probs = np.zeros(per_win + 4 * per_hop)
    probs[: per_win] = 0.9  # only the first window is mostly speech
    assert ev.eligible_windows(probs) >= 1
    assert ev.eligible_windows(np.zeros_like(probs)) == 0
