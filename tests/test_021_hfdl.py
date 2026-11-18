"""0.2.1 HFDL: the desk stands on the bound dumphfdl program and
benches with its staging reason without it; the rotation's day
and night windows carry the system-table channel sets with every
channel inside its window; the production feed's invocation and
output parsing follow dumphfdl 1.7.0's own forms.

Fixture honesty: no live HFDL frame has been decoded at the
reference site, so no fixture here claims to be one. The parse
samples are constructed to the JSON format the program's own
source defines (fmtr-json.c, lpdu.c, spdu.c in dumphfdl 1.7.0)
and are labelled as constructions; the argv test records what a
PATH stub was actually called with."""

import json
import os
import stat
import time

import pytest

from multidex import desks, gates, toolbind, tuner
from multidex.officer import ROTATION, build_bill

from conftest import T0


@pytest.fixture()
def stub_tools(tmp_path, monkeypatch):
    def _make(name, body):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n%s\n" % body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(
        "PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    toolbind.reset_cache()
    yield _make
    toolbind.reset_cache()


# ------------------------------------------------------------- binding

def test_hfdl_benches_without_dumphfdl(profile):
    toolbind.reset_cache()
    if toolbind.tool_available("dumphfdl"):
        pytest.skip("host carries dumphfdl")
    state, reason = desks.desk_state("hfdl", profile, "real", set())
    assert state == "benched"
    assert "dumphfdl" in reason and "user space" in reason
    assert "no HFDL decode is claimed" in reason


def test_hfdl_stands_with_dumphfdl_stub(stub_tools, profile):
    stub_tools("dumphfdl", "exit 0")
    assert toolbind.tool_available("dumphfdl")
    caps = tuner.RealSource(profile).capabilities()
    assert "hfdl" in caps
    state, reason = desks.desk_state("hfdl", profile, "real", caps)
    assert state == "standing" and reason is None
    reasons = desks.bench_reasons(profile, "real", caps)
    assert "hfdl" not in reasons


# ------------------------------------------------------------ rotation

def _windows(name):
    centers = ROTATION[name][0]
    return centers if isinstance(centers, tuple) else (centers,)


def _inside(freq, centers, half_span):
    return any(abs(freq - c) <= half_span for c in centers)


def test_hfdl_day_night_sets_are_band_pure():
    day = desks.HFDL_DAY_CHANNELS
    night = desks.HFDL_NIGHT_CHANNELS
    assert day and night
    assert not set(day) & set(night)
    assert all(f >= 11.0e6 for f in day)
    assert all(f < 11.0e6 for f in night)


def test_hfdl_channels_inside_their_windows():
    bw = ROTATION["hf_day"][1]
    assert ROTATION["hf_night"][1] == bw
    for f in desks.HFDL_DAY_CHANNELS:
        assert _inside(f, _windows("hf_day"), bw / 2), f
        # and inside the dumphfdl capture span at 3.2 Msps
        assert _inside(f, _windows("hf_day"), 3200000 / 2), f
    for f in desks.HFDL_NIGHT_CHANNELS:
        assert _inside(f, _windows("hf_night"), bw / 2), f
        assert _inside(f, _windows("hf_night"), 3200000 / 2), f


def test_hfdl_sets_anchor_on_the_us_stations():
    # Frequencies are the system table's (version 52), in Hz:
    # Riverhead day and night anchors, San Francisco, Molokai,
    # Barrow.
    day = set(desks.HFDL_DAY_CHANNELS)
    night = set(desks.HFDL_NIGHT_CHANNELS)
    for f in (11387000, 13276000, 17919000, 21931000):  # Riverhead
        assert f in day
    for f in (6661000, 8912000, 5652000):               # Riverhead
        assert f in night
    assert 11327000 in day and 8927000 in night        # San Francisco
    assert 11312000 in day and 8936000 in night        # Molokai
    assert 11354000 in day and 6646000 in night        # Barrow


def test_bill_flies_hfdl_on_its_hours_rules(profile):
    sats, _ = gates.load_sats(profile)
    bill = build_bill(profile, T0, sats)
    hf = [i for i in bill if "hfdl" in i["desks"]]
    assert hf, "the bill must carry the hfdl desk"
    for item in hf:
        hour = time.localtime(item["start"]).tm_hour
        if item["label"] == "Rotation hf_day":
            assert 8 <= hour < 20
            assert item["center_hz"] in _windows("hf_day")
        else:
            assert item["label"] == "Rotation hf_night"
            assert hour >= 20 or hour < 8
            assert item["center_hz"] in _windows("hf_night")
    labels = {i["label"] for i in hf}
    assert labels == {"Rotation hf_day", "Rotation hf_night"}


# ------------------------------------------------------------ dumphfdl

# Constructed to the JSON format dumphfdl 1.7.0's source defines
# (see the module docstring): an SPDU squitter from a named
# ground station, and a downlink LPDU logon confirm whose
# aircraft address rides at the PDU level, as lpdu.c emits it.
SAMPLE_SPDU = {"hfdl": {
    "app": {"name": "dumphfdl", "ver": "1.7.0"},
    "t": {"sec": 1794983377, "usec": 0},
    "freq": 11387000, "bit_rate": 300, "sig_level": -20.5,
    "noise_level": -30.1, "freq_skew": 12.3, "slot": "S",
    "spdu": {"err": False,
             "src": {"type": "Ground station", "id": 4,
                     "name": "Riverhead, New York"},
             "spdu_version": 0, "systable_version": 52}}}
SAMPLE_LPDU = {"hfdl": {
    "app": {"name": "dumphfdl", "ver": "1.7.0"},
    "t": {"sec": 1794983400, "usec": 0},
    "freq": 8927000, "bit_rate": 600, "sig_level": -18.0,
    "noise_level": -29.0, "freq_skew": -5.0, "slot": "S",
    "lpdu": {"err": False,
             "src": {"type": "Aircraft", "id": 12},
             "dst": {"type": "Ground station", "id": 1,
                     "name": "San Francisco, California"},
             "type": {"id": 7, "name": "Logon confirm"},
             "ac_info": {"icao": "A23721"}}}}


def _write_lines(tmp_path, lines):
    path = tmp_path / "dumphfdl-out.json"
    path.write_text("".join(
        (l if isinstance(l, str) else json.dumps(l)) + "\n"
        for l in lines))
    return str(path)


def test_dumphfdl_parse_samples(tmp_path):
    path = _write_lines(tmp_path, [SAMPLE_SPDU, SAMPLE_LPDU])
    msgs = tuner._parse_dumphfdl_output(path)
    assert len(msgs) == 2
    assert msgs[0]["pdu"] == "spdu"
    assert msgs[0]["freq_hz"] == 11387000
    assert msgs[0]["src"]["name"] == "Riverhead, New York"
    assert msgs[1]["pdu"] == "lpdu"
    assert msgs[1]["type_name"] == "Logon confirm"
    assert msgs[1]["ac_info"]["icao"] == "A23721"


def test_dumphfdl_parse_skips_err_and_garbage(tmp_path):
    bad = json.loads(json.dumps(SAMPLE_LPDU))
    bad["hfdl"]["lpdu"]["err"] = True
    path = _write_lines(tmp_path, [
        bad, "not json at all", {"vdl2": {}}, {"hfdl": {"app": {}}}])
    assert tuner._parse_dumphfdl_output(path) == []
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert tuner._parse_dumphfdl_output(str(empty)) == []
    assert tuner._parse_dumphfdl_output(
        str(tmp_path / "missing.json")) == []


def test_hfdl_desk_event_from_parsed_frame(profile):
    feed = {"messages": [{
        "freq_hz": 8927000, "pdu": "lpdu",
        "src": {"type": "Aircraft", "id": 12},
        "dst": {"type": "Ground station", "id": 1,
                "name": "San Francisco, California"},
        "type_name": "Logon confirm",
        "ac_info": {"icao": "A23721"}, "raw": "sample"}]}
    ctx = {"watch_id": "watch-t", "t0": T0, "t1": T0 + 60,
           "freq_hz": 8900000.0, "home": (39.0, -98.0),
           "profile": profile, "pass": None}
    events = desks.process_hfdl(feed, ctx)
    assert len(events) == 1
    ev = events[0]
    assert ev["emitter"] == "A23721"
    assert ev["grade"] == "CONFIRMED"
    assert "Logon confirm" in ev["content"]
    assert "San Francisco, California" in ev["content"]


@pytest.fixture()
def dumphfdl_rig(tmp_path, monkeypatch):
    """A staged-layout stub: bin/dumphfdl on the PATH and a
    system table at the share path the prefix resolution expects.
    The stub records its argv and writes the prepared lines to
    the file its --output specifier names."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tabledir = tmp_path / "share" / "dumphfdl"
    tabledir.mkdir(parents=True)
    (tabledir / "systable.conf").write_text("version = 52;\n")
    record = tmp_path / "dumphfdl.argv"
    lines = tmp_path / "dumphfdl.out"

    def _make(name, body):
        path = bindir / name
        path.write_text("#!/bin/sh\n%s\n" % body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    _make("dumphfdl",
          "printf '%%s\\n' \"$@\" > %s\n"
          "for a in \"$@\"; do case \"$a\" in "
          "decoded:json:file:path=*) out=${a#decoded:json:file:path=} ;; "
          "esac; done\n"
          "cat %s > \"$out\"" % (record, lines))
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    toolbind.reset_cache()

    def _rig(out_lines):
        lines.write_text("".join(
            (l if isinstance(l, str) else json.dumps(l)) + "\n"
            for l in out_lines))
        return record

    yield _rig
    toolbind.reset_cache()


def test_dumphfdl_argv_contract(dumphfdl_rig, tmp_path):
    record = dumphfdl_rig([SAMPLE_LPDU])
    msgs = tuner._run_dumphfdl(8900000.0, [8912.0, 8927.0], 5.0)
    assert len(msgs) == 1 and msgs[0]["type_name"] == "Logon confirm"
    argv = record.read_text().splitlines()
    assert argv[0] == "--soapysdr" and argv[1] == "driver=rtlsdr"
    assert argv[2] == "--device-settings"
    assert argv[3] == "direct_samp=2"
    assert argv[argv.index("--sample-rate") + 1] == "3200000"
    assert argv[argv.index("--centerfreq") + 1] == "8900"
    assert argv[argv.index("--gain") + 1] == "40"
    spec = argv[argv.index("--output") + 1]
    assert spec.startswith("decoded:json:file:path=")
    table = argv[argv.index("--system-table") + 1]
    assert table == str(tmp_path / "share" / "dumphfdl"
                        / "systable.conf")
    assert argv[-2:] == ["8912", "8927"]


def test_dumphfdl_systable_resolution_follows_the_program(tmp_path,
                                                         monkeypatch):
    # No staged table beside the program: resolution is None and
    # the run carries no --system-table (covered by the toolbind
    # rigs, whose stubs sit outside a staged layout).
    bindir = tmp_path / "bare"
    bindir.mkdir()
    stub = bindir / "dumphfdl"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    assert tuner._dumphfdl_systable_path() is None
