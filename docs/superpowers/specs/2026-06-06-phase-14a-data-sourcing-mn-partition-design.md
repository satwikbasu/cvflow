# Phase 14A — Discovery Relevance v2: Data Sourcing, Field Prioritization & M/N Partition

> **Status:** design (brainstormed 2026-06-06). Supersedes the single-stage ranker built in Phase 13A.
> Phase 13's preferences config, digest_slots/resolve_targets/batch-gate, learning loop, and the
> deterministic title pre-filter all SURVIVE and are reused. This spec covers what we scrape, how we
> prioritize the fetched data, the deterministic gates, and how candidates split into the M/N cohorts.
> Companion specs: **14B** (distillation + crux schema), **14C** (benchmark + ranking + digest).

## 1. Why this exists

The Phase-13 live run exposed two root failures:
1. **Ranking timed out** — all ~400 candidates went to NIM in one giant prompt → read timeout → the
   whole digest degraded to unranked ("fit 0"), so the YOE/seniority filtering (which lives in the
   ranker) never ran.
2. **No descriptions for LinkedIn** — JobSpy returns LinkedIn rows without descriptions by default,
   so the ranker scored LinkedIn jobs on title alone (flat "fit 80"), and US jobs leaked in because
   `country_indeed` defaulted to `usa`.

The fix is a **two-stage pipeline** (distill → benchmark) over **prioritized, description-bearing,
India-correct** data, with salary-bearing and salary-less jobs scored separately so they don't create
noise. This spec is stage-0: getting clean, prioritized data and splitting it.

## 2. JobSpy configuration (locked decisions)

Installed: **python-jobspy 1.1.82**. Verified parameter behaviour (source + official README):

| Setting | Value | Rationale |
|---|---|---|
| `site_name` | `["linkedin", "indeed", "google", "naukri"]` | Add **Naukri** (India-native; the only source that populates INR salary + `experience_range`). Drop glassdoor/zip_recruiter (403s). |
| `linkedin_fetch_description` | `True` | Descriptions are MANDATORY for ranking. Cost: +1 HTTP request per LinkedIn job + higher ban risk — accepted (expendable account). Also honoured by Naukri in 1.1.82. |
| `hours_old` | from `discovery.hours_old` (72) | Daily cron → latest only. |
| `job_type` | **NOT sent** | On Indeed `hours_old` and `job_type` are mutually exclusive (API allows one composite filter); we choose recency. We also deliberately do NOT filter work-mode (see §3). |
| `country_indeed` | `"india"` | Fixes the US-jobs leak on Indeed/Glassdoor (only these two honour `country`). |
| `is_remote` | **NOT sent** | Do not discriminate remote vs onsite — we want the bigger pool incl. high-pay foreign roles. |
| `description_format` | `"markdown"` (default) | Clean text for distillation. |
| `enforce_annual_salary` | `True` | Normalize any stated pay to annual (kept from Phase 13A). |
| `results_wanted` | from config | unchanged |

**India-focused (locked 2026-06-06):** `country_indeed=india` pins Indeed to India. We deliberately do
**not** model overseas relocation — that was dropped to avoid unnecessary complexity (it required
currency conversion + a location/comp trade-off, the most fragile, least-testable part of the design).
The benchmark (14C) is therefore India-centric: no FX, no location-vs-pay weighting. A remote-foreign
role that still slips in via a LinkedIn/Google `Remote` search is harmless (it's remote, no relocation)
and simply ranks on fit like any other no-INR-salary job. **Re-enabling foreign targeting later is a
config flip** (`country_indeed`) plus re-adding one scoring block — the rationale is preserved in 14C §
"Deferred: foreign-reach". The crux still records `country`/`work_mode` as displayed info + a future hook.

## 3. Field prioritization — what we keep from each row

JobSpy output columns vary by site. Priority for THIS engine:

| Field | Source sites | Used for |
|---|---|---|
| `description` | indeed, google, naukri, linkedin(opt-in) | **distillation input (mandatory)** |
| `min_amount`/`max_amount`/`currency` | **naukri (INR)**, indeed sometimes | **M/N partition + comp score** |
| `experience_range` | **naukri only** | deterministic YOE gate (when present) |
| `title`,`company`,`location`,`date_posted`,`job_url`,`site` | all | id, dedup, recency cap, digest |
| `is_remote`,`job_level`,`company_industry`,`company_rating` | varies | hints passed to distiller |

`JobPosting` already carries `min_amount/max_amount/currency` (Phase 13A). Add (additive):
`experience_range: str | None`, and keep `is_remote`, `job_level`, `company_industry`,
`company_rating` only if cheaply available (optional, nullable) as distiller hints.

## 4. Pipeline order (stage-0)

```
scrape (§2)
  → normalize_rows  (Phase 4; NaN clean + salary fields, Phase 13A)
  → title pre-filter (Phase 13A; exclude_title_keywords)        [DETERMINISTIC]
  → cross-day dedup  (store.exists)                             [Phase 4]
  → PARTITION by stated salary  →  M (pay known) | N (pay unknown)
  → per-cohort recency cap (newest K each)  → bounds Gemini distill cost
  → [14B] distill each → crux (cached)
  → [14B/14C] deterministic crux gates  (drop, see §5)
  → [14C] benchmark + rank each cohort   → two-section digest
```

## 5. Deterministic gates (code only — never the LLM; applied both cohorts)

Order: cheap/title gates first (pre-distill), crux-derived gates after distillation.

**Pre-distill (on `JobPosting`):**
- **Title exclude** (Phase 13A): drop if title contains any `exclude_title_keywords`.
- **M salary floor**: M-cohort job with stated INR annual `max < min_ctc_lpa·100k` (7 LPA) → drop.
  (Only INR-stated jobs are in M — see §6; foreign-currency salaries are treated as no-INR-salary and
  fall into N, so no FX conversion is ever needed.)
- **Naukri YOE (when `experience_range` present)**: parse min years; if `> YOE_CEILING` → drop.

**Post-distill (on crux) — DECLARATIVE rule list `preferences.exclude_when` (not hardcoded booleans):**

Instead of one Python `if` per deal-breaker, exclusions are a config-driven list of rules evaluated
against crux fields. A job is dropped if it matches ANY rule. This makes adding/removing a filter a
config edit — no code change (the key generalization for reuse beyond a single user).

```yaml
preferences:
  exclude_when:
    - {field: night_shift_only,      equals: true}
    - {field: app_maintenance_focus, equals: true}
    - {field: min_years_required,    greater_than: 3}     # > YOE ceiling
    - {field: red_flags,             contains_any: [unpaid, commission_only, scam]}
```

Supported operators (small fixed set, in code): `equals`, `greater_than`, `less_than`, `contains_any`.
A rule whose `field` is **null/unknown on the crux does NOT match** (we never drop on absent facts —
invariant 2; e.g. `min_years_required: null` → kept + concern `YOE_UNKNOWN`). The operator set and the
evaluator are code; the *rules* are config. `YOE_CEILING` is expressed directly as the `greater_than`
value, derived from `yoe_have + yoe_buffer` if the user prefers (default ceiling 3; entry roles say "0-3").

Title exclusion (`exclude_title_keywords`) and the M salary floor (`min_ctc_lpa`) stay as their own
config knobs (they act on `JobPosting`, pre-distill, not on crux fields).

**Never silent (invariant 3):** every deterministic drop is logged at INFO with job_id + reason
(already the pattern in Phase 13A `_prefilter`). Counts summarized in the cron log.

## 6. M/N partition rule

```
has_inr_salary(job) = stated annual amount present AND currency == INR
                      (from JobSpy min/max_amount+currency, or crux.stated_salary)
M = [j for j in candidates if has_inr_salary(j)]    # fit + comp (14C)
N = [j for j in candidates if not has_inr_salary(j)]# fit only (14C)
```
Only **INR-stated** jobs enter M (so comp scoring needs no FX). Foreign-currency or unstated salary →
N. Partition happens BEFORE distillation for the JobSpy-structured case; the crux can promote an N→M
job only if it surfaces an explicit INR salary the structured field missed (rare; re-checked
post-distill). **No estimation** — an N job never gets a fabricated salary.

## 7. Recency cap (cost bound for distillation)

Distillation is one Gemini call per uncached job. Cap each cohort to the **newest `K`** by
`date_posted` (default `K = 60`; config `discovery.max_distill_per_cohort`). Worst case ≈120 Gemini
distills/day << 1400 RPD. Cached cruxes (14B) make re-runs near-free. Log how many were set aside.

## 8. Config additions (`preferences:` / `discovery:`)

```yaml
preferences:
  yoe_buffer: 2            # ceiling = yoe_have + yoe_buffer (used as a greater_than value)
  exclude_when:            # declarative crux-field exclusion rules (see §5)
    - {field: night_shift_only,      equals: true}
    - {field: app_maintenance_focus, equals: true}
    - {field: min_years_required,    greater_than: 3}
    - {field: red_flags,             contains_any: [unpaid, commission_only, scam]}
discovery:
  sites: ["linkedin", "indeed", "google", "naukri"]
  country_indeed: "india"
  linkedin_fetch_description: true
  max_distill_per_cohort: 60
```
(`exclude_title_keywords`, `min_ctc_lpa`, `prefer_product_companies`, etc. stay as Phase 13A.)

## 9. What survives / what changes

- **Survives:** preferences config, `digest_slots`+`resolve_targets`+batch `/apply`, learning loop,
  title pre-filter, `JobPosting` salary fields, state machine, gate, storage, cron orchestration.
- **Changes:** `DiscoveryService.discover()` gains partition + cap + distill + per-cohort benchmark;
  the Phase-13 single-stage `LLMRanker` (batching, flat fit_score) is REPLACED by the 14B/14C
  components; today's recency-cap-of-40 + batching become moot (cruxes rank in one call per cohort).
- **New:** `discovery` config keys (§8); `JobPosting.experience_range`.

## 10. Captured user preferences (canonical list — keep in sync with [[user-job-preferences]])

1 YOE / entry-level only; exclude senior·sr·lead·principal·staff·manager·architect·head·director·vp;
≥7 LPA floor (M cohort); based in Kolkata, prefer Kolkata + Indian metros + Remote; **India-focused — no
overseas relocation targeting** (kept simple by choice 2026-06-06; foreign reach is a future config
flip, not modelled now); do NOT filter remote vs onsite; product-based > service/consultancy/staffing; prefer
DevOps/SRE/platform/infra/backend over pure frontend or long-term app-maintenance; exclude internships,
night-shift-only, app-maintenance-focused; modern infra stack (Docker/K8s/CI-CD/cloud) as tie-breaker;
latest postings only.
