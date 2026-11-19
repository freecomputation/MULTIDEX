"""Dissemination: FLASH, SITREP, the Watch Report, and ask (section 10).

Every number in a product comes from the Index at render time. Every
product carries or points at its coverage account in its first lines.
Products are plain markdown: plain sentences, dash-led lines only
where a listing is needed, and no em dash characters anywhere (the
writer in util refuses them).
"""

import glob
import os
import time

from . import changelog, util
from .bands import BANDS, band_name
from .desks import SPECS, TRANSCRIPTION_REASON, transcription_state
from .exploit import GRADE_ORDER, day_picture

CONSEQUENCE = {
    "ais": "the marine picture rests on sweep observations only",
    "adsb": "the air picture has no position reports",
    "airband": "no voice activity is reported for the airband",
    "acars": "no datalink messages are reported",
    "wxradio": "weather alerts would be missed while the desk is down",
    "satellite": "satellite passes are predicted but not received",
    "sonde": "radiosonde flights are not tracked",
    "ism": "the device population is not reported",
    "aprs": "packet stations are not reported",
    "pager": "paging traffic is not reported",
    "sweep": "the spectrum census has no observations",
    "cellular": "the cellular census is not reported",
    "trunking": "trunked systems are not censused",
    "broadcast": "the broadcast census is not reported",
    "hfdl": "no HFDL datalink messages are reported; HF spectrum "
            "rests on the sweep and audio census desks",
}


def _meta(index, watch_id):
    return index.get_watch_meta(watch_id) or {}


def _bill(index, watch_id):
    path = os.path.join(index.watch_dir(watch_id), "bill.json")
    return util.read_json(path, default=[]) or []


def coverage_stats(index, watch_id, t_from=None, t_to=None):
    """The coverage account's raw material, from dwell records alone."""
    meta = _meta(index, watch_id)
    bill = _bill(index, watch_id)
    dwells = index.dwell_records(watch_id)
    if t_from is not None:
        dwells = [d for d in dwells if t_to is None
                  or (t_from <= d["t_start"] < t_to)]
        bill = [b for b in bill if t_from <= b["start"] < (t_to or 1e18)]
    executed_ids = {d["data"].get("bill_id") for d in dwells}
    executed_s = sum(d["t_end"] - d["t_start"] for d in dwells)
    planned_s = sum(b["dur"] for b in bill)
    missed = [b for b in bill if b["id"] not in executed_ids]
    gaps = index.events(watch_id=watch_id, kinds=["gap"], limit=10000)
    if t_from is not None:
        gaps = [g for g in gaps if t_from <= g["t_start"] < (t_to or 1e18)]
    visited = set()
    for d in dwells:
        if d["freq_hz"]:
            visited.add(band_name(d["freq_hz"]))
    for ev in index.events(watch_id=watch_id, kinds=["observation"],
                           limit=100000):
        if ev["freq_hz"]:
            visited.add(band_name(ev["freq_hz"]))
    missing_bands = [name for _lo, _hi, name in BANDS
                     if name not in visited]
    return {"executed_s": executed_s, "planned_s": planned_s,
            "dwells": dwells, "missed": missed, "gaps": gaps,
            "bands_visited": sorted(visited),
            "bands_missing": missing_bands,
            "source": meta.get("source", "unknown")}


def _source_line(meta):
    if meta.get("source_note"):
        return "Source note: %s" % meta["source_note"]
    if meta.get("source") == "simulated":
        return ("Source: simulated spectrum (deterministic synthetic "
                "traffic, no hardware). Every event in this watch is "
                "synthetic and is labeled as such by this line.")
    return "Source: real receiver via the real source."


def _flash_files(index, watch_id):
    return sorted(glob.glob(os.path.join(index.watch_dir(watch_id),
                                         "FLASH-*.md")))


def maybe_flash(index, watch_id, ev, profile):
    """Write a FLASH file if the event crosses a standing trigger.

    Returns the filename or None. Triggers are the design's defaults:
    alert-kind events, assessments at or over the threshold grade,
    completed passes the desks flag, watch-list emitters, and
    activity on watch-list frequencies.
    """
    flash = profile["flash"]
    reason = None
    if ev["kind"] == "alert":
        reason = "alert"
    elif ev["kind"] == "assessment" and GRADE_ORDER[ev["grade"]] >= \
            GRADE_ORDER[flash["threshold_grade"]]:
        reason = "assessment"
    elif ev["kind"] == "pass" and ev["data"].get("flash"):
        reason = "pass complete"
    elif ev["kind"] == "pass" and ev["desk"] == "satellite" \
            and ev["grade"] == "CONFIRMED" \
            and not ev["data"].get("acknowledged"):
        # An acknowledged pass is graded CONFIRMED but nothing
        # was captured for it (the METEOR acknowledgement
        # policy: no recording, no decode, a zero-byte
        # allocation), so it must never raise a FLASH whose
        # reason claims a capture.
        reason = "satellite pass captured"
    elif ev["kind"] == "imagery" and ev["data"].get("imagery"):
        reason = "satellite imagery decoded"
    elif ev.get("emitter") and ev["emitter"] in flash["watch_emitters"]:
        reason = "watch-list emitter"
    elif ev["freq_hz"] and any(abs(ev["freq_hz"] - f) < 1.0
                               for f in flash["watch_freqs_hz"]) \
            and ev["kind"] in ("voice", "observation"):
        reason = "watch-list frequency"
    if reason is None:
        return None
    seq = len(_flash_files(index, watch_id)) + 1
    name = "FLASH-%03d.md" % seq
    where = "%s, %s" % (util.fmt_hz(ev["freq_hz"]),
                        band_name(ev["freq_hz"]))
    lines = [
        "FLASH %03d - %s" % (seq, util.fmt_stamp(ev["t_start"])),
        "What: %s" % (ev["content"] or ev["data"].get("rule")
                      or ev["kind"]),
        "Where: %s" % where,
        "When: %s (trigger: %s)" % (util.fmt_stamp(ev["t_start"]), reason),
        "Confidence: %s" % ev["grade"],
        "Events: %s" % ", ".join([ev["id"]] + list(ev["links"])),
        "",
    ]
    util.write_text(os.path.join(index.watch_dir(watch_id), name),
                    "\n".join(lines))
    # The FLASH chime (profile [alerts]): sounded after the file is
    # written, never instead of it, and its outcome is recorded in
    # the triggering event's data. The caller persists the event
    # after this returns, so the record lands with the event.
    if profile.get("alerts", {}).get("chime", True):
        from .chime import play_chime
        ev["data"]["chime"] = play_chime(index.watch_dir(watch_id))
    return name


# ---------------------------------------------------------------- SITREP

def render_sitrep(index, watch_id, t_from, t_to, seq):
    meta = _meta(index, watch_id)
    cov = coverage_stats(index, watch_id, t_from, t_to)
    picture = day_picture(index, watch_id)
    period_events = index.events(watch_id=watch_id, t_from=t_from,
                                 t_to=t_to, limit=100000)
    pops = {}
    for ev in period_events:
        if ev["emitter"]:
            pops.setdefault(ev["desk"], set()).add(
                index.resolve(ev["emitter"]))
    lines = ["SITREP %d - watch %s" % (seq, watch_id),
             "Period: %s to %s" % (util.fmt_stamp(t_from),
                                   util.fmt_stamp(t_to)),
             _source_line(meta),
             "Coverage this period: %s of dwell time executed across "
             "%d dwells; %d gaps recorded. Bands not visited this "
             "period are listed in the Watch Report's coverage account."
             % (util.fmt_duration(cov["executed_s"]), len(cov["dwells"]),
                len(cov["gaps"])),
             "",
             "Population this period:"]
    if pops:
        for desk in sorted(pops):
            lines.append("- %s: %d distinct emitters"
                         % (desk, len(pops[desk])))
    else:
        lines.append("- none heard")
    lines += ["", "Notables:"]
    notables = [e for e in period_events
                if e["kind"] in ("alert", "assessment", "pass")]
    if notables:
        for ev in notables[:20]:
            lines.append("- %s %s: %s [%s]"
                         % (util.fmt_time(ev["t_start"]), ev["kind"],
                            ev["content"] or ev["data"].get("rule", ""),
                            ev["grade"]))
    else:
        lines.append("- nothing above the ordinary")
    flashes = _flash_files(index, watch_id)
    lines += ["", "FLASH roll: %d alert(s) this watch (%s)"
              % (len(flashes),
                 ", ".join(os.path.basename(f) for f in flashes)
                 if flashes else "none"),
              "", "Desk health:"]
    health = index.events(watch_id=watch_id, kinds=["desk_health"],
                          t_from=t_from, t_to=t_to, limit=1000)
    if health:
        for ev in health:
            lines.append("- %s" % ev["content"])
    else:
        lines.append("- all standing desks healthy")
    lines += ["", "Day picture so far: %d events, %d passes taken, "
              "%d missed, %d alerts, %d assessments."
              % (sum(d["events"] for d in picture["by_desk"].values()),
                 picture["passes_taken"], picture["passes_missed"],
                 picture["alerts"], picture["assessments"]),
              ""]
    return "\n".join(lines)


# ------------------------------------------------------------- change log

def write_changelog(index, watch_id):
    """Compute the watch's change log and file it as
    changelog.json in the watch folder (0.13.0). The officer
    calls this at stand-down as part of the product set, after
    the baselines fold and before the Watch Report renders, so
    the report's chapter 5 and the filed data are one
    computation. Returns the computed account."""
    log = changelog.compute_changelog(index, watch_id)
    util.write_json(os.path.join(index.watch_dir(watch_id),
                                 "changelog.json"), log)
    return log


# ----------------------------------------------------------- Watch Report

def render_report(index, watch_id):
    meta = _meta(index, watch_id)
    cov = coverage_stats(index, watch_id)
    picture = day_picture(index, watch_id)
    start = meta.get("start", 0)
    end = meta.get("end") or meta.get("planned_end", 0)
    total_events = index.count_events(watch_id=watch_id)
    dossiers = index.dossiers_in_watch(watch_id)
    new_dossiers = [d for d in dossiers if d["first_seen"] >= start]
    changed = [d for d in dossiers if d["first_seen"] < start]
    assessments = index.assessments(watch_id)
    health = index.events(watch_id=watch_id, kinds=["desk_health"],
                          limit=10000)
    passes = index.events(watch_id=watch_id, kinds=["pass"],
                          limit=10000)
    alerts = index.events(watch_id=watch_id, kinds=["alert"], limit=10000)
    snaps = index.snippet_accounting(watch_id)
    ceiling = int(meta.get("disk_ceiling_gb", 20) * 1e9)
    L = []
    L.append("WATCH REPORT - %s" % watch_id)
    L.append("Watch: %s to %s" % (util.fmt_stamp(start),
                                  util.fmt_stamp(end)))
    L.append(_source_line(meta))
    L.append("Coverage summary: %s of %s planned dwell time executed; "
             "%d gaps; %d bill items never executed. The full coverage "
             "account is chapter 2."
             % (util.fmt_duration(cov["executed_s"]),
                util.fmt_duration(cov["planned_s"]), len(cov["gaps"]),
                len(cov["missed"])))
    L.append("")
    L.append("## 1. Executive summary")
    L.append("The watch stood for %s over %s. It filed %d events "
             "resolving to %d emitter dossiers, took %d satellite or "
             "sonde passes and missed %d, raised %d alerts and %d "
             "assessments."
             % (util.fmt_duration(end - start), meta.get("region",
                "the home region"), total_events, len(dossiers),
                picture["passes_taken"], picture["passes_missed"],
                len(alerts), len(assessments)))
    if meta.get("ended_early"):
        L.append("The watch ended early: %s. This report covers the "
                 "elapsed portion only, and its coverage account says "
                 "what the watch therefore cannot speak to."
                 % meta.get("end_reason", "operator stop"))
    if meta.get("resumed"):
        L.append("This watch was resumed from on-disk state after an "
                 "interruption; the gap is in the coverage account.")
    busiest = max(picture["by_desk"].items(),
                  key=lambda kv: kv[1]["events"], default=(None, None))
    if busiest[0]:
        L.append("The busiest desk was %s with %d events. The day's "
                 "rhythm is in the day picture, chapter 3."
                 % (busiest[0], busiest[1]["events"]))
    L.append("")
    L.append("## 2. Coverage account")
    L.append("Planned dwell time %s; executed %s across %d dwells."
             % (util.fmt_duration(cov["planned_s"]),
                util.fmt_duration(cov["executed_s"]),
                len(cov["dwells"])))
    if cov["missed"]:
        L.append("Bill items never executed:")
        for b in cov["missed"][:40]:
            L.append("- %s (%s, class %s) at %s"
                     % (b["label"], b["kind"], b["cls"],
                        util.fmt_time(b["start"])))
    if cov["gaps"]:
        L.append("Gaps recorded:")
        for g in cov["gaps"]:
            L.append("- %s: %s" % (util.fmt_stamp(g["t_start"]),
                                   g["content"]))
    else:
        L.append("No tuner gaps were recorded.")
    L.append("Bands visited: %s." % ", ".join(cov["bands_visited"]))
    if cov["bands_missing"]:
        L.append("Bands never visited, so this watch cannot speak to "
                 "them: %s." % ", ".join(cov["bands_missing"]))
    benched = sorted({e["data"].get("desk") for e in health
                      if e["data"].get("benched")})
    for desk in benched:
        if desk:
            L.append("While %s was benched, %s." % (desk, CONSEQUENCE.get(
                desk, "that service is unreported")))
    stood_reasons = {e["data"].get("desk"): e["data"].get("reason")
                     for e in health if e["data"].get("stood_down")}
    for desk in sorted(stood_reasons):
        if desk:
            L.append("The %s desk stood down at this home: %s. "
                     "This watch cannot speak to that service here."
                     % (desk, stood_reasons[desk]))
    L.append("Antenna for this watch: %s." % meta.get("antenna",
                                                      "not recorded"))
    if meta.get("source") == "real" and "hfdl" not in benched:
        L.append("HFDL stood this watch, and its coverage is "
                 "opportunistic: the fixed antenna is a VHF/UHF "
                 "antenna, so the desk reports the frames the "
                 "bound decoder heard on the rotation's HF "
                 "windows and claims nothing about HFDL traffic "
                 "beyond them.")
    # The reception account is this watch's slice of the home's
    # standing allocation ledger: the grants it held, what they
    # consumed, what they released unspent, what was shed. The
    # ledger is the system of record; the meta's opening pool
    # summary names the standing cap the slice was drawn from.
    from . import budget as _budget_mod
    _slice = None
    try:
        _ledger = _budget_mod.AllocationLedger.load(index.home)
        _slice = _budget_mod.watch_slice(_ledger, watch_id)
        if not _slice["grants"]:
            _slice = None
    except Exception:
        _slice = None
    if _slice is not None:
        _pool0 = meta.get("reception_pool") or {}
        _cap = _pool0.get("cap_bytes") or int(
            meta.get("disk_ceiling_gb", 20) * 1e9)
        L.append("Reception account: this watch's slice of the "
                 "standing allocation pool (standing cap %d "
                 "bytes). The watch was granted %d bytes across "
                 "%d receptions - %d bytes of it satellite "
                 "passes; it kept %d bytes of evidence, released "
                 "%d bytes unspent back to the pool, and shed "
                 "%d bytes under the ceiling."
                 % (_cap, _slice["granted_bytes"],
                    len(_slice["grants"]), _slice["satellite_bytes"],
                    _slice["consumed_bytes"],
                    _slice["released_bytes"], _slice["shed_bytes"]))
        cls_of = {g["bill_id"]: g["cls"] for g in _slice["grants"]
                  if g.get("bill_id")}
        consumed, shed_bytes = {}, {}
        for row in index.snippet_rows(watch_id):
            if not row["path"]:
                continue
            cls = cls_of.get(
                _budget_mod.bill_id_of_snippet_path(row["path"]))
            if cls is None:
                continue
            if row["shed"]:
                shed_bytes[cls] = shed_bytes.get(cls, 0) + row["bytes"]
            else:
                consumed[cls] = consumed.get(cls, 0) + row["bytes"]
        for cls in sorted(_slice["by_class"]):
            g = _slice["by_class"][cls]
            L.append("Class %s receptions: granted %d bytes; "
                     "%d bytes of evidence kept; %d bytes "
                     "released unspent; %d bytes shed."
                     % (cls, g["granted"],
                        consumed.get(cls, 0), g["released"],
                        shed_bytes.get(cls, 0)))
        _payload_rows = [r for r in index.snippet_rows(watch_id)
                         if r["kind"] == "payload" and not r["shed"]]
        if _payload_rows:
            L.append("Payload-class evidence (vocoder frame "
                     "archives): %d files, %d bytes kept. "
                     "Payload evidence is permanent Tier-0 "
                     "evidence: it is exempt from shedding "
                     "under the ceiling."
                     % (len(_payload_rows),
                        sum(r["bytes"] for r in _payload_rows)))
        for statement in (meta.get("reception_grants") or {}).get(
                "statements") or []:
            L.append("The watch's grants were reduced at "
                     "muster: %s" % statement)
    else:
        rb = meta.get("reception_budget")
        if rb:
            L.append("Reception account: at muster the bill's worst "
                     "case was allocated %d bytes against a disk "
                     "ceiling of %d bytes - %d bytes reserved for "
                     "satellite passes, %d bytes of free margin held "
                     "back." % (rb["total_bytes"], rb["ceiling_bytes"],
                                rb["satellite_reserve_bytes"],
                                rb["free_margin_bytes"]))
            cls_of = {b["id"]: b["cls"] for b in meta.get("bill", [])}
            consumed, shed_bytes = {}, {}
            for row in index.snippet_rows(watch_id):
                if not row["path"]:
                    continue
                cls = cls_of.get(
                    _budget_mod.bill_id_of_snippet_path(row["path"]))
                if cls is None:
                    continue
                if row["shed"]:
                    shed_bytes[cls] = shed_bytes.get(cls, 0) \
                        + row["bytes"]
                else:
                    consumed[cls] = consumed.get(cls, 0) + row["bytes"]
            for cls in sorted(rb["by_class"]):
                L.append("Class %s receptions: allocated %d bytes; "
                         "%d bytes of evidence kept; %d bytes shed."
                         % (cls, rb["by_class"][cls]["bytes"],
                            consumed.get(cls, 0),
                            shed_bytes.get(cls, 0)))
            for statement in rb.get("degraded") or []:
                L.append("The reception budget was degraded at "
                         "muster: %s" % statement)
    # The tier account (0.10.0): what this watch's evidence is
    # made of, by retention tier - held and shed per tier, and
    # sealed evidence apart, as the snippet store recorded it.
    from . import retention as _retention
    _acct = _retention.account(index.snippet_rows(watch_id))
    if any(_acct[_t]["kept_bytes"] or _acct[_t]["shed_bytes"]
           for _t in (0, 1, 2, 3)) or _acct["sealed"]["bytes"]:
        for _t in (0, 1, 2, 3):
            _g = _acct[_t]
            L.append("Tier %d (%s): %d bytes kept in %d files; "
                     "%d bytes shed in %d files."
                     % (_t, _retention.TIER_NAMES[_t],
                        _g["kept_bytes"], _g["kept_count"],
                        _g["shed_bytes"], _g["shed_count"]))
        if _acct["sealed"]["bytes"]:
            L.append("Sealed evidence stands apart from the "
                     "tiers: %d bytes in %d files, never shed."
                     % (_acct["sealed"]["bytes"],
                        _acct["sealed"]["count"]))
    # The retroactive account (0.12.0): the passes this watch
    # ran over the archive, aggregated from the summary events
    # they filed, and the debt still standing in the home when
    # the report renders. Retroactive finds themselves are
    # filed under the watches that filed the captures; this
    # chapter accounts for the work, and the Index holds the
    # finds.
    _retro_evs = [e for e in index.events(
        watch_id=watch_id, kinds=["system"], limit=100000)
        if (e.get("data") or {}).get("retroactive_account")]
    _owed = {}
    try:
        from . import retro as _retro_mod
        _owed = _retro_mod.owed_counts(index)
    except Exception:
        _owed = {}
    if _retro_evs or _owed:
        _examined = sum(e["data"]["retroactive_account"]["examined"]
                        for e in _retro_evs)
        _found = sum(e["data"]["retroactive_account"]["found"]
                     for e in _retro_evs)
        _seeded = sum(e["data"].get("retro_seeded", 0)
                      for e in _retro_evs)
        L.append("Retroactive account: this watch's passes "
                 "re-examined %d retained captures and filed %d "
                 "retroactive events from them; %d captures were "
                 "seeded as already exploited, their decoder's "
                 "product already standing in the record."
                 % (_examined, _found, _seeded))
        _per = {}
        for e in _retro_evs:
            for _name, _cell in (e["data"]["retroactive_account"]
                                 .get("per_decoder") or {}).items():
                _agg = _per.setdefault(_name, {"examined": 0,
                                               "found": 0})
                _agg["examined"] += _cell["examined"]
                _agg["found"] += _cell["found"]
        for _name in sorted(_per):
            L.append("Decoder %s: %d captures examined, %d "
                     "retroactive events filed."
                     % (_name, _per[_name]["examined"],
                        _per[_name]["found"]))
        if _owed:
            L.append("Retroactive debt still standing in this "
                     "home: %s. The debt carries to the next "
                     "watch; it is a property of the archive, "
                     "not of this watch."
                     % ", ".join("%s %d" % (n, _owed[n])
                                 for n in sorted(_owed)))
    L.append("")
    L.append("## 3. Day picture, service by service")
    for desk in sorted(picture["by_desk"]):
        info = picture["by_desk"][desk]
        tier = SPECS.get(desk, (0, desk))[0]
        L.append("- %s (tier %s): %d events, %d distinct emitters"
                 % (desk, tier, info["events"], info["emitters"]))
    L.append("")
    L.append("## 4. Emitter index delta")
    L.append("New dossiers opened this watch: %d. Dossiers heard again "
             "this watch: %d." % (len(new_dossiers), len(changed)))
    for d in new_dossiers[:30]:
        L.append("- new: %s (%s), first heard %s"
                 % (d["key"], d["kind"], util.fmt_stamp(d["first_seen"])))
    missing = [a for a in assessments
               if a["data"].get("rule") == "missing_regular"]
    if missing:
        L.append("Regulars gone silent:")
        for a in missing:
            L.append("- %s" % a["content"])
    L.append("")
    # The change log (0.13.0): the same computation the officer
    # files as changelog.json at stand-down, rendered here as
    # the report's chapter 5. Pure Index computation, so the
    # prose and the data cannot disagree.
    L.extend(changelog.render_section(
        changelog.compute_changelog(index, watch_id)))
    L.append("")
    L.append("## 6. Anomaly chapter")
    if assessments:
        for a in assessments:
            L.append("- %s [%s] %s (rule: %s; baseline: %s; evidence: "
                     "%s)" % (util.fmt_stamp(a["t_start"]), a["grade"],
                              a["content"], a["data"].get("rule"),
                              a["data"].get("baseline", "none"),
                              ", ".join(a["links"]) or "none"))
    else:
        L.append("No assessments were filed this watch.")
    L.append("")
    L.append("## 7. Satellite and sonde chapter")
    if passes:
        for p in passes:
            L.append("- %s %s [%s]: %s"
                     % (util.fmt_stamp(p["t_start"]), p["desk"],
                        p["grade"], p["content"]))
            if p["snippet"]:
                L.append("  imagery filed: %s" % p["snippet"])
    else:
        L.append("No passes were taken or attempted this watch.")
    L.append("Pass predictions are graded PROBABLE: the propagator is "
             "a simplified Keplerian model on a spherical Earth, a "
             "scheduling instrument and not an ephemeris.")
    L.append("")
    L.append("## 8. Transcript highlights")
    voice = index.count_events(watch_id=watch_id, kind="voice")
    transcripts = [ev for ev in index.events(
        watch_id=watch_id, desk="audiocensus", kinds=("decode",))
        if (ev.get("data") or {}).get("machine_transcript")]
    if transcripts:
        L.append("Voice transmissions heard: %d. Machine "
                 "transcripts produced: %d, from grade-A census "
                 "snippets inside the bounded transcription "
                 "budget. They are unverified machine transcripts "
                 "and inherit their snippet's grade."
                 % (voice, len(transcripts)))
        for ev in transcripts[:3]:
            L.append("- %s: \"%s\""
                     % (ev.get("emitter") or "unknown audio",
                        (ev["data"].get("transcript") or "")[:240]))
    else:
        _tstate, _treason = transcription_state()
        if _tstate == "bound":
            detail = ("the engine is bound, but no snippet "
                      "passed the grade-A triage gate inside "
                      "the budget this watch")
        else:
            detail = _treason
        L.append("Voice transmissions heard: %d. Transcription "
                 "state: %s. No transcript is quoted in this "
                 "report, because none was produced; voice "
                 "evidence is filed as audio snippets under the "
                 "snippet accounting in the appendix."
                 % (voice, detail))
    L.append("")
    L.append("## 9. Desk health and failure account")
    if health:
        for ev in health:
            L.append("- %s %s" % (util.fmt_stamp(ev["t_start"]),
                                  ev["content"]))
    else:
        L.append("No desk failed, crashed, was benched or stood "
                 "down this watch.")
    L.append("")
    L.append("## 10. Statistics appendix")
    L.append("Events by desk:")
    for desk, n in sorted(index.counts_by_desk(watch_id).items()):
        L.append("- %s: %d" % (desk, n))
    L.append("Events by confidence grade:")
    for grade, n in sorted(index.counts_by_grade(watch_id).items()):
        L.append("- %s: %d" % (grade, n))
    by_class = {}
    for b in _bill(index, watch_id):
        slot = by_class.setdefault(b["cls"], {"planned": 0, "done": 0})
        slot["planned"] += b["dur"]
    done_ids = {d["data"].get("bill_id") for d in cov["dwells"]}
    for b in _bill(index, watch_id):
        if b["id"] in done_ids:
            by_class[b["cls"]]["done"] += b["dur"]
    L.append("Dwell accounting against the bill, by class:")
    for cls in sorted(by_class):
        L.append("- class %s: %s planned, %s executed"
                 % (cls, util.fmt_duration(by_class[cls]["planned"]),
                    util.fmt_duration(by_class[cls]["done"])))
    L.append("Snippet accounting: %d kept (%d bytes), %d shed "
             "(%d bytes), against a ceiling of %d bytes."
             % (snaps["kept_count"], snaps["kept_bytes"],
                snaps["shed_count"], snaps["shed_bytes"], ceiling))
    L.append("Total events in the Index for this watch: %d."
             % total_events)
    L.append("")
    return "\n".join(L)


# -------------------------------------------------------------------- ask

def _parse_freq(text):
    text = text.strip().lower().replace(",", "")
    mult = 1.0
    if text.endswith("mhz"):
        mult, text = 1e6, text[:-3]
    elif text.endswith("khz"):
        mult, text = 1e3, text[:-3]
    elif text.endswith("hz"):
        text = text[:-2]
    try:
        return float(text) * mult
    except ValueError:
        return None


def ask(index, query):
    """The Index query surface: emitter, frequency, band, or service."""
    q = query.strip()
    if q.lower() in ("changes", "change log", "changelog"):
        # The standing change account (0.13.0): the most recent
        # watch's counts, briefly. The filed changelog.json is
        # read where it stands; a watch whose stand-down
        # predates the change log gets the same computation
        # live, so the answer is the record either way.
        watch_id = resolve_current_watch(index.home,
                                         allow_complete=True)
        if not watch_id:
            return ("No watch has stood in this home yet; there "
                    "is no change log.")
        path = os.path.join(index.watch_dir(watch_id),
                            "changelog.json")
        log = util.read_json(path, default=None)
        if log is None:
            log = changelog.compute_changelog(index, watch_id)
        counts = log["counts"]
        base = log["baseline"]
        return ("Change log for watch %s: %d new, %d vanished, "
                "%d moved, %d rhythm-changed; %d established "
                "emitter(s) unwatched. Baseline: %d earlier "
                "watch(es), %s. The full account is changelog.json "
                "in the watch folder and chapter 5 of its Watch "
                "Report."
                % (watch_id, counts["new"], counts["vanished"],
                   counts["moved"], counts["rhythm-changed"],
                   counts["unwatched"], base["watch_count"],
                   "provisional" if base["provisional"]
                   else "established"))
    dossier = index.dossier(q)
    if dossier is not None:
        lines = ["Dossier: %s (%s)" % (dossier["key"], dossier["kind"]),
                 "First heard %s, last heard %s, across %d watch(es), "
                 "%d events."
                 % (util.fmt_stamp(dossier["first_seen"]),
                    util.fmt_stamp(dossier["last_seen"]),
                    len(dossier["watches"]), dossier["events_count"])]
        if len(dossier["aliases"]) > 1:
            lines.append("Also known as: %s"
                         % ", ".join(a for a in dossier["aliases"]
                                     if a != dossier["key"]))
        lines.append("Recent events:")
        for ev in index.events(emitter=q, limit=10, newest=True):
            lines.append("- %s %s %s [%s] %s"
                         % (util.fmt_stamp(ev["t_start"]), ev["desk"],
                            ev["kind"], ev["grade"],
                            ev["content"] or ""))
        return "\n".join(lines)
    freq = _parse_freq(q)
    if freq is not None:
        tol = max(12500.0, freq * 0.005)
        evs = index.events_near_freq(freq, tol, limit=50, newest=True)
        lines = ["%d events within %s of %s:"
                 % (len(evs), util.fmt_hz(tol), util.fmt_hz(freq))]
        for ev in evs[:20]:
            lines.append("- %s %s [%s] %s"
                         % (util.fmt_stamp(ev["t_start"]), ev["desk"],
                            ev["grade"], ev["content"] or ""))
        return "\n".join(lines)
    if q.lower().startswith("band "):
        frag = q[5:].lower()
        for lo, hi, name in BANDS:
            if frag in name.lower():
                evs = index.events_in_band(lo, hi, limit=50,
                                           newest=True)
                return ("%d events in the %s band (%s to %s). Latest: "
                        "%s" % (len(evs), name, util.fmt_hz(lo),
                                util.fmt_hz(hi),
                                evs[0]["content"] if evs else "none"))
        return "No band matches %r." % frag
    if q.lower() in SPECS:
        desk = q.lower()
        total = index.count_events(desk=desk)
        evs = index.events(desk=desk, limit=10, newest=True)
        return ("Service %s (tier %d): %d events in the Index. Latest "
                "activity: %s"
                % (desk, SPECS[desk][0], total,
                   util.fmt_stamp(evs[0]["t_start"]) if evs else "none"))
    matches = [d for d in index.all_dossiers()
               if q.lower() in d["key"].lower()]
    if matches:
        lines = ["Dossiers matching %r:" % q]
        for d in matches[:20]:
            lines.append("- %s (%s), %d events, last heard %s"
                         % (d["key"], d["kind"], d["events_count"],
                            util.fmt_stamp(d["last_seen"])))
        return "\n".join(lines)
    return ("Nothing in the Index answers to %r. Try an emitter key, a "
            "frequency, 'band <name>', a desk name, or 'changes'." % q)


# ----------------------------------------------------------------- status

def resolve_current_watch(home, allow_complete=False):
    """The watch the pointer means, validated against the disk.

    The current_watch pointer is a claim, not a fact: the folder it
    names may be gone, or the watch may have completed without the
    pointer being cleared. Trust the pointer only when its folder
    exists and (unless allow_complete) its state is not complete;
    otherwise resolve to the newest watch folder whose state is not
    complete. Returns a watch id or None.
    """
    watches_dir = os.path.join(home, "watches")
    pointer = util.read_json(os.path.join(home, "current_watch"),
                             default=None)

    def state_of(watch_id):
        path = os.path.join(watches_dir, watch_id, "state.json")
        if not os.path.isdir(os.path.join(watches_dir, watch_id)):
            return None
        return util.read_json(path, default={}) or {}

    if pointer:
        state = state_of(pointer)
        if state is not None and (allow_complete
                                  or not state.get("complete")):
            return pointer
    try:
        folders = sorted(os.listdir(watches_dir), reverse=True)
    except OSError:
        return None
    for folder in folders:
        state = state_of(folder)
        if state is not None and (allow_complete
                                  or not state.get("complete")):
            return folder
    return None


def _watch_state(home, watch_id):
    """A watch folder's state dict, or None when there is no folder
    or no readable state file in it."""
    if not watch_id:
        return None
    folder = os.path.join(home, "watches", watch_id)
    if not os.path.isdir(folder):
        return None
    return util.read_json(os.path.join(folder, "state.json"),
                          default=None)


def watch_liveness(home):
    """(live_id, claimed_id) for the home.

    live_id is a watch whose officer process is alive - its state
    file is incomplete and the pid it recorded still exists. The
    pointer's watch is checked first, then every watch folder.
    claimed_id is the incomplete watch the pointer names, whether
    or not anything is alive behind it. A state file with no pid,
    or a pid that is gone, is not a running watch: a folder alone
    never makes a watch live.
    """
    pointer = util.read_json(os.path.join(home, "current_watch"),
                             default=None)

    def is_live(watch_id):
        state = _watch_state(home, watch_id)
        return bool(state) and not state.get("complete") \
            and util.pid_alive(state.get("pid"))

    claimed = None
    if pointer:
        state = _watch_state(home, pointer)
        if state is not None and not state.get("complete"):
            claimed = pointer
            if is_live(pointer):
                return pointer, claimed
    try:
        folders = sorted(os.listdir(os.path.join(home, "watches")),
                         reverse=True)
    except OSError:
        folders = []
    for folder in folders:
        if is_live(folder):
            return folder, claimed
    return None, claimed


def status_text(home):
    from .index import Index
    pointer = util.read_json(os.path.join(home, "current_watch"),
                             default=None)
    live_id, claimed_id = watch_liveness(home)
    if not live_id and claimed_id:
        return ("Watch %s is not running: its officer process is "
                "gone and the watch stands interrupted. Its record "
                "covers the elapsed portion; it can be resumed with "
                "watch --resume %s." % (claimed_id, claimed_id))
    watch_id = live_id or resolve_current_watch(home)
    if watch_id and _watch_state(home, watch_id) is None:
        # A watch folder with no state.json is never reported as
        # running: no officer has ever reported for it.
        return ("Watch %s is not running: it has no state file; "
                "no officer is reporting for it." % watch_id)
    if not watch_id:
        # No running watch. If the pointer names a completed watch,
        # say which one finished; a stale pointer to a vanished
        # folder is reported as no watch, never as a phantom id.
        # The standing pool is reported either way: it is a
        # property of the home, not of a run.
        pool_line = _pool_line(home)
        retro_line = _retro_line(home)
        extra = "".join("\n" + line for line in
                        (pool_line, retro_line) if line)
        if pointer:
            state = _watch_state(home, pointer)
            if state and state.get("complete"):
                text = ("No watch is running. Last watch %s "
                        "completed." % pointer)
                return text + extra
        return "No watch is running." + extra
    index = Index(home)
    try:
        state = util.read_json(os.path.join(
            index.watch_dir(watch_id), "state.json"), default={}) or {}
        meta = _meta(index, watch_id)
    finally:
        index.close()
    elapsed = state.get("elapsed_s", 0)
    total = (meta.get("planned_end", 0) - meta.get("start", 0)) or 1
    health = state.get("benched") or []
    lines = [
        "Watch %s: %s elapsed, %s remaining"
        % (watch_id, util.fmt_duration(elapsed),
           util.fmt_duration(max(0, total - elapsed))),
        "Now: %s" % state.get("current", "between dwells"),
        "Events %d, dossiers %d, FLASH %d"
        % (state.get("events", 0), state.get("dossiers", 0),
           state.get("flash", 0)),
        "Desks: %s" % (("benched: " + ", ".join(sorted(health)))
                       if health else "all standing desks healthy"),
        "Source: %s" % meta.get("source", "unknown"),
    ]
    # Stood-down desks are a separate account from benched desks
    # and are stated separately, with their reasons: benched
    # cannot run on this host, stood down cannot run at this home.
    stood = state.get("stood_down") or {}
    for name in sorted(stood):
        lines.append("Stood down: %s - %s" % (name, stood[name]))
    if meta.get("source_note"):
        lines.append("Source note: %s" % meta["source_note"])
    bstate = state.get("budget")
    if bstate and bstate.get("cap_bytes") is not None:
        lines.append("Reception pool: standing cap %d bytes; "
                     "%d consumed, %d reserved in open grants, "
                     "%d free. This watch holds %d granted bytes "
                     "(%d consumed, %d shed)."
                     % (bstate["cap_bytes"],
                        bstate["consumed_bytes"],
                        bstate["reserved_bytes"],
                        bstate["free_bytes"] or 0,
                        bstate["watch_granted_bytes"],
                        bstate["watch_consumed_bytes"],
                        bstate["shed_bytes"]))
    elif bstate:
        lines.append("Reception budget: %d of %d allocated bytes "
                     "consumed, %d shed; satellite reserve %d "
                     "bytes (%d consumed)"
                     % (bstate["consumed_bytes"],
                        bstate["allocated_bytes"],
                        bstate["shed_bytes"],
                        bstate["satellite_reserve_bytes"],
                        bstate["satellite_consumed_bytes"]))
    if bstate and bstate.get("by_tier"):
        bt = bstate["by_tier"]
        lines.append("Retention by tier: " + "; ".join(
            "tier %s held %d bytes, shed %d bytes"
            % (t, bt[t]["held"], bt[t]["shed"])
            for t in sorted(bt, key=int)) + ".")
        if bstate.get("sealed_bytes"):
            lines.append("Sealed evidence stands apart from the "
                         "tiers: %d bytes held, never shed."
                         % bstate["sealed_bytes"])
    if state.get("retro_owed"):
        lines.append("Retroactive exploitation: %d retained "
                     "captures stand owed to a decoder whose "
                     "current version has not examined them; "
                     "the watch's passes work the debt down "
                     "inside its per-watch budget, and what "
                     "they do not reach carries to the next "
                     "watch." % state["retro_owed"])
    return "\n".join(lines)


def _retro_line(home):
    """The retroactive debt as one status line, computed from
    the home's store and processed ledger as they stand: the
    debt is a property of the archive, not of a run. None
    when the home has no Index yet, or nothing is owed."""
    try:
        import os as _os
        if not _os.path.exists(_os.path.join(home, "index.sqlite")):
            return None
        from . import retro as _retro_mod
        from .index import Index as _Index
        index = _Index(home)
        try:
            owed = _retro_mod.owed_counts(index)
        finally:
            index.close()
    except Exception:
        return None
    if not owed:
        return None
    return ("Retroactive exploitation: %d retained captures "
            "stand owed to a decoder whose current version has "
            "not examined them (%s); the next watch's passes "
            "work the debt down, and the reprocess verb runs "
            "it off now."
            % (sum(owed.values()),
               ", ".join("%s %d" % (n, owed[n])
                         for n in sorted(owed))))


def _pool_line(home):
    """The standing allocation pool as one status line, read
    from the home's ledger: the pool stands whether or not a
    watch does. None when the home has no ledger yet, or the
    ledger has never been told the standing cap."""
    try:
        from . import budget as _budget_mod
        pool = _budget_mod.AllocationLedger.load(home).pool()
    except Exception:
        return None
    if pool["cap_bytes"] is None:
        return None
    return ("Reception pool: standing cap %d bytes; %d consumed, "
            "%d reserved in %d open grants, %d free."
            % (pool["cap_bytes"], pool["consumed_bytes"],
               pool["reserved_bytes"], pool["open_grants"],
               pool["free_bytes"] or 0))
