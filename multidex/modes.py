"""Mode S / ADS-B frame codec (1090 MHz extended squitter, DF17).

Real decoding: 24-bit CRC with the Mode S generator polynomial,
identification (callsign), airborne altitude, airborne velocity, and
CPR position (global decode from an even/odd pair, local decode from a
reference position). The encoder exists so the simulated source can put
genuine frames on the wire for the desk to decode.
"""

import math

# Generator polynomial x^24 + ... = 0xFFF409 with the implicit top bit.
_GENERATOR = (1 << 24) | 0xFFF409

_CHARSET = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def _reduce(value):
    """Remainder of a GF(2) polynomial division by the Mode S generator."""
    shift = value.bit_length() - 25
    while shift >= 0:
        if (value >> (shift + 24)) & 1:
            value ^= _GENERATOR << shift
        shift -= 1
    return value & 0xFFFFFF


def crc_ok(frame_hex):
    """A full 112-bit DF17 frame checks when its remainder is zero."""
    try:
        value = int(frame_hex, 16)
    except ValueError:
        return False
    if value.bit_length() > 112:
        return False
    return _reduce(value) == 0


def crc_compute(payload_hex):
    """Parity for an 88-bit payload (first 11 bytes of a DF17 frame)."""
    value = int(payload_hex, 16)
    return _reduce(value << 24)


def _bits(hexstr, start, length):
    value = int(hexstr, 16)
    total = len(hexstr) * 4
    return (value >> (total - start - length)) & ((1 << length) - 1)


def df(frame_hex):
    return _bits(frame_hex, 0, 5)


def icao(frame_hex):
    return "%06X" % _bits(frame_hex, 8, 24)


def typecode(frame_hex):
    return _bits(frame_hex, 32, 5)


def _me(frame_hex):
    return frame_hex[8:22]


def decode_callsign(frame_hex):
    """TC 1-4: eight 6-bit characters."""
    me = _me(frame_hex)
    raw = _bits(me, 8, 48)
    chars = []
    for i in range(8):
        idx = (raw >> (42 - 6 * i)) & 0x3F
        chars.append(_CHARSET[idx])
    return "".join(chars).strip()


def decode_altitude(frame_hex):
    """TC 9-18: 12-bit altitude code, Q-bit form (25 ft steps)."""
    me = _me(frame_hex)
    code = _bits(me, 8, 12)
    if code & 0x10:  # Q bit set: 11-bit N in 25 ft steps
        n = ((code & 0xFE0) >> 1) + (code & 0x00F)
        return n * 25 - 1000
    return None  # Gillham (metric) form not carried by this build


def frame_parity(frame_hex):
    """The CPR odd/even flag (F bit) of a TC 9-18 frame."""
    return _bits(_me(frame_hex), 21, 1)


def decode_velocity(frame_hex):
    """TC 19 subtype 1: ground speed and heading from EW/NS components."""
    me = _me(frame_hex)
    subtype = _bits(me, 5, 3)
    if subtype not in (1, 2):
        return None
    s_ew = _bits(me, 13, 1)
    v_ew = _bits(me, 14, 10)
    s_ns = _bits(me, 24, 1)
    v_ns = _bits(me, 25, 10)
    if v_ew == 0 or v_ns == 0:
        return None
    mult = 4 if subtype == 2 else 1
    ew = (v_ew - 1) * mult * (-1 if s_ew else 1)
    ns = (v_ns - 1) * mult * (-1 if s_ns else 1)
    speed = math.hypot(ew, ns)
    heading = math.degrees(math.atan2(ew, ns)) % 360
    return {"speed_kt": speed, "heading_deg": heading,
            "ew_kt": ew, "ns_kt": ns}


def _nl(lat):
    """Number of longitude zones at a latitude (CPR)."""
    if lat == 0:
        return 59
    a = 1 - math.cos(math.pi / 30)
    b = math.cos(math.radians(lat)) ** 2
    if b <= a:
        return 1
    return int(math.floor(2 * math.pi / math.acos(1 - a / b)))


def _cpr_pair_decode(even, odd, newer_is_even):
    """Global CPR decode. Frames are (cpr_lat, cpr_lon, t) raw 17-bit pairs."""
    lat_e, lon_e, _ = even
    lat_o, lon_o, _ = odd
    lat_e_n = lat_e / 131072.0
    lat_o_n = lat_o / 131072.0
    lon_e_n = lon_e / 131072.0
    lon_o_n = lon_o / 131072.0
    dlat_e = 360.0 / 60
    dlat_o = 360.0 / 59
    j = math.floor(59 * lat_e_n - 60 * lat_o_n + 0.5)
    rlat_e = dlat_e * ((j % 60) + lat_e_n)
    rlat_o = dlat_o * ((j % 59) + lat_o_n)
    if rlat_e >= 270:
        rlat_e -= 360
    if rlat_o >= 270:
        rlat_o -= 360
    if _nl(rlat_e) != _nl(rlat_o):
        return None
    if newer_is_even:
        lat = rlat_e
        ni = max(_nl(lat), 1)
        dlon = 360.0 / ni
        m = math.floor(lon_e_n * (_nl(lat) - 1) - lon_o_n * _nl(lat) + 0.5)
        lon = dlon * ((m % ni) + lon_e_n)
    else:
        lat = rlat_o
        ni = max(_nl(lat) - 1, 1)
        dlon = 360.0 / ni
        m = math.floor(lon_e_n * (_nl(lat) - 1) - lon_o_n * _nl(lat) + 0.5)
        lon = dlon * ((m % ni) + lon_o_n)
    if lon >= 180:
        lon -= 360
    return (lat, lon)


def decode_position(frame_hex, other_frame_hex=None, ref=None):
    """Decode a TC 9-18 frame.

    With an opposite-parity partner frame, global CPR decode. With a
    reference position (lat, lon), locally unambiguous decode. Without
    either, returns None: the position is honestly not yet known.
    """
    me = _me(frame_hex)
    is_odd = _bits(me, 21, 1)
    cpr_lat = _bits(me, 22, 17)
    cpr_lon = _bits(me, 39, 17)
    if other_frame_hex is not None:
        me2 = _me(other_frame_hex)
        other = (_bits(me2, 22, 17), _bits(me2, 39, 17), 0)
        mine = (cpr_lat, cpr_lon, 0)
        if is_odd:
            return _cpr_pair_decode(other, mine, newer_is_even=False)
        return _cpr_pair_decode(mine, other, newer_is_even=True)
    if ref is not None:
        lat_ref, lon_ref = ref
        dlat = 360.0 / (59 if is_odd else 60)
        lat = dlat * (math.floor(lat_ref / dlat) + cpr_lat / 131072.0)
        if lat >= 270:
            lat -= 360
        ni = _nl(lat) - (1 if is_odd else 0)
        ni = max(ni, 1)
        dlon = 360.0 / ni
        lon = dlon * (math.floor(lon_ref / dlon) + cpr_lon / 131072.0)
        if lon >= 180:
            lon -= 360
        return (lat, lon)
    return None


# ---------------------------------------------------------------- encoder
# The simulated source speaks in genuine frames; these builders are the
# mirror of the decoders above.

def encode_frame(icao_hex, me_int56):
    """Assemble a DF17 frame from an ICAO address and a 56-bit ME field."""
    prefix = (0x8D << 80) | (int(icao_hex, 16) << 56) | me_int56
    frame_no_parity = prefix << 24
    parity = _reduce(frame_no_parity)
    return "%028X" % (frame_no_parity | parity)


def encode_callsign(icao_hex, callsign):
    me = (4 << 51)
    raw = 0
    text = (callsign + "        ")[:8]
    for i, ch in enumerate(text):
        idx = _CHARSET.index(ch if ch in _CHARSET else "#")
        raw |= idx << (42 - 6 * i)
    me |= raw
    return encode_frame(icao_hex, me)


def _altitude_code(alt_ft):
    n = int(round((alt_ft + 1000) / 25.0))
    # Insert the Q bit at bit 4 of the 12-bit field.
    return ((n & 0x7F0) << 1) | 0x10 | (n & 0x00F)


def _cpr_encode(lat, lon, is_odd):
    dlat = 360.0 / (59 if is_odd else 60)
    yz = math.floor(131072 * ((lat / dlat) - math.floor(lat / dlat)))
    yz = int(yz) % 131072
    ni = _nl(lat) - (1 if is_odd else 0)
    ni = max(ni, 1)
    dlon = 360.0 / ni
    xz = math.floor(131072 * ((lon / dlon) - math.floor(lon / dlon)))
    xz = int(xz) % 131072
    return yz, xz


def encode_position(icao_hex, lat, lon, alt_ft, is_odd):
    yz, xz = _cpr_encode(lat, lon, is_odd)
    me = (11 << 51) | (_altitude_code(alt_ft) << 36) | ((1 if is_odd else 0) << 34) \
        | (yz << 17) | xz
    return encode_frame(icao_hex, me)


def encode_velocity(icao_hex, speed_kt, heading_deg):
    rad = math.radians(heading_deg)
    ew = int(round(speed_kt * math.sin(rad)))
    ns = int(round(speed_kt * math.cos(rad)))
    me = (19 << 51) | (1 << 48)
    me |= ((1 if ew < 0 else 0) << 42) | ((abs(ew) + 1) << 32)
    me |= ((1 if ns < 0 else 0) << 31) | ((abs(ns) + 1) << 21)
    return encode_frame(icao_hex, me)
