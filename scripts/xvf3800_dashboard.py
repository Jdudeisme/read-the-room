"""Start the dashboard on the reSpeaker XVF3800 in its operating state:
AGC frozen at gain 2.0, on the 16 kHz WDM-KS endpoint.

Why this exists (FIELD-NOTES 2026-09-23, founder decision the same day):
the four-leg control session showed the array's stock AGC holds a talker
within ~1.3 dB of its target, flattening the loudness dynamics arousal rides
on, and pins the level hot enough to inflate the playback-off crowd path.
Frozen at gain 2.0 it beat both stock XVF3800 endpoints on every measure at
no certification cost. Whenever the XVF3800 is used, it runs in this state.
(Which microphone is the everyday default is a separate, still-open call:
the built-in array out-scored it on that solo, playback-off session.)

The device setting is runtime-only and reverts on power cycle, so it must be
re-applied after every plug-in or reboot — this launcher does that, verifies
the read-back, and refuses to start on an unverified state: a session whose
device state is unknown is comparable to nothing (protocol doc, section 3).
It never calls SAVE_CONFIGURATION, the one hard-to-undo action on this device.

It also resolves the WDM-KS endpoint by name + host API at launch, because
`--list-devices` indices move across reboots and replugs (25 on 2026-09-13,
21 on 2026-09-23).

Windows only: `xvf_host.exe` is Seeed's win32 host-control build, and WDM-KS
is a Windows host API. Fetch the three binaries and check their SHA-256
against docs/XVF3800-BASELINE-AND-SETUP-PROTOCOL.md section 3; they are not
committed.

    python scripts/xvf3800_dashboard.py                      # apply, verify, launch
    python scripts/xvf3800_dashboard.py --check              # apply + verify only
    python scripts/xvf3800_dashboard.py -- --no-emotion -v   # args after -- go to the dashboard
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Seeed's product default, and within 0.06 dB of the stock *adapted* gain on
# 2026-09-13 (1.985), so frozen sessions sit at the level the stock baseline
# was measured at. The 2026-09-23 control leg ran at exactly this value.
AGC_GAIN = 2.0

DEVICE_NAME = "reSpeaker XVF3800"
# Native 16 kHz: RTR analyses at 16 kHz, so nothing resamples. WASAPI (48 kHz)
# behaved the same within noise on 2026-09-23 but adds the resampler.
HOST_API = "Windows WDM-KS"

DEFAULT_XVF_DIR = Path.home() / "tools" / "xvf3800"

# `xvf_host COMMAND [value]`: no value reads, a value writes. Returns stdout.
XvfCall = Callable[..., str]


class SetupError(RuntimeError):
    """The device could not be put into, or verified in, the operating state."""


def make_xvf_call(xvf_dir: Path) -> XvfCall:
    exe = xvf_dir / "xvf_host.exe"
    if not exe.is_file():
        raise SetupError(
            f"{exe} not found. Fetch xvf_host.exe, command_map.dll and "
            f"device_usb.dll into {xvf_dir} (hashes in "
            f"docs/XVF3800-BASELINE-AND-SETUP-PROTOCOL.md section 3), "
            f"or pass --xvf-dir."
        )

    def call(*args: str) -> str:
        proc = subprocess.run(
            [str(exe), *args], cwd=xvf_dir, capture_output=True, text=True
        )
        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr).strip()
            raise SetupError(
                f"xvf_host {' '.join(args)} exited {proc.returncode}: {detail}"
            )
        return proc.stdout

    return call


def read_param(output: str, name: str) -> str:
    """Pull `NAME value` out of xvf_host's stdout, which also carries a
    `device_init() -- Found device ...` banner line."""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return parts[1]
    raise SetupError(f"no {name} in xvf_host output: {output.strip()!r}")


def apply_operating_state(call: XvfCall) -> tuple[str, float]:
    """Freeze adaptation, THEN pin the gain, then verify both.

    Order matters: gain applies whether or not adaptation is on, and
    writing it while adaptation is still live lets the AGC nudge it before
    the freeze lands — 2.0 written, 2.012035 read back (FIELD-NOTES
    2026-09-23, finding 6). Frozen first, the pinned value holds exactly.
    """
    call("PP_AGCONOFF", "0")
    call("PP_AGCGAIN", f"{AGC_GAIN}")
    onoff = read_param(call("PP_AGCONOFF"), "PP_AGCONOFF")
    gain_s = read_param(call("PP_AGCGAIN"), "PP_AGCGAIN")
    try:
        gain = float(gain_s)
    except ValueError:
        raise SetupError(f"unparseable PP_AGCGAIN read-back {gain_s!r}") from None
    if onoff != "0" or gain != AGC_GAIN:
        raise SetupError(
            f"operating state not applied: PP_AGCONOFF={onoff} "
            f"PP_AGCGAIN={gain_s} (want 0 and {AGC_GAIN})"
        )
    return onoff, gain


def resolve_device(devices: list[dict], hostapis: list[dict]) -> int:
    """Index of the XVF3800's WDM-KS input endpoint. Exactly one, or fail —
    the array also enumerates on MME, DirectSound and WASAPI, and a name
    substring alone would pick whichever of those sorts first."""
    matches = [
        idx
        for idx, info in enumerate(devices)
        if info["max_input_channels"] > 0
        and DEVICE_NAME in info["name"]
        and hostapis[info["hostapi"]]["name"] == HOST_API
    ]
    if len(matches) != 1:
        raise SetupError(
            f"expected one {HOST_API} input named like {DEVICE_NAME!r}, "
            f"found {len(matches)} ({matches}); is the array plugged in?"
        )
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the XVF3800 operating state (AGC frozen at gain "
        f"{AGC_GAIN}) and start the dashboard on its WDM-KS endpoint.",
    )
    parser.add_argument(
        "--xvf-dir",
        type=Path,
        default=DEFAULT_XVF_DIR,
        help=f"directory holding xvf_host.exe and its DLLs (default {DEFAULT_XVF_DIR})",
    )
    parser.add_argument(
        "--check", action="store_true", help="apply and verify, but do not launch"
    )
    parser.add_argument(
        "dashboard_args",
        nargs=argparse.REMAINDER,
        help="arguments after -- are passed to the dashboard",
    )
    args = parser.parse_args(argv)
    passthrough = [a for a in args.dashboard_args if a != "--"]
    if any(a == "--device" or a.startswith("--device=") for a in passthrough):
        parser.error("--device is resolved by this launcher; do not pass it")

    if sys.platform != "win32":
        print("xvf3800_dashboard: Windows only (win32 xvf_host, WDM-KS)", file=sys.stderr)
        return 2

    import sounddevice as sd

    try:
        _, gain = apply_operating_state(make_xvf_call(args.xvf_dir))
        idx = resolve_device(list(sd.query_devices()), list(sd.query_hostapis()))
    except SetupError as exc:
        print(f"xvf3800_dashboard: {exc}\nDashboard NOT started.", file=sys.stderr)
        return 1

    name = sd.query_devices(idx)["name"]
    print(
        f"XVF3800 operating state verified: AGC frozen at gain {gain:.1f} "
        f"(runtime-only). Device [{idx}] {HOST_API}: {name}\n"
        f"Record device state in session notes as \"AGC frozen at gain {gain:.1f}\".",
        flush=True,
    )
    if args.check:
        return 0

    from dashboard.__main__ import main as dashboard_main

    return dashboard_main(["--device", str(idx), *passthrough])


if __name__ == "__main__":
    sys.exit(main())
