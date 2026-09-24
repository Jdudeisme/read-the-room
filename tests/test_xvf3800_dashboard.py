"""XVF3800 launcher: the operating state is applied in the measured order,
verified exactly, and the WDM-KS endpoint resolved by name + host API — all
against a fake device (no xvf_host, no sounddevice, no hardware)."""

import importlib.util
import sys
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).parent.parent / "scripts" / "xvf3800_dashboard.py"
spec = importlib.util.spec_from_file_location("xvf3800_dashboard", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(spec)
sys.modules["xvf3800_dashboard"] = launcher
spec.loader.exec_module(launcher)

BANNER = "Device (USB)::device_init() -- Found device VID: 10374 PID: 26 interface: 3\n"


class FakeXvf:
    """Mimics the 2026-09-23 device: while adaptation is live, a gain write
    is nudged before it can be read back (2.0 -> 2.012035)."""

    def __init__(self, onoff="1", gain="5.506433", drift="2.012035"):
        self.state = {"PP_AGCONOFF": onoff, "PP_AGCGAIN": gain}
        self.drift = drift
        self.writes = []

    def __call__(self, name, value=None):
        if value is not None:
            self.writes.append((name, value))
            if name == "PP_AGCGAIN" and self.state["PP_AGCONOFF"] != "0":
                value = self.drift
            self.state[name] = value
            return BANNER
        return f"{BANNER}{name} {self.state[name]} \n"


def test_freezes_adaptation_before_pinning_gain():
    dev = FakeXvf()
    onoff, gain = launcher.apply_operating_state(dev)
    assert dev.writes == [("PP_AGCONOFF", "0"), ("PP_AGCGAIN", "2.0")]
    assert (onoff, gain) == ("0", 2.0)
    assert dev.state == {"PP_AGCONOFF": "0", "PP_AGCGAIN": "2.0"}


def test_gain_first_order_is_what_the_fake_punishes():
    # Guards the fake itself: the old doc order (gain, then freeze) must
    # reproduce the measured 2.012 read-back, or the test above proves nothing.
    dev = FakeXvf()
    dev("PP_AGCGAIN", "2.0")
    dev("PP_AGCONOFF", "0")
    assert dev.state["PP_AGCGAIN"] == "2.012035"


def test_drifted_readback_refuses():
    class StuckAdaptation(FakeXvf):
        def __call__(self, name, value=None):
            if name == "PP_AGCONOFF" and value is not None:
                self.writes.append((name, value))
                return BANNER  # write silently ignored; adaptation stays live
            return super().__call__(name, value)

    with pytest.raises(launcher.SetupError, match="not applied"):
        launcher.apply_operating_state(StuckAdaptation())


def test_read_param_skips_banner_and_rejects_missing():
    assert launcher.read_param(f"{BANNER}PP_AGCGAIN 2 \r\n", "PP_AGCGAIN") == "2"
    with pytest.raises(launcher.SetupError):
        launcher.read_param(BANNER, "PP_AGCGAIN")


HOSTAPIS = [
    {"name": "MME"},
    {"name": "Windows DirectSound"},
    {"name": "Windows WASAPI"},
    {"name": "Windows WDM-KS"},
]
XVF = "Echo Cancelling Speakerphone (reSpeaker XVF3800 4-Mic Array)"


def dev(name, hostapi, inputs=2):
    return {"name": name, "hostapi": hostapi, "max_input_channels": inputs}


def test_resolves_the_wdm_ks_endpoint_not_the_first_name_match():
    devices = [
        dev("Microphone Array on SoundWire D", 0),
        dev("Echo Cancelling Speakerphone (r", 0),  # MME truncates names
        dev(XVF, 1),
        dev(XVF, 2),
        dev("Speakers (reSpeaker XVF3800 4-Mic Array)", 3, inputs=0),
        dev(XVF, 3),
    ]
    assert launcher.resolve_device(devices, HOSTAPIS) == 5


@pytest.mark.parametrize("count", [0, 2])
def test_resolve_requires_exactly_one(count):
    devices = [dev("Microphone Array on SoundWire D", 3)] + [dev(XVF, 3)] * count
    with pytest.raises(launcher.SetupError, match=f"found {count}"):
        launcher.resolve_device(devices, HOSTAPIS)


def test_missing_binary_is_a_setup_error(tmp_path):
    with pytest.raises(launcher.SetupError, match="not found"):
        launcher.make_xvf_call(tmp_path)


def test_device_passthrough_is_rejected():
    with pytest.raises(SystemExit):
        launcher.main(["--check", "--", "--device", "1"])
