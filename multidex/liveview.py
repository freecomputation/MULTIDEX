"""The Console's curses layer (0.8.0): a pure viewer over a watch.

`multidex status --live` attaches the Console to the current
watch, and `multidex watch` on a terminal hands the screen to it
once the watch stands. The frame itself - allotment, panels -
is built by the pure frame builder in console.py
from the view model in dashboard.py and the display feed the
officer publishes; this layer only draws what it is given,
about once a second, and writes nothing to the watch. Quitting
with q or Ctrl-C never touches the watch, which keeps running
under its officer. A render failure withdraws to a plain error
line and files its complaint to the home's console.log, off the
watch's record. When curses or a usable terminal is unavailable,
the caller gets the reason and falls back to the plain status.

Under --keep-awake the Console also holds the display awake
through the host's inhibitor program (see keepawake.py, and its
dependence census entries); the hold's state rides the header,
stated plainly when the host cannot honour it, and is released
when the view exits.
"""

import os
import sys
import time

from . import console, keepawake, products

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
           "flash": curses.A_REVERSE | curses.A_BOLD,
           "confirmed": curses.A_BOLD, "probable": 0,
           "unresolved": curses.A_DIM, "active": curses.A_BOLD,
           "benched": curses.A_DIM, "bad": curses.A_BOLD}
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_BLUE, -1)
            curses.init_pair(7, curses.COLOR_WHITE, -1)
            pal["confirmed"] = curses.color_pair(1) | curses.A_BOLD
            pal["probable"] = curses.color_pair(2)
            pal["head"] = curses.color_pair(3) | curses.A_BOLD
            pal["active"] = curses.color_pair(1) | curses.A_BOLD
            pal["benched"] = curses.color_pair(4)
            pal["bad"] = curses.color_pair(4) | curses.A_BOLD
        except Exception:
            pass
    return pal


def _put(win, y, x, text, attr, width):
    if y < 0 or x >= width or not text:
        return
    try:
        win.addnstr(y, x, text, max(0, width - x), attr)
    except Exception:
        pass  # a corner write on a shrinking terminal is not fatal


def _draw_frame(win, frame, pal, width):
    win.erase()
    for y, row in enumerate(frame):
        x = 0
        for text, attr in row:
            if x >= width:
                break
            _put(win, y, x, text, pal.get(attr, 0), width)
            x += len(text)
    win.refresh()


def _log_error(home, exc):
    """The Console's own complaint file, off the watch's record."""
    try:
        with open(os.path.join(home, "console.log"), "a") as fh:
            fh.write("%s console render failed: %s: %s\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.gmtime()),
                        type(exc).__name__, exc))
    except OSError:
        pass


# ------------------------------------------------------------------- loop

def _loop(stdscr, home, watch_id, marker, hold):
    import curses
    try:
        curses.curs_set(0)
    except Exception:
        pass
    pal = _palette(curses)
    stdscr.timeout(200)
    view = None
    feed = None
    err = None
    last_fetch = 0.0
    while True:
        try:
            now = time.time()
            if view is None or now - last_fetch >= REFRESH_S:
                try:
                    view, feed = console.load_frame_data(
                        home, watch_id, now=now)
                    err = None
                except Exception as exc:
                    err = "%s: %s" % (type(exc).__name__, exc)
                last_fetch = now
            height, width = stdscr.getmaxyx()
            ka_state = None
            if hold is not None:
                hold.alive()
                ka_state = hold.state
            try:
                frame = console.build_frame(view, feed, width,
                                            height,
                                            keepawake=ka_state)
            except Exception as exc:
                # A render error withdraws the Console to a plain
                # statement and files its complaint off the record;
                # the watch is never the wiser.
                _log_error(home, exc)
                frame = [[("MULTIDEX - THE CONSOLE", "head")],
                         [("The console could not render this "
                           "frame (%s: %s). The watch is "
                           "unaffected; multidex status shows its "
                           "state." % (type(exc).__name__, exc),
                           "bad")]]
                frame = console._finish(frame, width, height)
            _draw_frame(stdscr, frame, pal, width)
            if err:
                _put(stdscr, max(0, height - 2), 0,
                     "refresh failed (%s) - showing the last good "
                     "frame" % err, pal["bad"], width)
                stdscr.refresh()
            marker["drew"] = True
            key = stdscr.getch()
        except KeyboardInterrupt:
            return
        if key in (ord("q"), ord("Q")):
            return


def attach(home, watch_id, keep_awake=False):
    """Run the Console for one watch until the viewer quits.

    Returns (ran, reason): ran is True when the view owned the
    terminal and exited by the viewer's choice (q or Ctrl-C);
    False, with the reason, when the view could not start at all.
    The keep-awake hold, when requested, is taken before the
    terminal is claimed and released whatever way the view ends.
    """
    ok, reason = curses_available()
    if not ok:
        return False, reason
    import curses
    hold = keepawake.acquire() if keep_awake else None
    marker = {"drew": False}
    try:
        curses.wrapper(_loop, home, watch_id, marker, hold)
    except KeyboardInterrupt:
        return True, None
    except Exception as exc:
        if marker["drew"]:
            return True, None
        return False, ("the terminal could not enter full-screen "
                       "mode (%s: %s)" % (type(exc).__name__, exc))
    finally:
        if hold is not None:
            hold.release()
    return True, None


def run_live(home, keep_awake=False):
    """The status --live verb: attach, or fall back with the
    reason stated and the plain status printed."""
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
    ran, reason = attach(home, watch_id, keep_awake=keep_awake)
    if not ran:
        print("Live view unavailable: %s" % reason)
        print(products.status_text(home))
    return 0
