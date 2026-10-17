"""Classification rules. Edit these tables to tune how jobs are categorised."""

import re

def _rx(words, flags=re.I):
    return re.compile(r"(?<![\w+#.])(?:%s)(?![\w+#])" % "|".join(words), flags)


# (function, subfunction) -> title patterns. Order matters: first strong title match wins.
FUNCTION_RULES = [
    ("Engineering", "Machine Learning / AI", [r"machine learning", r"\bml\b", r"\bai\b", r"deep learning", r"llm", r"nlp", r"computer vision", r"applied scientist", r"research scientist", r"research engineer", r"prompt engineer", r"mlops"]),
    ("Data", "Data Engineering", [r"data engineer", r"analytics engineer", r"etl", r"data platform", r"data infrastructure", r"data architect"]),
    ("Data", "Data Science", [r"data scien", r"statistician", r"quantitative", r"\bquant\b", r"econometric"]),
    ("Data", "Analytics / BI", [r"data analyst", r"business intelligence", r"\bbi\b", r"analytics", r"insights analyst", r"reporting analyst"]),
    ("Engineering", "Security", [r"security", r"secops", r"appsec", r"penetration", r"cyber", r"infosec", r"soc analyst", r"\bgrc\b"]),
    ("Engineering", "DevOps / SRE / Infrastructure", [r"devops", r"\bsre\b", r"site reliability", r"infrastructure", r"platform engineer", r"cloud engineer", r"systems engineer", r"kubernetes", r"devsecops", r"release engineer", r"build engineer", r"network engineer", r"systems administrator", r"sysadmin"]),
    ("Engineering", "Mobile", [r"\bios\b", r"android", r"mobile", r"react native", r"flutter", r"swift"]),
    ("Engineering", "Frontend", [r"front[\s-]?end", r"\bui engineer", r"web developer", r"javascript", r"react developer", r"angular", r"vue"]),
    ("Engineering", "Full Stack", [r"full[\s-]?stack"]),
    ("Engineering", "Backend", [r"back[\s-]?end", r"\bapi\b", r"server[\s-]side", r"golang", r"\bjava\b", r"python developer", r"\.net", r"ruby", r"php", r"node"]),
    ("Engineering", "QA / Test", [r"\bqa\b", r"quality assurance", r"\btest", r"\bsdet\b", r"automation engineer"]),
    ("Engineering", "Embedded / Hardware", [r"embedded", r"firmware", r"hardware", r"fpga", r"asic", r"electrical engineer", r"robotics", r"mechanical engineer", r"pcb"]),
    ("Engineering", "Blockchain / Web3", [r"blockchain", r"web3", r"solidity", r"smart contract", r"crypto"]),
    ("Engineering", "Game Development", [r"game", r"unity", r"unreal", r"gameplay"]),
    ("Engineering", "IT / Support Engineering", [r"fachinformatiker", r"informatiker", r"systemintegration", r"systemadministrat", r"\bit\b", r"help ?desk", r"desktop support", r"technical support", r"support engineer", r"it support", r"it specialist"]),
    ("Engineering", "Solutions / Sales Engineering", [r"technical account", r"customer success engineer", r"solutions? engineer", r"sales engineer", r"solutions architect", r"forward deployed", r"pre[\s-]?sales", r"implementation engineer", r"customer engineer"]),
    ("Engineering", "Engineering Management", [r"engineering manager", r"head of engineering", r"vp,? engineering", r"\bcto\b", r"director,? (of )?engineering"]),
    ("Engineering", "Software Engineering (general)", [r"architect", r"ingenieur", r"entwickler", r"softwareentwick", r"software", r"developer", r"programmer", r"engineer\b", r"swe\b", r"coder"]),
    ("Product", "Product Management", [r"product manager", r"product owner", r"\bpm\b", r"head of product", r"vp,? product", r"product lead", r"\bcpo\b", r"group product"]),
    ("Product", "Program / Project Management", [r"program manager", r"project manager", r"\btpm\b", r"scrum master", r"delivery manager", r"pmo"]),
    ("Design", "Product / UX / UI Design", [r"designer", r"\bux\b", r"\bui\b", r"user experience", r"interaction design", r"user research", r"ux research"]),
    ("Design", "Creative / Content Design", [r"illustrator", r"art director", r"motion", r"video editor", r"animator", r"creative director", r"photographer", r"3d artist"]),
    ("Marketing", "Growth / Performance", [r"growth", r"performance marketing", r"paid (media|social|search)", r"\bseo\b", r"\bsem\b", r"acquisition", r"demand gen"]),
    ("Marketing", "Content / Communications", [r"events?\b", r"event manager", r"community", r"localization", r"translator", r"content", r"copywriter", r"writer", r"editor", r"communications", r"\bpr\b", r"public relations", r"social media", r"community manager", r"journalist"]),
    ("Marketing", "Product / Brand Marketing", [r"product marketing", r"brand", r"marketing"]),
    ("Sales", "Account Executive / Closing", [r"seller", r"proposal", r"account director", r"sales director", r"account executive", r"\bae\b", r"sales executive", r"closer", r"sales representative", r"sales manager", r"head of sales", r"vp,? sales", r"\bsales\b"]),
    ("Sales", "Business Development / SDR", [r"partner manager", r"partner director", r"partner development", r"channel partner", r"business development", r"\bbdr\b", r"\bsdr\b", r"sales development", r"partnerships?", r"alliances"]),
    ("Sales", "Account Management", [r"account manager", r"key account", r"client partner", r"account partner", r"relationship manager"]),
    ("Customer Success & Support", "Customer Success", [r"customer success", r"\bcsm\b", r"onboarding", r"implementation (manager|specialist|consultant)", r"client success"]),
    ("Customer Success & Support", "Customer Support", [r"escalation", r"support manager", r"support specialist", r"customer support", r"customer service", r"support (specialist|agent|representative|associate)", r"call center", r"help center", r"customer experience", r"customer care"]),
    ("Operations", "Business / Strategy Operations", [r"business partner", r"workforce management", r"analyst, business", r"operations", r"\bops\b", r"strategy", r"chief of staff", r"business analyst", r"revops", r"revenue operations", r"sales operations", r"bizops"]),
    ("Operations", "Supply Chain / Logistics", [r"supply chain", r"logistics", r"procurement", r"purchasing", r"warehouse", r"fulfillment", r"inventory", r"buyer", r"sourcing", r"driver", r"dispatcher"]),
    ("Finance", "Accounting / Finance", [r"accountant", r"accounting", r"finance", r"financial", r"controller", r"\bfp&a\b", r"treasury", r"tax", r"audit", r"payroll", r"bookkeep", r"billing", r"\bcfo\b", r"actuar", r"investment", r"analyst, finance"]),
    ("People / HR", "Recruiting / Talent", [r"recruit", r"talent acquisition", r"sourcer", r"talent partner"]),
    ("People / HR", "HR / People Ops", [r"\bhr\b", r"human resources", r"people (ops|operations|partner)", r"hrbp", r"compensation", r"benefits", r"learning (and|&) development", r"\bl&d\b"]),
    ("Legal & Compliance", "Legal / Compliance", [r"policy", r"trust (?:and|&) safety", r"legal", r"counsel", r"attorney", r"lawyer", r"paralegal", r"compliance", r"privacy", r"regulatory", r"contracts manager", r"risk"]),
    ("Healthcare", "Clinical / Medical", [r"nurse", r"\brn\b", r"physician", r"doctor", r"clinical", r"medical", r"therap", r"pharmac", r"dental", r"health", r"caregiver", r"psycholog", r"counselor", r"veterinar"]),
    ("Education", "Teaching / Training", [r"teacher", r"tutor", r"instructor", r"professor", r"educator", r"curriculum", r"trainer", r"lecturer", r"instructional design"]),
    ("Administrative", "Admin / Office", [r"administrative", r"assistant", r"receptionist", r"office manager", r"executive assistant", r"data entry", r"secretary", r"coordinator", r"virtual assistant"]),
    ("Skilled Trades & Field", "Trades / Field / Manufacturing", [r"technician", r"electrician", r"plumber", r"mechanic", r"welder", r"carpenter", r"hvac", r"installer", r"operator", r"maintenance", r"construction", r"manufacturing", r"production", r"machinist", r"assembler", r"field service"]),
    ("Hospitality & Retail", "Hospitality / Retail", [r"retail", r"store", r"cashier", r"barista", r"chef", r"\bcook\b", r"server", r"hospitality", r"hotel", r"restaurant", r"merchandis", r"housekeep"]),
    ("Research & Science", "Research / Science", [r"scientist", r"researcher", r"research associate", r"chemist", r"biolog", r"laborator", r"\blab\b", r"physicist"]),
    ("Consulting", "Consulting", [r"consultant", r"consulting", r"advisory"]),
    ("Executive", "Executive Leadership", [r"\bceo\b", r"\bcoo\b", r"chief", r"president", r"general manager", r"managing director", r"founder"]),
]
# Descriptions are noisy ("it", "AI", "lead" appear everywhere), so the description fallback only uses
# the specific multi-word phrases from each rule and needs at least two hits.
DESC_RULES = [(f, s, re.compile(r"(?<![A-Za-z])(?:%s)" % "|".join(x for x in p if " " in x or r"[\s-]" in x), re.I))
              for f, s, p in FUNCTION_RULES if any(" " in x or r"[\s-]" in x for x in p)]

# Every pattern must start at a word boundary ("asic" must not match "basics"); the end stays open so
# "engineer" also matches "engineering" and "recruit" matches "recruiter".
FUNCTION_RULES = [(f, s, re.compile(r"(?<![A-Za-z])(?:%s)" % "|".join(p), re.I)) for f, s, p in FUNCTION_RULES]

SENIORITY_RULES = [
    ("C-Level", [r"\bchief\b", r"\bc[etofpmir]o\b", r"\bpresident\b", r"\bfounder\b"]),
    ("VP", [r"\bvp\b", r"vice president", r"\bsvp\b", r"\bevp\b"]),
    ("Director", [r"\bdirector\b", r"head of", r"\bhead,"]),
    ("Principal / Distinguished", [r"principal", r"distinguished", r"\bfellow\b", r"architect"]),
    ("Staff", [r"\bstaff\b"]),
    ("Manager", [r"\bmanager\b", r"\bmgr\b", r"supervisor"]),
    ("Lead", [r"\blead\b", r"team lead", r"tech lead"]),
    ("Senior", [r"\bsenior\b", r"\bsr\.?\b", r"\biii\b", r"\biv\b", r"\bexperienced\b", r"\bl5\b", r"\bsenior-level\b", r"berufserfahren"]),
    ("Intern", [r"\bintern\b", r"internship", r"\bpraktik", r"working student", r"werkstudent", r"co-op", r"\bapprentice"]),
    ("Entry / Junior", [r"\bjunior\b", r"\bjr\.?\b", r"entry[\s-]level", r"graduate", r"new grad", r"\bassociate\b", r"\bi\b$", r"trainee", r"berufseinstieg", r"\bearly career"]),
    ("Mid", [r"\bmid\b", r"mid[\s-]level", r"\bii\b", r"intermediate"]),
]
SENIORITY_RULES = [(n, re.compile("|".join(p), re.I)) for n, p in SENIORITY_RULES]
SENIORITY_RANK = {"Intern": 0, "Entry / Junior": 1, "Mid": 2, "Senior": 3, "Lead": 4, "Staff": 4, "Manager": 4,
                  "Principal / Distinguished": 5, "Director": 6, "VP": 7, "C-Level": 8}

EMPLOYMENT_RULES = [
    ("Internship", r"intern|praktik|werkstudent|working student|apprentice"),
    ("Contract", r"contract|contractor|freelanc|\b1099\b|\bc2c\b|corp[\s-]to[\s-]corp|consultant basis|temporary|\btemp\b|fixed[\s-]term|befristet"),
    ("Part-time", r"part[\s_-]?time|teilzeit"),
    ("Full-time", r"full[\s_-]?time|permanent|vollzeit|regular|unbefristet|fulltime"),
]

# Skill aliases are literal, lowercase phrases (1-3 words) matched against the posting's word set.
# Ambiguous short names (Go, C, R, Excel…) use case-sensitive context regexes in SKILL_SPECIAL instead.
SKILLS = {
    "programming_languages": {
        "Python": "python", "JavaScript": "javascript|js|es6|ecmascript", "TypeScript": "typescript|ts",
        "Java": "java|jvm", "Kotlin": "kotlin", "Scala": "scala", "Go": "golang", "Rust": "rust", "C++": "c++|cpp",
        "C#": "c#|csharp", "Ruby": "ruby", "PHP": "php", "Swift": "swift", "Objective-C": "objective-c", "Dart": "dart",
        "SQL": "sql|t-sql|pl/sql|postgresql|mysql", "MATLAB": "matlab", "Elixir": "elixir", "Erlang": "erlang",
        "Haskell": "haskell", "Clojure": "clojure", "Perl": "perl", "Bash/Shell": "bash|shell scripting|powershell|zsh",
        "Solidity": "solidity", "Lua": "lua", "COBOL": "cobol", "Fortran": "fortran", "OCaml": "ocaml",
        "VHDL/Verilog": "vhdl|verilog|systemverilog", "ABAP": "abap", "Apex": "apex", "Zig": "zig", "F#": "f#",
    },
    "frameworks": {
        "React": "react|reactjs|react.js", "React Native": "react native", "Next.js": "next.js|nextjs",
        "Vue": "vue|vuejs|vue.js", "Nuxt": "nuxt", "Angular": "angular|angularjs", "Svelte": "svelte|sveltekit",
        "Node.js": "node.js|nodejs|node", "Express": "express.js|expressjs", "NestJS": "nestjs|nest.js",
        "Django": "django", "Flask": "flask", "FastAPI": "fastapi", "Rails": "rails|ruby on rails",
        "Spring": "spring boot|spring framework|spring mvc", "Laravel": "laravel", "Symfony": "symfony",
        ".NET": ".net|asp.net|dotnet|.net core", "Flutter": "flutter", "SwiftUI": "swiftui", "Jetpack Compose": "jetpack compose",
        "GraphQL": "graphql", "gRPC": "grpc", "Tailwind": "tailwind|tailwindcss", "Redux": "redux",
        "Phoenix": "phoenix liveview|elixir/phoenix", "Unreal": "unreal|unreal engine", "jQuery": "jquery",
        "Electron": "electron", "Remix": "remix.run", "htmx": "htmx",
    },
    "cloud_devops": {
        "AWS": "aws|amazon web services|ec2|s3|lambda|cloudformation", "GCP": "gcp|google cloud|google cloud platform",
        "Azure": "azure", "Kubernetes": "kubernetes|k8s|eks|gke|aks", "Docker": "docker|containerization|containerized",
        "Terraform": "terraform", "Ansible": "ansible", "Pulumi": "pulumi", "Helm": "helm",
        "CI/CD": "ci/cd|cicd|continuous integration|github actions|gitlab ci|jenkins|circleci|buildkite|argocd|argo cd",
        "Linux": "linux|unix", "Datadog": "datadog", "Prometheus/Grafana": "prometheus|grafana", "Splunk": "splunk",
        "Vercel/Netlify": "vercel|netlify", "Cloudflare": "cloudflare", "Serverless": "serverless", "Nginx": "nginx",
        "Bazel": "bazel", "OpenTelemetry": "opentelemetry",
    },
    "databases": {
        "PostgreSQL": "postgres|postgresql", "MySQL": "mysql|mariadb", "MongoDB": "mongo|mongodb", "Redis": "redis",
        "Elasticsearch": "elasticsearch|opensearch|elk", "DynamoDB": "dynamodb", "Cassandra": "cassandra",
        "Snowflake": "snowflake", "BigQuery": "bigquery", "Redshift": "redshift", "Databricks": "databricks",
        "SQL Server": "sql server|mssql", "Oracle DB": "oracle", "SQLite": "sqlite", "ClickHouse": "clickhouse",
        "Kafka": "kafka", "RabbitMQ": "rabbitmq", "Spark": "spark|pyspark", "Airflow": "airflow", "dbt": "dbt",
        "Flink": "flink", "Hadoop": "hadoop|hive", "Trino/Presto": "trino|presto",
        "Vector DB": "pinecone|vector database|vector databases|weaviate|pgvector|qdrant|milvus|chroma",
    },
    "ai_ml": {
        "LLMs": "llm|llms|large language model|large language models|gpt|generative ai|genai|gen ai",
        "PyTorch": "pytorch|torch", "TensorFlow": "tensorflow|keras", "JAX": "jax", "scikit-learn": "scikit-learn|sklearn",
        "Hugging Face": "hugging face|huggingface", "LangChain/LlamaIndex": "langchain|llamaindex|langgraph",
        "RAG": "rag|retrieval-augmented|retrieval augmented", "Computer Vision": "computer vision|opencv",
        "NLP": "nlp|natural language processing", "MLOps": "mlops|mlflow|kubeflow|sagemaker|vertex ai",
        "Pandas/NumPy": "pandas|numpy|scipy", "Reinforcement Learning": "reinforcement learning|rlhf",
        "CUDA/GPU": "cuda|triton|gpu programming|nccl", "Agents": "ai agents|agentic|llm agents",
        "Fine-tuning": "fine-tuning|finetuning|fine tuning|lora",
    },
    "tools": {
        "Git": "git|github|gitlab|bitbucket", "Jira": "jira", "Confluence": "confluence", "Figma": "figma",
        "Adobe CC": "photoshop|adobe|after effects|premiere|indesign|adobe creative suite", "Salesforce": "salesforce|sfdc",
        "HubSpot": "hubspot", "Zendesk": "zendesk", "Intercom": "intercom", "Gong": "gong", "Outreach": "outreach.io",
        "Google Workspace": "google sheets|google workspace|g suite", "Tableau": "tableau", "Power BI": "power bi|powerbi",
        "Looker": "looker", "Mixpanel/Amplitude": "mixpanel|amplitude", "SAP": "sap", "NetSuite": "netsuite",
        "QuickBooks": "quickbooks", "Workday": "workday", "Notion": "notion", "Shopify": "shopify", "WordPress": "wordpress",
        "Stripe": "stripe", "Postman": "postman", "Test automation": "selenium|cypress|playwright|jest|pytest",
        "Marketo": "marketo", "Google Ads": "google ads|adwords", "Google Analytics": "google analytics|ga4",
        "Greenhouse/Lever ATS": "greenhouse ats|lever ats|ashby ats", "Zapier": "zapier", "Airtable": "airtable",
    },
}
SKILL_SPECIAL = {  # (category, name) -> case-sensitive regex with context
    ("programming_languages", "Go"): r"\bGo\b(?=\s*(?:,|/|\)|and\b|or\b|lang|programming|language|developer|engineer|services|microservices|backend))",
    ("programming_languages", "C"): r"(?<![\w+#.])C(?=\s*(?:/C\+\+|,\s*C\+\+|\s+and\s+C\+\+|\s+programming|\s+language))",
    ("programming_languages", "R"): r"(?<![\w-])R(?=\s*(?:,|/|\)|and\b|or\b|programming|language|studio|Shiny))",
    ("programming_languages", "Julia"): r"\bJulia\b(?=\s*(?:,|/|\)|programming|language))",
    ("frameworks", "Unity"): r"\bUnity(?:3D)?\b(?=\s*(?:,|/|\)|engine|3D|developer|C#))",
    ("tools", "Excel"): r"\b(?:Microsoft |MS |advanced )?Excel\b(?!lence|lent)",
    ("tools", "Slack"): r"\bSlack\b",
}
SKILL_SPECIAL = {k: re.compile(v) for k, v in SKILL_SPECIAL.items()}
SKILL_ALIASES = {}  # phrase -> [(category, name)]
for _cat, _items in SKILLS.items():
    for _name, _aliases in _items.items():
        for _al in _aliases.split("|"):
            SKILL_ALIASES.setdefault(_al.strip(), []).append((_cat, _name))
_TOKEN_RX = re.compile(r"[a-z0-9#][a-z0-9+#./-]*[a-z0-9+#]|[a-z0-9#+]")

SPOKEN_LANGS = ["English", "German", "French", "Spanish", "Portuguese", "Italian", "Dutch", "Polish", "Swedish",
                "Norwegian", "Danish", "Finnish", "Japanese", "Mandarin", "Chinese", "Cantonese", "Korean", "Arabic",
                "Hebrew", "Russian", "Ukrainian", "Turkish", "Hindi", "Vietnamese", "Thai", "Indonesian", "Czech",
                "Greek", "Romanian", "Hungarian", "Tagalog"]
SPOKEN_RX = re.compile(r"\b(%s)\b(?=[^.\n]{0,40}\b(?:speak|speaking|fluen|proficien|native|language|skills|written|verbal|required|C1|C2|B2)\b)|"
                       r"\b(?:fluent|fluency|proficient|proficiency|native|bilingual)\b[^.\n]{0,25}\b(%s)\b"
                       % ("|".join(SPOKEN_LANGS), "|".join(SPOKEN_LANGS)), re.I)

INDUSTRY_RULES = [  # specific phrases only; generic words like "delivery" or "media" cause false hits
    ("Fintech / Financial Services", r"fintech|payments? (platform|infrastructure|company|processing)|banking|neobank|lending|insurtech|wealth management|brokerage|financial services|financial infrastructure|card issuing|spend management|corporate cards?"),
    ("Crypto / Web3", r"crypto(?:currenc\w*|\b)|blockchain|web3|defi\b|nfts?\b|bitcoin|ethereum|stablecoins?|on-?chain"),
    ("Healthcare / Biotech", r"healthcare|health ?tech|patient care|hospitals?|biotech|pharmaceutical|medical devices?|telehealth|life sciences|genomics?|clinical trials?"),
    ("AI / Machine Learning", r"ai (research|lab|safety|company|systems)|artificial general intelligence|frontier models?|foundation models?|large language models?|generative ai|chatgpt|claude\b"),
    ("Cybersecurity", r"cybersecurity|cyber security|security company|zero trust|threat (intelligence|detection)|ddos|identity security"),
    ("E-commerce / Retail", r"e-?commerce|online marketplace|retailers?|grocery delivery|direct-to-consumer|shoppers"),
    ("Education / EdTech", r"edtech|language learning|learners|students|online education|e-learning"),
    ("Developer Tools / Infrastructure", r"developer tools|devtools|developer platform|developers? (worldwide|everywhere)|devops platform|open[- ]source|cloud infrastructure|edge network|observability|code editor|ai code|source code|git\b|ci/cd"),
    ("SaaS / B2B Software", r"\bsaas\b|b2b software|enterprise software|productivity (software|tools?)|collaboration (platform|tools?)|workspace|design tool"),
    ("Media / Entertainment / Gaming", r"streaming service|music streaming|podcasts?|video games?|gaming platform|entertainment company|social media platform|online communities|communities|creators"),
    ("Logistics / Transportation / Mobility", r"logistics|freight|ride-?hailing|autonomous vehicles?|robotaxis?|self-driving|last-mile|fleet management"),
    ("Real Estate / PropTech", r"real estate|proptech|mortgages?|home ?sharing|short-term rentals?|vacation rentals?"),
    ("Climate / Energy", r"climate tech|clean ?energy|renewables?|solar|batter(y|ies)|decarboni[sz]|carbon removal|electric vehicles?"),
    ("Travel / Hospitality", r"travel (company|platform|industry)|hospitality|hotels?|airlines?|tourism|guests? and hosts?"),
    ("HR / Recruiting Tech", r"hr tech|hrtech|recruiting platform|payroll (software|platform)|workforce management|talent platform"),
    ("Marketing / AdTech", r"adtech|advertising platform|ad tech|martech|marketing automation|programmatic"),
    ("Government / Public Sector", r"government agenc(y|ies)|public sector|federal agenc(y|ies)|department of defense|municipal"),
    ("Non-profit", r"non-?profit|charity|ngo\b|501\(c\)"),
    ("Hardware / Robotics / Manufacturing", r"robotics|semiconductors?|chip design|manufacturing company|aerospace|automotive|drones?|hardware company"),
    ("Food / Agriculture", r"food delivery|restaurants?|agtech|agricultur\w+|grocer(y|ies)"),
]
INDUSTRY_RULES = [(n, re.compile(r"\b(?:%s)" % p, re.I)) for n, p in INDUSTRY_RULES]

BENEFITS = {
    "benefit_health_insurance": r"health insurance|medical,? dental|medical insurance|healthcare coverage|health coverage|health benefits",
    "benefit_retirement": r"401\(?k\)?|pension|retirement plan|rrsp|superannuation",
    "benefit_unlimited_pto": r"unlimited (pto|paid time off|vacation|holiday)|flexible (pto|time off)",
    "benefit_parental_leave": r"parental leave|maternity|paternity|family leave",
    "benefit_equity": r"equity|stock options?|\brsus?\b|\bespp\b|ownership stake",
    "benefit_bonus": r"\bbonus|commission|\bote\b|profit sharing",
    "benefit_learning_budget": r"learning (budget|stipend)|education (budget|stipend)|professional development (budget|stipend)|conference budget|tuition reimbursement",
    "benefit_home_office": r"home office (stipend|budget|setup)|remote (work )?stipend|equipment (stipend|budget)|wfh stipend",
    "benefit_wellness": r"wellness|gym|mental health|fitness",
    "benefit_four_day_week": r"4[- ]day (work ?)?week|four[- ]day (work ?)?week",
    "benefit_relocation": r"relocation (assistance|package|support|bonus)",
    "benefit_flexible_hours": r"flexible (hours|schedule|working hours)|async(hronous)? (work|culture|first)",
}
BENEFITS = {k: re.compile(v, re.I) for k, v in BENEFITS.items()}

COUNTRIES = {
    "United States": ["united states", "usa", "u.s.", "us", "america"], "Canada": ["canada"], "United Kingdom": ["united kingdom", "uk", "england", "scotland", "wales", "great britain", "gb"],
    "Germany": ["germany", "deutschland", "de"], "France": ["france"], "Spain": ["spain", "españa"], "Portugal": ["portugal"], "Italy": ["italy"],
    "Netherlands": ["netherlands", "nl", "holland"], "Ireland": ["ireland"], "Poland": ["poland"], "Sweden": ["sweden"], "Norway": ["norway"],
    "Denmark": ["denmark"], "Finland": ["finland"], "Switzerland": ["switzerland"], "Austria": ["austria"], "Belgium": ["belgium"],
    "Czech Republic": ["czech", "czechia"], "Romania": ["romania"], "Greece": ["greece"], "Ukraine": ["ukraine"], "Estonia": ["estonia"],
    "Israel": ["israel"], "India": ["india"], "Singapore": ["singapore"], "Japan": ["japan"], "South Korea": ["korea"], "China": ["china"],
    "Hong Kong": ["hong kong"], "Taiwan": ["taiwan"], "Philippines": ["philippines"], "Indonesia": ["indonesia"], "Vietnam": ["vietnam"],
    "Australia": ["australia"], "New Zealand": ["new zealand"], "Brazil": ["brazil", "brasil"], "Mexico": ["mexico", "méxico"],
    "Argentina": ["argentina"], "Colombia": ["colombia"], "Chile": ["chile"], "Peru": ["peru"], "South Africa": ["south africa"],
    "Nigeria": ["nigeria"], "Kenya": ["kenya"], "Egypt": ["egypt"], "UAE": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "Turkey": ["turkey", "türkiye"], "Pakistan": ["pakistan"],
}
COUNTRY_CODES = {"US": "United States", "USA": "United States", "CA": "Canada", "GB": "United Kingdom", "UK": "United Kingdom",
                 "DE": "Germany", "FR": "France", "ES": "Spain", "NL": "Netherlands", "IE": "Ireland", "IN": "India", "AU": "Australia",
                 "BR": "Brazil", "MX": "Mexico", "PL": "Poland", "SE": "Sweden", "CH": "Switzerland", "SG": "Singapore", "JP": "Japan",
                 "IL": "Israel", "PT": "Portugal", "IT": "Italy", "AT": "Austria", "NZ": "New Zealand"}
COUNTRY_RX = [(c, re.compile(r"(?<![\w])(%s)(?![\w])" % "|".join(re.escape(a) for a in al), 0 if c in ("United States", "Germany", "Netherlands", "United Kingdom") else re.I))
              for c, al in COUNTRIES.items()]
# case-insensitive aliases for those with short codes (case-sensitive short codes avoid "us" the pronoun)
_LONG_CI = {"United States": ["united states", "usa", "u.s.", "america"], "Germany": ["germany", "deutschland"],
            "Netherlands": ["netherlands", "holland"], "United Kingdom": ["united kingdom", "england", "scotland", "wales", "great britain"]}
COUNTRY_RX += [(c, re.compile(r"(?<![\w])(%s)(?![\w])" % "|".join(re.escape(a) for a in al), re.I)) for c, al in _LONG_CI.items()]

US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
             "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
             "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
             "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
             "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
             "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
             "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
             "WY": "Wyoming", "DC": "District of Columbia"}
US_STATE_NAMES = {v.lower(): v for v in US_STATES.values()}
US_CITIES = {"new york", "nyc", "san francisco", "sf", "los angeles", "seattle", "austin", "boston", "chicago", "denver", "atlanta",
             "miami", "dallas", "houston", "washington dc", "washington, dc", "washington d.c.", "palo alto", "mountain view", "menlo park", "san jose", "san diego",
             "portland", "philadelphia", "phoenix", "salt lake city", "pittsburgh", "minneapolis", "nashville", "raleigh", "detroit"}
CITY_COUNTRY = {"london": "United Kingdom", "berlin": "Germany", "munich": "Germany", "münchen": "Germany", "hamburg": "Germany",
                "frankfurt": "Germany", "cologne": "Germany", "köln": "Germany", "bonn": "Germany", "stuttgart": "Germany", "düsseldorf": "Germany",
                "paris": "France", "amsterdam": "Netherlands", "dublin": "Ireland", "madrid": "Spain", "barcelona": "Spain", "lisbon": "Portugal",
                "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "sydney": "Australia", "melbourne": "Australia",
                "bangalore": "India", "bengaluru": "India", "mumbai": "India", "hyderabad": "India", "tel aviv": "Israel", "tokyo": "Japan",
                "singapore": "Singapore", "zurich": "Switzerland", "stockholm": "Sweden", "copenhagen": "Denmark", "warsaw": "Poland",
                "vienna": "Austria", "wien": "Austria", "são paulo": "Brazil", "sao paulo": "Brazil", "mexico city": "Mexico"}
REGIONS = {"Worldwide": r"worldwide|anywhere|global|any location|all countries|\bremote\b$",
           "Americas": r"americas|north america|latam|latin america|south america",
           "Europe": r"\beurope|\beu\b|\bemea\b|\beea\b|\bcet\b",
           "APAC": r"apac|asia|oceania|pacific"}
REGIONS = {k: re.compile(v, re.I) for k, v in REGIONS.items()}

# Approximate FX → USD. Good enough for comparing/sorting; update as needed.
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66, "NZD": 0.60, "CHF": 1.12, "SEK": 0.095,
             "NOK": 0.093, "DKK": 0.145, "PLN": 0.25, "CZK": 0.043, "INR": 0.012, "JPY": 0.0067, "SGD": 0.74, "BRL": 0.18,
             "MXN": 0.055, "ILS": 0.27, "ZAR": 0.055, "HKD": 0.128, "AED": 0.27, "PHP": 0.017, "CNY": 0.14, "KRW": 0.00073}
CUR_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "C$": "CAD", "CA$": "CAD", "A$": "AUD", "AU$": "AUD",
               "₹": "INR", "¥": "JPY", "R$": "BRL", "CHF": "CHF", "zł": "PLN"}
PERIOD_MULT = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}

MONEY_RX = re.compile(
    r"(?P<cur>US\$|CA\$|AU\$|C\$|A\$|R\$|[$€£₹]|\b(?:USD|EUR|GBP|CAD|AUD|CHF|SEK|NOK|DKK|PLN|INR|SGD|BRL|MXN|NZD)\b)\s?"
    r"(?P<a>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<ak>[kKmM])?"
    r"(?:\s*(?:-|–|—|to|and)\s*(?:US\$|CA\$|C\$|A\$|[$€£₹]|\b(?:USD|EUR|GBP|CAD|AUD)\b)?\s?"
    r"(?P<b>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<bk>[kKmM])?)?"
    r"(?:\s*(?:USD|EUR|GBP|CAD|AUD))?"
    r"(?:\s*(?:per|/|an|a|each)\s*(?P<per>hour|hr|h\b|year|yr|annum|annual|month|mo\b|week|wk|day))?",
    re.I)
# Same, but with the currency AFTER the numbers: "60.000 € – 75.000 €", "100k-160k GBP"
MONEY_TRAIL_RX = re.compile(
    r"(?<![\w$€£.,])(?P<a>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<ak>[kK])?\s?(?:€|£|EUR|GBP|USD|CHF)?"
    r"\s*(?:-|–|—|to|bis)\s*"
    r"(?P<b>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<bk>[kK])?\s?"
    r"(?P<cur>€|£|\b(?:USD|EUR|GBP|CAD|AUD|CHF|SEK|NOK|DKK|PLN|INR|SGD)\b)"
    r"(?:\s*(?:per|/|an|a|each)\s*(?P<per>hour|hr|h\b|year|yr|annum|annual|month|mo\b|week|wk|day))?",
    re.I)

