"""End to end: a time-accelerated watch on the simulated source."""

import os

from multidex import products
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0


def _run_watch(home, profile, hours=2):
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


def test_accelerated_watch_produces_products(home, profile):
    # A synthetic coastal home: on the shipped inland reference
    # position the AIS desk stands down at muster under 0.9.0
    # applicability, and this test wants the full desk spread
    # flying. The stand-down itself is covered in
    # test_fixes_090.py.
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    watch_id = _run_watch(home, profile)
    wdir = os.path.join(home, "watches", watch_id)
    assert os.path.exists(os.path.join(wdir, "REPORT.md"))
    assert os.path.exists(os.path.join(wdir, "events.jsonl"))
    assert os.path.exists(os.path.join(wdir, "bill.json"))
    sitreps = [f for f in os.listdir(wdir) if f.startswith("SITREP")]
    assert len(sitreps) >= 1
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "## 2. Coverage account" in report
    index = Index(home)
    try:
        assert index.count_events(watch_id=watch_id) > 100
        assert len(index.all_dossiers()) >= 10
        # The ask surface answers from this Index.
        assert "events within" in products.ask(index, "1090 MHz")
        desks = index.counts_by_desk(watch_id)
        for desk in ("adsb", "ais", "acars", "pager", "sweep"):
            assert desks.get(desk, 0) > 0, desk
    finally:
        index.close()
    # The current-watch pointer is cleared at stand-down.
    from multidex import util
    assert util.read_json(os.path.join(home, "current_watch"),
                          default=None) is None


def test_tuner_outage_becomes_a_gap(home, profile):
    profile["advanced"]["tuner_outages"] = [[200, 300]]
    watch_id = _run_watch(home, profile, hours=1)
    index = Index(home)
    try:
        gaps = index.events(watch_id=watch_id, kinds=["gap"])
        assert len(gaps) >= 1
        assert "tuner lost" in gaps[0]["content"]
        report = open(os.path.join(index.watch_dir(watch_id),
                                   "REPORT.md")).read()
        assert "tuner lost" in report
    finally:
        index.close()
