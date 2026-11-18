"""The command surface: seven verbs and nothing else (design 11).

No verb asks a question back. Anything a verb needs comes from the
profile, the defaults, or its arguments; anything it cannot get is a
refusal with the reason.
"""

import argparse
import os
import sys
import threading
import time

from . import gates, products, util
from .index import Index
from .officer import WatchRunner, build_bill
from .profile import ProfileError, load_profile

_BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _home(args):
    home = args.home or os.environ.get("MULTIDEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".multidex")
    os.makedirs(home, exist_ok=True)
    return home


def _profile_path(args, home):
    name = args.profile
    if os.path.exists(name):
        return name
    for candidate in (os.path.join(home, "profiles", name + ".toml"),
                      os.path.join(_BUILD_ROOT, "profiles",
                                   name + ".toml")):
        if os.path.exists(candidate):
            return candidate
    if name == "default":
        return None  # built-in defaults carry the same values
    raise ProfileError("profile not found: %s" % name)


def _load(args, home):
    path = _profile_path(args, home)
    if path:
        # The officer's update-check worker re-loads the same
        # profile in its own process; the resolved path rides
        # the environment so both read one file.
        os.environ["MULTIDEX_PROFILE_PATH"] = path
    return load_profile(path)


def _cmd_watch_tty(args, home, profile):
    """The watch verb on a terminal: the officer runs the watch as
    always, and once muster publishes the first state the terminal
    becomes the live dashboard. Quitting the view leaves the watch
    running in this terminal; the completion line prints when the
    watch stands down, exactly as without the view.
    """
    from . import liveview
    box = {}

    def officer():
        # The Index and its SQLite connection are born and used on
        # this thread alone, exactly as the single-threaded path
        # uses them on the main thread.
        index = Index(home)
        try:
            runner = WatchRunner(home, profile, index)
            box["runner"] = runner
            box["result"] = runner.start(resume_id=args.resume)
        except Exception as exc:  # re-raised on this thread below
            box["error"] = exc
        finally:
            index.close()

    thread = threading.Thread(target=officer, daemon=True)
    thread.start()
    watch_id = None
    while thread.is_alive():
        runner = box.get("runner")
        wid = getattr(runner, "watch_id", None) if runner else None
        if wid and products._watch_state(home, wid) is not None:
            watch_id = wid
            break
        time.sleep(0.05)
    if watch_id and thread.is_alive():
        ran, reason = liveview.attach(
            home, watch_id,
            keep_awake=getattr(args, "keep_awake", False))
        if ran:
            print("View closed. Watch %s keeps running in this "
                  "terminal." % watch_id)
            print("Reattach the live view with: multidex status "
                  "--live")
            print("Stop the watch with: multidex stop")
        else:
            print("Live view unavailable: %s. The watch is running; "
                  "multidex status shows its state." % reason)
    thread.join()
    if "error" in box:
        raise box["error"]
    result = box.get("result") or {}
    if not result.get("ok"):
        print(result.get("refusal", "the watch did not stand"))
        return 2
    if result.get("handed_over"):
        print("Watch %s handed over to generation %s at a dwell "
              "boundary; the keeper starts the successor, which "
              "adopts the watch from the record."
              % (result["watch_id"], result["to_generation"]))
        return 0
    print("Watch %s stood and completed. Products are in %s"
          % (result["watch_id"],
             os.path.join(home, "watches", result["watch_id"])))
    return 0


def cmd_watch(args):
    home = _home(args)
    profile = _load(args, home)
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _cmd_watch_tty(args, home, profile)
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(resume_id=args.resume)
    finally:
        index.close()
    if not result["ok"]:
        print(result["refusal"])
        return 2
    if result.get("handed_over"):
        print("Watch %s handed over to generation %s at a dwell "
              "boundary; the keeper starts the successor, which "
              "adopts the watch from the record."
              % (result["watch_id"], result["to_generation"]))
        return 0
    print("Watch %s stood and completed. Products are in %s"
          % (result["watch_id"],
             os.path.join(home, "watches", result["watch_id"])))
    return 0


def cmd_plan(args):
    home = _home(args)
    profile = _load(args, home)
    sats, _path = gates.load_sats(profile)
    start = time.time()
    bill = build_bill(profile, start, sats)
    counts = {}
    for item in bill:
        counts[item["cls"]] = counts.get(item["cls"], 0) + 1
    print("Watch bill for %s starting %s: %d items (class A %d, B %d, "
          "C %d)" % (profile["name"], util.fmt_stamp(start), len(bill),
                     counts.get("A", 0), counts.get("B", 0),
                     counts.get("C", 0)))
    for item in bill:
        print("- %s [%s/%s] %s - %s (%s)"
              % (util.fmt_time(item["start"]), item["cls"], item["kind"],
                 item["label"], util.fmt_hz(item["center_hz"]),
                 ", ".join(item["desks"]) or "no desks"))
    return 0


def cmd_status(args):
    home = _home(args)
    if getattr(args, "live", False):
        from . import liveview
        return liveview.run_live(
            home, keep_awake=getattr(args, "keep_awake", False))
    print(products.status_text(home))
    from . import updater
    # The update account rides the plain status only where a
    # generation layout exists; a single-tree install's status
    # stays exactly the account it has always printed, and its
    # update mode is stated by doctor and by `update status`.
    if updater.install_root() is not None:
        try:
            profile = _load(args, home)
        except ProfileError:
            profile = None
        for line in updater.status_lines(home, profile):
            print(line)
    return 0


def cmd_sitrep(args):
    home = _home(args)
    index = Index(home)
    try:
        watch_id = products.resolve_current_watch(home,
                                                  allow_complete=True)
        if not watch_id:
            print("No watch exists yet; there is nothing to report.")
            return 1
        meta = index.get_watch_meta(watch_id) or {}
        evs = index.events(watch_id=watch_id, limit=100000)
        t_to = max([e["t_start"] for e in evs],
                   default=meta.get("start", time.time()))
        seq = len([f for f in os.listdir(index.watch_dir(watch_id))
                   if f.startswith("SITREP")]) + 1
        text = products.render_sitrep(index, watch_id,
                                      meta.get("start", t_to), t_to, seq)
        path = os.path.join(index.watch_dir(watch_id),
                            "SITREP-%d.md" % seq)
        util.write_text(path, text)
        print("SITREP written to %s" % path)
        return 0
    finally:
        index.close()


def cmd_stop(args):
    home = _home(args)
    # The stop must land in the folder of the watch actually
    # running: a watch is running only when the pid its state file
    # recorded is still alive. A dead watch gets no flag and no
    # promise of a stand-down; the pointer alone proves nothing.
    live_id, claimed_id = products.watch_liveness(home)
    if live_id:
        watch_id = live_id
    elif claimed_id:
        print("No watch is running. Watch %s stands interrupted; "
              "there is no officer process to stand down."
              % claimed_id)
        return 1
    else:
        watch_id = products.resolve_current_watch(home)
        if not watch_id \
                or products._watch_state(home, watch_id) is None:
            print("No watch is running.")
            return 1
    flag = os.path.join(home, "watches", watch_id, "stop.flag")
    with open(flag, "w") as fh:
        fh.write("stop requested\n")
    print("Stop requested for %s. The watch stands down at the next "
          "dwell boundary and renders its Watch Report for the elapsed "
          "portion." % watch_id)
    return 0


def cmd_ask(args):
    home = _home(args)
    index = Index(home)
    try:
        print(products.ask(index, " ".join(args.query)))
        return 0
    finally:
        index.close()


def cmd_doctor(args):
    home = _home(args)
    profile = _load(args, home)
    text = gates.doctor_text(profile, home)
    print(text)
    return 0 if "FAIL" not in text else 1


def cmd_update(args):
    from . import updater
    home = _home(args)
    profile = _load(args, home)
    action = args.update_action or "status"
    if action == "check":
        gen, _root = updater.own_generation()
        try:
            with updater.CheckLock(home):
                report = updater.run_check(home, profile,
                                           running_hash=gen)
        except updater.UpdateError as exc:
            print(str(exc))
            return 1
        print(report["outcome"])
        return 0
    if action == "status":
        for line in updater.status_lines(home, profile):
            print(line)
        return 0
    if action == "approve":
        print(updater.approve_candidate(home, profile))
        return 0
    if action == "reset":
        print(updater.reset_failures(home))
        return 0
    if action == "rollback":
        print(updater.request_rollback(home, profile))
        return 0
    print("unknown update action: %s" % action)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="multidex",
        description="MULTIDEX - an autonomous 24-hour signals watch "
                    "on a single RTL-SDR.")
    parser.add_argument("--home", default=None,
                        help="data home (default: $MULTIDEX_HOME or "
                             "~/.multidex)")
    parser.add_argument("--profile", default="default",
                        help="profile name or path (default: default)")
    sub = parser.add_subparsers(dest="verb", required=True)
    p_watch = sub.add_parser("watch", help="stand the 24-hour watch")
    p_watch.add_argument("--resume", default=None,
                         help="resume an interrupted watch by id")
    p_watch.add_argument("--keep-awake", action="store_true",
                         help="hold the display awake while the "
                              "console is on the terminal")
    p_watch.set_defaults(fn=cmd_watch)
    sub.add_parser("plan", help="print the watch bill").set_defaults(
        fn=cmd_plan)
    p_status = sub.add_parser("status", help="state of the running "
                                             "watch")
    p_status.add_argument("--live", action="store_true",
                          help="attach the live console to the "
                               "current watch")
    p_status.add_argument("--keep-awake", action="store_true",
                          help="with --live, hold the display "
                               "awake while the console runs")
    p_status.set_defaults(fn=cmd_status)
    sub.add_parser("sitrep", help="render a SITREP now").set_defaults(
        fn=cmd_sitrep)
    sub.add_parser("stop", help="stand the watch down now").set_defaults(
        fn=cmd_stop)
    p_ask = sub.add_parser("ask", help="query the Index")
    p_ask.add_argument("query", nargs="+")
    p_ask.set_defaults(fn=cmd_ask)
    sub.add_parser("doctor", help="muster checks and desk inventory") \
        .set_defaults(fn=cmd_doctor)
    p_update = sub.add_parser(
        "update", help="live updates: check, status, approve, "
                       "reset, rollback")
    p_update.add_argument("update_action", nargs="?",
                          default="status",
                          choices=["check", "status", "approve",
                                   "reset", "rollback"])
    p_update.set_defaults(fn=cmd_update)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ProfileError as exc:
        print("Profile refusal: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
