"""Service layer shared by the web app, the REST API and the MCP server.

Everything returns plain JSON-able dicts. One Service instance per process; it caches the job table in
memory (≈10k rows) and reloads it after each collection or when the database file changes.
"""

import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from . import cli, http
from . import search as SE
from . import profile as P
from .store import TRACK_STATUSES, Store
from .util import LISTENERS, now_iso

SUMMARY_FIELDS = [
    "job_id", "status", "title", "company", "company_logo", "job_function", "job_subfunction", "seniority",
    "employment_type", "workplace_type", "remote_scope", "city", "state", "country", "location_raw",
    "salary_min_usd_annual", "salary_max_usd_annual", "salary_mid_usd_annual", "salary_min", "salary_max",
    "salary_currency", "salary_period", "posted_at", "age_days", "url", "apply_url", "also_listed_on",
    "industry_guess", "visa_sponsorship", "years_experience_min", "data_quality_score", "first_seen",
    "contact_emails", "apply_emails", "email_purposes", "company_website", "application_method", "email_count", "link_count",
] + P.SKILL_FIELDS
SORTS = {"match", "relevance", "newest", "salary", "company"}
HAS = {
    # "email" = an address you'd actually write to (apply/contact), not accommodations/privacy/benefits boilerplate
    "email": lambda j: "=apply" in (j.get("email_purposes") or "") or "=contact" in (j.get("email_purposes") or ""),
    "any-email": lambda j: bool(j.get("contact_emails")),
    "apply-email": lambda j: bool(j.get("apply_emails")),
    "salary": lambda j: bool(j.get("salary_min_usd_annual")),
    "pay": lambda j: bool(j.get("salary_min_usd_annual")),
    "form": lambda j: bool(j.get("links_form")),
    "link": lambda j: bool(j.get("link_count")),
    "links": lambda j: bool(j.get("link_count")),
    "website": lambda j: bool(j.get("company_website")),
    "linkedin": lambda j: bool(j.get("company_linkedin")),
    "contact": lambda j: bool(j.get("contact_names") or j.get("apply_emails")),
    "visa": lambda j: j.get("visa_sponsorship") == "Yes",
    "notes": lambda j: bool(j.get("notes")),
    "equity": lambda j: j.get("benefit_equity") in (True, "TRUE"),
}


def _has(j, what):
    fn = HAS.get(what)
    return fn(j) if fn else True


def _subfunction_map():
    from .taxonomy import FUNCTION_RULES
    m = {}
    for f, sub, _ in FUNCTION_RULES:
        m.setdefault(f, [])
        if sub not in m[f]:
            m[f].append(sub)
    return m


class Service:
    def __init__(self, db_path, companies_path=None):
        self.db_path = db_path
        self.companies_path = companies_path or cli.DEFAULT_COMPANIES
        self._local = threading.local()
        Store(db_path)  # create/migrate the schema once up front
        self.lock = threading.RLock()
        self._rows = None
        self._mtime = None
        self._matcher = None
        self.collect_state = {"running": False, "started": None, "finished": None, "log": [], "result": None}
        self._collect_lock = threading.Lock()

    @property
    def store(self):
        """One SQLite connection per thread — sqlite3 connections must not be shared across threads."""
        st = getattr(self._local, "store", None)
        if st is None:
            st = self._local.store = Store(self.db_path, migrate=False)
        return st

    def fts_ok(self):
        if getattr(self, "_fts", None) is None:
            self._fts = SE.available(self.store.con)
        return self._fts

    def ensure_index(self, force=False):
        if self.fts_ok() and (force or SE.needs_rebuild(self.store.con)):
            SE.rebuild(self.store.con)

    # -- cache ---------------------------------------------------------------
    def _db_mtime(self):
        try:
            return max(os.path.getmtime(p) for p in (self.db_path, self.db_path + "-wal") if os.path.exists(p))
        except ValueError:
            return 0

    def rows(self):
        with self.lock:
            m = self._db_mtime()
            if self._rows is None or (m != self._mtime and not self.collect_state["running"]):
                tracking = self.store.tracking()
                rows = []
                for r in self.store.rows(include_closed=True):
                    t = tracking.get(r["job_id"])
                    if r.get("status") == "closed" and not (t and t.get("status")):
                        continue  # closed jobs are only interesting if you were tracking them
                    r["track"] = (t or {}).get("status") or ""
                    r["notes"] = (t or {}).get("notes") or ""
                    rows.append(r)
                self._rows, self._mtime = rows, m
                self._matcher = None
                self.ensure_index()
                self._mtime = self._db_mtime()
            return self._rows

    def invalidate(self):
        with self.lock:
            self._rows = None

    def matcher(self):
        with self.lock:
            if self._matcher is None:
                self._matcher = P.Matcher(self.get_profile())
            return self._matcher

    # -- profile -------------------------------------------------------------
    def get_profile(self):
        with self.lock:
            return P.normalise(self.store.meta_get("profile", {}))

    def update_profile(self, changes):
        with self.lock:
            prof = self.get_profile()
            prof.update(changes or {})
            prof = P.normalise(prof)
            self.store.meta_set("profile", prof)
            self._mtime = self._db_mtime()
            self._matcher = None
            return prof

    # -- search --------------------------------------------------------------
    def search(self, q="", function=None, subfunction=None, seniority=None, workplace=None, country=None,
               company=None, source=None, min_salary=None, has_salary=False, max_age=None, min_match=None,
               track=None, include_hidden=False, include_excluded=False, sort="match", limit=50, offset=0,
               fields=None, _max_limit=500, has=None, domain=None, employment_type=None, has_email=False):
        """Filter + rank jobs. `q` accepts the full query language (see search.py). `track` =
        saved|applied|…|tracked (any)|untracked. Returns {total, jobs, facets, query}."""
        self.rows()  # loads data and makes sure the search index exists
        m = self.matcher()
        want = lambda v: [x.lower() for x in (v if isinstance(v, (list, tuple)) else str(v).split(","))] if v else []
        # -- full-text query language → FTS hits + structured filters
        hits, excl, terms, qinfo = None, set(), [], {"errors": []}
        if q and q.strip():
            if self.fts_ok():
                p = SE.parse(q)
                qinfo = {"errors": p["errors"], "match": p["match"], "filters": p["filters"]}
                f = p["filters"]
                function = function or f.get("function")
                subfunction = subfunction or f.get("subfunction")
                seniority = seniority or f.get("seniority")
                workplace = workplace or f.get("workplace")
                source = source or f.get("source")
                domain = domain or f.get("domain")
                employment_type = employment_type or f.get("employment_type")
                min_salary = min_salary or f.get("min_salary")
                max_age = max_age or f.get("max_age")
                min_match = min_match or f.get("min_match")
                track = track or f.get("track")
                has = list(want(has)) + f.get("has", [])
                if p["match"] or f.get("exclude_match"):
                    try:
                        res = SE.run(self.store.con, p["match"], f.get("exclude_match"))
                    except Exception as e:  # malformed FTS syntax → tell the caller instead of failing
                        return {"total": 0, "offset": 0, "limit": int(limit), "jobs": [], "facets": {},
                                "query": {"errors": ["couldn't parse the search: %s" % e]}}
                    excl = res.pop("__exclude__", set())
                    hits = res if p["match"] else None
            else:
                terms = [t.strip().lower() for t in q.replace(",", " ").split() if t.strip()]
        has = set(want(has))
        if has_email:
            has.add("email")
        fn, sub, sen, wp = want(function), want(subfunction), want(seniority), want(workplace)
        out, facets = [], {k: Counter() for k in ("job_function", "seniority", "workplace_type", "country", "track")}
        for j in self.rows():
            tr = j.get("track") or ""
            if track:
                if track == "tracked" and not tr or track == "untracked" and tr or track not in ("tracked", "untracked") and tr != track:
                    continue
            elif tr == "hidden" and not include_hidden:
                continue
            if not include_excluded and not track and m.excluded(j):
                continue
            if hits is not None and j["job_id"] not in hits or j["job_id"] in excl:
                continue
            if has and not all(_has(j, h) for h in has):
                continue
            if domain and domain not in " ".join(str(j.get(k) or "") for k in ("company_domain", "company_website", "contact_emails", "url")).lower():
                continue
            if employment_type and employment_type.lower() not in (j.get("employment_type") or "").lower():
                continue
            if terms:
                hay = j.get("_hay")
                if hay is None:
                    hay = j["_hay"] = " ".join(str(j.get(k) or "") for k in (
                        "title", "company", "job_subfunction", "tags", "location_raw", "description")).lower()
                if not all(t in hay for t in terms):
                    continue
            if company and company.lower() not in (j.get("company") or "").lower():
                continue
            if source and source.lower() not in (j.get("also_listed_on") or "").lower():
                continue
            if country and country.lower() not in " ".join(str(j.get(k) or "") for k in ("country", "location_raw", "remote_scope")).lower():
                continue
            if min_salary and not (j.get("salary_max_usd_annual") and float(j["salary_max_usd_annual"]) >= float(min_salary)):
                continue
            if has_salary and not j.get("salary_min_usd_annual"):
                continue
            if max_age and j.get("age_days") not in ("", None) and int(j["age_days"]) > int(max_age):
                continue
            sc = j.get("_sc")
            if sc is None or sc[0] is not m:  # scores are cached per profile version
                sc = j["_sc"] = (m,) + m.score(j)
            score, reasons = sc[1], sc[2]
            if min_match and score < int(min_match):
                continue
            # facets are counted before the facet-able filters so the UI can show alternatives
            facets["job_function"][j.get("job_function") or "Other"] += 1
            facets["seniority"][j.get("seniority") or "Unknown"] += 1
            facets["workplace_type"][(j.get("workplace_type") or "Unknown").replace(" (inferred)", "")] += 1
            facets["country"][(j.get("country") or "Unknown").split(";")[0]] += 1
            facets["track"][tr or "new"] += 1
            if fn and (j.get("job_function") or "").lower() not in fn:
                continue
            if sub and (j.get("job_subfunction") or "").lower() not in sub:
                continue
            if sen and (j.get("seniority") or "").lower() not in sen:
                continue
            if wp and (j.get("workplace_type") or "").replace(" (inferred)", "").lower() not in wp:
                continue
            out.append((score, reasons, j, hits.get(j["job_id"]) if hits else None))
        blend = lambda t: (0.55 * t[3] + 0.45 * t[0]) if t[3] is not None else t[0]
        match_expr = qinfo.get("match")
        key = {
            "match": lambda t: (blend(t), t[2].get("posted_at") or ""),
            "relevance": lambda t: (t[3] if t[3] is not None else 0, t[0]),
            "newest": lambda t: (t[2].get("posted_at") or "", t[0]),
            "salary": lambda t: (float(t[2].get("salary_max_usd_annual") or 0), t[0]),
            "company": lambda t: ((t[2].get("company") or "").lower(),),
        }[sort if sort in SORTS else "match"]
        out.sort(key=key, reverse=sort != "company")
        page = out[int(offset):int(offset) + max(1, min(int(limit), _max_limit))]
        flds = fields or SUMMARY_FIELDS
        snips = SE.snippets(self.store.con, match_expr, [t[2]["job_id"] for t in page]) if match_expr and hits else {}
        jobs = []
        for s_, r_, j_, rel in page:
            d = self._summary(j_, s_, r_, flds)
            if rel is not None:
                d.update(relevance=rel, snippet=snips.get(j_["job_id"], ""))
            jobs.append(d)
        return {"total": len(out), "offset": int(offset), "limit": int(limit), "jobs": jobs,
                "facets": {k: c.most_common(25) for k, c in facets.items()}, "query": qinfo}

    def _summary(self, j, score, reasons, fields):
        d = {k: j.get(k) for k in fields}
        d.update(match=score, reasons=[{"ok": ok, "text": t} for ok, t in reasons], track=j.get("track") or "",
                 has_notes=bool(j.get("notes")), matched_skills=j.get("_matched_skills", []))
        return d

    def job(self, job_id):
        with self.lock:
            j = next((r for r in self.rows() if r["job_id"] == job_id), None)
            if j is None:
                j = self.store.get(job_id)
                if j is None:
                    return None
            j = {k: v for k, v in j.items() if not k.startswith("_")}
            score, reasons = self.matcher().score(j)
            t = self.store.tracking().get(job_id) or {}
            j.update(match=score, reasons=[{"ok": ok, "text": tx} for ok, tx in reasons],
                     track=t.get("status") or "", notes=t.get("notes") or "",
                     track_history=__import__("json").loads(t.get("history") or "[]"),
                     changes=self.store.job_changes(job_id), matched_skills=j.pop("_matched_skills", []))
            from . import pages
            j["page_fetch"] = pages.last_fetch(self.store, job_id)
            return j

    def fetch_posting(self, job_id):
        """Fetch the job's own page (robots.txt permitting), merge what it adds, return the refreshed job."""
        from . import pages
        j = self.store.get(job_id)
        if j is None:
            raise KeyError(job_id)
        http.STOP.clear()
        status, details = pages.fetch(self.store, j)
        if self.fts_ok():
            SE.update(self.store.con, [job_id])
        self.invalidate()
        out = self.job(job_id)
        out["page_fetch"] = {"status": status, **details}
        return out

    def fetch_postings(self, min_match=70, limit=50, refetch=False):
        """Background batch: fetch posting pages for your best matches. Poll collect_status()."""
        from . import pages
        if not self._collect_lock.acquire(blocking=False):
            return {"started": False, "reason": "a collection or fetch is already running"}
        done = {r[0] for r in self.store.con.execute("SELECT job_id FROM pages")} if not refetch and self._has_pages() else set()
        jobs = [j for j in self.search(min_match=min_match, limit=5000, _max_limit=5000, fields=["job_id"])["jobs"]
                if j["job_id"] not in done][:int(limit)]
        st = self.collect_state
        st.update(running=True, started=now_iso(), finished=None, log=["Fetching %d posting pages…" % len(jobs)], result=None)

        def work():
            http.STOP.clear()
            try:
                store = Store(self.db_path, migrate=False)
                rows = [store.get(j["job_id"]) for j in jobs]
                counts = pages.fetch_many(store, [r for r in rows if r])
                if self.fts_ok():
                    SE.rebuild(store.con)
                st["result"] = {"ok": True, "fetched": dict(counts)}
                st["log"].append("Done: " + ", ".join("%s %d" % kv for kv in counts.items()))
            except BaseException as e:  # noqa: BLE001
                st["result"] = {"ok": False, "error": str(e)}
            finally:
                st.update(running=False, finished=now_iso())
                self.invalidate()
                self._collect_lock.release()

        threading.Thread(target=work, daemon=True, name="pages").start()
        return {"started": True, "jobs": len(jobs)}

    def _has_pages(self):
        return bool(self.store.con.execute("SELECT name FROM sqlite_master WHERE name='pages'").fetchone())

    def track(self, job_id, status=None, notes=None):
        with self.lock:
            if self.store.get(job_id) is None:
                raise KeyError(job_id)
            row = self.store.track(job_id, status, notes)
            self._mtime = self._db_mtime()  # our own write — no need to reload everything
            for r in self._rows or []:
                if r["job_id"] == job_id:
                    r["track"], r["notes"] = row["status"], row["notes"]
            return {"job_id": job_id, "track": row["status"], "notes": row["notes"], "updated_at": row["updated_at"]}

    def pipeline(self):
        cols = {s: [] for s in TRACK_STATUSES if s != "hidden"}
        res = self.search(track="tracked", sort="newest", limit=5000, _max_limit=5000, include_excluded=True)
        for j in res["jobs"]:
            if j["track"] in cols:
                cols[j["track"]].append(j)
        return {"columns": cols, "counts": {k: len(v) for k, v in cols.items()}}

    # -- insights ------------------------------------------------------------
    def stats(self, min_match=60):
        res = self.search(min_match=min_match, limit=500)
        alljobs = self.rows()
        top = self.search(min_match=min_match, limit=100000, _max_limit=100000, fields=["job_id", "company", "salary_mid_usd_annual",
                                                                       "workplace_type", "seniority"] + P.SKILL_FIELDS)["jobs"]
        skills = Counter(s for j in top for f in P.SKILL_FIELDS for s in (j.get(f) or "").split("; ") if s)
        mine = {s.lower() for s in self.get_profile()["skills"]}
        sal = sorted(float(j["salary_mid_usd_annual"]) for j in top if j.get("salary_mid_usd_annual"))
        q = lambda p: round(sal[min(len(sal) - 1, int(p * len(sal)))]) if sal else None
        runs = self.store.recent_runs(1)
        return {
            "jobs_total": len(alljobs), "open_total": sum(1 for j in alljobs if j.get("status") == "open"),
            "matches": res["total"], "min_match": min_match,
            "top_companies": Counter(j["company"] for j in top).most_common(12),
            "top_skills": [{"skill": s, "count": c, "yours": s.lower() in mine} for s, c in skills.most_common(25)],
            "skills_to_learn": [s for s, _ in skills.most_common(40) if s.lower() not in mine][:8],
            "salary": {"jobs_with_pay": len(sal), "p25": q(.25), "median": q(.5), "p75": q(.75), "p90": q(.9)},
            "workplace": Counter((j.get("workplace_type") or "Unknown").replace(" (inferred)", "") for j in top).most_common(),
            "seniority": Counter(j.get("seniority") or "Unknown" for j in top).most_common(),
            "pipeline": self.pipeline()["counts"],
            "last_run": runs[0] if runs else None,
        }

    def changes(self, since=None, limit=200):
        if not since:
            runs = self.store.recent_runs(2)
            since = runs[-1]["started"] if runs else "1970"
        tracking = self.store.tracking()
        ch = self.store.changes_since(since)
        for c in ch:
            c["track"] = (tracking.get(c["job_id"]) or {}).get("status") or ""
        ch.sort(key=lambda c: (c["track"] == "", c["at"]), reverse=False)
        return {"since": since, "total": len(ch), "changes": ch[:limit]}

    def new_since_last_run(self, limit=20):
        runs = self.store.recent_runs(1)
        if not runs:
            return {"jobs": []}
        started = runs[0]["started"]
        res = self.search(sort="match", limit=5000, _max_limit=5000, min_match=50)
        fresh = [j for j in res["jobs"] if (j.get("first_seen") or "") >= started]
        return {"run_started": started, "total": len(fresh), "jobs": fresh[:limit]}

    def meta(self):
        runs = self.store.recent_runs(1)
        return {"last_run": runs[0] if runs else None, "collecting": self.collect_state["running"],
                "app_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "jobs": len(self.rows()), "statuses": TRACK_STATUSES,
                "functions": sorted({r.get("job_function") for r in self.rows() if r.get("job_function")}),
                "subfunctions": sorted({r.get("job_subfunction") for r in self.rows() if r.get("job_subfunction")}),
                "subfunction_map": _subfunction_map(),
                "seniorities": sorted({r.get("seniority") for r in self.rows() if r.get("seniority")},
                                      key=lambda s: P.SENIORITY_RANK.get(s, 99))}

    # -- collection ----------------------------------------------------------
    def start_collect(self, sources=None, max_pages=None):
        """Start a background collection run. Returns immediately; poll collect_status()."""
        if not self._collect_lock.acquire(blocking=False):
            return {"started": False, "reason": "a collection is already running", **self.collect_status()}
        argv = ["collect", "--db", self.db_path, "--companies", self.companies_path]
        if sources:
            argv += ["--sources", ",".join(sources) if isinstance(sources, (list, tuple)) else str(sources)]
        if max_pages:
            argv += ["--max-pages", str(int(max_pages))]
        a = cli.prepare(cli.build_parser().parse_args(argv))
        st = self.collect_state
        st.update(running=True, started=now_iso(), finished=None, log=[], result=None)

        def listener(line):
            st["log"].append(line)
            del st["log"][:-400]

        def work():
            LISTENERS.append(listener)
            http.STOP.clear()
            t0 = time.time()
            try:
                store = Store(self.db_path)
                results = cli.collect(a, store)
                st["result"] = {"ok": True, "seconds": round(time.time() - t0),
                                "sources": {r["key"]: r["status"] for r in results}}
            except BaseException as e:  # noqa: BLE001 — report, never crash the server
                st["result"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
                st["log"].append("Collection failed: %s" % e)
            finally:
                LISTENERS.remove(listener)
                st.update(running=False, finished=now_iso())
                self.store.meta_set("last_collect", st["result"])
                self.invalidate()
                self._collect_lock.release()

        threading.Thread(target=work, name="collector", daemon=True).start()
        return {"started": True, **self.collect_status()}

    def collect_status(self, tail=60):
        st = self.collect_state
        return {"running": st["running"], "started": st["started"], "finished": st["finished"],
                "result": st["result"], "log": st["log"][-tail:]}

    def stop_collect(self):
        if self.collect_state["running"]:
            http.STOP.set()
            return {"stopping": True}
        return {"stopping": False}

    def due_for_refresh(self):
        hrs = self.get_profile().get("auto_refresh_hours") or 0
        if not hrs or self.collect_state["running"]:
            return False
        runs = self.store.recent_runs(1)
        if not runs:
            return True
        last = datetime.fromisoformat(runs[0]["started"])
        return (datetime.now(timezone.utc) - last).total_seconds() > hrs * 3600
