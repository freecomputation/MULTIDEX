"""0.4.1: LRPT imagery and reception containment.

The two halves of the build, each pinned where it can be proven
without hardware. LRPT: a locked METEOR pass files raw IQ and
states its decode state honestly - pending when SatDump is
staged, not decoded (with the reason) when it is not - and the
bounded post-pass decode files its outcome as an imagery event
beside the pass record, which stands either way. Containment:
every planned reception carries an allocation computed from its
own arithmetic, the muster budget degrades in a stated order
when the bill cannot fit the ceiling, consumption is accounted
live against the allocations, and a capture truncates at its
allocation.
"""

import base64
import os
import struct
import time
import zlib

import pytest

from multidex import budget, desks, gates, lrpt, products
from multidex.index import Index
from multidex.officer import WatchRunner
from multidex.sim import synthetic_lrpt_iq
from multidex.tuner import RealSource

from conftest import T0

W = "watch-t"


def _pass(sat="METEOR-M2 4", norad="59051", window=600):
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


def _png_bytes(w=64, h=64):
    """A real, decodable PNG of deterministic noise."""
    rng = __import__("random").Random(7)
    raw = b""
    for _y in range(h):
        raw += b"\x00" + bytes(rng.randrange(256) for _x in range(w))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FAKE_SATDUMP = """#!/usr/bin/env python3
import os, struct, sys, zlib
argv = sys.argv[1:]
with open(os.environ["FAKE_SATDUMP_ARGV"], "w") as fh:
    fh.write("\\n".join(argv))
out = argv[3]
mode = os.environ.get("FAKE_SATDUMP_MODE", "ok")
if mode == "fail":
    sys.exit(3)
os.makedirs(os.path.join(out, "MSU-MR"), exist_ok=True)
png = bytes.fromhex(os.environ["FAKE_PNG_HEX"])
with open(os.path.join(out, "MSU-MR", "MSU-MR-1.png"), "wb") as fh:
    fh.write(png)
sys.exit(0)
"""


@pytest.fixture()
def fake_satdump(tmp_path, monkeypatch):
    """A PATH carrying a stand-in satdump that writes a real PNG
    product (or fails, on FAKE_SATDUMP_MODE=fail) and records its
    argv. Returns the bin dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "satdump"
    script.write_text(FAKE_SATDUMP)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_SATDUMP_ARGV", str(tmp_path / "argv.txt"))
    monkeypatch.setenv("FAKE_PNG_HEX", _png_bytes().hex())
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + "/usr/bin:/bin")
    return bin_dir


def _lrpt_feed(**kw):
    iq = synthetic_lrpt_iq(seconds=1)
    feed = {"lock": True, "quality": 0.8, "sat": "METEOR-M2 4",
            "norad": "59051",
            "iq_b64": base64.b64encode(iq).decode("ascii"),
            "iq_name": "pass-59051-%d.cu8" % int(T0),
            "iq_rate": budget.LRPT_IQ_RATE, "iq_format": "cu8",
            "iq_bytes": len(iq), "iq_synthetic": True}
    feed.update(kw)
    return feed


def _ctx(t0=T0, t1=T0 + 600, **kw):
    ctx = {"watch_id": W, "desk": "satellite", "t0": t0, "t1": t1,
           "freq_hz": 137.1e6, "home": (39.0, -98.0),
           "profile": {}, "pass": _pass()}
    ctx.update(kw)
    return ctx


# ------------------------------------------------- allocation arithmetic

def test_lrpt_reservation_is_rate_times_window():
    item = _pass_item("bill-001", "METEOR-M2 4", window=600, dur=900)
    alloc = budget.item_allocation(item)
    # The window is the predicted AOS-to-LOS span (600 s), not
    # the padded bill duration (900 s): 600 s at 1,024,000
    # samples/s x 2 bytes, plus the image allowance.
    assert alloc["bytes"] == 600 * 1024000 * 2 \
        + budget.LRPT_PNG_ALLOWANCE
    assert alloc["tuner_s"] == 900


def test_apt_reservation_is_stored_rate_times_window():
    item = _pass_item("bill-001", "NOAA 19", window=600)
    alloc = budget.item_allocation(item)
    assert alloc["bytes"] == 600 * 22050 + budget.APT_PNG_ALLOWANCE


def test_budget_aggregates_classes_desks_and_reserve(profile):
    profile["watch"]["disk_ceiling_gb"] = 20.0
    bill = [_pass_item("bill-001", "METEOR-M2 4"),
            _pass_item("bill-002", "NOAA 19"),
            _dwell_item("bill-003", "B", dur=300)]
    budget.annotate_bill(bill)
    rb = budget.build_reception_budget(profile, bill)
    s = rb.summary()
    assert s["degraded"] == []
    assert s["satellite_reserve_bytes"] == \
        bill[0]["allocation"]["bytes"] + bill[1]["allocation"]["bytes"]
    assert s["by_class"]["A"]["bytes"] == s["satellite_reserve_bytes"]
    assert s["by_class"]["B"]["bytes"] == bill[2]["allocation"]["bytes"]
    assert s["by_desk"]["satellite"] == s["satellite_reserve_bytes"]
    assert s["total_bytes"] == sum(i["allocation"]["bytes"] for i in bill)
    assert s["free_margin_bytes"] == s["ceiling_bytes"] - s["total_bytes"]
    assert rb.allocation_for("bill-001") == bill[0]["allocation"]["bytes"]
    assert rb.allocation_for("bill-999") is None


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

def test_degradation_floors_c_then_b_and_holds_satellite(profile):
    # Conftest ceiling: 0.05 GB, usable 47.5 MB. An APT pass
    # reserves about 17.2 MB; ten class C dwells and five class
    # B dwells at full rate cannot fit beside it.
    bill = [_pass_item("bill-001", "NOAA 19")]
    bill += [_dwell_item("bill-1%02d" % i, "C", dur=600)
             for i in range(10)]
    bill += [_dwell_item("bill-2%02d" % i, "B", dur=600)
             for i in range(5)]
    budget.annotate_bill(bill)
    rb = budget.build_reception_budget(profile, bill)
    assert rb.degraded, "the budget must say it degraded"
    assert "class C" in rb.degraded[0]
    assert any("class B" in s for s in rb.degraded)
    # The satellite reservation held at its computed size.
    assert rb.allocation_for("bill-001") == \
        600 * 22050 + budget.APT_PNG_ALLOWANCE
    assert not any("satellite" in s for s in rb.degraded)
    assert rb.total_bytes <= int(0.05e9 * 0.95)


def test_degradation_reaches_satellite_last_and_says_so(profile):
    profile["watch"]["disk_ceiling_gb"] = 0.0005  # 500 KB usable-ish
    bill = [_pass_item("bill-001", "METEOR-M2 4"),
            _dwell_item("bill-002", "C", dur=600)]
    budget.annotate_bill(bill)
    rb = budget.build_reception_budget(profile, bill)
    assert any("class C" in s for s in rb.degraded)
    assert any("satellite pass reservations reduced" in s
               for s in rb.degraded)
    # Even floored, two items at the 1 MB floor cannot fit a
    # 500 KB ceiling: the budget states the overage honestly.
    assert "still exceeds" in rb.degraded[-1]
    assert rb.allocation_for("bill-001") == budget.FLOOR_BYTES


# ------------------------------------------------- LRPT desk honesty

def test_lrpt_event_pending_with_satdump_staged(profile, fake_satdump):
    events = desks.process_satellite(_lrpt_feed(), _ctx())
    ev = events[0]
    assert ev["data"]["downlink"] == "lrpt"
    assert ev["data"]["lrpt_decode"] == "pending"
    assert ev["data"]["imagery"] is None
    assert ev["data"]["imagery_file"].endswith(".png")
    assert "decode follows the pass" in ev["content"]
    assert ev["data"]["synthetic"] is True
    assert ev["data"]["_snippet"]["filename"].endswith(".cu8")


def test_lrpt_event_without_iq_states_it(profile):
    feed = _lrpt_feed()
    del feed["iq_b64"]
    events = desks.process_satellite(feed, _ctx())
    ev = events[0]
    assert ev["data"]["imagery"] is None
    assert "the feed carried no pass IQ" in ev["content"]
    assert "_snippet" not in ev["data"]


def test_sim_meteor_feed_is_iq_and_labelled(profile):
    from multidex.sim import SimSource
    src = SimSource(profile, T0)
    vfo = {"desk": "satellite", "freq_hz": 137.1e6,
           "pass": {"sat": "METEOR-M2 4", "norad": "59051",
                    "aos": T0, "los": T0 + 600, "max_elev_deg": 55.0}}
    feed = src._satellite_feed(vfo)
    assert feed["lock"] is True
    assert "audio_b64" not in feed
    assert feed["iq_synthetic"] is True
    assert feed["iq_rate"] == budget.LRPT_IQ_RATE
    raw = base64.b64decode(feed["iq_b64"])
    assert len(raw) == feed["iq_bytes"] > 0


# ------------------------------------------------- the SatDump binding

def test_pipeline_selection_by_generation():
    assert lrpt.pipeline_for("METEOR-M2 4") == \
        ("meteor_m2-x_lrpt", "M2-4")
    assert lrpt.pipeline_for("METEOR-M2 3") == \
        ("meteor_m2-x_lrpt", "M2-3")
    assert lrpt.pipeline_for("METEOR-M2") == ("meteor_m2_lrpt", "M2")
    assert lrpt.pipeline_for("METEOR-M2 2") == \
        ("meteor_m2_lrpt", "M2-2")


def test_decode_iq_with_fake_satdump(tmp_path, fake_satdump):
    iq_path = tmp_path / "pass.cu8"
    iq_path.write_bytes(synthetic_lrpt_iq(seconds=1))
    png, info = lrpt.decode_iq(str(iq_path), budget.LRPT_IQ_RATE,
                               "METEOR-M2 4")
    assert png is not None and png.startswith(b"\x89PNG")
    assert info["pipeline"] == "meteor_m2-x_lrpt"
    assert info["satellite_number"] == "M2-4"
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[0] == "meteor_m2-x_lrpt"
    assert argv[1] == "baseband"
    assert "--baseband_format" in argv and "cu8" in argv
    assert "--satellite_number" in argv and "M2-4" in argv
    assert "--dc_block" in argv


def test_decode_iq_failure_is_an_honest_reason(tmp_path, fake_satdump,
                                               monkeypatch):
    monkeypatch.setenv("FAKE_SATDUMP_MODE", "fail")
    iq_path = tmp_path / "pass.cu8"
    iq_path.write_bytes(b"\x00" * 64)
    png, reason = lrpt.decode_iq(str(iq_path), budget.LRPT_IQ_RATE,
                                 "METEOR-M2 4")
    assert png is None
    assert "exited 3" in reason and "no image" in reason


def test_decode_iq_without_satdump(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    png, reason = lrpt.decode_iq(str(tmp_path / "x.cu8"),
                                 budget.LRPT_IQ_RATE, "METEOR-M2 4")
    assert png is None
    assert "not staged" in reason


# ------------------------------------------------- officer decode drain

def _runner(home, profile, index):
    runner = WatchRunner(home, profile, index)
    runner.watch_id = W
    runner.watch_dir = index.watch_dir(W)
    os.makedirs(runner.watch_dir, exist_ok=True)
    return runner


def _submit_pass_job(runner, tmp_path):
    iq_path = tmp_path / "filed.cu8"
    iq_path.write_bytes(synthetic_lrpt_iq(seconds=1))
    rel_dir = os.path.join(runner.watch_dir, "snippets")
    os.makedirs(rel_dir, exist_ok=True)
    filed = os.path.join(rel_dir, "pass.cu8")
    iq_path.replace(filed)
    runner._lrpt_queue.submit({
        "iq_path": filed, "iq_rate": budget.LRPT_IQ_RATE,
        "sat": "METEOR-M2 4", "image_name": "pass-59051-1.png",
        "synthetic": True, "pass_event": "ev-pass",
        "bill_id": "bill-001", "t0": T0, "t1": T0 + 600,
        "freq_hz": 137.1e6})


def _wait_idle(runner, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runner._lrpt_queue.pending() == 0:
            return
        time.sleep(0.05)
    raise AssertionError("decode queue never drained")


def test_officer_files_imagery_event_and_flash(home, profile, index,
                                               tmp_path, fake_satdump):
    runner = _runner(home, profile, index)
    runner._start_lrpt_decode()
    assert runner._lrpt_queue is not None
    _submit_pass_job(runner, tmp_path)
    _wait_idle(runner)
    runner._drain_lrpt()
    imagery = index.events(watch_id=W, kinds=["imagery"])
    assert len(imagery) == 1
    ev = imagery[0]
    assert ev["data"]["imagery"] == "pass-59051-1.png"
    assert ev["data"]["synthetic"] is True
    assert ev["data"]["pass_event"] == "ev-pass"
    assert "LRPT imagery decoded and filed" in ev["content"]
    assert ev["snippet"] and ev["snippet"].endswith(".png")
    png_path = os.path.join(runner.watch_dir, ev["snippet"])
    with open(png_path, "rb") as fh:
        assert fh.read().startswith(b"\x89PNG")
    flashes = [f for f in os.listdir(runner.watch_dir)
               if f.startswith("FLASH")]
    assert len(flashes) == 1
    text = open(os.path.join(runner.watch_dir, flashes[0])).read()
    assert "satellite imagery decoded" in text
    assert "pass-59051-1.png" in text
    runner._lrpt_worker.stop()


def test_officer_decode_failure_keeps_pass_record(home, profile, index,
                                                  tmp_path, fake_satdump,
                                                  monkeypatch):
    monkeypatch.setenv("FAKE_SATDUMP_MODE", "fail")
    runner = _runner(home, profile, index)
    runner._start_lrpt_decode()
    _submit_pass_job(runner, tmp_path)
    _wait_idle(runner)
    runner._drain_lrpt()
    imagery = index.events(watch_id=W, kinds=["imagery"])
    assert len(imagery) == 1
    ev = imagery[0]
    assert ev["data"]["imagery"] is None
    assert "LRPT imagery not decoded" in ev["content"]
    assert "exited 3" in ev["content"]
    assert not [f for f in os.listdir(runner.watch_dir)
                if f.startswith("FLASH")]
    runner._lrpt_worker.stop()


# --------------------------------------- live accounting and refusal

def test_live_accounting_and_allocation_refusal(home, profile, index):
    runner = _runner(home, profile, index)
    bill = [_dwell_item("bill-001", "B", dur=60)]
    budget.annotate_bill(bill)
    runner.budget = budget.build_reception_budget(profile, bill)
    runner._bill_cls = {i["id"]: i["cls"] for i in bill}
    from multidex.events import make_event
    ev = make_event(W, "airband", "voice", T0, T0 + 60)
    payload = {"filename": "clip.wav", "kind": "voice",
               "b64": base64.b64encode(b"\x01" * 4096).decode()}
    runner._file_snippet(ev, "airband", "bill-001", payload)
    assert ev["snippet"]
    state = runner._budget_state()
    assert state["consumed_bytes"] == 4096
    assert state["by_class"]["B"]["consumed"] == 4096
    assert state["allocated_bytes"] == runner.budget.total_bytes
    # A payload that would cross the item's allocation is
    # refused at filing, with the refusal on the record.
    alloc = runner.budget.allocation_for("bill-001")
    big = {"filename": "huge.wav", "kind": "voice",
           "b64": base64.b64encode(b"\x02" * (alloc + 1)).decode()}
    ev2 = make_event(W, "airband", "voice", T0 + 120, T0 + 180)
    runner._file_snippet(ev2, "airband", "bill-001", big)
    assert not ev2.get("snippet")
    refusals = [e for e in index.events(watch_id=W, kinds=["system"])
                if "reception allocation" in (e["content"] or "")]
    assert len(refusals) == 1
    assert runner._budget_state()["consumed_bytes"] == 4096


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


# --------------------------------------- truncation at the allocation

def test_lrpt_capture_truncates_at_byte_cap(monkeypatch):
    source = RealSource({})
    source.scan = lambda *a, **k: {
        "noise_floor_db": -100.0,
        "emissions": [{"freq_hz": 137.1e6, "peak_dbm": -55.0,
                       "bw_hz": 40e3, "shape": "unknown",
                       "label": None}]}
    calls = []

    def fake_stream(cmd, path, seconds):
        calls.append(cmd)
        nsamp = int(cmd[cmd.index("-n") + 1])
        with open(path, "wb") as fh:
            fh.write(b"\x80" * nsamp * 2)
        return nsamp * 2

    monkeypatch.setattr("multidex.tuner._stream_to_file", fake_stream)
    now = time.time()
    vfo = {"desk": "satellite", "freq_hz": 137.1e6, "bw_hz": 150e3,
           "byte_cap": 4000000,
           "pass": {"sat": "METEOR-M2 4", "norad": "59051",
                    "aos": now - 60, "los": now + 540}}
    feed = source._feed_satellite([vfo], 137.1e6, 2.4e6,
                                  now - 60, now + 660)
    try:
        assert feed["lock"] is True
        assert feed["iq_truncated"] is True
        assert feed["iq_bytes"] == 4000000
        cmd = calls[0]
        # The capture asked rtl_sdr for exactly cap / 2 samples.
        assert int(cmd[cmd.index("-n") + 1]) == 2000000
        assert feed["iq_rate"] == budget.LRPT_IQ_RATE
    finally:
        if feed.get("iq_file"):
            os.remove(feed["iq_file"])


# ------------------------------------------------- doctor and products

def test_doctor_reports_satdump_and_budget(profile, home):
    text = gates.doctor_text(profile, home)
    assert "satellite LRPT" in text
    assert "Reception budget for this profile" in text


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
    assert meta["reception_budget"]["ceiling_bytes"] == 50000000
    assert os.path.exists(os.path.join(wdir, "budget.json"))
    state = util.read_json(os.path.join(wdir, "state.json"))
    assert state["budget"]["allocated_bytes"] > 0
    # The satellite reserve is exactly the bill's pass
    # allocations, whatever passes the window held.
    bill = util.read_json(os.path.join(wdir, "bill.json"))
    expected_reserve = sum(
        budget.item_allocation(i)["bytes"] for i in bill
        if i.get("kind") == "pass" and "satellite" in i["desks"])
    assert state["budget"]["satellite_reserve_bytes"] == \
        expected_reserve
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "Reception account" in report
    assert "Class A receptions: allocated" in report
    muster = open(os.path.join(wdir, "events.jsonl")).read()
    assert "reception budget: ceiling" in muster
    # Status surfaces the same budget while a watch stands:
    # present this watch's state as live and read it back.
    state["complete"] = False
    state["pid"] = os.getpid()
    util.write_json(os.path.join(wdir, "state.json"), state)
    util.write_json(os.path.join(home, "current_watch"), watch_id)
    status = products.status_text(home)
    assert "Reception budget:" in status
    assert "satellite reserve" in status
