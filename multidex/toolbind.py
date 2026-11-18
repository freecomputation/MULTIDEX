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
}
