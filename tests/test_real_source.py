"""Real-source tests for the 0.1.1 correctness fixes.

Fix 1: the real source feeds the desks (demodulators, tool feeds).
Fix 2: a capture that reaches its time limit keeps its bytes.
Fix 3: a real profile that cannot stand falls back with the
       substitution stated, never silently.
Fix 4: the FLASH chime and its recorded outcome.
Fix 5: the current_watch pointer is validated before it is used.
"""

import argparse
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time

import pytest

import rfixture
from conftest import T0
from multidex import cli, codecs, desks, products, util
from multidex.index import Index
from multidex.officer import WatchRunner
from multidex.profile import load_profile
from multidex.tuner import RealSource, TunerLost, demod_ais, demod_modes

W = "watch-t"


@pytest.fixture()
def shims(tmp_path):
    return rfixture.write_shims(str(tmp_path / "bin"))


@pytest.fixture()
def shim_path(monkeypatch, shims):
    monkeypatch.setenv(
        "PATH", shims + os.pathsep + os.environ.get("PATH", ""))
    return shims


def _ctx(profile, desk, t0=T0, t1=T0 + 300, **kw):
    ctx = {"watch_id": W, "desk": desk, "t0": t0, "t1": t1,
           "freq_hz": 0.0, "home": (profile["home"]["lat"],
                                    profile["home"]["lon"]),
           "profile": profile, "pass": None}
    ctx.update(kw)
    return ctx


# ------------------------------------------------------- fix 1: demod

def test_demod_modes_recovers_genuine_frames():
    from multidex import modes
    frames = [
        modes.encode_callsign("ABC123", "DLH1234"),
        modes.encode_position("ABC123", 39.5, -98.2, 35000,
                              is_odd=False),
        modes.encode_position("ABC123", 39.5, -98.2, 35000,
                              is_odd=True),
        modes.encode_velocity("ABC123", 450, 90),
        modes.encode_callsign("DEF456", "UAL987"),
    ]
    assert demod_modes(rfixture.modes_iq(frames)) == frames


def test_demod_modes_rejects_noise():
    assert demod_modes(bytes(4000)) == []


def test_demod_ais_recovers_sentences():
    s1 = codecs.ais_encode_position("366123456", 39.4, -98.1,
                                    sog_kt=8.5, cog_deg=92.0,
                                    heading_deg=92)
    s5 = codecs.ais_encode_static("366123456", "MV TEST", "WDA0001",
                                  destination="RIVERPORT")
    assert demod_ais(rfixture.ais_audio(s1), 48000) == [s1]
    assert demod_ais(rfixture.ais_audio(s5), 48000) == [s5]


def test_demod_ais_rejects_garbage():
    samples = rfixture.quiet_audio(rate=48000, seconds=1.0)
    assert demod_ais(samples, 48000) == []


# -------------------------------------------------- fix 1: the feeds

def test_real_source_feeds_the_desks(profile, shim_path):
    desks.reset_state()
    source = RealSource(profile)
    ok, _ = source.available()
    assert ok
    caps = source.capabilities()
    for desk in ("adsb", "ais", "pager", "ism", "airband", "wxradio",
                 "aprs", "sonde", "broadcast", "cellular", "trunking",
                 "satellite", "sweep"):
        assert desk in caps, desk
    assert "acars" in caps and "milair" in caps

    # ADS-B: genuine Mode S frames out of the 1090 MHz capture.
    feeds = source.capture(1090e6, 2.4e6, T0, T0 + 150,
                           [{"desk": "adsb", "freq_hz": 1090e6,
                             "bw_hz": 2e6}])
    assert feeds["adsb"]["frames"], "no Mode S frames demodulated"
    events = desks.process_adsb(feeds["adsb"], _ctx(profile, "adsb", t1=T0 + 150))
    decodes = [e for e in events if e["kind"] == "decode"]
    assert decodes and decodes[0]["emitter"] == rfixture.FIXTURE_ICAO
    assert any(e["kind"] == "telemetry" for e in events)

    # AIS + wxradio off the marine capture.
    feeds = source.capture(
        162.3e6, 2.4e6, T0, T0 + 300,
        [{"desk": "ais", "freq_hz": 161.975e6, "bw_hz": 25e3},
         {"desk": "ais", "freq_hz": 162.025e6, "bw_hz": 25e3},
         {"desk": "wxradio", "freq_hz": 162.475e6, "bw_hz": 200e3}])
    assert feeds["ais"]["sentences"], "no AIS sentences demodulated"
    events = desks.process_ais(feeds["ais"], _ctx(profile, "ais"))
    assert any(e["kind"] == "telemetry"
               and e["emitter"] == rfixture.FIXTURE_MMSI
               for e in events)
    assert feeds["wxradio"]["present"] is True
    assert feeds["wxradio"]["same"], "no SAME header decoded"
    events = desks.process_wxradio(feeds["wxradio"], _ctx(profile, "wxradio"))
    assert any(e["kind"] == "alert" for e in events)

    # Pager and APRS through multimon-ng.
    feeds = source.capture(929.6e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "pager", "freq_hz": 929.6125e6,
                             "bw_hz": 25e3}])
    assert feeds["pager"]["messages"][0]["message"] == "FIXTURE PAGE TEST"
    events = desks.process_pager(feeds["pager"], _ctx(profile, "pager"))
    assert any(e["kind"] == "decode" for e in events)
    feeds = source.capture(144.39e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "aprs", "freq_hz": 144.39e6,
                             "bw_hz": 25e3}])
    events = desks.process_aprs(feeds["aprs"], _ctx(profile, "aprs"))
    assert any(e["kind"] == "telemetry"
               and e["emitter"] == "W0TEST" for e in events)

    # ISM through rtl_433.
    feeds = source.capture(433.92e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "ism", "freq_hz": 433.92e6,
                             "bw_hz": 250e3}])
    events = desks.process_ism(feeds["ism"], _ctx(profile, "ism"))
    assert {e["emitter"] for e in events} >= {"Acurite-5n1-4242"}

    # Airband: the burst is a segment, and its audio is the snippet.
    channels = desks.channels_for("airband")
    vfos = [{"desk": "airband", "freq_hz": freq, "bw_hz": bw or 25e3}
            for freq, bw in channels]
    feeds = source.capture(125e6, 2.4e6, T0, T0 + 600, vfos)
    segments = feeds["airband"]["segments"]
    assert segments, "no airband segments segmented"
    assert any(s.get("audio_b64") for s in segments), \
        "the strong burst carries no captured audio"
    events = desks.process_airband(feeds["airband"], _ctx(profile, "airband", t1=T0 + 600))
    voices = [e for e in events if e["kind"] == "voice"]
    assert voices
    snippet = voices[0]["data"]["_snippet"]
    raw = base64.b64decode(snippet["b64"])
    assert raw[:4] == b"RIFF" and len(raw) > 20000

    # Scan-derived desks: sonde, broadcast, cellular, trunking.
    feeds = source.capture(403e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "sonde", "freq_hz": 403e6,
                             "bw_hz": 2.4e6}])
    events = desks.process_sonde(feeds["sonde"], _ctx(profile, "sonde"))
    assert events and events[0]["grade"] == "PROBABLE"
    assert events[0]["data"]["telemetry"] is False
    feeds = source.capture(98e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "broadcast", "freq_hz": 98e6,
                             "bw_hz": 200e3}])
    events = desks.process_broadcast(feeds["broadcast"], _ctx(profile, "broadcast"))
    assert events and events[0]["freq_hz"] == 98e6
    feeds = source.capture(752.5e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "cellular", "freq_hz": 752.5e6,
                             "bw_hz": 2.4e6}])
    events = desks.process_cellular(feeds["cellular"], _ctx(profile, "cellular"))
    assert events and events[0]["grade"] == "PROBABLE"
    feeds = source.capture(851.75e6, 2.4e6, T0, T0 + 300,
                           [{"desk": "trunking", "freq_hz": 851.75e6,
                             "bw_hz": 2.4e6}])
    events = desks.process_trunking(feeds["trunking"], _ctx(profile, "trunking"))
    assert events and events[0]["data"]["metadata_only"] is True
    assert events[0]["grade"] == "PROBABLE"

    # Satellite: a locked pass files the real audio, graded plainly.
    vfo = {"desk": "satellite", "freq_hz": 137.1e6, "bw_hz": 150e3,
           "pass": {"sat": "NOAA 19", "norad": "33591", "aos": T0,
                    "los": T0 + 400, "max_elev_deg": 60.0,
                    "freq_hz": 137.1e6}}
    feeds = source.capture(137.1e6, 2.4e6, T0, T0 + 400, [vfo])
    assert feeds["satellite"]["lock"] is True
    events = desks.process_satellite(feeds["satellite"], _ctx(profile, "satellite", t1=T0 + 400))
    assert events and events[0]["data"]["_snippet"]["filename"] \
        .endswith(".wav")
    assert "not decoded" in events[0]["content"]


def test_real_bench_reasons_are_specific(profile):
    caps = {"sweep", "adsb", "ais"}
    # 0.2.0: the conditional tool bench is the specific-reason case
    # (in 0.1.x this used acars, which stood up in pure Python).
    state, reason = desks.desk_state("vdl2", profile, "real", caps)
    assert state == "benched" and "dumpvdl2" in reason
    state, _ = desks.desk_state("adsb", profile, "real", caps)
    assert state == "standing"
    state, reason = desks.desk_state("ais", profile, "real", {"sweep"})
    assert state == "benched" and "real" in reason


# ------------------------------------------- fix 2: partial captures

_HANG_FM = '''#!/usr/bin/env python3
import os, shutil, sys, time
args = sys.argv[1:]
out = args[-1]
shutil.copyfile(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "airband.raw"), out)
time.sleep(120)
'''

_EMPTY_FM = '''#!/usr/bin/env python3
import sys, time
time.sleep(120)
'''


def _swap_shim(shims, name, text):
    path = os.path.join(shims, name)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o755)


def test_timed_out_capture_keeps_its_bytes(profile, shims,
                                          monkeypatch):
    _swap_shim(shims, "rtl_fm", _HANG_FM)
    monkeypatch.setenv(
        "PATH", shims + os.pathsep + os.environ.get("PATH", ""))
    source = RealSource(profile)
    started = time.time()
    feeds = source.capture(118.7e6, 2.4e6, T0, T0 + 4,
                           [{"desk": "airband", "freq_hz": 118.7e6,
                             "bw_hz": 25e3}])
    elapsed = time.time() - started
    # The shim never exits; the capture must still return with the
    # partial bytes processed, in roughly the slice length.
    assert feeds["airband"]["segments"], \
        "a timed-out capture lost the dwell"
    assert elapsed < 30
    assert "airband" not in source.errors


def test_empty_capture_files_a_gap_not_a_crash(profile, shims,
                                               monkeypatch):
    _swap_shim(shims, "rtl_fm", _EMPTY_FM)
    monkeypatch.setenv(
        "PATH", shims + os.pathsep + os.environ.get("PATH", ""))
    source = RealSource(profile)
    feeds = source.capture(118.7e6, 2.4e6, T0, T0 + 2,
                           [{"desk": "airband", "freq_hz": 118.7e6,
                             "bw_hz": 25e3}])
    assert "airband" not in feeds
    assert "airband" in source.errors  # recorded per desk, not raised


def test_missing_optional_tools_file_errors(profile, tmp_path,
                                            monkeypatch):
    # A host with only the core tools: rtl_433 and multimon-ng
    # absent. The desks they feed file errors; the rest still feed.
    bin_dir = str(tmp_path / "corebin")
    rfixture.write_shims(bin_dir)
    os.remove(os.path.join(bin_dir, "rtl_433"))
    os.remove(os.path.join(bin_dir, "multimon-ng"))
    path = bin_dir + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
    monkeypatch.setenv("PATH", path)
    if shutil.which("rtl_433") is not None \
            or shutil.which("multimon-ng") is not None:
        pytest.skip("this host genuinely has the optional tools")
    source = RealSource(profile)
    feeds = source.capture(
        433.92e6, 2.4e6, T0, T0 + 300,
        [{"desk": "ism", "freq_hz": 433.92e6, "bw_hz": 250e3},
         {"desk": "pager", "freq_hz": 929.6125e6, "bw_hz": 25e3},
         {"desk": "wxradio", "freq_hz": 162.475e6, "bw_hz": 200e3}])
    assert "ism" not in feeds and "rtl_433" in source.errors["ism"]
    assert "pager" not in feeds \
        and "multimon-ng" in source.errors["pager"]
    # The wxradio presence feed survives, and the SAME limitation
    # rides in the feed as a note - never as a capture error beside
    # the feed, which would file the dwell as both failed and heard.
    assert feeds["wxradio"]["present"] is True
    assert "wxradio" not in source.errors
    assert "multimon-ng" in feeds["wxradio"]["same_note"]


# ------------------------------------------------ fix 3: the fallback

def _no_dongle(monkeypatch):
    real_which = shutil.which

    def fake(name, path=None):
        if name in ("rtl_sdr", "rtl_fm"):
            return None
        return real_which(name, path=path)

    monkeypatch.setattr(shutil, "which", fake)


def test_default_profile_is_real_now():
    profile = load_profile(None)
    assert profile["source"]["kind"] == "real"
    assert profile["alerts"]["chime"] is True


def _run_accelerated(profile, home):
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        return result["watch_id"]
    finally:
        index.close()


def test_fallback_is_stated_never_silent(profile, home, monkeypatch):
    _no_dongle(monkeypatch)
    watch_id = _run_accelerated(profile, home)
    index = Index(home)
    try:
        meta = index.get_watch_meta(watch_id)
        assert meta["source"] == "simulated"
        assert meta["source_note"] \
            and "could not be stood" in meta["source_note"]
        muster = [e for e in index.events(kinds=["system"])
                  if (e["content"] or "").startswith("muster passed")]
        assert muster and "source_note" in muster[0]["data"]
    finally:
        index.close()
    report = open(os.path.join(
        home, "watches", watch_id, "REPORT.md")).read()
    assert "Source note:" in report
    assert "could not be stood" in report


def test_status_states_the_substitution(profile, home):
    index = Index(home)
    try:
        index.set_watch_meta(W, {
            "id": W, "start": T0, "planned_end": T0 + 3600,
            "profile": "default", "source": "simulated",
            "source_note": "the real source could not be stood "
                           "(test); this watch runs on the simulated "
                           "source and every event is synthetic."})
        util.write_json(os.path.join(index.watch_dir(W), "state.json"),
                        {"complete": False, "elapsed_s": 60,
                         "current": "Rotation adsb",
                         "events": 3, "dossiers": 1, "flash": 0,
                         "benched": []})
    finally:
        index.close()
    text = products.status_text(home)
    assert "Source note:" in text and "could not be stood" in text


def test_explicit_simulated_profile_is_unchanged(profile, home):
    profile["source"]["kind"] = "simulated"
    watch_id = _run_accelerated(profile, home)
    index = Index(home)
    try:
        meta = index.get_watch_meta(watch_id)
    finally:
        index.close()
    assert meta["source"] == "simulated"
    assert meta.get("source_note") is None


# ------------------------------------------------------ fix 4: chime

def _flash_event(index, profile):
    from multidex.events import make_event
    index.set_watch_meta(W, {"id": W, "start": T0,
                             "planned_end": T0 + 3600,
                             "profile": "default",
                             "source": "simulated"})
    return make_event(W, "wxradio", "alert", T0 + 60,
                      freq_hz=162.475e6, grade="CONFIRMED",
                      content="Tornado Warning for areas 020091",
                      data={"same": "ZCZC-TEST",
                            "areas": ["020091"]})


def test_chime_outcome_is_recorded_without_a_player(profile, home,
                                                    monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    index = Index(home)
    try:
        ev = _flash_event(index, profile)
        name = products.maybe_flash(index, W, ev, profile)
        assert name and name.startswith("FLASH")
        assert ev["data"]["chime"].startswith("not played")
        assert os.path.exists(os.path.join(index.watch_dir(W),
                                           "chime.wav"))
    finally:
        index.close()


def test_chime_plays_via_the_first_available_player(profile, home,
                                                    tmp_path,
                                                    monkeypatch):
    bin_dir = tmp_path / "playbin"
    bin_dir.mkdir()
    player = bin_dir / "pw-play"
    player.write_text("#!/bin/sh\nexit 0\n")
    player.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    index = Index(home)
    try:
        ev = _flash_event(index, profile)
        products.maybe_flash(index, W, ev, profile)
        assert ev["data"]["chime"] == "played via pw-play"
    finally:
        index.close()


def test_chime_off_records_nothing(profile, home):
    profile["alerts"]["chime"] = False
    index = Index(home)
    try:
        ev = _flash_event(index, profile)
        products.maybe_flash(index, W, ev, profile)
        assert "chime" not in ev["data"]
    finally:
        index.close()


# --------------------------------------------------- fix 5: pointer

def _watch_folder(home, watch_id, complete):
    index = Index(home)
    try:
        index.set_watch_meta(watch_id, {
            "id": watch_id, "start": T0, "planned_end": T0 + 3600,
            "profile": "default", "source": "simulated"})
        util.write_json(os.path.join(index.watch_dir(watch_id),
                                     "state.json"),
                        {"complete": complete, "elapsed_s": 60,
                         "current": "Rotation adsb", "events": 1,
                         "dossiers": 0, "flash": 0, "benched": []})
    finally:
        index.close()


def test_stale_pointer_resolves_to_the_running_watch(home):
    old, live = "watch-20260101-000000", "watch-20260102-000000"
    _watch_folder(home, old, complete=True)
    _watch_folder(home, live, complete=False)
    util.write_json(os.path.join(home, "current_watch"),
                    "watch-19990101-000000")  # folder long gone
    assert products.resolve_current_watch(home) == live
    text = products.status_text(home)
    assert live in text and "1999" not in text
    # The stop flag lands in the folder of the watch actually
    # running, nowhere else.
    args = argparse.Namespace(home=home, profile="default")
    assert cli.cmd_stop(args) == 0
    assert os.path.exists(os.path.join(
        home, "watches", live, "stop.flag"))
    assert not os.path.exists(os.path.join(
        home, "watches", old, "stop.flag"))


def test_completed_watch_gets_no_stop_flag(home):
    done = "watch-20260101-000000"
    _watch_folder(home, done, complete=True)
    util.write_json(os.path.join(home, "current_watch"), done)
    assert products.resolve_current_watch(home) is None
    assert "completed" in products.status_text(home)
    args = argparse.Namespace(home=home, profile="default")
    assert cli.cmd_stop(args) == 1
    assert not os.path.exists(os.path.join(
        home, "watches", done, "stop.flag"))
