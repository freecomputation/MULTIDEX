"""The Watch Officer: builds the watch bill and stands the watch.

The bill is built in the design's priority order - Class A clock
events own the tuner by the clock, Class B is a weighted standing
rotation re-weighted by propagation, Class C census sweeps fill the
gaps and own the deep-sweep hours, and interrupts preempt B and C
under an hourly cap. Execution is dwell by dwell against watch time;
a time-scale factor accelerates the pacing without changing any logic
path, which is what makes a 24-hour smoke run possible.
"""

import glob
import math
import os
import time

from . import gates, orbit, products, util
from .bands import band_name
from .channelizer import plan as channel_plan
from .desks import SPECS, channels_for, desk_state
from .events import make_event
from .exploit import Exploit
from .index import Index
from .profile import time_scale
from .supervisor import Supervisor
from .tuner import RealSource, TunerLost, TunerServer

# Rotation entry -> (center Hz, bandwidth Hz, desks, hours rule)
ROTATION = {
    "adsb":     (1090e6, 2.4e6, ["adsb"], "all"),
    "airband":  (125.0e6, 2.4e6, ["airband"], "all"),
    "datalink": (131.55e6, 2.4e6, ["acars"], "all"),
    "marine":   (162.3e6, 2.4e6, ["ais", "wxradio"], "all"),
    "aprs":     (144.39e6, 2.4e6, ["aprs"], "all"),
    "ism433":   (433.92e6, 2.4e6, ["ism"], "all"),
    "ism915":   (915e6, 2.4e6, ["ism"], "all"),
    "pager":    (929.6125e6, 2.4e6, ["pager"], "all"),
    "cellular": (752.5e6, 2.4e6, ["cellular"], "all"),
    "trunking": (851.75e6, 2.4e6, ["trunking"], "all"),
    "fmband":   (98e6, 2.4e6, ["broadcast", "sweep"], "all"),
    "hf_day":   (14.15e6, 2.4e6, ["sweep"], "day"),
    "hf_night": (7.1e6, 2.4e6, ["sweep"], "night"),
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
            "desks": ["sweep"] if is_iss else ["satellite"],
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
    last_sweep = [start - 2700]

    def excluded_window(center, bw):
        lo, hi = center - bw / 2, center + bw / 2
        return any(lo < b[1] and b[0] < hi for b in excluded)

    def pick_entry(t):
        hour = _local_hour(t)
        allowed = []
        for name, (center, bw, _d, rule) in ROTATION.items():
            if name not in rotation:
                continue
            if rule == "day" and not 8 <= hour < 20:
                continue
            if rule == "night" and not (hour >= 20 or hour < 8):
                continue
            if excluded_window(center, bw):
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
                    center, bw, desks, _rule = ROTATION[name]
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
        self.interrupts_used = {}
        self.flash_count = 0

    # ------------------------------------------------------------ helpers
    def _source(self, watch_start):
        if self.profile["source"]["kind"] == "real":
            return RealSource(self.profile)
        from .sim import SimSource
        return SimSource(self.profile, watch_start)

    def _desk_benched_reason(self, name, source):
        if name in self.extra_bench:
            return self.extra_bench[name]
        if self.supervisor.is_benched(name):
            return self.supervisor.benched[name]
        capabilities = None
        if source.kind == "real":
            capabilities = source.capabilities()
        state, reason = desk_state(name, self.profile, source.kind,
                                   capabilities)
        return reason if state == "benched" else None

    def _stop_requested(self):
        return os.path.exists(os.path.join(self.watch_dir, "stop.flag"))

    def _file(self, ev):
        self.index.add_event(ev)
        name = products.maybe_flash(self.index, self.watch_id, ev,
                                    self.profile)
        if name:
            self.flash_count += 1
        return ev

    def _pace(self, seconds):
        scale = time_scale(self.profile)
        if scale <= 0:
            return
        deadline = time.time() + seconds / scale
        while time.time() < deadline:
            if self._stop_requested():
                return
            time.sleep(min(0.05, max(0.0, deadline - time.time())))

    # -------------------------------------------------------------- start
    def start(self, start_epoch=None, resume_id=None):
        profile = self.profile
        if resume_id:
            return self._resume(resume_id)
        start = start_epoch or time.time()
        source = self._source(start)
        gate_list = gates.run_gates(profile, self.home, source,
                                    at_epoch=start)
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
        self.exploit = Exploit(self.index, profile)
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
        }
        self.meta = meta
        self.index.set_watch_meta(watch_id, meta)
        util.write_json(os.path.join(self.watch_dir, "watch.json"), meta)
        util.write_json(os.path.join(self.home, "current_watch"), watch_id)
        self.tuner = TunerServer(source, profile)
        self.tuner.begin_watch(start)
        self._file(make_event(
            watch_id, "officer", "system", start, start,
            content="muster passed; watch stood with %d bill items"
                    % len(bill), grade="CONFIRMED",
            data={"gates": [(n, ok, d) for n, ok, d in gate_list]}))
        for name in sorted(SPECS):
            reason = self._desk_benched_reason(name, source)
            if reason:
                self._file(make_event(
                    watch_id, "officer", "desk_health", start, start,
                    content="desk %s benched at muster: %s"
                            % (name, reason), grade="CONFIRMED",
                    data={"desk": name, "benched": True,
                          "reason": reason}))
        self._run_bill(bill, 0, start)
        return {"ok": True, "watch_id": watch_id}

    def _resume(self, watch_id):
        meta = self.index.get_watch_meta(watch_id)
        if meta is None:
            return {"ok": False,
                    "refusal": "no watch %s in the Index" % watch_id}
        self.watch_id = watch_id
        self.watch_dir = self.index.watch_dir(watch_id)
        self.meta = meta
        self.exploit = Exploit(self.index, self.profile)
        bill = util.read_json(os.path.join(self.watch_dir, "bill.json"),
                              default=[])
        state = util.read_json(os.path.join(self.watch_dir, "state.json"),
                               default={}) or {}
        source = self._source(meta["start"])
        self.tuner = TunerServer(source, self.profile)
        self.tuner.begin_watch(meta["start"])
        meta["resumed"] = True
        self.index.set_watch_meta(watch_id, meta)
        util.write_json(os.path.join(self.home, "current_watch"), watch_id)
        self._file(make_event(
            watch_id, "officer", "system", time.time(), time.time(),
            content="watch resumed from on-disk state at bill item %d"
                    % state.get("next_index", 0), grade="CONFIRMED",
            data={"resumed": True}))
        self._run_bill(bill, state.get("next_index", 0), meta["start"])
        return {"ok": True, "watch_id": watch_id}

    # ---------------------------------------------------------------- run
    def _run_bill(self, bill, from_index, watch_start):
        profile = self.profile
        sitrep_every = profile["watch"]["sitrep_hours"] * 3600
        next_sitrep = watch_start + sitrep_every
        sitrep_seq = len(glob.glob(os.path.join(
            self.watch_dir, "SITREP-*.md")))
        idx = from_index
        end_reason = None
        while idx < len(bill):
            item = bill[idx]
            if self._stop_requested():
                end_reason = "operator stop"
                break
            self._execute(item, watch_start)
            self._save_state(idx + 1, item, watch_start)
            self._pace(item["dur"])
            idx += 1
            while item["start"] + item["dur"] >= next_sitrep \
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

    def _save_state(self, next_index, item, watch_start):
        benched = dict(self.extra_bench)
        benched.update(self.supervisor.benched)
        state = {
            "next_index": next_index,
            "elapsed_s": (item["start"] + item["dur"]) - watch_start,
            "current": "%s (%s)" % (item["label"], ", ".join(
                item["desks"]) or "no desks"),
            "events": self.index.count_events(watch_id=self.watch_id),
            "dossiers": len(self.index.dossiers_in_watch(self.watch_id)),
            "flash": self.flash_count,
            "benched": sorted(benched),
            "complete": False,
        }
        util.write_json(os.path.join(self.watch_dir, "state.json"), state)

    def _execute(self, item, watch_start):
        t0, t1 = item["start"], item["start"] + item["dur"]
        source = self.tuner.source
        standing = []
        for name in item["desks"]:
            if self._desk_benched_reason(name, source) is None:
                standing.append(name)
        desk_channels = {name: channels_for(name) for name in standing}
        heavy = {n for n in standing if SPECS[n][2] == "heavy"}
        vfos, dropped = channel_plan(item["center_hz"], item["bw_hz"],
                                     desk_channels, heavy)
        if item["kind"] == "pass" and "satellite" in standing:
            vfos = [{"desk": "satellite", "freq_hz": item["center_hz"],
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
        if feeds is not None:
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
                events, info = self.supervisor.run(name, feed, ctx)
                if info.get("crashes"):
                    self._file(make_event(
                        self.watch_id, "officer", "desk_health", t0, t1,
                        grade="CONFIRMED",
                        content="desk %s crashed and was restarted"
                                % name,
                        data={"desk": name, "crashed": True}))
                if info.get("benched"):
                    self._file(make_event(
                        self.watch_id, "officer", "desk_health", t0, t1,
                        grade="CONFIRMED",
                        content="desk %s benched for the watch: %s"
                                % (name, info["reason"]),
                        data={"desk": name, "benched": True,
                              "reason": info["reason"]}))
                for ev in events:
                    self._adopt_snippet(ev, name)
                    self.index.add_event(ev)
                    new_events.append(ev)
                heard[name] = len(events)
        self.index.add_event(make_event(
            self.watch_id, "officer", "dwell", t0, t1,
            freq_hz=item["center_hz"], grade="CONFIRMED",
            content="%s: %s" % (item["label"], ", ".join(
                "%s %d" % (k, v) for k, v in sorted(heard.items()))
                or "silence"),
            data={"bill_id": item["id"], "cls": item["cls"],
                  "label": item["label"], "desks": standing,
                  "heard": heard, "bw_hz": item["bw_hz"],
                  "dropped": dropped}))
        for ev in self.exploit.process(self.watch_id, new_events):
            self._file(ev)
        for ev in new_events:
            name = products.maybe_flash(self.index, self.watch_id, ev,
                                        self.profile)
            if name:
                self.flash_count += 1
        self._maybe_interrupt(item, new_events, watch_start)

    def _adopt_snippet(self, ev, desk):
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
        rel, shed = self.index.keep_snippet(
            self.watch_id, self.watch_dir, desk, payload["kind"],
            payload["filename"], data, ceiling, count_cap=cap)
        for victim in shed:
            self.index.add_event(make_event(
                self.watch_id, "officer", "system", ev["t_start"],
                ev["t_end"], grade="CONFIRMED",
                content="snippet shed under the disk ceiling: %s"
                        % victim["path"],
                data={"shed": victim["path"]}))
        if rel:
            ev["snippet"] = rel

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
        end = watch_start + self.profile["watch"]["hours"] * 3600
        if end_reason:
            self.meta["ended_early"] = True
            self.meta["end_reason"] = end_reason
            end = watch_start + self._elapsed(bill)
        self.meta["end"] = end
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
        state = util.read_json(os.path.join(self.watch_dir, "state.json"),
                               default={}) or {}
        state["complete"] = True
        state["current"] = "stood down"
        util.write_json(os.path.join(self.watch_dir, "state.json"), state)
        util.write_json(os.path.join(self.home, "current_watch"), None)
        self.supervisor.shutdown()

    def _elapsed(self, bill):
        state = util.read_json(os.path.join(self.watch_dir, "state.json"),
                               default={}) or {}
        return state.get("elapsed_s", 0)
