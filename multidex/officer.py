"""The Watch Officer: builds the watch bill and stands the watch.

The bill is built in the design's priority order - Class A clock
events own the tuner by the clock, Class B is a weighted standing
rotation re-weighted by propagation, Class C census sweeps fill the
gaps and own the deep-sweep hours, and interrupts preempt B and C
under an hourly cap. Execution is dwell by dwell against watch time;
a time-scale factor accelerates the pacing without changing any logic
path, which is what makes a 24-hour smoke run possible.
"""

import fcntl
import glob
import math
import os
import subprocess
import threading
import time

from . import budget, displayfeed, gates, orbit, products, retention, \
    updater, util
from .bands import band_name
from .channelizer import plan as channel_plan
from .desks import SPECS, bench_reasons, channels_for, desk_state
from .events import make_event
from .exploit import Exploit
from .index import Index
from .profile import time_scale
from .supervisor import Supervisor
from .tuner import RealSource, TunerLost, TunerServer

# Rotation entry -> (centers, bandwidth Hz, desks, hours rule).
# Centers is one frequency or a tuple of window centres: successive
# dwells of the slot cycle through its windows in order, so every
# channel a desk serves falls inside a captured window on a known
# cadence instead of depending on one wide window containing them
# all (the 0.1.x airband slot centred 125.0 MHz and dropped four of
# its six channels on every dwell).
ROTATION = {
    "adsb":     (1090e6, 2.4e6, ["adsb"], "all"),
    "airband":  ((121.3e6, 118.6e6, 125.8e6, 134.3e6), 2.4e6,
                 ["airband"], "all"),
    "milair":   ((239.275e6, 243.0e6, 248.55e6, 270.35e6, 319.25e6,
                  327.125e6, 336.3e6, 371.35e6), 2.4e6,
                 ["milair"], "all"),
    "datalink": (131.55e6, 2.4e6, ["acars"], "all"),
    "marine":   (162.3e6, 2.4e6, ["ais", "wxradio"], "all"),
    "aprs":     (144.39e6, 2.4e6, ["aprs"], "all"),
    "ism433":   (433.92e6, 2.4e6, ["ism"], "all"),
    "ism915":   (915e6, 2.4e6, ["ism"], "all"),
    "pager":    (929.6125e6, 2.4e6, ["pager"], "all"),
    "cellular": (752.5e6, 2.4e6, ["cellular"], "all"),
    "trunking": (851.75e6, 2.4e6, ["trunking"], "all"),
    "fmband":   (98e6, 2.4e6, ["broadcast", "sweep"], "all"),
    # HF entries carry the HFDL desk on a propagation split:
    # day windows (12.3 MHz spanning the 11.3/13.3 MHz station
    # clusters, 17.9 and 21.9 MHz) serve the day channel set,
    # night windows (10.0, 8.9, 6.6, 5.5 MHz) the night set -
    # both transcribed from the dumphfdl system table and held
    # in desks.HFDL_DAY_CHANNELS / HFDL_NIGHT_CHANNELS. Successive
    # dwells cycle the window centres, as on the airband slots.
    "hf_day":   ((12.3e6, 17.9e6, 21.9e6), 2.4e6,
                 ["sweep", "amateur", "hfdl"], "day"),
    "hf_night": ((10.0e6, 8.9e6, 6.6e6, 5.5e6), 2.4e6,
                 ["sweep", "amateur", "hfdl"], "night"),
}

SWEEP_WINDOWS = [7.05e6, 14.2e6, 28.4e6, 50.5e6, 121.5e6, 146.5e6,
                 255.4e6, 401.5e6, 462.0e6, 751e6, 902.0e6, 1290e6]

SAT_FREQ = {"NOAA 19": 137.1e6, "NOAA 15": 137.62e6,
            "METEOR-M2 4": 137.9e6, "ISS (ZARYA)": 145.8e6}


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) \
        * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _local_hour(epoch):
    return time.localtime(epoch).tm_hour


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def sonde_items(profile, start, end):
    """Class A radiosonde sub-dwells from the profile's launch sites."""
    items = []
    home = profile["home"]
    day = 86400
    for d in range(int(start // day) - 1, int(end // day) + 2):
        for hour in profile["sondes"]["synoptic_hours"]:
            launch = d * day + hour * 3600 - 2700
            for site in profile["sondes"]["sites"]:
                if _haversine_km(home["lat"], home["lon"], site["lat"],
                                 site["lon"]) > 700:
                    continue
                for k in range(4):
                    t0 = launch + k * 1800
                    if end <= t0 or t0 + 600 <= start:
                        continue
                    items.append({
                        "cls": "A", "kind": "sonde",
                        "label": "Radiosonde window, %s" % site["name"],
                        "start": max(t0, start), "dur": 600,
                        "center_hz": 403e6, "bw_hz": 2.4e6,
                        "desks": ["sonde"]})
    return items


def satellite_items(profile, start, end, sats):
    items = []
    home = profile["home"]
    for p in orbit.all_passes(sats, home["lat"], home["lon"], start, end,
                              home_alt_m=home.get("alt_m", 0)):
        freq = SAT_FREQ.get(p["sat"], 137.1e6)
        is_iss = "ISS" in p["sat"]
        items.append({
            "cls": "A", "kind": "pass",
            "label": "Satellite pass, %s" % p["sat"],
            "start": p["aos"] - 120,
            "dur": (p["los"] - p["aos"]) + 240,
            "center_hz": freq, "bw_hz": 2.4e6,
            "desks": ["iss"] if is_iss else ["satellite"],
            "pass": p})
    return items


def build_bill(profile, start, sats):
    """The watch bill: Class A placed first, gaps filled with B and C."""
    end = start + profile["watch"]["hours"] * 3600
    placed = []
    for item in satellite_items(profile, start, end, sats):
        if not any(_overlaps(item["start"], item["start"] + item["dur"],
                             p["start"], p["start"] + p["dur"])
                   for p in placed):
            placed.append(item)
    for item in sonde_items(profile, start, end):
        if not any(_overlaps(item["start"], item["start"] + item["dur"],
                             p["start"], p["start"] + p["dur"])
                   for p in placed):
            placed.append(item)
    placed.sort(key=lambda i: i["start"])

    rotation = profile["rotation"]
    excluded = profile["excluded"]["bands"]
    credits = {name: 0.0 for name in ROTATION}
    sweep_cycle = [0]
    window_cycle = {name: 0 for name in ROTATION}
    last_sweep = [start - 2700]

    def centers_of(name):
        centers = ROTATION[name][0]
        return centers if isinstance(centers, tuple) else (centers,)

    def excluded_window(center, bw):
        lo, hi = center - bw / 2, center + bw / 2
        return any(lo < b[1] and b[0] < hi for b in excluded)

    def pick_entry(t):
        hour = _local_hour(t)
        allowed = []
        for name, (_centers, bw, _d, rule) in ROTATION.items():
            if name not in rotation:
                continue
            if rule == "day" and not 8 <= hour < 20:
                continue
            if rule == "night" and not (hour >= 20 or hour < 8):
                continue
            if all(excluded_window(c, bw) for c in centers_of(name)):
                continue
            allowed.append(name)
        if not allowed:
            return None
        total = sum(rotation[n]["weight"] for n in allowed)
        for n in allowed:
            credits[n] += rotation[n]["weight"]
        pick = max(allowed, key=lambda n: credits[n])
        credits[pick] -= total
        return pick

    def sweep_item(t, dur):
        center = SWEEP_WINDOWS[sweep_cycle[0] % len(SWEEP_WINDOWS)]
        sweep_cycle[0] += 1
        last_sweep[0] = t
        return {"cls": "C", "kind": "sweep", "label": "Census sweep",
                "start": t, "dur": dur, "center_hz": center,
                "bw_hz": 2.4e6, "desks": ["sweep"]}

    def fill(t0, t1):
        out = []
        t = t0
        while t < t1 - 20:
            remaining = t1 - t
            hour = _local_hour(t)
            deep = profile["watch"]["deep_sweep_start"] <= hour \
                < profile["watch"]["deep_sweep_end"]
            if deep:
                out.append(sweep_item(t, min(900, remaining)))
            elif t - last_sweep[0] >= 2700:
                out.append(sweep_item(t, min(300, remaining)))
            else:
                name = pick_entry(t)
                if name is None:
                    out.append(sweep_item(t, min(300, remaining)))
                else:
                    _c, bw, desks, _rule = ROTATION[name]
                    windows = [c for c in centers_of(name)
                               if not excluded_window(c, bw)]
                    center = windows[window_cycle[name] % len(windows)]
                    window_cycle[name] += 1
                    dur = min(rotation[name]["dwell_s"], remaining)
                    out.append({"cls": "B", "kind": "dwell",
                                "label": "Rotation %s" % name,
                                "start": t, "dur": dur,
                                "center_hz": center, "bw_hz": bw,
                                "desks": list(desks)})
            t = out[-1]["start"] + out[-1]["dur"]
        return out

    bill = []
    cursor = start
    for item in placed:
        if item["start"] > cursor:
            bill.extend(fill(cursor, item["start"]))
        bill.append(item)
        cursor = max(cursor, item["start"] + item["dur"])
    if cursor < end:
        bill.extend(fill(cursor, end))
    bill.sort(key=lambda i: i["start"])
    for i, item in enumerate(bill):
        item["id"] = "bill-%03d" % (i + 1)
        item.setdefault("pass", None)
    # Reception containment: every planned reception carries its
    # computed allocation (tuner seconds, worst-case bytes) from
    # the moment the bill exists.
    budget.annotate_bill(bill)
    return bill


class WatchRunner:
    def __init__(self, home, profile, index=None):
        self.home = home
        self.profile = profile
        self.index = index or Index(home)
        self.supervisor = Supervisor()
        self.exploit = None
        self.extra_bench = {}
        self.source_note = None
        self.interrupts_used = {}
        self.flash_count = 0
        self._lock_fh = None
        self._stood_down = False
        self._scale = None
        self._tx_queue = None
        self._tx_worker = None
        self._tx_results = []
        self._tx_lock = threading.Lock()
        self._tx_errors = 0
        # Reception containment: the home's standing allocation
        # ledger, this watch's grants against it, and the live
        # ledger of held snippet bytes they are accounted against.
        self.ledger = None
        self.grants = {}
        self._grant_statements = []
        self._bill_cls = {}
        self._held = {}
        self._held_by_desk = {}
        self._held_total = 0
        self._shed_total = 0
        self._tier_held = {0: 0, 1: 0, 2: 0, 3: 0}
        self._tier_shed = {0: 0, 1: 0, 2: 0, 3: 0}
        self._sealed_held = 0
        # Live updates (0.7.0): this officer's generation, read
        # from its own tree at startup and never from the
        # pointer; the handover state the boundary hook sets
        # when a successor is to adopt the watch; and the
        # probation record while this generation is on probation.
        self.generation, self._gen_root = updater.own_generation()
        self._handed_over = False
        self._handover_info = None
        self._probation = None
        self._keeperless_notice_for = None
        # The display feed the Console reads (0.8.0), opened when
        # the watch folder is known. Publishing is best-effort
        # and outside the watch's record; see displayfeed.py.
        self.display = None

    # ------------------------------------------------------------ helpers
    def _resolve_source(self, watch_start):
        """Pick the source for a new watch; returns (source, note).

        A profile that asks for the real source on a machine that
        cannot stand it - tools missing or the dongle unreachable -
        falls back to the simulated source, and the substitution is
        returned as a note the muster, the status verb and every
        product must state. It is never made silently. An explicitly
        simulated profile is used exactly as configured.
        """
        if self.profile["source"]["kind"] == "real":
            source = RealSource(self.profile)
            ok, detail = source.available()
            if ok:
                return source, None
            from .sim import SimSource
            note = ("the profile asked for the real source, but it "
                    "could not be stood: %s. This watch therefore "
                    "runs on the simulated source, and every event "
                    "in it is synthetic. This substitution is stated "
                    "in the muster output, in the status verb, and "
                    "in every product's coverage account." % detail)
            return SimSource(self.profile, watch_start), note
        from .sim import SimSource
        return SimSource(self.profile, watch_start), None

    def _source_for_meta(self, meta, watch_start):
        """The source a resumed watch flies: the one it mustered on,
        recorded in its metadata, fallback included."""
        if meta.get("source") == "real":
            return RealSource(self.profile)
        from .sim import SimSource
        return SimSource(self.profile, watch_start)

    def _bench_set(self, source=None):
        """The full benched set with reasons, from the one bench
        computation (desks.bench_reasons): muster benches, supervisor
        benches, capability and roster benches together."""
        if source is not None:
            kind = source.kind
            capabilities = source.capabilities() \
                if kind == "real" else None
        else:
            kind = self.profile["source"]["kind"]
            capabilities = RealSource(self.profile).capabilities() \
                if kind == "real" else None
        return bench_reasons(self.profile, kind, capabilities,
                             self.extra_bench, self.supervisor.benched)

    def _desk_benched_reason(self, name, source):
        return self._bench_set(source).get(name)

    def _live_other_watch(self, exclude=None):
        """(watch_id, pid) of another incomplete watch in this home
        whose officer process is alive, else None. The semantic gate
        for mutual exclusion: one home, one running watch."""
        watches_dir = os.path.join(self.home, "watches")
        try:
            folders = sorted(os.listdir(watches_dir))
        except OSError:
            return None
        for folder in folders:
            if folder == exclude:
                continue
            state = util.read_json(os.path.join(
                watches_dir, folder, "state.json"), default=None)
            if state and not state.get("complete") \
                    and util.pid_alive(state.get("pid")):
                return folder, state.get("pid")
        return None

    def _watch_is_standing(self, watch_id):
        """True when a watch's officer process is alive: its
        state file is incomplete and the pid it recorded still
        exists. The allocation ledger's orphan reclamation reads
        standing-ness through this, never through the pointer."""
        state = util.read_json(os.path.join(
            self.home, "watches", watch_id, "state.json"),
            default=None)
        return bool(state) and not state.get("complete") \
            and util.pid_alive(state.get("pid"))

    def _acquire_lock(self):
        """Take the home's watch lock without blocking, or None.

        The flock is released by the kernel when the holding process
        dies, so a stale lock from a dead officer never blocks a new
        watch; the live-pid scan above is the semantic gate.
        """
        os.makedirs(self.home, exist_ok=True)
        fh = open(os.path.join(self.home, "watch.lock"), "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return None
        return fh

    def _release_lock(self):
        fh = self._lock_fh
        if fh is not None:
            self._lock_fh = None
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()

    def _stop_requested(self):
        return os.path.exists(os.path.join(self.watch_dir, "stop.flag"))

    def _open_display_feed(self):
        """Open the display feed for this watch folder (0.8.0).

        The profile's advanced display_feed key governs it, on
        unless the profile turns it off. The feed is a projection
        for the Console, never part of the record: a watch with
        the feed off files an identical event record, and a
        failure to open the feed is swallowed here for the same
        reason every publish in displayfeed is best-effort."""
        try:
            enabled = bool(self.profile["advanced"].get(
                "display_feed", True))
        except Exception:
            enabled = True
        try:
            self.display = displayfeed.DisplayFeed(self.watch_dir,
                                                   enabled=enabled)
        except Exception:
            self.display = None

    def _file(self, ev):
        # FLASH first: the chime outcome is recorded in the event's
        # own data, so the event must not be persisted before
        # maybe_flash has had its say.
        name = products.maybe_flash(self.index, self.watch_id, ev,
                                    self.profile)
        if name:
            self.flash_count += 1
        self.index.add_event(ev)
        if self.display is not None:
            # The mind stream: the officer's own filed record,
            # mirrored to the display feed at filing time.
            self.display.thought(ev.get("t_start"), ev.get("desk"),
                                 ev.get("kind"), ev.get("content"),
                                 flash=bool(name))
        return ev

    def _pace(self, seconds):
        scale = self._scale if self._scale else time_scale(self.profile)
        if scale <= 0 or seconds <= 0:
            return
        deadline = time.time() + seconds / scale
        while time.time() < deadline:
            if self._stop_requested():
                return
            time.sleep(min(0.05, max(0.0, deadline - time.time())))

    # -------------------------------------------------------------- start
    def start(self, start_epoch=None, resume_id=None):
        if resume_id:
            return self._resume(resume_id)
        # Mutual exclusion, before anything is built or written: a
        # home stands one watch at a time. A second officer would
        # share the Index, the current_watch pointer and the tuner.
        other = self._live_other_watch()
        if other:
            return {"ok": False,
                    "refusal": "watch %s is already running in this "
                               "home (officer pid %s); a home stands "
                               "one watch at a time" % other}
        self._lock_fh = self._acquire_lock()
        if self._lock_fh is None:
            return {"ok": False,
                    "refusal": "another watch officer holds this "
                               "home's watch lock; a home stands one "
                               "watch at a time"}
        try:
            return self._start_fresh(start_epoch)
        finally:
            self._release_lock()

    def _start_fresh(self, start_epoch=None):
        profile = self.profile
        self._stood_down = False
        # The effective time scale is fixed at muster: a malformed
        # MULTIDEX_TIME_SCALE refuses the start here, in the open.
        self._scale = time_scale(profile)
        start = start_epoch or time.time()
        source, source_note = self._resolve_source(start)
        self.source_note = source_note
        gate_list = gates.run_gates(profile, self.home, source,
                                    at_epoch=start)
        if source_note:
            gate_list = [
                (n, ok, source_note if n == "tuner" else d)
                for n, ok, d in gate_list]
        if not gates.gates_passed(gate_list):
            failed = ["%s: %s" % (n, d) for n, ok, d in gate_list if not ok]
            return {"ok": False,
                    "refusal": "Muster failed; the watch does not start. "
                               + "; ".join(failed)}
        sats, _path = gates.load_sats(profile)
        age = orbit.tle_age_days(sats, start)
        if not sats:
            self.extra_bench["satellite"] = (
                "no orbital elements loaded at muster")
        elif age is not None and age > 30:
            self.extra_bench["satellite"] = (
                "orbital elements %.0f days old, over the 30-day rule"
                % age)
        watch_id = "watch-" + time.strftime("%Y%m%d-%H%M%S",
                                            time.gmtime(start))
        if os.path.exists(os.path.join(self.home, "watches", watch_id)):
            watch_id += "-%d" % int(start % 60)
        self.watch_id = watch_id
        self.watch_dir = self.index.watch_dir(watch_id)
        self.supervisor.set_watch_dir(self.watch_dir)
        self._open_display_feed()
        self.exploit = Exploit(self.index, profile)
        self._start_transcription()
        bill = build_bill(profile, start, sats)
        util.write_json(os.path.join(self.watch_dir, "bill.json"), bill)
        # Reception containment: this watch's allocations are
        # grants against the home's standing allocation ledger,
        # taken at muster before the first dwell. Grants a dead
        # watch left open are reclaimed first, standing satellite
        # reservations are refreshed from the same ephemeris the
        # bill was built from, and the bill is then granted
        # against the pool's current state - shrunk in the stated
        # order, with the statements filed below, when the pool
        # cannot carry the worst case.
        self.ledger = budget.AllocationLedger.load(self.home)
        orphan_statements = budget.close_orphaned_grants(
            self.ledger, self._watch_is_standing, start)
        budget.refresh_reserves(self.ledger, profile, sats, start)
        pool_before = self.ledger.pool()
        self.grants, self._grant_statements, pool_after = \
            budget.request_watch_grants(
                self.ledger, profile, watch_id, bill, start)
        self._bill_cls = {i["id"]: i["cls"] for i in bill}
        util.write_json(os.path.join(self.watch_dir, "budget.json"),
                        {"snapshot_of": "the standing allocation "
                                        "ledger at muster; the "
                                        "ledger is the system of "
                                        "record, this file is a "
                                        "view of it",
                         "pool": pool_after,
                         "grants": [self.grants[i["id"]]
                                    for i in bill
                                    if i["id"] in self.grants],
                         "statements": self._grant_statements})
        slice0 = budget.watch_slice(self.ledger, watch_id)
        meta = {
            "watch_id": watch_id, "start": start,
            "planned_end": start + profile["watch"]["hours"] * 3600,
            "end": None, "source": source.kind,
            "profile_name": profile["name"],
            "region": profile["home"]["region"],
            "antenna": profile["home"]["antenna"],
            "disk_ceiling_gb": profile["watch"]["disk_ceiling_gb"],
            "ephemeris_age_days": (None if age is None
                                   else round(max(age, 0.0), 1)),
            "ended_early": False, "end_reason": None, "resumed": False,
            "generation": self.generation,
            "source_note": source_note,
            "time_scale": self._scale,
            "reception_pool": pool_after,
            "reception_grants": {
                "granted_bytes": slice0["granted_bytes"],
                "satellite_bytes": slice0["satellite_bytes"],
                "by_class": {c: v["granted"] for c, v in
                             slice0["by_class"].items()},
                "statements": list(self._grant_statements)},
        }
        self.meta = meta
        self.index.set_watch_meta(watch_id, meta)
        util.write_json(os.path.join(self.watch_dir, "watch.json"), meta)
        util.write_json(os.path.join(self.home, "current_watch"), watch_id)
        self.tuner = TunerServer(source, profile)
        self.tuner.begin_watch(start)
        muster_data = {"gates": [(n, ok, d) for n, ok, d in gate_list],
                       "time_scale": self._scale,
                       "generation": self.generation}
        if source_note:
            muster_data["source_note"] = source_note
            print("Source note: %s" % source_note)
        self._file(make_event(
            watch_id, "officer", "system", start, start,
            content="muster passed; watch stood with %d bill items"
                    % len(bill), grade="CONFIRMED",
            data=muster_data))
        self._file(make_event(
            watch_id, "officer", "system", start, start,
            grade="CONFIRMED",
            content="reception allocations: the standing pool "
                    "(cap %d bytes; %d consumed and %d reserved "
                    "when this watch mustered) granted this watch "
                    "%d bytes across %d receptions, %d of it "
                    "satellite passes, leaving %d bytes free"
                    % (pool_after["cap_bytes"],
                       pool_before["consumed_bytes"],
                       pool_before["reserved_bytes"],
                       slice0["granted_bytes"], len(bill),
                       slice0["satellite_bytes"],
                       pool_after["free_bytes"]),
            data={"reception_pool": pool_after,
                  "reception_grants": meta["reception_grants"]}))
        for statement in orphan_statements:
            self._file(make_event(
                watch_id, "officer", "system", start, start,
                grade="CONFIRMED",
                content="reception allocations: %s" % statement,
                data={"reception_orphans_released": statement}))
        for statement in self.ledger.load_statements:
            self._file(make_event(
                watch_id, "officer", "system", start, start,
                grade="CONFIRMED",
                content="reception allocations: %s" % statement,
                data={"reception_policy_release": statement}))
        for statement in self._grant_statements:
            self._file(make_event(
                watch_id, "officer", "system", start, start,
                grade="CONFIRMED",
                content="reception allocation reduced at muster: %s"
                        % statement,
                data={"reception_allocation_reduced": statement}))
        for name in sorted(SPECS):
            reason = self._desk_benched_reason(name, source)
            if reason:
                self._file(make_event(
                    watch_id, "officer", "desk_health", start, start,
                    content="desk %s benched at muster: %s"
                            % (name, reason), grade="CONFIRMED",
                    data={"desk": name, "benched": True,
                          "reason": reason}))
        # Update stages the check worker queued while no officer
        # was filing land in the Index now, and a generation the
        # keeper flipped to with no watch standing begins its
        # probation on this watch.
        self._drain_update_events(start)
        self._begin_probation_if_marked(start)
        # The first state the operator can read: published at muster,
        # before the first dwell runs, carrying this officer's pid
        # and heartbeat from the start.
        self._save_state(0, None, start, elapsed_override=0.0)
        try:
            self._run_bill(bill, 0, start)
        except Exception:
            if not self._stood_down:
                try:
                    self._stand_down(bill, start, "officer failure")
                except Exception:
                    pass  # the on-disk record is the honest residue
            raise
        if self._handed_over:
            return {"ok": True, "watch_id": watch_id,
                    "handed_over": True,
                    "to_generation":
                        self._handover_info["to_generation"]}
        return {"ok": True, "watch_id": watch_id}

    def _resume(self, watch_id):
        meta = self.index.get_watch_meta(watch_id)
        if meta is None:
            return {"ok": False,
                    "refusal": "no watch %s in the Index" % watch_id}
        state = util.read_json(os.path.join(
            self.index.watch_dir(watch_id), "state.json"),
            default={}) or {}
        # A watch whose officer is alive is not interrupted, and a
        # home with another live watch cannot take this one either.
        if state and not state.get("complete") \
                and util.pid_alive(state.get("pid")):
            return {"ok": False,
                    "refusal": "watch %s is already running (officer "
                               "pid %s); it is not interrupted"
                               % (watch_id, state.get("pid"))}
        other = self._live_other_watch(exclude=watch_id)
        if other:
            return {"ok": False,
                    "refusal": "watch %s is already running in this "
                               "home (officer pid %s); a home stands "
                               "one watch at a time" % other}
        self._lock_fh = self._acquire_lock()
        if self._lock_fh is None:
            return {"ok": False,
                    "refusal": "another watch officer holds this "
                               "home's watch lock; a home stands one "
                               "watch at a time"}
        try:
            return self._resume_locked(watch_id, meta, state)
        finally:
            self._release_lock()

    def _resume_locked(self, watch_id, meta, state):
        profile = self.profile
        self._stood_down = False
        self._scale = time_scale(profile)
        self.watch_id = watch_id
        self.watch_dir = self.index.watch_dir(watch_id)
        self.supervisor.set_watch_dir(self.watch_dir)
        self._open_display_feed()
        self.meta = meta
        self.exploit = Exploit(self.index, profile)
        bill = util.read_json(os.path.join(self.watch_dir, "bill.json"),
                              default=[])
        self._bill_cls = {i["id"]: i["cls"] for i in bill}
        self._rebuild_held()
        # Reception grants are reconciled, never recomputed from
        # scratch and never assumed to have survived: the resumed
        # watch re-derives them from the standing ledger, its
        # bill and the snippet store, and the reconciliation
        # statements file with the resume event below - what
        # changed is said, in the open.
        self.ledger = budget.AllocationLedger.load(self.home)
        resume_t = meta["start"] + state.get("elapsed_s", 0)
        self.grants, self._grant_statements = \
            budget.reconcile_watch(self.ledger, profile, watch_id,
                                   bill, self.index, resume_t)
        if "reception_pool" not in meta:
            meta["reception_pool"] = self.ledger.pool()
        util.write_json(os.path.join(self.watch_dir, "budget.json"),
                        {"snapshot_of": "the standing allocation "
                                        "ledger at resume; the "
                                        "ledger is the system of "
                                        "record, this file is a "
                                        "view of it",
                         "pool": self.ledger.pool(),
                         "grants": [self.grants[i["id"]]
                                    for i in bill
                                    if i["id"] in self.grants],
                         "statements": self._grant_statements})
        source = self._source_for_meta(meta, meta["start"])
        self.source_note = meta.get("source_note")
        # The muster gates re-run on resume: the footing is verified
        # again, not inherited from a watch that died.
        gate_list = gates.run_gates(profile, self.home, source,
                                    at_epoch=time.time())
        if not gates.gates_passed(gate_list):
            failed = ["%s: %s" % (n, d) for n, ok, d in gate_list
                      if not ok]
            return {"ok": False,
                    "refusal": "Resume refused; the muster gates do "
                               "not pass. " + "; ".join(failed)}
        sats, _path = gates.load_sats(profile)
        age = orbit.tle_age_days(sats, meta["start"])
        if not sats:
            self.extra_bench["satellite"] = (
                "no orbital elements loaded at muster")
        elif age is not None and age > 30:
            self.extra_bench["satellite"] = (
                "orbital elements %.0f days old, over the 30-day rule"
                % age)
        self.tuner = TunerServer(source, profile)
        self.tuner.begin_watch(meta["start"])
        meta["resumed"] = True
        meta["generation"] = self.generation
        self.index.set_watch_meta(watch_id, meta)
        util.write_json(os.path.join(self.home, "current_watch"),
                        watch_id)
        # FLASH numbering continues from the files already on disk;
        # a resumed watch never reuses a FLASH number.
        self.flash_count = len(glob.glob(os.path.join(
            self.watch_dir, "FLASH-*.md")))
        # The watch clock, not wall time: the resume event belongs to
        # the watch's own timeline at the point it stopped.
        resume_t = meta["start"] + state.get("elapsed_s", 0)
        self._file(make_event(
            watch_id, "officer", "system", resume_t, resume_t,
            content="watch resumed from on-disk state at bill item %d"
                    % state.get("next_index", 0), grade="CONFIRMED",
            data={"resumed": True,
                  "generation": self.generation}))
        # A resume under a new generation is an adoption when a
        # handover names it, and a rollback landing when the
        # keeper's note names it; both file here, with the
        # tuner gate above standing as the device self-check.
        self._adopt_handover(resume_t, gate_list)
        self._drain_update_events(resume_t)
        budget.refresh_reserves(self.ledger, profile, sats, resume_t)
        for statement in self.ledger.load_statements:
            self._file(make_event(
                watch_id, "officer", "system", resume_t, resume_t,
                grade="CONFIRMED",
                content="reception allocations: %s" % statement,
                data={"reception_policy_release": statement}))
        for statement in self._grant_statements:
            self._file(make_event(
                watch_id, "officer", "system", resume_t, resume_t,
                grade="CONFIRMED",
                content="reception grant reconciliation on "
                        "resume: %s" % statement,
                data={"reception_reconciled": statement}))
        # The bench set is re-filed for the resumed watch, from the
        # same one computation the muster uses.
        for name in sorted(SPECS):
            reason = self._desk_benched_reason(name, source)
            if reason:
                self._file(make_event(
                    watch_id, "officer", "desk_health", resume_t,
                    resume_t,
                    content="desk %s benched at resume: %s"
                            % (name, reason), grade="CONFIRMED",
                    data={"desk": name, "benched": True,
                          "reason": reason}))
        # Dwells already executed are known by their dwell records:
        # a dwell interrupted mid-flight by the crash has no record
        # boundary confusion - it is re-flown only if it never filed
        # its dwell record; every recorded dwell is skipped.
        executed = {ev["data"].get("bill_id")
                    for ev in self.index.dwell_records(watch_id)}
        executed.discard(None)
        try:
            self._run_bill(bill, state.get("next_index", 0),
                           meta["start"], executed_ids=executed,
                           resume_elapsed=state.get("elapsed_s", 0))
        except Exception:
            if not self._stood_down:
                try:
                    self._stand_down(bill, meta["start"],
                                     "officer failure")
                except Exception:
                    pass
            raise
        if self._handed_over:
            return {"ok": True, "watch_id": watch_id,
                    "handed_over": True,
                    "to_generation":
                        self._handover_info["to_generation"]}
        return {"ok": True, "watch_id": watch_id}

    def _rebuild_held(self):
        """Re-derive the held-bytes ledger from the Index's
        snippet rows: the ledger is a view of the snippet store,
        so a resumed watch accounts exactly what the store holds."""
        self._held = {}
        self._held_by_desk = {}
        self._held_total = 0
        self._shed_total = 0
        self._tier_held = {0: 0, 1: 0, 2: 0, 3: 0}
        self._tier_shed = {0: 0, 1: 0, 2: 0, 3: 0}
        self._sealed_held = 0
        for row in self.index.snippet_rows(self.watch_id):
            if row["shed"]:
                self._shed_total += row["bytes"]
                self._tier_shed[row["tier"]] = \
                    self._tier_shed.get(row["tier"], 0) + row["bytes"]
                continue
            if row.get("sealed"):
                # Sealed captures stand outside the tiers in
                # every account, this one included.
                self._sealed_held += row["bytes"]
            else:
                self._tier_held[row["tier"]] = \
                    self._tier_held.get(row["tier"], 0) + row["bytes"]
            bid = budget.bill_id_of_snippet_path(row["path"])
            if bid:
                self._held[bid] = self._held.get(bid, 0) + row["bytes"]
            desk = row["desk"]
            self._held_by_desk[desk] = \
                self._held_by_desk.get(desk, 0) + row["bytes"]
            self._held_total += row["bytes"]

    def _held_add(self, bill_id, desk, size):
        if bill_id:
            self._held[bill_id] = self._held.get(bill_id, 0) + size
        self._held_by_desk[desk] = \
            self._held_by_desk.get(desk, 0) + size
        self._held_total += size

    def _held_shed(self, victim):
        bid = budget.bill_id_of_snippet_path(victim["path"])
        if bid and bid in self._held:
            self._held[bid] = max(0, self._held[bid]
                                  - victim["bytes"])
        desk = victim["desk"]
        if desk in self._held_by_desk:
            self._held_by_desk[desk] = max(
                0, self._held_by_desk[desk] - victim["bytes"])
        self._held_total = max(0, self._held_total - victim["bytes"])
        self._shed_total += victim["bytes"]

    def _pool_state(self):
        """The standing-pool block state.json carries: the pool's
        own figures (cap, consumed, reserved, free) with this
        watch's slice beside them, per class - the same numbers
        the Watch Report's reception account renders at
        stand-down. The allocated/consumed keys the dashboard
        has always read now carry the pool's granted and
        consumed, because the pool is what is standing."""
        pool = self.ledger.pool()
        sl = budget.watch_slice(self.ledger, self.watch_id)
        consumed_by_class = {}
        for bid, n in self._held.items():
            cls = self._bill_cls.get(bid)
            if cls:
                consumed_by_class[cls] = \
                    consumed_by_class.get(cls, 0) + n
        by_class = {}
        for cls, gslice in sorted(sl["by_class"].items()):
            by_class[cls] = {
                "allocated": gslice["granted"],
                "consumed": consumed_by_class.get(cls, 0)}
        return {
            "cap_bytes": pool["cap_bytes"],
            "ceiling_bytes": pool["cap_bytes"],
            "allocated_bytes": pool["granted_bytes"],
            "granted_bytes": pool["granted_bytes"],
            "reserved_bytes": pool["reserved_bytes"],
            "consumed_bytes": pool["consumed_bytes"],
            "free_bytes": pool["free_bytes"],
            "shed_bytes": self._shed_total,
            "satellite_reserve_bytes":
                pool["satellite_reserve_bytes"],
            "satellite_consumed_bytes":
                pool["satellite_consumed_bytes"],
            "watch_granted_bytes": sl["granted_bytes"],
            "watch_consumed_bytes": self._held_total,
            "watch_released_bytes": sl["released_bytes"],
            "by_tier": {str(t): {
                "held": self._tier_held.get(t, 0),
                "shed": self._tier_shed.get(t, 0)}
                for t in (0, 1, 2, 3)},
            "sealed_bytes": self._sealed_held,
            "by_class": by_class,
            "degraded": bool(self._grant_statements),
        }

    def _close_item_grant(self, item, now):
        """Release a finished dwell's or pass's unspent grant
        back to the standing pool."""
        if self.ledger is None:
            return
        grant = self.grants.get(item["id"])
        if grant is None or grant.get("status") != "open":
            return
        reason = "pass ended" if item.get("kind") == "pass" \
            else "dwell ended"
        self.ledger.close(grant["id"], now, reason)

    # ---------------------------------------------------------------- run
    def _run_bill(self, bill, from_index, watch_start,
                  executed_ids=None, resume_elapsed=0.0):
        profile = self.profile
        sitrep_every = profile["watch"]["sitrep_hours"] * 3600
        next_sitrep = watch_start + sitrep_every
        sitrep_seq = len(glob.glob(os.path.join(
            self.watch_dir, "SITREP-*.md")))
        executed_ids = executed_ids or set()
        # A resumed watch does not re-render SITREPs for boundaries
        # its elapsed time already covered.
        while next_sitrep <= watch_start + resume_elapsed:
            next_sitrep += sitrep_every
        source = self.tuner.source
        scale = self._scale if self._scale else time_scale(profile)
        # The watch clock position. On the real source it tracks
        # wall time: an item that starts late (because earlier work
        # took real time) executes at its true time and is recorded
        # so, and pacing sleeps only the remainder of its planned
        # duration. On the simulated source the bill's own times are
        # the watch's times and execution is instantaneous in them.
        position = watch_start + resume_elapsed
        idx = from_index
        end_reason = None
        while idx < len(bill):
            item = bill[idx]
            if self._stop_requested():
                end_reason = "operator stop"
                break
            if item["id"] in executed_ids:
                idx += 1
                continue
            planned_start = item["start"]
            if source.kind == "real":
                actual_start = max(planned_start, position)
            else:
                actual_start = planned_start
            self._save_state(idx, item, watch_start,
                             elapsed_override=actual_start - watch_start,
                             executing=item["id"])
            t_exec0 = time.monotonic()
            try:
                self._execute(item, watch_start, actual_start)
            except Exception as exc:
                # One bill item's failure never ends the watch: it
                # files as a system gap and the bill moves on.
                try:
                    self._file(make_event(
                        self.watch_id, "officer", "gap", actual_start,
                        actual_start + item["dur"],
                        freq_hz=item["center_hz"], grade="CONFIRMED",
                        content="bill item %s failed unexpectedly "
                                "and was skipped: %s: %s"
                                % (item["id"], type(exc).__name__, exc),
                        data={"bill_id": item["id"],
                              "cause": "officer error"}))
                except Exception:
                    pass
            exec_cost = 0.0
            if source.kind == "real":
                exec_cost = (time.monotonic() - t_exec0) * scale
                end_position = actual_start + max(item["dur"],
                                                  exec_cost)
            else:
                end_position = planned_start + item["dur"]
            # The dwell or pass has ended: its grant's unspent
            # remainder releases back to the standing pool here,
            # not at stand-down.
            self._close_item_grant(item, end_position)
            position = end_position
            self._save_state(idx + 1, item, watch_start,
                             elapsed_override=end_position - watch_start)
            if source.kind == "real":
                self._pace(max(0.0, item["dur"] - exec_cost))
            else:
                self._pace(item["dur"])
            idx += 1
            while end_position >= next_sitrep \
                    and next_sitrep <= watch_start + \
                    profile["watch"]["hours"] * 3600:
                sitrep_seq += 1
                text = products.render_sitrep(
                    self.index, self.watch_id,
                    next_sitrep - sitrep_every, next_sitrep, sitrep_seq)
                util.write_text(os.path.join(
                    self.watch_dir, "SITREP-%d.md" % sitrep_seq), text)
                for ev in self.exploit.check_missing_regulars(
                        self.watch_id, next_sitrep, watch_start):
                    self._file(ev)
                next_sitrep += sitrep_every
            # The dwell boundary: probation accounting for the
            # item just finished, then the live-update boundary
            # hook - a handover flown here returns from the run
            # without standing the watch down, and the successor
            # adopts it from the record.
            self._after_item(bill, idx, item, end_position)
            if self._handed_over:
                return
            if self._stop_requested():
                end_reason = "operator stop"
                break
        self._stand_down(bill, watch_start, end_reason)

    def _save_state(self, next_index, item, watch_start,
                    elapsed_override=None, executing=None):
        """Publish the operator's progress view.

        Saved at muster, at every dwell start and at every dwell
        end, so elapsed time tracks the watch clock rather than
        jumping a dwell at a time. Every save carries the officer's
        pid and a heartbeat, and the benched list comes from the one
        bench computation, capability benches included.
        """
        tuner = getattr(self, "tuner", None)
        benched = self._bench_set(tuner.source if tuner else None)
        if item is not None:
            elapsed = (item["start"] + item["dur"]) - watch_start
            current = "%s (%s)" % (item["label"], ", ".join(
                item["desks"]) or "no desks")
        else:
            elapsed = 0.0
            current = "at muster"
        if elapsed_override is not None:
            elapsed = elapsed_override
        state = {
            "next_index": next_index,
            "elapsed_s": elapsed,
            "current": current,
            "events": self.index.count_events(watch_id=self.watch_id),
            "dossiers": len(self.index.dossiers_in_watch(self.watch_id)),
            "flash": self.flash_count,
            "benched": sorted(benched),
            "complete": False,
            "pid": os.getpid(),
            "heartbeat_s": elapsed,
            "executing": executing,
            "generation": self.generation,
        }
        if self.ledger is not None:
            state["budget"] = self._pool_state()
        util.write_json(os.path.join(self.watch_dir, "state.json"), state)

    def _execute(self, item, watch_start, actual_start=None):
        self._drain_transcripts()
        planned_start = item["start"]
        t0 = actual_start if actual_start is not None else planned_start
        t1 = t0 + item["dur"]
        source = self.tuner.source
        standing = []
        for name in item["desks"]:
            if self._desk_benched_reason(name, source) is None:
                standing.append(name)
        # The audio census rides every captured window (rotation,
        # sweep and pass items alike): it is a window desk with no
        # channels of its own, fed from the window's IQ.
        if "audiocensus" not in standing and \
                self._desk_benched_reason("audiocensus", source) is None:
            standing.append("audiocensus")
        desk_channels = {name: channels_for(name) for name in standing}
        heavy = {n for n in standing if SPECS[n][2] == "heavy"}
        vfos, dropped = channel_plan(item["center_hz"], item["bw_hz"],
                                     desk_channels, heavy)
        if item["kind"] == "pass" and ("satellite" in standing
                                       or "iss" in standing):
            pdesk = "iss" if "iss" in item["desks"] else "satellite"
            vfo = {"desk": pdesk, "freq_hz": item["center_hz"],
                   "bw_hz": 150e3, "pass": item.get("pass")}
            vfos = [vfo]
        new_events = []
        heard = {}
        try:
            feeds = self.tuner.capture(item["center_hz"], item["bw_hz"],
                                       t0, t1, vfos)
        except TunerLost as exc:
            self._file(make_event(
                self.watch_id, "officer", "gap", t0, t1,
                freq_hz=item["center_hz"], grade="CONFIRMED",
                content="tuner lost during %s: %s" % (item["label"], exc),
                data={"bill_id": item["id"], "cause": "tuner loss"}))
            feeds = None
        except Exception as exc:
            self._file(make_event(
                self.watch_id, "officer", "gap", t0, t1,
                freq_hz=item["center_hz"], grade="CONFIRMED",
                content="capture failed during %s: %s: %s"
                        % (item["label"], type(exc).__name__, exc),
                data={"bill_id": item["id"], "cause": "capture failure"}))
            feeds = None
        pre_counts = {}
        work_lines = {}
        if feeds is not None:
            for desk_name, problem in \
                    self.tuner.capture_errors().items():
                self._file(make_event(
                    self.watch_id, "officer", "gap", t0, t1,
                    freq_hz=item["center_hz"], grade="CONFIRMED",
                    content="capture failed for %s during %s: %s"
                            % (desk_name, item["label"], problem),
                    data={"bill_id": item["id"], "desk": desk_name,
                          "cause": "capture failure"}))
            for desk_name, feed in feeds.items():
                for ch_err in feed.get("channel_errors") or []:
                    self._file(make_event(
                        self.watch_id, "officer", "gap", t0, t1,
                        freq_hz=ch_err.get("freq_hz"), grade="CONFIRMED",
                        content="channel %s capture failed for %s "
                                "during %s: %s"
                                % (util.fmt_hz(ch_err.get("freq_hz")),
                                   desk_name, item["label"],
                                   ch_err.get("error")),
                        data={"bill_id": item["id"], "desk": desk_name,
                              "cause": "capture failure",
                              "channel_freq_hz": ch_err.get("freq_hz")}))
            by_desk = {}
            for vfo in vfos:
                by_desk.setdefault(vfo["desk"], vfo)
            for name, vfo in by_desk.items():
                feed = feeds.get(name)
                if feed is None:
                    continue
                ctx = {"watch_id": self.watch_id, "desk": name,
                       "t0": t0, "t1": t1, "freq_hz": vfo["freq_hz"],
                       "home": (self.profile["home"]["lat"],
                                self.profile["home"]["lon"]),
                       "profile": self.profile,
                       "pass": item.get("pass")}
                # Per-desk isolation: one desk's processing failure
                # files as that desk's gap; the dwell's other desks
                # are unaffected.
                try:
                    events, info = self.supervisor.run(name, feed, ctx)
                    if info.get("crashes"):
                        self._file(make_event(
                            self.watch_id, "officer", "desk_health",
                            t0, t1, grade="CONFIRMED",
                            content="desk %s crashed and was restarted"
                                    % name,
                            data={"desk": name, "crashed": True}))
                    if info.get("benched"):
                        content = ("desk %s benched for the watch: %s"
                                   % (name, info["reason"]))
                        if info.get("stderr_tail"):
                            content += ("; last worker output: %s"
                                        % info["stderr_tail"])
                        self._file(make_event(
                            self.watch_id, "officer", "desk_health",
                            t0, t1, grade="CONFIRMED", content=content,
                            data={"desk": name, "benched": True,
                                  "reason": info["reason"]}))
                    for ev in events:
                        self._adopt_snippet(ev, name, item["id"])
                        self._submit_transcription(ev)
                        new_events.append(ev)
                    heard[name] = len(events)
                    if events:
                        work_lines[name] = [
                            ev.get("content") or ""
                            for ev in events]
                except Exception as exc:
                    self._file(make_event(
                        self.watch_id, "officer", "gap", t0, t1,
                        freq_hz=item["center_hz"], grade="CONFIRMED",
                        content="desk %s processing failed during %s: "
                                "%s: %s" % (name, item["label"],
                                            type(exc).__name__, exc),
                        data={"bill_id": item["id"], "desk": name,
                              "cause": "desk failure"}))
            # Snapshot dossier event counts for this batch's emitters
            # BEFORE the batch is indexed: exploitation judges first
            # contact against the pre-batch count, so a first batch
            # of two or more events still counts as a new emitter.
            for ev in new_events:
                if ev.get("emitter"):
                    key = self.index.resolve(ev["emitter"])
                    if key not in pre_counts:
                        dossier = self.index.dossier(key)
                        pre_counts[key] = dossier["events_count"] \
                            if dossier else 0
            # FLASH before persistence, so the chime outcome is
            # recorded in each event's own data; then the whole batch
            # lands in the Index before exploitation correlates it.
            for ev in new_events:
                name = products.maybe_flash(self.index, self.watch_id,
                                            ev, self.profile)
                if name:
                    self.flash_count += 1
            for ev in new_events:
                self.index.add_event(ev)
            if self.display is not None:
                # The Console's feed for this capture (0.8.0):
                # each desk's own output lines, then the capture's
                # spectrum frame with the brackets this batch
                # declared. Best-effort, outside the record.
                for desk_name, lines in work_lines.items():
                    for line in lines[-4:]:
                        self.display.work(t1, desk_name, line)
                self._publish_display_frame(item, feeds, new_events,
                                            t1)
        dwell_ev = make_event(
            self.watch_id, "officer", "dwell", t0, t1,
            freq_hz=item["center_hz"], grade="CONFIRMED",
            content="%s: %s" % (item["label"], ", ".join(
                "%s %d" % (k, v) for k, v in sorted(heard.items()))
                or "silence"),
            data={"bill_id": item["id"], "cls": item["cls"],
                  "label": item["label"], "desks": standing,
                  "heard": heard, "bw_hz": item["bw_hz"],
                  "dropped": dropped,
                  "generation": self.generation,
                  "planned_start": planned_start,
                  "planned_end": planned_start + item["dur"]})
        self._file_spectrum(item, feeds, dwell_ev, t0)
        self.index.add_event(dwell_ev)
        for ev in self.exploit.process(self.watch_id, new_events,
                                       pre_counts=pre_counts):
            self._file(ev)
        self._maybe_interrupt(item, new_events, watch_start)

    def _file_spectrum(self, item, feeds, dwell_ev, t0):
        """File this dwell's spectrum product as tier 1 evidence
        (0.10.0): the same display projection the Console's
        waterfall reads - the sweep scan's own power array on a
        sweep item, otherwise the audio census slice's PSD - kept
        in the snippet store under the dwell event as the dwell's
        band state, permanently. It is a few kilobytes by
        construction and files under the item's own grant. A
        capture that produced neither files nothing, as the
        waterfall holds its last frame."""
        import base64 as _b64
        import json as _json
        disp = None
        desk = None
        if item["kind"] == "sweep":
            disp = (feeds.get("sweep") or {}).get("display")
            desk = "sweep"
        if disp is None:
            disp = (feeds.get("audiocensus") or {}).get("display")
            desk = "audiocensus"
        if not disp:
            return
        record = {"desk": desk, "lo_hz": disp["lo_hz"],
                  "hi_hz": disp["hi_hz"],
                  "floor_db": disp.get("floor_db"),
                  "bins": disp["bins"], "t": t0}
        payload = {"filename": "spectrum-%d.json" % int(t0),
                   "kind": "spectrum",
                   "b64": _b64.b64encode(_json.dumps(
                       record, sort_keys=True).encode(
                       "utf-8")).decode("ascii")}
        self._file_snippet(dwell_ev, desk, item["id"], payload)

    def _publish_display_frame(self, item, feeds, events, t1):
        """One capture's spectrum frame for the Console (0.8.0).

        The spectrum is whichever display projection the capture
        already produced: the sweep scan's own power array on a
        sweep item, otherwise the audio census slice's PSD, which
        rides every captured window. A capture that produced
        neither - a satellite pass, whose feed is audio - publishes
        no frame, and the waterfall holds the last true frame with
        its age advancing, which is the design's stated behaviour
        between captures. Brackets are this batch's own events
        whose frequencies fall inside the frame's span: the desks
        declared them by filing them."""
        disp = None
        if item["kind"] == "sweep":
            disp = (feeds.get("sweep") or {}).get("display")
        if disp is None:
            disp = (feeds.get("audiocensus") or {}).get("display")
        if not disp:
            return
        lo_hz, hi_hz = disp["lo_hz"], disp["hi_hz"]
        bin_hz = (hi_hz - lo_hz) / max(1, len(disp["bins"]))
        brackets = []
        for ev in events:
            freq = ev.get("freq_hz")
            if freq is None or not lo_hz <= freq <= hi_hz:
                continue
            bw = (ev.get("data") or {}).get("bw_hz") or bin_hz
            brackets.append({
                "desk": ev.get("desk"), "name": ev.get("emitter"),
                "grade": ev.get("grade"), "freq_hz": freq,
                "lo_hz": freq - bw / 2.0, "hi_hz": freq + bw / 2.0})
        self.display.spectrum({
            "t": t1, "bill_id": item["id"], "label": item["label"],
            "kind": item["kind"], "lo_hz": lo_hz, "hi_hz": hi_hz,
            "floor_db": disp.get("floor_db"), "bins": disp["bins"],
            "brackets": brackets})

    def _adopt_snippet(self, ev, desk, bill_id):
        payload = ev["data"].pop("_snippet", None)
        if payload is None:
            return
        # A desk may file several payloads with one event (a
        # satellite pass files its audio and, when the imagery
        # step decoded it, its image). Payloads are filed in
        # order under the same ceiling accounting, and the
        # event's snippet names the last one filed - the
        # headline product.
        if isinstance(payload, dict):
            payload = [payload]
        for one in payload:
            self._file_snippet(ev, desk, bill_id, one)

    def _file_snippet(self, ev, desk, bill_id, payload):
        ceiling = int(self.profile["watch"]["disk_ceiling_gb"] * 1e9)
        # Every snippet filename carries the watch id and the bill
        # item id: two dwells can never produce the same path, so one
        # event's evidence can never overwrite another's.
        filename = "%s-%s-%s" % (self.watch_id, bill_id,
                                 payload["filename"])
        src = payload.get("file")
        if src is not None:
            # A file payload: the evidence already exists on disk.
            # A full-pass recording is far too large to ride the
            # feed as base64, so it moves by reference and its size
            # is read from the file. The staging file is consumed
            # either way: kept (copied into the snippet store) or
            # refused, it never lingers.
            size = os.path.getsize(src) if os.path.exists(src) else 0
            data = None
        else:
            import base64 as _b64
            data = _b64.b64decode(payload["b64"])
            size = len(data)
        cap = None
        if payload["kind"] == "voice":
            cap = self.profile["snippets"]["voice_cap_per_desk"]
        elif payload["kind"] == "iq":
            cap = self.profile["snippets"]["iq_max_per_watch"]
        try:
            # Reception containment, enforced at filing: a bill
            # item's kept evidence may not pass the grant it was
            # given at muster. A payload that would cross the
            # boundary is refused here and the refusal is
            # logged by the Index with the grant named. The
            # capture side truncates at the same boundary, so this
            # is the backstop, not the mechanism.
            grant = self.grants.get(bill_id) \
                if self.ledger is not None else None
            alloc = grant["bytes"] if grant else None
            if alloc is not None and size > 0 \
                    and self._held.get(bill_id, 0) + size > alloc:
                self.index._record_snippet_refusal(
                    self.watch_id, desk, payload["kind"], filename,
                    size,
                    "exceeds the bill item's reception allocation "
                    "(%d of %d bytes already held for this item)"
                    % (self._held.get(bill_id, 0), alloc),
                    t=ev["t_start"])
                return
            rel, shed = self.index.keep_snippet(
                self.watch_id, self.watch_dir, desk, payload["kind"],
                filename, data, ceiling, count_cap=cap,
                t=ev["t_start"], source_path=src, home_wide=True)
        finally:
            if src is not None and os.path.exists(src):
                try:
                    os.remove(src)
                except OSError:
                    pass
        for victim in shed:
            # Shedding reads the whole home: a victim may be
            # another watch's reception. Its shed posts against
            # the grant it was filed under, naming it; only this
            # watch's own victims move this watch's held ledger.
            vwatch = victim.get("watch_id") or self.watch_id
            vbid = budget.bill_id_of_snippet_path(victim["path"])
            vgrant = None
            if self.ledger is not None:
                vgrant = self.ledger.post_shed(
                    vwatch, vbid, victim["bytes"])
            if vwatch == self.watch_id:
                self._held_shed(victim)
                vtier = retention.tier_of(victim["kind"],
                                          victim["path"])
                self._tier_shed[vtier] = \
                    self._tier_shed.get(vtier, 0) + victim["bytes"]
                self._tier_held[vtier] = max(
                    0, self._tier_held.get(vtier, 0)
                    - victim["bytes"])
            self.index.add_event(make_event(
                self.watch_id, "officer", "system", ev["t_start"],
                ev["t_end"], grade="CONFIRMED",
                content="snippet shed under the disk ceiling: %s"
                        % victim["path"],
                data={"shed": victim["path"],
                      "desk": victim["desk"],
                      "bill_id": vbid,
                      "watch_id": vwatch,
                      "grant_id": vgrant["id"] if vgrant else None,
                      "bytes": victim["bytes"]}))
        if rel:
            ev["snippet"] = rel
            self._held_add(bill_id, desk, size)
            tier = retention.tier_of(payload["kind"], filename)
            self._tier_held[tier] = \
                self._tier_held.get(tier, 0) + size
            if self.ledger is not None:
                grant = self.grants.get(bill_id)
                if grant is not None:
                    self.ledger.consume(grant["id"], size)

    # -------------------------------------------------- transcription
    def _start_transcription(self):
        """Bind the bounded transcription queue when the host
        carries the engine. Absence is a state the products
        state, never an error. Real source only: the simulated
        source's snippets are synthetic audio, and a machine
        transcript of synthetic audio would be a fabrication in
        the products, so simulated watches never transcribe."""
        from .desks import transcription_state
        from .transcribe import TriageQueue, WhisperEngine, Worker
        if self.profile["source"]["kind"] != "real":
            return
        state, _reason = transcription_state()
        if state != "bound":
            return
        self._tx_queue = TriageQueue()

        def sink(job, result, error):
            with self._tx_lock:
                self._tx_results.append((job, result, error))

        self._tx_worker = Worker(self._tx_queue, WhisperEngine(),
                                 sink)
        self._tx_worker.start()

    def _submit_transcription(self, ev):
        if self._tx_queue is None:
            return
        if not ev["data"].pop("transcribe_candidate", False):
            return
        rel = ev.get("snippet")
        if not rel or not rel.endswith(".wav"):
            # The candidate's headline snippet is its voice
            # audio; anything else in that slot (a SigMF excerpt
            # filed where no audio was) is not transcribable.
            return
        self._tx_queue.submit({
            "snippet": os.path.join(self.watch_dir, rel),
            "freq_hz": ev.get("freq_hz"),
            "grade": ev["data"].get("voice_grade"),
            "score": ev["data"].get("voice_score", 0),
            "snr_db": ev["data"].get("snr_db") or 0,
            "duration_s": ev["data"].get("duration_s") or 10.0,
            "t": ev["t_start"], "emitter": ev.get("emitter")})

    def _drain_transcripts(self):
        if self._tx_queue is None:
            return
        with self._tx_lock:
            results, self._tx_results = self._tx_results, []
        for job, result, error in results:
            if error is not None or not result \
                    or not result.get("text"):
                if error is not None:
                    self._tx_errors += 1
                continue
            self._file(make_event(
                self.watch_id, "audiocensus", "decode",
                job["t"], job["t"], freq_hz=job["freq_hz"],
                emitter=job["emitter"], grade="PROBABLE",
                content="machine transcript (faster-whisper, "
                        "unverified; inherits the snippet's "
                        "grade %s): %s"
                        % (job["grade"], result["text"]),
                data={"emitter_kind": "audio_emission",
                      "machine_transcript": True,
                      "transcript": result["text"],
                      "language": result.get("language"),
                      "voice_grade": job["grade"]}))

    def _stop_transcription(self, quiet=False):
        if self._tx_worker is not None:
            self._tx_worker.stop()
            self._tx_worker = None
        self._drain_transcripts()
        if self._tx_queue is not None and not quiet:
            counts = dict(self._tx_queue.counts)
            counts["budget_spent_s"] = round(
                self._tx_queue.budget_spent_s)
            counts["engine_errors"] = self._tx_errors
            counts["pending_at_stand_down"] = self._tx_queue.pending()
            self._file(make_event(
                self.watch_id, "officer", "system",
                self.meta["end"], self.meta["end"],
                grade="CONFIRMED",
                content="transcription accounting: %d submitted, "
                        "%d transcribed, %d dropped at the gate, "
                        "%d dropped by budget, %d dropped by "
                        "queue, %d pending at stand-down"
                        % (counts["submitted"], counts["transcribed"],
                           counts["dropped_gate"],
                           counts["dropped_budget"],
                           counts["dropped_queue"],
                           counts["pending_at_stand_down"]),
                data={"transcription": counts}))
        if quiet:
            # A handover is not a stand-down: the queues stop
            # and drain, but no accounting event files - the
            # successor's stand-down files the watch's account.
            self._tx_queue = None

    def _maybe_interrupt(self, item, new_events, watch_start):
        if item["cls"] not in ("B", "C"):
            return
        cap = self.profile["advanced"]["interrupt_cap_per_hour"]
        for ev in new_events:
            if ev["kind"] != "observation":
                continue
            strong_unknown = ev["data"].get("interrupt_candidate") \
                and ev["data"].get("unknown_shape")
            if not strong_unknown:
                continue
            hour = int((ev["t_start"] - watch_start) // 3600)
            if self.interrupts_used.get(hour, 0) >= cap:
                return
            self.interrupts_used[hour] = \
                self.interrupts_used.get(hour, 0) + 1
            bw = min(2.4e6, max(400e3, (ev["data"].get("bw_hz") or 25e3)
                                * 4))
            interrupt = {"id": "int-%03d" % sum(
                self.interrupts_used.values()),
                "cls": "I", "kind": "interrupt",
                "label": "Interrupt on %s" % util.fmt_hz(ev["freq_hz"]),
                "start": ev["t_start"], "dur": 240,
                "center_hz": ev["freq_hz"], "bw_hz": bw,
                "desks": ["sweep"], "pass": None}
            if self.ledger is not None:
                # An interrupt is an unplanned reception: it
                # takes a mid-watch grant from the standing pool
                # like any other, shrunk or refused with the
                # reason filed when the pool cannot carry it.
                grant, statement = budget.request_item_grant(
                    self.ledger, self.profile, self.watch_id,
                    interrupt, ev["t_start"])
                if grant is None:
                    self._file(make_event(
                        self.watch_id, "officer", "system",
                        ev["t_start"], ev["t_start"], grade="CONFIRMED",
                        content="interrupt not flown: %s" % statement,
                        data={"reception_grant_refused": statement,
                              "bill_id": interrupt["id"]}))
                    return
                self.grants[interrupt["id"]] = grant
                if statement:
                    self._file(make_event(
                        self.watch_id, "officer", "system",
                        ev["t_start"], ev["t_start"], grade="CONFIRMED",
                        content="reception allocation: %s" % statement,
                        data={"reception_grant_shrunk": statement,
                              "bill_id": interrupt["id"]}))
            self._execute(interrupt, watch_start)
            self._close_item_grant(interrupt,
                                   ev["t_start"] + interrupt["dur"])
            return

    # -------------------------------------------------- live updates
    def _drain_update_events(self, t):
        """File the update stages the check worker queued in
        the Index. The worker never touches the Index itself -
        one writer, the officer, as everywhere else."""
        if self.generation is None:
            return
        try:
            with updater.CheckLock(self.home):
                state = updater.UpdateState.load(self.home)
                pending = state.drain_events()
                if pending:
                    state.save()
        except updater.UpdateError:
            return  # a check holds the lock; drain next boundary
        for ev in pending:
            self._file(make_event(
                self.watch_id, ev["desk"], ev["kind"], ev["t"],
                ev["t"], grade="CONFIRMED", content=ev["content"],
                data=ev["data"]))

    def _write_probation(self):
        util.write_json(os.path.join(
            self.home, updater.PROBATION_FILENAME), self._probation)

    def _begin_probation(self, t):
        required = int(self.profile["updates"]["probation_items"])
        self._probation = {
            "watch_id": self.watch_id, "generation": self.generation,
            "required": required, "done": 0, "status": "active",
            "reason": None, "started_at": time.time()}
        self._write_probation()
        return required

    def _begin_probation_if_marked(self, t):
        """A generation the keeper flipped to while no watch
        stood begins its probation on the next watch it flies."""
        if self.generation is None:
            return
        try:
            pointer = updater.load_pointer(self.home)
        except updater.UpdateError:
            return
        if not pointer:
            return
        current = pointer.get("current") or {}
        if current.get("hash") == self.generation \
                and current.get("status") == "probationary":
            required = self._begin_probation(t)
            self._file(make_event(
                self.watch_id, "updater", "update", t, t,
                grade="CONFIRMED",
                content="generation flipped: generation %s "
                        "stands watch %s under probation of %d "
                        "ordinary items; the keeper marks it "
                        "successful only when probation passes"
                        % (self.generation, self.watch_id,
                           required),
                data={"stage": "flipped",
                      "generation": self.generation,
                      "probation_items": required}))

    def _adopt_handover(self, t, gate_list):
        """File the landing side of a handover or a rollback on
        the resume that adopts the watch: the flipped event and
        the start of probation for an update, the rolled-back
        event for either kind of return to a known-good
        generation."""
        if self.generation is None:
            return
        tuner_detail = next(
            (d for n, _ok, d in gate_list if n == "tuner"), "")
        handover = util.read_json(os.path.join(
            self.home, updater.HANDOVER_FILENAME), default=None)
        if handover and handover.get("watch_id") == self.watch_id \
                and handover.get("to_generation") \
                == self.generation:
            try:
                os.remove(os.path.join(
                    self.home, updater.HANDOVER_FILENAME))
            except OSError:
                pass
            updater.clear_activation(self.home)
            updater.clear_rollback_request(self.home)
            if handover.get("kind") == "rollback":
                self._file(make_event(
                    self.watch_id, "updater", "update", t, t,
                    grade="CONFIRMED",
                    content="update rolled back by the "
                            "operator: watch %s returns to "
                            "generation %s from generation %s "
                            "at a dwell boundary; no probation "
                            "applies to a known-good generation"
                            % (self.watch_id, self.generation,
                               handover.get("from_generation")),
                    data={"stage": "rolled_back",
                          "generation": self.generation,
                          "from_generation":
                              handover.get("from_generation"),
                          "snapshot": handover.get("snapshot")}))
            else:
                required = self._begin_probation(t)
                self._file(make_event(
                    self.watch_id, "updater", "update", t, t,
                    grade="CONFIRMED",
                    content="generation flipped: generation %s "
                            "took the tuner for watch %s from "
                            "generation %s; the watch was "
                            "adopted from the record at bill "
                            "item %d, the tuner self-check "
                            "passed (%s), and probation of %d "
                            "ordinary items begins"
                            % (self.generation, self.watch_id,
                               handover.get("from_generation"),
                               handover.get("next_index", 0),
                               tuner_detail, required),
                    data={"stage": "flipped",
                          "generation": self.generation,
                          "from_generation":
                              handover.get("from_generation"),
                          "probation_items": required,
                          "snapshot": handover.get("snapshot")}))
            return
        note = util.read_json(os.path.join(
            self.home, updater.ROLLBACK_NOTE_FILENAME),
            default=None)
        if note and note.get("watch_id") == self.watch_id \
                and note.get("to_generation") == self.generation:
            try:
                os.remove(os.path.join(
                    self.home, updater.ROLLBACK_NOTE_FILENAME))
            except OSError:
                pass
            self._file(make_event(
                self.watch_id, "updater", "update", t, t,
                grade="CONFIRMED",
                content="update rolled back: generation %s "
                        "failed probation on watch %s (%s); "
                        "the pre-handover snapshot was "
                        "restored and generation %s resumes "
                        "the watch from it"
                        % (note.get("from_generation"),
                           self.watch_id, note.get("reason"),
                           self.generation),
                data={"stage": "rolled_back",
                      "generation": self.generation,
                      "from_generation":
                          note.get("from_generation"),
                      "reason": note.get("reason"),
                      "snapshot": note.get("snapshot")}))
            if note.get("degraded"):
                self._file(make_event(
                    self.watch_id, "updater", "update", t, t,
                    grade="CONFIRMED",
                    content="update posture degraded: %d "
                            "failed activations in 24 hours "
                            "reach the intensity limit; "
                            "automatic activation degrades to "
                            "notify-only until an operator "
                            "resets it with: multidex update "
                            "reset" % updater.INTENSITY_LIMIT,
                    data={"stage": "posture_degraded"}))
            return
        probation = util.read_json(os.path.join(
            self.home, updater.PROBATION_FILENAME), default=None)
        if probation and probation.get("status") == "active" \
                and probation.get("watch_id") == self.watch_id \
                and probation.get("generation") == self.generation:
            self._probation = probation

    def _probation_after_item(self, item, t):
        """Probation accounting for one finished item. Passes
        do not count - probation is proven on ordinary work.
        A gap the item filed with a probation cause fails it;
        the keeper reads the failure off the probation file
        and rolls back."""
        if not self._probation \
                or self._probation.get("status") != "active":
            return
        if item.get("kind") == "pass":
            return
        gaps = [ev for ev in self.index.events(
            watch_id=self.watch_id, kinds=["gap"], limit=100000)
            if ev["data"].get("bill_id") == item["id"]
            and ev["data"].get("cause")
            in updater.PROBATION_FAIL_CAUSES]
        if gaps:
            self._probation["status"] = "failed"
            self._probation["reason"] = gaps[-1]["content"]
            self._write_probation()
            self._file(make_event(
                self.watch_id, "updater", "update", t, t,
                grade="CONFIRMED",
                content="probation failed: generation %s on "
                        "watch %s: %s; the keeper rolls back "
                        "at the next boundary"
                        % (self.generation, self.watch_id,
                           gaps[-1]["content"]),
                data={"stage": "probation_failed",
                      "generation": self.generation,
                      "bill_id": item["id"],
                      "reason": gaps[-1]["content"]}))
            return
        self._probation["done"] += 1
        if self._probation["done"] >= self._probation["required"]:
            self._probation["status"] = "passed"
            self._write_probation()
            self._file(make_event(
                self.watch_id, "updater", "update", t, t,
                grade="CONFIRMED",
                content="probation passed: generation %s "
                        "completed %d ordinary items of watch "
                        "%s with the event log advancing and "
                        "the tuner working; the keeper marks "
                        "the generation successful"
                        % (self.generation,
                           self._probation["required"],
                           self.watch_id),
                data={"stage": "probation_passed",
                      "generation": self.generation,
                      "items": self._probation["required"]}))
        else:
            self._write_probation()

    def _after_item(self, bill, next_idx, item, position):
        """The dwell-boundary hook: probation first, then the
        update boundary - due checks spawn, queued stages
        file, and a written activation or rollback request is
        considered for handover."""
        self._probation_after_item(item, position)
        if next_idx < len(bill):
            self._update_boundary(bill, next_idx, position)

    def _profile_path_for_worker(self):
        env = os.environ.get("MULTIDEX_PROFILE_PATH")
        if env and os.path.exists(env):
            return env
        directory = self.profile.get("_dir")
        if directory:
            candidate = os.path.join(
                directory, self.profile["name"] + ".toml")
            if os.path.exists(candidate):
                return candidate
        candidate = os.path.join(self._gen_root, "profiles",
                                 "default.toml")
        if os.path.exists(candidate):
            return candidate
        return None

    def _spawn_update_check(self):
        profile_path = self._profile_path_for_worker()
        if not profile_path:
            return
        env = dict(os.environ)
        env["PYTHONPATH"] = self._gen_root + os.pathsep \
            + env.get("PYTHONPATH", "")
        try:
            log = open(os.path.join(self.home,
                                    "update-check.log"), "a")
        except OSError:
            return
        import sys as _sys
        try:
            subprocess.Popen(
                [_sys.executable, "-m", "multidex.updater",
                 "check-worker", self.home, profile_path],
                cwd=self._gen_root, env=env, stdout=log,
                stderr=log, start_new_session=True)
        except OSError:
            pass

    def _update_boundary(self, bill, next_idx, position):
        if self.generation is None:
            return
        self._drain_update_events(position)
        root = updater.install_root()
        if root is None:
            return
        upd = self.profile["updates"]
        state = updater.UpdateState.load(self.home)
        last = state.data.get("last_check") or 0
        if time.time() - last >= upd["check_interval_hours"] \
                * 3600:
            self._spawn_update_check()
        intent = None
        rollback = updater.read_rollback_request(self.home)
        if rollback:
            intent = {"to_generation": rollback["to_hash"],
                      "kind": "rollback",
                      "reason": rollback.get("reason")}
        else:
            activation = updater.read_activation(self.home)
            if activation:
                intent = {"to_generation": activation["hash"],
                          "kind": "update",
                          "counter": activation.get("counter")}
        if intent is None:
            return
        target = intent["to_generation"]
        if target == self.generation:
            updater.clear_activation(self.home)
            updater.clear_rollback_request(self.home)
            return
        if not os.path.isdir(updater.generation_dir(root, target)):
            return  # a stale request for a tree that is not here
        if intent["kind"] == "update":
            cand = state.data.get("candidate") or {}
            if cand.get("hash") != target \
                    or cand.get("status") != "staged" \
                    or target in state.data["bad"]:
                return  # approval no longer matches a staged,
                        # gate-passed, unbad candidate
        # The pass guard: no handover or rollback flies when
        # the next item is a satellite pass, or with a pass
        # inside the guard window. Deferral is silent and
        # retried at the next boundary - the intent stays
        # written.
        guard_s = upd["pass_guard_minutes"] * 60
        for future in bill[next_idx:]:
            if future.get("kind") == "pass":
                if future is bill[next_idx] \
                        or future["start"] - position <= guard_s:
                    return
                break
        if not os.environ.get("MULTIDEX_KEEPER"):
            if self._keeperless_notice_for != target:
                self._keeperless_notice_for = target
                self._file(make_event(
                    self.watch_id, "updater", "update", position,
                    position, grade="CONFIRMED",
                    content="update activation is written for "
                            "generation %s, but no keeper "
                            "supervises this watch; the "
                            "handover waits until the watch "
                            "is stood under the keeper" % target,
                    data={"stage": "handover_waiting",
                          "generation": target}))
            return
        snapshot_id = "snap-%s-%d" % (self.watch_id,
                                      int(time.time()))
        updater.take_snapshot(self.home, snapshot_id)
        if intent["kind"] == "update":
            what = "release %s" % intent.get("counter")
        else:
            what = "operator rollback"
        self._file(make_event(
            self.watch_id, "updater", "update", position,
            position, grade="CONFIRMED",
            content="update handover: watch %s hands over at a "
                    "dwell boundary from generation %s to "
                    "generation %s (%s); state is "
                    "checkpointed, the tuner is closed, and "
                    "the keeper starts the successor, which "
                    "adopts the watch from the record"
                    % (self.watch_id, self.generation, target,
                       what),
            data={"stage": "handover",
                  "from_generation": self.generation,
                  "to_generation": target,
                  "kind": intent["kind"],
                  "snapshot": snapshot_id}))
        util.write_json(os.path.join(
            self.home, updater.HANDOVER_FILENAME), {
                "watch_id": self.watch_id,
                "from_generation": self.generation,
                "to_generation": target,
                "kind": intent["kind"],
                "counter": intent.get("counter"),
                "snapshot": snapshot_id,
                "next_index": next_idx,
                "at": time.time()})
        state_now = util.read_json(os.path.join(
            self.watch_dir, "state.json"), default={}) or {}
        state_now["handed_over_to"] = target
        util.write_json(os.path.join(self.watch_dir,
                                     "state.json"), state_now)
        # The desk workers are idle at a boundary by
        # construction - supervisor.run is synchronous, so no
        # job is in flight here - and shutdown reaps them.
        # Transcription and LRPT workers drain their finished
        # work and stop without filing a stand-down account.
        self._stop_transcription(quiet=True)
        self.supervisor.shutdown()
        self._handed_over = True
        self._handover_info = intent

    # ----------------------------------------------------------- stand down
    def _stand_down(self, bill, watch_start, end_reason):
        self._stood_down = True
        end = watch_start + self.profile["watch"]["hours"] * 3600
        if end_reason:
            self.meta["ended_early"] = True
            self.meta["end_reason"] = end_reason
            end = watch_start + self._elapsed(bill)
        self.meta["end"] = end
        self._stop_transcription()
        # Stand-down releases every grant this watch still
        # holds: unspent room returns to the standing pool the
        # moment the watch ends, however it ends, and the filed
        # receptions stay accounted as consumed in the ledger's
        # history. The release lands before the Watch Report
        # renders, so the report's reception account is final.
        if self.ledger is not None:
            self.ledger.release_watch(
                self.watch_id, end,
                "watch ended early: %s" % end_reason if end_reason
                else "watch stood down",
                status="cancelled" if end_reason == "operator stop"
                else "closed")
        self.index.set_watch_meta(self.watch_id, self.meta)
        util.write_json(os.path.join(self.watch_dir, "watch.json"),
                        self.meta)
        for ev in self.exploit.check_missing_regulars(
                self.watch_id, end, watch_start):
            self._file(ev)
        self.exploit.build_baselines(self.watch_id)
        text = products.render_report(self.index, self.watch_id)
        util.write_text(os.path.join(self.watch_dir, "REPORT.md"), text)
        self._file(make_event(
            self.watch_id, "officer", "system", end, end,
            grade="CONFIRMED",
            content="watch stood down%s; Watch Report rendered"
                    % (" early: " + end_reason if end_reason else ""),
            data={"ended_early": bool(end_reason)}))
        # The final state save lands after the stand-down event, so
        # the counts in state.json equal the log exactly at
        # completion - the stand-down event itself included.
        tuner = getattr(self, "tuner", None)
        benched = self._bench_set(tuner.source if tuner else None)
        prior = util.read_json(os.path.join(self.watch_dir,
                                            "state.json"),
                               default={}) or {}
        state = {
            "next_index": prior.get("next_index", len(bill)),
            "elapsed_s": end - watch_start,
            "current": "stood down",
            "events": self.index.count_events(watch_id=self.watch_id),
            "dossiers": len(self.index.dossiers_in_watch(self.watch_id)),
            "flash": self.flash_count,
            "benched": sorted(benched),
            "complete": True,
            "pid": os.getpid(),
            "heartbeat_s": end - watch_start,
            "executing": None,
        }
        if self.ledger is not None:
            state["budget"] = self._pool_state()
        util.write_json(os.path.join(self.watch_dir, "state.json"),
                        state)
        util.write_json(os.path.join(self.home, "current_watch"), None)
        self.supervisor.shutdown()

    def _elapsed(self, bill):
        state = util.read_json(os.path.join(self.watch_dir, "state.json"),
                               default={}) or {}
        return state.get("elapsed_s", 0)
