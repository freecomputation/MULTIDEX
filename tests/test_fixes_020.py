"""0.3.0 regression tests: satellite imagery.

Field facts this build rests on (2026-11-17/18): the satellite
feed used to record an 8-second sample of a pass at the house
gain, and the one real pass recording it produced held no APT
subcarrier at all - a flat noise spectrum, undecodable in
principle. APT imagery needs the whole pass, recorded at maximum
gain like the ADS-B capture (0.1.3), and a decoder. Through
0.4.x the decoder was the aptdec program, bound when staged on
the host and stated as absent when it was not; 0.5.0 retired
that binding for the package's own APT decoder (multidex.apt),
and the content-level ground truth for the decoder lives in
tests/test_fixes_050.py. METEOR-M2 4 flies LRPT, a digital
downlink the APT decoder does not address; through 0.4.0 its
passes said imagery was not applicable, and 0.4.1 recorded the
pass as raw IQ for a bound post-pass decoder. The 0.6.0
acknowledgement policy supersedes both: a METEOR pass is
acknowledged only, and the test below pins that contract -
see tests/test_fixes_060.py for the full policy suite.
"""

import array
import base64
import os
import struct
import time

from multidex import desks, products, tuner
from multidex.sim import SimSource, synthetic_apt_samples
from multidex.tuner import RealSource, _resample_int16, _wav_b64

from conftest import T0

W = "watch-t"


def _ctx(profile, desk, t0=T0, t1=T0 + 600, **kw):
    ctx = {"watch_id": W, "desk": desk, "t0": t0, "t1": t1,
           "freq_hz": 137.1e6,
           "home": (profile["home"]["lat"], profile["home"]["lon"]),
           "profile": profile, "pass": None}
    ctx.update(kw)
    return ctx


def _pass(**kw):
    p = {"aos": T0, "los": T0 + 600, "t_max": T0 + 300,
         "max_elev_deg": 55.0, "aos_az_deg": 90.0, "los_az_deg": 270.0}
    p.update(kw)
    return p


def _apt_feed(sat="NOAA 19", norad="33591", seconds=12, rate=48000):
    samples = synthetic_apt_samples(seconds, rate)
    return {"lock": True, "quality": 0.8, "sat": sat, "norad": norad,
            "audio_b64": _wav_b64(samples, rate),
            "audio_name": "pass-%s-%d.wav" % (norad, int(T0)),
            "audio_rate": rate}


def _png_dims(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


# ------------------------------------------------- the imagery step

def test_apt_imagery_decodes_from_spec_shaped_wav(profile):
    events = desks.process_satellite(
        _apt_feed(), _ctx(profile, "satellite", **{"pass": _pass()}))
    ev = events[0]
    assert ev["grade"] == "CONFIRMED"
    image_name = "pass-33591-%d.png" % int(T0)
    assert ev["data"]["imagery"] == image_name
    # The FLASH product's What line is the event content: it must
    # name the image.
    assert image_name in ev["content"]
    snippets = ev["data"]["_snippet"]
    assert isinstance(snippets, list) and len(snippets) == 2
    assert snippets[0]["filename"].endswith(".wav")
    assert snippets[1]["filename"] == image_name
    width, height = _png_dims(base64.b64decode(snippets[1]["b64"]))
    assert width == 2080
    # 12 s at 2 lines/s, every line framed on its own sync by the
    # package's decoder (the retired aptdec binding framed 22,
    # dropping the edge lines; the content proof for all 24 is
    # in tests/test_fixes_050.py).
    assert height == 24
    assert ev["data"]["imagery_decode"]["lines"] == 24


def test_apt_imagery_decodes_the_stored_11025_copy(profile):
    # The real source stores pass audio resampled to 11025 Hz;
    # the imagery step must decode that copy, not only the 48 kHz
    # capture rate.
    samples = synthetic_apt_samples(12, 48000)
    stored = _resample_int16(array.array("h", samples), 48000, 11025)
    feed = {"lock": True, "quality": 0.8, "sat": "NOAA 19",
            "norad": "33591",
            "audio_b64": _wav_b64(stored, 11025),
            "audio_name": "pass-33591-%d.wav" % int(T0),
            "audio_rate": 11025}
    events = desks.process_satellite(
        feed, _ctx(profile, "satellite", **{"pass": _pass()}))
    ev = events[0]
    assert ev["data"]["imagery"] is not None
    png = base64.b64decode(ev["data"]["_snippet"][1]["b64"])
    width, height = _png_dims(png)
    assert width == 2080 and height >= 22


def test_flash_names_the_image(profile, index):
    index.set_watch_meta(W, {
        "watch_id": W, "start": T0, "planned_end": T0 + 86400,
        "end": None, "source": "real", "profile_name": "t",
        "region": "the test region", "antenna": "test antenna",
        "disk_ceiling_gb": 20, "ended_early": False,
        "end_reason": None, "resumed": False})
    ev = desks.process_satellite(
        _apt_feed(), _ctx(profile, "satellite", **{"pass": _pass()}))[0]
    ev["data"].pop("_snippet", None)
    name = products.maybe_flash(index, W, ev, profile)
    assert name and name.startswith("FLASH-")
    text = open(os.path.join(index.watch_dir(W), name)).read()
    assert ev["data"]["imagery"] in text


# ------------------------------------------------------- honesty

def test_undecodable_audio_is_stated_not_silent(profile):
    # A locked pass whose audio holds no APT at all (here:
    # silence) files its audio and states the own decoder's
    # reason - never a silent missing image, never an invented
    # one. (Through 0.4.x this test asserted the aptdec-absent
    # statement; the binding is retired, so the honesty now rides
    # on the decoder's own verdict.)
    feed = {"lock": True, "quality": 0.8, "sat": "NOAA 19",
            "norad": "33591",
            "audio_b64": _wav_b64([0] * 96000, 48000),
            "audio_name": "pass-33591-%d.wav" % int(T0),
            "audio_rate": 48000}
    events = desks.process_satellite(
        feed, _ctx(profile, "satellite", **{"pass": _pass()}))
    ev = events[0]
    assert ev["grade"] == "CONFIRMED"
    assert ev["data"]["imagery"] is None
    assert "no APT sync found" in ev["data"]["imagery_note"]
    assert "not decoded" in ev["content"]
    snippet = ev["data"]["_snippet"]
    assert isinstance(snippet, dict)
    assert snippet["filename"].endswith(".wav")


def test_meteor_pass_is_acknowledged_not_recorded(profile):
    # The 0.6.0 contract: a METEOR pass is acknowledged only.
    # Even a feed in the retired shape - a lock with IQ
    # attached, as the 0.4.1 sources produced - files the
    # acknowledgement and nothing else: no snippet, no decode
    # state, no imagery claim.
    feed = {"lock": True, "quality": 0.8, "sat": "METEOR-M2 4",
            "norad": "59051",
            "iq_b64": base64.b64encode(b"\x00" * 2048).decode(),
            "iq_name": "pass-59051-%d.cu8" % int(T0),
            "iq_rate": 1024000, "iq_format": "cu8",
            "iq_bytes": 2048, "iq_synthetic": True}
    events = desks.process_satellite(
        feed, _ctx(profile, "satellite", **{"pass": _pass()}))
    ev = events[0]
    assert ev["kind"] == "pass" and ev["grade"] == "CONFIRMED"
    assert ev["emitter"] == "METEOR-M2 4"
    assert "pass acknowledged, not recorded" in ev["content"]
    assert ev["data"]["acknowledged"] is True
    assert ev["data"]["recorded"] is False
    assert ev["data"]["allocation_bytes"] == 0
    assert "_snippet" not in ev["data"]
    assert "downlink" not in ev["data"]
    assert "imagery" not in ev["data"]


# --------------------------------------------- the recorder (real)

def _locking_scan(center_hz, *_a, **_k):
    return {"noise_floor_db": -100.0,
            "emissions": [{"freq_hz": center_hz, "peak_dbm": -55.0,
                           "bw_hz": 40e3, "shape": "unknown",
                           "label": None}]}


def test_feed_satellite_records_full_pass_at_max_gain():
    source = RealSource({})
    source.scan = _locking_scan
    calls = []

    def recorder(freq_hz, mode, rate, seconds, squelch=0, gain="23"):
        calls.append((freq_hz, mode, rate, seconds, gain))
        return struct.pack("<48000h", *([0] * 48000))

    source._rtl_fm = recorder
    now = time.time()
    vfo = {"desk": "satellite", "freq_hz": 137.1e6, "bw_hz": 150e3,
           "pass": {"sat": "NOAA 19", "norad": "33591",
                    "aos": now - 60, "los": now + 540}}
    feed = source._feed_satellite([vfo], 137.1e6, 2.4e6,
                                  now - 60, now + 660)
    assert feed["lock"] is True
    assert len(calls) == 1
    freq, mode, rate, seconds, gain = calls[0]
    assert (freq, mode, rate, gain) == (137.1e6, "fm", 48000, "49.6")
    # To the pass end (los at +540), not the dwell end (+660) and
    # not the old 8-second sample.
    assert 530 <= seconds <= 545
    assert feed["audio_rate"] == 11025
    wav = base64.b64decode(feed["audio_b64"])
    assert wav[:4] == b"RIFF"
    assert struct.unpack("<I", wav[24:28])[0] == 11025


def test_feed_satellite_recording_stops_at_the_dwell_end():
    source = RealSource({})
    source.scan = _locking_scan
    calls = []

    def recorder(freq_hz, mode, rate, seconds, squelch=0, gain="23"):
        calls.append(seconds)
        return struct.pack("<4800h", *([0] * 4800))

    source._rtl_fm = recorder
    now = time.time()
    vfo = {"desk": "satellite", "freq_hz": 137.62e6, "bw_hz": 150e3,
           "pass": {"sat": "NOAA 15", "norad": "25338",
                    "aos": now - 30, "los": now + 900}}
    feed = source._feed_satellite([vfo], 137.62e6, 2.4e6,
                                  now - 30, now + 300)
    assert feed["lock"] is True
    assert len(calls) == 1
    assert 290 <= calls[0] <= 305  # the dwell end comes first


def test_rtl_fm_default_gain_is_the_house_gain(monkeypatch):
    commands = []

    def fake_stream(cmd, path, seconds):
        commands.append(cmd)
        with open(path, "wb") as fh:
            fh.write(b"\x00\x00" * 10)
        return 20

    monkeypatch.setattr(tuner, "_stream_to_file", fake_stream)
    source = RealSource({})
    source._rtl_fm(137.1e6, "fm", 48000, 1.0)
    source._rtl_fm(137.1e6, "fm", 48000, 1.0, gain="49.6")
    gains = [cmd[cmd.index("-g") + 1] for cmd in commands]
    assert gains == ["23", "49.6"]


# ------------------------------------------------- the sim source

def test_sim_satellite_feed_carries_a_decodable_recording(profile):
    src = SimSource(profile, T0)
    vfo = {"desk": "satellite", "freq_hz": 137.1e6,
           "pass": {"sat": "NOAA 19", "norad": "33591", "aos": T0,
                    "los": T0 + 600, "max_elev_deg": 55.0}}
    feed = src._satellite_feed(vfo)
    assert feed["lock"] is True
    assert feed["audio_rate"] == 48000
    wav = base64.b64decode(feed["audio_b64"])
    assert wav[:4] == b"RIFF"
    assert struct.unpack("<I", wav[24:28])[0] == 48000
    # 12 seconds of mono int16 at 48000 Hz behind the 44-byte header.
    assert len(wav) == 44 + 12 * 48000 * 2
    miss = src._satellite_feed(
        {"desk": "satellite", "freq_hz": 137.1e6,
         "pass": {"sat": "NOAA 19", "norad": "33591", "aos": T0,
                  "los": T0 + 600, "max_elev_deg": 4.0}})
    assert miss["lock"] is False and "audio_b64" not in miss
