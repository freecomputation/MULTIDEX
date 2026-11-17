"""Small shared helpers: time, ids, JSON, and the output hygiene guard."""

import json
import os
import time
import uuid

EM_DASH = "—"
EN_DASH = "–"


def assert_clean(text):
    """Products and docs carry no em dash characters. Fail loudly."""
    if EM_DASH in text:
        raise ValueError("em dash character found in output")
    return text


def new_id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def fmt_hz(hz):
    """Human frequency: 1090000000 -> '1090.000 MHz'."""
    if hz is None:
        return "unknown frequency"
    if hz >= 1_000_000:
        return "%.3f MHz" % (hz / 1e6)
    if hz >= 1_000:
        return "%.1f kHz" % (hz / 1e3)
    return "%d Hz" % int(hz)


def fmt_time(epoch):
    """UTC clock time for an epoch second."""
    return time.strftime("%H:%M:%S", time.gmtime(epoch))


def fmt_stamp(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def pid_alive(pid):
    """True when a process with this pid exists on this host.

    Used for watch liveness: a state file's pid is the officer that
    wrote it, and a dead pid means the watch is not running, however
    its state file reads. PermissionError means the process exists
    but is owned by someone else: alive.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_text(path, text):
    assert_clean(text)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)
