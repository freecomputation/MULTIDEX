"""0.14.0: the anomaly register - permanent first-class
records for the unexplained clusters that earn them, under
the doctrine: no anomaly without a characterized baseline,
capture not conclusion, every attempt filed, and a ceiling
of CANDIDATE stated exactly that way.

The bar is tested both ways, the statement is composed from
a real seeded record, sealing is proven against a shed pass,
resolution arrives by a retroactive decode with its link
filed, a resolved entry reopens on a fresh unexplained
hearing, and the special candidate path is exercised on a
fixture meeting the plan's strict trigger.
"""

import os

import pytest

from multidex import __version__, anomaly, gates, products
from multidex import retro, util
from multidex.events import make_event
from multidex.index import Index

from conftest import T0

CEILING = int(1e9)
F_SPECIAL = 28.83e6
F_OPEN = 50.1e6


# ------------------------------------------------------------ seeding

def hear(index, watch, t, freq=None, emitter=None, snr=18.0,
         desk="sweep"):
    index.add_event(make_event(
        watch, desk, "observation", t, t + 5.0, freq_hz=freq,
        emitter=emitter, grade="UNRESOLVED",
        content="seeded unknown hearing",
        data={"snr_db": snr, "shape": "unknown-burst"}))


def stare(index, watch, t, freq=None, emitter=None,
          family="unknown", silent=False):
    ev = make_event(
        watch, "probe", "observation", t, t + 600,
        freq_hz=freq, emitter=emitter, grade="UNRESOLVED",
        content="seeded probe stare",
        data={"classification": {"family": family,
                                 "features": {},
                                 "reasons": ["seeded stare"]},
              "snr_db": 18.0, "silent": silent})
    index.add_event(ev)
    return ev


def vote(index, watch, t, freq=None, emitter=None,
         grade="UNRESOLVED"):
    index.add_event(make_event(
        watch, "probe", "assessment", t, t, freq_hz=freq,
        emitter=emitter, grade=grade,
        content="seeded cluster vote",
        data={"rule": "probe_cluster_vote", "vote": {}}))


def negative(index, watch, t, freq=None, emitter=None,
             attempted="audiocensus"):
    index.add_event(make_event(
        watch, "probe", "assessment", t, t, freq_hz=freq,
        emitter=emitter, grade="CONFIRMED",
        content="negative result: %s attempted a decode and "
                "filed no decode" % attempted,
        data={"rule": "probe_negative_result",
              "attempted_desk": attempted,
              "outcome": "no decode",
              "detail": "seeded negative result from %s"
                        % attempted}))


def special_cluster(index, watch="watch-a"):
    """The strict-trigger fixture: three strong hearings
    spanning hours, a probe stare whose tree placed the
    emission in no family, a vote that stayed unresolved,
    and negative results from two desks."""
    for dt in (0, 4000, 8000):
        hear(index, watch, T0 + dt, freq=F_SPECIAL)
    stare(index, watch, T0 + 9000, freq=F_SPECIAL)
    vote(index, watch, T0 + 9600, freq=F_SPECIAL)
    negative(index, watch, T0 + 9600, freq=F_SPECIAL,
             attempted="audiocensus")
    negative(index, watch, T0 + 9600, freq=F_SPECIAL,
             attempted="amateur")


# ------------------------------------------------------------- surfaces

def test_version():
    assert __version__ == "0.15.0"


# ------------------------------------------------------------ entry bar

def test_probe_survived_cluster_enters(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    summary = anomaly.refresh_register(index, T0 + 1000)
    assert summary["created"] == 1
    rows = index.register_rows()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["state"] == anomaly.STATE_OPEN
    assert entry["hearings"] >= 3
    assert entry["cluster"]["kind"] == "frequency"
    # The three-part statement is composed from the record,
    # every part present and grounded in it.
    st = entry["statement"]
    assert st["resembles"] and "vote" in st["resembles"]
    assert st["is_not"] and "not explained by any decode" \
        in st["is_not"]
    assert st["would_resolve"] and "second, independent " \
        "station" in st["would_resolve"]
    assert entry["discrimination"]["recurrence"]["verdict"]
    # A second refresh files nothing new.
    summary2 = anomaly.refresh_register(index, T0 + 2000)
    assert summary2["created"] == 0
    assert len(index.register_rows()) == 1


def test_one_off_hearing_files_nothing(index):
    hear(index, "watch-a", T0, freq=F_OPEN)
    vote(index, "watch-a", T0 + 100, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    assert index.register_rows() == []


def test_unresolved_but_unsurvived_cluster_files_nothing(index):
    for dt in (0, 100, 200, 300):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    assert index.register_rows() == []


def test_explained_cluster_never_enters(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    index.add_event(make_event(
        "watch-a", "acars", "decode", T0 + 100, T0 + 105,
        freq_hz=F_OPEN, grade="CONFIRMED",
        content="decoded: it was a known frame all along",
        data={}))
    anomaly.refresh_register(index, T0 + 1000)
    assert index.register_rows() == []


def test_equivalent_body_of_negatives_enters(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, emitter="UNKN:1",
             freq=146.5e6)
    negative(index, "watch-a", T0 + 300, emitter="UNKN:1",
             attempted="audiocensus")
    negative(index, "watch-a", T0 + 300, emitter="UNKN:1",
             attempted="amateur")
    anomaly.refresh_register(index, T0 + 1000)
    rows = index.register_rows()
    assert len(rows) == 1
    assert rows[0]["cluster"]["kind"] == "dossier"
    assert rows[0]["cluster"]["emitter"] == "UNKN:1"
    assert len(rows[0]["negative_results"]) == 2


def test_single_desk_negatives_do_not_meet_the_bar(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    negative(index, "watch-a", T0 + 300, freq=F_OPEN,
             attempted="audiocensus")
    negative(index, "watch-a", T0 + 400, freq=F_OPEN,
             attempted="audiocensus")
    anomaly.refresh_register(index, T0 + 1000)
    assert index.register_rows() == []


# --------------------------------------------------------------- sealing

def test_registered_evidence_is_sealed_and_survives_shedding(
        index, home):
    watch = "watch-a"
    # An unregistered bulk snippet, filed first.
    rel_bulk, _ = index.keep_snippet(
        watch, index.watch_dir(watch), "satellite", "satellite",
        "bulk.wav", b"B" * 100, CEILING, t=T0, home_wide=True)
    # The registered cluster's retained evidence.
    rel_ev, _ = index.keep_snippet(
        watch, index.watch_dir(watch), "sweep", "voice",
        "evidence.wav", b"E" * 100, CEILING, t=T0 + 1,
        home_wide=True)
    ev = make_event(watch, "sweep", "observation", T0, T0 + 5,
                    freq_hz=F_OPEN, grade="UNRESOLVED",
                    content="hearing with evidence",
                    data={"snr_db": 18.0, "shape": "unknown"},
                    snippet=rel_ev)
    index.add_event(ev)
    hear(index, watch, T0 + 100, freq=F_OPEN)
    hear(index, watch, T0 + 200, freq=F_OPEN)
    vote(index, watch, T0 + 300, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    rows = index.register_rows()
    assert len(rows) == 1
    ev_row = [r for r in index.snippet_rows(watch)
              if r["path"] == rel_ev][0]
    assert rows[0]["evidence"] == [ev_row["id"]]
    assert ev_row["sealed"] == 1
    acct = anomaly.register_account(index)
    assert acct["sealed_bytes"] == 100
    # A shed pass under a tight ceiling takes the unregistered
    # bulk file and leaves the sealed evidence standing.
    rel_new, shed = index.keep_snippet(
        watch, index.watch_dir(watch), "sweep", "voice",
        "new.wav", b"N" * 100, 250, t=T0 + 2000, home_wide=True)
    assert rel_new
    shed_paths = {s["path"] for s in shed}
    assert rel_bulk in shed_paths
    assert rel_ev not in shed_paths
    ev_row = [r for r in index.snippet_rows(watch)
              if r["path"] == rel_ev][0]
    assert ev_row["shed"] == 0 and ev_row["sealed"] == 1


# ------------------------------------------- resolution and reopening

def test_resolution_by_plain_decode_at_refresh(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    assert index.register_rows()[0]["state"] == \
        anomaly.STATE_OPEN
    dec = make_event("watch-a", "acars", "decode", T0 + 100,
                     T0 + 105, freq_hz=F_OPEN, grade="CONFIRMED",
                     content="decoded: a known frame", data={})
    index.add_event(dec)
    summary = anomaly.refresh_register(index, T0 + 2000)
    assert summary["resolved"] == 1
    entry = index.register_rows()[0]
    assert entry["state"] == anomaly.STATE_RESOLVED
    assert entry["resolved_event"] == dec["id"]
    # The report chapter notes the resolution since the last
    # watch, from the entry itself.
    index.set_watch_meta("watch-a", {"start": T0,
                                     "end": T0 + 3000})
    section = "\n".join(anomaly.render_section(index, "watch-a"))
    assert "Resolutions since the last watch" in section
    assert dec["id"] in section


def test_reopening_on_fresh_unexplained_hearing(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    index.add_event(make_event(
        "watch-a", "acars", "decode", T0 + 100, T0 + 105,
        freq_hz=F_OPEN, grade="CONFIRMED",
        content="decoded: a known frame", data={}))
    anomaly.refresh_register(index, T0 + 2000)
    assert index.register_rows()[0]["state"] == \
        anomaly.STATE_RESOLVED
    # A fresh hearing the old decode does not answer.
    hear(index, "watch-b", T0 + 25000, freq=F_OPEN)
    summary = anomaly.refresh_register(index, T0 + 26000)
    assert summary["reopened"] == 1
    entry = index.register_rows()[0]
    assert entry["state"] == anomaly.STATE_OPEN
    assert entry["reopened_at"] == T0 + 26000
    assert any("reopened" in h["note"] for h in entry["history"])


# --------------------------------------- retroactive resolution hook

@pytest.fixture()
def register_decoder():
    made = []

    def _reg(name, desk, version, kinds, run, **kw):
        entry = retro.register(name, desk, version, kinds, run,
                               **kw)
        made.append(entry)
        return entry

    yield _reg
    for entry in made:
        retro.DECODERS.remove(entry)


def _magic_run(ctx):
    if not ctx["data"].startswith(b"MAGIC"):
        return []
    ev0 = ctx["event"]
    return [make_event(
        ctx["watch_id"], "sweep", "decode", ev0["t_start"],
        ev0["t_end"], freq_hz=ev0.get("freq_hz"),
        emitter=ev0.get("emitter"), grade="CONFIRMED",
        content="the test decoder found the magic pattern",
        data={"emitter_kind": "test_signal"})]


def test_retroactive_decode_resolves_registered_cluster(
        index, home, register_decoder):
    entry_dec = register_decoder(
        "test-magic", "sweep", "1", ("voice",), _magic_run)
    watch = "watch-old"
    rel, _ = index.keep_snippet(
        watch, index.watch_dir(watch), "sweep", "voice",
        "capture.wav", b"MAGIC pattern bytes", CEILING, t=T0,
        home_wide=True)
    index.add_event(make_event(
        watch, "sweep", "observation", T0, T0 + 5,
        freq_hz=100e6, emitter="TEST:9", grade="UNRESOLVED",
        content="seeded unknown hearing",
        data={"snr_db": 18.0, "shape": "unknown"}, snippet=rel))
    hear(index, watch, T0 + 100, emitter="TEST:9", freq=100e6)
    hear(index, watch, T0 + 200, emitter="TEST:9", freq=100e6)
    vote(index, watch, T0 + 300, emitter="TEST:9", freq=100e6)
    anomaly.refresh_register(index, T0 + 1000)
    rows = index.register_rows()
    assert len(rows) == 1
    assert rows[0]["state"] == anomaly.STATE_OPEN
    # The decoder improvement lands; the pass files its decode
    # and the register entry closes with it, at that moment.
    rp = retro.RetroPass(index, home, decoders=[entry_dec])
    account = rp.step(T0 + 5000)
    assert account["found"] == 1
    decodes = index.events(watch_id=watch, kinds=["decode"])
    assert len(decodes) == 1
    entry = index.register_rows()[0]
    assert entry["state"] == anomaly.STATE_RESOLVED
    assert entry["resolved_event"] == decodes[0]["id"]
    assert "retroactive" in entry["resolved_via"]
    assert decodes[0]["data"]["retroactive"] is True
    # The Watch Report's anomaly account states the
    # resolution, from the entry.
    index.set_watch_meta(watch, {"start": T0, "end": T0 + 9000})
    section = "\n".join(anomaly.render_section(index, watch))
    assert "retroactive decode" in section
    assert decodes[0]["id"] in section


# --------------------------------------------------- special candidate

def test_special_candidate_path(index, home):
    special_cluster(index)
    # An ordinary open entry at another frequency, for the
    # chapter's ordering: special candidates first.
    for dt in (0, 100):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    summary = anomaly.refresh_register(index, T0 + 20000)
    assert summary["created"] == 2
    assert len(summary["special_new"]) == 1
    special = summary["special_new"][0]
    assert special["state"] == anomaly.STATE_SPECIAL
    assert all(c["outcome"] == "stands"
               for c in special["self_refutation"])
    assert len(special["self_refutation"]) >= 5
    # Its distinct alert product, separate from the FLASH
    # line, worded as a candidate and nothing more.
    name = anomaly.write_special_alert(index, "watch-a", special)
    assert name.startswith("SPECIAL-CANDIDATE-")
    text = open(os.path.join(home, "watches", "watch-a",
                             name)).read()
    assert special["id"] in text
    assert "candidate" in text.lower()
    # No surface of the register graduates past candidate
    # language.
    section = "\n".join(anomaly.render_section(index, "watch-a"))
    full = anomaly.entry_text(special)
    for surface in (text, section, full):
        assert "CONFIRMED" not in surface
        assert "contact" not in surface.lower()
        assert "discover" not in surface.lower()
    # Special candidates render before ordinary open entries.
    lines = [ln for ln in section.splitlines()
             if ln.startswith("- an-")]
    assert len(lines) == 2
    assert "SPECIAL CANDIDATE" in lines[0]
    assert "SPECIAL CANDIDATE" not in lines[1]


def test_special_trigger_fails_without_structure(index):
    # The same cluster, but the stare was silent: no
    # structure on record, so the trigger cannot fire and
    # the entry stands as an ordinary open one.
    for dt in (0, 4000, 8000):
        hear(index, "watch-a", T0 + dt, freq=F_SPECIAL)
    stare(index, "watch-a", T0 + 9000, freq=F_SPECIAL,
          silent=True)
    vote(index, "watch-a", T0 + 9600, freq=F_SPECIAL)
    negative(index, "watch-a", T0 + 9600, freq=F_SPECIAL,
             attempted="audiocensus")
    negative(index, "watch-a", T0 + 9600, freq=F_SPECIAL,
             attempted="amateur")
    summary = anomaly.refresh_register(index, T0 + 20000)
    assert summary["special_new"] == []
    assert index.register_rows()[0]["state"] == \
        anomaly.STATE_OPEN


# ------------------------------------------- discrimination refreshed

def test_discrimination_refreshed_as_hearings_accrue(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    negative(index, "watch-a", T0 + 300, freq=F_OPEN,
             attempted="audiocensus")
    negative(index, "watch-a", T0 + 300, freq=F_OPEN,
             attempted="amateur")
    anomaly.refresh_register(index, T0 + 1000)
    entry = index.register_rows()[0]
    assert entry["hearings"] == 3
    assert entry["discrimination"]["hearings_judged"] == 3
    first_verdict = entry["discrimination"]["recurrence"][
        "verdict"]
    hear(index, "watch-a", T0 + 400, freq=F_OPEN)
    hear(index, "watch-a", T0 + 500, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 2000)
    entry = index.register_rows()[0]
    assert entry["hearings"] == 5
    assert entry["discrimination"]["hearings_judged"] == 5
    assert entry["last_seen"] == T0 + 500


# ------------------------------------------------------------- surfaces

def test_report_and_ask_render_the_same_register(index):
    special_cluster(index)
    anomaly.refresh_register(index, T0 + 20000)
    entry = index.register_rows()[0]
    index.set_watch_meta("watch-a", {"start": T0,
                                     "end": T0 + 30000,
                                     "planned_end": T0 + 30000,
                                     "source": "simulated"})
    report = products.render_report(index, "watch-a")
    assert "## 6. Anomaly chapter" in report
    assert entry["id"] in report
    assert "Resembles:" in report
    listing = products.ask(index, "anomalies")
    assert entry["id"] in listing
    assert "special candidate" in listing.lower()
    full = products.ask(index, "anomaly %s" % entry["id"])
    assert entry["statement"]["resembles"] in full
    assert entry["statement"]["would_resolve"] in full
    by_freq = products.ask(index, "anomaly 28.83 MHz")
    assert entry["id"] in by_freq


def test_register_survives_an_index_rebuild(index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    assert len(index.register_rows()) == 1
    index.rebuild()
    rows = index.register_rows()
    assert len(rows) == 1
    assert rows[0]["statement"]["resembles"]


def test_doctor_states_register_size_and_sealed_bytes(
        profile, home, index):
    for dt in (0, 100, 200):
        hear(index, "watch-a", T0 + dt, freq=F_OPEN)
    vote(index, "watch-a", T0 + 300, freq=F_OPEN)
    anomaly.refresh_register(index, T0 + 1000)
    text = gates.doctor_text(profile, home)
    assert "Anomaly register: 1 entries" in text
    assert "sealed" in text


def test_simulated_watch_stands_down_with_register_chapter(
        home, profile):
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 2
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    from multidex.officer import WatchRunner
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        report = open(os.path.join(
            home, "watches", result["watch_id"],
            "REPORT.md")).read()
        assert "## 6. Anomaly chapter" in report
        assert "anomaly register" in report.lower()
    finally:
        index.close()
