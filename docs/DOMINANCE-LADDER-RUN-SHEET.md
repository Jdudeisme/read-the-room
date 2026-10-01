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
| Mic input volume | Settings → System → Sound → Microphone Array → Input volume | 34 % (as in M7 part (f), 2026-09-30) |
| Spatial sound on output | Settings → System → Sound → Speakers → Spatial sound | **Dolby Atmos for Speakers: ON** (see below) |
| Dolby mode / EQ (if the Dolby Access app shows one) | Dolby Access app | |
| Other speaker enhancements | Settings → Sound → Speakers → Properties | |
| Lenovo Smart Audio / Vantage audio mode | Lenovo Vantage | |
| Spotify "Normalize volume" + level | Spotify → Settings → Audio quality | |
| Spotify Equalizer | Spotify → Settings → Playback | |
| Spotify volume | Spotify app | 100 % |
| Repeat-one | Spotify | **on** (2026-09-06: Spotify otherwise moves on to the next track) |
| Power | plugged in? Windows power mode | |
| Laptop position | | centre of the room, lid at the usual angle |
| Talk position | tape marks on the floor | **N** = 0.6 m in front, **F** = 1.5 m |

**Why Dolby Atmos stays on.** The founder reports it has been applied to
the output in every test and whenever music plays from this laptop. It
was never recorded before 2026-09-30, so it's an uncontrolled part of all
JPad evidence: the 09-06 ladder, the 54 banked track signatures, and M7
part (f). Atmos processes the speaker signal (virtualisation, EQ, and
probably the compression 09-06 finding 2 observed). It therefore changes
the spectrum the mic hears, and with it the high-band share. Calibrate in
the condition RTR actually runs in. Turning it off would make this
ladder's knots, and every signature banked so far, belong to different
capture paths. Its effect is measured as one isolated variable in Block X.
If you later decide RTR should run with Atmos off, that is a new capture
path, and the knots and signature file get re-measured for it.

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

## Volume steps

Windows output volume is the lever; Spotify stays at 100 %. The steps
follow how RTR is meant to be used, not the 09-06 ladder's 56/76:

- **32 %: the small-room operating level** (1–5 people within arm's
  length; the level M7 part (f) ran at, kept deliberately). This is the
  level that matters most, and the one never measured for dominance.
- **76 %: the large-room level** (10–15+ people). Here music unambiguously
  dominates the mic, so it anchors `HI`. It's also the level the
  provisional knots came from.
- **56 %** is optional, in between.

What the mic hears at 32 % is unmeasured. For scale: on 2026-09-24 the
built-in array heard music at −46.1 dBFS at 16 % and −25.5 dBFS at 75 %.
The music-only takes below measure it. Expect speech to dominate the high
band at 32 %. Speech-over-music at that level may be hard to tell from
speech alone, which is what the decision rule's outcome B is for.

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
- **Notes:** say track, Windows volume, Spotify volume, Atmos state,
  position and style, e.g.
  `"T1 WTNY, Win 32%, Spotify 100%, Atmos on, mix, reading at N"`.

| # | ID | kind | track | Windows vol | talk | seconds | note |
|---|---|---|---|---|---|---|---|
| 1 | C1 | speech | — | — | N, reading | 180 | control: the reference |
| 2 | T1-MO32 | music | T1 | 32 % | silent | full track | mic-side music level at the operating volume |
| 3 | T1-MX32 | mix | T1 | 32 % | N, reading | full track | **the operating condition** |
| 4 | T1-MX76 | mix | T1 | 76 % | N, reading | full track | large-room; anchors HI |
| 5 | C2 | speech | — | — | **F**, reading | 180 | control: distance |
| 6 | T2-MO32 | music | T2 | 32 % | silent | full track | |
| 7 | T2-MX32 | mix | T2 | 32 % | N, reading | full track | |
| 8 | T2-MX76 | mix | T2 | 76 % | N, reading | full track | |
| 9 | C3 | speech | — | — | N, **animated conversation** (not reading; laugh, vary pitch) | 180 | control: style |
| 10 | T3-MO32 | music | T3 | 32 % | silent | full track | |
| 11 | T3-MX32 | mix | T3 | 32 % | N, reading | full track | |
| 12 | T3-MX76 | mix | T3 | 76 % | N, reading | full track | |
| 13 | C4 | speech | — | — | N, reading | 180 | control: repeat of C1 (drift) |

That's about 45–55 minutes of capture. 76 % is loud for a small room, so
keep those takes together per track and short breaks between them are
fine. C1 and C4 bracket the session, so drift shows up as a C1/C4
difference rather than as a knot. Optional 56 % mix takes (`Tn-MX56`) fill
in the middle if time allows.

**Optional Block X (only after take 13): one variable at a time.**
Each is 90 s of T1 music-only at 32 %, then put the setting back and
record that you did.
- `X-T1-MO32-atmosoff`: **Dolby Atmos off**. Measures how much Atmos
  moves the high-band share at the mic, i.e. whether it's a calibration
  variable.
- `X-T1-MO32-enh<on|off>`: **mic Audio enhancements** toggled to the
  other state. This tests the leading candidate for 2026-09-06 finding 5
  (the unexplained morning/evening discrepancy).

A big shift from either means that setting is part of the capture path
and belongs in every calibration record.

## Analysis (offline, any time after)

```powershell
$D = "data/captures/ladder-DATE"
.venv\Scripts\python.exe scripts\analyze_dominance_wav.py `
  speech:C1="$D-C1.wav" speech:C2="$D-C2.wav" speech:C3="$D-C3.wav" speech:C4="$D-C4.wav" `
  music:T1-MO32="$D-T1-MO32.wav" mix:T1-MX32="$D-T1-MX32.wav" mix:T1-MX76="$D-T1-MX76.wav" `
  music:T2-MO32="$D-T2-MO32.wav" mix:T2-MX32="$D-T2-MX32.wav" mix:T2-MX76="$D-T2-MX76.wav" `
  music:T3-MO32="$D-T3-MO32.wav" mix:T3-MX32="$D-T3-MX32.wav" mix:T3-MX76="$D-T3-MX76.wav" `
  --hi-from MX76 --json "$D-result.json"
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
- **HI\*** = the pooled p50 of the large-room (`MX76`) mix windows across
  all tracks, where music unambiguously dominates (`--hi-from MX76`).

Score the pair (LO\*, HI\*) with `--knots LO*,HI*`.

**What each test protects.** "Controls bankable ≤ 0.05" is the hard
requirement. It means speech alone almost never banks a pull sample, so
the correction is never invented where no music is contaminating the
speech. "Clean ≤ 0.10" on a mix take means music-contaminated speech is
rarely mistaken for clean speech and folded into the baseline. That
matters most where the music actually pulls the reading. At 32 % the pull
is small (on 09-24, contamination scaled with level), so absorbing
there costs less than banking speech as pull.

- **A — separable at the operating level.** HI\* > LO\*, **every**
  control has bankable ≤ 0.05, and **every** `MX32` and `MX76` take has
  clean ≤ 0.10. → Propose (LO\*, HI\*) as the new knots. This is the
  second ladder on this machine, so it meets CLAUDE.md's "measured twice"
  bar. Promoting them to `config.py` defaults (replacing the Mac's) is a
  REQUIRES-REVIEW diff with this entry as its evidence. `.env` then drops
  its override.
- **B — separable at large-room volume only.** As A, but some `MX32` take
  has clean > 0.10, while every `MX76` take has clean ≤ 0.10. →
  Same proposal, plus a written operating envelope: "the music-aware
  correction engages in large rooms. At small-room volume (32 %),
  speech over music mostly reads as clean speech, so it is left
  uncorrected." Whether that's acceptable is a founder decision recorded
  with it. It's likely to be, if the 32 % pull is small. Measuring that
  pull is a separate question this session does not answer.
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
