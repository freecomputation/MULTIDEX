"""0.1.3 regression tests: the ADS-B capture gain and slice length.

Field proof (2026-11-17): from a rural indoor antenna the 1090 MHz
band reads at the idle floor at the house gain of 23, while maximum
gain decodes airliners cleanly (31 CRC-valid frames in 30 s). The
ADS-B feed therefore captures ten seconds at maximum gain; every
other capture keeps the house gain.
"""

from multidex import tuner
from multidex.tuner import RealSource


def test_feed_adsb_captures_max_gain_ten_seconds():
    source = RealSource({})
    calls = []

    def recorder(center_hz, rate, nsamp, seconds=40, gain="23"):
        calls.append((center_hz, rate, nsamp, seconds, gain))
        return b""

    source._iq = recorder
    feed = source._feed_adsb([], 1090e6, 2.4e6, 0.0, 10.0)
    assert feed == {"frames": []}
    assert calls == [(1090e6, 2000000, 20000000, 60, "49.6")]


def test_iq_default_gain_is_the_house_gain(monkeypatch):
    commands = []

    def fake_stream(cmd, path, seconds):
        commands.append(cmd)
        with open(path, "wb") as fh:
            fh.write(b"\x7f\x7f" * 10)
        return 20

    monkeypatch.setattr(tuner, "_stream_to_file", fake_stream)
    source = RealSource({})
    source._iq(1090e6, 2000000, 1000)
    source._iq(1090e6, 2000000, 1000, gain="49.6")
    gains = [cmd[cmd.index("-g") + 1] for cmd in commands]
    assert gains == ["23", "49.6"]
