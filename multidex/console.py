"""The Console (0.8.0): MULTIDEX's terminal graphical system.

One fixed full-screen frame that redraws in place and never
scrolls: the tuner's spectrum with its history dripping down
the waterfall, the pipeline of tuner, channelizer and desks
with their real work streaming inside them, the sky panel, the
standing pool drawn as a reservoir, the graded event feed, and
the mind stream - the officer's own filed record, rendered as
it files. Everything on the glass is derived from published
state: the view model in dashboard.py and the display feed in
displayfeed.py. This module is the pure half - allotment and
frame building, no terminal, no disk of its own - and
liveview.py is the thin curses layer that draws what it
returns. A frame is a list of rows; a row is a list of
(text, attr) segments whose text never exceeds the frame's
width, and the frame always carries exactly the requested
number of rows.

The honesty rules are the design's. The waterfall renders
captures as they arrive and holds the last true frame between
captures with its age stated in its title; nothing is
interpolated and no motion is invented. Panels are allotted
from the live terminal size in the design's priority order -
header, waterfall, pipeline, sky, pool, event feed, mind
stream - and yield in the reverse of that order, the mind
stream first and the waterfall last; a panel below its minimum
disappears cleanly rather than crushing. Below the honest
minimum size the frame withdraws to the compact status
rendering, the same account the plain status verb gives.
"""

import os
import time

from . import dashboard, displayfeed, util

MIN_WIDTH = 76
MIN_HEIGHT = 22

WATER_RAMP = " ▁▂▃▄▅▆▇█"
WATER_SPAN_DB = 48.0     # the display window above the floor
HELD_AFTER_S = 90.0      # a frame older than this reads HELD

BLOCK_MIN_W = 30
BLOCK_ROWS = 3

SKY_BENCHED = (
    "No sky account has been published in this watch yet: the "
    "panel renders the officer's sky records from the display "
    "feed, and until the first record lands it claims no "
    "sidereal time, Jupiter window, solar band state or meteor "
    "rate. The hydrogen desk stands benched on its hardware "
    "gate - a dish and a low-noise amplifier - and no panel "
    "line will pretend otherwise.")

FOOTER = ("q - quit view (the watch keeps running) - reattach: "
          "multidex status --live - stop the watch: multidex stop")


# ---------------------------------------------------------------- data

def load_frame_data(home, watch_id, now=None):
    """One frame's inputs: the dashboard view model and the
    watch's display feed. (None, None) when the watch has no
    state file to view."""
    view = dashboard.load_view(home, watch_id, now=now)
    if view is None:
        return None, None
    feed = displayfeed.load_feed(os.path.join(home, "watches",
                                              watch_id))
    return view, feed


def frame_text(frame):
    """A frame as plain text rows, for headless inspection."""
    return ["".join(text for text, _attr in row) for row in frame]


# ------------------------------------------------------------- helpers

def _fit(segments, width):
    """Clip a row's segments so its text fits the width."""
    out = []
    used = 0
    for text, attr in segments:
        if used >= width:
            break
        room = width - used
        if len(text) > room:
            text = text[:room]
        if text:
            out.append((text, attr))
            used += len(text)
    return out


def _wrap(text, width, limit):
    """Greedy word wrap to at most limit lines of width."""
    lines = []
    cur = ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word[:width]
        if len(lines) >= limit:
            return lines[:limit]
    if cur and len(lines) < limit:
        lines.append(cur)
    return lines[:limit]


def _fmt_bytes(n):
    if n is None:
        return "unknown"
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(n) >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % int(n)


def _grade_attr(grade):
    return {"CONFIRMED": "confirmed", "PROBABLE": "probable",
            "UNRESOLVED": "unresolved"}.get(grade, "normal")


# -------------------------------------------------------------- header

def _header_rows(view, width, keepawake):
    status_word = {"running": "RUNNING", "complete": "COMPLETE",
                   "interrupted": "INTERRUPTED"}[view["status"]]
    status_attr = {"running": "active", "complete": "head",
                   "interrupted": "bad"}[view["status"]]
    title = "MULTIDEX - THE CONSOLE"
    right = "%s  [%s]" % (view["watch_id"], status_word)
    upd = view.get("update")
    if upd and upd.get("generation"):
        right += " - gen %s" % str(upd["generation"])[:8]
        if upd.get("status"):
            right += " %s" % upd["status"]
        prob = upd.get("probation")
        if prob:
            right += " - probation %s/%s" % (prob.get("done"),
                                             prob.get("required"))
    rows = [_fit([(title, "head"),
                  (" " * max(1, width - len(title) - len(right)),
                   "normal"),
                  (right, status_attr)], width)]
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
    if keepawake is not None:
        line += " - keep-awake: %s" % keepawake
    rows.append(_fit([(line, "normal")], width))
    if view["status"] == "complete":
        clock = "Stood down %s" % util.fmt_stamp(
            view["start"] + view["elapsed_s"])
    elif view["planned_end"]:
        clock = "Ends %s" % util.fmt_stamp(view["planned_end"])
    else:
        clock = ""
    rows.append(_fit([("Elapsed %s - Remaining %s%s"
                       % (util.fmt_duration(view["elapsed_s"]),
                          util.fmt_duration(view["remaining_s"]),
                          (" - " + clock) if clock else ""),
                       "normal")], width))
    bar_w = max(10, min(width - 10, 60))
    filled = int(round(bar_w * max(0.0, min(1.0, view["progress"]))))
    rows.append(_fit([("[%s%s] %d%%"
                        % ("#" * filled, "-" * (bar_w - filled),
                           int(round(100 * view["progress"]))),
                        "normal")], width))
    return rows


# ----------------------------------------------------------- waterfall

def _water_cells(frame, width):
    """One spectrum frame as (glyph, attr) cells across width:
    its bins max-pooled or stretched to the glass, its lock
    brackets laid over the trace in their grade's colour with
    the bracket's name written in place."""
    bins = frame["bins"]
    if len(bins) >= width:
        pooled = displayfeed.pool_bins(bins, width)
    else:
        pooled = [bins[min(len(bins) - 1,
                           int(i * len(bins) / width))]
                  for i in range(width)]
    floor = frame.get("floor_db")
    if floor is None:
        floor = min(pooled)
    cells = []
    for value in pooled:
        level = int((value - floor) / WATER_SPAN_DB * 9)
        level = max(0, min(8, level))
        cells.append([WATER_RAMP[level], "water%d" % min(7, level)])
    lo, hi = frame["lo_hz"], frame["hi_hz"]
    span = hi - lo

    def col(freq):
        return max(0, min(width - 1, int((freq - lo) / span * width)))

    for br in frame.get("brackets") or []:
        attr = _grade_attr(br.get("grade"))
        x0, x1 = col(br["lo_hz"]), col(br["hi_hz"])
        for x in range(x0, x1 + 1):
            cells[x][1] = attr
        label = br.get("name") or br.get("desk") or ""
        for i, ch in enumerate(label[:width - x0]):
            cells[x0 + i] = [ch, attr]
    return [(ch, attr) for ch, attr in cells]


def _waterfall_rows(view, feed, width, height):
    spectra = feed["spectra"]
    if not spectra:
        return [_fit([("SPECTRUM", "head")], width),
                _fit([("no capture published yet - the waterfall "
                       "fills at the first capture the desks "
                       "process", "dim")], width)]
    newest = spectra[-1]
    watch_now = view["start"] + view["elapsed_s"]
    age = max(0.0, watch_now - newest["t"])
    if view["status"] == "complete":
        state = "final capture - the watch is complete"
    elif age > HELD_AFTER_S:
        state = "HELD - last capture %s ago" % util.fmt_duration(age)
    else:
        state = "last capture %s ago" % util.fmt_duration(age)
    title = "SPECTRUM - %s to %s" % (util.fmt_hz(newest["lo_hz"]),
                                     util.fmt_hz(newest["hi_hz"]))
    rows = [_fit([(title, "head"),
                  (" " * max(1, width - len(title) - len(state)),
                   "normal"),
                  (state, "dim")], width)]
    plot_rows = max(0, height - 2)
    shown = spectra[-plot_rows:] if plot_rows else []
    for frame in reversed(shown):
        rows.append(_water_cells(frame, width))
    lo_label = util.fmt_hz(newest["lo_hz"])
    hi_label = util.fmt_hz(newest["hi_hz"])
    mid_label = util.fmt_hz((newest["lo_hz"] + newest["hi_hz"]) / 2)
    axis = [" "] * width
    for label, start in ((lo_label, 0),
                         (mid_label, (width - len(mid_label)) // 2),
                         (hi_label, width - len(hi_label))):
        for i, ch in enumerate(label):
            if 0 <= start + i < width:
                axis[start + i] = ch
    rows.append(_fit([("".join(axis), "dim")], width))
    return rows


# ------------------------------------------------------------ pipeline

def _blocks(view, feed):
    """The pipeline's blocks: tuner, channelizer, then every
    desk in the roster. Each block is BLOCK_ROWS lines of plain
    text with the attribute its first line carries."""
    work = {}
    for line in feed["work"]:
        work.setdefault(line["desk"], []).append(line["line"])
    blocks = []
    cur = view["current"]
    if view["status"] == "complete":
        tuner = ("TUNER - stood down", "the watch is complete", "")
    elif cur:
        if cur["kind"] == "interrupt":
            tuner = ("TUNER - INTERRUPT", cur["label"],
                     "displaced: %s" % (view.get("displaced_label")
                                        or "the item it preempted"))
        else:
            tuner = ("TUNER - ACTIVE", cur["label"],
                     "%s - %s left"
                     % (util.fmt_hz(cur["center_hz"]),
                        util.fmt_duration(max(0.0, cur["left_s"]))))
    elif view["state_current"] and view["state_current"] != "at muster":
        tuner = ("TUNER - between dwells",
                 "last: %s" % view["state_current"], "")
    else:
        tuner = ("TUNER - at muster", "", "")
    blocks.append((tuner, "active" if cur else "normal"))
    if cur:
        nxt = ""
        if view["next_a"]:
            nxt = ("next clock event in %s"
                   % util.fmt_duration(max(0.0,
                                           view["next_a"]["in_s"])))
        chan = ("CHANNELIZER - standing",
                "%d desks in a %s window"
                % (len(cur["desks"]), util.fmt_hz(cur["bw_hz"])),
                nxt)
    else:
        chan = ("CHANNELIZER - idle", "no window open", "")
    blocks.append((chan, "normal"))
    word = {"active": "ACTIVE", "standing": "standing",
            "benched": "BENCHED"}
    for desk in view["desks"]:
        name = desk["name"]
        first = "%s - %s" % (name, word[desk["state"]])
        if desk["state"] == "benched":
            lines = _wrap("benched: %s" % desk["reason"],
                          BLOCK_MIN_W - 2, BLOCK_ROWS - 1)
        else:
            lines = work.get(name, [])[-(BLOCK_ROWS - 1):]
        while len(lines) < BLOCK_ROWS - 1:
            lines.append("")
        attr = {"active": "active", "standing": "normal",
                "benched": "benched"}[desk["state"]]
        blocks.append(((first, lines[0], lines[1]), attr))
    return blocks


def _pipeline_full_height(view, width):
    cols = max(1, width // BLOCK_MIN_W)
    blocks = 2 + len(view["desks"])
    block_rows = -(-blocks // cols)
    return 1 + block_rows * BLOCK_ROWS


def _pipeline_rows(view, feed, width, height):
    rows = [_fit([("PIPELINE - tuner, channelizer, desks", "head")],
                 width)]
    blocks = _blocks(view, feed)
    cols = max(1, width // BLOCK_MIN_W)
    block_w = max(1, width // cols)
    usable = max(0, height - 1)
    whole = usable // BLOCK_ROWS
    for brow in range(whole):
        group = blocks[brow * cols:(brow + 1) * cols]
        if not group:
            break
        for li in range(BLOCK_ROWS):
            segments = []
            for bi, (lines, attr) in enumerate(group):
                text = lines[li][:block_w - 1]
                if bi < len(group) - 1:
                    text = text.ljust(block_w)
                segments.append((text,
                                 attr if li == 0 else "normal"))
            rows.append(_fit(segments, width))
    return rows


# ----------------------------------------------------------------- sky

def _sky_rows(feed, width, height):
    """The sky panel (0.15.0): the newest sky record from the
    display feed, rendered as published. The officer composes
    the records from the ephemeris and the desks' own filed
    tallies; the panel computes nothing, and when no record
    has landed it says so rather than standing in a bench
    note for data that now exists."""
    rows = [_fit([("SKY - the astronomical account", "head")], width)]
    records = (feed or {}).get("sky") or []
    if not records:
        for line in _wrap(SKY_BENCHED, width, max(0, height - 1)):
            rows.append(_fit([(line, "dim")], width))
        return rows
    rec = records[-1]
    lst = rec.get("lst_deg")
    lines = []
    if lst is not None:
        lines.append("local sidereal time %.1f deg (%.2f h)"
                     % (lst, lst / 15.0))
    jup = rec.get("jupiter") or {}
    if jup.get("alt_deg") is not None:
        text = "Jupiter %.0f deg up" % jup["alt_deg"]
        if jup.get("source_now"):
            text += ", in %s geometry now" % jup["source_now"]
        lines.append(text)
    if jup.get("next_window_start"):
        lines.append("next predicted window %s UTC (%s)"
                     % (time.strftime("%H:%M",
                                      time.gmtime(
                                          jup["next_window_start"])),
                        jup.get("next_source") or "geometry"))
    elif jup:
        lines.append("no predicted decametric window in the "
                     "next two days at this home")
    sol = rec.get("solar") or {}
    if sol.get("sun_alt_deg") is not None:
        lines.append("Sun %.0f deg %s the horizon%s"
                     % (abs(sol["sun_alt_deg"]),
                        "above" if sol.get("daylight") else "below",
                        "" if not sol.get("daylight") else ""))
    if sol.get("last_state"):
        lines.append("last band state: %s" % sol["last_state"])
    met = rec.get("meteor") or {}
    if met:
        if met.get("rate_per_sampled_hour") is not None:
            lines.append("meteors: %d ping(s), %.1f per sampled "
                         "hour" % (met.get("pings") or 0,
                                   met["rate_per_sampled_hour"]))
        else:
            lines.append("meteors: %d ping(s), no sampled span "
                         "yet to rate them over"
                         % (met.get("pings") or 0))
    sonde = rec.get("sonde") or {}
    if sonde.get("last_serial"):
        lines.append("sonde: last ascent %s to %.0f m"
                     % (sonde["last_serial"],
                        sonde.get("last_max_alt_m") or 0))
    else:
        lines.append("sonde: no ascent decoded yet this watch")
    for line in lines:
        for wrapped in _wrap(line, width, max(0, height - 1)):
            rows.append(_fit([(wrapped, "normal")], width))
        if len(rows) >= height:
            break
    return rows[:max(1, height)]


# ---------------------------------------------------------------- pool

def _pool_rows(view, width, height):
    rows = [_fit([("RECEPTION POOL - the standing allocation "
                   "ledger", "head")], width)]
    pool = view.get("pool")
    if pool is None:
        rows.append(_fit([("the standing ledger has published no "
                           "pool figures for this watch", "dim")],
                          width))
        return rows
    cap = pool["cap_bytes"] or 0
    if cap > 0:
        parts = (("█", pool["consumed_bytes"], "normal"),
                 ("▓", pool["reserved_bytes"], "dim"),
                 ("░", pool["free_bytes"], "dim"),
                 ("▒", pool["margin_bytes"], "normal"))
        vessel = []
        used = 0
        for glyph, n, attr in parts:
            cells = int(round(width * max(0, n) / cap))
            cells = max(0, min(width - used, cells))
            if cells:
                vessel.append((glyph * cells, attr))
                used += cells
        if used < width:
            vessel.append((" " * (width - used), "dim"))
        rows.append(_fit(vessel, width))
    rows.append(_fit([("cap %s - consumed %s - reserved %s - free "
                       "%s - never granted %s"
                       % (_fmt_bytes(pool["cap_bytes"]),
                          _fmt_bytes(pool["consumed_bytes"]),
                          _fmt_bytes(pool["reserved_bytes"]),
                          _fmt_bytes(pool["free_bytes"]),
                          _fmt_bytes(pool["margin_bytes"])),
                       "normal")], width))
    if pool.get("watch_granted_bytes") is not None:
        rows.append(_fit([("this watch - granted %s - held %s"
                           % (_fmt_bytes(pool["watch_granted_bytes"]),
                              _fmt_bytes(
                                  pool["watch_consumed_bytes"])),
                           "dim")], width))
    return rows


# ------------------------------------------------------- feed and mind

def _feed_rows(view, width, height):
    rows = [_fit([("EVENT FEED - newest first", "head")], width)]
    for ev in view["feed"][:max(0, height - 1)]:
        attr = "flash" if ev["flash"] else "normal"
        marker = "FLASH " if ev["flash"] else ""
        rows.append(_fit([
            ("%s " % ev["stamp"], attr),
            ("%s%-10s " % (marker, ev["grade"]),
             attr if ev["flash"] else _grade_attr(ev["grade"])),
            ("%s: %s" % (ev["desk"], ev["content"]), attr)], width))
    if not view["feed"]:
        rows.append(_fit([("no events filed yet", "dim")], width))
    return rows


def _mind_rows(feed, width, height):
    rows = [_fit([("MIND - the officer's record, as filed", "head")],
                 width)]
    lines = feed["thought"][-(max(0, height - 1)):]
    for th in lines:
        attr = "flash" if th.get("flash") else "normal"
        rows.append(_fit([
            ("%s " % util.fmt_time(th["t"]), "dim"),
            ("%s: " % th["desk"], attr),
            (th["content"], attr)], width))
    if not feed["thought"]:
        rows.append(_fit([("nothing filed yet - the stream moves "
                           "when the officer acts", "dim")], width))
    return rows


# -------------------------------------------------------------- compact

def _compact_rows(view, width, height):
    if height < 3 or width < 30:
        rows = [[("Terminal too small for the live view.", "bad")]]
        if height > 1:
            rows.append([("Use: multidex status", "normal")])
        return rows
    status_word = {"running": "RUNNING", "complete": "COMPLETE",
                   "interrupted": "INTERRUPTED"}[view["status"]]
    rows = [_fit([("MULTIDEX - %s [%s]"
                   % (view["watch_id"], status_word), "head")], width)]
    source = {"real": "REAL receiver",
              "simulated": "SIMULATED - synthetic traffic, no "
                           "hardware"}.get(view["source"],
                                           str(view["source"]))
    rows.append(_fit([("Source: %s" % source, "normal")], width))
    rows.append(_fit([("Elapsed %s - Remaining %s"
                       % (util.fmt_duration(view["elapsed_s"]),
                          util.fmt_duration(view["remaining_s"])),
                       "normal")], width))
    cur = view["current"]
    now_line = ("Now: %s" % cur["label"]) if cur else \
        ("Now: %s" % (view["state_current"] or "at muster"))
    rows.append(_fit([(now_line, "normal")], width))
    t = view["totals"]
    rows.append(_fit([("Events %d - Dossiers %d - FLASH %d"
                       % (t["events"], t["dossiers"], t["flash"]),
                       "normal")], width))
    pool = view.get("pool")
    if pool is not None:
        rows.append(_fit([("Reception pool %s of %s consumed"
                           % (_fmt_bytes(pool["consumed_bytes"]),
                              _fmt_bytes(pool["cap_bytes"])),
                           "normal")], width))
    rows.append(_fit([("compact status - terminal below the "
                       "console minimum of %dx%d"
                       % (MIN_WIDTH, MIN_HEIGHT), "dim")],
                      width))
    return rows


# ---------------------------------------------------------------- frame

_PANEL_MIN = {"waterfall": 5, "pipeline": 5, "sky": 3, "pool": 4,
              "feed": 3, "mind": 3}
_DROP_ORDER = ("mind", "feed", "pool", "sky", "pipeline")


def _allot(view, feed, width, height):
    """Panel heights for one frame, by the design's discipline:
    every panel at its minimum first, panels dropped from the
    bottom of the priority order when the frame cannot hold the
    minimums, then the surplus given in priority order - the
    waterfall first (it is the instrument's face), the pipeline
    to its full height, the feed and the mind stream to theirs,
    and the waterfall takes whatever remains. The waterfall's
    growth is capped by the history it actually holds: a young
    watch with two captures does not get forty rows of empty
    glass while desks wait below."""
    room = height - 5  # header 4, footer 1
    alloc = dict(_PANEL_MIN)
    for name in _DROP_ORDER:
        if sum(alloc.values()) <= room:
            break
        alloc[name] = 0
    water_cap = 2 + max(1, len(feed["spectra"]))
    surplus = room - sum(alloc.values())
    if surplus > 0 and alloc["waterfall"]:
        take = min(12, water_cap - alloc["waterfall"], surplus)
        alloc["waterfall"] += max(0, take)
        surplus -= max(0, take)
    if surplus > 0 and alloc["pipeline"]:
        full = _pipeline_full_height(view, width)
        take = min(max(0, full - alloc["pipeline"]), surplus)
        alloc["pipeline"] += take
        surplus -= take
    if surplus > 0 and alloc["feed"]:
        take = min(9 - alloc["feed"], surplus)
        alloc["feed"] += take
        surplus -= take
    if surplus > 0 and alloc["mind"]:
        take = min(8 - alloc["mind"], surplus)
        alloc["mind"] += take
        surplus -= take
    if surplus > 0 and alloc["waterfall"]:
        take = min(water_cap - alloc["waterfall"], surplus)
        alloc["waterfall"] += max(0, take)
        surplus -= max(0, take)
    return alloc


def build_frame(view, feed, width, height, keepawake=None):
    """One Console frame: exactly height rows, no row wider than
    width, nothing scrolled. keepawake, when given, is the
    keep-awake hold's plain-words state for the header."""
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        rows = _compact_rows(view, width, height) if view else \
            [[("MULTIDEX - THE CONSOLE", "head")],
             [("The watch state is not readable yet.", "normal")]]
        return _finish(rows, width, height)
    if view is None:
        rows = [[("MULTIDEX - THE CONSOLE", "head")],
                [("The watch state is not readable yet.", "normal")],
                [("The officer publishes state at muster; the "
                  "console fills when it lands.", "dim")]]
        return _finish(rows, width, height, footer=True)
    feed = feed or {"spectra": [], "work": [], "thought": []}
    alloc = _allot(view, feed, width, height)
    rows = _header_rows(view, width, keepawake)
    builders = (
        ("waterfall", lambda h: _waterfall_rows(view, feed, width, h)),
        ("pipeline", lambda h: _pipeline_rows(view, feed, width, h)),
        ("sky", lambda h: _sky_rows(feed, width, h)),
        ("pool", lambda h: _pool_rows(view, width, h)),
        ("feed", lambda h: _feed_rows(view, width, h)),
        ("mind", lambda h: _mind_rows(feed, width, h)),
    )
    for name, build in builders:
        h = alloc[name]
        if h <= 0:
            continue
        panel = build(h)
        rows.extend(panel[:h])
    return _finish(rows, width, height, footer=True)


def _finish(rows, width, height, footer=False):
    """Exactly height rows, none wider than width. With footer,
    the FOOTER line is anchored as the last row and any slack
    falls as blank rows above it."""
    rows = [_fit(row, width) for row in rows]
    if footer and height >= 1:
        rows = rows[:height - 1]
        while len(rows) < height - 1:
            rows.append([])
        rows.append(_fit([(FOOTER, "dim")], width))
        return rows
    rows = rows[:height]
    while len(rows) < height:
        rows.append([])
    return rows
