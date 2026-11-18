"""0.8.0: the Console, its display feed, and the keep-awake hold.

The frame builder is pure, so the acceptance discipline is the
house one: recorded feeds are replayed through it headlessly at
fixed terminal sizes and the rendered frames are asserted
against the feed's ground truth - the recorded lock bracket at
the recorded frequency, the recorded desk states, no panel
crushed or scrolled at any size, the compact fallback below the
minimum, a stale feed stating its age. The officer invariance
test runs two simulated watches, feed on and feed off, and
holds their event records identical.
"""

import json
import os

import numpy as np
import pytest

from multidex import (audiocensus, console, dashboard, displayfeed,
                      keepawake, toolbind, util)
from multidex.index import Index
from multidex.officer import WatchRunner
from multidex.sim import SimSource

from conftest import T0

WID = "watch-console-test"
START = 1000000.0


# ------------------------------------------------------------ fixtures

def _raw(spectra_t=START + 3590.0, complete=False):
    bill = [
        {"id": "bill-001", "cls": "C", "kind": "sweep",
         "label": "Census sweep", "start": START + 3000, "dur": 600,
         "center_hz": 14.2e6, "bw_hz": 2.4e6, "desks": ["sweep"],
         "pass": None},
        {"id": "bill-002", "cls": "A", "kind": "pass",
         "label": "NOAA 19 pass", "start": START + 7200, "dur": 600,
         "center_hz": 137.1e6, "bw_hz": 150e3, "desks": ["satellite"],
         "pass": {"sat": "NOAA 19"}},
    ]
    meta = {"start": START, "planned_end": START + 86400,
            "source": "simulated", "time_scale": 1.0,
            "region": "test region", "profile_name": "default"}
    state = {"next_index": 1, "elapsed_s": 3600.0,
             "current": "Census sweep (sweep)", "benched": ["hfdl"],
             "complete": complete, "pid": os.getpid(),
             "executing": "bill-001",
             "budget": {"cap_bytes": 20000000000,
                        "consumed_bytes": 3000000000,
                        "reserved_bytes": 6000000000,
                        "free_bytes": 10000000000,
                        "watch_granted_bytes": 5000000000,
                        "watch_consumed_bytes": 100000000}}
    feed_events = [
        {"id": "ev-flash", "t_start": START + 3500, "desk": "sweep",
         "kind": "observation", "grade": "UNRESOLVED",
         "content": "strong unknown emission", "freq_hz": 14.25e6,
         "data": {}},
        {"id": "ev-2", "t_start": START + 3400, "desk": "adsb",
         "kind": "track", "grade": "CONFIRMED",
         "content": "AAL1629 climbing", "freq_hz": 1090e6,
         "data": {}},
    ]
    return {"watch_id": WID, "state": state, "state_mtime": 5000.0,
            "meta": meta, "bill": bill, "feed_events": feed_events,
            "band_counts": {}, "executed_s": 3000.0, "gaps": 0,
            "events_total": 42, "dossiers_total": 3,
            "snippets": {"kept_count": 1, "kept_bytes": 100,
                         "shed_count": 0, "shed_bytes": 0},
            "bench_reasons": {
                "hfdl": "HFDL decode binds the dumphfdl program; "
                        "it is not installed on this host"},
            "flash_ids": {"ev-flash"}, "flash_count": 1,
            "pool": state["budget"], "update": None}


def _feed(spectra_t=START + 3590.0):
    bins = [-110.0] * 100 + [-70.0] * 5 + [-110.0] * 115
    return {
        "spectra": [{
            "t": spectra_t, "bill_id": "bill-001",
            "label": "Census sweep", "kind": "sweep",
            "lo_hz": 13.1e6, "hi_hz": 15.3e6, "floor_db": -110.0,
            "bins": bins,
            "brackets": [{"desk": "sweep", "name": "UNKNOWN-14.25",
                          "grade": "UNRESOLVED", "freq_hz": 14.25e6,
                          "lo_hz": 14.24e6, "hi_hz": 14.26e6}]}],
        "work": [{"t": spectra_t, "desk": "sweep",
                  "line": "unknown at 14.250 MHz"}],
        "thought": [
            {"t": START + 1, "desk": "officer", "kind": "system",
             "content": "muster passed; watch stood", "flash": False},
            {"t": START + 3500, "desk": "officer", "kind": "system",
             "content": "FLASH raised on the guard emission",
             "flash": True}],
    }


def _view(**kw):
    return dashboard.build_view(_raw(**kw), now=START + 3600.0)


def _text(view, feed, width, height, **kw):
    frame = console.build_frame(view, feed, width, height, **kw)
    return frame, console.frame_text(frame)


# --------------------------------------------------------- feed basics

def test_feed_rings_are_bounded(tmp_path):
    feed = displayfeed.DisplayFeed(str(tmp_path))
    for i in range(displayfeed.MAX_THOUGHT * 3):
        feed.thought(1000.0 + i, "officer", "system",
                     "thought %d" % i)
    path = os.path.join(str(tmp_path), displayfeed.THOUGHT_NAME)
    with open(path) as fh:
        lines = fh.readlines()
    assert len(lines) <= displayfeed.MAX_THOUGHT * 3 // 2 + 1
    loaded = displayfeed.load_feed(str(tmp_path))
    assert len(loaded["thought"]) == displayfeed.MAX_THOUGHT
    assert loaded["thought"][-1]["content"].endswith(
        str(displayfeed.MAX_THOUGHT * 3 - 1))
    for i in range(displayfeed.MAX_SPECTRA * 3):
        feed.spectrum({"t": float(i), "lo_hz": 1e6, "hi_hz": 2e6,
                       "floor_db": -100.0,
                       "bins": [-100.0] * 500, "brackets": []})
    loaded = displayfeed.load_feed(str(tmp_path))
    assert len(loaded["spectra"]) == displayfeed.MAX_SPECTRA
    assert all(len(f["bins"]) <= displayfeed.MAX_BINS
               for f in loaded["spectra"])


def test_feed_disabled_writes_nothing(tmp_path):
    feed = displayfeed.DisplayFeed(str(tmp_path), enabled=False)
    feed.thought(1.0, "officer", "system", "muster passed")
    feed.work(1.0, "sweep", "a line")
    feed.spectrum({"t": 1.0, "lo_hz": 1e6, "hi_hz": 2e6,
                   "bins": [-1.0], "brackets": []})
    assert os.listdir(str(tmp_path)) == []


def test_pool_bins_preserves_a_narrow_carrier():
    values = [-100.0] * 1000
    values[517] = -40.0
    pooled = displayfeed.pool_bins(values, 100)
    assert len(pooled) == 100
    assert max(pooled) == -40.0


# ------------------------------------------------------ spectrum sources

def test_audiocensus_display_spectrum():
    fs = 240000.0
    n = 65536
    t = np.arange(n) / fs
    rng = np.random.default_rng(7)
    iq = (0.5 * np.exp(2j * np.pi * 30000.0 * t)
          + 0.03 * (rng.standard_normal(n)
                    + 1j * rng.standard_normal(n))).astype(np.complex128)
    out = audiocensus.analyze(iq, fs, 100e6)
    disp = out["display"]
    assert disp is not None
    assert len(disp["bins"]) <= displayfeed.MAX_BINS
    assert disp["lo_hz"] < 100.03e6 < disp["hi_hz"]
    peak_i = int(np.argmax(disp["bins"]))
    peak_f = disp["lo_hz"] + (peak_i + 0.5) / len(disp["bins"]) \
        * (disp["hi_hz"] - disp["lo_hz"])
    bin_hz = (disp["hi_hz"] - disp["lo_hz"]) / len(disp["bins"])
    assert abs(peak_f - 100.03e6) < 2 * bin_hz


def test_sim_scan_display_spectrum(profile):
    source = SimSource(profile, T0)
    out = source.scan(162.4e6, 2.4e6, T0, T0 + 60)
    disp = out["display"]
    assert disp["lo_hz"] == pytest.approx(162.4e6 - 1.2e6)
    assert disp["hi_hz"] == pytest.approx(162.4e6 + 1.2e6)
    peak_i = int(np.argmax(disp["bins"]))
    peak_f = disp["lo_hz"] + (peak_i + 0.5) / len(disp["bins"]) \
        * (disp["hi_hz"] - disp["lo_hz"])
    # The weather radio carrier is the window's strongest model
    # emission by a wide margin.
    assert abs(peak_f - 162.475e6) < 100e3


# ------------------------------------------------------------ the frame

@pytest.mark.parametrize("width,height", [(210, 58), (120, 36),
                                          (76, 22)])
def test_frame_shape_holds_at_fixed_sizes(width, height):
    frame, text = _text(_view(), _feed(), width, height)
    assert len(frame) == height
    assert all(len(row) <= width for row in text)
    assert "SPECTRUM" in text[4]
    assert "PIPELINE" in "\n".join(text)
    assert text[-1].startswith("q - quit view")


def test_frame_bracket_lands_at_the_recorded_frequency():
    width = 210
    _frame, text = _text(_view(), _feed(), width, 58)
    plot = [row for row in text if "UNKNOWN-14.25" in row]
    assert len(plot) == 1
    col = plot[0].index("UNKNOWN-14.25")
    expected = int((14.24e6 - 13.1e6) / (15.3e6 - 13.1e6) * width)
    assert abs(col - expected) <= 3
    axis = [row for row in text if "13.100 MHz" in row
            and "14.200 MHz" in row]
    assert len(axis) == 1


def test_frame_carries_the_recorded_desk_states():
    _frame, text = _text(_view(), _feed(), 210, 58)
    joined = "\n".join(text)
    assert "hfdl - BENCHED" in joined
    assert "dumphfdl program" in joined
    assert "sweep - ACTIVE" in joined
    assert "unknown at 14.250 MHz" in joined


def test_frame_mind_stream_and_flash_marks():
    _frame, text = _text(_view(), _feed(), 210, 58)
    joined = "\n".join(text)
    assert "MIND - the officer's record" in joined
    assert "muster passed; watch stood" in joined
    assert "FLASH raised on the guard emission" in joined
    assert "FLASH UNRESOLVED" in joined  # the feed's FLASH event


def test_frame_pool_reservoir_and_sky_bench():
    _frame, text = _text(_view(), _feed(), 210, 58)
    joined = "\n".join(text)
    assert "RECEPTION POOL" in joined
    assert "never granted 1.0 GB" in joined
    assert "this watch - granted 5.0 GB" in joined
    assert "█" in joined and "▒" in joined
    assert "SKY - the astronomical account" in joined
    assert "benched in this build" in joined


def test_frame_yield_order_at_the_minimum_size():
    # At 76x22 the mind stream and the event feed yield first;
    # the waterfall and the pipeline stand.
    _frame, text = _text(_view(), _feed(), 76, 22)
    joined = "\n".join(text)
    assert "SPECTRUM" in joined
    assert "PIPELINE" in joined
    assert "MIND" not in joined
    assert "EVENT FEED" not in joined


@pytest.mark.parametrize("width,height", [(60, 18), (48, 30),
                                          (75, 40), (100, 21)])
def test_frame_below_the_minimum_is_the_compact_status(width,
                                                       height):
    frame, text = _text(_view(), _feed(), width, height)
    assert len(frame) == height
    assert all(len(row) <= width for row in text)
    joined = "\n".join(text)
    assert "compact status" in joined
    assert "SPECTRUM" not in joined


def test_frame_stale_feed_states_its_age():
    old = _feed(spectra_t=START + 60.0)
    _frame, text = _text(_view(), old, 210, 58)
    title = [row for row in text if row.startswith("SPECTRUM")][0]
    assert "HELD" in title
    assert "ago" in title


def test_frame_completed_watch_reads_final():
    _frame, text = _text(_view(complete=True), _feed(), 210, 58)
    title = [row for row in text if row.startswith("SPECTRUM")][0]
    assert "final capture" in title
    assert "COMPLETE" in text[0]


def test_frame_without_a_feed_stands_empty_and_says_so():
    empty = {"spectra": [], "work": [], "thought": []}
    frame, text = _text(_view(), empty, 210, 58)
    assert len(frame) == 58
    joined = "\n".join(text)
    assert "no capture published yet" in joined
    assert "nothing filed yet" in joined


def test_frame_update_posture_in_the_header():
    raw = _raw()
    raw["update"] = {"generation": "abcdef0123456789",
                     "status": "probationary",
                     "probation": {"done": 1, "required": 3,
                                   "status": "active"}}
    view = dashboard.build_view(raw, now=START + 3600.0)
    _frame, text = _text(view, _feed(), 210, 58)
    assert "gen abcdef01" in text[0]
    assert "probation 1/3" in text[0]


def test_frame_interrupt_names_the_displaced_item():
    raw = _raw()
    raw["bill"] = [
        {"id": "bill-001", "cls": "B", "kind": "dwell",
         "label": "Rotation airband", "start": START + 3000,
         "dur": 1200, "center_hz": 127e6, "bw_hz": 2.4e6,
         "desks": ["airband"], "pass": None},
        {"id": "int-001", "cls": "I", "kind": "interrupt",
         "label": "Interrupt on 28.794 MHz", "start": START + 3300,
         "dur": 240, "center_hz": 28.794e6, "bw_hz": 400e3,
         "desks": ["sweep"], "pass": None},
    ]
    raw["state"]["executing"] = "int-001"
    raw["state"]["next_index"] = 1
    view = dashboard.build_view(raw, now=START + 3600.0)
    assert view["displaced_label"] == "Rotation airband"
    _frame, text = _text(view, _feed(), 210, 58)
    joined = "\n".join(text)
    assert "TUNER - INTERRUPT" in joined
    assert "displaced: Rotation airband" in joined


# ------------------------------------------------------------- keep-awake

def test_keepawake_unavailable_is_stated_plainly(monkeypatch):
    monkeypatch.setattr(keepawake.shutil, "which", lambda name: None)
    hold = keepawake.acquire()
    assert hold.program is None
    assert not hold.alive()
    assert hold.state.startswith("not held")
    _frame, text = _text(_view(), _feed(), 210, 58,
                         keepawake=hold.state)
    assert "keep-awake: not held" in text[1]


def test_keepawake_hold_and_release(tmp_path, monkeypatch):
    script = tmp_path / "fake-inhibit"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr(keepawake.shutil, "which",
                        lambda name: str(script)
                        if name == "systemd-inhibit" else None)
    hold = keepawake.acquire()
    assert hold.program == "systemd-inhibit"
    assert hold.alive()
    assert hold.state == "held via systemd-inhibit"
    hold.release()
    assert hold.state == "released"
    assert not hold.alive()


def test_keepawake_mechanisms_stand_in_the_census():
    census = {target for _d, target, _k, _u in toolbind.DEPENDENCES}
    for name in keepawake.MECHANISMS:
        assert name in census


# ------------------------------------------- officer feed and invariance

def _run_watch(home, profile, hours=1):
    profile["watch"]["hours"] = hours
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


def _event_record(home, watch_id):
    index = Index(home)
    try:
        evs = index.events(watch_id=watch_id, limit=1000000)
        return [(ev["desk"], ev["kind"], ev["grade"],
                 ev["content"], ev["freq_hz"]) for ev in evs]
    finally:
        index.close()


def test_watch_publishes_the_feed_and_nothing_changes(
        tmp_path, profile):
    import copy
    home_on = str(tmp_path / "on")
    home_off = str(tmp_path / "off")
    prof_on = copy.deepcopy(profile)
    prof_off = copy.deepcopy(profile)
    prof_off["advanced"]["display_feed"] = False
    wid_on = _run_watch(home_on, prof_on)
    wid_off = _run_watch(home_off, prof_off)
    # The record is identical with the feed on and off.
    assert _event_record(home_on, wid_on) == \
        _event_record(home_off, wid_off)
    dir_on = os.path.join(home_on, "watches", wid_on)
    dir_off = os.path.join(home_off, "watches", wid_off)
    for name in (displayfeed.SPECTRA_NAME, displayfeed.WORK_NAME,
                 displayfeed.THOUGHT_NAME):
        assert not os.path.exists(os.path.join(dir_off, name))
    feed = displayfeed.load_feed(dir_on)
    assert feed["spectra"], "the watch published no spectrum frames"
    assert feed["thought"], "the watch published no thought lines"
    assert feed["work"], "the watch published no work lines"
    frame0 = feed["spectra"][0]
    assert frame0["hi_hz"] > frame0["lo_hz"]
    assert 0 < len(frame0["bins"]) <= displayfeed.MAX_BINS
    assert any(th["content"].startswith("muster passed")
               for th in feed["thought"])
    # The published feed renders through the real frame path.
    view, loaded = console.load_frame_data(home_on, wid_on)
    assert view is not None and loaded["spectra"]
    frame = console.build_frame(view, loaded, 210, 58)
    text = console.frame_text(frame)
    assert len(frame) == 58
    assert any(row.startswith("SPECTRUM") for row in text)
    assert any("final capture" in row for row in text)
