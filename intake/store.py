"""SQLite storage.

Tables
  jobs      one row per unique job (real columns, so you can query it with any SQLite tool)
  listings  every (source, source_job_id) that points at a job — drives open/closed detection
  changes   field-level history (pay, title, workplace, status…) across runs
  runs      one row per collection run with per-source results
  llm       cached AI enrichment, keyed by job_id + content_hash
"""

import json
import sqlite3
from datetime import datetime, timezone

from .util import now_iso

SCHEMA_VERSION = 2

CORE_COLUMNS = [
    "job_id", "status", "closed_at", "first_seen", "last_seen", "times_seen", "also_listed_on", "source_count",
    "last_run",
]
TRACKED = ["title", "salary_min_usd_annual", "salary_max_usd_annual", "workplace_type", "employment_type",
           "location_raw", "seniority", "status"]
NUMERIC = {"seniority_rank", "salary_min", "salary_max", "salary_min_usd_annual", "salary_max_usd_annual",
           "salary_mid_usd_annual", "years_experience_min", "years_experience_max", "skill_count",
           "description_word_count", "data_quality_score", "times_seen", "source_count", "age_days"}


def _sqltype(col):
    return "REAL" if col in NUMERIC else "TEXT"


class Store:
    def __init__(self, path, migrate=True):
        self.path = path
        self.con = sqlite3.connect(path, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA busy_timeout=30000")
        if migrate:
            self._migrate()

    # -- schema --------------------------------------------------------------
    def _migrate(self):
        c = self.con
        c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # v1 stored each job as a JSON blob in jobs.data — convert in place
        if "jobs" in tables and "data" in {r[1] for r in c.execute("PRAGMA table_info(jobs)")}:
            c.execute("ALTER TABLE jobs RENAME TO jobs_v1")
            tables.discard("jobs")
            tables.add("jobs_v1")
        c.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY)")
        c.execute("""CREATE TABLE IF NOT EXISTS listings (
            source_key TEXT, source_job_id TEXT, job_id TEXT, first_seen TEXT, last_seen TEXT,
            PRIMARY KEY (source_key, source_job_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS listings_job ON listings(job_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS changes (
            job_id TEXT, at TEXT, field TEXT, old TEXT, new TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS changes_job ON changes(job_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, args TEXT,
            fetched INTEGER, valid INTEGER, new INTEGER, updated INTEGER, closed INTEGER, reopened INTEGER,
            sources TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS llm (
            job_id TEXT, content_hash TEXT, model TEXT, at TEXT, data TEXT, PRIMARY KEY (job_id, content_hash))""")
        c.execute("""CREATE TABLE IF NOT EXISTS tracking (
            job_id TEXT PRIMARY KEY, status TEXT, notes TEXT, updated_at TEXT, history TEXT)""")
        if "jobs_v1" in tables:
            self._import_v1()
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        c.commit()

    def ensure_columns(self, cols):
        have = {r[1] for r in self.con.execute("PRAGMA table_info(jobs)")}
        for col in cols:
            if col not in have:
                self.con.execute('ALTER TABLE jobs ADD COLUMN "%s" %s' % (col, _sqltype(col)))
                have.add(col)
        for idx in ("status", "company_slug", "job_function", "country", "posted_at"):
            if idx in have:
                self.con.execute('CREATE INDEX IF NOT EXISTS jobs_%s ON jobs("%s")' % (idx, idx))

    def _import_v1(self):
        rows = self.con.execute("SELECT data, first_seen, last_seen, times_seen, sources FROM jobs_v1").fetchall()
        for data, fs, ls, ts, srcs in rows:
            d = json.loads(data)
            d.pop("age_days", None)
            d.update(status="open", first_seen=fs, last_seen=ls, times_seen=ts, also_listed_on=srcs,
                     source_count=len((srcs or "").split(",")))
            self.ensure_columns(d.keys())
            self._write(d)
        self.con.execute("DROP TABLE jobs_v1")

    # -- writes --------------------------------------------------------------
    def _write(self, d):
        cols = list(d.keys())
        self.con.execute('INSERT OR REPLACE INTO jobs (%s) VALUES (%s)' % (
            ",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))), [_cell(d[c]) for c in cols])

    def get(self, job_id):
        r = self.con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def upsert(self, jobs, run_started):
        """Insert/merge enriched jobs. Returns (new, updated)."""
        if not jobs:
            return 0, 0
        self.ensure_columns(list(jobs[0].keys()) + CORE_COLUMNS)
        new = updated = 0
        now = now_iso()
        before = {}  # job_id -> record as it was before this run touched it (for the change log)
        for j in jobs:
            self.con.execute(
                "INSERT INTO listings VALUES (?,?,?,?,?) ON CONFLICT(source_key, source_job_id) "
                "DO UPDATE SET last_seen=excluded.last_seen, job_id=excluded.job_id",
                (j["source_key"], j["source_job_id"] or j["url"], j["job_id"], run_started, run_started))
            old = self.get(j["job_id"])
            if old is None:
                rec = dict(j, status="open", closed_at="", first_seen=now, last_seen=now, times_seen=1,
                           also_listed_on=j["source"], source_count=1, last_run=run_started)
                self._write(rec)
                new += 1
                continue
            srcs = sorted(set(filter(None, (old.get("also_listed_on") or "").split(","))) | {j["source"]})
            # keep the richer record: structured pay beats parsed; longer description beats shorter
            loses_pay = bool(old.get("salary_min_usd_annual")) and not j.get("salary_min_usd_annual")
            richer = not loses_pay and (
                (j["salary_source"] == "structured" and old.get("salary_source") != "structured") or
                (j["description_word_count"] or 0) > (old.get("description_word_count") or 0) * 1.2 or
                old.get("source_key") == j["source_key"])
            rec = dict(j) if richer else {k: old.get(k) for k in j}
            if not richer:  # still take fields the old record was missing
                for k, v in j.items():
                    if rec.get(k) in (None, "", 0) and v not in (None, "", 0, False):
                        rec[k] = v
            already_seen_this_run = old.get("last_run") == run_started
            if already_seen_this_run:
                # a second listing of the same job in this run (e.g. one role posted twice): don't let the two
                # versions overwrite each other — keep the first and only fill in fields it was missing
                rec = {k: old.get(k) for k in j}
                for k, v in j.items():
                    if rec.get(k) in (None, "", 0) and v not in (None, "", 0, False):
                        rec[k] = v
            rec.update(status="open", closed_at="", first_seen=old["first_seen"], last_seen=now,
                       times_seen=(old.get("times_seen") or 0) + (0 if already_seen_this_run else 1),
                       also_listed_on=",".join(srcs), source_count=len(srcs), last_run=run_started)
            if not already_seen_this_run:
                before[j["job_id"]] = old
            self._write(rec)
            updated += 1
        for job_id, old in before.items():  # compare start-of-run vs end-of-run, so duplicates can't add noise
            self._log_changes(old, self.get(job_id), now)
        self.con.commit()
        return new, updated

    def _log_changes(self, old, new, at):
        for f in TRACKED:
            a, b = old.get(f), new.get(f)
            if _norm(a) != _norm(b) and not (f == "status" and a is None):
                self.con.execute("INSERT INTO changes VALUES (?,?,?,?,?)", (new["job_id"], at, f, _cell(a), _cell(b)))

    def close_missing(self, complete_sources, run_started):
        """A job is closed when every listing it has is from a source that returned its full list this run
        and none of those listings were seen this run. Returns (closed, reopened_count_unused)."""
        if not complete_sources:
            return 0
        complete = set(complete_sources)
        rows = self.con.execute(
            "SELECT j.job_id, l.source_key, l.last_seen FROM jobs j JOIN listings l ON l.job_id=j.job_id WHERE j.status='open'"
        ).fetchall()
        by_job = {}
        for job_id, key, seen in rows:
            by_job.setdefault(job_id, []).append((key, seen))
        now = now_iso()
        closed = 0
        # orphans: open jobs whose listings were all re-pointed to another job_id (e.g. after a parser fix)
        for (job_id,) in self.con.execute(
                "SELECT job_id FROM jobs j WHERE status='open' AND source_key IN (%s) AND (last_run IS NULL OR last_run < ?) "
                "AND NOT EXISTS (SELECT 1 FROM listings l WHERE l.job_id=j.job_id)" % ",".join("?" * len(complete)),
                list(complete) + [run_started]).fetchall():
            self.con.execute("UPDATE jobs SET status='closed', closed_at=? WHERE job_id=?", (now, job_id))
            self.con.execute("INSERT INTO changes VALUES (?,?,?,?,?)", (job_id, now, "status", "open", "closed (duplicate)"))
            closed += 1
        for job_id, ls in by_job.items():
            # listings are stamped with the run's start time, so "seen this run" is last_seen == run_started
            if all(k in complete and (s or "") < run_started for k, s in ls):
                self.con.execute("UPDATE jobs SET status='closed', closed_at=? WHERE job_id=?", (now, job_id))
                self.con.execute("INSERT INTO changes VALUES (?,?,?,?,?)", (job_id, now, "status", "open", "closed"))
                closed += 1
        self.con.commit()
        return closed

    def record_run(self, **kw):
        cols = list(kw)
        self.con.execute("INSERT INTO runs (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                         [json.dumps(v) if isinstance(v, (dict, list)) else v for v in kw.values()])
        self.con.commit()

    # -- reads ---------------------------------------------------------------
    def rows(self, include_closed=False):
        q = "SELECT * FROM jobs" + ("" if include_closed else " WHERE status='open'")
        now = datetime.now(timezone.utc)
        llm = {r[0]: json.loads(r[1]) for r in self.con.execute(
            "SELECT job_id, data FROM llm l WHERE at = (SELECT MAX(at) FROM llm WHERE job_id=l.job_id)")}
        for r in self.con.execute(q):
            d = dict(r)
            d["age_days"] = _age(d.get("posted_at"), now)
            for k, v in (llm.get(d["job_id"]) or {}).items():
                d["claude_" + k] = "; ".join(v) if isinstance(v, list) else v
            yield d

    def count(self, where="1=1"):
        return self.con.execute("SELECT COUNT(*) FROM jobs WHERE " + where).fetchone()[0]

    def recent_runs(self, n=10):
        return [dict(r) for r in self.con.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (n,))]

    def changes_since(self, since):
        return [dict(r) for r in self.con.execute(
            "SELECT c.*, j.title, j.company FROM changes c LEFT JOIN jobs j USING(job_id) "
            "WHERE c.at >= ? ORDER BY c.at DESC", (since,))]

    def update_fields(self, job_id, fields):
        """Overwrite some columns of one job (adds columns that don't exist yet)."""
        if not fields:
            return
        self.ensure_columns(list(fields))
        cols = list(fields)
        self.con.execute('UPDATE jobs SET %s WHERE job_id=?' % ", ".join('"%s"=?' % c for c in cols),
                         [_cell(fields[c]) for c in cols] + [job_id])
        self.con.commit()

    # -- personal tracking ----------------------------------------------------
    def tracking(self):
        return {r["job_id"]: dict(r) for r in self.con.execute("SELECT * FROM tracking")}

    def track(self, job_id, status=None, notes=None):
        """Set status (one of TRACK_STATUSES, or "" to clear) and/or notes. Returns the tracking row."""
        if status is not None and status not in TRACK_STATUSES + [""]:
            raise ValueError("status must be one of: %s (or empty to clear)" % ", ".join(TRACK_STATUSES))
        cur = self.con.execute("SELECT * FROM tracking WHERE job_id=?", (job_id,)).fetchone()
        cur = dict(cur) if cur else {"job_id": job_id, "status": "", "notes": "", "history": "[]"}
        now = now_iso()
        hist = json.loads(cur.get("history") or "[]")
        if status is not None and status != cur["status"]:
            hist.append({"at": now, "status": status})
            cur["status"] = status
        if notes is not None:
            cur["notes"] = notes
        cur["updated_at"] = now
        cur["history"] = json.dumps(hist)
        self.con.execute("INSERT OR REPLACE INTO tracking VALUES (?,?,?,?,?)",
                         (job_id, cur["status"], cur["notes"], now, cur["history"]))
        self.con.commit()
        return cur

    def meta_get(self, key, default=None):
        r = self.con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def meta_set(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))
        self.con.commit()

    def job_changes(self, job_id):
        return [dict(r) for r in self.con.execute(
            "SELECT at, field, old, new FROM changes WHERE job_id=? ORDER BY at DESC", (job_id,))]

    def llm_get(self, job_id, content_hash):
        r = self.con.execute("SELECT data FROM llm WHERE job_id=? AND content_hash=?", (job_id, content_hash)).fetchone()
        return json.loads(r[0]) if r else None

    def llm_put(self, job_id, content_hash, model, data):
        self.con.execute("INSERT OR REPLACE INTO llm VALUES (?,?,?,?,?)",
                         (job_id, content_hash, model, now_iso(), json.dumps(data)))
        self.con.commit()


TRACK_STATUSES = ["saved", "applied", "interviewing", "offer", "rejected", "hidden"]


def _cell(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


def _norm(v):
    if v in (None, ""):
        return ""
    try:
        return str(round(float(v)))
    except (TypeError, ValueError):
        return str(v)


def _age(posted, now):
    if not posted:
        return ""
    try:
        return (now - datetime.fromisoformat(posted)).days
    except ValueError:
        return ""
