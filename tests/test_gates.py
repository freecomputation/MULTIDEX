"""The muster gates: the fifth law, in plain words."""

import shutil

from multidex import gates
from multidex.profile import load_profile, validate
from multidex.tuner import RealSource


def _gate(gate_list, name):
    return [g for g in gate_list if g[0] == name][0]


def test_gates_pass_on_simulated_profile(profile, home):
    from multidex.sim import SimSource
    source = SimSource(profile, 1794916800.0)
    gate_list = gates.run_gates(profile, home, source,
                                at_epoch=1794916800.0)
    assert gates.gates_passed(gate_list), gate_list


def test_disk_gate_fails_when_ceiling_unreachable(profile, home):
    profile["watch"]["disk_ceiling_gb"] = 10 ** 9
    from multidex.sim import SimSource
    source = SimSource(profile, 1794916800.0)
    gate_list = gates.run_gates(profile, home, source,
                                at_epoch=1794916800.0)
    name, ok, detail = _gate(gate_list, "disk")
    assert not ok and "ceiling" in detail
    assert not gates.gates_passed(gate_list)


def test_clock_gate(monkeypatch, profile, home):
    from multidex.sim import SimSource
    source = SimSource(profile, 1794916800.0)
    monkeypatch.setattr(gates, "clock_offset_s", lambda: 2.5)
    gate_list = gates.run_gates(profile, home, source,
                                at_epoch=1794916800.0)
    assert not _gate(gate_list, "clock")[1]
    monkeypatch.setattr(gates, "clock_offset_s", lambda: 0.25)
    gate_list = gates.run_gates(profile, home, source,
                                at_epoch=1794916800.0)
    assert _gate(gate_list, "clock")[1]
    monkeypatch.setattr(gates, "clock_offset_s", lambda: None)
    gate_list = gates.run_gates(profile, home, source,
                                at_epoch=1794916800.0)
    ok, detail = _gate(gate_list, "clock")[1], _gate(gate_list,
                                                     "clock")[2]
    assert ok and "could not be verified" in detail


def test_home_position_gate_and_profile_validation():
    prof = load_profile(None)
    prof["home"]["lat"] = 0
    prof["home"]["lon"] = 0
    problems = validate(prof)
    assert any("home position is unset" in p for p in problems)
    prof["home"]["lat"] = 91
    assert any("latitude" in p for p in validate(prof))


def test_real_source_benches_itself_without_tools():
    source = RealSource(load_profile(None))
    ok, reason = source.available()
    if shutil.which("rtl_sdr") is None:
        assert not ok and "rtl_sdr" in reason
        # 0.1.1: the real source feeds every desk the host tool set
        # carries; only the ALWAYS/REAL benched desks stay down.
        caps = source.capabilities()
        for desk in ("adsb", "ais", "pager", "ism", "airband",
                     "wxradio", "aprs", "sonde", "broadcast",
                     "cellular", "trunking", "satellite", "sweep"):
            assert desk in caps, desk
        assert "acars" not in caps


def test_doctor_text_lists_tiers(profile, home):
    text = gates.doctor_text(profile, home)
    assert "adsb (tier 1" in text
    assert "satellite (tier 2" in text
    assert "sweep (tier 3" in text
    assert "benched" in text
    assert "transcription: benched" in text
