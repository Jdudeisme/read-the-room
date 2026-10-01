# Dominance ladder on JPad — run sheet

**Status.** Written 2026-09-30, not yet run. Human-run on JPad (the only
machine; the Mac was retired 2026-09-30), founder solo. It changes no
constant, threshold, ramp or default. Its output is a FIELD-NOTES entry and
one founder decision on the knots. Any knot change that follows is a
separate, signed-off diff, because the knots shape the valence/arousal
values written to frames and the corpus (CLAUDE.md, REQUIRES-REVIEW).

**The question.** Do `RTR_MUSIC_DOMINANCE_LO/HI` exist on this mic that put
speech alone at m ≈ 0 and speech over music at m ≥ `pull_m_floor` (0.25)?
If they do, what are they? If they don't, we need to know that before
fitting anything.

## Why this session

- **The knots in force are PROVISIONAL.** `.env` carries 0.022 / 0.050,
  fitted 2026-09-06 on one track (Taylor Swift, "Welcome To New York
  (Taylor's Version)"), read live off the dashboard in 40 s takes. Three
  speech-only controls disagreed at the tail (max 0.0198 / 0.0298 /
  0.0346), and `LO` sits inside that disagreement. The promotion bar
  (CLAUDE.md) is a ladder across **≥ 2 tracks and ≥ 3 speech-only
  controls**.
- **The defaults are now orphaned.** `config.py`'s 0.05 / 0.30 were
  measured on the retired Mac. They leave the pull estimator dead on JPad
  (2026-09-06 finding 4). This ladder decides what replaces them.
- **Song section matters more than volume** (2026-09-06 finding 2).
  Three music-only takes at one volume spread 0.039–0.066. So every take
  here covers a **full play of the track**, not a 40 s sample.
- **An offline check on 2026-09-30 says the controls are the crux.** Five
  speech-only captures from 2026-09-06 (same mic, quiet room, no music;
  `scripts/analyze_dominance_wav.py`, eligible windows):

  | capture (09-06) | n | p50 | p95 | max |
  |---|---|---|---|---|
  | A centre | 43 | 0.0281 | 0.0392 | 0.0407 |
  | B wall + corner | 43 | 0.0166 | 0.0318 | 0.0386 |
  | E centre, still | 43 | 0.0320 | 0.0419 | 0.0484 |
  | F centre, natural speech + movement | 43 | 0.0300 | 0.0508 | 0.0615 |
  | G centre, natural, 4 min | 118 | 0.0304 | 0.0492 | 0.0613 |

  The same day's live ladder controls maxed at 0.020–0.035. Speech alone
  here reaches **0.061**, above the provisional `HI`. Scored as if music
  were playing, 47–65 % of these pure-speech windows would bank pull
  samples under the knots in force. The ladder controls and these
  captures differ in ways not yet separated: talking distance, speaking
  style, level (−38.7 dBFS live vs −31 to −35 here), and whatever made
  the morning and evening readings disagree on 09-06 (finding 5, still
  open). This session controls those variables on purpose.

## Fixed conditions — record before the first take, then don't touch

Fill this table in; it goes into the FIELD-NOTES entry verbatim.

| setting | where | value |
|---|---|---|
| Mic "Audio enhancements" | Settings → System → Sound → Microphone Array → Properties | |
| Voice Clarity / Studio Effects (if listed) | same page, and Quick Settings | |
| Speaker enhancements / Dolby | Settings → Sound → Speakers → Properties; Lenovo Vantage / Dolby app if installed | |
| Lenovo Smart Audio / Vantage audio mode | Lenovo Vantage | |
| Spotify "Normalize volume" + level | Spotify → Settings → Audio quality | |
| Spotify Equalizer | Spotify → Settings → Playback | |
| Spotify volume | Spotify app | 100 % |
| Repeat-one | Spotify | **on** (2026-09-06: Spotify otherwise moves on to the next track) |
| Power | plugged in? Windows power mode | |
| Laptop position | | centre of the room, lid at the usual angle |
| Talk position | tape marks on the floor | **N** = 0.6 m in front, **F** = 1.5 m |

Also:
- **The dashboard must not be running.** On 2026-09-06, two streams on
  this array at once gave different embedding spread (addendum 1, item 5).
  Check that no process is serving it:
  `Get-CimInstance Win32_Process | Where-Object CommandLine -match 'dashboard' | Select-Object ProcessId, CommandLine`.
  Stop any you find with `Stop-Process -Id <pid>`. `pkill` from Git Bash
  does nothing on Windows.
- **Capture device** comes from `.env` (`RTR_INPUT_DEVICE=Microphone
  Array on SoundWire D`). Confirm that the first line the capture script
  prints names it.
- **Nobody else talks, and there are no notifications.** Turn on Do Not
  Disturb.

## Tracks

| id | track | why | Spotify URI |
|---|---|---|---|
| T1 | "Welcome To New York (Taylor's Version)" | continuity with 2026-09-06 | `spotify:track:1hR8BSuEqPCCZfv93zzzz9` |
| T2 | founder's pick: **mellow** (acoustic, soft top end) | the low-high-band end, where mix is most likely absorbed as clean | |
| T3 | founder's pick: **bright** (dense hi-hats, EDM/hip-hop) | the high end; checks `HI` saturation | |

At least two tracks are required; T1 + T2 is the minimum. T3 is strongly
recommended.

## Takes

Use the venv interpreter for every command. Each take is one capture
command. `DATE` is the session date (`20261001` style). Names must be
unique, because the capture script overwrites files of the same name.

```powershell
.venv\Scripts\python.exe scripts\capture_room_wav.py --seconds <S> --name ladder-DATE-<ID> --note "<note>"
```

- **Speech, all takes:** read aloud continuously from the same book or
  article at a natural level, unless the take says otherwise.
- **Mix and music:** start the track from 0:00 with repeat-one on, then
  start the capture. `<S>` = the track's length plus 10 s, at least 180.
- **Notes:** say track, Windows volume, Spotify volume, position and
  style, e.g. `"T1 WTNY, Win 76%, Spotify 100%, mix, reading at N"`.

| # | ID | kind | track | Windows vol | talk | seconds | note |
|---|---|---|---|---|---|---|---|
| 1 | C1 | speech | — | — | N, reading | 180 | control: the reference |
| 2 | T1-MO76 | music | T1 | 76 % | silent | full track | |
| 3 | T1-MX56 | mix | T1 | 56 % | N, reading | full track | |
| 4 | T1-MX76 | mix | T1 | 76 % | N, reading | full track | |
| 5 | C2 | speech | — | — | **F**, reading | 180 | control: distance |
| 6 | T2-MO76 | music | T2 | 76 % | silent | full track | |
| 7 | T2-MX56 | mix | T2 | 56 % | N, reading | full track | |
| 8 | T2-MX76 | mix | T2 | 76 % | N, reading | full track | |
| 9 | C3 | speech | — | — | N, **animated conversation** (not reading; laugh, vary pitch) | 180 | control: style |
| 10 | T3-MO76 | music | T3 | 76 % | silent | full track | |
| 11 | T3-MX56 | mix | T3 | 56 % | N, reading | full track | |
| 12 | T3-MX76 | mix | T3 | 76 % | N, reading | full track | |
| 13 | C4 | speech | — | — | N, reading | 180 | control: repeat of C1 (drift) |

That's about 45–55 minutes of capture. C1 and C4 bracket the session, so
drift shows up as a C1/C4 difference rather than as a knot. Optional 40 %
mix takes (`Tn-MX40`) can go after each track's 76 % take if time allows.
That is the absorption regime, the low-volume tail that 2026-09-06 found
still landing under `m_max`.

**Optional Block X (only after take 13): the morning/evening discrepancy.**
Run 90 s of T1 music-only at 76 %, **with mic Audio enhancements toggled
to the other state** (ID `X-T1-MO76-enh<on|off>`). Then restore the
setting and record that you did. This tests the leading candidate for
2026-09-06 finding 5 directly. A big shift means the enhancement state is
part of the capture path and belongs in every calibration record.

## Analysis (offline, any time after)

```powershell
$D = "data/captures/ladder-DATE"
.venv\Scripts\python.exe scripts\analyze_dominance_wav.py `
  speech:C1="$D-C1.wav" speech:C2="$D-C2.wav" speech:C3="$D-C3.wav" speech:C4="$D-C4.wav" `
  music:T1-MO76="$D-T1-MO76.wav" mix:T1-MX56="$D-T1-MX56.wav" mix:T1-MX76="$D-T1-MX76.wav" `
  music:T2-MO76="$D-T2-MO76.wav" mix:T2-MX56="$D-T2-MX56.wav" mix:T2-MX76="$D-T2-MX76.wav" `
  music:T3-MO76="$D-T3-MO76.wav" mix:T3-MX56="$D-T3-MX56.wav" mix:T3-MX76="$D-T3-MX76.wav" `
  --listening 76 --json "$D-result.json"
```

It mirrors the engine: 5 s windows every 2 s, streaming Silero at the
playback certification threshold, the same `dsp.analyze` and
`dominance()` functions, and only eligible windows (speech ratio ≥ 0.2)
are scored. It scores the `config.py` defaults, the knots in force, and
the draft rule's knots, plus any `--knots LO,HI` you add. For each take
it reports:
- **clean**: the fraction of windows at m ≤ `m_max`, which the engine
  treats as clean speech for the baseline;
- **bankable**: the fraction at m ≥ `pull_m_floor`, which bank a pull
  sample.

## Decision rule — DRAFT, founder edits and signs before the session

The two tolerances below (0.05 and 0.10) are **proposals**. Change them
before the session if you want different ones, then sign. They must not
change after the data is in.

Inputs, from the analyzer:
- **LO\*** = the worst control's p95. The `rule inputs` line prints it,
  taken over C1–C4.
- **HI\*** = the pooled p50 of the 76 % mix windows across all tracks.

Score the pair (LO\*, HI\*) with `--knots LO*,HI*`. Then:

- **A — separable.** HI\* > LO\*, **every** control has bankable ≤ 0.05,
  and **every** `MX76` take has clean ≤ 0.10. → Propose (LO\*, HI\*) as
  the new knots. This is the second ladder on this machine, so it meets
  CLAUDE.md's "measured twice" bar. Promoting them to `config.py`
  defaults (replacing the Mac's) is a REQUIRES-REVIEW diff with this
  entry as its evidence. `.env` then drops its override.
- **B — separable at listening volume only.** As A, but some `MX56` take
  has clean > 0.10. → Same proposal, plus a written operating envelope
  ("music-aware correction valid at ≥ 76 %; quieter music is partly
  absorbed as clean"). Whether that's acceptable is a founder decision
  recorded with it.
- **C — not separable.** HI\* ≤ LO\*, **or** no pair scored meets the
  control condition (bankable ≤ 0.05) while keeping `MX76` clean
  ≤ 0.10. → **Change no knots.** On this mic, high-band *share* cannot
  separate speech from speech over music, as 2026-09-06 finding 1
  predicted structurally (speech dilutes the ratio). File a REQUIRES-REVIEW
  roadmap item to replace the proxy, e.g. absolute high-band energy, or
  high-band energy relative to the noise floor. Separately, decide whether
  `RTR_MUSIC_AWARE_ENABLED` stays on meanwhile, since the knots in force
  would then be known to bank speech as pull.

C1 vs C4 is reported either way. If C4's p95 differs from C1's by more
than the spread among C1–C3, record the session as drifting. The verdict
then stands only if it holds with C4 excluded **and** with C1 excluded.

Signed: ____________ Date: ________

## Afterwards

- Write the FIELD-NOTES entry: the fixed-conditions table, the analyzer
  output (paste the `--json` summary), the outcome letter with the rule
  worked line by line, and Block X if it was run.
- Keep the WAVs until the decision is made and any knot diff has merged.
  They are the evidence, and re-analysis is free. Then delete them
  (`capture_room_wav.py` privacy note): `data/captures/` is local and
  never committed.
- Nothing in this session touches `.env`, `config.py`, or the signature
  files.
