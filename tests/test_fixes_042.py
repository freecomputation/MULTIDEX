"""0.4.2: the standing allocation ledger.

Reception overhead is a property of the installation, not of
a watch: the home's ledger holds situational grants against a
standing pool, watches take and release them as they stand,
cancel, and resume, reservations expire on their own, and the
surfaces (doctor, status, the officer's state) all read the
same figures. Each test pins one clause of that structure.
"""

import json
import os
import re

from multidex import budget, gates, products
from multidex.officer import WatchRunner

from conftest import T0

W = "watch-t"


def _dwell_item(bid, cls, dur=60, desks=("airband",)):
    return {"id": bid, "cls": cls, "kind": "dwell",
            "label": "dwell %s" % bid, "start": T0, "dur": dur,
            "center_hz": 121.5e6, "bw_hz": 400e3,
            "desks": list(desks), "pass": None}


def _grants_for(profile, home, watch_id, bill, now=T0):
    budget.annotate_bill(bill)
    ledger = budget.AllocationLedger.load(home)
    grants, statements, _pool = budget.request_watch_grants(
        ledger, profile, watch_id, bill, now)
    assert statements == []
    return ledger, grants


# ------------------------------------------------------- persistence

def test_ledger_persists_across_restart(home, profile):
    bill = [_dwell_item("bill-001", "B", dur=60)]
    ledger, grants = _grants_for(profile, home, W, bill)
    ledger.consume(grants["bill-001"]["id"], 2048)
    before = budget.AllocationLedger.load(home).pool()
    # A new officer process on the same home: a fresh load from
    # disk, nothing carried in memory.
    ledger2 = budget.AllocationLedger.load(home)
    after = ledger2.pool()
    for key in ("cap_bytes", "consumed_bytes", "reserved_bytes",
                "granted_bytes", "free_bytes", "open_grants"):
        assert after[key] == before[key], key
    assert after["consumed_bytes"] == 2048
    sl = budget.watch_slice(ledger2, W)
    assert sl["granted_bytes"] == grants["bill-001"]["bytes"]
    assert sl["consumed_bytes"] == 2048
    # The file itself is on disk beside where the Index lives,
    # with its format version recorded.
    raw = json.load(open(os.path.join(
        home, budget.LEDGER_FILENAME)))
    assert raw["format"] == budget.LEDGER_FORMAT
    assert raw["cap_bytes"] == 50000000


# ------------------------------------------------------- cancellation

def test_cancellation_releases_unspent_consumed_stays(home, profile):
    bill = [_dwell_item("bill-001", "B", dur=60)]
    ledger, grants = _grants_for(profile, home, W, bill)
    grant = grants["bill-001"]
    total = grant["bytes"]
    ledger.consume(grant["id"], 4096)
    closed, reverted, released = ledger.release_watch(
        W, T0 + 120, "watch ended early: operator stop",
        status="cancelled")
    assert (closed, reverted) == (1, 0)
    assert released == total - 4096
    grant = ledger.find(grant["id"])
    assert grant["status"] == "cancelled"
    assert grant["released_bytes"] == total - 4096
    # The filed reception stays accounted as consumed in the
    # ledger's history; only the unspent room returned.
    assert grant["consumed_bytes"] == 4096
    pool = ledger.pool()
    assert pool["consumed_bytes"] == 4096
    assert pool["reserved_bytes"] == 0
    assert pool["free_bytes"] == \
        pool["cap_bytes"] - pool["margin_bytes"] - 4096


def test_fresh_watch_grants_against_same_pool_after_stop(home,
                                                         profile):
    # Watch A stands, files, and is stopped early.
    bill_a = [_dwell_item("bill-001", "B", dur=60)]
    ledger, grants_a = _grants_for(profile, home, "watch-a", bill_a)
    ledger.consume(grants_a["bill-001"]["id"], 100000)
    ledger.release_watch("watch-a", T0 + 300,
                         "watch ended early: operator stop",
                         status="cancelled")
    # Watch B musters later against the same standing pool: it
    # sees A's consumed bytes in the pool's figures and still
    # takes its full grant, because A's unspent room returned.
    bill_b = [_dwell_item("bill-001", "B", dur=60)]
    budget.annotate_bill(bill_b)
    grants_b, statements, pool = budget.request_watch_grants(
        ledger, profile, "watch-b", bill_b, T0 + 3600)
    assert statements == []
    assert grants_b["bill-001"]["bytes"] == \
        bill_b[0]["allocation"]["bytes"]
    assert pool["consumed_bytes"] == 100000
    sl_b = budget.watch_slice(ledger, "watch-b")
    assert sl_b["granted_bytes"] == bill_b[0]["allocation"]["bytes"]
    sl_a = budget.watch_slice(ledger, "watch-a")
    assert sl_a["consumed_bytes"] == 100000
    assert sl_a["released_bytes"] == \
        bill_a[0]["allocation"]["bytes"] - 100000


# ------------------------------------------------------- reservations

def test_expired_reservation_releases(home, profile):
    ledger = budget.AllocationLedger.load(home)
    ledger.set_cap(budget.cap_bytes(profile))
    reserve = ledger.add_grant(
        T0 - 7200, kind="reserve", owner="standing", bill_id=None,
        cls="A", desks=["satellite"], label="Satellite pass, NOAA 19",
        sat="NOAA 19", downlink="apt", bytes=5000000,
        computed_bytes=5000000, tuner_s=600,
        window_start=T0 - 7200, window_end=T0 - 3600)
    pool = ledger.pool()
    assert pool["satellite_reserve_bytes"] == 5000000
    assert pool["reserved_bytes"] == 5000000
    expired = ledger.expire(T0)
    assert [g["id"] for g in expired] == [reserve["id"]]
    reserve = ledger.find(reserve["id"])
    assert reserve["status"] == "expired"
    assert reserve["released_bytes"] == 5000000
    pool = ledger.pool()
    assert pool["satellite_reserve_bytes"] == 0
    assert pool["reserved_bytes"] == 0
    assert pool["free_bytes"] == \
        pool["cap_bytes"] - pool["margin_bytes"]


# ------------------------------------------------------- reconciliation

def test_resume_reconciliation(home, profile, index):
    bill = [_dwell_item("bill-001", "B", dur=60),
            _dwell_item("bill-002", "B", dur=60)]
    ledger, grants = _grants_for(profile, home, W, bill)
    # Evidence filed in the snippet store that the ledger's
    # grant never saw posted (the officer died mid-filing).
    watch_dir = index.watch_dir(W)
    os.makedirs(watch_dir, exist_ok=True)
    rel, _shed = index.keep_snippet(
        W, watch_dir, "airband", "voice", "clip-bill-001.wav",
        data=b"\x01" * 2048, ceiling_bytes=10 ** 9, t=T0)
    assert rel
    # The other grant was closed behind the resumed watch's
    # back, as a stand-down in another process would leave it.
    old_id = grants["bill-002"]["id"]
    ledger.close(old_id, T0 + 30,
                 "watch ended early: operator stop",
                 status="cancelled")
    grants2, statements = budget.reconcile_watch(
        ledger, profile, W, bill, index, T0 + 120)
    # Both changes are stated, in plain words.
    assert any("bill-001" in s and "re-derived from the "
               "snippet store" in s for s in statements)
    assert any("bill-002" in s and "stood cancelled" in s
               for s in statements)
    # The store is the truth for consumption.
    assert grants2["bill-001"]["consumed_bytes"] == 2048
    # The closed grant was re-taken fresh, open, new identity.
    assert grants2["bill-002"]["status"] == "open"
    assert grants2["bill-002"]["id"] != old_id


# ------------------------------------------------------- schema tolerance

def test_ledger_schema_tolerance(home, profile):
    os.makedirs(home, exist_ok=True)
    doc = {
        "format": budget.LEDGER_FORMAT,
        "cap_bytes": 50000000,
        "a_future_root_field": {"anything": [1, 2, 3]},
        "grants": [
            {"id": "grant-full", "kind": "watch", "owner": W,
             "bill_id": "bill-001", "cls": "B",
             "desks": ["airband"], "label": "dwell",
             "sat": None, "downlink": None, "bytes": 100000,
             "computed_bytes": 100000, "tuner_s": 60,
             "consumed_bytes": 3000, "shed_bytes": 0,
             "released_bytes": 0, "status": "open",
             "granted_at": T0, "window_start": None,
             "window_end": None, "closed_at": None,
             "close_reason": None, "adopted_reserve": False,
             "an_unknown_grant_field": "from a newer version"},
            # A grant written by an older, sparer hand: most
            # fields missing entirely.
            {"id": "grant-sparse", "kind": "watch", "owner": W,
             "bill_id": "bill-002", "bytes": 2000,
             "status": "open"},
        ],
    }
    with open(os.path.join(home, budget.LEDGER_FILENAME),
              "w") as fh:
        json.dump(doc, fh)
    ledger = budget.AllocationLedger.load(home)
    pool = ledger.pool()
    assert pool["cap_bytes"] == 50000000
    assert pool["consumed_bytes"] == 3000
    assert pool["granted_bytes"] == 102000
    assert pool["reserved_bytes"] == 102000 - 3000
    # And a home with no ledger at all reads as an empty one.
    empty = budget.AllocationLedger.load(home + "-absent")
    assert empty.grants == []
    assert empty.pool()["cap_bytes"] is None


# ------------------------------------------------------- standing surfaces

_POOL_DOCTOR_RE = re.compile(
    r"standing cap (\d+) bytes; (\d+) consumed, (\d+) reserved "
    r"in (\d+) open grants \((\d+) of it satellite "
    r"reservations\), (\d+) free; a margin of (\d+) bytes")
_POOL_LINE_RE = re.compile(
    r"standing cap (\d+) bytes; (\d+) consumed, (\d+) reserved "
    r"in (\d+) open grants, (\d+) free")


def test_standing_surfaces_agree_with_ledger(home, profile, index):
    bill = [_dwell_item("bill-001", "B", dur=60)]
    ledger, grants = _grants_for(profile, home, W, bill)
    ledger.consume(grants["bill-001"]["id"], 8192)
    # Doctor refreshes the standing reservations; run it once
    # to let them settle, then every surface must state the
    # same pool the ledger holds.
    gates.doctor_text(profile, home)
    text = gates.doctor_text(profile, home)
    pool = budget.AllocationLedger.load(home).pool()
    m = _POOL_DOCTOR_RE.search(text)
    assert m, text
    cap, consumed, reserved, open_n, sat, free, margin = \
        (int(x) for x in m.groups())
    assert cap == pool["cap_bytes"]
    assert consumed == pool["consumed_bytes"]
    assert reserved == pool["reserved_bytes"]
    assert open_n == pool["open_grants"]
    assert sat == pool["satellite_reserve_bytes"]
    assert free == pool["free_bytes"]
    assert margin == pool["margin_bytes"]
    # Status's pool line, read with no watch standing, agrees.
    line = products._pool_line(home)
    assert line is not None
    m = _POOL_LINE_RE.search(line)
    assert m, line
    cap, consumed, reserved, open_n, free = \
        (int(x) for x in m.groups())
    assert (cap, consumed, reserved, open_n, free) == (
        pool["cap_bytes"], pool["consumed_bytes"],
        pool["reserved_bytes"], pool["open_grants"],
        pool["free_bytes"])
    # The officer's state block over the same ledger agrees.
    runner = WatchRunner(home, profile, index)
    runner.watch_id = W
    runner.ledger = budget.AllocationLedger.load(home)
    state = runner._pool_state()
    assert state["cap_bytes"] == pool["cap_bytes"]
    assert state["consumed_bytes"] == pool["consumed_bytes"]
    assert state["free_bytes"] == pool["free_bytes"]
    assert state["satellite_reserve_bytes"] == \
        pool["satellite_reserve_bytes"]
