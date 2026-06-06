# Phase 13A — Relevance Engine (preferences + two-stage filtering/ranking) — Design

Date: 2026-06-06
Status: approved (brainstorm), pre-implementation
Related: build plan Phase 4 (discovery) + its salary-hike follow-up; supersedes the 4.8 LPA floor
in `[[salary-hike-filter]]`. Part of Phase 13 (A relevance / B apply-UX / C learning).

## Problem
Discovery surfaces jobs that match skills but ignore experience level, location, pay, and role
type — e.g. a 10+ YOE listing for a <1-yr candidate — and the ranking rationales are generic and
near-identical. There is no persisted, auditable source of truth for the user's hard filters and
soft preferences.

## Decisions (locked in brainstorm)
- Preferences live in **structured config (hard thresholds) + `profile/preferences.md` (soft prose)**.
- **Exclude hard mismatches; keep + FLAG unknowns** (never silently drop missing-info jobs — invariant 3).
- Ranking emits **per-job fit score + specific rationale + concerns**.
- **Two-stage funnel:** deterministic pre-filter on structured data, then LLM ranking for the
  genuinely unstructured judgments. (JobSpy exposes more structure than first assumed.)

## The user's concrete preferences
- YOE: has **1**. Keep only jobs whose stated requirement **includes 1** (e.g. "1–3", "0–2") or
  **fresher**; exclude minimum > 1; keep+flag if YOE not stated.
- Locations: **any Indian metro + Remote** (broad search); **rank up reputable, product-based companies**.
- CTC: drop **stated** pay **< 7 LPA**; keep+flag if pay not stated.
- Deal-breakers (exclude): **senior/lead/manager/architect** titles; **internships/unpaid**;
  **night-shift / rotational-on-call-only**; **pure long-term app-codebase maintenance**.

## Components

### 1. Preferences source of truth
- `config.yaml` → new `preferences:` block, typed as `PreferencesConfig` in `config.py`:
  ```yaml
  preferences:
    yoe_have: 1
    min_ctc_lpa: 7
    job_type: "fulltime"          # JobSpy query filter
    exclude_title_keywords: ["senior", "sr.", "lead", "principal", "staff",
                             "manager", "architect", "head", "director", "vp"]
    prefer_product_companies: true
    exclude_app_maintenance: true
    exclude_night_shift_only: true
  ```
- `profile/preferences.md` (committed PII) — soft prose fed to the ranker: product-company focus,
  company-reputation weighting, DevOps/infra leaning, culture notes.

### 2. Deterministic pre-filter (new `discovery` stage, structured data only)
Runs on normalized postings BEFORE ranking; each drop is logged (never silent):
- **job_type**: pass `job_type=config.preferences.job_type` ("fulltime") to JobSpy `_jobspy_search`
  (+ `enforce_annual_salary=True`) so internships/contract aren't even fetched.
- **Title keywords**: drop postings whose `title` (lowercased) contains any `exclude_title_keywords`.
- **Salary**: when `min_amount`/`max_amount` present and currency is INR (or absent → assume INR for
  India), compute LPA (`amount / 100_000`); drop if the job's upper bound (`max_amount` else
  `min_amount`) < `min_ctc_lpa`. If pay absent or currency non-INR → keep + flag (no silent drop).
- `JobPosting` gains structured `min_amount: float | None`, `max_amount: float | None`,
  `currency: str | None` (carried from JobSpy via `normalize_rows`; `nan`/missing → None).

### 3. Smarter LLM ranking (upgrade `LLMRanker`)
- Prompt now injects: the structured preferences, `preferences.md` prose, and the candidate's real
  YOE/profile. It instructs the model to, for the remaining (pre-filtered) postings:
  - **Exclude** (omit from output) jobs whose JD implies: required YOE minimum > 1 (range must
    include 1 / fresher ok); night-shift or rotational-on-call only; pure long-term app-codebase
    maintenance. Unknown/!stated → keep.
  - For kept jobs emit `{job_id, fit_score (0-100), rationale (specific, cites profile/prefs),
    concerns: [..]}` — concerns include "salary not stated", "YOE not stated", etc.
  - Prioritize reputable **product-based** companies; sort best-first by `fit_score`.
  - Use only provided job_ids (drop fabricated — existing behavior).
- `RankedJob` gains `fit_score: int` and `concerns: list[str]` (additive; default 0 / []).

### 4. Cleanups
- `normalize_rows`: treat pandas `NaN` company/fields as empty (so digests show "Unknown company",
  not "nan"). Helper `_clean(v)` returns "" for None/NaN/"nan".
- Drop `glassdoor`, `zip_recruiter` from `config.discovery.sites` (constant 403s) — config edit.

### 5. Digest
`format_digest` (Phase 11) shows `fit_score` and any `concerns` per job. (Ordinals come in 13B.)

## Data flow
search (job_type filter) → `normalize_rows` (clean NaN, carry salary) → **deterministic pre-filter**
(title/salary, log drops) → cross-day dedup → **LLM rank** (exclude fuzzy mismatches, score+concerns)
→ persist presented → digest.

## Error handling
Every deterministic drop is logged. Ranking failure still falls back to unranked candidates
(Phase-11 behavior). Missing salary/YOE never causes a silent drop — kept + flagged.

## Testing (mocked LLM, no network)
- Pre-filter: title-keyword drop; salary-below-floor drop; salary-absent kept+flagged;
  non-INR kept+flagged; `job_type` passed to `search_fn`.
- `normalize_rows`: NaN company → "".
- Ranker: prompt contains the preferences + YOE; parses `fit_score`/`concerns`; omitted job_ids
  (LLM-excluded) simply don't appear; fabricated ids dropped.
- Digest renders fit score + concerns.

## Exit criteria
A discovery run excludes internships/contract, senior-titled, and below-7-LPA-when-stated jobs
deterministically; the LLM excludes >1-YOE-min / night-shift / app-maintenance jobs and returns
specific per-job rationales with fit scores + concerns; no-salary/no-YOE jobs are surfaced+flagged,
never dropped.

## Accepted trade-offs
- YOE / shift / app-vs-infra / product-vs-service are **unstructured** → LLM-judged (config holds the
  thresholds, the LLM enforces). Not bit-deterministic, but precise and the only zero-cost option.
- Salary filtering only fires when the board returns structured amounts (often absent for India) —
  hence keep+flag is the common path.
- `prefer_product_companies` / reputation is an LLM soft signal (no reliable structured source).

## Out of scope
Ordinal apply/skip (13B); preference learning from apply/skip history (13C); swapping in a metered
API like Adzuna (kept behind the existing `search_fn` seam for the future).
