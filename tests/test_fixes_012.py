"""Regression tests for the 0.1.2 audit fixes.

Each test pins one fix from the full logic audit of 0.1.1 that the
audit suite itself did not already cover with a failing test: the
positive controls and the live halves of the liveness, mutual
exclusion, supervisor, true-time, wxradio and resume fixes, plus the
AIS known-answer check for the rebuilt fixture.
"""

import calendar
import json
import os
import sys
import time

import pytest

BUILD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BUILD)
sys.path.insert(0, os.path.join(BUILD, "tests"))

from conftest import T0
from multidex import cli, codecs, desks as desks_mod, modes, products
from multidex import profile as profilemod
from multidex import supervisor as supervisor_mod
from multidex.events import make_event
from multidex.exploit import Exploit
from multidex.index import Index
from multidex.officer import WatchRunner
from multidex.profile import ProfileError
from multidex.tuner import demod_ais

import rfixture


def _utc(y, mo, d, h, mi=0):
    return float(calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0)))


# ------------------------------------------------------------- C5 control

def test_cpr_frames_five_seconds_apart_do_pair():
    """The pairing window's other half: even and odd frames five
    seconds apart DO pair into a CONFIRMED global position."""
    desks_mod.reset_state()
    home = (39.0, -98.0)

    def ctx(t0):
        return {"watch_id": "w", "desk": "adsb", "t0": t0,
                "t1": t0 + 150, "freq_hz": 1090e6, "home": home,
                "profile": {}, "pass": None}

    even = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                 is_odd=False)
    desks_mod.process_adsb({"frames": [even]}, ctx(T0))
    odd = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                is_odd=True)
    out = desks_mod.process_adsb({"frames": [odd]}, ctx(T0 + 5))
    tele = [e for e in out if e["kind"] == "telemetry"]
    assert len(tele) == 1
    assert tele[0]["data"]["cpr"] == "cpr-global"
    assert tele[0]["grade"] == "CONFIRMED"
    assert abs(tele[0]["data"]["lat"] - 39.5) < 0.1
    assert abs(tele[0]["data"]["lon"] - (-98.5)) < 0.1


# ------------------------------------------------------------ M2 control

def test_missing_regular_positive_control(index, profile):
    """Watch starting 02:00, regular due 03:10 in-watch, silent,
    checked at 04:30: the missing-regular rule fires."""
    for wid, t in (("w1", _utc(2026, 11, 15, 3, 10)),
                   ("w2", _utc(2026, 11, 16, 3, 10))):
        index.add_event(make_event(
            wid, "ism", "decode", t, t, emitter="reg-9",
            grade="CONFIRMED", data={"emitter_kind": "device"}))
    eng = Exploit(index, profile)
    out = eng.check_missing_regulars(
        "w3", _utc(2026, 11, 17, 4, 30), _utc(2026, 11, 17, 2, 0))
    assert len(out) == 1
    assert out[0]["data"]["rule"] == "missing_regular"


# ------------------------------------------------------- C3: live watch

def _fabricate_watch(home, index, wid, pid, complete=False):
    from multidex import util
    wdir = index.watch_dir(wid)
    index.set_watch_meta(wid, {
        "watch_id": wid, "start": T0, "planned_end": T0 + 86400,
        "end": None, "source": "simulated", "profile_name": "default",
        "region": "x", "antenna": "y", "disk_ceiling_gb": 1,
        "ephemeris_age_days": None, "ended_early": False,
        "end_reason": None, "resumed": False, "source_note": None})
    util.write_json(os.path.join(wdir, "state.json"), {
        "next_index": 5, "elapsed_s": 900.0,
        "current": "Rotation adsb (adsb)", "events": 42,
        "dossiers": 3, "flash": 0, "benched": [],
        "complete": complete, "pid": pid, "heartbeat_s": 900.0,
        "executing": "bill-005"})
    util.write_json(os.path.join(home, "current_watch"), wid)
    return wdir


def test_live_watch_status_and_stop(home, index, capsys):
    """A watch whose recorded pid is alive is presented as running,
    and stop writes its flag and reports the stand-down."""
    wid = "watch-20990101-000000"
    _fabricate_watch(home, index, wid, pid=os.getpid())
    index.close()
    text = products.status_text(home)
    assert wid in text and "not running" not in text.lower()
    assert cli.main(["--home", home, "stop"]) == 0
    out = capsys.readouterr().out
    assert "Stop requested for %s" % wid in out
    assert os.path.exists(os.path.join(home, "watches", wid,
                                       "stop.flag"))


def test_watch_folder_without_state_is_never_running(home, index):
    """A watch folder with metadata but no state.json at all is
    never reported as running."""
    wid = "watch-20990102-000000"
    from multidex import util
    index.watch_dir(wid)
    index.set_watch_meta(wid, {"watch_id": wid, "start": T0,
                               "planned_end": T0 + 3600,
                               "source": "simulated"})
    util.write_json(os.path.join(home, "current_watch"), wid)
    index.close()
    text = products.status_text(home)
    assert "not running" in text.lower(), text


# ------------------------------------------------------------- C4 refusal

def test_start_refused_while_another_watch_lives(home, profile, index):
    """A fresh start in a home with a live incomplete watch is
    refused by name, before anything is built or written."""
    wid = "watch-20990103-000000"
    _fabricate_watch(home, index, wid, pid=os.getpid())
    runner = WatchRunner(home, profile, index)
    result = runner.start(start_epoch=T0)
    assert result["ok"] is False
    assert wid in result["refusal"]


# ------------------------------------------------- M5: stderr on bench

def test_benched_desk_carries_its_stderr_tail(tmp_path, monkeypatch):
    """When a desk is benched after crashes, the supervisor's info
    carries the tail of the desk's stderr log, and the log file
    itself holds the desk's traceback."""
    deskdir = tmp_path / "desks"
    deskdir.mkdir()
    (deskdir / "crashy.py").write_text(
        "def process(feed, ctx):\n"
        "    raise RuntimeError('boom-from-crashy')\n")
    monkeypatch.setenv("MULTIDEX_DESK_DIR", str(deskdir))
    watch_dir = tmp_path / "watch-x"
    sup = supervisor_mod.Supervisor()
    sup.set_watch_dir(str(watch_dir))
    try:
        _events, info = sup.run("crashy", {}, {"t0": 1000.0,
                                              "t1": 1060.0})
    finally:
        sup.shutdown()
    assert info["benched"] is True
    assert "boom-from-crashy" in info.get("stderr_tail", ""), info
    log = watch_dir / "desk-stderr-crashy.log"
    assert log.exists()
    assert "boom-from-crashy" in log.read_text()


# ------------------------------------------------------- M9: true times

def test_execute_records_actual_and_planned_times(home, profile,
                                                  index):
    """A dwell executed at an actual start later than its planned
    start records the actual times on the dwell event and the desk
    events, with the planned start carried alongside."""
    profile["source"]["kind"] = "simulated"
    runner = WatchRunner(home, profile, index)
    runner.watch_id = "watch-m9"
    runner.watch_dir = index.watch_dir("watch-m9")
    from multidex.sim import SimSource
    from multidex.tuner import TunerServer
    from multidex.exploit import Exploit
    runner.exploit = Exploit(index, profile)
    runner.tuner = TunerServer(SimSource(profile, T0), profile)
    runner.tuner.begin_watch(T0)
    item = {"id": "bill-001", "cls": "B", "kind": "dwell",
            "label": "Rotation adsb", "start": T0, "dur": 150,
            "center_hz": 1090e6, "bw_hz": 2.4e6, "desks": ["adsb"],
            "pass": None}
    runner._execute(item, T0, actual_start=T0 + 500)
    dwells = index.dwell_records("watch-m9")
    assert len(dwells) == 1
    dwell = dwells[0]
    assert dwell["t_start"] == T0 + 500
    assert dwell["t_end"] == T0 + 650
    assert dwell["data"]["planned_start"] == T0
    assert dwell["data"]["planned_end"] == T0 + 150
    desk_events = [e for e in index.events(watch_id="watch-m9")
                   if e["desk"] == "adsb"]
    assert desk_events, "the adsb desk filed nothing"
    assert all(e["t_start"] >= T0 + 500 for e in desk_events)


# ----------------------------------------------------- M10: wxradio grade

def test_wxradio_presence_is_probable_not_confirmed(profile):
    """Signal-energy presence files a PROBABLE census; CONFIRMED is
    reserved for a decoded SAME header."""
    ctx = {"watch_id": "w", "desk": "wxradio", "t0": T0, "t1": T0 + 60,
           "freq_hz": 162.475e6, "home": (39.0, -98.0),
           "profile": profile, "pass": None}
    out = desks_mod.process_wxradio(
        {"present": True, "same": [], "freq_hz": 162.475e6}, ctx)
    assert len(out) == 1
    assert out[0]["kind"] == "census"
    assert out[0]["grade"] == "PROBABLE"


# --------------------------------------------------------- m3: count cap

def test_snippet_count_cap_refusal_is_recorded(index):
    """A snippet refused by the per-desk count cap files a refusal
    event with the reason, like an oversize refusal does."""
    wdir = index.watch_dir("w1")
    rel, _ = index.keep_snippet("w1", wdir, "airband", "voice",
                                "a.wav", b"A" * 100, 10 ** 9,
                                count_cap=1)
    assert rel is not None
    rel, _ = index.keep_snippet("w1", wdir, "airband", "voice",
                                "b.wav", b"B" * 100, 10 ** 9,
                                count_cap=1)
    assert rel is None
    refusals = [e for e in index.events(watch_id="w1")
                if e["data"].get("snippet_refused") == "b.wav"]
    assert len(refusals) == 1
    assert "cap" in refusals[0]["data"]["reason"]


# ------------------------------------------------- m5: time scale record

def test_time_scale_recorded_in_meta_and_muster(home, profile, index):
    """An accelerated watch records its effective time scale in the
    watch metadata and in the muster event."""
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 0.02
    profile["advanced"]["time_scale"] = 5000.0
    runner = WatchRunner(home, profile, index)
    result = runner.start(start_epoch=T0)
    assert result["ok"]
    meta = index.get_watch_meta(result["watch_id"])
    assert meta["time_scale"] == 5000.0
    muster = [e for e in index.events(watch_id=result["watch_id"],
                                      kinds=["system"])
              if (e["content"] or "").startswith("muster passed")]
    assert muster and muster[0]["data"]["time_scale"] == 5000.0


def test_malformed_env_time_scale_refuses(profile, monkeypatch):
    """A malformed MULTIDEX_TIME_SCALE is a clear refusal, never a
    silently ignored value."""
    monkeypatch.setenv("MULTIDEX_TIME_SCALE", "ludicrous")
    with pytest.raises(ProfileError):
        profilemod.time_scale(profile)
    monkeypatch.setenv("MULTIDEX_TIME_SCALE", "-3")
    with pytest.raises(ProfileError):
        profilemod.time_scale(profile)
    monkeypatch.setenv("MULTIDEX_TIME_SCALE", "250.0")
    assert profilemod.time_scale(profile) == 250.0


# ------------------------------------------------------------ m6: resume

def test_resume_continues_flash_count_and_watch_clock(home, profile,
                                                      index):
    """Resume initializes the FLASH count from the files on disk
    and stamps its resume event with the watch clock (meta start
    plus saved elapsed), not wall time."""
    from multidex import util
    wid = "watch-20000101-000000"
    wdir = index.watch_dir(wid)
    profile["source"]["kind"] = "simulated"
    meta = {"watch_id": wid, "start": T0, "planned_end": T0 + 3600,
            "end": None, "source": "simulated",
            "profile_name": "default", "region": "x", "antenna": "y",
            "disk_ceiling_gb": 1, "ephemeris_age_days": None,
            "ended_early": False, "end_reason": None, "resumed": False,
            "source_note": None}
    index.set_watch_meta(wid, meta)
    util.write_json(os.path.join(wdir, "watch.json"), meta)
    util.write_json(os.path.join(wdir, "bill.json"), [])
    util.write_json(os.path.join(wdir, "state.json"), {
        "next_index": 0, "elapsed_s": 300.0, "current": "x",
        "events": 3, "dossiers": 0, "flash": 1, "benched": [],
        "complete": False})
    util.write_text(os.path.join(wdir, "FLASH-001.md"),
                    "FLASH 001 - fixture\n")
    runner = WatchRunner(home, profile, index)
    result = runner.start(resume_id=wid)
    assert result["ok"], result
    assert runner.flash_count >= 1
    systems = [e for e in index.events(watch_id=wid, kinds=["system"])
               if "resumed" in (e["content"] or "")]
    assert systems, "no resume event filed"
    assert systems[0]["t_start"] == T0 + 300.0, systems[0]["t_start"]


def test_resume_reruns_gates(home, profile, index, monkeypatch):
    """A resume whose muster gates no longer pass is refused, not
    flown on inherited footing."""
    from multidex import util
    from multidex import gates as gates_mod
    wid = "watch-20000102-000000"
    wdir = index.watch_dir(wid)
    profile["source"]["kind"] = "simulated"
    meta = {"watch_id": wid, "start": T0, "planned_end": T0 + 3600,
            "end": None, "source": "simulated",
            "profile_name": "default", "region": "x", "antenna": "y",
            "disk_ceiling_gb": 1, "ephemeris_age_days": None,
            "ended_early": False, "end_reason": None, "resumed": False,
            "source_note": None}
    index.set_watch_meta(wid, meta)
    util.write_json(os.path.join(wdir, "bill.json"), [])
    util.write_json(os.path.join(wdir, "state.json"), {
        "next_index": 0, "elapsed_s": 0.0, "current": "x",
        "events": 0, "dossiers": 0, "flash": 0, "benched": [],
        "complete": False})
    real_run_gates = gates_mod.run_gates

    def broken(profile_, home_, source, at_epoch=None):
        out = real_run_gates(profile_, home_, source,
                             at_epoch=at_epoch)
        return [(n, False if n == "disk" else ok, d)
                for n, ok, d in out]

    monkeypatch.setattr(gates_mod, "run_gates", broken)
    runner = WatchRunner(home, profile, index)
    result = runner.start(resume_id=wid)
    assert result["ok"] is False
    assert "gates" in result["refusal"]


# --------------------------------------------------- M1: one bench set

def test_bench_reasons_unifies_all_sources(profile):
    """bench_reasons combines capability, muster and supervisor
    benches into the one set every surface reads."""
    caps = {"sweep", "adsb", "ais", "wxradio", "ism", "aprs", "pager",
            "satellite", "sonde", "broadcast", "cellular", "trunking"}
    out = desks_mod.bench_reasons(
        profile, "real", caps,
        extra_bench={"satellite": "ephemeris bench (test)"},
        supervisor_benched={"pager": "crashed twice (test)"})
    assert out["acars"].startswith("ACARS decode needs")
    assert out["satellite"] == "ephemeris bench (test)"
    assert out["pager"] == "crashed twice (test)"
    assert out["vdl2"].startswith("VDL2 decode needs")
    assert "adsb" not in out


# ------------------------------------------------------ AIS known answer

def test_ais_known_answer_through_production_path():
    """A type-1 position report with fixed field values, modulated
    per the specification by the fixture, demodulates and decodes
    back to exactly those values through the production path."""
    sentence = codecs.ais_encode_position(
        "366123456", 39.4, -98.1, sog_kt=8.5, cog_deg=92.0,
        heading_deg=92)
    out = demod_ais(rfixture.ais_audio(sentence), 48000)
    assert out == [sentence]
    dec = codecs.ais_decode(out[0])
    assert dec["type"] == 1
    assert dec["mmsi"] == "366123456"
    assert abs(dec["lat"] - 39.4) < 0.001
    assert abs(dec["lon"] - (-98.1)) < 0.001
    assert abs(dec["sog_kt"] - 8.5) < 0.11
    assert abs(dec["cog_deg"] - 92.0) < 0.11
