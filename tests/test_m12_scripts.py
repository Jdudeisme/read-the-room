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
