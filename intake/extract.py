"""Pull contact details and links out of a posting: emails (incl. obfuscated ones), every link (classified),
how to apply, the company website, and social/profile links.

Only information published in the posting itself is extracted — nothing is guessed or looked up elsewhere.
"""

import html as _html
import re
import urllib.parse

# --------------------------------------------------------------------------- emails
EMAIL_RX = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])")
# "jobs [at] acme [dot] com", "jobs(at)acme.com", "jobs at acme dot com", "jobs AT acme DOT io"
OBFUSCATED_RX = re.compile(
    r"(?<![\w.])([A-Za-z0-9][A-Za-z0-9._+-]{0,63})\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>|\s+at\s+|\s+AT\s+|\s*@\s*)\s*"
    r"([A-Za-z0-9-]+(?:(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\})\s*|\s+dot\s+|\s+DOT\s+|\.)[A-Za-z0-9-]+)+)",
)
DOT_RX = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\s+DOT\s+|\.)\s*", re.I)
JUNK_EMAIL = re.compile(r"(example\.(com|org)|domain\.com|email\.com|yourname|your\.?email|test@|sentry|@\d+x\.|\.(png|jpe?g|gif|svg|webp)$|"
                        r"wixpress|noreply|no-reply|donotreply|do-not-reply|mailer-daemon)", re.I)
TLDS_OK = re.compile(r"\.(com|org|net|io|co|ai|dev|app|tech|uk|de|fr|es|it|nl|se|no|dk|fi|ch|at|be|ie|pl|pt|ca|us|au|nz|in|sg|jp|"
                     r"br|mx|ar|cl|il|ae|za|eu|me|xyz|so|gg|edu|gov|health|jobs|careers|cloud|ventures|studio|inc|ly|to|is|vc|"
                     r"[a-z]{2})$", re.I)
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "me.com",
             "proton.me", "protonmail.com", "aol.com", "gmx.de", "gmx.net", "web.de", "yandex.ru", "mail.com", "zoho.com"}

PURPOSE_RULES = [
    ("accommodation", r"accommodat|accessib|disabilit|ada@|reasonable"),
    ("privacy", r"privacy|gdpr|dpo@|data-?protection|dataprotection"),
    ("benefits", r"benefit"),
    ("diversity", r"belonging|diversity|inclusion|^dei[@._-]|equity@"),
    ("security", r"security@|abuse@|phish|fraud"),
    ("apply", r"^(jobs|job|careers?|apply|applications?|hiring|recruit\w*|talent\w*|hr|people|join|work|resumes?|cv|"
              r"bewerbung\w*|karriere|empleo|emploi|recrutement)[._+-]?\w*@"),
]
APPLY_CONTEXT = re.compile(r"\b(send|email|e-mail|mail|submit|forward|reach out|apply|contact|write to|drop us|cv|resume|résumé|portfolio|"
                           r"bewerbung|questions?)\b", re.I)


def _email_purpose(addr, context):
    a = addr.lower()
    for name, rx in PURPOSE_RULES:
        if re.search(rx, a):
            return name
    if APPLY_CONTEXT.search(context):
        return "apply"
    return "contact"


def find_emails(text):
    """[(email, purpose, context_snippet)] — de-duplicated, junk filtered, obfuscations decoded."""
    found = {}
    # pages embed JSON with escapes like \u003e (">") glued onto addresses; turn them into spaces first
    t = re.sub(r"\\?u00[0-9a-fA-F]{2}", " ", text or "")

    def add(addr, start, end):
        addr = addr.strip(".,;:").lower()
        if len(addr) > 120 or JUNK_EMAIL.search(addr) or not TLDS_OK.search(addr):
            return
        local, _, dom = addr.partition("@")
        if not local or "." not in dom or dom.startswith("-"):
            return
        ctx = t[max(0, start - 140):min(len(t), end + 60)].replace("\n", " ")
        if addr not in found:
            found[addr] = (_email_purpose(addr, ctx), re.sub(r"\s+", " ", ctx).strip())

    for m in EMAIL_RX.finditer(t):
        add(m.group(1), m.start(), m.end())
    for m in OBFUSCATED_RX.finditer(t):
        raw = m.group(0)
        if "@" in raw and EMAIL_RX.search(raw):
            continue  # a plain email, already handled
        if not re.search(r"\b(at|AT)\b|\[|\(|\{|<", raw):
            continue
        dom = DOT_RX.sub(".", m.group(2))
        if not re.search(r"\b(dot|DOT)\b|\.", m.group(2)) or dom.count(".") < 1:
            continue
        # "at" is an English word — require the result to look like a real domain and an email-ish context
        if " at " in raw.lower() and not re.search(r"(email|e-mail|mail|contact|reach|send|write)", t[max(0, m.start() - 80):m.start()], re.I):
            continue
        add("%s@%s" % (m.group(1), dom), m.start(), m.end())
    return [(e, p, c) for e, (p, c) in found.items()]


# --------------------------------------------------------------------------- links
HREF_RX = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(["'])(.*?)\1[^>]*>(.*?)</a>""", re.I | re.S)
URL_RX = re.compile(r"""(?<![@\w/])((?:https?://|www\.)[^\s<>"'()\[\]{}|\\^`]+[^\s<>"'()\[\]{}|\\^`.,;:!?])""", re.I)
TAG_RX = re.compile(r"<[^>]+>")

ATS_DOMAINS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "myworkdayjobs.com",
               "workday.com", "icims.com", "jobvite.com", "bamboohr.com", "recruitee.com", "personio.de", "personio.com",
               "teamtailor.com", "breezy.hr", "jazzhr.com", "applytojob.com", "rippling.com", "pinpointhq.com",
               "dover.com", "wellfound.com", "join.com", "workatastartup.com", "successfactors.com", "taleo.net",
               "oraclecloud.com", "paylocity.com", "ultipro.com", "gem.com", "comeet.com", "hire.trakstar.com")
JOB_BOARDS = ("remoteok.com", "remotive.com", "weworkremotely.com", "himalayas.app", "jobicy.com", "arbeitnow.com",
              "themuse.com", "workingnomads.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "news.ycombinator.com",
              "ycombinator.com")
CATEGORIES = [
    ("form", r"forms\.gle|docs\.google\.com/forms|typeform\.com|airtable\.com/(shr|app)|tally\.so|jotform|surveymonkey|forms\.office"),
    ("calendar", r"calendly\.com|cal\.com|savvycal|hubspot\.com/meetings|chilipiper"),
    ("linkedin", r"linkedin\.com"),
    ("github", r"github\.com|gitlab\.com"),
    ("social", r"twitter\.com|//x\.com|instagram\.com|facebook\.com|youtube\.com|youtu\.be|tiktok\.com|threads\.net|bsky\.app|mastodon"),
    ("reviews", r"glassdoor\.|kununu\.|comparably\.|levels\.fyi|blind\.com|teamblind"),
    ("docs", r"notion\.(so|site)|docs\.google\.com/(document|presentation)|dropbox\.com|drive\.google\.com|medium\.com|substack\.com"),
    ("benefits", r"benefits|perks"),
    ("legal", r"privacy|eeo|e-verify|everify|dol\.gov|eeoc\.gov|ofccp|pay-?transparency|gdpr|cookie|terms"),
]
TRACKING_PARAMS = re.compile(r"^(utm_\w+|gh_src|source|ref|fbclid|gclid|mc_\w+|_hs\w+|lever-source.*|lever-origin|trk)$", re.I)


def clean_url(u):
    u = _html.unescape(u).strip()
    if u.lower().startswith("www."):
        u = "https://" + u
    if not re.match(r"https?://", u, re.I):
        return ""
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return ""
    if not p.netloc or " " in p.netloc:
        return ""
    q = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)])
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", q, ""))


def domain_of(u):
    d = urllib.parse.urlsplit(u).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d


def root_domain(d):
    parts = d.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def classify_link(u, label=""):
    d = domain_of(u)
    s = (u + " " + label).lower()
    if any(d == a or d.endswith("." + a) for a in ATS_DOMAINS) or re.search(r"/(apply|application|careers?/apply)\b|\bapply\b", s):
        return "apply"
    for name, rx in CATEGORIES:
        if re.search(rx, s):
            return name
    if any(d == b or d.endswith("." + b) for b in JOB_BOARDS):
        return "job_board"
    if re.search(r"/(careers?|jobs?|join|work-with-us|open-positions|karriere|stellen)\b", urllib.parse.urlsplit(u).path.lower()):
        return "careers"
    return "website"


def find_links(html_text, plain_text=""):
    """[(url, category, label)] from <a href> and bare URLs in the text."""
    out = {}
    for m in HREF_RX.finditer(html_text or ""):
        href = m.group(2).strip()
        label = re.sub(r"\s+", " ", _html.unescape(TAG_RX.sub(" ", m.group(3)))).strip()[:80]
        if href.lower().startswith("mailto:"):
            continue  # handled as an email
        u = clean_url(href)
        if u and u not in out:
            out[u] = (classify_link(u, label), label)
    for m in URL_RX.finditer(plain_text or ""):
        u = clean_url(m.group(1))
        if u and u not in out:
            out[u] = (classify_link(u), "")
    return [(u, c, l) for u, (c, l) in out.items()]


# --------------------------------------------------------------------------- the posting as a whole
APPLY_KEY = re.compile(r"\b(to apply|how to apply|apply (?:by|via|at|here|through|now)|send (?:us |your )?(?:your )?"
                       r"(?:cv|resume|résumé|application|portfolio)|email (?:us|your)|submit (?:your )?(?:cv|resume|application)|"
                       r"bewirb dich|bewerbung an)", re.I)
_SENT_END = re.compile(r"\.\s|\n|$")


def _sentence_around(text, start, end, span=220):
    """The sentence containing text[start:end]. Dots inside emails/URLs (no following space) don't end it."""
    lo = max(0, start - span)
    left = text[lo:start]
    cut = max(left.rfind(". "), left.rfind("\n"))
    s0 = lo + cut + 1 if cut >= 0 else lo
    m = _SENT_END.search(text, end, min(len(text), end + span))
    s1 = m.start() if m else min(len(text), end + span)
    return text[s0:s1]


CONTACT_NAME = re.compile(r"\b(?i:recruiter|hiring manager|contact person|ansprechpartner(?:in)?|point of contact|talent partner)\s*[:\-–]\s*"
                          r"([A-ZÀ-Ý][a-zà-ÿ'’-]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ'’-]+){1,2})")


def extract(html_text, plain_text, job_url="", apply_url="", company=""):
    """Everything contact-related in one dict (all values are strings/ints so they fit CSV and SQLite)."""
    text = plain_text or ""
    # mailto: links count as emails even when the visible text hides them
    mailtos = " ".join(urllib.parse.unquote(m) for m in re.findall(r"mailto:([^\"'?>\s]+)", html_text or "", re.I))
    emails = find_emails(text + "\n" + mailtos)
    links = find_links(html_text, text)
    own = {domain_of(u) for u in (job_url, apply_url) if u}
    links = [(u, c, l) for u, c, l in links if domain_of(u) not in own or c not in ("apply", "job_board")]

    by_cat = {}
    for u, c, _ in links:
        by_cat.setdefault(c, []).append(u)
    apply_emails = [e for e, p, _ in emails if p == "apply"]

    # company website: the most-linked non-ATS, non-social domain, else the domain of a non-free email
    co = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    named = lambda dom: bool(co) and len(co) >= 3 and (co[:6] in dom.replace("-", "").replace(".", "") or
                                                        dom.split(".")[0].replace("-", "") in co)
    cand = {}
    for u, c, _ in links:
        if c in ("website", "careers"):
            d = root_domain(domain_of(u))
            cand[d] = cand.get(d, 0) + (2 if c == "careers" else 1)
    email_doms = [root_domain(e.split("@")[1]) for e, p, _ in emails if root_domain(e.split("@")[1]) not in FREE_MAIL]
    site_dom = (next((d for d in cand if named(d)), "") or next((d for d in email_doms if named(d)), "") or
                next(iter(email_doms), "") or (max(cand, key=cand.get) if cand else ""))
    if not site_dom:  # a posting hosted on the company's own site (stripe.com/jobs/…) names its domain
        for u in (job_url, apply_url):
            d = domain_of(u) if u else ""
            if d and not any(d == x or d.endswith("." + x) for x in ATS_DOMAINS + JOB_BOARDS):
                site_dom = root_domain(d)
                break
    site = "https://" + site_dom if site_dom else ""
    linkedin_company = next((u for u in by_cat.get("linkedin", []) if "/company/" in u or "/school/" in u), "")

    # best "how to apply" sentence: one naming an email/link beats a longer one beats a bare "Apply here"
    cands = [re.sub(r"\s+", " ", _sentence_around(text, m.start(), m.end())).strip(" -–•*.") for m in APPLY_KEY.finditer(text)]
    boiler = re.compile(r"accommodat|disabilit|privacy|benefit|equal opportunity|\beeo\b|background check|e-verify|"
                        r"fraud|scam|phishing|recruitment agenc|unsolicited", re.I)
    cands = [c for c in cands if not boiler.search(c)]
    how = max(cands, key=lambda c: ("@" in c or "http" in c, len(c)), default="")
    if by_cat.get("form"):
        method = "form"
    elif apply_emails and (how and any(e in how.lower() for e in apply_emails) or not apply_url):
        method = "email"
    elif apply_url or by_cat.get("apply"):
        method = "ats"
    else:
        method = "unknown"
    names = list(dict.fromkeys(CONTACT_NAME.findall(text)))[:3]

    return {
        "contact_emails": "; ".join(e for e, _, _ in emails),
        "apply_emails": "; ".join(apply_emails),
        "email_purposes": "; ".join("%s=%s" % (e, p) for e, p, _ in emails),
        "contact_names": "; ".join(names),
        "application_method": method,
        "apply_instructions": how[:400],
        "company_website": site,
        "company_domain": domain_of(site) if site else "",
        "company_linkedin": linkedin_company,
        "links_apply": "; ".join(by_cat.get("apply", [])[:5]),
        "links_form": "; ".join(by_cat.get("form", [])[:3]),
        "links_social": "; ".join((by_cat.get("linkedin", []) + by_cat.get("github", []) + by_cat.get("social", []))[:8]),
        "links_other": "; ".join((by_cat.get("careers", []) + by_cat.get("website", []) + by_cat.get("docs", []) +
                                  by_cat.get("calendar", []) + by_cat.get("reviews", []))[:12]),
        "all_links": "\n".join("%s\t%s\t%s" % (c, u, l) for u, c, l in links[:60]),
        "email_count": len(emails),
        "link_count": len(links),
    }
