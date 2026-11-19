"""0.2.0 airband repair: every served channel inside a captured
window, the verified local channel set, and the multi-window
rotation cycle."""

from multidex import gates
from multidex.channelizer import plan
from multidex.desks import AIRBAND_CHANNELS, MILAIR_CHANNELS, channels_for
from multidex.officer import ROTATION, build_bill

from conftest import T0


def _windows(name):
    centers = ROTATION[name][0]
    return centers if isinstance(centers, tuple) else (centers,)


def _assert_all_channels_covered(slot, desk):
    bw = ROTATION[slot][1]
    covered = set()
    for center in _windows(slot):
        vfos, dropped = plan(center, bw, {desk: channels_for(desk)})
        covered.update(v["freq_hz"] for v in vfos)
    want = {f for f, _bw in channels_for(desk)}
    assert covered == want, (
        "%s channels never inside a %s window: %s"
        % (desk, slot, sorted(want - covered)))


def test_airband_channels_all_inside_rotation_windows():
    _assert_all_channels_covered("airband", "airband")


def test_milair_channels_all_inside_rotation_windows():
    _assert_all_channels_covered("milair", "milair")


def test_airband_set_is_the_verified_local_set():
    mhz = {round(f / 1e6, 3) for f in AIRBAND_CHANNELS}
    # Cairns sectors, tower, clearance, ops, guard (0.2.0 research).
    assert {118.075, 121.1, 121.5, 125.4, 126.2, 133.45, 133.75,
            135.2} <= mhz
    # The retired 0.1.x list held no sector frequency at all.
    for stale in (118.7, 119.1, 124.35, 126.075, 127.8):
        assert stale not in mhz


def test_milair_set_carries_uhf_pairs_and_guard():
    mhz = {round(f / 1e6, 3) for f in MILAIR_CHANNELS}
    assert {239.275, 243.0, 248.55, 270.35, 319.25, 327.125,
            336.3, 371.35} <= mhz


def test_bill_cycles_airband_windows(profile):
    sats, _ = gates.load_sats(profile)
    bill = build_bill(profile, T0, sats)
    centers = [i["center_hz"] for i in bill
               if i["label"] == "Rotation airband"]
    assert len(centers) >= 4
    assert set(centers[:4]) == set(_windows("airband")), (
        "the first airband cycle must visit every window, got %s"
        % (centers[:4],))


def test_aircraft_cadence_is_minutes_not_an_hour(profile):
    import statistics
    sats, _ = gates.load_sats(profile)
    bill = build_bill(profile, T0, sats)
    starts = [i["start"] for i in bill
              if i["label"] in ("Rotation airband", "Rotation adsb",
                                "Rotation milair")]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps and statistics.median(gaps) < 900, (
        "aircraft dwells must be minutes apart in the median; "
        "median gap %s" % (statistics.median(gaps),))
    # The only long aircraft silences allowed are designed
    # occupation: the deep-sweep block (01:00-04:00), where
    # census sweeps own the tuner by design, and spans where
    # Class A clock items - satellite passes, sonde windows,
    # and (0.15.0) predicted Jupiter windows - own it by the
    # same design. A gap over 45 minutes must be filled by
    # sweeps, or mostly filled by Class A items; a gap filled
    # by neither is neglect, and fails.
    sweeps = [(i["start"], i["start"] + i["dur"]) for i in bill
              if i["label"] == "Census sweep"]
    class_a = [(i["start"], i["start"] + i["dur"]) for i in bill
               if i["cls"] == "A"]
    for a, b in zip(starts, starts[1:]):
        if b - a > 2700:
            covered = [s for s in sweeps if a <= s[0] and s[1] <= b]
            a_held = sum(min(e, b) - max(s, a) for s, e in class_a
                         if s < b and a < e)
            assert len(covered) >= 4 or a_held >= (b - a) / 2, (
                "aircraft gap %0.0f-%0.0f is neither the deep-sweep "
                "block nor Class A occupation" % (a - T0, b - T0))
