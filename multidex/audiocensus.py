"""Universal audio emissions census (0.2.0 flagship).

Survey-then-extract channelization over a captured IQ window:
Welch PSD, median-referenced detection with hysteresis edges,
per-candidate digital down-conversion, modulation classification
from signal features, demodulation, and a voice-likelihood grade
built from hard gates plus a 100-point rubric. The grader is
built so plain static cannot pass it: band concentration in the
300-3400 Hz voice band is the static killer, and the negative
controls (static, squelch bursts, music, data) are test fixtures.

Honesty contract: an emission no desk can decode is reported as
unknown audio with its estimated modulation and grade. Nothing
here invents speech; a grade is a machine estimate, labelled as
such in every product. DATA-class emissions are filed as data,
never graded as voice. Since 0.11.0 a steady tone is filed as
a tone, never as possible voice: the voice grader's tone gate
sits ahead of its rubric, because the rubric's band
concentration and harmonicity terms both pay a pure tone.

numpy does the DSP. It is present on the watch host; where it is
absent the census benches with that reason rather than degrading
silently (the feed builders surface the import failure as the
desk's capture error).
"""

import base64
import math

import numpy as np

# Detection (020-RESEARCH.md, audio census section).
DETECT_DB = 11.0        # threshold above the window median PSD
EDGE_DB = 6.0           # hysteresis edge extension
CENSUS_MIN_SNR_DB = 8.0
EXTRACT_MIN_SNR_DB = 10.0
MERGE_GAP_HZ = 1000.0
MIN_WIDTH_HZ = 400.0    # narrower: carrier / CW, census log only
MAX_CANDIDATES = 12
MAX_SNIPPETS = 4
SNIPPET_MIN_S = 3.0
SNIPPET_MAX_S = 15.0
AUDIO_RATE = 16000
# Tier 2 excerpts (0.10.0): a candidate the census extracted -
# so it cleared EXTRACT_MIN_SNR_DB - but could not classify
# carries a decimated channel IQ excerpt for later
# classification: the channel's most active span, bounded in
# samples (about 2 s at the 48 ksps narrow-channel rate), at
# most this many per window, strongest first.
EXCERPT_MAX_SAMPLES = 96000
EXCERPT_MAX_PER_WINDOW = 2


# ------------------------------------------------------------ PSD

def _welch_db(iq, fs, nperseg):
    """One-sided Welch PSD in dB, fftshifted, with its freq axis."""
    win = np.hanning(nperseg)
    step = nperseg // 2
    acc = None
    count = 0
    for start in range(0, len(iq) - nperseg + 1, step):
        seg = iq[start:start + nperseg] * win
        spec = np.fft.fftshift(np.fft.fft(seg))
        p = (spec.real ** 2 + spec.imag ** 2)
        acc = p if acc is None else acc + p
        count += 1
    if acc is None:
        seg = np.zeros(nperseg, dtype=complex)
        seg[:len(iq)] = iq
        spec = np.fft.fftshift(np.fft.fft(seg * win))
        acc = spec.real ** 2 + spec.imag ** 2
        count = 1
    psd = acc / count
    freqs = np.fft.fftshift(np.fft.fftfreq(nperseg, 1.0 / fs))
    out = 10.0 * np.log10(psd + 1e-20)
    return freqs, out


def _segments(freqs, psd_db, noise_db):
    """Threshold runs with hysteresis edges, gap merging, and
    valley splitting. Returns [(lo_idx, hi_idx), ...]."""
    n = len(freqs)
    bin_hz = freqs[1] - freqs[0] if n > 1 else 1.0
    above = psd_db > noise_db + DETECT_DB
    runs = []
    i = 0
    while i < n:
        if above[i]:
            j = i
            while j + 1 < n and (above[j + 1]
                                 or psd_db[j + 1] > noise_db + EDGE_DB):
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    # Merge runs separated by less than MERGE_GAP_HZ.
    merged = []
    for run in runs:
        if merged and (run[0] - merged[-1][1]) * bin_hz < MERGE_GAP_HZ:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    # Split wide runs at valleys more than 6 dB below both sides.
    out = []
    for lo, hi in merged:
        width_hz = (hi - lo) * bin_hz
        if width_hz > 60000:
            seg = psd_db[lo:hi + 1]
            mid = len(seg) // 2
            valley = int(np.argmin(seg[mid // 2: len(seg) - mid // 2])
                         ) + mid // 2 if len(seg) > 8 else -1
            if valley > 0:
                left_peak = seg[:valley].max()
                right_peak = seg[valley:].max()
                if seg[valley] < min(left_peak, right_peak) - 6.0:
                    out.append((lo, lo + valley))
                    out.append((lo + valley, hi))
                    continue
        out.append((lo, hi))
    return out, bin_hz


def _channel_duty(chan, rate):
    """Activity duty of an extracted channel: fraction of 0.25 s
    blocks whose energy exceeds 12% of the channel's own median
    block energy, capped so a pure carrier reads as continuous.
    Cheap by construction - it reuses the extraction, where the
    earlier design re-ran a Welch pass per candidate."""
    block = max(1, int(rate * 0.25))
    n = len(chan) // block
    if n < 2:
        return 1.0
    energy = np.array([float(np.mean(np.abs(
        chan[i * block:(i + 1) * block]) ** 2)) for i in range(n)])
    med = np.median(energy) + 1e-20
    active = energy > max(med * 0.12, energy.max() * 0.02)
    return float(np.mean(active))


# ------------------------------------------------------- extraction

def _extract(iq, fs, offset_hz, bw_hz):
    """Digital down-conversion: shift, FFT-mask, decimate to a
    channel rate suited to the bandwidth. Returns (iq, rate)."""
    n = len(iq)
    t = np.arange(n) / fs
    shifted = iq * np.exp(-2j * np.pi * offset_hz * t)
    spec = np.fft.fft(shifted)
    freqs = np.fft.fftfreq(n, 1.0 / fs)
    keep = np.abs(freqs) <= bw_hz * 0.65
    spec = np.where(keep, spec, 0.0)
    baseband = np.fft.ifft(spec)
    if bw_hz <= 25000:
        target = 48000.0
    elif bw_hz <= 100000:
        target = 240000.0
    else:
        target = 480000.0
    decim = max(1, int(fs / target))
    if decim > 1:
        baseband = baseband[::decim]
    return baseband, fs / decim


def _excerpt(chan, rate):
    """One unclassified channel's tier 2 excerpt: its most
    active contiguous span, bounded to EXCERPT_MAX_SAMPLES,
    ci16-encoded for its SigMF pair. Activity is read in
    quarter-second blocks, the same blocks the duty estimate
    uses. Returns the feed-carried dict, or None for an empty
    channel."""
    from . import sigmf
    n = len(chan)
    if n == 0:
        return None
    if n <= EXCERPT_MAX_SAMPLES:
        seg = chan
    else:
        block = max(1, int(rate * 0.25))
        nblocks = n // block
        energy = np.array([float(np.mean(np.abs(
            chan[i * block:(i + 1) * block]) ** 2))
            for i in range(nblocks)])
        win = max(1, EXCERPT_MAX_SAMPLES // block)
        if nblocks <= win:
            start = 0
        else:
            sums = np.convolve(energy, np.ones(win), mode="valid")
            start = int(np.argmax(sums)) * block
        seg = chan[start:start + EXCERPT_MAX_SAMPLES]
    data, peak = sigmf.data_bytes(seg)
    return {"b64": base64.b64encode(data).decode("ascii"),
            "rate": float(rate), "peak": peak,
            "samples": int(len(seg))}


def _classify(chan, rate):
    """Modulation class from channel features. Digital veto first.
    Returns (klass, detail) with klass in AM, FM, USB, LSB, SSB,
    DATA, UNKNOWN."""
    mag = np.abs(chan) + 1e-12
    env_cv = float(np.std(mag) / np.mean(mag))
    phase = np.unwrap(np.angle(chan))
    inst = np.diff(phase) * rate / (2 * np.pi)
    if len(inst) > 16:
        med = np.median(inst)
        dev = inst - med
        mad = np.median(np.abs(dev)) * 1.4826 + 1e-9
        keep = np.abs(dev) < 6 * mad
        if_std = float(np.std(inst[keep])) if keep.any() else 0.0
    else:
        if_std = 0.0
    # Instantaneous-frequency histogram: a small set of discrete
    # tones at DISTINCT frequencies with a steady envelope is data
    # (FSK and friends). A single concentrated peak is a carrier,
    # not data: AM and CW live there too.
    if len(inst) > 256 and env_cv < 0.30:
        hist, edges = np.histogram(inst, bins=48)
        peak_bins = np.nonzero(hist > len(inst) * 0.06)[0]
        if 2 <= len(peak_bins) <= 5 and if_std < 4000:
            centers = (edges[peak_bins] + edges[peak_bins + 1]) / 2
            if centers.max() - centers.min() > 400:
                return "DATA", {"env_cv": round(env_cv, 2),
                                "if_std_hz": round(if_std)}
    # Carrier fraction from the channel PSD: AM keeps a strong
    # discrete carrier, SSB suppresses it. This, not envelope CV
    # alone, is the AM/SSB separator (real AM voice env_cv runs
    # 0.2-0.4, inside the SSB range).
    nperseg = min(4096, len(chan))
    carrier_fraction = 0.0
    psd = None
    if nperseg >= 512:
        _f, psd = _welch_db(chan, rate, nperseg)
        power = 10.0 ** (psd / 10.0)
        peak = int(np.argmax(power))
        half = max(1, int(60.0 / (rate / nperseg)))
        carrier_fraction = float(
            power[max(0, peak - half):peak + half + 1].sum()
            / (power.sum() + 1e-20))
    if carrier_fraction > 0.22 and if_std < 900:
        return "AM", {"env_cv": round(env_cv, 2),
                      "if_std_hz": round(if_std),
                      "carrier_fraction": round(carrier_fraction, 2)}
    if env_cv < 0.25 and if_std > 1500:
        return "FM", {"env_cv": round(env_cv, 2),
                      "if_std_hz": round(if_std)}
    # SSB test: edge steepness, only with no carrier present. The
    # carrier edge of an SSB signal is a cliff; the far edge rolls
    # off. Measure PSD slope over the outer 15% at each side.
    if psd is not None and carrier_fraction < 0.15:
        band = psd - psd.min()
        edge = max(2, int(len(band) * 0.15))
        lo_slope = float(band[edge] - band[0])
        hi_slope = float(band[-edge - 1] - band[-1])
        if max(lo_slope, hi_slope) > 8.0 and \
                abs(lo_slope - hi_slope) > 5.0 and env_cv < 0.9:
            if lo_slope > hi_slope:
                return "USB", {"edge_db": round(lo_slope, 1)}
            return "LSB", {"edge_db": round(hi_slope, 1)}
    if env_cv >= 0.25:
        return "UNKNOWN", {"env_cv": round(env_cv, 2),
                           "if_std_hz": round(if_std)}
    return "UNKNOWN", {"env_cv": round(env_cv, 2),
                       "if_std_hz": round(if_std)}


def _demod(chan, rate, klass):
    """Demodulate channel IQ to audio at AUDIO_RATE (float, -1..1)."""
    if klass == "FM":
        phase = np.unwrap(np.angle(chan))
        audio = np.diff(phase) * rate / (2 * np.pi)
        audio = np.concatenate([audio, audio[-1:]])
        # One-pole de-emphasis.
        alpha = 1.0 / (1.0 + 2 * np.pi * 2100.0 / rate)
        out = np.zeros_like(audio)
        acc = 0.0
        for i, x in enumerate(audio):
            acc = acc + alpha * (x - acc)
            out[i] = acc
        audio = out
    elif klass in ("USB", "LSB", "SSB"):
        if klass == "LSB":
            chan = np.conj(chan)
        audio = chan.real
    else:  # AM and UNKNOWN ride the envelope.
        audio = np.abs(chan)
        audio = audio - np.mean(audio)
    # Resample to AUDIO_RATE by FFT.
    if rate != AUDIO_RATE:
        n_out = int(len(audio) * AUDIO_RATE / rate)
        spec = np.fft.rfft(audio)
        out_spec = np.zeros(n_out // 2 + 1, dtype=complex)
        m = min(len(spec), len(out_spec))
        out_spec[:m] = spec[:m]
        audio = np.fft.irfft(out_spec, n_out)
    peak = np.max(np.abs(audio)) + 1e-12
    return (audio / peak * 0.9).astype(np.float64)


# ---------------------------------------------------------- grading

def _frame_psd(audio, rate):
    n = len(audio)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(audio * win)) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    return freqs, spec


def grade_audio(audio, rate, snr_db, klass, duty):
    """Voice-likelihood grade. Returns (letter, score, parts).
    DATA is a class, not a grade: returns ('DATA', 0, {}).
    A steady tone is a class too: returns ('TONE', 0, parts)
    from the tone gate ahead of the rubric."""
    if klass == "DATA":
        return "DATA", 0, {}
    parts = {}
    # Band concentration, 300-3400 Hz (25).
    freqs, spec = _frame_psd(audio, rate)
    total = spec.sum() + 1e-20
    band = ((freqs >= 300) & (freqs <= 3400)).astype(float)
    conc = float((spec * band).sum() / total)
    parts["band_concentration"] = round(conc, 2)
    score = 25.0 * min(1.0, conc / 0.75)
    # Syllabic modulation 2-8 Hz in at least 2 of 3 sub-windows (25).
    env = np.abs(audio)
    k = max(1, int(rate * 0.01))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    thirds = np.array_split(env, 3)
    hits = 0
    for th in thirds:
        if len(th) < rate // 2:
            continue
        ef = np.abs(np.fft.rfft(th - th.mean()))
        efreqs = np.fft.rfftfreq(len(th), 1.0 / rate)
        tot = ef[1:].sum() + 1e-20
        frac = ef[(efreqs >= 2) & (efreqs <= 8)].sum() / tot
        if frac > 0.18:
            hits += 1
    parts["syllabic_windows"] = hits
    score += 25.0 * (hits / 3.0)
    # Harmonicity: autocorrelation pitch peak, 50-400 Hz (20).
    seg = audio[:int(rate * 2.0)]
    if len(seg) > rate // 2:
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        ac = ac / (ac[0] + 1e-20)
        lo = int(rate / 400)
        hi = min(int(rate / 50), len(ac) - 1)
        pitch = float(ac[lo:hi].max()) if hi > lo else 0.0
    else:
        pitch = 0.0
    parts["harmonicity"] = round(pitch, 2)
    score += 20.0 * min(1.0, max(0.0, pitch))
    # Spectral dynamics (15): frame centroid spread.
    frame = int(rate * 0.05)
    cents = []
    for s in range(0, len(audio) - frame, frame * 2):
        f2, s2 = _frame_psd(audio[s:s + frame], rate)
        cents.append(float((f2 * s2).sum() / (s2.sum() + 1e-20)))
    if len(cents) > 4:
        dyn = float(np.std(cents) / (np.mean(cents) + 1e-9))
    else:
        dyn = 0.0
    parts["spectral_dynamics"] = round(dyn, 2)
    score += 15.0 * min(1.0, dyn / 0.45)
    # Pause structure (15): activity duty inside the speech range.
    parts["duty"] = round(float(duty), 2)
    if 0.25 <= duty <= 0.85:
        score += 15.0
    elif 0.1 <= duty <= 0.95:
        score += 7.0
    score_i = int(round(score))
    # The tone gate (0.11.0), ahead of every letter the rubric
    # can award: a capture dominated by one persistent narrow
    # spectral peak and its harmonics, with no syllabic
    # modulation and no spectral movement, is a tone, and a
    # tone is filed as a tone, never as possible voice. The
    # rubric alone pays a pure tone twice - band concentration
    # (a tone sits inside the voice band) and harmonicity (a
    # tone's autocorrelation is a perfect pitch peak) - which
    # is how a steady 1010 Hz test tone with one harmonic,
    # flat level and no syllables graded voice C in the field
    # on 2026-11-18. The gate's test is exact: the peak bin
    # and its first three harmonics hold at least 85 percent
    # of the audio power, the syllabic window count is zero,
    # and the frame centroid spread is at most 0.05.
    in_band = (freqs >= 80) & (freqs <= 4000)
    if in_band.any():
        peak_i = int(np.argmax(np.where(in_band, spec, 0.0)))
        f0 = float(freqs[peak_i])
        bin_hz = float(freqs[1] - freqs[0]) if len(freqs) > 1 \
            else 1.0
        tone_power = 0.0
        for k in (1, 2, 3, 4):
            tone_power += float(spec[
                np.abs(freqs - k * f0) <= 1.5 * bin_hz].sum())
        tone_conc = tone_power / total
    else:
        f0, tone_conc = 0.0, 0.0
    parts["tone_concentration"] = round(float(tone_conc), 2)
    if f0:
        parts["tone_freq_hz"] = int(round(f0))
    if tone_conc >= 0.85 and hits == 0 and dyn <= 0.05:
        return "TONE", 0, parts
    # Hard gates before any letter above C.
    if snr_db < 12.0 or duty < 0.15:
        letter = "C" if score_i >= 35 else "D"
        if snr_db < 8.0:
            letter = "D"
        return letter, score_i, parts
    if score_i >= 75:
        letter = "A"
    elif score_i >= 55:
        letter = "B"
    elif score_i >= 35:
        letter = "C"
    else:
        letter = "D"
    return letter, score_i, parts


# ------------------------------------------------ spectrum product

PRODUCT_BINS = 240


def _spectrum_product(core_f, core_p, center_hz, noise_db):
    """The window's magnitude spectrum product, from the PSD
    this analysis already computed: max-pooled to at most
    PRODUCT_BINS bins over the analysed core span, so a narrow
    carrier survives the reduction. No new samples, no new
    transform - a projection of work already done, filed by
    the officer as the dwell's tier 1 spectrum evidence."""
    n = len(core_p)
    if n == 0:
        return None
    if n > PRODUCT_BINS:
        edges = np.linspace(0, n, PRODUCT_BINS + 1).astype(int)
        bins = [float(core_p[edges[i]:edges[i + 1]].max())
                for i in range(PRODUCT_BINS)]
    else:
        bins = [float(v) for v in core_p]
    return {"lo_hz": float(center_hz + core_f[0]),
            "hi_hz": float(center_hz + core_f[-1]),
            "floor_db": round(float(noise_db), 1),
            "bins": [round(b, 1) for b in bins]}


# ------------------------------------------------------------- main

def analyze(iq, fs, center_hz, extract_cap=MAX_CANDIDATES):
    """Census one captured window. iq: complex numpy array.
    Returns a JSON-safe dict: candidates (strongest first) with
    audio carried as int16 sample lists at AUDIO_RATE for graded
    candidates inside the snippet caps, plus census counters
    and the window's spectrum product. Candidates past
    extract_cap are census rows only (grade None): on the watch
    host the extraction FFTs are the CPU cost, so the cap is a
    stated bound, not a silent drop."""
    iq = np.asarray(iq)
    if len(iq) < 4096:
        return {"candidates": [], "carriers": 0, "over_cap": 0,
                "noise_floor_db": None, "spectrum": None}
    nperseg = 16384 if fs >= 2e6 else 4096
    freqs, psd = _welch_db(iq, fs, nperseg)
    n = len(freqs)
    lo_i, hi_i = int(n * 0.05), int(n * 0.95)
    core_f, core_p = freqs[lo_i:hi_i], psd[lo_i:hi_i]
    # Mask the centre DC spike.
    dc = (np.abs(core_f) < (freqs[1] - freqs[0]) * 3)
    core_p = core_p.copy()
    core_p[dc] = -300.0
    noise_db = float(np.median(core_p[core_p > -299]))
    spectrum = _spectrum_product(core_f, core_p, center_hz,
                                 noise_db)
    runs, bin_hz = _segments(core_f, core_p, noise_db)
    found = []
    carriers = 0
    for lo, hi in runs:
        width = (hi - lo) * bin_hz
        peak = float(core_p[lo:hi + 1].max())
        snr = peak - noise_db
        if snr < CENSUS_MIN_SNR_DB:
            continue
        if width < MIN_WIDTH_HZ:
            carriers += 1
            continue
        found.append({
            "freq_hz": float(center_hz + core_f[(lo + hi) // 2]),
            "lo_hz": float(core_f[lo]), "hi_hz": float(core_f[hi]),
            "bw_hz": float(width), "snr_db": round(snr, 1),
            "peak_db": round(peak, 1)})
    found.sort(key=lambda c: -c["snr_db"])
    over_cap = max(0, len(found) - MAX_CANDIDATES)
    found = found[:MAX_CANDIDATES]
    candidates = []
    snippets = 0
    excerpts = 0
    extracted = 0
    for cand in found:
        offset = cand["freq_hz"] - center_hz
        entry = {"freq_hz": cand["freq_hz"], "bw_hz": cand["bw_hz"],
                 "snr_db": cand["snr_db"], "modulation": "UNKNOWN",
                 "grade": "D", "score": 0, "audio": None,
                 "duration_s": 0.0}
        if extracted >= extract_cap:
            entry["grade"] = None
            candidates.append(entry)
            continue
        if cand["snr_db"] >= EXTRACT_MIN_SNR_DB:
            extracted += 1
            try:
                chan, rate = _extract(iq, fs, offset, cand["bw_hz"])
                klass, detail = _classify(chan, rate)
                duty = _channel_duty(chan, rate)
                entry["duration_s"] = round(
                    duty * len(iq) / fs, 1)
                entry["features"] = detail
                if klass == "DATA":
                    entry["modulation"] = "DATA"
                    entry["grade"] = "DATA"
                else:
                    audio = _demod(chan, rate, klass)
                    letter, score, parts = grade_audio(
                        audio, AUDIO_RATE, cand["snr_db"], klass, duty)
                    entry["modulation"] = klass
                    entry["grade"] = letter
                    entry["score"] = score
                    entry["grade_parts"] = parts
                    if letter in ("A", "B", "C") \
                            and snippets < MAX_SNIPPETS \
                            and entry["duration_s"] >= 1.0:
                        n = int(min(len(audio),
                                    SNIPPET_MAX_S * AUDIO_RATE))
                        entry["audio"] = (
                            audio[:n] * 32767).astype(np.int16).tolist()
                        snippets += 1
                    if klass == "UNKNOWN" \
                            and excerpts < EXCERPT_MAX_PER_WINDOW:
                        # Extracted but unclassified: the channel
                        # itself is the evidence a later classifier
                        # or decoder will need. It rides the feed
                        # as a tier 2 excerpt (0.10.0).
                        exc = _excerpt(chan, rate)
                        if exc is not None:
                            exc["freq_hz"] = cand["freq_hz"]
                            exc["bw_hz"] = cand["bw_hz"]
                            entry["excerpt"] = exc
                            excerpts += 1
            except Exception:
                entry["modulation"] = "UNKNOWN"
        candidates.append(entry)
    return {"candidates": candidates, "carriers": carriers,
            "over_cap": over_cap,
            "noise_floor_db": round(noise_db, 1),
            "spectrum": spectrum}
