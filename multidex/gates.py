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
from .desks import ALWAYS_BENCHED, SPECS, TRANSCRIPTION_REASON, desk_state
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
    problems = profilemod.validate(profile)
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
    from .sim import SimSource
    if profile["source"]["kind"] == "real":
        source = RealSource(profile)
        capabilities = source.capabilities()
    else:
        source = SimSource(profile, time.time())
        capabilities = None
    lines = ["MULTIDEX doctor", "Checks:"]
    for name, ok, detail in run_gates(profile, home, source):
        lines.append("- %s: %s - %s" % (name, "PASS" if ok else "FAIL",
                                        detail))
    lines.append("Desk inventory (tier, state, reason):")
    for name in sorted(SPECS):
        tier, service, _cpu, _ch = SPECS[name]
        state, reason = desk_state(name, profile, profile["source"]["kind"],
                                   capabilities)
        line = "- %s (tier %d, %s): %s" % (name, tier, service, state)
        if reason:
            line += " - %s" % reason
        lines.append(line)
    lines.append("- transcription: benched - %s" % TRANSCRIPTION_REASON)
    lines.append("Source capabilities: %s"
                 % (", ".join(sorted(capabilities)) if capabilities
                    else "all desks (simulated source)"))
    return "\n".join(lines)
