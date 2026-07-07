# Field notes — live sessions

Informal, non-gating observations from running RTR in real environments.
The gates live in the milestone test plans; this file records what the
tool did in the wild, what the logs captured, and which hypotheses that
raises. Newest session first.

## 2026-07-06 — M4 gate part (c): first real playback (apartment, evening)

Two live sessions on the Mac, ~30 min (21:07–21:37) + ~17 min re-check
(21:40–21:57), solo speaker, Spotify playing through the MacBook speakers.
All override types round-tripped, degrade/recover on quitting Spotify
worked (engine never gapped), and `tuning_report.py` read all 13 override
records back. Two real bugs found, fixed between the sessions
(commit `8438264`):

1. **Stale-queue pile-up.** The controller pushed every selection to
   Spotify's queue assuming replacement, but the queue is **append-only**
   (no replace/remove). With recommendations firing at the 30 s dwell
   floor, 12 selections queued in 8 minutes; boundaries played the OLDEST
   while the "next" label showed the newest (observed live: bar promised
   Classical, boundary delivered the jazz queued 13 minutes earlier). The
   FakeProvider in the test suite modeled the wished-for replace
   semantics, so 185 green tests had validated fiction — the fake now
   appends, and the controller holds selections locally (latest-wins) and
   pushes exactly one track inside the final `RTR_PLAYBACK_QUEUE_LEAD_S`
   (15 s) of the playing track. Re-check confirmed: 5–6 selections
   superseded each other per track, one push per boundary, label and
   boundary agreed.
2. **False played_through on provider death.** Quitting Spotify mid-track
   (the degrade test) logged a played_through weak positive for a track
   last seen at 90 s of 246 — "vanished" counted as "ended". Completion
   now requires the last observation inside the boundary window of the
   track's own end. Re-check: all four played_through lines show real
   completions (67/68, 298/302, 255/259, 219/219 s).

Sensing observations under real playback (expected, now seen live):

- **Vocal-music mood contamination is real and visible.** Hip-hop/pop
  playback pushed valence/arousal into the excited quadrant even while
  the human was deliberately mellow. This is the known v1 gap — the
  certification gate can reject non-speech, but sung vocals that pass VAD
  carry the song's emotion, not the room's. Part (d) phases 5/6 will
  quantify it; feeds the M5 music-detection decision.
- **Headcount blipped to 4** once, coinciding with the air conditioner
  starting plus laptop/body movement — consistent with the pool session's
  finding that broadband noise onset perturbs the estimator. One blip in
  ~50 min with music playing is much better than the pool baseline;
  worth a deliberate AC-on phase in a future controlled session.

Session hygiene notes: hard-refresh the dashboard after every milestone
(the browser cached the M3 page and hid the M4 UI — consider a no-cache
header); clear the Spotify queue before a session that follows a
pre-fix run.

## 2026-07-06 — UofA pool (large reverberant space, loud ventilation fan)

**Setup.** ~7 people around the laptop talking intermittently at varying
distances. Two dominant environmental factors, both new relative to all
prior (apartment) testing:

- a loud ventilation fan close enough that the mic picked it up strongly;
- a very large, hard-surfaced space — open-space acoustics, much closer to
  the large-club / dancefloor regime we eventually want to target than to
  a living room.

Audio recordings of the mic input exist outside the repo:
`UofA Pool RTR Test M3 copy.m4a` (3:22) and
`Me and Claire super excited RTR Test M3.m4a` (0:40).

### Test 1 — group of 7, intermittent chatter (`UofA Pool RTR Test M3 copy.m4a`)

- **Mood: good.** Valence/arousal sat in the *excited* quadrant, which
  matched the room.
- **Headcount: poor.** Bucket ranged `solo`..`4` against an actual ~7.
- **Speech pickup: poor at distance.** The dashboard voice-input bar
  stayed low unless the speaker was within ~3 ft of the laptop. In the
  apartment the same laptop hears speech well from ~15 ft, so the fan
  (masking/raised noise floor) and the room's reverberation are the prime
  suspects, not the mic. Undercounting follows directly: speakers the VAD
  can't hear contribute no embeddings to cluster.

### Test 2 — solo → Claire joins (`Me and Claire super excited RTR Test M3.m4a`)

Solo speech to RTR, then Claire sat down next to the laptop and we had an
animated back-and-forth about the tool.

- **Headcount tracked the transition correctly**: `solo` → `pair`. Both of
  us were within a few feet of the mic, which likely overcame the fan.
- **Mood read strongly excited — accurate** (mutual enthusiasm, genuinely
  a high point).
- **Anomaly:** at the very end the bucket jumped `pair` → `16` with no
  change in the room. See analysis below.

### What the annotation log shows (`data/annotations/2026-07-06.jsonl`)

| time | bucket | raw_clusters | crowd_weight | speech_ratio | dBFS | mood |
|---|---|---|---|---|---|---|
| 11:44:10 | solo | 1 | 0.099 | 0.60 | −9.9 | excited |
| 11:51:32 | 4 | 5 | 0.134 | 0.79 | −13.8 | excited |
| 11:52:18 | 4 | 2 | 0 | 0.04 | −20.2 | excited |
| 11:55:01 | pair | 1 | 0 | 0.21 | −18.9 | excited |
| 11:59:16 | pair | 1 | 0.215 | 0.85 | −12.6 | excited |
| 11:59:27 | pair | 1 | 0.074 | 0.85 | −9.7 | excited |
| 11:59:57 | **16** | 1 | 0 | 0.77 | −12.7 | excited |
| 13:53:45 | solo | 3 | 0.026 | 0.51 | −28.2 | flat |

Note the loudness floor: the pool session sits around −10..−20 dBFS even
during sparse speech (the fan), where the later quiet-room entry reads
−28 dBFS.

### Analysis of the `pair` → `16` jump

The `16` snapshot looks self-contradictory — `raw_clusters: 1`,
`crowd_weight: 0` — but it isn't a logging error. The published bucket is
`BucketSmoother` output (EMA in log2 space, `tau_s=20`, plus 3-update
hysteresis), while `raw_clusters`/`crowd_weight` in RoomState reflect only
the **latest** window. For the smoothed value to round to 16 and survive
hysteresis, several consecutive windows in the ~30 s before 11:59:57 must
have produced high-log2 estimates via the crowd/babble path
(`headcount.py`, `crowd_weight` blend), then subsided by the time the
snapshot was taken.

The babble path fires on exactly this session's signature: `saturation`
needs speech_ratio ≳ 0.6 (we were at 0.77–0.85) AND loudness ≳ −45..−20
dBFS (fan-inflated −9..−13), times `smear` — high within-cluster embedding
dispersion, which reverberant open-space acoustics plausibly produce even
for two speakers. When all three line up, `log2_babble` (8–1024 range)
leaks into the estimate and the EMA climbs. So the jump is most likely a
**babble-regime false positive driven by fan loudness + reverb smear**,
not a clustering bug. Two people talking excitedly near the mic in a loud
reverberant room matched the "packed room" signature.

### Takeaways / open questions

1. **Strong broadband background noise is a real hurdle** — it masks
   distant speech (kills VAD reach, starves the headcount of evidence)
   and inflates the loudness term of the babble heuristic (occasional
   massive overcounts). Both failure directions in one session.
2. **Open-space acoustics matter** and are on the roadmap (club/dancefloor
   analysis), so this isn't an out-of-scope environment — it's an early
   look at the target regime. Reverb likely smears ECAPA embeddings,
   raising `dispersion`; worth measuring directly on the pool recordings.
3. **Debuggability gap:** RoomState carries only the latest window's
   `raw_clusters`/`crowd_weight`, so a smoothed-bucket anomaly can't be
   attributed after the fact. Exposing `BucketSmoother.smoothed_log2`
   (and perhaps the last few raw estimates) in state/annotations would
   have made the `16` jump diagnosable from the log alone.
4. **Possible mitigations to explore** (not yet decided): noise-floor
   estimation so the `saturation`/babble loudness ramp keys on
   speech-band SNR rather than absolute dBFS; checking whether Silero VAD
   confidence degrades gracefully under fan noise or misclassifies it as
   speech.
5. **Next test should be the control:** a closed, quiet room with a known
   group size, to separate "M3 headcount limits" from "pool-specific
   noise/reverb effects".
