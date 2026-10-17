"""CSV / JSONL export with filters. Writes atomically (temp file + rename) so a crash never leaves a half file."""

import csv
import json
import os
import re
import tempfile

LEAD = ["job_id", "status", "title", "company", "job_function", "job_subfunction", "seniority", "workplace_type",
        "remote_scope", "city", "state", "country", "region", "salary_min_usd_annual", "salary_mid_usd_annual",
        "salary_max_usd_annual", "posted_at", "age_days", "url"]
TAIL = ["first_seen", "last_seen", "closed_at", "times_seen", "source_count", "also_listed_on",
        "data_quality_score", "classification_confidence", "content_hash", "description"]
INTERNAL = {"source_key", "last_run"}
LITE_DROP = {"description", "company_logo", "content_hash", "source_categories", "tags", "timezones"}


def columns_for(rows, lite=False):
    seen = []
    for r in rows:
        for k in r:
            if k not in seen:
                seen.append(k)
    ai = sorted(c for c in seen if c.startswith("claude_"))
    middle = [c for c in seen if c not in LEAD and c not in TAIL and c not in INTERNAL and not c.startswith("claude_")]
    cols = [c for c in LEAD if c in seen] + middle + ai + [c for c in TAIL if c in seen]
    return [c for c in cols if not (lite and c in LITE_DROP)]


def _safe(v):
    """Excel/Sheets treat cells starting with = + - @ as formulas — neutralise that (CSV injection)."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", v):
        return "'" + v
    return "" if v is None else v


def _atomic(path):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    os.close(fd)
    return tmp


def write(rows, path, lite=False, fmt=None):
    rows = sorted(rows, key=lambda r: (r.get("posted_at") or "", r.get("job_id")), reverse=True)
    fmt = fmt or ("jsonl" if path.endswith((".jsonl", ".json")) else "csv")
    tmp = _atomic(path)
    try:
        if fmt == "jsonl":
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps({k: v for k, v in r.items() if not (lite and k in LITE_DROP)}, ensure_ascii=False) + "\n")
        else:
            cols = columns_for(rows, lite)
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow({k: _safe(r.get(k)) for k in cols})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(rows)


def matches(j, a):
    terms = getattr(a, "query_terms", None)
    if terms:
        hay = " ".join(str(j.get(k) or "") for k in ("title", "tags", "source_categories", "description")).lower()
        if not any(t in hay for t in terms):
            return False
    if getattr(a, "title", None) and not re.search(a.title, j.get("title") or "", re.I):
        return False
    if getattr(a, "company", None) and a.company.lower() not in (j.get("company") or "").lower():
        return False
    if getattr(a, "function", None) and a.function.lower() not in (j.get("job_function") or "").lower():
        return False
    if getattr(a, "seniority", None) and a.seniority.lower() not in (j.get("seniority") or "").lower():
        return False
    if getattr(a, "remote_only", False) and j.get("workplace_type") != "Remote":
        return False
    if getattr(a, "country", None) and a.country.lower() not in " ".join(
            str(j.get(k) or "") for k in ("country", "location_raw", "remote_scope")).lower():
        return False
    if getattr(a, "source", None) and a.source.lower() not in (j.get("also_listed_on") or j.get("source") or "").lower():
        return False
    if getattr(a, "min_salary", None) and not (j.get("salary_max_usd_annual") and float(j["salary_max_usd_annual"]) >= a.min_salary):
        return False
    if getattr(a, "has_salary", False) and not j.get("salary_min_usd_annual"):
        return False
    if getattr(a, "max_age", None) and j.get("age_days") not in ("", None) and int(j["age_days"]) > a.max_age:
        return False
    if getattr(a, "min_quality", None) and (j.get("data_quality_score") or 0) < a.min_quality:
        return False
    return True
