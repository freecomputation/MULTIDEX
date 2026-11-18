"""Adversarial audit tests for MULTIDEX 0.1.1 (check-only audit).

Each test asserts the CORRECT behavior. A failing test is a confirmed
finding; a passing test is evidence the area is clean. Test names map
to the audit report's finding numbers.
"""

import calendar
import glob
import json
import os
import subprocess
import sys
import threading
import time

import pytest

BUILD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BUILD)

from multidex import cli, desks as desks_mod, modes, products
from multidex import profile as profilemod
from multidex import supervisor as supervisor_mod
from multidex.events import make_event
from multidex.exploit import Exploit
from multidex.officer import WatchRunner, sonde_items
from multidex.profile import ProfileError
from multidex.tuner import RealSource, TunerLost, demod_modes
from multidex.tuner import _run_tool, _stream_to_file

T0 = 1794916800.0  # 2026-11-17 12:00 UTC, as in conftest


def _utc(y, mo, d, h, mi=0):
    return float(calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0)))


# --------------------------------------------------------------- Mode S

def _modes_iq(frame_hex, data_base, offset=200, total=6000):
    """Synthesize a Mode S frame as unsigned I/Q at 2 Msps.

    data_base is the sample offset of the first data bit cell from the
    start of the preamble. The on-air standard puts the data block
    8 us after the preamble start: sample 16 at 2 Msps. The shipped
    test fixture in tests/rfixture.py originally used 10, inside the
    guard interval, and the demodulator shared the error.
    """
    bits = bin(int(frame_hex, 16))[2:].zfill(112)
    iq = bytearray([127]) * (2 * total)

    def pulse(s):
        iq[2 * s] = 232

    for s in (0, 2, 7, 9):
        pulse(offset + s)
    for j, bit in enumerate(bits):
        pulse(offset + data_base + 2 * j + (0 if bit == "1" else 1))
    return bytes(iq)


def test_audit_modes_fixture_timing_decodes():
    """The shipped fixture (tests/rfixture.py) now modulates at the
    specification offset, data at sample 16, and its frames decode
    through the production demodulator."""
    import rfixture
    frame = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                  is_odd=False)
    out = demod_modes(rfixture.modes_iq([frame]))
    assert out, "spec-timing fixture frame did not decode"
    assert modes.icao(out[0]) == "ABC123"


def test_audit_modes_guard_interval_timing_rejected():
    """A frame modulated at the old fixture offset (data at sample
    10, inside the guard interval) must NOT decode: no real
    transmitter sends there, and a demodulator that reads it is
    slicing the guard silence as data."""
    frame = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                  is_odd=False)
    out = demod_modes(_modes_iq(frame, data_base=10))
    assert out == [], (
        "a guard-interval frame decoded: the demodulator still "
        "slices data from sample 10")


def test_audit_modes_real_preamble_timing_decodes():
    """A real Mode S transmitter starts the data block 8 us after the
    preamble start (sample 16 at 2 Msps). The demodulator must decode
    such a frame; if it only decodes the fixture's 5 us dialect, the
    ADS-B desk is deaf on real RF."""
    frame = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                  is_odd=False)
    out = demod_modes(_modes_iq(frame, data_base=16))
    assert out, ("no frames decoded from a standard-timing Mode S "
                 "frame: demod_modes slices data from sample 10, "
                 "real frames start at sample 16")


# ------------------------------------------------------------ CPR pairs

def test_audit_cpr_frames_an_hour_apart_do_not_pair():
    """CPR global decode is only valid for even/odd frames received
    within about 10 seconds. A frame stored from an earlier dwell
    must not pair with a frame an hour later to produce a CONFIRMED
    global position."""
    desks_mod.reset_state()
    home = (39.0, -98.0)

    def ctx(t0):
        return {"watch_id": "w", "desk": "adsb", "t0": t0,
                "t1": t0 + 150, "freq_hz": 1090e6, "home": home,
                "profile": {}, "pass": None}

    even_a = modes.encode_position("ABC123", 39.5, -98.5, 35000,
                                   is_odd=False)
    desks_mod.process_adsb({"frames": [even_a]}, ctx(T0))
    odd_b = modes.encode_position("ABC123", 44.0, -90.0, 35000,
                                  is_odd=True)
    out = desks_mod.process_adsb({"frames": [odd_b]}, ctx(T0 + 3600))
    tele = [e for e in out if e["kind"] == "telemetry"]
    assert len(tele) == 1, (
        "the fresh odd frame produced %d telemetry events (stale "
        "partner suppressed or multiplied it)" % len(tele))
    ev = tele[0]
    assert ev["data"].get("cpr") == "cpr-local-reference", (
        "hour-old even frame paired into a %r position at "
        "(%s, %s), graded %s"
        % (ev["data"].get("cpr"), ev["data"].get("lat"),
           ev["data"].get("lon"), ev["grade"]))
    # A single-frame local decode resolves the CPR zone nearest the
    # reference (home, 39.0): the odd frame's zone nearest 39.0 is
    # 37.90, one zone width (360/59 = 6.10) south of the encoded
    # 44.0. No correct local decode can do better from a reference
    # five degrees away; what the finding forbids is the CONFIRMED
    # global stitch, asserted above. (The audit's first draft of
    # this test asserted the encoded latitude itself, which no
    # zone-correct local decode can return; corrected in 0.1.2.)
    assert abs(ev["data"]["lat"] - 37.90) < 0.6
    assert abs(ev["data"]["lat"] - 44.0) <= 360.0 / 59 + 0.6


# ------------------------------------------------------ profile holes

def test_audit_sitrep_hours_zero_refused(profile):
    profile["watch"]["sitrep_hours"] = 0
    with pytest.raises(ProfileError):
        profilemod.validate(profile)


def test_audit_sitrep_hours_zero_watch_hangs(tmp_path):
    """With sitrep_hours = 0 the officer's SITREP loop can never
    advance next_sitrep: the watch must refuse the profile instead of
    hanging and flooding the watch folder with SITREP files."""
    text = open(os.path.join(BUILD, "profiles", "default.toml")).read()
    text = text.replace('kind = "real"', 'kind = "simulated"')
    text = text.replace("sitrep_hours = 6", "sitrep_hours = 0")
    text = text.replace("hours = 24", "hours = 1")
    text = text.replace("disk_ceiling_gb = 20", "disk_ceiling_gb = 0.05")
    text = text.replace("time_scale = 1.0", "time_scale = 100000.0")
    prof_path = tmp_path / "hang.toml"
    prof_path.write_text(text)
    home_dir = tmp_path / "mdx"
    script = tmp_path / "run_watch.py"
    script.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from multidex.index import Index\n"
        "from multidex.officer import WatchRunner\n"
        "from multidex.profile import load_profile\n"
        "prof = load_profile(sys.argv[1])\n"
        "idx = Index(sys.argv[2])\n"
        "r = WatchRunner(sys.argv[2], prof, idx)\n"
        "print(r.start(start_epoch=1794916800.0), flush=True)\n" % BUILD)
    try:
        subprocess.run(
            [sys.executable, str(script), str(prof_path),
             str(home_dir)],
            timeout=8, capture_output=True, text=True)
        exited = True
    except subprocess.TimeoutExpired:
        exited = False
    sitreps = glob.glob(os.path.join(
        str(home_dir), "watches", "*", "SITREP-*.md"))
    assert exited and len(sitreps) <= 4, (
        "watch with sitrep_hours=0 %s; SITREP files written: %d"
        % ("exited" if exited else "HUNG (killed at timeout)",
           len(sitreps)))


def test_audit_rotation_scalar_is_profile_error(tmp_path):
    text = open(os.path.join(BUILD, "profiles", "default.toml")).read()
    text = text.replace("adsb     = { weight = 16, dwell_s = 120 }",
                        "adsb = 10")
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(ProfileError):
        profilemod.load_profile(str(path))


def test_audit_sonde_site_missing_lat_refused(profile):
    profile["sondes"]["sites"] = [{"name": "Nowhere", "lon": -98.0}]
    refused = False
    try:
        profilemod.validate(profile)
    except ProfileError:
        refused = True
    assert refused, (
        "validate() accepted a sonde site with no lat; build_bill "
        "then dies with KeyError inside sonde_items")


def test_audit_time_scale_string_refused(profile):
    profile["advanced"]["time_scale"] = "fast"
    with pytest.raises(ProfileError):
        profilemod.validate(profile)


# --------------------------------------------- state / status honesty

def test_audit_state_benched_includes_capability_benches(home, profile,
                                                         index):
    """state.json's benched list must carry every desk the officer
    itself considers benched, including build benches such as hfdl
    (0.2.0: hfdl is the always-benched desk; in 0.1.x this test used
    acars, which stood up in 0.2.0); status prints 'all standing
    desks healthy' when the list is empty."""
    runner = WatchRunner(home, profile, index)
    runner.watch_id = "watch-state"
    runner.watch_dir = index.watch_dir("watch-state")
    src = RealSource(profile)
    reason = runner._desk_benched_reason("hfdl", src)
    assert reason is not None, "sanity: hfdl must bench on real"
    item = {"start": T0, "dur": 150, "label": "Rotation adsb",
            "desks": ["adsb"]}
    runner._save_state(1, item, T0)
    state = json.load(open(os.path.join(runner.watch_dir,
                                        "state.json")))
    assert "hfdl" in state["benched"], (
        "officer benches hfdl (%r) but state.json benched=%r, so "
        "status reports all desks healthy" % (reason, state["benched"]))


def test_audit_dead_watch_not_presented_as_running(home, index, capsys):
    """A watch whose officer process is gone (state complete=False,
    nothing alive) must not be presented by status as the current
    watch, and stop must not claim it will stand down."""
    wid = "watch-20990101-000000"
    wdir = index.watch_dir(wid)
    index.set_watch_meta(wid, {
        "watch_id": wid, "start": T0, "planned_end": T0 + 86400,
        "end": None, "source": "simulated", "profile_name": "default",
        "region": "x", "antenna": "y", "disk_ceiling_gb": 1,
        "ephemeris_age_days": None, "ended_early": False,
        "end_reason": None, "resumed": False, "source_note": None})
    from multidex import util
    util.write_json(os.path.join(wdir, "state.json"), {
        "next_index": 5, "elapsed_s": 900.0,
        "current": "Rotation adsb (adsb)", "events": 42,
        "dossiers": 3, "flash": 0, "benched": [], "complete": False})
    util.write_json(os.path.join(home, "current_watch"), wid)
    index.close()
    text = products.status_text(home)
    assert "not running" in text.lower() \
        or "interrupt" in text.lower(), (
            "status presents a dead watch as live: %r" % text)
    cli.main(["--home", home, "stop"])
    out = capsys.readouterr().out
    assert "No watch is running" in out, (
        "stop told the operator the dead watch 'stands down at the "
        "next dwell boundary': %r" % out)


def test_audit_elapsed_not_quantized_to_dwells(home, profile, index):
    """state.json is the operator's progress view: the first elapsed
    it publishes must not already be a full dwell quantum (the state
    is written only when a dwell completes, so during the whole
    first dwell status shows nothing, then jumps)."""
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 6
    profile["advanced"]["time_scale"] = 100.0
    box = {}

    def run():
        from multidex.index import Index
        idx = Index(home)
        runner = WatchRunner(home, profile, idx)
        box["result"] = runner.start(start_epoch=T0)
        idx.close()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    first_elapsed = None
    deadline = time.time() + 15
    while time.time() < deadline:
        pointer = os.path.join(home, "current_watch")
        if os.path.exists(pointer):
            try:
                wid = json.load(open(pointer))
                spath = os.path.join(home, "watches", wid, "state.json")
                if os.path.exists(spath):
                    first_elapsed = json.load(open(spath))["elapsed_s"]
                    break
            except (ValueError, OSError):
                pass
        time.sleep(0.05)
    # Stand the watch down so the thread finishes.
    pointer = os.path.join(home, "current_watch")
    if os.path.exists(pointer):
        try:
            wid = json.load(open(pointer))
        except (ValueError, OSError):
            wid = None
        if wid:
            with open(os.path.join(home, "watches", wid, "stop.flag"),
                      "w") as fh:
                fh.write("stop\n")
    th.join(timeout=30)
    assert first_elapsed is not None, "state.json never appeared"
    assert first_elapsed < 300.0, (
        "first published elapsed_s is %.1f - a full opening-sweep "
        "quantum; state.json is written only at dwell boundaries"
        % first_elapsed)


# ---------------------------------------------------------- concurrency

def test_audit_second_watch_in_same_home_refused(home, profile):
    """Starting a second watch in a home where one is already
    running must be refused. Two officers share one Index, one
    current_watch pointer and (on real) one tuner."""
    def prof():
        p = json.loads(json.dumps(profile))
        p["source"]["kind"] = "simulated"
        p["watch"]["hours"] = 0.05
        p["watch"]["sitrep_hours"] = 6
        p["advanced"]["time_scale"] = 25.0
        return p

    from multidex.index import Index
    results, errors = {}, {}

    def run(tag):
        try:
            idx = Index(home)
            r = WatchRunner(home, prof(), idx)
            results[tag] = r.start(start_epoch=T0)
            idx.close()
        except Exception as exc:  # noqa: BLE001 - evidence either way
            errors[tag] = "%s: %s" % (type(exc).__name__, exc)

    ta = threading.Thread(target=run, args=("A",))
    ta.start()
    time.sleep(1.0)
    tb = threading.Thread(target=run, args=("B",))
    tb.start()
    ta.join(timeout=60)
    tb.join(timeout=60)
    assert results.get("B", {}).get("ok") is False, (
        "second watch was not refused: results=%r errors=%r"
        % (results, errors))


# -------------------------------------------------------------- exploit

def test_audit_missing_regular_not_yet_due(index, profile):
    """A regular heard at 03:xx on prior watches, in a watch that
    started at 14:00, is not 'missing' at the 20:00 SITREP: its hour
    has not occurred during this watch yet."""
    for wid, t in (("w1", _utc(2026, 11, 15, 3, 10)),
                   ("w2", _utc(2026, 11, 16, 3, 10))):
        index.add_event(make_event(
            wid, "ism", "decode", t, t, emitter="reg-1",
            grade="CONFIRMED", data={"emitter_kind": "device"}))
    eng = Exploit(index, profile)
    out = eng.check_missing_regulars(
        "w3", _utc(2026, 11, 17, 20, 0), _utc(2026, 11, 17, 14, 0))
    assert out == [], (
        "missing-regular fired before the regular's hour occurred "
        "in-watch: %r" % [e["content"] for e in out])


def _baseline(index, hour=12):
    index.put_baseline("desk:ism", hour, 3, {
        "events_mean": 10.0, "events_sd": 2.0, "emitters_mean": 5.0,
        "watches_seen": ["a", "b", "c"]})


def test_audit_new_emitter_batch_first_contact(index, profile):
    """The officer indexes a whole dwell batch before exploitation
    runs, so a first-contact emitter arriving with two events in its
    first batch already has events_count 2 and the new_emitter rule
    (which requires exactly 1) can never fire for it."""
    _baseline(index)
    evs = [make_event("wX", "ism", "decode", T0 + 10 * i, T0 + 10 * i,
                      emitter="newdev-1", grade="CONFIRMED",
                      data={"emitter_kind": "device"})
           for i in range(2)]
    for ev in evs:
        index.add_event(ev)
    out = Exploit(index, profile).process("wX", evs)
    hits = [a for a in out if a["data"].get("rule") == "new_emitter"]
    assert len(hits) == 1, (
        "first-contact emitter with a 2-event first batch produced "
        "%d new_emitter assessments" % len(hits))


def test_audit_new_emitter_single_event_control(index, profile):
    _baseline(index)
    ev = make_event("wX", "ism", "decode", T0, T0, emitter="newdev-2",
                    grade="CONFIRMED", data={"emitter_kind": "device"})
    index.add_event(ev)
    out = Exploit(index, profile).process("wX", [ev])
    hits = [a for a in out if a["data"].get("rule") == "new_emitter"]
    assert len(hits) == 1


def test_audit_new_emitter_first_watch_restraint(index, profile):
    """With no baseline for the desk/hour, the rule must stay silent:
    first watches teach, they do not accuse."""
    ev = make_event("wX", "ism", "decode", T0, T0, emitter="newdev-3",
                    grade="CONFIRMED", data={"emitter_kind": "device"})
    index.add_event(ev)
    out = Exploit(index, profile).process("wX", [ev])
    hits = [a for a in out if a["data"].get("rule") == "new_emitter"]
    assert hits == []


# ----------------------------------------------------------------- ask

def test_audit_ask_recent_events_are_newest(index):
    from multidex import util
    for i in range(15):
        index.add_event(make_event(
            "w1", "ism", "decode", T0 + 100 * i, T0 + 100 * i,
            emitter="dev-1", grade="CONFIRMED",
            content="reading %d" % i, data={}))
    text = products.ask(index, "dev-1")
    assert "reading 14" in text, (
        "'Recent events' omits the newest event; it lists the "
        "oldest 10 of 15")


def test_audit_ask_desk_latest_activity_is_latest(index):
    from multidex import util
    for i in range(15):
        index.add_event(make_event(
            "w1", "ism", "decode", T0 + 100 * i, T0 + 100 * i,
            emitter="dev-%d" % i, grade="CONFIRMED", data={}))
    text = products.ask(index, "ism")
    assert util.fmt_stamp(T0 + 1400) in text, (
        "'Latest activity' is not the latest event: %r" % text)


# ------------------------------------------------------- capture paths

def test_audit_airband_partial_channel_kept(profile):
    """If one airband channel's capture produces no bytes, the
    channels already captured in the same dwell are still evidence
    and must reach the desk."""
    src = RealSource(profile)

    def fake_audio(freq, mode, rate, seconds):
        if freq > 120e6:
            raise TunerLost("rtl_fm produced no audio")
        return [0] * 2400

    src._audio = fake_audio
    vfos = [{"desk": "airband", "freq_hz": 118.1e6, "bw_hz": 25e3},
            {"desk": "airband", "freq_hz": 121.5e6, "bw_hz": 25e3}]
    feeds = src.capture(120e6, 2.4e6, T0, T0 + 600, vfos)
    assert "airband" in feeds, (
        "one dead channel discarded the whole airband feed; "
        "errors=%r" % (src.errors,))


def test_audit_wxradio_no_gap_and_feed_together(profile):
    """When multimon-ng is absent the wxradio handler records a
    capture error AND returns a feed; the officer files a gap for
    the error and also processes the feed - a dwell cannot be both
    failed and heard."""
    import shutil
    if shutil.which("multimon-ng") is not None:
        pytest.skip("multimon-ng present on this host")
    src = RealSource(profile)
    src._audio = lambda freq, mode, rate, seconds: [1000] * 22050
    vfos = [{"desk": "wxradio", "freq_hz": 162.475e6, "bw_hz": 25e3}]
    feeds = src.capture(162.475e6, 25e3, T0, T0 + 300, vfos)
    assert not ("wxradio" in feeds and "wxradio" in src.errors), (
        "wxradio produced both a feed and a capture error: "
        "feeds=%r errors=%r" % (list(feeds), src.errors))


def test_audit_run_tool_timeout_keeps_partial(tmp_path):
    script = tmp_path / "decoder.sh"
    script.write_text("#!/bin/sh\nprintf 'partial-decode\\n'\nsleep 5\n")
    script.chmod(0o755)
    out = _run_tool([str(script)], timeout=1)
    assert "partial-decode" in out


def test_audit_stream_to_file_timeout_keeps_partial(tmp_path):
    script = tmp_path / "tool.sh"
    script.write_text("#!/bin/sh\nprintf 'abc' > \"$1\"\nsleep 5\n")
    script.chmod(0o755)
    target = tmp_path / "cap.raw"
    size = _stream_to_file([str(script), str(target)], str(target), 1)
    assert size == 3


# -------------------------------------------------------------- snippets

def test_audit_snippet_ceiling_bounds_bytes(index):
    wdir = index.watch_dir("w1")
    total_shed = 0
    for i in range(10):
        _rel, shed = index.keep_snippet(
            "w1", wdir, "sweep", "iq", "s%02d.iq" % i, b"X" * 1000,
            2500)
        total_shed += len(shed)
    acct = index.snippet_accounting("w1")
    assert acct["kept_bytes"] <= 2500
    assert total_shed > 0


def test_audit_snippet_same_filename_not_double_counted(index):
    """Airband snippet names are airband-<freq>-<int offset>.wav with
    no dwell component: two dwells can produce the same name. The
    store must not count two rows for the one file on disk."""
    wdir = index.watch_dir("w1")
    name = "airband-121500000-3.wav"
    index.keep_snippet("w1", wdir, "airband", "voice", name,
                       b"A" * 1000, 10 ** 9)
    index.keep_snippet("w1", wdir, "airband", "voice", name,
                       b"B" * 2000, 10 ** 9)
    acct = index.snippet_accounting("w1")
    on_disk = os.path.getsize(os.path.join(wdir, "snippets", name))
    assert acct["kept_bytes"] == on_disk, (
        "accounting claims %d bytes for a %d-byte file (two rows, "
        "one file); the earlier event's evidence was overwritten"
        % (acct["kept_bytes"], on_disk))


def test_audit_snippet_oversize_drop_is_recorded(index):
    """A snippet that can never fit the ceiling is dropped by
    keep_snippet; the drop must leave an auditable record, not
    silence."""
    wdir = index.watch_dir("w1")
    rel, _shed = index.keep_snippet(
        "w1", wdir, "sweep", "iq", "big.iq", b"X" * 5000, 1000)
    assert rel is not None or index.count_events(watch_id="w1") > 0, (
        "oversize snippet vanished with no event and no record")


def test_audit_rebuild_preserves_snippet_accounting(index):
    """rebuild() regenerates the DB from the JSONL logs; snippet
    files stay on disk, so the accounting must survive it. (Latent:
    no production caller today.)"""
    wdir = index.watch_dir("w1")
    index.keep_snippet("w1", wdir, "sweep", "iq", "a.iq", b"X" * 100,
                       10 ** 9)
    index.keep_snippet("w1", wdir, "sweep", "iq", "b.iq", b"Y" * 100,
                       10 ** 9)
    before = index.snippet_accounting("w1")["kept_bytes"]
    index.rebuild()
    after = index.snippet_accounting("w1")["kept_bytes"]
    assert after == before == 200, (
        "after rebuild, accounting says %d bytes while the snippet "
        "files still occupy %d on disk" % (after, before))


# ------------------------------------------------------- supervisor

def test_audit_slow_desk_not_benched_as_crashed(tmp_path, monkeypatch):
    """A desk whose process() takes longer than the job timeout
    before returning is alive, not crashed. The supervisor's timeout
    is per output line, and deskworker only writes after process()
    returns, so a slow-but-alive desk is restarted once and then
    benched as 'crashed twice within an hour'."""
    deskdir = tmp_path / "desks"
    deskdir.mkdir()
    (deskdir / "slowdesk.py").write_text(
        "import time\n\n\ndef process(feed, ctx):\n"
        "    time.sleep(1.0)\n    return []\n")
    monkeypatch.setenv("MULTIDEX_DESK_DIR", str(deskdir))
    old = supervisor_mod._Worker.run.__defaults__
    supervisor_mod._Worker.run.__defaults__ = (0.3,)
    try:
        sup = supervisor_mod.Supervisor()
        _events, info = sup.run("slowdesk", {},
                                {"t0": 1000.0, "t1": 1100.0})
        sup.shutdown()
    finally:
        supervisor_mod._Worker.run.__defaults__ = old
    assert "slowdesk" not in sup.benched, (
        "a desk that answers in 1.0 s was benched: %r (info=%r)"
        % (sup.benched, info))


# ------------------------------------------- FLASH / event agreement

def test_audit_flash_file_event_index_agreement(home, profile, index):
    """Observation 4: the FLASH file, the triggering event and the
    Index must agree - every FLASH file names a persisted event, and
    the chime outcome rides in that event's data."""
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 24
    profile["watch"]["sitrep_hours"] = 6
    profile["advanced"]["time_scale"] = 1000000.0
    runner = WatchRunner(home, profile, index)
    result = runner.start(start_epoch=T0)
    assert result["ok"]
    wdir = os.path.join(home, "watches", result["watch_id"])
    flash_files = sorted(glob.glob(os.path.join(wdir, "FLASH-*.md")))
    assert flash_files, "sanity: a 24 h sim watch raises FLASH files"
    events = {}
    with open(os.path.join(wdir, "events.jsonl")) as fh:
        for line in fh:
            ev = json.loads(line)
            events[ev["id"]] = ev
    for path in flash_files:
        text = open(path).read()
        line = [ln for ln in text.splitlines()
                if ln.startswith("Events:")][0]
        first_id = line.split(":", 1)[1].strip().split(",")[0].strip()
        assert first_id in events, (
            "%s names event %s, which is not in events.jsonl"
            % (os.path.basename(path), first_id))
        ev = events[first_id]
        assert "chime" in ev["data"], (
            "triggering event %s reached the log without its chime "
            "outcome" % first_id)
        assert index.count_events(watch_id=result["watch_id"]) == \
            len(events)


# ------------------------------------------- plain watch never adopts

def test_audit_plain_watch_does_not_adopt_incomplete(home, profile,
                                                     index):
    """Observation 1: a plain watch (no --resume) must muster fresh
    on its own profile, never adopt an incomplete watch left in the
    home - even one that flew a different source."""
    from multidex import util
    old_id = "watch-20000101-000000"
    wdir = index.watch_dir(old_id)
    index.set_watch_meta(old_id, {
        "watch_id": old_id, "start": T0 - 90000,
        "planned_end": T0 - 3600, "end": None, "source": "real",
        "profile_name": "default", "region": "x", "antenna": "y",
        "disk_ceiling_gb": 1, "ephemeris_age_days": None,
        "ended_early": False, "end_reason": None, "resumed": False,
        "source_note": None})
    util.write_json(os.path.join(wdir, "state.json"),
                    {"next_index": 3, "elapsed_s": 900.0,
                     "current": "x", "events": 1, "dossiers": 0,
                     "flash": 0, "benched": [], "complete": False})
    util.write_json(os.path.join(home, "current_watch"), old_id)
    index.close()
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 0.02
    profile["advanced"]["time_scale"] = 100000.0
    from multidex.index import Index
    idx2 = Index(home)
    runner = WatchRunner(home, profile, idx2)
    result = runner.start(start_epoch=T0)
    idx2.close()
    assert result["ok"]
    assert result["watch_id"] != old_id
    meta = json.load(open(os.path.join(
        home, "watches", result["watch_id"], "watch.json")))
    assert meta["source"] == "simulated", (
        "plain watch adopted the stale watch's source: %r"
        % meta["source"])
