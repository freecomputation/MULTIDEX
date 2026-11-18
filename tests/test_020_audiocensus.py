"""0.2.0 audio census: detection, classification, grading, and
the negative controls. Static must grade D; data must be DATA;
voice-shaped signals must grade as voice candidates."""

import numpy as np

from multidex import audiocensus as ac
from multidex import desks

FS = 480000.0
CENTER = 146.0e6


def _noise(n, seed=7, sigma=0.02):
    rng = np.random.default_rng(seed)
    return (rng.normal(0, sigma, n) + 1j * rng.normal(0, sigma, n))


def _voice_audio(seconds=4.0, rate=16000.0, pitch=130.0, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(int(rate * seconds)) / rate
    env = np.zeros_like(t)
    pos = 0.1
    while pos < seconds - 0.3:
        dur = rng.uniform(0.25, 0.7)
        env[int(pos * rate):int((pos + dur) * rate)] = 1.0
        pos += dur + rng.uniform(0.15, 0.6)
    audio = np.zeros_like(t)
    for h in range(1, 20):
        f = pitch * h
        if f > 3400:
            break
        form = np.exp(-((f - 800) ** 2) / (2 * 500 ** 2)) \
            + 0.6 * np.exp(-((f - 2200) ** 2) / (2 * 600 ** 2))
        audio += form / h * np.sin(2 * np.pi * f * t)
    audio *= env
    return audio / (np.max(np.abs(audio)) + 1e-9)


def _am_voice(offset=50000.0, seconds=4.0):
    n = int(FS * seconds)
    t = np.arange(n) / FS
    audio = _voice_audio(seconds)
    up = np.interp(t, np.arange(len(audio)) / 16000.0, audio)
    return (1.0 + 0.85 * up) * np.exp(2j * np.pi * offset * t)


def test_static_window_grades_no_voice():
    iq = _noise(int(FS * 4))
    result = ac.analyze(iq, FS, CENTER)
    for cand in result["candidates"]:
        assert cand["grade"] in ("D", None), cand


def test_static_audio_grades_d_directly():
    rng = np.random.default_rng(11)
    white = rng.normal(0, 0.5, 16000 * 4)
    letter, score, _parts = ac.grade_audio(white, 16000, 20.0,
                                           "FM", 1.0)
    assert letter == "D", (letter, score)


def test_am_voice_detected_classified_and_graded():
    iq = _noise(int(FS * 4)) + 0.4 * _am_voice()
    result = ac.analyze(iq, FS, CENTER)
    cands = [c for c in result["candidates"]
             if abs(c["freq_hz"] - (CENTER + 50000)) < 6000]
    assert cands, result["candidates"]
    cand = cands[0]
    assert cand["modulation"] == "AM", cand
    assert cand["grade"] in ("A", "B"), cand
    assert cand["audio"], "graded candidates carry a snippet"


def test_fsk_is_data_never_voice():
    n = int(FS * 4)
    t = np.arange(n) / FS
    tones = np.where((t * 8).astype(int) % 2 == 0, 1200.0, 2200.0)
    phase = 2 * np.pi * np.cumsum(tones) / FS
    iq = _noise(n) + 0.5 * np.exp(1j * (2 * np.pi * -60000 * t + phase))
    result = ac.analyze(iq, FS, CENTER)
    cands = [c for c in result["candidates"]
             if abs(c["freq_hz"] - (CENTER - 60000)) < 8000]
    assert cands, result["candidates"]
    assert cands[0]["grade"] == "DATA", cands[0]
    assert cands[0]["audio"] is None


def test_carrier_is_census_log_only():
    n = int(FS * 4)
    t = np.arange(n) / FS
    iq = _noise(n) + 0.5 * np.exp(2j * np.pi * 30000 * t)
    result = ac.analyze(iq, FS, CENTER)
    assert result["carriers"] >= 1
    assert all(c["grade"] in ("D", None)
               for c in result["candidates"])


def test_desk_reports_unknown_audio_honestly(profile):
    feed = {"candidates": [{
        "freq_hz": 121.1e6, "bw_hz": 4200.0, "snr_db": 21.0,
        "modulation": "AM", "grade": "B", "score": 63,
        "duration_s": 4.0, "audio": [0] * 1600,
        "grade_parts": {"band_concentration": 0.8}}],
        "carriers": 0, "over_cap": 0}
    ctx = {"watch_id": "w", "desk": "audiocensus", "t0": 0.0,
           "t1": 10.0, "freq_hz": 121.3e6, "profile": profile,
           "home": (39.0, -98.0), "pass": None}
    events = desks.process_audiocensus(feed, ctx)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "audio"
    assert "unknown audio emission" in ev["content"]
    assert "not decoded" in ev["content"]
    assert ev["data"]["voice_grade"] == "B"
    assert ev["data"]["_snippet"]["kind"] == "voice"
