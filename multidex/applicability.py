"""Applicability: whether a desk's targets can exist within
reach of the home (0.9.0).

A desk that flies when its targets provably cannot exist at the
home wastes the tuner and files a coverage account that implies
listening where there was nothing to hear. At muster, before the
bill is built, every desk is evaluated against the home position
and the desks that provably cannot apply stand down. Stood down
is a distinct state from benched, and every surface keeps them
distinct: a benched desk cannot run on this host at all, while a
stood-down desk cannot run at this home and would stand at
another.

Every test here fails toward coverage. A desk stands down only
when the shipped data establishes that its targets cannot exist
within reach; uncertainty of any kind - a coarse grid cell, a
missing position, a test the build does not ship - resolves
toward the desk standing. The geographic basis is the coarse
land and water grid in geodata.py, public geography with its
provenance stated in that module.

The one test this build does not ship is the radiosonde test.
It wants a list of upper-air launch sites with the same clean
public standing as the geography grid, and at build time no such
list could be obtained: the Integrated Global Radiosonde
Archive's station list at NOAA's NCEI, the natural source,
refused every download route available to the build, and no
mirror with verifiable provenance was found. Rather than ship
hand-copied data of uncertain accuracy, the sonde desk carries
no applicability test in this build and stands everywhere, and
this paragraph is the record of why.
"""

from . import geodata
from .desks import SPECS

# The balloon radio horizon the sonde test will use when its
# launch-site list ships: a balloon at burst altitude near 30 km
# is visible from the ground at roughly 700 km. It is stated
# here, beside the missing test, so the figure is on the record.
SONDE_HORIZON_KM = 700.0

# The applicability table. Every desk in SPECS carries an
# explicit judgment; no desk's applicability is left to be
# inferred from the absence of an entry. The judgment text is
# the reasoning a reviewer reads; the surfaces print reasons
# only for desks that stand down.
RULES = {
    "adsb": ("universal",
             "aircraft cross any sky; ADS-B traffic exists within "
             "reach of every home"),
    "airband": ("universal",
                "aircraft cross any sky; airband traffic exists "
                "within reach of every home"),
    "milair": ("universal",
               "military aircraft cross any sky within reach of "
               "every home"),
    "acars": ("universal",
              "an aircraft datalink; its targets are wherever "
              "aircraft are"),
    "ais": ("marine",
            "vessels float only on water; the desk applies only "
            "when water is within VHF reach of the home"),
    "wxradio": ("wxradio-service",
                "NOAA Weather Radio is a United States and Canada "
                "service; the desk applies only inside that area"),
    "ism": ("universal",
            "ISM devices are wherever people and their machines "
            "are, on every continent"),
    "aprs": ("universal",
             "amateur packet radio is a worldwide service"),
    "pager": ("universal",
              "paging transmitters are a land service on every "
              "inhabited continent"),
    "satellite": ("universal",
                  "passes are computed from the home position "
                  "itself; the desk applies wherever the home is"),
    "sonde": ("launch-sites",
              "balloons rise from launch sites within radio "
              "horizon of the home; no site list ships in this "
              "build (see the module docstring), so no test is "
              "applied and the desk stands"),
    "broadcast": ("universal",
                  "broadcast transmitters stand within reach of "
                  "every inhabited home"),
    "vdl2": ("universal",
             "an aircraft datalink; its targets are wherever "
             "aircraft are"),
    "amateur": ("universal",
                "amateur radio is a worldwide service on every "
                "band this desk covers"),
    "digitalvoice": ("universal",
                     "land mobile radio is a worldwide service"),
    "hfdl": ("universal",
             "HF propagation is intercontinental by nature; no "
             "distance from the home excludes it"),
    "iss": ("universal",
            "passes are computed from the home position itself; "
            "the desk applies wherever the home is"),
    "cellular": ("universal",
                 "cellular networks cover every inhabited region "
                 "the system is deployed in"),
    "trunking": ("universal",
                 "trunked land mobile systems are a worldwide "
                 "service"),
    "audiocensus": ("universal",
                    "the census reports whatever the spectrum at "
                    "the home holds; no target class exists for "
                    "geography to exclude"),
    "sweep": ("universal",
              "the spectrum census is the system's own baseline "
              "instrument and applies at every home"),
    "probe": ("universal",
              "the probe's targets are the home's own unresolved "
              "record; wherever the station has heard something "
              "unexplained more than once, a stare can be billed"),
}


def _is_number(value):
    return isinstance(value, (int, float)) \
        and not isinstance(value, bool)


def _positioned(home):
    """A home position the tests may reason from, or None.

    A missing, malformed or unset (0, 0) position proves nothing
    about any desk, so every test abstains and every desk stands.
    """
    lat, lon = home.get("lat"), home.get("lon")
    if not _is_number(lat) or not _is_number(lon):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    if lat == 0 and lon == 0:
        return None
    return lat, lon


def evaluate(profile):
    """Judge every desk for the profile's home.

    Returns {desk: {"stands": bool, "reason": str or None,
    "by": str}} covering every desk in SPECS. "by" names what
    decided the outcome: "test" for the geographic tests,
    "universal" for the table's standing judgments, "no test"
    where this build ships none, "no position" where the home
    position cannot support a judgment, and "override" where a
    profile override changed the outcome the test reached.
    "reason" is set for every stood-down desk, and for a desk an
    override holds standing against its test; it is None for a
    desk that simply stands.
    """
    app = profile.get("applicability") or {}
    force_stand = set(app.get("force_stand") or [])
    force_down = set(app.get("force_stand_down") or [])
    range_km = app.get("marine_range_km", 100)
    if not _is_number(range_km) or range_km <= 0:
        range_km = 100
    home = profile.get("home") or {}
    position = _positioned(home)
    decisions = {}
    for name in SPECS:
        rule, _judgment = RULES[name]
        stands, reason, by = True, None, "universal"
        if rule in ("marine", "wxradio-service"):
            if position is None:
                by = "no position"
            else:
                by = "test"
                lat, lon = position
                if rule == "marine":
                    dist = geodata.nearest_water_km(lat, lon)
                    if dist > range_km:
                        stands = False
                        reason = (
                            "no water within reach of this home: "
                            "the nearest water in the shipped land "
                            "and water grid is about %d km away, "
                            "beyond the %s km applicability range "
                            "for ship AIS reception"
                            % (int(round(dist)),
                               "%g" % range_km))
                elif not geodata.in_wxradio_service_area(lat, lon):
                    stands = False
                    reason = (
                        "home is outside the weather radio "
                        "service area drawn in the shipped grid "
                        "(the United States and Canada), so no "
                        "NOAA Weather Radio broadcast can be "
                        "heard at this home")
        elif rule == "launch-sites":
            by = "no test"
        if name in force_down and stands:
            stands, by = False, "override"
            reason = ("stood down by the profile applicability "
                      "override: force_stand_down names this desk, "
                      "and the override decides regardless of the "
                      "applicability test")
        elif name in force_stand and not stands:
            stands, by = True, "override"
            reason = ("the applicability test stood this desk "
                      "down (%s); the profile applicability "
                      "override force_stand decides instead, and "
                      "the desk stands" % reason)
        decisions[name] = {"stands": stands, "reason": reason,
                           "by": by}
    return decisions


def stood_down(decisions):
    """{desk: reason} for the desks an evaluation stood down."""
    return {name: d["reason"] for name, d in decisions.items()
            if not d["stands"]}
