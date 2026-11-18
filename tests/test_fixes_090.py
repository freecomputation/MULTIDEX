"""0.9.0 applicability: desks stand down where their targets
cannot exist within reach of the home.

All positions used here are synthetic reference positions. The
inland one is the shipped profile's own reference position in
the central United States; the coastal and overseas ones are
round-number points chosen against the shipped grid.
"""

import os

import pytest

from multidex import applicability, gates, products, util
from multidex.desks import SPECS
from multidex.index import Index
from multidex.officer import WatchRunner, build_bill
from multidex.profile import ProfileError, load_profile

from conftest import T0

INLAND = (39.0, -98.0)        # the shipped reference position
COASTAL = (29.9, -90.0)       # synthetic Gulf coast point
OVERSEAS = (51.5, -0.12)      # synthetic point outside US/Canada
MARGIN = (31.2, -90.0)        # raw grid distance just over 100 km
BEYOND = (31.6, -90.0)        # well beyond the range


def _at(profile, lat, lon):
    profile["home"]["lat"] = lat
    profile["home"]["lon"] = lon
    return profile


def _bill(profile):
    sats, _ = gates.load_sats(profile)
    return build_bill(profile, T0, sats)


def test_inland_home_stands_ais_down(profile):
    decisions = applicability.evaluate(profile)
    # Every desk in the roster carries an explicit decision.
    assert set(decisions) == set(SPECS)
    ais = decisions["ais"]
    assert not ais["stands"] and ais["by"] == "test"
    # The reason states the distance and the range.
    assert "1055 km" in ais["reason"]
    assert "100 km applicability range" in ais["reason"]
    # Weather radio stands at the same home: it is inside the
    # service area the grid draws.
    assert decisions["wxradio"]["stands"]
    # The radiosonde desk carries no test in this build and
    # stands, with the gap stated in its decision.
    assert decisions["sonde"]["stands"]
    assert decisions["sonde"]["by"] == "no test"


def test_inland_bill_bills_no_ais_but_marine_flies(profile):
    bill = _bill(profile)
    assert all("ais" not in item["desks"] for item in bill)
    marine = [i for i in bill if i["label"] == "Rotation marine"]
    assert marine, "the marine rotation must still fly"
    assert all(i["desks"] == ["wxradio"] for i in marine)


def test_coastal_home_keeps_ais_standing(profile):
    _at(profile, *COASTAL)
    decisions = applicability.evaluate(profile)
    assert decisions["ais"]["stands"]
    bill = _bill(profile)
    marine = [i for i in bill if i["label"] == "Rotation marine"]
    assert marine
    assert all(i["desks"] == ["ais", "wxradio"] for i in marine)


def test_overseas_home_stands_wxradio_down_only(profile):
    _at(profile, *OVERSEAS)
    decisions = applicability.evaluate(profile)
    wx = decisions["wxradio"]
    assert not wx["stands"] and wx["by"] == "test"
    assert "service area" in wx["reason"]
    # The same home is within reach of water, so AIS stands:
    # the two tests are independent.
    assert decisions["ais"]["stands"]


def test_grid_uncertainty_fails_toward_coverage(profile):
    # The raw distance to the nearest water cell at the margin
    # point is just over the 100 km range; with the grid's
    # uncertainty deducted, as the module contract states, the
    # desk stands. Narrowing the range past the deducted
    # distance stands it down.
    _at(profile, *MARGIN)
    assert applicability.evaluate(profile)["ais"]["stands"]
    profile["applicability"]["marine_range_km"] = 80
    narrowed = applicability.evaluate(profile)["ais"]
    assert not narrowed["stands"]
    assert "80 km applicability range" in narrowed["reason"]
    _at(profile, *BEYOND)
    profile["applicability"]["marine_range_km"] = 100
    assert not applicability.evaluate(profile)["ais"]["stands"]


def test_missing_position_fails_toward_coverage(profile):
    profile["home"]["lat"] = None
    decisions = applicability.evaluate(profile)
    assert all(d["stands"] for d in decisions.values())
    assert decisions["ais"]["by"] == "no position"
    profile["home"]["lat"] = 0.0
    profile["home"]["lon"] = 0.0
    decisions = applicability.evaluate(profile)
    assert all(d["stands"] for d in decisions.values())


def test_dataset_edges_do_not_raise(profile):
    for lat, lon in ((89.5, 180.0), (-89.5, -180.0), (0.5, 179.9),
                     (-0.5, -179.9)):
        _at(profile, lat, lon)
        decisions = applicability.evaluate(profile)
        assert set(decisions) == set(SPECS)


def test_override_forces_stand_down(profile):
    profile["applicability"]["force_stand_down"] = ["adsb"]
    decisions = applicability.evaluate(profile)
    adsb = decisions["adsb"]
    assert not adsb["stands"] and adsb["by"] == "override"
    assert "force_stand_down" in adsb["reason"]
    bill = _bill(profile)
    assert all("adsb" not in item["desks"] for item in bill)


def test_override_forces_stand(profile):
    profile["applicability"]["force_stand"] = ["ais"]
    decisions = applicability.evaluate(profile)
    ais = decisions["ais"]
    assert ais["stands"] and ais["by"] == "override"
    assert "force_stand" in ais["reason"]
    bill = _bill(profile)
    marine = [i for i in bill if i["label"] == "Rotation marine"]
    assert marine and all("ais" in i["desks"] for i in marine)


def test_profile_validation_refuses_bad_applicability(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[applicability]\nforce_stand = ["nosuch"]\n')
    with pytest.raises(ProfileError):
        load_profile(str(path))
    path.write_text('[applicability]\nmarine_range_km = -5\n')
    with pytest.raises(ProfileError):
        load_profile(str(path))
    path.write_text('[applicability]\nforce_stand = ["ais"]\n'
                    'force_stand_down = ["ais"]\n')
    with pytest.raises(ProfileError):
        load_profile(str(path))


def test_doctor_states_stood_down_desks(profile, home):
    text = gates.doctor_text(profile, home)
    ais_line = [ln for ln in text.splitlines()
                if ln.startswith("- ais ")][0]
    assert "stood down" in ais_line and "1055 km" in ais_line
    wx_line = [ln for ln in text.splitlines()
               if ln.startswith("- wxradio ")][0]
    assert "standing" in wx_line and "stood down" not in wx_line


def test_status_states_stood_down_desks(profile, home):
    index = Index(home)
    try:
        watch_id = "watch-20261117-120000"
        wdir = index.watch_dir(watch_id)
        reason = applicability.evaluate(profile)["ais"]["reason"]
        index.set_watch_meta(watch_id, {
            "watch_id": watch_id, "start": T0,
            "planned_end": T0 + 86400, "source": "simulated",
            "stood_down": {"ais": reason}})
        util.write_json(os.path.join(wdir, "state.json"), {
            "next_index": 3, "elapsed_s": 600.0,
            "current": "Rotation marine (wxradio)",
            "events": 10, "dossiers": 2, "flash": 0,
            "benched": [], "stood_down": {"ais": reason},
            "complete": False, "pid": os.getpid(),
            "heartbeat_s": 600.0, "executing": None})
        util.write_json(os.path.join(home, "current_watch"), watch_id)
    finally:
        index.close()
    text = products.status_text(home)
    assert "Stood down: ais - " in text
    assert "1055 km" in text
    # The benched account does not claim the desk: the two
    # states stay distinct on the surface.
    assert "benched: ais" not in text


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


def test_watch_records_applicability(home, profile):
    watch_id = _run_watch(home, profile)
    wdir = os.path.join(home, "watches", watch_id)
    bill = util.read_json(os.path.join(wdir, "bill.json"))
    assert all("ais" not in i["desks"] for i in bill)
    marine = [i for i in bill if i["label"] == "Rotation marine"]
    assert marine and all(i["desks"] == ["wxradio"] for i in marine)
    # No grant was drawn for the stood-down desk: grants ride
    # bill items, and every grant's desk list is ais-free, while
    # the marine items hold grants in wxradio's name.
    budget_doc = util.read_json(os.path.join(wdir, "budget.json"))
    grants = budget_doc["grants"]
    assert grants
    assert all("ais" not in (g.get("desks") or []) for g in grants)
    marine_ids = {i["id"] for i in marine}
    assert any(g["bill_id"] in marine_ids for g in grants)
    # The state and the meta carry the account.
    state = util.read_json(os.path.join(wdir, "state.json"))
    assert "ais" in state["stood_down"]
    meta = util.read_json(os.path.join(wdir, "watch.json"))
    assert "ais" in meta["stood_down"]
    index = Index(home)
    try:
        health = index.events(watch_id=watch_id,
                              kinds=["desk_health"], limit=10000)
        down = [e for e in health
                if e["data"].get("stood_down")
                and e["data"].get("desk") == "ais"]
        assert down and "stood down at muster" in down[0]["content"]
        counts = index.counts_by_desk(watch_id)
        assert counts.get("ais", 0) == 0
        assert counts.get("wxradio", 0) > 0
    finally:
        index.close()
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "desk ais stood down at muster" in report
    assert "The ais desk stood down at this home" in report
    assert "While ais was benched" not in report
