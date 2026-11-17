"""The watch bill: classes, placement, and the fill rules."""

import time

from multidex import gates
from multidex.officer import build_bill

from conftest import T0


def _bill(profile):
    sats, _ = gates.load_sats(profile)
    return build_bill(profile, T0, sats)


def test_bill_covers_the_watch(profile):
    bill = _bill(profile)
    assert bill
    assert bill[0]["start"] >= T0 - 130
    last_end = max(i["start"] + i["dur"] for i in bill)
    assert last_end >= T0 + 24 * 3600 - 900
    total = sum(i["dur"] for i in bill)
    assert total > 0.9 * 24 * 3600


def test_bill_items_never_overlap(profile):
    bill = _bill(profile)
    spans = sorted((i["start"], i["start"] + i["dur"]) for i in bill)
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        assert a1 <= b0 + 1, "bill items overlap"


def test_bill_has_all_classes(profile):
    bill = _bill(profile)
    classes = {i["cls"] for i in bill}
    assert classes == {"A", "B", "C"}
    kinds = {i["kind"] for i in bill}
    assert "pass" in kinds and "sonde" in kinds and "sweep" in kinds


def test_class_a_owns_its_window(profile):
    bill = _bill(profile)
    for item in bill:
        if item["cls"] == "A":
            for other in bill:
                if other is item:
                    continue
                assert not (other["start"] < item["start"] + item["dur"]
                            and item["start"] < other["start"]
                            + other["dur"])


def test_deep_sweep_hours_are_sweeps(profile):
    bill = _bill(profile)
    deep = [i for i in bill
            if profile["watch"]["deep_sweep_start"]
            <= time.localtime(i["start"]).tm_hour
            < profile["watch"]["deep_sweep_end"]]
    assert deep, "expected items in the deep sweep window"
    # The fill in the deep window is all sweeps; Class A clock events
    # (satellite passes) still own their windows absolutely.
    assert all(i["kind"] == "sweep" for i in deep if i["cls"] != "A")


def test_excluded_band_removes_entries(profile):
    profile["excluded"]["bands"] = [[1089e6, 1091e6]]
    bill = _bill(profile)
    adsb_items = [i for i in bill if "adsb" in i["desks"]]
    assert adsb_items == []


def test_rotation_weights_shape_the_day(profile):
    bill = _bill(profile)
    counts = {}
    for i in bill:
        if i["cls"] == "B":
            counts[i["label"]] = counts.get(i["label"], 0) + 1
    # adsb has the highest weight (10) and short dwells: it must be
    # among the most frequent rotation entries.
    assert counts.get("Rotation adsb", 0) >= counts.get(
        "Rotation fmband", 0)
