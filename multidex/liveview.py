"""The live terminal dashboard (0.4.0): a pure viewer over a watch.

`multidex status --live` attaches this view to the current watch,
and `multidex watch` on a terminal hands the screen to it once the
watch stands. The view reads the watch's on-disk state and Index
about once a second (the view model in dashboard.py does the
reading and the deriving) and writes nothing: quitting it with q or
Ctrl-C never touches the watch, which keeps running under its
officer. When curses or a usable terminal is unavailable, the caller
gets the reason and falls back to the plain five-line status.
"""

import sys
import time

from . import dashboard, products, util

REFRESH_S = 1.0


def curses_available():
    """(ok, reason): can this Python even attempt a curses view?"""
    try:
        import curses  # noqa: F401
    except Exception as exc:
        return False, "curses is not available in this Python (%s)" \
            % exc
    return True, None


# ------------------------------------------------------------------ draw

def _palette(curses):
    pal = {"normal": 0, "head": curses.A_BOLD, "dim": curses.A_DIM,
           "flash": curses.A_REVERSE | curses.A_BOLD}
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            pal["confirmed"] = curses.color_pair(1) | curses.A_BOLD
            pal["probable"] = curses.color_pair(2)
            pal["unresolved"] = curses.A_DIM
            pal["head"] = curses.color_pair(3) | curses.A_BOLD
            pal["active"] = curses.color_pair(1) | curses.A_BOLD
            pal["benched"] = curses.color_pair(4)
            pal["bad"] = curses.color_pair(4) | curses.A_BOLD
        except Exception:
            pass
    pal.setdefault("confirmed", curses.A_BOLD)
    pal.setdefault("probable", 0)
    pal.setdefault("unresolved", curses.A_DIM)
    pal.setdefault("active", curses.A_BOLD)
    pal.setdefault("benched", curses.A_DIM)
    pal.setdefault("bad", curses.A_BOLD)
    return pal


def _put(win, y, x, text, attr, width):
    if y < 0 or x >= width or not text:
        return
    try:
        win.addnstr(y, x, text, max(0, width - x), attr)
    except Exception:
        pass  # a corner write on a shrinking terminal is not fatal


def _row(win, y, segments, width):
    x = 0
    for text, attr in segments:
        if x >= width:
            break
        _put(win, y, x, text, attr, width)
        x += len(text)


def _countdown(seconds):
    return util.fmt_duration(max(0.0, seconds))


def _header_rows(view, pal, width):
    status_word = {"running": "RUNNING", "complete": "COMPLETE",
                   "interrupted": "INTERRUPTED"}[view["status"]]
    status_attr = {"running": pal["active"], "complete": pal["head"],
                   "interrupted": pal["bad"]}[view["status"]]
    title = "MULTIDEX - LIVE WATCH"
    right = "%s  [%s]" % (view["watch_id"], status_word)
    rows = [[(title, pal["head"]),
             (" " * max(1, width - len(title) - len(right)), 0),
             (right, status_attr)]]
    if view["source"] == "real":
        source = "REAL receiver"
    elif view["source"] == "simulated":
        source = "SIMULATED - synthetic traffic, no hardware"
    else:
        source = str(view["source"])
    line = "Source: %s" % source
    if view["region"]:
        line += " - %s" % view["region"]
    if view["profile_name"]:
        line += " - profile %s" % view["profile_name"]
    if view["source_note"]:
        line += " - %s" % view["source_note"]
    rows.append([(line, pal["normal"])])
    if view["status"] == "complete":
        clock = "Stood down %s" % util.fmt_stamp(
            view["start"] + view["elapsed_s"])
    elif view["planned_end"]:
        clock = "Ends %s" % util.fmt_stamp(view["planned_end"])
    else:
        clock = ""
    rows.append([("Elapsed %s - Remaining %s%s"
                  % (util.fmt_duration(view["elapsed_s"]),
                     util.fmt_duration(view["remaining_s"]),
                     (" - " + clock) if clock else ""), pal["normal"])])
    bar_w = max(10, min(width - 10, 60))
    filled = int(round(bar_w * max(0.0, min(1.0, view["progress"]))))
    rows.append([("[%s%s] %d%%" % ("#" * filled, "-" * (bar_w - filled),
                                   int(round(100 * view["progress"]))),
                  pal["normal"])])
    return rows


def _tuner_rows(view, pal):
    rows = [[("TUNER", pal["head"])]]
    cur = view["current"]
    if view["status"] == "complete":
        now = "Now: stood down - the watch is complete"
    elif cur:
        now = "Now: %s - %s - %s - %s - %s left" % (
            cur["label"], ", ".join(cur["desks"]) or "no desks",
            util.fmt_hz(cur["center_hz"]), cur["kind_label"],
            _countdown(cur["left_s"]))
    elif view["state_current"] and view["state_current"] != "at muster":
        now = "Now: between dwells (last: %s)" % view["state_current"]
    else:
        now = "Now: at muster"
    rows.append([(now, pal["normal"])])
    nxt = view["next"]
    if nxt:
        rows.append([("Next: %s - %s - in %s"
                      % (nxt["label"], util.fmt_hz(nxt["center_hz"]),
                         _countdown(nxt["in_s"])), pal["normal"])])
    else:
        rows.append([("Next: nothing left on the bill", pal["dim"])])
    na = view["next_a"]
    if na:
        name = na["pass_sat"] or na["label"]
        rows.append([("Next clock event: %s - %s - in %s"
                      % (name, util.fmt_hz(na["center_hz"]),
                         _countdown(na["in_s"])), pal["normal"])])
    else:
        rows.append([("Next clock event: none left this watch",
                      pal["dim"])])
    return rows


def _totals_rows(view, pal):
    t = view["totals"]
    line = ("Events %d - Dossiers %d - FLASH %d - Snippets %d - "
            "Coverage %s of %s planned dwell - Gaps %d"
            % (t["events"], t["dossiers"], t["flash"], t["snippets"],
               util.fmt_duration(t["executed_s"]),
               util.fmt_duration(t["planned_s"]), t["gaps"]))
    rows = [[(line, pal["normal"])]]
    if t.get("budget_allocated_bytes") is not None:
        rows.append([("Reception budget %d of %d bytes consumed"
                      % (t["budget_consumed_bytes"] or 0,
                         t["budget_allocated_bytes"]),
                      pal["normal"])])
    return rows


def _feed_rows(view, pal, limit):
    rows = [[("EVENT FEED - newest first", pal["head"])]]
    grade_attr = {"CONFIRMED": pal["confirmed"],
                  "PROBABLE": pal["probable"],
                  "UNRESOLVED": pal["unresolved"]}
    for ev in view["feed"][:limit]:
        attr = pal["flash"] if ev["flash"] else pal["normal"]
        marker = "FLASH " if ev["flash"] else ""
        rows.append([
            ("%s " % ev["stamp"], attr or pal["dim"]),
            ("%s%-10s " % (marker, ev["grade"]),
             attr or grade_attr.get(ev["grade"], pal["normal"])),
            ("%s: %s" % (ev["desk"], ev["content"]), attr),
        ])
    if not view["feed"]:
        rows.append([("no events filed yet", pal["dim"])])
    return rows


def _desk_rows(view, pal, width):
    rows = [[("DESKS", pal["head"])]]
    word = {"active": "ACTIVE", "standing": "standing",
            "benched": "BENCHED"}
    attr = {"active": pal["active"], "standing": pal["normal"],
            "benched": pal["benched"]}
    cells = [(d["name"], word[d["state"]], attr[d["state"]])
             for d in view["desks"]]
    cell_w = max(len(n) + len(w) for n, w, _a in cells) + 3
    cols = max(1, width // max(12, cell_w))
    for i in range(0, len(cells), cols):
        row = []
        for name, w, a in cells[i:i + cols]:
            row.append(("%-*s" % (cell_w, "%s %s" % (name, w)), a))
        rows.append(row)
    for d in view["desks"]:
        if d["state"] == "benched":
            rows.append([("benched: %s - %s" % (d["name"], d["reason"]),
                          pal["benched"])])
    return rows


def _band_rows(view, pal):
    rows = [[("BAND ACTIVITY - recent sweep and census detections",
              pal["head"])]]
    if view["bands"]:
        for band in view["bands"]:
            rows.append([("%-28s %s %d"
                          % (band["name"], "#" * band["bar"],
                             band["count"]), pal["normal"])])
    else:
        rows.append([(view["bands_note"] or "no detections yet",
                      pal["dim"])])
    return rows


def _draw(win, view, err, pal):
    import curses
    height, width = win.getmaxyx()
    win.erase()
    footer = ("q - quit view (the watch keeps running) - reattach: "
              "multidex status --live - stop the watch: multidex stop")
    if height < 5 or width < 40:
        _put(win, 0, 0, "Terminal too small for the live view.",
             pal["bad"], width)
        if height > 1:
            _put(win, 1, 0, "Use: multidex status", pal["normal"],
                 width)
        win.refresh()
        return
    if view is None:
        _put(win, 0, 0, "MULTIDEX - LIVE WATCH", pal["head"], width)
        _put(win, 2, 0, "The watch state is not readable yet.",
             pal["normal"], width)
        if err:
            _put(win, 3, 0, "refresh failed: %s" % err, pal["bad"],
                 width)
        _put(win, height - 1, 0, footer, pal["dim"], width)
        win.refresh()
        return
    header = _header_rows(view, pal, width)
    tuner = _tuner_rows(view, pal)
    totals = _totals_rows(view, pal)
    desks = _desk_rows(view, pal, width)
    bands = _band_rows(view, pal)
    # Budget: header, tuner and totals are fixed; the feed takes
    # about half of what remains, desks and bands share the rest,
    # and the footer (plus an error line when a refresh failed)
    # always lands.
    fixed = len(header) + len(tuner) + len(totals) + 2  # feed head + foot
    room = max(0, height - 1 - fixed)
    feed_n = max(1, room // 2)
    rest = room - feed_n
    desk_take = min(len(desks), max(1, rest * 2 // 3))
    band_take = min(len(bands), max(0, rest - desk_take))
    feed = _feed_rows(view, pal, feed_n)
    sections = [header, tuner, totals, feed,
                desks[:desk_take], bands[:band_take]]
    y = 0
    for section in sections:
        for row in section:
            if y >= height - 1:
                break
            _row(win, y, row, width)
            y += 1
        if y >= height - 1:
            break
    if err:
        _put(win, height - 2, 0,
             "refresh failed (%s) - showing the last good frame" % err,
             pal["bad"], width)
    _put(win, height - 1, 0, footer, pal["dim"], width)
    win.refresh()


# ------------------------------------------------------------------- loop

def _loop(stdscr, home, watch_id, marker):
    import curses
    try:
        curses.curs_set(0)
    except Exception:
        pass
    pal = _palette(curses)
    stdscr.timeout(200)
    view = None
    err = None
    last_fetch = 0.0
    while True:
        try:
            now = time.time()
            if view is None or now - last_fetch >= REFRESH_S:
                try:
                    view = dashboard.load_view(home, watch_id, now=now)
                    err = None
                except Exception as exc:
                    err = "%s: %s" % (type(exc).__name__, exc)
                last_fetch = now
            _draw(stdscr, view, err, pal)
            marker["drew"] = True
            key = stdscr.getch()
        except KeyboardInterrupt:
            return
        if key in (ord("q"), ord("Q")):
            return


def attach(home, watch_id):
    """Run the dashboard for one watch until the viewer quits.

    Returns (ran, reason): ran is True when the view owned the
    terminal and exited by the viewer's choice (q or Ctrl-C); False,
    with the reason, when the view could not start at all.
    """
    ok, reason = curses_available()
    if not ok:
        return False, reason
    import curses
    marker = {"drew": False}
    try:
        curses.wrapper(_loop, home, watch_id, marker)
    except KeyboardInterrupt:
        return True, None
    except Exception as exc:
        if marker["drew"]:
            return True, None
        return False, ("the terminal could not enter full-screen "
                       "mode (%s: %s)" % (type(exc).__name__, exc))
    return True, None


def run_live(home):
    """The status --live verb: attach, or fall back with the reason
    stated and the plain five-line status printed."""
    watch_id = products.resolve_current_watch(home, allow_complete=True)
    state = products._watch_state(home, watch_id) if watch_id else None
    if not watch_id or state is None:
        print(products.status_text(home))
        return 0
    if not sys.stdout.isatty():
        print("Live view needs an interactive terminal; showing the "
              "plain status instead.")
        print(products.status_text(home))
        return 0
    ran, reason = attach(home, watch_id)
    if not ran:
        print("Live view unavailable: %s" % reason)
        print(products.status_text(home))
    return 0
