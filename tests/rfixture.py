"""Fixture builders for the real-source tests.

These put genuine protocol bytes on the wire the way the real tools
would deliver them: Mode S frames PPM-modulated into I/Q for a shim
rtl_sdr, an AIS sentence framed in HDLC/NRZI and modulated into
discriminator audio for a shim rtl_fm, and plain audio bursts. The
modulators follow the same published formats the demodulators in
multidex.tuner implement - they are inverses by specification, not
by shared code. The AIS frame builder below implements bit stuffing
and the CRC-16/X.25 FCS from the specification in this file, and the
Mode S modulator places its data block at the specification offset,
so a demodulator that shares a wrong convention with a fixture has
nothing to hide behind.
"""

import math
import os
import random
import struct

from multidex import codecs


# ------------------------------------------------------------- Mode S I/Q

# Mode S at 2 Msps, per the standard: preamble pulses at 0, 1.0, 3.5
# and 4.5 us (samples 0, 2, 7, 9); the 112-bit PPM data block starts
# 8 us after the preamble start (sample 16); each bit cell is 1 us
# (2 samples), pulse in the first half for a 1, second half for a 0.
_MODES_PREAMBLE_SAMPLES = (0, 2, 7, 9)
_MODES_DATA_START_SAMPLE = 16


def modes_iq(frames, rate=2000000, seconds=0.25, seed=7):
    """PPM-modulate Mode S frame hex strings into unsigned I/Q bytes."""
    rng = random.Random(seed)
    n = int(rate * seconds)
    iq = bytearray()
    for _ in range(n):
        iq += bytes((128 + rng.randrange(-3, 4),
                     128 + rng.randrange(-3, 4)))
    pos = 500
    for frame in frames:
        value = int(frame, 16)
        bits = [(value >> (111 - j)) & 1 for j in range(112)]
        pulses = list(_MODES_PREAMBLE_SAMPLES)
        for j, bit in enumerate(bits):
            pulses.append(_MODES_DATA_START_SAMPLE + 2 * j
                          + (0 if bit else 1))
        for p in pulses:
            at = (pos + p) * 2
            if at + 1 < len(iq):
                iq[at] = 232
                iq[at + 1] = 128
        pos += 240 + 400
    return bytes(iq)


# ------------------------------------------------------------------- AIS
# The AIS frame builder, from the specification (ITU-R M.1371 /
# IEC 61162): the payload bit stream is packed into HDLC data octets
# in transmission order, first bit transmitted being the least
# significant bit of its octet; the frame check sequence is
# CRC-16/X.25 (reflected polynomial 0x8408, initial value 0xFFFF,
# transmitted complemented, low octet first) over those octets; a 0
# is stuffed after any five consecutive 1s so the flag 0x7E occurs
# only at frame boundaries. Implemented here, in the fixture, so the
# production demodulator is checked against the specification and
# not against its own helper functions.

def _spec_bytes_of(bits):
    """Group a transmission-order bit stream into octets, first bit
    received being the least significant bit of its octet."""
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte |= bits[i + j] << j
        out.append(byte)
    return bytes(out)


def _spec_fcs(data):
    """CRC-16/X.25 frame check sequence over data octets, returned
    as the two octets in transmission order (low octet first)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    crc ^= 0xFFFF
    return bytes((crc & 0xFF, crc >> 8))


def ais_frame_bits(sentence):
    """The on-air bit stream (flags included) for an AIVDM sentence."""
    parts = sentence.split(",")
    payload = parts[5]
    fill = int(parts[6].split("*")[0])
    bitstr = codecs._ais_bits(payload)
    if fill:
        bitstr = bitstr[:-fill]
    bits = [int(c) for c in bitstr]
    while len(bits) % 8:
        bits.append(0)
    data = _spec_bytes_of(bits)
    frame = data + _spec_fcs(data)
    stream = []
    for byte in frame:
        for j in range(8):
            stream.append((byte >> j) & 1)
    stuffed = []
    ones = 0
    for b in stream:
        stuffed.append(b)
        if b == 1:
            ones += 1
            if ones == 5:
                stuffed.append(0)
                ones = 0
        else:
            ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    return flag + stuffed + flag


def ais_audio(sentence, rate=48000, seconds=1.2, seed=11):
    """NRZI-modulate the AIS frame into discriminator audio samples."""
    spb = rate // 9600
    bits = ais_frame_bits(sentence)
    rng = random.Random(seed)
    total = int(rate * seconds)
    samples = []
    level = 1
    # A run of idle ones (steady level) before the flag, as a real
    # channel carries between frames.
    cells = [1] * 12 + bits + [1] * 12
    for bit in cells:
        if bit == 0:
            level = -level
        for _ in range(spb):
            samples.append(level * 9000)
    while len(samples) < total:
        samples.append(level * 9000)
    # Light smoothing, the discriminator's band-limiting in miniature.
    out = []
    for i, s in enumerate(samples):
        prev = samples[i - 1] if i else s
        nxt = samples[i + 1] if i + 1 < len(samples) else s
        out.append(int((prev + 2 * s + nxt) / 4)
                   + rng.randrange(-150, 151))
    return out[:total]


def samples_bytes(samples):
    return struct.pack("<%dh" % len(samples), *samples)


def scan_iq(rate=2400000, nsamp=262144, seed=13):
    """I/Q holding one strong carrier at the window center, as a
    shim rtl_sdr delivers for any census scan: the FFT in the real
    source finds exactly one emission, at the center frequency."""
    rng = random.Random(seed)
    out = bytearray()
    for i in range(nsamp):
        out += bytes((205 + rng.randrange(-3, 4),
                      128 + rng.randrange(-3, 4)))
    return bytes(out)


# -------------------------------------------------------------- shim tools

FIXTURE_ICAO = "ABC123"
FIXTURE_MMSI = "366123456"

_RTL_SDR_SHIM = '''#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
out = args[-1]
freq = int(float(args[args.index("-f") + 1]))
rate = int(float(args[args.index("-s") + 1]))
src = "scan.iq"
if freq == 1090000000:
    src = "modes.iq"
elif rate == 250000:
    src = "ism.iq"
shutil.copyfile(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), src), out)
'''

_RTL_FM_SHIM = '''#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
out = args[-1]
freq = int(float(args[args.index("-f") + 1]))
src = "quiet.raw"
if freq in (161975000, 162025000):
    src = "ais.raw"
elif freq == 162475000:
    src = "wx.raw"
elif freq in (137100000, 137620000, 137900000):
    src = "sat.raw"
elif 118000000 <= freq < 137000000:
    src = "airband.raw"
shutil.copyfile(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), src), out)
'''

_RTL_433_SHIM = '''#!/usr/bin/env python3
print('{"time": "2026-11-17 12:00:00", "model": "Acurite-5n1", '
      '"id": 4242, "temperature_C": 21.5, "humidity": 40, '
      '"rssi": -71.0}')
print('{"time": "2026-11-17 12:00:01", "model": "LaCrosse-TX141", '
      '"id": 87, "temperature_C": 19.0, "rssi": -80.0}')
'''

_MULTIMON_SHIM = '''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
decoders = [args[i + 1] for i, a in enumerate(args) if a == "-a"]
if "EAS" in decoders:
    print("EAS: %s" % SAME_HEADER)
if any(d.startswith("POCSAG") for d in decoders):
    print("POCSAG1200: Address: 1234567 Function: 0 "
          "Alpha: FIXTURE PAGE TEST")
if "AFSK1200" in decoders:
    print("APRS: W0TEST>APRS,TCPIP*:!3900.00N/09800.00W-fixture")
'''

_RTL_POWER_SHIM = "#!/bin/sh\nexit 0\n"


def write_shims(directory):
    """Write the fake receiver tools and their fixture files.

    The shims answer the way the real tools would on a good day:
    genuine Mode S frames in the 1090 capture, a genuine AIS frame
    in the marine audio, a SAME header from the EAS decoder, POCSAG
    and APRS decodes from multimon-ng, JSON devices from rtl_433.
    """
    from multidex import modes
    os.makedirs(directory, exist_ok=True)

    def put(name, data, mode="wb"):
        with open(os.path.join(directory, name), mode) as fh:
            fh.write(data)

    frames = [
        modes.encode_callsign(FIXTURE_ICAO, "DLH1234"),
        modes.encode_position(FIXTURE_ICAO, 39.5, -98.2, 35000,
                              is_odd=False),
        modes.encode_position(FIXTURE_ICAO, 39.5, -98.2, 35000,
                              is_odd=True),
        modes.encode_velocity(FIXTURE_ICAO, 450, 90),
    ]
    put("modes.iq", modes_iq(frames))
    sentence = codecs.ais_encode_position(
        FIXTURE_MMSI, 39.4, -98.1, sog_kt=8.5, cog_deg=92.0,
        heading_deg=92)
    put("ais.raw", samples_bytes(ais_audio(sentence)))
    put("airband.raw", samples_bytes(am_burst_audio()))
    put("wx.raw", samples_bytes(fm_tone_audio()))
    put("sat.raw", samples_bytes(fm_tone_audio(rate=48000, seconds=1.0)))
    put("quiet.raw", samples_bytes(quiet_audio(rate=22050)))
    put("scan.iq", scan_iq())
    put("ism.iq", bytes(2000))
    same = codecs.same_build("TOR", ["020091"], "KXXX",
                             issued="3171200")
    put("rtl_sdr", _RTL_SDR_SHIM, "w")
    put("rtl_fm", _RTL_FM_SHIM, "w")
    put("rtl_433", _RTL_433_SHIM, "w")
    put("multimon-ng", _MULTIMON_SHIM.replace(
        "SAME_HEADER", repr(same)), "w")
    put("rtl_power", _RTL_POWER_SHIM, "w")
    for name in ("rtl_sdr", "rtl_fm", "rtl_433", "multimon-ng",
                 "rtl_power"):
        os.chmod(os.path.join(directory, name), 0o755)
    return directory


# ------------------------------------------------------------------ audio

def am_burst_audio(rate=12000, seconds=6.0, burst_at=2.0, burst_s=2.0,
                   seed=3):
    """Quiet AM audio with one loud burst: a voice-energy segment."""
    rng = random.Random(seed)
    out = []
    for i in range(int(rate * seconds)):
        t = i / rate
        if burst_at <= t < burst_at + burst_s:
            v = int(12000 * math.sin(2 * math.pi * 700 * t))
            out.append(v + rng.randrange(-400, 401))
        else:
            out.append(rng.randrange(-120, 121))
    return out


def fm_tone_audio(rate=22050, seconds=2.0, seed=5):
    """Loud steady FM audio: a broadcast carrier present."""
    rng = random.Random(seed)
    return [int(9000 * math.sin(2 * math.pi * 1000 * i / rate))
            + rng.randrange(-200, 201)
            for i in range(int(rate * seconds))]


def quiet_audio(rate=12000, seconds=2.0, seed=9):
    rng = random.Random(seed)
    return [rng.randrange(-100, 101) for _ in range(int(rate * seconds))]
