"""MCP (Model Context Protocol) server over stdio — lets Claude and other AI clients use jobintake as tools.

    python3 jobintake.py mcp

Register with Claude Code:
    claude mcp add jobintake -- python3 /path/to/jobintake.py mcp

Implements the stdio transport with no dependencies: newline-delimited JSON-RPC 2.0 on stdin/stdout.
All logging goes to stderr so stdout stays a clean protocol stream.
"""

import json
import sys
import time

from . import __version__
from .api import Service
from .store import TRACK_STATUSES
from .util import LISTENERS

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

S = lambda **props: {"type": "object", "properties": props, "additionalProperties": False}
STR = {"type": "string"}
INT = {"type": "integer"}
ARR = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {"name": "search_jobs",
     "description": "Search and rank job postings against the user's profile. Each result has a `match` score "
                    "(0-100) and `reasons`. Default sort is best match first. Use get_job for the full description.",
     "inputSchema": S(q={**STR, "description": "Search query: words, \"phrases\", OR, -exclude, plus filters title:, company:, "
                                              "loc:, skill:, is:remote, level:senior, pay:150k+, posted:7d, has:email, "
                                              "has:apply-email, domain:x.com"},
                      has={**STR, "description": "Comma list: email, apply-email, salary, form, link, website, contact, visa"},
                      function={**STR, "description": "Comma list, e.g. 'Engineering,Data'"},
                      subfunction={**STR, "description": "Comma list, e.g. 'Backend,Machine Learning / AI'"},
                      seniority={**STR, "description": "Comma list, e.g. 'Senior,Staff'"},
                      workplace={**STR, "description": "Comma list of Remote, Hybrid, On-site"},
                      country=STR, company=STR,
                      min_salary={"type": "number", "description": "Annual USD, compared to top of range"},
                      has_salary={"type": "boolean"}, max_age={**INT, "description": "Posted within N days"},
                      min_match={**INT, "description": "0-100"},
                      track={**STR, "description": "saved|applied|interviewing|offer|rejected|hidden|tracked|untracked"},
                      sort={"type": "string", "enum": ["match", "newest", "salary", "company"]},
                      limit={**INT, "description": "Default 20, max 100"}, offset=INT)},
    {"name": "get_job",
     "description": "Full details for one job: description, pay, requirements, skills, match reasons, the user's "
                    "status/notes, and change history.",
     "inputSchema": {**S(job_id=STR), "required": ["job_id"]}},
    {"name": "update_job_status",
     "description": "Record the user's progress on a job (saved, applied, interviewing, offer, rejected, hidden; "
                    "empty string clears it) and/or replace the notes.",
     "inputSchema": {**S(job_id=STR, status={"type": "string", "enum": TRACK_STATUSES + [""]}, notes=STR),
                     "required": ["job_id"]}},
    {"name": "fetch_posting",
     "description": "Fetch one job's own posting page (only where the site's robots.txt allows) to add the full description, "
                    "contact emails, links, structured pay and dates. Returns the refreshed job.",
     "inputSchema": {**S(job_id=STR), "required": ["job_id"]}},
    {"name": "get_pipeline", "description": "The user's tracked jobs grouped by status.", "inputSchema": S()},
    {"name": "get_new_matches", "description": "Good matches (score ≥ 50) first seen in the most recent collection run.",
     "inputSchema": S(limit=INT)},
    {"name": "get_profile", "description": "The user's search profile, which drives match scores.", "inputSchema": S()},
    {"name": "update_profile",
     "description": "Partially update the profile. Fields: functions, subfunctions, skills, seniority (lists); "
                    "workplace (remote_only|remote_or_near|anywhere); home_city, home_state, home_country; "
                    "min_salary (annual USD); include_keywords, exclude_keywords, exclude_companies, "
                    "favorite_companies (lists); needs_visa (bool); auto_refresh_hours.",
     "inputSchema": {"type": "object", "additionalProperties": True}},
    {"name": "get_insights",
     "description": "Market insights for the user's matches: pay percentiles, top skills (and which the user has), "
                    "skills to learn, top companies, pipeline counts.",
     "inputSchema": S(min_match=INT)},
    {"name": "get_changes", "description": "What changed since the previous collection run (closed jobs, pay/title edits). "
                                           "Changes to jobs the user tracks are listed first.",
     "inputSchema": S(since={**STR, "description": "ISO date; default = previous run"})},
    {"name": "refresh_jobs",
     "description": "Start collecting fresh jobs from every source in the background (~4 minutes). "
                    "Poll refresh_status until running=false.",
     "inputSchema": S(sources={**ARR, "description": "Optional subset of source names"})},
    {"name": "refresh_status", "description": "Progress/result of the current or last collection run.", "inputSchema": S()},
]


def _slim(job):
    keep = ["job_id", "match", "relevance", "track", "title", "company", "job_function", "job_subfunction", "seniority",
            "workplace_type", "remote_scope", "city", "country", "salary_min_usd_annual", "salary_max_usd_annual",
            "posted_at", "age_days", "url", "apply_url", "application_method", "apply_emails", "contact_emails",
            "company_website", "matched_skills"]
    d = {k: job.get(k) for k in keep if job.get(k) not in (None, "", [])}
    if job.get("snippet"):
        d["snippet"] = job["snippet"].replace("\x02", "**").replace("\x03", "**")
    d["why"] = "; ".join(("✓ " if r["ok"] else "✗ " if r["ok"] is False else "· ") + r["text"] for r in job["reasons"])
    return d


def call(svc, name, a):
    a = a or {}
    if name == "search_jobs":
        kw = {k: v for k, v in a.items() if v not in (None, "")}
        kw["limit"] = min(int(kw.get("limit", 20)), 100)
        res = svc.search(**kw)
        return {"total": res["total"], "offset": res["offset"], "jobs": [_slim(j) for j in res["jobs"]]}
    if name == "get_job":
        j = svc.job(a["job_id"])
        if not j:
            raise KeyError("no job with id %s" % a["job_id"])
        drop = {"company_logo", "content_hash", "source_key", "last_run", "_sc", "_hay", "_txt"}
        return {k: v for k, v in j.items() if k not in drop and v not in (None, "", [], False)}
    if name == "update_job_status":
        return svc.track(a["job_id"], a.get("status"), a.get("notes"))
    if name == "fetch_posting":
        j = svc.fetch_posting(a["job_id"])
        drop = {"company_logo", "content_hash", "source_key", "last_run", "description", "all_links"}
        return {k: v for k, v in j.items() if k not in drop and v not in (None, "", [], False)}
    if name == "get_pipeline":
        p = svc.pipeline()
        return {"counts": p["counts"], "columns": {k: [_slim(j) for j in v] for k, v in p["columns"].items()}}
    if name == "get_new_matches":
        r = svc.new_since_last_run(int(a.get("limit", 20)))
        return {**r, "jobs": [_slim(j) for j in r.get("jobs", [])]}
    if name == "get_profile":
        return svc.get_profile()
    if name == "update_profile":
        return svc.update_profile(a)
    if name == "get_insights":
        return svc.stats(int(a.get("min_match", 60)))
    if name == "get_changes":
        return svc.changes(a.get("since"), limit=100)
    if name == "refresh_jobs":
        return svc.start_collect(a.get("sources"))
    if name == "refresh_status":
        return svc.collect_status(tail=25)
    raise KeyError("unknown tool %s" % name)


def run(db, companies):
    svc = Service(db, companies)
    LISTENERS.clear()  # never let collection logs reach stdout
    out = sys.stdout

    def send(msg):
        out.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        out.flush()

    sys.stdout = sys.stderr  # anything that prints by accident goes to stderr, not the protocol stream
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # notification (e.g. notifications/initialized) — no reply
            continue
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                result = {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "jobintake", "version": __version__},
                          "instructions": "Job search assistant data. Start with get_profile and search_jobs; "
                                          "results are ranked by `match` (0-100) with reasons. Use get_job for "
                                          "details and update_job_status to track progress."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                t0 = time.time()
                try:
                    data = call(svc, params.get("name"), params.get("arguments"))
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str, indent=1)}],
                              "isError": False}
                except (KeyError, ValueError, TypeError) as e:
                    result = {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True}
                sys.stderr.write("mcp: %s %.2fs\n" % (params.get("name"), time.time() - t0))
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        except Exception as e:  # noqa: BLE001
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})
