"""The shimmed-real integration test: fix 1 proven end to end.

Fake rtl_sdr / rtl_fm / rtl_power / rtl_433 / multimon-ng
executables on PATH emit fixture bytes - genuine Mode S frames with
a known ICAO, a genuine AIS frame, a SAME header, POCSAG and APRS
decodes, rtl_433 JSON, and an AM audio burst. A short real-source
watch then runs through the real CLI. It must muster on the real
source with no fallback note, stand the desks 0.1.0 benched on a
real source, decode the fixture aircraft into a dossier, and file
airband evidence from the burst.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

import rfixture
from multidex.index import Index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FED_DESKS = {"adsb", "ais", "aprs", "broadcast", "cellular", "ism",
             "pager", "satellite", "sonde", "trunking", "wxradio"}
STILL_BENCHED = {"acars", "amateur", "digitalvoice", "hfdl", "iss",
                 "vdl2"}


def _profile_file(tmp_path):
    text = open(os.path.join(ROOT, "profiles",
                             "default.toml")).read()
    # Two hours, and no deep-sweep window: the bill's composition
    # must not depend on the wall-clock hour the suite happens to
    # run at. A one-hour bill started inside the deep-sweep window
    # (or beside the sonde windows) can hold no pager dwell at all,
    # which made this test fail or pass by the clock.
    text = text.replace("hours = 24", "hours = 2")
    text = text.replace("deep_sweep_start = 1", "deep_sweep_start = 0")
    text = text.replace("deep_sweep_end = 4", "deep_sweep_end = 0")
    text = text.replace("sitrep_hours = 6", "sitrep_hours = 1")
    text = text.replace("disk_ceiling_gb = 20", "disk_ceiling_gb = 0.05")
    text = text.replace("time_scale = 1.0", "time_scale = 100000.0")
    path = tmp_path / "real.toml"
    path.write_text(text)
    shutil.copyfile(os.path.join(ROOT, "profiles", "tle.txt"),
                    str(tmp_path / "tle.txt"))
    return str(path)


@pytest.mark.skipif(shutil.which("rtl_sdr") is not None,
                    reason="this host has a real rtl_sdr; the shim "
                           "PATH would not be the one under test")
def test_shimmed_real_watch(tmp_path):
    shims = rfixture.write_shims(str(tmp_path / "bin"))
    home = str(tmp_path / "mdhome")
    env = dict(os.environ)
    env["PATH"] = shims + os.pathsep + env.get("PATH", "")
    env.pop("MULTIDEX_TIME_SCALE", None)
    done = subprocess.run(
        [sys.executable, "-m", "multidex", "--home", home,
         "--profile", _profile_file(tmp_path), "watch"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=900)
    assert done.returncode == 0, done.stdout + done.stderr

    watches = os.listdir(os.path.join(home, "watches"))
    assert len(watches) == 1
    watch_dir = os.path.join(home, "watches", watches[0])
    meta = json.load(open(os.path.join(watch_dir, "watch.json")))
    # Mustered on the real source: no fallback, no substitution.
    assert meta["source"] == "real"
    assert not meta.get("source_note")

    events = [json.loads(line) for line in
              open(os.path.join(watch_dir, "events.jsonl"))]
    health = [e for e in events
              if e["kind"] == "desk_health"
              and e["data"].get("state") == "benched"]
    benched = {e["desk"] for e in health}
    # Every desk the host tool set can feed stood at muster; only
    # the desks needing an absent external decoder stayed benched.
    assert not (benched & FED_DESKS), benched
    assert benched <= STILL_BENCHED, benched
    for e in health:
        if e["desk"] == "acars":
            assert "acarsdec" in e["data"]["reason"]

    # The fixture aircraft was decoded off the real capture and
    # correlated into a dossier.
    index = Index(home)
    try:
        dossier = index.dossier(rfixture.FIXTURE_ICAO)
        vessel = index.dossier(rfixture.FIXTURE_MMSI)
    finally:
        index.close()
    assert dossier is not None
    assert vessel is not None
    adsb_kinds = {e["kind"] for e in events
                  if e.get("emitter") == rfixture.FIXTURE_ICAO}
    assert "decode" in adsb_kinds and "telemetry" in adsb_kinds

    # Airband evidence: a voice event whose snippet is real audio.
    voices = [e for e in events if e["desk"] == "airband"
              and e["kind"] == "voice"]
    assert voices, "the AM burst filed no airband voice event"
    assert any(e.get("snippet") for e in voices), \
        "no airband snippet was filed from the burst"

    # The other fed desks left their marks too.
    seen = {(e["desk"], e["kind"]) for e in events}
    assert ("ais", "telemetry") in seen
    assert ("pager", "decode") in seen
    assert ("aprs", "telemetry") in seen
    assert ("ism", "decode") in seen
    assert ("wxradio", "alert") in seen

    # The full product set exists, and the report's coverage
    # account names the real source.
    names = os.listdir(watch_dir)
    assert any(n.startswith("FLASH") for n in names)
    report = open(os.path.join(watch_dir, "REPORT.md")).read()
    assert "Source: real receiver" in report
    assert "could not be stood" not in report
