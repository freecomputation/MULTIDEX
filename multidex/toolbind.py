"""Bindings to external decoder programs the host may carry.

MULTIDEX is a standard-library build. A small number of desks
decode through a proven external program when the host carries
one - the WSJT-X jt9 decoder for FT8, dumpvdl2 for VDL2, DSD-FME
for digital-voice signalling. The binding is arms-length: the
program is invoked as a separate process over files, never
linked, never bundled. When the program is absent the desk
benches with the staging instruction as its reason, stated on
every surface that reports the bench. Nothing here pretends a
missing tool is present.

Every such binding is a dependence, and the dependence census
at the bottom of this module is the standing account of them:
the doctor prints it, and the test suite asserts it names
every program the package source invokes, so a binding cannot
be added quietly. An entry leaves the census only when an own
implementation retires it - as the package's own APT decoder
(multidex.apt) retired the aptdec binding in 0.5.0 - or when
the function it served is retired, as the METEOR
acknowledgement policy retired the satellite imagery decoder
binding in 0.6.0: METEOR passes are acknowledged only and
nothing is decoded for them, so no decoder is bound for them
at all.
"""

import shutil

_cache = {}


def tool_available(name):
    """True when the named program is on the PATH."""
    if name not in _cache:
        _cache[name] = shutil.which(name) is not None
    return _cache[name]


def reset_cache():
    _cache.clear()


# desk -> (program, plain-word staging instruction)
TOOL_DESKS = {
    "vdl2": ("dumpvdl2",
             "VDL2 decode binds the dumpvdl2 program (GPL-3.0, "
             "with the MIT libacars library), staged in user space; "
             "it is not installed on this host. Until it is staged, "
             "the VDL2 frequency is covered by a burst census and "
             "no VDL2 message decode is claimed"),
    "digitalvoice": ("dsd-fme",
                     "digital voice signalling binds the DSD-FME "
                     "program, staged in user space; it is not "
                     "installed on this host. Until it is staged, "
                     "trunked systems are censused as metadata by "
                     "the trunking desk and no DMR, P25 or NXDN "
                     "signalling decode is claimed"),
    "hfdl": ("dumphfdl",
             "HFDL decode binds the dumphfdl program (GPL-3.0, "
             "with the MIT libacars library), staged in user "
             "space; it is not installed on this host. Until it "
             "is staged, HF spectrum is censused by the sweep "
             "and audio census desks on every HF dwell and no "
             "HFDL decode is claimed"),
}


# ------------------------------------------------------ the census
#
# The dependence census: every external program this package
# can invoke, the desk or function that depends on it, and how
# its presence is probed - "path" programs are looked up on the
# PATH, "module" entries are host Python stacks probed by
# import. The table is the single source the doctor's census
# section is printed from, and the suite holds it honest from
# the other side: an AST scan of the package source collects
# every program the code actually invokes, and the two sets
# must be equal (tests/test_fixes_050.py). The operating system
# and its libraries are substrate, not dependences, and are not
# listed; the package's own worker processes are this package,
# not another person's program, and are not listed either.

# (display name, probe target, probe kind, used by)
DEPENDENCES = (
    ("rtl_sdr", "rtl_sdr", "path",
     "the tuner server - real-source IQ capture"),
    ("rtl_fm", "rtl_fm", "path",
     "the tuner server - real-source audio capture"),
    ("rtl_433", "rtl_433", "path",
     "the ISM desk feed"),
    ("multimon-ng", "multimon-ng", "path",
     "the pager, APRS and weather radio desk feeds"),
    ("jt9", "jt9", "path",
     "the amateur desk - FT8 decode"),
    ("dumpvdl2", "dumpvdl2", "path",
     "the VDL2 desk"),
    ("dsd-fme", "dsd-fme", "path",
     "the digital voice desk"),
    ("dumphfdl", "dumphfdl", "path",
     "the HFDL desk"),
    ("faster-whisper", "faster_whisper", "module",
     "transcription - the host faster-whisper stack"),
    ("chronyc", "chronyc", "path",
     "the muster clock gate"),
    ("pw-play", "pw-play", "path",
     "the FLASH chime"),
    ("paplay", "paplay", "path",
     "the FLASH chime"),
    ("aplay", "aplay", "path",
     "the FLASH chime"),
)


def dependence_census():
    """The census as data: one row per dependence, with its
    presence on this host probed the way the binding itself
    probes it."""
    import importlib.util
    rows = []
    for display, target, kind, used_by in DEPENDENCES:
        if kind == "module":
            present = importlib.util.find_spec(target) is not None
        else:
            present = tool_available(target)
        rows.append({"program": display, "used_by": used_by,
                     "present": present})
    return rows
