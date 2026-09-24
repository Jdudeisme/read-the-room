# reSpeaker XVF3800: stock baseline, device setup, and the control protocol

**Status:** stock baseline measured 2026-09-13 (night), reference machine
(`JPad`). Device configuration **unchanged** — everything below was read, not
written. The four-leg control session **ran 2026-09-23** (19:44–20:12); its
results and attribution are FIELD-NOTES 2026-09-23, which supersedes §2's
readings of Findings A and B where they differ. §3's setup script was
corrected the same day. **Written:** 2026-09-13.

**Scope discipline.** This document changes no constant, threshold, ramp, or
default, and proposes none. It records what the hardware is, what it measured
at stock settings, how to configure it, and what to measure next. Acceptance
below is a measurement protocol, not a target number (repo convention, see
ROADMAP.md).

> **One-paragraph summary.** The XVF3800 is a good microphone for Read the
> Room: it delivers the cleanest VAD certification this project has measured
> (0.60 median speech ratio, 0.87 peak) at a healthy level with no clipping,
> and it needs no code change — `MicSource`'s `indata[:, 0]` already reads the
> correct channel. Two things it does **not** fix and one it makes worse: its
> AGC flattens loudness dynamics to 3.7 dB of spread, which is a live suspect
> for depressed arousal readings; and a solo speaker ratcheted to bucket `4`
> in under three minutes, further than the laptop mic's documented solo→pair
> split. Both are device-configuration questions before they are RTR
> questions.

---

## 1. The hardware, as this machine sees it

`Get-PnpDevice` on the reference machine, 2026-09-13:

| Interface | InstanceId | Purpose |
|---|---|---|
| `reSpeaker XVF3800 4-Mic Array` | `USB\VID_2886&PID_001A&MI_00` | Audio (UAC) |
| `reSpeaker Control` | `USB\VID_2886&PID_001A&MI_03` | **Runtime control — this is the tuning interface** |
| `reSpeaker DFU Factory` | `USB\VID_2886&PID_001A&MI_04` | **Firmware flashing — do not touch** |

`VID_2886` is Seeed Technology; `PID_001A` is the XVF3800. Firmware reports
`VERSION 2 0 6`.

### Audio endpoints and which one to use

The array enumerates on four host APIs. `--list-devices` indices are **not
stable across reboots or replugs** — re-check them at the start of every
session rather than trusting the numbers below.

| Idx (2026-09-13) | Host API | Rate | Notes |
|---|---|---|---|
| 8 | DirectSound | 44.1 kHz | |
| 14 | WASAPI | 48 kHz | Resamples 48→16k via the project's `Resampler` |
| 25 | WDM-KS | 16 kHz | **Native analysis rate — no resampling.** Used for the 2026-09-13 baseline |

Device 25 is the cleanest signal path (RTR analyses at 16 kHz, so nothing
resamples). WDM-KS takes the device exclusively; if it fails to open, another
application holds the mic.

### Channel layout — resolved, no code change needed

The array presents 2 channels. `MicSource` hardcodes `indata[:, 0]`
(`src/sensing/audio.py`), so the question of which channel carries the
processed output mattered. Measured, 12 s of continuous speech, device 25:

| | RMS | Peak | Noise floor | Speech-above-floor |
|---|---|---|---|---|
| **ch0** | −23.7 dBFS | 0.80 | −45.4 dBFS | **21.7 dB** |
| ch1 | −26.0 dBFS | 0.40 | −38.2 dBFS | 12.2 dB |

**ch0 is the processed/beamformed output and is the correct channel.**
`MicSource` is already right; `audio.py` needs no change.

> **Trap for the next person.** In a *silent* room ch1 reads 15–20 dB hotter
> than ch0, which looks like ch0 is dead. It is not — that is ch1's higher
> noise floor with no speech to compare against. Only a talking test
> separates them. Do not "fix" the channel index on a silence measurement.

---

## 2. Stock baseline — 2026-09-13, solo, shadow mode

**Conditions.** Reference machine (`JPad`), device 25 (WDM-KS, 16 kHz native),
one speaker (founder) alone in a quiet room, continuous natural speech,
`RTR_PLAYBACK_ENABLED=0` (forced via shell env var; `.env` has it at 1).
Device at **stock settings** — nothing written. 81 state frames over 2.7 min,
read from the dashboard websocket. Config mirrors `.env` as committed, which
carries the PROVISIONAL dominance knots (`LO=0.022`, `HI=0.050`); playback was
off so the M6 correction path was not exercised.

### What is good

| Signal | Reading |
|---|---|
| Loudness | −23.1 dBFS median (min −40.6, max −19.4) |
| Speech ratio | **0.60 median, 0.87 peak** |
| Emotion layer | `ready` 79/81 frames, confidence 0.65 median |
| Noise floor | −32.0 dBFS (settled) |

Speech certification is the headline. This is the cleanest certification the
project has measured, and it is the thing RTR most needs from a microphone —
the array's beamforming and echo cancellation are doing real work.

### Finding A — loudness dynamics are flattened (AGC)

Across 71 frames with `speech_ratio >= 0.3`:

```
p5  = -25.70    p50 = -23.00    p90 = -21.20
p10 = -24.90    p75 = -22.30    p95 = -20.65
p25 = -23.85

p90 - p10 spread = 3.70 dB      stdev = 1.51 dB
corr(loudness, arousal) = -0.328
```

3.70 dB of spread across ~3 minutes of animated speech is very tight, and
loudness carries no useful arousal information (the correlation is weakly
*negative*).

**The device confirms it independently.** Read from `MI_03` at stock settings:

```
PP_AGCONOFF         1          <- AGC active and adapting
PP_AGCGAIN          1.985468   <- adapted; Seeed's product default is 2.0
PP_AGCMAXGAIN       64
PP_AGCDESIREDLEVEL  0.0045
```

Read as a power target, `10*log10(0.0045) = -23.47 dBFS`. RTR measured median
loudness **−23.00 dBFS**. The AGC is holding the talker to within half a dB of
its programmed target, from two independent measurements.

> **Caveat on that arithmetic.** XMOS documents `PP_AGCDESIREDLEVEL` as
> "target output power" without stating units. The power reading
> (`10*log10`) lands on −23.47 dBFS; an amplitude reading (`20*log10`) would
> give −46.9 dBFS, which is nowhere near anything measured. The power
> interpretation is inferred from the match, not from the documentation.
> Treat the *mechanism* as confirmed and the *exact figure* as inferred.

**Why this matters to RTR.** AGC exists to hold a talker at constant level for
the far end of a call — the correct behaviour for a speakerphone and the
opposite of what an arousal estimator wants, since vocal effort is one of the
cues arousal rides on. The founder's live observation on 2026-09-13 was that
the room read flatter than it felt; this is a measured mechanism for that.

**What this does not establish.** Whether the AGC is the *whole* story. The
audeering model reads conservatively in general, and there was no built-in-mic
control leg on 2026-09-13. Across the session, **zero of 71 frames** crossed
the mapper's `+0.25` "high" cutoff on either axis:

```
valence  p5 -0.617  p50 -0.148  p95 -0.014  max 0.109  stdev 0.174
arousal  p5 -0.378  p50 -0.138  p95  0.046  max 0.093  stdev 0.127
quadrant cells: (mid,mid) 51  (low,low) 10  (low,mid) 6  (mid,low) 4
```

Attribution between mic and model is exactly what leg A of the protocol
(§4) exists to settle. Do not move a mapping cutoff on the strength of this
entry.

### Finding B — a solo speaker ratchets to bucket `4`

One person, alone, the bucket climbed monotonically and had not stabilised
when the session ended:

```
t+16s   solo   raw_clusters=1   log2=0.00   disp=0.000   speech=0.36
t+40s   pair   raw_clusters=2   log2=0.70   disp=0.530   speech=0.60
t+112s  pair   raw_clusters=3   log2=1.75   disp=0.585   speech=0.81
t+120s  4      raw_clusters=2   log2=1.86   disp=0.570   speech=0.82
t+160s  4      raw_clusters=4   log2=1.95   disp=0.576   speech=0.57   <- still climbing
```

Bucket distribution: `pair` 41 / `4` 21 / `solo` 12 / `None` 7.
`headcount_dispersion` settled at **0.569–0.606**. `crowd_weight` was ~0.001,
so this is the real-count path, not crowd inflation. Final recommendation:
`"tense room, ~4 people → Jazz; raise energy"` — driven by the wrong count.

**Relation to the known laptop-mic split.** This is *not* a new phenomenon,
and the doc that already covers it is
`docs/HEADCOUNT-SOLO-SPLIT-PROTOCOL.md`. FIELD-NOTES 2026-09-06 records solo
reading `pair` on the laptop mic at dispersion 0.551/0.588, and
`headcount.py`'s min-mass comment records same-speaker scatter at ~0.35 clean
audio / ~0.6 laptop mic. **The XVF3800's 0.569–0.606 sits in that same
regime — the array did not escape it.** What differs is how far the bucket
travelled: the laptop mic's documented failure is solo→pair oscillating
around the truth; here it ratcheted to `4` and tracked speech ratio upward,
which is the wrong direction for accumulating evidence.

That makes candidate 2 of the solo-split protocol — **"the capture path's own
processing"** — materially stronger. We now have a device that *reports* its
own adaptive gain stage is running.

---

## 3. Configuring the device

### The utility

Seeed ships prebuilt binaries in
[`respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY`](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY)
under `host_control/win32/` (also `linux_x86_64`, `mac_arm64`, `rpi_64bit`,
`jetson`). Three files, all required, kept together in one directory:

| File | Bytes | SHA-256 (fetched 2026-09-13) |
|---|---|---|
| `xvf_host.exe` | 124416 | `948b45a0d5e2c6694bc0abfa55a9a18dce8845b761782b4866faab5711279c5d` |
| `command_map.dll` | 226304 | `96bc9a536032fe72dc29c5c7e069660644d77fbed7a4476c27819549ccdab29f` |
| `device_usb.dll` | 112640 | `c48d7ee97c8912ee3f9c8e9a2637b8224166c47c137152d295bfbfc5a651b5c1` |

Raw URL base:
`https://raw.githubusercontent.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/master/host_control/win32/`

Syntax: `xvf_host.exe COMMAND [value]` — no value reads, a value writes.
Transport defaults to `usb` (`--use i2c|spi|usb` to override); it finds the
device on interface 3, matching `MI_03` above.

### Persistence semantics — read before writing anything

- **`xvf_host` writes are runtime-only.** They revert when the device loses
  power. This makes experimentation safe: unplug to restore stock.
- **`SAVE_CONFIGURATION` writes to flash**, and the XVF3800 has **no
  documented factory reset** — restoring defaults means rebuilding and
  reflashing firmware from the YAML defaults in
  `sources/app_xvf3800/autogeneration/yaml_files/settings_and_defaults/`.
- **Therefore: do not run `SAVE_CONFIGURATION`** as part of any protocol here.
  It is the one hard-to-undo action on this device, and per-session runtime
  setup achieves the same result reversibly. If it is ever run, that is a
  deliberate founder decision that belongs in FIELD-NOTES.
- XMOS also warns against changing the AGC **time constants**: speeding them
  up causes level fluctuation during speech.

### The AGC parameters

| Parameter | Stock (2026-09-13) | Meaning |
|---|---|---|
| `PP_AGCONOFF` | `1` | Whether gain is permitted to *adapt*. `0` freezes it |
| `PP_AGCGAIN` | `1.985468` | The multiplicative gain, **always applied regardless of `PP_AGCONOFF`** |
| `PP_AGCMAXGAIN` | `64` | Ceiling on adaptation (Seeed pre-tuned) |
| `PP_AGCDESIREDLEVEL` | `0.0045` | Target output power the AGC drives toward |

> **The subtlety that makes runs comparable.** `PP_AGCGAIN` is applied whether
> or not adaptation is on. Setting `PP_AGCONOFF 0` *alone* freezes the gain at
> whatever value it had drifted to at that instant — different every session,
> so legs are not comparable. **Always pin `PP_AGCGAIN` explicitly, and do it
> *after* disabling adaptation**, then read it back. 2.0 is chosen because it
> is Seeed's product default and within 0.06 dB of the stock adapted value on
> 2026-09-13, so it preserves the level the baseline was measured at.
>
> *Corrected 2026-09-23.* This box originally said to set the gain *first*,
> then disable adaptation. Measured on the device, that order leaves
> adaptation live between the two writes and it nudges the gain: 2.0 written,
> **2.012035** read back. Writing 2.0 again once adaptation was off held
> exactly (read back 2, and still 2 at the end of a 5-minute leg). See
> FIELD-NOTES 2026-09-23, finding 6.

### Session setup script

Runtime-only, so run it after every plug-in or reboot, before starting the
dashboard. Save alongside the three binaries as `rtr-mic-setup.sh`. (Promoting
this to `scripts/` is a separate call — it is reproduced here so the procedure
survives independent of any working directory.)

```bash
#!/usr/bin/env bash
# Put the reSpeaker XVF3800 into "measurement mode" for Read the Room.
# Runtime-only: reverts on power cycle. SAVE_CONFIGURATION deliberately not
# called - see the persistence semantics in
# docs/XVF3800-BASELINE-AND-SETUP-PROTOCOL.md before adding it.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
XVF=./xvf_host.exe
[ -x "$XVF" ] || { echo "xvf_host.exe not found next to this script" >&2; exit 1; }
q() { "$XVF" "$@" 2>&1 | grep -v 'device_init' | sed '/^$/d'; }

echo "=== XVF3800 current state ==="
q VERSION
for p in PP_AGCONOFF PP_AGCGAIN PP_AGCMAXGAIN PP_AGCDESIREDLEVEL; do q "$p"; done

if [ "${1:-}" = "--read-only" ]; then echo; echo "(read-only; nothing written)"; exit 0; fi

echo; echo "=== applying measurement mode ==="
q PP_AGCONOFF 0       # stop adaptation FIRST - otherwise it nudges the gain
                      # written below (2.0 -> 2.012 measured, 2026-09-23)
q PP_AGCGAIN 2.0      # then pin; gain applies regardless of ONOFF

echo; echo "=== read back ==="
for p in PP_AGCONOFF PP_AGCGAIN PP_AGCMAXGAIN PP_AGCDESIREDLEVEL; do q "$p"; done

# Fail loudly rather than let a session run under an unknown device state.
onoff=$(q PP_AGCONOFF | awk '{print $2}')
gain=$(q PP_AGCGAIN | awk '{print $2}')
if [ "$onoff" != "0" ] || ! awk -v g="$gain" 'BEGIN { exit !(g == 2.0) }'; then
    echo "MEASUREMENT MODE NOT APPLIED (PP_AGCONOFF=$onoff PP_AGCGAIN=$gain)" >&2
    exit 1
fi
echo; echo "OK: AGC frozen at gain 2.0."
echo "Runtime-only: power-cycle restores stock. Nothing written to flash."
```

**Always record which device state a session ran under** in its FIELD-NOTES
entry — "stock AGC" or "AGC frozen at gain 2.0". A session whose device state
is unknown is not comparable to either.

---

## 4. The control protocol (run 2026-09-23 — see FIELD-NOTES)

**Question.** Which of the 2026-09-13 findings are caused by the XVF3800's
processing, which by its endpoint, and which are baseline RTR behaviour
independent of microphone?

**Execution.** Human-run on the reference machine, founder solo, one session,
one room, one sitting. Changes no constants; output is a FIELD-NOTES entry.

### Legs

Same room, same speaker, same rough script, back to back. Each leg: relaunch
the dashboard on the named device, talk continuously for **at least 4
minutes** (the solo-split protocol's first run established that 90 s barely
reaches buffer saturation, which is where the interesting behaviour lives).

| Leg | Device state | Isolates |
|---|---|---|
| **A** | Built-in array (`--device 1`), stock | **Baseline.** Is any of this XVF3800-specific at all? |
| **B** | XVF3800 48 kHz WASAPI, stock AGC | Endpoint / processing-profile differences vs leg C |
| **C** | XVF3800 16 kHz WDM-KS, stock AGC | Reproduces the 2026-09-13 baseline |
| **D** | XVF3800 16 kHz WDM-KS, **AGC frozen** (`rtr-mic-setup.sh`) | The AGC's contribution, against leg C |

Re-check device indices before starting; they move.

### What to measure per leg

1. **Loudness dynamics** — p10, p50, p90 and the p90−p10 spread over frames
   with `speech_ratio >= 0.3`. The 2026-09-13 stock figure is 3.70 dB.
2. **Bucket trajectory** — the `headcount_bucket` series over time, plus
   `raw_clusters`, `headcount_smoothed_log2`, `crowd_weight`. Distinguish
   *oscillating around solo* from *ratcheting upward*.
3. **`headcount_dispersion`** — the settled range. Stock XVF3800 was
   0.569–0.606; laptop mic is recorded at 0.551/0.588; the cluster cut is
   0.70.
4. **Valence/arousal spread** — p5/p50/p95 and how many frames clear the
   mapper's ±0.25 band cutoffs. Stock XVF3800 was 0 of 71 on both axes.
5. **Speech ratio** — median and peak, to confirm the certification advantage
   survives whatever else changes.

### Acceptance

The protocol passes when all four legs are captured under recorded device
state and the five measurements above are tabulated per leg in a FIELD-NOTES
entry. **There is no target number.** The protocol's job is to attribute the
2026-09-13 findings to mic / endpoint / AGC / RTR-baseline, not to hit a
value.

### What each comparison answers

- **A vs C** — is the ratcheting XVF3800-specific, or does RTR do this to a
  solo speaker on any mic on this machine? If leg A also ratchets to `4`, the
  finding is about RTR and `HEADCOUNT-SOLO-SPLIT-PROTOCOL.md` owns it.
- **B vs C** — does the 16 kHz WDM-KS endpoint run a different processing
  profile than the 48 kHz WASAPI one?
- **C vs D** — does freezing the AGC widen the loudness spread, and does the
  valence/arousal range open up with it? This is the direct test of Finding A.
- **C vs D on dispersion** — a *bonus* test of Finding B: a continuously
  varying gain is a plausible contributor to same-voice ECAPA drift, so AGC-off
  may move dispersion too. If it does, Findings A and B share one cause.

### Only after this

If the array still inflates same-voice dispersion with AGC frozen, *then* a
calibration conversation starts — with its own protocol, a FIELD-NOTES entry,
and a live re-gate, per the prime directive. Nothing in the 2026-09-13 session
justifies moving `RTR_HEADCOUNT_CLUSTER_THRESHOLD` or any mapping cutoff: it is
one speaker, one room, one machine, one sitting, with no control leg.

---

## 5. Open questions

1. **Attribution of flat affect** — how much is AGC, how much is the audeering
   model's conservatism? Legs A and D together.
2. **Does AGC-off cost certification?** The 0.60/0.87 speech ratio is the
   array's biggest win. If freezing the gain degrades it, that is a real
   tradeoff and the protocol should surface it (measurement 5).
3. **Envelope advisory margin.** The 2026-09-13 session sat at −23.1 dBFS with
   the noise floor at −32.0 — about 9 dB of separation, against
   `RTR_PLAYBACK_ADVISORY_DB_OVER_FLOOR=10.0`. Playback was off so this was
   never exercised, but the margin is thinner on this mic than the advisory was
   tuned for. Watch it on the first XVF3800 playback session; do not pre-emptively
   move the constant.
4. **Does the array change the M6 picture?** The dominance knots in `.env`
   (`LO=0.022`, `HI=0.050`) are PROVISIONAL, fitted on one track on the
   built-in mic. A different capture path plausibly moves the high-band
   spectral share the ramp keys on. Unmeasured; do not assume they transfer.
5. **Beam steering.** The leading hypothesis for same-voice embedding drift is
   that the array re-steers as the speaker moves, changing the effective
   channel. `xvf_host` exposes beam-related parameters not explored on
   2026-09-13. If leg D leaves dispersion high, that is the next place to look.

## 6. References

- [reSpeaker XVF3800 host_control README](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/blob/master/host_control/README.md)
- [XMOS XVF3800 — Tuning the Application](https://www.xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/user_guide/04_tuning_the_application.html)
- [XMOS XVF3800 — Using the Host Application](https://www.xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/user_guide/03_using_the_host_application.html)
- [XMOS XVF3800 — Control Commands appendix](https://www.xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/user_guide/AA_control_command_appendix.html)
- `docs/HEADCOUNT-SOLO-SPLIT-PROTOCOL.md` — the prior owner of the solo→pair
  question; candidate 2 (capture-path processing) is strengthened by §2's
  Finding B.
- `docs/MACHINE-DOCTRINE-REVISION.md` — why the reference machine is `JPad`.
