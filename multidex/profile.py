"""Watch profiles: the whole configuration surface (design section 12).

A profile is a TOML file. The shipped profiles/default.toml carries the
same values as the built-in defaults below, so a missing or partial
profile still yields a complete, sane watch.

Validation is two-tier. Hard problems - values that would crash, hang
or corrupt a watch mid-run - raise ProfileError out of validate()
itself. Soft problems are returned as a list of plain-word strings;
load_profile refuses a profile with either kind, and the muster
profile gate reports them.
"""

import copy
import os
import tomllib

DEFAULTS = {
    "name": "default",
    "source": {"kind": "real", "seed": 20261117},
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
        "adsb":     {"weight": 16, "dwell_s": 120},
        "airband":  {"weight": 20, "dwell_s": 120},
        "milair":   {"weight": 10, "dwell_s": 90},
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
        "stand": ["adsb", "airband", "milair", "acars", "ais", "wxradio",
                  "ism", "aprs", "pager", "sweep", "satellite", "sonde",
                  "cellular", "trunking", "broadcast", "audiocensus",
                  "amateur", "iss", "vdl2", "digitalvoice"],
        "bench": [],
    },
    "flash": {
        "threshold_grade": "PROBABLE",
        "watch_emitters": [],
        "watch_freqs_hz": [121500000],
        "guard_hz": 121500000,
    },
    "alerts": {"chime": True},
    "updates": {
        "posture": "notify-only",
        "remote": "origin",
        "remote_url": "",
        "check_interval_hours": 6,
        "probation_items": 3,
        "pass_guard_minutes": 30,
        "freeze_warning_days": 14,
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
        "display_feed": True,
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


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _hard_problems(profile):
    """Problems that would crash, hang or corrupt a running watch.

    Every check is type-first: a malformed value is reported, never
    raised as a raw AttributeError, KeyError or ValueError.
    """
    hard = []
    watch = profile.get("watch", {})
    if not isinstance(watch, dict):
        return ["watch section is not a table"]
    sitrep = watch.get("sitrep_hours")
    if not _is_number(sitrep) or sitrep <= 0 or sitrep > 24:
        hard.append("watch sitrep_hours must be a number over 0 and "
                    "at most 24; a value of 0 or less would loop the "
                    "SITREP cadence forever")
    hours = watch.get("hours")
    if not _is_number(hours):
        hard.append("watch length must be a number of hours")
    ceiling = watch.get("disk_ceiling_gb")
    if not _is_number(ceiling):
        hard.append("disk ceiling must be a number of GB")
    for key in ("deep_sweep_start", "deep_sweep_end"):
        value = watch.get(key)
        if not _is_number(value) or not 0 <= value <= 24:
            hard.append("watch %s must be an hour of day, 0 to 24" % key)
    rotation = profile.get("rotation", {})
    if not isinstance(rotation, dict):
        hard.append("rotation section is not a table")
        rotation = {}
    if rotation:
        from .officer import ROTATION as _ROTATION_SLOTS
        for entry, spec in rotation.items():
            if entry not in _ROTATION_SLOTS:
                hard.append("rotation entry %s is not a known rotation "
                            "slot" % entry)
            if not isinstance(spec, dict):
                hard.append("rotation entry %s is not a table of "
                            "weight and dwell_s" % entry)
                continue
            weight = spec.get("weight")
            if not _is_number(weight) or weight < 0:
                hard.append("rotation entry %s has a weight that is "
                            "not a number at least 0" % entry)
            dwell = spec.get("dwell_s")
            if not _is_number(dwell) or dwell <= 0:
                hard.append("rotation entry %s has a dwell_s that is "
                            "not a positive number" % entry)
    from .desks import SPECS as _DESKS
    desks = profile.get("desks", {})
    if isinstance(desks, dict):
        for section in ("stand", "bench"):
            names = desks.get(section, [])
            if not isinstance(names, list):
                hard.append("desks %s is not a list of desk names"
                            % section)
                continue
            for name in names:
                if name not in _DESKS:
                    hard.append("desks %s names %r, which is not a "
                                "known desk" % (section, name))
    transcription = profile.get("transcription", {})
    if isinstance(transcription, dict):
        for name in transcription.get("desks", []) or []:
            if name not in _DESKS:
                hard.append("transcription desks names %r, which is "
                            "not a known desk" % (name,))
    excluded = profile.get("excluded", {})
    bands = excluded.get("bands", []) if isinstance(excluded, dict) \
        else []
    if not isinstance(bands, list):
        hard.append("excluded bands is not a list of [low, high] pairs")
    else:
        for band in bands:
            if not isinstance(band, (list, tuple)) or len(band) != 2 \
                    or not _is_number(band[0]) \
                    or not _is_number(band[1]) or band[0] >= band[1]:
                hard.append("excluded band %r is not a numeric "
                            "[low, high] pair with low below high"
                            % (band,))
    flash = profile.get("flash", {})
    if isinstance(flash, dict):
        freqs = flash.get("watch_freqs_hz", [])
        if not isinstance(freqs, list) or any(
                not _is_number(f) for f in freqs):
            hard.append("flash watch_freqs_hz must be a list of "
                        "numbers, in Hz")
        grade = flash.get("threshold_grade")
        if grade not in ("CONFIRMED", "PROBABLE", "UNRESOLVED"):
            hard.append("flash threshold grade is not a confidence "
                        "grade")
        guard = flash.get("guard_hz")
        if guard is not None and not _is_number(guard):
            hard.append("flash guard_hz must be a number, in Hz")
    sondes = profile.get("sondes", {})
    if isinstance(sondes, dict):
        sites = sondes.get("sites", [])
        if not isinstance(sites, list):
            hard.append("sonde sites is not a list of sites")
        else:
            for site in sites:
                if not isinstance(site, dict) \
                        or not _is_number(site.get("lat")) \
                        or not _is_number(site.get("lon")):
                    hard.append("sonde site %r does not carry numeric "
                                "lat and lon"
                                % (site.get("name") if isinstance(
                                    site, dict) else site,))
                    continue
                radius = site.get("radius_km", site.get("radius"))
                if radius is not None and (not _is_number(radius)
                                           or radius <= 0):
                    hard.append("sonde site %r has a radius that is "
                                "not a positive number"
                                % (site.get("name"),))
        synoptic = sondes.get("synoptic_hours", [])
        if not isinstance(synoptic, list) or any(
                not _is_number(h) or not 0 <= h <= 23
                for h in synoptic):
            hard.append("sonde synoptic_hours must be hours of day, "
                        "0 to 23")
    snippets = profile.get("snippets", {})
    if isinstance(snippets, dict):
        for key in ("iq_max_per_watch", "voice_cap_per_desk"):
            value = snippets.get(key)
            if value is not None and (not _is_number(value)
                                      or value < 0):
                hard.append("snippets %s must be a number at least 0"
                            % key)
    updates = profile.get("updates", {})
    if isinstance(updates, dict):
        posture = updates.get("posture")
        if posture not in ("automatic", "notify-only", "off"):
            hard.append("updates posture must be 'automatic', "
                        "'notify-only' or 'off'")
        for key in ("check_interval_hours", "probation_items",
                    "pass_guard_minutes", "freeze_warning_days"):
            value = updates.get(key)
            if not _is_number(value) or value <= 0:
                hard.append("updates %s must be a positive number"
                            % key)
        remote = updates.get("remote")
        if not isinstance(remote, str) or not remote:
            hard.append("updates remote must name a git remote")
        remote_url = updates.get("remote_url")
        if remote_url is not None \
                and not isinstance(remote_url, str):
            hard.append("updates remote_url must be a string")
    advanced = profile.get("advanced", {})
    if isinstance(advanced, dict) and "time_scale" in advanced:
        scale = advanced.get("time_scale")
        if not _is_number(scale) or scale <= 0:
            hard.append("advanced time_scale must be a positive "
                        "number; %r would break the watch clock"
                        % (scale,))
    return hard


def validate(profile):
    """Check a profile; return the list of soft problems.

    Hard problems (values that would crash, hang or corrupt a watch)
    raise ProfileError instead of returning. Nothing else is ever
    raised: every check is written type-first so a malformed value
    is reported in plain words.
    """
    hard = _hard_problems(profile)
    if hard:
        raise ProfileError("; ".join(hard))
    problems = []
    home = profile.get("home", {})
    lat, lon = home.get("lat"), home.get("lon")
    if not _is_number(lat) or not -90.0 <= lat <= 90.0:
        problems.append("home latitude is missing or outside -90 to 90")
    if not _is_number(lon) or not -180.0 <= lon <= 180.0:
        problems.append("home longitude is missing or outside -180 to 180")
    if lat == 0 and lon == 0:
        problems.append("home position is unset (0, 0); satellite and "
                        "sonde work is impossible without it")
    hours = profile.get("watch", {}).get("hours")
    if not _is_number(hours) or hours <= 0:
        problems.append("watch length must be a positive number of hours")
    ceiling = profile.get("watch", {}).get("disk_ceiling_gb")
    if not _is_number(ceiling) or ceiling <= 0:
        problems.append("disk ceiling must be a positive number of GB")
    kind = profile.get("source", {}).get("kind")
    if kind not in ("simulated", "real"):
        problems.append("source kind must be 'simulated' or 'real'")
    chime = profile.get("alerts", {}).get("chime")
    if chime is not None and not isinstance(chime, bool):
        problems.append("alerts chime must be true or false")
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
    """Watch seconds per real second: profile knob, env override wins.

    A malformed MULTIDEX_TIME_SCALE is a ProfileError, never silently
    ignored: a rehearsal knob the operator set must not be dropped
    on the floor. A scale at or below zero is refused the same way.
    """
    env = os.environ.get("MULTIDEX_TIME_SCALE")
    if env:
        try:
            value = float(env)
        except (TypeError, ValueError):
            raise ProfileError(
                "MULTIDEX_TIME_SCALE is set to %r, which is not a "
                "number; the watch refuses to start on a time scale "
                "it cannot read" % (env,))
        if value <= 0:
            raise ProfileError(
                "MULTIDEX_TIME_SCALE is set to %r; the time scale "
                "must be a positive number" % (env,))
        return value
    raw = profile["advanced"].get("time_scale", 1.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ProfileError(
            "advanced time_scale is %r, which is not a number"
            % (raw,))
    if value <= 0:
        raise ProfileError(
            "advanced time_scale is %r; the time scale must be a "
            "positive number" % (raw,))
    return value
