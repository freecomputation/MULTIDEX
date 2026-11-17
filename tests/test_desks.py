"""Desk workers against simulated feeds, called directly."""

from multidex import desks
from multidex.sim import SimSource

from conftest import T0


def _ctx(profile, desk, t0=T0, t1=T0 + 300, **kw):
    ctx = {"watch_id": "watch-t", "desk": desk, "t0": t0, "t1": t1,
           "freq_hz": 0.0, "home": (profile["home"]["lat"],
                                    profile["home"]["lon"]),
           "profile": profile, "pass": None}
    ctx.update(kw)
    return ctx


def _source(profile):
    return SimSource(profile, T0)


def test_adsb_desk_decodes_real_frames(profile):
    src = _source(profile)
    feed = src.capture(1090e6, 2.4e6, T0, T0 + 150,
                       [{"desk": "adsb", "freq_hz": 1090e6,
                         "bw_hz": 2e6}])["adsb"]
    assert feed["frames"], "simulator must put frames on the wire"
    desks.reset_state()
    events = desks.process_adsb(feed, _ctx(profile, "adsb"))
    kinds = {e["kind"] for e in events}
    assert "decode" in kinds and "telemetry" in kinds
    positions = [e for e in events if e["data"].get("lat") is not None]
    assert positions, "CPR pairing inside one dwell must yield positions"
    assert all(e["grade"] in ("CONFIRMED", "PROBABLE") for e in events)
    assert all(e["emitter"] for e in events)


def test_ais_desk(profile):
    src = _source(profile)
    feed = src.capture(162.3e6, 2.4e6, T0, T0 + 300,
                       [{"desk": "ais", "freq_hz": 162.025e6,
                         "bw_hz": 25e3}])["ais"]
    events = desks.process_ais(feed, _ctx(profile, "ais"))
    assert events
    assert all(e["data"]["emitter_kind"] == "vessel" for e in events)
    assert all(e["grade"] == "CONFIRMED" for e in events)


def test_acars_desk_links_tail_and_flight(profile):
    feed = {"messages": ["ACARS MODE 2 LABEL Q0 TAIL N842US FLIGHT "
                         "DL441 MSG HELLO"]}
    events = desks.process_acars(feed, _ctx(profile, "acars"))
    assert len(events) == 1
    assert events[0]["emitter"] == "N842US"
    assert events[0]["data"]["also"] == ["DL441"]
    assert events[0]["content"] == "HELLO"


def test_pager_desk_decodes_pocsag(profile):
    src = _source(profile)
    feed = src.capture(929.6125e6, 2.4e6, T0, T0 + 300,
                       [{"desk": "pager", "freq_hz": 929.6125e6,
                         "bw_hz": 25e3}])["pager"]
    events = desks.process_pager(feed, _ctx(profile, "pager"))
    for ev in events:
        assert ev["emitter"].startswith("PAGER:")
        assert ev["content"]
        assert ev["data"]["broadcast"] is True


def test_wxradio_same_alert_flagged(profile):
    from multidex import codecs
    feed = {"present": True, "freq_hz": 162.475e6,
            "same": [codecs.same_build("SVR", ["020091"], "KXXX")]}
    events = desks.process_wxradio(feed, _ctx(profile, "wxradio"))
    alerts = [e for e in events if e["kind"] == "alert"]
    assert len(alerts) == 1
    assert alerts[0]["data"]["flash"] is True
    assert "Severe Thunderstorm Warning" in alerts[0]["content"]


def test_sweep_desk_marks_unknowns(profile):
    feed = {"noise_floor_db": -112.0, "emissions": [
        {"freq_hz": 462.5e6, "bw_hz": 25e3, "peak_dbm": -58.0,
         "shape": "unknown-chirp", "label": None},
        {"freq_hz": 98.5e6, "bw_hz": 200e3, "peak_dbm": -52.0,
         "shape": "fm-broadcast", "label": "KFDI"}]}
    events = desks.process_sweep(feed, _ctx(profile, "sweep"))
    assert len(events) == 2
    unknown = events[0]
    assert unknown["grade"] == "UNRESOLVED"
    assert unknown["data"]["unknown_shape"] is True
    assert unknown["data"]["interrupt_candidate"] is True
    assert events[1]["grade"] == "PROBABLE"


def test_satellite_desk_lock_and_miss(profile):
    ok = desks.process_satellite(
        {"lock": True, "quality": 0.8, "max_elev_deg": 55.0,
         "sat": "NOAA 19", "norad": "33591"},
        _ctx(profile, "satellite",
             **{"pass": {"aos": T0, "los": T0 + 600, "t_max": T0 + 300,
                         "max_elev_deg": 55.0}}))
    assert ok[0]["kind"] == "pass" and ok[0]["grade"] == "CONFIRMED"
    assert ok[0]["data"]["_snippet"]["kind"] == "satellite"
    miss = desks.process_satellite(
        {"lock": False, "quality": 0.0, "max_elev_deg": 4.0,
         "sat": "NOAA 19", "norad": "33591"}, _ctx(profile, "satellite"))
    assert miss[0]["grade"] == "UNRESOLVED"
    assert miss[0]["data"]["failure"] == "no lock"


def test_sonde_desk_completion(profile):
    feed = {"frames": [
        {"serial": "S12345", "lat": 39.5, "lon": -98.4, "alt_m": 9000,
         "burst": False, "site": "North site"},
        {"serial": "S12345", "lat": 39.7, "lon": -98.0, "alt_m": 29800,
         "burst": True, "site": "North site"}]}
    events = desks.process_sonde(feed, _ctx(profile, "sonde"))
    passes = [e for e in events if e["kind"] == "pass"]
    assert len(passes) == 1
    assert passes[0]["data"]["flash"] is True


def test_tier3_desks_are_metadata_only(profile):
    cells = desks.process_cellular(
        {"cells": [{"cell_id": "LTE-1", "band": "LTE B12",
                    "freq_hz": 751.5e6, "rsrp_dbm": -80.0}]},
        _ctx(profile, "cellular"))
    assert cells[0]["data"]["metadata_only"] is True
    assert cells[0]["kind"] == "census"


def test_benched_desks_carry_reasons(profile):
    for name, reason in desks.ALWAYS_BENCHED.items():
        state, why = desks.desk_state(name, profile, "simulated", None)
        assert state == "benched"
        assert why == reason and len(why) > 20
    state, _ = desks.desk_state("adsb", profile, "simulated", None)
    assert state == "standing"
    state, why = desks.desk_state("ais", profile, "real", {"sweep"})
    assert state == "benched" and "real" in why
