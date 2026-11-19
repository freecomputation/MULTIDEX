"""The ephemeris (0.15.0): pure-Python positions for the sky desks.

Everything the astronomical patch predicts stands on this module:
the Sun's position, the station's sidereal time, Jupiter's
position with Io's orbital phase and the System III central
meridian longitude, and rise, transit and set for both. It is
stdlib only, by the independence law - no external program, no
data files, no network. Positions come from the JPL approximate
planetary elements (the "Approximate Positions of the Planets"
Table 1 elements and rates, J2000 mean ecliptic and equinox),
which JPL itself bounds at roughly 400 arcseconds of longitude
error for Jupiter over 1800-2050; the module states that class
of accuracy wherever it reports a position, and the tests prove
the implementation against published reference values inside
stated tolerances rather than claiming more.

Conventions, stated once and held everywhere in the patch:

- Times are UTC throughout. Terrestrial Time runs about 69 s
  ahead of UTC in this era; at Jupiter's rotation rate that is
  under a degree of central meridian, inside every tolerance
  this module is used at, and the Radio JOVE forecasting
  practice this module follows feeds its formulae UTC Julian
  days the same way.
- Jupiter's RA and Dec are astrometric J2000 coordinates,
  light-time corrected - the frame the published J2000
  ephemerides the tests check against use.
- Io's phase follows the Radio JOVE convention: 0 degrees at
  superior geocentric conjunction (Io behind Jupiter), 180
  degrees at inferior conjunction (Io transiting). Meeus's
  angle u1 is measured from inferior conjunction, so the phase
  here is u1 + 180.
- The System III central meridian follows the formulation in
  Jean Meeus's Astronomical Algorithms as published by Project
  Pluto and used by the Radio JOVE forecasting community (it
  is the formulation the jovian-noise forecaster implements):
  a linear term in the Julian day plus a geometric correction
  from Jupiter's equation of center and the Earth-Jupiter
  synodic angle. Validated in the tests against Fred Espenak's
  published geocentric ephemeris for Jupiter (the sub-Earth
  longitude column) within 7 degrees across the first quarter
  of 2025, and against the Radio JOVE worked example of the
  1999 October 23 storm, which it places inside the Io-B
  source box. The source boxes below are the ones that
  forecaster uses, themselves adapted from the University of
  Florida decametric source ranges.
"""

import math

J2000 = 2451545.0  # Julian day of 2000 Jan 1, 12:00
AU_LIGHT_S = 499.0047838  # light time across one AU, seconds

# JPL Table 1 elements and rates per Julian century:
# (a [AU], e, I [deg], L [deg], long. peri. [deg], long. node [deg])
_ELEMENTS = {
    "earth": ((1.00000261, 0.01671123, -0.00001531,
               100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668,
               35999.37244981, 0.32327364, 0.0)),
    "jupiter": ((5.20288700, 0.04838624, 1.30439695,
                 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714,
                 3034.74612775, 0.21252668, 0.20469106)),
}

# The decametric source boxes (CML III range, Io phase range),
# as used by the Radio JOVE forecasting community: Io-A is
# mostly L-bursts, Io-B mostly S-bursts, Io-C both, and the
# non-Io-A source is CML-governed alone. Ranges in degrees;
# a range whose low edge exceeds its high edge wraps past 360.
IO_SOURCES = (
    ("Io-A", (200.0, 270.0), (205.0, 260.0)),
    ("Io-B", (105.0, 185.0), (80.0, 110.0)),
    ("Io-C", (300.0, 20.0), (225.0, 260.0)),
    ("non-Io-A", (230.0, 280.0), None),
)


def julian_day(epoch):
    """Julian day for a Unix epoch (UTC)."""
    return epoch / 86400.0 + 2440587.5


def _solve_kepler(mean_anom_rad, ecc):
    e_anom = mean_anom_rad
    for _ in range(12):
        e_anom -= (e_anom - ecc * math.sin(e_anom) - mean_anom_rad) \
            / (1.0 - ecc * math.cos(e_anom))
    return e_anom


def _helio(body, jd):
    """Heliocentric ecliptic J2000 position of a body, in AU."""
    (a0, e0, i0, l0, w0, o0), (a1, e1, i1, l1, w1, o1) = _ELEMENTS[body]
    t = (jd - J2000) / 36525.0
    a = a0 + a1 * t
    ecc = e0 + e1 * t
    inc = math.radians(i0 + i1 * t)
    lon = (l0 + l1 * t) % 360.0
    peri = w0 + w1 * t
    node = math.radians(o0 + o1 * t)
    argp = math.radians(peri - (o0 + o1 * t))
    mean_anom = math.radians((lon - peri) % 360.0)
    e_anom = _solve_kepler(mean_anom, ecc)
    xp = a * (math.cos(e_anom) - ecc)
    yp = a * math.sqrt(1 - ecc * ecc) * math.sin(e_anom)
    cw, sw = math.cos(argp), math.sin(argp)
    ci, si = math.cos(inc), math.sin(inc)
    cn, sn = math.cos(node), math.sin(node)
    x = (cw * cn - sw * sn * ci) * xp + (-sw * cn - cw * sn * ci) * yp
    y = (cw * sn + sw * cn * ci) * xp + (-sw * sn + cw * cn * ci) * yp
    z = (sw * si) * xp + (cw * si) * yp
    return (x, y, z)


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


# ------------------------------------------------------------ sidereal

def gmst_deg(epoch):
    """Greenwich mean sidereal time in degrees (IAU expression,
    as in the Explanatory Supplement): the linear term in days
    from J2000 with the small quadratic corrections."""
    d = julian_day(epoch) - J2000
    t = d / 36525.0
    gmst = (280.46061837 + 360.98564736629 * d
            + 0.000387933 * t * t - t ** 3 / 38710000.0)
    return gmst % 360.0


def lst_deg(epoch, lon_deg):
    """Local sidereal time in degrees for an east longitude."""
    return (gmst_deg(epoch) + lon_deg) % 360.0


# ----------------------------------------------------------------- Sun

def sun_position(epoch):
    """The Sun's apparent geocentric position (Meeus's
    low-accuracy algorithm, good to about 0.01 degrees):
    ecliptic longitude, RA and Dec in degrees, distance in AU."""
    t = (julian_day(epoch) - J2000) / 36525.0
    mean_lon = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    m = math.radians(mean_anom)
    center = ((1.914602 - t * (0.004817 + 0.000014 * t)) * math.sin(m)
              + (0.019993 - 0.000101 * t) * math.sin(2 * m)
              + 0.000289 * math.sin(3 * m))
    true_lon = mean_lon + center
    omega = 125.04 - 1934.136 * t
    app_lon = true_lon - 0.00569 \
        - 0.00478 * math.sin(math.radians(omega))
    mean_obliq = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059
                             - t * 0.001813))) / 60.0) / 60.0
    obliq = mean_obliq + 0.00256 * math.cos(math.radians(omega))
    eps = math.radians(obliq)
    lam = math.radians(app_lon)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam),
                                 math.cos(lam))) % 360.0
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    dist = (1.000001018 * (1 - ecc * ecc)
            / (1 + ecc * math.cos(math.radians(
                mean_anom + center))))
    return {"ra_deg": ra, "dec_deg": dec,
            "ecliptic_lon_deg": app_lon % 360.0,
            "distance_au": dist}


# ------------------------------------------------------------- Jupiter

def jupiter_position(epoch):
    """Jupiter's geocentric position: astrometric J2000 RA and
    Dec in degrees, distance to Earth and to the Sun in AU,
    and the Sun-Jupiter-Earth phase angle in degrees. The
    geocentric vector is light-time iterated, as an ephemeris
    position must be."""
    jd = julian_day(epoch)
    earth = _helio("earth", jd)
    jup = _helio("jupiter", jd)
    dist = _dist(jup, earth)
    for _ in range(3):
        lt_days = dist * AU_LIGHT_S / 86400.0
        jup = _helio("jupiter", jd - lt_days)
        dist = _dist(jup, earth)
    geo = tuple(jup[i] - earth[i] for i in range(3))
    eps = math.radians(23.439291111)  # J2000 mean obliquity
    x = geo[0]
    y = geo[1] * math.cos(eps) - geo[2] * math.sin(eps)
    z = geo[1] * math.sin(eps) + geo[2] * math.cos(eps)
    ra = math.degrees(math.atan2(y, x)) % 360.0
    dec = math.degrees(math.atan2(z, math.hypot(x, y)))
    sun_dist = math.sqrt(sum(c * c for c in jup))
    # The phase angle at Jupiter, between the Sun and Earth
    # directions, from the same geometry.
    sun_dir = tuple(-c for c in jup)
    earth_dir = geo
    ns = math.sqrt(sum(c * c for c in sun_dir))
    cos_k = sum(sun_dir[i] * earth_dir[i] for i in range(3)) \
        / (ns * dist)
    phase = math.degrees(math.acos(max(-1.0, min(1.0, cos_k))))
    return {"ra_deg": ra, "dec_deg": dec, "distance_au": dist,
            "sun_distance_au": sun_dist, "phase_angle_deg": phase,
            "light_time_s": dist * AU_LIGHT_S}


def jupiter_cml3(epoch):
    """The System III central meridian longitude of Jupiter in
    degrees, by the Meeus formulation the Radio JOVE
    forecasting community uses (see the module docstring for
    its provenance and its validated tolerance)."""
    jd = julian_day(epoch)
    jup_mean = (jd - 2455636.938) * 360.0 / 4332.89709
    eqn_center = 5.55 * math.sin(math.radians(jup_mean))
    angle = (jd - 2451870.628) * 360.0 / 398.884 - eqn_center
    correction = (11.0 * math.sin(math.radians(angle))
                  + 5.0 * math.cos(math.radians(angle))
                  - 1.25 * math.cos(math.radians(jup_mean))
                  - eqn_center)
    return (138.41 + 870.4535567 * jd + correction) % 360.0


def io_phase(epoch):
    """Io's orbital phase in degrees, Radio JOVE convention:
    0 at superior geocentric conjunction, 180 at inferior.
    Meeus's u1 (Astronomical Algorithms, the Galilean satellite
    chapter), measured from inferior conjunction, with the
    light-time term and the phase-angle and equation-of-center
    corrections, converted by 180 degrees."""
    jd = julian_day(epoch)
    pos = jupiter_position(epoch)
    dist = pos["distance_au"]
    d = jd - J2000
    t = d / 36525.0
    (a0, e0, i0, l0, w0, o0), (a1, e1, i1, l1, w1, o1) = \
        _ELEMENTS["jupiter"]
    mean_anom_j = ((l0 + l1 * t) - (w0 + w1 * t)) % 360.0
    eqn_j = (5.555 * math.sin(math.radians(mean_anom_j))
             + 0.168 * math.sin(math.radians(2 * mean_anom_j)))
    # sin(psi) = (R / dist) * sin(phase angle), Meeus's psi
    # being the correction for Jupiter's phase geometry.
    psi = math.degrees(math.asin(
        (pos["sun_distance_au"] / dist)
        * math.sin(math.radians(pos["phase_angle_deg"]))))
    u1 = (163.8069 + 203.4058646 * (d - dist / 173.0)
          + psi - eqn_j) % 360.0
    return (u1 + 180.0) % 360.0


def _in_range(value, lo, hi):
    if lo <= hi:
        return lo <= value <= hi
    return value >= lo or value <= hi


def io_source(cml_deg, phase_deg):
    """The decametric source whose box holds this geometry, or
    None. Io-controlled sources are tested before the
    non-Io-A source, whose CML range overlaps Io-A's."""
    for name, (clo, chi), box in IO_SOURCES:
        if not _in_range(cml_deg, clo, chi):
            continue
        if box is None or _in_range(phase_deg, box[0], box[1]):
            return name
    return None


def jupiter_geometry(epoch):
    """Everything a desk or a panel needs about Jupiter at one
    instant: position, distance, CML III, Io phase, and the
    source the geometry favours (None when no box holds it)."""
    pos = jupiter_position(epoch)
    cml = jupiter_cml3(epoch)
    phase = io_phase(epoch)
    return {"ra_deg": pos["ra_deg"], "dec_deg": pos["dec_deg"],
            "distance_au": pos["distance_au"],
            "cml3_deg": cml, "io_phase_deg": phase,
            "source": io_source(cml, phase)}


# ------------------------------------------------------------- alt-az

def altaz(ra_deg, dec_deg, epoch, lat_deg, lon_deg):
    """Altitude and azimuth (from north, eastward) in degrees
    for an equatorial position over a station."""
    lst = lst_deg(epoch, lon_deg)
    ha = (lst - ra_deg + 180.0) % 360.0 - 180.0
    h = math.radians(ha)
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    alt = math.asin(math.sin(dec) * math.sin(lat)
                    + math.cos(dec) * math.cos(lat) * math.cos(h))
    az = math.atan2(
        math.sin(h),
        math.cos(h) * math.sin(lat) - math.tan(dec) * math.cos(lat))
    return (math.degrees(alt), (math.degrees(az) + 180.0) % 360.0)


def sun_altaz(epoch, lat_deg, lon_deg):
    pos = sun_position(epoch)
    return altaz(pos["ra_deg"], pos["dec_deg"], epoch,
                 lat_deg, lon_deg)


def jupiter_altaz(epoch, lat_deg, lon_deg):
    pos = jupiter_position(epoch)
    return altaz(pos["ra_deg"], pos["dec_deg"], epoch,
                 lat_deg, lon_deg)


# --------------------------------------------------------- rise / set

def _crossings(alt_fn, start, end, horizon_deg, step_s=300.0):
    """Zero crossings of (altitude - horizon) over [start, end],
    bisected to the second: [(epoch, 'rise'|'set'|'transit')].
    A transit is reported at the altitude maximum between a
    rise and a set when one exists inside the span."""
    out = []
    t = start
    prev_t, prev_v = start, alt_fn(start) - horizon_deg
    peak_t, peak_v = start, prev_v
    while t < end:
        t = min(end, t + step_s)
        v = alt_fn(t) - horizon_deg
        if v > peak_v:
            peak_t, peak_v = t, v
        if (prev_v < 0) != (v < 0):
            lo, hi = prev_t, t
            for _ in range(40):
                mid = (lo + hi) / 2.0
                if (alt_fn(mid) - horizon_deg < 0) == (prev_v < 0):
                    lo = mid
                else:
                    hi = mid
            cross = (lo + hi) / 2.0
            kind = "rise" if prev_v < 0 else "set"
            if kind == "set" and out and out[-1][1] == "rise":
                # The maximum between this rise and set, refined
                # by golden section on the bracket.
                a, b = out[-1][0], cross
                gr = (math.sqrt(5) - 1) / 2.0
                c = b - gr * (b - a)
                d2 = a + gr * (b - a)
                for _ in range(40):
                    if alt_fn(c) < alt_fn(d2):
                        a = c
                    else:
                        b = d2
                    c = b - gr * (b - a)
                    d2 = a + gr * (b - a)
                out.append(((a + b) / 2.0, "transit"))
            out.append((cross, kind))
            peak_t, peak_v = t, v
        prev_t, prev_v = t, v
    return out


def sun_rise_set(epoch_day_start, lat_deg, lon_deg):
    """Sunrise, transit and sunset for the UTC day beginning at
    epoch_day_start, at the conventional -0.583 degree horizon
    (refraction and the solar semidiameter). Any of the three
    may be absent on a polar day or night; the list says so by
    omission."""
    fn = lambda t: sun_altaz(t, lat_deg, lon_deg)[0]
    return _crossings(fn, epoch_day_start, epoch_day_start + 86400,
                      -0.583)


def jupiter_rise_set(epoch_day_start, lat_deg, lon_deg):
    """Rise, transit and set for Jupiter over the UTC day
    beginning at epoch_day_start, horizon 0 degrees."""
    fn = lambda t: jupiter_altaz(t, lat_deg, lon_deg)[0]
    return _crossings(fn, epoch_day_start, epoch_day_start + 86400,
                      0.0)


# ------------------------------------------------------------ windows

def jupiter_windows(lat_deg, lon_deg, start, end, min_alt_deg=10.0,
                    step_s=300.0):
    """Predicted decametric windows over [start, end]: spans
    where Jupiter stands above min_alt_deg AND the CML/Io
    geometry sits inside a source box. Contiguous favourable
    steps merge into one window; a window's source is the box
    holding the majority of its steps (the first, on a tie).
    Each window is a dict: start, end, source, and the geometry
    at its midpoint. Windows are predictions of favourable
    geometry, never of emission - the desk's own wording holds
    that line."""
    windows = []
    current = None
    t = start
    while t <= end:
        geo = jupiter_geometry(t)
        alt, _az = altaz(geo["ra_deg"], geo["dec_deg"], t,
                         lat_deg, lon_deg)
        favoured = geo["source"] is not None and alt >= min_alt_deg
        if favoured:
            if current is None:
                current = {"start": t, "end": t + step_s,
                           "votes": {geo["source"]: 1},
                           "samples": [(t, geo, alt)]}
            else:
                current["end"] = t + step_s
                current["votes"][geo["source"]] = \
                    current["votes"].get(geo["source"], 0) + 1
                current["samples"].append((t, geo, alt))
        else:
            if current is not None:
                windows.append(current)
                current = None
        t += step_s
    if current is not None:
        windows.append(current)
    out = []
    for w in windows:
        source = max(sorted(w["votes"]), key=lambda k: w["votes"][k])
        mid_t, mid_geo, mid_alt = w["samples"][len(w["samples"]) // 2]
        out.append({"start": w["start"], "end": w["end"],
                    "source": source,
                    "mid_cml3_deg": mid_geo["cml3_deg"],
                    "mid_io_phase_deg": mid_geo["io_phase_deg"],
                    "mid_alt_deg": mid_alt})
    return out


def next_jupiter_window(lat_deg, lon_deg, after, horizon_s=172800.0,
                        min_alt_deg=10.0):
    """The first predicted window beginning after `after`,
    searched over the next horizon_s seconds (48 hours by
    default), or None when the geometry offers none."""
    wins = jupiter_windows(lat_deg, lon_deg, after,
                           after + horizon_s,
                           min_alt_deg=min_alt_deg)
    for w in wins:
        if w["end"] > after:
            return w
    return None
