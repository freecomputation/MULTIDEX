"""APT imagery decoding, in-package (0.5.0).

NOAA APT is an analogue picture transmission: 2080 words per
line at 4160 words per second, two lines per second. The line
layout in words is fixed by the published format - Sync A (39,
a 1040 Hz pulse train), space (47), image A (909), telemetry A
(45), Sync B (39, an 832 Hz pulse train), space (47), image B
(909), telemetry B (45) - and the video envelope amplitude-
modulates a 2400 Hz subcarrier.

This module is the package's own decoder for that format, and
it replaced the aptdec program binding in 0.5.0 under the
independence law: no external program is invoked and no image
library is used. The decode is:

- envelope recovery: the analytic-signal magnitude of the
  recorded audio (an FFT Hilbert transform over numpy), lightly
  smoothed, resampled onto the 4160 word-per-second grid;
- framing: the Sync A template is correlated against the whole
  recording and the correlations folded at the 2080-word line
  period, so the line phase is the one the entire pass agrees
  on; each line's start is then refined against its own sync,
  which absorbs clock drift across a long pass;
- calibration: grey levels are mapped from the sync pulses
  themselves - the median envelope over Sync A's high phases
  is the white reference and over its low phases the black
  reference - so the mapping is the signal's own, not a gain
  guessed at the desk;
- output: an 8-bit greyscale PNG written by the encoder below
  over zlib - signature, IHDR, one IDAT, IEND, filter 0 rows.

A recording with no APT in it fails plainly: when the sync
correlation or the sync level span is too weak to frame a
line, AptError carries the reason and the satellite desk files
the audio with that reason stated. Nothing here invents an
image from noise: a decode is produced only from lines whose
sync was actually found.
"""

import io
import struct
import wave
import zlib

import numpy as np

SUBCARRIER_HZ = 2400.0
WORD_RATE = 4160.0          # envelope words per second
LINE_WORDS = 2080           # words per APT line

# Line layout, in words from the line start.
SYNC_A_WORDS = 39
IMAGE_A_OFF = 86            # after Sync A (39) and space (47)
IMAGE_WORDS = 909
TELEM_A_OFF = 995
SYNC_B_OFF = 1040
IMAGE_B_OFF = 1126
TELEM_B_OFF = 2035

# Sync templates over one word each: Sync A is a 1040 Hz square
# wave sampled at the word rate (period 4 words, high for 2);
# Sync B is 832 Hz (period 5 words, high for 2.5, sampled high
# at word centres 0, 1, 2 of each period).
SYNC_A_TEMPLATE = np.array(
    [1.0 if (i % 4) < 2 else 0.0 for i in range(SYNC_A_WORDS)])
SYNC_B_TEMPLATE = np.array(
    [1.0 if (i % 5) < 3 else 0.0 for i in range(SYNC_A_WORDS)])

# A line is framed only when its sync actually correlates: the
# mean normalised Sync A correlation over the framed lines must
# clear this, and the sync pulses must span enough envelope to
# calibrate against. Both were set from the format, not tuned to
# a fixture: a real APT sync is a full-depth pulse train, so a
# genuine pass correlates near 1.0, while uncorrelated audio
# (the 0.3.0 field recording held no subcarrier at all) sits
# near zero.
MIN_SYNC_CORRELATION = 0.5
MIN_SYNC_SPAN = 0.02        # envelope units, full scale ~1.0
REFINE_WORDS = 16           # per-line sync search half-window


class AptError(Exception):
    """The recording holds no decodable APT image; the message
    is the plain-word reason the desk files."""


# ------------------------------------------------------------ envelope

def _envelope(samples, rate):
    """The AM envelope of the 2400 Hz subcarrier, as floats.

    samples is an int16 array. The analytic signal's magnitude
    is the envelope of a carrier whose modulation is everywhere
    non-negative, which the APT video signal is.
    """
    x = np.asarray(samples, dtype=np.float64) / 32768.0
    n = len(x)
    if n == 0:
        return x
    spectrum = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[1:n // 2] = 2.0
        h[n // 2] = 1.0
    else:
        h[1:(n + 1) // 2] = 2.0
    env = np.abs(np.fft.ifft(spectrum * h))
    return env


def _to_words(env, rate):
    """Resample the envelope onto the 4160 word/second grid.

    Each word is sampled at its opening instant: the format
    defines a word's level as the level the envelope takes at
    the word's start, and the sync calibration below reads the
    same convention, so levels stay comparable across the line.
    """
    n_words = int(len(env) * WORD_RATE / rate)
    if n_words <= 0:
        return np.zeros(0)
    pos = np.arange(n_words) * (rate / WORD_RATE)
    return np.interp(pos, np.arange(len(env)), env)


# -------------------------------------------------------------- framing

def _sync_correlation(words):
    """Normalised correlation of the Sync A template at every
    word offset. Returns an array of length len(words)-38 whose
    k-th entry scores a line starting at word k."""
    t = SYNC_A_TEMPLATE - SYNC_A_TEMPLATE.mean()
    t_norm = np.sqrt(np.sum(t * t))
    raw = np.correlate(words, t, mode="valid")
    # Pearson normalisation: the template is zero-mean, so the
    # numerator already ignores each window's DC level; the
    # denominator must ignore it too, or a strong carrier's DC
    # alone drags a perfect sync's score toward 0.5. Window
    # sums and sums of squares come from cumulative sums, so
    # the whole pass scores in one pass.
    win = len(t)
    csum = np.concatenate(([0.0], np.cumsum(words)))
    sq = np.concatenate(([0.0], np.cumsum(words * words)))
    energy = (sq[win:] - sq[:-win]
              - (csum[win:] - csum[:-win]) ** 2 / win)
    denom = np.sqrt(np.maximum(energy, 1e-12)) * t_norm
    return raw / denom


def _frame_lines(words):
    """Find the line starts in a word-rate envelope.

    Returns (starts, mean_sync_correlation). The global phase is
    the fold of every line's sync correlation at the line
    period; each line then refines its own start within a small
    window, so a pass whose clock drifts still frames line by
    line on the sync that is actually there.
    """
    if len(words) < LINE_WORDS + SYNC_A_WORDS:
        raise AptError("the recording is shorter than one APT "
                       "line; no image can be framed from it")
    corr = _sync_correlation(words)
    usable = len(corr) - (len(corr) % LINE_WORDS)
    if usable <= 0:
        raise AptError("no APT sync found in this recording")
    folded = corr[:usable].reshape(-1, LINE_WORDS).sum(axis=0)
    phase = int(np.argmax(folded))
    starts = []
    scores = []
    line = 0
    while True:
        expected = phase + line * LINE_WORDS
        lo = max(0, expected - REFINE_WORDS)
        hi = min(len(corr) - 1, expected + REFINE_WORDS)
        if lo > hi or lo + LINE_WORDS > len(words):
            break
        start = lo + int(np.argmax(corr[lo:hi + 1]))
        if start + LINE_WORDS > len(words):
            break
        starts.append(start)
        scores.append(float(corr[start]))
        line += 1
    if not starts:
        raise AptError("no APT sync found in this recording")
    return starts, float(np.mean(scores))


# --------------------------------------------------------------- decode

def decode_samples(samples, rate):
    """Decode APT imagery from int16 audio samples.

    Returns (pixels, info): pixels is a uint8 array of shape
    (lines, 2080) - the full APT line, sync and telemetry
    included, exactly as the format lays it down - and info
    carries the framing evidence (line count, mean sync
    correlation, the calibration references). Raises AptError
    with the reason when the recording holds no decodable APT.
    """
    env = _envelope(samples, rate)
    words = _to_words(env, rate)
    starts, mean_corr = _frame_lines(words)
    if mean_corr < MIN_SYNC_CORRELATION:
        raise AptError(
            "no APT sync found in this recording (best mean "
            "sync correlation %.2f)" % mean_corr)
    t = SYNC_A_TEMPLATE
    high = np.concatenate([words[s:s + SYNC_A_WORDS][t > 0.5]
                           for s in starts])
    low = np.concatenate([words[s:s + SYNC_A_WORDS][t <= 0.5]
                          for s in starts])
    ref_high = float(np.median(high))
    ref_low = float(np.median(low))
    span = ref_high - ref_low
    if span < MIN_SYNC_SPAN:
        raise AptError(
            "APT sync pulses are too weak in this recording to "
            "calibrate an image (sync span %.3f)" % span)
    lines = np.stack([words[s:s + LINE_WORDS] for s in starts])
    grey = np.clip((lines - ref_low) / span, 0.0, 1.0)
    pixels = np.round(grey * 255.0).astype(np.uint8)
    info = {"lines": len(starts), "width": LINE_WORDS,
            "sync_correlation": round(mean_corr, 3),
            "ref_low": round(ref_low, 4), "ref_high": round(ref_high, 4)}
    return pixels, info


def decode_wav(wav_bytes):
    """Decode APT imagery from WAV bytes (the filed pass audio).

    Returns (png_bytes, info). Raises AptError when the bytes
    are not a readable mono 16-bit WAV or hold no decodable APT.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError, struct.error) as exc:
        raise AptError("the pass audio is not a readable WAV "
                       "recording (%s)" % exc)
    if channels != 1 or width != 2:
        raise AptError("the pass audio is not a mono 16-bit WAV "
                       "recording")
    samples = np.frombuffer(frames, dtype="<i2")
    if len(samples) < rate // 2:
        raise AptError("the recording is shorter than one APT "
                       "line; no image can be framed from it")
    pixels, info = decode_samples(samples, rate)
    return encode_png(pixels), info


# ----------------------------------------------------------------- PNG

def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(pixels):
    """An 8-bit greyscale PNG of a (lines, width) uint8 array,
    written with this build's own encoder over zlib: filter 0
    on every row, one IDAT. No image library is involved."""
    pixels = np.asarray(pixels, dtype=np.uint8)
    height, width = pixels.shape
    raw = b"".join(b"\x00" + pixels[i].tobytes()
                   for i in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))
