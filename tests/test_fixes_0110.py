"""0.11.0: the probe desk, the feature-tree classifier, cluster
voting, the tone gate, negative results, and discrimination.

The classifier fixtures are held out from the tuning work by
construction: a different voice pitch and seed, different
carrier offsets, and a 14 dB signal-to-noise ratio where the
tuning smoke ran at 20 dB, so the tests measure
generalization, not memorization. The FSK fixture deviates
plus and minus 4.5 kHz at 1200 baud, the shape of the paging
family the system actually decodes; the FM voice fixture
deviates plus and minus 4 kHz around a 300 Hz residual
offset, the shape of an extracted narrowband FM channel.
"""

import base64
import os

import numpy as np
import pytest

from multidex import __version__, audiocensus, classifier, desks
from multidex import probe as probemod
from multidex import profile as profilemod
from multidex import sigmf
from multidex.events import make_event
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0

RATE = 48000.0
DUR = 1.5
N = int(RATE * DUR)
T = np.arange(N) / RATE


# ------------------------------------------------------- fixtures

def _voice_audio(pitch=118.0, seed=17):
    rng = np.random.default_rng(seed)
    rate = 16000.0
    t = np.arange(int(rate * DUR)) / rate
    env = np.zeros_like(t)
    pos = 0.1
    while pos < DUR - 0.3:
        d = rng.uniform(0.25, 0.7)
        env[int(pos * rate):int((pos + d) * rate)] = 1.0
        pos += d + rng.uniform(0.15, 0.6)
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


def _up(audio, rate=16000.0):
    return np.interp(T, np.arange(len(audio)) / rate, audio)


def _noise(sig, snr_db=14.0, seed=23):
    rng = np.random.default_rng(seed)
    p = np.mean(np.abs(sig) ** 2)
    npow = p / (10 ** (snr_db / 10))
    return sig + np.sqrt(npow / 2) * (
        rng.normal(0, 1, len(sig)) + 1j * rng.normal(0, 1, len(sig)))


def _excerpt(chan, freq_hz=0.0, bw_hz=25000.0):
    data, peak = sigmf.data_bytes(chan)
    return {"b64": base64.b64encode(data).decode("ascii"),
            "rate": RATE, "peak": peak, "samples": len(chan),
            "freq_hz": freq_hz, "bw_hz": bw_hz}


def _family(chan):
    return classifier.classify_excerpt(_excerpt(chan))["family"]


def _seed_hearing(index, t, freq_hz, snr_db=38.0, emitter=None,
                  grade="UNRESOLVED", kind="observation",
                  desk="sweep", watch="watch-seed", data=None):
    d = {"shape": "unknown-pulsed", "unknown_shape": True,
         "snr_db": snr_db, "bw_hz": 25000.0}
    if data:
        d.update(data)
    index.add_event(make_event(
        watch, desk, kind, t, t + 5.0, freq_hz=freq_hz,
        emitter=emitter, grade=grade, content="seeded hearing",
        data=d))


# ------------------------------------------------------- surfaces

def test_version_and_roster(profile):
    assert __version__ == "0.12.1"
    assert "probe" in desks.SPECS
    assert "probe" in desks.PROCESS
    from multidex import applicability
    assert "probe" in applicability.RULES
    state, reason = desks.desk_state("probe", profile,
                                     "simulated", None)
    assert state == "standing", reason
    from multidex.sim import SimSource
    from multidex.tuner import RealSource
    assert "probe" in SimSource.capabilities(profile)
    assert "probe" in RealSource.capabilities(profile)


def test_profile_probe_validation(profile):
    profile["probe"]["dwell_s"] = -5
    with pytest.raises(profilemod.ProfileError):
        profilemod.validate(profile)


# ------------------------------------------------------- tone gate

def test_tone_grades_as_tone_not_voice():
    # The 2026-11-18 catch, rebuilt from its described
    # characteristics: a steady 1010 Hz tone with one
    # harmonic, flat level, no syllables, no spectral
    # movement. It graded voice-likelihood C on the day. It
    # must now grade as a tone before any voice score exists.
    rate = audiocensus.AUDIO_RATE
    t = np.arange(rate * 4) / rate
    audio = 0.35 * np.sin(2 * np.pi * 1010 * t) \
        + 0.08 * np.sin(2 * np.pi * 2025 * t)
    letter, score, parts = audiocensus.grade_audio(
        audio, rate, 18.4, "AM", 1.0)
    assert letter == "TONE"
    assert parts["tone_concentration"] >= 0.85
    assert parts["syllabic_windows"] == 0
    assert parts["tone_freq_hz"] == 1010


def test_voice_fixture_still_grades_as_voice():
    # The correction must not overreach: a genuine
    # voice-shaped signal still grades as voice.
    audio = _voice_audio()
    letter, score, parts = audiocensus.grade_audio(
        audio, 16000.0, 25.0, "AM", 0.6)
    assert letter in ("A", "B", "C")
    assert parts["tone_concentration"] < 0.85


def test_tone_filed_as_tone_by_the_census_desk():
    # End to end through the census: an AM carrier whose
    # modulation is the steady tone is filed as a tone with
    # its frequency and stability stated, never as voice.
    rng = np.random.default_rng(5)
    fs = 240000.0
    n = int(fs * 2.5)
    t = np.arange(n) / fs
    audio_t = np.arange(int(16000 * 2.5)) / 16000.0
    env = 1.0 + 0.8 * np.sin(2 * np.pi * 1010 * audio_t)
    env_up = np.interp(t, audio_t, env)
    iq = env_up * np.exp(2j * np.pi * 3000 * t)
    iq = iq + 0.01 * (rng.normal(0, 1, n)
                      + 1j * rng.normal(0, 1, n))
    out = audiocensus.analyze(iq, fs, 462_000_000.0, 4)
    tones = [c for c in out["candidates"] if c["grade"] == "TONE"]
    assert tones, [c["grade"] for c in out["candidates"]]
    feed = {"t0": T0, "t1": T0 + 2.5, "center_hz": 462_000_000.0,
            "bw_hz": 200000.0, "source": "simulated",
            "candidates": out["candidates"], "excerpts": [],
            "spectrum": None}
    events = desks.process_audiocensus(
        feed, {"watch_id": "watch-test", "t0": T0, "t1": T0 + 2.5})
    tone_events = [e for e in events
                   if e["data"].get("voice_grade") == "TONE"]
    assert tone_events
    ev = tone_events[0]
    assert "steady tone" in ev["content"]
    assert "1010 Hz" in ev["content"]
    assert ev["data"].get("transcribe_candidate") is not True
    assert "_snippet" not in ev["data"]


# ------------------------------------------------------- classifier

def test_classifier_families():
    va = _up(_voice_audio())
    am = _noise((1.0 + 0.85 * va) * np.exp(2j * np.pi * 1700 * T))
    assert _family(am) == "am-voice"
    phase = 2 * np.pi * np.cumsum(va * 4000.0) / RATE
    fm = _noise(np.exp(1j * phase) * np.exp(2j * np.pi * -300 * T))
    assert _family(fm) == "fm-voice"
    spec = np.fft.fft(_voice_audio())
    h = np.zeros(len(spec))
    h[0] = 1.0
    h[1:len(spec) // 2] = 2.0
    h[len(spec) // 2] = 1.0
    analytic = np.fft.ifft(spec * h)
    ssb = _noise(_up(analytic) * np.exp(2j * np.pi * 900 * T))
    assert _family(ssb) == "ssb-voice"
    rng = np.random.default_rng(31)
    bits = rng.integers(0, 2, int(DUR * 1200) + 2)
    fsk_f = np.array([bits[int(t * 1200)] for t in T]) * 9000 - 4500
    phase = 2 * np.pi * np.cumsum(fsk_f) / RATE
    assert _family(_noise(np.exp(1j * phase))) == "fsk-data"
    gate = np.zeros(N)
    tt = 0.1
    for p in [1, 0, 1, 0, 3, 0, 1, 0, 1, 0, 1, 0, 3, 0]:
        if p:
            gate[int(tt * RATE):int((tt + p * 0.06) * RATE)] = 1.0
        tt += (p + 1) * 0.06
    cw = _noise(gate * np.exp(2j * np.pi * -1500 * T))
    assert _family(cw) == "cw"
    assert _family(_noise(np.exp(2j * np.pi * 2100 * T))) == "tone"
    rng = np.random.default_rng(41)
    noise = (rng.normal(0, 0.3, N)
             + 1j * rng.normal(0, 0.3, N)).astype(np.complex128)
    assert _family(noise) == "noise"


def test_classifier_unknown_is_a_first_class_answer():
    # Shapes outside every family the system decodes stay
    # unknown: a sweeping chirp and a four-carrier comb.
    phase = 2 * np.pi * (-8000 * T + 8000 * T * T)
    assert _family(_noise(np.exp(1j * phase))) == classifier.UNKNOWN
    mc = sum(np.exp(2j * np.pi * f * T + 1j * j)
             for j, f in enumerate((-6000, -2000, 2000, 6000)))
    assert _family(_noise(mc / 4)) == classifier.UNKNOWN


# ------------------------------------------------------- voting

def test_vote_margin_and_split():
    # A clear cluster: the family is carried with its margin.
    out = probemod.vote([("fm-voice", 20.0), ("fm-voice", 18.0),
                         ("ssb-voice", 10.0)])
    assert out["family"] == "fm-voice"
    assert out["observations"] == 3
    assert out["split"]["fm-voice"]["count"] == 2
    assert out["share"] >= probemod.VOTE_MARGIN
    # A split cluster stays unresolved with the split shown.
    out = probemod.vote([("fm-voice", 20.0), ("ssb-voice", 19.0)])
    assert out["family"] is None
    assert out["split"]["fm-voice"]["weight"] \
        > out["split"]["ssb-voice"]["weight"]
    # One loud burst does not make a cluster.
    out = probemod.vote([("cw", 30.0)])
    assert out["family"] is None
    # Unknown can lead the split and still cannot win it.
    out = probemod.vote([("unknown", 25.0), ("am-voice", 20.0),
                         ("am-voice", 10.0)])
    assert out["family"] is None
    assert out["leading"] == "unknown"
    # SNR weighting: two quiet agreements outweigh one loud
    # disagreement only when their summed weight says so.
    out = probemod.vote([("fsk-data", 12.0), ("fsk-data", 12.0),
                         ("am-voice", 20.0)])
    assert out["family"] is None  # 31.7 against 100: no


# ------------------------------------------------------- recurrence

def test_recurrence_folds():
    # Twenty hearings exactly on the solar day: the solar
    # fold clusters, the sidereal fold has drifted an hour
    # and a quarter wide and clusters less.
    solar = [T0 + k * 86400.0 for k in range(20)]
    out = probemod.recurrence(solar)
    assert out["solar"]["share"] == 1.0
    assert "solar fold" in out["verdict"]
    # Twenty hearings exactly on the sidereal day.
    sidereal = [T0 + k * 86164.0905 for k in range(20)]
    out = probemod.recurrence(sidereal)
    assert out["sidereal"]["share"] == 1.0
    assert "sidereal fold" in out["verdict"]
    # Scattered hearings cluster on neither clock.
    scattered = [T0 + k * 100000.0 + (k % 7) * 5000.0
                 for k in range(10)]
    out = probemod.recurrence(scattered)
    assert "neither fold clusters" in out["verdict"]


# ------------------------------------------------------- candidates

def test_candidates_from_a_seeded_index(index, profile):
    # A recurring unresolved dossier: chosen.
    for i in range(3):
        _seed_hearing(index, T0 - 10000 + i * 3600, 143.998e6,
                      snr_db=18.0, emitter="AUDIO:143.998 MHz",
                      desk="audiocensus", kind="audio")
    # A recurring unresolved frequency cluster with no
    # dossier: chosen on its frequency.
    for i in range(2):
        _seed_hearing(index, T0 - 8000 + i * 3600, 461.0e6)
    # A one-off unresolved hearing: not chosen.
    _seed_hearing(index, T0 - 5000, 255.4e6)
    # A cluster the record has since decoded: not chosen.
    for i in range(2):
        _seed_hearing(index, T0 - 9000 + i * 3600, 121.5e6,
                      emitter="AUDIO:121.500 MHz", desk="airband",
                      kind="audio")
    _seed_hearing(index, T0 - 1000, 121.5e6,
                  emitter="AUDIO:121.500 MHz", desk="airband",
                  kind="decode", grade="CONFIRMED")
    cands = probemod.candidates(index, profile)
    assert cands[0]["emitter"] == "AUDIO:143.998 MHz"  # most first
    assert all(c["emitter"] != "AUDIO:121.500 MHz" for c in cands)
    assert len(cands) == 2
    freq_cand = [c for c in cands if c["kind"] == "frequency"][0]
    assert abs(freq_cand["freq_hz"] - 461.0e6) < 12500


def test_probe_budget_bounds_the_bill(index, profile):
    bill = [{"id": "b-0001", "start": T0, "end": T0 + 3600,
             "cls": "B", "kind": "rotation", "desks": ["adsb"],
             "label": "seed item", "freq_hz": 1090e6,
             "bw_hz": 2000000.0, "dur": 3600}]
    cands = [{"kind": "frequency",
              "emitter": None, "freq_hz": 100e6 + i * 1e6,
              "bw_hz": 25000.0, "observations": 5,
              "mean_snr_db": 30.0, "uncertainty": 1.0,
              "first_seen": T0 - 9000, "last_seen": T0 - 1000}
             for i in range(6)]
    out, chosen = probemod.plan_items(bill, profile, cands, T0)
    probes = [b for b in out if b["cls"] == "P"]
    assert len(probes) == 4  # the profile count bound
    assert len(chosen) == 4
    assert sum(b["dur"] for b in probes) <= 2400
    # The seconds bound bites on its own.
    profile["probe"]["max_per_watch"] = 6
    profile["probe"]["max_seconds_per_watch"] = 1000
    out, chosen = probemod.plan_items(bill, profile, cands, T0)
    probes = [b for b in out if b["cls"] == "P"]
    assert len(probes) == 1
    # No candidates: the bill is untouched, the budget unspent.
    out, chosen = probemod.plan_items(bill, profile, [], T0)
    assert len(out) == 1 and chosen == []


# ------------------------------------------------------- probe desk

def _narrowband_noise_excerpt():
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1, N) + 1j * rng.normal(0, 1, N)
    spec = np.fft.fft(noise)
    freqs = np.fft.fftfreq(N, 1.0 / RATE)
    spec[np.abs(freqs - 500.0) > 150.0] = 0.0
    chan = np.fft.ifft(spec)
    return _excerpt(chan, freq_hz=131.55e6, bw_hz=1000.0)


def _probe_ctx(history, coincidence=None):
    if coincidence is None:
        coincidence = {"windows_checked": 2, "by_band": {},
                       "examples": [], "window_s": 120.0}
    return {"watch_id": "watch-test", "t0": T0, "t1": T0 + 600,
            "profile": None,
            "probe": {"history": history,
                      "coincidence": coincidence}}


def test_probe_files_negative_results_per_attempted_desk():
    target = {"kind": "frequency",
              "emitter": None, "freq_hz": 131.55e6,
              "bw_hz": 25000.0, "observations": 2,
              "mean_snr_db": 30.0}
    history = [{"t": T0 - 7200, "family": "fsk-data",
                "snr_db": 20.0},
               {"t": T0 - 3600, "family": "fsk-data",
                "snr_db": 18.0}]
    feed = {"target": target, "target_freq_hz": 131.55e6,
            "snr_db": 15.0, "source": "simulated",
            "census": {"candidates": []},
            "excerpt": _narrowband_noise_excerpt(),
            "context": {"capture_s": 2.5, "pre_s": 10.0,
                        "post_s": 20.0,
                        "note": "test context"}}
    events = desks.process_probe(feed, _probe_ctx(history))
    negatives = [e for e in events
                 if e["data"].get("rule") == "probe_negative_result"]
    attempted = {e["data"]["attempted_desk"] for e in negatives}
    # The census heard nothing at the target, the narrowband
    # excerpt carried no CW text, and no ACARS frame decoded:
    # three filed attempts, three negative results.
    assert attempted == {"audiocensus", "amateur", "acars"}
    stare = [e for e in events if e["kind"] == "observation"]
    assert len(stare) == 1
    for e in negatives:
        assert e["grade"] == "CONFIRMED"
        assert e["links"] == [stare[0]["id"]]
    assert "_snippet" in stare[0]["data"]  # the excerpt rides
    assert stare[0]["data"]["context"]["pre_s"] == 10.0
    vote = [e for e in events
            if e["data"].get("rule") == "probe_cluster_vote"]
    assert len(vote) == 1
    assert vote[0]["grade"] == "PROBABLE"
    assert vote[0]["data"]["vote"]["family"] == "fsk-data"


def test_probe_vote_split_stays_unresolved_with_reasons():
    target = {"kind": "frequency",
              "emitter": None, "freq_hz": 461.0e6,
              "bw_hz": 25000.0, "observations": 2,
              "mean_snr_db": 30.0}
    history = [{"t": T0 - 7200, "family": "am-voice",
                "snr_db": 20.0},
               {"t": T0 - 3600, "family": "fsk-data",
                "snr_db": 19.0}]
    mc = sum(np.exp(2j * np.pi * f * T + 1j * j)
             for j, f in enumerate((-6000, -2000, 2000, 6000)))
    feed = {"target": target, "target_freq_hz": 461.0e6,
            "snr_db": 30.0, "source": "simulated",
            "census": None,
            "excerpt": _excerpt(_noise(mc / 4), freq_hz=461.0e6),
            "context": {"capture_s": 2.5, "pre_s": 0.0,
                        "post_s": 0.0, "note": "test context"}}
    events = desks.process_probe(feed, _probe_ctx(history))
    vote = [e for e in events
            if e["data"].get("rule") == "probe_cluster_vote"][0]
    assert vote["grade"] == "UNRESOLVED"
    assert vote["data"]["vote"]["family"] is None
    assert set(vote["data"]["vote"]["split"]) >= {"am-voice",
                                                  "fsk-data"}
    # The families the vote leaves open name the desks no
    # in-process attempt could be filed for.
    not_attempted = {n["desk"] for n in vote["data"]["not_attempted"]}
    assert "pager" in not_attempted and "airband" in not_attempted
    disc = [e for e in events
            if e["data"].get("rule") == "probe_discrimination"]
    assert len(disc) == 1
    assert "recurrence" in disc[0]["data"]
    assert "coincidence" in disc[0]["data"]
    # No census in this feed: no audiocensus attempt is filed.
    negatives = [e for e in events
                 if e["data"].get("rule") == "probe_negative_result"]
    assert negatives == []


# ------------------------------------------------------- coincidence

def test_coincidence_check_counts_other_bands_only(index, profile):
    for i in range(2):
        _seed_hearing(index, T0 - 7200 + i * 3600, 461.0e6)
    # Another band firing inside a hearing minute: counted.
    index.add_event(make_event(
        "watch-seed", "adsb", "telemetry", T0 - 7200 + 60,
        T0 - 7200 + 65, freq_hz=1090e6, emitter="A9B2E9",
        grade="CONFIRMED", content="an aircraft", data={}))
    # The same band at a different frequency: not a
    # cross-band coincidence.
    index.add_event(make_event(
        "watch-seed", "sweep", "observation", T0 - 3600 + 30,
        T0 - 3600 + 35, freq_hz=462.5e6, grade="UNRESOLVED",
        content="another UHF hearing",
        data={"shape": "unknown-wideband", "unknown_shape": True}))
    cands = probemod.candidates(index, profile)
    target = [c for c in cands if c["kind"] == "frequency"
              and abs(c["freq_hz"] - 461.0e6) < 12500][0]
    ctx = probemod.execution_context(index, target)
    coin = ctx["coincidence"]
    assert coin["windows_checked"] == 2
    assert sum(coin["by_band"].values()) == 1


# ------------------------------------------------------- end to end

def test_simulated_watch_probe_finds_the_planted_unknown(
        home, profile):
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 2
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    index = Index(home)
    try:
        # The station's record already holds the planted
        # unknown twice, unexplained: the probe's candidates.
        for i in range(2):
            _seed_hearing(index, T0 - 7200 + i * 3600, 461.0e6)
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        watch_id = result["watch_id"]
        import json
        bill = json.load(open(os.path.join(
            home, "watches", watch_id, "bill.json")))
        probes = [b for b in bill if b["cls"] == "P"]
        assert len(probes) == 1
        assert probes[0]["desks"] == ["probe"]
        evs = index.events(watch_id=watch_id, desk="probe",
                           limit=500)
        kinds = {e["kind"] for e in evs}
        assert "observation" in kinds
        rules = {e["data"].get("rule") for e in evs
                 if e["kind"] == "assessment"}
        assert "probe_cluster_vote" in rules
        assert "probe_discrimination" in rules
        stare = [e for e in evs if e["kind"] == "observation"][0]
        assert stare["data"]["context"]["capture_s"] > 0
        assert "Context:" in stare["content"]
        rows = index.snippet_rows(watch_id)
        assert any(r["desk"] == "probe" and r["kind"] == "iq"
                   for r in rows)
        report = open(os.path.join(
            home, "watches", watch_id, "REPORT.md")).read()
        assert "probe cluster vote" in report
    finally:
        index.close()
