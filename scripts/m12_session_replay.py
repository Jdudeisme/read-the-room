"""M12-05 analysis: replay a paired session's takes with cancellation off and
on, and apply the run sheet's pre-registered decision rule.

    python scripts/m12_session_replay.py data/captures/m12s-20261012 [--json OUT]
    python scripts/m12_session_replay.py data/captures/ladder-20261009 --ladder [--json OUT]

`docs/M12-05-RUN-SHEET.md` section F specifies this script, and its
preflight (B3) requires it to be written and tested **before** the
session, so the rule's analysis is fixed in code before the data exists.
`--ladder` is that test: it maps the 2026-10-09 ladder takes onto the
session's structure (Block S only; C1/C4 bracket), and its energy numbers
must equal `m12_energy_survey.py`'s for the same takes.

**Per take, both modes, on the engine's schedule** (5 s windows every 2 s):
- **off**: cancellation off. VAD, DSP and headcount on the raw mic.
- **on**: cancellation on, as the engine runs it after M12-04. VAD and
  headcount on the clean stream (`aec.cancel`, causal, bit-identical to
  the live `CleanSource`). The published `energy`/`trend` use raw
  loudness and activity with clean certification. `clean_energy` and
  `clean_trend` use the clean window's loudness and activity.

Energy is the three-term `energy_score(..., None)`, as in the
2026-10-11 survey: arousal is the same input to raw and clean energy, so it
can't create a raw-vs-clean difference (run sheet F).

**Config mirrored:** `Config()` defaults, which equal JPad's `.env` for
everything used here. That is headcount interval 2.0 s (= the hop, so every
eligible tick runs), min speech ratio 0.2, certification 0.75 on
music takes and 0.5 on controls, rescue OFF, estimator and smoother at
config values, noise floor `Ema` over windows with certified ratio < 0.1,
fed the raw level (as the engine does), ECAPA on `config.torch_threads`.
Each take replays from cold (fresh VAD, estimator, smoother, floor,
canceller), because each is a separate capture.

**Counting.** Music takes count from the canceller's first lock: the
window must start at or after it, as in `m12_energy_survey.py`. Both
modes use the same ticks, so off and on compare like with like. A take
that never locks is reported `locked: false`; there clean = raw. Bucket
shares count headcount *updates* (eligible ticks with an estimate), as
the 2026-09-30 table did.

Offline; reads consent-gated captures under `data/` (gitignored) and
writes numbers only, no audio.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_reference import read_wav  # noqa: E402
from sensing import aec, dsp  # noqa: E402
from sensing.config import Config  # noqa: E402
from sensing.state import Ema, TrendTracker, energy_score  # noqa: E402
from sensing.vad import VadGate  # noqa: E402

# -- the pre-registered rule (run sheet G) -----------------------------------------
# Changing any of these after the session voids the rule.
R1A_MAX_ELIGIBLE = 0.05  # music-only takes: eligible share of locked windows
R1B_SOLO_MARGIN = 0.15  # H over I, and H over the controls, solo share
R1C_SOLO_MARGIN = 0.15  # Block S: a locked mix take's solo share under the controls'
R2A_RISING_MARGIN = 0.20  # trend minus clean_trend, rising share after the step
R2B_TOLERANCE = 0.01  # clean_energy may be at most this much farther from controls
STEP_SPAN_S = 60.0  # the before/after windows around a volume step
DEFAULT_STEP_AT_S = 180.0  # run sheet E: the slider moves at 3:00
SESSION_ID = re.compile(r"^(?P<block>[SG])-(?:(?P<ctl>C\d)|(?P<kind>MO|MX)-(?P<track>[HIJ])(?P<vol>32|66|76)|VS-(?P<vtrack>[HIJ]))$")


@dataclass
class Take:
    id: str
    block: str  # "S" or "G"
    kind: str  # "C", "MO", "MX", "VS"
    track: str | None = None
    vol: int | None = None
    bracket: bool = True  # controls: does it bracket its block?
    step_at: float | None = None
    gated: bool = False
    result: dict = field(default_factory=dict)

    @property
    def playback(self) -> bool:
        return self.kind != "C"


def session_takes(prefix: str) -> list[Take]:
    takes = []
    base = Path(prefix).name
    for wav in sorted(Path(prefix).parent.glob(f"{base}-*.wav")):
        if wav.name.endswith(".ref.wav"):
            continue
        tid = wav.stem[len(base) + 1:]
        m = SESSION_ID.match(tid)
        if m is None:
            print(f"skipping unrecognised take {tid!r}", file=sys.stderr)
            continue
        if m["ctl"]:
            takes.append(Take(tid, m["block"], "C"))
        elif m["kind"]:
            takes.append(Take(tid, m["block"], m["kind"], m["track"], int(m["vol"])))
        else:
            takes.append(Take(tid, m["block"], "VS", m["vtrack"], step_at=DEFAULT_STEP_AT_S))
    return takes


def ladder_takes() -> list[Take]:
    """The 2026-10-09 ladder as a Block S: C1/C4 bracket (C2 far and C3
    conversation are reported, not bracketing), T1-T3 stand in for tracks."""
    takes = [Take("C1", "S", "C"), Take("C2", "S", "C", bracket=False),
             Take("C3", "S", "C", bracket=False), Take("C4", "S", "C")]
    for t in ("T1", "T2", "T3"):
        takes.append(Take(f"{t}-MO32", "S", "MO", t, 32))
        for v in (32, 66, 76):
            takes.append(Take(f"{t}-MX{v}", "S", "MX", t, v))
    return takes


# -- the replay -------------------------------------------------------------------


def replay_mode(mic, stream, clean, playback, config, embed, mode):
    """Per tick of one take in one mode. `stream` feeds VAD and headcount;
    DSP is always on `mic` for `energy`, and on `clean` for the clean_*
    values (mode "on" only)."""
    from sensing.headcount import BucketSmoother, HeadcountEstimator, speech_segments

    sr = config.sample_rate
    win, hop = int(config.window_s * sr), int(config.hop_s * sr)
    thr = config.vad_playback_threshold if playback else config.vad_threshold
    vad = VadGate(sr, config.window_s, config.vad_threshold)
    vad.load()
    tau = config.smooth_tau_dsp_s
    loud, act, speech = Ema(tau), Ema(tau), Ema(tau)
    c_loud, c_act = Ema(tau), Ema(tau)
    trend = TrendTracker(config.trend_horizon_s, config.trend_slope_threshold)
    c_trend = TrendTracker(config.trend_horizon_s, config.trend_slope_threshold)
    floor = Ema(config.noise_floor_tau_s)
    est = HeadcountEstimator(
        buffer_s=config.headcount_buffer_s,
        buffer_cap=config.headcount_buffer_cap,
        cluster_threshold=config.headcount_cluster_threshold,
        min_cluster_evidence_frac=config.headcount_min_cluster_frac,
        rescue_enabled=config.headcount_rescue_enabled,
        rescue_margin=config.headcount_rescue_margin,
    )
    sm = BucketSmoother(tau_s=config.headcount_smooth_tau_s, hold_k=config.headcount_hysteresis_k)
    rows, fed = [], 0
    for start in range(0, mic.size - win + 1, hop):
        end = start + win
        t = end / sr
        vad.feed(stream[fed:end])
        fed = end
        ratio = vad.speech_ratio(thr)
        d = dsp.analyze(mic[start:end], sr)
        sp = speech.update(ratio, t)
        if ratio < 0.1:
            floor.update(d.rms_dbfs, t)
        e = energy_score(loud.update(d.rms_dbfs, t), act.update(d.onset_density, t), sp, None)
        row = {"t": t, "ratio": ratio, "energy": e, "trend": trend.update(e, t)}
        if mode == "on":
            dc = dsp.analyze(clean[start:end], sr)
            ce = energy_score(c_loud.update(dc.rms_dbfs, t), c_act.update(dc.onset_density, t), sp, None)
            row.update(clean_energy=ce, clean_trend=c_trend.update(ce, t))
        if embed is not None and ratio >= config.headcount_min_speech_ratio:
            segs = speech_segments(stream[start:end], vad.speech_mask(thr), sr)
            if segs:
                est.add(embed(segs), [s.size / sr for s in segs], t)
            r = est.estimate(ratio, d.rms_dbfs, playback, floor.value)
            if r is not None:
                row["bucket"] = sm.update(r.log2_count, t).value
        rows.append(row)
    return rows


def summarize(rows, take: Take, from_s, config) -> dict:
    counted = [r for r in rows if from_s is None or r["t"] - config.window_s >= from_s]
    if not counted:
        return {"n": 0}
    buckets: dict[str, int] = {}
    for r in counted:
        if "bucket" in r:
            buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    n_hc = sum(buckets.values())
    out = {
        "n": len(counted),
        "eligible": round(sum(r["ratio"] >= config.headcount_min_speech_ratio for r in counted) / len(counted), 3),
        "energy": round(float(np.median([r["energy"] for r in counted])), 3),
        "trend_moving": round(sum(r["trend"] != "stable" for r in counted) / len(counted), 3),
        "buckets": buckets,
        "solo": None if n_hc == 0 else round(buckets.get("solo", 0) / n_hc, 3),
    }
    if "clean_energy" in counted[0]:
        out["clean_energy"] = round(float(np.median([r["clean_energy"] for r in counted])), 3)
        out["clean_trend_moving"] = round(sum(r["clean_trend"] != "stable" for r in counted) / len(counted), 3)
    if take.kind == "VS":
        at = take.step_at
        before = [r for r in rows if at - STEP_SPAN_S <= r["t"] < at]
        after = [r for r in rows if at <= r["t"] < at + STEP_SPAN_S]
        step = {}
        for key, tkey in (("energy", "trend"), ("clean_energy", "clean_trend")):
            if after and key in after[0]:
                step[key] = {
                    "step": round(float(np.median([r[key] for r in after]) - np.median([r[key] for r in before])), 3),
                    "rising_before": round(sum(r[tkey] == "rising" for r in before) / len(before), 3),
                    "rising_after": round(sum(r[tkey] == "rising" for r in after) / len(after), 3),
                }
        out["step"] = step
    return out


def replay_take(prefix: str, take: Take, config: Config, embed) -> dict:
    mic = read_wav(Path(f"{prefix}-{take.id}.wav"))
    ref = read_wav(Path(f"{prefix}-{take.id}.ref.wav"))
    res = aec.cancel(mic, ref)
    lock = res.relocks[0] if res.relocks else None
    from_s = lock if take.playback else None
    out = {"id": take.id, "block": take.block, "kind": take.kind, "track": take.track,
           "vol": take.vol, "first_lock_s": lock,
           "locked": (lock is not None) or not take.playback, "gated": take.gated}
    for mode, stream in (("off", mic), ("on", res.clean)):
        rows = replay_mode(mic, stream, res.clean, take.playback, config, embed, mode)
        out[mode] = summarize(rows, take, from_s, config)
    return out


# -- the rule ---------------------------------------------------------------------


def controls(results, block):
    c = [r for r in results if r["block"] == block and r["kind"] == "C" and r["bracket"] and not r["gated"]]
    return c


def apply_rule(results: list[dict]) -> dict:
    """Run sheet G, on the cancellation-on replay. Returns each test's
    inputs and verdict; 'n/a' where the session lacks the takes."""
    on = lambda r: r["on"]  # noqa: E731
    usable = [r for r in results if not r["gated"]]
    rule: dict = {}

    # 1a: music-only certification.
    mo = [r for r in usable if r["kind"] == "MO"]
    rule["1a"] = {
        "takes": {r["id"]: on(r)["eligible"] for r in mo},
        "fires": [r["id"] for r in mo if on(r)["eligible"] > R1A_MAX_ELIGIBLE],
    } if mo else "n/a"

    # 1b: vocal (H) vs instrumental (I) headcount, Block G.
    ctl = controls(usable, "G")
    ctl_solo = [on(r)["solo"] for r in ctl if on(r)["solo"] is not None]
    b = {}
    for vol in (32, 66, 76):
        h = next((r for r in usable if r["block"] == "G" and r["kind"] == "MX" and r["track"] == "H" and r["vol"] == vol), None)
        i = next((r for r in usable if r["block"] == "G" and r["kind"] == "MX" and r["track"] == "I" and r["vol"] == vol), None)
        if h is None or i is None or on(h)["solo"] is None or on(i)["solo"] is None or not ctl_solo:
            b[vol] = "n/a"
            continue
        c_mean = float(np.mean(ctl_solo))
        fires = (on(h)["solo"] - on(i)["solo"] > R1B_SOLO_MARGIN
                 and on(h)["solo"] - c_mean > R1B_SOLO_MARGIN)
        # H_locked is reported, not ruled on: an unlocked H take is
        # uncancelled (the B66 envelope), which the entry should say.
        b[vol] = {"H": on(h)["solo"], "I": on(i)["solo"], "controls": round(c_mean, 3),
                  "H_locked": h["locked"], "fires": fires}
    rule["1b"] = b

    # 1c: phantom count over our own playback, cancellation on. Block S is
    # one person, so every non-solo update is a phantom. Locked takes only:
    # unlocked, the take is uncancelled and 1c's question doesn't apply.
    s_ctl = [on(r)["solo"] for r in controls(usable, "S") if on(r)["solo"] is not None]
    c = {}
    if s_ctl:
        s_mean = float(np.mean(s_ctl))
        for r in usable:
            if r["block"] != "S" or r["kind"] != "MX" or not r["locked"] or on(r)["solo"] is None:
                continue
            c[r["id"]] = {"solo": on(r)["solo"], "controls": round(s_mean, 3),
                          "fires": s_mean - on(r)["solo"] > R1C_SOLO_MARGIN}
    rule["1c"] = c or "n/a"

    # 2a: the feedback loop on a volume step.
    vs = [r for r in usable if r["kind"] == "VS"]
    a = {}
    for r in vs:
        s = on(r).get("step", {})
        if "energy" in s and "clean_energy" in s:
            d = s["energy"]["rising_after"] - s["clean_energy"]["rising_after"]
            a[r["id"]] = {"trend_rising": s["energy"]["rising_after"],
                          "clean_rising": s["clean_energy"]["rising_after"],
                          "holds": d >= R2A_RISING_MARGIN}
    rule["2a"] = a or "n/a"

    # 2b: clean no farther from the bracketing controls than published energy,
    # 66/76 % only. At 32 % the music's loudness bias (~0) is under the
    # take-to-take talk variation, so the gap measures the talk: on the
    # ladder test the one locked 32 % take (T3-MX32) failed by 0.006 for
    # that reason (run sheet G, 2026-10-11).
    bt = {}
    for r in usable:
        if r["kind"] != "MX" or r["vol"] == 32:
            continue
        c = controls(usable, r["block"])
        if not c:
            continue
        c_e = float(np.mean([on(x)["energy"] for x in c]))  # controls: clean == raw
        d_raw = abs(on(r)["energy"] - c_e)
        d_clean = abs(on(r)["clean_energy"] - c_e)
        bt[r["id"]] = {"energy_gap": round(d_raw, 3), "clean_gap": round(d_clean, 3),
                       "holds": d_clean <= d_raw + R2B_TOLERANCE}
    rule["2b"] = bt or "n/a"

    # Outcomes.
    fired_1a = rule["1a"] != "n/a" and bool(rule["1a"]["fires"])
    fired_1b = [v for v in b.values() if isinstance(v, dict) and v["fires"]]
    if fired_1a or fired_1b:
        rule["outcome_1"] = "REOPEN M12-03 at step 3"
    elif rule["1a"] == "n/a" and all(v == "n/a" for v in b.values()):
        rule["outcome_1"] = "n/a"
    else:
        rule["outcome_1"] = "STAYS DEFERRED"
    if rule["1c"] == "n/a":
        rule["outcome_1c"] = "n/a"
    else:
        fired = [k for k, v in rule["1c"].items() if v["fires"]]
        rule["outcome_1c"] = f"FILE phantom-count finding ({', '.join(fired)})" if fired else "CLEAR"
    holds_2a = isinstance(rule["2a"], dict) and any(v["holds"] for v in rule["2a"].values())
    holds_2b = isinstance(rule["2b"], dict) and all(v["holds"] for v in rule["2b"].values())
    if rule["2b"] == "n/a":
        rule["outcome_2"] = "n/a"
    elif not holds_2b:
        rule["outcome_2"] = "FILE"
    elif rule["2a"] == "n/a":
        rule["outcome_2"] = "n/a (no volume-step take)"
    else:
        rule["outcome_2"] = "SWITCH" if holds_2a else "HOLD"

    # Drift: a block's C1-vs-C2 gap against the spread among its 32 % mix takes.
    drift = {}
    for block in ("S", "G"):
        c = controls(usable, block)
        mx32 = [r for r in usable if r["block"] == block and r["kind"] == "MX" and r["vol"] == 32]
        if len(c) < 2 or len(mx32) < 2:
            continue
        for key in ("eligible", "energy"):
            gap = abs(on(c[0])[key] - on(c[-1])[key])
            spread = float(np.ptp([on(r)[key] for r in mx32]))
            drift[f"{block}.{key}"] = {"controls_gap": round(gap, 3), "mx32_spread": round(spread, 3),
                                       "drifting": gap > spread}
    rule["drift"] = drift
    return rule


def apply_rule_with_drift(results: list[dict]) -> dict:
    """Run sheet G's drift check: if a block drifts, each outcome stands
    only if it holds with that block's first control excluded **and** with
    its last control excluded."""
    rule = apply_rule(results)
    drifting = sorted({k.split(".")[0] for k, v in rule["drift"].items() if v["drifting"]})
    robust = {}
    for block in drifting:
        ctl = controls([r for r in results if not r["gated"]], block)
        for drop in (ctl[0]["id"], ctl[-1]["id"]):
            alt = apply_rule([dict(r, gated=r["gated"] or r["id"] == drop) for r in results])
            robust[f"without {drop}"] = {k: alt[k] for k in ("outcome_1", "outcome_1c", "outcome_2")}
    if robust:
        rule["drift_robust"] = robust
        for k in ("outcome_1", "outcome_1c", "outcome_2"):
            if any(v[k] != rule[k] for v in robust.values()):
                rule[k] = f"NOT ROBUST TO DRIFT (all controls: {rule[k]})"
    return rule


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prefix")
    ap.add_argument("--ladder", action="store_true", help="the pre-session test on the 2026-10-09 ladder")
    ap.add_argument("--json")
    ap.add_argument("--no-headcount", action="store_true", help="skip ECAPA (energy and certification only)")
    ap.add_argument("--step-at", action="append", default=[], metavar="ID=SECONDS",
                    help="the noted second the volume moved, per VS take (default 180)")
    ap.add_argument("--gate", action="append", default=[], metavar="ID",
                    help="a take whose occupancy changed: reported, left out of the rule")
    a = ap.parse_args()
    config = Config()
    takes = ladder_takes() if a.ladder else session_takes(a.prefix)
    steps = dict(s.split("=") for s in a.step_at)
    for t in takes:
        if t.id in steps:
            t.step_at = float(steps[t.id])
        t.gated = t.id in a.gate
    embed = None
    if not a.no_headcount:
        from sensing.headcount import load_ecapa
        embed = load_ecapa(config.headcount_model, config.torch_threads, config.os_truststore)
    results = []
    for t in takes:
        r = replay_take(a.prefix, t, config, embed)
        r["bracket"] = t.bracket
        print(json.dumps(r), flush=True)
        results.append(r)
    rule = apply_rule_with_drift(results)
    print(json.dumps({"rule": rule}, indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps({"takes": results, "rule": rule}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
