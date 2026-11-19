"""The anomaly register (0.14.0): the unexplained, kept as
first-class records.

A standing watch files over a thousand unresolved census
observations, and almost none of them are anomalies in any
sense worth keeping. This module is the register the anomaly
doctrine promised: one permanent entry per unexplained
cluster that has earned it, holding the cluster's retained
evidence, its negative results, its discrimination checks,
and a three-part statement of what it resembles, what it is
not, and what would resolve it. The register lives in the
Index, in a table beside the events, and its entries are
structured data throughout, so a later export can carry them
without re-deriving anything.

The entry bar is the register's credibility, and it is
stated here in code: a cluster (the probe module's own
clustering - one emitter dossier, or one frequency bucket of
emitter-less hearings) earns an entry only when it is graded
UNRESOLVED, it recurs at or past the probe candidate minimum
of hearings, and it has survived the system's attempts to
explain it - a probe stare whose cluster vote stayed
unresolved, or an equivalent body of filed negative results.
A one-off unresolved hearing files nothing. A cluster any
desk has decoded never enters; a registered cluster a desk
later explains leaves by resolution, with the resolving
event linked.

The lifecycle runs at the two moments the record changes:
when a probe stare completes on a cluster, and at
stand-down. Retroactive exploitation consults the register
directly: a retroactive decode that resolves a registered
cluster closes its entry at the moment it files. Retained
evidence for a registered cluster is sealed under the
retention tiers - shed-exempt, counted in the sealed account
the tiers already keep.

Above the ordinary entries stands the special candidate,
under the special anomaly plan's strict trigger: the
emission matches no known shape the vote can carry, carries
structure no desk could decode across the filed attempt set,
persists or recurs, survives a probe stare, and survives the
self-refutation checks this module files for it (catalogued
emitters and decodes, cross-band coincidence, harmonics of
stronger hearings, a solar habit). A special candidate
raises its own alert product, separate from the FLASH line.
The confirmation rule is absolute and is the register's
ceiling: one station cannot settle an event of this class,
so the register's strongest word is CANDIDATE, stated
exactly that way, indefinitely. The station never answers:
every entry in this register is a record of listening, and
nothing here transmits anything, ever.
"""

import os

from . import probe, retention, util
from .bands import band_name

REGISTER_FORMAT = 1

STATE_OPEN = "OPEN"
STATE_RESOLVED = "RESOLVED"
STATE_SPECIAL = "SPECIAL CANDIDATE"

# The equivalent body of filed negative results that stands
# in for a probe stare at the entry bar: at least this many
# filed attempts, from at least this many distinct desks.
ENTRY_NEGATIVE_MIN = 2
ENTRY_NEGATIVE_DESKS_MIN = 2

# The special trigger's persistence and structure floors, on
# top of the plan's conjunction: at least this many hearings,
# spanning at least this long or heard in at least two
# watches, with the strongest hearing at or past this SNR,
# and filed negative results from at least this many desks.
SPECIAL_MIN_HEARINGS = 3
SPECIAL_MIN_SPAN_S = 3600.0
SPECIAL_MIN_NEGATIVE_DESKS = 2
SPECIAL_MIN_SNR_DB = 10.0


def cluster_key(cluster):
    """The stable identity of one cluster, the key the
    register's rows are matched on across refreshes."""
    if cluster.get("kind") == "dossier":
        return "dossier:%s" % cluster.get("emitter")
    return "frequency:%s" % cluster.get("bucket")


def cluster_label(cluster):
    if cluster.get("kind") == "dossier":
        return str(cluster.get("emitter"))
    centre = (cluster.get("bucket") or 0) * probe.FREQ_BUCKET_HZ
    return "frequency bucket centred at %s" % util.fmt_hz(centre)


# ------------------------------------------------------------ clustering

def _matches(index, ev, cluster):
    """Whether one event belongs to one cluster, under the
    probe module's own identity rules: the Index's resolved
    emitter for a dossier cluster, the probe frequency bucket
    for an emitter-less one."""
    if cluster.get("kind") == "dossier":
        return bool(ev.get("emitter")) and \
            index.resolve(ev["emitter"]) == cluster.get("emitter")
    return ev.get("freq_hz") is not None and \
        probe._bucket(ev["freq_hz"]) == cluster.get("bucket")


def _clusters(index, events):
    """The unresolved hearing clusters of the whole Index,
    grouped exactly as the probe desk groups its candidates:
    by resolved emitter dossier, or by frequency bucket for
    hearings that carry no emitter name."""
    groups = {}
    for ev in events:
        if ev["kind"] not in probe.OBS_KINDS:
            continue
        if ev.get("grade") != "UNRESOLVED":
            continue
        if ev.get("emitter"):
            key = ("dossier", index.resolve(ev["emitter"]))
        elif ev.get("freq_hz"):
            key = ("frequency", probe._bucket(ev["freq_hz"]))
        else:
            continue
        groups.setdefault(key, []).append(ev)
    out = []
    for (kind, ident), evs in sorted(groups.items()):
        evs = sorted(evs, key=lambda e: e["t_start"])
        freqs = [e["freq_hz"] for e in evs if e.get("freq_hz")]
        if kind == "dossier":
            cluster = {"kind": "dossier", "emitter": ident,
                       "freq_hz": (sum(freqs) / len(freqs))
                       if freqs else None, "bucket": None,
                       "bucket_hz": probe.FREQ_BUCKET_HZ}
        else:
            centre = ident * probe.FREQ_BUCKET_HZ
            cluster = {"kind": "frequency", "emitter": None,
                       "freq_hz": (sum(freqs) / len(freqs))
                       if freqs else centre, "bucket": ident,
                       "bucket_hz": probe.FREQ_BUCKET_HZ}
        cluster["key"] = cluster_key(cluster)
        out.append((cluster, evs))
    return out


def _matching(events, index, cluster, kinds=None, rules=None):
    out = []
    for ev in events:
        if kinds is not None and ev["kind"] not in kinds:
            continue
        if rules is not None and \
                (ev.get("data") or {}).get("rule") not in rules:
            continue
        if _matches(index, ev, cluster):
            out.append(ev)
    return sorted(out, key=lambda e: e["t_start"])


# ------------------------------------------------------- record pieces

def _evidence_ids(index, events):
    """The snippet store identities retained for a cluster's
    events: each event's headline snippet, and the excerpts
    and audio its data names beside it, resolved to store
    rows by watch and path. Shed rows are not evidence; the
    list reads the store as it stands."""
    rows = index.snippet_rows_all()
    by_watch_path = {(r["watch_id"], r["path"]): r["id"]
                     for r in rows if not r["shed"]}
    by_watch_base = {}
    for r in rows:
        if r["shed"]:
            continue
        by_watch_base.setdefault(
            (r["watch_id"], os.path.basename(r["path"] or "")),
            r["id"])
    ids = []
    for ev in events:
        found = set()
        if ev.get("snippet"):
            sid = by_watch_path.get((ev["watch_id"], ev["snippet"]))
            if sid:
                found.add(sid)
        data = ev.get("data") or {}
        for key in ("iq_excerpt", "audio", "imagery"):
            name = data.get(key)
            if name:
                sid = by_watch_base.get((ev["watch_id"], name))
                if sid:
                    found.add(sid)
        ids.extend(sorted(found))
    return sorted(set(ids))


def _discrimination(cluster, hearings, ctx):
    """The two cheap exact checks over the cluster's full
    history, from the probe module's own machinery: cross-band
    coincidence, and recurrence folded on the solar and
    sidereal clocks. Refreshed at every refresh, so the entry
    states the folds as the record now stands. The execution
    context is computed once by the caller and shared with
    the vote, so a refresh judges a cluster from one assembly
    of its history."""
    times = [h["t"] for h in ctx["history"]] or \
        [e["t_start"] for e in hearings]
    return {"coincidence": ctx["coincidence"],
            "recurrence": probe.recurrence(times),
            "hearings_judged": len(times)}


def _self_refutation(index, events, cluster, discrimination,
                     vote_result):
    """The system's attempt to kill its own finding, filed as
    checks with outcomes. Each check names the ordinary
    explanation it tests and what the record says: an outcome
    of explains ends the matter (the entry resolves or never
    rises), weakens counts against the special trigger, and
    stands is the only outcome a special candidate may carry
    on every check."""
    checks = []
    decodes = _matching(events, index, cluster, kinds=["decode"])
    confirmed = [e for e in decodes if e["grade"] == "CONFIRMED"]
    checks.append({
        "check": "catalogued emitter or decode",
        "outcome": "explains" if confirmed else "stands",
        "detail": ("a decode in the Index explains this cluster"
                   if confirmed else
                   "no desk has decoded this cluster and no "
                   "catalogued emitter in the Index carries it")})
    coin = discrimination["coincidence"]
    checked = coin.get("windows_checked", 0)
    hit = sum((coin.get("by_band") or {}).values())
    if checked and hit >= max(1, checked // 2):
        checks.append({
            "check": "cross-band coincidence",
            "outcome": "weakens",
            "detail": ("other bands fired alongside %d of %d "
                       "checked hearings (%s): a local or "
                       "intermodulation origin is not excluded"
                       % (hit, checked, ", ".join(
                           "%s band in %d window(s)" % (b, n)
                           for b, n in sorted(
                               coin["by_band"].items()))))})
    else:
        checks.append({
            "check": "cross-band coincidence",
            "outcome": "stands",
            "detail": ("in %d checked windows around the "
                       "hearings, other bands fired alongside "
                       "%d of them: no cross-band companion "
                       "explains the cluster" % (checked, hit))})
    freq = cluster.get("freq_hz")
    harmonic = []
    if freq:
        for ev in events:
            f = ev.get("freq_hz")
            if not f or ev["kind"] in probe.OBS_KINDS:
                continue
            if abs(f - 2.0 * freq) <= 0.01 * freq or \
                    abs(f - freq / 2.0) <= 0.01 * freq:
                harmonic.append(ev)
    checks.append({
        "check": "harmonic or image of a stronger hearing",
        "outcome": "weakens" if harmonic else "stands",
        "detail": ("%d filed record(s) stand at the cluster's "
                   "harmonic or sub-harmonic frequencies and "
                   "are not excluded as its source"
                   % len(harmonic) if harmonic else
                   "no filed record stands at twice or half "
                   "the cluster's frequency")})
    rec = discrimination["recurrence"]
    solar, sidereal = rec["solar"], rec["sidereal"]
    if solar["share"] >= probe.FOLD_CLUSTER_MIN_SHARE \
            and solar["share"] > sidereal["share"] + 0.1:
        checks.append({
            "check": "a terrestrial, human-hours habit",
            "outcome": "weakens",
            "detail": ("the hearings cluster on the solar "
                       "fold (share %.2f against the sidereal "
                       "fold's %.2f): the habit keeps human "
                       "hours" % (solar["share"],
                                  sidereal["share"]))})
    else:
        checks.append({
            "check": "a terrestrial, human-hours habit",
            "outcome": "stands",
            "detail": ("the solar fold does not cluster the "
                       "hearings beyond the sidereal fold: %s"
                       % rec["verdict"])})
    if vote_result and vote_result.get("family"):
        checks.append({
            "check": "a known shape in the cluster vote",
            "outcome": "explains",
            "detail": ("the cluster vote carries the known "
                       "family %s" % vote_result["family"])})
    else:
        checks.append({
            "check": "a known shape in the cluster vote",
            "outcome": "stands",
            "detail": ("the cluster vote carries no known "
                       "family: the leading family is %s"
                       % (vote_result or {}).get("leading"))})
    return checks


def _statement(cluster, vote_result, negatives, discrimination,
               stare, hearings):
    """The three-part statement, composed from the record and
    from nothing else: resembles from the vote split and the
    stare's classification, is-not from the negative results
    and the discrimination checks, would-resolve from the
    evidence the record concretely lacks."""
    split = (vote_result or {}).get("split") or {}
    known = [(fam, s) for fam, s in sorted(
        split.items(), key=lambda kv: -kv[1]["weight"])
        if fam != "unknown"]
    if known:
        lead = ", ".join(
            "%s (%d hearing(s), %.0f percent of the vote "
            "weight)" % (fam, s["count"], 100.0 * s["weight"] /
                         max(1.0, sum(v["weight"]
                                      for v in split.values())))
            for fam, s in known[:2])
        resembles = ("the cluster vote leans toward %s, "
                     "without any family carrying the vote"
                     % lead)
    elif stare and (stare.get("data") or {}).get(
            "classification", {}).get("family") not in (
                None, "unknown"):
        fam = stare["data"]["classification"]["family"]
        resembles = ("no family takes a share of the cluster "
                     "vote; the probe stare's feature tree "
                     "read the emission as %s, the nearest "
                     "known shape it could name" % fam)
    else:
        resembles = ("no known family takes a share of the "
                     "cluster vote and the probe stare's "
                     "feature tree placed it in no family: "
                     "an unclassified structured emission, "
                     "nearest to nothing the system decodes")
    if stare and (stare.get("data") or {}).get("classification"):
        reasons = stare["data"]["classification"].get(
            "reasons") or []
        if reasons:
            resembles += "; the stare's stated reason: %s" \
                % reasons[0]
    parts = []
    desks = sorted({n["attempted_desk"] for n in negatives})
    if negatives:
        parts.append("not decoded by %s: %d filed attempt(s) "
                     "on this cluster are negative results"
                     % (", ".join(desks), len(negatives)))
    if vote_result and not vote_result.get("family"):
        parts.append("not carried as any known family by the "
                     "probe cluster vote: the leading family %s "
                     "holds %.0f percent of the weight against "
                     "the %.0f percent margin"
                     % (vote_result.get("leading"),
                        100.0 * vote_result.get("share", 0.0),
                        100.0 * probe.VOTE_MARGIN))
    coin = discrimination["coincidence"]
    if coin.get("windows_checked") and not coin.get("by_band"):
        parts.append("not accompanied on other bands: in %d "
                     "checked windows around its hearings, "
                     "nothing on another band fired with it"
                     % coin["windows_checked"])
    elif coin.get("by_band"):
        parts.append("not isolated in time, and that is "
                     "stated rather than hidden: other bands "
                     "fired within its windows (%s)"
                     % ", ".join("%s band in %d window(s)"
                                 % (b, n) for b, n in sorted(
                                     coin["by_band"].items())))
    parts.append("not explained by any decode in the Index")
    is_not = "; ".join(parts)
    rec = discrimination["recurrence"]
    resolve = ["a second, independent station hearing the "
               "same emission at the same time, which is the "
               "only route past candidate standing for an "
               "event of this class",
               "a bearing on the emitter, which this station "
               "cannot take: one fixed antenna and no "
               "direction-finding array"]
    if stare and (stare.get("data") or {}).get("silent"):
        resolve.append("a probe capture that overlaps the "
                       "emission: the stare on record was "
                       "silent, so the cluster's structure "
                       "rests on its census hearings alone")
    if len(hearings) < 5:
        resolve.append("more hearings: the record holds %d, "
                       "and the recurrence folds need more to "
                       "separate the two clocks - %s"
                       % (len(hearings), rec["verdict"]))
    if known:
        resolve.append("a decode from the desk that owns %s, "
                       "run over a capture the vote can carry"
                       % known[0][0])
    would = "; ".join(resolve)
    return {"resembles": resembles, "is_not": is_not,
            "would_resolve": would}


# ------------------------------------------------------------- the bar

def _bar(cluster, hearings, vote_assessments, negatives,
         min_obs):
    """Whether a cluster earns an entry, and why: recurring,
    unresolved (the caller has already excluded decoded
    clusters), and survived - a probe vote that stayed
    unresolved, or the equivalent body of negative results."""
    if len(hearings) < min_obs:
        return False, "heard %d time(s), under the minimum " \
            "of %d" % (len(hearings), min_obs)
    survived_vote = any(a["grade"] == "UNRESOLVED"
                        for a in vote_assessments)
    desks = {n["attempted_desk"] for n in negatives}
    survived_attempts = len(negatives) >= ENTRY_NEGATIVE_MIN \
        and len(desks) >= ENTRY_NEGATIVE_DESKS_MIN
    if survived_vote:
        return True, "a probe stare's cluster vote stayed " \
            "unresolved on this cluster"
    if survived_attempts:
        return True, "%d filed negative results from %d desks " \
            "stand against it" % (len(negatives), len(desks))
    return False, "it has not yet survived a probe stare or " \
        "an equivalent body of filed attempts"


def _special(cluster, hearings, vote_result, negatives,
             discrimination, stare, checks):
    """The special anomaly plan's strict trigger, as a
    conjunction over the record. Every condition is required;
    the checks are the self-refutation set, and a candidate
    carries only checks that stand."""
    if len(hearings) < SPECIAL_MIN_HEARINGS:
        return False
    span = hearings[-1]["t_start"] - hearings[0]["t_start"]
    watches = {e["watch_id"] for e in hearings}
    if span < SPECIAL_MIN_SPAN_S and len(watches) < 2:
        return False
    if not vote_result or vote_result.get("family"):
        return False
    if vote_result.get("leading") not in (None, "unknown") \
            and vote_result.get("share", 0) >= probe.VOTE_MARGIN:
        return False
    desks = {n["attempted_desk"] for n in negatives}
    if len(desks) < SPECIAL_MIN_NEGATIVE_DESKS:
        return False
    snrs = [(e.get("data") or {}).get("snr_db") for e in hearings]
    snrs = [float(s) for s in snrs if isinstance(s, (int, float))]
    if not snrs or max(snrs) < SPECIAL_MIN_SNR_DB:
        return False
    if stare is None:
        return False
    cls = (stare.get("data") or {}).get("classification") or {}
    if (stare.get("data") or {}).get("silent"):
        return False
    if cls.get("family") in ("noise",):
        return False
    if any(c["outcome"] != "stands" for c in checks):
        return False
    return True


# ------------------------------------------------------------ lifecycle

def _record_for(index, events, cluster, hearings, now,
                vote_assessments, neg_events):
    """Assemble one cluster's full register record from the
    Index as it stands: negatives, discrimination, vote,
    statement, self-refutation, evidence. The caller has
    already matched the cluster's vote assessments and
    negative results for the entry-bar judgement, and passes
    them in, so the expensive assembly - the execution
    context, the discrimination folds - runs only for a
    cluster that earns it or already holds an entry."""
    negatives = [{
        "event_id": e["id"],
        "attempted_desk": (e.get("data") or {}).get(
            "attempted_desk") or e["desk"],
        "outcome": (e.get("data") or {}).get("outcome")
        or "no decode",
        "detail": (e.get("data") or {}).get("detail")
        or e.get("content") or "",
        "t": e["t_start"]} for e in neg_events]
    stares = [e for e in _matching(events, index, cluster,
                                   kinds=["observation"])
              if e["desk"] == "probe"]
    stare = stares[-1] if stares else None
    ctx = probe.execution_context(index, {
        "kind": cluster["kind"], "emitter": cluster.get("emitter"),
        "freq_hz": cluster.get("freq_hz")})
    discrimination = _discrimination(cluster, hearings, ctx)
    votes = [(h.get("family") or "unknown", h.get("snr_db"))
             for h in ctx["history"]]
    vote_result = probe.vote(votes) if votes else None
    checks = _self_refutation(index, events, cluster,
                              discrimination, vote_result)
    relevant = list(hearings) + list(stares) + \
        list(vote_assessments) + list(neg_events)
    evidence = _evidence_ids(index, relevant)
    statement = _statement(cluster, vote_result, negatives,
                           discrimination, stare, hearings)
    return {"vote_assessments": vote_assessments,
            "negatives": negatives, "stares": stares,
            "stare": stare, "discrimination": discrimination,
            "vote": vote_result, "self_refutation": checks,
            "evidence": evidence, "statement": statement}


def refresh_register(index, now, profile=None, watch_id=None):
    """Create and refresh register entries from the Index as
    it stands. Called when a probe stare completes and at
    stand-down; idempotent, so a refresh that changes nothing
    files nothing. Returns the summary the callers state:
    created, refreshed, resolved, reopened, and the entries
    newly raised to special candidate (whose alert product
    the caller files in the standing watch's folder)."""
    min_obs = 2
    if profile:
        min_obs = int((profile.get("probe") or {}).get(
            "min_observations", 2))
    events = index.events(limit=1000000)
    existing = {e["cluster"]["key"]: e
                for e in index.register_rows()}
    summary = {"created": 0, "refreshed": 0, "resolved": 0,
               "reopened": 0, "special_new": []}
    for cluster, hearings in _clusters(index, events):
        key = cluster["key"]
        entry = existing.get(key)
        decodes = [e for e in _matching(events, index, cluster,
                                        kinds=["decode"])
                   if e["grade"] == "CONFIRMED"]
        if entry is not None and entry["state"] == STATE_RESOLVED:
            # A resolved entry is judged on what stands after
            # its resolution: fresh unexplained hearings with
            # no later decode explaining them reopen it, and
            # the reopening is stated in its history. The
            # decode that resolved it predates the fresh
            # hearings and does not answer them.
            resolved_at = entry.get("resolved_at") or 0
            fresh = [e for e in hearings
                     if e["t_start"] > resolved_at]
            fresh_decodes = [
                e for e in decodes
                if ((e.get("data") or {}).get("found_at")
                    or e["t_start"]) > resolved_at]
            if not fresh or fresh_decodes:
                continue
            vote_assessments = _matching(
                events, index, cluster, kinds=["assessment"],
                rules=["probe_cluster_vote"])
            neg_events = _matching(
                events, index, cluster, kinds=["assessment"],
                rules=["probe_negative_result"])
            record = _record_for(index, events, cluster,
                                 hearings, now, vote_assessments,
                                 neg_events)
            entry["state"] = STATE_OPEN
            entry["reopened_at"] = float(now)
            entry["resolution"] = None
            entry["resolved_event"] = None
            entry.setdefault("history", []).append({
                "t": float(now),
                "note": "reopened: %d fresh unexplained "
                        "hearing(s) on the resolved cluster"
                        % len(fresh)})
            summary["reopened"] += 1
            _apply_record(index, entry, cluster, hearings,
                          record, now)
            index.upsert_register(entry)
            continue
        if decodes:
            if entry is not None:
                _resolve_entry(index, entry, decodes[-1], now,
                               summary)
            continue
        vote_assessments = _matching(
            events, index, cluster, kinds=["assessment"],
            rules=["probe_cluster_vote"])
        neg_events = _matching(
            events, index, cluster, kinds=["assessment"],
            rules=["probe_negative_result"])
        neg_summary = [{
            "attempted_desk": (e.get("data") or {}).get(
                "attempted_desk") or e["desk"]}
            for e in neg_events]
        qualifies, _why = _bar(cluster, hearings,
                               vote_assessments, neg_summary,
                               min_obs)
        if entry is None and not qualifies:
            continue
        record = _record_for(index, events, cluster, hearings,
                             now, vote_assessments, neg_events)
        if entry is None:
            entry = {"id": util.new_id("an"),
                     "format": REGISTER_FORMAT,
                     "cluster": {k: cluster.get(k) for k in (
                         "kind", "emitter", "freq_hz", "bucket",
                         "bucket_hz", "key")},
                     "state": STATE_OPEN,
                     "created_at": float(now),
                     "history": [{"t": float(now),
                                  "note": "entered the register: "
                                          "%s" % _why}]}
            summary["created"] += 1
            _apply_record(index, entry, cluster, hearings,
                          record, now)
            if _special(cluster, hearings, record["vote"],
                        record["negatives"],
                        record["discrimination"], record["stare"],
                        record["self_refutation"]):
                entry["state"] = STATE_SPECIAL
                entry["history"].append({
                    "t": float(now),
                    "note": "raised to special candidate under "
                            "the strict trigger: it stands as a "
                            "candidate and nothing more"})
                summary["special_new"].append(entry)
            index.upsert_register(entry)
            continue
        # An open entry, refreshed against the record as it
        # now stands.
        was_special = entry["state"] == STATE_SPECIAL
        _apply_record(index, entry, cluster, hearings, record,
                      now)
        if not was_special and _special(
                cluster, hearings, record["vote"],
                record["negatives"], record["discrimination"],
                record["stare"], record["self_refutation"]):
            entry["state"] = STATE_SPECIAL
            entry.setdefault("history", []).append({
                "t": float(now),
                "note": "raised to special candidate under "
                        "the strict trigger: it stands as a "
                        "candidate and nothing more"})
            summary["special_new"].append(entry)
        index.upsert_register(entry)
        summary["refreshed"] += 1
    return summary


def _apply_record(index, entry, cluster, hearings, record, now):
    """Fold one assembled record into an entry and seal its
    evidence under the tiers."""
    entry["first_seen"] = min(e["t_start"] for e in hearings)
    entry["last_seen"] = max(e["t_start"] for e in hearings)
    entry["hearings"] = len(hearings)
    entry["watches"] = sorted({e["watch_id"] for e in hearings})
    entry["band"] = band_name(cluster.get("freq_hz")) \
        if cluster.get("freq_hz") else "unknown band"
    entry["evidence"] = record["evidence"]
    entry["negative_results"] = record["negatives"]
    entry["discrimination"] = record["discrimination"]
    entry["vote"] = record["vote"]
    entry["probe_stares"] = [e["id"] for e in record["stares"]]
    entry["self_refutation"] = record["self_refutation"]
    entry["statement"] = record["statement"]
    entry["updated_at"] = float(now)
    if record["evidence"]:
        index.seal_snippets(record["evidence"])


def _resolve_entry(index, entry, resolving_ev, now, summary=None):
    retro = bool((resolving_ev.get("data") or {}).get(
        "retroactive"))
    entry["state"] = STATE_RESOLVED
    entry["resolved_event"] = resolving_ev["id"]
    entry["resolved_at"] = float(now)
    entry["resolved_via"] = (
        "retroactive decode by %s" % (resolving_ev.get("data")
                                      or {}).get("decoder")
        if retro else "decode by the %s desk"
        % resolving_ev["desk"])
    entry["resolution"] = resolving_ev.get("content") or \
        "decoded by the %s desk" % resolving_ev["desk"]
    entry.setdefault("history", []).append({
        "t": float(now),
        "note": "resolved: %s (event %s)"
                % (entry["resolved_via"], resolving_ev["id"])})
    entry["updated_at"] = float(now)
    index.upsert_register(entry)
    if summary is not None:
        summary["resolved"] += 1
    return entry


def resolve_from_event(index, ev, now):
    """Close a registered cluster's entry when one event
    resolves it: the hook retroactive exploitation calls as
    it files a decode, so the entry closes with the decode
    linked at the moment the find lands, not at the next
    refresh. Returns the closed entry, or None."""
    if ev.get("kind") not in ("decode", "imagery"):
        return None
    if ev.get("grade") != "CONFIRMED":
        return None
    for entry in index.register_rows():
        if entry["state"] == STATE_RESOLVED:
            continue
        cluster = entry["cluster"]
        if _matches(index, ev, cluster):
            return _resolve_entry(index, entry, ev, now)
    return None


# -------------------------------------------------------------- surfaces

def entries(index):
    return index.register_rows()


def open_entries(index):
    return [e for e in index.register_rows()
            if e["state"] in (STATE_OPEN, STATE_SPECIAL)]


def register_account(index):
    """The register's size and its sealed bytes, as doctor
    states them: entries by state, and the sealed account
    the retention tiers keep for the whole home."""
    rows = index.register_rows()
    acct = retention.account(index.snippet_rows_all())
    return {"entries": len(rows),
            "open": len([e for e in rows
                         if e["state"] == STATE_OPEN]),
            "special": len([e for e in rows
                            if e["state"] == STATE_SPECIAL]),
            "resolved": len([e for e in rows
                              if e["state"] == STATE_RESOLVED]),
            "sealed_bytes": acct["sealed"]["bytes"],
            "sealed_count": acct["sealed"]["count"]}


def entry_text(entry):
    """One entry in full, as the ask verb answers it."""
    cluster = entry["cluster"]
    lines = ["Anomaly register entry %s - %s"
             % (entry["id"], cluster_label(cluster)),
             "State: %s. First heard %s, last heard %s, %d "
             "hearings across %d watch(es)."
             % (entry["state"],
                util.fmt_stamp(entry["first_seen"]),
                util.fmt_stamp(entry["last_seen"]),
                entry["hearings"], len(entry.get("watches")
                                        or [])),
             "Evidence: %d retained snippet(s), sealed under "
             "the retention tiers." % len(entry.get("evidence")
                                          or []),
             "Negative results: %d filed attempt(s)."
             % len(entry.get("negative_results") or [])]
    for n in entry.get("negative_results") or []:
        lines.append("- %s: %s" % (n["attempted_desk"],
                                   n["detail"]))
    disc = entry.get("discrimination") or {}
    rec = disc.get("recurrence") or {}
    if rec:
        lines.append("Discrimination: %s"
                     % (rec.get("verdict") or "not computed"))
    st = entry.get("statement") or {}
    lines.append("Resembles: %s" % st.get("resembles", ""))
    lines.append("Is not: %s" % st.get("is_not", ""))
    lines.append("Would resolve: %s" % st.get("would_resolve",
                                              ""))
    if entry["state"] == STATE_RESOLVED:
        lines.append("Resolution: %s (event %s)."
                     % (entry.get("resolution"),
                        entry.get("resolved_event")))
    if entry["state"] == STATE_SPECIAL:
        lines.append("This entry stands as a candidate and "
                     "nothing more: one station cannot settle "
                     "an event of this class, and the register "
                     "states it exactly that way.")
    return "\n".join(lines)


def find_entry(index, text):
    """An entry by its register id, its emitter, or a
    frequency the ask verb was given, in that order."""
    rows = index.register_rows()
    for e in rows:
        if e["id"] == text:
            return e
    for e in rows:
        if e["cluster"].get("emitter") == text:
            return e
    try:
        freq = float(text)
    except (TypeError, ValueError):
        freq = None
    if freq is not None:
        for e in rows:
            if e["cluster"].get("bucket") is not None and \
                    probe._bucket(freq) == e["cluster"]["bucket"]:
                return e
    low = text.lower()
    for e in rows:
        if low in cluster_label(e["cluster"]).lower():
            return e
    return None


def write_special_alert(index, watch_id, entry):
    """The special candidate's own alert product, separate
    from the FLASH line: a file in the standing watch's
    folder, worded as a candidate and nothing more. The
    operator is the only reader this product presumes;
    nothing in it is an announcement, and the register makes
    none."""
    folder = index.watch_dir(watch_id)
    import glob as _glob
    seq = len(_glob.glob(os.path.join(
        folder, "SPECIAL-CANDIDATE-*.md"))) + 1
    name = "SPECIAL-CANDIDATE-%03d.md" % seq
    st = entry.get("statement") or {}
    lines = [
        "SPECIAL CANDIDATE %03d - register entry %s"
        % (seq, entry["id"]),
        "Cluster: %s, heard %d times, first heard %s, last "
        "heard %s." % (cluster_label(entry["cluster"]),
                       entry["hearings"],
                       util.fmt_stamp(entry["first_seen"]),
                       util.fmt_stamp(entry["last_seen"])),
        "This cluster stands in the anomaly register as a "
        "candidate and nothing more. One station cannot "
        "settle an event of this class; the entry states "
        "what the record holds and claims nothing past it.",
        "Resembles: %s" % st.get("resembles", ""),
        "Is not: %s" % st.get("is_not", ""),
        "Would resolve: %s" % st.get("would_resolve", ""),
        "The station received only. It has transmitted "
        "nothing in answer, and it will not.",
        "",
    ]
    util.write_text(os.path.join(folder, name), "\n".join(lines))
    return name


def render_section(index, watch_id):
    """The Watch Report's anomaly chapter, rendered from the
    register: special candidates first, then the open
    entries with their three-part statements, then the
    resolutions since the last watch."""
    lines = ["## 6. Anomaly chapter"]
    rows = index.register_rows()
    meta = index.get_watch_meta(watch_id) or {}
    start = meta.get("start", 0)
    if not rows:
        lines.append(
            "The anomaly register holds no entries. No "
            "unexplained cluster has met the entry bar - "
            "recurring, unresolved, and survived the "
            "system's attempts to explain it - and one-off "
            "unresolved hearings are catches, not register "
            "entries.")
        return lines
    acct = register_account(index)
    lines.append(
        "The anomaly register holds %d entries: %d open, %d "
        "standing as special candidates, %d resolved. "
        "Registered evidence is sealed under the retention "
        "tiers: %d bytes in %d files, never shed."
        % (acct["entries"], acct["open"], acct["special"],
           acct["resolved"], acct["sealed_bytes"],
           acct["sealed_count"]))
    opens = [e for e in rows if e["state"] != STATE_RESOLVED]
    opens.sort(key=lambda e: (0 if e["state"] == STATE_SPECIAL
                              else 1, e["first_seen"]))
    for e in opens:
        st = e.get("statement") or {}
        lines.append(
            "- %s [%s] %s: %d hearings, first heard %s, last "
            "heard %s; %d retained evidence file(s), sealed; "
            "%d negative result(s) on file."
            % (e["id"], e["state"], cluster_label(e["cluster"]),
               e["hearings"], util.fmt_stamp(e["first_seen"]),
               util.fmt_stamp(e["last_seen"]),
               len(e.get("evidence") or []),
               len(e.get("negative_results") or [])))
        lines.append("  Resembles: %s" % st.get("resembles", ""))
        lines.append("  Is not: %s" % st.get("is_not", ""))
        lines.append("  Would resolve: %s"
                     % st.get("would_resolve", ""))
        if e["state"] == STATE_SPECIAL:
            lines.append("  This entry stands as a candidate "
                         "and nothing more.")
    resolved = [e for e in rows if e["state"] == STATE_RESOLVED
                and (e.get("resolved_at") or 0) >= start]
    if resolved:
        lines.append("Resolutions since the last watch:")
        for e in resolved:
            lines.append(
                "- %s %s resolved: %s (resolving event %s, "
                "by %s)."
                % (e["id"], cluster_label(e["cluster"]),
                   e.get("resolution"), e.get("resolved_event"),
                   e.get("resolved_via")))
    return lines
