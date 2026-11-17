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
  frequency, with the pass audio filed as evidence.

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
        return True, detail

    def capabilities(self):
        return {"sweep", "adsb", "airband", "ais", "wxradio", "ism",
                "aprs", "pager", "satellite", "sonde", "broadcast",
                "cellular", "trunking"}

    # -------------------------------------------------------- raw capture
    def _rtl_fm(self, freq_hz, mode, rate, seconds, squelch=0):
        with tempfile.NamedTemporaryFile(suffix=".raw",
                                         delete=False) as fh:
            path = fh.name
        try:
            size = _stream_to_file(
                ["rtl_fm", "-f", str(int(freq_hz)), "-M", mode,
                 "-s", str(rate), "-g", "23", "-l", str(squelch), path],
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

    def _audio(self, freq_hz, mode, rate, seconds):
        """One rtl_fm capture as a list of int16 samples.

        Raises TunerLost when the capture produced no bytes at
        all; a capture stopped at its time limit returns the
        partial samples it did get (fix: partial capture is
        evidence, not failure).
        """
        raw = self._rtl_fm(freq_hz, mode, rate, seconds)
        n = len(raw) // 2
        return list(struct.unpack("<%dh" % n, raw[:2 * n]))

    def _iq(self, center_hz, rate, nsamp, seconds=40):
        with tempfile.NamedTemporaryFile(suffix=".iq",
                                         delete=False) as fh:
            path = fh.name
        try:
            size = _stream_to_file(
                ["rtl_sdr", "-f", str(int(center_hz)), "-s", str(rate),
                 "-n", str(nsamp), "-g", "23", path], path, seconds)
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
        raw = self._iq(1090e6, 2000000, 2000000, seconds=40)
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
            samples = self._audio(vfo["freq_hz"], "fm", 48000, 8.0)
            feed["audio_b64"] = _wav_b64(samples, 48000)
            feed["audio_name"] = "pass-%s-%d.wav" % (p.get("norad"),
                                                     int(t0))
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
