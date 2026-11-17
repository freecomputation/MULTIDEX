"""AIS, POCSAG, SAME, APRS, ACARS codecs."""

import pytest

from multidex import codecs


def test_ais_position_round_trip():
    s = codecs.ais_encode_position("370123456", 38.9, -94.6,
                                   sog_kt=12.5, cog_deg=87.0,
                                   heading_deg=88)
    d = codecs.ais_decode(s)
    assert d["type"] == 1 and d["mmsi"] == "370123456"
    assert abs(d["lat"] - 38.9) < 1e-3
    assert abs(d["lon"] + 94.6) < 1e-3
    assert d["sog_kt"] == 12.5


def test_ais_static_round_trip():
    s = codecs.ais_encode_static("370123456", "MV EXAMPLE", "WDA1234",
                                 destination="RIVERPORT")
    d = codecs.ais_decode(s)
    assert d["type"] == 5
    assert d["name"] == "MV EXAMPLE"
    assert d["callsign"] == "WDA1234"
    assert d["destination"] == "RIVERPORT"


def test_ais_checksum_enforced():
    s = codecs.ais_encode_position("370123456", 38.9, -94.6)
    assert codecs.ais_checksum_ok(s)
    # Flip exactly one payload character: the XOR checksum must catch
    # it (two identical swaps would cancel, which is a property of
    # the NMEA checksum, not a decoder weakness).
    star = s.index("*")
    tampered = s[:star - 1] + ("0" if s[star - 1] != "0" else "1") \
        + s[star:]
    assert not codecs.ais_checksum_ok(tampered)
    with pytest.raises(ValueError):
        codecs.ais_decode(tampered)


def test_pocsag_round_trip():
    batch, frame = codecs.pocsag_encode_batch(1234567, 0,
                                              "TEST MESSAGE 123")
    assert codecs.pocsag_decode_batch(batch, frame) == (
        1234567, 0, "TEST MESSAGE 123")
    batch, frame = codecs.pocsag_encode_batch(1654321, 2,
                                              "MEET AT THE SITE AT 1800")
    assert codecs.pocsag_decode_batch(batch, frame) == (
        1654321, 2, "MEET AT THE SITE AT 1800")


def test_pocsag_bch_and_parity():
    word = codecs.pocsag_codeword(0x12345, False)
    assert codecs.pocsag_valid(word)
    assert not codecs.pocsag_valid(word ^ 0x40)
    assert not codecs.pocsag_valid(word ^ (1 << 31))


def test_pocsag_address_range():
    with pytest.raises(ValueError):
        codecs.pocsag_encode_batch(1 << 21, 0, "TOO BIG")
    assert codecs.pocsag_decode_batch("DEADBEEF") is None


def test_same_parse():
    header = codecs.same_build("TOR", ["020091"], "KXXX",
                               issued="3171830")
    parsed = codecs.same_parse(header)
    assert parsed["event_code"] == "TOR"
    assert parsed["event"] == "Tornado Warning"
    assert parsed["areas"] == ["020091"]
    assert codecs.same_parse("not a header") is None


def test_aprs_round_trip():
    packet = codecs.aprs_build_position("N0CALL", 39.1, -94.6,
                                        "test station")
    parsed = codecs.aprs_parse(packet)
    assert parsed["source"] == "N0CALL"
    assert parsed["kind"] == "position"
    assert abs(parsed["lat"] - 39.1) < 0.01
    assert abs(parsed["lon"] + 94.6) < 0.01
    assert codecs.aprs_parse("garbage") is None


def test_acars_parse():
    raw = codecs.acars_build("N842US", "DL441", "Q0", "HELLO")
    parsed = codecs.acars_parse(raw)
    assert parsed["tail"] == "N842US"
    assert parsed["flight"] == "DL441"
    assert parsed["text"] == "HELLO"
    assert codecs.acars_parse("noise") is None
