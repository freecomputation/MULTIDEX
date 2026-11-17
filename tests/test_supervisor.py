"""The supervisor: crash, restart once, bench on the second crash."""

import os

from multidex.supervisor import Supervisor

CRASHY = '''
def process(feed, ctx):
    raise RuntimeError("boom")
'''

FLAKY = '''
import os
MARKER = os.path.join(os.path.dirname(__file__), "flaky.count")

def process(feed, ctx):
    try:
        n = int(open(MARKER).read())
    except OSError:
        n = 0
    open(MARKER, "w").write(str(n + 1))
    if n == 0:
        raise RuntimeError("first call only")
    return [{"ok": True}]
'''


def _ctx():
    return {"watch_id": "watch-s", "desk": "x", "t0": 1000.0,
            "t1": 1060.0, "freq_hz": 0.0, "home": (39.0, -98.0),
            "profile": {}, "pass": None}


def test_always_crashing_desk_is_benched(tmp_path, monkeypatch):
    (tmp_path / "crashy.py").write_text(CRASHY)
    monkeypatch.setenv("MULTIDEX_DESK_DIR", str(tmp_path))
    sup = Supervisor()
    try:
        events, info = sup.run("crashy", {}, _ctx())
        assert events == []
        assert info["benched"] is True
        assert info["crashes"] == 2
        assert sup.is_benched("crashy")
        # A benched desk is not spawned again.
        events, info = sup.run("crashy", {}, _ctx())
        assert events == [] and info["benched"] is True
    finally:
        sup.shutdown()


def test_flaky_desk_restarts_and_recovers(tmp_path, monkeypatch):
    (tmp_path / "flaky.py").write_text(FLAKY)
    monkeypatch.setenv("MULTIDEX_DESK_DIR", str(tmp_path))
    sup = Supervisor()
    try:
        events, info = sup.run("flaky", {}, _ctx())
        assert events == [{"ok": True}]
        assert info.get("restarted") is True
        assert not sup.is_benched("flaky")
    finally:
        sup.shutdown()


def test_real_desk_through_the_pipe():
    sup = Supervisor()
    try:
        events, info = sup.run(
            "acars",
            {"messages": ["ACARS MODE 2 LABEL Q0 TAIL N1 FLIGHT F1 "
                          "MSG PIPE OK"]},
            _ctx())
        assert len(events) == 1
        assert events[0]["content"] == "PIPE OK"
        assert info["crashes"] == 0
    finally:
        sup.shutdown()
