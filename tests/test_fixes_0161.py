"""0.16.1: the spectrum display, removed - the Console without
the waterfall.

The spectrum waterfall panel, its display-feed ring and the
streaming machinery behind it (0.8.0's panel, 0.16.0's stream)
are gone. What these tests hold: the Console still renders a
full frame at the verified sizes with no spectrum panel in it;
a simulated watch publishes the work, thought and sky rings
and no spectrum ring; the officer's event record is identical
with the display feed on and off; and the dwell's spectrum
product is still filed as tier 1 evidence in the snippet
store, in both feed states - the evidence path never rode the
display publication, and the removal severs nothing from it.
"""

import copy
import os

import pytest

from multidex import __version__, console, dashboard, displayfeed
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0

START = 1000000.0
WID = "watch-0161-test"


def test_version():
    assert __version__ == "0.16.1"


# ------------------------------------------------------------ the frame

def _raw():
    bill = [
        {"id": "bill-001", "cls": "C", "kind": "sweep",
         "label": "Census sweep", "start": START + 3000,
         "dur": 600, "center_hz": 14.2e6, "bw_hz": 2.4e6,
         "desks": ["sweep"], "pass": None},
    ]
    meta = {"start": START, "planned_end": START + 86400,
            "source": "simulated", "time_scale": 1.0,
            "region": "test region", "profile_name": "default"}
    state = {"next_index": 1, "elapsed_s": 3600.0,
             "current": "Census sweep (sweep)", "benched": [],
             "complete": False, "pid": os.getpid(),
             "executing": "bill-001",
             "budget": {"cap_bytes": 20000000000,
                        "consumed_bytes": 3000000000,
                        "reserved_bytes": 6000000000,
                        "free_bytes": 10000000000,
                        "watch_granted_bytes": 5000000000,
                        "watch_consumed_bytes": 100000000}}
    return {"watch_id": WID, "state": state,
            "state_mtime": START + 3600.0,
            "meta": meta, "bill": bill, "feed_events": [],
            "band_counts": {}, "executed_s": 3000.0, "gaps": 0,
            "events_total": 42, "dossiers_total": 3,
            "snippets": {"kept_count": 1, "kept_bytes": 100,
                         "shed_count": 0, "shed_bytes": 0},
            "bench_reasons": {}, "flash_ids": set(),
            "flash_count": 0, "pool": state["budget"],
            "update": None}


def _feed():
    return {
        "work": [{"t": START + 3590.0, "desk": "sweep",
                  "line": "unknown at 14.250 MHz"}],
        "thought": [
            {"t": START + 1, "desk": "officer", "kind": "system",
             "content": "muster passed; watch stood",
             "flash": False}],
        "sky": [],
    }


def _view():
    return dashboard.build_view(_raw(), now=START + 3600.0)


@pytest.mark.parametrize("width,height", [(210, 58), (120, 36),
                                          (76, 22)])
def test_frame_renders_full_with_no_spectrum_panel(width, height):
    frame = console.build_frame(_view(), _feed(), width, height)
    text = console.frame_text(frame)
    assert len(frame) == height
    assert all(len(row) <= width for row in text)
    joined = "\n".join(text)
    # No spectrum panel anywhere in the frame, under any name.
    assert "SPECTRUM" not in joined
    # The pipeline now stands first under the header, and the
    # footer still anchors the frame.
    assert text[4].startswith("PIPELINE")
    assert text[-1].startswith("q - quit view")


def test_frame_at_full_size_carries_the_remaining_panels():
    frame = console.build_frame(_view(), _feed(), 210, 58)
    joined = "\n".join(console.frame_text(frame))
    assert "PIPELINE - tuner, channelizer, desks" in joined
    assert "SKY - the astronomical account" in joined
    assert "RECEPTION POOL" in joined
    assert "EVENT FEED - newest first" in joined
    assert "MIND - the officer's record, as filed" in joined
    assert "SPECTRUM" not in joined


# ------------------------------------------------------------- the feed

def test_feed_has_no_spectrum_publication(tmp_path):
    feed = displayfeed.DisplayFeed(str(tmp_path))
    assert not hasattr(feed, "spectrum")
    feed.work(1.0, "sweep", "a line")
    feed.thought(1.0, "officer", "system", "muster passed")
    feed.sky({"t": 1.0})
    loaded = displayfeed.load_feed(str(tmp_path))
    assert set(loaded) == {"work", "thought", "sky"}
    assert loaded["work"] and loaded["thought"] and loaded["sky"]
    assert not os.path.exists(
        os.path.join(str(tmp_path), "display-spectra.jsonl"))


# ------------------------------------------------- watch-level proofs

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


def _spectrum_evidence(home, watch_id):
    index = Index(home)
    try:
        rows = index.snippet_rows(watch_id)
        return sorted((r["desk"], r["kind"], r["path"], r["bytes"])
                      for r in rows if r["kind"] == "spectrum")
    finally:
        index.close()


def test_watch_publishes_no_spectrum_ring_and_the_record_holds(
        tmp_path, profile):
    home_on = str(tmp_path / "on")
    home_off = str(tmp_path / "off")
    prof_on = copy.deepcopy(profile)
    prof_off = copy.deepcopy(profile)
    prof_off["advanced"]["display_feed"] = False
    wid_on = _run_watch(home_on, prof_on)
    wid_off = _run_watch(home_off, prof_off)
    # The invariance law, unchanged by the removal: the record
    # is identical with the feed on and off.
    assert _event_record(home_on, wid_on) == \
        _event_record(home_off, wid_off)
    dir_on = os.path.join(home_on, "watches", wid_on)
    dir_off = os.path.join(home_off, "watches", wid_off)
    # No spectrum ring is published in either state, ever.
    for watch_dir in (dir_on, dir_off):
        assert not os.path.exists(
            os.path.join(watch_dir, "display-spectra.jsonl"))
    # The feed-off watch publishes nothing at all.
    for name in (displayfeed.WORK_NAME, displayfeed.THOUGHT_NAME,
                 displayfeed.SKY_NAME):
        assert not os.path.exists(os.path.join(dir_off, name))
    # The feed-on watch publishes the three remaining rings.
    loaded = displayfeed.load_feed(dir_on)
    assert set(loaded) == {"work", "thought", "sky"}
    assert loaded["work"], "the watch published no work lines"
    assert loaded["thought"], "the watch published no thought lines"
    assert loaded["sky"], "the watch published no sky records"
    assert any(th["content"].startswith("muster passed")
               for th in loaded["thought"])
    # The evidence severance, proven at the store: the dwell
    # spectrum products are filed as tier 1 evidence in both
    # feed states, identically.
    ev_on = _spectrum_evidence(home_on, wid_on)
    ev_off = _spectrum_evidence(home_off, wid_off)
    assert ev_on, "no spectrum evidence filed with the feed on"
    assert ev_on == ev_off
    index = Index(home_on)
    try:
        tiers = {r["tier"] for r in index.snippet_rows(wid_on)
                 if r["kind"] == "spectrum"}
    finally:
        index.close()
    assert tiers == {1}
