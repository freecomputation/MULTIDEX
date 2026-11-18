"""The feature-tree classifier (0.11.0).

An in-house, interpretable classifier for channel IQ excerpts:
a deterministic feature extractor computing about twenty
features from an excerpt, feeding a decision tree whose
splits are the readable constants named at the head of this
module. It is the first rung of the classification hierarchy
the limitations research set out - a deterministic feature
front-end and a shallow tree first, dossier-cluster voting
decisive above it, and a tiny convolutional second opinion
only later. The tree and the vote ship in this build; the
convolutional second opinion is the stated later addition,
and nothing in this module pretends to be it.

The tree classifies into the signal families the system
already decodes - AM voice, FM voice, SSB voice, FSK data,
CW - plus the two honest non-family outcomes a capture can
honestly be: a tone, and noise. Anything else is UNKNOWN. An
unknown result is a first-class answer, never a forced label:
a shape outside every family the system decodes stays
unknown, and the probe desk's business is exactly those.

The voice test inside each voice branch is the census
engine's own voice grader, run on that branch's demodulation:
the classifier does not invent a second voice instrument
beside the calibrated one. A demodulation the grader scores
at or over its own C line counts as voice dynamics present.

No external machine-learning library is used or needed.
numpy is substrate, as everywhere in the signal desks. The
features lean on the census engine's own measurements where
they exist (the Welch PSD, the channel duty estimate, the
instantaneous-frequency statistics of its modulation
classifier), so the tree reads the same quantities the census
already trusts.
"""

import base64

import numpy as np

from . import audiocensus

# The families the tree can return. UNKNOWN is not in this
# list: it is the absence of a family, returned as its own
# string so no caller can mistake it for a classification.
FAMILIES = ("am-voice", "fm-voice", "ssb-voice", "fsk-data",
            "cw", "tone", "noise")
UNKNOWN = "unknown"

# ------------------------------------------------------- tree splits
# Every threshold the tree tests, named once here so the tree
# below reads as the constants it is.

# A tone: one spectral peak that persists in nearly every
# frame of the excerpt, inside a narrow occupied bandwidth.
# The bandwidth ceiling is what separates a tone from an AM
# carrier: the carrier also persists in every frame, but its
# sidebands spread its excess power over kilohertz, while a
# tone's excess power sits inside a few hundred hertz.
TONE_PERSISTENCE_MIN = 0.90
TONE_BW_MAX_HZ = 300.0
TONE_PEAK_MIN = 0.30
# A sweeping emission: the spectral centroid travels at least
# this far across the excerpt, and the travel is a large
# share of the band's own interquartile width. No family the
# system decodes sweeps, so a sweeper is unknown by
# construction.
DRIFT_MIN_HZ = 3000.0
DRIFT_BW_FRACTION = 0.6
# FSK data: a small set of discrete instantaneous-frequency
# tones, a steady envelope, a readable symbol rate in the
# autocorrelation, and no discrete carrier (an AM carrier is
# one strong line, not a tone set).
FSK_PEAKS_MIN = 2
FSK_PEAKS_MAX = 6
FSK_SPAN_MIN_HZ = 400.0
FSK_ENV_CV_MAX = 0.35
FSK_CARRIER_MAX = 0.35
BAUD_STRENGTH_MIN = 0.35
# CW: a keyed carrier is narrow, gated, and tone-like while
# it is keyed. A steady narrow carrier is the tone branch
# above, not CW: CW requires the keying gaps. The gating is
# read as the envelope's activity duty in 10 ms blocks
# (keying_duty): the census duty estimate's quarter-second
# blocks are coarser than a dit and call every fast-keyed
# signal continuous.
CW_BW_MAX_HZ = 400.0
CW_DUTY_MIN = 0.08
CW_DUTY_MAX = 0.92
CW_PERSISTENCE_MIN = 0.40
# AM: a strong discrete carrier is the separator. The
# instantaneous-frequency gate is wider than the census
# classifier's own 900 Hz: at excerpt SNRs the phase noise
# widens a true AM signal's spread past that line, and the
# gate's remaining work is only to exclude the wild-frequency
# families (keyed carriers, sidebands) whose spreads run to
# five figures.
AM_CARRIER_MIN = 0.22
AM_IF_STD_MAX_HZ = 2500.0
# FM: a constant envelope with a wide instantaneous-frequency
# swing. Narrowband FM voice deviates a few kilohertz, so the
# swing floor sits under a kilohertz of standard deviation.
FM_ENV_CV_MAX = 0.25
FM_IF_STD_MIN_HZ = 700.0
# SSB: no carrier, and one steep spectral edge, read as the
# asymmetry between the occupied band's two edge slopes. The
# carrier ceiling is looser than the census's own 0.15: a
# voice's strongest harmonic concentrates real power in one
# bin of a long Welch average, and the AM branch above has
# already claimed every signal with a quiet instantaneous
# frequency, so what reaches this branch with a hot bin and a
# one-sided edge is a sideband, not a carrier.
SSB_CARRIER_MAX = 0.35
SSB_ASYM_MIN_DB = 5.0
# Voice, on any branch: the census grader's score on that
# branch's demodulation at or over its own C line (35 of
# 100, the line its hard gates and letters are built on).
VOICE_SCORE_MIN = 35
# Noise: a flat spectrum with no peak standing out of it.
NOISE_FLATNESS_MIN = 0.50
NOISE_PEAK_MAX = 0.05


def decode_excerpt(exc):
    """A feed-carried excerpt dict (ci16 SigMF payload, as the
    census engine encodes it) back to complex samples at its
    recorded scale."""
    raw = base64.b64decode(exc["b64"])
    vals = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    peak = float(exc.get("peak") or 1.0)
    chan = (vals[0::2] + 1j * vals[1::2]) / 32767.0 * peak
    return chan, float(exc["rate"])


def _baud(inst, env, rate):
    """Symbol-rate estimate from autocorrelation. Random data
    has no spectral line at its own baud rate - an NRZ stream's
    spectrum is a continuum - but its autocorrelation is a
    triangle that falls to zero at one symbol period, so the
    first lag where the normalized autocorrelation of the
    instantaneous frequency (FSK keying) or of the envelope
    (on-off keying) drops under 0.25 reads the symbol period.
    Strength is the depth of that fall from the short-lag
    value: a keyed signal starts correlated and lets go, a
    smooth one (voice, noise) either never holds on or never
    lets go inside the keying range."""
    best_hz, best_strength = 0.0, 0.0
    for sig in (inst, env):
        x = np.asarray(sig, dtype=float)
        if len(x) < 1024:
            continue
        x = x[:8192] - float(np.mean(x))
        ac = np.correlate(x, x, mode="full")[len(x) - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lo = max(2, int(rate / 6000.0))
        hi = min(int(rate / 8.0), len(ac) - 1)
        if hi <= lo or ac[lo] < 0.5:
            continue
        cross = None
        for lag in range(lo, hi):
            if ac[lag] < 0.25:
                cross = lag
                break
        if cross is None:
            continue
        strength = float(ac[lo] - ac[cross])
        if strength > best_strength:
            best_hz, best_strength = rate / cross, strength
    return best_hz, best_strength


def _empty_features():
    return {"occupied_bw_hz": 0.0, "iqr_bw_hz": 0.0,
            "keying_duty": 0.0,
            "spectral_flatness": 0.0,
            "peak_concentration": 0.0, "carrier_fraction": 0.0,
            "centroid_hz": 0.0, "centroid_drift_hz": 0.0,
            "env_cv": 0.0, "duty": 0.0, "if_std_hz": 0.0,
            "if_peaks": 0.0, "if_peak_span_hz": 0.0,
            "baud_hz": 0.0, "baud_strength": 0.0,
            "tone_persistence": 0.0, "sideband_asym_db": 0.0,
            "voice_score_am": 0.0, "voice_score_fm": 0.0,
            "voice_score_ssb": 0.0, "snr_db": 0.0}


def extract_features(chan, rate):
    """The feature vector of one channel excerpt: about twenty
    deterministic features, every one a plain measurement of
    the samples. Returns a dict of floats (counts as floats;
    callers round for display).

    Occupied bandwidth and peak concentration are read from
    the power in excess of the channel's own noise floor: on
    raw power a narrow signal in a wide channel measures as
    wide as the channel, because the floor's spread outweighs
    the signal in the cumulative sum."""
    chan = np.asarray(chan)
    n = len(chan)
    feats = {"samples": float(n)}
    if n < 256:
        feats.update(_empty_features())
        return feats
    mag = np.abs(chan) + 1e-12
    feats["env_cv"] = float(np.std(mag) / np.mean(mag))
    feats["duty"] = float(audiocensus._channel_duty(chan, rate))
    # Keying duty: the envelope's activity in 10 ms blocks,
    # under the duty estimate's own threshold rule - the
    # finer clock a keyed signal's gaps are visible on.
    blk = max(1, int(rate * 0.01))
    nb = len(mag) // blk
    if nb >= 8:
        energy = np.array([float(np.mean(
            mag[i * blk:(i + 1) * blk] ** 2)) for i in range(nb)])
        med = np.median(energy) + 1e-20
        feats["keying_duty"] = float(np.mean(
            energy > max(med * 0.12, energy.max() * 0.02)))
    else:
        feats["keying_duty"] = feats["duty"]
    # The channel PSD, the census engine's own Welch estimate.
    nperseg = min(4096, n)
    freqs, psd_db = audiocensus._welch_db(chan, rate, nperseg)
    power = 10.0 ** (psd_db / 10.0)
    total = float(power.sum()) + 1e-20
    floor = float(np.median(power))
    excess = np.maximum(power - floor, 0.0)
    etotal = float(excess.sum()) + 1e-20
    cum = np.cumsum(excess)
    lo = int(np.searchsorted(cum, 0.005 * etotal))
    hi = int(np.searchsorted(cum, 0.995 * etotal))
    hi = min(hi, len(freqs) - 1)
    feats["occupied_bw_hz"] = float(freqs[hi] - freqs[lo])
    q1 = int(np.searchsorted(cum, 0.25 * etotal))
    q3 = int(np.searchsorted(cum, 0.75 * etotal))
    feats["iqr_bw_hz"] = float(freqs[min(q3, len(freqs) - 1)]
                               - freqs[q1])
    inband = power[lo:hi + 1]
    if len(inband):
        feats["spectral_flatness"] = float(
            np.exp(np.mean(np.log(inband + 1e-20)))
            / (np.mean(inband) + 1e-20))
    else:
        feats["spectral_flatness"] = 0.0
    peak = int(np.argmax(power))
    feats["peak_concentration"] = float(excess[peak] / etotal)
    feats["centroid_hz"] = float((freqs * excess).sum() / etotal)
    feats["snr_db"] = float(psd_db[peak] - np.median(psd_db))
    half = max(1, int(60.0 / (rate / nperseg)))
    feats["carrier_fraction"] = float(
        power[max(0, peak - half):peak + half + 1].sum() / total)
    # Sideband asymmetry: the census engine's SSB edge test,
    # read over the occupied band's own edges (the slopes of
    # the segment's outer 15 percent at each side), because an
    # excerpt's channel is wider than the signal inside it.
    seg = psd_db[lo:hi + 1]
    if len(seg) >= 8:
        band = seg - seg.min()
        edge = max(2, int(len(band) * 0.15))
        lo_slope = float(band[edge] - band[0])
        hi_slope = float(band[-edge - 1] - band[-1])
        feats["sideband_asym_db"] = float(abs(lo_slope - hi_slope))
    else:
        feats["sideband_asym_db"] = 0.0
    # Instantaneous frequency: robust spread, and the discrete
    # tone count from its histogram - computed on the
    # MAD-masked samples (the census classifier's own outlier
    # rejection, so keying transitions do not smear the tones
    # across the histogram), with adjacent hot bins grouped
    # into one tone each.
    phase = np.unwrap(np.angle(chan))
    inst = np.diff(phase) * rate / (2 * np.pi)
    keep = None
    if len(inst) > 16:
        med = np.median(inst)
        dev = inst - med
        mad = np.median(np.abs(dev)) * 1.4826 + 1e-9
        keep = np.abs(dev) < 6 * mad
        feats["if_std_hz"] = float(np.std(inst[keep])) \
            if keep.any() else 0.0
    else:
        feats["if_std_hz"] = 0.0
    feats["if_peaks"] = 0.0
    feats["if_peak_span_hz"] = 0.0
    if keep is not None and int(keep.sum()) > 256:
        hist, edges = np.histogram(inst[keep], bins=48)
        hot = hist > len(inst[keep]) * 0.05
        groups = []
        i = 0
        while i < len(hot):
            if hot[i]:
                j = i
                while j + 1 < len(hot) and hot[j + 1]:
                    j += 1
                groups.append((edges[i] + edges[j + 1]) / 2)
                i = j + 1
            else:
                i += 1
        feats["if_peaks"] = float(len(groups))
        if len(groups) >= 2:
            feats["if_peak_span_hz"] = float(
                max(groups) - min(groups))
    # The baud estimate reads the clipped instantaneous
    # frequency (keying transitions spike it, and the spikes'
    # broadband energy would swamp the keying line) and the
    # envelope.
    if keep is not None:
        inst_baud = np.where(keep, inst, np.median(inst))
    else:
        inst_baud = inst
    baud_hz, baud_strength = _baud(inst_baud, mag, rate)
    feats["baud_hz"] = baud_hz
    feats["baud_strength"] = baud_strength
    # Centroid drift: per-frame spectral centroids across the
    # excerpt, fitted linearly; the feature is the total
    # travel, read on excess power like the centroid itself.
    frames = 8
    cents = []
    seg_n = n // frames
    if seg_n >= 256:
        for i in range(frames):
            f2, p2 = audiocensus._welch_db(
                chan[i * seg_n:(i + 1) * seg_n], rate,
                min(1024, seg_n))
            pw = 10.0 ** (p2 / 10.0)
            ex = np.maximum(pw - np.median(pw), 0.0)
            cents.append(float((f2 * ex).sum()
                               / (ex.sum() + 1e-20)))
    if len(cents) >= 4:
        xs = np.arange(len(cents), dtype=float)
        slope = float(np.polyfit(xs, np.array(cents), 1)[0])
        feats["centroid_drift_hz"] = slope * (len(cents) - 1)
    else:
        feats["centroid_drift_hz"] = 0.0
    # Tone persistence: the share of short frames whose peak
    # bin is the excerpt's own peak bin, within one frame bin
    # (the frame transform is coarser than the full one, so
    # the tolerance is the frame's own bin width).
    frame = max(256, int(rate * 0.05))
    if n >= frame * 4:
        fseg = min(1024, frame)
        frame_bin = rate / fseg
        hits = 0
        count = 0
        for s in range(0, n - frame + 1, frame):
            _f, p2 = audiocensus._welch_db(
                chan[s:s + frame], rate, fseg)
            pw = 10.0 ** (p2 / 10.0)
            pk_hz = _f[int(np.argmax(pw))]
            count += 1
            if abs(pk_hz - freqs[peak]) <= frame_bin:
                hits += 1
        feats["tone_persistence"] = hits / max(1, count)
    else:
        feats["tone_persistence"] = 0.0
    # The voice scores: the census grader on each voice
    # branch's demodulation. A tone grades TONE on every path
    # and scores 0, so no steady tone can buy voice dynamics
    # here either.
    for key, klass in (("voice_score_am", "AM"),
                       ("voice_score_fm", "FM"),
                       ("voice_score_ssb", "USB")):
        try:
            audio = audiocensus._demod(chan, rate, klass)
            _letter, score, _parts = audiocensus.grade_audio(
                audio, audiocensus.AUDIO_RATE, feats["snr_db"],
                klass, feats["duty"])
            feats[key] = float(score)
        except Exception:
            feats[key] = 0.0
    return feats


def classify(feats):
    """The decision tree. Returns (family, reasons): the family
    the splits reach, and the plain-word path taken, so every
    classification in the record can be re-derived by a reader
    from the stated features and the stated constants."""
    reasons = []
    obw = feats.get("occupied_bw_hz", 0.0)
    # 1. A persistent narrow peak is a tone, whatever else the
    # excerpt holds: no voice family holds one frequency still.
    if feats.get("tone_persistence", 0.0) >= TONE_PERSISTENCE_MIN \
            and obw <= TONE_BW_MAX_HZ \
            and feats.get("peak_concentration", 0.0) >= TONE_PEAK_MIN:
        reasons.append(
            "one spectral peak persists in %.0f%% of frames "
            "inside %.0f Hz of occupied bandwidth: a tone"
            % (feats["tone_persistence"] * 100, obw))
        return "tone", reasons
    # 2. A sweeping emission belongs to no decoded family.
    drift = abs(feats.get("centroid_drift_hz", 0.0))
    if drift >= max(DRIFT_MIN_HZ, DRIFT_BW_FRACTION
                    * feats.get("iqr_bw_hz", 0.0)) \
            and feats.get("iqr_bw_hz", 0.0) >= 1000.0:
        reasons.append(
            "the spectral centroid travels %.0f Hz across the "
            "excerpt: a sweeping emission, and no family the "
            "system decodes sweeps" % drift)
        return UNKNOWN, reasons
    # 3. Discrete instantaneous-frequency tones on a steady
    # envelope, keyed at a readable symbol rate, with no
    # discrete carrier: FSK data.
    if FSK_PEAKS_MIN <= feats.get("if_peaks", 0.0) <= FSK_PEAKS_MAX \
            and feats.get("if_peak_span_hz", 0.0) >= FSK_SPAN_MIN_HZ \
            and feats.get("env_cv", 1.0) < FSK_ENV_CV_MAX \
            and feats.get("carrier_fraction", 1.0) <= FSK_CARRIER_MAX \
            and feats.get("baud_strength", 0.0) >= BAUD_STRENGTH_MIN:
        reasons.append(
            "%.0f discrete frequency tones spanning %.0f Hz on "
            "a steady envelope, keyed at about %.0f baud: FSK "
            "data" % (feats["if_peaks"], feats["if_peak_span_hz"],
                      feats["baud_hz"]))
        return "fsk-data", reasons
    # 4. A narrow gated carrier, tone-like while keyed: CW.
    if obw <= CW_BW_MAX_HZ \
            and CW_DUTY_MIN <= feats.get("keying_duty", 1.0) \
            <= CW_DUTY_MAX \
            and feats.get("tone_persistence", 0.0) \
            >= CW_PERSISTENCE_MIN:
        reasons.append(
            "a carrier %.0f Hz wide, keyed with duty %.2f: CW"
            % (obw, feats["keying_duty"]))
        return "cw", reasons
    # 5-7. The voice branches. Each is a hypothesis: a
    # structural gate lets the branch be tested, and the
    # branch is claimed only where the census grader hears
    # voice dynamics in that branch's demodulation. A branch
    # whose structure fits but whose voice test fails does not
    # end the walk - it is recorded as a near miss and the
    # next hypothesis is tested, because at excerpt SNRs the
    # structural features of neighbouring families overlap
    # (an FM signal's peak bin can hold a quarter of its power
    # and read as a weak carrier). Only the fallthrough at the
    # end returns UNKNOWN, with every near miss in its reasons.
    if feats.get("carrier_fraction", 0.0) > AM_CARRIER_MIN \
            and feats.get("if_std_hz", 1e9) < AM_IF_STD_MAX_HZ:
        if feats.get("voice_score_am", 0.0) >= VOICE_SCORE_MIN:
            reasons.append(
                "a discrete carrier (fraction %.2f) whose "
                "envelope demodulation scores %.0f of 100 as "
                "voice on the census grader: AM voice"
                % (feats["carrier_fraction"], feats["voice_score_am"]))
            return "am-voice", reasons
        reasons.append(
            "a discrete carrier (fraction %.2f) whose envelope "
            "demodulation scores %.0f of 100 as voice: not "
            "claimed as AM voice"
            % (feats["carrier_fraction"], feats["voice_score_am"]))
    if feats.get("env_cv", 1.0) < FM_ENV_CV_MAX \
            and feats.get("if_std_hz", 0.0) > FM_IF_STD_MIN_HZ:
        if feats.get("voice_score_fm", 0.0) >= VOICE_SCORE_MIN:
            reasons.append(
                "a constant envelope swinging %.0f Hz whose FM "
                "demodulation scores %.0f of 100 as voice on "
                "the census grader: FM voice"
                % (feats["if_std_hz"], feats["voice_score_fm"]))
            return "fm-voice", reasons
        reasons.append(
            "a constant envelope swinging %.0f Hz whose FM "
            "demodulation scores %.0f of 100 as voice: not "
            "claimed as FM voice"
            % (feats["if_std_hz"], feats["voice_score_fm"]))
    if feats.get("carrier_fraction", 1.0) < SSB_CARRIER_MAX \
            and feats.get("sideband_asym_db", 0.0) > SSB_ASYM_MIN_DB:
        if feats.get("voice_score_ssb", 0.0) >= VOICE_SCORE_MIN:
            reasons.append(
                "no carrier and a one-sided spectral edge "
                "(%.1f dB asymmetry) whose sideband "
                "demodulation scores %.0f of 100 as voice on "
                "the census grader: SSB voice"
                % (feats["sideband_asym_db"],
                   feats["voice_score_ssb"]))
            return "ssb-voice", reasons
        reasons.append(
            "no carrier and a one-sided spectral edge whose "
            "sideband demodulation scores %.0f of 100 as "
            "voice: not claimed as SSB voice"
            % feats.get("voice_score_ssb", 0.0))
    # 8. A flat spectrum with no peak is noise: classified as
    # what it is, which is nothing heard.
    if feats.get("spectral_flatness", 0.0) >= NOISE_FLATNESS_MIN \
            and feats.get("peak_concentration", 1.0) \
            <= NOISE_PEAK_MAX:
        reasons.append(
            "a flat spectrum (flatness %.2f) with no peak "
            "standing out of it: noise"
            % feats["spectral_flatness"])
        return "noise", reasons
    reasons.append(
        "no branch of the tree claims this excerpt: occupied "
        "bandwidth %.0f Hz, envelope CV %.2f, frequency spread "
        "%.0f Hz, carrier fraction %.2f"
        % (obw, feats.get("env_cv", 0.0),
           feats.get("if_std_hz", 0.0),
           feats.get("carrier_fraction", 0.0)))
    return UNKNOWN, reasons


def classify_excerpt(exc):
    """Classify one feed-carried excerpt end to end: decode,
    measure, walk the tree. Returns {family, features, reasons}
    with the features rounded for the record."""
    chan, rate = decode_excerpt(exc)
    feats = extract_features(chan, rate)
    family, reasons = classify(feats)
    return {"family": family,
            "features": {k: round(float(v), 4)
                         for k, v in feats.items()},
            "reasons": reasons}
