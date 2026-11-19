"""0.12.0: retroactive exploitation - the capability manifest,
the processed ledger, the bounded pass, retroactive filing,
idempotence and scope discipline, and the three production
decoders wired through the mechanism for real.

The fixture story the mechanism tests tell: an archive holding
a capture filed as an unknown before its decoder existed; a
decoder version lands; the pass finds it, files the
retroactive decode linked to the original, and the dossier
resolves; a second pass files nothing; a different decoder's
bump does not re-examine the first decoder's captures; a
budget cut mid-way resumes with exactly the unexamined
remainder owed; a shed capture is not owed.
"""

import base64
import io
import os
import wave

import numpy as np
import pytest

from multidex import __version__, acars, cli, desks, gates
from multidex import products, profile as profilemod, retro, sigmf
from multidex import util
from multidex.events import make_event
from multidex.index import Index
from multidex.officer import WatchRunner

from conftest import T0

CEILING = int(1e9)
RATE = 48000.0


# ------------------------------------------------------------ seeding

def _keep(index, watch, desk, kind, filename, data, t):
    rel, shed = index.keep_snippet(
        watch, index.watch_dir(watch), desk, kind, filename, data,
        CEILING, t=t, home_wide=True)
    assert rel, "seed snippet refused"
    return rel


def _row_id(index, watch, path):
    rows = [r for r in index.snippet_rows(watch) if r["path"] == path]
    assert len(rows) == 1
    return rows[0]["id"]


def _seed_hearing_with_voice(index, watch, t, emitter, payload,
                             freq_hz=100.0e6):
    """An UNRESOLVED hearing and the voice snippet it filed:
    a capture waiting for a decoder that does not exist yet."""
    filename = "%s-bill-001-voice-%d.wav" % (watch, int(t))
    rel = _keep(index, watch, "sweep", "voice", filename, payload, t)
    ev = make_event(
        watch, "sweep", "observation", t, t + 5.0, freq_hz=freq_hz,
        emitter=emitter, grade="UNRESOLVED",
        content="seeded unknown hearing", data={"shape": "unknown"},
        snippet=rel)
    index.add_event(ev)
    return ev, rel


@pytest.fixture()
def register():
    """Register test decoders through the manifest's own
    registration path, and take them back out afterwards."""
    made = []

    def _reg(name, desk, version, kinds, run, **kw):
        entry = retro.register(name, desk, version, kinds, run,
                               **kw)
        made.append(entry)
        return entry

    yield _reg
    for entry in made:
        retro.DECODERS.remove(entry)


def _magic_run(ctx):
    if not ctx["data"].startswith(b"MAGIC"):
        return []
    ev0 = ctx["event"]
    return [make_event(
        ctx["watch_id"], "sweep", "decode", ev0["t_start"],
        ev0["t_end"], freq_hz=ev0.get("freq_hz"),
        emitter=ev0.get("emitter"), grade="CONFIRMED",
        content="the test decoder found the magic pattern",
        data={"emitter_kind": "test_signal"})]


# ------------------------------------------------------------- surfaces

def test_version_and_manifest():
    assert __version__ == "0.16.0"
    entries = retro.manifest()
    assert [e["name"] for e in entries] == [
        "apt-imagery", "excerpt-classifier", "acars-excerpt"]
    known_kinds = {"payload", "spectrum", "image", "voice", "iq",
                   "satellite"}
    for e in entries:
        assert e["desk"] in desks.SPECS
        assert isinstance(e["version"], str) and e["version"]
        assert set(e["kinds"]) <= known_kinds
        assert callable(e["run"])
    lines = retro.manifest_lines()
    assert len(lines) == 3
    for e in entries:
        assert any(e["name"] in line for line in lines)


def test_profile_retro_validation(profile):
    profile["retro"]["max_captures_per_watch"] = 0
    with pytest.raises(profilemod.ProfileError):
        profilemod.validate(profile)


def test_ledger_persists_across_reopen(home, index):
    ledger = retro.ProcessedLedger.load(home)
    ledger.record("apt-imagery", "1", "sn-aaa", retro.FOUND, T0,
                  events=["ev-1"])
    ledger.record("apt-imagery", "1", "sn-bbb", retro.NOTHING, T0)
    again = retro.ProcessedLedger.load(home)
    assert again.examined_ids("apt-imagery", "1") == {"sn-aaa",
                                                      "sn-bbb"}
    assert again.counts("apt-imagery", "1") == {retro.FOUND: 1,
                                                retro.NOTHING: 1}
    assert again.found_event_ids("apt-imagery", "1") == ["ev-1"]
    # A new Index on the same home (an officer restart) reads
    # the same ledger: the file is the memory, not the process.
    index.close()
    idx2 = Index(home)
    try:
        rp = retro.RetroPass(idx2, home, decoders=[])
        assert rp.ledger.examined_ids("apt-imagery", "1") == {
            "sn-aaa", "sn-bbb"}
    finally:
        idx2.close()


# ------------------------------------------------------- fixture story

def test_fixture_story(index, home, register):
    entry = register("test-magic", "sweep", "1", ("voice",),
                     _magic_run)
    ev0, rel = _seed_hearing_with_voice(
        index, "watch-old", T0, "TEST:1", b"MAGIC pattern bytes")
    row_id = _row_id(index, "watch-old", rel)
    assert index.dossier("TEST:1")["events_count"] == 1
    rp = retro.RetroPass(index, home, decoders=[entry])
    plan = rp.plan(T0 + 100)
    assert plan == {"owed": 1, "seeded": 0}
    account = rp.step(T0 + 100)
    assert account["examined"] == 1 and account["found"] == 1
    assert account["owed_after"] == 0
    decodes = index.events(watch_id="watch-old", kinds=["decode"])
    assert len(decodes) == 1
    dec = decodes[0]
    assert dec["grade"] == "CONFIRMED"
    assert dec["emitter"] == "TEST:1"
    assert dec["links"] == [ev0["id"]]
    data = dec["data"]
    assert data["retroactive"] is True
    assert data["decoder"] == "test-magic"
    assert data["decoder_version"] == "1"
    assert data["source_snippet"] == row_id
    assert data["source_event"] == ev0["id"]
    assert data["capture_t"] == T0
    assert data["found_at"] == T0 + 100
    # The dossier resolves through the normal grading path,
    # and the original event is untouched.
    assert index.dossier("TEST:1")["events_count"] == 2
    original = [e for e in index.events(watch_id="watch-old")
                if e["id"] == ev0["id"]][0]
    assert original["grade"] == "UNRESOLVED"
    assert "retroactive" not in original["data"]


def test_second_pass_files_nothing(index, home, register):
    entry = register("test-magic", "sweep", "1", ("voice",),
                     _magic_run)
    _seed_hearing_with_voice(index, "watch-old", T0, "TEST:1",
                             b"MAGIC one")
    rp = retro.RetroPass(index, home, decoders=[entry])
    rp.step(T0 + 100)
    assert index.count_events(watch_id="watch-old") == 2
    # A fresh pass over the unchanged manifest - a new officer,
    # a new watch - examines nothing and files nothing.
    rp2 = retro.RetroPass(index, home, decoders=[entry])
    plan = rp2.plan(T0 + 200)
    assert plan == {"owed": 0, "seeded": 0}
    account = rp2.step(T0 + 200)
    assert account["examined"] == 0 and account["found"] == 0
    assert index.count_events(watch_id="watch-old") == 2


def test_version_bump_reruns_only_the_bumped_decoder(
        index, home, register):
    magic = register("test-magic", "sweep", "1", ("voice",),
                     _magic_run)

    def other_run(ctx):
        if not ctx["data"].startswith(b"OTHER"):
            return []
        ev0 = ctx["event"]
        return [make_event(
            ctx["watch_id"], "sweep", "decode", ev0["t_start"],
            ev0["t_end"], emitter=ev0.get("emitter"),
            grade="CONFIRMED", content="the other decoder fired",
            data={"emitter_kind": "test_signal"})]

    other1 = register("test-other", "sweep", "1", ("voice",),
                      other_run)
    _seed_hearing_with_voice(index, "watch-old", T0, "TEST:1",
                             b"MAGIC one")
    _seed_hearing_with_voice(index, "watch-old", T0 + 10, "TEST:2",
                             b"OTHER two")
    rp = retro.RetroPass(index, home, decoders=[magic, other1])
    account = rp.step(T0 + 100)
    assert account["found"] == 2
    # The other decoder's version lands; the magic decoder is
    # unchanged. Only the bumped decoder re-examines, and it
    # re-runs over its full matching archive - both captures
    # match its kinds, so both are examined again, and its new
    # find files again under the new version.
    other2 = register("test-other", "sweep", "2", ("voice",),
                      other_run)
    rp2 = retro.RetroPass(index, home, decoders=[magic, other2])
    plan = rp2.plan(T0 + 200)
    assert plan["owed"] == 2
    account2 = rp2.step(T0 + 200)
    assert set(account2["per_decoder"]) == {"test-other"}
    assert account2["found"] == 1
    decodes = index.events(watch_id="watch-old", kinds=["decode"])
    assert len(decodes) == 3
    versions = {(d["data"]["decoder"], d["data"]["decoder_version"])
                for d in decodes}
    assert versions == {("test-magic", "1"), ("test-other", "1"),
                        ("test-other", "2")}


def test_budget_cut_resumes_with_exact_remainder(
        index, home, register):
    entry = register("test-magic", "sweep", "1", ("voice",),
                     _magic_run)
    for i in range(3):
        _seed_hearing_with_voice(index, "watch-old", T0 + i * 10,
                                 "TEST:%d" % i, b"MAGIC %d" % i)
    rp = retro.RetroPass(index, home, decoders=[entry])
    first = rp.step(T0 + 100, max_captures=1)
    assert first["examined"] == 1 and first["owed_after"] == 2
    # The next watch replans from the ledger: exactly the
    # unexamined remainder is owed, nothing already done is
    # repeated.
    rp2 = retro.RetroPass(index, home, decoders=[entry])
    assert rp2.plan(T0 + 200)["owed"] == 2
    second = rp2.step(T0 + 200, max_captures=1)
    assert second["examined"] == 1 and second["owed_after"] == 1
    rp3 = retro.RetroPass(index, home, decoders=[entry])
    third = rp3.step(T0 + 300)
    assert third["examined"] == 1 and third["owed_after"] == 0
    decodes = index.events(watch_id="watch-old", kinds=["decode"])
    assert len(decodes) == 3
    assert {d["emitter"] for d in decodes} == {"TEST:0", "TEST:1",
                                               "TEST:2"}


def test_shed_capture_is_not_owed(index, home, register):
    entry = register("test-magic", "sweep", "1", ("voice",),
                     _magic_run)
    _ev, rel = _seed_hearing_with_voice(
        index, "watch-old", T0, "TEST:1", b"MAGIC gone")
    row_id = _row_id(index, "watch-old", rel)
    work, _seed = retro.owed_captures(
        index, retro.ProcessedLedger.load(home), [entry])
    assert len(work["test-magic"]) == 1
    # The retention tiers shed the capture: the row stands shed
    # and the file is gone, exactly the state keep_snippet
    # leaves behind.
    index.db.execute("UPDATE snippets SET shed=1 WHERE id=?",
                     (row_id,))
    index.db.commit()
    os.remove(os.path.join(home, "watches", "watch-old", rel))
    work, _seed = retro.owed_captures(
        index, retro.ProcessedLedger.load(home), [entry])
    assert work.get("test-magic", []) == []
    rp = retro.RetroPass(index, home, decoders=[entry])
    assert rp.plan(T0 + 100)["owed"] == 0


def test_narrowing_registration_unfiles_nothing(
        index, home, register):
    wide = register("test-magic", "sweep", "1", ("voice",),
                    _magic_run)
    _seed_hearing_with_voice(index, "watch-old", T0, "TEST:1",
                             b"MAGIC one")
    retro.RetroPass(index, home, decoders=[wide]).step(T0 + 100)
    assert index.count_events(watch_id="watch-old",
                              kind="decode") == 1
    # The decoder's registration narrows to a kind nothing is
    # filed under: the debt vanishes, and everything already
    # filed stands.
    narrow = register("test-magic", "sweep", "2", ("spectrum",),
                      _magic_run)
    rp = retro.RetroPass(index, home, decoders=[narrow])
    assert rp.plan(T0 + 200)["owed"] == 0
    assert index.count_events(watch_id="watch-old",
                              kind="decode") == 1


# ------------------------------------------------- production: APT

_WORDS = 4160
_BLACK, _WHITE = 0.13, 0.95


def _apt_sync(n_words, freq):
    half = _WORDS / (2.0 * freq)
    out = np.empty(n_words)
    pos = 0.0
    for i in range(n_words):
        out[i] = _WHITE if (pos % (2 * half)) < half else 0.05
        pos += 1.0
    return out


def _apt_line(row):
    img_a = _BLACK + (_WHITE - _BLACK) * np.linspace(0, 1, 909)
    img_b = img_a[::-1].copy()
    if 8 <= row < 16:
        img_a[400:520] = _WHITE
        img_b[400:520] = _WHITE
    return np.concatenate([
        _apt_sync(39, 1040), np.full(47, _BLACK), img_a,
        np.full(45, 0.5), _apt_sync(39, 832), np.full(47, _BLACK),
        img_b, np.full(45, 0.5)])


def _apt_wav_bytes(seconds=8, rate=48000):
    env = np.concatenate([_apt_line(r) for r in range(seconds * 2)])
    t_env = np.arange(len(env)) / _WORDS
    t_out = np.arange(int(seconds * rate)) / rate
    env_up = np.interp(t_out, t_env, env)
    audio = env_up * np.cos(2 * np.pi * 2400 * t_out)
    samples = (np.clip(audio * 0.9, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _seed_pass(index, watch, t, sat="NOAA 19", norad=25338,
               freq=137.1e6, imagery=None, wav=None):
    """A satellite pass as the desk files it: the pass audio
    whole, and imagery only when the live decode produced it."""
    rel = None
    if wav is not None:
        rel = _keep(index, watch, "satellite", "satellite",
                    "%s-bill-007-pass.wav" % watch, wav, t)
    ev = make_event(
        watch, "satellite", "pass", t, t + 600, freq_hz=freq,
        emitter=sat, grade="CONFIRMED",
        content="pass captured; APT imagery not decoded"
                if imagery is None else "pass captured; imagery "
                                         "decoded",
        data={"emitter_kind": "satellite", "sat": sat,
              "norad": norad, "imagery": imagery,
              "audio": "pass.wav"},
        snippet=rel)
    index.add_event(ev)
    return ev, rel


def test_apt_decoder_finds_imagery_in_old_pass_audio(index, home):
    wav = _apt_wav_bytes()
    ev0, rel = _seed_pass(index, "watch-old", T0, wav=wav)
    row_id = _row_id(index, "watch-old", rel)
    rp = retro.RetroPass(index, home)
    account = rp.step(T0 + 3600)
    assert account["per_decoder"]["apt-imagery"]["found"] == 1
    imagery = index.events(watch_id="watch-old", kinds=["imagery"])
    assert len(imagery) == 1
    ev = imagery[0]
    assert ev["desk"] == "satellite"
    assert ev["grade"] == "CONFIRMED"
    assert ev["emitter"] == "NOAA 19"
    assert ev["links"] == [ev0["id"]]
    data = ev["data"]
    assert data["retroactive"] is True
    assert data["decoder"] == "apt-imagery"
    assert data["decoder_version"] == "1"
    assert data["source_snippet"] == row_id
    assert data["source_event"] == ev0["id"]
    assert data["capture_t"] == T0
    assert data["imagery"].endswith(".png")
    # The decoded image stands in the store as evidence, a PNG
    # by its signature, named by the event's snippet.
    assert ev["snippet"]
    png_path = os.path.join(home, "watches", "watch-old",
                            ev["snippet"])
    with open(png_path, "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
    rows = [r for r in index.snippet_rows("watch-old")
            if r["path"] == ev["snippet"]]
    assert len(rows) == 1 and rows[0]["shed"] == 0
    # The original pass event is never modified.
    original = [e for e in index.events(watch_id="watch-old")
                if e["id"] == ev0["id"]][0]
    assert original["data"]["imagery"] is None
    # And the debt is paid: a second pass files nothing.
    account2 = retro.RetroPass(index, home).step(T0 + 7200)
    assert account2["examined"] == 0
    assert len(index.events(watch_id="watch-old",
                            kinds=["imagery"])) == 1


def test_apt_already_decoded_pass_is_seeded_not_owed(index, home):
    wav = _apt_wav_bytes()
    _seed_pass(index, "watch-old", T0, sat="NOAA 15", norad=25338,
               freq=137.62e6, imagery="pass-25338-1.png", wav=wav)
    rp = retro.RetroPass(index, home)
    plan = rp.plan(T0 + 3600)
    assert plan["owed"] == 0
    assert plan["seeded"] == 1
    account = rp.step(T0 + 3600)
    assert account["examined"] == 0
    assert index.events(watch_id="watch-old",
                        kinds=["imagery"]) == []
    counts = rp.ledger.counts("apt-imagery", "1")
    assert counts.get(retro.ALREADY) == 1


# --------------------------------------- production: classifier, ACARS

def _seed_excerpt(index, watch, t, chan, freq_hz, desk="audiocensus",
                  emitter=None, grade="UNRESOLVED", data=None,
                  snippet_which="data"):
    """A retained SigMF excerpt and the hearing that filed it,
    in the store's own shape: the pair under one event, whose
    headline snippet is the data file (or, for a probe stare,
    the metadata file, as the officer's filing leaves it)."""
    stem = "%s-%d" % (desk, int(freq_hz))
    pair = sigmf.pair_payloads(stem, chan, RATE, freq_hz, t,
                               label="unclassified emission")
    rels = []
    for payload in pair:
        rel = _keep(index, watch, desk, "iq",
                    "%s-bill-003-%s" % (watch, payload["filename"]),
                    base64.b64decode(payload["b64"]), t)
        rels.append(rel)
    ev_data = {"emitter_kind": "audio_emission",
               "iq_excerpt": stem + ".sigmf-data"}
    if data:
        ev_data.update(data)
    ev = make_event(
        watch, desk, "observation", t, t + 1.5, freq_hz=freq_hz,
        emitter=emitter, grade=grade, content="seeded hearing",
        data=ev_data,
        snippet=rels[0] if snippet_which == "data" else rels[1])
    index.add_event(ev)
    return ev, rels


def _tone_chan(seed=23):
    t = np.arange(int(RATE * 1.5)) / RATE
    sig = np.exp(2j * np.pi * 2100 * t)
    rng = np.random.default_rng(seed)
    power = np.mean(np.abs(sig) ** 2)
    npow = power / (10 ** (14.0 / 10))
    return sig + np.sqrt(npow / 2) * (
        rng.normal(0, 1, len(sig)) + 1j * rng.normal(0, 1, len(sig)))


def _comb_chan(seed=41):
    t = np.arange(int(RATE * 1.5)) / RATE
    mc = sum(np.exp(2j * np.pi * f * t + 1j * j)
             for j, f in enumerate((-6000, -2000, 2000, 6000)))
    rng = np.random.default_rng(seed)
    sig = mc / 4
    power = np.mean(np.abs(sig) ** 2)
    npow = power / (10 ** (14.0 / 10))
    return sig + np.sqrt(npow / 2) * (
        rng.normal(0, 1, len(sig)) + 1j * rng.normal(0, 1, len(sig)))


def test_classifier_assessment_from_retained_excerpt(index, home):
    ev0, rels = _seed_excerpt(index, "watch-old", T0, _tone_chan(),
                              143.998e6, emitter="AUDIO:143.998 MHz")
    row_id = _row_id(index, "watch-old", rels[0])
    rp = retro.RetroPass(index, home)
    account = rp.step(T0 + 3600)
    assert account["per_decoder"]["excerpt-classifier"]["found"] \
        == 1
    found = [e for e in index.events(watch_id="watch-old",
                                     kinds=["assessment"])
             if e["data"].get("rule") == "retroactive_classification"]
    assert len(found) == 1
    ev = found[0]
    assert ev["desk"] == "probe"
    assert ev["grade"] == "PROBABLE"
    assert ev["links"] == [ev0["id"]]
    assert ev["data"]["classification"]["family"] == "tone"
    assert ev["data"]["decoder"] == "excerpt-classifier"
    assert ev["data"]["source_snippet"] == row_id


def test_classifier_unknown_excerpt_files_nothing(index, home):
    _seed_excerpt(index, "watch-old", T0, _comb_chan(), 50.5e6)
    rp = retro.RetroPass(index, home)
    account = rp.step(T0 + 3600)
    cell = account["per_decoder"]["excerpt-classifier"]
    assert cell["examined"] == 1 and cell["found"] == 0
    assert [e for e in index.events(watch_id="watch-old")
            if e["data"].get("rule")
            == "retroactive_classification"] == []
    # Examined is remembered: the unknown is not re-asked.
    assert retro.RetroPass(index, home).plan(T0 + 7200)["owed"] \
        == 0


def _acars_chan(seed=3):
    frame = acars.build_frame_bytes(
        "2", "N12345", " ", "H1", "A",
        "001UA12 OOOI 1234 FUEL 9200 KG")
    audio = acars.bits_to_audio(acars.bytes_to_bits(frame), 48000)
    rng = np.random.default_rng(seed)
    n = 48000
    chan = (rng.normal(0, 0.02, n)
            + 1j * rng.normal(0, 0.02, n)).astype(complex)
    chan[10000:10000 + len(audio)] += (1.0 + 0.7 * audio)
    return chan


def test_acars_decode_from_retained_excerpt(index, home):
    ev0, rels = _seed_excerpt(index, "watch-old", T0, _acars_chan(),
                              131.55e6)
    rp = retro.RetroPass(index, home)
    account = rp.step(T0 + 3600)
    assert account["per_decoder"]["acars-excerpt"]["found"] == 1
    decodes = [e for e in index.events(watch_id="watch-old",
                                       kinds=["decode"])
               if e["desk"] == "acars"]
    assert len(decodes) == 1
    ev = decodes[0]
    assert ev["emitter"] == "N12345"
    assert ev["grade"] == "CONFIRMED"
    assert "FUEL 9200 KG" in ev["content"]
    assert ev["links"] == [ev0["id"]]
    assert ev["data"]["decoder"] == "acars-excerpt"
    assert ev["data"]["flight"] == "UA12"


def test_probe_linked_attempt_files_negative_result(index, home):
    # A probe stare at an ACARS channel: its excerpt is narrow
    # noise with no frame in it. The probe already classified
    # the stare (the classifier seeds it as already exploited),
    # and the ACARS decoder's retroactive attempt finds
    # nothing - which, on a probe target, files the negative
    # result in the probe's own shape.
    rng = np.random.default_rng(7)
    n = int(RATE * 1.5)
    noise = rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)
    spec = np.fft.fft(noise)
    freqs = np.fft.fftfreq(n, 1.0 / RATE)
    spec[np.abs(freqs - 500.0) > 150.0] = 0.0
    chan = np.fft.ifft(spec)
    ev0, rels = _seed_excerpt(
        index, "watch-old", T0, chan, 131.55e6, desk="probe",
        data={"emitter_kind": "probe_target",
              "classification": {"family": "fsk-data",
                                 "features": {}, "reasons": []}},
        snippet_which="meta")
    rp = retro.RetroPass(index, home)
    plan = rp.plan(T0 + 3600)
    assert plan["seeded"] == 1  # the classifier's seeding
    account = rp.step(T0 + 3600)
    assert account["per_decoder"]["acars-excerpt"]["examined"] \
        == 1
    negatives = [e for e in index.events(watch_id="watch-old",
                                         kinds=["assessment"])
                 if e["data"].get("rule")
                 == "probe_negative_result"]
    assert len(negatives) == 1
    neg = negatives[0]
    assert neg["desk"] == "probe"
    assert neg["grade"] == "CONFIRMED"
    assert neg["links"] == [ev0["id"]]
    assert neg["data"]["attempted_desk"] == "acars"
    assert neg["data"]["decoder"] == "acars-excerpt"
    assert "negative result" in neg["content"]


# ------------------------------------------------------------ surfaces

def test_doctor_states_manifest_and_account(profile, home, index):
    _seed_pass(index, "watch-old", T0, wav=_apt_wav_bytes())
    text = gates.doctor_text(profile, home)
    assert "Retroactive exploitation" in text
    for name in ("apt-imagery", "excerpt-classifier",
                 "acars-excerpt"):
        assert name in text
    assert "Retroactive account, apt-imagery version 1" in text
    assert "1 captures stand owed to it" in text


def test_status_states_the_owed_debt(home, index):
    _seed_pass(index, "watch-old", T0, wav=_apt_wav_bytes())
    # The seeded watch stands complete, so status speaks of
    # the home: the debt is stated with no watch running.
    util.write_json(os.path.join(home, "watches", "watch-old",
                                 "state.json"),
                    {"complete": True, "pid": 99999999})
    text = products.status_text(home)
    assert "No watch is running" in text
    assert "Retroactive exploitation" in text
    assert "stand owed" in text


def test_reprocess_verb_runs_the_debt_off(home, index, capsys):
    ev0, _rel = _seed_pass(index, "watch-old", T0,
                           wav=_apt_wav_bytes())
    util.write_json(os.path.join(home, "watches", "watch-old",
                                 "state.json"),
                    {"complete": True, "pid": 99999999})
    index.close()
    rc = cli.main(["--home", home, "reprocess"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "apt-imagery" in out
    idx2 = Index(home)
    try:
        imagery = idx2.events(watch_id="watch-old",
                              kinds=["imagery"])
        assert len(imagery) == 1
        assert imagery[0]["data"]["decoder"] == "apt-imagery"
        assert retro.owed_counts(idx2) == {}
    finally:
        idx2.close()


def test_reprocess_verb_refuses_while_a_watch_stands(
        home, index, capsys):
    _seed_pass(index, "watch-old", T0, wav=_apt_wav_bytes())
    wid = "watch-live"
    index.watch_dir(wid)
    index.set_watch_meta(wid, {"watch_id": wid, "start": T0,
                               "planned_end": T0 + 86400,
                               "source": "simulated"})
    util.write_json(os.path.join(home, "current_watch"), wid)
    util.write_json(os.path.join(home, "watches", wid,
                                 "state.json"),
                    {"pid": os.getpid(), "complete": False})
    index.close()
    rc = cli.main(["--home", home, "reprocess"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "one-writer rule" in out
    idx2 = Index(home)
    try:
        assert idx2.events(watch_id="watch-old",
                           kinds=["imagery"]) == []
    finally:
        idx2.close()


# ------------------------------------------------------------- muster

def test_muster_pass_files_summary_and_report_account(
        home, profile):
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 2
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    index = Index(home)
    try:
        _seed_pass(index, "watch-old", T0 - 86400,
                   wav=_apt_wav_bytes())
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        watch_id = result["watch_id"]
        # The muster pass found the imagery in the old pass
        # audio and filed it under the watch that filed the
        # capture.
        imagery = index.events(watch_id="watch-old",
                               kinds=["imagery"])
        assert len(imagery) == 1
        assert imagery[0]["data"]["decoder"] == "apt-imagery"
        # The standing watch carries the pass's summary.
        summaries = [e for e in index.events(watch_id=watch_id,
                                             kinds=["system"])
                     if e["data"].get("retroactive_account")]
        assert summaries
        assert summaries[0]["data"]["retroactive_account"][
            "examined"] >= 1
        assert os.path.exists(os.path.join(home,
                                           "retro-ledger.json"))
        report = open(os.path.join(
            home, "watches", watch_id, "REPORT.md")).read()
        assert "Retroactive account" in report
        # A second watch files nothing more under the old
        # watch and the APT debt stays paid. The second watch
        # may owe work of its own - the first watch's retained
        # excerpts are new archive to it - and that is the
        # mechanism working, not a repeat.
        runner2 = WatchRunner(home, profile, index)
        result2 = runner2.start(start_epoch=T0 + 7200)
        assert result2["ok"], result2
        assert len(index.events(watch_id="watch-old",
                                kinds=["imagery"])) == 1
        assert "apt-imagery" not in retro.owed_counts(index)
    finally:
        index.close()
