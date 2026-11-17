"""The Tuner Server: sole owner of the receiver (design section 5).

Two sources sit behind it. The simulated source (multidex.sim) is a
deterministic synthetic spectrum that runs the whole system with no
hardware. The real source shells to rtl_sdr and rtl_fm when they are
present and benches itself honestly when they are not; on real
hardware it honestly feeds only what pure Python can carry - spectrum
sweeps by FFT over short captures, and airband activity by envelope
over rtl_fm audio - and the remaining desks bench with that reason
stated at muster.

Configured tuner outages (a rehearsal knob) are enforced here, so the
failure behavior of section 13 is exercised the same way on any source.
"""

import math
import os
import shutil
import struct
import subprocess
import tempfile


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


class RealSource:
    kind = "real"

    def __init__(self, profile):
        self.profile = profile

    def available(self):
        missing = [tool for tool in ("rtl_sdr", "rtl_fm")
                   if shutil.which(tool) is None]
        if missing:
            return False, ("real source needs %s on the PATH; not found"
                           % " and ".join(missing))
        return True, "rtl_sdr and rtl_fm present"

    def capabilities(self):
        return {"sweep", "airband"}

    def scan(self, center_hz, bw_hz, t0, t1):
        """Short rtl_sdr capture, FFT, peaks above the floor."""
        rate = 2400000
        nsamp = 262144
        with tempfile.NamedTemporaryFile(suffix=".iq", delete=False) as fh:
            path = fh.name
        try:
            subprocess.run(
                ["rtl_sdr", "-f", str(int(center_hz)), "-s", str(rate),
                 "-n", str(nsamp), "-g", "23", path],
                check=True, timeout=30,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(path, "rb") as fh:
                raw = fh.read()
        except (subprocess.SubprocessError, OSError) as exc:
            raise TunerLost("rtl_sdr capture failed: %s" % exc)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
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
        return {"noise_floor_db": round(floor - 60, 1),
                "emissions": emissions}

    def capture(self, center_hz, bw_hz, t0, t1, vfos):
        feeds = {}
        for vfo in vfos:
            desk = vfo["desk"]
            if desk == "sweep":
                feeds["sweep"] = self.scan(center_hz, bw_hz, t0, t1)
            elif desk == "airband":
                feeds["airband"] = {"segments": self._airband_segments(
                    vfo["freq_hz"], t1 - t0)}
        return feeds

    def _airband_segments(self, freq_hz, duration_s):
        """rtl_fm AM demod, then envelope segmentation of the audio."""
        duration_s = min(duration_s, 30.0)  # probe slice, not the dwell
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as fh:
            path = fh.name
        try:
            subprocess.run(
                ["rtl_fm", "-f", str(int(freq_hz)), "-M", "am",
                 "-s", "12000", "-g", "23", "-l", "12", path],
                check=True, timeout=duration_s + 20,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(path, "rb") as fh:
                raw = fh.read()
        except (subprocess.SubprocessError, OSError) as exc:
            raise TunerLost("rtl_fm capture failed: %s" % exc)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        samples = struct.unpack("<%dh" % (len(raw) // 2),
                                raw[:len(raw) // 2 * 2])
        segments = []
        rate = 12000
        win = rate // 10
        open_at = None
        for i in range(0, len(samples) - win, win):
            chunk = samples[i:i + win]
            level = sum(abs(s) for s in chunk) / len(chunk)
            if level > 900 and open_at is None:
                open_at = i / rate
            elif level <= 900 and open_at is not None:
                segments.append({"freq_hz": float(freq_hz),
                                 "offset_s": open_at,
                                 "dur_s": i / rate - open_at,
                                 "snr_db": 12.0, "callsign": None})
                open_at = None
        return segments


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
