"""Mode S decoding against known frames and encoder round trips."""

from multidex import modes

# Frames from the Mode S literature (Junzi Sun, The 1090 Megahertz
# Riddle): a callsign frame and an airborne position frame.
KLM_FRAME = "8D4840D6202CC371C32CE0576098"
POS_FRAME = "8D40621D58C382D690C8AC2863A7"


def test_known_callsign_frame():
    assert modes.crc_ok(KLM_FRAME)
    assert modes.df(KLM_FRAME) == 17
    assert modes.icao(KLM_FRAME) == "4840D6"
    assert modes.typecode(KLM_FRAME) == 4
    assert modes.decode_callsign(KLM_FRAME) == "KLM1023"


def test_known_position_frame():
    assert modes.crc_ok(POS_FRAME)
    assert modes.typecode(POS_FRAME) == 11
    assert modes.decode_altitude(POS_FRAME) == 38000


def test_crc_rejects_tampered_frame():
    bad = KLM_FRAME[:-1] + ("0" if KLM_FRAME[-1] != "0" else "1")
    assert not modes.crc_ok(bad)
    assert not modes.crc_ok("ZZZZ")
    assert not modes.crc_ok("")


def test_callsign_round_trip():
    frame = modes.encode_callsign("ABC123", "TEST1234")
    assert modes.crc_ok(frame)
    assert modes.decode_callsign(frame) == "TEST1234"
    assert modes.icao(frame) == "ABC123"


def test_position_round_trip_global_cpr():
    lat, lon, alt = 39.0997, -94.5786, 33000
    even = modes.encode_position("ABC123", lat, lon, alt, is_odd=False)
    odd = modes.encode_position("ABC123", lat, lon, alt, is_odd=True)
    got = modes.decode_position(odd, even)
    assert got is not None
    assert abs(got[0] - lat) < 0.01
    assert abs(got[1] - lon) < 0.01
    assert modes.decode_altitude(even) == alt


def test_position_local_reference():
    lat, lon = 39.0997, -94.5786
    frame = modes.encode_position("ABC123", lat, lon, 10000, is_odd=False)
    got = modes.decode_position(frame, ref=(39.0, -95.0))
    assert abs(got[0] - lat) < 0.01
    assert abs(got[1] - lon) < 0.01
    assert modes.decode_position(frame) is None  # honestly unknown


def test_velocity_round_trip():
    frame = modes.encode_velocity("ABC123", 450, 90)
    vel = modes.decode_velocity(frame)
    assert abs(vel["speed_kt"] - 450) < 2
    assert abs(vel["heading_deg"] - 90) < 2
    frame2 = modes.encode_velocity("ABC123", 300, 225)
    vel2 = modes.decode_velocity(frame2)
    assert abs(vel2["speed_kt"] - 300) < 3
    assert abs(vel2["heading_deg"] - 225) < 3
