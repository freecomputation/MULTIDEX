"""0.7.0: the live update system.

The verification path is exercised for real: a local git repo
whose tree is this tree, releases tagged with a throwaway SSH
signing key generated in the test, verification through git
verify-tag against an allowed-signers file in a temp home.
Nothing about the signature path is mocked.
"""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from multidex import budget, cli, gates, updater, util
from multidex.index import Index

BUILD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRINCIPAL = "release@multidex.test"


def _git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s"
                             % (args[0], proc.stderr))
    return proc


def _copy_tree(dst):
    for name in ("multidex", "bin", "profiles", "keeper"):
        shutil.copytree(os.path.join(BUILD, name),
                        os.path.join(dst, name),
                        ignore=shutil.ignore_patterns(
                            "__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copyfile(os.path.join(BUILD, name),
                        os.path.join(dst, name))


class ReleaseFactory:
    """A source repo carrying this tree, and signed releases."""

    def __init__(self, tmp_path):
        self.dir = str(tmp_path / "factory")
        os.makedirs(self.dir)
        self.key = os.path.join(self.dir, "relkey")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "",
                        "-f", self.key, "-C", PRINCIPAL],
                       check=True, capture_output=True)
        self.other_key = os.path.join(self.dir, "otherkey")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "",
                        "-f", self.other_key, "-C", PRINCIPAL],
                       check=True, capture_output=True)
        self.src = os.path.join(self.dir, "source")
        os.makedirs(self.src)
        _git(self.src, "init", "-q")
        _git(self.src, "config", "user.email", PRINCIPAL)
        _git(self.src, "config", "user.name", "Release Test")
        _git(self.src, "config", "gpg.format", "ssh")
        _git(self.src, "config", "user.signingkey", self.key)
        _copy_tree(self.src)
        _git(self.src, "add", "-A")
        _git(self.src, "commit", "-qm", "base")
        self.base = _git(self.src, "rev-parse", "HEAD").stdout \
            .strip()
        self.tag(1)

    def signers_file(self):
        with open(self.key + ".pub") as fh:
            pub = fh.read().strip()
        return "%s %s\n" % (PRINCIPAL, pub)

    def commit(self, mutate, message="change"):
        mutate(self.src)
        _git(self.src, "add", "-A")
        _git(self.src, "commit", "-qm", message)
        return _git(self.src, "rev-parse", "HEAD").stdout.strip()

    def tag(self, counter, key=None, target=None):
        args = ["tag", "-s", "release-%d" % counter, "-m",
                "multidex release %d" % counter]
        if key:
            args = ["-c", "user.signingkey=" + key] + args
        if target:
            args.append(target)
        _git(self.src, *args)
        return _git(self.src, "rev-parse",
                    "release-%d^{}" % counter).stdout.strip()


@pytest.fixture()
def factory(tmp_path):
    # Function-scoped on purpose: several tests commit hostile
    # trees (binaries, escaping symlinks) that must not leak
    # into another test's candidate diff.
    return ReleaseFactory(tmp_path)


@pytest.fixture()
def layout(tmp_path, factory):
    """An install layout: repo clone, base generation staged,
    pointer written, and a home with the signers file."""
    root = str(tmp_path / "install")
    os.makedirs(os.path.join(root, "releases"))
    subprocess.run(["git", "clone", "--quiet", factory.src,
                    os.path.join(root, "repo")], check=True)
    repo = os.path.join(root, "repo")
    updater.materialise(repo, root, factory.base)
    pointer = updater.new_pointer(factory.base)
    home = str(tmp_path / "home")
    os.makedirs(home)
    updater.save_pointer(home, pointer)
    with open(updater.signers_path(home), "w") as fh:
        fh.write(factory.signers_file())
    return {"root": root, "repo": repo, "home": home}


@pytest.fixture()
def upd_profile(profile):
    profile["source"]["kind"] = "simulated"
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    return profile


def _use_layout(monkeypatch, layout):
    monkeypatch.setattr(updater, "install_root",
                        lambda: layout["root"])


def _stages(home):
    state = updater.UpdateState.load(home)
    return [ev["data"]["stage"]
            for ev in state.data["pending_events"]]


# ------------------------------------------------------- layout

def test_materialise_and_pointer(layout, factory):
    gen_dir = updater.generation_dir(layout["root"],
                                     factory.base)
    assert os.path.isdir(gen_dir)
    assert updater.verify_tree(layout["repo"], factory.base,
                               gen_dir) == []
    pointer = updater.load_pointer(layout["home"])
    assert pointer["current"]["hash"] == factory.base
    updater.flip_symlink(layout["root"], factory.base)
    assert os.path.basename(os.readlink(
        os.path.join(layout["root"], "current"))) == factory.base


def test_pointer_corruption_recovers_from_backup(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(home)
    pointer = updater.new_pointer("a" * 40)
    updater.save_pointer(home, pointer)
    pointer["previous"] = {"hash": "b" * 40,
                           "status": "successful"}
    updater.save_pointer(home, pointer)
    path = updater.pointer_path(home)
    with open(path, "w") as fh:
        fh.write("{corrupt")
    loaded = updater.load_pointer(home)
    assert loaded["current"]["hash"] == "a" * 40
    assert loaded["previous"] is None  # the backup's content
    with open(path + ".bak", "w") as fh:
        fh.write("{corrupt too")
    with pytest.raises(updater.UpdateError):
        updater.load_pointer(home)


# ------------------------------------------------------- the check

def test_check_full_pipeline_automatic(layout, factory,
                                       upd_profile, monkeypatch):
    _use_layout(monkeypatch, layout)
    cand = factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nrelease two\n"))
    factory.tag(2)
    upd_profile["updates"]["posture"] = "automatic"
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "gate-passed" in report["outcome"], report["outcome"]
    state = updater.UpdateState.load(layout["home"])
    assert state.data["candidate"]["hash"] == cand
    assert state.data["candidate"]["status"] == "staged"
    assert state.data["highest_counter"] == 2
    activation = updater.read_activation(layout["home"])
    assert activation["hash"] == cand
    assert os.path.isdir(updater.generation_dir(
        layout["root"], cand))
    stages = _stages(layout["home"])
    for stage in ("offered", "verified", "staged", "gate_passed"):
        assert stage in stages, stages


def test_check_notify_only_then_approve(layout, factory,
                                        upd_profile,
                                        monkeypatch):
    _use_layout(monkeypatch, layout)
    cand = factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nrelease two\n"))
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "awaiting approval" in report["outcome"]
    assert updater.read_activation(layout["home"]) is None
    sentence = updater.approve_candidate(layout["home"],
                                         upd_profile)
    assert "approved" in sentence
    assert updater.read_activation(
        layout["home"])["hash"] == cand


def test_check_bad_signature_marked_bad(layout, factory,
                                        upd_profile,
                                        monkeypatch):
    _use_layout(monkeypatch, layout)
    cand = factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nforged\n"))
    factory.tag(2, key=factory.other_key)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    state = updater.UpdateState.load(layout["home"])
    assert cand in state.data["bad"]
    assert state.data["candidate"] is None
    assert "marked_bad" in _stages(layout["home"])
    assert updater.read_activation(layout["home"]) is None


def test_check_missing_signers_fails_closed(layout, factory,
                                            upd_profile,
                                            monkeypatch):
    _use_layout(monkeypatch, layout)
    os.remove(updater.signers_path(layout["home"]))
    cand = factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nrelease two\n"))
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "allowed-signers" in report["outcome"]
    state = updater.UpdateState.load(layout["home"])
    assert cand not in state.data["bad"]  # an install problem,
    assert state.data["candidate"] is None  # not a bad release


def test_check_moved_tag_stops(layout, factory, upd_profile,
                               monkeypatch):
    _use_layout(monkeypatch, layout)
    factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nrelease two\n"))
    factory.tag(2)
    _git(layout["repo"], "fetch", "--tags", "origin")
    # The tag moves on the remote: same name, new commit.
    _git(factory.src, "tag", "-d", "release-2")
    factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nmoved\n"))
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "moved" in report["outcome"], report["outcome"]


def test_check_remote_url_mismatch_stops(layout, factory,
                                         upd_profile,
                                         monkeypatch):
    _use_layout(monkeypatch, layout)
    _git(layout["repo"], "remote", "set-url", "origin",
         "/nonexistent/elsewhere")
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "moved" in report["outcome"]
    state = updater.UpdateState.load(layout["home"])
    assert state.data["last_successful_check"] is None


def test_check_non_fast_forward_marked_bad(layout, factory,
                                           upd_profile,
                                           monkeypatch):
    _use_layout(monkeypatch, layout)
    _git(factory.src, "checkout", "--orphan", "divergent")
    _git(factory.src, "add", "-A")
    _git(factory.src, "commit", "-qm", "divergent root")
    cand = _git(factory.src, "rev-parse", "HEAD").stdout.strip()
    factory.tag(2)
    _git(factory.src, "checkout", "master")
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    state = updater.UpdateState.load(layout["home"])
    assert cand in state.data["bad"]
    assert "descend" in state.data["bad"][cand]["reason"]


def test_check_counter_rollback_refused(layout, factory,
                                        upd_profile,
                                        monkeypatch):
    _use_layout(monkeypatch, layout)
    state = updater.UpdateState.load(layout["home"])
    state.data["highest_counter"] = 5
    state.save()
    factory.commit(
        lambda src: open(os.path.join(src, "README.md"),
                         "a").write("\nold\n"))
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "does not exceed" in report["outcome"]


# ------------------------------------------------------- diff audit

def test_diff_audit_refuses_binary(factory):
    cand = factory.commit(
        lambda src: open(os.path.join(src, "payload.bin"),
                         "wb").write(b"\x00\x01\x02" * 64))
    problems = updater.audit_diff(factory.src, factory.base,
                                  cand)
    assert any("binary" in p for p in problems), problems


def test_diff_audit_refuses_keeper_change(factory):
    def mutate(src):
        with open(os.path.join(src, "keeper", "keeper.py"),
                  "a") as fh:
            fh.write("\n# touched\n")
    cand = factory.commit(mutate)
    problems = updater.audit_diff(factory.src, factory.base,
                                  cand)
    assert any("keeper/" in p for p in problems), problems


def test_diff_audit_refuses_escaping_symlink(factory):
    def mutate(src):
        os.symlink("../../etc/passwd",
                   os.path.join(src, "escape-link"))
    cand = factory.commit(mutate)
    problems = updater.audit_diff(factory.src, factory.base,
                                  cand)
    assert any("outside the tree" in p for p in problems), \
        problems


def test_diff_audit_refuses_submodule(factory):
    def mutate(src):
        with open(os.path.join(src, ".gitmodules"), "w") as fh:
            fh.write('[submodule "x"]\n\tpath = x\n'
                     '\turl = https://example.invalid/x\n')
    cand = factory.commit(mutate)
    problems = updater.audit_diff(factory.src, factory.base,
                                  cand)
    assert any(".gitmodules" in p for p in problems), problems


# ------------------------------------------------------- gates

def test_format_raise_blocked_not_bad(layout, factory,
                                      upd_profile, monkeypatch):
    _use_layout(monkeypatch, layout)

    def mutate(src):
        path = os.path.join(src, "multidex", "budget.py")
        with open(path) as fh:
            text = fh.read()
        with open(path, "w") as fh:
            fh.write(text.replace("LEDGER_WRITE_FORMAT = 1",
                                  "LEDGER_WRITE_FORMAT = 2"))
    cand = factory.commit(mutate)
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    assert "format" in report["outcome"]
    state = updater.UpdateState.load(layout["home"])
    assert state.data["candidate"]["status"] == "blocked-format"
    assert cand not in state.data["bad"]
    assert "blocked_format" in _stages(layout["home"])
    assert updater.read_activation(layout["home"]) is None


def test_sim_gate_failure_marked_bad(layout, factory,
                                     upd_profile, monkeypatch):
    _use_layout(monkeypatch, layout)

    def mutate(src):
        with open(os.path.join(src, "bin", "multidex"),
                  "w") as fh:
            fh.write("#!/usr/bin/env python3\n"
                     "import sys\nsys.exit(3)\n")
    cand = factory.commit(mutate)
    factory.tag(2)
    upd_profile["updates"]["remote_url"] = factory.src
    report = updater.run_check(layout["home"], upd_profile,
                               running_hash=factory.base)
    state = updater.UpdateState.load(layout["home"])
    assert cand in state.data["bad"]
    assert "simulation gate" in state.data["bad"][cand]["reason"]
    assert "gate_failed" in _stages(layout["home"])


def test_compare_summaries_rules():
    running = {"watch_id": "w1", "next_index": 5,
               "executed": ["bill-001", "bill-002"], "events": 40,
               "dossiers": 3, "ledger_format": 1,
               "ledger_grants": 10}
    ahead = dict(running, next_index=6, events=44,
                 executed=["bill-001", "bill-002", "bill-003"])
    assert updater.compare_summaries(running, ahead) == []
    behind = dict(running, events=39)
    assert updater.compare_summaries(running, behind)
    missing = dict(running, executed=["bill-001"])
    assert updater.compare_summaries(running, missing)
    wrong_watch = dict(running, watch_id="w2")
    assert updater.compare_summaries(running, wrong_watch)
    wrong_format = dict(running, ledger_format=2)
    assert updater.compare_summaries(running, wrong_format)


def test_read_proof_against_real_home(home, profile):
    from multidex.officer import WatchRunner
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        result = WatchRunner(home, profile, index).start(
            start_epoch=1794916800.0)
        assert result["ok"]
    finally:
        index.close()
    summary = updater.adoptable_summary(home)
    assert summary["watch_id"] == result["watch_id"]
    assert summary["events"] > 0
    proc = updater._run_candidate(BUILD, ["read-proof", home])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert updater.compare_summaries(summary,
                                     payload["summary"]) == []


def test_proof_copy_drops_torn_log_line(home, profile):
    from multidex.officer import WatchRunner
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        result = WatchRunner(home, profile, index).start(
            start_epoch=1794916800.0)
        assert result["ok"]
        log = index.log_path(result["watch_id"])
    finally:
        index.close()
    with open(log, "a") as fh:
        fh.write('{"id": "ev-torn", "partial"')
    dest = os.path.join(home, "proof-copy")
    updater._copy_home_for_proof(home, dest)
    copied = os.path.join(dest, "watches", result["watch_id"],
                          "events.jsonl")
    with open(copied) as fh:
        for line in fh:
            if line.strip():
                json.loads(line)  # every kept line parses


# ------------------------------------------------------- formats

def test_format_problems_pure():
    standing = 1
    good = updater.declared_formats()
    assert updater.format_problems(good, standing) == []
    raised = json.loads(json.dumps(good))
    raised["ledger"]["write"] = 2
    problems = updater.format_problems(raised, standing)
    assert any("blocked" in p for p in problems)
    narrow = json.loads(json.dumps(good))
    narrow["ledger"]["read_max"] = 0
    assert updater.format_problems(narrow, standing)


def test_additive_log_and_golden_fixtures(home, index):
    # A historical-format ledger loads.
    util.write_json(os.path.join(home, "allocation-ledger.json"),
                    {"format": 1, "cap_bytes": 1000,
                     "grants": []})
    ledger = budget.AllocationLedger.load(home)
    assert ledger.data["format"] == 1
    # An event carrying fields a newer build added is accepted
    # by the write path and survives a rebuild; an event in the
    # historical shape (no generation anywhere) rebuilds too.
    from multidex.events import make_event
    ev = make_event("watch-golden", "officer", "system", 100.0,
                    100.0, grade="CONFIRMED", content="old shape",
                    data={"resumed": True})
    index.add_event(ev)
    ev2 = make_event("watch-golden", "updater", "update", 101.0,
                     101.0, grade="CONFIRMED", content="new",
                     data={"stage": "offered",
                           "future_field": [1, 2]})
    ev2["brand_new_top_level"] = "ignored by old readers"
    index.add_event(ev2)
    count = index.rebuild()
    assert count == 2
    kinds = {e["kind"] for e in index.events(
        watch_id="watch-golden")}
    assert kinds == {"system", "update"}


# ------------------------------------------------------- workers

def test_workers_spawn_from_own_generation(monkeypatch):
    from multidex import supervisor
    root = os.path.dirname(os.path.dirname(
        os.path.abspath(supervisor.__file__)))
    assert supervisor._BUILD_ROOT == root
    captured = {}

    import io

    class FakeProc:
        stdout = iter(())
        stdin = io.StringIO()

        def poll(self):
            return 0

        def kill(self):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env") or {}
        return FakeProc()

    monkeypatch.setattr(supervisor.subprocess, "Popen",
                        fake_popen)
    worker = supervisor._Worker("sweep")
    assert captured["args"][1:3] == ["-m", "multidex.deskworker"]
    assert captured["env"]["PYTHONPATH"].split(
        os.pathsep)[0] == root
    worker.kill()


# ------------------------------------------------------- handover

def _handover_runner(profile, layout, target):
    from multidex.officer import WatchRunner
    home = layout["home"]
    profile["watch"]["hours"] = 1
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    runner = WatchRunner(home, profile, index)
    runner.watch_id = "watch-handover"
    runner.watch_dir = index.watch_dir("watch-handover")
    runner.generation = "a" * 40
    os.makedirs(updater.generation_dir(layout["root"], target),
                exist_ok=True)
    state = updater.UpdateState.load(home)
    state.data["candidate"] = {
        "hash": target, "counter": 9, "tag": "release-9",
        "staged_at": time.time(), "status": "staged",
        "gates": {}}
    state.save()
    updater.write_activation(home, target, 9)
    return runner, index


def test_handover_refused_beside_pass(profile, layout,
                                      monkeypatch):
    target = "d" * 40
    runner, index = _handover_runner(profile, layout, target)
    home = layout["home"]
    try:
        monkeypatch.setattr(updater, "install_root",
                            lambda: layout["root"])
        monkeypatch.setenv("MULTIDEX_KEEPER", "1")
        done = {"index": 1, "kind": "dwell", "desk": "sweep",
                "start": time.time() - 600,
                "end": time.time() - 60}
        nxt = {"index": 2, "kind": "pass", "freq": 137.1,
               "start": time.time() + 600,
               "end": time.time() + 1200}
        runner._update_boundary([done, nxt], 1, time.time())
        assert not runner._handed_over
        assert not os.path.exists(
            os.path.join(home, "handover-ready.json"))
    finally:
        index.close()


def test_handover_at_dwell_boundary(profile, layout,
                                    monkeypatch):
    target = "d" * 40
    runner, index = _handover_runner(profile, layout, target)
    home = layout["home"]
    try:
        monkeypatch.setattr(updater, "install_root",
                            lambda: layout["root"])
        monkeypatch.setenv("MULTIDEX_KEEPER", "1")
        done = {"index": 1, "kind": "dwell", "desk": "sweep",
                "start": time.time() - 600,
                "end": time.time() - 60}
        nxt = {"index": 2, "kind": "dwell", "desk": "sweep",
               "start": time.time() + 3600,
               "end": time.time() + 4200}
        runner._update_boundary([done, nxt], 1, time.time())
        assert runner._handed_over
        ready = util.read_json(os.path.join(
            home, "handover-ready.json"))
        assert ready["to_generation"] == target
        assert ready["from_generation"] == "a" * 40
        assert os.path.isdir(os.path.join(
            home, "update-snapshots", ready["snapshot"]))
        stages = [ev["data"].get("stage") for ev in index.events(
            watch_id="watch-handover")]
        assert "handover" in stages
    finally:
        index.close()


# ------------------------------------------------------- surfaces

def test_single_tree_mode(home, profile):
    report = updater.run_check(home, profile)
    assert "single tree" in report["outcome"]
    lines = updater.status_lines(home, profile)
    assert "single-tree" in lines[0]
    text = gates.doctor_text(profile, home)
    assert "layout: single-tree" in text


def test_freeze_warning(layout, upd_profile, monkeypatch):
    _use_layout(monkeypatch, layout)
    state = updater.UpdateState.load(layout["home"])
    state.data["last_successful_check"] = time.time() \
        - 20 * 86400
    state.save()
    lines = updater.status_lines(layout["home"], upd_profile)
    assert any("WARNING" in line for line in lines), lines


def test_effective_posture_degrades(layout, upd_profile):
    pointer = updater.load_pointer(layout["home"])
    pointer["degraded"] = {"at": time.time(),
                           "reason": "3 failed activations"}
    updater.save_pointer(layout["home"], pointer)
    upd_profile["updates"]["posture"] = "automatic"
    assert updater.effective_posture(
        upd_profile, layout["home"]) == "notify-only"
    sentence = updater.reset_failures(layout["home"])
    assert "cleared" in sentence
    assert updater.effective_posture(
        upd_profile, layout["home"]) == "automatic"


def test_update_cli_verbs(home):
    rc = cli.main(["--home", home, "update", "status"])
    assert rc == 0
    rc = cli.main(["--home", home, "update", "approve"])
    assert rc == 0
    rc = cli.main(["--home", home, "update", "reset"])
    assert rc == 0
    rc = cli.main(["--home", home, "update", "rollback"])
    assert rc == 0


def test_rollback_without_watch_flips_pointer(layout,
                                              upd_profile):
    pointer = updater.load_pointer(layout["home"])
    other = "c" * 40
    pointer["previous"] = {"hash": other,
                           "status": "successful"}
    updater.save_pointer(layout["home"], pointer)
    sentence = updater.request_rollback(layout["home"],
                                        upd_profile)
    assert "rolled back" in sentence
    pointer = updater.load_pointer(layout["home"])
    assert pointer["current"]["hash"] == other
