"""The sky account: what the station's own record says about
the sky above it, folded the way the sky actually turns.

Two standings of account live here.

The sidereal fold. A noise floor measured at the same clock
hour on different days is not the same measurement: the sky
over a fixed antenna repeats on the sidereal day (23h 56m),
not the solar one, so structure tied to the sky itself -
the galactic plane crossing the beam, a sidereal-recurrent
interferer - only separates from the day's habits when the
census is folded by local sidereal time. The fold takes the
sweep census's own noise-floor readings under 30 MHz across
the home's whole Index, bins them by sidereal hour, and
files the medians as standing data: the report renders it,
and the probe desk's recurrence work already leans on the
same distinction (0.11.0).

The meteor local-hour account. Meteor rates are a function
of the local hour - the pre-dawn maximum is the radiant
geometry, not a preference - so the meteor desk's pings are
accounted by local solar hour with the sampled time each
hour's count was gathered over, and the report states the
pre-dawn comparison plainly, whichever way it comes out.

And the live sky record: the composition the officer
publishes into the display feed for the Console's sky panel -
local sidereal time, Jupiter's altitude and next predicted
window, the solar state, the meteor rate so far, the sonde
state - computed from the ephemeris and the desks' own filed
events. The console renders records; it computes nothing,
and it holds the last record honestly when the feed is quiet.
"""

import statistics

from . import ephemeris

FOLD_MAX_HZ = 30e6


def _floor_events(index):
    """Sweep census readings carrying a noise floor, HF only,
    across the home's whole record."""
    out = []
    for row in index.events(kinds=["observation"], desk="sweep",
                            limit=1000000):
        data = row.get("data") or {}
        floor = data.get("noise_floor_db")
        freq = row.get("freq_hz")
        if floor is None or freq is None or freq >= FOLD_MAX_HZ:
            continue
        out.append((row["t_start"], float(floor)))
    return out


def build_account(index, home):
    """The sidereal fold over the standing record."""
    readings = _floor_events(index)
    bins = [[] for _ in range(24)]
    for (t, floor) in readings:
        lst_h = ephemeris.lst_deg(t, home["lon"]) / 15.0
        bins[int(lst_h) % 24].append(floor)
    out_bins = []
    for h in range(24):
        vals = bins[h]
        out_bins.append({
            "lst_hour": h,
            "median_floor_db": (round(statistics.median(vals), 1)
                                if vals else None),
            "count": len(vals)})
    return {"bins": out_bins, "events_folded": len(readings),
            "max_hz": FOLD_MAX_HZ}


def render_fold_lines(account):
    """The fold as report lines: one line per sidereal hour,
    the bar showing how far that hour's median floor stands
    above the quietest hour's. Hours with no readings say so;
    an empty fold says it has nothing rather than drawing a
    figure from air."""
    bins = account["bins"]
    have = [b for b in bins if b["median_floor_db"] is not None]
    if not have:
        return ["The sidereal fold has no readings yet: no HF "
                "census noise floors stand in the record to fold."]
    quiet = min(b["median_floor_db"] for b in have)
    lines = []
    for b in bins:
        if b["median_floor_db"] is None:
            lines.append("LST %02dh  no readings" % b["lst_hour"])
            continue
        lift = b["median_floor_db"] - quiet
        bar = "#" * max(0, min(40, int(round(lift * 2))))
        lines.append("LST %02dh  %6.1f dB  (%d reading%s)  %s"
                     % (b["lst_hour"], b["median_floor_db"],
                        b["count"], "" if b["count"] == 1 else "s",
                        bar))
    return lines


def meteor_hours(index, watch_id, home):
    """The meteor account for one watch by local solar hour:
    pings counted, seconds of carrier sampled, and the rate
    per sampled hour - a rate is never stated without the
    time it was counted over."""
    counts = [0] * 24
    sampled = [0.0] * 24
    total = 0
    for row in index.events(watch_id=watch_id, desk="meteor"):
        data = row.get("data") or {}
        hour = int(((row["t_start"] / 3600.0) + home["lon"] / 15.0)
                   % 24)
        if data.get("ping"):
            counts[hour] += 1
            total += 1
        if data.get("sampled_s"):
            sampled[hour] += float(data["sampled_s"])
    hours = []
    for h in range(24):
        rate = (counts[h] / (sampled[h] / 3600.0)
                if sampled[h] > 0 else None)
        hours.append({"local_hour": h, "pings": counts[h],
                      "sampled_s": round(sampled[h], 1),
                      "rate_per_sampled_hour":
                          (round(rate, 1) if rate is not None
                           else None)})
    predawn = sum(counts[3:9])
    rest = total - predawn
    return {"hours": hours, "total_pings": total,
            "predawn_pings": predawn, "other_pings": rest}


def compose_record(t, home, runtime):
    """One live sky record for the display feed, from the
    ephemeris and the desks' filed state so far this watch."""
    lat, lon = home["lat"], home["lon"]
    lst = ephemeris.lst_deg(t, lon)
    geo = ephemeris.jupiter_geometry(t)
    j_alt, _j_az = ephemeris.altaz(geo["ra_deg"], geo["dec_deg"],
                                   t, lat, lon)
    nxt = ephemeris.next_jupiter_window(lat, lon, t)
    sun_alt, _s_az = ephemeris.sun_altaz(t, lat, lon)
    sampled = runtime.get("meteor_sampled_s") or 0.0
    pings = runtime.get("meteor_pings") or 0
    rate = (pings / (sampled / 3600.0)) if sampled > 0 else None
    return {
        "t": t, "lst_deg": round(lst, 1),
        "jupiter": {
            "alt_deg": round(j_alt, 1),
            "source_now": geo["source"],
            "next_window_start": (nxt["start"] if nxt else None),
            "next_source": (nxt["source"] if nxt else None)},
        "solar": {"sun_alt_deg": round(sun_alt, 1),
                  "daylight": sun_alt > 0.0,
                  "last_state": runtime.get("solar_last_state")},
        "meteor": {"pings": pings,
                   "rate_per_sampled_hour":
                       (round(rate, 1) if rate is not None
                        else None)},
        "sonde": {"last_serial": runtime.get("sonde_last_serial"),
                  "last_max_alt_m":
                      runtime.get("sonde_last_max_alt_m")},
    }
