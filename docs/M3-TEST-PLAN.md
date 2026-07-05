# Milestone 3 Test Plan — Dashboard + shadow-mode mapping + annotation log

Logic-level tests live in `tests/test_mapper.py`, `tests/test_dashboard.py`
and `tests/test_tuning_report.py` (synthetic RoomState only — no mic, no
models, no network; run anywhere with `PYTHONPATH=src python -m pytest
tests/ -q`). This plan covers the milestone gate, which runs on the 2019
Intel MacBook Pro — the deployment target. Numbers measured on the Windows
dev box are informational only.

## The gate (all four must pass, on the Mac)

### (a) M2 regression: `--fallback` benchmark unchanged and passing

```
RTR_TORCH_THREADS=2 python scripts/bench_headcount.py --fallback
```

Same criteria as M2 (headcount p95 < 1.37 s on contended hops, emotion
overall p95 < 1.2 s absolute). M3 adds no compute to the engine path — the
mapper is a few comparisons per hop and the dashboard is I/O — so any drift
here is a regression, not a cost.

### (b) 10-minute live run with the dashboard connected

```
read-the-room-dashboard            # or: python -m dashboard
```

With the production `.env` (`RTR_HEADCOUNT_MIN_INTERVAL_S=4.0`,
`RTR_TORCH_THREADS=2`), browser open on http://127.0.0.1:8000 for 10+
minutes of natural speech/silence phases:

- header `emotion age` / `headcount age` stay within the fallback-schedule
  bounds during speech: emotion ≤ ~2× `RTR_EMOTION_MIN_INTERVAL_S`,
  headcount ≤ ~2× `RTR_HEADCOUNT_MIN_INTERVAL_S` — growth beyond that under
  load means a worker is starving (the honest end-to-end health numbers);
- during silence both ages climb monotonically and the page says so
  (staleness chips amber) rather than pretending freshness;
- quadrant dot + trail move with the room; timeline scrolls; no frozen
  page. Kill the server mid-run: the page must show DISCONNECTED and
  auto-recover when the server returns;
- no engine consumer errors in the server log, no worker deaths.

### (c) mapping and tuning-report unit tests green

```
PYTHONPATH=src python -m pytest tests/ -q
```

### (d) annotation round-trip via a real button tap

While the live run shows a recommendation, tap **Good call** once and
**Wrong call** once. Then inspect `data/annotations/<today>.jsonl`:

- two lines, `schema_version: 1`, correct `verdict` values;
- each line's `state` matches what the page displayed at tap time (not a
  later frame — compare `timestamp`);
- `recommendation.matched_cell` and `recommendation.boundaries_snapshot`
  present and populated (the attribution contract);
- `python scripts/tuning_report.py` reads them and prints sane output
  without writing anything.

## Shakedown scenarios (best-effort, not gating)

| Scenario | Expectation |
|---|---|
| Cold start, silent room | Guard recommendation ("insufficient signal — hold"), confidence 0.15, buttons usable; no rulebook cell pretended |
| Solo speech 3 min | Bucket `solo` cell fires; recommendation stable (no flapping) — re-emissions ≥ 30 s apart and only on material change |
| Two-person animated chat | Mood quadrant tracks; energy_action follows the room's direction (raise while warming up, not always escalate) |
| Radio/TV voices | Known M2 limitation: media voices inflate the bucket, so the mapper will read the room as larger than it is. Annotate Wrong — that is the loop working — but do not tune boundaries against media-contaminated sessions |

## Out of scope (deliberately)

- Playback and everything downstream of it (see M3-PROPOSAL "Deferred").
- Learned/automatic tuning — the report proposes, a human applies.
- Sensing-layer accuracy: gated in M1/M2; M3 only consumes RoomState.
