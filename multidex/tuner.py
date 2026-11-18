"""The Tuner Server: sole owner of the receiver (design section 5).

Two sources sit behind it. The simulated source (multidex.sim) is a
deterministic synthetic spectrum that runs the whole system with no
hardware. The real source shells to the receiver tools a normal host
carries - rtl_sdr, rtl_fm, rtl_power's sibling tools rtl_433 and
multimon-ng - and feeds the standing desks from real captures:

- sweep, broadcast, cellular, trunking and the sonde window are fed
  from FFT censuses of short rtl_sdr captures;
- ADS-B is fed by a Mode S demodulator in this module: preamble
  detection and PPM slicing over the magnitude stream, with the
  desk's CRC check deciding what is real;
- AIS is fed by a discriminator demodulator in this module: HDLC
  deframing with NRZI decode and the X.25 FCS, emitting AIVDM
  sentences only for frames that pass, which the desk then decodes
  through the AIS checksum as well;
- airband and weather radio are fed from rtl_fm audio - envelope
  segmentation for voice activity, and multimon-ng's EAS decoder for
  SAME headers on the weather channel;
- pager and APRS are fed from rtl_fm audio decoded by multimon-ng;
- ISM is fed by rtl_433 run over a captured I/Q file;
- satellite passes are fed by a measured signal lock on the pass
  frequency; on a lock the whole remaining pass is recorded at
  maximum gain and filed as evidence, and the satellite desk
  decodes APT imagery from that recording when aptdec is staged.

Desks that need a decoder program outside that tool set bench with
the reason stated (multidex.desks). A capture failure on one desk is
recorded in the source's error map and filed by the Watch Officer as
a gap for that desk; it never takes the whole dwell down.

Partial captures are evidence (0.1.1): a streaming capture stopped
by its time limit keeps whatever bytes it wrote, and only a capture
that returns nothing usable is a failure.

Configured tuner outages (a rehearsal knob) are enforced here, so the
failure behavior of section 13 is exercised the same way on any source.
"""

import array
import base64
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import wave

from . import codecs, modes


class TunerLost(Exception):
    """The tuner is gone or taken. A coverage gap, never a fight."""


def _fft(values):
    """Iterative radix-2 FFT over a list of complex numbers."""
    n = len(values)
    if n == 1:
        return values
    even = _fft(values[0::2])
    odd = _fft(values[1::2])
    out = [0j] * n
    for k in range(n // 2):
        tw = complex(math.cos(-2 * math.pi * k / n),
                     math.sin(-2 * math.pi * k / n)) * odd[k]
        out[k] = even[k] + tw
        out[k + n // 2] = even[k] - tw
    return out


# ---------------------------------------------------------- capture runs

def _stream_to_file(cmd, path, seconds):
    """Run a capture tool that writes `path`, for at most `seconds`.

    Streaming tools (rtl_fm) never exit on their own: at the limit
    the tool is stopped and the file keeps whatever was captured.
    One-shot tools (rtl_sdr -n) exit by themselves well inside the
    limit. Returns the number of bytes in the file; never raises for
    reaching the limit.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _run_tool(cmd, timeout=60):
    """Run a file-consuming tool; return its stdout text.

    On timeout the partial stdout is returned: a decoder stopped
    mid-file has still decoded the part it read.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return out.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        return (exc.output or b"").decode("utf-8", "replace")


_JT8_LINE = re.compile(
    r"^\d{6}\s+(-?\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+~\s+(.*)$")
_DSD_PATTERNS = [
    ("DMR", re.compile(r"DMR.*?(?:TG|Talkgroup)[:\s]*(\d+)", re.I)),
    ("P25", re.compile(r"P25.*?(?:TG|Talkgroup)[:\s]*(\d+)", re.I)),
    ("NXDN", re.compile(r"NXDN.*?(?:TG|Talkgroup)[:\s]*(\d+)", re.I)),
]


def _run_jt8(audio12, ft8_hz):
    """Write 12 kHz audio to a temp WAV and run the host's jt9
    in FT8 mode; parse its decode lines."""
    out = []
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav",
                                         delete=False) as fh:
            path = fh.name
        import numpy as np
        pcm = (np.clip(audio12, -1, 1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(12000)
            w.writeframes(pcm.tobytes())
        text = _run_tool(["jt9", "-8", "-f", "1500", path],
                         timeout=90)
        for line in text.splitlines():
            m = _JT8_LINE.match(line.strip())
            if m:
                snr, dt, off, msg = m.groups()
                out.append({"freq_hz": ft8_hz + int(off),
                            "snr_db": int(snr), "dt_s": float(dt),
                            "text": msg.strip()})
    except Exception:
        return out
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    return out


def _run_dumpvdl2(source, t0, t1):
    """IQ capture into dumpvdl2 with JSON output; messages are
    the frames libacars structured (ACARS over VDL2 where the
    payload is ACARS). Any tool failure yields an empty list -
    the dwell's gap machinery reports capture health separately,
    and the burst census is the no-tool path, not a fallback for
    a broken tool."""
    msgs = []
    iq_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".iq",
                                         delete=False) as fh:
            iq_path = fh.name
        with tempfile.NamedTemporaryFile(suffix=".json",
                                         delete=False) as fh:
            out_path = fh.name
        rate = 1050000
        size = _stream_to_file(
            ["rtl_sdr", "-f", "136975000", "-s", str(rate),
             "-n", str(rate * 45), "-g", "23", iq_path],
            iq_path, 90)
        if size == 0:
            return msgs
        # dumpvdl2 2.7.0 invocation, verified against the staged
        # program: the input flag is --iq-file (--iq is accepted
        # as an abbreviation), there is no --sample-rate option
        # (the default rate is already 1050000 sps and the
        # default channel 136975000 Hz, matching the capture),
        # and the output specifier grammar is
        # decoded:json:file:path=<file>. The 0.2.0 form failed on
        # both counts and _run_tool swallowed the failure, so the
        # desk decoded nothing while appearing to run.
        _run_tool(["dumpvdl2", "--iq-file", iq_path,
                   "--output", "decoded:json:file:path=%s" % out_path],
                  timeout=180)
        with open(out_path) as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                v = obj.get("vdl2", {})
                # In the program's JSON the ACARS block libacars
                # structured nests under the AVLC frame that
                # carried it (vdl2.avlc.acars); vdl2.app carries
                # only the program name and version. Frames with
                # no ACARS block yield no message - nothing is
                # extracted from other payloads.
                avlc = v.get("avlc", {})
                acars = avlc.get("acars", {}) if isinstance(
                    avlc, dict) else {}
                if acars:
                    dst = avlc.get("dst", {})
                    msgs.append({
                        "tail": acars.get("reg") or (
                            dst.get("addr") if isinstance(
                                dst, dict) else None),
                        "text": "ACARS over VDL2 label %s: %s"
                                % (acars.get("label"),
                                   (acars.get("msg_text") or "")
                                   .strip()),
                        "raw": line.strip()[:400]})
    except Exception:
        return msgs
    finally:
        for p in (iq_path, out_path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
    return msgs


def _run_dsd_fme(center_hz, seconds):
    """Run DSD-FME against the RTL-SDR for a bounded slice and
    parse its log for call metadata. The program's own output
    vocabulary varies by build; the parser is deliberately
    narrow (protocol + talkgroup lines) and anything it cannot
    read is not reported. Field note: the program's rtl path
    reports a center frequency 252 kHz above the requested
    channel, which reads as its internal offset tuning (its
    demod output is at 12 kHz); the invocation is unchanged
    for it."""
    calls = []
    try:
        proc = subprocess.Popen(
            ["dsd-fme", "-i", "rtl:0:%d:23" % int(center_hz),
             "-o", "null"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            raw, _ = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            raw, _ = proc.communicate()
        text = raw.decode("utf-8", "replace")
    except Exception:
        return calls
    seen = set()
    for line in text.splitlines():
        for proto, pat in _DSD_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            src = re.search(r"(?:SRC|Source)[:\s]*(\d+)", line, re.I)
            slot = re.search(r"(?:SLOT|TS)[:\s]*([12])", line, re.I)
            key = (proto, m.group(1),
                   src.group(1) if src else None)
            if key in seen:
                continue
            seen.add(key)
            calls.append({
                "protocol": proto, "talkgroup": m.group(1),
                "source": src.group(1) if src else None,
                "slot": int(slot.group(1)) if slot else None,
                "encrypted": "encrypt" in line.lower(),
                "freq_hz": center_hz})
    return calls


def _dumphfdl_systable_path():
    """The staged dumphfdl's system table, resolved from the
    program's own location: the staged layout puts the program
    in <prefix>/bin and the table in
    <prefix>/share/dumphfdl/systable.conf. dumphfdl does not
    load the table by default (its documentation says so, and a
    bounded run without the option confirmed it), so the feed
    passes it explicitly when the staged file is found beside
    the program. No host's home path is written here: the
    resolution follows whichever dumphfdl the PATH carries.
    Returns None when no table sits at the resolved path; the
    program still decodes without it, with numeric station and
    frequency ids in its output."""
    exe = shutil.which("dumphfdl")
    if not exe:
        return None
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
    path = os.path.join(prefix, "share", "dumphfdl", "systable.conf")
    return path if os.path.exists(path) else None


def _parse_dumphfdl_output(path):
    """Parse dumphfdl decoded JSON output into message dicts.

    The envelope is the program's own (fmtr-json in the
    dumphfdl source): {"hfdl": {"app", "t", "freq" (Hz),
    "bit_rate", "sig_level", ..., <pdu>}} where <pdu> is one of
    spdu, mpdu, lpdu, hfnpdu carrying "err", "src" and "dst"
    party objects. The parser is deliberately narrow, on the
    DSD-FME discipline: a frame whose PDU is flagged err, and
    anything that is not a decoded HFDL frame, yields no
    message."""
    msgs = []
    try:
        fh = open(path)
    except OSError:
        return msgs
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            h = obj.get("hfdl", {})
            if not isinstance(h, dict):
                continue
            pdu = None
            for key in ("spdu", "lpdu", "mpdu", "hfnpdu"):
                if isinstance(h.get(key), dict):
                    pdu = key
                    break
            if pdu is None:
                continue
            body = h[pdu]
            if body.get("err"):
                continue
            type_name = None
            ptype = body.get("type")
            if isinstance(ptype, dict):
                type_name = ptype.get("name")
            msgs.append({
                "freq_hz": h.get("freq"),
                "pdu": pdu,
                "src": body.get("src"),
                "dst": body.get("dst"),
                "type_name": type_name,
                "ac_info": body.get("ac_info"),
                "raw": line.strip()[:400]})
    return msgs


def _run_dumphfdl(center_hz, channels_khz, seconds):
    """Run dumphfdl against the RTL-SDR for a bounded slice and
    return its decoded frames. The invocation is the staged
    program's proven form: SoapySDR device driver=rtlsdr with
    direct sampling mode 2 (the HF input), 3.2 Msps, gain 40,
    the window centre in kHz, decoded JSON to a file, and the
    monitored channels as kHz arguments. dumphfdl exits
    gracefully on TERM, so the bound is a terminate, and the
    output file keeps whatever was decoded up to it. Any
    failure yields an empty list - the dwell's gap machinery
    reports capture health separately."""
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json",
                                         delete=False) as fh:
            out_path = fh.name
        cmd = ["dumphfdl", "--soapysdr", "driver=rtlsdr",
               "--device-settings", "direct_samp=2",
               "--sample-rate", "3200000",
               "--centerfreq", "%g" % (center_hz / 1000.0),
               "--gain", "40",
               "--output", "decoded:json:file:path=%s" % out_path]
        table = _dumphfdl_systable_path()
        if table:
            cmd += ["--system-table", table]
        cmd += ["%g" % f for f in channels_khz]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return _parse_dumphfdl_output(out_path)
    except Exception:
        return []
    finally:
        if out_path:
            try:
                os.remove(out_path)
            except OSError:
                pass


# ----------------------------------------------------------- Mode S demod

def demod_modes(raw, rate=2000000):
    """Demodulate DF17/DF18 Mode S frames from unsigned 8-bit I/Q.

    Preamble detection on the power stream, then PPM bit slicing:
    each bit cell is two samples, pulse-first is a 1. Frames are
    returned as hex strings, deduplicated; the ADS-B desk's CRC
    check has the final word on every one.
    """
    n = len(raw) // 2
    if n < 240:
        return []
    mag = [0.0] * n
    total = 0.0
    for i in range(n):
        iv = raw[2 * i] - 127.5
        qv = raw[2 * i + 1] - 127.5
        p = iv * iv + qv * qv
        mag[i] = p
        total += p
    mean = total / n
    frames = []
    seen = set()
    i = 0
    # The data block starts 8 us after the preamble start: at 2 Msps
    # that is sample 16. Samples 10-15 are the guard interval between
    # the last preamble pulse and the first data cell; slicing from
    # sample 10 reads the guard as data and real frames never decode.
    last = n - (16 + 224)
    while i < last:
        high = min(mag[i], mag[i + 2], mag[i + 7], mag[i + 9])
        if high > mean * 3:
            low = max(mag[i + 1], mag[i + 3], mag[i + 4], mag[i + 5],
                      mag[i + 6], mag[i + 8])
            if high > low * 1.8:
                value = 0
                base = i + 16
                for j in range(112):
                    value = (value << 1) | (
                        1 if mag[base + 2 * j] >= mag[base + 2 * j + 1]
                        else 0)
                frame = "%028X" % value
                if modes.df(frame) in (17, 18) and modes.crc_ok(frame):
                    if frame not in seen:
                        seen.add(frame)
                        frames.append(frame)
                    i = base + 224
                    continue
        i += 1
    return frames


# -------------------------------------------------------------- AIS demod
# AIS rides in HDLC frames: NRZI encoding, bit stuffing after five
# ones, and the CRC-16/X.25 FCS. After an FM discriminator the audio
# waveform is close to the NRZI level stream itself, so the demod is:
# slice at every plausible clock phase, NRZI decode, hunt the flag,
# de-stuff, and keep only frames whose FCS verifies. A frame that
# fails is dropped, never repaired; the desk's AIS checksum check is
# a second gate downstream.

def _crc_x25(data):
    """CRC-16/X.25 register over bytes, LSB-first, init 0xFFFF."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc


def _bytes_of(bits):
    """Group a transmission-order bit stream into bytes, first bit
    received being the least significant bit of its byte."""
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte |= bits[i + j] << j
        out.append(byte)
    return bytes(out)


def _ais_payloads_from_bits(bits):
    """HDLC deframe a received bit stream; yield AIVDM sentences.

    Flags are hunted in the raw stream first - bit stuffing
    guarantees six consecutive ones occur only in a flag - and the
    content between flags is de-stuffed and FCS-checked after.
    """
    out = []
    pos = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    while True:
        try:
            start = _find_flag(bits, flag, pos)
            end = _find_flag(bits, flag, start + 8)
        except ValueError:
            return out
        raw = []
        ones = 0
        valid = True
        for b in bits[start + 8:end]:
            if ones == 5:
                if b == 0:
                    ones = 0
                    continue  # stuffed zero: not data
                valid = False  # six ones inside a frame: garbage
                break
            raw.append(b)
            ones = ones + 1 if b == 1 else 0
        pos = end
        if not valid or len(raw) < 40:
            continue
        frame = _bytes_of(raw)
        if len(frame) >= 5:
            data, fcs = frame[:-2], frame[-2:]
            want = _crc_x25(data) ^ 0xFFFF
            if fcs[0] == (want & 0xFF) and fcs[1] == (want >> 8):
                payload_bits = "".join(
                    str(b) for b in raw[:len(data) * 8])
                payload, fill = codecs._ais_payload(payload_bits)
                out.append(codecs._ais_sentence(payload, fill))


def _find_flag(bits, flag, pos):
    n = len(bits)
    for i in range(pos, n - 7):
        if bits[i:i + 8] == flag:
            return i
    raise ValueError("no flag")


def demod_ais(samples, rate):
    """Demodulate AIS sentences from discriminator audio samples."""
    if rate % 9600:
        return []
    spb = rate // 9600
    if not samples:
        return []
    mean = sum(samples) / len(samples)
    levels = [1 if s > mean else 0 for s in samples]
    sentences = []
    seen = set()
    for phase in range(spb):
        bits = []
        prev = levels[phase + spb // 2] \
            if phase + spb // 2 < len(levels) else 0
        k = phase + spb // 2
        while k < len(levels):
            level = levels[k]
            bits.append(1 if level == prev else 0)  # NRZI
            prev = level
            k += spb
        for sentence in _ais_payloads_from_bits(bits):
            if sentence not in seen:
                seen.add(sentence)
                sentences.append(sentence)
        if sentences:
            break
    return sentences


# ------------------------------------------------------------------ audio

def _wav_b64(samples, rate):
    """A mono 16-bit WAV of int samples, base64 for the feed."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _write_wav(path, samples, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


def _resample_int16(samples, src_rate, dst_rate):
    """Linear-interpolating resample of int16 samples.

    Satellite pass audio is stored at 11025 Hz, the rate APT
    tooling expects: a full pass stored at the 48 kHz capture rate
    is over four times the bytes for no more signal, because the
    APT subcarrier sits at 2400 Hz, far under the stored rate's
    Nyquist limit. Works on an array('h') so a ten-minute pass
    never materializes as a list of Python ints.
    """
    if src_rate == dst_rate or not samples:
        return array.array("h", samples)
    n_out = int(len(samples) * dst_rate // src_rate)
    out = array.array("h", bytes(2 * n_out))
    step = src_rate / dst_rate
    last = len(samples) - 1
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        if j >= last:
            out[i] = samples[last]
        else:
            frac = pos - j
            out[i] = int(samples[j] * (1.0 - frac)
                         + samples[j + 1] * frac)
    return out


# Cellular downlink bands the census desk can speak to from a scan:
# (low Hz, high Hz, band words). Presence only, graded PROBABLE.
_DOWNLINK_BANDS = [
    (617e6, 652e6, "LTE B71 downlink"),
    (729e6, 746e6, "LTE B12 downlink"),
    (746e6, 756e6, "LTE B13 downlink"),
    (869e6, 894e6, "LTE B5 downlink"),
    (925e6, 960e6, "GSM 900 downlink"),
]

_PAGER_RE = re.compile(
    r"POCSAG\d+: Address:\s*(\d+)\s+Function:\s*(\d+)\s+Alpha:\s*(.*)")
_APRS_RE = re.compile(r"([A-Z0-9]{3,}(?:-[0-9]{1,2})?)>")


class RealSource:
    kind = "real"

    def __init__(self, profile):
        self.profile = profile
        self.errors = {}
        self._scan_cache = {}

    # ------------------------------------------------------------- gates
    def available(self):
        missing = [tool for tool in ("rtl_sdr", "rtl_fm")
                   if shutil.which(tool) is None]
        if missing:
            return False, ("real source needs %s on the PATH; not found"
                           % " and ".join(missing))
        detail = "rtl_sdr and rtl_fm present"
        for tool in ("rtl_433", "multimon-ng"):
            detail += "; %s %s" % (
                tool, "present" if shutil.which(tool) else
                "absent (its desks file the loss per dwell)")
        detail += "; aptdec %s" % (
            "present" if shutil.which("aptdec") else
            "absent (satellite passes file their audio; APT imagery "
            "is reported not decoded)")
        return True, detail

    def capabilities(self):
        caps = {"sweep", "adsb", "airband", "milair", "ais", "wxradio",
                "ism", "aprs", "pager", "satellite", "sonde", "broadcast",
                "cellular", "trunking", "acars", "audiocensus",
                "amateur", "iss"}
        # Tool-bound desks stand on the real source only when their
        # decoder program is present on the host; desk_state states
        # the staging reason when they are not.
        from .toolbind import tool_available
        if tool_available("dumpvdl2"):
            caps.add("vdl2")
        if tool_available("dsd-fme"):
            caps.add("digitalvoice")
        if tool_available("dumphfdl"):
            caps.add("hfdl")
        return caps

    # -------------------------------------------------------- raw capture
    def _rtl_fm(self, freq_hz, mode, rate, seconds, squelch=0,
                gain="23"):
        with tempfile.NamedTemporaryFile(suffix=".raw",
                                         delete=False) as fh:
            path = fh.name
        try:
            size = _stream_to_file(
                ["rtl_fm", "-f", str(int(freq_hz)), "-M", mode,
                 "-s", str(rate), "-g", gain, "-l", str(squelch),
                 path],
                path, seconds)
            if size == 0:
                raise TunerLost("rtl_fm produced no audio")
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _audio(self, freq_hz, mode, rate, seconds, gain="23"):
        """One rtl_fm capture as a list of int16 samples.

        Raises TunerLost when the capture produced no bytes at
        all; a capture stopped at its time limit returns the
        partial samples it did get (fix: partial capture is
        evidence, not failure).
        """
        raw = self._rtl_fm(freq_hz, mode, rate, seconds, gain=gain)
        n = len(raw) // 2
        return list(struct.unpack("<%dh" % n, raw[:2 * n]))

    def _iq(self, center_hz, rate, nsamp, seconds=40, gain="23"):
        with tempfile.NamedTemporaryFile(suffix=".iq",
                                         delete=False) as fh:
            path = fh.name
        try:
            size = _stream_to_file(
                ["rtl_sdr", "-f", str(int(center_hz)), "-s", str(rate),
                 "-n", str(nsamp), "-g", gain, path], path, seconds)
            if size == 0:
                raise TunerLost("rtl_sdr produced no samples")
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # --------------------------------------------------------------- scan
    def scan(self, center_hz, bw_hz, t0, t1):
        """Short rtl_sdr capture, FFT, peaks above the floor."""
        key = (center_hz, bw_hz)
        if key in self._scan_cache:
            return self._scan_cache[key]
        rate = 2400000
        raw = self._iq(center_hz, rate, 262144, seconds=30)
        samples = []
        for i in range(0, len(raw) - 1, 2):
            samples.append(complex((raw[i] - 127.5) / 127.5,
                                   (raw[i + 1] - 127.5) / 127.5))
        n = 2048
        accum = [0.0] * n
        chunks = 0
        for start in range(0, len(samples) - n, n * 4):
            spec = _fft(samples[start:start + n])
            for k in range(n):
                accum[k] += spec[k].real ** 2 + spec[k].imag ** 2
            chunks += 1
        if chunks == 0:
            raise TunerLost("rtl_sdr capture held no samples")
        power = [10 * math.log10(a / chunks + 1e-12) for a in accum]
        power = power[n // 2:] + power[:n // 2]
        floor = sorted(power)[len(power) // 2]
        emissions = []
        bin_hz = rate / n
        for k, p in enumerate(power):
            if p > floor + 18:
                freq = center_hz - rate / 2 + k * bin_hz
                emissions.append({
                    "freq_hz": freq, "bw_hz": bin_hz * 3,
                    "peak_dbm": round(p - 60, 1), "shape": "unknown",
                    "label": None})
        result = {"noise_floor_db": round(floor - 60, 1),
                  "emissions": emissions}
        self._scan_cache[key] = result
        return result

    # ------------------------------------------------------------ capture
    def capture(self, center_hz, bw_hz, t0, t1, vfos):
        self.errors = {}
        self._scan_cache = {}
        by_desk = {}
        for vfo in vfos:
            by_desk.setdefault(vfo["desk"], []).append(vfo)
        feeds = {}
        for desk, desk_vfos in by_desk.items():
            handler = getattr(self, "_feed_" + desk, None)
            if handler is None:
                self.errors[desk] = "the real source has no feed " \
                                    "for this desk"
                continue
            try:
                feed = handler(desk_vfos, center_hz, bw_hz, t0, t1)
            except TunerLost as exc:
                self.errors[desk] = str(exc)
                continue
            except Exception as exc:
                # A desk feed that fails any other way - a tool that
                # vanished mid-watch, a decode blowup - is that desk's
                # capture error, filed as a gap by the officer. It
                # never propagates and takes the dwell, or the watch,
                # down with it.
                self.errors[desk] = "%s: %s" % (type(exc).__name__, exc)
                continue
            if feed is not None:
                feeds[desk] = feed
        return feeds

    # -------------------------------------------------------- desk feeds
    def _feed_sweep(self, vfos, center_hz, bw_hz, t0, t1):
        return self.scan(center_hz, bw_hz, t0, t1)

    def _feed_adsb(self, vfos, center_hz, bw_hz, t0, t1):
        # ADS-B is a weak-signal pulse service: at the house gain of
        # 23 a rural indoor antenna reads the 1090 MHz band at the
        # idle floor, while maximum gain decodes airliners cleanly
        # (field-proven 2026-11-17: 31 CRC-valid frames in 30 s at
        # gain 49.6 against silence at 23). Capture ten seconds at
        # maximum gain so a dwell hears whole even/odd CPR pairs,
        # not a one-second slice.
        raw = self._iq(1090e6, 2000000, 20000000, seconds=60,
                       gain="49.6")
        return {"frames": demod_modes(raw)}

    def _feed_airband(self, vfos, center_hz, bw_hz, t0, t1):
        dwell = max(t1 - t0, 1.0)
        slice_s = min(12.0, max(2.0, dwell / max(len(vfos), 1)))
        segments = []
        channel_errors = []
        offset_base = 0.0
        for vfo in vfos:
            # Each channel is captured in isolation: a channel that
            # produces nothing records its own error and the dwell
            # keeps every other channel's segments.
            try:
                samples = self._audio(vfo["freq_hz"], "am", 12000,
                                      slice_s)
            except TunerLost as exc:
                channel_errors.append({"freq_hz": vfo["freq_hz"],
                                       "error": str(exc)})
            else:
                segments.extend(self._airband_segments(
                    samples, 12000, vfo["freq_hz"], offset_base))
            offset_base += slice_s
        if channel_errors and len(channel_errors) == len(vfos):
            raise TunerLost(channel_errors[0]["error"])
        feed = {"segments": segments}
        if channel_errors:
            feed["channel_errors"] = channel_errors
        return feed

    def _airband_segments(self, samples, rate, freq_hz, offset_base):
        """Envelope segmentation of AM audio, with per-segment SNR
        measured against the slice's own floor and the loud segments'
        audio carried in the feed as snippet evidence."""
        win = rate // 10
        levels = []
        for i in range(0, len(samples) - win, win):
            chunk = samples[i:i + win]
            levels.append(sum(abs(s) for s in chunk) / len(chunk))
        if not levels:
            return []
        ordered = sorted(levels)
        floor = ordered[len(ordered) // 4] or 1.0
        threshold = max(900.0, floor * 3)
        guard = self.profile["flash"]["guard_hz"]
        segments = []
        open_at = None
        peak = 0.0
        for idx, level in enumerate(levels):
            if level > threshold and open_at is None:
                open_at = idx
                peak = level
            elif level > threshold:
                peak = max(peak, level)
            elif level <= threshold and open_at is not None:
                segments.append(self._segment(
                    samples, rate, freq_hz, offset_base, open_at, idx,
                    peak, floor))
                open_at = None
        if open_at is not None:
            segments.append(self._segment(
                samples, rate, freq_hz, offset_base, open_at,
                len(levels), peak, floor))
        return segments

    def _segment(self, samples, rate, freq_hz, offset_base, open_idx,
                 close_idx, peak, floor):
        snr = round(20 * math.log10(max(peak, 1.0) / max(floor, 1.0)), 1)
        seg = {"freq_hz": float(freq_hz),
               "offset_s": offset_base + open_idx * 0.1,
               "dur_s": (close_idx - open_idx) * 0.1,
               "snr_db": min(snr, 45.0), "callsign": None}
        if seg["snr_db"] >= 18 or abs(freq_hz
                                      - self.profile["flash"]["guard_hz"]) < 1.0:
            lo = max(0, int((open_idx * 0.1 - 0.2) * rate))
            hi = min(len(samples), int((close_idx * 0.1 + 0.2) * rate))
            seg["audio_b64"] = _wav_b64(samples[lo:hi], rate)
        return seg

    def _feed_acars(self, vfos, center_hz, bw_hz, t0, t1):
        """ACARS on the real source: AM audio at 131.55 MHz, burst
        segmentation on the envelope, and the pure-Python ARINC
        618 decoder (multidex.acars). The capture is bounded at
        90 s of the datalink dwell; only CRC-verified frames are
        reported, as normalised ACARS text."""
        import numpy as np
        from . import acars as acars_mod
        freq = vfos[0]["freq_hz"] if vfos else 131.55e6
        seconds = min(90.0, max(10.0, t1 - t0))
        samples = np.asarray(
            self._audio(freq, "am", 12000, seconds), dtype=float)
        if len(samples) < 12000:
            return {"messages": []}
        env = np.abs(samples)
        k = 60  # 5 ms smoothing at 12 ksps
        env = np.convolve(env, np.ones(k) / k, mode="same")
        floor = np.median(env) + 1e-9
        active = env > floor * 4.0
        messages = []
        i = 0
        n = len(samples)
        while i < n:
            if active[i]:
                j = i
                while j < n and (active[j] or
                                 (j + 1200 < n and active[j + 1200])):
                    j += 1
                burst = samples[max(0, i - 600):min(n, j + 600)]
                if len(burst) > 2400:
                    messages.extend(
                        acars_mod.decode_audio(burst, 12000))
                i = j
            else:
                i += 1
        return {"messages": messages}

    def _feed_milair(self, vfos, center_hz, bw_hz, t0, t1):
        # UHF military airband is AM voice on the same terms as the
        # VHF airband desk: per-channel rtl_fm AM captures, envelope
        # segmentation, audio carried for the strong segments.
        return self._feed_airband(vfos, center_hz, bw_hz, t0, t1)

    def _feed_audiocensus(self, vfos, center_hz, bw_hz, t0, t1):
        """One bounded IQ slice of the window through the census
        engine: 5 s at the window rate, at most 6 extractions.
        The slice is a sample of the dwell and the desk says so;
        the census never claims the whole dwell was demodulated."""
        import numpy as np
        from . import audiocensus
        rate = 2400000
        seconds = 5.0
        raw = self._iq(center_hz, rate, int(rate * seconds),
                       seconds=int(seconds) + 30)
        n = len(raw) // 2
        u = np.frombuffer(raw[:2 * n], dtype=np.uint8).astype(
            np.float32)
        iq = ((u[0::2] - 127.5) / 127.5
              + 1j * ((u[1::2] - 127.5) / 127.5)).astype(np.complex64)
        return audiocensus.analyze(iq, float(rate), center_hz,
                                   extract_cap=6)

    def _feed_amateur(self, vfos, center_hz, bw_hz, t0, t1):
        """Amateur desk: CW decoded in pure Python from an IQ
        slice of the band's CW segment; FT8 bound to the host's
        jt9 program when present (its absence benches FT8 only,
        stated in the feed, never the whole desk). SSB voice on
        these windows is the audio census desk's catch."""
        import numpy as np
        from . import cw as cw_mod
        from .audiocensus import _extract
        from .toolbind import tool_available
        feed = {"cw": [], "ft8": [], "ft8_note": None}
        if center_hz < 10e6:
            cw_center, ft8_hz = 7030000.0, 7074000.0
        else:
            cw_center, ft8_hz = 14030000.0, 14074000.0
        rate = 240000
        raw = self._iq(cw_center, rate, rate * 6, seconds=40)
        n = len(raw) // 2
        u = np.frombuffer(raw[:2 * n], dtype=np.uint8).astype(
            np.float32)
        iq = ((u[0::2] - 127.5) / 127.5
              + 1j * ((u[1::2] - 127.5) / 127.5)).astype(np.complex64)
        chan, crate = _extract(iq, float(rate), 0.0, 40000.0)
        for sig in cw_mod.detect(chan, crate):
            sig = dict(sig)
            sig["freq_hz"] = cw_center + sig.pop("freq_offset_hz")
            feed["cw"].append(sig)
        if not tool_available("jt9"):
            feed["ft8_note"] = ("FT8 not bound: jt9 (WSJT-X) is "
                                "not on this host")
            return feed
        # FT8: 16 s of IQ over the watering hole, product-demod
        # to 12 kHz USB audio, jt9 does the LDPC decode.
        raw = self._iq(ft8_hz + 1500, rate, rate * 16, seconds=60)
        n = len(raw) // 2
        u = np.frombuffer(raw[:2 * n], dtype=np.uint8).astype(
            np.float32)
        iq = ((u[0::2] - 127.5) / 127.5
              + 1j * ((u[1::2] - 127.5) / 127.5)).astype(np.complex64)
        chan, crate = _extract(iq, float(rate), -1500.0, 4000.0)
        from .audiocensus import _demod
        audio = _demod(chan, crate, "USB")
        audio12 = np.interp(
            np.arange(int(len(audio) * 12000 / 16000)),
            np.arange(len(audio)) * (12000 / 16000), audio)
        feed["ft8"] = _run_jt8(audio12, ft8_hz)
        return feed

    def _feed_iss(self, vfos, center_hz, bw_hz, t0, t1):
        """ISS pass: FM audio on the downlink for the pass window
        (bounded), an SSTV decode attempt on the whole capture,
        and envelope voice segmentation. Most passes are silent;
        the feed says attempted either way."""
        import numpy as np
        from . import sstv as sstv_mod
        seconds = min(600.0, max(30.0, t1 - t0))
        samples = self._audio(145.8e6, "fm", 22050, seconds)
        feed = {"attempted": True, "segments": [], "sstv": None}
        if not samples:
            return feed
        dec = None
        try:
            dec = sstv_mod.decode_audio(
                np.asarray(samples, dtype=float) / 32768.0, 22050)
        except Exception:
            dec = None
        if dec:
            import base64 as _b64
            feed["sstv"] = {"mode": dec["mode"],
                            "lines": dec["lines"],
                            "height": dec["height"],
                            "png_b64": _b64.b64encode(
                                dec["png"]).decode("ascii")}
            return feed
        feed["segments"] = self._airband_segments(
            samples, 22050, 145.8e6, 0.0)
        return feed

    def _feed_vdl2(self, vfos, center_hz, bw_hz, t0, t1):
        """VDL2: bound to dumpvdl2 when the host carries it (IQ in,
        JSON out); otherwise a burst census on 136.975 MHz -
        burst counts only, no frame claims."""
        from .toolbind import tool_available
        import numpy as np
        if tool_available("dumpvdl2"):
            return {"messages": _run_dumpvdl2(self, t0, t1),
                    "bursts": []}
        rate = 2400000
        raw = self._iq(136.975e6, rate, rate * 3, seconds=30)
        n = len(raw) // 2
        u = np.frombuffer(raw[:2 * n], dtype=np.uint8).astype(
            np.float32)
        iq = (u[0::2] - 127.5) / 127.5 \
            + 1j * ((u[1::2] - 127.5) / 127.5)
        power = np.abs(iq) ** 2
        block = rate // 10
        blocks = [float(power[i:i + block].mean())
                  for i in range(0, len(power) - block, block)]
        if not blocks:
            return {"messages": [], "bursts": []}
        floor = float(np.median(blocks))
        bursts = sum(1 for b in blocks if b > floor * 6)
        return {"messages": [],
                "bursts": [{"count": bursts}] if bursts else []}

    def _feed_digitalvoice(self, vfos, center_hz, bw_hz, t0, t1):
        """Digital voice metadata via the bound DSD-FME program's
        own log. Voice is never decoded (see the desk)."""
        from .toolbind import tool_available
        if not tool_available("dsd-fme"):
            return {"calls": []}
        return {"calls": _run_dsd_fme(center_hz,
                                      min(60.0, max(10.0, t1 - t0)))}

    def _feed_hfdl(self, vfos, center_hz, bw_hz, t0, t1):
        """HFDL via the bound dumphfdl program, which drives the
        SDR itself through SoapySDR with direct sampling. The
        monitored channels are the system-table day and night
        sets' channels inside this dwell's window - every one of
        them also inside the program's capture span at its
        3.2 Msps rate - and the listen is a bounded slice of the
        dwell, not the whole dwell."""
        from .toolbind import tool_available
        if not tool_available("dumphfdl"):
            return {"messages": []}
        from .desks import HFDL_DAY_CHANNELS, HFDL_NIGHT_CHANNELS
        lo, hi = center_hz - bw_hz / 2, center_hz + bw_hz / 2
        channels = sorted(
            f / 1000.0
            for f in HFDL_DAY_CHANNELS + HFDL_NIGHT_CHANNELS
            if lo <= f <= hi)
        if not channels:
            return {"messages": []}
        return {"messages": _run_dumphfdl(
            center_hz, channels, min(120.0, max(10.0, t1 - t0)))}

    def _feed_ais(self, vfos, center_hz, bw_hz, t0, t1):
        sentences = []
        channel_errors = []
        for vfo in vfos:
            # Per-channel isolation, as on the airband desk: one
            # dead channel must not discard the other's sentences.
            try:
                samples = self._audio(vfo["freq_hz"], "fm", 48000, 10.0)
            except TunerLost as exc:
                channel_errors.append({"freq_hz": vfo["freq_hz"],
                                       "error": str(exc)})
                continue
            sentences.extend(demod_ais(samples, 48000))
        if channel_errors and len(channel_errors) == len(vfos):
            raise TunerLost(channel_errors[0]["error"])
        feed = {"sentences": sentences}
        if channel_errors:
            feed["channel_errors"] = channel_errors
        return feed

    def _feed_wxradio(self, vfos, center_hz, bw_hz, t0, t1):
        freq = vfos[0]["freq_hz"]
        samples = self._audio(freq, "fm", 22050, 20.0)
        rms = math.sqrt(sum(s * s for s in samples)
                        / max(len(samples), 1))
        feed = {"present": rms > 300.0, "same": [], "freq_hz": freq}
        if shutil.which("multimon-ng") is None:
            # SAME decode is unavailable on this host, but the
            # broadcast presence above is real evidence and the
            # dwell heard it: the limitation rides in the feed as a
            # note, never as a capture error beside the feed - a
            # dwell cannot be both failed and heard. A gap files
            # only when the audio capture itself fails.
            feed["same_note"] = (
                "multimon-ng is not installed; SAME headers cannot "
                "be decoded on this host (broadcast presence is "
                "still reported)")
            return feed
        headers = []
        with tempfile.NamedTemporaryFile(suffix=".wav",
                                         delete=False) as fh:
            path = fh.name
        try:
            _write_wav(path, samples, 22050)
            out = _run_tool(["multimon-ng", "-a", "EAS", "-t", "wav",
                             path], timeout=60)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        for line in out.splitlines():
            at = line.find("ZCZC")
            if at < 0:
                continue
            # The whole remainder of the line: the station field of
            # a SAME header may itself contain spaces.
            token = line[at:].strip()
            if codecs.same_parse(token) is None \
                    and not token.endswith("-"):
                token += "-"
            if codecs.same_parse(token) is not None:
                headers.append(token)
        feed["same"] = headers
        return feed

    def _multimon(self, freq_hz, decoders, seconds=15.0):
        """FM audio at 22050 Hz through multimon-ng; returns stdout."""
        if shutil.which("multimon-ng") is None:
            raise TunerLost("multimon-ng is not installed on this host")
        samples = self._audio(freq_hz, "fm", 22050, seconds)
        with tempfile.NamedTemporaryFile(suffix=".wav",
                                         delete=False) as fh:
            path = fh.name
        try:
            _write_wav(path, samples, 22050)
            cmd = ["multimon-ng"]
            for dec in decoders:
                cmd += ["-a", dec]
            cmd += ["-t", "wav", path]
            return _run_tool(cmd, timeout=60)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _feed_pager(self, vfos, center_hz, bw_hz, t0, t1):
        out = self._multimon(vfos[0]["freq_hz"],
                             ["POCSAG512", "POCSAG1200", "POCSAG2400"])
        messages = []
        for line in out.splitlines():
            m = _PAGER_RE.search(line)
            if m:
                messages.append({
                    "address": int(m.group(1)),
                    "function": int(m.group(2)),
                    "message": m.group(3).strip(),
                    "decoder": "multimon-ng"})
        return {"messages": messages}

    def _feed_aprs(self, vfos, center_hz, bw_hz, t0, t1):
        out = self._multimon(vfos[0]["freq_hz"], ["AFSK1200"])
        packets = []
        for line in out.splitlines():
            m = _APRS_RE.search(line)
            if m:
                packet = re.sub(r"\s*>\s*", ">", line[m.start():])
                packets.append(packet.strip())
        return {"packets": packets}

    def _feed_ism(self, vfos, center_hz, bw_hz, t0, t1):
        if shutil.which("rtl_433") is None:
            raise TunerLost("rtl_433 is not installed on this host")
        raw = self._iq(center_hz, 250000, 1000000, seconds=30)
        with tempfile.NamedTemporaryFile(suffix=".cu8",
                                         delete=False) as fh:
            fh.write(raw)
            path = fh.name
        try:
            out = _run_tool(["rtl_433", "-r", path, "-F", "json"],
                            timeout=90)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        skip = {"time", "model", "id", "rssi", "snr", "noise", "freq",
                "channel", "mod", "type"}
        bursts = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            model = ev.get("model", "unknown")
            device = "%s-%s" % (model, ev.get("id", "0"))
            bursts.append({
                "device": device, "model": model,
                "freq_hz": float(ev.get("freq", center_hz)),
                "data": {k: v for k, v in ev.items() if k not in skip},
                "rssi_dbm": float(ev.get("rssi", -80.0))})
        return {"bursts": bursts}

    def _feed_satellite(self, vfos, center_hz, bw_hz, t0, t1):
        vfo = vfos[0]
        p = vfo.get("pass") or {}
        scan = self.scan(center_hz, bw_hz, t0, t1)
        floor = scan["noise_floor_db"]
        best = None
        for em in scan["emissions"]:
            if abs(em["freq_hz"] - vfo["freq_hz"]) <= 150e3:
                snr = em["peak_dbm"] - floor
                if best is None or snr > best:
                    best = snr
        lock = best is not None and best >= 8.0
        feed = {"lock": lock,
                "quality": round(min(1.0, max(0.0, (best or 0.0) / 30.0)), 2),
                "sat": p.get("sat"), "norad": p.get("norad")}
        if lock:
            # Record the whole remaining pass at maximum gain. APT
            # imagery decodes from a continuous full-pass capture;
            # the 8-second sample this feed used to take could never
            # hold a picture. Maximum gain for the reason the ADS-B
            # capture established in 0.1.3: this band, at a rural
            # indoor antenna, reads near the idle floor at the house
            # gain. The recording runs to the pass end or the dwell
            # end, whichever comes first, and the stored copy is
            # resampled to 11025 Hz.
            end = min(t1, p.get("los") or t1)
            now = time.time()
            start = now if t0 <= now <= end else t0
            seconds = max(0.0, end - start)
            raw = self._rtl_fm(vfo["freq_hz"], "fm", 48000, seconds,
                               gain="49.6")
            samples = array.array("h")
            samples.frombytes(raw[:len(raw) - len(raw) % 2])
            stored = _resample_int16(samples, 48000, 11025)
            feed["audio_b64"] = _wav_b64(stored, 11025)
            feed["audio_name"] = "pass-%s-%d.wav" % (p.get("norad"),
                                                     int(t0))
            feed["audio_rate"] = 11025
        return feed

    def _feed_sonde(self, vfos, center_hz, bw_hz, t0, t1):
        scan = self.scan(center_hz, bw_hz, t0, t1)
        carriers = [dict(freq_hz=em["freq_hz"],
                         peak_dbm=em["peak_dbm"],
                         noise_floor_db=scan["noise_floor_db"])
                    for em in scan["emissions"]]
        return {"carriers": carriers}

    def _feed_broadcast(self, vfos, center_hz, bw_hz, t0, t1):
        scan = self.scan(center_hz, bw_hz, t0, t1)
        floor = scan["noise_floor_db"]
        stations = [{"freq_hz": em["freq_hz"], "rds": None,
                     "peak_dbm": em["peak_dbm"]}
                    for em in scan["emissions"]
                    if em["peak_dbm"] >= floor + 10]
        return {"stations": stations}

    def _feed_cellular(self, vfos, center_hz, bw_hz, t0, t1):
        scan = self.scan(center_hz, bw_hz, t0, t1)
        cells = []
        for em in scan["emissions"]:
            for lo, hi, band in _DOWNLINK_BANDS:
                if lo <= em["freq_hz"] <= hi:
                    cells.append({
                        "cell_id": "CELL-%.1fM" % (em["freq_hz"] / 1e6),
                        "band": band, "freq_hz": em["freq_hz"],
                        "rsrp_dbm": em["peak_dbm"], "grade": "PROBABLE"})
                    break
        return {"cells": cells}

    def _feed_trunking(self, vfos, center_hz, bw_hz, t0, t1):
        scan = self.scan(center_hz, bw_hz, t0, t1)
        floor = scan["noise_floor_db"]
        systems = [{"sysid": "CC-%.4fM" % (em["freq_hz"] / 1e6),
                    "freq_hz": em["freq_hz"], "activity_only": True,
                    "peak_dbm": em["peak_dbm"]}
                   for em in scan["emissions"]
                   if em["peak_dbm"] >= floor + 12]
        return {"systems": systems}


class TunerServer:
    """The only code that talks to a source. Enforces outage windows."""

    def __init__(self, source, profile):
        self.source = source
        self.outages = [(float(a), float(b)) for a, b in
                        profile["advanced"].get("tuner_outages", [])]
        self.watch_start = None
        self.current = None  # (center_hz, bw_hz)

    def begin_watch(self, watch_start):
        self.watch_start = watch_start

    def outage_at(self, t):
        if self.watch_start is None:
            return False
        offset = t - self.watch_start
        return any(start <= offset < start + dur
                   for start, dur in self.outages)

    def tune(self, center_hz, bw_hz):
        self.current = (center_hz, bw_hz)

    def scan(self, center_hz, bw_hz, t0, t1):
        if self.outage_at(t0):
            raise TunerLost("tuner unavailable (configured outage)")
        return self.source.scan(center_hz, bw_hz, t0, t1)

    def capture(self, center_hz, bw_hz, t0, t1, vfos):
        if self.outage_at(t0):
            raise TunerLost("tuner unavailable (configured outage)")
        self.tune(center_hz, bw_hz)
        return self.source.capture(center_hz, bw_hz, t0, t1, vfos)

    def capture_errors(self):
        """Per-desk capture failures from the last capture, if the
        source keeps an error map (the real source does)."""
        return getattr(self.source, "errors", {}) or {}
