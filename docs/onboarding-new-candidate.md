# Onboarding a new candidate — re-tune cvflow to your own profile

cvflow was built around one person's profile. Everything it does — which jobs it ranks,
how it scores fit, what it writes on forms, the résumé it tailors — is driven by a small
set of **candidate files** committed in the repo. To use this project for *yourself*, you
replace those files with your own.

This guide gives you a single, self-contained prompt to paste into any capable LLM
(Claude, ChatGPT, Gemini — anything that accepts file uploads), along with your résumé(s).
The LLM returns the full contents of every candidate file, which you drop into the repo.
No secrets are involved — the LLM never sees or produces API keys.

> **Scope / assumption:** the engine is **India / INR-focused** (salary floor, comp score,
> and the M/N salary split are all in lakhs-per-annum). If you're outside India you can
> still use it, but the salary numbers and some defaults assume INR; deeper localization
> (currency/FX) is a code change, out of scope here.

---

## 1. The files that define a candidate

These — and *only* these — encode "who the candidate is." Everything else in the repo is
generic machinery.

| Path | What it holds | The LLM produces it |
|---|---|---|
| `profile/skills.md` | technical skills + proficiency, grouped | ✅ |
| `profile/experience.md` | employment history: roles, dates, responsibilities, impact | ✅ |
| `profile/education.md` | degrees, institutions, dates | ✅ |
| `profile/personality.md` | work style, goals, culture-fit signals (your voice) | ✅ |
| `profile/preferences.md` | soft ranking signals (company type, role leaning, location) | ✅ |
| `profile/projects.md` | summary index of showcase projects | ✅ |
| `profile/projects/<slug>.md` | one detail file per project | ✅ (one per project) |
| `profile/essay_answers.md` | reusable application-essay answers, in your voice | ✅ (skeleton; you fill prose) |
| `profile/form_fields.json` | structured recurring form values (name/email/links/…) | ✅ |
| `profile/candidate_skills.yaml` | **the skill set + synonyms the hard "must-have" gate matches against** | ✅ (must stay in sync with `skills.md`) |
| `config.yaml` → `discovery:` + `preferences:` blocks | search terms, role weights, salary floor, YOE, deal-breakers | ✅ (non-secret blocks only) |
| `resume/master.tex` heading + `resume/sections/*.tex` | your master LaTeX résumé content | ✅ (content only; keep the template/macros) |

**Not candidate files — do not regenerate:** `profile/README.md`, `resume/README.md`
(generic docs), anything under `src/`, and the **secret** parts of `config.yaml`
(`telegram`, `auth`, all `llm.*.api_key`, `security`). The LLM never touches secrets.

Two things matter more than the rest, because the discovery engine depends on them
mechanically (see `docs/discovery-engine.md`):
- **`candidate_skills.yaml`** drives the deterministic skill gate. If a real skill is
  missing here, good jobs get dropped; it must mirror `skills.md`.
- **`config.yaml` `preferences:`** holds the hard gates (seniority, YOE, country, salary
  floor) and `prefer_roles` (the weights that dominate fit ranking).

---

## 2. What to gather before you start

Upload these to the LLM along with the prompt in §3:

1. **Your résumé(s)** — PDF, DOCX, or pasted text. Multiple versions are fine and
   encouraged (the LLM will merge them into one factual superset). These are the **only**
   source of skills/experience facts.
2. **Optional but helpful:** your LinkedIn URL, GitHub URL, portfolio URL, a list of your
   public project repos, and any preferences you already know (target roles, minimum
   salary in LPA, locations you'll accept, notice period, work authorization).
3. **Decide a few knobs** (or let the LLM propose defaults you then adjust):
   - target **role families** and rough priority,
   - **minimum acceptable salary** in LPA (the floor below which jobs are dropped),
   - **years of experience** you have,
   - **locations** (e.g. Remote + your metros).

---

## 3. The prompt — paste this into the LLM with your files attached

> Copy everything inside the box. Attach your résumé(s) first.

```text
You are configuring an open-source, self-hosted job-application agent called "cvflow"
for a NEW candidate (me). You have NO prior knowledge of this project or of me — work
only from the résumé file(s) I have attached and the facts I give you in this prompt.
Your job is to produce the full contents of a set of candidate-specific files that I will
paste verbatim into the project. Do not produce any code, secrets, or API keys.

## How cvflow uses these files (so you produce correct output)
cvflow scrapes job postings daily, then RANKS them for me. It enforces my HARD
requirements (seniority, years of experience, country, mandatory skills, salary floor) in
deterministic code, and uses an LLM only to score "fit" and tailor a résumé. Because of
this:
- NEVER invent, infer, or embellish any fact about me. Use ONLY what the résumé(s) state.
  If a value isn't stated, leave it blank (per the rules below) — do not guess.
- A curated skill list (candidate_skills.yaml) is matched against each job's mandatory
  skills by EXACT string + synonyms; if I'm missing >50% of a job's must-haves it's
  dropped. So that list must be accurate and reasonably complete, with common aliases.
- "prefer_roles" weights drive ranking: a role family I want weighted high surfaces; an
  unrelated one weighted low sinks. The system is India/INR-focused (salary in lakhs/annum).

## Output format (STRICT)
Output each file in its own fenced code block. Immediately BEFORE each block, put a line:
=== FILE: <relative/path> ===
Produce ALL of the files listed below, in order, complete and ready to paste. After all
files, add a "REVIEW THESE" section listing every value you had to estimate or assume
(especially salary/role weights) so I can correct them.

## Facts I'm giving you (fill any the résumé doesn't cover; ask me nothing — leave unknowns blank)
- Full name, email, phone, city/state/country: <fill or leave from résumé>
- LinkedIn / GitHub / portfolio URLs: <fill or blank>
- Current title & company: <from résumé>
- Years of professional experience (number): <fill>
- Target role families (pick from this fixed list and rank them): devops, sre, platform,
  infra, backend, fullstack, frontend, network, sysadmin, data, security, other
- Minimum acceptable salary in LPA (lakhs/annum): <fill, e.g. 6>
- A realistic "top" salary anchor in LPA for someone at my level: <fill, e.g. 15>
- Locations I'll accept: <e.g. Remote, India / specific metros>
- Notice period, work authorization, willing to relocate: <fill or leave blank>

## Files to produce

1) === FILE: profile/skills.md ===
   Markdown. A short intro blockquote, then bullet groups by category
   (Languages, Web/API, Databases, DevOps & Cloud, Networking, Messaging, Monitoring,
   Tools, etc.). State proficiency only where the résumé does; never assume a level.

2) === FILE: profile/experience.md ===
   Markdown. One "## <Title> — <Company>" per role, with **Dates**, **Location**,
   bulleted **Responsibilities & impact** (measurable where stated), and a **Tech used**
   line. Factual only.

3) === FILE: profile/education.md ===
   Markdown. One "## <Degree> — <Institution>" per entry with **Dates**, **Location**,
   and scores if stated.

4) === FILE: profile/personality.md ===
   Markdown. "## Career goal" (target roles, primary motivation, any soft tie-breaker
   preference), "## Background signals" (honest context — coding background, certs), and
   "## Open (to be filled by user)" with `<!-- to fill -->` placeholders for things only
   I can answer (work-environment prefs, values). Write in my voice; do not flatter.

5) === FILE: profile/preferences.md ===
   Markdown. A short blockquote noting "hard filters live in config.yaml", then bullets:
   target company type, role leaning, experience level, location, tie-breakers. These are
   SOFT signals for the ranker.

6) === FILE: profile/projects.md ===
   Markdown. A blockquote, then a table: | Project | Repo | Stack |. One row per real
   project from the résumé/repos I gave.

7) For EACH project, === FILE: profile/projects/<slug>.md ===
   Markdown. "# <Name>", then **Repo**, **What it is**, **Details**, **Tech**, and any
   **Note**. <slug> = lowercase-hyphenated name. Only real projects; no fabrication.

8) === FILE: profile/essay_answers.md ===
   Markdown SKELETON only. "# Essay Answers", a blockquote saying these must be written by
   me, then "## <question>" headers for: Why do you want to work here?, Tell us about a
   challenging project., Why are you leaving / looking?, Where do you see yourself in 5
   years?, What are your salary expectations?, plus any common ones. Under each, put
   `<!-- to fill -->`. Do NOT write the answers — leave them for me (the system never
   fabricates my voice).

9) === FILE: profile/form_fields.json ===
   JSON with EXACTLY these keys (same set, same names):
   full_name, email, phone, address_line1, address_city, address_state,
   address_postal_code, address_country, date_of_birth, work_authorization,
   requires_sponsorship, salary_expectation, linkedin_url, github_url, portfolio_url,
   willing_to_relocate, notice_period, current_company, current_title, highest_degree.
   Also keep a "_comment" key explaining: non-empty = used directly, empty string =
   known-but-unfilled (the agent asks me, never guesses). Fill from the résumé; leave
   unknowns as "" (empty string) — NEVER guess.

10) === FILE: profile/candidate_skills.yaml ===
    YAML with two keys:
      skills:   a list of every CONCRETE tool/technology I actually know, lowercased,
                normalized (e.g. "go", "python", "docker", "kubernetes", "postgresql",
                "linux", "kafka", "ci/cd"). Derive STRICTLY from skills.md/experience.md.
                Include only things I genuinely have. Do not include vague phrases.
      synonyms: a map of {alias: canonical} for common ways a job description might name
                each skill, where canonical is one of the entries in `skills`. Examples:
                golang->go, postgres->postgresql, "rest api"->rest, "ci/cd pipeline"->ci/cd,
                k8s->kubernetes (only if kubernetes is in skills). Be generous with aliases;
                every synonym's target MUST exist in `skills`. Start the file with a short
                comment explaining it feeds a deterministic must-have-skill gate (exact +
                synonym match, drop a job when I'm missing >50% of its must-haves), so it
                must be accurate and kept in sync with skills.md.

11) === FILE: config-tuning.yaml ===
    A YAML snippet containing ONLY these two blocks, which I will MERGE into my real
    config.yaml (keeping my secret blocks untouched). Use this exact shape:

    discovery:
      search_terms: [ ... ]          # 6-9 broad job-board queries aligned to my target
                                      # roles (e.g. "devops", "backend", "golang",
                                      # "python", "software engineer"). Cast a wide net.
      locations: [ "Remote", "India" ]   # or my metros
      sites: ["linkedin", "indeed", "google", "naukri"]
      results_wanted_per_site: 25
      hours_old: 48
      country_indeed: "india"
      linkedin_fetch_description: true
      max_distill_per_cohort: 400
      top_n_per_cohort: 5
      reconsider_discovered: false
    preferences:
      yoe_have: <my years, integer>
      min_ctc_lpa: <my salary floor, integer LPA>
      exclude_title_keywords: ["senior", "sr.", "principal", "manager", "director", "vp"]
      yoe_buffer: 1
      top_ctc_lpa: <my top anchor, integer LPA>
      fit_weight: 0.70
      comp_weight: 0.30
      prefer_roles:                  # weight EACH of the 12 role families 0.0-1.0 for me
        devops: <0-1>
        sre: <0-1>
        platform: <0-1>
        infra: <0-1>
        backend: <0-1>
        fullstack: <0-1>
        frontend: <0-1>
        network: <0-1>
        sysadmin: <0-1>
        data: <0-1>
        security: <0-1>
        other: 0.2
      exclude_when:
        - {field: seniority_signal, contains_any: ["senior", "lead"]}
        - {field: min_years_required, greater_than: <yoe_have + yoe_buffer>}
        - {field: country, equals: "other"}
        - {field: red_flags, contains_any: ["unpaid", "commission_only", "scam"]}
    # Notes for me: fit_weight+comp_weight MUST sum to 1.0. Weight my strongest/most-wanted
    # role families near 1.0 and unrelated ones near 0.1-0.3. Set min_years_required's
    # greater_than to (yoe_have + yoe_buffer).

12) === FILE: resume/master.tex (HEADING block only) ===
    Output ONLY the replacement for the \begin{center} ... \end{center} heading block
    (name, phone, email, LinkedIn, GitHub, city) AND the list of \input{sections/...}
    lines. Keep all LaTeX macros/preamble exactly as the project ships — I will paste your
    heading over mine. Use my real contact details.

13) For each résumé section, === FILE: resume/sections/<name>.tex ===
    Produce: experience.tex, education.tex, projects.tex, skills.tex, certifications.tex
    (and resume/sections/projects/<slug>.tex per project if you split them). Use the
    project's existing LaTeX macros (\resumeSubheading, \resumeItem, \resumeProjectHeading,
    \resumeItemListStart/End, \resumeSubHeadingListStart/End, \section{}). Content must be
    factual and match experience.md/skills.md/projects.md. Do NOT invent bullets.

## Final reminder
Every fact must trace to my résumé(s) or the values I gave above. Leave unknowns blank.
Keep candidate_skills.yaml consistent with skills.md. End with the "REVIEW THESE" list.
```

---

## 4. What the LLM gives back

A sequence of `=== FILE: <path> ===` blocks — one per file in §1 — followed by a
**REVIEW THESE** list of any values it estimated (salary anchors, role weights). Read that
list and correct anything before you trust the digest; the salary floor and role weights
directly change what you see.

---

## 5. Applying the output to the project

1. **Replace the profile files.** Overwrite each `profile/*.md`, the
   `profile/projects/*.md` set (delete old ones that don't apply), `profile/form_fields.json`,
   and `profile/candidate_skills.yaml` with the LLM's output.
2. **Merge the config blocks — by hand, never overwrite the whole file.** `config.yaml` is
   gitignored and holds your secrets. Open it and replace **only** the `discovery:` and
   `preferences:` blocks with the LLM's `config-tuning.yaml` content. Leave `telegram`,
   `auth`, `llm.*.api_key`, `storage`, `security`, `resume`, `automation`, `profile`
   untouched. (Tip: back it up first — `cp config.yaml ~/config.yaml.bak`.)
3. **Replace the résumé content.** Paste the heading block into `resume/master.tex`
   (keep all the macros/preamble), and overwrite each `resume/sections/*.tex`.
4. **Wipe the previous candidate's runtime data (recommended).** The SQLite store and any
   generated PDFs/auth state belong to the old profile and are gitignored runtime:
   `rm -f data/cvflow.db data/resumes/* ` and clear `data/auth_state/` if present. (Job
   "crux" caches are about the *job*, not the candidate, so they're harmless to keep, but a
   clean DB avoids stale discovered/applied rows.)

---

## 6. Verify before relying on it

```bash
source .venv/bin/activate
pytest tests/test_skills.py -q          # the candidate_skills.yaml drift guard must pass
python -c "from cvflow.config import load_config; load_config('config.yaml')"   # config still valid
python -c "from cvflow.knowledge import KnowledgeBase; \
  kb=KnowledgeBase.load('profile','profile/form_fields.json'); \
  print('docs:', kb.doc_keys()); print('missing form fields:', kb.missing_form_fields())"
# Résumé compiles (needs tectonic/latexmk + TeX Live):
cd resume && tectonic master.tex && cd ..
```

Checklist:
- `test_skills.py` passes → `candidate_skills.yaml` is well-formed and synonyms resolve.
- `load_config` raises nothing → your config merge is valid (`fit_weight + comp_weight == 1.0`,
  all required keys present).
- `KnowledgeBase.load` lists your docs and shows which `form_fields` are still empty (those
  are the ones cvflow will ask you about in chat — fill the ones you can).
- `master.tex` compiles to a PDF.
- Then run a real discovery (`python -m cvflow.cron discover`) and sanity-check the digest:
  the top jobs should match your target roles, and the `🔍 Filtered today:` footer should
  show the gates removing senior/over-experience/wrong-stack jobs.

---

## 7. Keep these honest over time

- **`candidate_skills.yaml` must track `skills.md`.** When you learn a new tool, add it to
  both. The drift test only catches structural rot (a synonym pointing nowhere), not a
  skill you forgot to list — a missing skill silently drops good jobs.
- **The system never fabricates facts about you.** Empty `form_fields.json` values and
  `<!-- to fill -->` essay slots are deliberate: cvflow asks you in chat rather than
  guessing. Fill them when you can; don't have the LLM invent them.
- **Re-tune `prefer_roles` / `min_ctc_lpa` from the footer.** If the digest skews wrong,
  the drop footer and the fit scores tell you which knob to turn (see
  `docs/discovery-engine.md` §12–13).
