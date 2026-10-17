"""Source adapters. Each one yields raw_job() dicts.

Feeds take (ctx). Company boards take (ctx, token). A source sets ctx.complete = True only when it
has returned its *entire* listing (no page cap hit) — only then can missing jobs be marked closed.
"""

import html
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import http
from .util import as_list, html_to_text, parse_date, raw_job


STOP = http.STOP  # set on Ctrl-C so every source winds down and keeps what it has


class Ctx:
    def __init__(self, opts, key):
        self.opts = opts
        self.key = key
        self.complete = False
        self.started = time.monotonic()
        self.budget = getattr(opts, "source_timeout", 300)
        self.notes = []

    def time_left(self):
        return self.budget - (time.monotonic() - self.started)

    def out_of_time(self):
        if STOP.is_set():
            self.notes.append("interrupted")
            return True
        if self.time_left() <= 0:
            self.notes.append("time budget hit")
            return True
        return False

    @property
    def query_terms(self):
        return getattr(self.opts, "query_terms", []) or []

    @property
    def max_pages(self):
        return getattr(self.opts, "max_pages", 5)


# ---------------------------------------------------------------------------
# Market-wide feeds
# ---------------------------------------------------------------------------

def remotive(ctx):
    d = http.get_json("https://remotive.com/api/remote-jobs")
    for j in d.get("jobs", []):
        yield raw_job(
            source="remotive", source_job_id=str(j.get("id", "")), title=j.get("title", ""),
            company=j.get("company_name", ""), company_logo=j.get("company_logo") or "",
            url=j.get("url", ""), apply_url=j.get("url", ""), posted_at=parse_date(j.get("publication_date")),
            location_raw=j.get("candidate_required_location", ""), description_html=j.get("description", ""),
            source_categories=as_list(j.get("category")), tags=as_list(j.get("tags")),
            employment_type_raw=j.get("job_type", ""), remote_flag=True, workplace_raw="remote",
            salary_text=j.get("salary") or "",
        )
    ctx.complete = True


def remoteok(ctx):
    d = http.get_json("https://remoteok.com/api")
    for j in d:
        if not isinstance(j, dict) or "id" not in j or "position" not in j:
            continue  # first element is a legal notice
        has_sal = bool(j.get("salary_min"))
        yield raw_job(
            source="remoteok", source_job_id=str(j.get("id")), title=j.get("position", ""),
            company=j.get("company", ""), company_logo=j.get("company_logo") or j.get("logo") or "",
            url=j.get("url", ""), apply_url=j.get("apply_url") or j.get("url", ""),
            posted_at=parse_date(j.get("epoch") or j.get("date")), location_raw=j.get("location", ""),
            description_html=j.get("description", ""), tags=as_list(j.get("tags")),
            remote_flag=True, workplace_raw="remote",
            salary_min=j.get("salary_min") or None, salary_max=j.get("salary_max") or None,
            salary_currency="USD" if has_sal else "", salary_period="year" if has_sal else "",
        )
    ctx.complete = True


def arbeitnow(ctx):
    for page in range(1, ctx.max_pages + 1):
        d = http.get_json("https://www.arbeitnow.com/api/job-board-api?page=%d" % page)
        rows = d.get("data", [])
        for j in rows:
            yield raw_job(
                source="arbeitnow", source_job_id=j.get("slug", ""), title=j.get("title", ""),
                company=j.get("company_name", ""), url=j.get("url", ""), apply_url=j.get("url", ""),
                posted_at=parse_date(j.get("created_at")), location_raw=j.get("location", ""),
                description_html=j.get("description", ""), tags=as_list(j.get("tags")),
                employment_type_raw=" ".join(as_list(j.get("job_types"))),
                remote_flag=bool(j.get("remote")), workplace_raw="remote" if j.get("remote") else "",
                country_hint="DE",
            )
        if not rows or not (d.get("links") or {}).get("next"):
            ctx.complete = True
            return
        if ctx.out_of_time():
            return


def himalayas(ctx):
    per = 20
    for page in range(ctx.max_pages * 5):
        d = http.get_json("https://himalayas.app/jobs/api?limit=%d&offset=%d" % (per, page * per))
        rows = d.get("jobs", [])
        for j in rows:
            yield raw_job(
                source="himalayas", source_job_id=j.get("guid", ""), title=j.get("title", ""),
                company=j.get("companyName", ""), company_logo=j.get("companyLogo") or "",
                url=j.get("applicationLink") or j.get("guid", ""), apply_url=j.get("applicationLink", ""),
                posted_at=parse_date(j.get("pubDate")), expires_at=parse_date(j.get("expiryDate")),
                location_raw=", ".join(as_list(j.get("locationRestrictions"))) or "Worldwide",
                description_html=j.get("description", ""),
                source_categories=as_list(j.get("parentCategories")) + as_list(j.get("categories")),
                employment_type_raw=j.get("employmentType", ""), seniority_raw=" ".join(as_list(j.get("seniority"))),
                remote_flag=True, workplace_raw="remote",
                salary_min=j.get("minSalary"), salary_max=j.get("maxSalary"),
                salary_currency=j.get("currency") or "", salary_period=j.get("salaryPeriod") or "",
                timezones=",".join(str(x) for x in as_list(j.get("timezoneRestrictions"))),
            )
        if len(rows) < per:
            ctx.complete = True
            return
        if ctx.out_of_time():
            return


def jobicy(ctx):
    d = http.get_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    for j in d.get("jobs", []):
        yield raw_job(
            source="jobicy", source_job_id=str(j.get("id", "")), title=html.unescape(j.get("jobTitle", "")),
            company=j.get("companyName", ""), company_logo=j.get("companyLogo") or "",
            url=j.get("url", ""), apply_url=j.get("url", ""), posted_at=parse_date(j.get("pubDate")),
            location_raw=j.get("jobGeo", ""), description_html=j.get("jobDescription", ""),
            source_categories=[html.unescape(x) for x in as_list(j.get("jobIndustry"))],
            employment_type_raw=" ".join(as_list(j.get("jobType"))), seniority_raw=j.get("jobLevel", ""),
            remote_flag=True, workplace_raw="remote",
            salary_min=j.get("salaryMin") or j.get("annualSalaryMin"),
            salary_max=j.get("salaryMax") or j.get("annualSalaryMax"),
            salary_currency=j.get("salaryCurrency") or "", salary_period=j.get("salaryPeriod") or "year",
        )


def themuse(ctx):
    for page in range(1, ctx.max_pages + 1):
        d = http.get_json("https://www.themuse.com/api/public/jobs?page=%d&descending=true" % page)
        for j in d.get("results", []):
            locs = as_list(j.get("locations"))
            refs = j.get("refs") or {}
            yield raw_job(
                source="themuse", source_job_id=str(j.get("id", "")), title=j.get("name", ""),
                company=(j.get("company") or {}).get("name", ""),
                url=refs.get("landing_page", ""), apply_url=refs.get("landing_page", ""),
                posted_at=parse_date(j.get("publication_date")), location_raw="; ".join(locs),
                description_html=j.get("contents", ""), source_categories=as_list(j.get("categories")),
                seniority_raw=" ".join(as_list(j.get("levels"))),
                remote_flag=any("remote" in l.lower() for l in locs) or None,
                workplace_raw="remote" if any("flexible / remote" in l.lower() for l in locs) else "",
            )
        if page >= d.get("page_count", 0):
            ctx.complete = True
            return
        if ctx.out_of_time():
            return


def workingnomads(ctx):
    d = http.get_json("https://www.workingnomads.com/api/exposed_jobs/")
    for j in d:
        yield raw_job(
            source="workingnomads", source_job_id=j.get("url", ""), title=j.get("title", ""),
            company=j.get("company_name", ""), url=j.get("url", ""), apply_url=j.get("url", ""),
            posted_at=parse_date(j.get("pub_date")), location_raw=j.get("location", "") or "Worldwide",
            description_html=j.get("description", ""), source_categories=as_list(j.get("category_name")),
            tags=[t.strip() for t in (j.get("tags") or "").split(",") if t.strip()],
            remote_flag=True, workplace_raw="remote",
        )
    ctx.complete = True


def weworkremotely(ctx):
    text = http.fetch("https://weworkremotely.com/remote-jobs.rss")
    root = ET.fromstring(text.encode("utf-8"))
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        title = g("title")
        company, _, role = title.partition(":")
        if not role:
            company, role = "", title
        yield raw_job(
            source="weworkremotely", source_job_id=g("guid") or g("link"), title=role.strip(), company=company.strip(),
            url=g("link"), apply_url=g("link"), posted_at=parse_date(g("pubDate")),
            location_raw=g("region") or "Worldwide", description_html=g("description"),
            source_categories=as_list(g("category")), employment_type_raw=g("type"),
            tags=[s.strip() for s in g("skills").split(",") if s.strip()],
            remote_flag=True, workplace_raw="remote",
        )
    ctx.complete = True


def workable_search(ctx):
    """Workable's public, market-wide job search (all companies using Workable)."""
    queries = ctx.query_terms or [""]
    pages = ctx.max_pages * 4  # 20 jobs/page; Workable rate-limits heavy use, so stay modest
    for q in queries:
        token = None
        for _ in range(pages):
            params = {"query": q} if q else {}
            if token:
                params["pageToken"] = token
            d = http.get_json("https://jobs.workable.com/api/v1/jobs?" + urllib.parse.urlencode(params))
            for j in d.get("jobs", []):
                yield _workable_search_job(j)
            token = d.get("nextPageToken")
            if not token or not d.get("jobs"):
                break
            if ctx.out_of_time():
                return


def _workable_search_job(j):
    loc = j.get("location") or {}
    co = j.get("company") or {}
    body = "".join(j.get(k) or "" for k in ("description", "requirementsSection", "benefitsSection"))
    wp = (j.get("workplace") or "").replace("_", "-")
    return raw_job(
        source="workable", source_job_id=j.get("id", ""), title=j.get("title", ""), company=co.get("title", ""),
        company_logo=co.get("image") or "", url=j.get("url", ""), apply_url=j.get("url", ""),
        posted_at=parse_date(j.get("created")),
        location_raw="; ".join(as_list(j.get("locations"))) or ", ".join(
            x for x in [loc.get("city"), loc.get("subregion"), loc.get("countryName")] if x),
        description_html=body, department=j.get("department") or "", employment_type_raw=j.get("employmentType") or "",
        workplace_raw=wp, remote_flag=(wp == "remote") or None, country_hint=loc.get("countryName") or "",
        company_industry=(co.get("industry") or ""), company_website=co.get("website") or "",
    )


HN_ROLE_RX = (r"engineer|developer|scientist|designer|manager|lead|architect|analyst|\bsre\b|devops|founding|head of|"
              r"director|researcher|recruiter|marketing|sales|product|roles|positions|swe\b|programmer|ops\b")
HN_LOC_RX = (r"remote|onsite|on-site|hybrid|in[- ]office|[A-Z][a-z]+,\s*[A-Z]{2}\b|london|berlin|new york|nyc|\bsf\b|"
             r"san francisco|europe|\busa?\b|\buk\b|canada|anywhere|\bbay area\b")


def hackernews(ctx):
    """The latest monthly 'Ask HN: Who is hiring?' thread — one job per top-level comment."""
    d = http.get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
                      "&query=" + urllib.parse.quote("who is hiring") + "&hitsPerPage=5")
    thread = next((h for h in d.get("hits", []) if h.get("title", "").lower().startswith("ask hn: who is hiring")), None)
    if not thread:
        return
    item = http.get_json("https://hn.algolia.com/api/v1/items/%s" % thread["objectID"], timeout=60)
    for c in item.get("children", []):
        text = c.get("text") or ""
        if not text or c.get("author") in (None, "[deleted]"):
            continue
        header = html_to_text(text.split("<p>", 1)[0])
        parts = [p.strip() for p in re.split(r"\s+\|\s+|\s*\|\s*", header) if p.strip()]
        if len(parts) < 2:
            continue
        company = re.sub(r"\s*\(.*?\)|\s*https?://\S+", "", parts[0]).strip()
        is_loc = lambda p: re.search(HN_LOC_RX, p, re.I)
        is_comp = lambda p: re.search(r"[$€£]\s?\d|\d+k\b|equity", p, re.I)
        is_kind = lambda p: re.search(r"^(full[- ]?time|part[- ]?time|contract|intern\w*|visa|https?://)", p, re.I)
        role = next((p for p in parts[1:] if re.search(HN_ROLE_RX, p, re.I) and not is_loc(p)), None) or \
            next((p for p in parts[1:] if not (is_loc(p) or is_comp(p) or is_kind(p))), "Multiple roles")
        locs = [p for p in parts[1:] if p is not role and is_loc(p)]
        comp = next((p for p in parts if is_comp(p)), "")
        yield raw_job(
            source="hackernews", source_job_id=str(c.get("id")), title=role[:200], company=company[:120],
            url="https://news.ycombinator.com/item?id=%s" % c.get("id"),
            apply_url="https://news.ycombinator.com/item?id=%s" % c.get("id"),
            posted_at=parse_date(c.get("created_at")), location_raw="; ".join(locs),
            description_html=text, salary_text=comp,
            workplace_raw=" ".join(locs), employment_type_raw=" ".join(parts),
        )
    ctx.complete = True


def adzuna(ctx):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        ctx.notes.append("skipped: set ADZUNA_APP_ID and ADZUNA_APP_KEY (free at developer.adzuna.com)")
        return
    country = os.environ.get("ADZUNA_COUNTRY", "us")
    what = " ".join(ctx.query_terms)
    cur = {"us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD", "de": "EUR", "fr": "EUR", "nl": "EUR", "in": "INR"}.get(country, "")
    for page in range(1, ctx.max_pages * 4 + 1):
        q = urllib.parse.urlencode({"app_id": app_id, "app_key": app_key, "results_per_page": 50,
                                    "what_or": what, "content-type": "application/json"})
        d = http.get_json("https://api.adzuna.com/v1/api/jobs/%s/search/%d?%s" % (country, page, q))
        rows = d.get("results", [])
        for j in rows:
            yield raw_job(
                source="adzuna", source_job_id=str(j.get("id", "")), title=j.get("title", ""),
                company=(j.get("company") or {}).get("display_name", ""), url=j.get("redirect_url", ""),
                apply_url=j.get("redirect_url", ""), posted_at=parse_date(j.get("created")),
                location_raw=(j.get("location") or {}).get("display_name", ""), description_html=j.get("description", ""),
                source_categories=as_list((j.get("category") or {}).get("label")),
                employment_type_raw=" ".join(x for x in [j.get("contract_time"), j.get("contract_type")] if x),
                salary_min=j.get("salary_min"), salary_max=j.get("salary_max"), salary_currency=cur,
                salary_period="year", country_hint=country.upper(),
            )
        if not rows or ctx.out_of_time():
            return


def usajobs(ctx):
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        ctx.notes.append("skipped: set USAJOBS_API_KEY and USAJOBS_EMAIL (free at developer.usajobs.gov)")
        return
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    for page in range(1, ctx.max_pages + 1):
        q = urllib.parse.urlencode({"Keyword": " ".join(ctx.query_terms), "ResultsPerPage": 500, "Page": page})
        d = http.get_json("https://data.usajobs.gov/api/search?" + q, headers=headers)
        items = (d.get("SearchResult") or {}).get("SearchResultItems", [])
        for it in items:
            m = it.get("MatchedObjectDescriptor", {})
            rem = (m.get("PositionRemuneration") or [{}])[0]
            det = (m.get("UserArea") or {}).get("Details", {})
            duties = det.get("MajorDuties") or []
            yield raw_job(
                source="usajobs", source_job_id=m.get("PositionID", ""), title=m.get("PositionTitle", ""),
                company=m.get("OrganizationName", ""), url=m.get("PositionURI", ""),
                apply_url=(m.get("ApplyURI") or [""])[0], posted_at=parse_date(m.get("PublicationStartDate")),
                expires_at=parse_date(m.get("ApplicationCloseDate")), location_raw=m.get("PositionLocationDisplay", ""),
                description=m.get("QualificationSummary", "") + "\n\n" + "\n".join(duties if isinstance(duties, list) else [duties]),
                source_categories=as_list([c.get("Name") for c in m.get("JobCategory", [])]),
                department=m.get("DepartmentName", ""),
                employment_type_raw=" ".join(as_list([s.get("Name") for s in m.get("PositionSchedule", [])])),
                salary_min=rem.get("MinimumRange"), salary_max=rem.get("MaximumRange"), salary_currency="USD",
                salary_period={"PA": "year", "PH": "hour"}.get(rem.get("RateIntervalCode"), "year"),
                country_hint="US", company_industry="Government",
            )
        if len(items) < 500:
            ctx.complete = True
            return


# ---------------------------------------------------------------------------
# Company career boards (applicant-tracking systems)
# ---------------------------------------------------------------------------

def greenhouse(ctx, token):
    d = http.get_json("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % token, timeout=60)
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        yield raw_job(
            source="greenhouse", source_job_id=str(j.get("id", "")), title=j.get("title", ""),
            company=j.get("company_name") or token, url=j.get("absolute_url", ""), apply_url=j.get("absolute_url", ""),
            posted_at=parse_date(j.get("first_published") or j.get("updated_at")), location_raw=loc,
            description_html=j.get("content", ""), department=", ".join(as_list(j.get("departments"))),
            team=", ".join(as_list(j.get("offices"))), remote_flag=("remote" in loc.lower()) or None,
        )
    ctx.complete = True


def lever(ctx, token):
    d = http.get_json("https://api.lever.co/v0/postings/%s?mode=json" % token, timeout=60)
    for j in d:
        c = j.get("categories") or {}
        sal = j.get("salaryRange") or {}
        body = (j.get("description") or "") + "".join(
            "<h3>%s</h3>%s" % (x.get("text", ""), x.get("content", "")) for x in (j.get("lists") or [])
        ) + (j.get("additional") or "")
        yield raw_job(
            source="lever", source_job_id=j.get("id", ""), title=j.get("text", ""), company=token,
            url=j.get("hostedUrl", ""), apply_url=j.get("applyUrl", ""), posted_at=parse_date(j.get("createdAt")),
            location_raw=", ".join(as_list(c.get("allLocations"))) or c.get("location", ""),
            description_html=body, department=c.get("department", ""), team=c.get("team", ""),
            employment_type_raw=c.get("commitment", ""), workplace_raw=j.get("workplaceType", ""),
            country_hint=j.get("country", ""), salary_min=sal.get("min"), salary_max=sal.get("max"),
            salary_currency=sal.get("currency", ""), salary_period=sal.get("interval", ""),
        )
    ctx.complete = True


def ashby(ctx, token):
    d = http.get_json("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % token, timeout=60)
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        locs = [j.get("location", "")] + [x.get("location", "") for x in (j.get("secondaryLocations") or [])]
        addr = (j.get("address") or {}).get("postalAddress") or {}
        smin = smax = None
        cur = per = ""
        for tier in comp.get("summaryComponents") or []:
            if tier.get("compensationType") == "Salary":
                smin, smax = tier.get("minValue"), tier.get("maxValue")
                cur, per = tier.get("currencyCode", ""), tier.get("interval", "")
                break
        yield raw_job(
            source="ashby", source_job_id=j.get("id", ""), title=(j.get("title") or "").strip(), company=token,
            url=j.get("jobUrl", ""), apply_url=j.get("applyUrl", ""), posted_at=parse_date(j.get("publishedAt")),
            location_raw="; ".join(l for l in locs if l), description_html=j.get("descriptionHtml", ""),
            description=j.get("descriptionPlain", ""), department=j.get("department", ""), team=j.get("team", ""),
            employment_type_raw=j.get("employmentType", ""), workplace_raw=j.get("workplaceType", ""),
            remote_flag=j.get("isRemote"), country_hint=addr.get("addressCountry", ""),
            salary_min=smin, salary_max=smax, salary_currency=cur, salary_period=per,
            salary_text=comp.get("compensationTierSummary") or "",
        )
    ctx.complete = True


def workable(ctx, token):
    d = http.get_json("https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % token, timeout=60)
    name = d.get("name") or token
    for j in d.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]
        loc_str = "; ".join(", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x) for l in locs)
        remote = str(j.get("telecommuting")).lower() == "true"
        yield raw_job(
            source="workable", source_job_id=j.get("shortcode", ""), title=j.get("title", ""), company=name,
            url=j.get("url") or j.get("shortlink", ""), apply_url=j.get("application_url", ""),
            posted_at=parse_date(j.get("published_on") or j.get("created_at")), location_raw=loc_str,
            description_html=j.get("description", ""), department=j.get("department", ""),
            source_categories=as_list(j.get("function")), employment_type_raw=j.get("employment_type", ""),
            seniority_raw=j.get("experience", ""), remote_flag=remote or None,
            workplace_raw="remote" if remote else "", country_hint=j.get("country", ""),
            company_industry=j.get("industry", ""),
        )
    ctx.complete = True


def smartrecruiters(ctx, token):
    detail_limit = getattr(ctx.opts, "detail_limit", 150)
    offset, details = 0, 0
    while True:
        d = http.get_json("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100&offset=%d" % (token, offset))
        rows = d.get("content", [])
        for j in rows:
            body, url = "", "https://jobs.smartrecruiters.com/%s/%s" % (token, j.get("id"))
            if details < detail_limit and ctx.time_left() > 0:
                try:
                    full = http.get_json(j.get("ref") or "", retries=1)
                    secs = (full.get("jobAd") or {}).get("sections") or {}
                    body = "".join("<h3>%s</h3>%s" % (v.get("title", ""), v.get("text", "")) for v in secs.values() if isinstance(v, dict))
                    url = full.get("postingUrl") or url
                    details += 1
                except (http.PermanentError, http.TransientError):
                    pass
            loc = j.get("location") or {}
            remote = bool(loc.get("remote"))
            yield raw_job(
                source="smartrecruiters", source_job_id=str(j.get("id", "")), title=j.get("name", ""),
                company=(j.get("company") or {}).get("name") or token, url=url, apply_url=url,
                posted_at=parse_date(j.get("releasedDate")),
                location_raw=", ".join(x for x in [loc.get("city", "").title(), loc.get("region"), (loc.get("country") or "").upper()] if x),
                description_html=body, department=(j.get("department") or {}).get("label", ""),
                source_categories=as_list((j.get("function") or {}).get("label")),
                employment_type_raw=(j.get("typeOfEmployment") or {}).get("label", ""),
                seniority_raw=(j.get("experienceLevel") or {}).get("label", ""),
                remote_flag=remote or None, workplace_raw="remote" if remote else "",
                country_hint=(loc.get("country") or "").upper(),
                company_industry=(j.get("industry") or {}).get("label", ""),
            )
        offset += len(rows)
        if not rows or offset >= d.get("totalFound", 0):
            if details < offset:
                ctx.notes.append("descriptions fetched for %d of %d (raise --detail-limit for more)" % (details, offset))
            ctx.complete = True
            return
        if ctx.out_of_time():
            return


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FEEDS = {
    "remotive": (remotive, "Remote jobs (Remotive)"),
    "remoteok": (remoteok, "Remote jobs (RemoteOK)"),
    "arbeitnow": (arbeitnow, "Germany/EU jobs (Arbeitnow)"),
    "himalayas": (himalayas, "Remote jobs with salary data (Himalayas)"),
    "jobicy": (jobicy, "Remote jobs (Jobicy)"),
    "themuse": (themuse, "General US jobs (The Muse)"),
    "workingnomads": (workingnomads, "Remote jobs (Working Nomads)"),
    "weworkremotely": (weworkremotely, "Remote jobs (We Work Remotely RSS)"),
    "workable_search": (workable_search, "Market-wide search across every Workable company (gentle: ~400 jobs/run at default depth)"),
    "hackernews": (hackernews, "Latest HN 'Who is hiring?' thread"),
    "adzuna": (adzuna, "General job market [needs ADZUNA_APP_ID/ADZUNA_APP_KEY]"),
    "usajobs": (usajobs, "US federal jobs [needs USAJOBS_API_KEY/USAJOBS_EMAIL]"),
}
BOARDS = {
    "greenhouse": (greenhouse, "boards.greenhouse.io/<token>"),
    "lever": (lever, "jobs.lever.co/<token>"),
    "ashby": (ashby, "jobs.ashbyhq.com/<token>"),
    "workable": (workable, "apply.workable.com/<token>"),
    "smartrecruiters": (smartrecruiters, "jobs.smartrecruiters.com/<CompanyId>"),
}
ALL = list(FEEDS) + list(BOARDS)

# How to check whether a token exists on each ATS (used by `discover`)
PROBES = {
    "greenhouse": lambda t: "https://boards-api.greenhouse.io/v1/boards/%s" % t,
    "lever": lambda t: "https://api.lever.co/v0/postings/%s?mode=json&limit=1" % t,
    "ashby": lambda t: "https://api.ashbyhq.com/posting-api/job-board/%s" % t,
    "workable": lambda t: "https://apply.workable.com/api/v1/widget/accounts/%s" % t,
    "smartrecruiters": lambda t: "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=1" % t,
}


def probe_count(kind, token):
    """Number of open jobs on a board, or None if the board doesn't exist."""
    try:
        d = http.get_json(PROBES[kind](token), retries=1, timeout=15)
    except (http.PermanentError, http.TransientError):
        return None
    if kind == "greenhouse":
        try:
            return len(http.get_json("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % token, retries=1).get("jobs", []))
        except Exception:
            return 0
    if kind == "lever":
        return len(d) if isinstance(d, list) and d else None
    if kind == "ashby":
        return len(d.get("jobs", []))
    if kind == "workable":
        return len(d.get("jobs", []))
    if kind == "smartrecruiters":
        return d.get("totalFound") or None
    return None
