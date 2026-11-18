"""0.7.0: the keeper, exercised end to end as a subprocess.

A real generation (this tree, materialised) stands a short
simulated watch under the keeper; a staged activation hands it
over to a stub generation that dies at once; the keeper must
roll back - snapshot restored, pointer flipped, generation
marked bad, failure recorded - and the real generation resumes
and completes the watch.
"""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from multidex import updater, util

BUILD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_A = "a" * 40
GEN_B = "b" * 40


def _stage_tree(root, gen, stub=False):
    gen_dir = os.path.join(root, "releases", gen)
    if stub:
        os.makedirs(os.path.join(gen_dir, "bin"))
        with open(os.path.join(gen_dir, "bin", "multidex"),
                  "w") as fh:
            fh.write("#!/usr/bin/env python3\n"
                     "import sys\nsys.exit(1)\n")
        return
    for name in ("multidex", "bin", "profiles"):
        shutil.copytree(os.path.join(BUILD, name),
                        os.path.join(gen_dir, name),
                        ignore=shutil.ignore_patterns(
                            "__pycache__"))


def _profile_text():
    return """
[watch]
hours = 1
sitrep_hours = 1
disk_ceiling_gb = 0.05

[source]
kind = "simulated"

[updates]
posture = "automatic"
probation_items = 2
check_interval_hours = 24

[advanced]
time_scale = 100000.0
"""


@pytest.fixture()
def keeper_layout(tmp_path):
    root = str(tmp_path / "install")
    os.makedirs(os.path.join(root, "releases"))
    shutil.copytree(os.path.join(BUILD, "keeper"),
                    os.path.join(root, "keeper"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    _stage_tree(root, GEN_A)
    _stage_tree(root, GEN_B, stub=True)
    home = str(tmp_path / "home")
    os.makedirs(home)
    pointer = updater.new_pointer(GEN_A)
    updater.save_pointer(home, pointer)
    profile_path = str(tmp_path / "keeper-profile.toml")
    with open(profile_path, "w") as fh:
        fh.write(_profile_text())
    return {"root": root, "home": home, "profile": profile_path}


def _stage_activation(home, target=GEN_B):
    state = updater.UpdateState.load(home)
    state.data["candidate"] = {
        "hash": target, "counter": 7, "tag": "release-7",
        "staged_at": time.time(), "status": "staged",
        "gates": {}}
    state.data["last_check"] = time.time()
    state.save()
    updater.write_activation(home, target, 7)


def _watch_events(home):
    events = []
    watches = os.path.join(home, "watches")
    for name in sorted(os.listdir(watches)):
        log = os.path.join(watches, name, "events.jsonl")
        if os.path.exists(log):
            with open(log) as fh:
                for line in fh:
                    if line.strip():
                        events.append(json.loads(line))
    return events


def test_keeper_probation_rollback_end_to_end(keeper_layout):
    home = keeper_layout["home"]
    _stage_activation(home)
    keeper = os.path.join(keeper_layout["root"], "keeper",
                          "multidex-keeper")
    proc = subprocess.run(
        [sys.executable, keeper, "--home", home,
         "--profile", keeper_layout["profile"],
         "--heartbeat-timeout-s", "180", "--poll-s", "0.5"],
        capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = _watch_events(home)
    stages = [ev["data"].get("stage") for ev in events
              if ev["kind"] == "update"]
    assert "handover" in stages, stages
    assert "rolled_back" in stages, stages
    pointer = updater.load_pointer(home)
    assert pointer["current"]["hash"] == GEN_A
    assert GEN_B in pointer["bad"]
    assert len(pointer["failures"]) == 1
    states = []
    watches = os.path.join(home, "watches")
    for name in os.listdir(watches):
        state = util.read_json(os.path.join(watches, name,
                                            "state.json"))
        if state:
            states.append(state)
    assert any(s.get("complete") for s in states), states


def test_keeper_intensity_cap_degrades(keeper_layout):
    home = keeper_layout["home"]
    pointer = updater.load_pointer(home)
    now = time.time()
    pointer["failures"] = [now - 60, now - 30]
    updater.save_pointer(home, pointer)
    _stage_activation(home)
    keeper = os.path.join(keeper_layout["root"], "keeper",
                          "multidex-keeper")
    proc = subprocess.run(
        [sys.executable, keeper, "--home", home,
         "--profile", keeper_layout["profile"],
         "--heartbeat-timeout-s", "180", "--poll-s", "0.5"],
        capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pointer = updater.load_pointer(home)
    assert pointer["degraded"] is not None
    assert len(pointer["failures"]) == 3
    stages = [ev["data"].get("stage")
              for ev in _watch_events(home)
              if ev["kind"] == "update"]
    assert "posture_degraded" in stages, stages


def test_keeper_status_and_corrupt_pointer(keeper_layout):
    keeper = os.path.join(keeper_layout["root"], "keeper",
                          "multidex-keeper")
    proc = subprocess.run(
        [sys.executable, keeper, "--home", keeper_layout["home"],
         "--profile", keeper_layout["profile"], "status"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert GEN_A in proc.stdout
    path = updater.pointer_path(keeper_layout["home"])
    with open(path, "w") as fh:
        fh.write("{corrupt")
    with open(path + ".bak", "w") as fh:
        fh.write("{corrupt too")
    proc = subprocess.run(
        [sys.executable, keeper, "--home", keeper_layout["home"],
         "--profile", keeper_layout["profile"], "status"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "escape hatch" in proc.stdout + proc.stderr
