"""The Index: write-time grade enforcement, dossiers and alias
merging, rebuild from logs, baselines, and the snippet ceiling."""

import os

import pytest

from multidex.events import make_event

from conftest import T0

W = "watch-test"


def _ev(**kw):
    base = dict(watch_id=W, desk="adsb", kind="decode", t_start=T0,
                grade="CONFIRMED")
    base.update(kw)
    return make_event(**base)


def test_grade_enforced_at_write(index):
    bad = _ev()
    bad["grade"] = "CERTAIN"
    with pytest.raises(ValueError):
        index.add_event(bad)
    bad2 = _ev()
    bad2["kind"] = "rumor"
    with pytest.raises(ValueError):
        index.add_event(bad2)
    with pytest.raises(ValueError):
        make_event(W, "adsb", "decode", T0, grade="HIGH")


def test_events_round_trip(index):
    index.add_event(_ev(content="hello"))
    assert index.count_events(watch_id=W) == 1
    rows = index.events(watch_id=W)
    assert rows[0]["content"] == "hello"


def test_dossier_alias_merge_across_desks(index):
    # ADS-B identity: emitter ICAO, alias flight callsign.
    index.add_event(_ev(emitter="4840D6", data={
        "emitter_kind": "aircraft", "also": ["KLM1023"]}))
    # ACARS: emitter tail, alias the same flight callsign.
    index.add_event(_ev(desk="acars", emitter="PH-BXA", data={
        "emitter_kind": "aircraft", "also": ["KLM1023"]}))
    dossiers = index.all_dossiers()
    assert len(dossiers) == 1
    d = dossiers[0]
    assert set(d["aliases"]) >= {"4840D6", "PH-BXA", "KLM1023"}
    assert d["events_count"] == 2
    # Querying by any alias finds both events.
    assert len(index.events(emitter="PH-BXA")) == 2
    assert index.dossier("KLM1023")["key"] == d["key"]


def test_rebuild_from_logs(index, home):
    index.add_event(_ev(emitter="4840D6", data={
        "emitter_kind": "aircraft", "also": ["KLM1023"]}))
    index.add_event(_ev(desk="ais", emitter="370123456", data={
        "emitter_kind": "vessel"}))
    n = index.rebuild()
    assert n == 2
    assert index.count_events() == 2
    assert len(index.all_dossiers()) == 2


def test_baselines_provisional_until_three_watches(index):
    index.put_baseline("desk:adsb", 12, 1, {"events_mean": 10})
    base = index.get_baseline("desk:adsb", 12)
    assert base["provisional"] is True
    index.put_baseline("desk:adsb", 12, 3, {"events_mean": 10})
    assert index.get_baseline("desk:adsb", 12)["provisional"] is False
    assert index.get_baseline("desk:adsb", 13) is None


def test_snippet_ceiling_sheds_oldest(index, home):
    wdir = index.watch_dir(W)
    ceiling = 3000
    kept = []
    for i in range(5):
        rel, shed = index.keep_snippet(
            W, wdir, "airband", "voice", "v%d.wav" % i, b"x" * 1000,
            ceiling)
        if rel:
            kept.append(rel)
    account = index.snippet_accounting(W)
    assert account["kept_bytes"] <= ceiling
    assert account["shed_count"] >= 2
    # The survivors on disk are the newest files, and the accounting
    # agrees with the disk.
    survivors = [rel for rel in kept
                 if os.path.exists(os.path.join(home, "watches", W,
                                                rel))]
    assert account["kept_count"] == len(survivors) == 3
    assert survivors == kept[-3:]


def test_snippet_count_cap(index, home):
    wdir = index.watch_dir(W)
    rel, _ = index.keep_snippet(W, wdir, "airband", "voice", "a.wav",
                                b"x" * 10, 10 ** 9, count_cap=1)
    assert rel
    rel2, _ = index.keep_snippet(W, wdir, "airband", "voice", "b.wav",
                                 b"x" * 10, 10 ** 9, count_cap=1)
    assert rel2 is None


def test_regulars_need_two_watches(index):
    index.add_event(_ev(emitter="4840D6", data={"emitter_kind": "x"}))
    assert index.regulars() == []
    ev2 = _ev(watch_id="watch-other", emitter="4840D6",
              data={"emitter_kind": "x"})
    index.add_event(ev2)
    assert len(index.regulars()) == 1
