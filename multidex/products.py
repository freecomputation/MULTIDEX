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

from . import util
from .bands import BANDS, band_name
from .desks import SPECS, TRANSCRIPTION_REASON
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
            and ev["grade"] == "CONFIRMED":
        reason = "satellite pass captured"
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
    L.append("Antenna for this watch: %s." % meta.get("antenna",
                                                      "not recorded"))
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
    L.append("## 5. Anomaly chapter")
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
    L.append("## 6. Satellite and sonde chapter")
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
    L.append("## 7. Transcript highlights")
    voice = index.count_events(watch_id=watch_id, kind="voice")
    L.append("Voice transmissions heard: %d. Transcription state: %s. "
             "No transcript is quoted in this report, because none was "
             "produced; voice evidence is filed as audio snippets "
             "under the snippet accounting in the appendix."
             % (voice, TRANSCRIPTION_REASON))
    L.append("")
    L.append("## 8. Desk health and failure account")
    if health:
        for ev in health:
            L.append("- %s %s" % (util.fmt_stamp(ev["t_start"]),
                                  ev["content"]))
    else:
        L.append("No desk failed, crashed, or was benched this watch.")
    L.append("")
    L.append("## 9. Statistics appendix")
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
        for ev in index.events(emitter=q, limit=10):
            lines.append("- %s %s %s [%s] %s"
                         % (util.fmt_stamp(ev["t_start"]), ev["desk"],
                            ev["kind"], ev["grade"],
                            ev["content"] or ""))
        return "\n".join(lines)
    freq = _parse_freq(q)
    if freq is not None:
        tol = max(12500.0, freq * 0.005)
        evs = index.events_near_freq(freq, tol, limit=50)
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
                evs = index.events_in_band(lo, hi, limit=50)
                return ("%d events in the %s band (%s to %s). Latest: "
                        "%s" % (len(evs), name, util.fmt_hz(lo),
                                util.fmt_hz(hi),
                                evs[-1]["content"] if evs else "none"))
        return "No band matches %r." % frag
    if q.lower() in SPECS:
        desk = q.lower()
        total = index.count_events(desk=desk)
        evs = index.events(desk=desk, limit=10)
        return ("Service %s (tier %d): %d events in the Index. Latest "
                "activity: %s"
                % (desk, SPECS[desk][0], total,
                   util.fmt_stamp(evs[-1]["t_start"]) if evs else "none"))
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
            "frequency, 'band <name>', or a desk name." % q)


# ----------------------------------------------------------------- status

def status_text(home):
    pointer = os.path.join(home, "current_watch")
    watch_id = util.read_json(pointer, default=None)
    if not watch_id:
        return "No watch is running."
    from .index import Index
    index = Index(home)
    try:
        state = util.read_json(os.path.join(
            index.watch_dir(watch_id), "state.json"), default={}) or {}
        meta = _meta(index, watch_id)
    finally:
        index.close()
    if state.get("complete"):
        return "No watch is running. Last watch %s completed." % watch_id
    elapsed = state.get("elapsed_s", 0)
    total = (meta.get("planned_end", 0) - meta.get("start", 0)) or 1
    health = state.get("benched") or []
    return "\n".join([
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
    ])
