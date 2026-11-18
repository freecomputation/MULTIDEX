"""Muster gates and the doctor verb (design sections 6 and 11).

The fifth law made concrete: a watch starts only on verified footing,
and every failure is a refusal in plain words. Doctor runs the same
checks without the watch, plus the desk inventory against tiers.
"""

import os
import shutil
import subprocess
import time

from . import orbit, profile as profilemod
from .desks import (ALWAYS_BENCHED, SPECS, TRANSCRIPTION_REASON,
                    desk_state, transcription_state)
from .tuner import RealSource


def clock_offset_s():
    """Measured clock offset in seconds, or None when unverifiable.

    Uses chronyc when the host runs chrony. Without a time service the
    offset cannot be verified offline, and the gate says so rather
    than pretending.
    """
    if shutil.which("chronyc"):
        try:
            out = subprocess.run(["chronyc", "tracking"], capture_output=True,
                                 text=True, timeout=5).stdout
            for line in out.splitlines():
                if "System time" in line:
                    parts = line.split(":")[1].split()
                    return abs(float(parts[0]))
        except (subprocess.SubprocessError, OSError, ValueError,
                IndexError):
            return None
    return None


def load_sats(profile):
    path = None
    if profile.get("_dir"):
        path = os.path.join(profile["_dir"],
                            profile["advanced"]["tle_file"])
    if path and os.path.exists(path):
        with open(path) as fh:
            return orbit.parse_tle(fh.read()), path
    return [], path


def run_gates(profile, home, source, at_epoch=None):
    os.makedirs(home, exist_ok=True)
    """Returns [(name, ok, detail)] - the muster checklist."""
    at_epoch = at_epoch or time.time()
    gates = []
    ok, detail = source.available()
    gates.append(("tuner", ok, detail))
    offset = clock_offset_s()
    plausible = at_epoch > 1704067200  # after 2024-01-01
    if not plausible:
        gates.append(("clock", False,
                      "host clock reads before 2024; it is not true"))
    elif offset is None:
        gates.append(("clock", True,
                      "host clock plausible; offset could not be "
                      "verified on this host (no chrony), stated "
                      "rather than hidden"))
    elif offset > 1.0:
        gates.append(("clock", False,
                      "host clock is %.2f s off network time; the "
                      "muster limit is 1 s" % offset))
    else:
        gates.append(("clock", True,
                      "host clock within %.2f s of network time" % offset))
    ceiling = profile["watch"]["disk_ceiling_gb"] * 1e9
    free = shutil.disk_usage(home).free
    gates.append(("disk", free >= ceiling,
                  "%.1f GB free against a %.0f GB watch ceiling"
                  % (free / 1e9, ceiling / 1e9)))
    try:
        problems = profilemod.validate(profile)
    except profilemod.ProfileError as exc:
        problems = [str(exc)]
    gates.append(("profile", not problems,
                  "; ".join(problems) if problems else "profile valid"))
    lat, lon = profile["home"]["lat"], profile["home"]["lon"]
    gates.append(("home position", not (lat == 0 and lon == 0),
                  "home position set (kept local, never printed)"))
    sats, _path = load_sats(profile)
    age = orbit.tle_age_days(sats, at_epoch)
    if not sats:
        gates.append(("ephemeris", True,
                      "no orbital elements loaded; the satellite desk "
                      "benches for this watch"))
    elif age > 30:
        gates.append(("ephemeris", True,
                      "orbital elements are %.0f days old; over the "
                      "30-day rule, the satellite desk benches for "
                      "this watch" % age))
    elif age > 7:
        gates.append(("ephemeris", True,
                      "orbital elements are %.0f days old; pass "
                      "scheduling degraded and graded PROBABLE" % age))
    else:
        gates.append(("ephemeris", True,
                      "orbital elements %.1f days old" % max(age, 0.0)))
    return gates


def gates_passed(gates):
    return all(ok for _name, ok, _detail in gates)


def doctor_text(profile, home):
    from .desks import bench_reasons
    from .sim import SimSource
    fallback = None
    if profile["source"]["kind"] == "real":
        source = RealSource(profile)
        ok, detail = source.available()
        if ok:
            capabilities = source.capabilities()
        else:
            # The watch this profile stands would fall back to the
            # simulated source and state the substitution everywhere.
            # Doctor shows that footing, with the fallback stated in
            # the open, never as a bare tuner failure.
            fallback = detail
            source = SimSource(profile, time.time())
            capabilities = None
    else:
        source = SimSource(profile, time.time())
        capabilities = None
    lines = ["MULTIDEX doctor", "Checks:"]
    for name, ok, detail in run_gates(profile, home, source):
        if fallback and name == "tuner":
            detail = ("real source unavailable (%s); a watch on this "
                      "profile falls back to the simulated source, "
                      "and the substitution is stated in its muster "
                      "output, its status and every product's "
                      "coverage account" % fallback)
        lines.append("- %s: %s - %s" % (name, "PASS" if ok else "FAIL",
                                        detail))
    # One bench computation for every surface: capability benches,
    # the muster ephemeris bench, and the profile roster together,
    # so the doctor cannot disagree with a watch's own account.
    sats, _path = load_sats(profile)
    extra = {}
    if not sats:
        extra["satellite"] = "no orbital elements loaded at muster"
    else:
        age = orbit.tle_age_days(sats, time.time())
        if age is not None and age > 30:
            extra["satellite"] = ("orbital elements %.0f days old, "
                                  "over the 30-day rule" % age)
    benched = bench_reasons(profile, source.kind, capabilities, extra)
    lines.append("Desk inventory (tier, state, reason):")
    for name in sorted(SPECS):
        tier, service, _cpu, _ch = SPECS[name]
        if name in benched:
            state, reason = "benched", benched[name]
        else:
            state, reason = "standing", None
        line = "- %s (tier %d, %s): %s" % (name, tier, service, state)
        if reason:
            line += " - %s" % reason
        lines.append(line)
    _tstate, _treason = transcription_state()
    if _tstate == "bound":
        lines.append("- transcription: bound - the host faster-whisper "
                     "stack is importable; the bounded triage queue "
                     "governs what reaches it")
    else:
        lines.append("- transcription: benched - %s" % _treason)
    from .toolbind import tool_available as _ta
    if _ta("jt9"):
        lines.append("- amateur FT8: bound - the host jt9 program "
                     "decodes; the amateur desk files its lines")
    else:
        lines.append("- amateur FT8: benched - jt9 (WSJT-X) is not "
                     "on this host; the amateur desk still stands "
                     "for CW, and SSB voice on its windows is the "
                     "audio census desk's catch")
    if _ta("satdump"):
        lines.append("- satellite LRPT: bound - the host satdump "
                     "program decodes recorded METEOR IQ into "
                     "imagery after each pass")
    else:
        lines.append("- satellite LRPT: satdump is not staged on "
                     "this host; METEOR passes still record and "
                     "file their IQ, and their events state that "
                     "LRPT imagery was not decoded")
    lines.append("Source capabilities: %s"
                 % (", ".join(sorted(capabilities)) if capabilities
                    else "all desks (simulated source)"))
    # The reception budget's shape for this profile, computed on
    # a bill built from now: the same numbers muster will state,
    # so a short ceiling is visible before any watch stands.
    # Local imports: the officer imports this module.
    try:
        import time as _time
        from . import budget as _budget_mod
        from . import officer as _officer_mod
        _sats, _path = load_sats(profile)
        _bill = _officer_mod.build_bill(profile, _time.time(),
                                        _sats)
        _rb = _budget_mod.build_reception_budget(profile, _bill)
        _s = _rb.summary()
        lines.append("Reception budget for this profile: ceiling "
                     "%d bytes; a bill built from now allocates "
                     "%d bytes worst case, %d of it satellite "
                     "reserve, %d bytes free margin"
                     % (_s["ceiling_bytes"], _s["total_bytes"],
                        _s["satellite_reserve_bytes"],
                        _s["free_margin_bytes"]))
        for _statement in _s["degraded"]:
            lines.append("Reception budget degraded: %s"
                         % _statement)
    except Exception as exc:
        lines.append("Reception budget: could not be computed "
                     "(%s: %s)" % (type(exc).__name__, exc))
    return "\n".join(lines)
