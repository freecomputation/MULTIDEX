"""The normalized event: the one thing every desk emits and the Index keeps.

Confidence grades are enforced here, at the point of writing, per the
design's second law. An event with an unknown grade cannot be created,
so it can never reach the Index.
"""

import time

from . import util

GRADES = ("CONFIRMED", "PROBABLE", "UNRESOLVED")

# Event kinds used across the system.
KINDS = (
    "decode",       # a decoder produced content or a structured record
    "observation",  # an emission seen, not decoded (sweep)
    "voice",        # a voice transmission heard on a voice desk
    "audio",        # an audio emission found by the census, not decoded
    "alert",        # a broadcast alert (SAME header, guard activity)
    "pass",         # a satellite pass or sonde flight milestone
    "telemetry",    # a tracked object's telemetry (sonde, vessel, aircraft)
    "census",       # a population / presence record (Tier 3, broadcast)
    "dwell",        # a dwell record: the coverage account's raw material
    "gap",          # a coverage gap: tuner loss, contention, early stop
    "desk_health",  # a desk crash, benching, or recovery
    "assessment",   # an exploitation assessment with evidence links
    "system",       # watch lifecycle: muster, stand-down, resume
)


def make_event(watch_id, desk, kind, t_start, t_end=None, freq_hz=None,
               channel=None, emitter=None, grade="UNRESOLVED", content=None,
               data=None, snippet=None, links=None):
    if grade not in GRADES:
        raise ValueError("unknown confidence grade: %r" % (grade,))
    if kind not in KINDS:
        raise ValueError("unknown event kind: %r" % (kind,))
    if t_end is None:
        t_end = t_start
    return {
        "id": util.new_id("ev"),
        "watch_id": watch_id,
        "desk": desk,
        "kind": kind,
        "t_start": float(t_start),
        "t_end": float(t_end),
        "freq_hz": None if freq_hz is None else float(freq_hz),
        "channel": channel,
        "emitter": emitter,
        "grade": grade,
        "content": content,
        "data": data or {},
        "snippet": snippet,
        "links": list(links or []),
    }


def now_epoch():
    return time.time()
