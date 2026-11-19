"""ACARS physical layer and ARINC 618 frame assembly (0.2.0).

Pure Python + numpy. ACARS is MSK at 2400 baud on AM: bit 1 is
the 1200 Hz tone, bit 0 the 2400 Hz tone. Characters are 7-bit
ASCII with an odd-parity eighth bit, transmitted LSB first. A
frame runs: bit-sync preamble, SYN SYN SOH, mode, address
(7 chars), acknowledgement character, label (2 chars), block id,
STX, text, ETX/ETB, and a two-byte block check sequence -
CRC-16 (polynomial x^16+x^12+x^5+1, initialised to zero) over
mode through ETX inclusive.

The decoder is noncoherent (per-bit tone energy), searches all
eight byte alignments for the SYN SYN SOH opening, verifies
parity on the frame bytes and the CRC before any frame is
reported, and normalises surviving frames into the project's
ACARS text contract (codecs.acars_build). Frames that fail
parity or CRC are dropped silently by design: a datalink desk
reports decodes, not guesses.

Validation in this build: synthetic encode/decode round-trips
including CRC-failure and parity-failure rejection, in the test
suite. The encoder exists for those fixtures.
"""

import numpy as np

BAUD = 2400
TONE_ONE = 1200.0
TONE_ZERO = 2400.0
SYN = 0x16
SOH = 0x01
STX = 0x02
ETX = 0x03
ETB = 0x17


def crc16(data, init=0):
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def _with_parity(byte):
    byte &= 0x7F
    ones = bin(byte).count("1")
    return byte | (0x80 if ones % 2 == 0 else 0)


def _parity_ok(byte):
    return bin(byte).count("1") % 2 == 1


def build_frame_bytes(mode, address, ack, label, block_id, text):
    """The full on-air byte sequence (parity applied), preamble
    included, for fixtures."""
    body = [ord(mode)]
    body += [ord(c) for c in (address + ".......")[:7]]
    body.append(ord(ack))
    body += [ord(c) for c in (label + "  ")[:2]]
    body.append(ord(block_id))
    body.append(STX)
    body += [ord(c) & 0x7F for c in text[:220]]
    body.append(ETX)
    crc = crc16(bytes(b & 0x7F for b in body))
    preamble = [0x55] * 6 + [SYN, SYN, SOH]
    # Parity rides on the character bytes only; the two BCS bytes
    # are transmitted as they are.
    return bytes([_with_parity(b) for b in preamble + body]
                 + [(crc >> 8) & 0xFF, crc & 0xFF])


def bytes_to_bits(data):
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> i) & 1)
    return bits


def bits_to_audio(bits, rate=12000, amp=0.8):
    """MSK fixture audio: one tone per bit, phase-continuous."""
    spb = rate // BAUD
    out = np.zeros(len(bits) * spb)
    phase = 0.0
    for k, bit in enumerate(bits):
        freq = TONE_ONE if bit else TONE_ZERO
        idx = np.arange(spb)
        seg = np.sin(phase + 2 * np.pi * freq * idx / rate)
        out[k * spb:(k + 1) * spb] = amp * seg
        phase += 2 * np.pi * freq * spb / rate
    return out


def _tone_bits(samples, rate):
    """Noncoherent demodulation: per bit period, the tone with
    more energy wins. Returns one candidate bit stream per sample
    phase (plus the strongest stream's inverse): at some phases a
    stream is bit-slipped or alternately inverted, and the frame
    layer's parity and CRC checks are the arbiters of which
    candidate, if any, is a real frame."""
    spb = int(round(rate / BAUD))
    if len(samples) // spb < 64:
        return []
    idx = np.arange(spb)
    ref1 = np.exp(-2j * np.pi * TONE_ONE * idx / rate)
    ref0 = np.exp(-2j * np.pi * TONE_ZERO * idx / rate)
    streams = []
    best = None
    for phase in range(spb):
        n_bits = (len(samples) - phase) // spb
        if n_bits < 64:
            continue
        seg = samples[phase:phase + n_bits * spb]
        seg = seg[:n_bits * spb].reshape(n_bits, spb)
        e1 = np.abs(seg @ ref1) ** 2
        e0 = np.abs(seg @ ref0) ** 2
        sep = np.mean(np.abs(e1 - e0) / (e1 + e0 + 1e-12))
        bits = (e1 > e0).astype(int)
        streams.append(bits)
        if best is None or sep > best[0]:
            best = (sep, bits)
    if best is not None:
        streams.append(1 - best[1])
    return streams


def _bytes_at(bits, shift):
    out = []
    usable = (len(bits) - shift) // 8 * 8
    for i in range(shift, shift + usable, 8):
        byte = 0
        for j in range(8):
            byte |= int(bits[i + j]) << j
        out.append(byte)
    return out


def _parse_frame(raw, rawb=None):
    """raw: parity-stripped bytes starting at the mode character;
    rawb: the same bytes unstripped, for the BCS (which carries
    no parity and must be read as transmitted).
    Returns a frame dict or None (parity/CRC/structure)."""
    if len(raw) < 16:
        return None
    mode = chr(raw[0])
    address = "".join(chr(b) for b in raw[1:8]).rstrip(".")
    ack = chr(raw[8])
    label = "".join(chr(b) for b in raw[9:11])
    block_id = chr(raw[11])
    if raw[12] != STX:
        return None
    end = None
    for i in range(13, min(len(raw) - 2, 13 + 224)):
        if raw[i] in (ETX, ETB):
            end = i
            break
    if end is None:
        return None
    text = "".join(chr(b) if 32 <= b < 127 else " "
                   for b in raw[13:end])
    src = rawb if rawb is not None else raw
    expect = (src[end + 1] << 8) | src[end + 2]
    if crc16(bytes(raw[:end + 1])) != expect:
        return None
    return {"mode": mode, "tail": address, "ack": ack,
            "label": label, "block": block_id, "text": text}


def decode_audio(samples, rate=12000):
    """All verified frames in one audio burst, as normalised
    ACARS text strings (the codecs.acars_build contract)."""
    from . import codecs
    samples = np.asarray(samples, dtype=float)
    if len(samples) < 512:
        return []
    streams = _tone_bits(samples, rate)
    out = []
    for bits in streams:
        for shift in range(8):
            raw_bytes = _bytes_at(bits, shift)
            stripped = [b & 0x7F for b in raw_bytes]
            for i in range(len(stripped) - 2):
                if stripped[i] == SYN and stripped[i + 1] == SYN \
                        and stripped[i + 2] == SOH:
                    frame_bytes = raw_bytes[i + 3:]
                    plain = stripped[i + 3:]
                    # Parity gate: the frame's character bytes
                    # must be overwhelmingly parity-clean (the
                    # trailing BCS pair carries no parity and is
                    # excluded).
                    span = min(len(frame_bytes), 240)
                    gated = frame_bytes[:span][:-2] \
                        or frame_bytes[:span]
                    ok = sum(1 for b in gated if _parity_ok(b))
                    if gated and ok / len(gated) < 0.95:
                        continue
                    frame = _parse_frame(plain, frame_bytes)
                    if frame is None:
                        continue
                    text = frame["text"].strip()
                    flight = "-"
                    body = text
                    # The common downlink prefix: 3-digit serial,
                    # 4-character flight id (airline pair +
                    # number), then a separator. Anything else
                    # keeps the full text and reports no flight
                    # id rather than carving one out of content.
                    if len(text) >= 8 and text[:3].isdigit() \
                            and text[3:5].isalpha() \
                            and text[5:7].isalnum() \
                            and text[7] in " /":
                        flight = text[3:7]
                        body = text[7:].lstrip(" /")
                    out.append(codecs.acars_build(
                        frame["tail"], flight, frame["label"],
                        body or text, frame["mode"]))
                    return out
    return out


# ------------------------------------------------------- front end
#
# The conforming front end: the one route by which audio
# reaches the frame layer, shared by the desk's live path
# (multidex.tuner), the probe desk's ACARS attempt
# (multidex.desks), and the retroactive ACARS decoder
# (multidex.retro), so the three cannot drift apart. It
# exists because of the frame layer's parity gate, above:
# the gate reads a span of up to 240 bytes from a frame's
# start and demands the span overwhelmingly parity-clean,
# the BCS pair aside, so bytes demodulated from whatever
# trails a frame inside its slice - capture padding, or a
# tuner margin - sink the gate and a verified frame is
# discarded as if it were noise. A slice must therefore end
# at the burst's activity edge, within a fraction of a byte
# of it. The front end's halves are an AM demodulation
# against a local carrier baseline (for paths that start
# from channel IQ) and an envelope segmentation that ends
# each slice at the activity edge with a guard of half the
# smoothing window - six bits at the frame layer's rate,
# under one byte - which covers the smoothed envelope's own
# edge softness without feeding the gate a trailing span of
# noise it cannot afford.

FRONT_RATE = 12000


def _resample_fft(samples, rate_in, rate_out):
    """FFT resampling: truncate or zero-pad the spectrum at
    the new rate."""
    n_out = int(len(samples) * rate_out / rate_in)
    if n_out <= 0:
        return samples
    spec = np.fft.rfft(samples)
    out_spec = np.zeros(n_out // 2 + 1, dtype=complex)
    m = min(len(spec), len(out_spec))
    out_spec[:m] = spec[:m]
    return np.fft.irfft(out_spec, n_out)


def am_demod(chan, rate):
    """AM-demodulate a baseband channel to audio at
    FRONT_RATE, the rate the frame layer works at. The
    envelope is taken against a local carrier baseline - a
    moving average spanning ten cycles of the 1200 Hz tone,
    so the tones themselves average out of it - and the
    baseline is subtracted, which leaves silence quiet, as
    the rtl_fm audio the tuner feeds the frame layer is
    quiet in silence. A single global mean, the census
    demodulator's way, leaves a baseline step at a burst's
    edges that biases the frame layer's per-bit tone
    decisions across the whole burst."""
    env = np.abs(chan)
    w = max(8, int(round(rate / TONE_ONE)) * 10)
    pad = np.pad(env, w, mode="reflect")
    base = np.convolve(pad, np.ones(w) / w, mode="same")[w:-w]
    return _resample_fft(env - base, rate, FRONT_RATE)


def burst_slices(audio, rate=FRONT_RATE):
    """Burst slices of demodulated audio, segmented on the
    envelope in the tuner's own ACARS pattern: 5 ms
    smoothing, activity at four times the median envelope,
    gaps under a tenth of a second bridged, and slices kept
    only past a tenth of a second of activity. Each slice is
    extended by half the smoothing window at both ends -
    six bits at the frame layer's rate, under one byte -
    which covers the smoothed envelope's edge softness (in
    heavy noise its crossing can sit a few samples inside
    the burst, and the frame's last bits live there) at a
    trailing cost the parity gate absorbs, where the
    tuner's former 600-sample margins fed it fifteen bytes
    of noise and sank it. An excerpt that is one burst wall
    to wall segments to itself; audio with no activity
    segments to nothing."""
    audio = np.asarray(audio, dtype=float)
    n = len(audio)
    if n == 0:
        return []
    smooth = max(1, int(round(rate * 0.005)))
    bridge = int(round(rate * 0.1))
    guard = smooth // 2
    env = np.abs(audio)
    if smooth > 1:
        env = np.convolve(env, np.ones(smooth) / smooth,
                          mode="same")
    floor = float(np.median(env)) + 1e-12
    active = env > floor * 4.0
    slices = []
    i = 0
    while i < n:
        if active[i]:
            j = i
            while j < n and (active[j] or
                             (j + bridge < n and active[j + bridge])):
                j += 1
            if j - i > bridge:
                slices.append(audio[max(0, i - guard):
                                    min(n, j + guard)])
            i = j
        else:
            i += 1
    return slices


def decode_sliced(audio, rate=FRONT_RATE, whole_fallback=False):
    """All verified frames in demodulated audio, decoded
    slice by slice through the frame layer. The slicing is
    load-bearing twice over: the frame layer reports at
    most one frame per call, so one slice per burst is what
    lets two frames in one window both be heard, and the
    activity-edge slice ends are what the parity gate
    demands. With whole_fallback, audio that segments to
    nothing - a retained excerpt whose activity never
    crosses the segmentation floor, offered as the burst
    candidate it was filed as - is offered whole; dwell
    captures pass False, because a silent dwell is silence,
    not a candidate. Audio with no activity and no fallback
    decodes to nothing and files nothing."""
    slices = burst_slices(audio, rate)
    if not slices and whole_fallback and len(audio):
        slices = [np.asarray(audio, dtype=float)]
    out = []
    for burst in slices:
        out.extend(decode_audio(burst, rate))
    return out
