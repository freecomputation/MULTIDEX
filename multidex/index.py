"""The Index: the system's memory (design section 8).

Native form is an append-only JSONL event log per watch; the SQLite
database is rebuilt from those logs and serves queries. Dossiers are
keyed by emitter identity, with an alias table so one aircraft heard
by ICAO address on one desk and by tail on another is one dossier.
Grades and kinds are re-validated at write time: nothing ungraded
enters the Index, ever.
"""

import json
import os
import sqlite3

from . import events as evmod

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, watch_id TEXT, desk TEXT, kind TEXT,
  t_start REAL, t_end REAL, freq_hz REAL, emitter TEXT, grade TEXT,
  content TEXT, snippet TEXT, data_json TEXT, links_json TEXT);
CREATE INDEX IF NOT EXISTS ix_events_watch ON events(watch_id);
CREATE INDEX IF NOT EXISTS ix_events_emitter ON events(emitter);
CREATE INDEX IF NOT EXISTS ix_events_desk ON events(desk);
CREATE TABLE IF NOT EXISTS dossiers(
  key TEXT PRIMARY KEY, kind TEXT, first_seen REAL, last_seen REAL,
  watches_json TEXT, events_count INTEGER);
CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, canonical TEXT);
CREATE TABLE IF NOT EXISTS baselines(
  scope TEXT, hour INTEGER, watch_count INTEGER, stats_json TEXT,
  PRIMARY KEY(scope, hour));
CREATE TABLE IF NOT EXISTS snippets(
  id TEXT PRIMARY KEY, watch_id TEXT, desk TEXT, kind TEXT, path TEXT,
  bytes INTEGER, shed INTEGER, t REAL);
CREATE TABLE IF NOT EXISTS watch_meta(watch_id TEXT PRIMARY KEY, data_json TEXT);
"""


class Index:
    def __init__(self, home):
        self.home = home
        self.watches_dir = os.path.join(home, "watches")
        os.makedirs(self.watches_dir, exist_ok=True)
        self.db_path = os.path.join(home, "index.sqlite")
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._log_handles = {}

    # ------------------------------------------------------------ paths
    def watch_dir(self, watch_id):
        path = os.path.join(self.watches_dir, watch_id)
        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "snippets"), exist_ok=True)
        return path

    def log_path(self, watch_id):
        return os.path.join(self.watch_dir(watch_id), "events.jsonl")

    # ------------------------------------------------------------ write
    def add_event(self, ev, commit=True):
        if ev.get("grade") not in evmod.GRADES:
            raise ValueError("event grade not a confidence grade: %r"
                             % (ev.get("grade"),))
        if ev.get("kind") not in evmod.KINDS:
            raise ValueError("event kind unknown: %r" % (ev.get("kind"),))
        handle = self._log_handles.get(ev["watch_id"])
        if handle is None:
            handle = open(self.log_path(ev["watch_id"]), "a")
            self._log_handles[ev["watch_id"]] = handle
        handle.write(json.dumps(ev, sort_keys=True) + "\n")
        handle.flush()
        self._insert(ev)
        if commit:
            self.db.commit()
        return ev

    def add_events(self, evs):
        for ev in evs:
            self.add_event(ev, commit=False)
        self.db.commit()

    def _insert(self, ev):
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev["id"], ev["watch_id"], ev["desk"], ev["kind"], ev["t_start"],
             ev["t_end"], ev["freq_hz"], ev["emitter"], ev["grade"],
             ev["content"], ev["snippet"], json.dumps(ev["data"]),
             json.dumps(ev["links"])))
        if cur.rowcount and ev["emitter"]:
            self._touch_dossier(ev)

    def _touch_dossier(self, ev):
        aliases = list(ev["data"].get("also", []))
        canonical = self.resolve(ev["emitter"])
        for alias in aliases:
            other = self.resolve(alias)
            if other != canonical:
                canonical = self._merge_dossiers(canonical, other)
            self.db.execute(
                "INSERT OR REPLACE INTO aliases VALUES (?,?)",
                (alias, canonical))
        self.db.execute(
            "INSERT OR REPLACE INTO aliases VALUES (?,?)",
            (ev["emitter"], canonical))
        kind = ev["data"].get("emitter_kind", "emitter")
        row = self.db.execute("SELECT * FROM dossiers WHERE key=?",
                              (canonical,)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO dossiers VALUES (?,?,?,?,?,?)",
                (canonical, kind, ev["t_start"], ev["t_end"],
                 json.dumps([ev["watch_id"]]), 1))
        else:
            watches = json.loads(row["watches_json"])
            if ev["watch_id"] not in watches:
                watches.append(ev["watch_id"])
            self.db.execute(
                "UPDATE dossiers SET first_seen=min(first_seen,?), "
                "last_seen=max(last_seen,?), watches_json=?, "
                "events_count=events_count+1 WHERE key=?",
                (ev["t_start"], ev["t_end"], json.dumps(watches), canonical))

    def _merge_dossiers(self, keep, drop):
        a = self.db.execute("SELECT * FROM dossiers WHERE key=?",
                            (keep,)).fetchone()
        b = self.db.execute("SELECT * FROM dossiers WHERE key=?",
                            (drop,)).fetchone()
        self.db.execute("UPDATE aliases SET canonical=? WHERE canonical=?",
                        (keep, drop))
        self.db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?)",
                        (drop, keep))
        if a is not None and b is not None:
            watches = sorted(set(json.loads(a["watches_json"]))
                             | set(json.loads(b["watches_json"])))
            self.db.execute(
                "UPDATE dossiers SET first_seen=?, last_seen=?, "
                "watches_json=?, events_count=? WHERE key=?",
                (min(a["first_seen"], b["first_seen"]),
                 max(a["last_seen"], b["last_seen"]), json.dumps(watches),
                 a["events_count"] + b["events_count"], keep))
            self.db.execute("DELETE FROM dossiers WHERE key=?", (drop,))
        elif a is None and b is not None:
            self.db.execute("UPDATE dossiers SET key=? WHERE key=?",
                            (keep, drop))
        return keep

    def resolve(self, key):
        row = self.db.execute("SELECT canonical FROM aliases WHERE alias=?",
                              (key,)).fetchone()
        return row["canonical"] if row else key

    def aliased_keys(self, canonical):
        rows = self.db.execute("SELECT alias FROM aliases WHERE canonical=?",
                               (canonical,)).fetchall()
        keys = [r["alias"] for r in rows]
        if canonical not in keys:
            keys.append(canonical)
        return keys

    def add_link(self, event_id, other_id):
        """Record a correlation link both ways (derived data: it lives
        in the database and is regenerated by re-running correlation;
        the JSONL log keeps the events themselves)."""
        for a, b in ((event_id, other_id), (other_id, event_id)):
            row = self.db.execute("SELECT links_json FROM events WHERE "
                                  "id=?", (a,)).fetchone()
            if row is None:
                continue
            links = json.loads(row["links_json"])
            if b not in links:
                links.append(b)
                self.db.execute("UPDATE events SET links_json=? WHERE "
                                "id=?", (json.dumps(links), a))
        self.db.commit()

    # ----------------------------------------------------------- rebuild
    def rebuild(self):
        """Regenerate the database from the JSONL logs. The log is truth.

        Two kinds of state have no record in the logs and are carried
        across the rebuild rather than lost: the snippet table (the
        snippet files stay on disk, so their accounting must too) and
        the event links and watch metadata, which are derived state
        held only in the database.
        """
        snippets = self._rows("SELECT * FROM snippets")
        links = {r["id"]: r["links_json"] for r in self._rows(
            "SELECT id, links_json FROM events")}
        metas = self._rows("SELECT * FROM watch_meta")
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        count = 0
        for name in sorted(os.listdir(self.watches_dir)):
            log = os.path.join(self.watches_dir, name, "events.jsonl")
            if not os.path.exists(log):
                continue
            with open(log) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._insert(json.loads(line))
                        count += 1
        for row in snippets:
            self.db.execute(
                "INSERT OR REPLACE INTO snippets VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], row["watch_id"], row["desk"], row["kind"],
                 row["path"], row["bytes"], row["shed"], row["t"]))
        for event_id, links_json in links.items():
            self.db.execute("UPDATE events SET links_json=? WHERE id=?",
                            (links_json, event_id))
        for row in metas:
            self.db.execute(
                "INSERT OR REPLACE INTO watch_meta VALUES (?,?)",
                (row["watch_id"], row["data_json"]))
        self.db.commit()
        return count

    # ------------------------------------------------------------ queries
    def _rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _event_rows(self, rows):
        for r in rows:
            r["data"] = json.loads(r.pop("data_json"))
            r["links"] = json.loads(r.pop("links_json"))
        return rows

    def count_events(self, watch_id=None, desk=None, kind=None, grade=None):
        sql = "SELECT COUNT(*) AS n FROM events WHERE 1=1"
        args = []
        for col, val in (("watch_id", watch_id), ("desk", desk),
                         ("kind", kind), ("grade", grade)):
            if val is not None:
                sql += " AND %s=?" % col
                args.append(val)
        return self.db.execute(sql, args).fetchone()["n"]

    def events(self, watch_id=None, desk=None, kinds=None, emitter=None,
               t_from=None, t_to=None, limit=500, newest=False):
        sql = "SELECT * FROM events WHERE 1=1"
        args = []
        if watch_id:
            sql += " AND watch_id=?"
            args.append(watch_id)
        if desk:
            sql += " AND desk=?"
            args.append(desk)
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args.extend(kinds)
        if emitter:
            keys = self.aliased_keys(self.resolve(emitter))
            sql += " AND emitter IN (%s)" % ",".join("?" * len(keys))
            args.extend(keys)
        if t_from is not None:
            sql += " AND t_start>=?"
            args.append(t_from)
        if t_to is not None:
            sql += " AND t_start<?"
            args.append(t_to)
        sql += " ORDER BY t_start %s LIMIT ?" % ("DESC" if newest
                                                 else "ASC")
        args.append(limit)
        return self._event_rows(self._rows(sql, args))

    def events_near_freq(self, freq_hz, tol_hz, watch_id=None, limit=200,
                         newest=False):
        sql = ("SELECT * FROM events WHERE freq_hz IS NOT NULL "
               "AND abs(freq_hz-?)<=?")
        args = [freq_hz, tol_hz]
        if watch_id:
            sql += " AND watch_id=?"
            args.append(watch_id)
        sql += " ORDER BY t_start %s LIMIT ?" % ("DESC" if newest
                                                 else "ASC")
        args.append(limit)
        return self._event_rows(self._rows(sql, args))

    def events_in_band(self, lo_hz, hi_hz, watch_id=None, limit=500,
                       newest=False):
        sql = ("SELECT * FROM events WHERE freq_hz>=? AND freq_hz<?")
        args = [lo_hz, hi_hz]
        if watch_id:
            sql += " AND watch_id=?"
            args.append(watch_id)
        sql += " ORDER BY t_start %s LIMIT ?" % ("DESC" if newest
                                                 else "ASC")
        args.append(limit)
        return self._event_rows(self._rows(sql, args))

    def population(self, watch_id, desk=None):
        sql = ("SELECT COUNT(DISTINCT emitter) AS n FROM events "
               "WHERE watch_id=? AND emitter IS NOT NULL")
        args = [watch_id]
        if desk:
            sql += " AND desk=?"
            args.append(desk)
        return self.db.execute(sql, args).fetchone()["n"]

    def population_by_desk(self, watch_id):
        rows = self._rows(
            "SELECT desk, COUNT(DISTINCT emitter) AS n FROM events "
            "WHERE watch_id=? AND emitter IS NOT NULL GROUP BY desk",
            (watch_id,))
        return {r["desk"]: r["n"] for r in rows}

    def counts_by_desk(self, watch_id):
        rows = self._rows(
            "SELECT desk, COUNT(*) AS n FROM events WHERE watch_id=? "
            "GROUP BY desk", (watch_id,))
        return {r["desk"]: r["n"] for r in rows}

    def counts_by_grade(self, watch_id):
        rows = self._rows(
            "SELECT grade, COUNT(*) AS n FROM events WHERE watch_id=? "
            "GROUP BY grade", (watch_id,))
        return {r["grade"]: r["n"] for r in rows}

    def dwell_records(self, watch_id):
        return self.events(watch_id=watch_id, kinds=["dwell"], limit=100000)

    def assessments(self, watch_id):
        return self.events(watch_id=watch_id, kinds=["assessment"],
                           limit=100000)

    def dossier(self, key):
        canonical = self.resolve(key)
        row = self.db.execute("SELECT * FROM dossiers WHERE key=?",
                              (canonical,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["key"] = canonical
        out["watches"] = json.loads(out.pop("watches_json"))
        out["aliases"] = self.aliased_keys(canonical)
        return out

    def all_dossiers(self):
        rows = self._rows("SELECT * FROM dossiers ORDER BY first_seen")
        for r in rows:
            r["watches"] = json.loads(r.pop("watches_json"))
            r["aliases"] = self.aliased_keys(r["key"])
        return rows

    def dossiers_in_watch(self, watch_id):
        return [d for d in self.all_dossiers() if watch_id in d["watches"]]

    def regulars(self, min_watches=2):
        return [d for d in self.all_dossiers()
                if len(d["watches"]) >= min_watches]

    # ---------------------------------------------------------- baselines
    def put_baseline(self, scope, hour, watch_count, stats):
        self.db.execute(
            "INSERT OR REPLACE INTO baselines VALUES (?,?,?,?)",
            (scope, hour, watch_count, json.dumps(stats)))
        self.db.commit()

    def get_baseline(self, scope, hour):
        row = self.db.execute(
            "SELECT * FROM baselines WHERE scope=? AND hour=?",
            (scope, hour)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["stats"] = json.loads(out.pop("stats_json"))
        out["provisional"] = out["watch_count"] < 3
        return out

    def baseline_scopes(self):
        rows = self._rows("SELECT DISTINCT scope FROM baselines")
        return [r["scope"] for r in rows]

    # ----------------------------------------------------------- snippets
    def _record_snippet_refusal(self, watch_id, desk, kind, filename,
                                size, reason, t):
        """A refused snippet leaves an auditable record, never silence."""
        self.add_event(evmod.make_event(
            watch_id, desk, "system", t, t, grade="CONFIRMED",
            content="snippet refused: %s (%s)" % (filename, reason),
            data={"snippet_refused": filename, "snippet_kind": kind,
                  "bytes": size, "reason": reason}))

    def keep_snippet(self, watch_id, watch_dir, desk, kind, filename, data,
                     ceiling_bytes, count_cap=None, t=None):
        """Write a snippet under quota and ceiling. Returns (path, shed).

        Sheds oldest current-watch snippets (satellite imagery last)
        when the ceiling demands it; every shed is reported so the
        officer can file it as an event. Returns (None, shed) when the
        snippet itself does not fit policy, and files a refusal event
        saying why: a drop is a coverage fact, not silence. A filename
        that already has a live row replaces it: the old row is
        retired as shed, so one file on disk is exactly one live row
        and the accounting cannot double-count it.
        """
        import time as _time
        if t is None:
            t = _time.time()
        snip_dir = os.path.join(watch_dir, "snippets")
        os.makedirs(snip_dir, exist_ok=True)
        rel = os.path.join("snippets", filename)
        shed = []
        if count_cap is not None:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM snippets WHERE watch_id=? "
                "AND desk=? AND kind=? AND shed=0",
                (watch_id, desk, kind)).fetchone()
            if row["n"] >= count_cap:
                self.db.commit()
                self._record_snippet_refusal(
                    watch_id, desk, kind, filename, len(data),
                    "the per-desk snippet count cap is reached", t)
                return None, shed
        total = self._snippet_bytes(watch_id)
        while total + len(data) > ceiling_bytes:
            victim = self.db.execute(
                "SELECT * FROM snippets WHERE watch_id=? AND shed=0 "
                "ORDER BY (kind='satellite') ASC, t ASC LIMIT 1",
                (watch_id,)).fetchone()
            if victim is None:
                self.db.commit()
                self._record_snippet_refusal(
                    watch_id, desk, kind, filename, len(data),
                    "the snippet does not fit the disk ceiling", t)
                return None, shed
            try:
                os.remove(os.path.join(watch_dir, victim["path"]))
            except OSError:
                pass
            self.db.execute("UPDATE snippets SET shed=1 WHERE id=?",
                            (victim["id"],))
            shed.append(dict(victim))
            total -= victim["bytes"]
        # Replacement retires the previous live row for this path
        # (unless the ceiling loop already shed it as a victim), so
        # the file about to be written is the row's only accounting.
        existing = self.db.execute(
            "SELECT * FROM snippets WHERE watch_id=? AND path=? "
            "AND shed=0", (watch_id, rel)).fetchone()
        if existing is not None:
            self.db.execute("UPDATE snippets SET shed=1 WHERE id=?",
                            (existing["id"],))
            shed.append(dict(existing))
        with open(os.path.join(watch_dir, rel), "wb") as fh:
            fh.write(data)
        from . import util as _util
        self.db.execute(
            "INSERT INTO snippets VALUES (?,?,?,?,?,?,?,?)",
            (_util.new_id("sn"), watch_id, desk, kind, rel, len(data), 0,
             t))
        self.db.commit()
        return rel, shed

    def _snippet_bytes(self, watch_id):
        row = self.db.execute(
            "SELECT COALESCE(SUM(bytes),0) AS b FROM snippets "
            "WHERE watch_id=? AND shed=0", (watch_id,)).fetchone()
        return row["b"]

    def snippet_accounting(self, watch_id):
        kept = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b FROM "
            "snippets WHERE watch_id=? AND shed=0", (watch_id,)).fetchone()
        shed = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b FROM "
            "snippets WHERE watch_id=? AND shed=1", (watch_id,)).fetchone()
        return {"kept_count": kept["n"], "kept_bytes": kept["b"],
                "shed_count": shed["n"], "shed_bytes": shed["b"]}

    # --------------------------------------------------------- watch meta
    def set_watch_meta(self, watch_id, meta):
        self.db.execute("INSERT OR REPLACE INTO watch_meta VALUES (?,?)",
                        (watch_id, json.dumps(meta)))
        self.db.commit()

    def get_watch_meta(self, watch_id):
        row = self.db.execute("SELECT data_json FROM watch_meta WHERE "
                              "watch_id=?", (watch_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def close(self):
        for handle in self._log_handles.values():
            handle.close()
        self._log_handles.clear()
        self.db.close()
