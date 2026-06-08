# Discovery engine — operational reference

The complete, current picture of how cvflow's daily discovery pipeline works, end to
end, **as of 2026-06-08** — after the Phase-14 build and the live-run hardening cascade
(LLM-provider migration, deterministic skill/seniority gates, drop-reason footer, the
Naukri IP-block discovery). This reflects the **actual code**, not the original 14A–D
specs (which assumed Gemini and dropped Naukri). For original design rationale see
`docs/superpowers/specs/2026-06-06-phase-14{a,b,c,d}-*.md`; for chronology see the
build-plan progress log.

Discovery is the heart of the project: once a day it turns hundreds of raw job postings
into a short, ranked, two-section Telegram digest the user can act on with `/apply` /
`/skip`. Everything downstream (JD analysis, résumé tailoring, the approval gate,
submission) only runs on jobs that surface here.

---

## 1. The core principle: deterministic gates, LLM only for ranking

Every **hard requirement** (seniority, years of experience, country, mandatory skills,
salary floor, deal-breakers) is enforced in **plain Python code**. The LLM is used for
exactly two things, both of which only *reorder or describe* jobs that already passed the
gates — it can never let an unqualified job through:

1. **Distillation** — read one messy JD, emit a small structured `Crux` of facts.
2. **Fit scoring** — given a crux + the candidate fingerprint, score role/skill match 0–100.

This split exists because the LLM is charitable and inconsistent on hard requirements
(it once scored a Generative-AI role "strong fit" for a candidate with no LLM skills, and
flagged "missing Java" on a job where the candidate *has* Java). So: **gates are code,
ranking is LLM.** If you ever find a hard requirement being judged inside a prompt, that's
a bug — move it to code (this is exactly what the seniority and must-have gates did).

Two CLAUDE.md invariants shape the whole engine:
- **Never fabricate facts** (invariant 2): an unknown salary/YOE becomes `null`, never a
  guess. The distiller is explicitly told not to infer.
- **Never fail silently** (invariant 3): every dropped batch, blocked source, and filtered
  job is logged, and the digest footer reports aggregate drop counts to the user.

---

## 2. Entry point & orchestration

```
python -m cvflow.cron discover
```

- `cvflow/cron.py::main` builds the services (`_build`) from `config.yaml` and calls
  `run_job("discover", ...)`.
- `run_job` calls `discovery.discover()`, records the presented job_ids as digest slots
  (`store.set_digest_slots`, so `/apply 1` maps to a real job), renders the digest
  (`format_digest`), and pushes it to Telegram via `HermesNotifier`.
- Any uncaught exception is caught at the top of `main`, reported to the user via Telegram,
  then re-raised — a cron crash never dies quietly.

The agent "brain" (NIM llama-3.3-70b) is **not** involved in discovery. Discovery runs as a
plain deterministic job; the brain only enters later, when the user replies to select or
approve. `_build` constructs a minimal service set (no browser/automator): the store, the
knowledge base, the Mistral distiller, the candidate fingerprint, the curated skill set,
and the `DiscoveryService`.

---

## 3. The data objects that flow through the pipeline

| Type | Defined in | What it is |
|---|---|---|
| raw row (`dict`) | scrapers | One posting as the board/adapter returned it — heterogeneous keys |
| `JobPosting` (frozen dataclass) | `discovery/__init__.py` | Normalized posting: stable `job_id`, title, company, location, description, url, site, structured salary/experience/job_type |
| `Crux` (pydantic) | `discovery/distill.py` | The LLM-distilled structured facts of one JD (see §6) |
| `FitResult` (frozen dataclass) | `discovery/benchmark.py` | `fit_score` (0–100), `fit_reason`, `concerns[]` from the fit LLM |
| `BenchmarkedJob` (frozen dataclass) | `discovery/benchmark.py` | The final ranked unit: posting + `benchmark` + `fit_score` + `fit_reason` + `concerns` + `cohort` + `ctc_lpa` |
| result `dict` | `discovery/__init__.py` | `{"M":[BenchmarkedJob], "N":[BenchmarkedJob], "_dropped":{bucket:count}}` |

**Hand-off chain:** `list[dict]` → `list[JobPosting]` → `list[Crux]` (cached) → partitioned
crux lists → `dict[job_id,FitResult]` → `list[BenchmarkedJob]` → digest string → Telegram.

---

## 4. The pipeline, stage by stage

All of this lives in `DiscoveryService.discover()` (`discovery/__init__.py`). A `drops`
counter dict is threaded through every stage to build the digest footer.

### Stage 0 — Scrape (`_gather_rows`, no LLM, ~10–16 min)
Loops over every `search_term × location` pair and queries two kinds of source:
- **JobSpy** (`linkedin`, `indeed`, `google`) via `_jobspy_search` — India-pinned
  (`country_indeed`), LinkedIn full descriptions on, `hours_old` window, annual-salary
  enforced.
- **Naukri** via our own `search_naukri` adapter (JobSpy's Naukri 406s — see §10).

Each source is wrapped in try/except: a failing batch (anti-bot 403, endpoint drift, one
unparseable posting, Naukri block) is logged and skipped — it never aborts the run or
silences the digest. A throttle (`throttle_seconds`, default 5s) sits between pairs.
**Output:** a flat `list[dict]` of raw rows.

### Stage 1 — Normalize (`normalize_rows`)
Converts each raw dict into a frozen `JobPosting`: a stable `job_id` (`<site>:<id>`, or
`<site>:<sha1(url)>` when the board gives no id), NaN/None→"" cleanup, numeric salary
parsing, within-batch dedup (first wins). **Output:** `list[JobPosting]`.

### Stage 2 — Deterministic prefilter (`_prefilter`, no LLM)
Drops, by rule, before any LLM cost:
- title contains an `exclude_title_keywords` term → bucket **other filter**
- `job_type` contains "intern" → bucket **other filter** (absent job_type never dropped)
- stated **INR** salary below `min_ctc_lpa` → bucket **low pay** (non-INR kept, flagged later)
- Naukri `experience_range` whose parsed minimum exceeds `yoe_ceiling`
  (`yoe_have + yoe_buffer`) → bucket **over-experience** (unparseable/absent range kept)

### Stage 3 — Cross-day dedup (`_already_seen`)
A `job_id` already in the SQLite store is "seen" and skipped → bucket **already seen**.
With `reconsider_discovered: true` (a *testing* toggle), a job that's only ever been
`discovered` (never `/apply`'d or `/skip`'d) is re-admitted; jobs advanced past discovery
stay excluded. **In production keep this `false`** — `true` floods the candidate pool with
old jobs and pushes fresh ones past the distill cap (Stage 4).

### Stage 4 — Cap (`max_distill_per_cohort`)
Take the first `max_distill_per_cohort` candidates in **gather order** (not a date sort —
`date_posted` strings are inconsistent across sources: ISO vs "2 Days Ago"; gather order
already front-loads earlier/priority search terms). Anything beyond the cap → bucket
**capped** (never distilled this run). Raise the cap for more coverage at the cost of a
slower first run (cached afterward).

### Stage 5 — Distill (`distill_all` → `Distiller.distill`, **Mistral**, see §6)
Each JD → one `Crux`, cached in the `job_cruxes` table keyed by `(job_id, DISTILL_VERSION)`.
A cached crux of the current version is reused; a stale/older-version or corrupt cache
re-distills. One bad/garbled/over-budget JD is logged and skipped, never aborts the run.

### Stage 6 — Gate + partition (no LLM)
For each crux, in order — first match wins, and each bucket is tallied:
1. **`exclude_when` rules** (`crux_excluded`, `rules.py`) — declarative deal-breakers over
   crux fields (seniority `senior`/`lead` → **too senior**; `min_years_required > N` →
   **over-experience**; `country == other` → **abroad**; red flags → **red flag**; etc.).
2. **Must-have skill gate** (`coverage_drop`, `skills.py`) — drop when the candidate lacks
   **> 50%** of the crux's `must_have_skills` → bucket **wrong stack**. Only fires when the
   curated skill set is loaded *and* the crux lists must-haves (see §7).
3. **Salary floor** — `effective_lpa < min_ctc_lpa` → bucket **low pay**.
4. **Partition** by `effective_lpa` (`benchmark.py`): a stated INR salary → **cohort M**
   (with pay); none → **cohort N** (no stated pay). `effective_lpa` prefers the
   crux-extracted salary and falls back to JobSpy's structured field — because Indian
   listings usually state pay only in the JD text, which the structured fields miss.

### Stage 7 — Fit-score + benchmark (`_rank_cohort`, **Mistral** + code)
Per cohort:
- `fit_scores` (`benchmark.py`) sends the **whole cohort in one call** to Mistral: a trimmed
  `_fit_view` of each crux + the candidate fingerprint. Returns one `{fit_score, fit_reason,
  concern_codes}` per job (see §8 for the rubric). Robust to malformed JSON; on total
  failure the cohort degrades to fit 0 + `RANKING_DEGRADED` (never lost).
- `benchmark_cohort` computes the final ranking score deterministically (§9), adds code-side
  concerns (`PAY_UNKNOWN`, `YOE_UNKNOWN`), sorts by benchmark, and `_rank_cohort` takes
  `top_n_per_cohort`.

### Stage 8 — Persist + footer
Each presented job is written to the store with status `discovered` (idempotent).
`discover()` attaches `result["_dropped"] = drops` and returns
`{"M":[...], "N":[...], "_dropped":{...}}`.

### Stage 9 — Digest (`cron.format_digest`)
Two sections — **💰 With stated pay** (M, ranked by value) and **📋 Pay not stated** (N,
ranked by fit) — continuously numbered so the slot map works. Each entry shows title,
company, `bench`, `fit`, pay, location, the one-line fit reason, any `⚠️` concern codes, and
`/apply`/`/skip` commands. A footer (`_filtered_footer`) summarises the drop buckets:
`🔍 Filtered today: 100 too senior · 239 over-experience · …` so the gates are never a
black box. Empty sections print a one-liner ("No stated-pay jobs today.").

---

## 5. The two deterministic gates added during hardening

### Seniority gate (config only)
`preferences.exclude_when: - {field: seniority_signal, contains_any: ["senior","lead"]}`.
The `contains_any` operator matches a scalar field, so this drops any crux the distiller
classified `senior`/`lead`. It catches mis-titled senior roles the title-keyword filter
misses (e.g. "Software Engineer III", distilled as `senior`). No code change — purely a
declarative rule evaluated by `crux_excluded`.

### Must-have skill gate (`discovery/skills.py`)
A hard requirement, in code, not the prompt:
- The candidate's real skills + a synonym map live in **`profile/candidate_skills.yaml`**
  (committed PII, hand-maintained). `load_skill_profile` normalizes them.
- `coverage_drop(must_have, have, synonyms, max_missing_ratio=0.5)`: normalize each
  must-have, resolve synonyms (`golang→go`, `postgres→postgresql`, `rest api→rest`,
  `ci/cd pipeline→ci/cd`, …), and **drop the job if the candidate is missing strictly more
  than 50%** of the must-haves.
- Drop-on-**majority**, not drop-on-any — chosen deliberately so a single missing tool on an
  otherwise-matching stack (e.g. Antal: Java/Python/Go present, k8s/Terraform missing = 40%)
  is *kept*, while a role whose core you don't do (LLM engineer, .NET, React, telephony) is
  dropped. This protects digest volume.
- It only fires when the crux's `must_have_skills` is **non-empty**. This is a real loophole:
  a JD that lists no mandatory *tech* skills (e.g. "Purchase Executive") has nothing to gate
  on, so it can reach the digest at fit 0 (see §11, M-cohort noise).
- A `tests/test_skills.py` drift guard fails if `candidate_skills.yaml` rots: a synonym
  pointing at a non-existent skill, an un-normalized entry, or an empty skill set.

**Maintenance rule:** add a skill to `candidate_skills.yaml` the moment it becomes real, or
the gate will keep dropping jobs that need it. Matching is exact + synonyms (no substring,
to avoid `java` matching `javascript`), so JD phrasings you haven't mapped count as missing.

---

## 6. Distillation — the `Crux` (Mistral `mistral-small-2506`)

`Distiller.distill` (`discovery/distill.py`) sends one JD (title/company/location +
`experience_range` hint + up to 12k chars of description) to Mistral with a strict
`json_schema`, `temperature=0`, fixed seed, 512 output tokens. The model is told: use only
stated facts, never infer salary or YOE, classify role/seniority from the **description not
the title**, and emit only concrete named technologies in `must_have_skills` (no generic
phrases like "backend development").

`Crux` fields: `job_id`, `role_family` (enum: devops/sre/platform/infra/backend/fullstack/
frontend/network/sysadmin/data/security/other), `seniority_signal` (fresher/junior/mid/
senior/lead/unknown), `min/max_years_required` (`int | null`), `work_mode`, `location_text`,
`country` (india/other/global-remote), `stated_salary` (`Salary{min,max,currency,period}` or
null), `tech_stack[]`, `must_have_skills[]` (≤5 mandatory concrete tools), `night_shift_only`,
`app_maintenance_focus`, `company_type` (product/service/staffing/unknown), `red_flags[]`,
`applicant_instructions`, `one_line`.

**Cache versioning:** `DISTILL_VERSION` (currently `"4"`). Bump it whenever the distill
prompt or `Crux` schema changes — cached cruxes of an older version are ignored and
re-distilled automatically, no manual cache wipe.

---

## 7. The fit model (Mistral, `benchmark.py::fit_scores`)

One call per cohort scores every job. The prompt (`_FIT_PREAMBLE`) is **role-weight
dominant**: the job's `role_family` is matched against the `prefer_roles` weight map, and
that sets the band — stack overlap only matters once the role fits.

| Band | Condition |
|---|---|
| 85–100 | weight ≥0.8 **and** real core-tool overlap **and** seniority fresher/junior/mid |
| 65–84 | weight ≥0.8 weak overlap, or 0.5–0.8 strong overlap |
| 40–64 | weight 0.3–0.5 (adjacent role) with some overlap |
| 0–39 | weight <0.3 or an unrelated role (support/QA/ERP/pure-frontend) **even if a few tools coincide** |

Modifiers: −10 service/staffing company, +5 modern infra stack. The prompt explicitly tells
the model **not** to gate on `must_have_skills` or emit `MISSING_MUST_HAVE` — the
deterministic code gate (§5) owns that. (The prompt-based must-have gate was *removed* during
hardening because it produced false, contradictory warnings like "missing Java" on jobs the
candidate qualifies for, and leaked "HARD GATE:" scaffolding into the digest.)

Robustness: strict `json_schema` (Mistral enforces the per-job array shape; NIM can't — its
schema mode times out and its object mode returns a single object). Malformed output is
salvaged object-by-object (`_salvage_objects`); a provider error degrades the whole cohort to
fit 0 + `RANKING_DEGRADED` rather than losing it. Any job the model omits still gets a row.

---

## 8. Concern codes (the `⚠️` flags)

- **From the fit LLM:** `STACK_MISMATCH`, `SERVICE_COMPANY`, `SENIORITY_BORDERLINE`,
  `ROLE_ADJACENT`.
- **Code-side (`benchmark_cohort`):** `PAY_UNKNOWN` (no stated INR salary), `YOE_UNKNOWN`
  (crux `min_years_required` is null — never fabricated, flagged not dropped),
  `RANKING_DEGRADED` (fit call failed/omitted this job).

---

## 9. Benchmark math (`benchmark.py`, deterministic)

- **N cohort (no pay):** `benchmark = round(100 · fit/100) = fit`.
- **M cohort (stated pay):** `benchmark = round(100 · (fit_weight·fit/100 + comp_weight·comp))`,
  with `fit_weight=0.70`, `comp_weight=0.30` (must sum to 1.0, validated in `config.py`).
- **comp score:** `comp = clamp((LPA − min_ctc_lpa) / (top_ctc_lpa − min_ctc_lpa), 0, 1)` —
  0 at the floor, 1 at the top anchor. INR only, no FX, no location score (deferred by design).

So N is pure role/skill fit; M blends fit with how good the pay is. This is why a low-fit but
salaried job can still appear in M when salaried jobs are scarce (§11).

---

## 10. Naukri — the richest source, and its fragility

Naukri's `jobapi/v3/search` returns **406 "recaptcha required"** to a plain request because it
needs **`nkparam`** — a one-time anti-bot token its frontend mints by RSA-encrypting
`v0|<ms-timestamp>|121_srp` (PKCS1_v1_5) with a public key embedded in Naukri's own JS. We
reproduce that in pure Python (`pycryptodome`) in `discovery/naukri.py::make_nkparam`, seed
cookies with one page GET, and call the API with `appid:109 + gid + nkparam`. A fresh token
per request; a 403 retries once with a new token; later/sparse pages return a benign 400
(end-of-pages). Technique credit: `github.com/Traverser25/NopeRi` (no code copied; the key is
Naukri's). Salary labels ("3-6 Lacs PA") are parsed by `parse_salary`; rows are mapped into the
`normalize_rows` shape by `_row_from_job`.

**IP-block reality (corrects the earlier "never an IP ban" note).** On **2026-06-08** Naukri
began returning 406 to *every* request from the EC2 box — including from a **genuine headless
Chrome** running on it (real TLS, real JS, real token, real cookies, 0 job cards). The exact
same `openssl`-minted request from a **residential IP returned 200 with jobs**. So this is
**datacenter-IP reputation blocking by Akamai**, independent of the token, TLS fingerprint
(`httpcloak`/`curl_cffi` also 406'd), or cookies. Diagnosis evidence is in the session log; a
local-vs-server `curl` probe is the confirmation test.

Consequences and the operational posture:
- **It is intermittent and IP-bound.** Restarting the EC2 instance (new public IP) restored
  Naukri immediately. A flagged AWS IP can recover on its own over time.
- **The only durable fix is a non-datacenter egress** (residential/mobile proxy) for Naukri
  traffic — which **costs money** and so is deferred (CLAUDE.md invariant 4: flag any cost; the
  user has not opted in). Rotating to another *AWS* IP won't help (same range).
- **Treat Naukri as best-effort.** When its IP is clean it's the best source of INR salary +
  experience data; when blocked, the run continues on JobSpy (LinkedIn/Indeed/Google) and the
  Naukri batches just contribute 0 rows. (A "Naukri blocked today" user notice is a sensible
  future addition — currently it degrades quietly aside from per-batch log warnings.)
- If Naukri rotates the key or token format, `make_nkparam` needs a one-line update — same risk
  class as all scraping; treat endpoints/accounts as expendable.

---

## 11. LLM provider map

| Task | Provider / model | Why |
|---|---|---|
| **Distillation** (JD → `Crux`) | **Mistral `mistral-small-2506`** | strict `json_schema`, 2.25M TPM, 5 RPS |
| **Fit ranking** (per cohort) | **Mistral `mistral-small-2506`** | needs a per-job JSON *array*; NIM can't emit it |
| **JD analysis** (`analyze_jd`, post-`/apply`) | **NIM `meta/llama-3.3-70b-instruct`** | free-form text, big ctx, no daily cap |
| **Résumé tailoring** (post-`/apply`) | **Cerebras `gpt-oss-120b`** | high quality, 65k ctx, low volume |

All four are OpenAI-compatible (`POST /chat/completions`) behind one `NimProvider` client;
`llm.{brain,distillation,tailoring}` share one `ProviderConfig` shape in `config.yaml`. Quirks
handled in `NimProvider`: Mistral uses `random_seed` (not `seed`) and rejects `top_p` under
`temperature=0`; Cerebras (behind Cloudflare) needs a browser `User-Agent` or returns 403/1010.

**Gemini was dropped** (Google gutted the 2.5 Flash free tier to 5 RPM / 20 RPD). **NIM can't do
structured ranking** (schema mode times out, object mode returns one object not an array) — both
proven live — which is why distillation + fit run on Mistral.

---

## 12. Config knobs that drive quality/volume (`config.yaml`)

| Key | Effect |
|---|---|
| `discovery.search_terms` × `locations` | the scrape matrix; cast a wide net (ranking filters by fit, not title) |
| `discovery.sites` | which boards; `naukri` routes to our adapter, the rest to JobSpy |
| `discovery.results_wanted_per_site` | per `term×location×site` scrape cap — the main raw-volume dial |
| `discovery.hours_old` | freshness window |
| `discovery.max_distill_per_cohort` | total jobs distilled+ranked per run; raise for coverage, slower first run |
| `discovery.top_n_per_cohort` | jobs shown per 💰/📋 section |
| `discovery.reconsider_discovered` | **testing toggle**; keep `false` in production (else it bloats the pool past the cap) |
| `preferences.prefer_roles` | role_family → weight (0–1); **dominates** the fit band |
| `preferences.exclude_when` | declarative crux-field deal-breakers (seniority, min_years, country, red_flags) |
| `preferences.min_ctc_lpa` / `top_ctc_lpa` | salary floor (M/N split + prefilter) and comp-score top anchor |
| `preferences.yoe_have` + `yoe_buffer` | Naukri pre-distill experience ceiling |
| `preferences.fit_weight` / `comp_weight` | M-cohort blend weights (must sum to 1.0) |
| `profile/candidate_skills.yaml` | the curated skill set + synonyms the must-have gate matches against |

Five legacy config fields were removed as inert (`discovery.top_n_to_present`,
`preferences.job_type` / `prefer_product_companies` / `exclude_app_maintenance` /
`exclude_night_shift_only`) — the last three are now handled by `exclude_when` rules.

---

## 13. Known limits / non-bugs

- **Salary-stated jobs are scarce** in Indian listings (Naukri "Not disclosed", LinkedIn/Indeed
  rarely state INR pay), so the 💰 M cohort is often small or empty. We never fabricate pay.
- **M can fill with low-fit salaried noise.** When few salaried jobs survive the gates, M ranks
  by value, so a fit-0 unrelated role with stated pay and **no listed must-haves** (e.g.
  "Purchase Executive", "MEP Engineer") can surface — the must-have gate can't catch an empty
  must-have list, and there is **no `min_fit_score` floor** (the user declined one to protect
  volume). The clean fix, if reconsidered, is a deterministic fit floor (drop `fit < ~40`).
- **Over-experienced jobs can slip through** when neither the Naukri `experience_range` nor the
  JD text states years (`min_years_required = null` → never fabricated → flagged `YOE_UNKNOWN`,
  not dropped). Seniority extraction is the backstop, but it too is LLM-judged.
- **Naukri is IP-fragile** (§10) — best-effort, blocked when the EC2 IP is Akamai-flagged.
- **Crux cache is version-gated** — bump `DISTILL_VERSION` on any prompt/schema change.
- **`reconsider_discovered: true` reduces coverage**, it doesn't add salaried jobs — it re-admits
  old (mostly no-pay) jobs and pushes fresh ones past the distill cap.

---

## 14. File map

| File | Responsibility |
|---|---|
| `discovery/__init__.py` | `DiscoveryService` orchestration, `JobPosting`, `normalize_rows`, prefilter, dedup, gate+partition, drop footer buckets |
| `discovery/distill.py` | `Crux`/`Salary` schema, `Distiller`, `distill_all`, cache + `DISTILL_VERSION` |
| `discovery/benchmark.py` | `fit_scores` (LLM), `benchmark_cohort`/`comp_score` (code), `effective_lpa`, `build_fingerprint`, concern codes |
| `discovery/rules.py` | `crux_excluded` — declarative `exclude_when` evaluator |
| `discovery/skills.py` | `load_skill_profile`, `coverage_drop` — the deterministic must-have gate |
| `discovery/naukri.py` | nkparam-signed Naukri adapter |
| `profile/candidate_skills.yaml` | hand-maintained candidate skill set + synonyms |
| `cron.py` | `discover` entrypoint, `format_digest` (+ footer), notify wiring |
| `config.py` | typed load/validation of all the knobs above |

---

## 15. Extension seams

- **Source adapter:** `DiscoveryService(search_fn=...)` / `naukri_search_fn=...` are injectable
  — a different board or a hosted jobs API slots in without touching dedup, gates, or ranking.
- **Provider swap:** any OpenAI-compatible endpoint works via `NimProvider` + a `ProviderConfig`
  block. Distillation/fit just need strict `json_schema` array support (the reason for Mistral).
- **Gates:** new deal-breakers are one-line `exclude_when` rules; new skill matching is
  `candidate_skills.yaml` edits — no code change for either.
</content>
