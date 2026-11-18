"""Reception containment: a standing allocation ledger.

Everything a watch receives costs two things - tuner seconds and
disk bytes. Before 0.4.1 those costs were bounded piecemeal; 0.4.1
pieced them into one allocation system, but computed it as a
per-watch budget: a ReceptionBudget was summed at one watch's
muster against a per-watch ceiling and died with that watch.
Overhead is not a property of a run, so 0.4.2 restructures the
same arithmetic around a standing ledger:

- The ledger is a plain durable file in the home directory,
  beside the Index, owned by no watch: allocation-ledger.json.
  Any watch, doctor, status or the dashboard reads it. A
  software update never resets it: reads are schema-tolerant
  (unknown fields are ignored, a missing file is an empty
  ledger) and the ledger records its own format version.
- The pool it allocates from is a property of the installation:
  a standing cap (the profile's disk ceiling, read as a
  home-level cap) bounded by the home filesystem's actual free
  space, with a free margin that is never granted.
- Allocations are situational grants against that pool. A
  standing watch holds grants for its bill, sized from the
  bill's own arithmetic (below) and the pool's state at grant
  time. Satellite passes carry standing reservations computed
  from the ephemeris for the windows ahead, whether or not a
  watch is standing; a watch whose bill covers a reserved pass
  adopts the reservation as its grant for that item. A watch
  may request further grants mid-watch as unplanned receptions
  (interrupts) firm up. A grant the pool cannot carry is shrunk
  or refused with the reason stated, in the class-priority
  order the per-watch budget used: Class C yields first, then
  B, then A non-satellite, satellite reservations last.
- The lifecycle follows the cancel-and-update reality.
  Consumption posts against a grant as receptions file. The
  unspent remainder of a grant releases when its dwell or pass
  ends. Stopping or cancelling a watch releases all of its
  unspent grants immediately; its filed receptions stay
  accounted as consumed in the ledger's history. A reservation
  whose window passes unclaimed expires and releases. A watch
  that died holding grants leaves them open in the ledger; the
  next muster releases the orphans, and a resumed watch
  reconciles - it re-derives its grants from the ledger, its
  bill and the snippet store, and says what changed.
- Shedding under the ceiling reads the whole home: the oldest
  filed receptions across all watches are candidates, and
  every shed posts against the grant that reception was filed
  under, naming it.
- The per-watch views remain views: budget.json in a watch
  directory is a snapshot of the ledger at muster, and the
  Watch Report's reception account is that watch's slice of
  the standing ledger - granted, consumed, released, shed
  during its life. Neither is the system of record.

The arithmetic the grants stand on is unchanged from 0.4.1:

- At bill build, every planned reception (bill item) carries a
  computed allocation: its tuner seconds (the item's duration)
  and its worst-case bytes. For a capture the worst case is the
  capture's own arithmetic - sample rate x bytes per sample x
  duration. For a satellite pass it is computed from the actual
  predicted pass window.
- NOAA APT passes are stored as audio at 11025 Hz, 16-bit mono:
  11025 x 2 = 22050 bytes per second of pass window, plus a
  stated allowance for the decoded PNG. Standing satellite
  reservations cover APT passes only.
- METEOR passes are acknowledged only (0.6.0): the pass is
  predicted and billed, its pass event states the
  acknowledgement, and nothing is recorded. A METEOR pass
  carries a zero-byte allocation, takes no standing
  reservation and no grant, and a ledger that still holds
  METEOR reservations from an older build releases them on
  load, with the release stated on each grant it closes and
  in the loading surface's account.
- Desks that file audio snippets are charged at their capture
  rate for the whole dwell - the honest worst case, a dwell
  that never stops talking. Desks that file only event records
  are charged a flat per-item event allowance.
"""

import os
import shutil
import time

from . import util

# ------------------------------------------------------------- rates

APT_STORED_RATE = 11025          # Hz, 16-bit mono WAV as stored
APT_BYTES_PER_S = APT_STORED_RATE * 2
APT_PNG_ALLOWANCE = 4_000_000    # bytes, decoded APT image

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

# The free margin of the standing pool that is never granted.
MARGIN_FRACTION = 0.05

# The standing ledger: its file, its format version, and how far
# ahead satellite reservations are held for passes that will
# occur during any plausible watch.
LEDGER_FILENAME = "allocation-ledger.json"
LEDGER_FORMAT = 1
RESERVE_HORIZON_S = 48 * 3600

# Two predictions of the same pass (a reserve computed at doctor
# time, the same pass placed in a bill at muster) name the same
# window if their AOS values land within this tolerance.
_PASS_MATCH_S = 90

# Bill id inside a snippet filename: filenames are built as
# <watch-id>-<bill-id>-<desk name>, and the bill id is the one
# "bill-NNN" token in the name.
import re as _re

_BILL_ID_RE = _re.compile(r"(bill-\d+)")


def downlink_kind(sat_name):
    """The allocation class of a satellite's downlink: 'apt'
    for NOAA (recorded and reserved), 'acknowledged' for a
    METEOR pass (acknowledged only under the 0.6.0 policy; it
    allocates nothing), 'other' for anything else (charged at
    the APT rate, as before)."""
    sat = (sat_name or "").upper()
    if "METEOR" in sat:
        return "acknowledged"
    if "NOAA" in sat:
        return "apt"
    return "other"


def _acknowledged_pass(item):
    """True for a METEOR pass bill item: acknowledged only, so
    it takes no reservation and no grant - its computed
    allocation is zero bytes, stated as such."""
    if item.get("kind") != "pass" \
            or "satellite" not in (item.get("desks") or []):
        return False
    return downlink_kind((item.get("pass") or {}).get("sat")) \
        == "acknowledged"


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
        if kind == "acknowledged":
            # Acknowledged only: nothing is recorded, so the
            # allocation is zero bytes, stated as such wherever
            # the pass's allocation is reported.
            return {"tuner_s": dur, "bytes": 0}
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


def cap_bytes(profile):
    """The standing cap for a home, from the profile: the disk
    ceiling read as a property of the installation, not of a
    run."""
    return int(profile["watch"]["disk_ceiling_gb"] * 1e9)


def _is_satellite_grant(grant):
    return grant.get("sat") is not None


# ------------------------------------------------------------- ledger

_GRANT_DEFAULTS = {
    "kind": "watch",            # "watch" | "reserve"
    "owner": "standing",        # a watch id, or "standing"
    "bill_id": None,
    "cls": None,
    "desks": [],
    "label": "",
    "sat": None,
    "downlink": None,
    "bytes": 0,                 # granted size, after any shrink
    "computed_bytes": 0,        # the full computed allocation
    "tuner_s": 0.0,
    "consumed_bytes": 0,
    "shed_bytes": 0,
    "released_bytes": 0,
    "status": "open",           # open | closed | expired | cancelled
    "granted_at": None,
    "window_start": None,
    "window_end": None,
    "closed_at": None,
    "close_reason": None,
    "adopted_reserve": False,
}


class AllocationLedger:
    """The standing allocation ledger for one home.

    Grants are plain dicts in a plain JSON file, so the ledger
    can be read by anything that can read the home. Loading is
    schema-tolerant: a missing file is an empty ledger, unknown
    fields anywhere are ignored (and preserved), and a grant
    missing a field reads with the default above. Saving is the
    atomic write the rest of the system uses.
    """

    def __init__(self, home, data=None):
        self.home = home
        self.path = os.path.join(home, LEDGER_FILENAME)
        self.data = data if data is not None else {
            "format": LEDGER_FORMAT, "cap_bytes": None, "grants": []}
        # Statements a load itself produced (the acknowledgement
        # policy release below), for the loading surface to file
        # or print: a policy reconciliation is stated, never
        # silent.
        self.load_statements = []

    @classmethod
    def load(cls, home):
        raw = util.read_json(os.path.join(home, LEDGER_FILENAME),
                             default=None)
        if not isinstance(raw, dict):
            return cls(home)
        grants = raw.get("grants")
        if not isinstance(grants, list):
            grants = []
        data = dict(raw)
        data["grants"] = [cls._normalize(g) for g in grants
                          if isinstance(g, dict) and g.get("id")]
        data.setdefault("format", LEDGER_FORMAT)
        data.setdefault("cap_bytes", None)
        ledger = cls(home, data)
        ledger._release_meteor_grants(time.time())
        return ledger

    # The reason recorded on every grant the acknowledgement
    # policy closes.
    _METEOR_RELEASE_REASON = (
        "released under the METEOR acknowledgement policy: "
        "METEOR passes are acknowledged only and hold no "
        "reservation or grant")

    def _release_meteor_grants(self, now):
        """Reconcile the ledger to the acknowledgement policy:
        every open grant held for a METEOR pass - a standing
        reservation taken by an older build, or one a watch
        adopted from it - is released on load. The release is
        recorded on each grant (its unspent bytes return to the
        pool; bytes already consumed stay in the history) and
        stated in load_statements for the loading surface.
        Returns the released grants."""
        released = []
        for grant in self.grants:
            if grant.get("status") != "open" \
                    or "METEOR" not in (grant.get("sat")
                                        or "").upper():
                continue
            grant["released_bytes"] = max(
                0, grant["bytes"] - grant["consumed_bytes"])
            grant["status"] = "closed"
            grant["closed_at"] = now
            grant["close_reason"] = self._METEOR_RELEASE_REASON
            released.append(grant)
        if released:
            self.save()
            self.load_statements = [
                "released %d open METEOR reservation(s) (%d "
                "bytes) held from an earlier build: METEOR "
                "passes are acknowledged only and hold no "
                "reservation or grant under the current policy"
                % (len(released),
                   sum(g["released_bytes"] for g in released))]
        return released

    @staticmethod
    def _normalize(grant):
        out = dict(grant)
        for key, default in _GRANT_DEFAULTS.items():
            if key not in out or out[key] is None:
                out[key] = list(default) \
                    if isinstance(default, list) else default
        if not isinstance(out.get("desks"), list):
            out["desks"] = []
        return out

    def save(self):
        os.makedirs(self.home, exist_ok=True)
        self.data["format"] = LEDGER_FORMAT
        util.write_json(self.path, self.data)

    # ------------------------------------------------------ queries
    @property
    def grants(self):
        return self.data["grants"]

    def find(self, grant_id):
        for grant in self.grants:
            if grant["id"] == grant_id:
                return grant
        return None

    def select(self, owner=None, status=None, kind=None):
        out = []
        for grant in self.grants:
            if owner is not None and grant.get("owner") != owner:
                continue
            if status is not None and grant.get("status") != status:
                continue
            if kind is not None and grant.get("kind") != kind:
                continue
            out.append(grant)
        return out

    def grant_for(self, owner, bill_id, status=None):
        """The most recent grant an owner holds for a bill item."""
        found = None
        for grant in self.grants:
            if grant.get("owner") == owner \
                    and grant.get("bill_id") == bill_id \
                    and (status is None
                         or grant.get("status") == status):
                found = grant
        return found

    def pool(self, cap=None, disk_free=None):
        """The standing pool's figures, from the ledger alone.

        consumed is every byte the ledger accounts as filed
        evidence still held, across all grants ever made;
        reserved is the unspent room of the grants still open;
        free is what remains of the cap once the never-granted
        margin, the consumed bytes and the reserved room are
        held back. grantable is free bounded by the filesystem's
        own free space, when that is known: the pool never
        promises room the disk does not have.
        """
        if cap is None:
            cap = self.data.get("cap_bytes")
        consumed = sum(g["consumed_bytes"] for g in self.grants)
        shed = sum(g["shed_bytes"] for g in self.grants)
        open_grants = self.select(status="open")
        reserved = sum(g["bytes"] - g["consumed_bytes"]
                       for g in open_grants)
        sat_reserved = sum(
            g["bytes"] - g["consumed_bytes"] for g in open_grants
            if _is_satellite_grant(g))
        sat_consumed = sum(g["consumed_bytes"] for g in self.grants
                           if _is_satellite_grant(g))
        if disk_free is None:
            try:
                disk_free = shutil.disk_usage(self.home).free
            except OSError:
                disk_free = None
        figures = {
            "cap_bytes": cap,
            "margin_bytes": (int(cap * MARGIN_FRACTION)
                             if cap is not None else None),
            "consumed_bytes": consumed,
            "shed_bytes": shed,
            "granted_bytes": sum(g["bytes"] for g in open_grants),
            "reserved_bytes": reserved,
            "satellite_reserve_bytes": sat_reserved,
            "satellite_consumed_bytes": sat_consumed,
            "open_grants": len(open_grants),
            "disk_free_bytes": disk_free,
            "free_bytes": None,
            "grantable_bytes": None,
        }
        if cap is not None:
            free = cap - figures["margin_bytes"] - consumed - reserved
            figures["free_bytes"] = free
            grantable = free
            if disk_free is not None:
                grantable = min(grantable, disk_free)
            figures["grantable_bytes"] = grantable
        return figures

    # ---------------------------------------------------- mutations
    def set_cap(self, cap):
        """Record the standing cap this home's profile states.
        The cap lives in the profile; the ledger keeps the last
        value it was told so read-only surfaces (status) can
        state the pool without loading one."""
        if cap is not None and self.data.get("cap_bytes") != cap:
            self.data["cap_bytes"] = int(cap)
            self.save()

    def add_grant(self, now, **fields):
        grant = dict(_GRANT_DEFAULTS)
        grant.update(fields)
        grant["desks"] = list(grant.get("desks") or [])
        grant["id"] = fields.get("id") or util.new_id("grant")
        grant["granted_at"] = now
        self.grants.append(grant)
        self.save()
        return grant

    def consume(self, grant_id, nbytes):
        grant = self.find(grant_id)
        if grant is None or nbytes <= 0:
            return
        grant["consumed_bytes"] += int(nbytes)
        self.save()

    def post_shed(self, owner, bill_id, nbytes):
        """Post a shed against the grant the reception was filed
        under: its bytes leave the held evidence, so the grant's
        consumed falls and its shed rises by the same amount.
        Returns the grant posted against, or None when no grant
        names that reception (evidence filed before the ledger,
        or under a watch that never held one)."""
        grant = self.grant_for(owner, bill_id, status="open") \
            or self.grant_for(owner, bill_id)
        if grant is None or nbytes <= 0:
            return None
        grant["consumed_bytes"] = max(
            0, grant["consumed_bytes"] - int(nbytes))
        grant["shed_bytes"] += int(nbytes)
        self.save()
        return grant

    def close(self, grant_id, now, reason, status="closed"):
        grant = self.find(grant_id)
        if grant is None or grant.get("status") != "open":
            return None
        grant["released_bytes"] = max(
            0, grant["bytes"] - grant["consumed_bytes"])
        grant["status"] = status
        grant["closed_at"] = now
        grant["close_reason"] = reason
        self.save()
        return grant

    def _revert_reserve(self, grant):
        """An adopted satellite reservation a watch never drew
        on returns to standing ownership: the pass is still
        ahead, and its reservation is a property of the
        installation, not of the watch that ended."""
        grant["owner"] = "standing"
        grant["kind"] = "reserve"
        grant["bill_id"] = None
        grant["adopted_reserve"] = False

    def release_watch(self, watch_id, now, reason, status="closed"):
        """Release every open grant a watch holds: the unspent
        remainder returns to the pool immediately, consumed
        bytes stay accounted in the ledger's history. An adopted
        reservation the watch never consumed, whose window is
        still ahead, reverts to a standing reservation instead
        of closing. Returns (closed, reverted, released_bytes).
        """
        closed = reverted = released = 0
        for grant in self.select(owner=watch_id, status="open"):
            if grant.get("adopted_reserve") \
                    and grant["consumed_bytes"] == 0 \
                    and grant.get("window_end") is not None \
                    and grant["window_end"] > now:
                self._revert_reserve(grant)
                reverted += 1
                continue
            grant["released_bytes"] = max(
                0, grant["bytes"] - grant["consumed_bytes"])
            grant["status"] = status
            grant["closed_at"] = now
            grant["close_reason"] = reason
            released += grant["released_bytes"]
            closed += 1
        if closed or reverted:
            self.save()
        return closed, reverted, released

    def expire(self, now):
        """Release standing reservations whose windows have
        passed unclaimed. Returns the expired grants."""
        expired = []
        for grant in self.select(kind="reserve", status="open"):
            if grant.get("window_end") is not None \
                    and grant["window_end"] < now:
                grant["released_bytes"] = max(
                    0, grant["bytes"] - grant["consumed_bytes"])
                grant["status"] = "expired"
                grant["closed_at"] = now
                grant["close_reason"] = (
                    "the reserved pass window passed unclaimed")
                expired.append(grant)
        if expired:
            self.save()
        return expired


def watch_slice(ledger, watch_id):
    """One watch's slice of the standing ledger: the grants it
    holds and held, summed - granted, consumed, released, shed,
    in total and per class. The Watch Report's reception account
    is drawn from this."""
    grants = ledger.select(owner=watch_id)
    out = {"grants": grants,
           "granted_bytes": sum(g["bytes"] for g in grants),
           "consumed_bytes": sum(g["consumed_bytes"] for g in grants),
           "released_bytes": sum(g["released_bytes"] for g in grants),
           "shed_bytes": sum(g["shed_bytes"] for g in grants),
           "satellite_bytes": sum(g["bytes"] for g in grants
                                  if _is_satellite_grant(g)),
           "by_class": {}}
    for grant in grants:
        cls = out["by_class"].setdefault(
            grant.get("cls") or "?", {"granted": 0, "consumed": 0,
                                      "released": 0, "shed": 0})
        cls["granted"] += grant["bytes"]
        cls["consumed"] += grant["consumed_bytes"]
        cls["released"] += grant["released_bytes"]
        cls["shed"] += grant["shed_bytes"]
    return out


# ------------------------------------------------------- reservations

def _pass_pseudo_item(p):
    """A bill-shaped pass item for one predicted pass, so a
    standing reservation is sized by exactly the arithmetic a
    bill item would carry."""
    return {"kind": "pass", "cls": "A", "desks": ["satellite"],
            "label": "Satellite pass, %s" % p["sat"],
            "dur": (p["los"] - p["aos"]) + 240, "pass": p}


def refresh_reserves(ledger, profile, sats, now,
                     horizon_s=RESERVE_HORIZON_S):
    """Hold standing reservations for the satellite passes the
    ephemeris predicts in the window ahead, and release the
    ones whose windows have passed. Reservations cover APT
    passes only: a METEOR pass is acknowledged only and is
    never reserved. Reservations are computed
    whether or not a watch is standing - a pass that will occur
    during any plausible watch already has its size known.
    They are taken in window order while the pool can carry
    them: a reservation is a promise, and the pool makes no
    promise its cap and margin cannot hold. A pass the pool
    cannot carry is simply not reserved; if a watch bills it,
    the grant path sizes it then, with any reduction stated.
    Returns (created, expired)."""
    from . import orbit
    ledger.set_cap(cap_bytes(profile))
    expired = ledger.expire(now)
    created = 0
    if sats:
        home = profile["home"]
        passes = orbit.all_passes(
            sats, home["lat"], home["lon"], now, now + horizon_s,
            home_alt_m=home.get("alt_m", 0))
        known = [g for g in ledger.grants
                 if g.get("sat") is not None
                 and g.get("status") != "expired"]
        room = ledger.pool()["grantable_bytes"]
        for p in passes:
            if "ISS" in p["sat"]:
                continue  # the ISS desk flies those, not satellite
            if "METEOR" in p["sat"].upper():
                continue  # acknowledged only: never reserved
            if any(g["sat"] == p["sat"]
                   and g.get("window_start") is not None
                   and abs(g["window_start"] - p["aos"]) <= _PASS_MATCH_S
                   for g in known):
                continue
            alloc = item_allocation(_pass_pseudo_item(p))
            if room is not None and alloc["bytes"] > room:
                continue  # the pool cannot carry this one
            grant = ledger.add_grant(
                now, kind="reserve", owner="standing", bill_id=None,
                cls="A", desks=["satellite"],
                label="Satellite pass, %s" % p["sat"],
                sat=p["sat"], downlink=downlink_kind(p["sat"]),
                bytes=alloc["bytes"], computed_bytes=alloc["bytes"],
                tuner_s=alloc["tuner_s"],
                window_start=p["aos"], window_end=p["los"])
            known.append(grant)
            if room is not None:
                room -= alloc["bytes"]
            created += 1
    return created, len(expired)


# ------------------------------------------------------------ granting

# Class order for degradation: C sheds its allowance first, then
# B, then A non-satellite. Satellite pass items are degraded
# last of all, and only if the floored bill still cannot fit.
_DEGRADE_ORDER = ("C", "B", "A")


def _shrink_to_fit(requests, grantable):
    """Shrink a list of pending grant requests (dicts with
    computed, cls, satellite) toward a grantable total, in the
    degradation order, flooring per item. Mutates each request's
    'bytes'. Returns the plain-word statements of every round,
    including the honest overage when even the floored requests
    cannot fit - the grants are then made at the floor and the
    disk ceiling's own shedding rule remains the backstop, as
    the per-watch budget stated before."""
    statements = []

    def total():
        return sum(r["bytes"] for r in requests)

    if total() <= grantable:
        return statements
    for cls in _DEGRADE_ORDER:
        if total() <= grantable:
            break
        touched = 0
        for req in requests:
            if req["cls"] != cls or req["satellite"]:
                continue
            if req["bytes"] > FLOOR_BYTES:
                req["bytes"] = FLOOR_BYTES
                touched += 1
        if touched:
            statements.append(
                "class %s reception grants reduced to the %d-byte "
                "floor (%d items) so the bill's worst case fits "
                "the standing pool" % (cls, FLOOR_BYTES, touched))
    if total() > grantable:
        touched = 0
        for req in requests:
            if req["satellite"] and req["bytes"] > FLOOR_BYTES:
                req["bytes"] = FLOOR_BYTES
                touched += 1
        if touched:
            statements.append(
                "satellite pass grants reduced to the %d-byte "
                "floor (%d passes): the bill's worst case left no "
                "other room in the standing pool"
                % (FLOOR_BYTES, touched))
    if total() > grantable:
        statements.append(
            "the floored bill's worst case still exceeds the "
            "standing pool's grantable room by %d bytes; the "
            "grants stand at the floor and the disk ceiling's "
            "shedding rule is the backstop, stated rather than "
            "hidden" % (total() - grantable))
    return statements


def _item_request(item):
    alloc = item.get("allocation") or item_allocation(item)
    p = item.get("pass") or {}
    satellite = item.get("kind") == "pass" \
        and "satellite" in (item.get("desks") or [])
    return {
        "item": item,
        "cls": item["cls"],
        "satellite": satellite,
        "sat": p.get("sat") if satellite else None,
        "downlink": (downlink_kind(p.get("sat")) if satellite
                     else None),
        "computed": int(alloc["bytes"]),
        "bytes": int(alloc["bytes"]),
        "tuner_s": float(alloc["tuner_s"]),
        "window_start": p.get("aos") if satellite else None,
        "window_end": p.get("los") if satellite else None,
    }


def _grant_fields(req, watch_id):
    item = req["item"]
    return {
        "kind": "watch", "owner": watch_id, "bill_id": item["id"],
        "cls": item["cls"], "desks": list(item.get("desks") or []),
        "label": item.get("label", ""), "sat": req["sat"],
        "downlink": req["downlink"], "bytes": req["bytes"],
        "computed_bytes": req["computed"], "tuner_s": req["tuner_s"],
        "window_start": req["window_start"],
        "window_end": req["window_end"],
    }


def _find_reserve(ledger, req):
    """The open standing reservation for a pass request, if the
    ephemeris refresh already holds one for its window."""
    if not req["satellite"] or req["window_start"] is None:
        return None
    for grant in ledger.select(kind="reserve", status="open"):
        if grant.get("sat") == req["sat"] \
                and grant.get("window_start") is not None \
                and abs(grant["window_start"] - req["window_start"]) \
                <= _PASS_MATCH_S:
            return grant
    return None


def request_watch_grants(ledger, profile, watch_id, bill, now):
    """Grant a standing watch its bill against the standing
    pool. Satellite items adopt their standing reservation when
    one is held (its room is already counted in the pool, so
    adoption needs no new room); the rest are granted fresh,
    shrunk in the degradation order when the pool cannot carry
    the bill's worst case. Returns (grants by bill id, the
    statements muster files, the pool figures after granting).
    """
    ledger.set_cap(cap_bytes(profile))
    ledger.expire(now)
    # Acknowledged METEOR passes take no grant at all: they are
    # filtered out here, so the watch's grant map simply has no
    # entry for their bill items.
    requests = [_item_request(item) for item in bill
                if not _acknowledged_pass(item)]
    adopted = {}
    for req in requests:
        reserve = _find_reserve(ledger, req)
        if reserve is not None:
            adopted[req["item"]["id"]] = reserve
    fresh = [r for r in requests if r["item"]["id"] not in adopted]
    pool = ledger.pool()
    grantable = pool["grantable_bytes"]
    if grantable is None:
        grantable = sum(r["bytes"] for r in fresh)
    statements = _shrink_to_fit(fresh, grantable)
    grants = {}
    for req in requests:
        item_id = req["item"]["id"]
        if item_id in adopted:
            grant = adopted[item_id]
            grant["owner"] = watch_id
            grant["kind"] = "watch"
            grant["bill_id"] = item_id
            grant["cls"] = req["cls"]
            grant["desks"] = list(req["item"].get("desks") or [])
            grant["label"] = req["item"].get("label",
                                             grant["label"])
            grant["adopted_reserve"] = True
            ledger.save()
            grants[item_id] = grant
        else:
            grants[item_id] = ledger.add_grant(
                now, **_grant_fields(req, watch_id))
    return grants, statements, ledger.pool()


def request_item_grant(ledger, profile, watch_id, item, now):
    """A mid-watch grant for an unplanned reception (an
    interrupt the bill never carried). Granted whole when the
    pool carries it, shrunk to the grantable room when that
    room is still a usable grant, refused with the reason when
    it is not. Returns (grant or None, statement or None)."""
    ledger.set_cap(cap_bytes(profile))
    req = _item_request(item)
    pool = ledger.pool()
    grantable = pool["grantable_bytes"]
    if grantable is None or grantable >= req["computed"]:
        return ledger.add_grant(now, **_grant_fields(req, watch_id)), None
    if grantable >= FLOOR_BYTES:
        req["bytes"] = grantable
        statement = (
            "mid-watch grant for %s shrunk to %d of %d computed "
            "bytes: the standing pool's grantable room"
            % (item.get("label", item["id"]), grantable,
               req["computed"]))
        return ledger.add_grant(now, **_grant_fields(req, watch_id)), \
            statement
    return None, (
        "mid-watch grant for %s refused: the standing pool has "
        "%d grantable bytes, under the %d-byte floor"
        % (item.get("label", item["id"]), max(grantable, 0),
           FLOOR_BYTES))


def reconcile_watch(ledger, profile, watch_id, bill, index, now):
    """Re-derive a resumed watch's grants: from the ledger, its
    bill, and the snippet store. A grant whose recorded
    consumption disagrees with the store is corrected to the
    store. A grant that closed while the officer was down, or
    was never made, is taken fresh against the pool's current
    state. Returns (grants by bill id, statements of everything
    that changed)."""
    ledger.set_cap(cap_bytes(profile))
    ledger.expire(now)
    live, shed = {}, {}
    cls_of = {}
    for item in bill:
        cls_of[item["id"]] = item["cls"]
    for row in index.snippet_rows(watch_id):
        bid = bill_id_of_snippet_path(row["path"])
        if not bid:
            continue
        if row["shed"]:
            shed[bid] = shed.get(bid, 0) + row["bytes"]
        else:
            live[bid] = live.get(bid, 0) + row["bytes"]
    grants = {}
    statements = []
    missing = []
    for item in bill:
        if _acknowledged_pass(item):
            continue  # acknowledged only: no grant to reconcile
        bid = item["id"]
        grant = ledger.grant_for(watch_id, bid, status="open")
        if grant is None:
            prior = ledger.grant_for(watch_id, bid)
            if prior is not None:
                statements.append(
                    "grant for %s stood %s in the ledger (%s); a "
                    "fresh grant has been taken"
                    % (bid, prior["status"],
                       prior.get("close_reason") or "no reason "
                       "recorded"))
            else:
                statements.append(
                    "no grant for %s stood in the ledger; one has "
                    "been taken" % bid)
            missing.append(item)
            continue
        changed = False
        if grant["consumed_bytes"] != live.get(bid, 0):
            statements.append(
                "grant for %s re-derived from the snippet store: "
                "consumed %d -> %d bytes"
                % (bid, grant["consumed_bytes"], live.get(bid, 0)))
            grant["consumed_bytes"] = live.get(bid, 0)
            changed = True
        if grant["shed_bytes"] != shed.get(bid, 0):
            grant["shed_bytes"] = shed.get(bid, 0)
            changed = True
        if changed:
            ledger.save()
        grants[bid] = grant
    if missing:
        requests = [_item_request(item) for item in missing]
        pool = ledger.pool()
        grantable = pool["grantable_bytes"]
        if grantable is None:
            grantable = sum(r["bytes"] for r in requests)
        statements.extend(_shrink_to_fit(requests, grantable))
        for req in requests:
            grants[req["item"]["id"]] = ledger.add_grant(
                now, **_grant_fields(req, watch_id))
    # Grants for receptions the bill no longer names (interrupts
    # flown before the interruption) stay as they stand; they
    # close at stand-down with the rest.
    for grant in ledger.select(owner=watch_id, status="open"):
        grants.setdefault(grant["bill_id"], grant)
    return grants, statements


def close_orphaned_grants(ledger, is_standing, now):
    """Release the open grants of watches that are not standing.
    A watch that died holding grants leaves them open in the
    ledger - durable data outlives the process - and the next
    muster is where the pool reclaims them: the owning watch's
    unspent room returns, its consumed stays in the history,
    and a resumed watch reconciles fresh grants for itself and
    says so. Adopted reservations revert as in release_watch.
    Returns statements naming each reclaimed watch."""
    owners = []
    for grant in ledger.select(status="open", kind="watch"):
        owner = grant.get("owner")
        if owner and owner != "standing" and owner not in owners:
            owners.append(owner)
    statements = []
    for owner in owners:
        if is_standing(owner):
            continue
        closed, reverted, released = ledger.release_watch(
            owner, now,
            "the owning watch is not standing; its unspent "
            "grants are released")
        if closed or reverted:
            statements.append(
                "released %d unspent grants (%d bytes) left open "
                "by %s, which is not standing; %d adopted "
                "reservations returned to standing"
                % (closed, released, owner, reverted))
    return statements
