"""Retroactive exploitation: every decoder improvement is owed
to the archive (0.12.0).

The snippet store is permanent and the decoders keep improving,
so an improvement is owed to the past as well as the future.
When a decoder's capability changes - a new desk, a new
protocol, an own implementation retiring a bound tool - the
system re-runs that decoder over every retained capture that
could hold its targets and files what it finds in the old
material, linked to the original capture and marked as found
late. A signal recorded before its decoder existed is not
lost; it is waiting.

Three pieces make that a mechanism rather than a sentiment.

The capability manifest: every decoder the system owns
registers itself here, in code, with a stable name, the desk
that owns it, a version string the desk bumps when its
capability changes, the snippet kinds it consumes, and the
frequency range or channels it targets. Desks that decode
nothing register nothing. Doctor states the manifest from
this table, so the account cannot drift from the code.

The processed ledger: a JSON file in the home beside the
Index (retro-ledger.json), holding, for each decoder version,
the set of capture identities it has already examined and
what the examination produced. Capture identity is the
snippet row's own id in the store, never a filename guess.
The ledger survives watches and updates exactly as the
allocation ledger does; no version landing resets it, and a
decoder version's examined set is the whole memory of what
that version has seen.

The pass: at muster the officer diffs the manifest against
the ledger. A decoder that is new, or whose version is new,
owes a pass over every retained capture matching its
registration that its current version has not examined. The
pass is bounded by a per-watch budget of captures and of
officer seconds, spent at muster and at dwell boundaries, so
it never delays a billed dwell; what the budget does not
reach stays owed, is stated as owed in status and in the
Watch Report, and carries to the next watch. The ledger is
written as captures complete, so an interrupted pass loses
nothing already done. The reprocess verb runs the same pass
unbounded at the operator's direct ask, while no watch
stands.

Filing rules. What a decoder finds files as a new event
under the watch that filed the capture: the decode itself,
graded by the decoder's normal rules, linked to the original
event, and stamped in its data with the retroactive mark,
the decoder's name and version, the source snippet's
identity, and the capture's original time beside the time it
was found. The original event is never modified. A capture a
decoder examines and finds nothing in files no per-capture
event; the pass's own summary carries the counts. The one
exception is the probe desk's stricter rule, kept: a capture
filed by a probe stare is a probe target, and a retroactive
attempt on it that finds nothing files the negative result
in the probe's own shape, linked to the stare. Captures a
decoder's product already covers - a pass whose imagery the
desk already decoded, an excerpt the probe already
classified - are not owed; the pass seeds them into the
ledger as already exploited, so the debt is only ever real
work. Captures the retention tiers have shed are absent from
the store and are simply not owed: the owed computation
reads the store as it stands.

Products a decoder files from old material (a decoded image)
go through the snippet store under the standing ceiling,
home-wide, exactly as live products do, and sheds they cause
are filed as shed events in the officer's shape and posted
against the allocation ledger when one is at hand. The pass
draws no reception grant: grants are taken at muster for
planned receptions, and a retroactive product is not a
reception. Run functions name their products with the
retro- prefix, so a retroactive product can never collide
with the live product it stands beside.

Retroactive events are filed through the Index, not through
the officer's FLASH path: a find in old material is reported
in the retroactive account and the Watch Report, not chimed
as breaking news over a capture days old.
"""

import base64
import json
import os
import time

from . import util
from .events import make_event

LEDGER_FILENAME = "retro-ledger.json"
LEDGER_FORMAT = 1

# The outcomes a ledger entry records.
FOUND = "found"            # the examination filed events
NOTHING = "nothing"        # examined; nothing decodable in it
ALREADY = "already"        # the decoder's product already stood
ERROR = "error"            # the decoder raised on this capture
UNREADABLE = "unreadable"  # the capture's bytes could not be read


# ------------------------------------------------------------ manifest

DECODERS = []


def register(name, desk, version, kinds, run, freq_range=None,
             suffixes=None, already=None, skip=None, blurb=""):
    """Register one decoder in the capability manifest.

    name is the decoder's stable identity across versions;
    desk is the owning desk's name; version is the string the
    desk bumps when its capability changes. kinds names the
    snippet kinds the decoder consumes; suffixes, when given,
    narrows those kinds to filenames ending so (a SigMF data
    file, a WAV recording). freq_range, when given, is a
    (low, high) pair in Hz the filing event's frequency must
    fall inside. already(filing_event) marks a capture the
    decoder's product already covers; skip(filing_event)
    marks one outside the decoder's scope for a stated
    reason. run(ctx) does the decode and returns draft events
    (see RetroPass for the ctx contract)."""
    entry = {"name": name, "desk": desk, "version": str(version),
             "kinds": tuple(kinds), "run": run,
             "freq_range": freq_range,
             "suffixes": tuple(suffixes) if suffixes else None,
             "already": already, "skip": skip, "blurb": blurb}
    DECODERS.append(entry)
    return entry


def manifest():
    """The registered decoders, in registration order."""
    return list(DECODERS)


def decoder_named(name):
    for entry in DECODERS:
        if entry["name"] == name:
            return entry
    return None


def manifest_lines():
    """The manifest as doctor states it: one line per decoder,
    read from the table itself."""
    lines = []
    for e in DECODERS:
        kinds = ", ".join(e["kinds"])
        scope = ""
        if e["freq_range"]:
            scope = " between %s and %s" % (
                util.fmt_hz(e["freq_range"][0]),
                util.fmt_hz(e["freq_range"][1]))
        if e["suffixes"]:
            scope += " (files ending %s)" % ", ".join(e["suffixes"])
        lines.append("- %s version %s (%s desk): consumes %s "
                     "snippets%s. %s"
                     % (e["name"], e["version"], e["desk"], kinds,
                        scope, e["blurb"]))
    return lines


# ------------------------------------------------------ processed ledger

class ProcessedLedger:
    """What each decoder version has already examined, held in
    the home beside the Index. Written as captures complete,
    so an interrupted pass loses nothing already done."""

    def __init__(self, home, data=None):
        self.home = home
        self.path = os.path.join(home, LEDGER_FILENAME)
        self.data = data or {"format": LEDGER_FORMAT, "decoders": {}}

    @classmethod
    def load(cls, home):
        raw = util.read_json(os.path.join(home, LEDGER_FILENAME),
                             default=None)
        if not isinstance(raw, dict) \
                or raw.get("format") != LEDGER_FORMAT \
                or not isinstance(raw.get("decoders"), dict):
            raw = {"format": LEDGER_FORMAT, "decoders": {}}
        return cls(home, raw)

    def save(self):
        util.write_json(self.path, self.data)

    def _cell(self, name, version):
        return self.data["decoders"].setdefault(name, {}) \
            .setdefault(str(version), {})

    def examined_ids(self, name, version):
        cell = self.data["decoders"].get(name, {}).get(str(version),
                                                       {})
        return set(cell)

    def record(self, name, version, snippet_id, outcome, t,
               events=(), detail=None):
        entry = {"outcome": outcome, "t": float(t),
                 "events": list(events)}
        if detail:
            entry["detail"] = detail
        self._cell(name, version)[snippet_id] = entry
        self.save()

    def counts(self, name, version):
        cell = self.data["decoders"].get(name, {}).get(str(version),
                                                       {})
        out = {}
        for entry in cell.values():
            out[entry["outcome"]] = out.get(entry["outcome"], 0) + 1
        return out

    def found_event_ids(self, name, version):
        cell = self.data["decoders"].get(name, {}).get(str(version),
                                                       {})
        out = []
        for entry in cell.values():
            out.extend(entry.get("events") or [])
        return out


# ------------------------------------------------------------ owed work

def _matches(entry, row, event):
    """Whether a retained snippet row is inside a decoder's
    registration: its kind, its filename's suffix, and - when
    the registration carries a frequency range - the filing
    event's frequency inside it. A capture with no filing
    event cannot prove it is in range, so a ranged decoder
    does not claim it."""
    if row["kind"] not in entry["kinds"]:
        return False
    if entry["suffixes"]:
        name = os.path.basename(row["path"] or "")
        if not any(name.endswith(s) for s in entry["suffixes"]):
            return False
    if entry["freq_range"]:
        if event is None or event.get("freq_hz") is None:
            return False
        lo, hi = entry["freq_range"]
        if not (lo <= event["freq_hz"] <= hi):
            return False
    return True


def _resolve_event(watch_events, row):
    """The event a snippet row was filed under, from its
    watch's events. The headline case is exact: the event's
    snippet names the row's path. An excerpt pair filed
    beside a voice snippet, and pass audio filed beside its
    decoded image, are instead named in the filing event's
    data (iq_excerpt, audio), as the desk-local filename the
    store path ends with; among those, the event whose time
    stands nearest the row's filing time is the filer."""
    for ev in watch_events:
        if ev.get("snippet") == row["path"]:
            return ev
    base = os.path.basename(row["path"] or "")
    named = []
    for ev in watch_events:
        data = ev.get("data") or {}
        for key in ("iq_excerpt", "audio"):
            name = data.get(key)
            if name and base.endswith(name):
                named.append(ev)
                break
    if not named:
        return None
    named.sort(key=lambda ev: abs(ev["t_start"] - row["t"]))
    return named[0]


def owed_captures(index, ledger, decoders=None):
    """The debt, computed from the store as it stands.

    Returns (work, seed): work maps a decoder name to the
    captures its current version owes an examination; seed
    maps a decoder name to captures its product already
    covers, which the pass records as already exploited
    rather than examining. A capture is {row, event, meta_row}:
    the snippet row, the event that filed it (or None), and,
    for a SigMF pair, the metadata mate's row (or None). Shed
    rows are absent from the computation entirely: shed
    evidence is gone and is not owed."""
    decoders = decoders if decoders is not None else manifest()
    rows = [r for r in index.snippet_rows_all() if not r["shed"]]
    by_path = {(r["watch_id"], r["path"]): r for r in rows}
    by_watch = {}
    resolved = {}
    work, seed = {}, {}

    def filing_event(row):
        key = (row["watch_id"], row["path"])
        if key not in resolved:
            if row["watch_id"] not in by_watch:
                by_watch[row["watch_id"]] = index.events(
                    watch_id=row["watch_id"], limit=100000)
            resolved[key] = _resolve_event(by_watch[row["watch_id"]],
                                           row)
        return resolved[key]

    for entry in decoders:
        examined = ledger.examined_ids(entry["name"],
                                       entry["version"])
        for row in rows:
            if row["id"] in examined:
                continue
            event = filing_event(row)
            if not _matches(entry, row, event):
                continue
            if entry["skip"] is not None and event is not None \
                    and entry["skip"](event):
                continue
            capture = {"row": row, "event": event,
                       "meta_row": None}
            stem = None
            if row["path"] and row["path"].endswith(".sigmf-data"):
                stem = row["path"][:-len(".sigmf-data")]
            if stem is not None:
                capture["meta_row"] = by_path.get(
                    (row["watch_id"], stem + ".sigmf-meta"))
            if entry["already"] is not None and event is not None \
                    and entry["already"](event):
                seed.setdefault(entry["name"], []).append(capture)
            else:
                work.setdefault(entry["name"], []).append(capture)
    for caps in list(work.values()) + list(seed.values()):
        caps.sort(key=lambda c: c["row"]["t"])
    return work, seed


def owed_counts(index, decoders=None):
    """{decoder name: captures owed} for the home's Index as it
    stands, the figure status and the Watch Report state."""
    ledger = ProcessedLedger.load(index.home)
    work, _seed = owed_captures(index, ledger, decoders)
    return {name: len(caps) for name, caps in work.items()
            if caps}


# ------------------------------------------------------------------ pass

class RetroPass:
    """One home's retroactive exploitation state: the plan
    computed from the store and the ledger, and the slices
    that work it down inside a budget.

    The plan is computed once, at muster or at the verb's
    start; captures filed after the plan are next watch's
    debt, never this plan's moving target. A slice runs
    decoders in manifest order and captures oldest first, and
    returns its account. The run context a decoder receives:

    {index, home, row, event, data, meta, watch_id, now} -
    the snippet row, its filing event (or None), the capture's
    bytes (for a SigMF pair, the data file's bytes), the
    pair's parsed metadata (or None), the source watch, and
    the pass time. Draft events come back as make_event dicts
    under the source watch; a draft may carry snippet payloads
    in data["_snippet"] in the officer's shape, which the pass
    files through the store and names on the event.
    """

    def __init__(self, index, home, profile=None, decoders=None,
                 allocation_ledger=None):
        self.index = index
        self.home = home
        self.ceiling = int(profile["watch"]["disk_ceiling_gb"]
                           * 1e9) if profile else int(20e9)
        self.decoders = decoders if decoders is not None \
            else manifest()
        self.ledger = ProcessedLedger.load(home)
        self.alloc = allocation_ledger
        self._queue = None
        self._pos = 0
        self.seeded = 0

    def plan(self, now):
        """Compute the debt and seed the already-exploited
        captures into the ledger. Returns {owed, seeded}."""
        work, seed = owed_captures(self.index, self.ledger,
                                   self.decoders)
        self.seeded = 0
        for entry in self.decoders:
            for capture in seed.get(entry["name"], []):
                self.ledger.record(entry["name"], entry["version"],
                                   capture["row"]["id"], ALREADY,
                                   now)
                self.seeded += 1
        queue = []
        for entry in self.decoders:
            for capture in work.get(entry["name"], []):
                queue.append((entry, capture))
        self._queue = queue
        self._pos = 0
        return {"owed": len(queue), "seeded": self.seeded}

    def owed_remaining(self):
        if self._queue is None:
            return 0
        return len(self._queue) - self._pos

    def done(self):
        return self._queue is not None and self.owed_remaining() == 0

    def _read(self, row):
        path = os.path.join(self.home, "watches", row["watch_id"],
                            row["path"])
        with open(path, "rb") as fh:
            return fh.read()

    def _live(self, row):
        """The row as the store now stands: a capture shed
        since the plan is gone, and the pass does not pretend
        to examine it."""
        current = self.index.snippet_row(row["id"])
        if current is None or current["shed"]:
            return None
        return current

    def _file_products(self, ev, filer, now):
        """File a draft event's snippet payloads through the
        store, in the officer's accounting shape. Returns the
        kept product paths; shed events are filed through the
        same filer, and sheds post against the allocation
        ledger when one is at hand."""
        payload = ev["data"].pop("_snippet", None)
        if payload is None:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        kept = []
        for one in payload:
            src = one.get("file")
            if src is not None:
                data = None
            else:
                data = base64.b64decode(one["b64"])
            rel, shed = self.index.keep_snippet(
                ev["watch_id"], self.index.watch_dir(ev["watch_id"]),
                ev["desk"], one["kind"], one["filename"], data,
                self.ceiling, t=now, source_path=src, home_wide=True)
            for victim in shed:
                vwatch = victim.get("watch_id") or ev["watch_id"]
                from . import budget as _budget
                vbid = _budget.bill_id_of_snippet_path(victim["path"])
                vgrant = None
                if self.alloc is not None:
                    vgrant = self.alloc.post_shed(
                        vwatch, vbid, victim["bytes"])
                filer(make_event(
                    ev["watch_id"], "officer", "system", now, now,
                    grade="CONFIRMED",
                    content="snippet shed under the disk ceiling: %s"
                            % victim["path"],
                    data={"shed": victim["path"],
                          "desk": victim["desk"],
                          "bill_id": vbid,
                          "watch_id": vwatch,
                          "grant_id": vgrant["id"] if vgrant
                          else None,
                          "bytes": victim["bytes"]}))
            if rel:
                kept.append(rel)
        return kept

    def _probe_negative(self, entry, capture, filer):
        """The probe desk's stricter rule, kept for retroactive
        attempts: a capture a probe stare filed is a probe
        target, and an attempt on it that found nothing files
        the negative result in the probe's own shape."""
        ev0 = capture["event"]
        row = capture["row"]
        label = util.fmt_hz(ev0.get("freq_hz"))
        filer(make_event(
            row["watch_id"], "probe", "assessment",
            ev0["t_start"], ev0["t_end"], freq_hz=ev0.get("freq_hz"),
            emitter=ev0.get("emitter"), grade="CONFIRMED",
            content="negative result: %s attempted a retroactive "
                    "decode of the probe stare at %s and filed no "
                    "decode: the %s decoder (version %s) found "
                    "nothing it could decode in the retained "
                    "excerpt"
                    % (entry["desk"], label, entry["name"],
                       entry["version"]),
            data={"rule": "probe_negative_result",
                  "attempted_desk": entry["desk"],
                  "outcome": "no decode",
                  "detail": "retroactive attempt by %s version %s "
                            "over snippet %s"
                            % (entry["name"], entry["version"],
                               row["id"]),
                  "retroactive": True,
                  "decoder": entry["name"],
                  "decoder_version": entry["version"],
                  "source_snippet": row["id"],
                  "source_event": ev0["id"]},
            links=[ev0["id"]]))

    def step(self, now, max_captures=None, max_seconds=None,
             filer=None):
        """Run one slice of the plan inside the given budget.
        Returns the slice's account."""
        if self._queue is None:
            self.plan(now)
        filer = filer or self.index.add_event
        account = {"at": float(now), "examined": 0, "found": 0,
                   "errors": 0, "gone": 0, "per_decoder": {},
                   "finds": [], "used_captures": 0,
                   "used_seconds": 0.0}
        started = time.monotonic()
        while self._pos < len(self._queue):
            if max_captures is not None \
                    and account["used_captures"] >= max_captures:
                break
            if max_seconds is not None \
                    and time.monotonic() - started >= max_seconds:
                break
            entry, capture = self._queue[self._pos]
            self._pos += 1
            account["used_captures"] += 1
            row = capture["row"]
            cell = account["per_decoder"].setdefault(
                entry["name"], {"examined": 0, "found": 0,
                                "errors": 0})
            live = self._live(row)
            if live is None:
                account["gone"] += 1
                continue
            try:
                data = self._read(live)
            except OSError:
                self.ledger.record(entry["name"], entry["version"],
                                   row["id"], UNREADABLE, now)
                account["examined"] += 1
                cell["examined"] += 1
                continue
            meta = None
            if capture.get("meta_row") is not None:
                try:
                    meta = json.loads(self._read(
                        capture["meta_row"]).decode("utf-8"))
                except (OSError, ValueError):
                    meta = None
            ctx = {"index": self.index, "home": self.home,
                   "row": live, "event": capture["event"],
                   "data": data, "meta": meta,
                   "watch_id": row["watch_id"], "now": float(now)}
            try:
                drafts = entry["run"](ctx) or []
            except Exception as exc:
                self.ledger.record(
                    entry["name"], entry["version"], row["id"],
                    ERROR, now,
                    detail="%s: %s" % (type(exc).__name__, exc))
                account["examined"] += 1
                cell["examined"] += 1
                cell["errors"] += 1
                account["errors"] += 1
                continue
            filed_ids = []
            for ev in drafts:
                ev["data"]["retroactive"] = True
                ev["data"]["decoder"] = entry["name"]
                ev["data"]["decoder_version"] = entry["version"]
                ev["data"]["source_snippet"] = row["id"]
                ev["data"]["source_event"] = capture["event"]["id"] \
                    if capture["event"] else None
                ev["data"]["capture_t"] = capture["event"]["t_start"] \
                    if capture["event"] else live["t"]
                ev["data"]["found_at"] = float(now)
                if capture["event"] \
                        and capture["event"]["id"] not in ev["links"]:
                    ev["links"].append(capture["event"]["id"])
                kept = self._file_products(ev, filer, now)
                ev["snippet"] = kept[-1] if kept else row["path"]
                filer(ev)
                filed_ids.append(ev["id"])
                if len(account["finds"]) < 10:
                    account["finds"].append({
                        "decoder": entry["name"],
                        "event_id": ev["id"],
                        "content": ev.get("content")})
            self.ledger.record(entry["name"], entry["version"],
                               row["id"],
                               FOUND if filed_ids else NOTHING,
                               now, events=filed_ids)
            account["examined"] += 1
            cell["examined"] += 1
            if filed_ids:
                account["found"] += len(filed_ids)
                cell["found"] += len(filed_ids)
            elif capture["event"] is not None \
                    and capture["event"].get("desk") == "probe":
                self._probe_negative(entry, capture, filer)
        account["used_seconds"] = round(time.monotonic() - started,
                                        3)
        account["owed_after"] = self.owed_remaining()
        return account


def account_sentence(account, seeded=0):
    """One plain sentence stating a pass slice's account, the
    same words in the officer's summary event, the verb's
    output and the Watch Report's aggregation."""
    parts = []
    if seeded:
        parts.append("%d retained captures were already covered "
                     "by their decoder's product and were seeded "
                     "as already exploited" % seeded)
    if account["examined"]:
        parts.append("%d retained captures were re-examined and "
                     "%d retroactive events were filed from them"
                     % (account["examined"], account["found"]))
        if account["errors"]:
            parts.append("%d examinations raised errors and are "
                         "recorded as such in the processed ledger"
                         % account["errors"])
    if account.get("owed_after"):
        parts.append("%d captures remain owed and carry to the "
                     "next watch" % account["owed_after"])
    if not parts:
        return ("the archive owed nothing: every retained "
                "capture has been examined by the current "
                "version of every decoder that consumes its kind")
    return "; ".join(parts)


# ------------------------------------------------- production decoders

def _run_apt(ctx):
    """The APT imagery decoder over a filed pass recording:
    the satellite desk's own decoder (multidex.apt), run over
    the pass audio the desk filed whole at tier 3. A recording
    with no decodable APT in it is an examination that finds
    nothing, exactly as the desk states it live."""
    from . import apt as apt_mod
    ev0 = ctx["event"]
    try:
        png, info = apt_mod.decode_wav(ctx["data"])
    except apt_mod.AptError:
        return []
    sat = (ev0.get("data") or {}).get("sat") or ev0.get("emitter")
    norad = (ev0.get("data") or {}).get("norad")
    filename = "retro-pass-%s-%d.png" % (norad or "apt",
                                         int(ev0["t_start"]))
    ev = make_event(
        ctx["watch_id"], "satellite", "imagery", ev0["t_start"],
        ev0["t_end"], freq_hz=ev0.get("freq_hz"),
        emitter=ev0.get("emitter"), grade="CONFIRMED",
        content="APT imagery decoded from the filed pass audio "
                "after the fact: %d lines, sync correlation %.2f; "
                "the image is filed as %s and the pass it belongs "
                "to is linked"
                % (info.get("lines", 0),
                   info.get("sync_correlation", 0.0), filename),
        data={"emitter_kind": "satellite", "sat": sat,
              "norad": norad, "imagery": filename,
              "imagery_decode": info,
              "pass_event": ev0["id"]})
    ev["data"]["_snippet"] = {
        "filename": filename, "kind": "satellite",
        "b64": base64.b64encode(png).decode("ascii")}
    return [ev]


def _apt_already(ev):
    """A pass whose imagery already stands was exploited by
    this decoder when it was live."""
    return bool((ev.get("data") or {}).get("imagery"))


def _apt_skip(ev):
    """Imagery is classified for the NOAA satellites only, as
    the desk itself states on every other pass."""
    sat = (ev.get("data") or {}).get("sat") or ev.get("emitter") \
        or ""
    return bool(sat) and "NOAA" not in sat.upper()


def _excerpt_dict(ctx):
    """A stored SigMF pair back into the excerpt dict the
    in-process decoders consume, from the pair's own metadata:
    rate, scale peak and sample count are the metadata's
    facts, not guesses."""
    meta = ctx["meta"] or {}
    glob = meta.get("global") or {}
    rate = float(glob.get("core:sample_rate") or 0.0)
    peak = float(glob.get("multidex:peak") or 1.0)
    return {"b64": base64.b64encode(ctx["data"]).decode("ascii"),
            "rate": rate, "peak": peak,
            "samples": len(ctx["data"]) // 4}


def _run_classifier(ctx):
    """The feature-tree classifier over a retained channel
    excerpt: the probe desk's instrument, run over the tier 2
    excerpts the census and the sweep filed unclassified. An
    excerpt the tree still cannot claim is an examination
    that finds nothing; a claimed family files as an
    assessment in the probe's classification shape."""
    from . import classifier as classifier_mod
    ev0 = ctx["event"]
    result = classifier_mod.classify_excerpt(_excerpt_dict(ctx))
    family = result["family"]
    if family == classifier_mod.UNKNOWN:
        return []
    reasons = result["reasons"]
    return [make_event(
        ctx["watch_id"], "probe", "assessment", ev0["t_start"],
        ev0["t_end"], freq_hz=ev0.get("freq_hz"),
        emitter=ev0.get("emitter"), grade="PROBABLE",
        content="retroactive classification: the retained "
                "excerpt filed with this hearing classifies as "
                "%s under the feature tree: %s"
                % (family, reasons[0] if reasons
                   else "no reason stated"),
        data={"rule": "retroactive_classification",
              "classification": {"family": family,
                                 "features": result["features"],
                                 "reasons": reasons}})]


def _classifier_already(ev):
    """An excerpt the probe stare itself filed already
    carries the classification the stare reached."""
    return bool((ev.get("data") or {}).get("classification"))


def _run_acars(ctx):
    """The ACARS physical layer over a retained channel
    excerpt near an ACARS channel: the ACARS module's
    conforming front end - AM demodulation against a local
    carrier baseline, then activity-edge burst slices -
    shared with the desk's live path and the probe desk's
    attempt since 0.12.1, and the frame
    layer, which reports only parity- and CRC-verified
    frames. A frame files as a decode event in the ACARS
    desk's own shape; the same frame heard twice in one
    excerpt files once."""
    from . import acars as acars_mod
    from . import classifier as classifier_mod
    from . import codecs
    from .desks import _ACARS_CHANNELS
    ev0 = ctx["event"]
    freq = ev0.get("freq_hz")
    if freq is None or not any(abs(freq - ch) <= 25000.0
                               for ch in _ACARS_CHANNELS):
        return []
    chan, rate = classifier_mod.decode_excerpt(_excerpt_dict(ctx))
    audio = acars_mod.am_demod(chan, rate)
    raws = []
    for raw in acars_mod.decode_sliced(audio, whole_fallback=True):
        if raw not in raws:
            raws.append(raw)
    out = []
    for raw in raws:
        msg = codecs.acars_parse(raw)
        if msg is None:
            continue
        out.append(make_event(
            ctx["watch_id"], "acars", "decode", ev0["t_start"],
            ev0["t_end"], freq_hz=freq, emitter=msg["tail"],
            grade="CONFIRMED", content=msg["text"],
            data={"emitter_kind": "aircraft", "tail": msg["tail"],
                  "flight": msg["flight"], "label": msg["label"],
                  "also": [msg["flight"]], "raw": raw}))
    return out


register(
    "apt-imagery", "satellite", "1", ("satellite",), _run_apt,
    freq_range=(136.5e6, 138.5e6), suffixes=(".wav",),
    already=_apt_already, skip=_apt_skip,
    blurb="Its product is a decoded APT image, filed as an "
          "imagery event beside the pass the audio belongs to.")

register(
    "excerpt-classifier", "probe", "1", ("iq",), _run_classifier,
    suffixes=(".sigmf-data",), already=_classifier_already,
    blurb="Its product is a classification assessment in the "
          "probe's shape, filed against the hearing the excerpt "
          "was retained with.")

register(
    "acars-excerpt", "acars", "1", ("iq",), _run_acars,
    freq_range=(131.4e6, 131.8e6), suffixes=(".sigmf-data",),
    blurb="Its product is a decode event for each verified "
          "ACARS frame the excerpt holds, in the ACARS desk's "
          "own shape.")
