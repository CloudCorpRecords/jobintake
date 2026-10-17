"""Command-line interface. Run  python3 jobintake.py --help"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

from . import enrich as E
from . import export as X
from . import http, sources as S, util
from .store import Store
from .util import log, now_iso, parse_date, raw_job

MAX_REQUEST_GRACE = 150  # watchdog slack beyond --source-timeout (one slow request + classification of its page)
STRAGGLERS = []  # sources whose threads never returned; we exit without waiting for them

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(HERE, "jobs.db")
DEFAULT_COMPANIES = os.path.join(HERE, "companies.txt")
DEFAULT_LOG = os.path.join(HERE, "intake.log")


# ---------------------------------------------------------------------------
# companies.txt
# ---------------------------------------------------------------------------

def load_companies(path):
    """Lines:  kind:token [| Display Name [| Industry]]"""
    out, bad = [], []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [x.strip() for x in line.split("|")]
            kind, _, token = parts[0].partition(":")
            kind, token = kind.strip().lower(), token.strip()
            if kind not in S.BOARDS or not token:
                bad.append("line %d: %r" % (n, line))
                continue
            name = parts[1] if len(parts) > 1 else ""
            ind = parts[2] if len(parts) > 2 else ""
            E.COMPANY_META[token.lower()] = (name, ind)
            if name:
                E.COMPANY_META[name.lower()] = (name, ind)
            out.append((kind, token))
    if bad:
        log("companies.txt: ignored %d malformed line(s): %s" % (len(bad), "; ".join(bad[:3])))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def _run_source(key, gen_fn, ctx):
    """Consume a source generator, keeping whatever it yielded even if it fails part-way."""
    t0 = time.monotonic()
    rows, err = [], None
    http.set_deadline(ctx.started + ctx.budget)
    try:
        for r in gen_fn():
            r["source_key"] = key
            rows.append(r)
            if S.STOP.is_set():
                ctx.notes.append("interrupted")
                break
    except http.Interrupted:
        ctx.notes.append("interrupted")
    except http.RateLimited as e:
        err = str(e)
    except http.OutOfTime:
        ctx.notes.append("time budget hit (--source-timeout %ss)" % ctx.budget)
    except http.PermanentError as e:
        err = "HTTP %s (board/endpoint not found?)" % e.code if e.code != "too-large" else "response too large"
    except Exception as e:  # noqa: BLE001 — one bad source must never sink the run
        err = "%s: %s" % (type(e).__name__, str(e)[:160])
    if any(n.startswith("skipped") for n in ctx.notes) and not rows:
        status = "skipped"
    elif not rows and (err or ctx.notes):
        status, err = "failed", err or "; ".join(dict.fromkeys(ctx.notes))
    elif err or (ctx.notes and not ctx.complete):
        status = "partial"
    else:
        status = "ok"
    return dict(key=key, rows=rows, error=err, status=status, complete=ctx.complete and not err,
                seconds=round(time.monotonic() - t0, 1), notes=ctx.notes)


def _report(res):
    mark = {"ok": "✓", "partial": "◐", "failed": "✗", "skipped": "–"}[res["status"]]
    extra = res["error"] or "; ".join(dict.fromkeys(res["notes"]))
    log("  %s %-34s %6d  %5.1fs  %s" % (mark, res["key"][:34], len(res["rows"]), res["seconds"], extra[:90]))
    return res


def collect(a, store):
    names = [s.strip() for s in a.sources.split(",")] if a.sources else [n for n in S.ALL]
    if a.exclude:
        names = [n for n in names if n not in {x.strip() for x in a.exclude.split(",")}]
    unknown = [n for n in names if n not in S.ALL]
    if unknown:
        sys.exit("Unknown source(s): %s. Run `sources` to list them." % ", ".join(unknown))

    tasks = []
    for n in names:
        if n in S.FEEDS:
            tasks.append((n, S.FEEDS[n][0], None))
    boards = [(k, t) for k, t in load_companies(a.companies) if k in names]
    for kind, token in boards:
        tasks.append(("%s:%s" % (kind, token), S.BOARDS[kind][0], token))
    if any(n in S.BOARDS for n in names) and not boards:
        log("(no company boards in %s — add lines like `greenhouse:stripe`, or use `discover`)" % a.companies)

    run_started = now_iso()
    log("Collecting from %d feeds/boards (Ctrl-C stops early and keeps what's been fetched)…" % len(tasks))
    results = []
    ex = ThreadPoolExecutor(max_workers=a.workers)
    futs = {}
    for key, fn, token in tasks:
        ctx = S.Ctx(a, key)
        gen = (lambda f=fn, c=ctx: f(c)) if token is None else (lambda f=fn, c=ctx, t=token: f(c, t))
        futs[ex.submit(_run_source, key, gen, ctx)] = key
    handled = set()
    watchdog = a.source_timeout + MAX_REQUEST_GRACE
    try:
        for fut in as_completed(futs, timeout=watchdog):
            handled.add(fut)
            results.append(_report(fut.result()))
    except FuturesTimeout:
        S.STOP.set()  # tell stragglers to stop; whatever they don't return is reported as hung
        for fut, key in futs.items():
            if fut not in handled:
                if fut.done():
                    results.append(_report(fut.result()))
                else:
                    results.append(_report(dict(key=key, rows=[], error="hung — no result after %ss" % watchdog,
                                                status="failed", complete=False, seconds=watchdog, notes=[])))
                    STRAGGLERS.append(key)
    except KeyboardInterrupt:
        log("\nInterrupted — finishing in-flight requests and saving what was collected…")
        S.STOP.set()
        for fut in futs:
            if fut not in handled and not fut.cancelled():
                try:
                    results.append(_report(fut.result(timeout=40)))
                except Exception:  # noqa: BLE001
                    pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    raws = [r for res in results for r in res["rows"]]
    valid, rejected = [], Counter()
    for r in raws:
        why = E.validate(r)
        if why:
            rejected[why] += 1
        else:
            valid.append(r)
    log("Classifying %d postings (%d rejected: %s)…" % (len(valid), sum(rejected.values()),
                                                       ", ".join("%s %d" % kv for kv in rejected.items()) or "none"))
    enriched, errors = [], 0
    for r in valid:
        try:
            enriched.append(E.enrich(r))
        except Exception as e:  # noqa: BLE001
            errors += 1
            util.debug("enrich failed for %s/%s: %s" % (r.get("source"), r.get("title"), e))
    if errors:
        log("  ! %d postings failed to classify (run with -v for details)" % errors)
    E.company_industry_vote(enriched)

    filtered = bool(a.query_terms or a.title or a.function or a.seniority or a.remote_only or a.country or a.min_salary
                    or a.has_salary or a.max_age or getattr(a, "company", None) or getattr(a, "min_quality", None))
    kept = [j for j in enriched if X.matches(dict(j, age_days=""), a)] if filtered else enriched
    if filtered:
        log("Filters kept %d of %d" % (len(kept), len(enriched)))

    new, updated = store.upsert(kept, run_started)
    complete = [r["key"] for r in results if r["complete"]]
    closed = 0
    if filtered:
        log("(filters active — skipping closed-job detection this run)")
    elif not S.STOP.is_set():
        closed = store.close_missing(complete, run_started)
    _reindex(store)
    hs = http.stats()
    store.record_run(started=run_started, finished=now_iso(), args=vars_for_log(a), fetched=len(raws), valid=len(valid),
                     new=new, updated=updated, closed=closed, reopened=0,
                     sources={r["key"]: {"status": r["status"], "count": len(r["rows"]), "seconds": r["seconds"],
                                         "error": r["error"], "notes": r["notes"]} for r in results})
    st = Counter(r["status"] for r in results)
    log("Stored %d new, %d updated, %d closed → %d open / %d total jobs in %s" % (
        new, updated, closed, store.count("status='open'"), store.count(), os.path.basename(a.db)))
    log("Sources: %d ok, %d partial, %d failed, %d skipped · %d HTTP requests, %d retries, %.1f MB" % (
        st["ok"], st["partial"], st["failed"], st["skipped"], hs["requests"], hs["retries"], hs["bytes"] / 1e6))
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        log("Failed: " + "; ".join("%s (%s)" % (r["key"], r["error"][:110]) for r in failed))
    return results


def _reindex(store):
    from . import search as SE
    if SE.available(store.con):
        SE.rebuild(store.con)


def vars_for_log(a):
    return {k: v for k, v in vars(a).items() if k not in ("fn",) and isinstance(v, (str, int, float, bool, list, type(None)))}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _export(a, store):
    rows = [r for r in store.rows(include_closed=a.include_closed) if X.matches(r, a)]
    n = X.write(rows, a.output, lite=a.lite)
    log("Exported %d jobs → %s" % (n, a.output))
    if getattr(a, "lite_copy", True) and not a.lite and a.output.endswith(".csv"):
        lite = a.output[:-4] + "_lite.csv"
        X.write(rows, lite, lite=True)
        log("Exported %d jobs → %s (no descriptions — opens fast in Excel/Sheets)" % (n, lite))


def cmd_run(a):
    store = Store(a.db)
    collect(a, store)
    if a.ai:
        _ai(a, store)
    _export(a, store)


def cmd_collect(a):
    collect(a, Store(a.db))


def cmd_export(a):
    _export(a, Store(a.db))


def _ai(a, store):
    from . import llm
    rows = [r for r in store.rows() if X.matches(r, a)]
    rows.sort(key=lambda r: (r.get("posted_at") or ""), reverse=True)
    todo = [r for r in rows if store.llm_get(r["job_id"], r["content_hash"]) is None][:a.ai_limit]
    if not todo:
        log("AI enrichment: nothing new to classify.")
        return
    est = llm.estimate_cost(len(todo), a.ai_model)
    log("AI enrichment: %d jobs with %s — estimated cost ≈ $%.2f" % (len(todo), a.ai_model, est))
    if est > a.ai_max_cost:
        log("  Estimate exceeds --ai-max-cost $%.2f; lower --ai-limit or raise the cap. Skipping." % a.ai_max_cost)
        return
    done, cached, failed, usage = llm.enrich_jobs(store, todo, model=a.ai_model, workers=a.ai_workers)
    log("AI enrichment: %d classified, %d failed · %s input / %s output tokens ≈ $%.2f" % (
        done, failed, usage.get("input_tokens", 0), usage.get("output_tokens", 0), llm.actual_cost(usage, a.ai_model)))


def cmd_ai(a):
    _ai(a, Store(a.db))


def cmd_import(a):
    store = Store(a.db)
    path = a.file
    if path.lower().endswith((".json", ".jsonl")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if path.lower().endswith(".jsonl"):
            data = [json.loads(l) for l in text.splitlines() if l.strip()]
        else:
            data = json.loads(text)
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            data = list(csv.DictReader(f))
    src = "import:" + os.path.basename(path)
    out, rejected = [], 0
    for i, row in enumerate(data):
        g = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
        pick = lambda *ks: next((g[k] for k in ks if g.get(k) not in (None, "")), "")
        desc = str(pick("description", "job_description", "body", "details", "summary"))
        tags = pick("tags", "skills")
        r = raw_job(
            source=src, source_key=src, source_job_id=str(pick("id", "job_id") or i),
            title=pick("title", "job_title", "position", "role"),
            company=pick("company", "company_name", "employer", "organization"),
            url=pick("url", "link", "job_url") or "import://%s/%d" % (os.path.basename(path), i),
            apply_url=pick("apply_url", "application_url"),
            posted_at=parse_date(pick("posted_at", "date", "date_posted", "created_at", "published")),
            location_raw=pick("location", "locations", "city"),
            description_html=desc if "<" in desc else "", description="" if "<" in desc else desc,
            source_categories=util.as_list(pick("category", "categories")),
            tags=[t.strip() for t in str(tags).split(",") if t.strip()] if tags else [],
            department=pick("department"), employment_type_raw=pick("employment_type", "job_type", "type"),
            workplace_raw=pick("workplace", "workplace_type", "remote"), salary_text=str(pick("salary", "compensation", "pay")),
            salary_min=pick("salary_min", "min_salary"), salary_max=pick("salary_max", "max_salary"),
            salary_currency=pick("currency", "salary_currency"), salary_period=pick("salary_period", "pay_period"),
        )
        if E.validate(r):
            rejected += 1
            continue
        out.append(E.enrich(r))
    new, updated = store.upsert(out, now_iso())
    log("Imported %d rows (%d new, %d merged, %d rejected for missing title/company) from %s" % (
        len(out), new, updated, rejected, path))
    if a.output:
        n = X.write([store.get(j["job_id"]) for j in out], a.output, lite=a.lite)
        log("Exported %d → %s" % (n, a.output))


def cmd_stats(a):
    store = Store(a.db)
    rows = [r for r in store.rows(include_closed=a.include_closed) if X.matches(r, a)]
    print("Jobs: %d (open %d, closed %d in database)\n" % (len(rows), store.count("status='open'"), store.count("status='closed'")))
    if not rows:
        return

    def table(field, n):
        c = Counter((r.get(field) or "(unknown)") for r in rows)
        print("== %s ==" % field)
        for k, v in c.most_common(n):
            print("  %-42s %6d  %5.1f%%" % (str(k)[:42], v, 100.0 * v / len(rows)))
        print()

    for field, n in [("also_listed_on", 15), ("job_function", 20), ("job_subfunction", 25), ("seniority", 12),
                     ("workplace_type", 6), ("employment_type", 6), ("country", 15), ("industry_guess", 15),
                     ("classification_confidence", 3), ("visa_sponsorship", 3)]:
        table(field, n)
    for field in ["programming_languages", "frameworks", "cloud_devops", "databases_data", "ai_ml", "tools"]:
        c = Counter(s for r in rows for s in (r.get(field) or "").split("; ") if s)
        print("== top %s ==\n  %s\n" % (field, ", ".join("%s (%d)" % kv for kv in c.most_common(15))))
    sal = sorted(float(r["salary_mid_usd_annual"]) for r in rows if r.get("salary_mid_usd_annual"))
    if sal:
        q = lambda p: sal[min(len(sal) - 1, int(p * len(sal)))]
        print("== salary (annual USD midpoint; %d jobs = %.0f%% have pay data) ==" % (len(sal), 100.0 * len(sal) / len(rows)))
        print("  p25 $%s   median $%s   p75 $%s   p90 $%s\n" % tuple("{:,.0f}".format(q(p)) for p in (.25, .5, .75, .9)))
    qs = [r.get("data_quality_score") or 0 for r in rows]
    print("== data quality ==\n  average score %.0f/100 · %d%% of jobs score ≥ 60\n" % (
        sum(qs) / len(qs), 100 * sum(1 for x in qs if x >= 60) // len(qs)))


def cmd_history(a):
    store = Store(a.db)
    for r in store.recent_runs(a.n):
        src = json.loads(r["sources"] or "{}")
        st = Counter(v["status"] for v in src.values())
        print("#%-4s %s  fetched %-6s new %-5s updated %-5s closed %-5s  sources ok %d / partial %d / failed %d" % (
            r["run_id"], (r["started"] or "")[:16], r["fetched"], r["new"], r["updated"], r["closed"],
            st["ok"], st["partial"], st["failed"]))
        if a.verbose_sources:
            for k, v in sorted(src.items()):
                print("        %-8s %-34s %5s  %s" % (v["status"], k, v["count"], v.get("error") or "; ".join(v.get("notes") or [])))


def cmd_changes(a):
    store = Store(a.db)
    since = a.since or (store.recent_runs(2)[-1]["started"] if store.recent_runs(2) else "")
    ch = store.changes_since(since)
    if not ch:
        print("No changes since %s" % since)
        return
    by = Counter(c["field"] for c in ch)
    print("%d changes since %s: %s\n" % (len(ch), since[:16], ", ".join("%s %d" % kv for kv in by.most_common())))
    for c in ch[:a.n]:
        print("  %-10s %-24s %-40s %s → %s" % (c["field"][:10], (c["company"] or "")[:24], (c["title"] or "")[:40],
                                               c["old"], c["new"]))
    if a.output:
        X.write(ch, a.output)
        log("Wrote %d changes → %s" % (len(ch), a.output))


def _token_candidates(name):
    words = re.findall(r"[A-Za-z0-9]+", name)
    base = [w.lower() for w in words]
    cands = ["".join(base), "-".join(base), "_".join(base), "".join(w.capitalize() for w in words), base[0] if base else ""]
    if len(base) > 1 and base[-1] in ("inc", "labs", "ai", "hq", "io", "app", "group", "co", "technologies", "tech"):
        cands.append("".join(base[:-1]))
    cands += [c + suf for c in cands[:2] for suf in ("hq", "inc", "jobs")]
    return [c for c in dict.fromkeys(cands) if c]


def cmd_discover(a):
    """Find which applicant-tracking system each company uses."""
    names = list(a.names)
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            names += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    existing = set(load_companies(a.companies))
    probes = [(n, kind, tok) for n in names for tok in _token_candidates(n) for kind in S.BOARDS]
    log("Probing %d candidate boards for %d companies…" % (len(probes), len(names)))
    found = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(S.probe_count, kind, tok): (n, kind, tok) for n, kind, tok in probes}
        for fut in as_completed(futs):
            n, kind, tok = futs[fut]
            cnt = fut.result()
            if cnt:
                found.setdefault(n, []).append((cnt, kind, tok))
    lines = []
    for n in names:
        hits = sorted(found.get(n, []), reverse=True)
        if not hits:
            print("  ✗ %-28s not found on %s" % (n, ", ".join(S.BOARDS)))
            continue
        cnt, kind, tok = hits[0]
        dup = " (already in companies.txt)" if (kind, tok) in existing else ""
        print("  ✓ %-28s %s:%s — %d open jobs%s%s" % (n, kind, tok, cnt, dup,
                                                     "  [also: %s]" % ", ".join("%s:%s" % (k, t) for _, k, t in hits[1:3]) if len(hits) > 1 else ""))
        if not dup:
            lines.append("%s:%s | %s" % (kind, tok, n))
    if lines and a.add:
        with open(a.companies, "a", encoding="utf-8") as f:
            f.write("\n# added by discover %s\n" % now_iso()[:10] + "\n".join(lines) + "\n")
        log("Added %d board(s) to %s" % (len(lines), a.companies))
    elif lines:
        log("Re-run with --add to append these to %s" % a.companies)


def cmd_doctor(a):
    """Health-check every source, every configured board, and the database."""
    a.max_pages, a.source_timeout, a.query_terms = 1, 45, []
    checks = [(n, S.FEEDS[n][0], None) for n in S.FEEDS]
    checks += [("%s:%s" % (k, t), S.BOARDS[k][0], t) for k, t in load_companies(a.companies)]
    log("Checking %d sources…" % len(checks))
    bad = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = []
        for key, fn, tok in checks:
            ctx = S.Ctx(a, key)
            if tok is None:
                gen = lambda f=fn, c=ctx: (x for i, x in zip(range(25), f(c)))
            else:
                gen = lambda f=fn, c=ctx, t=tok: (x for i, x in zip(range(25), f(c, t)))
            futs.append(ex.submit(_run_source, key, gen, ctx))
        for fut in as_completed(futs):
            r = fut.result()
            if r["status"] == "failed" or (r["status"] == "ok" and not r["rows"]):
                bad.append(r["key"])
            mark = "✓" if r["rows"] else "–" if r["status"] == "skipped" else "✗"
            print("  %s %-34s %5.1fs  %s" % (mark, r["key"], r["seconds"],
                                            r["error"] or "; ".join(r["notes"]) or "%d+ jobs" % len(r["rows"])))
    if os.path.exists(a.db):
        ok = sqlite3.connect(a.db).execute("PRAGMA integrity_check").fetchone()[0]
        print("\nDatabase %s: %s" % (os.path.basename(a.db), ok))
    if bad:
        print("\nNeeds attention: %s" % ", ".join(sorted(bad)))
        print("A board returning 0 jobs or HTTP 404 usually means the company changed its token or ATS — try `discover`.")


def cmd_serve(a):
    from . import server
    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.token:
        sys.exit("Refusing to listen on %s without --token (anyone on your network could use the API)." % a.host)
    server.serve(a.db, a.companies, a.host, a.port, a.token, a.open, a.verbose)


def cmd_mcp(a):
    from . import mcp_server
    mcp_server.run(a.db, a.companies)


def cmd_fetch_postings(a):
    from .api import Service
    svc = Service(a.db)
    r = svc.fetch_postings(a.min_match, a.limit, a.refetch)
    if not r.get("started"):
        sys.exit(r.get("reason"))
    log("Fetching posting pages for %d of your best matches (robots.txt is respected)…" % r["jobs"])
    while svc.collect_state["running"]:
        time.sleep(1)
    log("\n".join(svc.collect_state["log"][1:]))


def cmd_reclassify(a):
    """Re-run the title/category classifiers on stored jobs (after editing taxonomy.py) — no re-download."""
    store = Store(a.db)
    store.ensure_columns(["contact_emails", "apply_emails", "email_purposes", "contact_names", "application_method",
                          "apply_instructions", "company_website", "company_domain", "company_linkedin", "links_apply",
                          "links_form", "links_social", "links_other", "all_links", "email_count", "link_count"])
    rows = store.con.execute("SELECT job_id, title, source_categories, tags, department, description, years_experience_min, "
                             "seniority, seniority_basis, job_function, job_subfunction, url, apply_url, company, "
                             "all_links, company_website FROM jobs").fetchall()
    from .extract import extract as extract_contacts
    n = 0
    for r in rows:
        cats = [c for c in ((r["source_categories"] or "") + "; " + (r["tags"] or "")).split("; ") if c]
        f, sub, fb = E.classify_function(r["title"] or "", cats, r["department"] or "", r["description"] or "")
        upd = {"job_function": f, "job_subfunction": sub, "function_basis": fb}
        if r["seniority_basis"] in ("title", "years", "default"):  # source-provided levels aren't stored raw; keep them
            yrs = r["years_experience_min"]
            sen, sb = E.classify_seniority(r["title"] or "", "", int(yrs) if yrs not in (None, "") else None)
            upd.update(seniority=sen, seniority_basis=sb, seniority_rank=E.SENIORITY_RANK.get(sen, ""),
                       is_people_manager="TRUE" if sen in ("Manager", "Director", "VP", "C-Level") else "FALSE")
        # contacts from the stored text (link targets hidden behind anchor text need a re-collect; keep those if present)
        c = extract_contacts("", r["description"] or "", r["url"] or "", r["apply_url"] or "", r["company"] or "")
        if r["all_links"]:
            c = {k: v for k, v in c.items() if not k.startswith("links_") and k not in ("all_links", "link_count")}
        if r["company_website"] and not c.get("company_website"):
            c.pop("company_website", None)
            c.pop("company_domain", None)
        upd.update(c)
        if (f, sub) != (r["job_function"], r["job_subfunction"]) or upd.get("seniority", r["seniority"]) != r["seniority"]:
            n += 1
        store.con.execute("UPDATE jobs SET %s WHERE job_id=?" % ", ".join('"%s"=?' % k for k in upd),
                          list(upd.values()) + [r["job_id"]])
    store.con.commit()
    _reindex(store)
    log("Reclassified %d jobs (%d changed category or level)." % (len(rows), n))


def cmd_sources(a):
    print("Feeds (whole-market):")
    for n, (_, desc) in S.FEEDS.items():
        print("  %-16s %s" % (n, desc))
    print("\nCompany boards (add to companies.txt as kind:token | Name | Industry):")
    for n, (_, desc) in S.BOARDS.items():
        print("  %-16s %s" % (n, desc))
    print("\nBoards configured: %d" % len(load_companies(a.companies)))


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="jobintake", description="Collect, classify and export job postings.")
    p.add_argument("-v", "--verbose", action="store_true", help="show retries and per-record errors")
    sub = p.add_subparsers(dest="cmd")

    def filters(sp):
        g = sp.add_argument_group("filters")
        g.add_argument("-q", "--query", default="", help="comma-separated keywords; keep jobs matching ANY")
        g.add_argument("--title", help="regex the title must match")
        g.add_argument("--company", help="substring of company name")
        g.add_argument("--function", help="e.g. Engineering, Data, Sales, Design")
        g.add_argument("--seniority", help="e.g. Senior, Entry, Director")
        g.add_argument("--remote-only", action="store_true")
        g.add_argument("--country", help="substring match on country/location")
        g.add_argument("--source", help="only jobs listed on this source")
        g.add_argument("--min-salary", type=float, help="min annual USD (compares salary_max)")
        g.add_argument("--has-salary", action="store_true", help="only jobs with pay data")
        g.add_argument("--max-age", type=int, help="only jobs posted within N days")
        g.add_argument("--min-quality", type=int, help="only jobs with data_quality_score ≥ N (0-100)")

    def output(sp, default="jobs.csv"):
        sp.add_argument("-o", "--output", default=default, help=".csv or .jsonl")
        sp.add_argument("--lite", action="store_true", help="drop the long description column")
        sp.add_argument("--include-closed", action="store_true", help="also export jobs no longer listed")

    def fetch(sp):
        g = sp.add_argument_group("collection")
        g.add_argument("--sources", help="comma list (default: all). See `sources`.")
        g.add_argument("--exclude", help="comma list of sources to skip")
        g.add_argument("--companies", default=DEFAULT_COMPANIES)
        g.add_argument("--max-pages", type=int, default=5, help="depth for paginated feeds (default 5)")
        g.add_argument("--workers", type=int, default=10)
        g.add_argument("--source-timeout", type=int, default=300, help="seconds per source before it stops paging")
        g.add_argument("--detail-limit", type=int, default=150, help="SmartRecruiters: postings per company to fetch full text for")

    def ai(sp, flag=True):
        g = sp.add_argument_group("AI enrichment (optional, uses the Claude API)")
        if flag:
            g.add_argument("--ai", action="store_true", help="also run AI classification after collecting")
        g.add_argument("--ai-model", default="claude-opus-5-5")
        g.add_argument("--ai-limit", type=int, default=50, help="max jobs to classify per run (newest first)")
        g.add_argument("--ai-max-cost", type=float, default=5.0, help="skip if the estimate exceeds this many USD")
        g.add_argument("--ai-workers", type=int, default=4)

    def db(sp):
        sp.add_argument("--db", default=DEFAULT_DB, help="SQLite file (accumulates across runs)")

    s = sub.add_parser("run", help="collect + export (the everyday command)")
    db(s); filters(s); output(s); fetch(s); ai(s); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("collect", help="fetch + classify into the database")
    db(s); filters(s); fetch(s); s.set_defaults(fn=cmd_collect)
    s = sub.add_parser("export", help="database → CSV/JSONL")
    db(s); filters(s); output(s); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("ai", help="AI-classify jobs already in the database")
    db(s); filters(s); ai(s, flag=False); s.set_defaults(fn=cmd_ai)
    s = sub.add_parser("stats", help="category breakdowns")
    db(s); filters(s); s.add_argument("--include-closed", action="store_true"); s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("import", help="classify your own CSV/JSON/JSONL of jobs")
    db(s); s.add_argument("file"); s.add_argument("-o", "--output"); s.add_argument("--lite", action="store_true")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("history", help="recent collection runs")
    db(s); s.add_argument("-n", type=int, default=10); s.add_argument("--verbose-sources", action="store_true")
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("changes", help="what changed (new closures, pay/title edits) since the last run")
    db(s); s.add_argument("--since", help="ISO date, e.g. 2026-10-01"); s.add_argument("-n", type=int, default=40)
    s.add_argument("-o", "--output"); s.set_defaults(fn=cmd_changes)
    s = sub.add_parser("discover", help="find a company's job board and add it to companies.txt")
    s.add_argument("names", nargs="*", help='company names, e.g. "Hugging Face" Figma')
    s.add_argument("--file", help="text file with one company name per line")
    s.add_argument("--add", action="store_true", help="append found boards to companies.txt")
    s.add_argument("--companies", default=DEFAULT_COMPANIES); s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("doctor", help="health-check every source, board and the database")
    db(s); s.add_argument("--companies", default=DEFAULT_COMPANIES); s.add_argument("--workers", type=int, default=10)
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("serve", help="start the web app + REST API (http://localhost:8780)")
    db(s); s.add_argument("--companies", default=DEFAULT_COMPANIES)
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other devices (set --token!)")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--token", default=os.environ.get("JOBINTAKE_TOKEN"), help="require this bearer token on /api")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("mcp", help="run as an MCP server on stdio (for Claude and other AI clients)")
    db(s); s.add_argument("--companies", default=DEFAULT_COMPANIES); s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("fetch-postings", help="fetch posting pages for your best matches (adds emails, links, full text)")
    db(s); s.add_argument("--min-match", type=int, default=70); s.add_argument("--limit", type=int, default=50)
    s.add_argument("--refetch", action="store_true"); s.set_defaults(fn=cmd_fetch_postings)
    s = sub.add_parser("reclassify", help="re-run classification on stored jobs (after editing taxonomy.py)")
    db(s); s.set_defaults(fn=cmd_reclassify)
    s = sub.add_parser("sources", help="list sources")
    s.add_argument("--companies", default=DEFAULT_COMPANIES); s.set_defaults(fn=cmd_sources)

    return p


def prepare(a):
    """Normalise parsed args (shared by the CLI, the web server and the MCP server)."""
    util.VERBOSE = getattr(a, "verbose", False)
    a.query_terms = [t.strip().lower() for t in (getattr(a, "query", "") or "").split(",") if t.strip()]
    for k in ("title", "function", "seniority", "remote_only", "country", "min_salary", "has_salary", "max_age"):
        if not hasattr(a, k):
            setattr(a, k, None)
    if hasattr(a, "companies"):
        load_companies(a.companies)
    return a


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return
    prepare(a)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        log("Interrupted.")
        sys.exit(130)
    if STRAGGLERS:
        # Everything is saved; don't let a stuck network thread keep the process alive.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
