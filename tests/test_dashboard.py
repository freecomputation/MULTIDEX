"""The live dashboard (0.4.0): the view model over fixture watches,
and the CLI paths around it - status --live falling back off a
terminal, watch unchanged without one, and the TTY handoff.
"""

import io
import os
import sys
import time

import pytest

from multidex import cli, dashboard, liveview, util
from multidex.events import make_event

from conftest import T0

WID = "watch-test-0001"


def _meta(**kw):
    meta = {"watch_id": WID, "start": T0, "planned_end": T0 + 86400,
            "end": None, "source": "real", "source_note": None,
            "profile_name": "default", "region": "test region",
            "antenna": "test antenna", "disk_ceiling_gb": 20,
            "ephemeris_age_days": 1.0, "ended_early": False,
            "end_reason": None, "resumed": False, "time_scale": 1.0}
    meta.update(kw)
    return meta


def _bill():
    return [
        {"id": "bill-001", "cls": "B", "kind": "dwell",
         "label": "Rotation airband", "start": T0, "dur": 600,
         "center_hz": 124.35e6, "bw_hz": 2.4e6, "desks": ["airband"],
         "pass": None},
        {"id": "bill-002", "cls": "B", "kind": "dwell",
         "label": "Rotation adsb", "start": T0 + 600, "dur": 600,
         "center_hz": 1090e6, "bw_hz": 2.4e6, "desks": ["adsb"],
         "pass": None},
        {"id": "bill-003", "cls": "C", "kind": "sweep",
         "label": "Census sweep", "start": T0 + 1200, "dur": 300,
         "center_hz": 98.1e6, "bw_hz": 2.4e6, "desks": ["sweep"],
         "pass": None},
        {"id": "bill-004", "cls": "A", "kind": "pass",
         "label": "Satellite pass, NOAA 19", "start": T0 + 3600,
         "dur": 840, "center_hz": 137.1e6, "bw_hz": 2.4e6,
         "desks": ["satellite"],
         "pass": {"sat": "NOAA 19", "aos": T0 + 3720, "los": T0 + 4200}},
    ]


def _write_watch(home, index, state, meta=None, bill=None):
    wdir = index.watch_dir(WID)
    util.write_json(os.path.join(wdir, "watch.json"), meta or _meta())
    util.write_json(os.path.join(wdir, "bill.json"),
                    _bill() if bill is None else bill)
    util.write_json(os.path.join(wdir, "state.json"), state)
    index.set_watch_meta(WID, meta or _meta())
    return wdir


def _state(**kw):
    state = {"next_index": 1, "elapsed_s": 900.0,
             "current": "Rotation adsb (adsb)", "events": 0,
             "dossiers": 0, "flash": 0, "benched": ["hfdl"],
             "complete": False, "pid": os.getpid(),
             "heartbeat_s": 900.0, "executing": "bill-002"}
    state.update(kw)
    return state


def _mid_watch(home, index):
    """A watch 900 watch-seconds in: one dwell executed, a benched
    desk with its reason, a FLASHed alert, band detections, a
    snippet, and a dossier."""
    wdir = _write_watch(home, index, _state())
    index.add_event(make_event(
        WID, "officer", "system", T0, T0, grade="CONFIRMED",
        content="muster passed; watch stood with 4 bill items"))
    index.add_event(make_event(
        WID, "officer", "desk_health", T0, T0, grade="CONFIRMED",
        content="desk hfdl benched at muster: dumphfdl is not "
                "staged on this host",
        data={"desk": "hfdl", "benched": True,
              "reason": "dumphfdl is not staged on this host"}))
    index.add_event(make_event(
        WID, "officer", "dwell", T0, T0 + 600, freq_hz=124.35e6,
        grade="CONFIRMED", content="Rotation airband: silence",
        data={"bill_id": "bill-001", "cls": "B",
              "label": "Rotation airband", "desks": ["airband"],
              "heard": {}, "bw_hz": 2.4e6, "dropped": 0,
              "planned_start": T0, "planned_end": T0 + 600}))
    for t, freq in ((T0 + 100, 124.35e6), (T0 + 200, 124.35e6),
                    (T0 + 300, 98.1e6)):
        index.add_event(make_event(
            WID, "sweep", "observation", t, t, freq_hz=freq,
            grade="UNRESOLVED", content="emission seen"))
    index.add_event(make_event(
        WID, "adsb", "decode", T0 + 700, T0 + 700, freq_hz=1090e6,
        emitter="A5D33B", grade="CONFIRMED",
        content="AAL1629 at 36000 ft",
        data={"emitter_kind": "aircraft"}))
    alert = make_event(
        WID, "wxradio", "alert", T0 + 800, T0 + 800, freq_hz=162.475e6,
        grade="CONFIRMED", content="SAME alert decoded")
    index.add_event(alert)
    util.write_text(os.path.join(wdir, "FLASH-001.md"),
                    "FLASH 001 - test\nWhat: SAME alert decoded\n"
                    "Events: %s\n" % alert["id"])
    index.keep_snippet(WID, wdir, "wxradio", "voice", "alert.wav",
                       b"RIFF-test", 10 ** 9, t=T0 + 800)
    return wdir


# ------------------------------------------------------------ view model

def test_view_model_mid_watch(home, index):
    _mid_watch(home, index)
    view = dashboard.load_view(home, WID, now=time.time())
    assert view is not None
    assert view["status"] == "running"
    assert view["source"] == "real"
    assert view["progress"] == pytest.approx(900 / 86400, abs=0.001)
    cur = view["current"]
    assert cur["id"] == "bill-002"
    assert cur["desks"] == ["adsb"]
    assert cur["center_hz"] == 1090e6
    assert cur["kind_label"] == "rotation dwell"
    assert view["next"]["id"] == "bill-003"
    assert 200 < view["next"]["in_s"] <= 300
    assert view["next_a"]["pass_sat"] == "NOAA 19"
    assert 2600 < view["next_a"]["in_s"] <= 2700
    totals = view["totals"]
    assert totals["events"] == 8
    assert totals["dossiers"] == 1
    assert totals["flash"] == 1
    assert totals["snippets"] == 1
    assert totals["executed_s"] == 600
    assert totals["planned_s"] == 2340
    assert totals["gaps"] == 0
    desks = {d["name"]: d for d in view["desks"]}
    assert desks["hfdl"]["state"] == "benched"
    assert desks["hfdl"]["reason"] == \
        "dumphfdl is not staged on this host"
    assert desks["adsb"]["state"] == "active"
    assert desks["airband"]["state"] == "standing"
    feed = view["feed"]
    assert feed[0]["desk"] == "wxradio"  # newest first
    assert feed[0]["flash"] is True
    assert feed[0]["grade"] == "CONFIRMED"
    assert all(not row["flash"] for row in feed[1:])
    stamps = [row["stamp"] for row in feed]
    assert stamps == sorted(stamps, reverse=True)
    bands = {b["name"]: b for b in view["bands"]}
    assert bands["aviation VHF"]["count"] == 2
    assert bands["FM broadcast"]["count"] == 1
    assert bands["aviation VHF"]["bar"] == dashboard.BAR_WIDTH
    assert bands["FM broadcast"]["bar"] == dashboard.BAR_WIDTH // 2


def test_view_model_completed_watch(home, index):
    _write_watch(home, index, _state(
        next_index=4, elapsed_s=86400.0, current="stood down",
        benched=[], executing=None, complete=True,
        heartbeat_s=86400.0))
    view = dashboard.load_view(home, WID, now=time.time())
    assert view["status"] == "complete"
    assert view["progress"] == pytest.approx(1.0)
    assert view["remaining_s"] == 0
    assert view["current"] is None
    assert view["next"] is None
    assert view["next_a"] is None


def test_view_model_just_mustered(home, index):
    _write_watch(home, index, _state(
        next_index=0, elapsed_s=0.0, current="at muster", benched=[],
        executing=None, heartbeat_s=0.0))
    index.add_event(make_event(
        WID, "officer", "system", T0, T0, grade="CONFIRMED",
        content="muster passed; watch stood with 4 bill items"))
    view = dashboard.load_view(home, WID, now=time.time())
    assert view["status"] == "running"
    assert view["current"] is None
    assert view["next"]["id"] == "bill-001"
    assert view["next"]["in_s"] <= 1
    assert view["totals"]["events"] == 1
    assert view["totals"]["executed_s"] == 0
    assert view["bands"] == []
    assert view["bands_note"] == \
        "no sweep or census detections yet this watch"
    assert len(view["feed"]) == 1
    assert view["feed"][0]["flash"] is False


def test_view_model_no_state_is_none(home, index):
    index.watch_dir(WID)  # a folder alone is not a watch to view
    assert dashboard.load_view(home, WID, now=time.time()) is None


def _raw(state, meta, bill, mtime):
    return {"watch_id": WID, "state": state, "state_mtime": mtime,
            "meta": meta, "bill": bill, "feed_events": [],
            "band_counts": {}, "executed_s": 0.0, "gaps": 0,
            "events_total": 0, "dossiers_total": 0,
            "snippets": {"kept_count": 0, "kept_bytes": 0,
                         "shed_count": 0, "shed_bytes": 0},
            "bench_reasons": {}, "flash_ids": set(), "flash_count": 0}


def test_build_view_elapsed_estimate_and_countdowns():
    bill = [
        {"id": "bill-001", "cls": "B", "kind": "dwell", "label": "one",
         "start": 10000, "dur": 600, "center_hz": 1e6, "bw_hz": 1e6,
         "desks": ["adsb"], "pass": None},
        {"id": "bill-002", "cls": "B", "kind": "dwell", "label": "two",
         "start": 10600, "dur": 600, "center_hz": 2e6, "bw_hz": 1e6,
         "desks": ["adsb"], "pass": None},
        {"id": "bill-003", "cls": "A", "kind": "pass", "label": "pass",
         "start": 11200, "dur": 600, "center_hz": 137.1e6,
         "bw_hz": 1e6, "desks": ["satellite"],
         "pass": {"sat": "NOAA 19"}},
    ]
    meta = {"start": 10000, "planned_end": 13600, "source": "real",
            "time_scale": 2.0}
    state = {"next_index": 1, "elapsed_s": 600.0, "current": "two",
             "benched": [], "complete": False, "pid": os.getpid(),
             "executing": "bill-002"}
    view = dashboard.build_view(_raw(state, meta, bill, 5000.0),
                                now=5100.0)
    # 100 wall seconds since the save at twice speed: +200.
    assert view["elapsed_s"] == 800.0
    assert view["current"]["left_s"] == 400.0
    assert view["next"]["in_s"] == 400.0
    assert view["next_a"]["in_s"] == 400.0
    assert view["progress"] == pytest.approx(800 / 3600)


def test_build_view_interrupted_watch_does_not_advance():
    meta = {"start": 10000, "planned_end": 13600, "source": "real",
            "time_scale": 1.0}
    state = {"next_index": 1, "elapsed_s": 600.0, "current": "two",
             "benched": [], "complete": False, "pid": None,
             "executing": "bill-002"}
    view = dashboard.build_view(_raw(state, meta, [], 5000.0),
                                now=999999.0)
    assert view["status"] == "interrupted"
    assert view["elapsed_s"] == 600.0


# -------------------------------------------------------------------- CLI

class _FakeStream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def _fast_profile(profile):
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 2000.0
    return profile


def test_status_plain_path_unchanged(home, index, monkeypatch):
    _write_watch(home, index, _state(complete=True, executing=None,
                                     current="stood down",
                                     elapsed_s=86400.0, benched=[]))
    util.write_json(os.path.join(home, "current_watch"), WID)
    out = _FakeStream(False)
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["--home", home, "status"]) == 0
    assert out.getvalue().strip() == \
        "No watch is running. Last watch %s completed." % WID


def test_status_live_without_tty_falls_back(home, index, monkeypatch):
    _write_watch(home, index, _state(complete=True, executing=None,
                                     current="stood down",
                                     elapsed_s=86400.0, benched=[]))
    util.write_json(os.path.join(home, "current_watch"), WID)
    out = _FakeStream(False)
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["--home", home, "status", "--live"]) == 0
    text = out.getvalue()
    assert "Live view needs an interactive terminal" in text
    assert "Last watch %s completed." % WID in text


def test_watch_non_tty_unchanged(home, profile, monkeypatch):
    prof = _fast_profile(profile)
    monkeypatch.setattr(cli, "load_profile", lambda path: prof)

    def boom(*a, **k):
        raise AssertionError("the dashboard must not start off a TTY")

    monkeypatch.setattr(liveview, "attach", boom)
    out, inp = _FakeStream(False), _FakeStream(False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", inp)
    assert cli.main(["--home", home, "watch"]) == 0
    text = out.getvalue()
    assert "stood and completed" in text
    assert "View closed" not in text


def test_watch_tty_hands_terminal_to_dashboard(home, profile,
                                               monkeypatch):
    prof = _fast_profile(profile)
    monkeypatch.setattr(cli, "load_profile", lambda path: prof)
    calls = []

    def fake_attach(h, wid, keep_awake=False):
        calls.append((h, wid, keep_awake))
        return True, None

    monkeypatch.setattr(liveview, "attach", fake_attach)
    out, inp = _FakeStream(True), _FakeStream(True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", inp)
    assert cli.main(["--home", home, "watch"]) == 0
    assert len(calls) == 1
    assert calls[0][0] == home
    assert calls[0][1].startswith("watch-")
    assert calls[0][2] is False  # no --keep-awake on this invocation
    text = out.getvalue()
    assert "View closed. Watch %s keeps running" % calls[0][1] in text
    assert "multidex status --live" in text
    assert "multidex stop" in text
    assert "stood and completed" in text
