"""Fetch a job's own posting page and pull everything useful from it: schema.org JobPosting data (full
description, pay, dates, hiring organisation, website), plus emails and links.

Polite by design: honours robots.txt for every host, identifies itself, rate-limits per host (via http.py),
and only fetches the posting/apply URL already on record — no crawling.
"""

import json
import re
import time
import urllib.parse
import urllib.robotparser

from . import enrich as E
from . import http
from .extract import extract, find_emails
from .util import html_to_text, now_iso, parse_date, raw_job

ROBOTS_AGENT = "jobintake"
_robots = {}
LD_RX = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
META_RX = re.compile(r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I)
SKIP_HOSTS = ("news.ycombinator.com",)  # comment pages; the posting text is already complete


def allowed(url):
    p = urllib.parse.urlsplit(url)
    root = "%s://%s" % (p.scheme, p.netloc)
    rp = _robots.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            text = http.fetch(root + "/robots.txt", retries=1, timeout=10)
            rp.parse(text.splitlines())
        except http.PermanentError:
            rp.parse([])  # no robots.txt (404) → allowed
        except Exception:  # noqa: BLE001 — unreachable robots.txt: be conservative
            rp.parse(["User-agent: *", "Disallow: /"])
        _robots[root] = rp
    return rp.can_fetch(ROBOTS_AGENT, url)


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "mainEntity", "itemListElement"):
            if k in node:
                yield from _walk(node[k])


def job_posting_ld(page):
    for block in LD_RX.findall(page):
        try:
            data = json.loads(block.strip().rstrip(";"))
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return node
    return None


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _salary(ld):
    bs = _first(ld.get("baseSalary")) or {}
    if not isinstance(bs, dict):
        return {}
    val = bs.get("value") or {}
    if not isinstance(val, dict):
        val = {"value": val}
    lo = val.get("minValue") or val.get("value")
    hi = val.get("maxValue") or val.get("value")
    unit = (val.get("unitText") or bs.get("unitText") or "YEAR").lower()
    return {"salary_min": lo, "salary_max": hi, "salary_currency": bs.get("currency") or val.get("currency") or "",
            "salary_period": {"hour": "hour", "day": "day", "week": "week", "month": "month"}.get(unit, "year")}


def _location(ld):
    locs = []
    for jl in ld.get("jobLocation") or []:
        a = (jl or {}).get("address") or {} if isinstance(jl, dict) else {}
        if isinstance(a, dict):
            locs.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"), _country(a.get("addressCountry"))] if x))
    if isinstance(ld.get("jobLocation"), dict):
        a = ld["jobLocation"].get("address") or {}
        if isinstance(a, dict):
            locs.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"), _country(a.get("addressCountry"))] if x))
    return "; ".join(l for l in locs if l)


def _country(c):
    return c.get("name") if isinstance(c, dict) else c


def fetch(store, job):
    """Fetch + parse one posting page. Returns (status, details dict). Writes results to the database."""
    urls = [u for u in (job.get("url"), job.get("apply_url")) if u and u.startswith("http")]
    urls = list(dict.fromkeys(urls))
    if not urls:
        return _save(store, job, "no_url", "", {})
    url = urls[0]
    if any(h in url for h in SKIP_HOSTS):
        return _save(store, job, "skipped", url, {"reason": "the source already provided the full text"})
    try:
        if not allowed(url):
            return _save(store, job, "blocked_by_robots", url, {"reason": "this site's robots.txt asks bots not to fetch job pages"})
        page = http.fetch(url, retries=1, timeout=25)
    except http.PermanentError as e:
        return _save(store, job, "gone" if e.code in (404, 410) else "http_%s" % e.code, url, {})
    except Exception as e:  # noqa: BLE001
        return _save(store, job, "error", url, {"reason": str(e)[:160]})

    ld = job_posting_ld(page) or {}
    meta = {k.lower(): v for k, v in META_RX.findall(page)}
    org = _first(ld.get("hiringOrganization")) or {}
    org = org if isinstance(org, dict) else {"name": org}
    desc_html = ld.get("description") or ""
    page_emails = find_emails(html_to_text(page))
    details = {
        "jsonld": bool(ld),
        "title": ld.get("title") or meta.get("og:title", ""),
        "date_posted": parse_date(ld.get("datePosted")),
        "valid_through": parse_date(ld.get("validThrough")),
        "employment_type": ", ".join(ld["employmentType"]) if isinstance(ld.get("employmentType"), list) else ld.get("employmentType") or "",
        "remote": ld.get("jobLocationType") == "TELECOMMUTE",
        "location": _location(ld),
        "org_name": org.get("name") or "",
        "org_website": org.get("sameAs") or org.get("url") or "",
        "org_logo": (org.get("logo") or {}).get("url") if isinstance(org.get("logo"), dict) else org.get("logo") or "",
        "direct_apply": ld.get("directApply"),
        "page_emails": [(e, p) for e, p, _ in page_emails][:20],
        "description_chars": len(html_to_text(desc_html)),
        **_salary(ld),
    }
    _merge(store, job, desc_html, details, url)
    return _save(store, job, "ok", url, details)


def _merge(store, job, desc_html, d, url):
    """Re-classify the job with whatever the page added (longer description, structured pay, dates)."""
    stored_text = job.get("description") or ""
    better_text = html_to_text(desc_html) if desc_html else ""
    use_html = desc_html if len(better_text) > len(stored_text) * 1.1 else ""
    has_pay = bool(job.get("salary_min_usd_annual"))
    r = raw_job(
        source=job.get("source") or "", source_key=job.get("source_key") or job.get("source") or "",
        source_job_id=job.get("source_job_id") or "", title=job.get("title") or "", company=job.get("company") or "",
        company_logo=job.get("company_logo") or d.get("org_logo") or "", url=job.get("url") or url,
        apply_url=job.get("apply_url") or "", posted_at=job.get("posted_at") or d.get("date_posted") or "",
        expires_at=job.get("expires_at") or d.get("valid_through") or "", location_raw=job.get("location_raw") or d.get("location") or "",
        description_html=use_html or "", description="" if use_html else stored_text,
        source_categories=[c for c in (job.get("source_categories") or "").split("; ") if c],
        tags=[t for t in (job.get("tags") or "").split("; ") if t], department=job.get("department") or "",
        team=job.get("team") or "", employment_type_raw=job.get("employment_type") or d.get("employment_type") or "",
        workplace_raw="remote" if d.get("remote") else (job.get("workplace_type") or "").replace(" (inferred)", ""),
        salary_min=job.get("salary_min") if has_pay else d.get("salary_min"),
        salary_max=job.get("salary_max") if has_pay else d.get("salary_max"),
        salary_currency=job.get("salary_currency") if has_pay else d.get("salary_currency", ""),
        salary_period=job.get("salary_period") if has_pay else d.get("salary_period", ""),
        company_website=d.get("org_website") or "",
    )
    new = E.enrich(r)
    if new["job_id"] != job["job_id"]:
        new["job_id"] = job["job_id"]  # never re-key an existing job
    # page-wide emails (e.g. in a sidebar) that the description didn't include
    extra = [e for e, p in d.get("page_emails", []) if e not in (new.get("contact_emails") or "")]
    if extra:
        new["contact_emails"] = "; ".join([x for x in (new.get("contact_emails") or "").split("; ") if x] + extra)
        new["email_count"] = len(new["contact_emails"].split("; "))
        purposes = dict(d.get("page_emails", []))
        new["email_purposes"] = "; ".join("%s=%s" % (e, purposes.get(e, "contact")) for e in new["contact_emails"].split("; "))
        applys = [e for e in extra if purposes.get(e) == "apply"]
        if applys:
            new["apply_emails"] = "; ".join([x for x in (new.get("apply_emails") or "").split("; ") if x] + applys)
        if not new.get("company_website"):
            from .extract import FREE_MAIL, root_domain
            dom = next((root_domain(e.split("@")[1]) for e in extra if root_domain(e.split("@")[1]) not in FREE_MAIL), "")
            if dom:
                new["company_website"], new["company_domain"] = "https://" + dom, dom
    keep = {"status", "closed_at", "first_seen", "last_seen", "times_seen", "also_listed_on", "source_count", "last_run"}
    changes = {k: v for k, v in new.items() if k not in keep}
    store.update_fields(job["job_id"], changes)


def _save(store, job, status, url, details):
    store.con.execute("CREATE TABLE IF NOT EXISTS pages (job_id TEXT PRIMARY KEY, url TEXT, fetched_at TEXT, status TEXT, data TEXT)")
    store.con.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?)", (job["job_id"], url, now_iso(), status, json.dumps(details)))
    store.con.commit()
    return status, details


def last_fetch(store, job_id):
    try:
        r = store.con.execute("SELECT url, fetched_at, status, data FROM pages WHERE job_id=?", (job_id,)).fetchone()
    except Exception:  # noqa: BLE001 — table not created yet
        return None
    return {"url": r[0], "fetched_at": r[1], "status": r[2], "details": json.loads(r[3] or "{}")} if r else None


def fetch_many(store, jobs, max_seconds=600):
    """Fetch pages for many jobs (sequentially per host thanks to http.py throttling). Returns status counts."""
    from collections import Counter
    t0, counts = time.time(), Counter()
    for j in jobs:
        if time.time() - t0 > max_seconds or http.STOP.is_set():
            counts["not_attempted (time limit)"] += 1
            continue
        status, _ = fetch(store, j)
        counts[status] += 1
    return counts
