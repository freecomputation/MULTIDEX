"""0.2.0 amateur and ISS desks: CW and SSTV round-trips through
the production decoders, and honest desk products."""

import numpy as np

from multidex import cw as cw_mod
from multidex import desks, sstv as sstv_mod


def test_cw_roundtrip():
    iq = cw_mod.encode_iq("CQ CQ DE W1ABC K", wpm=18, fs=48000.0,
                          offset_hz=2500.0)
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 0.02, len(iq)) \
        + 1j * rng.normal(0, 0.02, len(iq))
    found = cw_mod.detect(iq + noise, 48000.0)
    assert found, "no CW decoded"
    assert "W1ABC" in found[0]["text"], found[0]
    assert found[0]["confidence"] > 0.7


def test_cw_noise_decodes_nothing():
    rng = np.random.default_rng(8)
    iq = rng.normal(0, 0.05, 48000 * 2) \
        + 1j * rng.normal(0, 0.05, 48000 * 2)
    assert cw_mod.detect(iq, 48000.0) == []


def _test_image(h=240, w=320):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 0] = np.linspace(0, 255, w).astype(np.uint8)
    img[:, :, 1] = np.linspace(0, 255, h).astype(np.uint8)[:, None]
    img[h // 3:2 * h // 3, w // 3:2 * w // 3, 2] = 255
    return img


def test_sstv_robot36_roundtrip():
    audio = sstv_mod.encode_audio(_test_image(), "Robot 36", 12000)
    dec = sstv_mod.decode_audio(audio, 12000)
    assert dec is not None
    assert dec["mode"] == "Robot 36"
    assert dec["lines"] >= 200, dec["lines"]
    assert dec["png"][:8] == b"\x89PNG\r\n\x1a\n"


def test_sstv_pd120_roundtrip():
    audio = sstv_mod.encode_audio(_test_image(496, 640), "PD120",
                                  12000)
    dec = sstv_mod.decode_audio(audio, 12000)
    assert dec is not None
    assert dec["mode"] == "PD120"
    assert dec["lines"] >= 400, dec["lines"]


def test_sstv_martin_m1_roundtrip():
    audio = sstv_mod.encode_audio(_test_image(256, 320), "Martin M1",
                                  12000)
    dec = sstv_mod.decode_audio(audio, 12000)
    assert dec is not None
    assert dec["mode"] == "Martin M1"
    assert dec["lines"] >= 200, dec["lines"]


def test_sstv_silence_decodes_nothing():
    assert sstv_mod.decode_audio(np.zeros(12000 * 10), 12000) is None


def _ctx(profile, desk):
    return {"watch_id": "w", "desk": desk, "t0": 0.0, "t1": 60.0,
            "freq_hz": 14030000.0, "profile": profile,
            "home": (39.0, -98.0), "pass": {"sat": "ISS (ZARYA)"}}


def test_amateur_desk_products(profile):
    feed = {"cw": [{"text": "CQ CQ DE W1ABC K", "wpm": 17.5,
                    "confidence": 0.92, "snr_db": 14.0,
                    "freq_hz": 7032500.0}],
            "ft8": [{"text": "CQ K4XYZ FN42", "snr_db": -9,
                     "dt_s": 0.3, "freq_hz": 7075123.0}]}
    events = desks.process_amateur(feed, _ctx(profile, "amateur"))
    assert len(events) == 2
    assert events[0]["emitter"] == "W1ABC"
    assert "machine decode" in events[0]["content"]
    assert "jt9" in events[1]["content"]


def test_digitalvoice_desk_is_metadata_only(profile):
    feed = {"calls": [{"protocol": "DMR", "system": "SYS:X",
                       "talkgroup": "100", "source": "123456",
                       "slot": 1, "encrypted": True,
                       "freq_hz": 851.75e6}]}
    events = desks.process_digitalvoice(
        feed, _ctx(profile, "digitalvoice"))
    assert len(events) == 1
    ev = events[0]
    assert ev["data"]["voice_decoded"] is False
    assert "voice is not decoded" in ev["content"]
    assert "encrypted" in ev["content"]


def test_iss_desk_silent_pass_is_honest(profile):
    feed = {"attempted": True, "segments": [], "sstv": None}
    events = desks.process_iss(feed, _ctx(profile, "iss"))
    assert len(events) == 1
    assert events[0]["kind"] == "observation"
    assert "no voice and no SSTV" in events[0]["content"]


def test_vdl2_desk_burst_is_not_a_decode(profile):
    feed = {"messages": [], "bursts": [{"count": 5}]}
    events = desks.process_vdl2(feed, _ctx(profile, "vdl2"))
    assert len(events) == 1
    assert events[0]["kind"] == "observation"
    assert "no frame decoded" in events[0]["content"]
