"""0.13.0: the change log - new, vanished, moved, and
rhythm-changed emitters, computed from the Index at stand-down
and filed as changelog.json beside the Watch Report chapter
that renders the same entries.

The evidence rules are the product's honesty, and they are
tested both ways: a vanishing is claimed only where the
emitter's band flew comparable coverage, a move requires the
Index's own identity on both sides, and no rhythm is claimed
on fewer observations than the fold itself requires.
"""

import json
import os

from multidex import changelog, products, util
from multidex.__init__ import __version__
from multidex.events import make_event
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0

DAY = 86400.0
MIDNIGHT = 1704067200.0  # 2024-01-01 00:00 UTC, a day boundary
CUR = "watch-now"


def hear(index, watch, emitter, t, freq=100e6, desk="sweep",
         kind="observation", grade="PROBABLE", ekind=None):
    data = {}
    if ekind:
        data["emitter_kind"] = ekind
    index.add_event(make_event(
        watch, desk, kind, t, t + 10, freq_hz=freq,
        emitter=emitter, grade=grade, content="heard", data=data))


def dwell(index, watch, t, freq, desks):
    index.add_event(make_event(
        watch, "officer", "dwell", t, t + 150, freq_hz=freq,
        grade="CONFIRMED", content="dwell",
        data={"bill_id": "bill-x", "cls": "B", "desks": desks,
              "heard": {}}))


def by_kind(log, kind):
    return {e["emitter"]: e for e in log["entries"]
            if e["kind"] == kind}


def test_version():
    assert __version__ == "0.13.0"


# ------------------------------------------------------------------ new

def test_new_arrival_files_and_stray_does_not(index):
    hear(index, "watch-a", "OLD", T0, freq=100e6)
    hear(index, CUR, "NEW1", T0 + 100, freq=121.5e6, desk="airband",
         kind="voice", grade="CONFIRMED", ekind="aircraft")
    hear(index, CUR, "NEW1", T0 + 200, freq=121.5e6, desk="airband",
         kind="voice", grade="CONFIRMED")
    hear(index, CUR, "NEW1", T0 + 300, freq=121.5e6, desk="airband",
         kind="voice", grade="PROBABLE")
    hear(index, CUR, "STRAY", T0 + 400, freq=121.6e6, desk="airband",
         kind="voice")
    log = changelog.compute_changelog(index, CUR)
    new = by_kind(log, "new")
    assert set(new) == {"NEW1"}
    entry = new["NEW1"]
    assert entry["hearings"] == 3
    assert entry["first_heard"] == T0 + 100
    assert entry["last_heard"] == T0 + 300
    assert entry["grade"] == "CONFIRMED"
    assert entry["band"] == "aviation VHF"
    assert entry["freq_hz"] == 121.5e6
    assert log["counts"]["new"] == 1
    # The stray is nowhere: not an entry, not an unwatched claim.
    assert all(e["emitter"] != "STRAY" for e in log["entries"])
    assert all(e["emitter"] != "STRAY" for e in log["unwatched"])
    # An emitter heard before and again now is not new either.
    hear(index, CUR, "OLD", T0 + 500, freq=100e6)
    hear(index, CUR, "OLD", T0 + 600, freq=100e6)
    log = changelog.compute_changelog(index, CUR)
    assert "OLD" not in by_kind(log, "new")


# ------------------------------------------------------------- vanished

def test_vanished_with_coverage_files(index):
    # Established by watches: heard in two earlier watches.
    for watch, base in (("watch-a", T0), ("watch-b", T0 + DAY)):
        hear(index, watch, "GONE", base + 10, freq=162.475e6,
             desk="wxradio", kind="voice", grade="CONFIRMED")
        hear(index, watch, "GONE", base + 20, freq=162.475e6,
             desk="wxradio", kind="voice")
    # Established by hearings alone: five in one earlier watch.
    for k in range(5):
        hear(index, "watch-a", "GONE-TOO", T0 + 100 + k,
             freq=162.55e6, desk="wxradio", kind="voice")
    # The current watch flew the band with the desk that hears
    # them standing.
    dwell(index, CUR, T0 + 2 * DAY, 162.475e6, ["wxradio"])
    log = changelog.compute_changelog(index, CUR)
    vanished = by_kind(log, "vanished")
    assert set(vanished) == {"GONE", "GONE-TOO"}
    assert vanished["GONE"]["watches_prior"] == 2
    assert vanished["GONE"]["hearings_prior"] == 4
    assert vanished["GONE"]["coverage_dwells"] >= 1
    assert vanished["GONE"]["band"] == "VHF high"
    assert not log["unwatched"]


def test_vanished_refused_without_coverage(index):
    for watch, base in (("watch-a", T0), ("watch-b", T0 + DAY)):
        hear(index, watch, "GONE2", base + 10, freq=146.52e6,
             desk="amateur", kind="voice")
        hear(index, watch, "GONE2", base + 20, freq=146.52e6,
             desk="amateur", kind="voice")
    # The current watch flew, but never the amateur 2 m band.
    dwell(index, CUR, T0 + 2 * DAY, 1090e6, ["adsb"])
    log = changelog.compute_changelog(index, CUR)
    assert "GONE2" not in by_kind(log, "vanished")
    assert [e["emitter"] for e in log["unwatched"]] == ["GONE2"]
    assert log["counts"]["unwatched"] == 1
    # The prose states the coverage fact and claims no change.
    section = "\n".join(changelog.render_section(log))
    assert "went unwatched" in section
    assert "- vanished: GONE2" not in section


def test_sweep_coverage_counts_for_vanished(index):
    # A sweep dwell in the emitter's band is comparable coverage
    # for any desk's emitter: the census hears what it walks.
    for watch, base in (("watch-a", T0), ("watch-b", T0 + DAY)):
        hear(index, watch, "HF-GONE", base + 10, freq=7.1e6,
             desk="amateur", kind="voice")
        hear(index, watch, "HF-GONE", base + 20, freq=7.1e6,
             desk="amateur", kind="voice")
    dwell(index, CUR, T0 + 2 * DAY, 10e6, ["sweep"])
    log = changelog.compute_changelog(index, CUR)
    assert "HF-GONE" in by_kind(log, "vanished")


def test_unestablished_absence_files_nothing(index):
    # Heard once, in one earlier watch: not established, so its
    # absence is neither a vanishing nor an unwatched claim.
    hear(index, "watch-a", "FAINT", T0, freq=162.475e6,
         desk="wxradio", kind="voice")
    dwell(index, CUR, T0 + DAY, 162.475e6, ["wxradio"])
    log = changelog.compute_changelog(index, CUR)
    assert all(e["emitter"] != "FAINT" for e in log["entries"])
    assert all(e["emitter"] != "FAINT" for e in log["unwatched"])


# ---------------------------------------------------------------- moved

def test_moved_files_with_delta(index):
    for k, off in enumerate((0, 5 * 3600, 11 * 3600)):
        hear(index, "watch-a", "MOVER", MIDNIGHT + off,
             freq=100.0e6, desk="trunking", kind="census")
    for k, off in enumerate((3600, 6 * 3600, 12 * 3600)):
        hear(index, CUR, "MOVER", MIDNIGHT + DAY + off,
             freq=100.3e6, desk="trunking", kind="census")
    log = changelog.compute_changelog(index, CUR)
    moved = by_kind(log, "moved")
    assert set(moved) == {"MOVER"}
    entry = moved["MOVER"]
    assert entry["old_freq_hz"] == 100.0e6
    assert entry["new_freq_hz"] == 100.3e6
    assert abs(entry["delta_hz"] - 300000.0) < 1.0
    assert entry["hearings_prior"] == 3
    assert entry["hearings"] == 3


def test_moved_requires_identity_not_resemblance(index):
    # Two different identities at nearby frequencies on either
    # side of the watch boundary: no move is matched by
    # resemblance, and the current one is simply new.
    for k in range(2):
        hear(index, "watch-a", "UNKNOWN-A", T0 + k * 60,
             freq=200.0e6)
    for k in range(2):
        hear(index, CUR, "UNKNOWN-B", T0 + DAY + k * 60,
             freq=200.3e6)
    log = changelog.compute_changelog(index, CUR)
    assert by_kind(log, "moved") == {}
    assert "UNKNOWN-B" in by_kind(log, "new")


def test_small_shift_is_not_a_move(index):
    for k in range(3):
        hear(index, "watch-a", "STEADY", T0 + k * 3600,
             freq=100.0e6, desk="trunking", kind="census")
    for k in range(3):
        hear(index, CUR, "STEADY", T0 + DAY + k * 3600,
             freq=100.01e6, desk="trunking", kind="census")
    log = changelog.compute_changelog(index, CUR)
    assert "STEADY" not in by_kind(log, "moved")


# --------------------------------------------------------------- rhythm

def _daily(index, watch, emitter, days, hour, freq=100e6):
    for d in days:
        hear(index, watch, emitter, MIDNIGHT + d * DAY + hour * 3600,
             freq=freq, desk="trunking", kind="census")


def test_rhythm_shifted(index):
    _daily(index, "watch-a", "RSH", range(0, 3), 8)
    _daily(index, "watch-b", "RSH", range(3, 6), 8)
    _daily(index, CUR, "RSH", range(10, 14), 14)
    log = changelog.compute_changelog(index, CUR)
    rhythm = by_kind(log, "rhythm-changed")
    assert set(rhythm) == {"RSH"}
    entry = rhythm["RSH"]
    assert entry["change"] == "shifted"
    assert entry["hearings_prior"] == 6
    assert entry["hearings"] == 4
    assert "08:30" in entry["before"]
    assert "14:30" in entry["after"]


def test_rhythm_appeared(index):
    scattered = (0, 5 * 3600, 11 * 3600, 17 * 3600, 23 * 3600,
                 3 * 3600)
    for d, off in enumerate(scattered):
        hear(index, "watch-a" if d < 3 else "watch-b", "RAP",
             MIDNIGHT + d * DAY + off, freq=100e6, desk="trunking",
             kind="census")
    _daily(index, CUR, "RAP", range(10, 14), 6)
    log = changelog.compute_changelog(index, CUR)
    rhythm = by_kind(log, "rhythm-changed")
    assert rhythm["RAP"]["change"] == "appeared"
    assert "no daily habit established" in rhythm["RAP"]["before"]


def test_rhythm_dissolved(index):
    _daily(index, "watch-a", "RDI", range(0, 3), 6)
    _daily(index, "watch-b", "RDI", range(3, 6), 6)
    for k, off in enumerate((3600, 7 * 3600, 13 * 3600, 20 * 3600)):
        hear(index, CUR, "RDI", MIDNIGHT + 10 * DAY + k * 9973 + off,
             freq=100e6, desk="trunking", kind="census")
    log = changelog.compute_changelog(index, CUR)
    rhythm = by_kind(log, "rhythm-changed")
    assert rhythm["RDI"]["change"] == "dissolved"
    assert "no daily habit established" in rhythm["RDI"]["after"]


def test_rhythm_thin_record_files_none(index):
    # Two prior hearings: fewer than the fold requires, so even
    # a perfectly regular current side claims no rhythm change.
    _daily(index, "watch-a", "RTH", range(0, 2), 6)
    _daily(index, CUR, "RTH", range(10, 14), 6)
    # And the mirror: a full prior habit, two current hearings.
    _daily(index, "watch-a", "RTH2", range(0, 3), 6)
    _daily(index, "watch-b", "RTH2", range(3, 6), 6)
    _daily(index, CUR, "RTH2", range(10, 12), 18)
    log = changelog.compute_changelog(index, CUR)
    rhythm = by_kind(log, "rhythm-changed")
    assert "RTH" not in rhythm
    assert "RTH2" not in rhythm


def test_rhythm_steady_habit_files_none(index):
    _daily(index, "watch-a", "RST", range(0, 3), 9)
    _daily(index, "watch-b", "RST", range(3, 6), 9)
    _daily(index, CUR, "RST", range(10, 14), 9)
    log = changelog.compute_changelog(index, CUR)
    assert "RST" not in by_kind(log, "rhythm-changed")


# ------------------------------------------- provisional and the account

def test_provisional_heading_before_three_watches(index):
    hear(index, "watch-a", "OLD", T0, freq=100e6)
    hear(index, CUR, "NEW1", T0 + DAY + 10, freq=100e6)
    hear(index, CUR, "NEW1", T0 + DAY + 20, freq=100e6)
    log = changelog.compute_changelog(index, CUR)
    assert log["baseline"]["watch_count"] == 1
    assert log["baseline"]["provisional"] is True
    section = "\n".join(changelog.render_section(log))
    assert "This account is provisional" in section
    # Two more earlier watches stand behind the baseline and
    # the heading clears, in the data and in the prose alike.
    hear(index, "watch-b", "OLD", T0 + 2 * DAY, freq=100e6)
    hear(index, "watch-c", "OLD", T0 + 3 * DAY, freq=100e6)
    log = changelog.compute_changelog(index, CUR)
    assert log["baseline"]["watch_count"] == 3
    assert log["baseline"]["provisional"] is False
    section = "\n".join(changelog.render_section(log))
    assert "This account is provisional" not in section
    assert "is established" in section


def test_json_shape_and_prose_carry_the_same_entries(index, home):
    for watch, base in (("watch-a", T0), ("watch-b", T0 + DAY)):
        hear(index, watch, "GONE", base + 10, freq=162.475e6,
             desk="wxradio", kind="voice")
        hear(index, watch, "GONE", base + 20, freq=162.475e6,
             desk="wxradio", kind="voice")
    dwell(index, CUR, T0 + 2 * DAY, 162.475e6, ["wxradio"])
    hear(index, CUR, "NEW1", T0 + 2 * DAY + 30, freq=121.5e6,
         desk="airband", kind="voice")
    hear(index, CUR, "NEW1", T0 + 2 * DAY + 40, freq=121.5e6,
         desk="airband", kind="voice")
    index.set_watch_meta(CUR, {
        "watch_id": CUR, "start": T0 + 2 * DAY,
        "planned_end": T0 + 3 * DAY, "end": T0 + 3 * DAY,
        "source": "simulated", "region": "the test region",
        "antenna": "test antenna", "disk_ceiling_gb": 20})
    log = products.write_changelog(index, CUR)
    path = os.path.join(index.watch_dir(CUR), "changelog.json")
    assert os.path.exists(path)
    filed = json.load(open(path))
    assert filed == log
    assert filed["format"] == 1
    assert filed["watch_id"] == CUR
    assert set(filed["baseline"]) >= {"watches", "watch_count",
                                      "provisional",
                                      "established_watches_required"}
    assert set(filed["counts"]) == {"new", "vanished", "moved",
                                    "rhythm-changed", "unwatched"}
    assert {(e["kind"], e["emitter"]) for e in filed["entries"]} == \
        {("new", "NEW1"), ("vanished", "GONE")}
    report = products.render_report(index, CUR)
    assert "## 5. Change log" in report
    assert "- new: NEW1" in report
    assert "- vanished: GONE" in report
    assert "1 new, 1 vanished, 0 moved, 0 rhythm-changed" in report
    assert util.EM_DASH not in report


def test_ask_changes_surface(index):
    hear(index, "watch-a", "OLD", T0, freq=100e6)
    hear(index, CUR, "NEW1", T0 + DAY + 10, freq=100e6)
    hear(index, CUR, "NEW1", T0 + DAY + 20, freq=100e6)
    products.write_changelog(index, CUR)
    text = products.ask(index, "changes")
    assert "Change log for watch %s" % CUR in text
    assert "1 new, 0 vanished, 0 moved, 0 rhythm-changed" in text
    assert "provisional" in text


# ------------------------------------------------------------- end to end

def _run_watch(home, profile, start_epoch, hours=1):
    profile["watch"]["hours"] = hours
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=start_epoch)
        assert result["ok"], result
        return result["watch_id"]
    finally:
        index.close()


def test_two_watch_run_files_second_watch_changelog(home, profile):
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    first = _run_watch(home, profile, T0)
    second = _run_watch(home, profile, T0 + 90000)
    wdir = os.path.join(home, "watches", second)
    path = os.path.join(wdir, "changelog.json")
    assert os.path.exists(path)
    log = json.load(open(path))
    assert log["watch_id"] == second
    assert log["format"] == 1
    assert first in log["baseline"]["watches"]
    assert log["baseline"]["watch_count"] >= 1
    assert log["baseline"]["provisional"] is True
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "## 5. Change log" in report
    assert "## 6. Anomaly chapter" in report
    assert util.EM_DASH not in report
