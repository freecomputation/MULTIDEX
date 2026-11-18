"""The Console's keep-awake hold (0.8.0).

Under the --keep-awake flag the Console holds the display awake
while it runs and releases the hold the moment it exits. There
is no terminal-layer mechanism for this: curses can draw, it
cannot speak to the host's power management. The honest
mechanism is the host's own inhibitor program, held as a child
process for the Console's lifetime:

- systemd-inhibit on a systemd host, holding an idle lock while
  it runs this package's own interpreter asleep; the lock dies
  with the child.
- caffeinate on a Mac, with -d for the display and -w on the
  Console's own pid, so the assertion releases itself when the
  Console exits even if the release below never runs.

Both are external programs, and both therefore stand in the
dependence census in toolbind.py, declared like every other
binding: the census test reads them from this module's own
lookups. Where neither program exists the hold reports itself
unheld in plain words, and the Console prints that state in its
header rather than pretending the screen is held. This module
never raises: a failed hold is a stated fact, not an error.
"""

import os
import shutil
import subprocess
import sys

# Probed in order; the first the host carries is the hold.
MECHANISMS = ("systemd-inhibit", "caffeinate")


class Hold:
    """One keep-awake hold: a child process, or the plain-words
    reason there is none."""

    def __init__(self):
        self.program = None
        self._child = None
        self.state = ("not held: no keep-awake program (%s) is "
                      "present on this host"
                      % ", ".join(MECHANISMS))

    def alive(self):
        """True while the hold stands. A child that exited on
        its own ends the hold, and the state says so."""
        if self._child is None:
            return False
        if self._child.poll() is None:
            return True
        self.state = ("not held: the keep-awake program %s "
                      "exited" % self.program)
        self._child = None
        return False

    def release(self):
        """Free the hold. Idempotent, and never raises."""
        child, self._child = self._child, None
        if child is not None:
            try:
                child.terminate()
                child.wait(timeout=3)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        if self.program is not None:
            self.state = "released"


def acquire():
    """Take the hold with the first mechanism the host carries.
    Returns a Hold in every case; check .state, or .alive()."""
    hold = Hold()
    for name in MECHANISMS:
        exe = shutil.which(name)
        if exe is None:
            continue
        try:
            if name == "systemd-inhibit":
                child = subprocess.Popen(
                    [exe, "--what=idle", "--who=MULTIDEX console",
                     "--why=the operator is watching the console",
                     "--mode=block", sys.executable, "-c",
                     "import time; time.sleep(86400)"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            else:  # caffeinate
                child = subprocess.Popen(
                    [exe, "-d", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        hold.program = name
        hold._child = child
        hold.state = "held via %s" % name
        return hold
    return hold
