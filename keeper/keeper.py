#!/usr/bin/env python3
"""The MULTIDEX keeper: the small frozen base of the live
update system.

The keeper starts the watch officer for the current
generation, watches it, and performs the generation flips a
standing watch needs: when an officer hands over at a dwell
boundary, the keeper starts the successor generation, which
adopts the watch from the record; when a generation fails its
probation, the keeper restores the pre-handover snapshot,
flips back to the previous generation, and starts it.

The keeper does no network, git, verification, or device
work. It reads and writes exactly these files in the home:
the generation pointer (its own file, format below), the
handover and probation markers the officers leave, the
rollback note it leaves for the adopting officer, the
snapshots it restores, and its own log. It never imports
code from any generation - a generation it may have to roll
back cannot be code it runs. Its own updates are manual,
with the officer stopped, and the README says so.

Pointer format (generation-pointer.json in the home):
  {"format": 1,
   "current": {"hash": H, "status": "successful"|"probationary"},
   "previous": {"hash": H, "status": "successful"} | None,
   "probationary": {"hash": H, "watch_id": W, "snapshot": S,
                    "since": epoch} | None,
   "bad": [H, ...],
   "failures": [epoch, ...],
   "degraded": {"at": epoch, "reason": str} | None,
   "updated_at": epoch, "checksum": sha256 of the rest}
Writes are atomic, and the last good file is kept beside it
as generation-pointer.json.bak; a corrupt main file is
recovered from the backup, and a pointer that verifies
neither way is a stop condition with the escape hatch in
the README as the way forward.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

POINTER_FILENAME = "generation-pointer.json"
HANDOVER_FILENAME = "handover-ready.json"
PROBATION_FILENAME = "update-probation.json"
ROLLBACK_NOTE_FILENAME = "update-rollback-note.json"
ACTIVATION_FILENAME = "update-activation.json"
ROLLBACK_REQUEST_FILENAME = "update-rollback-request.json"
SNAPSHOT_DIRNAME = "update-snapshots"
LEDGER_FILENAME = "allocation-ledger.json"
INTENSITY_LIMIT = 3
INTENSITY_WINDOW_S = 24 * 3600
RESTART_BUDGET = 2


# ------------------------------------------------------------ tiny io

def _read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


# ------------------------------------------------------------ pointer

def _checksum(payload):
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _payload(data):
    return {k: v for k, v in data.items() if k != "checksum"}


def load_pointer(home):
    path = os.path.join(home, POINTER_FILENAME)
    for candidate in (path, path + ".bak"):
        raw = _read_json(candidate, default=None)
        if isinstance(raw, dict) and raw.get("checksum") == \
                _checksum(_payload(raw)):
            return raw
    if not os.path.exists(path):
        return None
    raise SystemExit(
        "keeper: the generation pointer %s is corrupt and "
        "its backup does not verify either. Follow the "
        "escape hatch in the README: run a retained "
        "generation's officer directly, then restore the "
        "pointer." % path)


def save_pointer(home, data):
    path = os.path.join(home, POINTER_FILENAME)
    os.makedirs(home, exist_ok=True)
    existing = _read_json(path, default=None)
    if isinstance(existing, dict) and existing.get("checksum") \
            == _checksum(_payload(existing)):
        shutil.copyfile(path, path + ".bak")
    data = dict(data)
    data["format"] = 1
    data["updated_at"] = time.time()
    data["checksum"] = _checksum(_payload(data))
    _write_json(path, data)
    return data


def flip_symlink(root, gen_hash):
    link = os.path.join(root, "current")
    tmp = os.path.join(root, ".current.tmp")
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(os.path.join("releases", gen_hash), tmp)
    os.replace(tmp, link)


def restore_snapshot(home, snapshot_id):
    dest = os.path.join(home, SNAPSHOT_DIRNAME, snapshot_id)
    if not snapshot_id or not os.path.isdir(dest):
        return False
    ledger = os.path.join(dest, LEDGER_FILENAME)
    if os.path.exists(ledger):
        shutil.copyfile(ledger, os.path.join(home,
                                             LEDGER_FILENAME))
    db = os.path.join(dest, "index.sqlite")
    if os.path.exists(db):
        shutil.copyfile(db, os.path.join(home, "index.sqlite"))
    return True


# ------------------------------------------------------------ keeper

class Keeper:
    def __init__(self, root, home, profile, poll_s=2.0,
                 heartbeat_timeout_s=1800.0):
        self.root = root
        self.home = home
        self.profile = profile
        self.poll_s = poll_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.restarts = RESTART_BUDGET

    def log(self, message):
        line = "%s keeper: %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S UTC",
                          time.gmtime()), message)
        print(line, flush=True)
        try:
            os.makedirs(self.home, exist_ok=True)
            with open(os.path.join(self.home, "keeper.log"),
                      "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    # -------------------------------------------------- home reads
    def current_watch(self):
        return _read_json(os.path.join(self.home,
                                       "current_watch"),
                          default=None)

    def watch_state(self, watch_id):
        if not watch_id:
            return None
        return _read_json(os.path.join(
            self.home, "watches", watch_id, "state.json"),
            default=None)

    def watch_live(self):
        watch_id = self.current_watch()
        state = self.watch_state(watch_id)
        return bool(state) and not state.get("complete") \
            and _pid_alive(state.get("pid"))

    # -------------------------------------------------- supervision
    def start_officer(self, gen_hash, resume_id=None):
        gen_dir = os.path.join(self.root, "releases", gen_hash)
        entry = os.path.join(gen_dir, "bin", "multidex")
        if not os.path.exists(entry):
            raise SystemExit(
                "keeper: generation %s has no bin/multidex; "
                "the tree is not a MULTIDEX generation"
                % gen_hash)
        cmd = [sys.executable, entry, "--home", self.home,
               "--profile", self.profile, "watch"]
        if resume_id:
            cmd += ["--resume", resume_id]
        env = dict(os.environ)
        env["MULTIDEX_KEEPER"] = "1"
        self.log("starting officer for generation %s%s"
                 % (gen_hash,
                    " (resume %s)" % resume_id if resume_id
                    else ""))
        return subprocess.Popen(cmd, env=env)

    def supervise(self, proc):
        """Watch one officer. Returns (outcome, detail):
        exited, killed-heartbeat, or probation-failed."""
        watch_id = self.current_watch()
        last_mtime = None
        last_progress = time.time()
        while proc.poll() is None:
            time.sleep(self.poll_s)
            if not watch_id:
                watch_id = self.current_watch()
            probation = _read_json(os.path.join(
                self.home, PROBATION_FILENAME), default=None)
            if probation and probation.get("status") == "failed":
                self.log("probation failed for generation %s: "
                         "%s; stopping the officer"
                         % (probation.get("generation"),
                            probation.get("reason")))
                self._terminate(proc)
                return "probation-failed", probation.get(
                    "reason")
            state_path = None
            if watch_id:
                state_path = os.path.join(
                    self.home, "watches", watch_id, "state.json")
            if state_path and os.path.exists(state_path):
                mtime = os.path.getmtime(state_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    last_progress = time.time()
            if time.time() - last_progress \
                    > self.heartbeat_timeout_s:
                self.log("the officer's state has not advanced "
                         "in %.0f s; killing it - a hung "
                         "officer must not hold the tuner"
                         % self.heartbeat_timeout_s)
                self._terminate(proc)
                return "killed-heartbeat", (
                    "no state progress in %.0f s"
                    % self.heartbeat_timeout_s)
        return "exited", proc.returncode

    def _terminate(self, proc):
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            return
        try:
            proc.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    # -------------------------------------------------- main loop
    def run(self):
        pointer = load_pointer(self.home)
        if pointer is None:
            link = os.path.join(self.root, "current")
            if os.path.islink(link):
                target = os.path.basename(os.readlink(link))
                pointer = {
                    "format": 1,
                    "current": {"hash": target,
                                "status": "successful"},
                    "previous": None, "probationary": None,
                    "bad": [], "failures": [], "degraded": None}
                save_pointer(self.home, pointer)
                self.log("wrote the first pointer from the "
                         "current symlink: generation %s"
                         % target)
            else:
                raise SystemExit(
                    "keeper: no generation pointer in the "
                    "home and no current symlink in the "
                    "install; run keeper/bootstrap.py first")
        self._prune_snapshots()
        while True:
            pointer = load_pointer(self.home)
            gen = pointer["current"]["hash"]
            watch_id = self.current_watch()
            state = self.watch_state(watch_id)
            if state and state.get("complete"):
                self.log("watch %s is complete; the keeper "
                         "stands down" % watch_id)
                return 0
            resume = watch_id if (state
                                  and not state.get("complete")) \
                else None
            proc = self.start_officer(gen, resume_id=resume)
            outcome, detail = self.supervise(proc)
            watch_id = self.current_watch() or watch_id
            state = self.watch_state(watch_id)
            if state and state.get("complete"):
                if pointer["current"].get("status") \
                        == "probationary":
                    self._mark_successful(pointer, watch_id)
                self.log("watch %s completed under generation "
                         "%s" % (watch_id, gen))
                return 0
            handover = _read_json(os.path.join(
                self.home, HANDOVER_FILENAME), default=None)
            if handover and handover.get("to_generation") \
                    and handover.get("to_generation") != gen \
                    and os.path.isdir(os.path.join(
                        self.root, "releases",
                        handover["to_generation"])):
                target = handover["to_generation"]
                pointer["previous"] = {
                    "hash": gen, "status": "successful"}
                pointer["current"] = {"hash": target,
                                      "status": "probationary"}
                pointer["probationary"] = {
                    "hash": target, "watch_id": watch_id,
                    "snapshot": handover.get("snapshot"),
                    "since": time.time()}
                save_pointer(self.home, pointer)
                flip_symlink(self.root, target)
                self.log("handover: generation %s adopts "
                         "watch %s under probation (snapshot "
                         "%s)" % (target, watch_id,
                                  handover.get("snapshot")))
                continue
            if pointer["current"].get("status") \
                    == "probationary":
                if outcome == "exited":
                    probation = _read_json(os.path.join(
                        self.home, PROBATION_FILENAME),
                        default=None)
                    if probation and probation.get(
                            "status") == "passed" and probation \
                            .get("generation") == gen:
                        self._mark_successful(pointer, watch_id)
                        self.log("probation passed; generation "
                                 "%s is successful, and the "
                                 "watch stands interrupted - "
                                 "run the keeper again to resume "
                                 "it" % gen)
                        return 0
                return self._rollback(pointer, gen, watch_id,
                                      outcome, detail)
            # No handover, no probation: the officer is gone
            # and the watch stands interrupted.
            if outcome == "exited" and detail == 0 \
                    and self.restarts > 0:
                # A clean exit with an incomplete watch and no
                # handover is not expected; spend one restart
                # before calling it an interruption.
                self.restarts -= 1
                self.log("the officer exited cleanly with "
                         "watch %s incomplete and no handover; "
                         "restarting it (%d restarts left)"
                         % (watch_id, self.restarts))
                continue
            if outcome != "exited" and self.restarts > 0:
                self.restarts -= 1
                self.log("the officer died (%s); restarting "
                         "the watch's officer (%d restarts "
                         "left)" % (detail, self.restarts))
                continue
            if watch_id is None:
                self.log("the officer exited before any watch "
                         "stood (exit %s); nothing is standing, "
                         "and the keeper stops - the officer's "
                         "own output above carries the reason"
                         % (detail,))
                return 2
            self.log("watch %s stands interrupted; resume it "
                     "by running the keeper again, or run the "
                     "generation's officer directly with watch "
                     "--resume %s" % (watch_id, watch_id))
            return 1 if outcome != "exited" else (detail or 1)

    def _mark_successful(self, pointer, watch_id):
        probationary = pointer.get("probationary") or {}
        snapshot = probationary.get("snapshot")
        pointer["current"]["status"] = "successful"
        pointer["probationary"] = None
        save_pointer(self.home, pointer)
        if snapshot:
            shutil.rmtree(os.path.join(
                self.home, SNAPSHOT_DIRNAME, snapshot),
                ignore_errors=True)
        try:
            os.remove(os.path.join(self.home,
                                   PROBATION_FILENAME))
        except OSError:
            pass

    def _rollback(self, pointer, gen, watch_id, outcome, detail):
        probation = _read_json(os.path.join(
            self.home, PROBATION_FILENAME), default=None)
        if probation and probation.get("reason"):
            reason = probation["reason"]
        elif outcome == "probation-failed":
            reason = detail or "probation failed"
        elif outcome == "killed-heartbeat":
            reason = ("the officer hung during probation: %s"
                      % detail)
        else:
            reason = ("the officer exited during probation "
                      "before probation passed (exit %s)"
                      % (detail,))
        previous = pointer.get("previous")
        probationary = pointer.get("probationary") or {}
        snapshot = probationary.get("snapshot")
        now = time.time()
        pointer["failures"] = [
            t for t in pointer.get("failures", [])
            if now - t < INTENSITY_WINDOW_S] + [now]
        degraded = pointer.get("degraded")
        if len(pointer["failures"]) >= INTENSITY_LIMIT \
                and not degraded:
            degraded = {
                "at": now,
                "reason": "%d failed activations in 24 hours"
                          % len(pointer["failures"])}
            pointer["degraded"] = degraded
        if gen not in pointer.get("bad", []):
            pointer.setdefault("bad", []).append(gen)
        if snapshot:
            if restore_snapshot(self.home, snapshot):
                self.log("restored the pre-handover snapshot "
                         "%s" % snapshot)
            else:
                self.log("WARNING: snapshot %s could not be "
                         "restored; the rollback proceeds on "
                         "the standing state" % snapshot)
        if not previous:
            save_pointer(self.home, pointer)
            self.log("probation failed for generation %s and "
                     "no previous generation stands by; the "
                     "watch stands interrupted and the failure "
                     "is on record" % gen)
            return 1
        target = previous["hash"]
        _write_json(os.path.join(
            self.home, ROLLBACK_NOTE_FILENAME), {
                "watch_id": watch_id, "from_generation": gen,
                "to_generation": target, "reason": reason,
                "snapshot": snapshot,
                "degraded": bool(degraded), "at": now})
        pointer["current"] = {"hash": target,
                              "status": "successful"}
        pointer["previous"] = None
        pointer["probationary"] = None
        save_pointer(self.home, pointer)
        flip_symlink(self.root, target)
        # The intent that produced the failed generation is
        # consumed by the rollback: the activation, the
        # handover record and any rollback request all named
        # the bad generation as their target, and the resumed
        # officer must never act on them again.
        for name in (PROBATION_FILENAME, ACTIVATION_FILENAME,
                     HANDOVER_FILENAME,
                     ROLLBACK_REQUEST_FILENAME):
            try:
                os.remove(os.path.join(self.home, name))
            except OSError:
                pass
        self.log("rolled back: generation %s resumes watch "
                 "%s; generation %s is marked bad (%s)"
                 % (target, watch_id, gen, reason))
        proc = self.start_officer(target, resume_id=watch_id)
        outcome, detail = self.supervise(proc)
        state = self.watch_state(watch_id)
        if state and state.get("complete"):
            self.log("watch %s completed under generation %s "
                     "after the rollback" % (watch_id, target))
            return 0
        self.log("watch %s stands interrupted after the "
                 "rollback; resume it by running the keeper "
                 "again" % watch_id)
        return 1

    def _prune_snapshots(self):
        store = os.path.join(self.home, SNAPSHOT_DIRNAME)
        try:
            names = sorted(os.listdir(store))
        except OSError:
            return
        for name in names[:-2]:
            shutil.rmtree(os.path.join(store, name),
                          ignore_errors=True)


# ------------------------------------------------------------ commands

def _home_from(args):
    home = args.get("home") or os.environ.get(
        "MULTIDEX_HOME") or os.path.join(
            os.path.expanduser("~"), ".multidex")
    return home


def cmd_status(root, home):
    pointer = load_pointer(home)
    if pointer is None:
        print("no generation pointer in %s" % home)
        return 0
    print("current generation: %s (%s)"
          % (pointer["current"]["hash"],
             pointer["current"]["status"]))
    prev = pointer.get("previous")
    print("previous generation: %s"
          % (prev["hash"] if prev else "none"))
    prob = pointer.get("probationary")
    print("probationary: %s"
          % (prob["hash"] if prob else "none"))
    print("marked bad: %s"
          % (", ".join(pointer.get("bad") or []) or "none"))
    if pointer.get("degraded"):
        print("posture degraded: %s"
              % pointer["degraded"]["reason"])
    return 0


def cmd_rollback(root, home):
    keeper = Keeper(root, home, "default")
    if keeper.watch_live():
        print("a watch is standing; use `multidex update "
              "rollback` so the officer hands over at a dwell "
              "boundary")
        return 1
    pointer = load_pointer(home)
    if pointer is None or not pointer.get("previous"):
        print("no previous generation stands by; nothing to "
              "roll back to")
        return 1
    target = pointer["previous"]["hash"]
    pointer["previous"] = pointer["current"]
    pointer["current"] = {"hash": target,
                          "status": "successful"}
    pointer["probationary"] = None
    save_pointer(home, pointer)
    flip_symlink(root, target)
    print("rolled back: generation %s is current" % target)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))
    args = {"home": None, "profile": "default", "poll_s": 2.0,
            "heartbeat_timeout_s": 1800.0}
    command = "run"
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--home":
            i += 1
            args["home"] = argv[i]
        elif arg == "--profile":
            i += 1
            args["profile"] = argv[i]
        elif arg == "--poll-s":
            i += 1
            args["poll_s"] = float(argv[i])
        elif arg == "--heartbeat-timeout-s":
            i += 1
            args["heartbeat_timeout_s"] = float(argv[i])
        elif arg in ("status", "rollback"):
            command = arg
        else:
            sys.stderr.write("unknown argument: %s\n" % arg)
            return 2
        i += 1
    home = _home_from(args)
    if command == "status":
        return cmd_status(root, home)
    if command == "rollback":
        return cmd_rollback(root, home)
    keeper = Keeper(root, home, args["profile"],
                    poll_s=args["poll_s"],
                    heartbeat_timeout_s=args[
                        "heartbeat_timeout_s"])
    try:
        return keeper.run()
    except KeyboardInterrupt:
        keeper.log("the keeper was interrupted; the officer "
                   "keeps its watch - stopping the keeper "
                   "does not stop the watch")
        return 130


if __name__ == "__main__":
    sys.exit(main())
