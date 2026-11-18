"""Reception containment: one allocation system for receptions.

Everything a watch receives costs two things - tuner seconds and
disk bytes - and before 0.4.1 those costs were bounded piecemeal:
a whole-watch disk ceiling in the profile, per-desk snippet count
quotas, oldest-first shedding under the ceiling, and the bill's
planned dwell times. This module pieces them into one coherent
allocation system, computed before the watch stands:

- At bill build, every planned reception (bill item) carries a
  computed allocation: its tuner seconds (the item's duration)
  and its worst-case bytes. For a capture the worst case is the
  capture's own arithmetic - sample rate x bytes per sample x
  duration. For a satellite pass it is computed from the actual
  predicted pass window, APT and LRPT separately (below).
- A ReceptionBudget is computed at muster from the profile's
  disk ceiling and the bill: per-desk and per-class byte
  allocations, the satellite reserve (the Class A pass
  allocations together), and a free margin held back from the
  ceiling.
- If the bill's worst case cannot fit the ceiling minus the
  margin, the budget degrades coherently at muster, never
  mid-watch: planned allowances are reduced to a floor in a
  stated priority order - Class C first, then Class B, then
  Class A non-satellite - and Class A satellite reservations
  hold longest. Every reduction is recorded as a plain-word
  statement in the budget and filed at muster.
- During the watch the officer accounts consumption live
  against these allocations (held snippet bytes per item, desk
  and class). A reception that reaches its allocation stops
  cleanly at the boundary: the LRPT IQ capture truncates at its
  byte cap inside the tuner, and a snippet that would take its
  reception past the allocation is refused with the refusal
  filed, the way ceiling refusals already were.
- Shedding still follows the Index's oldest-first rule (with
  satellite evidence shed last); the officer's shed events name
  the desk and bill item - the allocation - each shed came from.

The arithmetic this module stands on:

- NOAA APT passes are stored as audio at 11025 Hz, 16-bit mono:
  11025 x 2 = 22050 bytes per second of pass window, plus a
  stated allowance for the decoded PNG.
- METEOR LRPT passes are recorded as raw IQ at
  LRPT_IQ_RATE samples per second in the RTL-SDR's native
  unsigned 8-bit I/Q (2 bytes per sample): LRPT_IQ_RATE x 2
  bytes per second of pass window, plus a stated allowance for
  the decoded PNG. LRPT is a 72 ksym/s QPSK downlink in about
  140 kHz; the rate is sized to the signal with room for the
  decoder's resampler, and the reservation is rate x 2 x the
  predicted window in seconds.
- Desks that file audio snippets are charged at their capture
  rate for the whole dwell - the honest worst case, a dwell
  that never stops talking. Desks that file only event records
  are charged a flat per-item event allowance.
"""

# ------------------------------------------------------------- rates

APT_STORED_RATE = 11025          # Hz, 16-bit mono WAV as stored
APT_BYTES_PER_S = APT_STORED_RATE * 2
APT_PNG_ALLOWANCE = 4_000_000    # bytes, decoded APT image

LRPT_IQ_RATE = 1024000           # samples per second, cu8 IQ
LRPT_BYTES_PER_SAMPLE = 2        # unsigned 8-bit I + 8-bit Q
LRPT_BYTES_PER_S = LRPT_IQ_RATE * LRPT_BYTES_PER_SAMPLE
LRPT_PNG_ALLOWANCE = 8_000_000   # bytes, decoded LRPT image

# Worst-case snippet bytes per second of dwell, per desk: the
# capture rate of the audio path the desk files snippets from.
AUDIO_BYTES_PER_S = {
    "airband": 48000 * 2,
    "milair": 48000 * 2,
    "audiocensus": 48000 * 2,
    "wxradio": 22050 * 2,
    "ais": 48000 * 2,
    "iss": 22050 * 2,
}

# Desks that file event records only: a flat per-item allowance
# for the records themselves.
EVENT_ALLOWANCE = 262144

# Degradation: allowances fall to this floor per item, in class
# priority order, before any satellite reservation is touched.
FLOOR_BYTES = 1_000_000

# The free margin held back from the ceiling at muster.
MARGIN_FRACTION = 0.05

# Bill id inside a snippet filename: filenames are built as
# <watch-id>-<bill-id>-<desk name>, and the bill id is the one
# "bill-NNN" token in the name.
import re as _re

_BILL_ID_RE = _re.compile(r"(bill-\d+)")


def downlink_kind(sat_name):
    """'lrpt', 'apt', or 'other' for a satellite's downlink."""
    sat = (sat_name or "").upper()
    if "METEOR" in sat:
        return "lrpt"
    if "NOAA" in sat:
        return "apt"
    return "other"


def pass_window_s(item):
    """The predicted pass window in seconds for a pass item: the
    AOS-to-LOS span from the orbit prediction, not the bill
    item's padded duration."""
    p = item.get("pass") or {}
    aos, los = p.get("aos"), p.get("los")
    if aos is not None and los is not None and los > aos:
        return float(los - aos)
    return float(item.get("dur") or 0)


def item_allocation(item):
    """One bill item's allocation: tuner seconds and worst-case
    bytes, by the arithmetic in this module's docstring."""
    dur = float(item.get("dur") or 0)
    desks = item.get("desks") or []
    if item.get("kind") == "pass" and "satellite" in desks:
        window = pass_window_s(item)
        kind = downlink_kind((item.get("pass") or {}).get("sat"))
        if kind == "lrpt":
            nbytes = int(window * LRPT_BYTES_PER_S) \
                + LRPT_PNG_ALLOWANCE
        elif kind == "apt":
            nbytes = int(window * APT_BYTES_PER_S) \
                + APT_PNG_ALLOWANCE
        else:
            nbytes = int(window * APT_BYTES_PER_S) \
                + APT_PNG_ALLOWANCE
        return {"tuner_s": dur, "bytes": nbytes}
    nbytes = 0
    for desk in desks:
        rate = AUDIO_BYTES_PER_S.get(desk)
        if rate is not None:
            nbytes += int(rate * dur)
        else:
            nbytes += EVENT_ALLOWANCE
    # The audio census rides every captured window whether or
    # not the bill names it (the officer adds it at execution),
    # so every window item carries its worst case too.
    if item.get("kind") in ("dwell", "sweep") \
            and "audiocensus" not in desks:
        nbytes += int(AUDIO_BYTES_PER_S["audiocensus"] * dur)
    return {"tuner_s": dur, "bytes": nbytes}


def annotate_bill(bill):
    """Attach each item's computed allocation, at bill build."""
    for item in bill:
        item["allocation"] = item_allocation(item)
    return bill


def bill_id_of_snippet_path(path):
    """The bill id a snippet filename carries, or None."""
    if not path:
        return None
    m = _BILL_ID_RE.search(path.replace("\\", "/").rsplit("/", 1)[-1])
    return m.group(1) if m else None


class ReceptionBudget:
    """The muster-computed allocation system for one watch.

    allocations: {bill_id: {"tuner_s": s, "bytes": b}} after any
    degradation. by_class / by_desk aggregate the same numbers.
    degraded holds the plain-word statements of every reduction
    muster made; it is empty when the bill fit as planned.
    """

    def __init__(self, ceiling_bytes, bill, allocations, degraded):
        self.ceiling_bytes = int(ceiling_bytes)
        self.allocations = allocations
        self.degraded = list(degraded)
        self.by_class = {}
        self.by_desk = {}
        self.satellite_reserve_bytes = 0
        for item in bill:
            alloc = allocations[item["id"]]
            cls = self.by_class.setdefault(
                item["cls"], {"items": 0, "tuner_s": 0.0, "bytes": 0})
            cls["items"] += 1
            cls["tuner_s"] += alloc["tuner_s"]
            cls["bytes"] += alloc["bytes"]
            for desk in item.get("desks") or []:
                share = alloc["bytes"] // max(
                    1, len(item.get("desks") or []))
                self.by_desk[desk] = self.by_desk.get(desk, 0) + share
            if item.get("kind") == "pass" \
                    and "satellite" in (item.get("desks") or []):
                self.satellite_reserve_bytes += alloc["bytes"]
        self.total_bytes = sum(a["bytes"] for a in allocations.values())
        self.free_margin_bytes = self.ceiling_bytes - self.total_bytes

    def allocation_for(self, bill_id):
        alloc = self.allocations.get(bill_id)
        return alloc["bytes"] if alloc else None

    def summary(self):
        """The budget as data: meta, state, products and the
        doctor all read this one shape."""
        return {
            "ceiling_bytes": self.ceiling_bytes,
            "total_bytes": self.total_bytes,
            "free_margin_bytes": self.free_margin_bytes,
            "satellite_reserve_bytes": self.satellite_reserve_bytes,
            "by_class": {cls: dict(v) for cls, v in
                         sorted(self.by_class.items())},
            "by_desk": dict(sorted(self.by_desk.items())),
            "degraded": list(self.degraded),
        }


# Class order for degradation: C sheds its allowance first, then
# B, then A non-satellite. Satellite pass items are degraded
# last of all, and only if the floored bill still cannot fit.
_DEGRADE_ORDER = ("C", "B", "A")


def build_reception_budget(profile, bill):
    """Compute the budget for a bill under the profile's ceiling.

    Every item keeps its computed allocation when the bill fits.
    When it does not, allowances are reduced to FLOOR_BYTES per
    item in _DEGRADE_ORDER (satellite passes last), each round
    recorded as a statement, until the bill fits the ceiling
    minus the free margin - or everything stands at the floor
    and the budget states the remaining overage honestly.
    """
    ceiling = int(profile["watch"]["disk_ceiling_gb"] * 1e9)
    usable = ceiling - int(ceiling * MARGIN_FRACTION)
    allocations = {}
    for item in bill:
        alloc = item.get("allocation") or item_allocation(item)
        allocations[item["id"]] = {"tuner_s": float(alloc["tuner_s"]),
                                   "bytes": int(alloc["bytes"])}
    degraded = []

    def total():
        return sum(a["bytes"] for a in allocations.values())

    if total() > usable:
        by_id = {item["id"]: item for item in bill}
        for cls in _DEGRADE_ORDER:
            if total() <= usable:
                break
            touched = 0
            for item in bill:
                if item["cls"] != cls:
                    continue
                if item.get("kind") == "pass" \
                        and "satellite" in (item.get("desks") or []):
                    continue  # satellite reservations hold longest
                alloc = allocations[item["id"]]
                if alloc["bytes"] > FLOOR_BYTES:
                    alloc["bytes"] = FLOOR_BYTES
                    touched += 1
            if touched:
                degraded.append(
                    "class %s reception allowances reduced to the "
                    "%d-byte floor (%d items) so the bill's worst "
                    "case fits the disk ceiling"
                    % (cls, FLOOR_BYTES, touched))
        if total() > usable:
            touched = 0
            for item in bill:
                if item.get("kind") == "pass" \
                        and "satellite" in (item.get("desks") or []):
                    alloc = allocations[item["id"]]
                    if alloc["bytes"] > FLOOR_BYTES:
                        alloc["bytes"] = FLOOR_BYTES
                        touched += 1
            if touched:
                degraded.append(
                    "satellite pass reservations reduced to the "
                    "%d-byte floor (%d passes): the bill's worst "
                    "case left no other room" % (FLOOR_BYTES, touched))
        if total() > usable:
            degraded.append(
                "the floored bill's worst case still exceeds the "
                "usable ceiling by %d bytes; the watch stands on "
                "the ceiling's own shedding rule and the budget "
                "says so" % (total() - usable))
    return ReceptionBudget(ceiling, bill, allocations, degraded)
