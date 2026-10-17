"""Full-text search engine on SQLite FTS5.

Query language (all parts optional, combine freely):
    python django                 all words (stemmed, prefix-matched: "engineer" finds "engineering")
    "machine learning"            exact phrase
    react OR vue                  either
    -senior   -"sales engineer"   exclude
    title:staff  company:stripe  loc:berlin  skill:rust  source:greenhouse  domain:stripe.com
    level:senior  field:data  type:contract  is:remote | is:hybrid | is:onsite
    pay:150k+   pay:>150000       minimum annual USD (top of range)
    posted:7d   age:<14           posted within N days
    has:email  has:apply-email  has:salary  has:form  has:link  has:visa  has:notes
    status:saved | applied | interviewing | offer | rejected | hidden | tracked
Synonyms are expanded automatically (k8s → kubernetes, ml → machine learning, sf → san francisco, …).
"""

import re
import sqlite3
import time

from .util import log

FTS_COLUMNS = ["title", "company", "skills", "location", "body", "contacts"]
WEIGHTS = (10.0, 6.0, 4.0, 2.0, 1.0, 1.5)  # bm25 column weights in FTS_COLUMNS order

SYNONYMS = {
    "ml": ["machine learning"], "ai": ["artificial intelligence", "machine learning", "llm"], "llm": ["large language model", "llms"],
    "genai": ["generative ai"], "nlp": ["natural language processing"], "cv": ["computer vision"],
    "k8s": ["kubernetes"], "kubernetes": ["k8s"], "js": ["javascript"], "ts": ["typescript"], "golang": ["go"],
    "postgres": ["postgresql"], "postgresql": ["postgres"], "py": ["python"], "node": ["nodejs"], "nodejs": ["node"],
    "swe": ["software engineer"], "sde": ["software engineer", "software development engineer"], "sre": ["site reliability"],
    "devops": ["platform engineer", "infrastructure"], "pm": ["product manager"], "tpm": ["technical program manager"],
    "em": ["engineering manager"], "ds": ["data scientist", "data science"], "de": ["data engineer"], "qa": ["quality assurance", "test"],
    "ux": ["user experience"], "ui": ["user interface"], "fe": ["frontend", "front end"], "frontend": ["front end"], "backend": ["back end"],
    "fullstack": ["full stack"], "ios": ["swift"], "aws": ["amazon web services"], "gcp": ["google cloud"],
    "nyc": ["new york"], "sf": ["san francisco"], "la": ["los angeles"], "uk": ["united kingdom", "london"],
    "us": ["united states"], "usa": ["united states"], "remote": ["work from anywhere", "distributed"],
    "jr": ["junior"], "sr": ["senior"], "intern": ["internship"], "wfh": ["remote"],
}

TOKEN_RX = re.compile(r'(-?)(\w[\w.+#/-]*):("[^"]*"|\S+)|(-?)"([^"]+)"|(\S+)')


def available(con):
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts_probe USING fts5(x)")
        con.execute("DROP TABLE IF EXISTS temp._fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def rebuild(con):
    """(Re)build the full-text index from the jobs table. ~1-2s for 10k jobs."""
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS jobs_fts")
    con.execute("CREATE VIRTUAL TABLE jobs_fts USING fts5(job_id UNINDEXED, %s, tokenize='porter unicode61 remove_diacritics 2', "
                "prefix='3', detail=full)" % ", ".join(FTS_COLUMNS))
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    g = lambda c: 'COALESCE("%s", \'\')' % c if c in cols else "''"
    skills = " || ' ' || ".join(g(c) for c in ("programming_languages", "frameworks", "cloud_devops", "databases_data", "ai_ml",
                                               "tools", "tags", "job_subfunction", "job_function", "source_categories"))
    loc = " || ' ' || ".join(g(c) for c in ("city", "state", "country", "region", "location_raw", "remote_scope", "workplace_type"))
    contacts = " || ' ' || ".join(g(c) for c in ("contact_emails", "company_website", "company_domain", "links_apply", "links_other",
                                                 "apply_instructions", "contact_names"))
    # FTS rowid = jobs rowid, so hits map back to job_ids without touching the (large) FTS content rows
    con.execute("INSERT INTO jobs_fts (rowid, job_id, title, company, skills, location, body, contacts) "
                "SELECT rowid, job_id, %s, %s, %s, %s, %s, %s FROM jobs" % (
                    g("title"), g("company"), skills, loc, g("description"), contacts))
    n = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('fts_rows', ?)", (str(n),))
    con.commit()
    log("Search index rebuilt: %d jobs in %.1fs" % (n, time.time() - t0))


def update(con, job_ids):
    """Refresh the index entries of a few jobs (after a posting fetch) without rebuilding everything."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    g = lambda c: 'COALESCE("%s", \'\')' % c if c in cols else "''"
    marks = ",".join("?" * len(job_ids))
    rids = [r[0] for r in con.execute("SELECT rowid FROM jobs WHERE job_id IN (%s)" % marks, list(job_ids))]
    if not rids:
        return
    con.execute("DELETE FROM jobs_fts WHERE rowid IN (%s)" % ",".join("?" * len(rids)), rids)
    skills = " || ' ' || ".join(g(c) for c in ("programming_languages", "frameworks", "cloud_devops", "databases_data", "ai_ml",
                                               "tools", "tags", "job_subfunction", "job_function", "source_categories"))
    loc = " || ' ' || ".join(g(c) for c in ("city", "state", "country", "region", "location_raw", "remote_scope", "workplace_type"))
    contacts = " || ' ' || ".join(g(c) for c in ("contact_emails", "company_website", "company_domain", "links_apply", "links_other",
                                                 "apply_instructions", "contact_names"))
    con.execute("INSERT INTO jobs_fts (rowid, job_id, title, company, skills, location, body, contacts) "
                "SELECT rowid, job_id, %s, %s, %s, %s, %s, %s FROM jobs WHERE rowid IN (%s)" % (
                    g("title"), g("company"), skills, loc, g("description"), contacts, ",".join("?" * len(rids))), rids)
    con.commit()


def needs_rebuild(con):
    try:
        have = con.execute("SELECT v FROM meta WHERE k='fts_rows'").fetchone()
        n = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        con.execute("SELECT 1 FROM jobs_fts LIMIT 1")
        return not have or int(have[0]) != n
    except sqlite3.OperationalError:
        return True


# --------------------------------------------------------------------------- query parsing

def _fts_word(w):
    w = re.sub(r"[^\w+#.-]", "", w).strip(".-")
    if not w:
        return None
    w = w.replace('"', "")
    # FTS5 barewords can't contain + # . - ; quote those, prefix-match plain words
    if re.fullmatch(r"\w+", w) and not w.isdigit():
        return '"%s"*' % w if len(w) >= 3 else '"%s"' % w
    return '"%s"' % w


def _expand(word):
    base = _fts_word(word)
    if not base:
        return None
    alts = [base] + ['"%s"' % s for s in SYNONYMS.get(word.lower(), [])]
    return alts[0] if len(alts) == 1 else "(" + " OR ".join(alts) + ")"


def parse(q):
    """Split a query into an FTS5 MATCH string and structured filters.

    Returns {"match": str|None, "filters": {...}, "terms": [highlight words], "errors": [...]}"""
    filters, must, must_not, errors, terms = {}, [], [], [], []
    pending_or = False
    for m in TOKEN_RX.finditer(q or ""):
        neg_f, field, val, neg_p, phrase, word = m.groups()
        if field:
            val = val.strip('"')
            key = field.lower()
            neg = bool(neg_f)
            if key in ("title", "company", "skill", "skills", "loc", "location", "body", "description", "email", "contacts"):
                col = {"skill": "skills", "loc": "location", "description": "body", "email": "contacts"}.get(key, key)
                inner = " ".join(x for x in (_fts_word(t) for t in val.split()) if x)
                if inner:
                    expr = "%s : (%s)" % (col, inner)
                    (must_not if neg else must).append(expr)
                    if not neg:
                        terms += val.split()
            elif key in ("is", "workplace"):
                filters.setdefault("workplace", []).append({"onsite": "On-site", "on-site": "On-site", "remote": "Remote",
                                                            "hybrid": "Hybrid"}.get(val.lower(), val))
            elif key in ("level", "seniority"):
                filters.setdefault("seniority", []).append(val)
            elif key in ("field", "function"):
                filters.setdefault("function", []).append(val)
            elif key in ("sub", "subfunction", "specialty"):
                filters.setdefault("subfunction", []).append(val.replace("_", " "))
            elif key == "type":
                filters["employment_type"] = val
            elif key in ("source", "src"):
                filters["source"] = val
            elif key == "domain":
                filters["domain"] = val.lower()
            elif key in ("pay", "salary", "min_salary"):
                n = re.sub(r"[^\d.k]", "", val.lower())
                try:
                    filters["min_salary"] = float(n[:-1]) * 1000 if n.endswith("k") else float(n)
                except ValueError:
                    errors.append("couldn't read pay value %r" % val)
            elif key in ("posted", "age", "max_age"):
                n = re.sub(r"\D", "", val)
                if n:
                    filters["max_age"] = int(n) * (7 if val.endswith("w") else 30 if val.endswith("m") else 1)
            elif key == "has":
                filters.setdefault("has", []).append(val.lower())
            elif key in ("status", "track"):
                filters["track"] = val.lower()
            elif key in ("match", "min_match"):
                filters["min_match"] = int(re.sub(r"\D", "", val) or 0)
            else:
                errors.append("unknown filter %s:" % key)
            continue
        if phrase:
            expr = '"%s"' % phrase.replace('"', "")
            (must_not if neg_p else must).append(expr)
            if not neg_p:
                terms += [phrase]
            continue
        if word == "OR":
            pending_or = True
            continue
        neg = word.startswith("-") and len(word) > 1
        w = word[1:] if neg else word
        expr = _fts_word(w) if neg else _expand(w)
        if not expr:
            continue
        if neg:
            must_not.append(expr)
        elif pending_or and must:
            must[-1] = "(%s OR %s)" % (must[-1], expr)
        else:
            must.append(expr)
        if not neg:
            terms.append(w)
        pending_or = False
    match = " AND ".join(must) if must else None
    if match and must_not:
        match += " NOT " + " NOT ".join(must_not)
    elif must_not and not must:
        filters["exclude_match"] = " OR ".join(must_not)
    return {"match": match, "filters": filters, "terms": terms, "errors": errors}


def _rowmap(con):
    return dict(con.execute("SELECT rowid, job_id FROM jobs"))


def run(con, match, exclude_match=None, limit=20000):
    """{job_id: relevance 0-100} for an FTS5 MATCH expression. Selects rowids only — reading any FTS column
    would load each hit's full description and make broad queries ~200x slower."""
    out = {}
    rowmap = _rowmap(con)
    if match:
        rows = con.execute("SELECT rowid, bm25(jobs_fts, 0, %s) AS r FROM jobs_fts WHERE jobs_fts MATCH ? ORDER BY r LIMIT ?"
                           % ", ".join(str(w) for w in WEIGHTS), (match, limit)).fetchall()
        if rows:
            best, worst = rows[0][1], rows[-1][1]
            span = (worst - best) or 1.0
            for rid, r in rows:
                if rid in rowmap:
                    out[rowmap[rid]] = round(100 - 70 * (r - best) / span)  # top hit 100, weakest still 30
    if exclude_match:
        out["__exclude__"] = {rowmap.get(r[0]) for r in con.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", (exclude_match,))}
    return out


def snippets(con, match, job_ids):
    """Highlighted description excerpts (\x02…\x03 around matches) for just the jobs being shown."""
    if not match or not job_ids:
        return {}
    marks = ",".join("?" * len(job_ids))
    rids = dict(con.execute("SELECT rowid, job_id FROM jobs WHERE job_id IN (%s)" % marks, list(job_ids)))
    if not rids:
        return {}
    rows = con.execute("SELECT rowid, snippet(jobs_fts, 5, '\x02', '\x03', ' … ', 24) FROM jobs_fts "
                       "WHERE jobs_fts MATCH ? AND rowid IN (%s)" % ",".join("?" * len(rids)), [match] + list(rids)).fetchall()
    return {rids[r]: sn for r, sn in rows}
