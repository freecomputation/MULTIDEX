"""Slow-scan television decode: VIS detection, Robot 36, PD120,
Martin M1 and M2, pure Python + numpy (0.2.0).

The decoder works from FM-demodulated audio: an instantaneous
frequency track, sync-pulse anchoring per line, per-mode channel
timings, YCbCr or RGB assembly, and a PNG written with the
standard library only (zlib + struct). The encoder exists for
fixtures and for the simulated source, and synthesises a mode's
tone audio from an RGB image.

Honesty: a decode reports the VIS mode it actually detected and
the number of complete lines it actually recovered. A partial
image is filed as partial, never padded into a claimed full
frame.
"""

import struct
import zlib

import numpy as np

BLACK_HZ = 1500.0
WHITE_HZ = 2300.0
SYNC_HZ = 1200.0

VIS_CODES = {8: "Robot 36", 12: "Robot 72", 44: "Martin M1",
             40: "Martin M2", 95: "PD120", 96: "PD180",
             94: "PD290", 60: "Scottie 1"}
SUPPORTED = ("Robot 36", "PD120", "Martin M1", "Martin M2")

# mode -> (width, height, seconds per image line, layout)
MODES = {
    "Robot 36": (320, 240, 0.150),
    "PD120": (640, 496, 0.50848),     # per line PAIR; halved below
    "Martin M1": (320, 256, 0.446694),
    "Martin M2": (320, 256, 0.226594),
}


# ------------------------------------------------------------ PNG

def png_bytes(rgb):
    """rgb: (h, w, 3) uint8 array -> PNG file bytes."""
    h, w, _ = rgb.shape
    raw = b""
    for y in range(h):
        raw += b"\x00" + rgb[y].astype(np.uint8).tobytes()

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data)
                               & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


# ------------------------------------------------------- tone track

def _tone_track(audio, rate):
    """Instantaneous frequency per sample, 1 ms median smoothed."""
    n = len(audio)
    spec = np.fft.fft(audio)
    freqs = np.fft.fftfreq(n, 1.0 / rate)
    spec = np.where(freqs < 0, 0.0, spec) * 2
    analytic = np.fft.ifft(spec)
    phase = np.unwrap(np.angle(analytic))
    inst = np.diff(phase) * rate / (2 * np.pi)
    inst = np.concatenate([inst, inst[-1:]])
    k = max(1, int(rate * 0.001))
    if k > 1:
        inst = np.convolve(inst, np.ones(k) / k, mode="same")
    return inst


def _value(freq):
    v = (freq - BLACK_HZ) / (WHITE_HZ - BLACK_HZ) * 255.0
    return float(np.clip(v, 0, 255))


def detect_vis(track, rate):
    """Find the VIS header. Returns (mode_name, data_start_sample)
    or (None, None)."""
    # Leader: ~1900 Hz sustained, then a 1200 Hz break, then the
    # 30 ms bit cells.
    step = int(rate * 0.005)
    means = [float(np.mean(track[i:i + step]))
             for i in range(0, len(track) - step, step)]
    for idx, m in enumerate(means):
        if abs(m - 1900) < 120:
            # Confirm at least 150 ms of leader.
            run = 0
            j = idx
            while j < len(means) and abs(means[j] - 1900) < 150:
                run += 1
                j += 1
            if run * 0.005 < 0.15:
                continue
            start = j * step
            # Skip the break and the start bit (30 ms each at
            # 1200 Hz), then read 7 bits + parity at 30 ms cells.
            pos = start + int(rate * 0.010)
            bitpos = pos + int(rate * 0.030)
            code = 0
            ok = True
            for b in range(7):
                cell = track[bitpos + int(rate * 0.008):
                             bitpos + int(rate * 0.022)]
                if len(cell) == 0:
                    ok = False
                    break
                f = float(np.mean(cell))
                if abs(f - 1100) < 130:
                    code |= (1 << b)
                elif abs(f - 1300) < 130:
                    pass
                else:
                    ok = False
                    break
                bitpos += int(rate * 0.030)
            if not ok:
                continue
            mode = VIS_CODES.get(code)
            if mode in SUPPORTED:
                data_start = bitpos + int(rate * 0.060)
                return mode, data_start
            return None, None
    return None, None


def _find_sync(track, rate, start, min_ms=4.0):
    """Next sustained 1200 Hz run at or after start. A search
    that opens inside a run measures the whole run (extending
    backwards), so a caller landing a few samples into a sync
    does not discard it and skip a line."""
    step = max(1, int(rate * 0.001))
    i = start
    need = int(rate * min_ms / 1000.0)
    while i < len(track) - need:
        if track[i] < 1350:
            j = i
            while j < len(track) and track[j] < 1350:
                j += 1
            run_start = i
            if i == start:
                k = i
                while k > 0 and track[k - 1] < 1350:
                    k -= 1
                run_start = k
            if j - run_start >= need:
                return run_start, j
            i = j
        else:
            i += step
    return None, None


def _sample_channel(track, rate, start_s, dur_s, pixels):
    if dur_s <= 0:
        return np.full(pixels, 128.0)
    centers = (start_s + (np.arange(pixels) + 0.5)
               * dur_s / pixels) * rate
    idx = np.clip(centers.astype(int), 2, len(track) - 3)
    # 5-sample mean around each pixel centre via a cumsum.
    cs = np.concatenate([[0.0], np.cumsum(track)])
    vals = (cs[idx + 3] - cs[idx - 2]) / 5.0
    return np.clip((vals - BLACK_HZ) / (WHITE_HZ - BLACK_HZ)
                   * 255.0, 0, 255)


def _ycbcr_to_rgb(y, cr, cb):
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255)


def decode_audio(audio, rate=12000):
    """Decode one SSTV transmission from FM audio.
    Returns {mode, width, height, lines, png} or None."""
    audio = np.asarray(audio, dtype=float)
    if len(audio) < rate * 5:
        return None
    track = _tone_track(audio, rate)
    mode, start = detect_vis(track, rate)
    if mode is None:
        return None
    w, h, _line_s = MODES[mode]
    img = np.zeros((h, w, 3))
    lines_done = 0
    pos = start
    if mode == "Robot 36":
        cr_line = np.full(w, 128.0)
        cb_line = np.full(w, 128.0)
        for line in range(h):
            s0, s1 = _find_sync(track, rate, pos, 6.0)
            if s0 is None:
                break
            y_start = (s1 + int(rate * 0.003)) / rate
            y = _sample_channel(track, rate, y_start, 0.088, w)
            c_start = y_start + 0.088 + 0.0045
            chroma = _sample_channel(track, rate, c_start, 0.044, w)
            if line % 2 == 0:
                cr_line = chroma
            else:
                cb_line = chroma
            img[line] = _ycbcr_to_rgb(y, cr_line, cb_line)
            lines_done += 1
            pos = int((c_start + 0.044) * rate)
    elif mode == "PD120":
        for pair in range(h // 2):
            s0, s1 = _find_sync(track, rate, pos, 12.0)
            if s0 is None:
                break
            base = (s1 + int(rate * 0.00208)) / rate
            y0 = _sample_channel(track, rate, base, 0.1216, w)
            cr = _sample_channel(track, rate, base + 0.1216,
                                 0.1216, w)
            cb = _sample_channel(track, rate, base + 0.2432,
                                 0.1216, w)
            y1 = _sample_channel(track, rate, base + 0.3648,
                                 0.1216, w)
            img[2 * pair] = _ycbcr_to_rgb(y0, cr, cb)
            img[2 * pair + 1] = _ycbcr_to_rgb(y1, cr, cb)
            lines_done += 2
            pos = int((base + 0.4864) * rate)
    else:  # Martin M1 / M2
        scan = 0.146432 if mode == "Martin M1" else 0.073216
        for line in range(h):
            s0, s1 = _find_sync(track, rate, pos, 3.5)
            if s0 is None:
                break
            base = s1 / rate
            g = _sample_channel(track, rate, base + 0.000572,
                                scan, w)
            b = _sample_channel(track, rate,
                                base + 0.001144 + scan, scan, w)
            r = _sample_channel(track, rate,
                                base + 0.001716 + 2 * scan, scan, w)
            img[line] = np.stack([r, g, b], axis=-1)
            lines_done += 1
            pos = int((base + 0.001716 + 3 * scan) * rate)
    if lines_done < 8:
        return None
    return {"mode": mode, "width": w, "height": h,
            "lines": lines_done,
            "png": png_bytes(img.astype(np.uint8))}


# ---------------------------------------------------------- encoder

class _ToneGen:
    """Phase-continuous tone writer with cumulative rounding:
    segment lengths are rounded against the running total, so a
    scan of 320 three-sample pixels is exactly its nominal
    duration, not 10% short from per-pixel truncation."""

    def __init__(self, rate):
        self.rate = rate
        self.phase = 0.0
        self.cursor = 0.0     # exact seconds written
        self.written = 0      # samples written
        self.parts = []

    def add(self, freq, seconds):
        self.cursor += seconds
        n = int(round(self.cursor * self.rate)) - self.written
        if n <= 0:
            return
        t = np.arange(n) / self.rate
        self.parts.append(
            np.sin(self.phase + 2 * np.pi * freq * t))
        self.phase += 2 * np.pi * freq * n / self.rate
        self.written += n

    def audio(self):
        return np.concatenate(self.parts) if self.parts \
            else np.zeros(0)


def _px_freq(v):
    return BLACK_HZ + (np.asarray(v, dtype=float) / 255.0) \
        * (WHITE_HZ - BLACK_HZ)


def encode_audio(rgb, mode="Robot 36", rate=12000):
    """Synthesise SSTV tone audio for an (h, w, 3) uint8 image."""
    rgb = np.asarray(rgb, dtype=float)
    h, w, _ = rgb.shape
    code = {v: k for k, v in VIS_CODES.items()}[mode]
    gen = _ToneGen(rate)
    add = gen.add

    def add_scan(values, seconds):
        # Vectorised: one phase-continuous pass over the scan,
        # pixel boundaries by cumulative rounding (the same
        # discipline as _ToneGen, at scan granularity).
        freqs = _px_freq(np.asarray(values, dtype=float))
        total = int(round(seconds * rate))
        bounds = np.linspace(0, total, len(freqs) + 1).astype(int)
        counts = np.diff(bounds)
        sample_freq = np.repeat(freqs, counts)
        phase = gen.phase + 2 * np.pi * np.concatenate(
            [[0.0], np.cumsum(sample_freq)])[:-1] / rate
        gen.parts.append(np.sin(phase))
        gen.phase = gen.phase + 2 * np.pi * float(
            sample_freq.sum()) / rate
        gen.cursor += seconds
        gen.written += total

    add(1900.0, 0.300)
    add(1200.0, 0.010)
    add(1200.0, 0.030)                       # start bit
    parity = 0
    for b in range(7):
        bit = (code >> b) & 1
        parity ^= bit
        add(1100.0 if bit else 1300.0, 0.030)
    add(1100.0 if parity else 1300.0, 0.030)  # parity bit
    add(1200.0, 0.030)                        # stop bit
    y = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] \
        + 0.114 * rgb[:, :, 2]
    cr = (rgb[:, :, 0] - y) / 1.402 + 128.0
    cb = (rgb[:, :, 2] - y) / 1.772 + 128.0
    if mode == "Robot 36":
        for line in range(h):
            add(1200.0, 0.009)
            add(1500.0, 0.003)
            add_scan(y[line], 0.088)
            add(1500.0, 0.0045)
            add_scan(cr[line] if line % 2 == 0 else cb[line], 0.044)
    elif mode == "PD120":
        for pair in range(h // 2):
            add(1200.0, 0.020)
            add(1500.0, 0.00208)
            add_scan(y[2 * pair], 0.1216)
            add_scan(cr[2 * pair], 0.1216)
            add_scan(cb[2 * pair], 0.1216)
            add_scan(y[2 * pair + 1], 0.1216)
    else:
        scan = 0.146432 if mode == "Martin M1" else 0.073216
        for line in range(h):
            add(1200.0, 0.004862)
            add(1500.0, 0.000572)
            add_scan(rgb[line, :, 1], scan)
            add(1500.0, 0.000572)
            add_scan(rgb[line, :, 2], scan)
            add(1500.0, 0.000572)
            add_scan(rgb[line, :, 0], scan)
    return gen.audio()
