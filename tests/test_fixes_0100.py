"""0.10.0: feature-first tiered retention under the standing
ledger.

The snippet store's organizing law is the tier model: tier 0
payloads never shed, tier 1 derived products shed last, tier 2
selective excerpts (voice audio and SigMF channel IQ) shed by
value, tier 3 bulk capture material (satellite pass audio)
sheds first, and sealed captures stand outside the tiers.
Each test pins one clause of that law, plus the SigMF pair
format read back through an independent reader written here,
and a simulated watch under a deliberately tiny pool.
"""

import base64
import json
import os

import numpy as np

from multidex import audiocensus as ac
from multidex import budget, desks, gates, retention, sigmf
from multidex.events import make_event

from conftest import T0

W = "watch-t"
BIG = 10 ** 9


def _rows_by_name(index, watch=W):
    return {os.path.basename(r["path"]): r
            for r in index.snippet_rows(watch)}


def _file_event(index, watch, wdir, desk, rel, emitter,
                grade, data=None, t=T0):
    ev = make_event(watch, desk, "audio", t, t, emitter=emitter,
                    grade=grade, content="filed evidence",
                    data=data or {})
    ev["snippet"] = rel
    index.add_event(ev)
    return ev


# ------------------------------------------------------- tier map

def test_tier_assignment_per_kind():
    """Every kind the desks file has a stated tier. The
    satellite desk files two artifacts under one kind: the
    decoded image is the derived product (tier 1) and the pass
    audio is the bulk capture (tier 3)."""
    assert retention.tier_of("payload") == 0
    assert retention.tier_of("spectrum") == 1
    assert retention.tier_of("image") == 1
    assert retention.tier_of("satellite", "pass.png") == 1
    assert retention.tier_of("satellite", "pass.wav") == 3
    assert retention.tier_of("voice") == 2
    assert retention.tier_of("iq", "x.sigmf-data") == 2
    # A kind the map does not name is bulk until it is named.
    assert retention.tier_of("unnamed-kind") == 3


def test_value_orders_unresolved_unique_over_confirmed_common():
    rare = {"grade": "UNRESOLVED", "emitter": "E1", "data": {}}
    common = {"grade": "CONFIRMED", "emitter": "E2", "data": {}}
    assert retention.value_of(rare, 1) > retention.value_of(common, 40)
    assert retention.value_of(common, 40) < retention.value_of(common, 1)
    # Unlinked evidence reads from the neutral middle.
    assert retention.value_of(None, None) == 1.0


# ------------------------------------------------------- shed order

def test_shed_order_across_tiers(index):
    """Under pressure the store sheds tier 3 first, then tier
    2, then tier 1; tier 0 payload evidence is filed anyway
    and survives, and a tier 1 product goes only once no tier
    2 or tier 3 material remains."""
    wdir = index.watch_dir(W)
    index.keep_snippet(W, wdir, "satellite", "satellite",
                       "pass.wav", b"S" * 1000, BIG, t=T0)
    index.keep_snippet(W, wdir, "airband", "voice",
                       "voice.wav", b"V" * 1000, BIG, t=T0 + 1)
    index.keep_snippet(W, wdir, "sweep", "spectrum",
                       "spectrum-1.json", b"P" * 1000, BIG, t=T0 + 2)
    index.keep_snippet(W, wdir, "digitalvoice", "payload",
                       "frames.bin", b"F" * 1000, BIG, t=T0 + 3)
    # A second payload filing against a 3500-byte ceiling: the
    # store must free 1500 bytes - the pass audio and the voice
    # excerpt - and stop there, sparing the tier 1 product.
    rel, _shed = index.keep_snippet(
        W, wdir, "digitalvoice", "payload", "frames2.bin",
        b"G" * 1000, 3500, t=T0 + 4)
    assert rel is not None
    rows = _rows_by_name(index)
    assert rows["pass.wav"]["shed"] == 1
    assert rows["voice.wav"]["shed"] == 1
    assert rows["spectrum-1.json"]["shed"] == 0
    assert rows["frames.bin"]["shed"] == 0
    assert rows["frames2.bin"]["shed"] == 0
    # Drive the ceiling to the payloads alone: now the tier 1
    # product sheds too, and the payloads still stand.
    index.keep_snippet(W, wdir, "digitalvoice", "payload",
                       "frames3.bin", b"H" * 1000, 2500, t=T0 + 5)
    rows = _rows_by_name(index)
    assert rows["spectrum-1.json"]["shed"] == 1
    assert rows["frames.bin"]["shed"] == 0
    assert rows["frames2.bin"]["shed"] == 0
    assert rows["frames3.bin"]["shed"] == 0


def test_value_order_within_a_tier(index):
    """Two tier 2 voice excerpts: the one from a constantly
    heard, CONFIRMED emitter sheds before the one from an
    emitter heard once and graded UNRESOLVED, even though the
    valuable one is older - age is only the tiebreak."""
    wdir = index.watch_dir(W)
    rel_old, _ = index.keep_snippet(
        W, wdir, "audiocensus", "voice", "rare.wav", b"R" * 1000,
        BIG, t=T0)
    _file_event(index, W, wdir, "audiocensus", rel_old,
                "AUDIO:100.000 MHz", "UNRESOLVED",
                data={"voice_score": 50}, t=T0)
    rel_new, _ = index.keep_snippet(
        W, wdir, "audiocensus", "voice", "common.wav", b"C" * 1000,
        BIG, t=T0 + 10)
    _file_event(index, W, wdir, "audiocensus", rel_new,
                "AUDIO:200.000 MHz", "CONFIRMED",
                data={"voice_score": 95}, t=T0 + 10)
    for i in range(4):
        index.add_event(make_event(
            W, "audiocensus", "audio", T0 + 20 + i, T0 + 20 + i,
            emitter="AUDIO:200.000 MHz", grade="CONFIRMED",
            content="heard again", data={}))
    # One payload filing that must free exactly one excerpt's
    # room: the common emitter's excerpt is the victim.
    index.keep_snippet(W, wdir, "digitalvoice", "payload",
                       "frames.bin", b"F" * 1000, 2500, t=T0 + 30)
    rows = _rows_by_name(index)
    assert rows["common.wav"]["shed"] == 1
    assert rows["rare.wav"]["shed"] == 0
    assert rows["frames.bin"]["shed"] == 0


def test_tier0_and_sealed_survive_a_pool_driven_to_zero(index):
    """Payload evidence and a sealed capture are filed even
    when nothing sheddable remains and the ceiling is zero;
    the sealed bytes are accounted apart from every tier."""
    wdir = index.watch_dir(W)
    rel_v, _ = index.keep_snippet(
        W, wdir, "airband", "voice", "voice.wav", b"V" * 500,
        1000, t=T0)
    assert rel_v is not None
    rel_p, _ = index.keep_snippet(
        W, wdir, "digitalvoice", "payload", "frames.bin",
        b"F" * 500, 1000, t=T0 + 1)
    assert rel_p is not None
    # The sealed filing needs the whole ceiling: the voice
    # excerpt sheds for it; the payload does not.
    rel_s, _ = index.keep_snippet(
        W, wdir, "sweep", "iq", "sealed-capture.sigmf-data",
        b"Q" * 500, 1000, t=T0 + 2, sealed=True)
    assert rel_s is not None
    # And a payload filing against a zero ceiling still files.
    rel_p2, _ = index.keep_snippet(
        W, wdir, "digitalvoice", "payload", "frames2.bin",
        b"G" * 500, 0, t=T0 + 3)
    assert rel_p2 is not None
    rows = _rows_by_name(index)
    assert rows["voice.wav"]["shed"] == 1
    assert rows["frames.bin"]["shed"] == 0
    assert rows["frames2.bin"]["shed"] == 0
    assert rows["sealed-capture.sigmf-data"]["shed"] == 0
    acct = index.snippet_accounting(W)
    assert acct["sealed_bytes"] == 500
    assert acct["sealed_count"] == 1
    assert acct["by_tier"][0]["kept_bytes"] == 1000
    on_disk = os.path.join(wdir, "snippets", "sealed-capture.sigmf-data")
    assert os.path.exists(on_disk)


def test_sigmf_pair_sheds_together(index):
    """A SigMF excerpt is a pair: under pressure both files
    shed, or neither does - the store never holds half an
    excerpt."""
    wdir = index.watch_dir(W)
    samples = [0.5 + 0.25j] * 100
    pair = sigmf.pair_payloads("excerpt-1", samples, 48000.0,
                               146.5e6, T0, bw_hz=9000.0)
    for payload in pair:
        index.keep_snippet(W, wdir, "sweep", "iq",
                           payload["filename"],
                           base64.b64decode(payload["b64"]), BIG,
                           t=T0)
    index.keep_snippet(W, wdir, "sweep", "spectrum",
                       "spectrum-1.json", b"P" * 500, BIG, t=T0 + 1)
    index.keep_snippet(W, wdir, "digitalvoice", "payload",
                       "frames.bin", b"F" * 500, BIG, t=T0 + 2)
    pair_bytes = sum(len(base64.b64decode(p["b64"])) for p in pair)
    ceiling = pair_bytes + 1000 - 1  # one byte short of all of it
    index.keep_snippet(W, wdir, "digitalvoice", "payload",
                       "frames2.bin", b"G" * 500, ceiling, t=T0 + 3)
    rows = _rows_by_name(index)
    assert rows["excerpt-1.sigmf-data"]["shed"] == 1
    assert rows["excerpt-1.sigmf-meta"]["shed"] == 1
    snip = os.path.join(wdir, "snippets")
    assert not os.path.exists(os.path.join(snip, "excerpt-1.sigmf-data"))
    assert not os.path.exists(os.path.join(snip, "excerpt-1.sigmf-meta"))
    assert rows["frames.bin"]["shed"] == 0
    assert rows["frames2.bin"]["shed"] == 0


# ------------------------------------------------------- SigMF format

def _independent_sigmf_read(data_path, meta_path):
    """A minimal SigMF reader written for this test alone: it
    shares no code with the writer, so agreement between them
    is evidence about the format, not about the module."""
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    raw = open(data_path, "rb").read()
    assert meta["global"]["core:datatype"] == "ci16_le"
    rate = meta["global"]["core:sample_rate"]
    ints = np.frombuffer(raw, dtype="<i2")
    samples = (ints[0::2] + 1j * ints[1::2]) / 32767.0
    return meta, rate, samples


def test_sigmf_round_trip_through_an_independent_reader(tmp_path):
    rng = np.random.default_rng(5)
    src = (rng.normal(0, 0.3, 2000)
           + 1j * rng.normal(0, 0.3, 2000)).astype(np.complex64)
    stem = os.path.join(str(tmp_path), "cap-1")
    count = sigmf.write_pair(stem, src, 48000.0, 146.52e6, T0,
                             bw_hz=12500.0,
                             label="unclassified emission")
    assert count == 2000
    data_path, meta_path = stem + ".sigmf-data", stem + ".sigmf-meta"
    meta, rate, back = _independent_sigmf_read(data_path, meta_path)
    assert rate == 48000.0
    assert len(back) == 2000
    cap = meta["captures"][0]
    assert cap["core:frequency"] == 146.52e6
    assert cap["core:datetime"].endswith("Z")
    ann = meta["annotations"][0]
    assert ann["core:sample_start"] == 0
    assert ann["core:sample_count"] == 2000
    assert ann["core:freq_lower_edge"] == 146.52e6 - 6250.0
    assert ann["core:freq_upper_edge"] == 146.52e6 + 6250.0
    peak = meta["global"]["multidex:peak"]
    err = np.max(np.abs(back * peak - src))
    assert err < 2e-4, err


# ------------------------------------------------------- excerpt feed

FS = 480000.0
CENTER = 146.0e6


def _chirp_window():
    n = int(FS * 4)
    t = np.arange(n) / FS
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.02, n) + 1j * rng.normal(0, 0.02, n)
    phase = 2 * np.pi * (-40000.0 * t + 10000.0 * t * t)
    return noise + 0.5 * np.exp(1j * phase)


def test_census_files_excerpt_for_unclassified_emission(profile):
    """End to end through the census engine and its desk: a
    strong emission the census extracts but cannot classify
    carries a tier 2 SigMF excerpt on its event, readable by
    the independent reader, while classified and data
    emissions carry none."""
    result = ac.analyze(_chirp_window(), FS, CENTER)
    unknowns = [c for c in result["candidates"]
                if c.get("modulation") == "UNKNOWN"]
    assert unknowns, result["candidates"]
    cand = unknowns[0]
    assert cand.get("excerpt"), cand
    exc = cand["excerpt"]
    assert exc["samples"] <= ac.EXCERPT_MAX_SAMPLES
    assert len(base64.b64decode(exc["b64"])) == exc["samples"] * 4
    ctx = {"watch_id": W, "desk": "audiocensus", "t0": T0,
           "t1": T0 + 4, "freq_hz": CENTER, "profile": profile,
           "home": None, "pass": None}
    events = desks.process_audiocensus(result, ctx)
    ev = [e for e in events
          if e["data"].get("iq_excerpt")][0]
    pair = ev["data"]["_snippet"]
    assert isinstance(pair, list) and len(pair) == 2
    names = sorted(p["filename"] for p in pair)
    assert names[0].endswith(".sigmf-data")
    assert names[1].endswith(".sigmf-meta")
    meta = json.loads(base64.b64decode(
        [p for p in pair
         if p["filename"].endswith(".sigmf-meta")][0]["b64"]))
    assert meta["captures"][0]["core:frequency"] == cand["freq_hz"]
    assert meta["annotations"][0]["core:sample_count"] == exc["samples"]


def test_sweep_excerpt_attaches_to_its_observation(profile):
    samples = [0.25 - 0.5j] * 4800
    pair = sigmf.pair_payloads("sweep-1", samples, 48000.0,
                               146.52e6, T0)
    exc = {"b64": pair[0]["b64"], "samples": len(samples),
           "rate": 48000.0, "peak": 0.559, "freq_hz": 146.52e6,
           "bw_hz": 9000.0}
    feed = {"noise_floor_db": -110.0,
            "emissions": [{"freq_hz": 146.52e6, "bw_hz": 9000.0,
                           "peak_dbm": -60.0, "shape": "unknown"}],
            "excerpts": [exc]}
    ctx = {"watch_id": W, "desk": "sweep", "t0": T0, "t1": T0 + 6,
           "freq_hz": 146.52e6, "profile": profile,
           "home": None, "pass": None}
    events = desks.process_sweep(feed, ctx)
    assert len(events) == 1
    ev = events[0]
    assert ev["grade"] == "UNRESOLVED"
    assert ev["data"]["iq_excerpt"].endswith(".sigmf-data")
    assert ev["data"]["interrupt_candidate"] is True


# ------------------------------------------------------- ledger slice

def test_watch_slice_states_tier_composition(home, profile):
    """A watch's slice of the ledger, given its snippet rows,
    states the tier composition and the sealed bytes apart;
    without rows the slice is the 0.4.2 account unchanged."""
    ledger = budget.AllocationLedger.load(home)
    bill = [{"id": "bill-1", "cls": "C", "kind": "dwell",
             "label": "d", "start": T0, "dur": 60,
             "center_hz": 121.5e6, "bw_hz": 400e3,
             "desks": ["airband"], "pass": None}]
    budget.annotate_bill(bill)
    grants, statements, _pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    assert statements == []
    plain = budget.watch_slice(ledger, W)
    assert "by_tier" not in plain
    rows = [
        {"path": "snippets/w--bill-1--a.wav", "kind": "voice",
         "bytes": 100, "shed": 0},
        {"path": "snippets/w--bill-1--b.sigmf-data", "kind": "iq",
         "bytes": 50, "shed": 1},
        {"path": "snippets/w--bill-1--c.bin", "kind": "payload",
         "bytes": 70, "shed": 0},
        {"path": "snippets/w--bill-1--d.wav", "kind": "satellite",
         "bytes": 30, "shed": 0, "sealed": 1},
    ]
    sl = budget.watch_slice(ledger, W, rows)
    assert sl["by_tier"][2]["kept_bytes"] == 100
    assert sl["by_tier"][2]["shed_bytes"] == 50
    assert sl["by_tier"][0]["kept_bytes"] == 70
    assert sl["sealed_bytes"] == 30
    assert sl["sealed_count"] == 1


# ------------------------------------------------------- doctor

def test_doctor_states_the_tier_policy(profile, home, index):
    wdir = index.watch_dir(W)
    index.keep_snippet(W, wdir, "airband", "voice", "v.wav",
                       b"V" * 100, BIG, t=T0)
    index.close()
    text = gates.doctor_text(profile, home)
    assert "Retention: evidence is held in tiers" in text
    assert "Tier 0 is permanent" in text
    assert "Evidence by tier in this home" in text
    assert "sealed 0 bytes held apart" in text


# ------------------------------------------------------- tiny pool e2e

def test_simulated_watch_under_a_tiny_pool_accounts_every_tier(
        home, profile):
    """A simulated watch whose pool is deliberately too small
    for its evidence completes, sheds under the tier law, and
    its Watch Report accounts for every tier."""
    from multidex.index import Index
    from multidex.officer import WatchRunner
    profile["watch"]["disk_ceiling_gb"] = 0.002
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
    report = open(os.path.join(home, "watches", watch_id,
                               "REPORT.md")).read()
    assert "Reception account" in report
    for tier in (0, 1, 2, 3):
        assert "Tier %d (" % tier in report
    acct = Index(home).snippet_accounting(watch_id)
    assert acct["by_tier"][1]["kept_bytes"] > 0, (
        "the dwell spectrum products are tier 1 evidence and "
        "a two-hour watch files them at every dwell")
