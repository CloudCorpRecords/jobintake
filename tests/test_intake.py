"""Offline tests — no network. Run:  python3 -m unittest discover tests"""

import csv
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intake import enrich as E  # noqa: E402
from intake import export as X  # noqa: E402
from intake import sources as S  # noqa: E402
from intake.store import Store  # noqa: E402
from intake.util import html_to_text, parse_date, raw_job  # noqa: E402


def job(**kw):
    base = dict(source="test", source_key="test", source_job_id="1", title="Senior Backend Engineer",
                company="Acme, Inc.", url="https://x/1", location_raw="Austin, TX",
                description="We need 5+ years of experience with Python and PostgreSQL. Hybrid, 3 days a week in office. "
                            "Salary $150,000 - $180,000 per year. Bachelor's degree required. 401(k) matching.")
    base.update(kw)
    return raw_job(**base)


class Salary(unittest.TestCase):
    cases = {
        "$150,000 - $210,000 per year": (150000, 210000, "USD", "year"),
        "$120k–$150k": (120000, 150000, "USD", "year"),
        "Gehalt: 60.000 € – 75.000 €": (60000, 75000, "EUR", "year"),
        "100k-160k GBP + equity": (100000, 160000, "GBP", "year"),
        "$45/hr": (45, 45, "USD", "hour"),
        "4.500 - 5.500 EUR per month": (4500, 5500, "EUR", "month"),
        "€75k–80k": (75000, 80000, "EUR", "year"),
    }

    def test_ranges(self):
        for text, want in self.cases.items():
            with self.subTest(text=text):
                got = E.parse_salary_text(text)
                self.assertIsNotNone(got)
                self.assertEqual((round(got[0]), round(got[1]), got[2], got[3]), want)

    def test_rejects_non_salary_money(self):
        for text in ["We raised $20M Series B", "up to $500 stipend", "a $5 coffee", "founded in 2015 with 300 staff"]:
            with self.subTest(text=text):
                self.assertIsNone(E.parse_salary_text(text))

    def test_prefers_range_over_single_figure(self):
        got = E.parse_salary_text("$1,000 signing bonus. Base pay $130,000 - $160,000.")
        self.assertEqual(got[:2], (130000, 160000))


class Classification(unittest.TestCase):
    def test_function(self):
        cases = {
            "Staff Machine Learning Engineer": ("Engineering", "Machine Learning / AI"),
            "Senior Data Engineer": ("Data", "Data Engineering"),
            "Product Manager, Payments": ("Product", "Product Management"),
            "Account Executive, Mid-Market": ("Sales", "Account Executive / Closing"),
            "Registered Nurse (RN)": ("Healthcare", "Clinical / Medical"),
            "Werkstudent Marketing (m/w/d)": ("Marketing", "Product / Brand Marketing"),
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(E.classify_function(title, [], "", "")[:2], want)

    def test_seniority(self):
        cases = {"Senior Software Engineer": "Senior", "Product Manager": "Mid", "Engineering Manager": "Manager",
                 "Staff Engineer": "Staff", "VP of Sales": "VP", "Software Engineering Intern": "Intern",
                 "Junior Designer": "Entry / Junior", "Head of Growth": "Director"}
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(E.classify_seniority(title, "", None)[0], want)

    def test_seniority_from_years_when_title_silent(self):
        self.assertEqual(E.classify_seniority("Backend Engineer", "", 6), ("Senior", "years"))

    def test_skills(self):
        sk = E.skill_hits("Python/Django, Go, C# and .NET, React Native, k8s on AWS, PyTorch, Excel. Go to the store.")
        self.assertIn("Python", sk["programming_languages"])
        self.assertIn("Go", sk["programming_languages"])
        self.assertIn("C#", sk["programming_languages"])
        self.assertIn(".NET", sk["frameworks"])
        self.assertIn("Django", sk["frameworks"])
        self.assertIn("Kubernetes", sk["cloud_devops"])
        self.assertIn("PyTorch", sk["ai_ml"])
        self.assertIn("Excel", sk["tools"])

    def test_no_false_go(self):
        self.assertNotIn("Go", E.skill_hits("Go to market and go above and beyond.")["programming_languages"])

    def test_location(self):
        self.assertEqual(E.parse_location("Austin, TX", "", "")[:3], ("Austin", "Texas", "United States"))
        self.assertEqual(E.parse_location("London", "", "")[2], "United Kingdom")
        self.assertEqual(E.parse_location("Remote - Europe", "", "")[3], "Europe")
        self.assertEqual(E.parse_location("", "DE", "")[2], "Germany")

    def test_visa_and_degree(self):
        self.assertEqual(E.detect_visa("We are unable to sponsor visas for this role."), "No")
        self.assertEqual(E.detect_visa("Visa sponsorship is available."), "Yes")
        self.assertEqual(E.detect_degree("Bachelor's degree in CS or equivalent experience"), ("Bachelor's", "preferred"))
        self.assertEqual(E.detect_degree("You have managed large teams")[0], "")  # 'managed' is not a GED

    def test_company_cleanup(self):
        for raw, want in [("Acme, Inc.", "Acme"), ("Foo GmbH", "Foo"), ("Bar Technologies Ltd", "Bar"), ("Inc", "Inc")]:
            self.assertEqual(E.clean_company(raw), want)


class Enrich(unittest.TestCase):
    def test_full_record(self):
        o = E.enrich(job())
        self.assertEqual(o["company"], "Acme")
        self.assertEqual(o["job_function"], "Engineering")
        self.assertEqual(o["seniority"], "Senior")
        self.assertEqual(o["workplace_type"], "Hybrid")
        self.assertEqual(o["salary_mid_usd_annual"], 165000)
        self.assertEqual(o["years_experience_min"], 5)
        self.assertTrue(o["benefit_retirement"])
        self.assertEqual(o["classification_confidence"], "high")
        self.assertGreaterEqual(o["data_quality_score"], 60)

    def test_same_job_different_sources_dedupes(self):
        a = E.enrich(job(source="greenhouse", company="Acme Inc"))
        b = E.enrich(job(source="workable", company="ACME, Inc.", title="Senior Backend Engineer (Remote)"))
        self.assertEqual(a["job_id"], b["job_id"])

    def test_distinct_roles_stay_distinct(self):
        pairs = [("Member of Technical Staff (ML)", "Member of Technical Staff (Applied AI)"),
                 ("Account Executive, Enterprise - Hunter", "Account Executive, Enterprise (Grower)"),
                 ("Software Engineer, Backend", "Software Engineer, Frontend")]
        for a, b in pairs:
            with self.subTest(a=a):
                self.assertNotEqual(E.enrich(job(title=a))["job_id"], E.enrich(job(title=b))["job_id"])

    def test_workplace_and_gender_tags_ignored_for_identity(self):
        for variant in ["Senior Backend Engineer (Remote)", "Senior Backend Engineer - Remote", "Senior Backend Engineer (m/w/d)",
                        "Senior Backend Engineer (Remote, Full-time)", "Senior Backend Engineer*"]:
            with self.subTest(v=variant):
                self.assertEqual(E.enrich(job(title=variant))["job_id"], E.enrich(job())["job_id"])

    def test_validate(self):
        self.assertIsNone(E.validate(job()))
        self.assertEqual(E.validate(job(title="")), "missing title")
        self.assertEqual(E.validate(job(company=" ")), "missing company")
        self.assertEqual(E.validate(job(url="", apply_url="")), "missing url")


class Util(unittest.TestCase):
    def test_html(self):
        self.assertEqual(html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;"), "Hi & bye")

    def test_dates(self):
        self.assertEqual(parse_date(1791448888), "2026-10-08T08:41:28+00:00")
        self.assertEqual(parse_date("2026-04-07T17:12:35.753+00:00"), "2026-04-07T17:12:35+00:00")
        self.assertEqual(parse_date("Thu, 08 Oct 2026 07:54:30 +0000"), "2026-10-08T07:54:30+00:00")
        self.assertEqual(parse_date("garbage"), "")


class Storage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "t.db")

    def test_upsert_merge_close_reopen_and_history(self):
        st = Store(self.db)
        j1 = E.enrich(job(source_job_id="a"))
        j2 = E.enrich(job(source_job_id="b", title="Designer", description="Figma."))
        t0 = "2000-01-01T00:00:00+00:00"
        self.assertEqual(st.upsert([j1, j2], t0), (2, 0))

        # run 2: board returns only j1, with a pay change → j2 closes, j1 change logged
        run2 = "2100-01-01T00:00:00+00:00"
        j1b = E.enrich(job(source_job_id="a", description=job()["description"].replace("$180,000", "$200,000")))
        st.con.execute("UPDATE listings SET last_seen=?", (t0,))
        st.upsert([j1b], run2)
        self.assertEqual(st.close_missing(["test"], run2), 1)
        self.assertEqual(st.get(j2["job_id"])["status"], "closed")
        fields = {c["field"] for c in st.changes_since(t0)}
        self.assertIn("salary_max_usd_annual", fields)
        self.assertIn("status", fields)

        # run 3: j2 is back → reopened
        st.upsert([j2], "2200-01-01T00:00:00+00:00")
        self.assertEqual(st.get(j2["job_id"])["status"], "open")

    def test_duplicate_listings_in_one_run_do_not_flip_flop(self):
        st = Store(self.db)
        a = E.enrich(job(source_job_id="a"))
        b = E.enrich(job(source_job_id="b", description=job()["description"].replace("Hybrid", "Fully remote")))
        st.upsert([a, b], "2000-01-01T00:00:00+00:00")
        st.upsert([a, b], "2100-01-01T00:00:00+00:00")
        self.assertEqual(st.changes_since("1999"), [])
        self.assertEqual(st.get(a["job_id"])["times_seen"], 2)

    def test_listing_without_pay_never_erases_pay(self):
        st = Store(self.db)
        paid = E.enrich(job(source_job_id="a"))
        unpaid = E.enrich(job(source_job_id="b", description="5+ years of Python. Hybrid."))
        st.upsert([paid, unpaid], "2000-01-01T00:00:00+00:00")
        st.upsert([unpaid, paid], "2100-01-01T00:00:00+00:00")  # order flips between runs
        self.assertEqual(st.get(paid["job_id"])["salary_mid_usd_annual"], 165000)
        self.assertEqual(st.changes_since("1999"), [])

    def test_incomplete_source_never_closes(self):
        st = Store(self.db)
        j = E.enrich(job())
        st.upsert([j], "2000-01-01T00:00:00+00:00")
        st.con.execute("UPDATE listings SET last_seen='2000-01-01T00:00:00+00:00'")
        self.assertEqual(st.close_missing([], "2100-01-01T00:00:00+00:00"), 0)
        self.assertEqual(st.close_missing(["other-source"], "2100-01-01T00:00:00+00:00"), 0)

    def test_migrates_v1_database(self):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, data TEXT, first_seen TEXT, last_seen TEXT, times_seen INTEGER, sources TEXT)")
        d = E.enrich(job())
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (d["job_id"], json.dumps(d), "2026-01-01", "2026-01-02", 3, "greenhouse"))
        con.commit()
        con.close()
        st = Store(self.db)
        row = st.get(d["job_id"])
        self.assertEqual(row["times_seen"], 3)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["title"], "Senior Backend Engineer")


class Export(unittest.TestCase):
    def test_csv_injection_guard_and_atomic_write(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "o.csv")
        rows = [{"job_id": "1", "title": "=HYPERLINK(\"x\")", "salary_min": -5.0, "description": "+1 call"}]
        X.write(rows, path)
        with open(path, encoding="utf-8-sig") as f:
            r = next(csv.DictReader(f))
        self.assertEqual(r["title"], "'=HYPERLINK(\"x\")")
        self.assertEqual(r["salary_min"], "-5")
        self.assertEqual(r["description"], "'+1 call")
        self.assertEqual([p for p in os.listdir(d) if p.startswith(".tmp")], [])


class HackerNewsParsing(unittest.TestCase):
    def test_header_parsing(self):
        fake = {"hits": [{"title": "Ask HN: Who is hiring? (October 2026)", "objectID": "1"}]}
        item = {"children": [
            {"id": 2, "author": "a", "created_at": "2026-10-01T15:00:00Z",
             "text": "Acme (YC W20) | Senior Backend Engineer | REMOTE (US) | Full-time | $170k to $230k<p>We build things."},
            {"id": 3, "author": "b", "created_at": "2026-10-01T15:00:00Z",
             "text": "Shepherd | ONSITE | San Francisco, CA<p>Many roles."},
        ]}
        orig = S.http.get_json
        S.http.get_json = lambda url, **kw: fake if "search_by_date" in url else item
        try:
            class O:
                max_pages, query_terms, source_timeout = 1, [], 30
            rows = list(S.hackernews(S.Ctx(O(), "hackernews")))
        finally:
            S.http.get_json = orig
        self.assertEqual(rows[0]["company"], "Acme")
        self.assertEqual(rows[0]["title"], "Senior Backend Engineer")
        self.assertIn("REMOTE", rows[0]["location_raw"])
        self.assertEqual(rows[1]["title"], "Multiple roles")
        o = E.enrich(rows[0])
        self.assertEqual((o["salary_min"], o["salary_max"]), (170000, 230000))


if __name__ == "__main__":
    unittest.main()


class Matching(unittest.TestCase):
    def setUp(self):
        from intake import profile as P
        self.P = P

    def base(self, **kw):
        j = E.enrich(job(**kw))
        j["age_days"] = 1
        return j

    def test_profile_drives_score(self):
        m = self.P.Matcher({"functions": ["Engineering"], "skills": ["Python", "PostgreSQL"], "home_city": "Austin",
                            "home_country": "United States", "min_salary": 140000})
        score, reasons = m.score(self.base())
        self.assertGreaterEqual(score, 85)
        texts = " ".join(t for _, t in reasons)
        self.assertIn("near you", texts)
        self.assertIn("Python", texts)

    def test_wrong_field_and_far_away_score_low(self):
        m = self.P.Matcher({"functions": ["Data"], "skills": ["Tableau"], "home_city": "Berlin", "home_country": "Germany"})
        self.assertLess(m.score(self.base())[0], 40)

    def test_remote_only_excludes_onsite(self):
        m = self.P.Matcher({"workplace": "remote_only"})
        self.assertEqual(m.excluded(self.base()), "not remote")
        self.assertIsNone(m.excluded(self.base(description="Fully remote role. Python.", location_raw="Remote")))

    def test_profile_normalisation(self):
        p = self.P.normalise({"skills": "Python, SQL ,", "min_salary": "150000", "needs_visa": "yes", "bogus": 1, "workplace": "moon"})
        self.assertEqual(p["skills"], ["Python", "SQL"])
        self.assertEqual(p["min_salary"], 150000)
        self.assertTrue(p["needs_visa"])
        self.assertEqual(p["workplace"], "remote_or_near")
        self.assertNotIn("bogus", p)


class WebApi(unittest.TestCase):
    """Starts the real HTTP server on a free port against a temp database."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from intake import server
        from intake.api import Service
        cls.dir = tempfile.mkdtemp()
        db = os.path.join(cls.dir, "w.db")
        st = Store(db)
        cls.job = E.enrich(job())
        st.upsert([cls.job, E.enrich(job(source_job_id="2", title="Data Scientist", description="Python, SQL. Remote."))],
                  "2000-01-01T00:00:00+00:00")
        server.Handler.svc, server.Handler.token = Service(db), None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, method="GET", body=None, ctype="application/json", headers=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_search_track_pipeline_profile(self):
        code, res = self.req("/api/jobs?q=backend")
        self.assertEqual(code, 200)
        self.assertEqual(res["total"], 1)
        self.assertIn("match", res["jobs"][0])
        jid = res["jobs"][0]["job_id"]
        self.assertEqual(self.req("/api/jobs/%s/track" % jid, "POST", {"status": "applied", "notes": "hi"})[0], 200)
        self.assertEqual(self.req("/api/pipeline")[1]["counts"]["applied"], 1)
        self.assertEqual(self.req("/api/jobs/%s" % jid)[1]["notes"], "hi")
        code, prof = self.req("/api/profile", "PATCH", {"skills": ["Python"]})
        self.assertEqual((code, prof["skills"]), (200, ["Python"]))
        self.assertEqual(self.req("/api/jobs/%s/track" % jid, "POST", {"status": "bogus"})[0], 400)

    def test_writes_need_json_and_local_host(self):
        self.assertEqual(self.req("/api/profile", "PATCH", {"x": 1}, ctype="text/plain")[0], 415)
        self.assertEqual(self.req("/api/meta", headers={"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.req("/api/jobs/ffffffffffffffff")[0], 404)

    def test_openapi(self):
        code, spec = self.req("/api/openapi.json")
        self.assertEqual(code, 200)
        self.assertIn("/api/jobs", spec["paths"])


class Extraction(unittest.TestCase):
    def test_emails_plain_obfuscated_and_purpose(self):
        from intake.extract import find_emails
        got = dict((e, p) for e, p, _ in find_emails(
            "Send your CV to jobs [at] acme [dot] io. Questions: hiring(at)foo.dev. "
            "Accommodations: accommodations@acme.io. logo@2x.png noreply@acme.io x\\u003ecareers@acme.io"))
        self.assertEqual(got.get("jobs@acme.io"), "apply")
        self.assertEqual(got.get("hiring@foo.dev"), "apply")
        self.assertEqual(got.get("accommodations@acme.io"), "accommodation")
        self.assertIn("careers@acme.io", got)
        self.assertNotIn("logo@2x.png", got)
        self.assertNotIn("noreply@acme.io", got)

    def test_english_at_is_not_an_email(self):
        from intake.extract import find_emails
        self.assertEqual(find_emails("We meet at the office at 9am at main dot street."), [])

    def test_links_website_and_method(self):
        from intake.extract import extract
        h = ('<a href="https://www.acme.com/about?utm_source=x">About</a> <a href="https://www.linkedin.com/company/acme">in</a> '
             '<a href="mailto:talent@acme.com">mail</a>')
        r = extract(h, "To apply, send your resume to talent@acme.com. Recruiter: Jane Smith.", "", "", "Acme")
        self.assertEqual(r["company_website"], "https://acme.com")
        self.assertEqual(r["company_linkedin"], "https://www.linkedin.com/company/acme")
        self.assertEqual(r["apply_emails"], "talent@acme.com")
        self.assertEqual(r["application_method"], "email")
        self.assertEqual(r["contact_names"], "Jane Smith")
        self.assertIn("talent@acme.com", r["apply_instructions"])
        self.assertNotIn("utm_source", r["links_other"])

    def test_company_hosted_posting_names_domain(self):
        from intake.extract import extract
        self.assertEqual(extract("", "Join us.", "https://stripe.com/jobs/search?gh_jid=1", "", "Stripe")["company_website"],
                         "https://stripe.com")
        self.assertEqual(extract("", "Join us.", "https://boards.greenhouse.io/x/jobs/1", "", "X")["company_website"], "")


class SearchEngine(unittest.TestCase):
    def test_parse(self):
        from intake.search import parse
        p = parse('"machine learning" python OR rust -senior title:staff is:remote pay:150k+ posted:7d has:email level:Senior')
        f = p["filters"]
        self.assertEqual(f["workplace"], ["Remote"])
        self.assertEqual(f["min_salary"], 150000)
        self.assertEqual(f["max_age"], 7)
        self.assertEqual(f["has"], ["email"])
        self.assertEqual(f["seniority"], ["Senior"])
        self.assertIn('"machine learning"', p["match"])
        self.assertIn("OR", p["match"])
        self.assertIn('NOT "senior"', p["match"])
        self.assertIn("title : (", p["match"])
        self.assertIn('"kubernetes"', parse("k8s")["match"])

    def test_fts_end_to_end(self):
        from intake import search as SE
        st = Store(os.path.join(tempfile.mkdtemp(), "s.db"))
        if not SE.available(st.con):
            self.skipTest("SQLite without FTS5")
        a = E.enrich(job(source_job_id="a", title="Staff Kubernetes Engineer", description="Run our Kubernetes fleet. Email jobs@acme.com"))
        b = E.enrich(job(source_job_id="b", title="Sales Manager", description="Sell things."))
        st.upsert([a, b], "2000-01-01T00:00:00+00:00")
        SE.rebuild(st.con)
        self.assertEqual(set(SE.run(st.con, SE.parse("k8s")["match"])), {a["job_id"]})
        self.assertEqual(set(SE.run(st.con, SE.parse("email:acme")["match"])), {a["job_id"]})
        sn = SE.snippets(st.con, SE.parse("kubernetes")["match"], [a["job_id"]])
        self.assertIn("\x02Kubernetes\x03", sn[a["job_id"]])


class Determinism(unittest.TestCase):
    def test_job_id_stable_across_processes(self):
        """job_ids must not depend on Python's per-process hash randomisation (set/dict-of-set iteration)."""
        import subprocess
        code = ("import sys; sys.path.insert(0, %r); from intake import enrich as E; from intake.util import raw_job; "
                "print(E.enrich(raw_job(source='t', title='Staff PM, Security', company='Databricks', url='u', "
                "location_raw='Seattle, Washington; Remote - San Francisco or New York', description='x'))['job_id'])"
                % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ids = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONHASHSEED=str(seed))).stdout.strip() for seed in (1, 2, 3, 42, 99)}
        self.assertEqual(len(ids), 1, ids)
