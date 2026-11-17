"""TLE parsing and the simplified propagator."""

import os

from multidex import orbit

from conftest import PROFILES, T0


def _sats():
    with open(os.path.join(PROFILES, "tle.txt")) as fh:
        return orbit.parse_tle(fh.read())


def test_tle_parse():
    sats = _sats()
    assert len(sats) == 4
    noaa = [s for s in sats if s["name"] == "NOAA 19"][0]
    assert noaa["norad"] == "33591"
    assert abs(noaa["incl_deg"] - 99.19) < 0.01
    assert abs(noaa["mean_motion"] - 14.124) < 0.001


def test_propagator_altitude_sane():
    sats = _sats()
    lat, lon, alt = orbit.propagate(sats[0], T0)
    assert 780 < alt < 900  # NOAA-class orbit
    assert -90 <= lat <= 90 and -180 <= lon <= 180


def test_pass_prediction_finds_passes():
    sats = _sats()
    passes = orbit.all_passes(sats, 39.0, -98.0, T0, T0 + 86400)
    assert len(passes) >= 8
    for p in passes:
        assert p["los"] > p["aos"]
        assert p["max_elev_deg"] >= 10.0
    assert passes == sorted(passes, key=lambda p: p["aos"])


def test_tle_age():
    sats = _sats()
    age = orbit.tle_age_days(sats, T0)
    assert age is not None and abs(age) < 1.0
    assert orbit.tle_age_days([], T0) is None
