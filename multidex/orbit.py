"""Orbital mechanics for the satellite desk: TLE parsing, a simplified
Keplerian propagator, and pass prediction.

The propagator is deliberately simple: two-body Kepler motion on a
spherical Earth, no J2, no drag. That is good enough to schedule a
dwell minutes wide, and no better than that - so pass predictions are
graded PROBABLE everywhere they are used, per the honesty law. This is
a scheduling instrument, not an ephemeris.
"""

import math
import time

MU = 398600.4418  # km^3 / s^2
EARTH_R = 6371.0  # km, spherical


def _epoch_seconds(year2, day_of_year):
    year = 2000 + year2 if year2 < 57 else 1900 + year2
    base = time.mktime(time.struct_time(
        (year, 1, 1, 0, 0, 0, 0, 1, 0))) - time.timezone
    return base + (day_of_year - 1) * 86400.0


def parse_tle(text):
    """Parse two-line elements into satellite dicts."""
    sats = []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()
             and not ln.lstrip().startswith("#")]
    i = 0
    pending_name = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("1 ") and i + 1 < len(lines):
            l1, l2 = line, lines[i + 1]
            f2 = l2.split()
            year2 = int(l1[18:20])
            day = float(l1[20:32])
            sats.append({
                "name": pending_name or ("NORAD " + l1[2:7].strip()),
                "norad": l1[2:7].strip(),
                "epoch": _epoch_seconds(year2, day),
                "incl_deg": float(f2[2]),
                "raan_deg": float(f2[3]),
                "ecc": int(f2[4]) / 1e7,
                "argp_deg": float(f2[5]),
                "mean_anom_deg": float(f2[6]),
                "mean_motion": float(f2[7]),
            })
            pending_name = None
            i += 2
        else:
            pending_name = line.strip()
            i += 1
    return sats


def tle_age_days(sats, at_epoch):
    if not sats:
        return None
    newest = max(s["epoch"] for s in sats)
    return (at_epoch - newest) / 86400.0


def _gmst_deg(epoch):
    jd = epoch / 86400.0 + 2440587.5
    return (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0


def propagate(sat, epoch):
    """Sub-satellite point (lat_deg, lon_deg, alt_km) at an epoch."""
    n = sat["mean_motion"] * 2 * math.pi / 86400.0
    a = (MU / (n * n)) ** (1.0 / 3.0)
    e = sat["ecc"]
    dt = epoch - sat["epoch"]
    M = math.radians(sat["mean_anom_deg"]) + n * dt
    E = M
    for _ in range(12):
        E = E - (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2),
                        math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))
    u = math.radians(sat["argp_deg"]) + nu
    raan = math.radians(sat["raan_deg"])
    incl = math.radians(sat["incl_deg"])
    x = r * (math.cos(raan) * math.cos(u)
             - math.sin(raan) * math.sin(u) * math.cos(incl))
    y = r * (math.sin(raan) * math.cos(u)
             + math.cos(raan) * math.sin(u) * math.cos(incl))
    z = r * (math.sin(u) * math.sin(incl))
    theta = math.radians(_gmst_deg(epoch))
    xe = x * math.cos(theta) + y * math.sin(theta)
    ye = -x * math.sin(theta) + y * math.cos(theta)
    lat = math.degrees(math.atan2(z, math.hypot(xe, ye)))
    lon = math.degrees(math.atan2(ye, xe))
    return lat, lon, r - EARTH_R


def look_angles(sat, epoch, home_lat, home_lon, home_alt_m=0):
    """(elevation_deg, azimuth_deg, range_km) of a satellite from home."""
    lat, lon, alt_km = propagate(sat, epoch)
    phi1, phi2 = math.radians(home_lat), math.radians(lat)
    dl = math.radians(lon - home_lon)
    # Observer and satellite ECEF on a spherical Earth.
    ro = EARTH_R + home_alt_m / 1000.0
    ox = ro * math.cos(phi1) * math.cos(math.radians(home_lon))
    oy = ro * math.cos(phi1) * math.sin(math.radians(home_lon))
    oz = ro * math.sin(phi1)
    rs = EARTH_R + alt_km
    sx = rs * math.cos(phi2) * math.cos(math.radians(lon))
    sy = rs * math.cos(phi2) * math.sin(math.radians(lon))
    sz = rs * math.sin(phi2)
    dx, dy, dz = sx - ox, sy - oy, sz - oz
    rng = math.hypot(dx, dy, dz)
    # ENU basis at the observer.
    sp, cp = math.sin(phi1), math.cos(phi1)
    sl, cl = math.sin(math.radians(home_lon)), math.cos(math.radians(home_lon))
    east = -sl * dx + cl * dy
    north = -sp * cl * dx - sp * sl * dy + cp * dz
    up = cp * cl * dx + cp * sl * dy + sp * dz
    elev = math.degrees(math.asin(up / rng)) if rng else -90.0
    azim = math.degrees(math.atan2(east, north)) % 360.0
    return elev, azim, rng


def predict_passes(sat, home_lat, home_lon, t0, t1, min_elev_deg=10.0,
                   step_s=20.0, home_alt_m=0):
    """All passes above min elevation in [t0, t1], PROBABLE grade data."""
    passes = []
    t = t0
    current = None
    while t <= t1:
        elev, azim, rng = look_angles(sat, t, home_lat, home_lon, home_alt_m)
        if elev >= min_elev_deg:
            if current is None:
                current = {"sat": sat["name"], "norad": sat["norad"],
                           "aos": t, "los": t, "t_max": t,
                           "max_elev_deg": elev, "aos_az_deg": azim,
                           "los_az_deg": azim, "min_range_km": rng}
            else:
                current["los"] = t
                current["los_az_deg"] = azim
                if elev > current["max_elev_deg"]:
                    current["max_elev_deg"] = elev
                    current["t_max"] = t
                current["min_range_km"] = min(current["min_range_km"], rng)
        elif current is not None:
            passes.append(current)
            current = None
        t += step_s
    if current is not None:
        passes.append(current)
    return passes


def all_passes(sats, home_lat, home_lon, t0, t1, **kw):
    out = []
    for sat in sats:
        out.extend(predict_passes(sat, home_lat, home_lon, t0, t1, **kw))
    out.sort(key=lambda p: p["aos"])
    return out
