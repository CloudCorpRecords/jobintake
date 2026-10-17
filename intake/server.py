"""Local web app + REST API.

    python3 jobintake.py serve            → http://localhost:8780  (UI)   ·   /docs  (API reference)

Security: binds to 127.0.0.1 by default, rejects requests whose Host header isn't local (DNS-rebinding),
and requires JSON bodies for writes (so other websites can't post to it). Use --token (or
JOBINTAKE_TOKEN) to require `Authorization: Bearer <token>` on /api — do that before using --host 0.0.0.0.
"""

import csv
import io
import json
import mimetypes
import os
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .api import SORTS, Service
from .store import TRACK_STATUSES
from .util import log

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

FILTER_PARAMS = {
    "q": "Search query. Words, \"phrases\", OR, -exclude, and filters like title:, company:, loc:, skill:, is:remote, "
         "level:senior, pay:150k+, posted:7d, has:email, has:apply-email, domain:acme.com, status:saved",
    "function": "Comma list of job functions, e.g. Engineering,Data",
    "subfunction": "Comma list, e.g. Backend,Machine Learning / AI",
    "seniority": "Comma list, e.g. Senior,Staff",
    "workplace": "Comma list: Remote,Hybrid,On-site",
    "country": "Substring of country/location",
    "company": "Substring of company name",
    "source": "Only jobs listed on this source (greenhouse, ashby, …)",
    "min_salary": "Minimum annual USD (compares the top of the range)",
    "has_salary": "true = only jobs with pay data",
    "max_age": "Only jobs posted within N days",
    "min_match": "Minimum match score 0-100",
    "track": "saved|applied|interviewing|offer|rejected|hidden|tracked|untracked",
    "include_hidden": "true = include jobs you hid",
    "include_excluded": "true = include jobs your profile rules out",
    "sort": "match (default; blends relevance with your profile match) | relevance | newest | salary | company",
    "has": "Comma list: email, apply-email, salary, form, link, website, linkedin, contact, visa, notes, equity",
    "domain": "Company domain or email domain, e.g. stripe.com",
    "limit": "Page size (default 50, max 500)",
    "offset": "Pagination offset",
}


def openapi(base):
    q = [{"name": k, "in": "query", "required": False, "description": v, "schema": {"type": "string"}}
         for k, v in FILTER_PARAMS.items()]
    jid = [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}]
    ok = {"200": {"description": "OK", "content": {"application/json": {}}}}
    body = lambda props, req=None: {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "properties": props, **({"required": req} if req else {})}}}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "jobintake API", "version": __version__,
                 "description": "Search, rank and track job postings collected by jobintake. Every job has a "
                                "`match` score (0-100) against the user's profile, with human-readable `reasons`."},
        "servers": [{"url": base}],
        "paths": {
            "/api/jobs": {"get": {"operationId": "searchJobs", "summary": "Search and rank jobs", "parameters": q, "responses": ok}},
            "/api/jobs/{job_id}": {"get": {"operationId": "getJob", "summary": "Full job details incl. description, match reasons, tracking and change history",
                                           "parameters": jid, "responses": ok}},
            "/api/jobs/{job_id}/track": {"post": {
                "operationId": "trackJob", "summary": "Set your status and/or notes for a job", "parameters": jid,
                "requestBody": body({"status": {"type": "string", "enum": TRACK_STATUSES + [""]},
                                     "notes": {"type": "string"}}), "responses": ok}},
            "/api/jobs/{job_id}/fetch": {"post": {
                "operationId": "fetchPosting", "summary": "Fetch the job's own posting page (robots.txt permitting) to add the full "
                "description, emails, links, structured pay and dates", "parameters": jid, "requestBody": body({}), "responses": ok}},
            "/api/fetch-postings": {"post": {
                "operationId": "fetchPostings", "summary": "Background: fetch posting pages for your best matches",
                "requestBody": body({"min_match": {"type": "integer"}, "limit": {"type": "integer"}, "refetch": {"type": "boolean"}}),
                "responses": ok}},
            "/api/pipeline": {"get": {"operationId": "getPipeline", "summary": "Your tracked jobs grouped by status", "responses": ok}},
            "/api/new": {"get": {"operationId": "getNewMatches", "summary": "Good matches first seen in the latest collection run", "responses": ok}},
            "/api/profile": {
                "get": {"operationId": "getProfile", "summary": "Your search profile (drives match scores)", "responses": ok},
                "patch": {"operationId": "updateProfile", "summary": "Change profile fields (partial update)",
                          "requestBody": body({"functions": {"type": "array", "items": {"type": "string"}},
                                               "skills": {"type": "array", "items": {"type": "string"}},
                                               "seniority": {"type": "array", "items": {"type": "string"}},
                                               "workplace": {"type": "string", "enum": ["remote_only", "remote_or_near", "anywhere"]},
                                               "home_city": {"type": "string"}, "home_state": {"type": "string"},
                                               "home_country": {"type": "string"}, "min_salary": {"type": "number"},
                                               "include_keywords": {"type": "array", "items": {"type": "string"}},
                                               "exclude_keywords": {"type": "array", "items": {"type": "string"}},
                                               "exclude_companies": {"type": "array", "items": {"type": "string"}},
                                               "favorite_companies": {"type": "array", "items": {"type": "string"}},
                                               "needs_visa": {"type": "boolean"}, "auto_refresh_hours": {"type": "number"}}),
                          "responses": ok}},
            "/api/stats": {"get": {"operationId": "getInsights", "summary": "Insights about your matches: pay, top skills, skills to learn",
                                   "parameters": [{"name": "min_match", "in": "query", "schema": {"type": "integer"}}], "responses": ok}},
            "/api/changes": {"get": {"operationId": "getChanges", "summary": "What changed since the previous run (closed jobs, pay edits…)",
                                     "parameters": [{"name": "since", "in": "query", "schema": {"type": "string"}}], "responses": ok}},
            "/api/collect": {
                "get": {"operationId": "collectStatus", "summary": "Status and log of the current/last collection run", "responses": ok},
                "post": {"operationId": "startCollect", "summary": "Fetch fresh jobs from all sources (runs in background, ~4 min)",
                         "requestBody": body({"sources": {"type": "array", "items": {"type": "string"}},
                                              "max_pages": {"type": "integer"}}), "responses": ok}},
            "/api/collect/stop": {"post": {"operationId": "stopCollect", "summary": "Stop a running collection (keeps what was fetched)",
                                           "requestBody": body({}), "responses": ok}},
            "/api/export.csv": {"get": {"operationId": "exportCsv", "summary": "Download the current search as CSV", "parameters": q,
                                        "responses": {"200": {"description": "CSV", "content": {"text/csv": {}}}}}},
            "/api/meta": {"get": {"operationId": "getMeta", "summary": "Available functions, seniorities, statuses, last run", "responses": ok}},
        },
    }


def _bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def search_kwargs(qs):
    g = lambda k: (qs.get(k) or [None])[0]
    kw = {k: g(k) for k in ("q", "function", "subfunction", "seniority", "workplace", "country", "company", "source", "track",
                            "has", "domain")}
    for k in ("min_salary", "max_age", "min_match", "limit", "offset"):
        if g(k) not in (None, ""):
            kw[k] = float(g(k)) if k == "min_salary" else int(float(g(k)))
    for k in ("has_salary", "include_hidden", "include_excluded"):
        kw[k] = _bool(g(k))
    s = g("sort")
    if s:
        if s not in SORTS:
            raise ValueError("sort must be one of %s" % ", ".join(sorted(SORTS)))
        kw["sort"] = s
    return {k: v for k, v in kw.items() if v not in (None, "")}


class Handler(BaseHTTPRequestHandler):
    server_version = "jobintake/" + __version__
    svc = None
    token = None
    quiet = True

    # -- plumbing --------------------------------------------------------------
    def log_message(self, fmt, *args):
        if not self.quiet:
            log("  web: " + fmt % args)

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        bind = self.server.server_address[0]
        return host in LOCAL_HOSTS or bind not in ("127.0.0.1", "::1", "localhost")

    def _auth_ok(self):
        if not self.token:
            return True
        h = self.headers.get("Authorization") or ""
        return h == "Bearer " + self.token or self.headers.get("X-Api-Key") == self.token

    def _json_body(self):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise PermissionError("write requests must send Content-Type: application/json")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def _static(self, rel):
        path = os.path.normpath(os.path.join(WEB, rel))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._err(404, "not found")
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype, {"Cache-Control": "no-cache"})

    # -- routing ---------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_PUT(self):
        self._route("PATCH")

    def _route(self, method):
        try:
            if not self._host_ok():
                return self._err(403, "bad Host header")
            u = urllib.parse.urlsplit(self.path)
            path, qs = u.path.rstrip("/") or "/", urllib.parse.parse_qs(u.query)
            if not path.startswith("/api"):
                if method != "GET":
                    return self._err(405, "method not allowed")
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path == "/docs":
                    return self._static("docs.html")
                return self._static(path.lstrip("/"))
            if not self._auth_ok():
                return self._err(401, "missing or wrong API token (send Authorization: Bearer <token>)")
            return self._api(method, path, qs)
        except PermissionError as e:
            self._err(415, str(e))
        except (ValueError, TypeError) as e:
            self._err(400, str(e))
        except KeyError as e:
            self._err(404, "not found: %s" % e)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._err(500, "%s: %s" % (type(e).__name__, e))

    def _api(self, method, path, qs):
        svc = self.svc
        g = lambda k, d=None: (qs.get(k) or [d])[0]
        if path == "/api" and method == "GET":
            return self._send(200, {"name": "jobintake", "version": __version__, "docs": "/docs",
                                    "openapi": "/api/openapi.json", "endpoints": sorted(openapi("")["paths"])})
        if path == "/api/openapi.json":
            host = self.headers.get("Host") or "localhost:8780"
            return self._send(200, openapi("http://" + host))
        if path == "/api/jobs" and method == "GET":
            return self._send(200, svc.search(**search_kwargs(qs)))
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{6,40})(/track|/fetch)?", path)
        if m:
            jid, sub = m.group(1), m.group(2)
            if sub == "/fetch" and method == "POST":
                self._json_body()
                return self._send(200, svc.fetch_posting(jid))
            if not sub and method == "GET":
                j = svc.job(jid)
                return self._send(200, j) if j else self._err(404, "no job %s" % jid)
            if sub == "/track" and method == "POST":
                b = self._json_body()
                return self._send(200, svc.track(jid, b.get("status"), b.get("notes")))
        if path == "/api/pipeline" and method == "GET":
            return self._send(200, svc.pipeline())
        if path == "/api/new" and method == "GET":
            return self._send(200, svc.new_since_last_run(int(g("limit", 20))))
        if path == "/api/profile":
            if method == "GET":
                return self._send(200, svc.get_profile())
            if method == "PATCH":
                return self._send(200, svc.update_profile(self._json_body()))
        if path == "/api/stats" and method == "GET":
            return self._send(200, svc.stats(int(g("min_match", 60))))
        if path == "/api/changes" and method == "GET":
            return self._send(200, svc.changes(g("since")))
        if path == "/api/meta" and method == "GET":
            return self._send(200, svc.meta())
        if path == "/api/collect":
            if method == "GET":
                return self._send(200, svc.collect_status())
            if method == "POST":
                b = self._json_body()
                return self._send(202, svc.start_collect(b.get("sources"), b.get("max_pages")))
        if path == "/api/fetch-postings" and method == "POST":
            b = self._json_body()
            return self._send(202, svc.fetch_postings(int(b.get("min_match", 70)), int(b.get("limit", 50)), bool(b.get("refetch"))))
        if path == "/api/collect/stop" and method == "POST":
            self._json_body()
            return self._send(200, svc.stop_collect())
        if path == "/api/export.csv" and method == "GET":
            kw = search_kwargs(qs)
            kw.update(limit=100000, offset=0)
            res = svc.search(_max_limit=100000, **kw)
            buf = io.StringIO()
            cols = ["match", "track", "title", "company", "job_function", "job_subfunction", "seniority", "workplace_type",
                    "city", "country", "salary_min_usd_annual", "salary_max_usd_annual", "posted_at", "url", "apply_url",
                    "application_method", "apply_emails", "contact_emails", "company_website"]
            w = csv.writer(buf)
            w.writerow(cols + ["why"])
            for j in res["jobs"]:
                row = [j.get(c) if j.get(c) is not None else "" for c in cols]
                row = ["'" + v if isinstance(v, str) and v[:1] in "=+-@" else v for v in row]
                w.writerow(row + ["; ".join(r["text"] for r in j["reasons"])])
            return self._send(200, "﻿" + buf.getvalue(), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="jobintake-%s.csv"' % time.strftime("%Y%m%d")})
        return self._err(404 if method == "GET" else 405, "no route for %s %s — see /docs" % (method, path))


def scheduler(svc, stop):
    """Re-collect automatically every profile.auto_refresh_hours."""
    while not stop.wait(600):
        try:
            if svc.due_for_refresh():
                log("Auto-refresh: starting a collection run")
                svc.start_collect()
        except Exception as e:  # noqa: BLE001
            log("Auto-refresh error: %s" % e)


def serve(db, companies, host="127.0.0.1", port=8780, token=None, open_browser=False, verbose=False):
    svc = Service(db, companies)
    svc.rows()  # warm the cache before the first request
    Handler.svc, Handler.token, Handler.quiet = svc, token, not verbose
    try:
        ThreadingHTTPServer.request_queue_size = 128  # default 5 drops bursts of parallel requests from agents
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        raise SystemExit("Can't listen on %s:%d (%s). Is jobintake already running? Try --port %d." % (host, port, e.strerror, port + 1))
    httpd.daemon_threads = True
    url = "http://%s:%d" % ("localhost" if host in ("127.0.0.1", "0.0.0.0") else host, port)
    log("jobintake %s is running → %s   (API docs: %s/docs)" % (__version__, url, url))
    log("%d jobs loaded%s. Press Ctrl-C to stop." % (len(svc.rows()), " · API token required" if token else ""))
    stop = threading.Event()
    threading.Thread(target=scheduler, args=(svc, stop), daemon=True, name="scheduler").start()
    if svc.due_for_refresh():
        log("Data is older than your auto-refresh interval — collecting fresh jobs in the background.")
        svc.start_collect()
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Stopping…")
    finally:
        stop.set()
        svc.stop_collect()
        httpd.server_close()
