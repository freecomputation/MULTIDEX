"""Codecs for the text and frame protocols the desks decode.

AIS (AIVDM) and POCSAG are decoded at the frame layer: 6-bit packed
AIS payloads, and POCSAG batches of 32-bit codewords with their real
BCH(31,21) check bits and even parity. SAME and APRS are parsed in
their standard text forms. ACARS is parsed in the demodulated monitor
format the simulated source emits.
"""

import re

# ------------------------------------------------------------------- AIS

_AIS_TEXT = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"


def _ais_bits(payload):
    bits = []
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        if v < 0 or v > 63:
            raise ValueError("bad AIS payload character %r" % ch)
        bits.append(format(v, "06b"))
    return "".join(bits)


def _ais_payload(bitstr):
    out = []
    pad = (-len(bitstr)) % 6
    bitstr = bitstr + "0" * pad
    for i in range(0, len(bitstr), 6):
        v = int(bitstr[i:i + 6], 2)
        out.append(chr(v + 48) if v < 40 else chr(v + 56))
    return "".join(out), pad


def _field(bitstr, start, length, signed=False):
    raw = int(bitstr[start:start + length] or "0", 2)
    if signed and raw >= (1 << (length - 1)):
        raw -= 1 << length
    return raw


def _ais_text_field(bitstr, start, chars):
    out = []
    for i in range(chars):
        v = int(bitstr[start + 6 * i:start + 6 * i + 6] or "0", 2)
        out.append(_AIS_TEXT[v])
    return "".join(out).rstrip("@ ").strip()


def ais_checksum_ok(sentence):
    if "*" not in sentence or not sentence.startswith("!"):
        return False
    body, _, given = sentence[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given[:2], 16)
    except ValueError:
        return False


def ais_decode(sentence):
    """Decode an AIVDM sentence (message types 1 and 5 carried)."""
    if not ais_checksum_ok(sentence):
        raise ValueError("AIS checksum failed")
    parts = sentence.split(",")
    payload = parts[5]
    fill = int(parts[6].split("*")[0])
    bitstr = _ais_bits(payload)
    if fill:
        bitstr = bitstr[:-fill]
    msg_type = _field(bitstr, 0, 6)
    mmsi = _field(bitstr, 8, 30)
    out = {"type": msg_type, "mmsi": str(mmsi)}
    if msg_type in (1, 2, 3):
        out["lon"] = _field(bitstr, 61, 28, signed=True) / 600000.0
        out["lat"] = _field(bitstr, 89, 27, signed=True) / 600000.0
        out["sog_kt"] = _field(bitstr, 50, 10) / 10.0
        out["cog_deg"] = _field(bitstr, 116, 12) / 10.0
        out["heading_deg"] = _field(bitstr, 128, 9)
        out["nav_status"] = _field(bitstr, 38, 4)
    elif msg_type == 5:
        out["callsign"] = _ais_text_field(bitstr, 70, 7)
        out["name"] = _ais_text_field(bitstr, 112, 20)
        out["ship_type"] = _field(bitstr, 232, 8)
        out["destination"] = _ais_text_field(bitstr, 302, 20)
    return out


def _put(bitlist, start, value, length, signed=False):
    if signed and value < 0:
        value += 1 << length
    text = format(value & ((1 << length) - 1), "0%db" % length)
    bitlist[start:start + length] = list(text)


def _put_text(bitlist, start, text, chars):
    text = (text + "@" * chars)[:chars]
    for i, ch in enumerate(text):
        v = _AIS_TEXT.index(ch)
        _put(bitlist, start + 6 * i, v, 6)


def ais_encode_position(mmsi, lat, lon, sog_kt=0.0, cog_deg=0.0,
                        heading_deg=511, nav_status=0):
    bits = ["0"] * 168
    _put(bits, 0, 1, 6)
    _put(bits, 8, int(mmsi), 30)
    _put(bits, 38, nav_status, 4)
    _put(bits, 50, int(round(sog_kt * 10)), 10)
    _put(bits, 60, 1, 1)
    _put(bits, 61, int(round(lon * 600000)), 28, signed=True)
    _put(bits, 89, int(round(lat * 600000)), 27, signed=True)
    _put(bits, 116, int(round(cog_deg * 10)), 12)
    _put(bits, 128, heading_deg, 9)
    payload, fill = _ais_payload("".join(bits))
    return _ais_sentence(payload, fill)


def ais_encode_static(mmsi, name, callsign, ship_type=70, destination=""):
    bits = ["0"] * 424
    _put(bits, 0, 5, 6)
    _put(bits, 8, int(mmsi), 30)
    _put_text(bits, 70, callsign, 7)
    _put_text(bits, 112, name, 20)
    _put(bits, 232, ship_type, 8)
    _put_text(bits, 302, destination, 20)
    payload, fill = _ais_payload("".join(bits))
    return _ais_sentence(payload, fill)


def _ais_sentence(payload, fill):
    body = "AIVDM,1,1,,A,%s,%d" % (payload, fill)
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    return "!%s*%02X" % (body, calc)


# ---------------------------------------------------------------- POCSAG

POCSAG_SYNC = 0x7CD215D8
POCSAG_IDLE = 0x7A89C197
_BCH_GEN = 0x769  # x^10+x^9+x^8+x^6+x^5+x^3+1


def _bch_check(info21):
    """10 BCH check bits for the 21 info bits of a POCSAG codeword.

    The 21 info bits are the flag bit plus 20 data bits; the BCH is
    computed over all 21, as the format specifies.
    """
    value = info21 << 10
    for shift in range(20, -1, -1):
        if (value >> (shift + 10)) & 1:
            value ^= _BCH_GEN << shift
    return value & 0x3FF


def pocsag_codeword(data20, is_message):
    info21 = ((1 if is_message else 0) << 20) | (data20 & 0xFFFFF)
    word = info21 << 11
    word |= _bch_check(info21) << 1
    if bin(word).count("1") % 2:
        word |= 1  # even parity over the whole codeword
    return word


def pocsag_valid(word):
    if bin(word).count("1") % 2:
        return False
    info21 = (word >> 11) & 0x1FFFFF
    return ((word >> 1) & 0x3FF) == _bch_check(info21)


def pocsag_encode_batch(address, function, message):
    """One batch: sync, address codeword, message codewords, idle."""
    if not 0 <= address < (1 << 21):
        raise ValueError("POCSAG addresses are 21 bits")
    words = [POCSAG_SYNC]
    data21 = (((address >> 3) & 0x3FFFF) << 2) | (function & 0x3)
    words.append(pocsag_codeword(data21, is_message=False))
    frame_no = address & 0x7
    bits = ""
    for ch in message:
        bits += format(ord(ch) & 0x7F, "07b")
    while len(bits) % 20:
        bits += "0"
    for i in range(0, len(bits), 20):
        chunk = int(bits[i:i + 20], 2)
        words.append(pocsag_codeword(chunk, is_message=True))
    words.append(POCSAG_IDLE)
    return "".join("%08X" % w for w in words), frame_no


def pocsag_decode_batch(batch_hex, frame_no=0):
    """Decode one batch. Returns (address, function, message) or None."""
    words = [int(batch_hex[i:i + 8], 16)
             for i in range(0, len(batch_hex) - 7, 8)]
    if POCSAG_SYNC not in words:
        return None
    idx = words.index(POCSAG_SYNC) + 1
    address = None
    function = 0
    bits = ""
    while idx < len(words):
        word = words[idx]
        idx += 1
        if word == POCSAG_IDLE or word == POCSAG_SYNC:
            break
        if not pocsag_valid(word):
            continue
        info21 = (word >> 11) & 0x1FFFFF
        data20 = info21 & 0xFFFFF
        if word & (1 << 31):
            if address is None:
                continue
            bits += format(data20, "020b")
        else:
            address = (((data20 >> 2) & 0x3FFFF) << 3) | (frame_no & 0x7)
            function = data20 & 0x3
    if address is None:
        return None
    chars = []
    for i in range(0, len(bits) - 6, 7):
        code = int(bits[i:i + 7], 2)
        if code == 0:
            break
        chars.append(chr(code))
    return address, function, "".join(chars).strip()


# ------------------------------------------------------------------ SAME

_SAME_EVENTS = {
    "TOR": "Tornado Warning", "SVR": "Severe Thunderstorm Warning",
    "FFW": "Flash Flood Warning", "WSW": "Winter Storm Warning",
    "SPS": "Special Weather Statement", "RWT": "Required Weekly Test",
    "SMW": "Special Marine Warning", "HWW": "High Wind Warning",
}

_SAME_RE = re.compile(
    r"ZCZC-([A-Z]{3})-([A-Z]{3})-((?:\d{6}-)*\d{6})\+(\d{4})-(\d{7})-([A-Z0-9/ ]{8})-")


def same_event_name(code):
    return _SAME_EVENTS.get(code, "Weather event %s" % code)


def same_parse(header):
    m = _SAME_RE.search(header or "")
    if not m:
        return None
    org, code, areas, duration, issued, station = m.groups()
    return {"originator": org, "event_code": code,
            "event": same_event_name(code),
            "areas": areas.split("-"), "duration": duration,
            "issued": issued, "station": station.strip()}


def same_build(code, areas, station, duration="0100", issued="3171200",
               originator="WXR"):
    area_text = "-".join(areas)
    return "ZCZC-%s-%s-%s+%s-%s-%s-" % (
        originator, code, area_text, duration, issued,
        (station + "        ")[:8])


# ------------------------------------------------------------------ APRS

_APRS_POS_RE = re.compile(
    r"([!@])(\d{2})(\d{2}\.\d{2})([NS])(.)(\d{3})(\d{2}\.\d{2})([EW])(.)(.*)")


def aprs_parse(packet):
    """Parse one APRS packet: SOURCE>DEST,PATH:payload."""
    if ">" not in packet or ":" not in packet:
        return None
    head, _, payload = packet.partition(":")
    source = head.split(">")[0].strip()
    out = {"source": source, "payload": payload}
    m = _APRS_POS_RE.match(payload)
    if m:
        _, latd, latm, ns, _t, lond, lonm, ew, _s, comment = m.groups()
        lat = int(latd) + float(latm) / 60.0
        lon = int(lond) + float(lonm) / 60.0
        out["lat"] = lat if ns == "N" else -lat
        out["lon"] = lon if ew == "E" else -lon
        out["comment"] = comment.strip()
        out["kind"] = "position"
    elif payload.startswith(":"):
        out["kind"] = "message"
    else:
        out["kind"] = "raw"
    return out


def aprs_build_position(callsign, lat, lon, comment=""):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    alat, alon = abs(lat), abs(lon)
    lat_text = "%02d%05.2f" % (int(alat), (alat - int(alat)) * 60)
    lon_text = "%03d%05.2f" % (int(alon), (alon - int(alon)) * 60)
    return "%s>APRS,TCPIP*:!%s%s/%s%s> %s" % (
        callsign, lat_text, ns, lon_text, ew, comment)


# ----------------------------------------------------------------- ACARS

_ACARS_RE = re.compile(
    r"ACARS MODE (\S) LABEL (\S{2}) TAIL (\S+) FLIGHT (\S+) MSG (.*)")


def acars_parse(raw):
    m = _ACARS_RE.match(raw or "")
    if not m:
        return None
    mode, label, tail, flight, text = m.groups()
    return {"mode": mode, "label": label, "tail": tail,
            "flight": flight, "text": text}


def acars_build(tail, flight, label, text, mode="2"):
    return "ACARS MODE %s LABEL %s TAIL %s FLIGHT %s MSG %s" % (
        mode, label, tail, flight, text)
