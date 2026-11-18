"""0.8.1: an acknowledged pass never raises a capture FLASH.

A METEOR acknowledgement event is a satellite pass graded
CONFIRMED, but nothing was captured for it: no recording, no
decode, a zero-byte allocation. Before this fix the CONFIRMED
satellite-pass trigger in products.maybe_flash matched it, and
the FLASH it wrote claimed a capture that never happened. These
tests pin both directions through the desk's own event
construction: the acknowledged pass files no FLASH and sounds
no chime, and a genuinely captured pass raises its FLASH
exactly as before.
"""

import glob
import os

from multidex import desks, products

from conftest import T0

W = "watch-t"


def _pass(sat="METEOR-M2 4", norad="59051", window=600):
    return {"sat": sat, "norad": norad, "aos": T0,
            "los": T0 + window, "t_max": T0 + window // 2,
            "max_elev_deg": 55.0, "aos_az_deg": 90.0,
            "los_az_deg": 270.0}


def _sat_ctx(profile, sat="METEOR-M2 4", norad="59051"):
    return {"watch_id": W, "desk": "satellite", "t0": T0,
            "t1": T0 + 600, "freq_hz": 137.9e6,
            "home": (39.0, -98.0), "profile": profile,
            "pass": _pass(sat, norad)}


def _seed(index):
    index.set_watch_meta(W, {
        "watch_id": W, "start": T0, "planned_end": T0 + 86400,
        "end": None, "source": "real", "profile_name": "t",
        "region": "the test region", "antenna": "test antenna",
        "disk_ceiling_gb": 20, "ended_early": False,
        "end_reason": None, "resumed": False})


def test_acknowledged_meteor_pass_files_no_flash(index, profile):
    _seed(index)
    # The real source's feed shape for a METEOR pass, carried
    # through the desk exactly as the officer files it.
    feed = {"sat": "METEOR-M2 4", "norad": "59051",
            "acknowledged": True}
    ev = desks.process_satellite(feed, _sat_ctx(profile))[0]
    assert ev["kind"] == "pass" and ev["desk"] == "satellite"
    assert ev["grade"] == "CONFIRMED"
    assert ev["data"]["acknowledged"] is True
    name = products.maybe_flash(index, W, ev, profile)
    assert name is None
    assert glob.glob(os.path.join(index.watch_dir(W),
                                  "FLASH-*.md")) == []
    assert "chime" not in ev["data"]


def test_captured_noaa_pass_still_flashes(index, profile):
    _seed(index)
    # A genuine capture: the desk locked and recorded the pass.
    # The feed carried no audio, which the event states, but the
    # pass was captured and its FLASH stands as before.
    feed = {"lock": True, "quality": 0.6, "sat": "NOAA 19",
            "norad": "33591"}
    ctx = _sat_ctx(profile, "NOAA 19", "33591")
    ev = desks.process_satellite(feed, ctx)[0]
    assert ev["kind"] == "pass" and ev["desk"] == "satellite"
    assert ev["grade"] == "CONFIRMED"
    assert "pass captured" in ev["content"]
    assert not ev["data"].get("acknowledged")
    name = products.maybe_flash(index, W, ev, profile)
    assert name and name.startswith("FLASH-")
    text = open(os.path.join(index.watch_dir(W), name)).read()
    assert "trigger: satellite pass captured" in text
    assert ev["content"] in text
