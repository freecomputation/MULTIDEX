"""The live dashboard's view model (0.4.0).

Two halves, kept apart on purpose. collect() reads one snapshot of a
watch from the disk and the Index: the officer's state file, the
watch metadata, the bill, recent events, dwell records, snippet
accounting, bench reasons, and the FLASH files. build_view() is a
pure function that turns that snapshot into the exact structure the
terminal draws. The curses layer in liveview.py only draws; every
number on the screen is derived here, from the same records the
products read.

What the record does not carry, the dashboard does not show. The
officer's state and the dwell records carry no per-dwell tuner gain
or demodulation mode - gain lives inside the tuner's feed paths and
is never written down - so the tuner panel shows what is recorded:
the dwell's desks, its centre frequency and bandwidth, and its kind.
"""

import glob
import os
import time

from . import util
from .bands import BANDS, band_name
from .desks import SPECS
from .index import Index

KIND_LABELS = {
    "dwell": "rotation dwell",
    "sweep": "census sweep",
    "pass": "satellite pass",
    "sonde": "radiosonde window",
    "interrupt": "interrupt",
}

# Kinds that mean something was detected in a band, for the activity
# strip: sweep observations, census records, and audio census finds.
BAND_KINDS = ("observation", "census", "audio")

BAR_WIDTH = 24


# ---------------------------------------------------------------- collect

def _flash_ids(watch_dir):
    """Event ids named by the watch's FLASH files, and their count.

    A FLASH file's Events line names its triggering event first; an
    event is a FLASH event when any FLASH file names it.
    """
    ids = set()
    files = sorted(glob.glob(os.path.join(watch_dir, "FLASH-*.md")))
    for path in files:
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith("Events: "):
                        for token in line[len("Events: "):].split(","):
                            token = token.strip()
                            if token:
                                ids.add(token)
        except OSError:
            continue
    return ids, len(files)


def collect(home, watch_id, feed_limit=60, band_limit=1000):
    """One raw snapshot of a watch, or None when the watch has no
    state file (no officer has ever reported for it)."""
    watch_dir = os.path.join(home, "watches", watch_id)
    state_path = os.path.join(watch_dir, "state.json")
    state = util.read_json(state_path, default=None)
    if state is None:
        return None
    try:
        state_mtime = os.path.getmtime(state_path)
    except OSError:
        state_mtime = time.time()
    meta = util.read_json(os.path.join(watch_dir, "watch.json"),
                          default=None)
    bill = util.read_json(os.path.join(watch_dir, "bill.json"),
                          default=[]) or []
    flash_ids, flash_count = _flash_ids(watch_dir)
    index = Index(home)
    try:
        if meta is None:
            meta = index.get_watch_meta(watch_id) or {}
        feed = index.events(watch_id=watch_id, limit=feed_limit,
                            newest=True)
        recent = index.events(watch_id=watch_id, limit=band_limit,
                              newest=True)
        band_counts = {}
        for ev in recent:
            if ev["freq_hz"] and ev["kind"] in BAND_KINDS:
                name = band_name(ev["freq_hz"])
                band_counts[name] = band_counts.get(name, 0) + 1
        dwells = index.dwell_records(watch_id)
        executed_s = sum(d["t_end"] - d["t_start"] for d in dwells)
        bench_reasons = {}
        health = index.events(watch_id=watch_id,
                              kinds=["desk_health"], limit=10000)
        for ev in health:
            data = ev.get("data") or {}
            if data.get("benched") and data.get("desk"):
                bench_reasons[data["desk"]] = data.get("reason")
        raw = {
            "watch_id": watch_id,
            "state": state,
            "state_mtime": state_mtime,
            "meta": meta or {},
            "bill": bill,
            "feed_events": feed,
            "band_counts": band_counts,
            "executed_s": executed_s,
            "gaps": index.count_events(watch_id=watch_id, kind="gap"),
            "events_total": index.count_events(watch_id=watch_id),
            "dossiers_total": len(index.dossiers_in_watch(watch_id)),
            "snippets": index.snippet_accounting(watch_id),
            "bench_reasons": bench_reasons,
            "flash_ids": flash_ids,
            "flash_count": flash_count,
        }
    finally:
        index.close()
    return raw


# ------------------------------------------------------------------- view

def _kind_label(kind):
    return KIND_LABELS.get(kind, kind or "dwell")


def _item_view(item, watch_now):
    return {
        "id": item.get("id"),
        "label": item.get("label", ""),
        "kind": item.get("kind", ""),
        "kind_label": _kind_label(item.get("kind", "")),
        "cls": item.get("cls", ""),
        "desks": list(item.get("desks") or []),
        "center_hz": item.get("center_hz"),
        "bw_hz": item.get("bw_hz"),
        "start": item.get("start", 0),
        "dur": item.get("dur", 0),
        "in_s": item.get("start", 0) - watch_now,
        "left_s": item.get("start", 0) + item.get("dur", 0) - watch_now,
        "pass_sat": (item.get("pass") or {}).get("sat"),
    }


def build_view(raw, now=None):
    """The dashboard's data structure, from a collect() snapshot.

    Pure: no disk, no clock of its own - `now` is an argument, and
    the elapsed estimate is derived from the state file's saved
    elapsed, its mtime, and the watch's recorded time scale.
    """
    if now is None:
        now = time.time()
    state = raw["state"]
    meta = raw["meta"]
    bill = raw["bill"]
    start = meta.get("start")
    if start is None and bill:
        start = bill[0].get("start", now)
    if start is None:
        start = now
    planned = 0.0
    if meta.get("planned_end"):
        planned = meta["planned_end"] - start
    if planned <= 0:
        planned = sum(item.get("dur", 0) for item in bill)
    complete = bool(state.get("complete"))
    running = (not complete) and util.pid_alive(state.get("pid"))
    scale = meta.get("time_scale") or 1.0
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    saved_elapsed = float(state.get("elapsed_s") or 0)
    if complete:
        elapsed = saved_elapsed
        status = "complete"
    elif running:
        # The officer saves state at dwell boundaries; between saves
        # the watch clock advances at its recorded time scale from
        # the save's wall moment, which the file's mtime records.
        elapsed = saved_elapsed + max(
            0.0, now - raw["state_mtime"]) * scale
        if planned > 0:
            elapsed = min(elapsed, planned)
        status = "running"
    else:
        elapsed = saved_elapsed
        status = "interrupted"
    watch_now = start + elapsed

    by_id = {}
    for idx, item in enumerate(bill):
        by_id[item.get("id")] = (idx, item)
    current = None
    next_item = None
    executing = state.get("executing")
    next_index = state.get("next_index") or 0
    if complete:
        next_index = len(bill)
    if executing and executing in by_id:
        idx, item = by_id[executing]
        current = _item_view(item, watch_now)
        if idx + 1 < len(bill):
            next_item = _item_view(bill[idx + 1], watch_now)
    elif 0 < next_index <= len(bill):
        last = bill[next_index - 1]
        if not complete and watch_now < last.get("start", 0) \
                + last.get("dur", 0):
            current = _item_view(last, watch_now)
        if next_index < len(bill):
            next_item = _item_view(bill[next_index], watch_now)
    elif next_index == 0 and bill:
        next_item = _item_view(bill[0], watch_now)

    next_a = None
    for item in bill:
        if item.get("cls") == "A" and item.get("start", 0) > watch_now:
            next_a = _item_view(item, watch_now)
            break

    benched = set(state.get("benched") or [])
    active = set()
    if current:
        active = {d for d in current["desks"] if d not in benched}
    desks = []
    for name in sorted(SPECS):
        tier, service = SPECS[name][0], SPECS[name][1]
        if name in benched:
            dstate = "benched"
            reason = raw["bench_reasons"].get(
                name, "reason not recorded in the watch record")
        elif name in active:
            dstate, reason = "active", None
        else:
            dstate, reason = "standing", None
        desks.append({"name": name, "tier": tier, "service": service,
                      "state": dstate, "reason": reason})

    feed = []
    for ev in raw["feed_events"]:
        content = ev.get("content") or (ev.get("data") or {}).get(
            "rule") or ev.get("kind", "")
        feed.append({
            "id": ev.get("id"),
            "stamp": util.fmt_time(ev.get("t_start", start)),
            "desk": ev.get("desk", ""),
            "kind": ev.get("kind", ""),
            "grade": ev.get("grade", ""),
            "content": content,
            "freq_hz": ev.get("freq_hz"),
            "flash": ev.get("id") in raw["flash_ids"],
        })

    counts = raw["band_counts"]
    bands = []
    if counts:
        peak = max(counts.values())
        for _lo, _hi, name in BANDS:
            n = counts.get(name, 0)
            if n:
                bands.append({
                    "name": name, "count": n,
                    "bar": max(1, int(round(BAR_WIDTH * n / peak))),
                })
    planned_dwell = sum(item.get("dur", 0) for item in bill)
    snaps = raw["snippets"]
    return {
        "watch_id": raw["watch_id"],
        "status": status,
        "complete": complete,
        "source": meta.get("source", "unknown"),
        "source_note": meta.get("source_note"),
        "region": meta.get("region", ""),
        "profile_name": meta.get("profile_name", ""),
        "start": start,
        "planned_end": start + planned if planned else None,
        "elapsed_s": elapsed,
        "remaining_s": max(0.0, planned - elapsed) if planned else 0.0,
        "progress": (elapsed / planned) if planned > 0 else 0.0,
        "current": current,
        "next": next_item,
        "next_a": next_a,
        "state_current": state.get("current", ""),
        "feed": feed,
        "totals": {
            "events": raw["events_total"],
            "dossiers": raw["dossiers_total"],
            "flash": raw["flash_count"],
            "snippets": snaps["kept_count"],
            "snippets_bytes": snaps["kept_bytes"],
            "executed_s": raw["executed_s"],
            "planned_s": planned_dwell,
            "gaps": raw["gaps"],
            # The dashboard's budget row reads the standing
            # pool: consumed against the pool's cap (state files
            # from before the standing ledger carry only the
            # per-watch allocated figure, which stands in).
            "budget_allocated_bytes":
                (state.get("budget") or {}).get(
                    "cap_bytes",
                    (state.get("budget") or {}).get(
                        "allocated_bytes")),
            "budget_consumed_bytes":
                (state.get("budget") or {}).get("consumed_bytes"),
        },
        "desks": desks,
        "bands": bands,
        "bands_note": None if bands else
        "no sweep or census detections yet this watch",
    }


def load_view(home, watch_id, now=None, feed_limit=60):
    """collect() + build_view(): one dashboard frame's data, or None
    when the watch has no state file to view."""
    raw = collect(home, watch_id, feed_limit=feed_limit)
    if raw is None:
        return None
    return build_view(raw, now=now)
