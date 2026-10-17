"""Small shared helpers: text cleaning, dates, the raw-record shape every source produces."""

import html
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

MAX_DESC = 30000  # Excel cell limit is 32,767 chars
VERBOSE = False


LISTENERS = []  # callables that also receive every log line (the web UI uses this to stream progress)


def log(msg):
    print(msg, file=sys.stderr, flush=True)
    for fn in list(LISTENERS):
        try:
            fn(msg)
        except Exception:  # noqa: BLE001
            pass


def debug(msg):
    if VERBOSE:
        log("    · " + msg)


_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section|article)[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(str(s))
    if "&lt;" in s or "&gt;" in s:  # double-escaped (Greenhouse)
        s = html.unescape(s)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _NL.sub("\n\n", s).strip()


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, dict):
                x = x.get("name") or x.get("location") or x.get("text") or ""
            if x not in (None, ""):
                out.append(str(x))
        return out
    return [str(v)]


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def parse_date(v):
    """Return ISO-8601 UTC string or ''."""
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            n = float(v)
            if n > 1e12:
                n /= 1000.0
            return datetime.fromtimestamp(n, tz=timezone.utc).isoformat(timespec="seconds")
        s = str(v).strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            try:
                d = parsedate_to_datetime(s)
            except Exception:
                m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
                if not m:
                    return ""
                d = datetime.fromisoformat(m.group(1))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")



def raw_job(**kw):
    base = dict(
        source="", source_key="", source_job_id="", title="", company="", company_logo="", url="", apply_url="",
        posted_at="", expires_at="", location_raw="", description_html="", description="",
        source_categories=[], tags=[], department="", team="",
        employment_type_raw="", seniority_raw="", workplace_raw="", remote_flag=None,
        salary_min=None, salary_max=None, salary_currency="", salary_period="", salary_text="",
        country_hint="", timezones="", company_industry="", company_website="",
    )
    base.update(kw)
    return base
