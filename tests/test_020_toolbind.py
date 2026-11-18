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
    calls = tuner._run_dsd_fme(851750000.0, 0.5)
    protos = {c["protocol"] for c in calls}
    assert "DMR" in protos
    dmr = [c for c in calls if c["protocol"] == "DMR"][0]
    assert dmr["talkgroup"] == "100"
    assert dmr["source"] == "123456"
    assert dmr["slot"] == 1
