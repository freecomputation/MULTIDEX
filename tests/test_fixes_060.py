"""0.6.0: the METEOR acknowledgement policy and the vocoder
frame archive.

Part 1 retires the LRPT reception path. A METEOR pass is
acknowledged only: predicted and billed, its pass event states
the acknowledgement and that nothing was recorded, neither
source attempts a capture, its allocation is zero bytes, no
reservation or grant is taken for it, and a ledger holding
METEOR reservations from an earlier build releases them on
load with the release stated. NOAA APT passes are untouched.

Part 2 gives the digital voice desk a frame archive. The bound
DSD-FME program exposes the de-interleaved vocoder parameter
frames through its mbe data files (its -d option): the same
demodulated, error-corrected parameter arrays its synthesizer
consumes, written per slot as a 4-byte cookie plus one record
per 20 ms frame (an error byte and the packed parameter bits -
49 bits in 7 bytes for AMBE+2, 88 bits in 11 for IMBE). The
desk archives them as payload-class evidence: linked to the
event and the dossier, counted at true size, never shed.
"""

import base64
import json
import os

from multidex import budget, desks, gates, products, toolbind, tuner
from multidex.sim import SimSource, synthetic_mbe_archive
from multidex.tuner import RealSource

from conftest import T0

W = "watch-t"

ACK_CONTENT = (
    "pass acknowledged, not recorded: METEOR passes are "
    "acknowledged only under the acknowledgement policy - no "
    "capture was attempted, no audio or IQ was filed, and the "
    "pass carries a zero-byte reception allocation")


def _pass(sat="METEOR-M2 4", norad="59051", window=600):
    return {"sat": sat, "norad": norad, "aos": T0, "los": T0 + window,
            "t_max": T0 + window // 2, "max_elev_deg": 55.0,
            "aos_az_deg": 90.0, "los_az_deg": 270.0}


def _sat_ctx(**kw):
    ctx = {"watch_id": W, "desk": "satellite", "t0": T0,
           "t1": T0 + 600, "freq_hz": 137.9e6, "home": (39.0, -98.0),
           "profile": {}, "pass": _pass()}
    ctx.update(kw)
    return ctx


# ------------------------------------------- Part 1: the sources

def test_sim_meteor_feed_attempts_no_capture(profile):
    src = SimSource(profile, T0)
    vfo = {"desk": "satellite", "freq_hz": 137.9e6,
           "pass": {"sat": "METEOR-M2 4", "norad": "59051",
                    "aos": T0, "los": T0 + 600, "max_elev_deg": 55.0}}
    feed = src._satellite_feed(vfo)
    assert feed["acknowledged"] is True
    assert feed["sat"] == "METEOR-M2 4"
    for key in ("lock", "quality", "audio_b64", "iq_b64",
                "iq_file"):
        assert key not in feed


def test_real_meteor_feed_attempts_no_capture():
    source = RealSource({})

    def forbidden_scan(*a, **k):
        raise AssertionError(
            "a METEOR pass must not be scanned for a capture")

    source.scan = forbidden_scan
    vfo = {"desk": "satellite", "freq_hz": 137.9e6, "bw_hz": 150e3,
           "pass": {"sat": "METEOR-M2 4", "norad": "59051",
                    "aos": T0, "los": T0 + 600}}
    feed = source._feed_satellite([vfo], 137.9e6, 2.4e6, T0,
                                  T0 + 600)
    assert feed == {"sat": "METEOR-M2 4", "norad": "59051",
                    "acknowledged": True}


def test_acknowledgement_event_wording_and_shape(profile):
    # The event a METEOR pass files, from the real source's feed
    # shape: the exact policy wording, the acknowledgement in
    # the data, and nothing filed.
    feed = {"sat": "METEOR-M2 4", "norad": "59051",
            "acknowledged": True}
    events = desks.process_satellite(feed, _sat_ctx())
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "pass" and ev["desk"] == "satellite"
    assert ev["grade"] == "CONFIRMED"
    assert ev["emitter"] == "METEOR-M2 4"
    assert ev["content"] == ACK_CONTENT
    assert ev["data"]["acknowledged"] is True
    assert ev["data"]["recorded"] is False
    assert ev["data"]["allocation_bytes"] == 0
    assert ev["data"]["policy"] == "acknowledgement-only"
    assert "_snippet" not in ev["data"]


def test_every_meteor_name_acknowledges(profile):
    for sat in ("METEOR-M2 3", "METEOR-M2 4", "METEOR-M 2",
                "METEOR-M2"):
        feed = {"sat": sat, "norad": "00000", "acknowledged": True}
        ctx = _sat_ctx(**{"pass": _pass(sat)})
        ev = desks.process_satellite(feed, ctx)[0]
        assert ev["content"] == ACK_CONTENT, sat


# ------------------------------------- Part 1: money and the ledger

def test_refresh_reserves_never_reserves_meteor(profile, home):
    sats, _path = gates.load_sats(profile)
    assert any("METEOR" in s["name"] for s in sats)
    ledger = budget.AllocationLedger.load(home)
    created, _expired = budget.refresh_reserves(
        ledger, profile, sats, T0)
    assert created >= 1  # the NOAA passes ahead hold reservations
    for grant in ledger.grants:
        assert "METEOR" not in (grant.get("sat") or "")
    assert any("NOAA" in (g.get("sat") or "")
               for g in ledger.select(kind="reserve",
                                      status="open"))


def _seed_grant(gid, sat, nbytes, consumed=0):
    return {"id": gid, "kind": "reserve", "owner": "standing",
            "bill_id": None, "cls": "A", "desks": ["satellite"],
            "label": "Satellite pass, %s" % sat, "sat": sat,
            "downlink": "lrpt" if "METEOR" in sat else "apt",
            "bytes": nbytes, "computed_bytes": nbytes,
            "tuner_s": 600, "consumed_bytes": consumed,
            "shed_bytes": 0, "released_bytes": 0, "status": "open",
            "granted_at": T0, "window_start": T0 + 3600,
            "window_end": T0 + 4200, "closed_at": None,
            "close_reason": None, "adopted_reserve": False}


def test_ledger_releases_meteor_reservations_on_load(home):
    os.makedirs(home, exist_ok=True)
    doc = {"format": budget.LEDGER_FORMAT, "cap_bytes": 50000000,
           "grants": [
               _seed_grant("grant-m1", "METEOR-M2 4", 1000000),
               _seed_grant("grant-m2", "METEOR-M2 3", 2000000,
                           consumed=500000),
               _seed_grant("grant-n1", "NOAA 19", 17000000)]}
    with open(os.path.join(home, budget.LEDGER_FILENAME),
              "w") as fh:
        json.dump(doc, fh)
    ledger = budget.AllocationLedger.load(home)
    m1 = ledger.find("grant-m1")
    m2 = ledger.find("grant-m2")
    n1 = ledger.find("grant-n1")
    # The METEOR reservations are released, with the release on
    # each grant: unspent bytes return, consumed bytes stay in
    # the history.
    for grant, released in ((m1, 1000000), (m2, 1500000)):
        assert grant["status"] == "closed"
        assert grant["released_bytes"] == released
        assert "acknowledgement policy" in grant["close_reason"]
        assert grant["closed_at"] is not None
    assert m2["consumed_bytes"] == 500000
    # The NOAA reservation stands untouched.
    assert n1["status"] == "open"
    assert ledger.pool()["satellite_reserve_bytes"] == 17000000
    # The release is stated for the loading surface, once.
    assert len(ledger.load_statements) == 1
    assert "METEOR" in ledger.load_statements[0]
    assert "2500000" in ledger.load_statements[0]
    again = budget.AllocationLedger.load(home)
    assert again.load_statements == []
    assert again.find("grant-m1")["status"] == "closed"


def test_doctor_states_the_meteor_release(profile, home):
    os.makedirs(home, exist_ok=True)
    doc = {"format": budget.LEDGER_FORMAT, "cap_bytes": 50000000,
           "grants": [_seed_grant("grant-m1", "METEOR-M2 4",
                                  1000000)]}
    with open(os.path.join(home, budget.LEDGER_FILENAME),
              "w") as fh:
        json.dump(doc, fh)
    text = gates.doctor_text(profile, home)
    assert "released 1 open METEOR reservation(s)" in text


# ------------------------------------------- Part 2: the container

def test_synthetic_archive_container_shape():
    arc = synthetic_mbe_archive("AMBE+2", frames=50, slot=1)
    raw = base64.b64decode(arc["b64"])
    assert raw[:4] == b".amb"
    # 50 frames at 8 container bytes each, behind the cookie.
    assert len(raw) == 4 + 50 * 8 == arc["bytes"]
    # The parameter payload alone is the task's figure: 49 bits
    # per 20 ms frame is 306.25 bytes per second of speech.
    assert 50 * 49 / 8 == 306.25
    imb = synthetic_mbe_archive("IMBE", frames=50, slot=2)
    raw = base64.b64decode(imb["b64"])
    assert raw[:4] == b".imb"
    assert len(raw) == 4 + 50 * 12 == imb["bytes"]


def test_harvest_mbe_frames_reads_the_containers(tmp_path):
    d = tmp_path / "mbe"
    d.mkdir()
    amb = b".amb" + bytes(100 * 8)
    imb = b".imb" + bytes(50 * 12)
    (d / "20261118_031000_ab12_S1.amb").write_bytes(amb)
    (d / "20261118_031100_cd34_S2.imb").write_bytes(imb)
    # A file whose cookie does not match its extension is not
    # an archive; neither is an unrelated file.
    (d / "20261118_031200_ef56_S1.amb").write_bytes(
        b"XXXX" + bytes(80))
    (d / "notes.txt").write_bytes(b".amb" + bytes(80))
    out = tuner._harvest_mbe_frames(str(d))
    assert len(out) == 2
    by_fmt = {a["format"]: a for a in out}
    a = by_fmt["AMBE+2"]
    assert a["frames"] == 100 and a["slot"] == 1
    assert a["param_bits"] == 49 and a["bytes"] == len(amb)
    assert base64.b64decode(a["b64"]) == amb
    i = by_fmt["IMBE"]
    assert i["frames"] == 50 and i["slot"] == 2
    assert i["param_bits"] == 88 and i["bytes"] == len(imb)


FAKE_DSD = """#!/usr/bin/env python3
import os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_DSD_ARGV"], "w") as fh:
    fh.write("\\n".join(argv))
d = argv[argv.index("-d") + 1]
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "20261118_031000_ab12_S1.amb"),
          "wb") as fh:
    fh.write(b".amb" + bytes(25 * 8))
print("DMR Slot 1 TG 42 SRC 1001")
"""


def test_run_dsd_fme_harvests_and_associates_by_slot(tmp_path,
                                                     monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "dsd-fme"
    script.write_text(FAKE_DSD)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_DSD_ARGV", str(tmp_path / "argv.txt"))
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + "/usr/bin:/bin")
    toolbind.reset_cache()
    feed = tuner._run_dsd_fme(460000000, 5)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert "-d" in argv  # the mbe data output was requested
    assert len(feed["calls"]) == 1
    call = feed["calls"][0]
    assert call["protocol"] == "DMR" and call["slot"] == 1
    assert call["talkgroup"] == "42" and call["source"] == "1001"
    assert feed["frame_archives"] == []
    assert len(call["frames"]) == 1
    arc = call["frames"][0]
    assert arc["frames"] == 25 and arc["format"] == "AMBE+2"
    assert arc["association"] == "slot"
    assert base64.b64decode(arc["b64"]) == b".amb" + bytes(25 * 8)


# ---------------------------------------------- Part 2: the desk

def _dv_ctx():
    return {"watch_id": W, "desk": "digitalvoice", "t0": T0,
            "t1": T0 + 60, "freq_hz": 460e6, "home": (39.0, -98.0),
            "profile": {}, "pass": None}


def _call(frames=None, **kw):
    call = {"protocol": "DMR", "system": "SYS:TEST-1",
            "talkgroup": "42", "source": "1001", "slot": 1,
            "encrypted": False, "freq_hz": 460e6}
    call.update(kw)
    if frames is not None:
        call["frames"] = frames
    return call


def test_desk_archives_ambe_frames_with_the_gate_stated():
    arc = synthetic_mbe_archive("AMBE+2", frames=50, slot=1)
    feed = {"calls": [_call(frames=[arc])], "frame_archives": []}
    ev = desks.process_digitalvoice(feed, _dv_ctx())[0]
    assert "vocoder frames archived as payload evidence: " \
           "50 AMBE+2 parameter frames" in ev["content"]
    assert "archived undecodable until the in-house " \
           "synthesizer exists and the US8359197 gate opens " \
           "on 2028-05-20" in ev["content"]
    rec = ev["data"]["frame_archives"][0]
    assert rec["format"] == "AMBE+2" and rec["frames"] == 50
    assert rec["bytes"] == arc["bytes"] and rec["slot"] == 1
    assert rec["synthetic"] is True
    payload = ev["data"]["_snippet"]
    assert payload["kind"] == "payload"
    assert base64.b64decode(payload["b64"]) == \
        base64.b64decode(arc["b64"])


def test_desk_archives_imbe_frames_with_the_gate_stated():
    arc = synthetic_mbe_archive("IMBE", frames=25, slot=2)
    feed = {"calls": [_call(frames=[arc], protocol="P25",
                            slot=2)],
            "frame_archives": []}
    ev = desks.process_digitalvoice(feed, _dv_ctx())[0]
    assert "25 IMBE parameter frames" in ev["content"]
    assert "the frames await the in-house IMBE build" \
        in ev["content"]
    assert ev["data"]["frame_archives"][0]["param_bits"] == 88


def test_encrypted_call_archives_no_frames():
    # The feed carries no frames for an encrypted call (the
    # bound program writes none it cannot hear), and the event
    # claims none.
    feed = {"calls": [_call(encrypted=True)], "frame_archives": []}
    ev = desks.process_digitalvoice(feed, _dv_ctx())[0]
    assert "encrypted" in ev["content"]
    assert "frame_archives" not in ev["data"]
    assert "_snippet" not in ev["data"]


def test_unassociated_frames_still_archive():
    arc = synthetic_mbe_archive("AMBE+2", frames=30, slot=2)
    # The tuner's unassociated archives carry no association of
    # their own; the desk files them under the capture window.
    arc = {k: v for k, v in arc.items() if k != "association"}
    feed = {"calls": [], "frame_archives": [arc]}
    events = desks.process_digitalvoice(feed, _dv_ctx())
    assert len(events) == 1
    ev = events[0]
    assert ev["grade"] == "PROBABLE"
    assert "whose signalling the log parser did not read" \
        in ev["content"]
    assert "30 AMBE+2 parameter frames" in ev["content"]
    rec = ev["data"]["frame_archives"][0]
    assert rec["association"] == "capture window"
    assert ev["data"]["_snippet"]["kind"] == "payload"


# ---------------------------------------------- Part 2: the store

def test_archive_filed_linked_counted_and_never_shed(home,
                                                      index):
    arc = synthetic_mbe_archive("AMBE+2", frames=100, slot=1)
    raw = base64.b64decode(arc["b64"])
    feed = {"calls": [_call(frames=[arc])], "frame_archives": []}
    ev = desks.process_digitalvoice(feed, _dv_ctx())[0]
    payload = ev["data"].pop("_snippet")
    watch_dir = index.watch_dir(W)
    # Ordinary evidence files first, older than the archive.
    for i in range(3):
        rel, _shed = index.keep_snippet(
            W, watch_dir, "airband", "voice", "clip-%d.wav" % i,
            data=b"\x01" * 1000, ceiling_bytes=10 ** 9, t=T0 + i)
        assert rel
    rel, _shed = index.keep_snippet(
        W, watch_dir, "digitalvoice", "payload",
        "%s-bill-001-%s" % (W, payload["filename"]), data=raw,
        ceiling_bytes=10 ** 9, t=T0 + 10)
    assert rel
    # The event and its dossier carry the archive.
    ev["snippet"] = rel
    index.add_event(ev)
    stored = index.events(watch_id=W, kinds=["decode"])
    assert stored[0]["snippet"] == rel
    assert stored[0]["data"]["frame_archives"][0]["frames"] == 100
    dossier = index.dossier("SYS:TEST-1")
    assert dossier is not None and dossier["events_count"] == 1
    # The archived bytes read back identical.
    with open(os.path.join(watch_dir, rel), "rb") as fh:
        assert fh.read() == raw
    # Counted at true size in the account.
    acct = index.snippet_accounting(W)
    assert acct["payload_count"] == 1
    assert acct["payload_bytes"] == len(raw)
    assert acct["kept_bytes"] == 3000 + len(raw)
    # Pressure: a large ordinary snippet under a small ceiling
    # sheds the ordinary snippets around the archive; the
    # archive is never a victim, and when nothing sheddable
    # remains the ordinary snippet is refused instead.
    rel2, shed = index.keep_snippet(
        W, watch_dir, "airband", "voice", "big.wav",
        data=b"\x02" * 2000, ceiling_bytes=2500, t=T0 + 20)
    assert rel2 is None
    assert len(shed) == 3
    assert all(v["kind"] == "voice" for v in shed)
    rows = {r["path"]: r for r in index.snippet_rows(W)}
    assert rows[rel]["shed"] == 0
    with open(os.path.join(watch_dir, rel), "rb") as fh:
        assert fh.read() == raw
    acct = index.snippet_accounting(W)
    assert acct["payload_bytes"] == len(raw)
    assert acct["kept_bytes"] == len(raw)


def test_payload_filed_even_when_nothing_sheddable(home, index):
    arc = synthetic_mbe_archive("IMBE", frames=10, slot=1)
    raw = base64.b64decode(arc["b64"])
    watch_dir = index.watch_dir(W)
    rel, _shed = index.keep_snippet(
        W, watch_dir, "digitalvoice", "payload",
        arc["source_name"], data=raw, ceiling_bytes=10, t=T0)
    assert rel is not None
    with open(os.path.join(watch_dir, rel), "rb") as fh:
        assert fh.read() == raw


def test_report_states_payload_evidence_and_exemption(home,
                                                       profile,
                                                       index):
    arc = synthetic_mbe_archive("AMBE+2", frames=100, slot=1)
    raw = base64.b64decode(arc["b64"])
    bill = [{"id": "bill-001", "cls": "B", "kind": "dwell",
             "label": "dwell bill-001", "start": T0, "dur": 60,
             "center_hz": 460e6, "bw_hz": 400e3,
             "desks": ["digitalvoice"], "pass": None}]
    budget.annotate_bill(bill)
    ledger = budget.AllocationLedger.load(home)
    grants, _statements, pool = budget.request_watch_grants(
        ledger, profile, W, bill, T0)
    watch_dir = index.watch_dir(W)
    rel, _shed = index.keep_snippet(
        W, watch_dir, "digitalvoice", "payload",
        "%s-bill-001-%s" % (W, arc["source_name"]), data=raw,
        ceiling_bytes=10 ** 9, t=T0)
    assert rel
    index.set_watch_meta(W, {
        "watch_id": W, "start": T0, "planned_end": T0 + 86400,
        "end": T0 + 3600, "source": "simulated",
        "profile_name": "t", "region": "the test region",
        "antenna": "test antenna", "disk_ceiling_gb": 20,
        "ended_early": False, "end_reason": None,
        "resumed": False, "reception_pool": pool,
        "reception_grants": {
            "granted_bytes": grants["bill-001"]["bytes"],
            "satellite_bytes": 0,
            "by_class": {"B": grants["bill-001"]["bytes"]},
            "statements": []}})
    report = products.render_report(index, W)
    assert "Payload-class evidence (vocoder frame archives): " \
           "1 files, %d bytes kept" % len(raw) in report
    assert "exempt from shedding" in report
