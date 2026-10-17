"""Your job-search profile and the match score (0-100) every job gets against it."""

import re

from .taxonomy import SENIORITY_RANK

DEFAULT_PROFILE = {
    "name": "",
    "functions": ["Engineering", "Data"],      # job_function values you want
    "subfunctions": [],                         # optional narrower picks, e.g. "Backend", "Machine Learning / AI"
    "skills": [],                               # your skills — the more you add, the better the ranking
    "seniority": [],                            # e.g. ["Mid", "Senior"]; empty = any
    "workplace": "remote_or_near",              # remote_only | remote_or_near | anywhere
    "home_city": "",
    "home_state": "",
    "home_country": "United States",
    "min_salary": 0,                            # annual USD; 0 = don't care
    "include_keywords": [],                     # boost jobs mentioning any of these
    "exclude_keywords": [],                     # hide jobs whose title contains any of these
    "exclude_companies": [],
    "favorite_companies": [],
    "needs_visa": False,
    "max_age_days": 60,
    "auto_refresh_hours": 12,                   # the server re-collects jobs this often (0 = never)
}

SKILL_FIELDS = ["programming_languages", "frameworks", "cloud_devops", "databases_data", "ai_ml", "tools"]
WORKPLACE_CHOICES = ["remote_only", "remote_or_near", "anywhere"]


def normalise(p):
    out = dict(DEFAULT_PROFILE)
    for k, v in (p or {}).items():
        if k not in DEFAULT_PROFILE:
            continue
        d = DEFAULT_PROFILE[k]
        if isinstance(d, list):
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",")]
            v = [str(x).strip() for x in (v or []) if str(x).strip()]
        elif isinstance(d, bool):
            v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
        elif isinstance(d, (int, float)):
            try:
                v = type(d)(float(v or 0))
            except (TypeError, ValueError):
                v = d
        else:
            v = str(v or "").strip()
        out[k] = v
    if out["workplace"] not in WORKPLACE_CHOICES:
        out["workplace"] = DEFAULT_PROFILE["workplace"]
    return out


def _low(xs):
    return {x.lower() for x in xs}


def job_skills(job):
    out = []
    for f in SKILL_FIELDS:
        out += [s for s in (job.get(f) or "").split("; ") if s]
    return out


class Matcher:
    """Precomputes profile lookups once, then scores many jobs quickly."""

    def __init__(self, profile):
        p = self.p = normalise(profile)
        self.functions = _low(p["functions"])
        self.subs = _low(p["subfunctions"])
        self.skills = _low(p["skills"])
        self.sen = _low(p["seniority"])
        self.sen_ranks = {SENIORITY_RANK[s] for s in SENIORITY_RANK if s.lower() in self.sen}
        self.excl_kw = [k.lower() for k in p["exclude_keywords"]]
        self.incl_kw = [k.lower() for k in p["include_keywords"]]
        self.excl_co = _low(p["exclude_companies"])
        self.fav_co = _low(p["favorite_companies"])
        self.city = p["home_city"].lower()
        self.state = p["home_state"].lower()
        self.country = p["home_country"].lower()
        # skills the job's own skill extraction doesn't know about are checked in the text
        self.free_skills = [(s, re.compile(r"(?<![\w+#])%s(?![\w+#])" % re.escape(s))) for s in self.skills if len(s) > 2]

    def excluded(self, j):
        """Return a reason string if the profile rules this job out entirely."""
        t = (j.get("title") or "").lower()
        for k in self.excl_kw:
            if k and k in t:
                return 'title contains "%s"' % k
        if (j.get("company") or "").lower() in self.excl_co:
            return "excluded company"
        if self.p["workplace"] == "remote_only" and j.get("workplace_type") not in ("Remote",):
            return "not remote"
        return None

    def score(self, j):
        """(score 0-100, reasons[list of (ok: bool|None, text)])"""
        reasons = []
        total = 0

        # 1. Field (25)
        f, sub = (j.get("job_function") or "").lower(), (j.get("job_subfunction") or "").lower()
        if not self.functions or f in self.functions:
            pts = 25 if (not self.subs or sub in self.subs) else 17
            total += pts
            reasons.append((True, "%s · %s" % (j.get("job_function"), j.get("job_subfunction"))))
        else:
            reasons.append((False, "Outside your fields (%s)" % (j.get("job_function") or "unknown")))

        # 2. Location (25)
        wp = j.get("workplace_type") or "Unknown"
        loc = " ".join(str(j.get(k) or "") for k in ("city", "state", "country", "location_raw")).lower()
        scope = (j.get("remote_scope") or "").lower()
        in_country = bool(self.country) and (self.country in loc or self.country in scope)
        near = bool(self.city) and self.city in loc or (bool(self.state) and self.state in loc and in_country)
        if wp == "Remote":
            if not self.country or "worldwide" in scope or in_country or not scope:
                total += 25
                reasons.append((True, "Remote" + ("" if not scope else " (%s)" % j.get("remote_scope").split(": ")[-1])))
            else:
                total += 8
                reasons.append((None, "Remote, but limited to %s" % j.get("remote_scope").split(": ")[-1]))
        elif self.p["workplace"] == "anywhere":
            total += 20
            reasons.append((True, wp.replace(" (inferred)", "")))
        elif near:
            total += 25
            reasons.append((True, "%s near you" % wp.replace(" (inferred)", "")))
        elif not self.city and in_country:
            total += 14
            reasons.append((None, "%s in %s — set your city to rank by commute" % (wp.replace(" (inferred)", ""), j.get("country"))))
        elif in_country:
            total += 5
            reasons.append((False, "%s in %s (not near you)" % (wp.replace(" (inferred)", ""), j.get("city") or j.get("country"))))
        else:
            reasons.append((False, "%s outside your area" % wp.replace(" (inferred)", "")))

        # 3. Skills (25)
        js = job_skills(j)
        if self.skills:
            hit = [s for s in js if s.lower() in self.skills]
            if len(hit) < len(self.skills):
                text = j.get("_txt")
                if text is None:
                    text = j["_txt"] = (j.get("title", "") + " " + (j.get("tags") or "") + " " +
                                        (j.get("description") or "")[:6000]).lower()
                have = {h.lower() for h in hit}
                for s, rx in self.free_skills:
                    if s not in have and s in text and rx.search(text):
                        hit.append(s)
            denom = max(1, min(len(self.skills), max(len(js), 3)))
            frac = min(1.0, len(hit) / float(denom))
            total += round(25 * frac)
            if hit:
                reasons.append((frac >= 0.4, "Skills: %s" % ", ".join(sorted(set(hit), key=str.lower)[:6])))
            else:
                reasons.append((False, "None of your skills mentioned"))
            j["_matched_skills"] = sorted(set(h.lower() for h in hit))
        else:
            total += 12
            reasons.append((None, "Add your skills in Profile for better ranking"))

        # 4. Seniority (10)
        sen = j.get("seniority") or ""
        rank = j.get("seniority_rank")
        if not self.sen:
            total += 7
        elif sen.lower() in self.sen:
            total += 10
            reasons.append((True, sen))
        elif rank not in (None, "") and any(abs(float(rank) - r) <= 1 for r in self.sen_ranks):
            total += 5
            reasons.append((None, "%s (close to your level)" % sen))
        else:
            reasons.append((False, "%s level" % sen))

        # 5. Pay (10)
        smax = j.get("salary_max_usd_annual")
        mn = self.p["min_salary"]
        if not mn:
            total += 7 if smax else 5
            if smax:
                reasons.append((True, "Pay listed: up to $%s" % _k(smax)))
        elif smax and float(smax) >= mn:
            total += 10
            reasons.append((True, "Pay up to $%s (≥ your $%s)" % (_k(smax), _k(mn))))
        elif smax:
            reasons.append((False, "Pay up to $%s (below your $%s)" % (_k(smax), _k(mn))))
        else:
            total += 4
            reasons.append((None, "Pay not listed"))

        # 6. Freshness (5)
        age = j.get("age_days")
        if age in ("", None):
            total += 2
        elif int(age) <= 7:
            total += 5
            reasons.append((True, "Posted %s" % ("today" if int(age) == 0 else "%dd ago" % int(age))))
        elif int(age) <= 30:
            total += 3
        elif int(age) > self.p["max_age_days"]:
            reasons.append((False, "Posted %d days ago" % int(age)))

        # Modifiers
        if self.p["needs_visa"] and j.get("visa_sponsorship") == "No":
            total -= 30
            reasons.append((False, "No visa sponsorship"))
        elif self.p["needs_visa"] and j.get("visa_sponsorship") == "Yes":
            total += 5
            reasons.append((True, "Sponsors visas"))
        if self.incl_kw:
            blob = ((j.get("title") or "") + " " + (j.get("description") or "")[:6000]).lower()
            kws = [k for k in self.incl_kw if k in blob]
            if kws:
                total += min(10, 4 * len(kws))
                reasons.append((True, "Mentions %s" % ", ".join(kws[:3])))
        if (j.get("company") or "").lower() in self.fav_co:
            total += 10
            reasons.append((True, "Favorite company"))
        if j.get("status") == "closed":
            reasons.append((False, "No longer listed"))
        return max(0, min(100, int(total))), reasons


def _k(v):
    v = float(v)
    return "%dk" % round(v / 1000) if v >= 1000 else "%d" % v
