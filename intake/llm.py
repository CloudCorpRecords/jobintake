"""Optional AI enrichment with Claude.

Adds claude_* columns (summary, refined function/seniority/industry, must-have skills, red flags…).
Results are cached per job + content hash, so re-running never pays twice for an unchanged posting.

Requires:  pip install anthropic   and credentials (ANTHROPIC_API_KEY, or `ant auth login`).
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .taxonomy import FUNCTION_RULES, INDUSTRY_RULES, SENIORITY_RANK
from .util import log

DEFAULT_MODEL = "claude-opus-5-5"
# $ per million tokens (input, output) — used only for the up-front estimate
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
          "claude-fable-5-1": (10.0, 50.0)}
# Server-side fallback on safety declines; the "default" form is supported on these models
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
EST_IN_TOKENS, EST_OUT_TOKENS = 2200, 450
MAX_DESC_CHARS = 12000

FUNCTIONS = sorted({f for f, _, _ in FUNCTION_RULES} | {"Other"})
SUBFUNCTIONS = sorted({s for _, s, _ in FUNCTION_RULES} | {"Other"})
SENIORITIES = sorted(SENIORITY_RANK, key=SENIORITY_RANK.get)
INDUSTRIES = sorted({n for n, _ in INDUSTRY_RULES} | {"Consulting / Agency", "Telecom", "Other"})

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two plain sentences: what the job is and who it suits."},
        "job_function": {"type": "string", "enum": FUNCTIONS},
        "job_subfunction": {"type": "string", "enum": SUBFUNCTIONS},
        "seniority": {"type": "string", "enum": SENIORITIES},
        "industry": {"type": "string", "enum": INDUSTRIES},
        "workplace_type": {"type": "string", "enum": ["Remote", "Hybrid", "On-site", "Unknown"]},
        "remote_eligible_locations": {"type": "string", "description": "Where a remote hire may live, or '' if not remote/unstated."},
        "employment_type": {"type": "string", "enum": ["Full-time", "Part-time", "Contract", "Internship", "Temporary", "Unknown"]},
        "years_experience_min": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "must_have_skills": {"type": "array", "items": {"type": "string"}, "description": "Up to 10, explicitly required."},
        "nice_to_have_skills": {"type": "array", "items": {"type": "string"}, "description": "Up to 8."},
        "visa_sponsorship": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
        "salary_stated": {"type": "string", "description": "Pay exactly as stated in the posting, or '' if none."},
        "red_flags": {"type": "array", "items": {"type": "string"},
                      "description": "Concrete concerns a candidate should know (e.g. unpaid trial, commission-only, vague pay). Empty if none."},
        "team_or_product": {"type": "string", "description": "Team or product area the role sits in, or ''."},
    },
    "required": ["summary", "job_function", "job_subfunction", "seniority", "industry", "workplace_type",
                 "remote_eligible_locations", "employment_type", "years_experience_min", "must_have_skills",
                 "nice_to_have_skills", "visa_sponsorship", "salary_stated", "red_flags", "team_or_product"],
    "additionalProperties": False,
}

SYSTEM = """You classify job postings into a fixed schema for a job-market dataset.

Base every field on what the posting says. When the posting doesn't state something, use "Unknown", null, \
or an empty string/list rather than inferring it. For seniority, judge from responsibilities and required \
experience as well as the title; "Manager" means managing people, so an IC role titled "Product Manager" \
is usually Mid or Senior. For industry, classify the hiring company's business, not the role. Keep skill \
names short and canonical (e.g. "PostgreSQL", "React", "Salesforce")."""


def estimate_cost(n, model):
    pin, pout = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return n * (EST_IN_TOKENS * pin + EST_OUT_TOKENS * pout) / 1e6


def _prompt(job):
    desc = (job.get("description") or "")[:MAX_DESC_CHARS]
    return ("Title: %s\nCompany: %s\nLocation: %s\nListed workplace: %s\nSource categories: %s\n\nDescription:\n%s" % (
        job.get("title"), job.get("company"), job.get("location_raw"), job.get("workplace_type"),
        job.get("source_categories") or job.get("department") or "", desc))


def _client():
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The AI step needs the Anthropic SDK:  pip install anthropic")
    return anthropic, anthropic.Anthropic(max_retries=4)


def classify_one(client, anthropic, job, model):
    kw = dict(
        model=model,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": _prompt(job)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if model in FALLBACK_MODELS:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    else:
        resp = client.messages.create(**kw)
    if resp.stop_reason == "refusal":
        raise RuntimeError("declined (%s)" % getattr(resp.stop_details, "category", None))
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("output truncated")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), resp.usage


def enrich_jobs(store, jobs, model=DEFAULT_MODEL, workers=4):
    """Classify jobs that don't already have a cached result. Returns (done, cached, failed, usage dict)."""
    todo, cached = [], 0
    for j in jobs:
        if store.llm_get(j["job_id"], j["content_hash"]) is not None:
            cached += 1
        else:
            todo.append(j)
    if not todo:
        return 0, cached, 0, {}
    anthropic, client = _client()
    lock = threading.Lock()
    usage = {"input_tokens": 0, "output_tokens": 0}
    done = failed = 0

    def work(j):
        return j, classify_one(client, anthropic, j, model)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, j) for j in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                j, (data, u) = fut.result()
            except anthropic.AuthenticationError:
                for f in futs:
                    f.cancel()
                raise SystemExit("Anthropic API rejected the credentials — set ANTHROPIC_API_KEY or run `ant auth login`.")
            except (anthropic.APIError, RuntimeError, ValueError, StopIteration) as e:
                failed += 1
                log("  ! AI enrichment failed: %s" % str(e)[:120])
                continue
            with lock:
                store.llm_put(j["job_id"], j["content_hash"], model, data)
                usage["input_tokens"] += u.input_tokens
                usage["output_tokens"] += u.output_tokens
                done += 1
            if i % 25 == 0:
                log("  … %d/%d" % (i, len(todo)))
    return done, cached, failed, usage


def actual_cost(usage, model):
    pin, pout = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout) / 1e6
