"""Band plan used by the coverage account and the anomaly rules.

A band is a named stretch of spectrum the watch can speak about. The
coverage account lists bands never visited; the anomaly layer treats
some bands as quiet, where a new strong shape means more.
"""

BANDS = [
    (0.5e6, 30e6, "HF"),
    (30e6, 88e6, "VHF low"),
    (88e6, 108e6, "FM broadcast"),
    (108e6, 118e6, "aviation navigation"),
    (118e6, 137e6, "aviation VHF"),
    (137e6, 144e6, "satellite and space"),
    (144e6, 148e6, "amateur 2 m"),
    (148e6, 174e6, "VHF high"),
    (174e6, 300e6, "VHF-UHF gap"),
    (300e6, 406e6, "UHF land mobile and sonde"),
    (406e6, 512e6, "UHF 70 cm region"),
    (512e6, 806e6, "UHF television and cellular low"),
    (806e6, 960e6, "cellular and ISM 900"),
    (960e6, 1164e6, "aeronautical L-band"),
    (1164e6, 1766e6, "L-band high"),
]

# Bands where the floor is normally low and traffic sparse: a new
# strong emission shape here is an anomaly candidate.
QUIET_BANDS = {"VHF-UHF gap", "UHF 70 cm region", "VHF low",
               "satellite and space"}


def band_name(freq_hz):
    if freq_hz is None:
        return "unknown band"
    for lo, hi, name in BANDS:
        if lo <= freq_hz < hi:
            return name
    return "out of range"


def band_range(name):
    for lo, hi, n in BANDS:
        if n == name:
            return lo, hi
    return None
