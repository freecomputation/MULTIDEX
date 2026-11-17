"""Watch profiles: the whole configuration surface (design section 12).

A profile is a TOML file. The shipped profiles/default.toml carries the
same values as the built-in defaults below, so a missing or partial
profile still yields a complete, sane watch.
"""

import copy
import os
import tomllib

DEFAULTS = {
    "name": "default",
    "source": {"kind": "simulated", "seed": 20261117},
    "home": {
        "lat": 39.0, "lon": -98.0, "alt_m": 300,
        "region": "the central United States",
        "antenna": "general coverage antenna",
    },
    "watch": {
        "hours": 24, "sitrep_hours": 6,
        "deep_sweep_start": 1, "deep_sweep_end": 4,
        "disk_ceiling_gb": 20,
    },
    "rotation": {
        "adsb":     {"weight": 10, "dwell_s": 150},
        "airband":  {"weight": 9,  "dwell_s": 600},
        "datalink": {"weight": 6,  "dwell_s": 300},
        "marine":   {"weight": 6,  "dwell_s": 300},
        "aprs":     {"weight": 4,  "dwell_s": 240},
        "ism433":   {"weight": 5,  "dwell_s": 240},
        "ism915":   {"weight": 4,  "dwell_s": 240},
        "pager":    {"weight": 4,  "dwell_s": 300},
        "cellular": {"weight": 3,  "dwell_s": 240},
        "trunking": {"weight": 3,  "dwell_s": 240},
        "fmband":   {"weight": 2,  "dwell_s": 180},
        "hf_day":   {"weight": 4,  "dwell_s": 300},
        "hf_night": {"weight": 4,  "dwell_s": 300},
    },
    "desks": {
        "stand": ["adsb", "airband", "acars", "ais", "wxradio", "ism",
                  "aprs", "pager", "sweep", "satellite", "sonde",
                  "cellular", "trunking", "broadcast"],
        "bench": [],
    },
    "flash": {
        "threshold_grade": "PROBABLE",
        "watch_emitters": [],
        "watch_freqs_hz": [121500000],
        "guard_hz": 121500000,
    },
    "snippets": {
        "iq_max_per_watch": 100,
        "voice_cap_per_desk": 200,
        "satellite_keep": True,
    },
    "transcription": {"desks": ["airband"], "cap_per_watch": 40},
    "excluded": {"bands": []},
    "sondes": {
        "sites": [
            {"name": "North site", "lat": 40.2, "lon": -98.3},
            {"name": "South site", "lat": 37.8, "lon": -97.6},
        ],
        "synoptic_hours": [0, 12],
    },
    "advanced": {
        "gain_db": {"default": 23},
        "tle_file": "tle.txt",
        "interrupt_cap_per_hour": 3,
        "time_scale": 1.0,
        "tuner_outages": [],
    },
}


class ProfileError(Exception):
    """A profile problem, stated in plain words."""


def _merge(base, over):
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def validate(profile):
    """Return a list of plain-word problems; empty means the profile stands."""
    problems = []
    home = profile.get("home", {})
    lat, lon = home.get("lat"), home.get("lon")
    if not isinstance(lat, (int, float)) or not -90.0 <= lat <= 90.0:
        problems.append("home latitude is missing or outside -90 to 90")
    if not isinstance(lon, (int, float)) or not -180.0 <= lon <= 180.0:
        problems.append("home longitude is missing or outside -180 to 180")
    if lat == 0 and lon == 0:
        problems.append("home position is unset (0, 0); satellite and "
                        "sonde work is impossible without it")
    hours = profile.get("watch", {}).get("hours")
    if not isinstance(hours, (int, float)) or hours <= 0:
        problems.append("watch length must be a positive number of hours")
    ceiling = profile.get("watch", {}).get("disk_ceiling_gb")
    if not isinstance(ceiling, (int, float)) or ceiling <= 0:
        problems.append("disk ceiling must be a positive number of GB")
    kind = profile.get("source", {}).get("kind")
    if kind not in ("simulated", "real"):
        problems.append("source kind must be 'simulated' or 'real'")
    for entry, spec in profile.get("rotation", {}).items():
        if spec.get("weight", 0) < 0 or spec.get("dwell_s", 0) <= 0:
            problems.append("rotation entry %s has a bad weight or dwell"
                            % entry)
    for band in profile.get("excluded", {}).get("bands", []):
        if len(band) != 2 or band[0] >= band[1]:
            problems.append("excluded band %r is not a [low, high] pair"
                            % (band,))
    grade = profile.get("flash", {}).get("threshold_grade")
    if grade not in ("CONFIRMED", "PROBABLE", "UNRESOLVED"):
        problems.append("flash threshold grade is not a confidence grade")
    return problems


def load_profile(path=None):
    """Load a profile TOML over the built-in defaults and validate it."""
    data = {}
    profile_dir = None
    if path:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        profile_dir = os.path.dirname(os.path.abspath(path))
    profile = _merge(DEFAULTS, data)
    profile["_dir"] = profile_dir
    problems = validate(profile)
    if problems:
        raise ProfileError("; ".join(problems))
    return profile


def time_scale(profile):
    """Watch seconds per real second: profile knob, env override wins."""
    env = os.environ.get("MULTIDEX_TIME_SCALE")
    if env:
        try:
            value = float(env)
            if value > 0:
                return value
        except ValueError:
            pass
    return float(profile["advanced"].get("time_scale", 1.0))
