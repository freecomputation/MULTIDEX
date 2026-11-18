"""0.2.0 tool-bound desks: bench-or-stand follows the host tool
set, and the tool output parsers read the programs' real output
shapes (exercised against stub programs on the PATH)."""

import os
import stat

import numpy as np
import pytest

from multidex import desks, toolbind, tuner


@pytest.fixture()
def stub_tools(tmp_path, monkeypatch):
    def _make(name, body):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n%s\n" % body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(
        "PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    toolbind.reset_cache()
    yield _make
    toolbind.reset_cache()


def test_vdl2_benches_without_dumpvdl2(profile):
    toolbind.reset_cache()
    if toolbind.tool_available("dumpvdl2"):
        pytest.skip("host carries dumpvdl2")
    state, reason = desks.desk_state("vdl2", profile, "real", set())
    assert state == "benched"
    assert "dumpvdl2" in reason and "user space" in reason


def test_jt9_line_parser():
    line = "000130 -18  0.3 1234 ~  CQ W1ABC FN42"
    m = tuner._JT8_LINE.match(line)
    assert m and m.group(4) == "CQ W1ABC FN42"
    assert tuner._JT8_LINE.match("garbage line") is None


def test_run_jt8_with_stub(stub_tools, tmp_path):
    stub_tools("jt9", "echo '000130 -18  0.3 1234 ~  CQ W1ABC FN42'")
    out = tuner._run_jt8(np.zeros(12000), 14074000.0)
    assert len(out) == 1
    assert out[0]["text"] == "CQ W1ABC FN42"
    assert out[0]["snr_db"] == -18
    assert out[0]["freq_hz"] == 14074000 + 1234


def test_dsd_fme_parser_with_stub(stub_tools):
    stub_tools(
        "dsd-fme",
        "echo 'DMR BS DCC:1 TG: 100 SRC: 123456 SLOT: 1 voice'\n"
        "echo 'P25 NAC: 0x123 Talkgroup: 200 SRC: 777'")
    # Since 0.6.0 the run returns the whole feed: the calls the
    # parser read, plus the frame archives harvested from the
    # program's mbe data files (the shell stub writes none).
    feed = tuner._run_dsd_fme(851750000.0, 0.5)
    calls = feed["calls"]
    assert feed["frame_archives"] == []
    protos = {c["protocol"] for c in calls}
    assert "DMR" in protos
    dmr = [c for c in calls if c["protocol"] == "DMR"][0]
    assert dmr["talkgroup"] == "100"
    assert dmr["source"] == "123456"
    assert dmr["slot"] == 1


def test_vdl2_stands_with_dumpvdl2_stub(stub_tools, profile):
    stub_tools("dumpvdl2", "exit 0")
    assert toolbind.tool_available("dumpvdl2")
    caps = tuner.RealSource(profile).capabilities()
    assert "vdl2" in caps
    state, reason = desks.desk_state("vdl2", profile, "real", caps)
    assert state == "standing" and reason is None
    reasons = desks.bench_reasons(profile, "real", caps)
    assert "vdl2" not in reasons


def test_digitalvoice_benches_without_dsd_fme(profile):
    toolbind.reset_cache()
    if toolbind.tool_available("dsd-fme"):
        pytest.skip("host carries dsd-fme")
    state, reason = desks.desk_state("digitalvoice", profile,
                                     "real", set())
    assert state == "benched"
    assert "DSD-FME" in reason and "user space" in reason


def test_digitalvoice_stands_with_dsd_fme_stub(stub_tools, profile):
    stub_tools("dsd-fme", "exit 0")
    assert toolbind.tool_available("dsd-fme")
    caps = tuner.RealSource(profile).capabilities()
    assert "digitalvoice" in caps
    state, reason = desks.desk_state("digitalvoice", profile,
                                     "real", caps)
    assert state == "standing" and reason is None
    reasons = desks.bench_reasons(profile, "real", caps)
    assert "digitalvoice" not in reasons


# ------------------------------------------------------- dumpvdl2 2.7.0

# Genuine dumpvdl2 2.7.0 JSON output, the two AVLC frames decoded
# from the project's shipped model recording (fixture staged with
# the program on the reference host). Neither frame carries a
# libacars ACARS block - the payloads are X.25 - so the extraction
# must yield no messages from them and invent none.
GENUINE_VDL2_LINES = [
    '{"vdl2":{"app":{"name":"dumpvdl2","ver":"2.7.0"},"t":{"sec":1794983377,"usec":558151},"freq":136975000,"burst_len_octets":504,"hdr_bits_fixed":0,"octets_corrected_by_fec":0,"idx":0,"sig_level":-9.840716,"noise_level":1.799104,"freq_skew":-0.070450,"avlc":{"src":{"addr":"345678","type":"Ground station","status":"On ground"},"dst":{"addr":"A23721","type":"Aircraft"},"cr":"Command","frame_type":"I","rseq":1,"sseq":1,"poll":false,"x25":{"err":true,"unknown_proto":{"data":[84,65,70,32,65,77,68,32,75,82,79,67,32,48,56,49,50,52,50,90,32,48,56,49,51,49,50,32,48,52,48,49,50,75,84,32,50,83,77,32,82,65,32,66,82,32,83,67,84,48,48,54,32,79,86,67,48,49,53,32,84,69,77,80,79,32,49,51,49,52,32,51,83,77,32,45,82,65,32,66,82,32,66,75,78,48,48,54,32,79,86,67,48,49,53,10,32,70,77,49,52,48,48,32,48,52,48,49,50,71,49,56,75,84,32,53,83,77,32,66,82,32,83,67,84,48,48,54,32,79,86,67,48,49,53,32,84,69,77,80,79,32,49,52,50,48,32,51,83,77,32,45,82,65,32,66,82,32,79,86,67,48,48,54,10,32,70,77,50,48,48,48,32,48,52,48,49,51,71,50,48,75,84,32,53,83,77,32,45,82,65,32,66,82,32,66,75,78,48,48,54,32,79,86,67,48,49,50,32,84,69,77,80,79,32,50,48,50,52,32,50,83,77,32,82,65,32,66,82,32,79,86,67,48,48,51,10,32,70,77,48,48,48,48,32,48,54,48,49,50,75,84,32,52,83,77,32,66,82,32,83,67,84,48,48,53,32,79,86,67,48,49,50,32,84,69,77,80,79,32,48,48,49,50,32,50,83,77,32,45,82,65,32,66,82,32,79,86,67,48,48,53,10]}}}}}',
    '{"vdl2":{"app":{"name":"dumpvdl2","ver":"2.7.0"},"t":{"sec":1794983377,"usec":558151},"freq":136975000,"burst_len_octets":504,"hdr_bits_fixed":0,"octets_corrected_by_fec":0,"idx":1,"sig_level":-9.840716,"noise_level":1.799104,"freq_skew":-0.070450,"avlc":{"src":{"addr":"345678","type":"Ground station","status":"On ground"},"dst":{"addr":"A23721","type":"Aircraft"},"cr":"Command","frame_type":"I","rseq":2,"sseq":1,"poll":true,"x25":{"err":true,"unknown_proto":{"data":[77,69,84,65,82,32,75,82,79,67,32,48,56,49,51,53,52,90,32,48,51,48,49,52,75,84,32,54,83,77,32,45,82,65,32,66,82,32,66,75,78,48,48,52,32,79,86,67,48,49,48,32,48,49,47,48,49,32,65,50,57,54,54,32,82,77,75,32,65,79,50,32,83,76,80,48,53,48,32,76,75,32,84,77,80,32,51,57,10,77,69,84,65,82,32,75,68,67,65,32,48,56,49,51,53,51,90,32,49,56,48,48,56,75,84,32,50,83,77,32,45,68,90,32,66,82,32,79,86,67,48,48,54,32,49,55,47,49,55,32,65,50,57,57,51,32,82,77,75,32,68,90,66,52,52,32,67,73,71,32,48,48,53,86,48,48,55,32,83,76,80,49,51,53,10]}}}}}',
]


@pytest.fixture()
def dumpvdl2_rig(stub_tools, tmp_path):
    """PATH stubs for the whole _run_dumpvdl2 chain: rtl_sdr
    writes dummy IQ bytes to its output file; dumpvdl2 records
    the argv it was called with and writes the prepared lines to
    the file its --output specifier names."""
    record = tmp_path / "dumpvdl2.argv"
    lines = tmp_path / "dumpvdl2.out"

    def _rig(out_lines):
        lines.write_text("".join(l + "\n" for l in out_lines))
        stub_tools(
            "rtl_sdr",
            "for last; do :; done\n"
            "printf 'IQIQIQIQIQIQ' > \"$last\"")
        stub_tools(
            "dumpvdl2",
            "printf '%%s\n' \"$@\" > %s\n"
            "for a in \"$@\"; do case \"$a\" in "
            "decoded:json:file:path=*) out=${a#decoded:json:file:path=} ;; "
            "esac; done\n"
            "cat %s > \"$out\"" % (record, lines))
        return record

    return _rig


def test_dumpvdl2_argv_contract_270(dumpvdl2_rig):
    record = dumpvdl2_rig([])
    msgs = tuner._run_dumpvdl2(None, 0.0, 45.0)
    assert msgs == []
    argv = record.read_text().splitlines()
    assert "--sample-rate" not in argv
    assert argv[0] == "--iq-file"
    assert argv[2] == "--output"
    assert argv[3].startswith("decoded:json:file:path=")
    assert len(argv[3]) > len("decoded:json:file:path=")
    assert len(argv) == 4


def test_dumpvdl2_genuine_270_output_parses(dumpvdl2_rig):
    dumpvdl2_rig(GENUINE_VDL2_LINES)
    msgs = tuner._run_dumpvdl2(None, 0.0, 45.0)
    assert msgs == []


def test_dumpvdl2_acars_extraction(dumpvdl2_rig):
    import json as _json
    line = _json.dumps({"vdl2": {
        "app": {"name": "dumpvdl2", "ver": "2.7.0"},
        "avlc": {
            "src": {"addr": "109A2A", "type": "Ground station"},
            "dst": {"addr": "A23721", "type": "Aircraft"},
            "acars": {"reg": ".N12345", "label": "H1",
                      "msg_text": "TEST MESSAGE"}}}})
    dumpvdl2_rig([line])
    msgs = tuner._run_dumpvdl2(None, 0.0, 45.0)
    assert len(msgs) == 1
    assert msgs[0]["tail"] == ".N12345"
    assert msgs[0]["text"] == "ACARS over VDL2 label H1: TEST MESSAGE"
