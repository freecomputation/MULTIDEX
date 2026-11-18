"""0.4.1: reception containment, carried through 0.6.0.

The containment half of the 0.4.1 build, pinned where it can be
proven without hardware: every planned reception carries an
allocation computed from its own arithmetic, grants against
the home's standing pool shrink in a stated order when the
bill cannot fit it, consumption is accounted live against the
grants, and a capture truncates at its grant.

The build's other half - LRPT imagery, a locked METEOR pass
filing raw IQ for a bound post-pass decode - retired in
0.6.0 under the METEOR acknowledgement policy: METEOR passes
are acknowledged only, carry a zero-byte allocation, and take
no reservation or grant. The policy's tests live in
tests/test_fixes_060.py; what remains here of the satellite
arithmetic is the APT reservation and the acknowledgement's
zero.
"""

import base64
import os

from multidex import budget, gates, products
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0

W = "watch-t"


def _pass(sat="NOAA 19", norad="33591", window=600):
    return {"sat": sat, "norad": norad, "aos": T0, "los": T0 + window,
            "t_max": T0 + window // 2, "max_elev_deg": 55.0,
            "aos_az_deg": 90.0, "los_az_deg": 270.0}


def _pass_item(bid, sat, window=600, dur=900, cls="A"):
    return {"id": bid, "cls": cls, "kind": "pass",
            "label": "%s pass" % sat, "start": T0, "dur": dur,
            "center_hz": 137.1e6, "bw_hz": 150e3,
            "desks": ["satellite"], "pass": _pass(sat, window=window)}


def _dwell_item(bid, cls, dur=600, desks=("airband",)):
    return {"id": bid, "cls": cls, "kind": "dwell",
            "label": "dwell %s" % bid, "start": T0, "dur": dur,
            "center_hz": 121.5e6, "bw_hz": 400e3,
            "desks": list(desks), "pass": None}


# ------------------------------------------------- allocation arithmetic

def test_meteor_allocation_is_zero_bytes():
    # Acknowledged only: the pass is billed (its tuner seconds
    # stand) and allocates nothing.
    item = _pass_item("bill-001", "METEOR-M2 4", window=600,
                      dur=900)
    alloc = budget.item_allocation(item)
    assert alloc["bytes"] == 0
    assert alloc["tuner_s"] == 900
    assert budget.downlink_kind("METEOR-M2 4") == "acknowledged"
    assert budget.downlink_kind("METEOR-M 2") == "acknowledged"


def test_apt_reservation_is_stored_rate_times_window():
    item = _pass_item("bill-001", "NOAA 19", window=600)
    alloc = budget.item_allocation(item)
    assert alloc["bytes"] == 600 * 22050 + budget.APT_PNG_ALLOWANCE


def test_grants_aggregate_classes_and_reserve(profile, home):
    # Adapted to the standing model: the aggregates are the
    # watch's grants in the home's ledger, and the satellite
    # figure is the pool's open satellite reservation. The
    # windows are short because the pool also reads the home's
    # actual disk state, and the test home sits on a small
    # scratch filesystem: the bill must fit both. The METEOR
    # item takes no grant under the acknowledgement policy, so
    # the satellite figure is the APT pass alone.
    profile["watch"]["disk_ceiling_gb"] = 20.0
    bill = [_pass_item("bill-001", "METEOR-M2 4", window=60),
            _pass_item("bill-002", "NOAA 19", window=300),
            _dwell_item("bill-003", "B", dur=300)]
    budget.annotate_bill(bill)
    ledger = budget.AllocationLedger.load(home)
    grants, statements, pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    assert statements == []
    assert "bill-001" not in grants
    sat = bill[1]["allocation"]["bytes"]
    assert pool["satellite_reserve_bytes"] == sat
    sl = budget.watch_slice(ledger, W)
    assert sl["by_class"]["A"]["granted"] == sat
    assert sl["satellite_bytes"] == sat
    assert sl["by_class"]["B"]["granted"] == \
        bill[2]["allocation"]["bytes"]
    assert sl["granted_bytes"] == \
        bill[1]["allocation"]["bytes"] \
        + bill[2]["allocation"]["bytes"]
    assert pool["free_bytes"] == pool["cap_bytes"] \
        - pool["margin_bytes"] - sl["granted_bytes"]
    assert grants["bill-002"]["bytes"] == \
        bill[1]["allocation"]["bytes"]
    assert "bill-999" not in grants


def test_dwell_allocation_charges_capture_rates_and_census(profile):
    item = _dwell_item("bill-001", "B", dur=100, desks=("airband",))
    alloc = budget.item_allocation(item)
    # airband at its capture rate plus the audio census the
    # officer adds to every captured window.
    assert alloc["bytes"] == 100 * (96000 + 96000)
    ev_item = _dwell_item("bill-002", "C", dur=100, desks=("pager",))
    assert budget.item_allocation(ev_item)["bytes"] == \
        budget.EVENT_ALLOWANCE + 100 * 96000


# ------------------------------------------------- muster degradation

def test_degradation_floors_c_then_b_and_holds_satellite(profile,
                                                         home):
    # Conftest ceiling: 0.05 GB, grantable 47.5 MB. An APT pass
    # reserves about 17.2 MB; ten class C dwells and five class
    # B dwells at full rate cannot fit beside it.
    bill = [_pass_item("bill-001", "NOAA 19")]
    bill += [_dwell_item("bill-1%02d" % i, "C", dur=600)
             for i in range(10)]
    bill += [_dwell_item("bill-2%02d" % i, "B", dur=600)
             for i in range(5)]
    budget.annotate_bill(bill)
    ledger = budget.AllocationLedger.load(home)
    grants, statements, _pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    assert statements, "the grants must say they shrank"
    assert "class C" in statements[0]
    assert any("class B" in s for s in statements)
    # The satellite grant held at its computed size.
    assert grants["bill-001"]["bytes"] == \
        600 * 22050 + budget.APT_PNG_ALLOWANCE
    assert not any("satellite" in s for s in statements)
    assert sum(g["bytes"] for g in grants.values()) <= \
        int(0.05e9 * 0.95)


def test_degradation_reaches_satellite_last_and_says_so(profile,
                                                        home):
    profile["watch"]["disk_ceiling_gb"] = 0.0005  # 500 KB cap
    bill = [_pass_item("bill-001", "NOAA 19"),
            _dwell_item("bill-002", "C", dur=600)]
    budget.annotate_bill(bill)
    ledger = budget.AllocationLedger.load(home)
    grants, statements, _pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    assert any("class C" in s for s in statements)
    assert any("satellite pass grants reduced" in s
               for s in statements)
    # Even floored, two items at the 1 MB floor cannot fit the
    # pool's grantable room: the overage is stated honestly.
    assert "still exceeds" in statements[-1]
    assert grants["bill-001"]["bytes"] == budget.FLOOR_BYTES


# --------------------------------------- live accounting and refusal

def _runner(home, profile, index):
    runner = WatchRunner(home, profile, index)
    runner.watch_id = W
    runner.watch_dir = index.watch_dir(W)
    os.makedirs(runner.watch_dir, exist_ok=True)
    return runner


def test_live_accounting_and_allocation_refusal(home, profile, index):
    runner = _runner(home, profile, index)
    bill = [_dwell_item("bill-001", "B", dur=60)]
    budget.annotate_bill(bill)
    # The watch's grants stand in the home's ledger now, and
    # consumption posts against them as receptions file.
    ledger = budget.AllocationLedger.load(home)
    grants, _statements, _pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    runner.ledger = ledger
    runner.grants = grants
    runner._bill_cls = {i["id"]: i["cls"] for i in bill}
    from multidex.events import make_event
    ev = make_event(W, "airband", "voice", T0, T0 + 60)
    payload = {"filename": "clip.wav", "kind": "voice",
               "b64": base64.b64encode(b"\x01" * 4096).decode()}
    runner._file_snippet(ev, "airband", "bill-001", payload)
    assert ev["snippet"]
    state = runner._pool_state()
    assert state["watch_consumed_bytes"] == 4096
    assert state["consumed_bytes"] == 4096  # the pool's own figure
    assert state["by_class"]["B"]["consumed"] == 4096
    assert state["watch_granted_bytes"] == grants["bill-001"]["bytes"]
    # A payload that would cross the item's grant is refused at
    # filing, with the refusal on the record.
    alloc = grants["bill-001"]["bytes"]
    big = {"filename": "huge.wav", "kind": "voice",
           "b64": base64.b64encode(b"\x02" * (alloc + 1)).decode()}
    ev2 = make_event(W, "airband", "voice", T0 + 120, T0 + 180)
    runner._file_snippet(ev2, "airband", "bill-001", big)
    assert not ev2.get("snippet")
    refusals = [e for e in index.events(watch_id=W, kinds=["system"])
                if "reception allocation" in (e["content"] or "")]
    assert len(refusals) == 1
    assert runner._pool_state()["watch_consumed_bytes"] == 4096


def test_file_payload_moves_by_reference(home, profile, index,
                                         tmp_path):
    runner = _runner(home, profile, index)
    stage = tmp_path / "stage.cu8"
    stage.write_bytes(b"\x7f" * 8192)
    from multidex.events import make_event
    ev = make_event(W, "satellite", "pass", T0, T0 + 600)
    runner._file_snippet(ev, "satellite", "bill-001",
                         {"filename": "pass.cu8", "kind": "satellite",
                          "file": str(stage), "bytes": 8192})
    assert ev["snippet"]
    kept = os.path.join(runner.watch_dir, ev["snippet"])
    assert os.path.getsize(kept) == 8192
    assert not stage.exists()  # the staging file was consumed
    assert runner._held_total == 8192
    rows = index.snippet_rows(W)
    assert rows[0]["bytes"] == 8192 and rows[0]["shed"] == 0


# ------------------------------------------------- doctor and products

def test_doctor_reports_satellite_policy_and_budget(profile, home):
    text = gates.doctor_text(profile, home)
    assert "acknowledged only" in text
    assert "satdump" not in text
    assert "Reception pool for this home" in text


def test_report_reception_account_and_live_state(home, profile):
    profile["watch"]["hours"] = 2
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        watch_id = result["watch_id"]
    finally:
        index.close()
    wdir = os.path.join(home, "watches", watch_id)
    from multidex import util
    meta = util.read_json(os.path.join(wdir, "watch.json"))
    # The meta names the standing pool the watch drew on, not a
    # private budget: the cap is the home's standing cap.
    assert meta["reception_pool"]["cap_bytes"] == 50000000
    assert meta["reception_grants"]["granted_bytes"] > 0
    # budget.json is a snapshot view of the ledger at muster:
    # it carries the pool figures and one grant per bill item.
    # A billed METEOR pass holds no grant under the
    # acknowledgement policy, so the snapshot's grants are the
    # bill's items minus its acknowledged passes.
    snap = util.read_json(os.path.join(wdir, "budget.json"))
    assert snap["pool"]["cap_bytes"] == 50000000
    bill = util.read_json(os.path.join(wdir, "bill.json"))
    acknowledged = {i["id"] for i in bill
                    if budget.downlink_kind(
                        (i.get("pass") or {}).get("sat"))
                    == "acknowledged"}
    assert {g["bill_id"] for g in snap["grants"]} == \
        {i["id"] for i in bill} - acknowledged
    state = util.read_json(os.path.join(wdir, "state.json"))
    assert state["budget"]["cap_bytes"] == 50000000
    assert state["budget"]["watch_granted_bytes"] > 0
    # The standing satellite reservations exist independently
    # of this watch's bill (the bill held no passes at all):
    # they were taken at muster for the windows ahead and are
    # still standing in the pool after stand-down. They are
    # APT reservations only under the acknowledgement policy.
    assert state["budget"]["satellite_reserve_bytes"] > 0
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "Reception account" in report
    assert "standing allocation pool" in report
    assert "Class A receptions: granted" in report
    muster = open(os.path.join(wdir, "events.jsonl")).read()
    assert "reception allocations: the standing pool" in muster
    # Status surfaces the same pool while a watch stands:
    # present this watch's state as live and read it back.
    state["complete"] = False
    state["pid"] = os.getpid()
    util.write_json(os.path.join(wdir, "state.json"), state)
    util.write_json(os.path.join(home, "current_watch"), watch_id)
    status = products.status_text(home)
    assert "Reception pool:" in status
    assert "standing cap" in status
