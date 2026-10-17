"""Turns a raw posting into a fully classified record."""

import hashlib
import html
import re
from collections import Counter

from .taxonomy import *  # noqa: F401,F403
from .taxonomy import _TOKEN_RX
from .extract import extract as extract_contacts
from .util import MAX_DESC, html_to_text, slugify

COMPANY_META = {}  # board token / lowercased company -> (display name, industry), filled from companies.txt

def _num(s, k=None):
    s = str(s).strip()
    if re.fullmatch(r"\d{1,3}(?:[,.\s]\d{3})+", s):
        s = re.sub(r"[,.\s]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    if k and k.lower() == "k":
        n *= 1000
    elif k and k.lower() == "m":
        n *= 1_000_000
    return n


def _norm_period(p):
    p = (p or "").lower()
    if p.startswith(("h", "ph")):
        return "hour"
    if p.startswith(("y", "an", "pa")):
        return "year"
    if p.startswith("mo"):
        return "month"
    if p.startswith("w"):
        return "week"
    if p.startswith("d"):
        return "day"
    return ""


def _to_float(v):
    if v in (None, "", 0, "0"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_salary_text(text):
    """Find the most plausible salary range in free text. Returns (min, max, currency, period) or None."""
    best = None
    text = text or ""
    for m in list(MONEY_RX.finditer(text)) + list(MONEY_TRAIL_RX.finditer(text)):
        cur_raw = m.group("cur")
        cur = CUR_SYMBOLS.get(cur_raw, cur_raw.upper())
        a = _num(m.group("a"), m.group("ak") or m.group("bk"))
        b = _num(m.group("b"), m.group("bk")) if m.group("b") else None
        if a is None:
            continue
        per = _norm_period(m.group("per"))
        hi = b or a
        if not per:
            per = "hour" if hi < 300 else ("month" if hi < 15000 else "year")
        annual = hi * PERIOD_MULT[per]
        if not (12000 <= annual * FX_TO_USD.get(cur, 1) <= 3_000_000):
            continue
        if b is not None and b < a:
            continue
        cand = (a, b or a, cur, per)
        # prefer ranges over single figures, then larger annualized value (avoids "$500 stipend")
        score = (b is not None, annual)
        if best is None or score > best[0]:
            best = (score, cand)
    return best[1] if best else None


def classify_function(title, cats, dept, desc):
    for f, s, rx in FUNCTION_RULES:
        if rx.search(title):
            return f, s, "title"
    blob = " ".join(cats + [dept])
    for f, s, rx in FUNCTION_RULES:
        if blob and rx.search(blob):
            return f, s, "category"
    head = desc[:2500]
    best = max(((len(rx.findall(head)), f, s) for f, s, rx in DESC_RULES), default=(0, "", ""))
    if best[0] >= 2:
        return best[1], best[2], "description"
    return "Other", "Other", "none"


IC_MANAGER_RX = re.compile(r"(account|product|project|program|community|success|case|content|social media|office|"
                           r"marketing|partner|relationship|territory|channel|category|campaign|brand|technical account|"
                           r"solutions?|implementation|onboarding|portfolio|property|facilities|payroll|benefits|events?)\s+manager", re.I)


def classify_seniority(title, raw, years_min):
    ic_mgr = bool(IC_MANAGER_RX.search(title))
    for name, rx in SENIORITY_RULES:
        if name == "Manager" and ic_mgr:
            continue
        if rx.search(title):
            return name, "title"
    for name, rx in SENIORITY_RULES:
        if raw and rx.search(raw):
            return name, "source"
    if years_min is not None:
        if years_min >= 8:
            return "Staff", "years"
        if years_min >= 5:
            return "Senior", "years"
        if years_min >= 2:
            return "Mid", "years"
        return "Entry / Junior", "years"
    return "Mid", "default"


def extract_years(text):
    yrs = []
    for m in re.finditer(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?|jahre)(?:\s+of)?(?:[^.\n]{0,60}?)(?:experience|exp\b|erfahrung|in a|working|building|as a)", text, re.I):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else None
        if 0 < a <= 25:
            yrs.append((a, b if b and b <= 30 and b >= a else None))
    if not yrs:
        return None, None
    lo = min(a for a, _ in yrs)
    hi = max((b or a) for a, b in yrs)
    return lo, hi if hi != lo else None


def detect_degree(text):
    t = text.lower()
    level = ""
    if re.search(r"\bph\.?d\b|doctorate|doctoral", t):
        level = "PhD"
    elif re.search(r"master'?s|\bm\.?s\.?\b(?= in| degree)|\bmba\b|\bmsc\b", t):
        level = "Master's"
    elif re.search(r"bachelor'?s|\bb\.?s\.?\b(?= in| degree)|\bba\b(?= in| degree)|\bbsc\b|undergraduate degree|college degree|4-year degree|university degree|degree in", t):
        level = "Bachelor's"
    elif re.search(r"high school diploma|\bged\b|high school (degree|education)", t):
        level = "High school"
    if not level:
        return "", ""
    pref = "preferred" if re.search(r"(preferred|nice to have|a plus|bonus|or equivalent( practical)? experience|not required)", t) else "required"
    return level, pref


def detect_visa(text):
    t = text.lower()
    if re.search(r"(not|unable to|cannot|can't|won't|will not|do not|does not)\s+(be able to\s+)?(offer|provide|sponsor|support)[^.]{0,40}(visa|sponsorship|work authori)|no (visa )?sponsorship|without (the need for )?(visa )?sponsorship|must be (legally )?authori[sz]ed to work|us citizens? only|citizenship (is )?required", t):
        return "No"
    if re.search(r"visa sponsorship (is )?(available|offered|provided)|(we|will) (can )?sponsor|sponsorship available|relocation and visa|visa support|h-?1b (transfer|sponsorship)", t):
        return "Yes"
    return "Unknown"


def detect_clearance(text):
    m = re.search(r"\b(ts/sci|top secret|secret clearance|security clearance|public trust|\bsc clearance|dv clearance|polygraph)\b", text, re.I)
    return m.group(1).upper() if m else ""


def detect_travel(text):
    m = re.search(r"(\d{1,2})\s*%\s*(?:of the time\s*)?travel|travel(?:ing)?\s*(?:up to\s*)?(\d{1,2})\s*%", text, re.I)
    if m:
        return (m.group(1) or m.group(2)) + "%"
    if re.search(r"(occasional|some|regular|frequent|extensive) travel|travel required|willing(ness)? to travel", text, re.I):
        return "Yes"
    return ""


def detect_language(text):
    t = " %s " % text[:3000].lower()
    scores = {
        "en": sum(t.count(w) for w in [" the ", " and ", " you ", " with ", " our "]),
        "de": sum(t.count(w) for w in [" und ", " die ", " der ", " wir ", " sie ", " mit "]),
        "fr": sum(t.count(w) for w in [" et ", " les ", " nous ", " vous ", " des "]),
        "es": sum(t.count(w) for w in [" y ", " los ", " para ", " con ", " nuestro "]),
        "pt": sum(t.count(w) for w in [" e ", " para ", " com ", " você ", " nossa "]),
        "nl": sum(t.count(w) for w in [" en ", " het ", " wij ", " je ", " van "]),
    }
    lang, s = max(scores.items(), key=lambda x: x[1])
    return lang if s >= 3 else ""


def _first_hit(text, names):
    """The name from `names` that occurs earliest in `text` (ties → longest), or None. Deterministic."""
    best = None
    for n in sorted(names):
        m = re.search(r"\b%s\b" % re.escape(n), text)
        if m and (best is None or (m.start(), -len(n)) < (best[0], -len(best[1]))):
            best = (m.start(), n)
    return best[1] if best else None


def parse_location(raw, hint, text_flags):
    loc = (raw or "").strip()
    country, region, city, state = "", "", "", ""
    lower = loc.lower()

    # US "City, ST" pattern
    m = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\b", loc)
    if m and m.group(2) in US_STATES:
        city, state, country = m.group(1).strip(), US_STATES[m.group(2)], "United States"
    if not country:
        for name in US_STATE_NAMES:
            if re.search(r"\b%s\b" % re.escape(name), lower) and name not in ("washington", "georgia"):
                state, country = US_STATE_NAMES[name], "United States"
                break
    # cities: take the one that appears FIRST in the text (sets have no stable order, and the city feeds the job_id)
    if not country:
        c = _first_hit(lower, CITY_COUNTRY)
        if c:
            city, country = c.title(), CITY_COUNTRY[c]
    if not country:
        c = _first_hit(lower, US_CITIES)
        if c:
            city = {"nyc": "New York", "sf": "San Francisco"}.get(c, "Washington" if c.startswith("washington") else c.title())
            country = "United States"
            if not state:
                st = _first_hit(lower[lower.find(c) + len(c):], US_STATE_NAMES)
                state = US_STATE_NAMES[st] if st else ""
    if not country:
        found = [c for c, rx in COUNTRY_RX if rx.search(loc)]
        if found:
            country = found[0] if len(set(found)) == 1 else "; ".join(dict.fromkeys(found))
    if not country and hint:
        country = COUNTRY_CODES.get(hint.upper(), hint if len(hint) > 3 else "")
    if not city and loc and not country:
        first = re.split(r"[,;(/|]", loc)[0].strip()
        if first and not re.search(r"remote|anywhere|worldwide|hybrid|distributed|multiple|various|flexible", first, re.I) and len(first) < 40:
            city = first
    for name, rx in REGIONS.items():
        if rx.search(loc):
            region = name
            break
    if not region and country:
        c0 = country.split(";")[0]
        if c0 in ("United States", "Canada", "Mexico", "Brazil", "Argentina", "Colombia", "Chile", "Peru"):
            region = "Americas"
        elif c0 in ("India", "Singapore", "Japan", "South Korea", "China", "Hong Kong", "Taiwan", "Philippines",
                    "Indonesia", "Vietnam", "Australia", "New Zealand"):
            region = "APAC"
        elif c0 in ("Israel", "UAE", "Turkey", "Egypt", "South Africa", "Nigeria", "Kenya", "Pakistan"):
            region = "MEA"
        else:
            region = "Europe"
    return city, state, country, region


def workplace(raw_job_, text):
    wr = (raw_job_.get("workplace_raw") or "").lower()
    loc = (raw_job_.get("location_raw") or "").lower()
    title = (raw_job_.get("title") or "").lower()
    if "hybrid" in wr or "hybrid" in loc or "hybrid" in title:
        return "Hybrid"
    if "on-site" in wr or "onsite" in wr or wr == "office" or "in office" in wr:
        return "On-site"
    if "remote" in wr or raw_job_.get("remote_flag") is True or "remote" in loc or "remote" in title or "anywhere" in loc:
        return "Remote"
    t = text[:4000].lower()
    if re.search(r"\bhybrid\b", t):
        return "Hybrid"
    if re.search(r"fully remote|100% remote|remote-first|work from anywhere|remote position|this (role|position) is remote", t):
        return "Remote"
    if raw_job_.get("remote_flag") is False or re.search(r"on-?site|in[- ]office|in person|\bdays? (a|per) week in", t):
        return "On-site"
    if loc and not re.search(r"remote|anywhere|worldwide|global|multiple|various", loc):
        return "On-site (inferred)"
    return "Unknown"


def remote_scope(wp, loc, region, country):
    if wp != "Remote":
        return ""
    l = (loc or "").lower()
    if not l or re.search(r"worldwide|anywhere|global", l):
        return "Worldwide"
    if country and ";" not in country:
        return "Country: " + country
    if region:
        return "Region: " + region
    return "Restricted"


def employment_type(raw, title, text):
    for name, pat in EMPLOYMENT_RULES:
        if re.search(pat, raw or "", re.I):
            return name
    for name, pat in EMPLOYMENT_RULES[:3]:
        if re.search(pat, title, re.I):
            return name
    head = text[:3000]
    if re.search(r"\b(contract|contractor|freelance) (role|position|basis|engagement)\b|\b\d+[- ]month contract", head, re.I):
        return "Contract"
    if re.search(r"part[- ]time (role|position)", head, re.I):
        return "Part-time"
    return "Full-time"


def guess_industry(desc):
    """Score each industry by keyword hits in the opening (company blurb) of the description."""
    head = desc[:2500]
    scores = sorted(((len(rx.findall(head)), n) for n, rx in INDUSTRY_RULES), reverse=True)
    (top, name), (second, _) = scores[0], scores[1]
    return name if top >= 2 and top > second else ""


def company_industry_vote(jobs):
    """Industry is a company attribute: give every posting its company's majority guess."""
    by_co = {}
    for j in jobs:
        if j["industry_guess"]:
            by_co.setdefault(j["company_slug"], Counter())[j["industry_guess"]] += 1
    for j in jobs:
        c = by_co.get(j["company_slug"])
        if c and j["industry_basis"] != "companies.txt":
            j["industry_guess"] = c.most_common(1)[0][0]
    return jobs


_LEGAL_SUFFIX = re.compile(
    r"[,.]?\s+(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|ag|se|sa|sas|s\.a|bv|b\.v|"
    r"nv|plc|pty|pte|oy|ab|as|aps|kg|ug|srl|s\.r\.l|spa|sp\. z o\.o|kft|holdings?|group|technologies|labs?)\.?$", re.I)


def clean_company(name):
    """'Acme, Inc.' -> 'Acme'.  Used for display and for cross-source de-duplication."""
    n = html.unescape(name or "").strip().strip(",.")
    for _ in range(2):
        n = _LEGAL_SUFFIX.sub("", n).strip().strip(",.")
    return n or (name or "").strip()


def validate(r):
    """Return a reason string if a raw record is unusable, else None."""
    if not (r.get("title") or "").strip():
        return "missing title"
    if not (r.get("company") or "").strip():
        return "missing company"
    if not (r.get("url") or r.get("apply_url")):
        return "missing url"
    if len(r.get("title", "")) > 300:
        return "title too long"
    return None


def quality_score(o):
    """0-100: how complete and trustworthy the record is."""
    checks = [
        (15, bool(o["description_word_count"] >= 80)),
        (15, bool(o["salary_min_usd_annual"])),
        (10, o["salary_source"] == "structured"),
        (10, o["function_basis"] == "title"),
        (10, o["seniority_basis"] in ("title", "source")),
        (10, bool(o["country"]) and ";" not in o["country"]),
        (10, o["workplace_type"] in ("Remote", "Hybrid", "On-site")),
        (5, bool(o["posted_at"])),
        (5, bool(o["industry_guess"])),
        (5, o["skill_count"] > 0),
        (5, o["years_experience_min"] != ""),
    ]
    return sum(w for w, ok in checks if ok)


_DEDUP_NOISE = re.compile(
    r"\((?:\s*(?:remote|hybrid|on-?site|in[- ]office|full[- ]time|part[- ]time|contract|m/w/d|w/m/d|f/m/d|m/f/d|"
    r"d/f/m|m/w/x|all genders?|gn|\*)[\s,/|-]*)+\)|"
    r"[-–|,]\s*(?:remote|hybrid|on-?site)(?:\s*[-–(]?\s*(?:us|usa|uk|eu|europe|emea|global|worldwide|anywhere)\)?)?\s*$|"
    r"\b(?:m/w/d|w/m/d|f/m/d|m/f/d)\b|\*", re.I)


def dedup_title(title):
    """Identity key for a title: drops only workplace/gender tags, keeps role details like '(ML)' or '- Hunter'."""
    t = html.unescape(title or "")
    for _ in range(2):
        t = _DEDUP_NOISE.sub(" ", t)
    return slugify(t)


def title_normalized(title):
    t = re.sub(r"\(.*?\)|\[.*?\]", "", title)
    t = re.split(r"\s[-–|@]\s|,\s(?=[A-Z][a-z]+ ?(?:Team|Remote|US|EU|Europe))", t)[0]
    t = re.sub(r"\b(remote|hybrid|on-?site|m/w/d|w/m/d|f/m/d|m/f/d|all genders?|\(?\*\)?)\b", "", t, flags=re.I)
    t = re.sub(r"[*/]+$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -–,|/")


def skill_hits(text):
    toks = _TOKEN_RX.findall(re.sub(r"(?<![a-z0-9])\.net\b", "dotnet", text.lower()))
    grams = set(toks)
    grams.update(t.rstrip(".") for t in toks)
    grams.update(p for t in toks if "/" in t for p in t.split("/"))  # "python/django"
    grams.update(" ".join(toks[i:i + 2]) for i in range(len(toks) - 1))
    grams.update(" ".join(toks[i:i + 3]) for i in range(len(toks) - 2))
    out = {cat: [] for cat in SKILLS}
    for g in grams & SKILL_ALIASES.keys():
        for cat, name in SKILL_ALIASES[g]:
            if name not in out[cat]:
                out[cat].append(name)
    for (cat, name), rx in SKILL_SPECIAL.items():
        if name not in out[cat] and rx.search(text):
            out[cat].append(name)
    for cat in out:
        order = list(SKILLS[cat])
        out[cat].sort(key=lambda n: order.index(n) if n in order else 999)
    return out


def enrich(r):
    desc = r.get("description") or html_to_text(r.get("description_html"))
    title = html.unescape((r.get("title") or "").strip())
    cats = [html.unescape(c) for c in r.get("source_categories") or []]
    tags = [html.unescape(t) for t in r.get("tags") or []]
    full = "\n".join([title, " ".join(cats), " ".join(tags), desc])

    yrs_min, yrs_max = extract_years(desc)
    func, subfunc, func_basis = classify_function(title, cats + tags, r.get("department") or "", desc)
    senior, senior_basis = classify_seniority(title, r.get("seniority_raw") or "", yrs_min)
    wp = workplace(r, desc)
    city, state, country, region = parse_location(r.get("location_raw"), r.get("country_hint"), desc)

    # salary: structured first, then text
    smin, smax = _to_float(r.get("salary_min")), _to_float(r.get("salary_max"))
    cur = (r.get("salary_currency") or "").upper()
    per = _norm_period(r.get("salary_period"))
    sal_src = "structured" if (smin or smax) else ""
    if not sal_src:
        parsed = parse_salary_text(r.get("salary_text") or "") or parse_salary_text(desc)
        if parsed:
            smin, smax, cur, per = parsed
            sal_src = "parsed_from_text"
    if (smin or smax):
        if not cur:
            cur = "USD"
        if not per:
            per = "hour" if (smax or smin) < 300 else "year"
        if smin and not smax:
            smax = smin
        if smax and not smin:
            smin = smax
    mult = PERIOD_MULT.get(per, 1)
    fx = FX_TO_USD.get(cur)
    ann_min = round(smin * mult * fx) if (smin and fx) else None
    ann_max = round(smax * mult * fx) if (smax and fx) else None
    ann_mid = round((ann_min + ann_max) / 2) if (ann_min and ann_max) else None

    sk = skill_hits(full)
    langs = []
    for m in SPOKEN_RX.finditer(desc):
        l = (m.group(1) or m.group(2) or "").title()
        if l and l not in langs:
            langs.append(l)
    industry = guess_industry(desc)
    deg, deg_req = detect_degree(desc)
    posted = r.get("posted_at") or ""

    company = (r.get("company") or "").strip()
    meta_name, meta_industry = COMPANY_META.get(company.lower(), ("", ""))
    if meta_name:
        company = meta_name
    elif r["source"] in ("lever", "ashby", "greenhouse", "workable", "smartrecruiters") and company == company.lower():
        company = company.replace("-", " ").title()
    company = clean_company(company)
    industry_basis = "description" if industry else ""
    if meta_industry:
        industry, industry_basis = meta_industry, "companies.txt"
    elif not industry and r.get("company_industry"):
        industry, industry_basis = map_source_industry(r["company_industry"]), "source"
    key = "|".join([slugify(company), dedup_title(title), slugify(city or country or r.get("location_raw") or "")])
    job_id = hashlib.sha1(key.encode()).hexdigest()[:16]

    out = {
        "job_id": job_id,
        "source": r["source"],
        "source_key": r.get("source_key") or r["source"],
        "source_job_id": r.get("source_job_id", ""),
        "title": title,
        "title_normalized": title_normalized(title),
        "company": company,
        "company_slug": slugify(company),
        "job_function": func,
        "job_subfunction": subfunc,
        "function_basis": func_basis,
        "seniority": senior,
        "seniority_rank": SENIORITY_RANK.get(senior, ""),
        "seniority_basis": senior_basis,
        "is_people_manager": senior in ("Manager", "Director", "VP", "C-Level"),
        "employment_type": employment_type(r.get("employment_type_raw"), title, desc),
        "workplace_type": wp,
        "remote_scope": remote_scope(wp, r.get("location_raw"), region, country),
        "location_raw": r.get("location_raw") or "",
        "city": city,
        "state": state,
        "country": country,
        "region": region,
        "timezones": r.get("timezones") or "",
        "industry_guess": industry,
        "industry_basis": industry_basis,
        "department": r.get("department") or "",
        "team": r.get("team") or "",
        "source_categories": "; ".join(dict.fromkeys(cats)),
        "tags": "; ".join(dict.fromkeys(tags)),
        "salary_min": smin or "",
        "salary_max": smax or "",
        "salary_currency": cur if (smin or smax) else "",
        "salary_period": per if (smin or smax) else "",
        "salary_source": sal_src,
        "salary_min_usd_annual": ann_min or "",
        "salary_max_usd_annual": ann_max or "",
        "salary_mid_usd_annual": ann_mid or "",
        "years_experience_min": yrs_min if yrs_min is not None else "",
        "years_experience_max": yrs_max if yrs_max is not None else "",
        "degree_level": deg,
        "degree_requirement": deg_req,
        "visa_sponsorship": detect_visa(desc),
        "security_clearance": detect_clearance(desc),
        "travel_required": detect_travel(desc),
        "spoken_languages_required": "; ".join(langs),
        "posting_language": detect_language(desc),
        "programming_languages": "; ".join(sk["programming_languages"]),
        "frameworks": "; ".join(sk["frameworks"]),
        "cloud_devops": "; ".join(sk["cloud_devops"]),
        "databases_data": "; ".join(sk["databases"]),
        "ai_ml": "; ".join(sk["ai_ml"]),
        "tools": "; ".join(sk["tools"]),
        "skill_count": sum(len(v) for v in sk.values()),
        "posted_at": posted,
        "expires_at": r.get("expires_at") or "",
        "url": r.get("url") or "",
        "apply_url": r.get("apply_url") or r.get("url") or "",
        "company_logo": r.get("company_logo") or "",
        "description_word_count": len(desc.split()),
        "description": desc[:MAX_DESC],
    }
    for k, rx in BENEFITS.items():
        out[k] = bool(rx.search(desc))
    contacts = extract_contacts(r.get("description_html") or "", desc, out["url"], out["apply_url"], company)
    if not contacts["company_website"] and r.get("company_website"):
        contacts["company_website"] = r["company_website"]
        contacts["company_domain"] = re.sub(r"^https?://(www\.)?", "", r["company_website"]).split("/")[0].lower()
    out.update(contacts)
    out["classification_confidence"] = (
        "high" if func_basis == "title" and senior_basis in ("title", "source") else
        "medium" if func_basis in ("title", "category") else "low")
    out["data_quality_score"] = quality_score(out)
    out["content_hash"] = hashlib.sha1("|".join([title, company, out["location_raw"], desc]).encode()).hexdigest()[:16]
    return out


def map_source_industry(label):
    """Map a source's own industry label (e.g. Workable's 'Computer Software') onto our industry list."""
    for name, rx in INDUSTRY_RULES:
        if rx.search(label):
            return name
    low = label.lower()
    for hint, name in [("software", "SaaS / B2B Software"), ("internet", "SaaS / B2B Software"),
                       ("information technology", "SaaS / B2B Software"), ("financ", "Fintech / Financial Services"),
                       ("bank", "Fintech / Financial Services"), ("insurance", "Fintech / Financial Services"),
                       ("health", "Healthcare / Biotech"), ("medical", "Healthcare / Biotech"), ("pharma", "Healthcare / Biotech"),
                       ("education", "Education / EdTech"), ("e-learning", "Education / EdTech"), ("retail", "E-commerce / Retail"),
                       ("consumer", "E-commerce / Retail"), ("marketing", "Marketing / AdTech"), ("advertis", "Marketing / AdTech"),
                       ("staffing", "HR / Recruiting Tech"), ("human resources", "HR / Recruiting Tech"),
                       ("logistic", "Logistics / Transportation / Mobility"), ("transport", "Logistics / Transportation / Mobility"),
                       ("hospital", "Healthcare / Biotech"), ("hospitality", "Travel / Hospitality"), ("leisure", "Travel / Hospitality"),
                       ("real estate", "Real Estate / PropTech"), ("construction", "Hardware / Robotics / Manufacturing"),
                       ("manufactur", "Hardware / Robotics / Manufacturing"), ("energy", "Climate / Energy"),
                       ("government", "Government / Public Sector"), ("non-profit", "Non-profit"), ("nonprofit", "Non-profit"),
                       ("media", "Media / Entertainment / Gaming"), ("entertainment", "Media / Entertainment / Gaming"),
                       ("gaming", "Media / Entertainment / Gaming"), ("consult", "Consulting / Agency"), ("telecom", "Telecom"),
                       ("security", "Cybersecurity"), ("food", "Food / Agriculture")]:
        if hint in low:
            return name
    return label.strip()