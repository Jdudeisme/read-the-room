# M12-04 plan — playback-clean energy (APPROVED 2026-10-11)

The charter is ROADMAP M12-04, adopted verbatim. This plan says how,
grounded in the offline survey (FIELD-NOTES 2026-10-11). The item is
REQUIRES-REVIEW: plan, then diff.

**Founder decisions (2026-10-11):** the plan is approved, and so are
D1–D3 as recommended. Clean fields are `None` when cancellation is off;
the corpus schema is bumped; M12-05 gets a volume-step phase.

## What the survey already decides

| Fact (FIELD-NOTES 2026-10-11) | Consequence |
|---|---|
| Raw certification moved energy by −0.24…+0.09 over music. Hybrid (cancellation on, today's code) already reads `speech_ratio` on the clean stream | the charter's "`speech_ratio` from certification" needs no new code: it is M12-02's clean certification, with M12-03 deferred |
| Clean loudness is 1.9–4.9 dB under raw in 66/76 % mix takes, and 4.3–8.9 dB under in music-only takes. On a volume step, clean keeps 15–50 % of the raw step | clean loudness is worth publishing |
| Up to +8 dB of music, the loudness bias alone never trips the trend (≈ 9 dB would be needed) | the mapper switch is **not** urgent. It waits for M12-05, as the charter says |
| Onset activity reads 1.4–2.1/s on everything, and clean changes it by ≤ 0.3 | publish clean activity for completeness; the term's weakness is a separate finding (below), not M12-04's |
| One extra `dsp.analyze` costs 3.9 ms per 2 s tick | affordable; the heartbeat bench confirms it |

## Scope (the diff)

1. **Engine** (`src/sensing/engine.py`). When cancellation runs
   (`analysis is not self.source`), the tick also runs `dsp.analyze` on
   the clean window. It feeds its own EMAs for loudness and activity
   (same `smooth_tau_dsp_s`) and its own `TrendTracker` (same
   config). Then:
   `clean_energy = energy_score(clean_loudness, clean_activity, speech_ratio, arousal)`,
   with `speech_ratio` and `arousal` being the values the engine already
   has. The existing fields are unchanged: loudness, activity, spectral
   balance, energy and trend stay **raw**.
2. **RoomState** (`src/sensing/state.py`), additive fields:
   `clean_loudness_dbfs`, `clean_activity_density`,
   `clean_spectral_balance`, `clean_energy`, `clean_trend`. They are
   **`None` when cancellation isn't running** (D1).
3. **Corpus schema** (D2). Annotations 1 → 2 and overrides 2 → 3 (they
   stamp the displayed state). The `tuning_report.py` audit runs on the
   new schema, per invariant 9 and the charter's acceptance line.
4. **Mapper unchanged.** `state.trend` stays raw until the M12-05
   decision rule says otherwise. That switch is then a mapper-only diff.
5. **Tests** (offline, no models). Clean fields are `None` without
   cancellation. With a fake clean source, the clean fields follow the
   clean ring and the existing fields follow raw. With silent playback
   (clean = raw bit for bit), clean fields equal the raw ones. Schema
   versions are pinned.
6. **Evidence with the diff.** `scripts/bench_cancel_heartbeat.py`
   reruns with the change: tick p95 and the worst late tick against
   M12-02 step 4's row (p95 2.032 s, worst 63 ms late).

**No new tunable**, so there is no `RTR_*` variable and no `.env.example`
change. Clean energy uses the existing weights and taus, because
reweighting is out of scope by charter.

## What stays raw, and why

- **The blind detector** (`dashboard/bridge.py`) compares raw loudness
  to the noise-floor anchor. Its job is to notice music swamping the
  room, so it must see the music.
- **The noise floor** is fed raw. By design it is "whatever the room
  sounds like when nobody is talking", music included.
- **Headcount confidence's loudness term** (`headcount.py`, `rms_dbfs`
  vs noise floor) is published semantics of another layer. It is not
  M12-04's charter. Listed below as a finding.

## Decisions for the founder

- **D1 — clean fields when cancellation is off: `None` (recommended)
  or a copy of raw.** `None` is honest absence (invariant 5): nothing
  was cleaned. With cancellation on but nothing playing, the clean
  values equal raw anyway (bit-identical passthrough).
- **D2 — schema bump: yes (recommended, the charter's text) or no.**
  M12-02's `analysis_stream` and M12-06's `emotion_dominance_source`
  were added without a bump. M12-04's charter asks for one explicitly.
  The bump also lets `tuning_report.py` tell records that *could* carry
  clean fields from older ones.
- **D3 — M12-05 gets a volume-step phase.** This is constant talk with
  the volume moved 32 → 66 % mid-phase. The ladder can't show the
  feedback case (no in-take volume change), and the synthetic step is
  linear by construction. Its pre-registered rule for the mapper switch
  is drafted with the run sheet, not here.

## Findings to file (not fixed here)

- **Onset activity is level-invariant and barely content-sensitive**
  (1.4–2.1/s on speech, music and mix). It holds 0.20 of `energy_score`.
  Reweighting or redefining it is a calibration event (ROADMAP finding,
  for the founder to place).
- **Headcount confidence reads raw loudness over a raw floor during
  playback.** That should be measured in M12-05 before anything changes.
