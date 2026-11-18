"""0.2.0 ACARS PHY: synthetic ARINC 618 round-trips, CRC and
parity rejection, and the normalised text contract."""

import numpy as np

from multidex import acars, codecs


def _audio_for(text, tail="N12345", label="H1", flight="UA12"):
    body = "001" + flight + " " + text
    frame = acars.build_frame_bytes("2", tail, " ", label, "A", body)
    bits = acars.bytes_to_bits(frame)
    return acars.bits_to_audio(bits, 12000)


def test_roundtrip_decodes_frame():
    audio = _audio_for("OOOI 1234 FUEL 9200 KG")
    out = acars.decode_audio(audio, 12000)
    assert len(out) == 1, out
    msg = codecs.acars_parse(out[0])
    assert msg is not None
    assert msg["tail"] == "N12345"
    assert msg["label"] == "H1"
    assert msg["flight"] == "UA12"
    assert "FUEL 9200 KG" in msg["text"]


def test_crc_failure_is_rejected():
    frame = bytearray(acars.build_frame_bytes(
        "2", "N12345", " ", "H1", "A", "001UAL123 TEST"))
    frame[-1] ^= 0x01  # corrupt the BCS low byte
    audio = acars.bits_to_audio(acars.bytes_to_bits(bytes(frame)),
                                12000)
    assert acars.decode_audio(audio, 12000) == []


def test_text_corruption_is_rejected_by_crc():
    frame = bytearray(acars.build_frame_bytes(
        "2", "N12345", " ", "H1", "A", "001UAL123 TEST"))
    frame[20] ^= 0x04  # flip a bit inside the text
    audio = acars.bits_to_audio(acars.bytes_to_bits(bytes(frame)),
                                12000)
    assert acars.decode_audio(audio, 12000) == []


def test_noise_decodes_nothing():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.3, 24000)
    assert acars.decode_audio(noise, 12000) == []


def test_decode_with_noise_floor():
    audio = _audio_for("POS 39.02 -98.12")
    rng = np.random.default_rng(9)
    noisy = audio + rng.normal(0, 0.08, len(audio))
    out = acars.decode_audio(noisy, 12000)
    assert len(out) == 1, out
    assert "POS" in codecs.acars_parse(out[0])["text"]
