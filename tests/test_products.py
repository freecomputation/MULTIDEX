"""Dissemination: FLASH triggers, SITREP, the Watch Report, ask."""

import os

from multidex import products, util
from multidex.events import make_event

from conftest import T0

W = "watch-p"


def _seed_watch(index, profile):
    index.set_watch_meta(W, {
        "watch_id": W, "start": T0, "planned_end": T0 + 86400,
        "end": T0 + 86400, "source": "simulated", "profile_name": "t",
        "region": "the test region", "antenna": "test antenna",
        "disk_ceiling_gb": 20, "ended_early": False,
        "end_reason": None, "resumed": False})
    util.write_json(os.path.join(index.watch_dir(W), "bill.json"), [
        {"id": "bill-001", "cls": "B", "kind": "dwell",
         "label": "Rotation adsb", "start": T0, "dur": 150,
         "center_hz": 1090e6, "bw_hz": 2.4e6, "desks": ["adsb"],
         "pass": None},
        {"id": "bill-002", "cls": "A", "kind": "pass",
         "label": "Satellite pass, NOAA 19", "start": T0 + 3600,
         "dur": 840, "center_hz": 137.1e6, "bw_hz": 2.4e6,
         "desks": ["satellite"], "pass": None}])
    index.add_event(make_event(
        W, "officer", "dwell", T0, T0 + 150, freq_hz=1090e6,
        grade="CONFIRMED", content="Rotation adsb: adsb 4",
        data={"bill_id": "bill-001", "cls": "B", "desks": ["adsb"],
              "heard": {"adsb": 4}}))
    index.add_event(make_event(
        W, "adsb", "decode", T0 + 10, emitter="4840D6",
        grade="CONFIRMED", content="KLM1023",
        data={"emitter_kind": "aircraft", "also": ["KLM1023"]}))
    index.add_event(make_event(
        W, "officer", "gap", T0 + 7200, T0 + 7800, grade="CONFIRMED",
        content="tuner lost during Rotation marine: simulated outage",
        data={"cause": "tuner loss"}))


def test_flash_triggers(index, profile):
    _seed_watch(index, profile)
    alert = make_event(W, "wxradio", "alert", T0 + 60,
                       freq_hz=162.475e6, grade="CONFIRMED",
                       content="Severe Thunderstorm Warning for areas "
                               "020091", data={"flash": True})
    name = products.maybe_flash(index, W, alert, profile)
    assert name and name.startswith("FLASH-")
    text = open(os.path.join(index.watch_dir(W), name)).read()
    assert "Confidence: CONFIRMED" in text
    assert util.EM_DASH not in text
    # An ordinary decode does not flash.
    plain = make_event(W, "adsb", "decode", T0 + 70, emitter="4840D6",
                       grade="CONFIRMED", content="KLM1023", data={})
    assert products.maybe_flash(index, W, plain, profile) is None
    # An assessment at threshold flashes; below it does not.
    weak = make_event(W, "exploit", "assessment", T0 + 80,
                      grade="UNRESOLVED", content="weak", data={})
    assert products.maybe_flash(index, W, weak, profile) is None
    strong = make_event(W, "exploit", "assessment", T0 + 90,
                        grade="PROBABLE", content="strong", data={})
    assert products.maybe_flash(index, W, strong, profile)


def test_report_chapters_and_coverage(index, profile):
    _seed_watch(index, profile)
    report = products.render_report(index, W)
    for chapter in ("## 1. Executive summary", "## 2. Coverage account",
                    "## 3. Day picture", "## 4. Emitter index delta",
                    "## 5. Change log", "## 6. Anomaly chapter",
                    "## 7. Satellite and sonde",
                    "## 8. Transcript highlights", "## 9. Desk health",
                    "## 10. Statistics appendix"):
        assert chapter in report, chapter
    assert "tuner lost during Rotation marine" in report
    assert "Bands never visited" in report
    assert "Satellite pass, NOAA 19" in report  # missed bill item named
    assert util.EM_DASH not in report and util.EN_DASH not in report
    # Numbers come from the database: exactly one decode is in there.
    assert "Total events in the Index for this watch: 3." in report


def test_sitrep_renders(index, profile):
    _seed_watch(index, profile)
    text = products.render_sitrep(index, W, T0, T0 + 21600, 1)
    assert "SITREP 1" in text
    assert "Coverage this period" in text
    assert util.EM_DASH not in text


def test_ask_surfaces(index, profile):
    _seed_watch(index, profile)
    by_emitter = products.ask(index, "4840D6")
    assert "Dossier: 4840D6" in by_emitter
    assert "KLM1023" in by_emitter
    by_freq = products.ask(index, "1090 MHz")
    assert "events within" in by_freq
    by_band = products.ask(index, "band aeronautical")
    assert "aeronautical L-band" in by_band
    by_desk = products.ask(index, "adsb")
    assert "Service adsb (tier 1)" in by_desk
    assert "Nothing in the Index" in products.ask(index, "zzzzzz")
