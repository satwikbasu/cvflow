# Phase 14B — JD Distillation & the Crux Schema

> **Status:** design (2026-06-06). Stage-1 of the two-stage discovery engine. Reads the description-
> bearing `JobPosting`s from **14A**, emits a compact structured **crux** per job that **14C** ranks.
> Engine: **Gemini 2.5 Flash** (JD text only — least PII-sensitive call; user already consented).

## 1. Purpose

Compress each raw JD into a small, **fixed-shape, enum-heavy** record holding only the facts the
benchmark needs. This (a) lets the whole cohort fit in ONE ranking call (fixing the cross-job
calibration that batching broke), (b) makes ranking fast + consistent (llama sees uniform short input,
never raw prose), and (c) feeds the deterministic gates (YOE/shift/maintenance/red-flags) so hard rules
stay in code, not the LLM.

## 2. The crux schema (Pydantic → `response_schema`)

```python
class Crux(BaseModel):
    job_id: str
    role_family: Literal["devops","sre","platform","infra","backend","fullstack",
                          "frontend","network","sysadmin","data","security","other"]
    seniority_signal: Literal["fresher","junior","mid","senior","lead","unknown"]
    min_years_required: int | None   # null if JD does not state it — NEVER guessed
    max_years_required: int | None
    work_mode: Literal["remote","hybrid","onsite","unknown"]
    location_text: str               # short, e.g. "Bengaluru, IN" / "Remote (US)"
    country: Literal["india","other","global-remote"]
    stated_salary: Salary | None     # only if explicitly in the JD; else null (no estimation)
    tech_stack: list[str]            # normalized, max 8 (docker,k8s,aws,terraform,ci-cd,go,python…)
    night_shift_only: bool
    app_maintenance_focus: bool
    company_type: Literal["product","service","staffing","unknown"]
    red_flags: list[Literal["unpaid","commission_only","vague","scam"]]
    applicant_instructions: str | None   # Phase-5 "include the word pineapple"
    one_line: str                    # <=140 chars, human summary for the digest

class Salary(BaseModel):
    min_amount: float | None
    max_amount: float | None
    currency: str            # ISO-ish: INR/USD/EUR/GBP…
    period: Literal["year","month","hour","unknown"]
```

**Design rules:**
- **Enums everywhere it's bounded** → reproducible, trivially validated, small output.
- **`null`/`unknown` is mandatory when not stated** — invariant 2 (never fabricate). The distiller is
  explicitly told NOT to infer salary or YOE.
- **`tech_stack` normalized + capped** to keep input to the ranker uniform and short.
- Field order in the schema = output order (Gemini guarantees key order) → byte-stable structure.

## 3. Gemini call settings (verified against google-genai 2.8.0)

```python
GenerateContentConfig(
    temperature=0,
    seed=DISTILL_SEED,                       # fixed constant
    top_p=0.1,
    max_output_tokens=512,                    # crux is small
    response_mime_type="application/json",
    response_schema=Crux,                     # guaranteed shape
    thinking_config=ThinkingConfig(thinking_budget=0),  # DISABLE reasoning → fast+cheap; extraction needs none
)
```
- `thinking_budget=0` is the big latency/cost win for 2.5 Flash on a pure-extraction task.
- `temperature=0 + seed + schema` → outputs are as consistent as the API allows.

## 4. Distillation prompt (fixed, byte-stable)

System/preamble (identical every call → consistency + enables provider-side caching):
```
You extract structured facts from ONE job description into the provided schema.
Rules:
- Use ONLY facts present in the text. If a fact is not stated, output null (or "unknown" for enums).
- NEVER infer or estimate salary or years of experience.
- role_family / seniority_signal: classify from the DESCRIPTION, not just the title.
- night_shift_only: true ONLY if the role is night/rotational-on-call with no day option.
- app_maintenance_focus: true ONLY if the role is primarily long-term maintenance of a large
  existing application codebase.
- company_type: product (builds its own product) vs service/consultancy/staffing vs unknown.
- tech_stack: up to 8 concrete tools, lowercased, normalized (kubernetes->k8s, ci/cd->ci-cd).
- one_line: <=140 char neutral summary.
- applicant_instructions: copy any explicit applicant directive verbatim (e.g. "include the word X"), else null.
```
User content: `title`, `company`, `location`, `is_remote` hint, then the JD markdown (truncated to a
safe ceiling, e.g. 12k chars, to bound tokens).

## 5. Caching (cost + speed)

New storage table (additive; mirrors `jd_analyses`):
```sql
CREATE TABLE IF NOT EXISTS job_cruxes (
    job_id       TEXT PRIMARY KEY,
    crux_json    TEXT NOT NULL,
    distilled_at TEXT NOT NULL
);
```
`ApplicationStore.get_crux(job_id) / save_crux(job_id, crux)`. The distiller only calls Gemini for
job_ids absent from `job_cruxes`. Daily re-runs of the same posting are free. (job_id is the stable
`site:id`, so cruxes are reusable across runs.)

## 6. Robustness (invariant 3)

- A single JD's distillation failing (timeout/garbled/over-length) is **logged + skipped**, the job is
  dropped from ranking with a logged reason — one bad JD never sinks the run.
- Gemini RPD guard (`GeminiProvider`, Phase 2) still applies; if RPD is exhausted, remaining
  un-distilled jobs are reported (never silently dropped) and ranking proceeds on what distilled.
- Schema-validation failure → treat as a failed distill (skip + log).

## 7. Module shape

`src/cvflow/discovery/distill.py`:
- `Crux`, `Salary` pydantic models.
- `Distiller(provider, *, seed, max_jd_chars)` with `distill(posting) -> Crux` (raises on failure).
- `distill_all(postings, store, provider) -> list[Crux]` — cache-aware, per-job isolation, logs drops.

`GeminiProvider` (Phase 2) gains an optional structured/generation-config path:
`generate_structured(prompt, *, schema, seed, thinking_budget=0, temperature=0, max_output_tokens)`
returning parsed JSON — additive; the existing `generate(prompt)->str` seam stays for tailoring.

## 8. Test plan (TDD, mocked — no network)

- crux parse/validate from a canned Gemini JSON reply (all enums, nulls preserved).
- "not stated" → `min_years_required is None` / `stated_salary is None` (no fabrication).
- cache hit: second `distill_all` with a pre-seeded `job_cruxes` makes zero provider calls.
- per-job failure isolation: one raising distill is skipped, others returned, drop logged.
- the Gemini config carries `thinking_budget=0`, `temperature=0`, `response_schema` (assert on a fake client).
