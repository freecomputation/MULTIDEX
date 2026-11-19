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

import base64
import math
import random

from . import codecs, displayfeed, modes, orbit
from .desks import AIRBAND_CHANNELS, MILAIR_CHANNELS
from .tuner import _wav_b64

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
        # The probe desk's planted quarry (0.11.0): one fast,
        # regular unknown on a short period, so a probe stare
        # billed anywhere in a watch overlaps a burst - its
        # off-time (540 s) is shorter than the standard probe
        # dwell (600 s). Fixed values, drawn from no RNG
        # stream, so the rest of the world is unchanged.
        self.unknowns.append({
            "t": start_epoch + 300, "dur": 360,
            "freq_hz": 461.0e6, "bw_hz": 25e3, "peak_dbm": -66,
            "shape": "unknown-pulsed", "period": 900})
        self.sonde_sites = profile["sondes"]["sites"]
        self.synoptic_hours = profile["sondes"]["synoptic_hours"]
        # The astronomical plants (0.15.0), as fixed scripted
        # moments like the guard transmission - no RNG draws, so
        # the rest of the world is unchanged: a solar radio
        # burst in the decametric band early in the watch, and
        # a sudden ionospheric disturbance later, during which
        # the world's known HF signals fade together the way
        # the real ionosphere drops them.
        self.solar_burst_offset = 2.2 * 3600
        self.solar_burst_dur = 600
        self.sid_offset = 5.5 * 3600
        self.sid_dur = 2400
        self._jupiter_storm = None
        self._jupiter_storm_done = False
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

    def jupiter_storm(self):
        """The world's one planted decametric storm: inside the
        first predicted window the package's own ephemeris
        finds at the home, so the world and the bill agree by
        construction. Morphology follows the window's source -
        Io-B storms are the fast S-burst trains, the rest read
        as L-burst envelopes. (t0, t1, source, kind) or None."""
        if self._jupiter_storm_done:
            return self._jupiter_storm
        self._jupiter_storm_done = True
        from . import ephemeris
        windows = ephemeris.jupiter_windows(
            self.home[0], self.home[1], self.start,
            self.start + 48 * 3600)
        for w0 in windows:
            span = w0["end"] - w0["start"]
            if span < 600:
                continue
            # The storm occupies the window's middle: it
            # neither opens nor closes the window, the way a
            # real storm complex sits inside its geometry.
            t0 = w0["start"] + span * 0.2
            t1 = w0["start"] + span * 0.8
            self._jupiter_storm = (
                t0, t1, w0["source"],
                "S" if w0["source"] == "Io-B" else "L")
            break
        return self._jupiter_storm

    def in_sid(self, t):
        return self.start + self.sid_offset <= t \
            < self.start + self.sid_offset + self.sid_dur

    def in_solar_burst(self, t):
        return self.start + self.solar_burst_offset <= t \
            < self.start + self.solar_burst_offset \
            + self.solar_burst_dur

    def meteor_pings(self, t0, t1, freq_hz):
        """The world's meteor reflections over [t0, t1] on one
        carrier: an hourly rate following the radiant geometry
        - a pre-dawn maximum in local solar time - with
        deterministic per-hour draws, so the desk's account
        and the world agree."""
        pings = []
        hour = int(t0 // 3600) * 3600
        while hour < t1:
            seg0, seg1 = max(t0, hour), min(t1, hour + 3600)
            if seg1 > seg0:
                local_h = (hour / 3600.0 + self.home[1] / 15.0) % 24
                rate = 30.0 if 3 <= local_h < 9 else 8.0
                expect = rate * (seg1 - seg0) / 3600.0
                rng = _rng(self.seed, hour, "meteor")
                n = int(expect)
                if rng.random() < expect - n:
                    n += 1
                for _ in range(n):
                    off = seg0 - t0 + rng.uniform(
                        0, max(1.0, seg1 - seg0 - 1))
                    pings.append({
                        "freq_hz": freq_hz,
                        "offset_s": round(off, 1),
                        "dur_s": round(rng.uniform(0.1, 0.9), 2),
                        "snr_db": round(rng.uniform(8, 25), 1),
                        "peak_dbm": round(-110
                                          + rng.uniform(8, 25), 1)})
            hour += 3600
        return sorted(pings, key=lambda p: p["offset_s"])

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


# ------------------------------------------------------- APT signal
#
# A spec-shaped NOAA APT recording for the simulated satellite feed,
# so the desk's imagery pipeline (record, decode, file the image)
# runs end to end without hardware. The line format follows the
# published APT specification: 2080 words per line at 4160 words
# per second (2 lines per second) - Sync A (1040 Hz pulses), space,
# image A, telemetry A, Sync B (832 Hz pulses), space, image B,
# telemetry B - with the video envelope AM-modulating a 2400 Hz
# subcarrier. The picture is a horizontal gradient with a white
# block in the middle rows, so a correct decode is recognizable at
# a glance. Deterministic: the same duration and rate always
# produce the same samples, and the package's own APT decoder
# (multidex.apt) decodes them into a 2080-pixel-wide image.

_APT_WORDS = 4160        # envelope words per second (1 per word)
_APT_BLACK, _APT_WHITE = 0.13, 0.95


def _apt_sync(n_words, freq):
    half = _APT_WORDS / (2.0 * freq)
    out = []
    pos = 0.0
    for _ in range(n_words):
        out.append(_APT_WHITE if (pos % (2 * half)) < half else 0.05)
        pos += 1.0
    return out


def _apt_line(row):
    img_a = [_APT_BLACK + (_APT_WHITE - _APT_BLACK) * (i / 908.0)
             for i in range(909)]
    img_b = img_a[::-1]
    if 8 <= row < 16:
        for i in range(400, 520):
            img_a[i] = _APT_WHITE
            img_b[i] = _APT_WHITE
    return (_apt_sync(39, 1040) + [_APT_BLACK] * 47 + img_a
            + [0.5] * 45 + _apt_sync(39, 832) + [_APT_BLACK] * 47
            + img_b + [0.5] * 45)


def synthetic_mbe_archive(fmt="AMBE+2", frames=100, slot=1):
    """A synthetic vocoder frame archive in the container format
    DSD-FME's mbe data files use (0.6.0): the 4-byte cookie that
    names the format, then one record per 20 ms frame - an
    error-count byte followed by the parameter bits packed
    (49 bits in 7 bytes for AMBE+2, 88 bits in 11 bytes for
    IMBE). The parameter bytes are deterministic filler from a
    fixed seed: shaped like the evidence the digital voice desk
    archives, labelled synthetic wherever it appears, and never
    a real call's frames."""
    if fmt == "IMBE":
        ext, frame_bytes, param_bits = ".imb", 12, 88
    else:
        ext, frame_bytes, param_bits = ".amb", 8, 49
    rng = _rng("mbe-fixture", fmt, frames, slot)
    body = bytearray(ext.encode("ascii"))
    for _ in range(frames):
        body.append(0)
        body.extend(rng.randrange(256)
                    for _ in range(frame_bytes - 1))
    import base64 as _b64
    return {"format": fmt, "container": ext,
            "frame_bytes": frame_bytes, "param_bits": param_bits,
            "frames": frames, "bytes": len(body), "slot": slot,
            "source_name": "synthetic-S%s%s" % (slot, ext),
            "synthetic": True,
            "association": "synthetic fixture",
            "b64": _b64.b64encode(bytes(body)).decode("ascii")}


def synthetic_apt_samples(seconds=12, rate=48000):
    """The synthetic APT recording as a list of int16 samples."""
    env = []
    for r in range(int(seconds * 2)):
        env.extend(_apt_line(r))
    out = []
    total = int(seconds * rate)
    last = len(env) - 1
    for i in range(total):
        pos = i * _APT_WORDS / rate
        j = int(pos)
        if j >= last:
            value = env[last]
        else:
            frac = pos - j
            value = env[j] * (1.0 - frac) + env[j + 1] * frac
        sample = value * math.cos(2 * math.pi * 2400 * i / rate)
        out.append(int(max(-1.0, min(1.0, sample * 0.9)) * 32767))
    return out


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
        return {"adsb", "airband", "milair", "acars", "ais", "wxradio",
                "ism", "aprs", "pager", "sweep", "satellite", "sonde",
                "cellular", "trunking", "broadcast", "audiocensus",
                "amateur", "iss", "vdl2", "digitalvoice", "hfdl",
                "probe", "jupiter", "solar", "meteor"}

    # -------------------------------------------------------------- scan
    def _scan_model(self, center_hz, bw_hz, t0, t1):
        """The scan's world model for a span (0.16.0): the noise
        floor and the emissions the world holds inside it, with
        the span's seeded rng positioned after the floor draw,
        exactly where scan's own rendering has always found it.
        Extracted so the display stream can render the same
        model at a single instant without moving a single one
        of scan's own outputs."""
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
                # The world's sudden ionospheric disturbance
                # (0.15.0): during the planted span the known HF
                # signals fade together by some 25 dB - the
                # signature the solar desk reads its account
                # from. Unknown shapes are the census's own
                # business and are not faded by the plant.
                if w.in_sid(t0) and freq < 30e6 \
                        and not shape.startswith("unknown"):
                    peak -= 25.0
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
        # The world's planted solar burst (0.15.0): a wideband
        # elevation across the decametric band during its span,
        # standing well over the floor the way a real burst
        # stands over a quiet band.
        if w.in_solar_burst((t0 + t1) / 2) and 15e6 < center_hz < 35e6:
            floor += 4.0
            add(center_hz, 2.0e6, floor + 18.0, "solar-burst",
                "solar burst (simulated)")
        for sat in w.sats:
            elev, _az, _r = orbit.look_angles(
                sat, (t0 + t1) / 2, w.home[0], w.home[1])
            if elev > 5:
                freq = 137.62e6 if "15" in sat["name"] else 137.1e6
                if "METEOR" in sat["name"]:
                    freq = 137.9e6
                add(freq, 150e3, -88, "apt", sat["name"])
        return floor, emissions, lo, hi, rng

    def scan(self, center_hz, bw_hz, t0, t1):
        floor, emissions, lo, hi, rng = self._scan_model(
            center_hz, bw_hz, t0, t1)
        # The Console's waterfall frame for this window (0.8.0):
        # the simulated world's own spectrum, its emissions drawn
        # as gaussian shoulders over its floor - the same model
        # the census reads, rendered for the glass.
        bins = self._render_bins(floor, emissions, lo, hi, rng)
        excerpts = []
        unk = [e for e in emissions
               if (e.get("shape") or "").startswith("unknown")]
        if unk:
            em = max(unk, key=lambda e: e["peak_dbm"])
            if em["peak_dbm"] - floor >= 30.0:
                exc = self._scan_excerpt(em, t0)
                if exc is not None:
                    excerpts.append(exc)
        return {"noise_floor_db": round(floor, 1),
                "emissions": emissions,
                "excerpts": excerpts,
                "display": {"lo_hz": lo, "hi_hz": hi,
                            "floor_db": round(floor, 1),
                            "bins": bins}}

    @staticmethod
    def _render_bins(floor, emissions, lo, hi, rng):
        """The scan's display rendering (0.8.0, extracted
        0.16.0): the model's emissions as gaussian shoulders
        over its floor, one seeded noise draw per bin, in the
        order scan has always drawn them."""
        bins = []
        nb = 120
        for i in range(nb):
            f = lo + (hi - lo) * (i + 0.5) / nb
            v = floor + rng.uniform(-0.8, 0.8)
            for em in emissions:
                sigma = max(em["bw_hz"] / 2.5, (hi - lo) / nb)
                bump = (em["peak_dbm"] - floor) * math.exp(
                    -0.5 * ((f - em["freq_hz"]) / sigma) ** 2)
                if bump > 0.5:
                    v = max(v, floor + bump)
            bins.append(round(v, 1))
        return bins

    def _display_at(self, center_hz, bw_hz, ts):
        """The world's spectrum at one instant (0.16.0), for the
        display stream: the scan model evaluated at ts alone and
        drawn with the scan's own rendering, so a streamed frame
        is the same kind of measurement of the same world as the
        frame a scan publishes - the world as it stands at ts,
        its bursts and fades included, timestamped ts."""
        floor, emissions, lo, hi, rng = self._scan_model(
            center_hz, bw_hz, ts, ts)
        return {"t": ts, "lo_hz": lo, "hi_hz": hi,
                "floor_db": round(floor, 1),
                "bins": self._render_bins(floor, emissions,
                                          lo, hi, rng),
                "brackets": []}

    def _scan_excerpt(self, em, t0):
        """A synthesized tier 2 excerpt for the world's
        strongest unknown emission (0.10.0): its shape rendered
        as channel samples at 48 ksps - a chirp sweeps, a pulsed
        emission gates, anything else holds a tone - over noise,
        encoded the way the real source encodes its captures.
        The desk marks it synthetic in its SigMF metadata."""
        import numpy as np
        from . import sigmf
        rng = _rng(self.world.seed, int(em["freq_hz"]), int(t0))
        gen = np.random.default_rng(int(rng.random() * 1e9))
        rate = 48000.0
        n = int(rate * 0.5)
        t = np.arange(n) / rate
        shape = em.get("shape") or ""
        if "chirp" in shape:
            phase = 2 * np.pi * (-8000.0 * t + 16000.0 * t * t)
            sig = np.exp(1j * phase)
        elif "pulsed" in shape:
            gate = ((t % 0.1) < 0.03).astype(np.float64)
            sig = gate * np.exp(2j * np.pi * 1000.0 * t)
        else:
            sig = np.exp(2j * np.pi * 1500.0 * t)
        noise = 0.05 * (gen.normal(0, 1, n)
                        + 1j * gen.normal(0, 1, n))
        chan = (0.8 * sig + noise).astype(np.complex64)
        data, peak = sigmf.data_bytes(chan)
        import base64 as _b64
        return {"b64": _b64.b64encode(data).decode("ascii"),
                "rate": rate, "peak": peak, "samples": n,
                "freq_hz": em["freq_hz"], "bw_hz": em["bw_hz"]}

    def _probe_feed(self, center_hz, bw_hz, t0, t1):
        """The probe stare on the simulated source (0.11.0):
        the world model is exact about its unknowns, so the
        feed states plainly whether a burst of the target
        overlapped the stare, and renders the stare's most
        active span - a 2.5 s window placed on the burst - as
        IQ through the real census engine, with a channel
        excerpt of the target. The context states where the
        burst sat inside the dwell: the stare held the
        frequency for the whole dwell in the model, and the
        rendered window is the burst's span of it."""
        import numpy as np
        from . import audiocensus
        w = self.world
        fs = 240000.0
        seconds = 2.5
        n = int(fs * seconds)
        rng = _rng(w.seed, int(center_hz), int(t0), "probe")
        gen = np.random.default_rng(int(rng.random() * 1e9))
        t = np.arange(n) / fs
        iq = (gen.normal(0, 0.02, n)
              + 1j * gen.normal(0, 0.02, n)).astype(np.complex128)
        # The target: the world unknown nearest the stare's
        # centre. A stare is at one emitter; another unknown
        # bursting in the window does not make this one heard.
        target = None
        for unk in w.unknowns:
            if abs(unk["freq_hz"] - center_hz) <= bw_hz / 2:
                if target is None or abs(unk["freq_hz"] - center_hz) \
                        < abs(target["freq_hz"] - center_hz):
                    target = unk
        burst = None
        if target is not None:
            first = target["t"]
            if t1 >= first:
                k0 = max(0, int((t0 - first) // target["period"]) - 1)
                k1 = int((t1 - first) // target["period"]) + 1
                for k in range(k0, k1 + 1):
                    bs = first + k * target["period"]
                    be = bs + target["dur"]
                    if bs <= t1 and t0 <= be:
                        burst = (bs, be)
                        break
        silent = burst is None
        if not silent:
            bs, be = burst
            off = target["freq_hz"] - center_hz
            # The burst's span inside the rendered window: the
            # window sits on the burst (it starts at the later
            # of the dwell's start and the burst's start), so
            # the burst is on from the window's first sample.
            on_at = 0.0
            on_for = min(seconds, be - max(t0, bs))
            gate = ((t >= on_at) & (t < on_at + on_for)).astype(
                np.float64)
            shape = target["shape"]
            bw = target["bw_hz"]
            if "chirp" in shape:
                sweep = bw / 4.0
                phase = 2 * np.pi * (
                    (off - sweep) * t
                    + (2 * sweep / max(on_for, 0.1)) * t * t)
                sig = gate * np.exp(1j * phase)
            elif "pulsed" in shape:
                pulse = ((t % 0.1) < 0.03).astype(np.float64)
                sig = gate * pulse * np.exp(
                    2j * np.pi * (off + 1000.0) * t)
            elif "wideband" in shape:
                raw = (gen.normal(0, 1, n)
                       + 1j * gen.normal(0, 1, n))
                spec = np.fft.fft(raw)
                fr = np.fft.fftfreq(n, 1.0 / fs)
                keep = np.abs(fr - off) <= bw / 2
                sig = gate * np.fft.ifft(np.where(keep, spec, 0.0))
                sig = sig / (np.max(np.abs(sig)) + 1e-9)
            elif "multicarrier" in shape:
                sig = np.zeros(n, dtype=np.complex128)
                for j, frac in enumerate((-0.3, -0.1, 0.1, 0.3)):
                    sig = sig + gate * np.exp(
                        2j * np.pi * (off + frac * bw) * t
                        + 1j * j)
                sig = sig / 4.0
            else:
                sig = gate * np.exp(2j * np.pi * off * t)
            iq = iq + 0.5 * sig
        census = audiocensus.analyze(iq, fs, center_hz,
                                     extract_cap=6)
        excerpt = None
        snr = None
        cand = None
        for c in census.get("candidates") or []:
            if abs(c["freq_hz"] - center_hz) <= bw_hz / 2:
                if cand is None or c["snr_db"] > cand["snr_db"]:
                    cand = c
        if cand is not None:
            snr = cand["snr_db"]
        if target is not None and not silent:
            chan, rate = audiocensus._extract(
                iq, fs, target["freq_hz"] - center_hz,
                max(25e3, target["bw_hz"]))
            exc = audiocensus._excerpt(chan, rate)
            if exc is not None:
                exc["freq_hz"] = target["freq_hz"]
                exc["bw_hz"] = target["bw_hz"]
                excerpt = exc
        if silent:
            context = {
                "capture_s": seconds, "pre_s": None, "post_s": None,
                "note": "no burst of the target overlapped the "
                        "stare: the simulated source models the "
                        "stare for the whole dwell, and the "
                        "target's bursts are stated exactly by "
                        "its period"}
        else:
            bs, be = burst
            context = {
                "capture_s": seconds,
                "pre_s": round(max(0.0, bs - t0), 1),
                "post_s": round(max(0.0, t1 - be), 1),
                "note": "the simulated source models the stare "
                        "for the whole dwell; the rendered "
                        "capture window is placed on the burst, "
                        "the stare's most active span"}
        return {"target_freq_hz": float(center_hz),
                "census": census, "excerpt": excerpt,
                "silent": silent, "snr_db": snr,
                "context": context}

    def _airband_active(self, freq_hz, t):
        w = self.world
        if freq_hz == 121.5e6:
            return abs(t - (w.start + w.guard_tx_offset)) < 60
        rng = _rng(w.seed, int(t // 300), int(freq_hz))
        return rng.random() < 0.55

    # ----------------------------------------------------------- capture
    def capture(self, center_hz, bw_hz, t0, t1, vfos, stream=None):
        feeds = {}
        for vfo in vfos:
            desk = vfo["desk"]
            if desk == "sweep":
                feeds["sweep"] = self.scan(center_hz, bw_hz, t0, t1)
            elif desk == "audiocensus":
                feeds["audiocensus"] = self._audiocensus_feed(
                    center_hz, bw_hz, t0, t1)
            elif desk == "probe":
                feeds["probe"] = self._probe_feed(
                    center_hz, bw_hz, t0, t1)
            elif desk == "adsb":
                feeds["adsb"] = {"frames": self._adsb_frames(t0, t1)}
            elif desk == "acars":
                feeds["acars"] = {"messages": self._acars_messages(t0, t1)}
            elif desk == "airband":
                feeds["airband"] = {"segments":
                                    self._airband_segments(t0, t1, vfo)}
            elif desk == "milair":
                feeds["milair"] = {"segments": self._airband_segments(
                    t0, t1, vfo, MILAIR_CHANNELS)}
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
            elif desk == "amateur":
                feeds["amateur"] = self._amateur_feed(center_hz,
                                                      t0, t1)
            elif desk == "iss":
                feeds["iss"] = self._iss_feed(vfo, t0, t1)
            elif desk == "vdl2":
                feeds["vdl2"] = self._vdl2_feed(t0, t1)
            elif desk == "digitalvoice":
                feeds["digitalvoice"] = self._digitalvoice_feed(
                    center_hz, t0, t1)
            elif desk == "hfdl":
                feeds["hfdl"] = self._hfdl_feed(center_hz, t0, t1)
            elif desk == "sonde":
                feeds["sonde"] = {"channels":
                                  self._sonde_channels(t0, t1)}
            elif desk == "jupiter":
                feeds["jupiter"] = self._jupiter_feed(
                    center_hz, bw_hz, t0, t1)
            elif desk == "solar":
                feeds["solar"] = self.scan(center_hz, bw_hz, t0, t1)
            elif desk == "meteor":
                feeds["meteor"] = self._meteor_feed(
                    center_hz, bw_hz, t0, t1)
            elif desk == "cellular":
                feeds["cellular"] = {"cells": self._cell_feed(t0, center_hz)}
            elif desk == "trunking":
                feeds["trunking"] = {"systems": self._trunk_feed(t0)}
            elif desk == "broadcast":
                feeds["broadcast"] = {
                    "stations": [dict(freq_hz=f, rds=r, peak_dbm=-52.0)
                                 for f, r in self.world.fm_stations
                                 if abs(f - center_hz) <= bw_hz / 2]}
        if stream is not None:
            # The display stream (0.16.0): the tuned window's
            # spectrum at every cadence point inside the dwell,
            # rendered from the world model at that instant.
            # Display only and guarded as a whole - the feeds
            # above are the record, and a stream failure must
            # never reach them.
            try:
                ts = t0 + displayfeed.STREAM_CADENCE_S
                while ts < t1:
                    stream(self._display_at(center_hz, bw_hz, ts))
                    ts += displayfeed.STREAM_CADENCE_S
            except Exception:
                pass
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
        in_range = []
        for ac in w.aircraft:
            rng = _rng(w.seed, ac["icao"], int(t0 // 1500))
            pos = w._aircraft_pos(ac, (t0 + t1) / 2)
            if pos is None or pos[2] > 280:
                continue
            in_range.append((ac, rng, pos))
            if rng.random() < 0.55:
                continue
            text = rng.choice(_ACARS_TEXTS).format(
                eta="%02d%02d" % (rng.randrange(24), rng.randrange(60)),
                fuel="%d KG" % rng.randrange(3000, 19000),
                lat="%.2f" % pos[0], lon="%.2f" % pos[1])
            out.append(codecs.acars_build(
                ac["tail"], ac["flight"],
                rng.choice(["Q0", "H1", "5R", "A9"]), text))
        if not out and in_range:
            # A full dwell on the datalink channel with traffic in
            # range hears the channel work: the per-aircraft skip
            # models density, not a silent channel under traffic.
            ac, rng, pos = in_range[0]
            text = rng.choice(_ACARS_TEXTS).format(
                eta="%02d%02d" % (rng.randrange(24), rng.randrange(60)),
                fuel="%d KG" % rng.randrange(3000, 19000),
                lat="%.2f" % pos[0], lon="%.2f" % pos[1])
            out.append(codecs.acars_build(
                ac["tail"], ac["flight"],
                rng.choice(["Q0", "H1", "5R", "A9"]), text))
        return out

    def _airband_segments(self, t0, t1, vfo, channels=None):
        w = self.world
        rng = _rng(w.seed, int(t0), int(vfo["freq_hz"]))
        segments = []
        dur = t1 - t0
        for ch in (channels if channels is not None
                   else AIRBAND_CHANNELS):
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

    def _audiocensus_feed(self, center_hz, bw_hz, t0, t1):
        """Synthesize the window's IQ and run the real census
        engine over it: the simulated world carries voice-shaped
        AM, FM and SSB emissions, music, an FSK data signal and
        plain noise windows, on the same terms as the RF world."""
        import numpy as np
        from . import audiocensus
        fs = 240000.0
        seconds = 2.5
        n = int(fs * seconds)
        rng = _rng(self.world.seed, int(center_hz), int(t0 // 600))
        t = np.arange(n) / fs
        iq = (np.random.default_rng(int(rng.random() * 1e9))
              .normal(0, 0.02, n)
              + 1j * np.random.default_rng(int(rng.random() * 1e9) + 1)
              .normal(0, 0.02, n)).astype(np.complex128)
        if rng.random() < 0.75:
            kinds = rng.sample(["am_voice", "fm_voice", "ssb_voice",
                                "fm_music", "fsk_data", "carrier"],
                               k=rng.randrange(1, 3))
            for kind in kinds:
                off = rng.uniform(-bw_hz / 2 * 0.7, bw_hz / 2 * 0.7)
                amp = 0.35
                ar = 16000.0
                at = np.arange(int(ar * seconds)) / ar
                # Syllabic envelope: bursts with pauses.
                env = np.zeros_like(at)
                pos = 0.1
                while pos < seconds - 0.3:
                    dur = rng.uniform(0.25, 0.7)
                    i0, i1 = int(pos * ar), int((pos + dur) * ar)
                    env[i0:i1] = 1.0
                    pos += dur + rng.uniform(0.15, 0.6)
                pitch = rng.uniform(105, 165)
                audio = np.zeros_like(at)
                for h in range(1, 14):
                    f = pitch * h
                    if f > 3400:
                        break
                    form = math.exp(-((f - 800) ** 2) / (2 * 500 ** 2)) \
                        + 0.6 * math.exp(-((f - 2200) ** 2)
                                         / (2 * 600 ** 2))
                    audio += form / h * np.sin(
                        2 * np.pi * f * at + rng.uniform(0, 6.28))
                audio = audio * env
                audio /= (np.max(np.abs(audio)) + 1e-9)
                up = np.interp(t, at, audio)
                if kind == "am_voice":
                    sig = (1.0 + 0.85 * up) * np.exp(2j * np.pi * off * t)
                elif kind in ("fm_voice", "fm_music"):
                    if kind == "fm_music":
                        chord = sum(np.sin(2 * np.pi * f * at)
                                    for f in (261.6, 329.6, 392.0))
                        up = np.interp(t, at, chord / 3.0)
                        dev = 40000.0
                    else:
                        dev = 5000.0
                    phase = 2 * np.pi * np.cumsum(up) / fs * dev
                    sig = np.exp(1j * (2 * np.pi * off * t + phase))
                elif kind == "ssb_voice":
                    spec = np.fft.fft(up)
                    freqs = np.fft.fftfreq(len(up), 1 / fs)
                    spec = np.where(freqs < 0, 0.0, spec)
                    analytic = np.fft.ifft(spec) * 2
                    sig = analytic * np.exp(2j * np.pi * off * t)
                elif kind == "fsk_data":
                    tones = np.where((t * 8).astype(int) % 2 == 0,
                                     1200.0, 2200.0)
                    phase = 2 * np.pi * np.cumsum(tones) / fs
                    sig = 0.8 * np.exp(1j * (2 * np.pi * off * t + phase))
                else:  # carrier
                    sig = np.exp(2j * np.pi * off * t)
                iq = iq + amp * sig
        result = audiocensus.analyze(iq, fs, center_hz,
                                     extract_cap=6)
        for cand in result["candidates"]:
            if cand.get("audio"):
                cand["audio"] = cand["audio"][:16000 * 6]
        return result

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
        if "METEOR" in (p.get("sat") or "").upper():
            # Acknowledged only (0.6.0), exactly as the real
            # source behaves: no lock is computed and no capture
            # of any kind is attached - the desk files the
            # acknowledgement from the pass identity alone.
            return {"sat": p.get("sat"), "norad": p.get("norad"),
                    "acknowledged": True,
                    "max_elev_deg": p.get("max_elev_deg", 0)}
        max_elev = p.get("max_elev_deg", 0)
        feed = {"lock": max_elev >= 12.0,
                "quality": round(min(1.0, max(0.0,
                                 (max_elev - 8) / 60.0)), 2),
                "max_elev_deg": max_elev, "sat": p.get("sat"),
                "norad": p.get("norad")}
        if feed["lock"]:
            # A locked simulated pass carries a synthetic APT
            # recording, shaped to the published line format, so
            # the satellite desk records, decodes and files imagery
            # from it exactly as it does from a real capture.
            feed["audio_b64"] = _wav_b64(synthetic_apt_samples(),
                                         48000)
            feed["audio_name"] = "pass-%s-%d.wav" % (
                p.get("norad"), int(p.get("aos") or 0))
            feed["audio_rate"] = 48000
        return feed

    def _amateur_feed(self, center_hz, t0, t1):
        """CW by genuine round-trip: the message is encoded to IQ
        and recovered by the production detector. FT8 entries are
        modelled decodes, as the sim models the jt9 program's
        output the way it models other decoders'."""
        import numpy as np
        from . import cw as cw_mod
        rng = _rng(self.world.seed, int(center_hz), int(t0 // 900))
        feed = {"cw": [], "ft8": []}
        if rng.random() < 0.7:
            call = rng.choice(["W1ABC", "K4XYZ", "N0CALL", "DL2AB"])
            msg = "CQ CQ DE %s %s K" % (call, call)
            base = 7030000.0 if center_hz < 10e6 else 14030000.0
            iq = cw_mod.encode_iq(msg, wpm=17, fs=48000.0,
                                  offset_hz=2500.0)
            noise = (np.random.default_rng(rng.randrange(1 << 30))
                     .normal(0, 0.03, len(iq))
                     + 1j * np.random.default_rng(
                         rng.randrange(1 << 30))
                     .normal(0, 0.03, len(iq)))
            for sig in cw_mod.detect(iq + 0.5 * noise, 48000.0):
                sig = dict(sig)
                sig["freq_hz"] = base + sig.pop("freq_offset_hz")
                feed["cw"].append(sig)
        if rng.random() < 0.5:
            feed["ft8"].append({
                "freq_hz": (7074000.0 if center_hz < 10e6
                            else 14074000.0) + rng.randrange(400, 2400),
                "snr_db": rng.randrange(-18, 4),
                "dt_s": round(rng.uniform(-0.8, 0.8), 1),
                "text": rng.choice([
                    "CQ W1ABC FN42", "K4XYZ N0CALL -07",
                    "N0CALL DL2AB R-12", "CQ DL2AB JO62"])})
        return feed

    def _iss_feed(self, vfo, t0, t1):
        p = vfo.get("pass") or {}
        rng = _rng(self.world.seed, int(t0 // 600), 145800000)
        feed = {"attempted": True, "segments": [], "sstv": None}
        if p.get("max_elev_deg", 0) < 15:
            return feed
        roll = rng.random()
        if roll < 0.35:
            feed["segments"].append({
                "offset_s": rng.uniform(30, 200),
                "dur_s": rng.uniform(4, 12),
                "snr_db": rng.uniform(15, 30)})
        elif roll < 0.55:
            import base64 as _b64
            import numpy as np
            from . import sstv as sstv_mod
            img = np.zeros((240, 320, 3), dtype=np.uint8)
            img[:, :, 0] = np.linspace(0, 255, 320).astype(np.uint8)
            img[:, :, 1] = np.linspace(255, 0, 240).astype(
                np.uint8)[:, None]
            img[100:140, 100:220, 2] = 255
            audio = sstv_mod.encode_audio(img, "Robot 36", 12000)
            dec = sstv_mod.decode_audio(audio, 12000)
            if dec:
                feed["sstv"] = {"mode": dec["mode"],
                                "lines": dec["lines"],
                                "height": dec["height"],
                                "png_b64": _b64.b64encode(
                                    dec["png"]).decode("ascii")}
        return feed

    def _vdl2_feed(self, t0, t1):
        rng = _rng(self.world.seed, int(t0 // 900), 136975000)
        feed = {"messages": [], "bursts": []}
        if rng.random() < 0.6:
            tail = rng.choice(["N12345", "N67890", "N24680"])
            feed["messages"].append({
                "tail": tail,
                "text": "VDL2 ACARS label H1 from %s" % tail,
                "raw": "simulated vdl2 frame"})
        elif rng.random() < 0.5:
            feed["bursts"].append({"count": rng.randrange(2, 9)})
        return feed

    def _hfdl_feed(self, center_hz, t0, t1):
        rng = _rng(self.world.seed, int(center_hz), int(t0 // 900))
        feed = {"messages": []}
        if rng.random() < 0.5:
            feed["messages"].append({
                "freq_hz": float(center_hz),
                "pdu": "lpdu",
                "src": {"type": "Aircraft", "id": 12,
                        "ac_info": {"icao": "A23721"}},
                "dst": {"type": "Ground station", "id": 4,
                        "name": "Riverhead, New York"},
                "type_name": "Logon confirm",
                "raw": "simulated hfdl frame"})
        return feed

    def _digitalvoice_feed(self, center_hz, t0, t1):
        rng = _rng(self.world.seed, int(center_hz), int(t0 // 900))
        feed = {"calls": [], "frame_archives": []}
        if rng.random() < 0.6:
            call = {
                "protocol": rng.choice(["DMR", "P25", "NXDN"]),
                "system": "SYS:SIM-%d" % rng.randrange(100, 999),
                "talkgroup": str(rng.randrange(1, 500)),
                "source": str(rng.randrange(100000, 999999)),
                "slot": rng.choice([1, 2]),
                "encrypted": rng.random() < 0.3,
                "freq_hz": center_hz}
            if not call["encrypted"]:
                # The bound program writes vocoder frames only
                # for calls it can hear in the clear; the sim
                # models that product with a labelled synthetic
                # fixture in the program's own container format
                # (P25 modelled as Phase 1 IMBE, the TDMA
                # systems as AMBE+2).
                call["frames"] = [synthetic_mbe_archive(
                    "IMBE" if call["protocol"] == "P25"
                    else "AMBE+2",
                    frames=rng.randrange(40, 160),
                    slot=call["slot"])]
            feed["calls"].append(call)
        return feed

    def _jupiter_feed(self, center_hz, bw_hz, t0, t1):
        """The Jupiter window on the simulated source (0.15.0):
        a 2.5 s span at 48 ksps rendered the way the probe
        stare renders its span. Inside the world's planted
        storm the span carries the storm's morphology - an
        L-burst's smooth broadband envelope, or an S-burst
        train of millisecond pulses for an Io-B window. Outside
        it the span is quiet noise, with a deterministic
        narrowband terrestrial carrier in about half the
        windows: the impostor the desk must call by its shape,
        not by the window's hopes."""
        import numpy as np
        from . import audiocensus
        w = self.world
        # The channel is rendered at 48 ksps: the excerpt the
        # feed carries is bounded in samples, and at this rate
        # the bound holds a full two seconds of envelope - the
        # span burst morphology is judged over.
        fs = 48000.0
        seconds = 2.5
        n = int(fs * seconds)
        rng = _rng(w.seed, int(center_hz), int(t0), "jupiter")
        gen = np.random.default_rng(int(rng.random() * 1e9))
        t = np.arange(n) / fs
        iq = (gen.normal(0, 0.02, n)
              + 1j * gen.normal(0, 0.02, n)).astype(np.complex128)
        storm = w.jupiter_storm()
        in_storm = storm is not None and storm[0] < t1 \
            and t0 < storm[1] and 18e6 <= center_hz <= 28e6
        if in_storm:
            raw = (gen.normal(0, 1, n) + 1j * gen.normal(0, 1, n))
            spec = np.fft.fft(raw)
            fr = np.fft.fftfreq(n, 1.0 / fs)
            keep = np.abs(fr) <= 20e3
            burst = np.fft.ifft(np.where(keep, spec, 0.0))
            burst = burst / (np.max(np.abs(burst)) + 1e-9)
            if storm[3] == "S":
                gate = ((t % 0.09) < 0.02).astype(np.float64)
                gate *= (t < 2.2)
            else:
                gate = np.clip(np.sin(np.pi * t / seconds) ** 2
                               * 1.4, 0.0, 1.0)
            iq = iq + 0.85 * gate * burst
        elif rng.random() < 0.5:
            iq = iq + 0.4 * np.exp(2j * np.pi * 10e3 * t)
        exc = audiocensus._excerpt(iq, fs)
        return {"excerpt": exc,
                "span_note": "the simulated source renders the "
                             "window's most active span at "
                             "48 ksps, as the probe stare "
                             "renders its span"}

    def _meteor_feed(self, center_hz, bw_hz, t0, t1):
        """The meteor dwell on the simulated source: the world
        models the carrier watched for the whole dwell, so the
        sampled span is the dwell itself, stated as such."""
        pings = self.world.meteor_pings(t0, t1, 50.31e6)
        return {"pings": pings, "sampled_s": float(t1 - t0)}

    def _sonde_channels(self, t0, t1):
        """The sonde window on the simulated source (0.15.0):
        each flight in the window transmits real frames of the
        in-house format - the world's telemetry (position from
        its track, temperature and pressure from a standard
        lapse over its altitude) encoded and GFSK-modulated by
        the package's own codec, so the desk's decoder path is
        exercised end to end exactly as the real source
        exercises it."""
        import numpy as np
        from . import sigmf, sonde as sonde_mod
        w = self.world
        channels = []
        rate = 48000.0
        for flight in w.sonde_flights(t0, t1):
            parts = []
            heard = False
            for tt in (t0, (t0 + t1) / 2, t1 - 1):
                pos = w._sonde_pos(flight, tt)
                if pos is None:
                    continue
                heard = True
                lat, lon, alt = pos
                temp = 15.0 - 0.0065 * alt
                pressure = 1013.25 * (1 - 2.26e-5 * alt) ** 5.25
                humidity = max(5, int(60 - alt / 400))
                battery = 3.0 - (tt - flight["launch"]) / 7200 * 0.4
                payload = sonde_mod.encode_payload(
                    flight["serial"], lat, lon, alt, temp,
                    pressure, humidity, battery)
                parts.append(sonde_mod.modulate(payload, rate))
                parts.append(np.zeros(int(rate * 0.25),
                                      dtype=np.complex64))
            if not heard or not parts:
                continue
            iq = np.concatenate(parts).astype(np.complex128)
            rng = _rng(w.seed, int(flight["launch"]), "sonde")
            gen = np.random.default_rng(int(rng.random() * 1e9))
            iq = iq + 0.05 * (gen.normal(0, 1, len(iq))
                              + 1j * gen.normal(0, 1, len(iq)))
            data, peak = sigmf.data_bytes(list(iq))
            freq = 401.0e6 + (int(flight["serial"][1:]) % 3000) \
                * 1.5e3
            channels.append({
                "freq_hz": freq,
                "b64": base64.b64encode(data).decode(),
                "rate": rate, "peak": peak,
                "site": flight["site"]["name"]})
        return channels

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
