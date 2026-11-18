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
    # Applicability is judged from the same one evaluation the
    # muster uses, so doctor's account cannot disagree with a
    # watch's: stood down at this home is a state of its own,
    # distinct from benched on this host.
    from . import applicability as _applicability
    decisions = _applicability.evaluate(profile)
    lines.append("Desk inventory (tier, state, reason):")
    for name in sorted(SPECS):
        tier, service, _cpu, _ch = SPECS[name]
        if name in benched:
            state, reason = "benched", benched[name]
        elif not decisions[name]["stands"]:
            state, reason = "stood down", decisions[name]["reason"]
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
    lines.append("- satellite: NOAA APT passes are recorded "
                 "whole and decoded by the package's own APT "
                 "decoder; METEOR passes are acknowledged only "
                 "under the acknowledgement policy - not "
                 "recorded, carrying a zero-byte allocation, "
                 "and holding no reservation or grant")
    # The dependence census: every external program this build
    # invokes, printed from the binding table itself so the
    # account cannot drift from the code. Each entry is a
    # dependence until an own implementation retires it.
    from .toolbind import dependence_census
    lines.append("Dependences (external programs this build "
                 "invokes; each is a dependence until an own "
                 "implementation retires it):")
    for dep in dependence_census():
        lines.append("- %s: used by %s - %s - dependence"
                     % (dep["program"], dep["used_by"],
                        "present" if dep["present"] else "absent"))
    lines.append("Source capabilities: %s"
                 % (", ".join(sorted(capabilities)) if capabilities
                    else "all desks (simulated source)"))
    # The standing allocation pool for this home, read from its
    # ledger: a property of the installation, stated the same
    # whether or not a watch stands. Doctor also refreshes the
    # standing satellite reservations from the ephemeris, so
    # the passes ahead hold their reservations between watches.
    # Local imports: the officer imports this module.
    try:
        import time as _time
        from . import budget as _budget_mod
        _ledger = _budget_mod.AllocationLedger.load(home)
        for _statement in _ledger.load_statements:
            lines.append("Reception pool: %s" % _statement)
        _sats, _path = load_sats(profile)
        _created, _expired = _budget_mod.refresh_reserves(
            _ledger, profile, _sats, _time.time())
        _ledger.set_cap(_budget_mod.cap_bytes(profile))
        _p = _ledger.pool()
        lines.append("Reception pool for this home: standing "
                     "cap %d bytes; %d consumed, %d reserved in "
                     "%d open grants (%d of it satellite "
                     "reservations), %d free; a margin of %d "
                     "bytes is never granted"
                     % (_p["cap_bytes"], _p["consumed_bytes"],
                        _p["reserved_bytes"], _p["open_grants"],
                        _p["satellite_reserve_bytes"],
                        _p["free_bytes"] or 0, _p["margin_bytes"]))
        if _created:
            lines.append("Standing satellite reservations: %d "
                         "new reservations taken for passes in "
                         "the next 48 hours" % _created)
        if _expired:
            lines.append("Standing satellite reservations: %d "
                         "reservations expired unclaimed and "
                         "were released" % _expired)
    except Exception as exc:
        lines.append("Reception pool: could not be read "
                     "(%s: %s)" % (type(exc).__name__, exc))
    # The retention law (0.10.0): the tier policy stated once,
    # plainly, and this home's evidence accounted by tier from
    # the snippet store when the home has one.
    from . import retention as _retention
    lines.append("Retention: %s" % _retention.POLICY)
    try:
        import os as _os
        if _os.path.exists(_os.path.join(home, "index.sqlite")):
            from .index import Index as _Index
            _idx = _Index(home)
            try:
                _acct = _retention.account(_idx.snippet_rows_all())
            finally:
                _idx.close()
            lines.append("Evidence by tier in this home: "
                         + "; ".join(
                             "tier %d (%s) %d bytes held, %d shed"
                             % (_t, _retention.TIER_NAMES[_t],
                                _acct[_t]["kept_bytes"],
                                _acct[_t]["shed_bytes"])
                             for _t in (0, 1, 2, 3))
                         + "; sealed %d bytes held apart, never shed."
                         % _acct["sealed"]["bytes"])
    except Exception as exc:
        lines.append("Evidence by tier: could not be read "
                     "(%s: %s)" % (type(exc).__name__, exc))
    # Retroactive exploitation (0.12.0): the capability
    # manifest stated from the registry itself, and this
    # home's account against it - what the current decoder
    # versions have examined, what they found late, and what
    # stands owed.
    from . import retro as _retro_mod
    lines.append("Retroactive exploitation: every decoder "
                 "improvement is owed to the archive. The "
                 "registered decoders are:")
    lines.extend(_retro_mod.manifest_lines())
    try:
        import os as _os
        if _os.path.exists(_os.path.join(home, "index.sqlite")):
            from .index import Index as _Index
            _idx = _Index(home)
            try:
                _ledger = _retro_mod.ProcessedLedger.load(home)
                _owed = _retro_mod.owed_counts(_idx)
                for _entry in _retro_mod.manifest():
                    _counts = _ledger.counts(_entry["name"],
                                             _entry["version"])
                    _found = len(_ledger.found_event_ids(
                        _entry["name"], _entry["version"]))
                    lines.append(
                        "Retroactive account, %s version %s: "
                        "%d captures examined by this version "
                        "(%d filed retroactive events, %d "
                        "already exploited, %d errors); %d "
                        "captures stand owed to it."
                        % (_entry["name"], _entry["version"],
                           sum(_counts.values()), _found,
                           _counts.get(_retro_mod.ALREADY, 0),
                           _counts.get(_retro_mod.ERROR, 0),
                           _owed.get(_entry["name"], 0)))
            finally:
                _idx.close()
    except Exception as exc:
        lines.append("Retroactive account: could not be read "
                     "(%s: %s)" % (type(exc).__name__, exc))
    from . import updater as _updater
    lines.extend(_updater.doctor_lines(home, profile))
    return "\n".join(lines)
