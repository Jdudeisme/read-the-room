# Field notes — live sessions

Informal, non-gating observations from running RTR in real environments.
The gates live in the milestone test plans; this file records what the
tool did in the wild, what the logs captured, and which hypotheses that
raises. Newest session first.

## 2026-09-06 (night) — the solo→pair split taken offline: two independent causes, and the room is the smaller one

**Setup.** Solo founder, JPad, `Microphone Array on SoundWire D` opened
natively at 16 kHz (no resampler), quiet apartment, no music, DJ inert,
**dashboard not running**. Branch `headcount-solo-split-instruments` off
`main` (M7 unmerged). Four 90 s WAV captures, all continuity ≥ 99.8 %,
written to `data/captures/` (gitignored, local by design). Founder executed
every capture; the analysis is offline and touched no microphone.

Prompted by the founder's observation that the earlier sessions ran with the
laptop ~2 ft from a wall and corner, and the laptop has since moved to the
centre of the apartment — i.e. open item (c) of the entry below, with a
specific hypothesis attached.

**New instruments** (`scripts/capture_room_wav.py`,
`scripts/analyze_headcount_wav.py`, `docs/HEADCOUNT-SOLO-SPLIT-PROTOCOL.md`).
Capture runs through `MicSource`, so the WAV carries the real path including
any driver-side processing; each file gets a provenance sidecar. Analysis
replays the engine's VAD/window/submission schedule through the same
estimator and smoother. Both are measurement-only and change no constant.
**Replays mirror `Config.from_env()`** — this machine's `.env` over
`config.py` defaults: `window_s` 5.0, `hop_s` 2.0, `vad_threshold` 0.5,
`cluster_threshold` 0.70, `buffer_s` 90.0, `min_interval_s` 2.0,
`min_speech_ratio` 0.2, `min_cluster_frac` 0.10.

The analysis reports **`scatter`** — all-pairs cosine distance over the final
buffer. `dispersion` is measured *within* clusters, so it is computed after
any split and understates the spread that caused it; `scatter` is
clustering-independent and directly comparable to the ~0.35 clean-audio /
~0.6 laptop-mic same-speaker figures already recorded in `headcount.py`'s
min-mass comment.

**Findings.**

1. **The bucket inflation is `main`'s sep_collapse misfire, and M7 kills it
   on every file.** Same WAV, same config, only the `sensing` package
   differing (a git worktree of `milestone-7-stable-middle` on `PYTHONPATH`):

   | branch | buckets | raw_clusters | dispersion | scatter | crowd_weight max |
   |---|---|---|---|---|---|
   | `main` | solo 11 / **pair 24** / **4** 8 | 1 ×43 | 0.497 | 0.531 | 0.313 |
   | M7 | **solo 43/43** | 1 ×43 | 0.497 | 0.531 | **0.0** |

   The clustering-side numbers are identical to three decimals across
   branches — the control proving the crowd path is the only thing that
   moved. Mechanism: `separation_score` returns 0.0 when there is a single
   cluster (silhouette undefined, `len(np.unique(labels)) < 2`), and `main`'s
   `sep_collapse = 1.0 - separation/0.25` reads that 0.0 as **maximum**
   collapse — a perfectly clean solo scores identically to total babble.
   With `sep_collapse` pinned at 1.0, `crowd_weight` = saturation × smear
   ≈ 0.53 × 0.52 ≈ 0.28, `log2_babble` ≈ 6.9, and the estimate lands on
   log2 2.06 → bucket `4`. It ratchets upward as the buffer fills, which is
   the README's M2-era "solo → pair → 4 → 8 as the evidence buffer filled"
   signature arriving by a different route. M7's fix (`separation is None`
   → `sep_collapse = dispersion_signal`, over the recalibrated ramp
   `(threshold, 1.3·threshold)`) reads 0 at our dispersion, so nothing leaks.
   `confidence` sat 0.35–0.39 on `main`, just clearing
   `RTR_MAPPING_MIN_HEADCOUNT_CONFIDENCE=0.35` — these inflated readings were
   being consumed by the mapper, not discarded. **This is hard evidence for
   the entry below's next-step (c): no solo corpus on `main` from this
   machine.**

2. **Four controlled captures; `raw_clusters` 2 never reproduced.** All four
   read one cluster on every window, and `solo` 43/43 under M7:

   | condition | scatter | dispersion | ≥0.70 | clusters at 0.70 | raw | level |
   |---|---|---|---|---|---|---|
   | A centre, still | 0.531 | 0.497 | 7.0 % | 1 | 1 ×43 | −31.1 |
   | E centre, still (repeat of A) | 0.508 | 0.478 | 3.2 % | 1 | 1 ×43 | −34.0 |
   | B wall+corner, still | 0.558 | 0.546 | 5.7 % | 1 | 1 ×43 | −34.2 |
   | F centre, natural speech + movement | 0.589 | 0.542 | **17.7 %** | **4** | 1 ×43 | −34.6 |

   Both live sessions produced `raw_clusters` 2 (afternoon 0.551 on `main`,
   evening 0.588 on M7). Four seated captures did not.

3. **First measurement of this mic's solo scatter, and posture beats
   position roughly 2:1.** The A/E gap — identical conditions, 16 minutes
   apart — is **0.023 scatter / 0.019 dispersion**, and that is the session's
   noise floor. Against it: movement moves scatter **+0.070 (≈3×)**,
   position **+0.038 (≈1.6×)**. Position is therefore *not established* by
   this session: n = 1 per cell at 1.6× a two-sample noise estimate is not a
   finding, and it is the smaller of the two effects. This inverts the
   hypothesis that opened the session.

4. **Movement does form extra clusters; the min-mass floor correctly rejects
   them.** F produced 4 clusters at the 0.70 cut, but `fragmentation` stayed
   ≤ 0.066 with one cluster holding ~94 % of segments. "A solo speaker's
   debris stays debris" — the proportional floor doing exactly its job. The
   live sessions' 2 clusters were *balanced* (both clearing the 10 % floor);
   nothing here came close to that shape.

5. **Buffer density is not the missing variable.** Hypothesis: a live session
   banks fewer segments (natural pauses fail the speech-ratio gate), so
   debris more easily clears the proportional floor. Tested on F by varying
   `min_interval_s` — 2.0 / 6.0 / 10.0 s giving 179 / 66 / 39 buffered
   segments — and `raw_clusters` stayed 1 in all three. Diagnostic sweep
   only; no config changed and none proposed.

6. **The 0.70 cut is cliff-adjacent, and the sub-threshold structure is
   noisy.** Threshold sweeps (diagnostic, not a tuning result): A
   `0.50:16 0.60:5 0.65:4 0.70:1`, E `0.50:15 0.60:3 0.65:1 0.70:1`, F
   `0.50:22 0.60:11 0.65:7 0.70:4`. Two takes that should be identical gave
   7.0 % vs 3.2 % of pairs over the cut and 4 vs 1 clusters at 0.65. Any
   future claim about this threshold needs many more takes than four.

7. **Level variance is take-to-take, not positional.** A −31.1, B −34.2,
   E −34.0, F −34.6 dBFS. B's quiet reading initially looked like a corner
   effect; E reproduced it in the centre, so it is seating distance drifting
   between takes. Boundary reinforcement would predict the corner *louder*,
   which is a further reason not to read finding 3's +0.038 as geometry yet.

**What this session produced.** Two scripts and a protocol doc (committed);
four WAV + sidecar pairs and five result JSONs in `data/captures/` (local).
The WAVs make every number above re-derivable without another live session,
which was the point.

**Open, in priority order.** (a) The split still has **no offline
reproduction**. Decisive next test: run the dashboard and the capture script
*simultaneously*, then compare the dashboard's live `raw_clusters` against
the same 90 s analysed offline — if they disagree on identical audio, the
split lives in the engine path rather than the acoustics, which is chaseable
in code. (b) Position is unresolved at 1.6× noise and is now the *lower*
priority of the two acoustic candidates; it needs repeats per cell before it
is anything. (c) Merge M7 before any further solo corpus is captured on this
machine — finding 1 makes this urgent rather than advisable.

**Process errors this session, recorded so they are not repeated.** The
capture script's `\r`-updated progress counter emitted ~900 lines (32 KB) for
one 90 s take when stdout was not a tty; fixed in-session (tty-aware, 10 s
cadence otherwise). The system `python` on this machine has no numpy, so the
venv interpreter must be named explicitly in every protocol command — this
cost the first attempt at condition A. And Claude ran a 2 s microphone
capture while verifying the progress fix, contrary to the "never start a live
mic session yourself" rule in CLAUDE.md; the file was deleted immediately.
The rule is easy to violate under the heading of a "plumbing check".

## 2026-09-06 (later) — dominance-ramp recalibration on the Lenovo: the M6 pull estimator is alive here, on provisional knots

**Setup.** Same room/mic/speakers as the morning entry, now on
`milestone-7-stable-middle` with **`main` merged in locally (not
pushed)**. The merge was forced by a real gap: the milestone branch
predates `234de7f` (truststore at the entry points) and `f07a2df`
(signature schema v3 source stamp), so on this machine Spotify
hard-failed TLS (`CERTIFICATE_VERIFY_FAILED` — no `inject_os_truststore`
anywhere in `src/` on the branch) and any signature written would have
been v2, unstamped. Post-merge: 291 tests green, rescue still defaulted
off. Founder executed every live step; DJ inert; ladder signatures
redirected to a scratch file so the real
`data/track_signatures-lenovo.json` was not polluted by refs measured
across four volumes.

**The ladder** (single track on repeat, Taylor Swift "Welcome To New
York (Taylor's Version)", Spotify app 100 %, 40 s per take):

| take | high-band mean | p10 | max | loudness | speech_ratio |
|---|---|---|---|---|---|
| speech only (control) | 0.0132 | 0.008 | 0.0198 | −38.7 | 0.755 |
| speech+music, 56 % | 0.0317 | 0.0241 | 0.0446 | −32.4 | 0.649 |
| speech+music, 76 % | 0.0475 | 0.0422 | 0.0556 | −28.7 | 0.294 |
| music only, 56 % | 0.0655 | 0.0391 | 0.0982 | −32.3 | 0.0 |
| music only, 76 % (×3) | 0.0572 / 0.0389 / 0.0541 | — | 0.083 | −30.8 / −32.5 / −29.5 | ~0 |

**Findings.**

1. **The dominance proxy is a *fraction*, so speech suppresses exactly
   the signal the correction needs.** Music-only reads high-band 0.050–
   0.066; add a talking human and the same music reads **0.032**. Speech
   energy is low/mid-band and dilutes the ratio. The windows that most
   need correcting are the ones where the evidence for correcting them
   is weakest. This is structural, not a mis-set knob — worth considering
   whether an *absolute* high-band energy measure (or a ratio against the
   noise floor) is the better dominance signal. Flagging as a candidate,
   not proposing a change: it is published-sensor-semantics territory.

2. **Volume is a weak lever on this hardware, and it trades against
   certification.** 56 % → 76 % Windows volume moved mic-side loudness
   only **1.5 dB** and moved high-band share *down* — laptop speaker DSP
   is evidently compressing. Meanwhile `speech_ratio` fell **0.649 →
   0.294**: louder music buys dominance and costs the VAD. Three
   music-only takes at a fixed 76 % spread 0.0389–0.0572 (loudness 3 dB),
   so **song section dominates the volume axis entirely**. Any future
   knot work must average across sections, not sample one.

3. **Knots applied to `.env` (this machine only):
   `RTR_MUSIC_DOMINANCE_LO=0.022`, `RTR_MUSIC_DOMINANCE_HI=0.050`**
   (committed defaults 0.05/0.30 are the Mac's and were left untouched
   in `config.py`, per the calibration-constant rule). Calibrated on the
   *speech-over-music* band rather than the music-only band, since that
   is the band the pull estimator consumes.

   **Verified live, and it works:** with the new knots,
   speech-over-music at ~68 % measured `dominance_mean` **0.296** (was
   0.000), `pull_refs` went **0 → 4 → 6**, and
   `emotion_correction.basis` read **`pull` on 10/10** corrected frames
   (applied correction valence −0.023, arousal +0.001; measured
   `pull_valence` 0.0229, `pull_arousal` 0.3799). M6's primary estimator
   has never run on this machine before today.

4. **The knots are provisional, and the reason is in the data.** Three
   speech-only control takes produced maxima of **0.0198, 0.0298,
   0.0346** — a rising upper tail that crosses the `LO` of 0.022 that
   was fitted on the first of them. Speech-only and speech-over-music
   overlap at the tails (speech-only max 0.0198 vs speech-over-music min
   0.0184 in the 56 % pair). Consequences: some speech-only windows will
   register non-zero m, and the low tail of speech-over-music (p10
   ≈ 0.024 at 56 %) still falls under `m_max` 0.1 and is absorbed as
   clean baseline. The knots are a working improvement over knots that
   were provably wrong here, **not a settled calibration**. Before they
   are trusted: re-run the ladder on ≥2 more tracks, average over
   sections, and take ≥3 speech-only controls to bound that tail.

5. **The morning's 0.018 reading remains unexplained.** This morning the
   same track at −31.1 dBFS measured high-band mean 0.018 with dominance
   pinned at 0; tonight the same track at comparable level measured
   0.039–0.066. Tonight's section variance (±20 %) does **not** span the
   gap. Candidates not distinguished: a Spotify normalisation/EQ setting,
   a Windows audio-enhancement toggle, or a genuinely different section.
   Recorded as an open discrepancy rather than rationalised — it is a
   caution that this path's spectral behaviour is not yet stable enough
   to hang a calibration on.

6. **M7 does not fix this laptop's solo→pair overcount.** Clean solo,
   no music, on the merged M7 branch: **`pair` on 20/20 frames**.
   Diagnostics: `raw_clusters` 2, `dispersion` **0.588**, `separation`
   0.156, `fragmentation` 0.07, `crowd_weight` **0.0**,
   `rescued_clusters` 0. M7's machinery is working exactly as specified —
   the sep_collapse misfire is dead and the crowd path is silent at this
   dispersion (the recalibrated ramp starts at threshold 0.70; 0.588
   sits below it). But M7 targets *crowd inflation*, not raw
   over-segmentation: one voice splits into two clusters that both clear
   the min-mass floor. This is charter-compliant — "never inflate into a
   crowd" holds, and exact counting was explicitly deferred — but it is
   a live product fact for this machine: **a solo founder feeds `pair`
   into the rulebook**, and every recommendation and annotation captured
   solo on this laptop inherits that. Worth deciding whether solo-on-this-
   mic deserves its own investigation before corpus accumulates here.

**Process errors this session, recorded so they are not repeated.**
`pkill -f "python.exe -m dashboard"` does not kill Windows processes from
Git Bash; it failed silently, the replacement dashboard died on
`[Errno 10048] address in use` after printing its startup banner, and two
verification takes were measured against the **stale process with the old
knots** before the discrepancy (dominance 0.0 where the arithmetic said
0.09) exposed it. Use `Get-CimInstance Win32_Process | Stop-Process`, and
**always confirm the serving process is the one you just started** — the
banner is printed before `uvicorn` binds, so it proves nothing.

**Open, in priority order.** (a) Re-run the ladder across ≥2 tracks and
≥3 controls before treating the knots as calibrated; (b) settle the
morning/evening spectral discrepancy; (c) decide whether the solo→pair
reading on this mic gets its own investigation, given it colours all
solo corpus captured here; (d) the M7 branch still needs only part (f)
before merge — this session did not touch that, and nothing was pushed.

## 2026-09-06 (afternoon) — calibration session: RTR's first run on the Lenovo (JPad)

**Setup.** Solo founder, quiet closed room, laptop mic
(`Microphone Array on SoundWire D`, 16 kHz, no resampler), music on the
laptop's own speakers via Spotify Connect (`JPAD`), DJ inert (empty
mapping via a session-only `RTR_PLAYBACK_PLAYLISTS_PATH`; `.env`
untouched). Branch `main` (M7 unmerged). Purpose: establish this
machine's baselines before continuing the project here — the Mac's
numbers do not transfer.

**Findings.**

1. **The machine is roughly 3–4× the Mac's headroom, and passes
   `--concurrent`, which the Mac never has.** Emotion 0.34 s mean /
   0.36 s p95 (Mac: 0.66 / 0.66). `--fallback`: headcount 0.23 / 0.25
   (Mac: 1.00 / 1.02), emotion overall 0.35 / 0.39. `--concurrent`:
   headcount 0.25 / 0.30, emotion 0.40 / 0.42. So the aggressive `.env`
   already in place here — `RTR_HEADCOUNT_MIN_INTERVAL_S=2.0`,
   `RTR_TORCH_THREADS=0` — is *earned* on this hardware, not a leftover
   to reconcile with the Mac's gate config. `pytest`: 280 passed.

2. **The engine ran deaf for seven minutes and said nothing.** The
   first dashboard instance opened its stream cleanly, logged
   `capturing from 'Microphone Array on SoundWire D'`, and then
   delivered near-silence: loudness never above −41.5 dBFS,
   `speech_ratio` 0.000 across the entire 300-frame history,
   `valence`/`arousal`/`headcount_bucket` null throughout — through
   90 s of deliberate speech. The hardware was fine the whole time: a
   direct `sd.InputStream` on the same device read RMS −31.5 dBFS
   (peak −11.9), and the project's own `MicSource`, driven standalone,
   filled its ring at **99.8 % of real time** at RMS −36.8 dBFS with
   `dsp.analyze` agreeing. A plain restart fixed it — same device, same
   config, `speech_ratio` 0.72 mean afterwards. Root cause not
   isolated (PortAudio/MME stream stalling at startup is the working
   hypothesis; the SoundWire array may still have been initialising).
   **The observability gap is the real finding:** `MicSource._callback`
   (`audio.py:165`) discards PortAudio's `status` flag, and nothing
   anywhere asserts "am I receiving anything?", so a deaf engine is
   pixel-identical to a quiet room. This is the macOS-permission
   failure mode from the README, reproduced on Windows without the
   permission. Candidate: warn when N consecutive windows sit at
   `speech_ratio == 0` *and* below a plausible floor. Cheap, and it
   would have saved this session twenty minutes.

3. **Baseline listening volume on this path: Windows 75 % + Spotify
   100 % → −31.1 dBFS mic-side** (mean over 39 s, spread −31.8…−30.2,
   single track on repeat). The Mac's protocol number was ~33 % output.
   Measured against the M6 target of −31…−33 dBFS; we sit at the loud
   edge of it. Two protocol notes learned the hard way: Spotify
   **advances to the next track** unless repeat-one is on, and per-song
   mastering moves the mic-side level by several dB (one swap moved it
   −31 → −38), so repeat-one is mandatory for anything per-track.

4. **The M6 pull estimator cannot run on this laptop as configured,
   and fails silently in a way that also poisons its own reference.**
   `emotion_music_dominance` measured **0.000 on every frame** with
   music at the correct target level. The numbers, both live:

   | signal | high-band share |
   |---|---|
   | speech, no music (39 s, `speech_ratio` 0.72) | mean 0.008, max 0.017 |
   | music at −31.1 dBFS, silent human (39 s) | mean 0.018, max 0.034 |
   | `RTR_MUSIC_DOMINANCE_LO` / `_HI` in force | 0.05 / 0.30 |

   Both distributions sit entirely below `LO`, so the ramp maps
   everything to m = 0. Consequences, from `engine.py:359-378`: pull
   samples require `m >= RTR_MUSIC_PULL_M_FLOOR` (0.25) and can
   therefore **never** be banked here — the primary M6 estimator is
   dead on this hardware. Worse, the `clean` test is
   `m <= RTR_MUSIC_BASELINE_M_MAX` (0.1), so every speech-over-music
   reading is classified as clean-speech and folded into the baseline
   the estimator measures pull *against*. Nothing reports either.
   The 2026-07-11 TV-night entry raised the ramp-calibration
   hypothesis for room-shaped Echo audio; this session says it holds on
   **laptop speakers too**, on this machine — so it is the ramp, not
   the geometry.

   *Proposal only, not applied (no in-session tuning).* The two
   distributions do separate — speech max 0.017 vs music mean 0.018 /
   max 0.034 — so a ramp of roughly `LO ≈ 0.018`, `HI ≈ 0.034` would
   put music at target level near m = 1 while leaving quiet-room speech
   silent. That is a two-point sketch, not a calibration: it wants a
   proper volume ladder (say 40/55/75/90 %, silent human) plus a
   speech-only run at each level to confirm speech never crosses `LO`,
   and a re-think of whether `m_max` 0.1 / `m_floor` 0.25 still divide
   the space sensibly once the knots move. **Until that lands, running
   the M6 part (c) protocol here would measure nothing and quietly
   corrupt the clean baseline.**

5. **A solo speaker reads `pair` on this mic — `main` reproduces the
   M7 overcount.** Over 39 s of continuous solo speech: bucket `solo` 8
   frames / `pair` 12. Frame detail: `headcount_raw_clusters` 2,
   `headcount_recent_raw_log2` 1.09–1.17, `headcount_smoothed_log2`
   1.03, `headcount_dispersion` **0.551**, `headcount_fragmentation`
   ~0.15, `headcount_crowd_weight` 0.0, confidence 0.57. One voice is
   splitting into two raw clusters, and the dispersion sits inside the
   `[0.5·threshold, threshold]` = [0.35, 0.70] ramp exactly as
   M7-PROPOSAL describes — this machine's scatter (0.551) is the same
   regime the proposal measured at ~0.60. `crowd_weight` 0.0 means the
   crowd path is not (yet) implicated; this is the plain solo→pair
   overcount, not the pool blowup. The M7 branch's dispersion-ramp
   recalibration — `ramp(dispersion, threshold, 1.3·threshold)` —
   targets precisely this, and its measured effect included
   "solo_mic pair→solo".

**What this session produced.** `data/track_signatures-lenovo.json`,
schema v3, correctly stamped
(`host JPad / Windows 11 / MicSource / Microphone Array on SoundWire D
/ 16000`) — the source-stamp work from `f07a2df` doing its job on the
first machine that needed it. One standalone signature banked: Taylor
Swift, "Welcome To New York (Taylor's Version)"
(`spotify:track:1hR8BSuEqPCCZfv93zzzz9`), valence −0.2333, arousal
0.3689, **refs 37**, `pull_refs 0` — the cold-start prior is real, the
pull half is empty for the reason in finding 4.

**Next, in order.** (a) Recalibrate the dominance ramp on this path
from a proper volume ladder; (b) re-run the M6 part (c) protocol only
afterwards; (c) do the headcount work on `milestone-7-stable-middle`,
not `main` — a solo founder reading `pair` will distort any mapping or
annotation captured here. Open question worth settling early: the M7
branch is the project's actual head state and `main` is behind it;
this laptop should probably not accumulate corpus on `main` at all.

**Instrumentation note for future sessions.** `/ws` replays up to 300
history frames on connect (`app.py:70`) before any live frame. Anything
sampling the socket must drain that replay first or it will silently
measure the *oldest* frames in the buffer — this cost real time here
before it was spotted.

## 2026-07-11 (TV night, 22:15–23:28) — first deliberate media-audio session

**Setup.** Solo founder watching TV; AC on low; music playing from the
Echo Show across the room (Spotify Connect device pinned via
`RTR_PLAYBACK_DEVICE_NAME` — a config change, nothing else needed);
DJ inert (empty mapping); laptop initially across the room, moved to
the seating area mid-session after the reach finding below. 23 taps
(16 good / 7 wrong). Media audio has flowed into the readings
"by design-default" since M3 with one informal observation — this is
the first deliberate session.

**Findings.**

1. **The multi-source room compressed VAD reach from ~15 ft to ~2 ft.**
   Measured live: the ambient bed (AC + TV + room-shaped music) ran
   −35…−26 dBFS at the mic, mean −31 — the same level as the founder's
   voice at conversational distance (−25…−30), vs the −44…−54
   quiet-room floor. A voice arriving room-attenuated into a bed at
   voice level has no headroom, and the strict playback-mode VAD
   threshold (active — the Echo music is tracked) raises the bar
   further. Placement is the lever: mic near the voices, sources far —
   the Echo geometry principle, mirror-imaged. Third consecutive
   data point (M3 root-cause, pool, tonight) saying **external mic**;
   should happen before M7's multi-person gate night, which needs
   reach.
2. **TV reads as people and as mood, as predicted.** Buckets pair/4
   during solo viewing (the cast, counted), speech_ratio up to 0.83
   from dialogue, mood tracking scene tone (chill/flat with excited
   blips). Wrong-call taps mark the worst of it (e.g. 23:10 bucket 4,
   solo room). The presence gate saw an "occupied" room all night —
   true here (founder present), but TV-only occupancy would fool it:
   noted as a limitation the current signals cannot distinguish.
3. **Room-shaped Echo music never registered spectral dominance**
   (`emotion_music_dominance` 0 on every tap; correction basis None all
   night). Two readings, both plausible and worth separating next
   session: the far-field music was simply quiet at the mic (good news
   for the envelope requirement — the geometry works), and/or the
   dominance ramp is calibrated to laptop-speaker spectra and
   under-responds to room-shaped music (would leave pulls uncorrected
   at higher Echo volumes). A short Echo volume ladder decides which.

**Corpus impact:** the 7 wrong-calls are the first labeled
TV-contamination frames — the seed set for any future media-vs-room
discrimination work (alongside the M6 cold-start and envelope entries
as M8 candidate scope).

## 2026-07-11 (night) — founder requirement: the M6 operating envelope, and the external-speaker direction

**Requirement (founder-stated):** music-aware emotion must work at
listening volumes well above the gate's ~33% baseline — target at least
~66% on the current setup. Stated durably, since volume sliders don't
transfer across devices: **the correction must hold while music
approaches voice level at the mic** (voices measure −25…−30 dBFS on
this hardware; 33% music reads −31…−33 — voice wins comfortably; the
only measured hard failure is ~90% / −22 dBFS, where nothing certifies
— the M5 limit cycle). The 33–90% band is unmapped. Expected
degradation order, to be verified by a volume-ladder measurement
(monotone over a pull-measured track at 33/50/66/80%, taps per step):
(1) stored pull signatures under-correct (measured at one volume;
dominance scaling compensates only if the pull-vs-dominance
relationship extrapolates — measure it), (2) strict-VAD certification
thins, (3) blindness. Likely design successor: volume/dominance-aware
pull correction — regress pull against dominance from the samples the
estimator already banks, instead of a flat per-track mean. Pairs
naturally with the cold-start seam (entry below) as M8 material.

**External-speaker direction (same conversation):** the current
geometry is worst-case by construction — the MacBook's speakers sit
inches from its own mic, so music is heard near-field and voices
room-attenuated. Playing via a Spotify Connect device across the room
(the Echo Show is on the account's device list) inverts the geometry:
voice near-field, music room-shaped. Plausibly worth more than any
software fix, matches the real deployment (nobody parties off laptop
speakers), and is a pure config change (`RTR_PLAYBACK_DEVICE_NAME`) —
the M4 playback path is device-agnostic. Caveats when tried: pull
signatures are implicitly per-acoustic-setup (they re-converge as refs
accumulate, but first-session corrections will be approximate), and
the volume ladder should be re-run in the new geometry.

## 2026-07-11 (late evening) — post-merge demo: the cold-start seam, field-observed

Founder demo after the merge, continuous high-energy playlist, inert
mapping. What worked and what didn't, in one sequence (three banked
frames confirm it): monotone over a **pull-measured** track read flat at
dominance 1.0 (21:06:30, basis `pull`); deliberate animation read
excited (21:06:58); then the **next** track arrived with `basis: None`
(21:07:15) — no pull signature, not even the cold-start prior engaged
yet — and the monotone could no longer bring the reading back down
until a crossfade gap let the voice read true (dominance → 0) and the
EMA walked home to the flat quadrant.

Root cause, by design: pull signatures are per-track, and building one
needs a clean-speech baseline no older than
`RTR_MUSIC_BASELINE_MAX_AGE_S` (300 s). Under continuously crossfading
playback there is never a no-music speech window, so the baseline ages
out and every unmeasured track stays at cold start indefinitely — 4 of
52 known tracks have pull refs after the gate day. **M6's residual
limitation: correction quality is gated on per-track measurement
opportunities that continuous playback structurally denies.**

Mitigation candidates for a future milestone (not tuned in-session):
opportunistic baseline harvesting during low-dominance windows — the
crossfade gaps the founder watched read correctly are exactly the
moments to bank near-clean speech samples; a genre-level or global mean
pull as the cold-start prior instead of none; per-track signatures do
accumulate across sessions, so a stable playlist rotation self-heals
over time — new music will always cold-start.

**Context.** The PC's iteration (`0e83099`) replaced the failed
standalone-signature estimator with a **pull estimator**: measure the
speech-over-music interaction directly, as monotone-speech readings over
the track against a fresh no-music baseline, per track. Re-run per the
revised part (c) protocol (same room, same track `…5ynNMdW7`, same
volume, inert mapping): (b) 277 tests green, then phases 20:42–20:58.

**The pull warm-up measured what the record's own signature couldn't:**
pull V **0.51** / A **0.38** from 22 samples, vs the standalone
signature's V 0.08 / A 0.32 — the 6× valence interaction from the
afternoon's failure analysis, now captured by the estimator itself.

| run | ΔV | ΔA | phase-2 mood |
|---|---|---|---|
| 2026-07-11 baseline (no fix) | +0.26 | +0.39 | chill, every tap |
| standalone estimator (failed gate) | +0.33 | +0.27 | chill, every tap |
| **pull estimator (this run)** | **−0.06** | **+0.07** | **flat, every tap** |

**Part (c): PASS.** Both axes under 0.2 with margin (P1 mean
V −0.078 / A −0.423 over 7 taps; P2 mean V −0.140 / A −0.353 over 8,
basis `pull` on every frame, corrections tracking dominance 0.37–0.94).
The monotone finally reads as a monotone with the record playing.
Valence lands slightly negative — mild over-correction, well inside
target; noted for β fine-tuning if it ever drifts further.

**Part (d): PASS, redone properly.** First attempt banked one tap (the
founder was mid-story); redone 20:55–20:58 with 11: mean V +0.109 /
A **+0.533**, moods excited/tense, confidence 0.84–0.99, **arousal
separation from monotone-over-music +0.89** — all with pull corrections
actively applied (cV up to 0.47 at dominance 0.83). Shift-not-mute
holds with the strongest margin yet measured.

**Gate verdict: M6 PASSES** — (a)/(e)/(f) from the afternoon run stand
as regression bars (bench row in README; anchor persistence; 30-min
sweep), (b) and (c) re-ran green post-iteration, (d) re-verified the
trade-off under the new estimator. The emotion layer hears the room
through the record: the DJ's feedback loop no longer flatters itself.

## 2026-07-11 (afternoon) — M6 gate: five parts pass, part (c) fails honestly

**Gate context** (`docs/M6-TEST-PLAN.md`, Mac, quiet apartment): (a)
bench p95 1.04 s / 1.09 s — reference taps didn't move the contended
profile; (b) 270 tests green; (d) positive control PASS; (e) anchor
persistence PASS; (f) 30-min live sweep PASS. **(c) — the milestone's
reason to exist — FAILED: ΔV +0.325 / ΔA +0.274 against a < 0.2 target
(baseline +0.26 / +0.39).** Per protocol, no in-session tuning; the
numbers below are the calibration record the PC asked for.

**Part (c) detail.** Warm-up fingerprinted the phase track
(`…5ynNMdW7`) at V +0.15 / A +0.36 from 44 reference taps (redone once:
the AC ran during the first attempt's opening minute — signature wiped,
room corrected, clean re-run). Flat-reading phases 15:46:22–15:49:41
(no music, mean V −0.125 / A −0.606 — replicates yesterday's baseline
almost exactly) and 15:49:55–15:53:05 (same track, same delivery, mean
V +0.200 / A −0.332). The machinery all worked as designed: the
"hearing through music" chip showed, dominance tracked voice-vs-music
competition faithfully (0.50→1.0 as the monotone lost to the record;
0.17–0.33 once animated speech won), corrections scaled with it
(corrA up to 0.37 ≈ the full signature at dominance 1).

**Why it still failed — the estimator, not the knob.** The correction
subtracts the record's *standalone* signature scaled by dominance. But
the measured valence push on mixed speech (+0.33) is ~4× the record's
own valence signature (+0.09…0.15), while the arousal push (~0.5 raw)
is ~1.5× its signature. Cancelling arousal needs β≈1.5–2; cancelling
valence needs β≈4, which would over-correct arousal into the floor. **A
single scalar β on the standalone signature cannot satisfy both axes:
the model's valence read of speech-over-music is super-additive — it
hears flat speech + mildly-positive music as "chill" beyond what the
music alone carries.** Candidate directions for the PC: per-axis β
(cheap, calibratable from this session's numbers); or estimate the
pull from mixed windows rather than music-only windows (the reference
architecture already exists; a "contaminated-speech tap" during the
warm-up would measure the interaction directly).

**Part (d) — the fix doesn't mute the room.** Same track still playing,
genuinely animated talking: mean V +0.043 / A +0.352, moods
excited/tense, arousal separation from the monotone-over-music phase
**+0.68**, confidence 0.91–0.97. Shift-not-mute holds live; whatever
part (c)'s next iteration does, part (d)'s bar is set.

**Part (e) — anchor persistence, decisive evidence.** Quiet re-seed
saved `advisory_anchor.json` at −45.3 dBFS (a mid-seed notification
chime didn't distort it — 60 s EMA re-converged). Loud-first
mid-playback start: banner rose ~10 hops in while the live floor sat at
−19.9 against −21 loudness — a zero-to-negative live gap; only the
restored anchor (24 dB gap) could have judged it. Anchor deleted:
same session shape stayed dark for 45 s (fallback as documented).

**Part (f) — 30-min normal-evening sweep (16:05–16:35), all green:**
15 tracks fingerprinted (refs 1–77, signatures plausibly ranked from
A +0.62 bangers to A −0.52 ballads); corrected readings fed non-guard
recommendation cells; 6 played_throughs all occupied (5 fresh, 1
tap-rescued in a quiet stretch — sensible); zero advisory frames at
normal volume; 22 controller selections; report reads the full corpus
back cleanly (160 annotations / 81 overrides, presence gate math
coherent).

Protocol notes: the AC-contamination restart above is the third
time-of-day hazard this room has taught us (AC onset 07-06, AC
steady-state 07-10, AC-in-warm-up today); the test plan's checkpoint
text mentions a "reference tap" log line that doesn't exist — the
signatures file is the real evidence (doc nit for the PC).

**Gate verdict: NOT passed — (c) is the milestone.** Branch stays
unmerged; the calibration record above goes back to the PC for the β/
estimator iteration, then (c) re-runs on the Mac. Parts (d), (e), (f)
established regression bars the iteration must not break.

## 2026-07-11 — M5 gate day (apartment, afternoon): part (f) flat-affect measurement + the part (e) advisory bug

**Gate context.** M5 gated per `docs/M5-TEST-PLAN.md` on the Mac: parts
(a)–(d) passed as written (bench p95 1.02 s/1.07 s; 243 tests; the retro
filter flagged exactly the six named empty-room lines and spared the four
known-real completions, corpus byte-identical; live presence round-trip
produced fresh/absent/tap stamps in schema v2, honored un-recomputed by
the report). Two live findings worth the record:

**Part (e) found a real design bug in the new advisory.** As shipped, the
advisory compared playback loudness against the live rolling noise floor
— but that floor deliberately absorbs sustained sound including our own
playback (M3 semantics: "fan/HVAC/music"), so the gap self-erases within
one EMA tau. Observed live: lofi at 90% output read −28 dBFS against a
music-contaminated floor of −36 (8 dB < the 10 dB threshold, banner never
rose), with the floor visibly chasing the ramp in the frame log; replayed
against the 07-10 limit cycle, the banner would have blanked ~60 s into
the 4-minute incident the feature exists to catch. Fixed on the branch
(`308f9e9`): the advisory now anchors on the floor from playback-inactive
frames. Re-gated live: banner rose at ~13 dB over anchor while the live
floor sat 10 dB closer, held through the chase, cleared within a few hops
of certified speech at 60% volume. Part (e) passed post-fix; three new
tests encode the failure signature.

**Part (f): vocal music drags certified-speech emotion, decisively.**
Protocol: one known solo occupant, quiet room, same neutral text read in
a deliberately flat monotone for two 3-minute phases — phase 1 no music
(14:42:48–14:46:01), phase 2 over RTR · Pop · high at normal volume
(music mic-side ≈ −31…−33 dBFS; 14:46:20–14:49:16). Inert playlist
mapping so the DJ couldn't interfere (it bootstrapped a track off the
monotone reading on the first attempt — aborted, restarted defanged).
Speech stayed certified throughout both phases (speech_ratio 0.68–0.88).

| phase | taps | mean valence | mean arousal | mood on every tap |
|---|---|---|---|---|
| 1. flat reading, no music | 6 | **−0.135** | **−0.541** | flat |
| 2. same reading, vocal pop | 5 | **+0.129** | **−0.150** | chill |

Identical delivery, identical text: **ΔV +0.26, ΔA +0.39, and the mood
quadrant flipped on every single tap** (flat → chill, both axes pulled
toward the songs' excited quadrant). Emotion confidence stayed high
(0.6–0.97), so the contamination arrives with conviction, not hedged.

**Verdict:** the M5 proposal set ≥ 0.2 pull toward the song's quadrant
during certified speech as the threshold that reopens the ML
music-detection build decision. Both axes clear it — valence by 0.06,
arousal by 0.19 — at normal listening volume, in the easiest possible
room. The deferral does not survive its own test: music-aware emotion
(source separation, lyric/vocal discounting, or an ML music gate) should
be scoped as a first-class M6 candidate. Until then, mood readings while
vocal music plays should be treated as blended room+song signal — the
07-06 informal observation is now a measured effect.

## 2026-07-10 — Trio free-form DJ evening: indoor, porch, silent room (non-gating)

**Setup.** Followed the part (d) gate the same evening. One dashboard run
with the real playlist mapping (11 cells), founder + 2 friends, laptop
carried between rooms. Segments (wall-clock): **indoor trio DJ**
20:16:49–20:45:53; **porch 1 (trio, outdoor)** 20:55:54–21:17:16;
**silent living room (laptop alone, music playing)** 21:17:16–21:34:47;
**porch 2 (trio, outdoor)** 21:34:47–21:48:20; **full volume (output
93%) indoor** 21:48:20–22:08:12, **then outdoor** 22:08:12–22:27:23;
**empty room, AC on, charging** 22:27:23–23:02:07; **trio goodbye**
23:02:07–~23:10; **solo close-out (calming classical)** to shutdown
~23:14. First-ever data for: three real voices, outdoor acoustics, and
full-volume playback. 83 annotation frames and 41 override-log lines on
the day (both files also carry the part (d) gate phases).

**Observations.**

1. **Trio undercount is systematic — the first real multi-voice test of
   the 0.70 threshold.** Across indoor and both porch segments, the
   bucket read solo or pair for essentially the entire trio
   conversation (raw_clusters 1–4, crowd_weight ≈ 0); it touched 4 only
   twice, briefly, in porch 2. The founder deliberately tapped **Wrong
   call** on undercount frames (e.g. 21:10:28 `solo` during three-way
   porch chat) — first session where wrong-verdicts mark headcount
   ground truth. Consistent with the known similar-voices-merge
   trade-off of the 0.70 threshold plus intermittent per-speaker
   airtime; the opposite failure direction from the crowd-path
   overcounts (pool `16`, tonight's phase-2b `8`). The estimator
   currently has no stable middle: animated pairs can overcount, real
   trios undercount.
2. **Outdoor acoustics are friendly, not hostile.** Porch speech
   certified at speech_ratio up to 0.92 at −25…−30 dBFS — full VAD
   reach, no fan-masking, none of the pool session's reverb pathology.
   Open air (no reflections, low noise floor) looks like an easier
   regime than either the pool or a music-filled room. Mood tracked
   plausibly throughout: excited porch chatter early, flat/chill as the
   evening wound down, honest `None` in silence.
3. **`played_through` weak positives are banked by empty rooms.** Music
   kept playing while everyone sat outside (by design — silence is
   absence of evidence), and tracks that completed logged
   played_through "weak positive" lines with nobody present to veto:
   Thriller at 20:44:53, Just the Way You Are + Just In Time in the
   silent-living-room segment. **An empty room can't veto, so these are
   mislabeled approvals.** Before M5 learns from the override corpus,
   played_through lines need a presence gate (e.g. require speech
   evidence / low headcount staleness within the track's window) — or
   the corpus rewards whatever plays to an empty room.
4. **DJ mechanics round-tripped under real group use:** 6 manual picks
   honored (including genre pivots Hip-Hop→Jazz→Lofi as the group
   mellowed), 3 wrong_vibe vetoes each followed by a different-pool
   pick, boundary-only transitions, played_through only on real
   completions while occupied. The `guard/uncertain-regime` cell
   appeared at low headcount confidence — guards holding rather than
   guessing.

5. **Full volume revealed the gate's over-suppression limit — and a
   self-correcting cascade.** At 93% output the music read −22 dBFS at
   the mic, *louder than the participants' voices* (−25…−30 all night),
   and `speech_ratio` pinned to 0: the strict `vad_playback_threshold`
   that rejected sung vocals in part (d) phase 5 now rejected everyone.
   The cascade, all from the logs: blind system → guard recommendations
   → nothing selected/queued (last selection 22:06:07) → track completed
   into silence at 22:12:00 → **silence flipped `playback_active` off,
   the VAD reverted to its normal threshold, speech certified within
   ~30 s** (sr 0.33 by 22:12:28) → real recommendation fired →
   controller bootstrapped 'April Showers' at 22:13:13. No crash, no
   intervention — but at volumes where music out-shouts the room the
   system is a limit cycle (music blinds → starves → silence heals →
   repeats), not a DJ. Founder banked Wrong-call taps during the blind
   window (22:11:46, 22:12:01: stale 200+ s, sr 0, −22 dBFS) and Good
   taps on recovery — a clean before/after pair. **Operating envelope
   finding: voices must out-read music at the mic; ~60–70% output on
   this hardware.** After recovery, speech certified at 0.6–0.8 over
   playback for the rest of the segment.
6. **AC-on empty room: no phantom growth.** The deliberate AC phase the
   2026-07-06 notes asked for, opportunistically: 35 min of AC broadband
   + playback, nobody home. The one banked frame (22:59:06) shows the
   bucket holding stale (pair, 84 s), raw_clusters frozen, sr 0 — the
   contamination gate held against AC + music together. The 07-06 blip
   suspect (AC *onset* during active estimation) remains untested; this
   covers steady-state AC under playback.
7. **Participant-validated emotion accuracy.** All three participants —
   the first group to watch it live, and the first informal validation
   not done by the founder alone — reported being genuinely impressed by
   how accurately the valence/arousal readings tracked the room across
   the evening. The frame record agrees: excited through the animated
   porch conversation, chill/flat as the night wound down, a tense blip
   or two, and honest `None` whenever the room went quiet. Still
   informal (no per-frame verdicts on mood specifically, and the earlier
   phases-4/6 excitement confound stands), but the strongest
   multi-person endorsement of the emotion layer to date.
8. **The goodbye produced the night's most accurate group read.** Three
   people talking animatedly at 23:02 finally drove the bucket to 4 —
   the nearest bucket to a true 3 after an evening of solo/pair
   undercounts (suggesting simultaneous/overlapping speech, not just
   more speech, is what separates the clusters). When the friends left
   and calming classical took over, the gate certified nothing and the
   bucket froze at 4 with growing staleness — observed live by the
   founder as "still says 4 even though it's just me": correct hold
   semantics, informally the bookend of phase 1's held `pair`.

Session ended ~23:14 (dashboard stopped; Spotify kept playing the
close-out classical — the provider owning playback across shutdown, as
designed). `tuning_report.py` read the full corpus back cleanly: 113
annotations (104 good / 9 wrong) and 60 override records (4 skip,
10 wrong_vibe, 16 manual, 30 played_through) across all sessions, no
boundary adjustments suggested. Segment detail lives in
`data/annotations/2026-07-10.jsonl` and `data/overrides/2026-07-10.jsonl`
(timeline timestamps above segment the files cleanly).

## 2026-07-10 — M4 gate part (d) phases 2–6: contamination protocol (apartment, evening)

**Setup.** Two known participants (founder + 1 friend) for phases 2–3;
**correction, learned post-session: a third participant was present from
phase 4 onward** (arrival not caught on the phase clock; speech
participation in phases 4/6 unconfirmed — ground truth for those phases
is 2–3 speakers, not a clean pair). Same closed quiet apartment room as
phase 1, AC/fans off, mic input 34%. One continuous
dashboard run (started 19:40) with playback **enabled** but pointed at an
empty playlist mapping via `RTR_PLAYBACK_PLAYLISTS_PATH` — the controller
polls Spotify honestly (so `playback_active` tags evidence and the
stricter `vad_playback_threshold` engages) but every genre pool is
unmapped, so the DJ can never start or swap a track ("silence beats wrong
guess" doing measurement duty). Phase music was started by hand in
Spotify: instrumental = RTR · Lofi Beats · low, vocal = RTR · Pop · high,
volume set once at phase 3 and untouched through phase 6 (mic-side it
read −35…−45 dBFS — results are conditional on this moderate volume).
2-min quiet re-seed 19:41:57–19:43:57 preceded phase 2 (noise floor is a
60 s in-memory EMA; phase 1 ran in an earlier process — see entry below).
24 frames banked via taps.

Phase windows: 2 = 19:44:09–19:49:35 (solo half → both from 19:46:51);
3 = 19:49:35–19:55:00; 4 = 19:55:00–20:00:09; 5 = 20:00:09–20:05:33;
6 = 20:05:33–20:10:33.

Representative frames (full set in `data/annotations/2026-07-10.jsonl`):

| phase | frames | bucket range | raw | crowd_wt | frag | speech_ratio | dBFS | playback_active |
|---|---|---|---|---|---|---|---|---|
| 2a solo speech | 2 | solo→pair | 1 | 0.15–0.17 | 0–0.11 | 0.83–0.85 | −27 | false |
| 2b pair speech | 2 | pair→**8** | 3 | 0.26–0.27 | 0.23–0.32 | 0.95 | −25…−30 | false |
| 3 instr, silent | 6 | held 4 (stale 40→298 s) | frozen | 0 | frozen | **0.000–0.002** | −38…−45 | **true** |
| 4 instr + speech | 6 | solo/pair (one 4-blip) | 1–2 | 0–0.28 | 0.59–0.89 | 0.69–0.92 | −26…−35 | **true** |
| 5 vocal, silent | 4 | held pair (stale 30→298 s) | frozen | 0 | frozen | **0.000–0.003** | −36…−44 | **true** |
| 6 vocal + speech | 4 | solo/pair | 1–2 | 0–0.23 | 0.64–1.0 | 0.74–0.91 | −25…−31 | **true** |

**Findings.**

1. **The v1 contamination gate passed its hardest test.** Phase 5 — vocal
   music (Pop with sung vocals), silent room — certified essentially zero
   speech for 5 straight minutes (`speech_ratio` ≤ 0.003), so the
   headcount received no submissions (honest staleness growth to 298 s),
   the bucket held, and the mood went stale rather than absorbing the
   song. Zero phantom clusters, zero bucket creep. Per protocol, the
   `RTR_VAD_PLAYBACK_THRESHOLD=0.85` repeat was **not** run — no phantom
   growth to knock down. Same result in phase 3 (instrumental).
2. **Speech stayed certified under both music types.** Phases 4/6:
   speech_ratio 0.69–0.92 while music played, no phantom growth, and
   phase-6-vs-4 drift ≈ none (vocal penalty on headcount: not observed
   at this volume). `playback_active: true` on every music-phase frame.
   Caveat per the setup correction: true occupancy in 4/6 was 3, so if
   the third participant was talking, the solo/pair buckets are an
   **undercount** (consistent with the known similar-voices-merge
   trade-off at threshold 0.70) rather than a clean pair match — the
   no-phantom-growth conclusion stands either way, the accuracy claim
   doesn't.
3. **The night's real overcount came from the clean baseline.** Phase 2b —
   two animated people, NO music — hit bucket 8 (raw_clusters 3,
   crowd_weight 0.27, speech_ratio 0.95): the crowd/babble path engaging
   on excited pair conversation in a quiet room, a small-magnitude cousin
   of the pool session's pair→16. In a quiet apartment at moderate music
   volume, **the headcount's enemy is animated conversation, not
   playback**. The known crowd-regime misfire (separation_score=0 →
   sep_collapse=1) remains the suspect.
4. **Fragmentation runs hot under music + speech** (0.59–1.0 in phases
   4/6 vs ≤0.32 without music) — the proportional min-mass floor
   (`min_cluster_evidence_frac`) is what kept that debris from counting.
   Worth watching at higher music volumes.
5. **Mood contamination went unquantified tonight** — phases 4/6 read
   `excited` over genuinely excited conversation, so the song's emotion
   and the room's were confounded. The 2026-07-06 observation
   (deliberately mellow human read excited under hip-hop) remains the
   datapoint; a deliberately-flat-affect phase over vocal music is the
   missing measurement.
6. Solo speech (2a) blipped solo→pair (crowd_weight ~0.16), consistent
   with known solo behavior; mood tracked excited with one tense blip.

**M4 part (d): complete.** Measure-first baseline recorded; with (a)–(c)
green on 2026-07-06, the M4 gate is fully passed. Baseline numbers for
the M5 music-detection decision: at quiet-apartment volumes the VAD-side
gate already rejects vocals outright; M5's case must rest on louder
playback, valence contamination during speech, or harder rooms.

## 2026-07-10 — M4 gate part (d) phase 1: baseline quiet (apartment, evening)

**Setup.** Solo occupant (phases 2–6 with known participants run later
tonight), closed quiet apartment room, AC/fans off, mic input volume 34%,
dashboard fresh-started 19:25:23 with `RTR_PLAYBACK_ENABLED=0` for this
run — deliberate, so a bootstrap recommendation could not start music
during the no-playback phase (startup line confirmed shadow mode). Phase
window **19:27:23–19:32:23**; four frames banked via Good-call taps.

Protocol deviation, noted: phase 1 ran standalone ahead of phases 2–6
(separate engine run). The noise floor is an in-memory 60 s EMA
(`engine.py`, `Ema(noise_floor_tau_s)`) fed by quiescent windows, so it
does not persist across restarts — tonight's session will prepend ~2 min
of quiet before phase 2 to re-seed. Phase 1's own observations (hold /
staleness / no phantom growth) are restart-independent.

| time | bucket | stale (s) | raw_clusters | crowd_weight | speech_ratio | dBFS | playback_active | matched_cell |
|---|---|---|---|---|---|---|---|---|
| 19:28:56 | pair | 105.5 | 4 | 0 | 0 | −45.7 | false | guard/no-speech |
| 19:30:35 | pair | 205.5 | 4 | 0 | 0 | −44.3 | false | guard/no-speech |
| 19:31:36 | pair | 265.5 | 4 | 0 | 0 | −47.7 | false | guard/no-speech |
| 19:32:11 | pair | 299.5 | 4 | 0 | 0 | −54.4 | false | guard/no-speech |

**Result: expected phase 1 behavior on every axis.**

- **Bucket holds, staleness honest.** No headcount submissions in
  silence; staleness grew linearly 105→299 s while the bucket held.
  The last-window diagnostics (`raw_clusters` 4, `dispersion` 0.579,
  `fragmentation` 0.429, `smoothed_log2` 1.672) were frozen across all
  four frames — residue of the final pre-phase submission (~19:27:11,
  incidental pre-"go" audio), not live estimates. The held `pair` on a
  solo-occupant silent room is the designed "silence is absence of
  evidence" semantics, not an error.
- **VAD certified zero speech** (`speech_ratio` 0 on every frame) and the
  recommender stayed on the no-speech guard throughout — no
  recommendation fired in 5 min of silence.
- **Quiescent floor is low**: −44…−54 dBFS, far below the pool session's
  fan-inflated −10…−20 and below the prior quiet-room −28 reference.
  With `raw_ratio < 0.1` the entire phase, the 60 s-tau floor EMA was
  fully seeded well before phase end.
- **Debuggability gap (small, same family as the fixed `smoothed_log2`
  one):** the seeded `noise_floor_dbfs` isn't exposed in state or
  annotation frames, so "noise floor seeds" is inferred from quiescent
  loudness rather than read directly. Worth exposing before/during M5,
  since part (d)'s phases 3–6 lean on floor-relative terms.

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
