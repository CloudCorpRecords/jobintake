/* jobintake web app — vanilla JS, no build step. Talks only to the local REST API (/api/*). */
"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const k = (n) => (n >= 1000 ? Math.round(n / 1000) + "k" : String(Math.round(n)));
const STATUSES = ["saved", "applied", "interviewing", "offer", "rejected"];
const store = {
  get(key, d) { try { const v = localStorage.getItem("ji." + key); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(key, v) { try { localStorage.setItem("ji." + key, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  const d = Math.round(s / 86400);
  return d < 60 ? d + "d ago" : Math.round(d / 30) + "mo ago";
}
function pay(j) {
  const a = j.salary_min_usd_annual, b = j.salary_max_usd_annual;
  if (!a && !b) return "";
  if (j.salary_period === "hour" && j.salary_min) return "$" + Math.round(j.salary_min) + (j.salary_max && j.salary_max !== j.salary_min ? "–" + Math.round(j.salary_max) : "") + "/hr";
  return "$" + k(a || b) + (b && b !== a ? "–" + k(b) : "");
}
function ringCls(m) { return m >= 80 ? "g" : m >= 65 ? "m" : m >= 45 ? "w" : "l"; }
function ring(m, lg) { return `<div class="ring ${ringCls(m)}${lg ? " lg" : ""}" style="--p:${m}" title="Match score ${m}/100"><span>${m}</span></div>`; }
function wpPill(w) {
  if (!w) return "";
  const t = w.replace(" (inferred)", "");
  const c = t === "Remote" ? "remote" : t === "Hybrid" ? "hybrid" : "onsite";
  return t === "Unknown" ? "" : `<span class="pill ${c}">${esc(t)}</span>`;
}
function place(j) {
  if (j.workplace_type === "Remote") return j.remote_scope ? j.remote_scope.replace(/^(Country|Region): /, "") : "Anywhere";
  return [j.city, j.state || (j.country || "").split(";")[0]].filter(Boolean).join(", ") || j.location_raw || "";
}
function skillsOf(j) {
  return ["programming_languages", "frameworks", "cloud_devops", "databases_data", "ai_ml", "tools"]
    .flatMap((f) => (j[f] || "").split("; ").filter(Boolean));
}
function toast(msg, action) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<span>${esc(msg)}</span>${action ? `<button>${esc(action.label)}</button>` : ""}`;
  if (action) t.querySelector("button").onclick = () => { action.fn(); t.remove(); };
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), action ? 7000 : 3500);
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ---------------------------------------------------------------- state
const S = {
  view: store.get("view", "foryou"),
  q: "",
  filters: Object.assign({ function: [], seniority: [], workplace: [], min_match: 0, max_age: 0, has_salary: false, min_salary: 0, has: [] },
    store.get("filters", {})),
  sort: { foryou: "match", all: "newest" },
  jobs: [], total: 0, facets: {}, offset: 0, loading: false,
  sel: null, detail: null,
  profile: null, meta: null,
};

// ---------------------------------------------------------------- theme
(function initTheme() {
  const t = store.get("theme", null);
  if (t) document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  };
})();

// ---------------------------------------------------------------- views
function setView(v) {
  S.view = v;
  store.set("view", v);
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === v)));
  S.sel = null; S.detail = null;
  render();
}
$$(".tab").forEach((t) => (t.onclick = () => setView(t.dataset.view)));

function render() {
  const v = S.view;
  if (v === "foryou" || v === "all") return renderSearch();
  if (v === "new") return renderNew();
  if (v === "pipeline") return renderPipeline();
  if (v === "insights") return renderInsights();
  if (v === "changes") return renderChanges();
  if (v === "api") return renderApi();
}

// ---------------------------------------------------------------- search views
function searchParams(extra = {}) {
  const f = S.filters, p = new URLSearchParams();
  if (S.q) p.set("q", S.q);
  if (f.function.length) p.set("function", f.function.join(","));
  if (f.seniority.length) p.set("seniority", f.seniority.join(","));
  if (f.workplace.length) p.set("workplace", f.workplace.join(","));
  if (f.min_match) p.set("min_match", f.min_match);
  if (f.max_age) p.set("max_age", f.max_age);
  if (f.has_salary) p.set("has_salary", "true");
  if (f.min_salary) p.set("min_salary", f.min_salary);
  if ((f.has || []).length) p.set("has", f.has.join(","));
  if (S.view === "all") p.set("include_excluded", "true");
  p.set("sort", S.sort[S.view] || "match");
  for (const [a, b] of Object.entries(extra)) p.set(a, b);
  return p;
}

function renderSearch() {
  $("#main").innerHTML = `
    <div class="layout ${S.sel ? "" : "no-detail"}" id="layout">
      <aside class="filters" id="filters" aria-label="Filters"></aside>
      <section class="listcol" id="listcol" aria-label="Jobs"></section>
      <aside class="detail" id="detail" aria-label="Job details"></aside>
    </div>`;
  renderFilters();
  loadJobs(true);
}

async function loadJobs(reset) {
  if (reset) { S.offset = 0; S.jobs = []; }
  S.loading = true;
  const list = $("#listcol");
  if (reset && list) list.innerHTML = listHeader() + `<div class="skeleton"></div>`.repeat(6);
  try {
    const r = await api("/api/jobs?" + searchParams({ limit: 40, offset: S.offset }));
    S.total = r.total; S.facets = r.facets; S.queryInfo = r.query || {};
    S.jobs = reset ? r.jobs : S.jobs.concat(r.jobs);
    S.offset = S.jobs.length;
    const cnt = $("#c-" + S.view);
    if (cnt) cnt.textContent = r.total.toLocaleString();
  } catch (e) { toast("Couldn't load jobs: " + e.message); }
  S.loading = false;
  renderList();
  renderFilters();
}

function listHeader() {
  const base = S.view === "foryou" ? [["match", "Best match"], ["newest", "Newest"], ["salary", "Highest pay"]] : [["newest", "Newest"], ["match", "Best match"], ["salary", "Highest pay"], ["company", "Company"]];
  const sorts = S.q ? [["relevance", "Relevance"], ...base] : base;
  const cur = S.sort[S.view];
  return `<div class="listbar">
      <button class="btn sm filters-toggle" id="ftoggle">Filters</button>
      <h2>${S.view === "foryou" ? "Ranked for you" : "All jobs"}</h2>
      <span class="muted">${S.loading && !S.jobs.length ? "" : S.total.toLocaleString() + " jobs"}</span>
      <span class="spacer"></span>
      <div class="seg" role="group" aria-label="Sort">${sorts.map(([v, l]) => `<button data-sort="${v}" class="${cur === v ? "on" : ""}">${l}</button>`).join("")}</div>
      <a class="btn sm" href="/api/export.csv?${searchParams()}" title="Download this list as CSV (includes emails &amp; links)">CSV ↓</a>
    </div>${(S.queryInfo?.errors || []).length ? `<p class="qerr">${esc(S.queryInfo.errors.join(" · "))} — <a href="#" data-help>search tips</a></p>` : ""}`;
}

function welcomeCard() {
  const p = S.profile;
  if (!p || S.view !== "foryou" || (p.skills.length && (p.home_city || p.workplace !== "remote_or_near")) || store.get("welcomeDismissed", false)) return "";
  return `<div class="welcome">
    <h3>Make the ranking yours</h3>
    <p>Jobs are scored 0–100 against your profile. Add your skills and where you live so remote and nearby jobs rank first.</p>
    <div class="grid">
      <div><span class="flabel">Your skills</span><div id="wSkills"></div></div>
      <div><span class="flabel">Your city</span><input class="input" id="wCity" placeholder="e.g. Austin" value="${esc(p.home_city)}"></div>
      <div style="display:flex;gap:6px"><button class="btn primary" id="wSave">Save</button><button class="btn ghost" id="wSkip">Later</button></div>
    </div></div>`;
}

function renderList() {
  const el = $("#listcol");
  if (!el) return;
  let html = listHeader() + welcomeCard();
  if (!S.jobs.length && !S.loading) {
    html += `<div class="empty"><h3>No jobs match</h3><p>Try clearing a filter${S.q ? " or changing your search" : ""}.</p>
      <button class="btn" id="clearAll">Clear filters</button></div>`;
  }
  html += S.jobs.map(cardHtml).join("");
  if (S.jobs.length < S.total) html += `<div class="more"><button class="btn" id="moreBtn">Show more (${(S.total - S.jobs.length).toLocaleString()} left)</button></div>`;
  el.innerHTML = html;
  bindList(el);
  if ($("#wSkills")) {
    const ti = tagInput($("#wSkills"), S.profile.skills, topSkillSuggestions());
    $("#wSave").onclick = async () => {
      await saveProfile({ skills: ti.value(), home_city: $("#wCity").value.trim() });
      toast("Profile saved — jobs re-ranked");
    };
    $("#wSkip").onclick = () => { store.set("welcomeDismissed", true); renderList(); };
  }
}

function cardHtml(j) {
  const hits = new Set((j.matched_skills || []).map((s) => s.toLowerCase()));
  const sk = skillsOf(j);
  sk.sort((a, b) => hits.has(b.toLowerCase()) - hits.has(a.toLowerCase()));
  const chips = sk.slice(0, 6).map((s) => `<span class="skill ${hits.has(s.toLowerCase()) ? "hit" : ""}">${esc(s)}</span>`).join("");
  const p = pay(j);
  const isSaved = j.track === "saved";
  const snip = j.snippet ? `<p class="snip">${esc(j.snippet).replace(/\u0002/g, "<mark>").replace(/\u0003/g, "</mark>")}</p>` : "";
  const useful = (j.email_purposes || "").split("; ").filter((x) => /=(apply|contact)$/.test(x)).map((x) => x.split("=")[0]);
  const contact = [
    j.apply_emails ? `<span class="pill contact" title="${esc(j.apply_emails)}">✉ Apply by email</span>` : useful.length ? `<span class="pill contact-soft" title="${esc(useful.join(", "))}">✉ ${useful.length} contact</span>` : "",
    j.application_method === "form" ? `<span class="pill contact-soft">Form</span>` : "",
    j.company_website ? `<span class="pill contact-soft">${esc(j.company_website.replace(/^https?:\/\//, ""))}</span>` : "",
  ].join("");
  return `<article class="card ${S.sel === j.job_id ? "sel" : ""} ${j.status === "closed" ? "dim" : ""}" data-id="${esc(j.job_id)}" tabindex="0">
    ${ring(j.match)}
    <div>
      <h3>${esc(j.title)}</h3>
      <div class="sub"><b>${esc(j.company)}</b><span class="dot"></span><span>${esc(place(j))}</span>${j.age_days !== "" && j.age_days != null ? `<span class="dot"></span><span class="muted">${j.age_days === 0 ? "today" : esc(j.age_days) + "d ago"}</span>` : ""}</div>
      <div class="row2">${wpPill(j.workplace_type)}${p ? `<span class="pill pay">${esc(p)}</span>` : ""}<span class="pill">${esc(j.seniority || "")}</span>
        ${j.track && !isSaved ? `<span class="pill status st-${esc(j.track)}">${esc(j.track)}</span>` : ""}${j.status === "closed" ? `<span class="pill closed">Closed</span>` : ""}${j.has_notes ? `<span class="pill" title="Has notes">✎</span>` : ""}
        ${contact}${chips}</div>${snip}
    </div>
    <div class="actions">
      <button class="btn sm icon ${isSaved ? "on" : "ghost"}" data-act="save" title="${isSaved ? "Unsave" : "Save"} (s)" aria-label="Save">${isSaved ? "★" : "☆"}</button>
      <button class="btn sm icon ghost" data-act="hide" title="Hide (x)" aria-label="Hide">✕</button>
    </div>
  </article>`;
}

function bindList(el) {
  $$("[data-sort]", el).forEach((b) => (b.onclick = () => { S.sort[S.view] = b.dataset.sort; loadJobs(true); }));
  $("#moreBtn", el)?.addEventListener("click", () => loadJobs(false));
  $("#clearAll", el)?.addEventListener("click", clearFilters);
  $("#ftoggle", el)?.addEventListener("click", () => $("#filters").classList.toggle("open"));
  $$(".card", el).forEach((c) => {
    c.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      const id = c.dataset.id;
      if (act === "save") return quickStatus(id, S.jobs.find((x) => x.job_id === id)?.track === "saved" ? "" : "saved");
      if (act === "hide") return quickStatus(id, "hidden");
      openDetail(id);
    });
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") openDetail(c.dataset.id); });
  });
}

// ---------------------------------------------------------------- filters
function renderFilters() {
  const el = $("#filters");
  if (!el) return;
  const f = S.filters, fac = S.facets || {};
  const count = (facet, v) => (fac[facet] || []).find(([n]) => n === v)?.[1];
  const chipGroup = (key, values, facet) => values.map((v) => {
    const n = count(facet, v);
    return `<span class="chip ${f[key].includes(v) ? "on" : ""}" data-key="${key}" data-v="${esc(v)}" role="checkbox" aria-checked="${f[key].includes(v)}" tabindex="0">${esc(v)}${n ? `<span class="n">${n}</span>` : ""}</span>`;
  }).join("");
  const funcs = [...new Set([...(S.profile?.functions || []), ...(fac.job_function || []).map(([n]) => n)])].slice(0, 14);
  const sens = (S.meta?.seniorities || []).filter((s) => count("seniority", s) || f.seniority.includes(s));
  el.innerHTML = `
    <div class="fgroup"><label>Minimum match</label>
      <input type="range" class="range" id="fMatch" min="0" max="90" step="5" value="${f.min_match}">
      <div class="range-row"><span>Any</span><b id="fMatchV">${f.min_match ? f.min_match + "+" : "Any"}</b><span>90</span></div></div>
    <div class="fgroup"><label>Workplace</label><div class="chips">${chipGroup("workplace", ["Remote", "Hybrid", "On-site"], "workplace_type")}</div></div>
    <div class="fgroup"><label>Field</label><div class="chips">${chipGroup("function", funcs, "job_function")}</div></div>
    <div class="fgroup"><label>Level</label><div class="chips">${chipGroup("seniority", sens, "seniority")}</div></div>
    <div class="fgroup"><label>Contact</label>
      ${[["apply-email", "Apply by email"], ["email", "Has a contact email"], ["website", "Company website"], ["form", "Application form"]].map(([v, l]) =>
        `<label class="check"><input type="checkbox" data-has="${v}" ${(f.has || []).includes(v) ? "checked" : ""}> ${l}</label>`).join("")}</div>
    <div class="fgroup"><label for="fAge">Posted within</label>
      <select class="input" id="fAge">${[[0, "Any time"], [1, "24 hours"], [3, "3 days"], [7, "Week"], [14, "2 weeks"], [30, "Month"]].map(([v, l]) => `<option value="${v}" ${+f.max_age === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="fgroup"><label for="fPay">Minimum pay (USD / year)</label>
      <select class="input" id="fPay">${[0, 60000, 80000, 100000, 120000, 150000, 180000, 200000, 250000].map((v) => `<option value="${v}" ${+f.min_salary === v ? "selected" : ""}>${v ? "$" + k(v) + "+" : "Any"}</option>`).join("")}</select>
      <label class="check" style="margin-top:8px"><input type="checkbox" id="fHasPay" ${f.has_salary ? "checked" : ""}> Only jobs that list pay</label></div>
    <button class="btn sm ghost" id="fClear">Clear filters</button>
    <p class="help" style="margin-top:18px"><a href="#" data-help>Search tips</a> · press <kbd>j</kbd>/<kbd>k</kbd> to move, <kbd>s</kbd> save, <kbd>x</kbd> hide, <kbd>o</kbd> open posting.</p>`;
  $$(".chip", el).forEach((c) => {
    const toggle = () => {
      const arr = f[c.dataset.key], v = c.dataset.v;
      arr.includes(v) ? arr.splice(arr.indexOf(v), 1) : arr.push(v);
      filtersChanged();
    };
    c.onclick = toggle;
    c.onkeydown = (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } };
  });
  $("#fMatch").oninput = (e) => { $("#fMatchV").textContent = +e.target.value ? e.target.value + "+" : "Any"; };
  $("#fMatch").onchange = (e) => { f.min_match = +e.target.value; filtersChanged(); };
  $("#fAge").onchange = (e) => { f.max_age = +e.target.value; filtersChanged(); };
  $("#fPay").onchange = (e) => { f.min_salary = +e.target.value; filtersChanged(); };
  $("#fHasPay").onchange = (e) => { f.has_salary = e.target.checked; filtersChanged(); };
  $("#fClear").onclick = clearFilters;
  $$("[data-has]", el).forEach((cb) => (cb.onchange = () => {
    f.has = f.has || [];
    cb.checked ? f.has.push(cb.dataset.has) : f.has.splice(f.has.indexOf(cb.dataset.has), 1);
    filtersChanged();
  }));
}
function filtersChanged() { store.set("filters", S.filters); loadJobs(true); }
function clearFilters() {
  S.filters = { function: [], seniority: [], workplace: [], min_match: 0, max_age: 0, has_salary: false, min_salary: 0, has: [] };
  S.q = ""; $("#q").value = "";
  filtersChanged();
}
$("#q").addEventListener("input", debounce((e) => {
  S.q = e.target.value.trim();
  if (S.view !== "foryou" && S.view !== "all") { setView("foryou"); return; }
  loadJobs(true);
}, 250));

// ---------------------------------------------------------------- detail panel
async function openDetail(id) {
  S.sel = id;
  $("#layout")?.classList.remove("no-detail");
  $$(".card").forEach((c) => c.classList.toggle("sel", c.dataset.id === id));
  $(`.card[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
  const el = $("#detail");
  if (!el) return;
  el.innerHTML = `<div class="d-body"><div class="skeleton"></div><div class="skeleton"></div></div>`;
  try {
    const j = await api("/api/jobs/" + encodeURIComponent(id));
    if (S.sel !== id) return;
    S.detail = j;
    el.innerHTML = detailHtml(j);
    el.scrollTop = 0;
    bindDetail(el, j);
  } catch (e) { el.innerHTML = `<div class="empty"><h3>Couldn't load this job</h3><p>${esc(e.message)}</p></div>`; }
}
function closeDetail() {
  S.sel = null; S.detail = null;
  const d = $("#detail"); if (d) d.innerHTML = "";
  $("#layout")?.classList.add("no-detail");
  $$(".card.sel").forEach((c) => c.classList.remove("sel"));
}

function detailHtml(j) {
  const apply = safeUrl(j.apply_url) || safeUrl(j.url);
  const initials = esc((j.company || "?").trim().slice(0, 1).toUpperCase());
  const logo = safeUrl(j.company_logo);
  const reasons = (j.reasons || []).map((r) => `<li><span class="ic ${r.ok === true ? "ok" : r.ok === false ? "no" : "meh"}">${r.ok === true ? "✓" : r.ok === false ? "✕" : "•"}</span><span>${esc(r.text)}</span></li>`).join("");
  const hits = new Set((j.matched_skills || []).map((s) => s.toLowerCase()));
  const skillRows = [["Languages", "programming_languages"], ["Frameworks", "frameworks"], ["Cloud & DevOps", "cloud_devops"], ["Data", "databases_data"], ["AI / ML", "ai_ml"], ["Tools", "tools"]]
    .filter(([, f]) => j[f]).map(([l, f]) => `<div style="margin-bottom:8px"><span class="muted" style="font-size:12px">${l}</span><div class="chips" style="margin-top:4px">${j[f].split("; ").map((s) => `<span class="skill ${hits.has(s.toLowerCase()) ? "hit" : ""}">${esc(s)}</span>`).join("")}</div></div>`).join("");
  const benefits = Object.keys(j).filter((x) => x.startsWith("benefit_") && (j[x] === true || j[x] === "TRUE"))
    .map((x) => `<span class="pill">${esc(x.replace("benefit_", "").replace(/_/g, " "))}</span>`).join("");
  const p = pay(j);
  const fact = (kk, v) => (v || v === 0) && v !== "Unknown" ? `<div class="fact"><div class="k">${kk}</div><div class="v">${esc(v)}</div></div>` : "";
  const yrs = j.years_experience_min !== "" && j.years_experience_min != null ? j.years_experience_min + (j.years_experience_max ? "–" + j.years_experience_max : "+") + " years" : "";
  const ai = Object.keys(j).filter((x) => x.startsWith("claude_"));
  const desc = j.description || "";
  return `
  <div class="d-head">
    <div class="d-top">
      <div class="avatar">${logo ? `<img src="${esc(logo)}" alt="" referrerpolicy="no-referrer" data-initial="${initials}">` : initials}</div>
      <div class="d-title"><h2>${esc(j.title)}</h2><div class="co">${esc(j.company)} · ${esc(place(j))}</div></div>
      ${ring(j.match, true)}
      <button class="btn icon ghost closebtn" id="dClose" aria-label="Close">✕</button>
    </div>
    <div class="d-actions">
      ${apply ? `<a class="btn primary" href="${esc(apply)}" target="_blank" rel="noopener noreferrer">Apply / view posting ↗</a>` : ""}
      <div class="statusbar" role="group" aria-label="Your status">${STATUSES.map((s) => `<button data-st="${s}" class="${j.track === s ? "on" : ""}">${s}</button>`).join("")}</div>
      <button class="btn sm ghost" data-st="hidden" title="Hide this job">${j.track === "hidden" ? "Unhide" : "Hide"}</button>
    </div>
  </div>
  <div class="d-body">
    ${j.status === "closed" ? `<p class="pill closed" style="margin-bottom:14px">This job is no longer listed on its source (closed ${esc(ago(j.closed_at))}).</p>` : ""}
    <div class="section"><h4>Why it ${j.match >= 60 ? "matches" : "scores " + j.match}</h4><ul class="why">${reasons}</ul></div>
    ${contactSection(j)}
    ${ai.length ? `<div class="section"><h4>AI summary</h4><p>${esc(j.claude_summary || "")}</p>${j.claude_red_flags ? `<p class="muted">Red flags: ${esc(j.claude_red_flags)}</p>` : ""}</div>` : ""}
    <div class="section"><h4>Key facts</h4><div class="facts">
      ${fact("Pay", p ? p + (j.salary_currency && j.salary_currency !== "USD" ? ` (${j.salary_currency} ${k(j.salary_min)}–${k(j.salary_max)})` : "") : "Not listed")}
      ${fact("Workplace", (j.workplace_type || "").replace(" (inferred)", " (likely)"))}
      ${fact("Level", j.seniority)}${fact("Experience", yrs)}
      ${fact("Type", j.employment_type)}${fact("Field", j.job_subfunction)}
      ${fact("Visa sponsorship", j.visa_sponsorship)}${fact("Degree", j.degree_level ? j.degree_level + " (" + j.degree_requirement + ")" : "")}
      ${fact("Industry", j.industry_guess)}${fact("Posted", j.posted_at ? new Date(j.posted_at).toLocaleDateString() + " · " + ago(j.posted_at) : "")}
      ${fact("Listed on", (j.also_listed_on || "").replace(/,/g, ", "))}${fact("First seen", ago(j.first_seen))}
    </div></div>
    <div class="section"><h4>Your notes</h4>
      <textarea class="input" id="dNotes" placeholder="Recruiter name, referral, salary talk, interview dates…">${esc(j.notes || "")}</textarea>
      <div class="savehint" id="dNotesHint"></div></div>
    ${skillRows ? `<div class="section"><h4>Skills mentioned <span class="muted" style="text-transform:none;letter-spacing:0">· green = yours</span></h4>${skillRows}</div>` : ""}
    ${benefits ? `<div class="section"><h4>Benefits mentioned</h4><div class="chips">${benefits}</div></div>` : ""}
    <div class="section"><h4>Description</h4>
      <div class="desc ${desc.length > 2200 ? "clamp" : ""}" id="dDesc">${esc(desc) || '<span class="muted">No description provided by the source — open the posting.</span>'}</div>
      ${desc.length > 2200 ? `<button class="btn sm" id="dMore" style="margin-top:8px">Show full description</button>` : ""}</div>
    ${(j.changes || []).length || (j.track_history || []).length ? `<div class="section"><h4>History</h4><ul class="timeline">
      ${(j.track_history || []).slice().reverse().map((h) => `<li>${esc(new Date(h.at).toLocaleDateString())} · you marked it <b>${esc(h.status || "cleared")}</b></li>`).join("")}
      ${(j.changes || []).map((c) => `<li>${esc(new Date(c.at).toLocaleDateString())} · ${esc(c.field.replace(/_/g, " "))}: ${esc(c.old || "—")} → ${esc(c.new || "—")}</li>`).join("")}</ul></div>` : ""}
  </div>`;
}

function contactSection(j) {
  const purposes = Object.fromEntries((j.email_purposes || "").split("; ").filter(Boolean).map((x) => x.split("=")));
  const emails = (j.contact_emails || "").split("; ").filter(Boolean);
  const order = { apply: 0, contact: 1, accommodation: 2, diversity: 3, benefits: 4, privacy: 5, security: 6 };
  emails.sort((a, b) => (order[purposes[a]] ?? 9) - (order[purposes[b]] ?? 9));
  const label = { apply: "Apply / recruiting", contact: "Contact", accommodation: "Accommodations", diversity: "Diversity & inclusion", benefits: "Benefits", privacy: "Privacy", security: "Security" };
  const emailRows = emails.map((e) => `<div class="crow"><span class="ctag ${purposes[e] === "apply" ? "hot" : ""}">${esc(label[purposes[e]] || "Email")}</span>
      <a href="mailto:${esc(e)}">${esc(e)}</a><button class="btn sm ghost" data-copytext="${esc(e)}" title="Copy">Copy</button></div>`).join("");
  const links = (j.all_links || "").split("\n").filter(Boolean).map((l) => l.split("\t")).filter(([, u]) => safeUrl(u));
  const catName = { apply: "Apply", form: "Form", careers: "Careers", website: "Website", linkedin: "LinkedIn", github: "GitHub", social: "Social",
    docs: "Docs", calendar: "Calendar", reviews: "Reviews", benefits: "Benefits", legal: "Legal", job_board: "Job board" };
  const catOrder = ["apply", "form", "careers", "website", "linkedin", "github", "calendar", "docs", "reviews", "social", "benefits", "job_board", "legal"];
  links.sort((a, b) => catOrder.indexOf(a[0]) - catOrder.indexOf(b[0]));
  const linkRows = links.slice(0, 25).map(([c, u, lbl]) => `<div class="crow"><span class="ctag">${esc(catName[c] || c)}</span>
      <a href="${esc(u)}" target="_blank" rel="noopener noreferrer" title="${esc(u)}">${esc(lbl && lbl.length > 2 && !/^https?:/.test(lbl) ? lbl : u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60))}</a></div>`).join("");
  const site = safeUrl(j.company_website), li = safeUrl(j.company_linkedin);
  const method = { email: "Apply by email", form: "Apply via form", ats: "Apply on the company's job site", unknown: "See posting" }[j.application_method || "unknown"];
  const pf = j.page_fetch;
  const pfText = pf ? ({ ok: "Fetched", blocked_by_robots: "Site doesn't allow automated fetching", gone: "Posting page is gone (404)",
    skipped: "Not needed — source gave the full text", no_url: "No posting URL" }[pf.status] || "Fetch failed: " + pf.status) + " · " + ago(pf.fetched_at) : "";
  return `<div class="section"><h4>Contact &amp; links</h4>
    <div class="facts" style="margin-bottom:10px">
      <div class="fact"><div class="k">How to apply</div><div class="v">${esc(method)}</div></div>
      <div class="fact"><div class="k">Company site</div><div class="v">${site ? `<a href="${esc(site)}" target="_blank" rel="noopener noreferrer">${esc(site.replace(/^https?:\/\//, ""))}</a>` : '<span class="muted">—</span>'}${li ? ` · <a href="${esc(li)}" target="_blank" rel="noopener noreferrer">LinkedIn</a>` : ""}</div></div>
      ${j.contact_names ? `<div class="fact"><div class="k">Named contact</div><div class="v">${esc(j.contact_names)}</div></div>` : ""}
    </div>
    ${j.apply_instructions ? `<blockquote class="quote">${esc(j.apply_instructions)}</blockquote>` : ""}
    ${emailRows ? `<div class="clist">${emailRows}</div>` : `<p class="muted">No email addresses in this posting.</p>`}
    ${linkRows ? `<details class="links" ${links.length <= 6 ? "open" : ""}><summary>${links.length} link${links.length === 1 ? "" : "s"} in the posting</summary><div class="clist">${linkRows}</div></details>` : ""}
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
      <button class="btn sm" id="dFetch" title="Download the job's own page (robots.txt permitting) for more emails, links, pay and the full text">Fetch full posting</button>
      <span class="muted" id="dFetchMsg" style="font-size:12px">${esc(pfText)}</span></div>
  </div>`;
}

function bindDetail(el, j) {
  $$("[data-copytext]", el).forEach((b) => (b.onclick = () => navigator.clipboard.writeText(b.dataset.copytext).then(() => toast("Copied " + b.dataset.copytext))));
  $("#dFetch", el)?.addEventListener("click", async (e) => {
    const btn = e.target; btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Fetching…';
    try {
      const r = await api(`/api/jobs/${j.job_id}/fetch`, { method: "POST", body: {} });
      const added = (r.email_count || 0) - (j.email_count || 0);
      toast(r.page_fetch.status === "ok" ? `Posting fetched${added > 0 ? ` — ${added} new email${added > 1 ? "s" : ""}` : ""}` : "Couldn't fetch: " + r.page_fetch.status.replace(/_/g, " "));
      if (S.sel === j.job_id) { S.detail = r; el.innerHTML = detailHtml(r); bindDetail(el, r); }
    } catch (err) { toast("Fetch failed: " + err.message); btn.disabled = false; btn.textContent = "Fetch full posting"; }
  });
  $$("img[data-initial]", el).forEach((img) => img.addEventListener("error", () => img.replaceWith(document.createTextNode(img.dataset.initial))));
  $("#dClose", el)?.addEventListener("click", closeDetail);
  $$("[data-st]", el).forEach((b) => (b.onclick = () => {
    const st = b.dataset.st === j.track || (b.dataset.st === "hidden" && j.track === "hidden") ? "" : b.dataset.st;
    quickStatus(j.job_id, st);
  }));
  $("#dMore", el)?.addEventListener("click", (e) => { $("#dDesc").classList.remove("clamp"); e.target.remove(); });
  const notes = $("#dNotes", el), hint = $("#dNotesHint", el);
  const saveNotes = debounce(async () => {
    try { await api(`/api/jobs/${j.job_id}/track`, { method: "POST", body: { notes: notes.value } }); hint.textContent = "Saved"; updateLocal(j.job_id, { has_notes: !!notes.value }); }
    catch (e) { hint.textContent = "Couldn't save: " + e.message; }
  }, 600);
  notes.addEventListener("input", () => { hint.textContent = "Saving…"; saveNotes(); });
}

function updateLocal(id, patch) {
  const j = S.jobs.find((x) => x.job_id === id);
  if (j) Object.assign(j, patch);
}

async function quickStatus(id, status) {
  const prev = (S.jobs.find((x) => x.job_id === id) || S.detail || {}).track || "";
  try {
    await api(`/api/jobs/${id}/track`, { method: "POST", body: { status } });
  } catch (e) { return toast("Couldn't update: " + e.message); }
  updateLocal(id, { track: status });
  refreshCounts();
  const undo = { label: "Undo", fn: () => quickStatus(id, prev) };
  if (status === "hidden" && (S.view === "foryou" || S.view === "all")) {
    const idx = S.jobs.findIndex((x) => x.job_id === id);
    S.jobs = S.jobs.filter((x) => x.job_id !== id); S.total--;
    renderList();
    if (S.sel === id) { const nxt = S.jobs[idx] || S.jobs[idx - 1]; nxt ? openDetail(nxt.job_id) : closeDetail(); }
    toast("Hidden", undo);
    return;
  }
  if (S.view === "pipeline") return renderPipeline();
  if ($("#listcol")) renderList();
  if (S.sel === id) openDetail(id);
  toast(status ? `Marked as ${status}` : "Status cleared", undo);
}

// ---------------------------------------------------------------- new & changes
async function renderNew() {
  $("#main").innerHTML = `<div class="page"><div class="page-inner" id="pg"><div class="skeleton"></div><div class="skeleton"></div></div></div>`;
  const [n, c] = await Promise.all([api("/api/new?limit=60"), api("/api/changes")]).catch((e) => { toast(e.message); return [{}, {}]; });
  const tracked = (c.changes || []).filter((x) => x.track);
  $("#pg").innerHTML = `
    <div class="listbar"><h2>New since the last refresh</h2><span class="muted">${n.run_started ? "run started " + esc(ago(n.run_started)) : ""}</span></div>
    ${tracked.length ? `<div class="box" style="margin-bottom:16px"><h3>Updates to jobs you're tracking</h3>${tracked.map(changeRow).join("")}</div>` : ""}
    ${(n.jobs || []).length ? `<div id="newlist">${n.jobs.map(cardHtml).join("")}</div>` : `<div class="empty"><h3>No new good matches yet</h3><p>Hit Refresh to fetch the latest jobs. New jobs scoring 50+ appear here.</p></div>`}`;
  S.jobs = n.jobs || [];
  $$("#newlist .card").forEach((el) => el.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act, id = el.dataset.id;
    if (act === "save") return quickStatus(id, "saved");
    if (act === "hide") return quickStatus(id, "hidden").then(() => el.remove());
    openModalDetail(id);
  }));
}
function changeRow(c) {
  const fmt = (v) => (v && /^\d+(\.0)?$/.test(v) ? "$" + k(+v) : v || "—");
  return `<div class="change"><span class="f">${esc(c.field.replace(/_usd_annual|_/g, " ").trim())}</span>
    <a class="t" href="#" data-open="${esc(c.job_id)}">${esc(c.title || c.job_id)} <span class="muted">· ${esc(c.company || "")}</span></a>
    <span class="v">${esc(fmt(c.old))} → <b>${esc(fmt(c.new))}</b></span></div>`;
}
async function renderChanges() {
  $("#main").innerHTML = `<div class="page"><div class="page-inner" id="pg"><div class="skeleton"></div></div></div>`;
  const c = await api("/api/changes").catch((e) => ({ changes: [], error: e.message }));
  const by = {};
  (c.changes || []).forEach((x) => (by[x.field] = (by[x.field] || 0) + 1));
  $("#pg").innerHTML = `<div class="listbar"><h2>What changed</h2><span class="muted">since ${esc(c.since ? new Date(c.since).toLocaleString() : "the previous run")}</span></div>
    <div class="chips" style="margin-bottom:12px">${Object.entries(by).map(([f, n]) => `<span class="pill">${esc(f.replace(/_/g, " "))} · ${n}</span>`).join("")}</div>
    <div class="box">${(c.changes || []).length ? c.changes.map(changeRow).join("") : `<div class="empty"><h3>Nothing changed</h3><p>Changes appear after two or more refreshes: closed jobs, pay edits, title and location changes.</p></div>`}</div>`;
  $$("[data-open]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openModalDetail(a.dataset.open); }));
}

// detail as a modal (used outside the 3-column search layout)
async function openModalDetail(id) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true" style="width:min(820px,100%)"><div id="detail"></div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  S.sel = id;
  await openDetail(id);
  const close = $("#dClose", ov);
  if (close) { close.style.display = "inline-flex"; close.onclick = () => ov.remove(); }
}

// ---------------------------------------------------------------- pipeline
async function renderPipeline() {
  $("#main").innerHTML = `<div class="page"><div class="page-inner" style="max-width:1400px" id="pg"><div class="skeleton"></div></div></div>`;
  const p = await api("/api/pipeline").catch((e) => { toast(e.message); return { columns: {}, counts: {} }; });
  const total = Object.values(p.counts).reduce((a, b) => a + b, 0);
  $("#pg").innerHTML = `<div class="listbar"><h2>My pipeline</h2><span class="muted">${total} jobs · drag cards between columns</span></div>
    ${total ? "" : `<div class="empty" style="padding:24px"><h3>Nothing tracked yet</h3><p>Press ☆ on a job (or <kbd>s</kbd>) to save it. Move it along as you apply and interview.</p></div>`}
    <div class="board">${STATUSES.map((s) => `<div class="col" data-col="${s}"><h3><span>${s}</span><span class="muted">${p.counts[s] || 0}</span></h3>
      ${(p.columns[s] || []).map((j) => `<div class="mini" draggable="true" data-id="${esc(j.job_id)}"><b>${esc(j.title)}</b>
        <div class="s">${esc(j.company)} · ${esc(place(j))}</div><div class="s">${pay(j) ? esc(pay(j)) + " · " : ""}match ${j.match}${j.status === "closed" ? " · <span style='color:var(--bad)'>closed</span>" : ""}</div></div>`).join("")}
      </div>`).join("")}</div>`;
  $$(".mini").forEach((m) => {
    m.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", m.dataset.id); e.dataTransfer.effectAllowed = "move"; });
    m.addEventListener("click", () => openModalDetail(m.dataset.id));
  });
  $$(".col").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", (e) => { e.preventDefault(); col.classList.remove("drop"); quickStatus(e.dataTransfer.getData("text/plain"), col.dataset.col); });
  });
}

// ---------------------------------------------------------------- insights
async function renderInsights() {
  $("#main").innerHTML = `<div class="page"><div class="page-inner" id="pg"><div class="kpis">${'<div class="skeleton"></div>'.repeat(4)}</div></div></div>`;
  const s = await api("/api/stats?min_match=60").catch((e) => { toast(e.message); return null; });
  if (!s) return;
  const bars = (rows, label, val, cls) => {
    const max = Math.max(1, ...rows.map(val));
    return `<div class="bars">${rows.map((r) => `<div class="bar"><span class="lbl" title="${esc(label(r))}">${esc(label(r))}</span><span class="track"><span class="fill ${cls ? cls(r) : ""}" style="width:${(100 * val(r)) / max}%"></span></span><span class="num">${val(r)}</span></div>`).join("")}</div>`;
  };
  const remote = (s.workplace.find(([w]) => w === "Remote") || [0, 0])[1];
  const pipe = Object.values(s.pipeline).reduce((a, b) => a + b, 0);
  $("#pg").innerHTML = `
    <div class="listbar"><h2>Your market</h2><span class="muted">based on ${s.matches.toLocaleString()} jobs scoring 60+ for your profile</span></div>
    <div class="kpis">
      <div class="kpi"><div class="k">Good matches</div><div class="v">${s.matches.toLocaleString()}</div><div class="s">of ${s.open_total.toLocaleString()} open jobs</div></div>
      <div class="kpi"><div class="k">Median pay</div><div class="v">${s.salary.median ? "$" + k(s.salary.median) : "—"}</div><div class="s">${s.salary.p25 ? `$${k(s.salary.p25)}–$${k(s.salary.p75)} middle half · ${s.salary.jobs_with_pay} list pay` : "not enough pay data"}</div></div>
      <div class="kpi"><div class="k">Remote</div><div class="v">${s.matches ? Math.round((100 * remote) / s.matches) : 0}%</div><div class="s">${remote.toLocaleString()} remote matches</div></div>
      <div class="kpi"><div class="k">In your pipeline</div><div class="v">${pipe}</div><div class="s">${s.pipeline.applied || 0} applied · ${s.pipeline.interviewing || 0} interviewing</div></div>
    </div>
    <div class="grid2">
      <div class="box"><h3>Most-requested skills in your matches</h3>${bars(s.top_skills.slice(0, 15), (r) => r.skill, (r) => r.count, (r) => (r.yours ? "yours" : ""))}
        <div class="legend"><span><i style="background:var(--good)"></i>You have it</span><span><i style="background:var(--accent)"></i>You don't (yet)</span></div></div>
      <div class="box"><h3>Skills worth adding</h3><p class="muted" style="margin-top:-6px">Frequent in your matches but not in your profile. Add the ones you have to rank jobs better.</p>
        <div class="chips" id="learn">${s.skills_to_learn.map((x) => `<span class="chip" data-skill="${esc(x)}" title="Add to my skills">＋ ${esc(x)}</span>`).join("")}</div>
        <h3 style="margin-top:20px">Who's hiring for you</h3>${bars(s.top_companies.slice(0, 8), (r) => r[0], (r) => r[1])}</div>
      <div class="box"><h3>Level mix</h3>${bars(s.seniority, (r) => r[0], (r) => r[1])}</div>
      <div class="box"><h3>Workplace mix</h3>${bars(s.workplace, (r) => r[0], (r) => r[1])}
        <h3 style="margin-top:20px">Pay (annual USD midpoint)</h3>${s.salary.median ? bars([["25th percentile", s.salary.p25], ["Median", s.salary.median], ["75th percentile", s.salary.p75], ["90th percentile", s.salary.p90]], (r) => r[0], (r) => r[1]).replace(/<span class="num">(\d+)<\/span>/g, (m, n) => `<span class="num">$${k(+n)}</span>`) : '<p class="muted">Not enough pay data.</p>'}</div>
    </div>`;
  $$("#learn .chip").forEach((c) => (c.onclick = async () => {
    await saveProfile({ skills: [...S.profile.skills, c.dataset.skill] });
    c.remove(); toast(`Added ${c.dataset.skill} to your skills`);
  }));
}

// ---------------------------------------------------------------- API page
function renderApi() {
  const base = location.origin;
  $("#main").innerHTML = `<div class="page"><div class="page-inner" style="max-width:860px">
    <div class="listbar"><h2>Use it from an AI or script</h2></div>
    <div class="box" style="margin-bottom:14px"><h3>Option 1 — MCP (best for Claude)</h3>
      <p class="muted">Gives Claude tools like <code>search_jobs</code>, <code>get_job</code>, <code>update_job_status</code>, <code>get_insights</code> and <code>refresh_jobs</code>. Works even when this web app isn't running.</p>
      <p><b>Claude Code</b> — run once in a terminal:</p>
      <div class="code" id="mcp1">claude mcp add jobintake -- python3 ${esc(S.meta?.app_path || "~/jobintake")}/jobintake.py mcp<button class="btn sm copy" data-copy="mcp1">Copy</button></div>
      <p style="margin-top:14px"><b>Claude Desktop</b> — add to <code>claude_desktop_config.json</code> under <code>mcpServers</code>:</p>
      <div class="code" id="mcp2">"jobintake": {
  "command": "python3",
  "args": ["${esc(S.meta?.app_path || "/Users/you/jobintake")}/jobintake.py", "mcp"]
}<button class="btn sm copy" data-copy="mcp2">Copy</button></div>
      <p class="muted" style="margin-top:10px">Then ask things like “find remote Python data jobs over $150k posted this week and save the top 3”.</p></div>
    <div class="box" style="margin-bottom:14px"><h3>Option 2 — REST API</h3>
      <p class="muted">Any agent or script can call it while this app is running. Full reference and OpenAPI spec: <a href="/docs">/docs</a> · <a href="/api/openapi.json">/api/openapi.json</a></p>
      <div class="code" id="c1">curl '${esc(base)}/api/jobs?q=python&amp;workplace=Remote&amp;min_match=70&amp;limit=5'
curl '${esc(base)}/api/jobs/&lt;job_id&gt;'
curl -X POST '${esc(base)}/api/jobs/&lt;job_id&gt;/track' -H 'Content-Type: application/json' -d '{"status":"applied","notes":"Referred by Sam"}'
curl -X PATCH '${esc(base)}/api/profile' -H 'Content-Type: application/json' -d '{"skills":["Python","SQL"],"min_salary":140000}'
curl -X POST '${esc(base)}/api/collect' -H 'Content-Type: application/json' -d '{}'<button class="btn sm copy" data-copy="c1">Copy</button></div></div>
    <div class="box"><h3>Option 3 — files</h3><p class="muted">The command line still works: <code>python3 jobintake.py run</code> writes <code>jobs.csv</code>, and <code>jobs.db</code> is a plain SQLite file any tool can read.</p></div>
  </div></div>`;
  $$("[data-copy]").forEach((b) => (b.onclick = () => {
    const el = $("#" + b.dataset.copy);
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("");
    navigator.clipboard.writeText(text.trim()).then(() => toast("Copied"), () => toast("Copy failed — select the text instead"));
  }));
}

// ---------------------------------------------------------------- profile
function topSkillSuggestions() {
  return ["Python", "SQL", "TypeScript", "JavaScript", "React", "Go", "Java", "AWS", "GCP", "Kubernetes", "Docker", "PostgreSQL",
    "PyTorch", "LLMs", "Spark", "Airflow", "dbt", "Snowflake", "Terraform", "Node.js", "Rust", "C++", "Tableau", "Pandas/NumPy"];
}

function tagInput(host, initial, suggestions) {
  let vals = [...initial];
  host.innerHTML = `<div class="tags"><input type="text" placeholder="Type and press Enter"></div><div class="suggest"></div>`;
  const box = $(".tags", host), inp = $("input", host), sug = $(".suggest", host);
  const draw = () => {
    $$(".tag", box).forEach((t) => t.remove());
    vals.forEach((v, i) => {
      const t = document.createElement("span");
      t.className = "tag";
      t.innerHTML = `${esc(v)}<button type="button" aria-label="Remove ${esc(v)}">×</button>`;
      t.querySelector("button").onclick = () => { vals.splice(i, 1); draw(); };
      box.insertBefore(t, inp);
    });
    const low = new Set(vals.map((v) => v.toLowerCase()));
    sug.innerHTML = (suggestions || []).filter((s) => !low.has(s.toLowerCase())).slice(0, 14).map((s) => `<button type="button">+ ${esc(s)}</button>`).join("");
    $$("button", sug).forEach((b) => (b.onclick = () => { vals.push(b.textContent.slice(2)); draw(); }));
  };
  const add = () => {
    inp.value.split(",").map((x) => x.trim()).filter(Boolean).forEach((x) => { if (!vals.some((v) => v.toLowerCase() === x.toLowerCase())) vals.push(x); });
    inp.value = ""; draw();
  };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    else if (e.key === "Backspace" && !inp.value && vals.length) { vals.pop(); draw(); }
  });
  inp.addEventListener("blur", () => inp.value && add());
  box.addEventListener("click", () => inp.focus());
  draw();
  return { value: () => (inp.value && add(), vals) };
}

async function saveProfile(patch) {
  S.profile = await api("/api/profile", { method: "PATCH", body: patch });
  refreshCounts();
  if (S.view === "foryou" || S.view === "all") loadJobs(true); else render();
}

function openProfile() {
  const p = S.profile, m = S.meta || {};
  const ov = document.createElement("div");
  ov.className = "overlay";
  const chipSet = (name, opts, sel) => `<div class="chips" data-name="${name}">${opts.map((o) => `<span class="chip ${sel.includes(o) ? "on" : ""}" data-v="${esc(o)}" tabindex="0" role="checkbox" aria-checked="${sel.includes(o)}">${esc(o)}</span>`).join("")}</div>`;
  ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="pTitle">
    <header><h2 id="pTitle">Your profile</h2><button class="btn icon ghost" id="pX" aria-label="Close">✕</button></header>
    <div class="body"><div class="formgrid">
      <div class="full"><span class="flabel">Your skills</span><div id="pSkills"></div><div class="help">Biggest effect on ranking. Add languages, frameworks, tools.</div></div>
      <div class="full"><span class="flabel">Fields you want</span>${chipSet("functions", (m.functions || []).filter((f) => f !== "Other"), p.functions)}</div>
      <div class="full"><span class="flabel">Narrow to specialties (optional)</span><div id="pSubs"></div>
        <div class="help">Leave empty to accept any specialty within your fields.</div></div>
      <div class="full"><span class="flabel">Level</span>${chipSet("seniority", m.seniorities || [], p.seniority)}<div class="help">Empty = any level.</div></div>
      <div class="full"><span class="flabel">Where</span><div class="seg" id="pWp">${[["remote_only", "Remote only"], ["remote_or_near", "Remote or near me"], ["anywhere", "Anywhere"]].map(([v, l]) => `<button type="button" data-v="${v}" class="${p.workplace === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div><label class="flabel" for="pCity">City</label><input class="input" id="pCity" value="${esc(p.home_city)}" placeholder="e.g. Austin"></div>
      <div><label class="flabel" for="pState">State / region</label><input class="input" id="pState" value="${esc(p.home_state)}" placeholder="e.g. Texas"></div>
      <div><label class="flabel" for="pCountry">Country</label><input class="input" id="pCountry" value="${esc(p.home_country)}"></div>
      <div><label class="flabel" for="pPay">Minimum pay (USD / year)</label><input class="input" id="pPay" type="number" min="0" step="5000" value="${p.min_salary || ""}" placeholder="e.g. 140000"></div>
      <div class="full"><span class="flabel">Boost jobs that mention</span><div id="pInc"></div></div>
      <div class="full"><span class="flabel">Hide jobs whose title contains</span><div id="pExc"></div></div>
      <div><span class="flabel">Favorite companies</span><div id="pFav"></div></div>
      <div><span class="flabel">Never show companies</span><div id="pExCo"></div></div>
      <div><label class="check"><input type="checkbox" id="pVisa" ${p.needs_visa ? "checked" : ""}> I need visa sponsorship</label></div>
      <div><label class="flabel" for="pRefresh">Auto-refresh jobs every</label><select class="input" id="pRefresh">${[[0, "Never (manual only)"], [6, "6 hours"], [12, "12 hours"], [24, "Day"], [72, "3 days"]].map(([v, l]) => `<option value="${v}" ${+p.auto_refresh_hours === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <div class="help">Runs while the app is open.</div></div>
    </div></div>
    <footer><button class="btn" id="pCancel">Cancel</button><button class="btn primary" id="pSave">Save &amp; re-rank</button></footer></div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  $("#pX", ov).onclick = close; $("#pCancel", ov).onclick = close;
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  let subsSel = [...p.subfunctions];
  const bindChips = (scope) => $$(".chips[data-name] .chip", scope).forEach((c) => {
    const t = () => {
      c.classList.toggle("on"); c.setAttribute("aria-checked", c.classList.contains("on"));
      const group = c.closest(".chips").dataset.name;
      if (group === "subfunctions") subsSel = $$('.chips[data-name="subfunctions"] .chip.on', ov).map((x) => x.dataset.v);
      if (group === "functions") drawSubs();
    };
    c.onclick = t; c.onkeydown = (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); t(); } };
  });
  const drawSubs = () => {
    const fields = $$('.chips[data-name="functions"] .chip.on', ov).map((x) => x.dataset.v);
    const opts = fields.flatMap((f) => (m.subfunction_map || {})[f] || []).filter((s) => s !== "Other");
    subsSel = subsSel.filter((s) => opts.includes(s));
    $("#pSubs", ov).innerHTML = opts.length ? chipSet("subfunctions", opts, subsSel) : '<span class="muted">Pick a field first.</span>';
    bindChips($("#pSubs", ov));
  };
  bindChips(ov);
  drawSubs();
  let wp = p.workplace;
  $$("#pWp button", ov).forEach((b) => (b.onclick = () => { wp = b.dataset.v; $$("#pWp button", ov).forEach((x) => x.classList.toggle("on", x === b)); }));
  const skills = tagInput($("#pSkills", ov), p.skills, topSkillSuggestions());
  const inc = tagInput($("#pInc", ov), p.include_keywords, ["machine learning", "startup", "climate", "fintech", "open source"]);
  const exc = tagInput($("#pExc", ov), p.exclude_keywords, ["intern", "principal", "director", "sales", "clearance"]);
  const fav = tagInput($("#pFav", ov), p.favorite_companies, []);
  const exco = tagInput($("#pExCo", ov), p.exclude_companies, []);
  $("#pSave", ov).onclick = async () => {
    const picked = (name) => $$(`.chips[data-name="${name}"] .chip.on`, ov).map((c) => c.dataset.v);
    try {
      await saveProfile({
        functions: picked("functions"), subfunctions: picked("subfunctions"), seniority: picked("seniority"),
        skills: skills.value(), include_keywords: inc.value(), exclude_keywords: exc.value(),
        favorite_companies: fav.value(), exclude_companies: exco.value(), workplace: wp,
        home_city: $("#pCity", ov).value.trim(), home_state: $("#pState", ov).value.trim(), home_country: $("#pCountry", ov).value.trim(),
        min_salary: +$("#pPay", ov).value || 0, needs_visa: $("#pVisa", ov).checked, auto_refresh_hours: +$("#pRefresh", ov).value,
      });
      close(); toast("Profile saved — jobs re-ranked");
    } catch (e) { toast("Couldn't save: " + e.message); }
  };
  setTimeout(() => $("#pSkills input", ov)?.focus(), 50);
}
$("#profileBtn").onclick = openProfile;

// ---------------------------------------------------------------- refresh (collection)
let pollTimer = null;
function refreshLabel(st) {
  const btn = $("#refreshBtn"), lbl = $("#refreshLabel");
  if (st.running) { lbl.innerHTML = `<span class="spin"></span> Collecting…`; btn.classList.add("on"); }
  else {
    btn.classList.remove("on");
    const last = S.meta?.last_run?.finished;
    lbl.textContent = last ? "Updated " + ago(last) : "Refresh";
  }
}
function openRefreshPop() {
  let pop = $("#refreshPop");
  if (pop) { pop.remove(); return; }
  pop = document.createElement("div");
  pop.className = "pop"; pop.id = "refreshPop";
  document.body.appendChild(pop);
  drawPop();
  document.addEventListener("mousedown", function off(e) { if (!pop.contains(e.target) && !$("#refreshBtn").contains(e.target)) { pop.remove(); document.removeEventListener("mousedown", off); } });
}
async function drawPop(st) {
  const pop = $("#refreshPop");
  if (!pop) return;
  st = st || await api("/api/collect");
  const last = S.meta?.last_run;
  pop.innerHTML = st.running
    ? `<b>Collecting fresh jobs…</b><div class="progress"><i></i></div><div class="logbox" id="logbox">${esc(st.log.join("\n"))}</div>
       <div style="display:flex;justify-content:space-between;margin-top:10px"><span class="muted">Takes about 4 minutes. You can keep browsing.</span><button class="btn sm" id="stopBtn">Stop</button></div>`
    : `<b>Job data</b><p class="muted" style="margin:6px 0 12px">${last ? `Last run ${esc(ago(last.started))}: ${last.fetched.toLocaleString()} postings, ${last.new} new, ${last.closed} closed.` : "No runs yet."}</p>
       ${st.result && !st.result.ok ? `<p style="color:var(--bad)">${esc(st.result.error)}</p>` : ""}
       <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="goBtn">Collect fresh jobs now</button>
       <button class="btn" id="pagesBtn" title="Download the posting pages of your 50 best matches for extra emails, links and pay">Deep-fetch top 50 matches</button></div>
       ${st.log.length ? `<details style="margin-top:12px"><summary class="muted">Last run log</summary><div class="logbox">${esc(st.log.join("\n"))}</div></details>` : ""}`;
  const lb = $("#logbox"); if (lb) lb.scrollTop = lb.scrollHeight;
  $("#goBtn")?.addEventListener("click", startCollect);
  $("#pagesBtn")?.addEventListener("click", async () => {
    try { const r = await api("/api/fetch-postings", { method: "POST", body: { min_match: 60, limit: 50 } });
      if (!r.started) return toast(r.reason);
      toast(`Fetching ${r.jobs} posting pages in the background`); poll(); } catch (e) { toast(e.message); }
  });
  $("#stopBtn")?.addEventListener("click", async () => { await api("/api/collect/stop", { method: "POST", body: {} }); toast("Stopping — keeping what's been fetched"); });
}
async function startCollect() {
  try { await api("/api/collect", { method: "POST", body: {} }); } catch (e) { return toast(e.message); }
  toast("Collecting fresh jobs in the background");
  poll();
}
function poll() {
  clearInterval(pollTimer);
  const tick = async () => {
    let st;
    try { st = await api("/api/collect"); } catch { return; }
    refreshLabel(st);
    drawPop(st);
    if (!st.running) {
      clearInterval(pollTimer); pollTimer = null;
      await loadMeta();
      refreshLabel(st);
      const r = S.meta?.last_run;
      if (st.result?.fetched) toast("Posting pages: " + Object.entries(st.result.fetched).map(([a, b]) => `${b} ${a.replace(/_/g, " ")}`).join(", "));
      else if (st.result?.ok && r) toast(`Done: ${r.new} new jobs, ${r.closed} closed`, { label: "See new", fn: () => setView("new") });
      else if (st.result && !st.result.ok) toast("Collection failed: " + st.result.error);
      refreshCounts();
      if (S.view === "foryou" || S.view === "all") loadJobs(true);
    }
  };
  tick();
  pollTimer = setInterval(tick, 2000);
}
$("#refreshBtn").onclick = openRefreshPop;

// ---------------------------------------------------------------- counts & meta
async function loadMeta() {
  [S.meta, S.profile] = await Promise.all([api("/api/meta"), api("/api/profile")]);
}
async function refreshCounts() {
  try {
    const [p, n] = await Promise.all([api("/api/pipeline"), api("/api/new?limit=1")]);
    $("#c-pipeline").textContent = Object.values(p.counts).reduce((a, b) => a + b, 0) || "";
    $("#c-new").textContent = n.total || "";
  } catch { /* non-critical */ }
}

// ---------------------------------------------------------------- search tips
const TIPS = [
  ["python django", "all words (also finds “engineering” for “engineer”)"],
  ['"machine learning"', "exact phrase"], ["react OR vue", "either word"], ["-senior", "exclude a word"],
  ["k8s", "synonyms expand automatically (→ kubernetes)"], ["title:staff", "word in the title"],
  ["company:stripe", "company"], ["loc:berlin", "location"], ["skill:rust", "skill"], ["is:remote", "is:hybrid / is:onsite"],
  ["level:senior", "seniority"], ["field:data", "job field"], ["pay:150k+", "minimum annual pay (USD)"], ["posted:7d", "posted within 7 days"],
  ["has:apply-email", "has an email to apply to"], ["has:email", "a recruiting/contact email (not boilerplate)"], ["domain:stripe.com", "company or email domain"],
  ["email:recruiting", "search inside emails & links"], ["status:saved", "your tracked jobs"],
];
function openTips() {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true" style="width:min(640px,100%)"><header><h2>Search tips</h2><button class="btn icon ghost" aria-label="Close">✕</button></header>
    <div class="body"><p class="muted" style="margin-top:0">Combine anything, e.g. <code>"data engineer" is:remote pay:150k+ -contract has:email</code>. Click an example to try it.</p>
    <div class="tips">${TIPS.map(([q, d]) => `<button class="tip" data-q="${esc(q)}"><code>${esc(q)}</code><span>${esc(d)}</span></button>`).join("")}</div></div></div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  $("header button", ov).onclick = close;
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  $$(".tip", ov).forEach((b) => (b.onclick = () => { close(); $("#q").value = b.dataset.q; S.q = b.dataset.q; if (S.view !== "foryou" && S.view !== "all") setView("foryou"); else loadJobs(true); }));
}
document.addEventListener("click", (e) => { const t = e.target.closest("[data-help]"); if (t) { e.preventDefault(); openTips(); } });

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (e.key === "Escape") {
    const ov = $(".overlay"); if (ov) return ov.remove();
    if ($("#refreshPop")) return $("#refreshPop").remove();
    if (typing) return document.activeElement.blur();
    if (S.sel) return closeDetail();
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("#q").focus(); $("#q").select(); return; }
  if (!(S.view === "foryou" || S.view === "all") || $(".overlay")) return;
  const i = S.jobs.findIndex((j) => j.job_id === S.sel);
  if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); const n = S.jobs[Math.min(S.jobs.length - 1, i + 1)]; if (n) openDetail(n.job_id); }
  else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); const n = S.jobs[Math.max(0, i - 1)]; if (n) openDetail(n.job_id); }
  else if (e.key === "s" && S.sel) quickStatus(S.sel, S.jobs[i]?.track === "saved" ? "" : "saved");
  else if (e.key === "x" && S.sel) quickStatus(S.sel, "hidden");
  else if (e.key === "a" && S.sel) quickStatus(S.sel, "applied");
  else if (e.key === "o" && S.detail) { const u = safeUrl(S.detail.apply_url) || safeUrl(S.detail.url); if (u) window.open(u, "_blank", "noopener"); }
});

// ---------------------------------------------------------------- boot
(async function boot() {
  try { await loadMeta(); } catch (e) {
    $("#main").innerHTML = `<div class="empty"><h3>Can't reach the jobintake server</h3><p>${esc(e.message)}</p></div>`;
    return;
  }
  if (!S.meta.jobs) {
    $("#main").innerHTML = `<div class="page"><div class="empty"><h3>No jobs yet</h3><p>Collect jobs from 40+ sources to get started (about 4 minutes).</p><button class="btn primary" id="first">Collect jobs</button></div></div>`;
    $("#first").onclick = () => { startCollect(); openRefreshPop(); };
  } else {
    setView(S.view);
  }
  const st = await api("/api/collect");
  refreshLabel(st);
  if (st.running) poll();
  refreshCounts();
  setInterval(() => { if (!pollTimer) api("/api/collect").then((s) => { if (s.running) poll(); else refreshLabel(s); }).catch(() => {}); }, 60000);
})();
