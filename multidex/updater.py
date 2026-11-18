"""Live updates: signed generations, gated activation, handover.

The design in one paragraph. An install in the generation layout
holds immutable release trees under releases/<commit hash>/, a
repo clone that is fetched from but never run, and a current
pointer. This module fetches signed release tags from the
install's configured remote, verifies them against the
allowed-signers file in the home, stages a verified candidate as
a new generation, and gates its activation behind a full
simulated watch and a read-proof against copies of the standing
home. Activation itself is a dwell-boundary handover flown by the
officer and the keeper (see officer.py and keeper/): the running
watch never ends for an update. A plain single-tree checkout has
no layout, and everything here reports that and does nothing -
the machinery engages only where the layout exists.

Trust boundaries, stated once. The allowed-signers file, the
release counter, the bad-release marks and the generation
pointer all live in the home, outside every repo and generation
directory: nothing inside the thing being verified vouches for
it, and a bad generation cannot rewrite the record that rolls
it back. Verification proves origin and integrity, not virtue.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

from . import budget, events as evmod, util
from .profile import load_profile

SIGNERS_FILENAME = "allowed-signers"
STATE_FILENAME = "update-state.json"
POINTER_FILENAME = "generation-pointer.json"
ACTIVATION_FILENAME = "update-activation.json"
ROLLBACK_REQUEST_FILENAME = "update-rollback-request.json"
HANDOVER_FILENAME = "handover-ready.json"
PROBATION_FILENAME = "update-probation.json"
ROLLBACK_NOTE_FILENAME = "update-rollback-note.json"
SNAPSHOT_DIRNAME = "update-snapshots"
CHECK_LOCK_FILENAME = "update-check.lock"
TAG_RE = re.compile(r"^release-(\d+)$")
HASH_RE = re.compile(r"^[0-9a-f]{40}$")
# Probation failure causes, read off the gap events an item
# files: the item itself raised, the capture path failed, or
# the tuner was lost. A single desk's failure is not here on
# purpose - the supervisor benches a desk and the dwell's other
# work stands, in probation as in any other watch.
PROBATION_FAIL_CAUSES = ("officer error", "capture failure",
                         "tuner loss")
# Failed activations inside a day that degrade the automatic
# posture to notify-only until an operator resets it.
INTENSITY_LIMIT = 3
INTENSITY_WINDOW_S = 24 * 3600


class UpdateError(Exception):
    """An update refusal, stated in plain words."""


# ------------------------------------------------------------ layout

def package_root():
    """The tree this code runs from (the parent of multidex/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def own_generation():
    """(hash, root) when running from a generation directory -
    releases/<full commit hash> - else (None, root). The officer
    records its generation from its own resolved path, never
    from the pointer: what is running is a fact about the
    process, not about a file another process can flip."""
    root = package_root()
    base = os.path.basename(root)
    parent = os.path.basename(os.path.dirname(root))
    if parent == "releases" and HASH_RE.match(base):
        return base, root
    return None, root


def install_root():
    """The install root holding releases/, repo/ and keeper/,
    or None in a plain single-tree checkout."""
    gen, root = own_generation()
    if gen is None:
        return None
    return os.path.dirname(os.path.dirname(root))


def layout_mode():
    return "generation" if install_root() else "single-tree"


def repo_path(root=None):
    root = root or install_root()
    if root is None:
        return None
    path = os.path.join(root, "repo")
    if os.path.exists(os.path.join(path, ".git")) \
            or os.path.exists(os.path.join(path, "HEAD")):
        return path
    return None


def generation_dir(root, gen_hash):
    return os.path.join(root, "releases", gen_hash)


def list_generations(root=None):
    root = root or install_root()
    if root is None:
        return []
    releases = os.path.join(root, "releases")
    try:
        names = os.listdir(releases)
    except OSError:
        return []
    return sorted(n for n in names if HASH_RE.match(n)
                  and os.path.isdir(os.path.join(releases, n)))


# ------------------------------------------------- the pointer file
# The generation pointer is the keeper's file: which generation
# is current, which stands by as previous, which is on probation,
# and which are marked bad. Its format is part of the trusted
# base - specified here, implemented here for the package, and
# implemented again, independently, in keeper/keeper.py, because
# the keeper must never import code from a generation it might
# have to roll back. Writes are atomic, carry a checksum over
# the payload, and keep a backup copy of the last good file.

def _pointer_checksum(payload):
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def pointer_path(home):
    return os.path.join(home, POINTER_FILENAME)


def _pointer_payload(data):
    return {k: v for k, v in data.items() if k != "checksum"}


def load_pointer(home):
    """The pointer dict, recovered from the backup copy when the
    main file is corrupt. Raises UpdateError when neither reads:
    a pointer that cannot be trusted is a stop condition, and
    the README's escape hatch is the way forward."""
    path = pointer_path(home)
    for candidate in (path, path + ".bak"):
        raw = util.read_json(candidate, default=None)
        if not isinstance(raw, dict):
            continue
        checksum = raw.get("checksum")
        if checksum and checksum == _pointer_checksum(
                _pointer_payload(raw)):
            return raw
    if not os.path.exists(path):
        return None
    raise UpdateError(
        "the generation pointer %s is corrupt and its backup "
        "does not verify either; follow the escape hatch in the "
        "README (run a retained generation directly, then "
        "restore the pointer)" % path)


def save_pointer(home, data):
    path = pointer_path(home)
    os.makedirs(home, exist_ok=True)
    existing = util.read_json(path, default=None)
    if isinstance(existing, dict) and existing.get("checksum") == \
            _pointer_checksum(_pointer_payload(existing)):
        shutil.copyfile(path, path + ".bak")
    data = dict(data)
    data["format"] = 1
    data["updated_at"] = time.time()
    data["checksum"] = _pointer_checksum(_pointer_payload(data))
    util.write_json(path, data)
    return data


def new_pointer(current_hash):
    return {"format": 1, "current": {"hash": current_hash,
                                     "status": "successful"},
            "previous": None, "probationary": None, "bad": [],
            "failures": [], "degraded": None, "updated_at": None}


def flip_symlink(root, gen_hash):
    """Point the install's current symlink at a generation, by
    atomic rename: a reader sees the old target or the new one,
    never a half-written link."""
    link = os.path.join(root, "current")
    tmp = os.path.join(root, ".current.tmp")
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(os.path.join("releases", gen_hash), tmp)
    os.replace(tmp, link)


# -------------------------------------------------- the update state

_STATE_DEFAULTS = {
    "format": 1,
    "remote_url": None,
    "last_check": None,
    "last_successful_check": None,
    "highest_counter": 0,
    "candidate": None,
    "bad": {},
    "last_outcome": None,
    "pending_events": [],
}


class UpdateState:
    """The package's update record for one home: check history,
    the standing candidate, bad marks, and events filed but not
    yet carried into the Index by an officer. Whole-file atomic
    writes; every mutation takes the check lock's discipline -
    one writer at a time, enforced by the callers that matter
    (the check worker and the keeper never run concurrently
    with a drain)."""

    def __init__(self, home, data=None):
        self.home = home
        self.path = os.path.join(home, STATE_FILENAME)
        self.data = dict(_STATE_DEFAULTS)
        if data:
            self.data.update(data)

    @classmethod
    def load(cls, home):
        raw = util.read_json(os.path.join(home, STATE_FILENAME),
                             default=None)
        return cls(home, raw if isinstance(raw, dict) else None)

    def save(self):
        os.makedirs(self.home, exist_ok=True)
        self.data["format"] = 1
        util.write_json(self.path, self.data)

    def queue_event(self, stage, content, data=None, t=None):
        payload = {"stage": stage}
        if data:
            payload.update(data)
        self.data["pending_events"].append({
            "desk": "updater", "kind": "update",
            "content": content, "data": payload,
            "t": t if t is not None else time.time()})

    def drain_events(self):
        events = self.data["pending_events"]
        self.data["pending_events"] = []
        return events


def signers_path(home):
    return os.path.join(home, SIGNERS_FILENAME)


# ------------------------------------------------------------ formats

def declared_formats():
    """This build's state-format contract, from the constants
    beside the formats themselves."""
    return {
        "ledger": {"read_min": budget.LEDGER_READ_MIN,
                   "read_max": budget.LEDGER_READ_MAX,
                   "write": budget.LEDGER_WRITE_FORMAT},
        "eventlog": {"read_min": evmod.EVENTLOG_READ_MIN,
                     "read_max": evmod.EVENTLOG_READ_MAX,
                     "write": evmod.EVENTLOG_WRITE_FORMAT},
    }


def format_problems(candidate_formats, standing_ledger_format):
    """The format-raise block, as a pure check. A candidate may
    raise the maximum format it reads freely; a candidate whose
    writable format exceeds the standing home's written format
    is blocked in every posture, because the generation that
    would roll back to could no longer read the state."""
    problems = []
    own = declared_formats()
    standing = {"ledger": standing_ledger_format,
                "eventlog": own["eventlog"]["write"]}
    for store in ("ledger", "eventlog"):
        cand = candidate_formats.get(store) or {}
        current = standing[store]
        if not (cand.get("read_min", 1 << 30) <= current
                <= cand.get("read_max", -1)):
            problems.append(
                "the candidate reads %s formats %s to %s; the "
                "standing home is written at format %s"
                % (store, cand.get("read_min"),
                   cand.get("read_max"), current))
        if cand.get("write", 0) > current:
            problems.append(
                "the candidate writes %s format %s, above the "
                "standing format %s; a release that raises the "
                "written format is blocked from activation in "
                "every posture and takes the manual, "
                "snapshot-first upgrade path instead"
                % (store, cand.get("write"), current))
    return problems


# ------------------------------------------------------------ git work

def _git(repo, *args, timeout=120):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True,
                          timeout=timeout)
    return proc


def _git_out(repo, *args, timeout=120):
    proc = _git(repo, *args, timeout=timeout)
    if proc.returncode != 0:
        raise UpdateError("git %s failed: %s"
                          % (args[0], proc.stderr.strip()[:400]))
    return proc.stdout.strip()


def discover_release_tags(repo):
    """[(counter, tag, commit)] for every release-N tag, signed
    or not - discovery is cheap; belief comes later."""
    out = []
    names = _git_out(repo, "tag", "-l", "release-*").splitlines()
    for name in names:
        name = name.strip()
        m = TAG_RE.match(name)
        if not m:
            continue
        commit = _git_out(repo, "rev-parse", name + "^{}")
        out.append((int(m.group(1)), name, commit))
    return sorted(out)


def verify_tag(repo, home, tag):
    """git verify-tag against the home's allowed-signers file.
    Returns (ok, detail). A missing signers file is a failure
    with its own wording: verification fails closed, and no
    unsigned path exists in any posture."""
    signers = signers_path(home)
    if not os.path.exists(signers):
        return False, ("no allowed-signers file in the home "
                       "(%s); signature verification cannot "
                       "run, and no unsigned activation path "
                       "exists" % signers)
    proc = _git(repo, "-c", "gpg.format=ssh",
                "-c", "gpg.ssh.allowedSignersFile=" + signers,
                "verify-tag", tag)
    if proc.returncode != 0:
        return False, ("tag %s does not verify against the "
                       "home's allowed-signers: %s"
                       % (tag, (proc.stderr or proc.stdout)
                          .strip()[:300]))
    return True, "tag %s signature verifies" % tag


def audit_diff(repo, base, cand):
    """The diff audit: what a release may not carry. Binaries
    (the XZ payload hid in binary test files), submodule or
    .gitmodules changes, symlinks escaping the tree, unexpected
    file types, and any change under keeper/ - the keeper is
    updated manually, never by a release."""
    problems = []
    raw = _git_out(repo, "diff", "--raw", "-z", base, cand)
    tokens = raw.split("\0")
    i = 0
    entries = []
    while i < len(tokens):
        head = tokens[i]
        if not head.startswith(":"):
            i += 1
            continue
        parts = head[1:].split(" ")
        status = parts[-1]
        paths = [tokens[i + 1]]
        i += 2
        if status.startswith(("R", "C")):
            paths.append(tokens[i])
            i += 1
        entries.append((parts[0], parts[1], status, paths))
    numstat = _git_out(repo, "diff", "--numstat", "-z", base, cand)
    binary_paths = set()
    for token in numstat.split("\0"):
        fields = token.split("\t")
        if len(fields) >= 3 and fields[0] == "-" and fields[1] == "-":
            binary_paths.add(fields[2])
    for old_mode, new_mode, status, paths in entries:
        for path in paths:
            if path == ".gitmodules" or path.endswith("/.gitmodules"):
                problems.append("%s changes .gitmodules" % path)
            if path.startswith("keeper/"):
                problems.append(
                    "%s is under keeper/; the keeper is updated "
                    "manually, never by a release" % path)
            if path in binary_paths:
                problems.append("%s is a binary file" % path)
        if new_mode == "160000" or old_mode == "160000":
            problems.append("%s is a submodule entry"
                            % paths[-1])
        elif new_mode not in ("000000", "100644", "100755",
                              "120000"):
            problems.append("%s has unexpected file mode %s"
                            % (paths[-1], new_mode))
        if new_mode == "120000":
            target = _git_out(repo, "cat-file", "blob",
                              "%s:%s" % (cand, paths[-1]))
            resolved = os.path.normpath(os.path.join(
                os.path.dirname(paths[-1]), target))
            if os.path.isabs(target) or resolved.startswith(".."):
                problems.append(
                    "symlink %s points outside the tree (%s)"
                    % (paths[-1], target))
    return problems


def materialise(repo, root, gen_hash):
    """Materialise a commit's tree as a fresh generation
    directory and re-verify every file's git blob hash against
    the commit. Returns the generation directory. A directory
    that already exists and verifies is reused as it stands -
    generations are immutable, so a verified tree is never
    rewritten."""
    dest = generation_dir(root, gen_hash)
    if os.path.isdir(dest):
        problems = verify_tree(repo, gen_hash, dest)
        if not problems:
            return dest
        raise UpdateError(
            "the staged generation %s exists but does not "
            "verify: %s" % (gen_hash, "; ".join(problems[:3])))
    tmp = os.path.join(root, "releases", ".tmp-" + gen_hash)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    proc = subprocess.Popen(["git", "-C", repo, "archive",
                             "--format=tar", gen_hash],
                            stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                name = member.name
                if os.path.isabs(name) or ".." in name.split("/"):
                    raise UpdateError(
                        "archive member escapes the tree: %s"
                        % name)
                tar.extract(member, tmp)
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError("git archive of %s failed" % gen_hash)
    problems = verify_tree(repo, gen_hash, tmp)
    if problems:
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError(
            "the materialised tree does not verify against its "
            "commit: %s" % "; ".join(problems[:3]))
    os.replace(tmp, dest)
    return dest


def verify_tree(repo, gen_hash, tree):
    """Every file in the tree must hash to the blob the commit
    names for it, and the tree must hold nothing the commit
    does not name."""
    problems = []
    out = _git_out(repo, "ls-tree", "-r", "-z", gen_hash)
    expected = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, _typ, blob = meta.split(" ")
        expected[path] = (mode, blob)
    seen = set()
    for dirpath, _dirs, files in os.walk(tree):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, tree)
            seen.add(rel)
            if rel not in expected:
                problems.append("%s is not in the commit" % rel)
                continue
            mode, blob = expected[rel]
            if os.path.islink(full):
                content = os.readlink(full).encode()
            else:
                with open(full, "rb") as fh:
                    content = fh.read()
            digest = hashlib.sha1(
                b"blob %d\0" % len(content) + content).hexdigest()
            if digest != blob:
                problems.append("%s does not hash to its blob"
                                % rel)
    for path in expected:
        if path not in seen:
            problems.append("%s is missing from the tree" % path)
    return problems


# ------------------------------------------------------ the gates

def adoptable_summary(home):
    """The summary a resume would adopt this home by: the
    current watch's position, the dwells its log records as
    executed, its event and dossier counts, and the ledger's
    format and totals. Two generations that read the same home
    must arrive at the same summary - that is the read-proof."""
    from .index import Index
    from . import products
    summary = {"watch_id": None, "next_index": None,
               "executed": [], "events": 0, "dossiers": 0,
               "ledger_format": None, "ledger_grants": 0,
               "ledger_consumed": 0, "ledger_reserved": 0}
    ledger = budget.AllocationLedger.load(home)
    summary["ledger_format"] = ledger.data.get("format")
    summary["ledger_grants"] = len(ledger.grants)
    pool = ledger.pool()
    summary["ledger_consumed"] = pool["consumed_bytes"]
    summary["ledger_reserved"] = pool["reserved_bytes"]
    index = Index(home)
    try:
        watch_id = products.resolve_current_watch(
            home, allow_complete=True)
        summary["watch_id"] = watch_id
        if watch_id:
            state = util.read_json(os.path.join(
                home, "watches", watch_id, "state.json"),
                default={}) or {}
            summary["next_index"] = state.get("next_index")
            executed = {ev["data"].get("bill_id")
                        for ev in index.dwell_records(watch_id)}
            executed.discard(None)
            summary["executed"] = sorted(executed)
            summary["events"] = index.count_events(
                watch_id=watch_id)
            summary["dossiers"] = len(
                index.dossiers_in_watch(watch_id))
    finally:
        index.close()
    return summary


def compare_summaries(running, candidate):
    """The read-proof comparison. The candidate works from a
    copy taken moments after the running summary was read, and
    the officer may have filed in between, so the candidate's
    view may be ahead of the running view - never behind it.
    Structure must match exactly; progress must be at least the
    running generation's."""
    problems = []
    if candidate.get("watch_id") != running.get("watch_id"):
        problems.append("the candidate resolves watch %s; the "
                        "running generation resolves %s"
                        % (candidate.get("watch_id"),
                           running.get("watch_id")))
    if candidate.get("ledger_format") != running.get(
            "ledger_format"):
        problems.append("the candidate reads ledger format %s; "
                        "the standing ledger is format %s"
                        % (candidate.get("ledger_format"),
                           running.get("ledger_format")))
    if not set(running.get("executed") or []) <= set(
            candidate.get("executed") or []):
        problems.append("the candidate's executed dwells do not "
                        "cover the running generation's")
    for key in ("events", "dossiers", "ledger_grants"):
        if (candidate.get(key) or 0) < (running.get(key) or 0):
            problems.append("the candidate's %s (%s) fall behind "
                            "the running generation's (%s)"
                            % (key, candidate.get(key),
                               running.get(key)))
    run_next = running.get("next_index")
    cand_next = candidate.get("next_index")
    if run_next is not None and cand_next is not None \
            and cand_next < run_next:
        problems.append("the candidate's next bill index (%s) "
                        "falls behind the running generation's "
                        "(%s)" % (cand_next, run_next))
    return problems


def _copy_home_for_proof(home, dest):
    """Copy the parts of a home a read-proof reads - the ledger,
    the Index database (through SQLite's backup, never a raw
    copy of a live file), the watch state, bills and event
    logs - never the snippet payloads. A trailing event-log
    line that does not parse is an event the officer had not
    committed when the copy was taken; it is dropped, and the
    comparison's ahead-is-allowed rule covers the rest."""
    import sqlite3
    os.makedirs(dest, exist_ok=True)
    ledger = os.path.join(home, budget.LEDGER_FILENAME)
    if os.path.exists(ledger):
        shutil.copyfile(ledger, os.path.join(
            dest, budget.LEDGER_FILENAME))
    db_src = os.path.join(home, "index.sqlite")
    if os.path.exists(db_src):
        src = sqlite3.connect("file:%s?mode=ro" % db_src,
                              uri=True)
        try:
            dst = sqlite3.connect(os.path.join(dest,
                                               "index.sqlite"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    pointer = util.read_json(os.path.join(home, "current_watch"),
                             default=None)
    if pointer is not None:
        util.write_json(os.path.join(dest, "current_watch"),
                        pointer)
    watches = os.path.join(home, "watches")
    if os.path.isdir(watches):
        for name in sorted(os.listdir(watches)):
            src_dir = os.path.join(watches, name)
            if not os.path.isdir(src_dir):
                continue
            dst_dir = os.path.join(dest, "watches", name)
            os.makedirs(dst_dir, exist_ok=True)
            for fname in ("state.json", "bill.json", "watch.json"):
                src = os.path.join(src_dir, fname)
                if os.path.exists(src):
                    shutil.copyfile(src, os.path.join(dst_dir,
                                                      fname))
            log = os.path.join(src_dir, "events.jsonl")
            if os.path.exists(log):
                with open(log, "rb") as fh:
                    blob = fh.read()
                lines = blob.split(b"\n")
                kept = []
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                    except ValueError:
                        break
                    kept.append(line)
                with open(os.path.join(dst_dir, "events.jsonl"),
                          "wb") as fh:
                    fh.write(b"\n".join(kept) + b"\n")


def _candidate_python(gen_root):
    return [sys.executable, "-m", "multidex.updater"]


def _run_candidate(gen_root, args, timeout=300):
    env = dict(os.environ)
    env["PYTHONPATH"] = gen_root + os.pathsep + env.get(
        "PYTHONPATH", "")
    proc = subprocess.run(_candidate_python(gen_root) + args,
                          capture_output=True, text=True,
                          timeout=timeout, env=env, cwd=gen_root)
    return proc


def candidate_formats(gen_root):
    proc = _run_candidate(gen_root, ["declared-formats"])
    if proc.returncode != 0:
        raise UpdateError("the candidate cannot state its "
                          "formats: %s"
                          % (proc.stderr or proc.stdout)
                          .strip()[:300])
    return json.loads(proc.stdout)


def run_sim_gate(gen_root, standing_profile, home, timeout_s=None):
    """The candidate flies a full simulated watch in a
    throwaway home: its own muster, bill, desks and stand-down,
    end to end. Returns (ok, detail). The gate home is kept
    under the real home's update-gate directory until the next
    gate run replaces it, so a failure can be read after the
    fact."""
    gate_home = os.path.join(home, "update-gate")
    shutil.rmtree(gate_home, ignore_errors=True)
    os.makedirs(gate_home, exist_ok=True)
    hours = standing_profile["watch"]["hours"]
    gate_profile = {
        "name": "update-gate",
        "source": {"kind": "simulated",
                   "seed": standing_profile["source"].get(
                       "seed", 20261117)},
        "home": dict(standing_profile["home"]),
        "watch": {"hours": hours,
                  "sitrep_hours":
                      standing_profile["watch"]["sitrep_hours"],
                  "disk_ceiling_gb": 0.2},
        "advanced": {"time_scale": 100000.0,
                     "tle_file": "tle.txt"},
    }
    lines = ["name = %s" % json.dumps(gate_profile["name"]),
             "[source]", "kind = \"simulated\"",
             "seed = %d" % gate_profile["source"]["seed"],
             "[home]"]
    for key in ("lat", "lon", "alt_m"):
        lines.append("%s = %r" % (key, gate_profile["home"][key]))
    lines.append("region = %s" % json.dumps(
        gate_profile["home"]["region"]))
    lines.append("antenna = %s" % json.dumps(
        gate_profile["home"]["antenna"]))
    lines += ["[watch]", "hours = %r" % hours,
              "sitrep_hours = %r"
              % gate_profile["watch"]["sitrep_hours"],
              "disk_ceiling_gb = 0.2",
              "[advanced]", "time_scale = 100000.0",
              "tle_file = \"tle.txt\""]
    profile_path = os.path.join(gate_home, "gate-profile.toml")
    with open(profile_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    src_dir = standing_profile.get("_dir")
    if src_dir:
        tle = os.path.join(
            src_dir,
            standing_profile["advanced"].get("tle_file",
                                             "tle.txt"))
        if os.path.exists(tle):
            shutil.copyfile(tle, os.path.join(gate_home,
                                              "tle.txt"))
    if timeout_s is None:
        timeout_s = max(1200, int(hours) * 120)
    env = dict(os.environ)
    env.pop("MULTIDEX_KEEPER", None)
    env.pop("MULTIDEX_TIME_SCALE", None)
    try:
        proc = subprocess.run(
            [sys.executable,
             os.path.join(gen_root, "bin", "multidex"),
             "--home", gate_home, "--profile", profile_path,
             "watch"],
            capture_output=True, text=True, timeout=timeout_s,
            env=env, cwd=gen_root)
    except subprocess.TimeoutExpired:
        return False, ("the simulated gate watch did not finish "
                       "within %d s" % timeout_s)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-400:]
        return False, ("the simulated gate watch exited %d: %s"
                       % (proc.returncode, tail))
    watches = os.path.join(gate_home, "watches")
    try:
        names = sorted(os.listdir(watches))
    except OSError:
        names = []
    for name in names:
        state = util.read_json(os.path.join(
            watches, name, "state.json"), default=None)
        if state and state.get("complete"):
            return True, ("a full simulated watch (%s, %s hours) "
                          "stood and completed in a throwaway "
                          "home" % (name, hours))
    return False, ("the simulated gate watch produced no "
                   "completed watch")


def run_read_proof(gen_root, home):
    """The candidate reads copies of the standing home and must
    arrive at the running generation's adoptable summary.
    Returns (ok, detail)."""
    running = adoptable_summary(home)
    proof_home = os.path.join(home, "update-proof")
    shutil.rmtree(proof_home, ignore_errors=True)
    try:
        _copy_home_for_proof(home, proof_home)
        proc = _run_candidate(gen_root,
                              ["read-proof", proof_home],
                              timeout=600)
        if proc.returncode != 0:
            return False, ("the candidate's read-proof failed "
                           "to run: %s"
                           % (proc.stderr or proc.stdout)
                           .strip()[:300])
        payload = json.loads(proc.stdout)
        problems = compare_summaries(running,
                                     payload["summary"])
        if problems:
            return False, "; ".join(problems)
        return True, ("the candidate reconciled the standing "
                      "home to the running generation's summary "
                      "(watch %s, %d events, %d executed dwells)"
                      % (running["watch_id"], running["events"],
                         len(running["executed"])))
    finally:
        shutil.rmtree(proof_home, ignore_errors=True)


# -------------------------------------------------------- snapshots

def take_snapshot(home, snapshot_id):
    """The pre-handover snapshot: the ledger and the Index
    database, copied into the home's snapshot store. Restored
    on rollback; retained until the new generation is marked
    successful. The event logs are not snapshotted - the log
    is the record, and a rolled-back generation's probation
    events stay in it as the episode's own account."""
    import sqlite3
    dest = os.path.join(home, SNAPSHOT_DIRNAME, snapshot_id)
    os.makedirs(dest, exist_ok=True)
    ledger = os.path.join(home, budget.LEDGER_FILENAME)
    if os.path.exists(ledger):
        shutil.copyfile(ledger, os.path.join(
            dest, budget.LEDGER_FILENAME))
    db_src = os.path.join(home, "index.sqlite")
    if os.path.exists(db_src):
        src = sqlite3.connect("file:%s?mode=ro" % db_src,
                              uri=True)
        try:
            dst = sqlite3.connect(os.path.join(dest,
                                               "index.sqlite"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    manifest = {"id": snapshot_id, "at": time.time(),
                "files": sorted(os.listdir(dest))}
    util.write_json(os.path.join(dest, "manifest.json"), manifest)
    return dest


def restore_snapshot(home, snapshot_id):
    dest = os.path.join(home, SNAPSHOT_DIRNAME, snapshot_id)
    if not os.path.isdir(dest):
        raise UpdateError("snapshot %s is not in the home"
                          % snapshot_id)
    ledger = os.path.join(dest, budget.LEDGER_FILENAME)
    if os.path.exists(ledger):
        shutil.copyfile(ledger, os.path.join(
            home, budget.LEDGER_FILENAME))
    db = os.path.join(dest, "index.sqlite")
    if os.path.exists(db):
        shutil.copyfile(db, os.path.join(home, "index.sqlite"))
    return dest


def drop_snapshot(home, snapshot_id):
    shutil.rmtree(os.path.join(home, SNAPSHOT_DIRNAME,
                               snapshot_id), ignore_errors=True)


# ------------------------------------------------- posture and files

def effective_posture(profile, home):
    """The posture that governs activation: the profile's,
    degraded to notify-only while the intensity cap stands.
    The degradation lives in the pointer file, where the
    keeper records failed activations."""
    posture = profile["updates"]["posture"]
    try:
        pointer = load_pointer(home)
    except UpdateError:
        pointer = None
    if pointer and pointer.get("degraded") \
            and posture == "automatic":
        return "notify-only"
    return posture


def recent_failures(pointer, now=None):
    now = now if now is not None else time.time()
    return [t for t in (pointer or {}).get("failures", [])
            if now - t < INTENSITY_WINDOW_S]


def write_activation(home, gen_hash, counter, kind="update"):
    util.write_json(os.path.join(home, ACTIVATION_FILENAME), {
        "hash": gen_hash, "counter": counter, "kind": kind,
        "at": time.time()})


def read_activation(home):
    return util.read_json(os.path.join(home, ACTIVATION_FILENAME),
                          default=None)


def clear_activation(home):
    try:
        os.remove(os.path.join(home, ACTIVATION_FILENAME))
    except OSError:
        pass


def write_rollback_request(home, to_hash, reason):
    util.write_json(os.path.join(home, ROLLBACK_REQUEST_FILENAME),
                    {"to_hash": to_hash, "reason": reason,
                     "at": time.time()})


def read_rollback_request(home):
    return util.read_json(os.path.join(
        home, ROLLBACK_REQUEST_FILENAME), default=None)


def clear_rollback_request(home):
    try:
        os.remove(os.path.join(home, ROLLBACK_REQUEST_FILENAME))
    except OSError:
        pass


# ------------------------------------------------------------ the check

class CheckLock:
    """One check at a time per home. The officer spawns check
    workers detached; the CLI checks in process; the lock is
    what keeps those from ever overlapping."""

    def __init__(self, home):
        self.path = os.path.join(home, CHECK_LOCK_FILENAME)
        self.fh = None

    def __enter__(self):
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            raise UpdateError("an update check is already "
                              "running for this home")
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            import fcntl
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
            self.fh = None
        return False


def _mark_bad(state, gen_hash, counter, reason, t):
    state.data["bad"][gen_hash] = {"counter": counter,
                                   "reason": reason, "at": t}
    if (state.data.get("candidate") or {}).get("hash") \
            == gen_hash:
        state.data["candidate"] = None


def run_check(home, profile, running_hash=None, now=None):
    """One full check: fetch, verify, stage, gate. Writes the
    update state and queues every stage as a pending event for
    the next officer to file in the Index. Returns the report
    dict; the report's outcome is a plain sentence either way.

    running_hash is the commit the caller runs (the officer's
    own generation, or the pointer's current for a CLI check);
    ancestry is enforced against it.
    """
    now = now if now is not None else time.time()
    report = {"outcome": None, "candidate": None, "events": 0}
    state = UpdateState.load(home)

    def done(outcome):
        state.data["last_outcome"] = outcome
        report["outcome"] = outcome
        report["events"] = len(state.data["pending_events"])
        state.save()
        return report

    root = install_root()
    if root is None:
        return done("updates require the generation layout; "
                    "this install is a single tree, and no "
                    "update machinery engages")
    repo = repo_path(root)
    if repo is None:
        return done("the install layout has no repo clone to "
                    "fetch from; updates cannot run")
    upd = profile["updates"]
    remote = upd["remote"]
    proc = _git(repo, "remote", "get-url", remote)
    if proc.returncode != 0:
        return done("the repo clone has no remote named %s"
                    % remote)
    actual_url = proc.stdout.strip()
    expected_url = upd.get("remote_url") or state.data.get(
        "remote_url")
    if expected_url and actual_url != expected_url:
        state.queue_event(
            "refused",
            "update check stopped: the remote %s answers at "
            "%s, not the configured %s; a moved remote is a "
            "stop condition and nothing was fetched"
            % (remote, actual_url, expected_url),
            {"remote": remote})
        return done("the remote %s has moved (answers at %s, "
                    "configured %s); the check stopped before "
                    "fetching" % (remote, actual_url, expected_url))
    if not state.data.get("remote_url"):
        state.data["remote_url"] = actual_url
    proc = _git(repo, "fetch", "--tags", remote, timeout=300)
    state.data["last_check"] = now
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        if "would clobber" in detail or "clobber" in detail:
            state.queue_event(
                "refused",
                "update check stopped: fetching would clobber "
                "a local tag - a release tag on the remote has "
                "moved, and moved tags are never followed: %s"
                % detail[:300], {"remote": remote})
            return done("a release tag on the remote has moved; "
                        "the check stopped and nothing was "
                        "fetched")
        return done("the fetch from %s failed: %s"
                    % (remote, detail[:300]))
    state.data["last_successful_check"] = now
    tags = discover_release_tags(repo)
    if not tags:
        return done("no releases on the remote; nothing to "
                    "check against")
    counter, tag, cand_hash = tags[-1]
    if running_hash is None:
        pointer = load_pointer(home)
        if pointer:
            running_hash = pointer["current"]["hash"]
    if cand_hash == running_hash:
        return done("the newest signed release (%s) is the "
                    "running generation" % tag)
    if counter <= state.data["highest_counter"]:
        return done("no new release: the newest tag %s "
                    "(counter %d) does not exceed the highest "
                    "counter on record (%d)"
                    % (tag, counter,
                       state.data["highest_counter"]))
    if cand_hash in state.data["bad"]:
        return done("the newest release %s is marked bad (%s); "
                    "it will not be retried - a higher-counter "
                    "release is the only way forward"
                    % (tag,
                       state.data["bad"][cand_hash]["reason"]))
    report["candidate"] = cand_hash
    state.queue_event(
        "offered",
        "update offered: release %d (tag %s) names generation "
        "%s; the running generation is %s"
        % (counter, tag, cand_hash, running_hash),
        {"hash": cand_hash, "counter": counter, "tag": tag})
    obj_type = _git_out(repo, "cat-file", "-t", tag)
    if obj_type != "tag":
        reason = ("tag %s is not an annotated tag; releases are "
                  "signed annotated tags" % tag)
        _mark_bad(state, cand_hash, counter, reason, now)
        state.queue_event("marked_bad",
                          "update refused and marked bad: %s"
                          % reason, {"hash": cand_hash,
                                     "counter": counter})
        return done(reason)
    ok, detail = verify_tag(repo, home, tag)
    if not ok:
        if "no allowed-signers file" in detail:
            state.queue_event("refused",
                              "update refused: %s" % detail,
                              {"hash": cand_hash,
                               "counter": counter})
            return done(detail)
        _mark_bad(state, cand_hash, counter, detail, now)
        state.queue_event("marked_bad",
                          "update refused and marked bad: %s"
                          % detail, {"hash": cand_hash,
                                     "counter": counter})
        return done(detail)
    # A verified signature makes the counter real: consume it,
    # so no release at or below it can ever be offered again.
    state.data["highest_counter"] = max(
        state.data["highest_counter"], counter)
    if running_hash:
        proc = _git(repo, "merge-base", "--is-ancestor",
                    running_hash, cand_hash)
        if proc.returncode != 0:
            reason = ("release %d does not descend from the "
                      "running commit %s; fast-forward ancestry "
                      "is required" % (counter, running_hash))
            _mark_bad(state, cand_hash, counter, reason, now)
            state.queue_event("marked_bad",
                              "update refused and marked bad: %s"
                              % reason, {"hash": cand_hash,
                                         "counter": counter})
            return done(reason)
    problems = audit_diff(repo, running_hash or cand_hash,
                          cand_hash)
    if problems:
        reason = "the diff audit refuses release %d: %s" % (
            counter, "; ".join(problems))
        _mark_bad(state, cand_hash, counter, reason, now)
        state.queue_event("marked_bad",
                          "update refused and marked bad: %s"
                          % reason, {"hash": cand_hash,
                                     "counter": counter})
        return done(reason)
    state.queue_event(
        "verified",
        "update verified: tag %s's signature verifies against "
        "the home's allowed-signers, the candidate descends "
        "from the running commit, and the diff audit is clean "
        "(no binaries, no submodule changes, no escaping "
        "symlinks, no keeper changes)" % tag,
        {"hash": cand_hash, "counter": counter})
    try:
        gen_dir = materialise(repo, root, cand_hash)
    except UpdateError as exc:
        _mark_bad(state, cand_hash, counter, str(exc), now)
        state.queue_event("marked_bad",
                          "update refused and marked bad: %s"
                          % exc, {"hash": cand_hash,
                                  "counter": counter})
        return done(str(exc))
    state.data["candidate"] = {
        "hash": cand_hash, "counter": counter, "tag": tag,
        "staged_at": now, "status": "staged", "gates": {}}
    state.queue_event(
        "staged",
        "update staged: generation %s materialised under "
        "releases/ and re-verified file by file against its "
        "commit" % cand_hash,
        {"hash": cand_hash, "counter": counter})
    cand = state.data["candidate"]
    try:
        formats = candidate_formats(gen_dir)
    except UpdateError as exc:
        _mark_bad(state, cand_hash, counter, str(exc), now)
        state.queue_event("marked_bad",
                          "update refused and marked bad: %s"
                          % exc, {"hash": cand_hash,
                                  "counter": counter})
        return done(str(exc))
    standing_ledger = budget.AllocationLedger.load(
        home).data.get("format")
    problems = format_problems(formats, standing_ledger)
    if problems:
        cand["status"] = "blocked-format"
        cand["gates"]["formats"] = "; ".join(problems)
        state.queue_event(
            "blocked_format",
            "update blocked in every posture: %s"
            % "; ".join(problems),
            {"hash": cand_hash, "counter": counter})
        return done("release %d is blocked by the format rule: "
                    "%s" % (counter, "; ".join(problems)))
    cand["gates"]["formats"] = "the candidate's declared " \
                               "formats cover the standing home"
    ok, detail = run_sim_gate(gen_dir, profile, home)
    cand["gates"]["simulation"] = detail
    if not ok:
        _mark_bad(state, cand_hash, counter,
                  "simulation gate: " + detail, now)
        state.queue_event(
            "gate_failed",
            "update gate failed and the release is marked "
            "bad: simulation gate: %s" % detail,
            {"hash": cand_hash, "counter": counter,
             "gate": "simulation"})
        return done("release %d failed the simulation gate: %s"
                    % (counter, detail))
    ok, detail = run_read_proof(gen_dir, home)
    cand["gates"]["read_proof"] = detail
    if not ok:
        _mark_bad(state, cand_hash, counter,
                  "read-proof: " + detail, now)
        state.queue_event(
            "gate_failed",
            "update gate failed and the release is marked "
            "bad: read-proof: %s" % detail,
            {"hash": cand_hash, "counter": counter,
             "gate": "read_proof"})
        return done("release %d failed the read-proof: %s"
                    % (counter, detail))
    state.queue_event(
        "gate_passed",
        "update gates passed for generation %s: %s; %s"
        % (cand_hash, cand["gates"]["simulation"],
           cand["gates"]["read_proof"]),
        {"hash": cand_hash, "counter": counter})
    posture = effective_posture(profile, home)
    if posture == "automatic":
        write_activation(home, cand_hash, counter)
        state.queue_event(
            "staged",
            "update staged and gate-passed: generation %s "
            "activates at the next safe dwell boundary "
            "(posture automatic)" % cand_hash,
            {"hash": cand_hash, "counter": counter,
             "activation": "automatic"})
        return done("release %d verified, staged and "
                    "gate-passed; activation is written for the "
                    "next safe dwell boundary" % counter)
    if posture == "notify-only":
        state.queue_event(
            "staged",
            "update staged and gate-passed: generation %s "
            "awaits approval (posture notify-only); approve "
            "with: multidex update approve" % cand_hash,
            {"hash": cand_hash, "counter": counter,
             "activation": "awaiting approval"})
        return done("release %d verified, staged and "
                    "gate-passed; awaiting approval (posture "
                    "notify-only)" % counter)
    state.queue_event(
        "staged",
        "update staged and gate-passed: generation %s will "
        "not activate; the posture is off" % cand_hash,
        {"hash": cand_hash, "counter": counter,
         "activation": "off"})
    return done("release %d verified, staged and gate-passed; "
                "posture is off, so it will not activate"
                % counter)


def approve_candidate(home, profile):
    """The notify-only approval: write the activation for a
    staged, gate-passed candidate. Returns a plain sentence."""
    state = UpdateState.load(home)
    cand = state.data.get("candidate")
    if not cand or cand.get("status") != "staged":
        return ("no staged, gate-passed candidate is awaiting "
                "approval")
    if cand["hash"] in state.data["bad"]:
        return "the staged candidate is marked bad and cannot " \
               "be approved"
    write_activation(home, cand["hash"], cand["counter"])
    state.queue_event(
        "approved",
        "update approved by the operator: generation %s "
        "activates at the next safe dwell boundary"
        % cand["hash"],
        {"hash": cand["hash"], "counter": cand["counter"]})
    state.save()
    return ("approved: generation %s activates at the next "
            "safe dwell boundary (or on the keeper's next start "
            "when no watch stands)" % cand["hash"])


def reset_failures(home):
    """Clear the failed-activation record and any posture
    degradation it caused. The operator's reset, and the only
    way out of a degraded posture."""
    pointer = load_pointer(home)
    if pointer is None:
        return "no generation pointer in this home; nothing " \
               "to reset"
    pointer["failures"] = []
    pointer["degraded"] = None
    save_pointer(home, pointer)
    return ("failed-activation record cleared; the profile's "
            "posture governs activation again")


def request_rollback(home, profile):
    """Roll back to the previous generation. With a watch
    standing, the rollback is a boundary handover like any
    other and the officer is asked through the home; with no
    watch standing, the pointer flips here and now. Returns a
    plain sentence."""
    from . import products
    pointer = load_pointer(home)
    if pointer is None:
        return ("no generation pointer in this home; there is "
                "no previous generation to roll back to")
    previous = pointer.get("previous")
    if not previous:
        return "no previous generation stands by; nothing to " \
               "roll back to"
    target = previous["hash"]
    live_id, _claimed = products.watch_liveness(home)
    if live_id:
        write_rollback_request(
            home, target,
            "operator rollback requested through the update "
            "verb")
        state = UpdateState.load(home)
        state.queue_event(
            "rollback_requested",
            "rollback requested by the operator: the watch "
            "hands over to generation %s at the next safe "
            "dwell boundary" % target, {"hash": target})
        state.save()
        return ("rollback requested: watch %s hands over to "
                "generation %s at the next safe dwell boundary"
                % (live_id, target))
    pointer["previous"] = pointer["current"]
    pointer["current"] = {"hash": target,
                          "status": "successful"}
    pointer["probationary"] = None
    save_pointer(home, pointer)
    root = install_root()
    if root:
        flip_symlink(root, target)
    return ("rolled back: generation %s is current; %s stands "
            "by as previous"
            % (target, pointer["previous"]["hash"]))


# ------------------------------------------------------------ surfaces

def status_lines(home, profile=None):
    """The update account for the status verb: running
    generation, posture, highest counter seen, last outcome,
    fallback generation, and the freeze warning."""
    lines = []
    gen, _root = own_generation()
    root = install_root()
    if root is None:
        return ["Updates: single-tree install; the live update "
                "machinery engages only in a generation layout "
                "(see the README)"]
    state = UpdateState.load(home)
    pointer = None
    try:
        pointer = load_pointer(home)
    except UpdateError as exc:
        lines.append("Updates: %s" % exc)
    running = gen
    # The live watch's own record outranks this process's tree:
    # during a handover the pointer has flipped while the old
    # officer still flies the watch.
    try:
        from . import products as _products
        live_id, _claimed = _products.watch_liveness(home)
        if live_id:
            live_state = util.read_json(os.path.join(
                home, "watches", live_id, "state.json"),
                default={}) or {}
            if live_state.get("generation"):
                running = live_state["generation"]
    except Exception:
        pass
    if running is None and pointer:
        running = pointer["current"]["hash"]
    lines.append("Updates: generation layout; running "
                 "generation %s" % (running or "unknown"))
    if profile is not None:
        posture = effective_posture(profile, home)
        raw = profile["updates"]["posture"]
        if posture != raw:
            lines.append("Update posture: %s (profile %s, "
                         "degraded by failed activations)"
                         % (posture, raw))
        else:
            lines.append("Update posture: %s" % posture)
    lines.append("Highest release counter seen: %d"
                 % state.data["highest_counter"])
    if state.data.get("last_outcome"):
        lines.append("Last update outcome: %s"
                     % state.data["last_outcome"])
    cand = state.data.get("candidate")
    if cand:
        lines.append("Staged candidate: generation %s "
                     "(release %d, %s)"
                     % (cand["hash"], cand["counter"],
                        cand["status"]))
    if pointer:
        prev = pointer.get("previous")
        lines.append("Fallback generation: %s"
                     % (prev["hash"] if prev else "none"))
        bad = pointer.get("bad") or []
        if bad:
            lines.append("Generations marked bad: %s"
                         % ", ".join(bad))
    if not os.path.exists(signers_path(home)):
        lines.append("Release signers: no allowed-signers file "
                     "in the home; verification fails closed "
                     "and no release can activate")
    if profile is not None:
        last = state.data.get("last_successful_check")
        freeze_days = profile["updates"]["freeze_warning_days"]
        if last is None:
            lines.append("Remote checks: no check of the "
                         "remote has completed yet")
        elif time.time() - last > freeze_days * 86400:
            lines.append("Remote checks: WARNING - no check of "
                         "the remote has completed in %.0f "
                         "days (the freeze warning threshold "
                         "is %s); a silently frozen channel is "
                         "a failure, not a quiet state"
                         % ((time.time() - last) / 86400,
                            freeze_days))
    return lines


def doctor_lines(home, profile):
    """The doctor verb's updates account."""
    lines = ["Updates (live update system):"]
    root = install_root()
    if root is None:
        lines.append("- layout: single-tree checkout; the "
                     "generation machinery does not engage, "
                     "and this install updates by hand")
        return lines
    lines.append("- layout: generation layout at %s; "
                 "generations on disk: %s"
                 % (root, ", ".join(list_generations(root))
                    or "none"))
    try:
        pointer = load_pointer(home)
    except UpdateError as exc:
        lines.append("- keeper state: %s" % exc)
        pointer = None
    if pointer:
        lines.append("- keeper state: current generation %s "
                     "(%s); previous %s; probationary %s"
                     % (pointer["current"]["hash"],
                        pointer["current"]["status"],
                        (pointer.get("previous") or {})
                        .get("hash", "none"),
                        (pointer.get("probationary") or {})
                        .get("hash", "none")))
        failures = recent_failures(pointer)
        if failures:
            lines.append("- failed activations in the last "
                         "24 hours: %d of %d before the "
                         "posture degrades"
                         % (len(failures), INTENSITY_LIMIT))
        if pointer.get("degraded"):
            lines.append("- posture degraded: %s"
                         % pointer["degraded"].get("reason"))
    else:
        lines.append("- keeper state: no pointer file yet; "
                     "the keeper writes it on first start")
    if os.path.exists(signers_path(home)):
        lines.append("- release signers: allowed-signers "
                     "present in the home; signed releases "
                     "can verify")
    else:
        lines.append("- release signers: allowed-signers "
                     "ABSENT from the home; verification "
                     "fails closed and no release can "
                     "activate")
    state = UpdateState.load(home)
    last = state.data.get("last_successful_check")
    freeze_days = profile["updates"]["freeze_warning_days"]
    if last is None:
        lines.append("- remote checks: none has completed "
                     "yet")
    elif time.time() - last > freeze_days * 86400:
        lines.append("- remote checks: WARNING - none has "
                     "completed in %.0f days (threshold %s); "
                     "a frozen channel is a failure"
                     % ((time.time() - last) / 86400,
                        freeze_days))
    else:
        lines.append("- remote checks: last completed %s; "
                     "highest release counter seen %d"
                     % (util.fmt_stamp(last),
                        state.data["highest_counter"]))
    return lines


# ------------------------------------------------------------ workers

def check_worker_main(home, profile_path):
    """The detached check the officer spawns: one run_check
    under the check lock, events queued for the officer to
    file. Exit code 0 whatever the outcome - the outcome is
    data, recorded in the state, not a process failure."""
    profile = load_profile(profile_path)
    gen, _root = own_generation()
    try:
        with CheckLock(home):
            run_check(home, profile, running_hash=gen)
    except UpdateError:
        return 0
    return 0


def _main(argv):
    if argv and argv[0] == "declared-formats":
        print(json.dumps(declared_formats()))
        return 0
    if argv and argv[0] == "read-proof" and len(argv) == 2:
        payload = {"summary": adoptable_summary(argv[1]),
                   "formats": declared_formats()}
        print(json.dumps(payload))
        return 0
    if argv and argv[0] == "check-worker" and len(argv) == 3:
        return check_worker_main(argv[1], argv[2])
    sys.stderr.write("usage: updater declared-formats | "
                     "read-proof <home> | check-worker <home> "
                     "<profile>\n")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
