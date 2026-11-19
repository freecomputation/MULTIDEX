"""The change log (0.13.0): what changed in the RF environment.

The station's record is long enough now that the most valuable
product is not another catch but the account of movement between
watches. At stand-down the officer computes this account from the
Index alone - no tuner, no desk work - and files it two ways:
changelog.json in the watch folder (structured entries, data
first, the shape the REBER journal plan expects to publish) and
a Change Log chapter in the Watch Report rendering the same
entries as prose.

Four change kinds, each with its evidence rule stated in code:

- new: an emitter whose first-ever hearing in the Index falls
  inside the current watch, heard at least NEW_MIN_HEARINGS
  times there. A single stray hearing is a catch, not an
  arrival, and files nothing here.
- vanished: an emitter established in the baseline and not
  heard at all in the current watch, claimed only where the
  desks that hear that emitter actually flew comparable
  coverage of its band during this watch. Where coverage did
  not happen the entry is not made; the emitter is listed as
  unwatched instead, which is a coverage fact, not a change.
- moved: the same emitter identity - the Index's own resolved
  identity, never two similar unknowns matched by resemblance -
  heard at a frequency shifted beyond the stated tolerance
  from its baseline frequency, with hearings carrying
  frequencies on both sides.
- rhythm-changed: an established emitter whose folded habit
  moved, judged by the probe module's recurrence machinery
  over its full prior history against the current watch's
  hearings: a daily habit appeared, an established one
  dissolved, or the habit's centre shifted by more than the
  stated tolerance. No rhythm is claimed on fewer observations
  than the fold itself requires, on either side.

The baseline is the record of every earlier watch in the
Index. While fewer than BASELINE_ESTABLISHED_WATCHES watches
stand behind it, the whole account is headed provisional, in
the data and in the prose alike - the same honesty the
baselines discipline has always carried.
"""

from . import probe, util
from .bands import band_name
from .exploit import GRADE_ORDER

CHANGELOG_FORMAT = 1

# A hearing is an event that carries an emitter and is the
# emitter being heard, not the system talking about it:
# assessments, dwell records, gaps, health and lifecycle events
# name emitters without hearing them and never count.
NON_HEARING_KINDS = ("assessment", "dwell", "gap", "desk_health",
                     "system", "update")

NEW_MIN_HEARINGS = 2

# Established in the baseline, for the vanished rule: heard in
# at least this many earlier watches, or holding at least this
# many historical hearings across however many watches.
VANISHED_MIN_WATCHES = 2
VANISHED_MIN_HEARINGS = 5

# The moved rule: the shift must exceed the greater of a flat
# floor and a tenth of a percent of the baseline frequency.
MOVED_TOL_FLOOR_HZ = 25000.0
MOVED_TOL_FRACTION = 0.001
MOVED_MIN_HEARINGS_PER_SIDE = 2

# The rhythm rule: each side needs at least the fold's own
# minimum of observations (probe.recurrence judges no habit
# under three), and a shift counts only past this many seconds
# of folded-day centre movement.
RHYTHM_MIN_HEARINGS_PER_SIDE = 3
RHYTHM_SHIFT_TOL_S = 7200.0

# A baseline is established at three watches, the standing
# discipline; below that the account is provisional.
BASELINE_ESTABLISHED_WATCHES = 3

_KIND_ORDER = {"new": 0, "vanished": 1, "moved": 2,
               "rhythm-changed": 3}


def _median(values):
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    if n % 2:
        return float(vals[n // 2])
    return (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _hearings(index):
    """Every hearing in the Index, grouped by resolved emitter.

    Returns {canonical: [event, ...]} in time order. The
    Index's own identity is the only identity there is: an
    alias and its canonical are one emitter here, as everywhere
    else in the system.
    """
    groups = {}
    for ev in index.events(limit=1000000):
        if not ev.get("emitter"):
            continue
        if ev["kind"] in NON_HEARING_KINDS:
            continue
        groups.setdefault(index.resolve(ev["emitter"]),
                          []).append(ev)
    return groups


def _grade_of(evs):
    """The grade an entry states: the strongest grade among the
    hearings it rests on, so a change resting on a confirmed
    decode says CONFIRMED and one resting only on unresolved
    hearings says UNRESOLVED."""
    best = None
    for ev in evs:
        if best is None or GRADE_ORDER[ev["grade"]] > GRADE_ORDER[best]:
            best = ev["grade"]
    return best


def _emitter_kind(evs):
    for ev in evs:
        kind = (ev.get("data") or {}).get("emitter_kind")
        if kind:
            return kind
    return "emitter"


def _habit(times):
    """One side's folded habit, from the probe module's
    recurrence machinery: whether a daily habit is established,
    on which clock, how strongly it clusters, and where its
    centre sits. probe.recurrence decides the fold; it returns
    shares but no window location, so the centre is read off
    the same one-hour window definition with the probe module's
    own constants - the largest count of the side's phases
    inside one window of the folded day, the window's middle
    taken as the centre."""
    rec = probe.recurrence(times)
    solar, sidereal = rec["solar"], rec["sidereal"]
    clock, fold = "solar", solar
    if sidereal["share"] > solar["share"]:
        clock, fold = "sidereal", sidereal
    established = len(times) >= RHYTHM_MIN_HEARINGS_PER_SIDE \
        and fold["share"] >= probe.FOLD_CLUSTER_MIN_SHARE
    centre = None
    if established:
        period = probe.SOLAR_DAY_S if clock == "solar" \
            else probe.SIDEREAL_DAY_S
        phases = sorted(t % period for t in times)
        ext = phases + [p + period for p in phases]
        best_i, best_n = 0, -1
        j = 0
        for i, p in enumerate(phases):
            if j < i:
                j = i
            while j < len(ext) and ext[j] - p <= probe.FOLD_WINDOW_S:
                j += 1
            if j - i > best_n:
                best_i, best_n = i, j - i
        centre = (phases[best_i] + probe.FOLD_WINDOW_S / 2.0) % period
    return {"established": established, "clock": clock,
            "share": fold["share"], "count": len(times),
            "centre_s": centre, "verdict": rec["verdict"]}


def habit_words(habit):
    """One side's habit in plain words, for the prose account."""
    if not habit["established"]:
        return ("no daily habit established across %d hearings"
                % habit["count"])
    if habit["clock"] == "solar":
        return ("a daily habit keeping solar time, centred near "
                "%s UTC, across %d hearings"
                % (util.fmt_time(habit["centre_s"]), habit["count"]))
    return ("a daily habit keeping sidereal time, centred near "
            "%s of the sidereal day, across %d hearings"
            % (util.fmt_time(habit["centre_s"]), habit["count"]))


def _centre_distance(a, b):
    """Circular distance between two folded-day centres on the
    clock they share."""
    period = probe.SOLAR_DAY_S if a["clock"] == "solar" \
        else probe.SIDEREAL_DAY_S
    d = abs(a["centre_s"] - b["centre_s"]) % period
    return min(d, period - d)


def _band_coverage(index, watch_id, band, desks):
    """Comparable coverage of one band in the current watch:
    the dwell records in that band whose standing desks include
    a desk that hears this emitter, or the sweep, whose census
    hears every band it walks. Returns the dwell count; zero
    means the band went unwatched for this emitter, however
    busy the watch was elsewhere."""
    n = 0
    for dwell in index.dwell_records(watch_id):
        if not dwell.get("freq_hz"):
            continue
        if band_name(dwell["freq_hz"]) != band:
            continue
        standing = set((dwell.get("data") or {}).get("desks") or [])
        if standing & set(desks) or "sweep" in standing:
            n += 1
    return n


def compute_changelog(index, watch_id):
    """The change log for one watch, computed from the Index.

    Pure computation over the record: the same Index always
    yields the same account, so the JSON file and the Watch
    Report chapter rendered from it cannot disagree.
    """
    meta = index.get_watch_meta(watch_id) or {}
    groups = _hearings(index)
    baseline_watches = sorted({
        ev["watch_id"] for evs in groups.values() for ev in evs
        if ev["watch_id"] != watch_id})
    provisional = len(baseline_watches) < BASELINE_ESTABLISHED_WATCHES
    entries = []
    unwatched = []
    for emitter in sorted(groups):
        evs = groups[emitter]
        prior = [e for e in evs if e["watch_id"] != watch_id]
        current = [e for e in evs if e["watch_id"] == watch_id]
        prior_freqs = [e["freq_hz"] for e in prior if e.get("freq_hz")]
        cur_freqs = [e["freq_hz"] for e in current if e.get("freq_hz")]
        base_freq = _median(prior_freqs)
        cur_freq = _median(cur_freqs)
        rep_freq = cur_freq if cur_freq is not None else base_freq
        band = band_name(rep_freq) if rep_freq is not None \
            else "unknown band"
        if not prior:
            # New: first-ever hearing inside this watch, and
            # heard enough to be an arrival, not a stray.
            if len(current) >= NEW_MIN_HEARINGS:
                entries.append({
                    "kind": "new", "emitter": emitter,
                    "emitter_kind": _emitter_kind(current),
                    "band": band, "freq_hz": cur_freq,
                    "grade": _grade_of(current),
                    "first_heard": min(e["t_start"] for e in current),
                    "last_heard": max(e["t_start"] for e in current),
                    "hearings": len(current), "hearings_prior": 0,
                    "watches_prior": 0,
                    "desks": sorted({e["desk"] for e in current}),
                    "note": "first heard in this watch, %d hearings; "
                            "no hearing in any earlier watch stands "
                            "in the Index" % len(current)})
            continue
        prior_watches = len({e["watch_id"] for e in prior})
        if not current:
            # Vanished, or unwatched: the distinction is the
            # coverage the emitter's band actually flew.
            established = prior_watches >= VANISHED_MIN_WATCHES \
                or len(prior) >= VANISHED_MIN_HEARINGS
            if not established:
                continue
            desks = sorted({e["desk"] for e in prior})
            covered = _band_coverage(index, watch_id, band, desks)
            record = {
                "emitter": emitter,
                "emitter_kind": _emitter_kind(prior),
                "band": band, "freq_hz": base_freq,
                "grade": _grade_of(prior),
                "last_heard": max(e["t_start"] for e in prior),
                "hearings_prior": len(prior),
                "watches_prior": prior_watches, "desks": desks}
            if covered:
                record.update({
                    "kind": "vanished", "coverage_dwells": covered,
                    "note": "established in the baseline and not "
                            "heard in this watch, while its band "
                            "flew comparable coverage in %d "
                            "dwell(s) by the desks that hear it"
                            % covered})
                entries.append(record)
            else:
                record.update({
                    "kind": "unwatched",
                    "note": "established in the baseline and not "
                            "heard in this watch, but its band "
                            "went unwatched by the desks that "
                            "hear it, so no change is claimed"})
                unwatched.append(record)
            continue
        # Heard on both sides: moved, and rhythm.
        if len(prior_freqs) >= MOVED_MIN_HEARINGS_PER_SIDE \
                and len(cur_freqs) >= MOVED_MIN_HEARINGS_PER_SIDE:
            tol = max(MOVED_TOL_FLOOR_HZ,
                      MOVED_TOL_FRACTION * abs(base_freq))
            delta = cur_freq - base_freq
            if abs(delta) > tol:
                entries.append({
                    "kind": "moved", "emitter": emitter,
                    "emitter_kind": _emitter_kind(evs),
                    "band": band_name(cur_freq), "freq_hz": cur_freq,
                    "old_freq_hz": base_freq, "new_freq_hz": cur_freq,
                    "delta_hz": delta, "tolerance_hz": tol,
                    "grade": _grade_of(evs),
                    "hearings_prior": len(prior_freqs),
                    "hearings": len(cur_freqs),
                    "watches_prior": prior_watches,
                    "first_heard": min(e["t_start"] for e in current),
                    "last_heard": max(e["t_start"] for e in current),
                    "desks": sorted({e["desk"] for e in evs}),
                    "note": "the same emitter identity, heard at "
                            "%s against a baseline of %s, a shift "
                            "of %s beyond the stated tolerance of "
                            "%s" % (util.fmt_hz(cur_freq),
                                    util.fmt_hz(base_freq),
                                    util.fmt_hz(abs(delta)),
                                    util.fmt_hz(tol))})
        if len(prior) >= RHYTHM_MIN_HEARINGS_PER_SIDE \
                and len(current) >= RHYTHM_MIN_HEARINGS_PER_SIDE:
            before = _habit([e["t_start"] for e in prior])
            after = _habit([e["t_start"] for e in current])
            change = None
            if before["established"] and not after["established"]:
                change = "dissolved"
            elif after["established"] and not before["established"]:
                change = "appeared"
            elif before["established"] and after["established"] \
                    and (before["clock"] != after["clock"]
                         or _centre_distance(before, after)
                         > RHYTHM_SHIFT_TOL_S):
                change = "shifted"
            if change:
                entries.append({
                    "kind": "rhythm-changed", "emitter": emitter,
                    "emitter_kind": _emitter_kind(evs),
                    "band": band, "freq_hz": rep_freq,
                    "grade": _grade_of(evs), "change": change,
                    "before": habit_words(before),
                    "after": habit_words(after),
                    "hearings_prior": len(prior),
                    "hearings": len(current),
                    "watches_prior": prior_watches,
                    "first_heard": min(e["t_start"] for e in current),
                    "last_heard": max(e["t_start"] for e in current),
                    "desks": sorted({e["desk"] for e in evs}),
                    "note": "habit %s: before, %s; after, %s"
                            % (change, habit_words(before),
                               habit_words(after))})
    entries.sort(key=lambda e: (_KIND_ORDER[e["kind"]], e["emitter"]))
    unwatched.sort(key=lambda e: e["emitter"])
    counts = {"new": 0, "vanished": 0, "moved": 0,
              "rhythm-changed": 0, "unwatched": len(unwatched)}
    for e in entries:
        counts[e["kind"]] += 1
    return {
        "format": CHANGELOG_FORMAT,
        "watch_id": watch_id,
        "generated_at": meta.get("end") or (
            max([e["t_start"] for evs in groups.values() for e in evs
                 if e["watch_id"] == watch_id], default=0)),
        "baseline": {
            "watches": baseline_watches,
            "watch_count": len(baseline_watches),
            "provisional": provisional,
            "established_watches_required":
                BASELINE_ESTABLISHED_WATCHES},
        "thresholds": {
            "new_min_hearings": NEW_MIN_HEARINGS,
            "vanished_min_watches": VANISHED_MIN_WATCHES,
            "vanished_min_hearings": VANISHED_MIN_HEARINGS,
            "moved_tol_floor_hz": MOVED_TOL_FLOOR_HZ,
            "moved_tol_fraction": MOVED_TOL_FRACTION,
            "rhythm_min_hearings_per_side":
                RHYTHM_MIN_HEARINGS_PER_SIDE,
            "rhythm_shift_tol_s": RHYTHM_SHIFT_TOL_S},
        "counts": counts,
        "entries": entries,
        "unwatched": unwatched,
    }


def render_section(log):
    """The Watch Report chapter for one computed change log:
    the same entries as the JSON, as prose. The provisional
    heading stands in the prose exactly as it stands in the
    data, while the baseline is under three watches."""
    lines = ["## 5. Change log"]
    base = log["baseline"]
    if base["provisional"]:
        lines.append(
            "This account is provisional: its baseline stands on "
            "%d earlier watch(es), fewer than the %d an established "
            "baseline requires. Its entries are computed under the "
            "stated evidence rules and are headed provisional "
            "until the baseline is established."
            % (base["watch_count"],
               base["established_watches_required"]))
    else:
        lines.append(
            "This account's baseline stands on %d earlier watches "
            "and is established." % base["watch_count"])
    counts = log["counts"]
    if not log["entries"] and not log["unwatched"]:
        lines.append(
            "No emitter changed state against the baseline this "
            "watch: nothing arrived, vanished, moved, or changed "
            "rhythm under the stated evidence rules.")
        return lines
    lines.append(
        "Against the baseline this watch: %d new, %d vanished, "
        "%d moved, %d rhythm-changed."
        % (counts["new"], counts["vanished"], counts["moved"],
           counts["rhythm-changed"]))
    for e in log["entries"]:
        if e["kind"] == "new":
            lines.append(
                "- new: %s (%s) in the %s band at %s, grade %s, "
                "heard %d times, first heard %s, last heard %s."
                % (e["emitter"], e["emitter_kind"], e["band"],
                   util.fmt_hz(e["freq_hz"]), e["grade"],
                   e["hearings"], util.fmt_stamp(e["first_heard"]),
                   util.fmt_stamp(e["last_heard"])))
        elif e["kind"] == "vanished":
            lines.append(
                "- vanished: %s (%s) in the %s band at %s, last "
                "heard %s after %d hearings across %d earlier "
                "watch(es); its band flew comparable coverage in "
                "%d dwell(s) this watch, so the silence is a "
                "change, not a gap."
                % (e["emitter"], e["emitter_kind"], e["band"],
                   util.fmt_hz(e["freq_hz"]),
                   util.fmt_stamp(e["last_heard"]),
                   e["hearings_prior"], e["watches_prior"],
                   e["coverage_dwells"]))
        elif e["kind"] == "moved":
            lines.append(
                "- moved: %s (%s) from %s to %s, a shift of %s, "
                "on %d hearings before and %d this watch."
                % (e["emitter"], e["emitter_kind"],
                   util.fmt_hz(e["old_freq_hz"]),
                   util.fmt_hz(e["new_freq_hz"]),
                   util.fmt_hz(abs(e["delta_hz"])),
                   e["hearings_prior"], e["hearings"]))
        elif e["kind"] == "rhythm-changed":
            lines.append(
                "- rhythm changed: %s (%s) - before, %s; after, "
                "%s." % (e["emitter"], e["emitter_kind"],
                         e["before"], e["after"]))
    if log["unwatched"]:
        lines.append(
            "Established emitters whose band went unwatched this "
            "watch, so no change is claimed for them:")
        for e in log["unwatched"]:
            lines.append(
                "- %s (%s) in the %s band, last heard %s."
                % (e["emitter"], e["emitter_kind"], e["band"],
                   util.fmt_stamp(e["last_heard"])))
    return lines
