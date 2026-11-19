"""The display feed (0.8.0): what the officer publishes for the
Console, and what the Console reads.

The Console is a viewer, and a viewer needs something published to
view. The officer therefore keeps three small ring files inside
the watch folder, beside the state file it already publishes:

- display-spectra.jsonl: magnitude spectrum frames computed
  from samples the watch already took for its own work (the
  audio census desk's Welch PSD over its window slice, or the
  sweep scan's own power array), downsampled to at most
  MAX_BINS bins, with the span each covers and the lock
  brackets the desks' events declared inside that span. Since
  0.16.0 the frames stream while a capture runs, not only when
  it completes: the simulated source publishes a frame every
  STREAM_CADENCE_S seconds of dwell time, and the real source
  publishes at the points its capture path samples the window -
  the scan's frame as the scan computes it, and one frame per
  STREAM_BLOCK_S of the census slice, from the same Welch
  segments the census itself averages. The officer still
  publishes one final frame per capture, carrying the lock
  brackets the desks filed. The ring stays MAX_SPECTRA frames
  deep, so at streaming cadence the waterfall's history is
  cadence-dependent by construction: inside one long dwell the
  ring holds only that dwell's most recent frames, and the
  waterfall shows what the ring holds, never more.
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

The feed is not part of the watch's record. The Index is the
record; the feed is a projection of it for the glass, and it is
bounded so it can never grow into a storage concern: each ring
keeps a capped tail (spectra MAX_SPECTRA frames, work MAX_WORK
lines, thought MAX_THOUGHT lines), rewritten when it passes one
and a half times its cap, so a spectra file holds at most 60
frames of at most 240 bins and the whole feed stays well under a
megabyte per watch. Publishing is best-effort by law: every
failure is swallowed here, because a display must never be able
to reach back into the watch it displays. A watch with the feed
disabled (profile advanced key display_feed) files an identical
event record; only these files are absent.
"""

import json
import os

SPECTRA_NAME = "display-spectra.jsonl"
WORK_NAME = "display-work.jsonl"
THOUGHT_NAME = "display-thought.jsonl"
SKY_NAME = "display-sky.jsonl"

MAX_SPECTRA = 40
MAX_WORK = 240
MAX_THOUGHT = 240
MAX_SKY = 240
MAX_BINS = 240
MAX_BRACKETS = 12

# The display stream's cadence (0.16.0), bounded in both of its
# forms. STREAM_CADENCE_S is the simulated source's spacing
# between frames across a dwell: five seconds of dwell time,
# fine enough that the Console's newest frame is seconds old
# while a capture runs, coarse enough that a day of dwells
# publishes thousands of small frames into a forty-frame ring,
# never a flood. STREAM_BLOCK_S is the real source's spacing
# inside a captured window slice: one frame per half-second of
# sampled time, so the census desk's five-second slice yields
# ten frames and a twenty-second probe stare forty - the ring's
# whole depth, which is the stated trade of a stare that streams.
STREAM_CADENCE_S = 5.0
STREAM_BLOCK_S = 0.5
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
        self._spectra = _Ring(os.path.join(watch_dir, SPECTRA_NAME),
                              MAX_SPECTRA)
        self._work = _Ring(os.path.join(watch_dir, WORK_NAME),
                           MAX_WORK)
        self._thought = _Ring(os.path.join(watch_dir, THOUGHT_NAME),
                              MAX_THOUGHT)
        self._sky = _Ring(os.path.join(watch_dir, SKY_NAME),
                          MAX_SKY)

    def spectrum(self, frame):
        """Publish one capture's spectrum frame, sanitised to the
        feed's bounds: at most MAX_BINS bins, at most
        MAX_BRACKETS lock brackets."""
        if not self.enabled or not isinstance(frame, dict):
            return
        bins = []
        for value in (frame.get("bins") or [])[:MAX_BINS]:
            value = _num(value)
            if value is not None:
                bins.append(round(value, 1))
        lo_hz, hi_hz = _num(frame.get("lo_hz")), _num(frame.get("hi_hz"))
        if not bins or lo_hz is None or hi_hz is None or hi_hz <= lo_hz:
            return
        brackets = []
        for br in (frame.get("brackets") or [])[:MAX_BRACKETS]:
            if not isinstance(br, dict):
                continue
            freq = _num(br.get("freq_hz"))
            if freq is None:
                continue
            brackets.append({
                "desk": str(br.get("desk") or ""),
                "name": str(br.get("name") or ""),
                "grade": str(br.get("grade") or ""),
                "freq_hz": freq,
                "lo_hz": _num(br.get("lo_hz")) or freq,
                "hi_hz": _num(br.get("hi_hz")) or freq,
            })
        self._spectra.append({
            "t": _num(frame.get("t")) or 0.0,
            "bill_id": str(frame.get("bill_id") or ""),
            "label": str(frame.get("label") or ""),
            "kind": str(frame.get("kind") or ""),
            "lo_hz": lo_hz, "hi_hz": hi_hz,
            "floor_db": _num(frame.get("floor_db")),
            "bins": bins, "brackets": brackets,
        })

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
    """The Console's read of one watch's feed: the spectra frames
    oldest first, the work lines oldest first, the thought lines
    oldest first. Missing files read as empty; the feed's absence
    is a state the Console renders, never an error."""
    return {
        "spectra": _read_ring(os.path.join(watch_dir, SPECTRA_NAME),
                              MAX_SPECTRA),
        "work": _read_ring(os.path.join(watch_dir, WORK_NAME),
                           MAX_WORK),
        "thought": _read_ring(os.path.join(watch_dir, THOUGHT_NAME),
                              MAX_THOUGHT),
        "sky": _read_ring(os.path.join(watch_dir, SKY_NAME),
                          MAX_SKY),
    }


def pool_bins(values, cap=MAX_BINS):
    """Max-pool a magnitude series down to at most cap bins.

    Max-pooling is the spectrum display's honest reduction: a
    narrow carrier survives it, where an average would dissolve
    the carrier into the floor and show a quieter band than the
    receiver heard."""
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
