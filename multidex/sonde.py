"""The in-house radiosonde frame codec (0.15.0).

The independence law retires tools, and the sonde desk's decode
is this build's own work: no external decoder program is bound,
consulted or shelled to. What is decoded here is the sonde
frame format this module specifies, end to end - GFSK
modulation, preamble and sync acquisition, bit-clock recovery,
CRC-16 framing, and a telemetry payload of serial, position,
altitude, temperature, pressure and humidity. The simulated
source transmits exactly this format as a waveform, and the
desk demodulates it: the decode path the tests exercise is the
decode path the real source runs over captured channels.

The honesty line is stated plainly. This is a complete decoder
for the format specified below; it is not a claim about any
commercial sonde's proprietary subframe layout, and no event
the desk files says otherwise. On the real source, a carrier
whose frames do not pass this codec's framing and CRC files as
presence with the attempt and its outcome stated - the desk
never invents a track it did not decode.

The format, on air:

- GFSK at 4800 bit/s, deviation +-2400 Hz, the frequency
  trajectory smoothed over about a third of a bit period.
- A frame is: 4 preamble bytes of alternating bits (0xAA),
  a 2-byte sync word (0xD391), 1 length byte (the payload
  length), the payload, and a CRC-16/CCITT (polynomial
  0x1021, initial value 0xFFFF) over the length byte and the
  payload.
- The telemetry payload is 25 bytes: byte 0 the frame type
  (0x01 telemetry); bytes 1-6 the serial as ASCII; bytes 7-10
  latitude and 11-14 longitude as signed integers in units
  of 1e-7 degrees; bytes 15-18 altitude as a signed integer
  in units of 0.1 m; bytes 19-20 temperature as a signed
  integer in units of 0.01 C; bytes 21-22 pressure as an
  unsigned integer in units of 0.1 hPa; byte 23 humidity in
  percent; byte 24 battery in units of 0.1 V.
"""

import math
import struct

BAUD = 4800.0
DEVIATION_HZ = 2400.0
PREAMBLE = b"\xaa\xaa\xaa\xaa"
SYNC = b"\xd3\x91"
FRAME_TYPE_TELEMETRY = 0x01
_PAYLOAD = struct.Struct(">B6siiihHB B".replace(" ", ""))


def crc16_ccitt(data, init=0xFFFF):
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_payload(serial, lat, lon, alt_m, temp_c, pressure_hpa,
                   humidity_pct, battery_v):
    return _PAYLOAD.pack(
        FRAME_TYPE_TELEMETRY, serial.encode("ascii")[:6].ljust(6, b" "),
        int(round(lat * 1e7)), int(round(lon * 1e7)),
        int(round(alt_m * 10)), int(round(temp_c * 100)),
        int(round(pressure_hpa * 10)), int(humidity_pct),
        int(round(battery_v * 10)))


def decode_payload(payload):
    """One telemetry payload as a dict, or None when the bytes
    are not a well-formed telemetry frame of this format."""
    if len(payload) != _PAYLOAD.size:
        return None
    try:
        (ftype, serial, lat, lon, alt, temp, pres, hum, batt) = \
            _PAYLOAD.unpack(payload)
    except struct.error:
        return None
    if ftype != FRAME_TYPE_TELEMETRY:
        return None
    serial = serial.decode("ascii", "replace").strip()
    if not serial or not all(32 <= ord(c) < 127 for c in serial):
        return None
    lat_deg, lon_deg = lat / 1e7, lon / 1e7
    if not -90.0 <= lat_deg <= 90.0 or not -180.0 <= lon_deg <= 180.0:
        return None
    return {"serial": serial, "lat": lat_deg, "lon": lon_deg,
            "alt_m": alt / 10.0, "temp_c": temp / 100.0,
            "pressure_hpa": pres / 10.0, "humidity_pct": hum,
            "battery_v": batt / 10.0}


def encode_frame(payload):
    """Payload bytes -> the on-air byte string (preamble, sync,
    length, payload, CRC)."""
    body = bytes([len(payload)]) + payload
    crc = crc16_ccitt(body)
    return PREAMBLE + SYNC + body + struct.pack(">H", crc)


def _bytes_to_bits(data):
    bits = []
    for byte in data:
        for k in range(7, -1, -1):
            bits.append((byte >> k) & 1)
    return bits


def modulate(payload, rate, lead_silence_s=0.05, trail_silence_s=0.05):
    """One frame as complex baseband samples at `rate`: the
    encoder the simulated world transmits with. GFSK - the bit
    stream's frequency trajectory is smoothed with a short
    moving average before integration to phase."""
    import numpy as np
    frame = encode_frame(payload)
    bits = _bytes_to_bits(frame)
    spb = rate / BAUD
    n_lead = int(rate * lead_silence_s)
    n_trail = int(rate * trail_silence_s)
    n = int(len(bits) * spb)
    freq = np.zeros(n_lead + n + n_trail)
    idx = (np.arange(n) / spb).astype(int)
    idx = np.minimum(idx, len(bits) - 1)
    freq[n_lead:n_lead + n] = np.where(
        np.array(bits)[idx] == 1, DEVIATION_HZ, -DEVIATION_HZ)
    # Gaussian-ish smoothing: two moving-average passes over
    # about a third of a bit period each.
    width = max(1, int(spb / 3))
    kernel = np.ones(width) / width
    freq = np.convolve(np.convolve(freq, kernel, mode="same"),
                       kernel, mode="same")
    phase = 2 * np.pi * np.cumsum(freq) / rate
    return np.exp(1j * phase).astype(np.complex64)


def _discriminate(iq):
    """Instantaneous frequency of a complex baseband signal,
    in Hz, by the angle of the conjugate product."""
    import numpy as np
    prod = iq[1:] * np.conj(iq[:-1])
    return np.angle(prod) * (1.0 / (2 * np.pi))


def decode_channel(iq, rate):
    """Demodulate and deframe one channel of IQ.

    Returns (frames, stats): the telemetry dicts that passed
    framing and CRC, and an account of the attempt - bits
    sliced, sync words found, frames rejected - so the desk
    can state what the decode did, including when it found
    nothing it could stand behind."""
    import numpy as np
    stats = {"sync_found": 0, "crc_failed": 0, "malformed": 0}
    frames = []
    if iq is None or len(iq) < int(rate * 0.05):
        return frames, stats
    inst = _discriminate(np.asarray(iq, dtype=np.complex128))
    # The discriminator's output is in cycles per sample;
    # scale to Hz and smooth over a quarter bit.
    inst_hz = inst * rate
    spb = rate / BAUD
    width = max(1, int(spb / 4))
    kernel = np.ones(width) / width
    smooth = np.convolve(inst_hz, kernel, mode="same")
    bits = (smooth > 0).astype(np.uint8)
    # Bit-clock recovery and sync search, together: at each
    # candidate bit phase (samples per bit need not be an
    # integer at the caller's rate), resample the bit stream
    # and look for the preamble-plus-sync pattern; the phase
    # that finds a sync and then a CRC-valid frame wins.
    sync_bits = _bytes_to_bits(PREAMBLE[-2:] + SYNC)
    best = None
    phases = 24
    for p in range(phases):
        offset = p * spb / phases
        nbits = int((len(bits) - offset) / spb)
        if nbits < len(sync_bits) + 8:
            continue
        sample_idx = (offset + np.arange(nbits) * spb).astype(int)
        stream = bits[sample_idx]
        found = _extract_frames(stream, stats)
        if found:
            best = found
            break
        if best is None and stats["sync_found"]:
            best = []
    return best or [], stats


def _extract_frames(stream, stats):
    """Find framed payloads in a sliced bit stream (a numpy
    uint8 array or a list of 0/1). A candidate frame must
    carry the sync word and a CRC that checks; anything less
    is counted and passed over, never repaired into a decode."""
    bits = [int(b) for b in stream]
    sync_bits = _bytes_to_bits(PREAMBLE[-2:] + SYNC)
    out = []
    i = 0
    n = len(bits)
    while i + len(sync_bits) + 8 <= n:
        if bits[i:i + len(sync_bits)] == sync_bits:
            stats["sync_found"] += 1
            start = i + len(sync_bits)
            length = 0
            for k in range(8):
                if start + k < n:
                    length = (length << 1) | bits[start + k]
            body_start = start + 8
            total = length + 2  # payload + CRC
            if length == 0 or body_start + total * 8 > n:
                stats["malformed"] += 1
                i += 1
                continue
            raw = bytearray()
            for j in range(total):
                byte = 0
                for k in range(8):
                    byte = (byte << 1) | bits[body_start + j * 8 + k]
                raw.append(byte)
            payload = bytes(raw[:length])
            crc_got = (raw[length] << 8) | raw[length + 1]
            crc_want = crc16_ccitt(bytes([length]) + payload)
            if crc_got != crc_want:
                stats["crc_failed"] += 1
                i += 1
                continue
            decoded = decode_payload(payload)
            if decoded is None:
                stats["malformed"] += 1
            else:
                out.append(decoded)
            i = body_start + total * 8
        else:
            i += 1
    return out
