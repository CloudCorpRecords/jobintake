# jobintake

Collects job postings from public job feeds and company career boards. It classifies each one into about 80 fields, tracks jobs over time (new, changed, closed), and exports to CSV. It runs on plain Python 3.8 or newer with nothing to install; the optional AI step needs one package.

## The app
Double-click **Job Intake.command** in this folder, or run `python3 jobintake.py serve --open`. It opens at **http://localhost:8780**.

| View | What it does |
|---|---|
| **For you** | Every job scored 0–100 against your profile, with the reasons. Includes filters, a detail panel, notes and an apply link |
| **All jobs** | Everything, newest first |
| **New** | Good matches from the latest refresh, plus updates to jobs you're tracking |
| **My pipeline** | Saved → Applied → Interviewing → Offer → Rejected; drag cards between columns |
| **Insights** | Pay ranges, the most-requested skills (and which you have), skills worth adding, who's hiring |
| **Changes** | Closed jobs, pay changes and title edits since the last refresh |
| **API & AI** | Copy-paste setup for Claude (MCP) and the REST API |

**Profile** (top right) drives the ranking: your skills, fields, level, location and minimum pay. **Refresh** fetches new jobs in the background (about 4 minutes). It also runs automatically every 12 hours while the app is open; you can change the interval in Profile.

Keyboard shortcuts: `/` search · `j`/`k` next/previous · `s` save · `a` applied · `x` hide · `o` open the posting · `Esc` close.

## Search
The search box (and the `q` parameter of the API and MCP server) understands a small query language:

| Type | Finds |
|---|---|
| `python django` | All of the words. Word forms match too ("engineer" also finds "engineering") |
| `"machine learning"` | The exact phrase |
| `react OR vue` | Either word |
| `-senior`, `-"sales engineer"` | Excludes a word or phrase |
| `k8s`, `ml`, `sf`, `swe`, … | Synonyms expand automatically (k8s → Kubernetes) |
| `title:staff` `company:stripe` `loc:berlin` `skill:rust` `email:recruiting` | Searches one part of the posting |
| `is:remote` `level:senior` `field:data` `type:contract` `source:greenhouse` `domain:stripe.com` | Filters |
| `pay:150k+` `posted:7d` `status:saved` | Minimum pay, posted within N days, your tracking status |
| `has:apply-email` `has:email` `has:form` `has:website` `has:salary` `has:visa` | Postings that have that information |

Results are ranked by a blend of text relevance and your profile match, with the matching passage highlighted. Click **?** in the search box for clickable examples.

## Contact info and links
Every posting is scanned for:
- **Emails:** including disguised ones like `jobs [at] acme [dot] io`. Each is labeled by purpose: *apply/recruiting*, *contact*, *accommodations*, *benefits* or *privacy*. `has:email` counts only the recruiting and contact ones, not the boilerplate.
- **How to apply:** by email, through a form, or on the company's job site, plus the posting's own instruction sentence.
- **Company:** its website and domain, its LinkedIn page, and any named contact ("Recruiter: Jane Smith").
- **Links:** every link in the posting, sorted into apply, form, careers, website, LinkedIn, GitHub, docs, calendar, reviews and so on. Tracking parameters are removed.

**Fetch full posting** (a button on each job, `fetch-postings` on the command line, or `fetch_posting` in MCP) downloads the job's own page for more detail: structured data such as pay, dates and company website, plus page-level emails and the full text. It only fetches pages a site's robots.txt allows, and only the posting URL already on record; it never crawls other pages. Everything comes from what the employer published; nothing is guessed or looked up elsewhere.

## For AI agents
- **MCP (Claude Code, Claude Desktop, other MCP clients):** `claude mcp add jobintake -- python3 ~/jobintake/jobintake.py mcp`
  This gives 12 tools: `search_jobs`, `get_job`, `fetch_posting`, `update_job_status`, `get_pipeline`, `get_new_matches`, `get_profile`, `update_profile`, `get_insights`, `get_changes`, `refresh_jobs`, `refresh_status`. It doesn't need the web app to be running.
- **REST API:** available while the app runs. Docs are at http://localhost:8780/docs, and the OpenAPI spec is at `/api/openapi.json`.
- **Security:** the server only listens on this computer. Write requests must be JSON, which stops other websites from sending them. To allow other devices on your network, use `--host 0.0.0.0 --token <secret>`.

## Everyday use (command line)
```
python3 jobintake.py run          # collect everything → jobs.csv + jobs_lite.csv
python3 jobintake.py changes      # what changed since the last run (closed jobs, pay/title edits)
python3 jobintake.py stats        # breakdowns by function, seniority, skills, pay…
```
`jobs_lite.csv` is the same data without the long description column, so it opens fast in Excel or Google Sheets.

## Commands
| Command | What it does |
|---|---|
| `run` | Collect, classify and export in one step |
| `collect` / `export` | The same two halves, run separately |
| `changes` | Field-level changes since the last run, or since `--since 2026-10-01` |
| `history` | Recent runs, with per-source status (`--verbose-sources`) |
| `stats` | Category breakdowns |
| `discover "Company A" "Company B" --add` | Finds which job board each company uses and adds it to `companies.txt` |
| `doctor` | Health-checks every source and board, plus the database |
| `import file.csv -o out.csv` | Classifies your own CSV, JSON or JSONL of jobs |
| `ai` | Optional AI classification with Claude (see below) |
| `serve` | Starts the web app and REST API |
| `mcp` | Runs the MCP server for AI clients |
| `reclassify` | Re-applies the category and contact rules to stored jobs, after you edit `intake/taxonomy.py` |
| `fetch-postings --min-match 70 --limit 50` | Fetches the posting pages of your best matches for more emails, links and pay |
| `sources` | Lists every source |

## Filters
These work with `run`, `export`, `stats` and `ai`:
```
-q "python,data"   --title "engineer|developer"   --company Stripe   --function Engineering
--seniority Senior   --remote-only   --country Germany   --source greenhouse
--min-salary 150000 (annual USD)   --has-salary   --max-age 14   --min-quality 60
--include-closed   --lite   -o file.jsonl
```

## Sources
- **Feeds:** remotive, remoteok, arbeitnow, himalayas, jobicy, themuse, workingnomads, weworkremotely, workable_search (search across every company that uses Workable), hackernews (the latest "Who is hiring?" thread)
- **Optional feeds with free keys:** adzuna (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ADZUNA_COUNTRY`), usajobs (`USAJOBS_API_KEY`, `USAJOBS_EMAIL`)
- **Company boards** (listed in `companies.txt` as `kind:token | Name | Industry`): greenhouse, lever, ashby, workable, smartrecruiters. Use `discover` to find tokens automatically.

## How it stays reliable
- **Failures stay contained:** a failing source never stops the run, and a source that fails partway keeps what it already fetched.
- **Polite HTTP:** retries with backoff, honors the site's `Retry-After`, spaces out requests to each site, and reports clearly when a site blocks it for longer.
- **Time limit per source:** `--source-timeout` (default 300 s), enforced even during retries.
- **Ctrl-C:** stops within seconds and saves everything collected so far.
- **Closed-job detection:** a job is marked `closed` only when every source listing it returned its full list and the job wasn't in it. Page-capped feeds, failed sources and filtered runs never close jobs.
- **Duplicates:** the same job found on several sources becomes one row. `also_listed_on` shows where it appeared, and the richer version wins (structured pay beats pay read from text; a longer description beats a shorter one).
- **Safe output:** CSV files are written atomically and protected against spreadsheet formula injection. The database (`jobs.db`) upgrades its own schema and can be queried with any SQLite tool.
- **Tests:** `python3 -m unittest discover tests` runs offline tests of the parsers, deduplication, storage and export.

## AI classification (optional)
```
pip install anthropic              # plus ANTHROPIC_API_KEY, or `ant auth login`
python3 jobintake.py ai --function Engineering --ai-limit 100
python3 jobintake.py run --ai      # collect, then classify the newest jobs
```
This adds `claude_*` columns: a summary, refined function, seniority and industry, must-have and nice-to-have skills, visa status, pay as stated, and red flags.
- **Model:** Claude Opus 5.5 by default, about 1.5¢ per job. `--ai-model claude-haiku-5-5` costs a small fraction of that.
- **Spending caps:** each run classifies at most `--ai-limit` jobs (default 50). It shows a cost estimate first and skips the run if the estimate is over `--ai-max-cost` (default $5).
- **Caching:** results are cached per posting, so an unchanged job is never billed twice.
- **Fallback:** if Claude's safety checks decline a posting, the API retries it on a fallback model automatically.

## What each row gets
- **Identity:** id, open/closed status, title and normalized title, company with legal suffixes removed
- **Classification:** function, subfunction, seniority and rank, people-manager flag, employment type, industry
- **Location:** workplace type (remote / hybrid / on-site), remote scope, city, state, country, region, timezones
- **Pay:** raw min/max, currency and period, plus annual USD min/mid/max and where the pay came from
- **Requirements:** years of experience, degree, visa sponsorship, clearance, travel, spoken languages
- **Skills:** languages, frameworks, cloud/DevOps, data stores, AI/ML, tools
- **Benefits:** 12 flags
- **Quality:** `data_quality_score` (0–100), `classification_confidence`, and `*_basis` columns showing how each label was decided
- **Dates and links:** posted, age, first/last seen, closed_at, times seen, sources, links
