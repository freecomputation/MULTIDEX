"""The display feed (0.8.0): what the officer publishes for the
Console, and what the Console reads.

The Console is a viewer, and a viewer needs something published to
view. The officer therefore keeps three small ring files inside
the watch folder, beside the state file it already publishes:

- display-work.jsonl: each desk's real output as it happens, a
  few lines per desk per dwell, drawn from the events the desk
  itself produced.
- display-thought.jsonl: the officer's own filed record - muster
  verdicts, grants and refusals, benchings, assessment changes,
  FLASH raisings, update handovers - one line per event the
  officer files through its own filing path, at filing time.
- display-sky.jsonl (0.15.0): the sky account's live records -
  one per dwell, composed by the officer from the ephemeris
  and the astronomy desks' own filed tallies (local sidereal
  time, Jupiter's altitude and next predicted window, the
  solar state, the meteor rate, the sonde state). The Console
  renders these records and computes nothing of its own.

Until 0.16.1 the feed also carried spectrum frames for a
Console waterfall panel, streamed while captures ran. The
panel and its spectrum ring are removed in 0.16.1. The
spectrum itself was never the panel's to keep: each dwell's
spectrum product is filed by the officer in the snippet store
as tier 1 evidence (0.10.0), where it remains, unchanged.

The feed is not part of the watch's record. The Index is the
record; the feed is a projection of it for the glass, and it is
bounded so it can never grow into a storage concern: each ring
keeps a capped tail (work MAX_WORK lines, thought MAX_THOUGHT
lines, sky MAX_SKY lines), rewritten when it passes one and a
half times its cap, so the whole feed stays well under a
megabyte per watch. Publishing is best-effort by law: every
failure is swallowed here, because a display must never be able
to reach back into the watch it displays. A watch with the feed
disabled (profile advanced key display_feed) files an identical
event record; only these files are absent.
"""

import json
import os

WORK_NAME = "display-work.jsonl"
THOUGHT_NAME = "display-thought.jsonl"
SKY_NAME = "display-sky.jsonl"

MAX_WORK = 240
MAX_THOUGHT = 240
MAX_SKY = 240
# The bin bound of a capture's spectrum product: the max-pool
# reduction below caps a spectrum at this many bins, the bound
# the tier 1 evidence projection has carried since 0.10.0.
MAX_BINS = 240
WORK_LINE_CHARS = 200
THOUGHT_LINE_CHARS = 300


class _Ring:
    """One bounded JSON-lines ring file. Appends are single
    lines; when the file passes one and a half times its cap it
    is rewritten to its capped tail, so its size stays bounded
    without a rewrite on every append."""

    def __init__(self, path, cap):
        self.path = path
        self.cap = cap
        self.count = 0
        try:
            with open(path) as fh:
                self.count = sum(1 for _ in fh)
        except OSError:
            self.count = 0

    def append(self, record):
        try:
            line = json.dumps(record)
        except (TypeError, ValueError):
            return
        try:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            return
        self.count += 1
        if self.count > self.cap + self.cap // 2:
            self._trim()

    def _trim(self):
        try:
            with open(self.path) as fh:
                lines = fh.readlines()
        except OSError:
            return
        keep = lines[-self.cap:]
        try:
            with open(self.path, "w") as fh:
                fh.writelines(keep)
            self.count = len(keep)
        except OSError:
            pass


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DisplayFeed:
    """The officer's publisher. Every method is best-effort and
    never raises: the feed is a courtesy to a viewer that may not
    exist, and the watch must not feel it."""

    def __init__(self, watch_dir, enabled=True):
        self.enabled = bool(enabled)
        self.watch_dir = watch_dir
        self._work = _Ring(os.path.join(watch_dir, WORK_NAME),
                           MAX_WORK)
        self._thought = _Ring(os.path.join(watch_dir, THOUGHT_NAME),
                              MAX_THOUGHT)
        self._sky = _Ring(os.path.join(watch_dir, SKY_NAME),
                          MAX_SKY)

    def work(self, t, desk, line):
        if not self.enabled:
            return
        line = str(line or "").strip()
        if not line:
            return
        self._work.append({"t": _num(t) or 0.0, "desk": str(desk),
                           "line": line[:WORK_LINE_CHARS]})

    def thought(self, t, desk, kind, content, flash=False):
        if not self.enabled:
            return
        content = str(content or "").strip()
        if not content:
            return
        self._thought.append({
            "t": _num(t) or 0.0, "desk": str(desk or ""),
            "kind": str(kind or ""), "flash": bool(flash),
            "content": content[:THOUGHT_LINE_CHARS]})

    def sky(self, record):
        """Publish one sky record (0.15.0), sanitised to plain
        JSON values: the panel renders records, so a record
        that cannot be serialised is dropped, never repaired
        into a plausible one."""
        if not self.enabled or not isinstance(record, dict):
            return
        try:
            clean = json.loads(json.dumps(record))
        except (TypeError, ValueError):
            return
        self._sky.append(clean)


# ------------------------------------------------------------------ read

def _read_ring(path, cap):
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-cap * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a line the officer was mid-append on
        if isinstance(record, dict):
            out.append(record)
    return out[-cap:]


def load_feed(watch_dir):
    """The Console's read of one watch's feed: the work lines
    oldest first, the thought lines oldest first, the sky
    records oldest first. Missing files read as empty; the
    feed's absence is a state the Console renders, never an
    error."""
    return {
        "work": _read_ring(os.path.join(watch_dir, WORK_NAME),
                           MAX_WORK),
        "thought": _read_ring(os.path.join(watch_dir, THOUGHT_NAME),
                              MAX_THOUGHT),
        "sky": _read_ring(os.path.join(watch_dir, SKY_NAME),
                          MAX_SKY),
    }


def pool_bins(values, cap=MAX_BINS):
    """Max-pool a magnitude series down to at most cap bins.

    Max-pooling is the spectrum product's honest reduction: a
    narrow carrier survives it, where an average would dissolve
    the carrier into the floor and record a quieter band than
    the receiver heard."""
    values = list(values)
    if len(values) <= cap:
        return values
    out = []
    step = len(values) / float(cap)
    for i in range(cap):
        lo = int(i * step)
        hi = max(lo + 1, int((i + 1) * step))
        out.append(max(values[lo:hi]))
    return out
