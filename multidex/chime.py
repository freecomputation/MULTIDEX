"""The FLASH chime: a two-tone alert sounded on the host (fix 0.1.1).

The WAV is generated with the standard library at first use and
cached in the watch folder. Playback tries pw-play, then paplay, then
aplay. A chime is a courtesy beside the FLASH file, never a gate on
it: this module never raises, and its outcome is recorded in the
FLASH event's data by the caller - played, or not played and why.
"""

import math
import os
import shutil
import struct
import subprocess
import wave

RATE = 22050
TONES = [(987.77, 0.22), (1318.51, 0.34)]  # B5 then E6, seconds
AMP = 0.45
PLAYERS = ("pw-play", "paplay", "aplay")


def ensure_wav(path):
    """Generate the two-tone chime WAV at path if it is not there."""
    if os.path.exists(path):
        return path
    frames = bytearray()
    for freq, dur in TONES:
        n = int(RATE * dur)
        for i in range(n):
            t = i / RATE
            env = min(1.0, i / (RATE * 0.02)) * math.exp(-2.2 * t / dur)
            v = int(32767 * AMP * env * math.sin(2 * math.pi * freq * t))
            frames += struct.pack("<h", v)
        frames += b"\x00\x00" * int(RATE * 0.05)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path


def play_chime(watch_dir):
    """Sound the chime once. Returns the outcome in plain words.

    Never raises and never blocks for long: a missing player, a failed
    player, or an unwritable watch folder all come back as a recorded
    outcome, not an error.
    """
    try:
        wav = ensure_wav(os.path.join(watch_dir, "chime.wav"))
    except OSError as exc:
        return "not played: the chime file could not be written (%s)" % exc
    for name in PLAYERS:
        exe = shutil.which(name)
        if exe is None:
            continue
        try:
            done = subprocess.run([exe, wav], timeout=10,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
            if done.returncode == 0:
                return "played via %s" % name
        except (subprocess.SubprocessError, OSError):
            continue
    return ("not played: no audio player (%s) is available on this host"
            % ", ".join(PLAYERS))
