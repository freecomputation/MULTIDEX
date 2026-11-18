"""The probe desk's planning and record mathematics (0.11.0).

The probe desk stares at the unexplained. This module is its
half that runs outside the desk worker: at muster it computes
the candidate list from the Index (emitters the station has
heard more than once and never resolved), ranks it, and bills
the stares into the watch bill as class P items inside a
stated per-watch budget; at execution time it assembles the
target's full history from the Index for the desk worker,
which files the stare, the cluster vote, and the
discrimination checks from it.

The doctrine the desk works under: no anomaly without a
characterized baseline, so a candidate is an emitter measured
against the record, never a bare feeling; on an
anomaly-grade event the duty is capture, not conclusion, so
the probe files captures, attempts and computations, and its
assessments state what an event resembles, what it is not,
and what would resolve it; discrimination before
interpretation, so every probe files the two cheap exact
checks - cross-band coincidence and recurrence against the
solar and sidereal clocks - before any family is claimed.

Classification itself is per dossier, not per burst (the
limitations research, area 5): every observation of an
emitter contributes its per-burst classification weighted by
its SNR, and the dossier carries the voted family only when
the vote clears the stated margin. The per-burst labels stay
in the record as observations either way.

Receive-only, always: a probe dwell is a capture. The desk
files no bearing claims; direction finding is a hardware
build, separately queued, and is not part of this one.
"""

import math

from . import budget, retention
from .bands import band_name

# The observation kinds a cluster is built from: the hearings
# themselves, not the products derived from them.
OBS_KINDS = ("observation", "audio")

# Frequency clusters (hearings that carry no emitter name, as
# the sweep desk files its unknowns) group into buckets of
# this width: two hearings land in one cluster when their
# frequencies round to the same bucket.
FREQ_BUCKET_HZ = 25000.0

# The vote: the winning family must hold at least this share
# of the total SNR weight, on at least this many observations,
# before the dossier carries it. A vote that does not clear
# the margin leaves the dossier UNRESOLVED with the split
# shown - the split is the finding.
VOTE_MARGIN = 0.6
VOTE_MIN_OBSERVATIONS = 2

SOLAR_DAY_S = 86400.0
SIDEREAL_DAY_S = 86164.0905
# The fold window: observations cluster when this share of
# them falls inside one hour of the folded day.
FOLD_WINDOW_S = 3600.0
FOLD_CLUSTER_MIN_SHARE = 0.5

# Cross-band coincidence: another band firing within this many
# seconds of an observation counts as coincident with it.
COINCIDENCE_WINDOW_S = 120.0

# Per-burst family labels read off an event's own record: the
# census modulation estimate, the census tone verdict, and the
# sweep shape, mapped onto the classifier's families.
_MOD_FAMILY = {"AM": "am-voice", "FM": "fm-voice",
               "USB": "ssb-voice", "LSB": "ssb-voice",
               "SSB": "ssb-voice", "DATA": "fsk-data"}
_SHAPE_FAMILY = {"am-voice": "am-voice", "am-broadcast": "am-voice",
                 "fm-broadcast": "fm-voice", "nfm-voice": "fm-voice",
                 "data-burst": "fsk-data",
                 "control-channel": "fsk-data", "apt": "fsk-data",
                 "beacon": "tone"}


def family_of_event(ev):
    """The per-burst family one recorded event votes for, from
    its own data: the census tone verdict first (a tone is a
    tone, whatever modulation carried it), then the census
    modulation estimate, then the sweep shape. Anything the
    record does not classify votes unknown."""
    data = ev.get("data") or {}
    if data.get("voice_grade") == "TONE":
        return "tone"
    mod = data.get("modulation_estimate")
    if mod:
        return _MOD_FAMILY.get(mod, "unknown")
    shape = data.get("shape")
    if shape:
        if shape.startswith("unknown"):
            return "unknown"
        return _SHAPE_FAMILY.get(shape, "unknown")
    return "unknown"


def _bucket(freq_hz):
    return int(round(float(freq_hz) / FREQ_BUCKET_HZ))


def candidates(index, profile):
    """The probe candidate list from the Index, ranked.

    A candidate is a cluster of hearings - one emitter dossier,
    or one frequency bucket of emitter-less hearings - that is
    graded UNRESOLVED, has been heard at least the profile's
    minimum number of times, and has never been decoded: a
    cluster any desk has decoded at CONFIRMED is explained and
    is not a probe target. Ranking is by recurrence first
    (observation count), then classifier uncertainty (the
    retention module's measure, averaged over the cluster's
    hearings), then mean SNR: the emitter heard most, known
    least, and heard strongest earns the first stare.
    """
    pcfg = profile.get("probe") or {}
    min_obs = int(pcfg.get("min_observations", 2))
    events = index.events(kinds=list(OBS_KINDS), limit=100000)
    decodes = index.events(kinds=["decode"], limit=100000)
    decoded_emitters = set()
    decoded_buckets = set()
    for ev in decodes:
        if ev.get("grade") != "CONFIRMED":
            continue
        if ev.get("emitter"):
            decoded_emitters.add(index.resolve(ev["emitter"]))
        if ev.get("freq_hz"):
            decoded_buckets.add(_bucket(ev["freq_hz"]))
    groups = {}
    for ev in events:
        if ev.get("grade") != "UNRESOLVED":
            continue
        if ev.get("emitter"):
            key = ("dossier", index.resolve(ev["emitter"]))
        elif ev.get("freq_hz"):
            key = ("frequency", _bucket(ev["freq_hz"]))
        else:
            continue
        groups.setdefault(key, []).append(ev)
    out = []
    for (kind, ident), evs in groups.items():
        if len(evs) < min_obs:
            continue
        if kind == "dossier" and ident in decoded_emitters:
            continue
        if kind == "frequency" and ident in decoded_buckets:
            continue
        freqs = [e["freq_hz"] for e in evs if e.get("freq_hz")]
        bws = [(e.get("data") or {}).get("bw_hz") for e in evs]
        snrs = [(e.get("data") or {}).get("snr_db") for e in evs]
        snrs = [float(s) for s in snrs if isinstance(s, (int, float))]
        out.append({
            "kind": kind,
            "emitter": ident if kind == "dossier" else None,
            "freq_hz": (sum(freqs) / len(freqs)) if freqs else None,
            "bw_hz": max([float(b) for b in bws
                          if isinstance(b, (int, float))],
                         default=None),
            "observations": len(evs),
            "mean_snr_db": (round(sum(snrs) / len(snrs), 1)
                            if snrs else None),
            "uncertainty": round(sum(
                retention.uncertainty_of(e.get("data") or {})
                for e in evs) / len(evs), 3),
            "first_seen": min(e["t_start"] for e in evs),
            "last_seen": max(e["t_start"] for e in evs),
            "watches": len({e["watch_id"] for e in evs}),
        })
    out.sort(key=lambda c: (-c["observations"], -c["uncertainty"],
                            -(c["mean_snr_db"] or 0.0)))
    return out


def plan_items(bill, profile, cands, start):
    """Bill the stares into the watch bill as class P items.

    Class P is the probe's own bill class: planned at muster
    from the Index like class A is planned from the ephemeris,
    granted at muster like every other reception, and bounded
    by the profile's probe budget - at most max_per_watch
    stares and at most max_seconds_per_watch of tuner time,
    each stare dwell_s long. Unspent budget is simply not
    used: no candidate, no stare, and the bill is returned
    unchanged. The chosen stares are spread across the watch
    so a probe never crowds the muster or the stand-down.
    Returns (bill, chosen)."""
    pcfg = profile.get("probe") or {}
    max_n = int(pcfg.get("max_per_watch", 4))
    max_s = float(pcfg.get("max_seconds_per_watch", 2400))
    dwell = float(pcfg.get("dwell_s", 600))
    chosen = []
    spent = 0.0
    for cand in cands:
        if len(chosen) >= max_n or spent + dwell > max_s:
            break
        chosen.append(cand)
        spent += dwell
    if not chosen:
        return bill, []
    watch_s = float(profile["watch"]["hours"]) * 3600.0
    new = list(bill)
    for k, cand in enumerate(chosen):
        at = start + watch_s * (k + 1) / (len(chosen) + 1)
        bw = min(2.4e6, max(200e3, (cand.get("bw_hz") or 25e3) * 4))
        label = cand.get("emitter") or "%.3f MHz" % (
            cand["freq_hz"] / 1e6)
        new.append({
            "cls": "P", "kind": "probe",
            "label": "Probe stare: %s" % label,
            "start": at, "dur": dwell,
            "center_hz": cand["freq_hz"], "bw_hz": bw,
            "desks": ["probe"], "pass": None,
            "probe_target": cand})
    new.sort(key=lambda i: i["start"])
    for i, item in enumerate(new):
        item["id"] = "bill-%03d" % (i + 1)
        item.setdefault("pass", None)
    budget.annotate_bill(new)
    return new, chosen


def vote(votes):
    """The dossier-cluster vote. votes is a list of
    (family, snr_db) pairs, one per observation of the
    emitter. Each observation weighs 10 ** (snr/10) with the
    SNR clipped to 0..30 dB, so weight is received power in
    ratio terms and no single burst, however strong, counts
    for more than a thousand whispers.

    The dossier carries the winning family only when the
    winner is a real family (unknown can lead a vote; it
    cannot win one), holds at least VOTE_MARGIN of the total
    weight, and is backed by at least VOTE_MIN_OBSERVATIONS
    observations. Otherwise the dossier stays unresolved and
    the result carries the split, which the desk files as
    the finding."""
    weights = {}
    counts = {}
    for family, snr in votes:
        s = float(snr) if isinstance(snr, (int, float)) else 0.0
        w = 10.0 ** (min(max(s, 0.0), 30.0) / 10.0)
        weights[family] = weights.get(family, 0.0) + w
        counts[family] = counts.get(family, 0) + 1
    total = sum(weights.values())
    if not weights or total <= 0:
        return {"family": None, "leading": None, "share": 0.0,
                "split": {}, "observations": 0}
    leading = max(weights, key=lambda f: weights[f])
    share = weights[leading] / total
    resolved = leading != "unknown" and share >= VOTE_MARGIN \
        and counts[leading] >= VOTE_MIN_OBSERVATIONS
    return {
        "family": leading if resolved else None,
        "leading": leading,
        "share": round(share, 3),
        "split": {fam: {"weight": round(weights[fam], 1),
                        "count": counts[fam]}
                  for fam in sorted(weights)},
        "observations": len(votes),
    }


def _fold(times, period_s):
    """Fold observation times on one clock: the largest share
    of them falling inside any one-hour window of the folded
    day, circularly."""
    if not times:
        return {"share": 0.0, "in_window": 0, "count": 0}
    phases = sorted(t % period_s for t in times)
    ext = phases + [p + period_s for p in phases]
    best = 0
    j = 0
    for i, p in enumerate(phases):
        if j < i:
            j = i
        while j < len(ext) and ext[j] - p <= FOLD_WINDOW_S:
            j += 1
        best = max(best, j - i)
    return {"share": round(best / len(phases), 3),
            "in_window": best, "count": len(phases)}


def recurrence(times):
    """Recurrence against both clocks. The dossier's
    observation times folded by the solar day (human hours)
    and by the sidereal day (the sky's hours): the fold that
    clusters is stated plainly, because that single test
    divides terrestrial habit from celestial habit at no cost
    but arithmetic. With fewer than three observations, or no
    fold holding half the record inside one hour, the honest
    finding is that neither clock's habit is established."""
    solar = _fold(times, SOLAR_DAY_S)
    sidereal = _fold(times, SIDEREAL_DAY_S)
    verdict = ("neither fold clusters: no daily habit on "
               "either clock is established by this record")
    if len(times) >= 3:
        s, d = solar["share"], sidereal["share"]
        if max(s, d) >= FOLD_CLUSTER_MIN_SHARE:
            if s > d + 0.1:
                verdict = ("the observations cluster on the "
                           "solar fold: a terrestrial, human-hours "
                           "habit")
            elif d > s + 0.1:
                verdict = ("the observations cluster on the "
                           "sidereal fold: a habit that keeps the "
                           "sky's time, not the clock's")
            else:
                verdict = ("both folds cluster about equally: "
                           "the two clocks cannot be separated "
                           "on this record")
    return {"solar": solar, "sidereal": sidereal,
            "verdict": verdict}


def execution_context(index, target):
    """The target's full record, assembled by the officer for
    the desk worker: every hearing of the cluster with its
    per-burst family vote, and the cross-band coincidence
    count over the same history - for each hearing (the most
    recent forty), whether anything on another band fired
    within COINCIDENCE_WINDOW_S of it. The desk files both
    discrimination checks from this; the computations over
    the record are exact, and the inputs are stated so the
    filed result can be re-derived."""
    if not target:
        return {"history": [], "coincidence": {
            "windows_checked": 0, "by_band": {}, "examples": [],
            "window_s": COINCIDENCE_WINDOW_S}}
    if target.get("kind") == "dossier" and target.get("emitter"):
        evs = index.events(emitter=target["emitter"], limit=2000)
    else:
        evs = index.events_near_freq(target["freq_hz"],
                                     FREQ_BUCKET_HZ / 2, limit=2000)
    history = []
    for ev in evs:
        if ev.get("kind") not in OBS_KINDS:
            continue
        history.append({
            "t": ev["t_start"], "freq_hz": ev.get("freq_hz"),
            "snr_db": (ev.get("data") or {}).get("snr_db"),
            "family": family_of_event(ev),
            "grade": ev.get("grade"), "desk": ev.get("desk")})
    band = band_name(target["freq_hz"]) if target.get("freq_hz") \
        else None
    by_band = {}
    examples = []
    checked = 0
    for h in history[-40:]:
        t = h["t"]
        others = index.events(t_from=t - COINCIDENCE_WINDOW_S,
                              t_to=t + COINCIDENCE_WINDOW_S,
                              limit=300)
        checked += 1
        seen = set()
        for o in others:
            if o.get("freq_hz") is None or target.get("freq_hz") \
                    is None:
                continue
            if abs(o["freq_hz"] - target["freq_hz"]) <= 50000:
                continue
            ob = band_name(o["freq_hz"])
            if ob == band:
                continue
            seen.add(ob)
            if len(examples) < 3:
                examples.append({
                    "t": o["t_start"], "freq_hz": o["freq_hz"],
                    "band": ob, "desk": o.get("desk"),
                    "kind": o.get("kind")})
        for b in seen:
            by_band[b] = by_band.get(b, 0) + 1
    return {"history": history,
            "coincidence": {"windows_checked": checked,
                            "by_band": by_band,
                            "examples": examples,
                            "window_s": COINCIDENCE_WINDOW_S}}
