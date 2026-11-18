"""Morse (CW) detection and decode, pure Python + numpy (0.2.0).

Method: averaged PSD finds tone peaks in the CW sub-band; per
peak, a 5 ms-hop spectrogram column gives the keying envelope;
an adaptive threshold and duration clustering separate dits,
dahs, letter gaps and word gaps; characters that do not resolve
are reported as '?' rather than guessed. Clean machine-sent CW
decodes well; a weak fist, drift and pileups degrade honestly -
the confidence field says how much of the element stream
classified cleanly.
"""

import numpy as np

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1",
    "..---": "2", "...--": "3", "....-": "4", ".....": "5",
    "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    "-.-.-": ".", "--..--": ",", "..--..": "?", "-....-": "-",
    "-..-.": "/", "-...-": "=", ".--.-.": "@",
}
REVERSE = {v: k for k, v in MORSE.items()}


def encode_iq(text, wpm=18, fs=48000.0, offset_hz=3000.0,
              seconds_pad=0.3):
    """Keyed-carrier IQ fixture for a text string."""
    dit = 1.2 / wpm
    elements = []
    for word in text.upper().split(" "):
        for ch in word:
            code = REVERSE.get(ch)
            if code is None:
                continue
            for sym in code:
                elements.append(dit * (1 if sym == "." else 3))
                elements.append(dit)          # intra-character gap
            elements[-1] = dit * 3            # letter gap
        elements[-1] = dit * 7                # word gap
    total = sum(elements) + 2 * seconds_pad
    n = int(total * fs)
    t = np.arange(n) / fs
    env = np.zeros(n)
    pos = seconds_pad
    on = True
    for dur in elements:
        i0, i1 = int(pos * fs), int((pos + dur) * fs)
        if on:
            env[i0:i1] = 1.0
        on = not on
        pos += dur
    return env * np.exp(2j * np.pi * offset_hz * t)


def _envelope(iq, fs, freq_offset, hop_s=0.005):
    """Tone power over time at one offset: a direct DFT
    (Goertzel) at the exact frequency over each 10 ms window.
    An FFT bin near the tone ripples with the window's phase as
    it slides; the exact-frequency correlation does not."""
    win_n = int(fs * 0.010)
    hop = int(fs * hop_s)
    idx = np.arange(win_n)
    ref = np.exp(-2j * np.pi * freq_offset * idx / fs)
    win = np.hanning(win_n)
    cols = []
    for start in range(0, len(iq) - win_n + 1, hop):
        seg = iq[start:start + win_n] * win
        cols.append(abs(seg @ ref) ** 2)
    return np.array(cols), hop_s


def _decode_envelope(env, hop_s):
    """Keying envelope to (text, wpm, confidence)."""
    if len(env) < 20:
        return None
    # Smooth over ~15 ms: a single-bin power column ripples at
    # the tone's leakage beat, and unsmoothed thresholding
    # fragments marks into spurious dits.
    k = max(1, int(0.015 / hop_s))
    if k > 1:
        env = np.convolve(env, np.ones(k) / k, mode="same")
    floor = np.median(env)
    peak = np.percentile(env, 99)
    if peak <= floor * 4:
        return None
    thresh = floor + (peak - floor) * 0.35
    on = env > thresh
    runs = []
    cur = on[0]
    count = 1
    for v in on[1:]:
        if v == cur:
            count += 1
        else:
            runs.append((bool(cur), count * hop_s))
            cur, count = v, 1
    runs.append((bool(cur), count * hop_s))
    # Bridge dropouts: an OFF run under 25 ms inside a mark is
    # ripple, not a gap. Drop ON runs under 15 ms as spikes.
    bridged = []
    for is_on, d in runs:
        if not is_on and d < 0.025 and bridged and bridged[-1][0]:
            bridged[-1] = (True, bridged[-1][1] + d)
        elif bridged and is_on and not bridged[-1][0] \
                and bridged[-1][1] < 0.025 and len(bridged) > 1:
            prev = bridged.pop()
            bridged[-1] = (True, bridged[-1][1] + prev[1] + d)
        else:
            bridged.append((is_on, d))
    runs = [(o, d) for o, d in bridged if d >= 0.015 or not o]
    marks = [d for is_on, d in runs if is_on]
    if len(marks) < 3:
        return None
    dit = float(np.percentile(marks, 25))
    if dit <= 0:
        return None
    # Re-cluster: the shortest marks are dits; set the unit from
    # the lower cluster so a dah-heavy text still calibrates.
    shorts = [m for m in marks if m < dit * 1.6]
    if shorts:
        dit = float(np.mean(shorts))
    wpm = 1.2 / dit
    out = []
    code = ""
    clean = 0
    total_el = 0
    for is_on, d in runs:
        if is_on:
            total_el += 1
            if d < dit * 2.0:
                code += "."
                clean += 1
            elif d < dit * 4.5:
                code += "-"
                clean += 1
            else:
                code += "-"
        else:
            if d >= dit * 5.0:
                if code:
                    out.append(MORSE.get(code, "?"))
                    code = ""
                out.append(" ")
            elif d >= dit * 2.0:
                if code:
                    out.append(MORSE.get(code, "?"))
                    code = ""
    if code:
        out.append(MORSE.get(code, "?"))
    text = "".join(out).strip()
    if len(text) < 2:
        return None
    return {"text": text, "wpm": round(wpm, 1),
            "confidence": round(clean / max(1, total_el), 2)}


def detect(iq, fs, max_signals=3):
    """All decodable CW signals in a baseband IQ slice.
    Returns [{freq_offset_hz, text, wpm, confidence, snr_db}]."""
    iq = np.asarray(iq)
    if len(iq) < fs * 0.5:
        return []
    nperseg = 4096 if fs >= 200000 else 1024
    win = np.hanning(nperseg)
    acc = None
    step = nperseg * 2
    for start in range(0, len(iq) - nperseg + 1, step):
        spec = np.fft.fftshift(np.fft.fft(iq[start:start + nperseg]
                                          * win))
        p = spec.real ** 2 + spec.imag ** 2
        acc = p if acc is None else acc + p
    if acc is None:
        return []
    psd_db = 10 * np.log10(acc + 1e-20)
    freqs = np.fft.fftshift(np.fft.fftfreq(nperseg, 1.0 / fs))
    noise = float(np.median(psd_db))
    peaks = []
    for i in range(2, len(psd_db) - 2):
        if psd_db[i] > noise + 10 and psd_db[i] >= psd_db[i - 1] \
                and psd_db[i] >= psd_db[i + 1]:
            if not peaks or abs(freqs[i] - peaks[-1][0]) > 300:
                peaks.append((float(freqs[i]),
                              float(psd_db[i] - noise)))
    peaks.sort(key=lambda p: -p[1])
    out = []
    for offset, snr in peaks[:max_signals]:
        env, hop_s = _envelope(iq, fs, offset)
        dec = _decode_envelope(env, hop_s)
        if dec:
            dec.update({"freq_offset_hz": offset,
                        "snr_db": round(snr, 1)})
            out.append(dec)
    return out
