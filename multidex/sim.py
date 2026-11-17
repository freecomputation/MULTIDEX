"""The simulated source: a deterministic synthetic spectrum and traffic
model, so the whole system runs with no hardware.

Determinism is a design point, not a convenience: the same seed and
the same watch start produce the same spectrum, so a watch can be
rehearsed and a failure replayed. Frame-level protocols are genuine
(Mode S frames with real CRC, AIS sentences with real checksums,
POCSAG batches with real BCH); text protocols are emitted at the
demodulated layer a real decoder would print.

Everything here is synthetic. A simulated watch says so in its
metadata and in every product it renders.
"""

import math
import random

from . import codecs, modes, orbit
from .desks import AIRBAND_CHANNELS

PAGER_FREQ = 929.6125e6
WX_FREQ = 162.475e6
APRS_FREQ = 144.390e6
TRUNK_CC = 851.0125e6

_FLIGHTS = [("DL", "Delta"), ("UA", "United"), ("AA", "American"),
            ("WN", "Southwest"), ("FDX", "FedEx"), ("BAW", "Speedbird")]
_ACARS_TEXTS = [
    "OOOI REPORT FOLLOWS", "ETA UPDATE {eta}", "FUEL ON BOARD {fuel}",
    "GATE ASSIGNMENT REQUEST", "CREW MEAL COUNT 4",
    "MAINT CHECK DUE AT DEST", "WX UPDATE REQUEST",
    "POSITION REPORT {lat} {lon}",
]
_PAGER_TEXTS = [
    "CALL DISPATCH ON ARRIVAL", "DOOR ALARM ZONE 4 CHECKED OK",
    "MEETING MOVED TO 1500", "PUMP 2 PRESSURE NORMAL",
    "CALLBACK REQUESTED FROM OPS", "SHIFT SWAP CONFIRMED",
    "VEHICLE 12 FUEL LOW", "STANDBY FOR WEATHER UPDATE",
]
_UNKNOWN_SHAPES = ["unknown-chirp", "unknown-pulsed",
                   "unknown-wideband", "unknown-multicarrier"]

# Fixed ambient occupants for the sweep model: (freq Hz, peak dBm,
# shape, label). Shortwave broadcast, a 6 m beacon, a 2 m repeater,
# military UHF, and an L-band radar - the furniture of real windows.
_AMBIENT = [
    (7.410e6, -70, "am-broadcast", "shortwave broadcast"),
    (9.420e6, -74, "am-broadcast", "shortwave broadcast"),
    (11.720e6, -72, "am-broadcast", "shortwave broadcast"),
    (13.845e6, -76, "am-broadcast", "shortwave broadcast"),
    (15.560e6, -71, "am-broadcast", "shortwave broadcast"),
    (50.310e6, -88, "beacon", "6 m beacon"),
    (146.520e6, -78, "nfm-voice", "2 m repeater"),
    (255.550e6, -80, "nfm-voice", "UHF military"),
    (1290.0e6, -66, "pulsed-radar", "L-band radar"),
]

# Intermittent known-shape data bursts: (freq Hz, period s, phase s).
_AMBIENT_BURSTS = [
    (7.070e6, 420, 35),
    (14.070e6, 380, 120),
    (28.070e6, 460, 200),
    (50.620e6, 540, 60),
]


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _rng(*parts):
    """Deterministic RNG from integer parts (tuple seeds are not
    accepted by random.Random on Python 3.11+). String parts are
    folded by ordinal so ICAO-style keys work too."""
    h = 1469598103934665603
    for p in parts:
        if isinstance(p, str):
            for ch in p:
                h = ((h ^ ord(ch)) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        else:
            v = int(p) & 0xFFFFFFFFFFFFFFFF
            h = ((h ^ v) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
            h ^= h >> 29
    return random.Random(h)


class SimWorld:
    def __init__(self, seed, home_lat, home_lon, start_epoch, profile):
        self.seed = seed
        self.home = (home_lat, home_lon)
        self.start = start_epoch
        rng = random.Random(seed)
        self.aircraft = self._make_aircraft(rng)
        self.vessels = self._make_vessels(rng)
        self.devices = self._make_devices(rng)
        self.aprs_stations = self._make_aprs(rng)
        self.pagers = [1000000 + rng.randrange(900000) for _ in range(12)]
        self.cells = [
            {"cell_id": "LTE-310410-118201", "band": "LTE B12",
             "freq_hz": 751.5e6},
            {"cell_id": "LTE-310410-118202", "band": "LTE B12",
             "freq_hz": 753.5e6},
            {"cell_id": "GSM-310260-4417", "band": "GSM 900",
             "freq_hz": 942.5e6},
        ]
        self.trunk_systems = [
            {"sysid": "3A1", "freq_hz": TRUNK_CC, "sites": 2},
            {"sysid": "5C7", "freq_hz": 852.5125e6, "sites": 1},
        ]
        self.fm_stations = [
            (89.1e6, "KPRX"), (92.9e6, "KQMO"), (98.5e6, "KFDI"),
            (101.3e6, "KHUT"), (104.5e6, "KZCH"), (107.3e6, "KTHR"),
        ]
        # Scripted moments, as offsets from watch start.
        self.guard_tx_offset = 3 * 3600 + rng.randrange(2400)
        self.same_alerts = [
            (1.6 * 3600 + rng.randrange(1200), "SVR", ["020091", "020197"]),
            (8.9 * 3600 + rng.randrange(1200), "TOR", ["020091"]),
        ]
        self.unknowns = []
        for i, (off, freq) in enumerate([
                (5.2 * 3600, 462.5125e6), (11.4 * 3600, 255.4e6),
                (16.8 * 3600, 14.230e6), (20.1 * 3600, 902.4e6),
                (7.7 * 3600, 462.5125e6)]):
            self.unknowns.append({
                "t": start_epoch + off + rng.randrange(900),
                "dur": 600 + rng.randrange(900), "freq_hz": freq,
                "bw_hz": [25e3, 200e3, 1.2e6, 50e3, 25e3][i],
                "peak_dbm": -58 - rng.randrange(14),
                "shape": _UNKNOWN_SHAPES[i % len(_UNKNOWN_SHAPES)],
                # Unexplained emitters of the kind a census actually
                # finds - telemetry, beacons, spurs - recur rather
                # than sounding once. Each repeats on its own period.
                "period": 9000 + rng.randrange(3600),
            })
        self.sonde_sites = profile["sondes"]["sites"]
        self.synoptic_hours = profile["sondes"]["synoptic_hours"]
        tle_path = None
        adv = profile["advanced"]
        if profile.get("_dir"):
            import os
            tle_path = os.path.join(profile["_dir"], adv["tle_file"])
        self.sats = []
        if tle_path:
            try:
                with open(tle_path) as fh:
                    self.sats = orbit.parse_tle(fh.read())
            except OSError:
                self.sats = []

    # ---------------------------------------------------------- builders
    def _make_aircraft(self, rng):
        out = []
        for i in range(12):
            code, _name = _FLIGHTS[i % len(_FLIGHTS)]
            flight = "%s%d" % (code, 100 + rng.randrange(8800))
            tail = "N%d%s" % (100 + rng.randrange(899), code)
            icao = "%06X" % rng.randrange(0xA00000, 0xFFFFFF)
            bearing = rng.uniform(0, 2 * math.pi)
            dist = rng.uniform(120, 750)
            sx = dist * math.sin(bearing)
            sy = dist * math.cos(bearing)
            heading = bearing + math.pi + rng.uniform(-0.4, 0.4)
            speed_kmh = rng.uniform(700, 900)
            vx = speed_kmh * math.sin(heading)
            vy = speed_kmh * math.cos(heading)
            # Some aircraft are already airborne when the watch stands,
            # as they would be over any real home position; the rest
            # depart across the day.
            dep = rng.uniform(-2700, 18 * 3600)
            dur = dist * 2 / speed_kmh * 3600
            out.append({"icao": icao, "tail": tail, "flight": flight,
                        "sx": sx, "sy": sy, "vx": vx, "vy": vy,
                        "alt_ft": rng.randrange(28000, 43001, 1000),
                        "dep": dep, "dur": dur})
        return out

    def _aircraft_pos(self, ac, t):
        off = t - self.start - ac["dep"]
        if off < 0 or off > ac["dur"]:
            return None
        x = ac["sx"] + ac["vx"] * off / 3600.0
        y = ac["sy"] + ac["vy"] * off / 3600.0
        lat = self.home[0] + y / 111.32
        lon = self.home[1] + x / (111.32 * math.cos(math.radians(self.home[0])))
        dist = math.hypot(x, y)
        heading = math.degrees(math.atan2(ac["vx"], ac["vy"])) % 360
        speed_kt = math.hypot(ac["vx"], ac["vy"]) / 1.852
        return lat, lon, dist, heading, speed_kt

    def _make_vessels(self, rng):
        out = []
        names = [("MV NORTHFORK", "WDA2211"), ("MV OSAGE", "WDB8842"),
                 ("R/V SURVEYOR", "WDC3307"), ("MV KAW", "WDD5520")]
        for i, (name, callsign) in enumerate(names):
            out.append({
                "mmsi": str(366000000 + rng.randrange(899999)),
                "name": name, "callsign": callsign,
                "x0": -60 + i * 35.0, "y0": -25 + i * 9.0,
                "vx": rng.uniform(6, 14), "vy": rng.uniform(-2, 2),
                "sog": rng.uniform(5, 11)})
        return out

    def _vessel_pos(self, v, t):
        off = (t - self.start) / 3600.0
        x = v["x0"] + v["vx"] * off * 0.15
        y = v["y0"] + v["vy"] * off * 0.15
        lat = self.home[0] + y / 111.32
        lon = self.home[1] + x / (111.32 * math.cos(math.radians(self.home[0])))
        return lat, lon

    def _make_devices(self, rng):
        specs = [("Acurite 5n1", 433.92e6, "weather"),
                 ("AmbientWeather F007TH", 915e6, "weather"),
                 ("LaCrosse TX141", 433.92e6, "weather"),
                 ("Landis+Gyr Gridstream", 915e6, "meter"),
                 ("Neptune R900", 896e6, "meter"),
                 ("Schrader TPMS", 315e6, "tpms"),
                 ("Oregon Scientific THGR", 433.92e6, "weather"),
                 ("Ecowitt WH51", 915e6, "soil"),
                 ("Honeywell Door Sensor", 345e6, "contact"),
                 ("AcuRite Lightning", 433.92e6, "weather")]
        out = []
        for i, (model, freq, kind) in enumerate(specs):
            out.append({"id": "%s-%04d" % (model.split()[0], 1000 + i * 137),
                        "model": model, "freq_hz": freq, "kind": kind,
                        "period_s": rng.choice([16, 32, 48, 60]),
                        "phase": rng.randrange(60)})
        return out

    def _make_aprs(self, rng):
        out = []
        for i, cs in enumerate(["W0ABC", "K0XYZ-9", "N0DEF", "W0GHI-7",
                                "K0LMN", "N0PQR-1"]):
            lat = self.home[0] + rng.uniform(-0.7, 0.7)
            lon = self.home[1] + rng.uniform(-0.9, 0.9)
            out.append({"callsign": cs, "lat": lat, "lon": lon,
                        "comment": ["simulated station", "digipeater",
                                    "weather station", "mobile",
                                    "home station", "igate"][i],
                        "period_s": 600, "phase": rng.randrange(600)})
        return out

    # ------------------------------------------------------------- sonde
    def sonde_flights(self, t0, t1):
        """Flights overlapping [t0, t1]: dicts with serial and track fn."""
        flights = []
        day = 86400
        first_day = int(t0 // day) - 1
        for d in range(first_day, int(t1 // day) + 2):
            for hour in self.synoptic_hours:
                synoptic = d * day + hour * 3600
                launch = synoptic - 2700
                if launch + 7200 < t0 or launch > t1:
                    continue
                for si, site in enumerate(self.sonde_sites):
                    dist = _haversine_km(self.home[0], self.home[1],
                                         site["lat"], site["lon"])
                    if dist > 700:
                        continue
                    rng = _rng(self.seed, int(launch), si)
                    flights.append({
                        "serial": "S%05d" % rng.randrange(10000, 99999),
                        "site": site, "launch": launch,
                        "burst": launch + 6900,
                        "drift_e": rng.uniform(60, 140),
                        "drift_n": rng.uniform(-20, 40),
                    })
        return flights

    def _sonde_pos(self, flight, t):
        dt = t - flight["launch"]
        if dt < 0 or dt > 7200:
            return None
        climb = 5.5 * dt if dt < 5800 else 31900 - 2.0 * (dt - 5800)
        frac = dt / 7200.0
        lat = flight["site"]["lat"] + flight["drift_n"] * frac / 111.32
        lon = flight["site"]["lon"] + flight["drift_e"] * frac / (
            111.32 * math.cos(math.radians(flight["site"]["lat"])))
        return lat, lon, max(climb, 0.0)


class SimSource:
    kind = "simulated"

    def __init__(self, profile, watch_start):
        self.profile = profile
        self.world = SimWorld(profile["source"]["seed"],
                              profile["home"]["lat"], profile["home"]["lon"],
                              watch_start, profile)

    def available(self):
        return True, "simulated source (no hardware required)"

    def capabilities(self):
        return {"adsb", "airband", "acars", "ais", "wxradio", "ism",
                "aprs", "pager", "sweep", "satellite", "sonde",
                "cellular", "trunking", "broadcast"}

    # -------------------------------------------------------------- scan
    def scan(self, center_hz, bw_hz, t0, t1):
        w = self.world
        rng = _rng(w.seed, int(t0 // 60), int(center_hz))
        if center_hz < 30e6:
            floor = -98.0 + 4 * math.sin(t0 / 3600.0)
        elif center_hz < 300e6:
            floor = -110.0
        else:
            floor = -112.0
        floor += rng.uniform(-1.5, 1.5)
        lo, hi = center_hz - bw_hz / 2, center_hz + bw_hz / 2
        emissions = []

        def add(freq, bw, peak, shape, label=None):
            if lo <= freq <= hi:
                emissions.append({"freq_hz": freq, "bw_hz": bw,
                                  "peak_dbm": round(peak, 1),
                                  "shape": shape, "label": label})

        for freq, rds in w.fm_stations:
            add(freq, 200e3, -52, "fm-broadcast", rds)
        add(WX_FREQ, 25e3, -68, "nfm-voice", "NOAA Weather Radio")
        for cell in w.cells:
            add(cell["freq_hz"], 200e3, -74, "beacon", cell["cell_id"])
        for sys_ in w.trunk_systems:
            add(sys_["freq_hz"], 12.5e3, -70, "control-channel",
                "SYS " + sys_["sysid"])
        for ch in AIRBAND_CHANNELS:
            if self._airband_active(ch, t0):
                add(ch, 25e3, -76, "am-voice", None)
        for dev in w.devices:
            if (int(t0) + dev["phase"]) % dev["period_s"] < 8:
                add(dev["freq_hz"], 30e3, -84, "data-burst", dev["id"])
        for st in w.aprs_stations:
            if abs((t0 - w.start - st["phase"]) % st["period_s"]) < 5:
                add(APRS_FREQ, 12.5e3, -80, "data-burst", st["callsign"])
        # The fixed ambient population: occupants a real window at
        # these frequencies would hold. All carry known shapes, so
        # the census files them as ordinary PROBABLE observations.
        for freq, peak, shape, label in _AMBIENT:
            add(freq, 10e3, peak, shape, label)
        for freq, period, phase in _AMBIENT_BURSTS:
            if (int(t0) + phase) % period < 20:
                add(freq, 30e3, -82, "data-burst", None)
        for unk in w.unknowns:
            # Recurring bursts on the emitter's own period: active
            # when any burst overlaps the scan window.
            first = unk["t"]
            if t1 < first:
                continue
            k0 = max(0, int((t0 - first) // unk["period"]) - 1)
            k1 = int((t1 - first) // unk["period"]) + 1
            for k in range(k0, k1 + 1):
                burst_start = first + k * unk["period"]
                if burst_start <= t1 and t0 <= burst_start + unk["dur"]:
                    add(unk["freq_hz"], unk["bw_hz"], unk["peak_dbm"],
                        unk["shape"], None)
                    break
        for sat in w.sats:
            elev, _az, _r = orbit.look_angles(
                sat, (t0 + t1) / 2, w.home[0], w.home[1])
            if elev > 5:
                freq = 137.62e6 if "15" in sat["name"] else 137.1e6
                if "METEOR" in sat["name"]:
                    freq = 137.9e6
                add(freq, 150e3, -88, "apt", sat["name"])
        return {"noise_floor_db": round(floor, 1), "emissions": emissions}

    def _airband_active(self, freq_hz, t):
        w = self.world
        if freq_hz == 121.5e6:
            return abs(t - (w.start + w.guard_tx_offset)) < 60
        rng = _rng(w.seed, int(t // 300), int(freq_hz))
        return rng.random() < 0.55

    # ----------------------------------------------------------- capture
    def capture(self, center_hz, bw_hz, t0, t1, vfos):
        feeds = {}
        for vfo in vfos:
            desk = vfo["desk"]
            if desk == "sweep":
                feeds["sweep"] = self.scan(center_hz, bw_hz, t0, t1)
            elif desk == "adsb":
                feeds["adsb"] = {"frames": self._adsb_frames(t0, t1)}
            elif desk == "acars":
                feeds["acars"] = {"messages": self._acars_messages(t0, t1)}
            elif desk == "airband":
                feeds["airband"] = {"segments":
                                    self._airband_segments(t0, t1, vfo)}
            elif desk == "ais":
                feeds["ais"] = {"sentences": self._ais_sentences(t0, t1)}
            elif desk == "wxradio":
                feeds["wxradio"] = self._wx_feed(t0, t1)
            elif desk == "ism":
                feeds["ism"] = {"bursts": self._ism_bursts(t0, t1, center_hz)}
            elif desk == "aprs":
                feeds["aprs"] = {"packets": self._aprs_packets(t0, t1)}
            elif desk == "pager":
                feeds["pager"] = {"batches": self._pager_batches(t0, t1)}
            elif desk == "satellite":
                feeds["satellite"] = self._satellite_feed(vfo)
            elif desk == "sonde":
                feeds["sonde"] = {"frames": self._sonde_frames(t0, t1)}
            elif desk == "cellular":
                feeds["cellular"] = {"cells": self._cell_feed(t0, center_hz)}
            elif desk == "trunking":
                feeds["trunking"] = {"systems": self._trunk_feed(t0)}
            elif desk == "broadcast":
                feeds["broadcast"] = {
                    "stations": [dict(freq_hz=f, rds=r, peak_dbm=-52.0)
                                 for f, r in self.world.fm_stations
                                 if abs(f - center_hz) <= bw_hz / 2]}
        return feeds

    def _adsb_frames(self, t0, t1):
        w = self.world
        frames = []
        mid = (t0 + t1) / 2
        for ac in w.aircraft:
            pos = w._aircraft_pos(ac, mid)
            if pos is None or pos[2] > 280:
                continue
            lat, lon, _d, heading, speed = pos
            frames.append(modes.encode_callsign(ac["icao"], ac["flight"]))
            frames.append(modes.encode_position(
                ac["icao"], lat, lon, ac["alt_ft"], is_odd=False))
            frames.append(modes.encode_position(
                ac["icao"], lat, lon, ac["alt_ft"], is_odd=True))
            frames.append(modes.encode_velocity(ac["icao"], speed, heading))
        return frames

    def _acars_messages(self, t0, t1):
        w = self.world
        out = []
        for ac in w.aircraft:
            rng = _rng(w.seed, ac["icao"], int(t0 // 1500))
            if rng.random() < 0.55:
                continue
            pos = w._aircraft_pos(ac, (t0 + t1) / 2)
            if pos is None or pos[2] > 280:
                continue
            text = rng.choice(_ACARS_TEXTS).format(
                eta="%02d%02d" % (rng.randrange(24), rng.randrange(60)),
                fuel="%d KG" % rng.randrange(3000, 19000),
                lat="%.2f" % pos[0], lon="%.2f" % pos[1])
            out.append(codecs.acars_build(
                ac["tail"], ac["flight"],
                rng.choice(["Q0", "H1", "5R", "A9"]), text))
        return out

    def _airband_segments(self, t0, t1, vfo):
        w = self.world
        rng = _rng(w.seed, int(t0), int(vfo["freq_hz"]))
        segments = []
        dur = t1 - t0
        for ch in AIRBAND_CHANNELS:
            if abs(ch - vfo["freq_hz"]) > vfo["bw_hz"] / 2:
                continue
            if ch == 121.5e6:
                gt = w.start + w.guard_tx_offset
                if t0 <= gt <= t1:
                    segments.append({"freq_hz": ch, "offset_s": gt - t0,
                                     "dur_s": 9.0, "snr_db": 28.0,
                                     "callsign": None})
                continue
            count = rng.randrange(0, 4)
            for _ in range(count):
                callsign = None
                if rng.random() < 0.4:
                    active = [a for a in w.aircraft
                              if w._aircraft_pos(a, t0) is not None]
                    if active:
                        callsign = rng.choice(active)["flight"]
                segments.append({
                    "freq_hz": ch, "offset_s": rng.uniform(0, dur * 0.9),
                    "dur_s": rng.uniform(2, 14),
                    "snr_db": round(rng.uniform(8, 30), 1),
                    "callsign": callsign})
        return segments

    def _ais_sentences(self, t0, t1):
        w = self.world
        out = []
        for i, v in enumerate(w.vessels):
            lat, lon = w._vessel_pos(v, (t0 + t1) / 2)
            out.append(codecs.ais_encode_position(
                v["mmsi"], lat, lon, sog_kt=v["sog"],
                cog_deg=80 + i * 12, heading_deg=82 + i * 12))
            if (int(t0) // 1800 + i) % 5 == 0:
                out.append(codecs.ais_encode_static(
                    v["mmsi"], v["name"], v["callsign"],
                    destination="RIVERPORT"))
        return out

    def _wx_feed(self, t0, t1):
        w = self.world
        headers = []
        for offset, code, areas in w.same_alerts:
            at = w.start + offset
            if t0 - 1800 <= at <= t1:
                headers.append(codecs.same_build(
                    code, areas, "KXXX", issued="317%04d" % (int(at) % 2400)))
        return {"present": True, "same": headers, "freq_hz": WX_FREQ}

    def _ism_bursts(self, t0, t1, center_hz):
        w = self.world
        rng = _rng(w.seed, int(t0), int(center_hz))
        out = []
        for dev in w.devices:
            if abs(dev["freq_hz"] - center_hz) > 1.3e6:
                continue
            ticks = int((t1 - t0) / dev["period_s"]) + 1
            for _ in range(min(ticks, 12)):
                data = {}
                if dev["kind"] == "weather":
                    data = {"temp_c": round(rng.uniform(-4, 31), 1),
                            "humidity_pct": rng.randrange(20, 95)}
                elif dev["kind"] == "meter":
                    data = {"power_w": rng.randrange(40, 4200)}
                elif dev["kind"] == "tpms":
                    data = {"pressure_kpa": rng.randrange(180, 260)}
                elif dev["kind"] == "soil":
                    data = {"moisture_pct": rng.randrange(8, 60)}
                else:
                    data = {"state": rng.choice(["open", "closed"])}
                out.append({"device": dev["id"], "model": dev["model"],
                            "freq_hz": dev["freq_hz"], "data": data,
                            "rssi_dbm": round(rng.uniform(-88, -60), 1)})
        return out

    def _aprs_packets(self, t0, t1):
        w = self.world
        out = []
        for st in w.aprs_stations:
            phase = w.start + st["phase"]
            k = int((t0 - phase) // st["period_s"])
            for kk in (k, k + 1):
                bt = phase + kk * st["period_s"]
                if t0 <= bt <= t1:
                    out.append(codecs.aprs_build_position(
                        st["callsign"], st["lat"], st["lon"],
                        st["comment"]))
        return out

    def _pager_batches(self, t0, t1):
        w = self.world
        rng = _rng(w.seed, int(t0 // 300))
        out = []
        for _ in range(rng.randrange(0, 3)):
            address = rng.choice(w.pagers)
            hexs, frame = codecs.pocsag_encode_batch(
                address, 0, rng.choice(_PAGER_TEXTS))
            out.append({"hex": hexs, "frame": frame})
        return out

    def _satellite_feed(self, vfo):
        p = vfo.get("pass") or {}
        max_elev = p.get("max_elev_deg", 0)
        return {"lock": max_elev >= 12.0,
                "quality": round(min(1.0, max(0.0,
                                 (max_elev - 8) / 60.0)), 2),
                "max_elev_deg": max_elev, "sat": p.get("sat"),
                "norad": p.get("norad")}

    def _sonde_frames(self, t0, t1):
        w = self.world
        out = []
        for flight in w.sonde_flights(t0, t1):
            for tt in (t0, (t0 + t1) / 2, t1 - 1):
                pos = w._sonde_pos(flight, tt)
                if pos is None:
                    continue
                lat, lon, alt = pos
                out.append({"serial": flight["serial"], "lat": lat,
                            "lon": lon, "alt_m": alt,
                            "burst": tt >= flight["burst"] - 60,
                            "site": flight["site"]["name"]})
        return out

    def _cell_feed(self, t0, center_hz):
        rng = _rng(self.world.seed, int(t0 // 600))
        return [dict(cell_id=c["cell_id"], band=c["band"],
                     freq_hz=c["freq_hz"],
                     rsrp_dbm=round(rng.uniform(-95, -70), 1))
                for c in self.world.cells
                if abs(c["freq_hz"] - center_hz) < 1.3e6]

    def _trunk_feed(self, t0):
        rng = _rng(self.world.seed, int(t0 // 600))
        return [dict(sysid=s["sysid"], freq_hz=s["freq_hz"],
                     sites=s["sites"],
                     talkgroups_active=rng.randrange(2, 18))
                for s in self.world.trunk_systems]
