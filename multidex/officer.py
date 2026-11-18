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
import threading
import time

from . import gates, orbit, products, util
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
    "hf_day":   (14.15e6, 2.4e6, ["sweep", "amateur"], "day"),
    "hf_night": (7.1e6, 2.4e6, ["sweep", "amateur"], "night"),
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

    def _file(self, ev):
        # FLASH first: the chime outcome is recorded in the event's
        # own data, so the event must not be persisted before
        # maybe_flash has had its say.
        name = products.maybe_flash(self.index, self.watch_id, ev,
                                    self.profile)
        if name:
            self.flash_count += 1
        self.index.add_event(ev)
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
        self.exploit = Exploit(self.index, profile)
        self._start_transcription()
        bill = build_bill(profile, start, sats)
        util.write_json(os.path.join(self.watch_dir, "bill.json"), bill)
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
            "source_note": source_note,
            "time_scale": self._scale,
        }
        self.meta = meta
        self.index.set_watch_meta(watch_id, meta)
        util.write_json(os.path.join(self.watch_dir, "watch.json"), meta)
        util.write_json(os.path.join(self.home, "current_watch"), watch_id)
        self.tuner = TunerServer(source, profile)
        self.tuner.begin_watch(start)
        muster_data = {"gates": [(n, ok, d) for n, ok, d in gate_list],
                       "time_scale": self._scale}
        if source_note:
            muster_data["source_note"] = source_note
            print("Source note: %s" % source_note)
        self._file(make_event(
            watch_id, "officer", "system", start, start,
            content="muster passed; watch stood with %d bill items"
                    % len(bill), grade="CONFIRMED",
            data=muster_data))
        for name in sorted(SPECS):
            reason = self._desk_benched_reason(name, source)
            if reason:
                self._file(make_event(
                    watch_id, "officer", "desk_health", start, start,
                    content="desk %s benched at muster: %s"
                            % (name, reason), grade="CONFIRMED",
                    data={"desk": name, "benched": True,
                          "reason": reason}))
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
        self.meta = meta
        self.exploit = Exploit(self.index, profile)
        bill = util.read_json(os.path.join(self.watch_dir, "bill.json"),
                              default=[])
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
            data={"resumed": True}))
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
        return {"ok": True, "watch_id": watch_id}

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
        }
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
            vfos = [{"desk": pdesk, "freq_hz": item["center_hz"],
                     "bw_hz": 150e3, "pass": item.get("pass")}]
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
        self.index.add_event(make_event(
            self.watch_id, "officer", "dwell", t0, t1,
            freq_hz=item["center_hz"], grade="CONFIRMED",
            content="%s: %s" % (item["label"], ", ".join(
                "%s %d" % (k, v) for k, v in sorted(heard.items()))
                or "silence"),
            data={"bill_id": item["id"], "cls": item["cls"],
                  "label": item["label"], "desks": standing,
                  "heard": heard, "bw_hz": item["bw_hz"],
                  "dropped": dropped,
                  "planned_start": planned_start,
                  "planned_end": planned_start + item["dur"]}))
        for ev in self.exploit.process(self.watch_id, new_events,
                                       pre_counts=pre_counts):
            self._file(ev)
        self._maybe_interrupt(item, new_events, watch_start)

    def _adopt_snippet(self, ev, desk, bill_id):
        payload = ev["data"].pop("_snippet", None)
        if payload is None:
            return
        import base64 as _b64
        data = _b64.b64decode(payload["b64"])
        cap = None
        if payload["kind"] == "voice":
            cap = self.profile["snippets"]["voice_cap_per_desk"]
        elif payload["kind"] == "iq":
            cap = self.profile["snippets"]["iq_max_per_watch"]
        ceiling = int(self.profile["watch"]["disk_ceiling_gb"] * 1e9)
        # Every snippet filename carries the watch id and the bill
        # item id: two dwells can never produce the same path, so one
        # event's evidence can never overwrite another's.
        filename = "%s-%s-%s" % (self.watch_id, bill_id,
                                 payload["filename"])
        rel, shed = self.index.keep_snippet(
            self.watch_id, self.watch_dir, desk, payload["kind"],
            filename, data, ceiling, count_cap=cap, t=ev["t_start"])
        for victim in shed:
            self.index.add_event(make_event(
                self.watch_id, "officer", "system", ev["t_start"],
                ev["t_end"], grade="CONFIRMED",
                content="snippet shed under the disk ceiling: %s"
                        % victim["path"],
                data={"shed": victim["path"]}))
        if rel:
            ev["snippet"] = rel

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
        if not rel:
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

    def _stop_transcription(self):
        if self._tx_worker is not None:
            self._tx_worker.stop()
            self._tx_worker = None
        self._drain_transcripts()
        if self._tx_queue is not None:
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
            self._execute(interrupt, watch_start)
            return

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
        util.write_json(os.path.join(self.watch_dir, "state.json"),
                        state)
        util.write_json(os.path.join(self.home, "current_watch"), None)
        self.supervisor.shutdown()

    def _elapsed(self, bill):
        state = util.read_json(os.path.join(self.watch_dir, "state.json"),
                               default={}) or {}
        return state.get("elapsed_s", 0)
